# /home/mrak/gemini_companion/modules/media_player.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль гибридного медиа-плеера (MediaPlayer).

Обеспечивает:
1. Поиск и проигрывание аудио из YouTube Music через ytmusicapi + mpv.
2. Прямое управление mpv по Unix IPC-сокету (/tmp/jemi_music.sock).
3. Автоматический фоллбэк на playerctl для управления системными плеерами/браузерами,
   если наш mpv-плеер не запущен.
4. Очистку радио-процессов (Record) при запуск музыки.
"""

import os
import sys
import json
import socket
import time
import subprocess
from typing import Dict, Any, Optional

# Попытка импорта ytmusicapi
try:
    from ytmusicapi import YTMusic
except ImportError:
    YTMusic = None

# Пути к файлам обмена и сокетам
IPC_SOCKET_PATH = "/tmp/jemi_music.sock"
RADIO_PID_PATH = os.path.expanduser("~/.mpv_radio.pid")
STATE_FILE_PATH = os.path.expanduser("~/gemini_companion/data/ytmusic_state.json")


class MediaPlayer:
    """
    Класс управления воспроизведением.
    Объединяет YouTube Music (mpv IPC) и системные плееры (playerctl).
    """

    def __init__(self):
        self.ytmusic = None
        self._init_ytmusic()

    def _init_ytmusic(self):
        """Инициализация клиента YTMusic без требовании авторизации."""
        if YTMusic is not None:
            try:
                self.ytmusic = YTMusic()
            except Exception as e:
                print(f"[MediaPlayer] Ошибка инициализации ytmusicapi: {e}")

    def _is_ipc_socket_active(self) -> bool:
        """Проверяет, существует ли активный Unix IPC-сокет mpv."""
        if not os.path.exists(IPC_SOCKET_PATH):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect(IPC_SOCKET_PATH)
                return True
        except Exception:
            # Сокет мертв или остался после аварийного завершения
            return False

    def send_mpv_ipc_command(self, command: list) -> Optional[Dict[str, Any]]:
        """Отправляет JSON-команду в mpv через Unix IPC-сокет и возвращает ответ."""
        if not self._is_ipc_socket_active():
            return None

        payload = {"command": command}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(IPC_SOCKET_PATH)
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                response_raw = s.recv(4096)
                if response_raw:
                    return json.loads(response_raw.decode("utf-8"))
        except Exception as e:
            print(f"[MediaPlayer] Ошибка IPC сокета: {e}")
        return None

    def _stop_radio_if_playing(self):
        """Останавливает радио Record, если оно было запущено."""
        if os.path.exists(RADIO_PID_PATH):
            try:
                with open(RADIO_PID_PATH, "r") as f:
                    pid_str = f.read().strip()
                if pid_str.isdigit():
                    os.system(f"kill {pid_str} 2>/dev/null")
                os.remove(RADIO_PID_PATH)
            except Exception:
                pass

    def stop_mpv_music(self):
        """Полностью останавливает текущий mpv музыкальный процесс."""
        if self._is_ipc_socket_active():
            self.send_mpv_ipc_command(["quit"])
            time.sleep(0.2)

        # Если сокет застрял, чистим файл сокета
        if os.path.exists(IPC_SOCKET_PATH):
            try:
                os.remove(IPC_SOCKET_PATH)
            except Exception:
                pass

        # Чистим файл состояния
        if os.path.exists(STATE_FILE_PATH):
            try:
                os.remove(STATE_FILE_PATH)
            except Exception:
                pass

    def search_and_play_ytmusic(self, query: str) -> Dict[str, Any]:
        """
        Ищет трек в YouTube Music по запросу и запускает воспроизведение в mpv.
        Возвращает словарь с информацией о треке или ошибке.
        """
        if self.ytmusic is None:
            return {"success": False, "error": "Библиотека ytmusicapi не инициализирована."}

        clean_query = query.strip()
        if not clean_query:
            return {"success": False, "error": "Пустой поисковый запрос."}

        try:
            search_results = self.ytmusic.search(clean_query, filter="songs", limit=1)
            if not search_results:
                # Попробуем без фильтра, если среди песен не нашлось
                search_results = self.ytmusic.search(clean_query, limit=1)

            if not search_results:
                return {"success": False, "error": f"Ничего не найдено по запросу '{clean_query}'."}

            first_item = search_results[0]
            video_id = first_item.get("videoId")
            title = first_item.get("title", "Неизвестный трек")

            artists = first_item.get("artists", [])
            artist_name = artists[0].get("name") if artists else "Неизвестный исполнитель"

            if not video_id:
                return {"success": False, "error": "Не удалось извлечь ID видео."}

            track_url = f"https://www.youtube.com/watch?v={video_id}"

            # 1. Останавливаем радио и прошлый mpv
            self._stop_radio_if_playing()
            self.stop_mpv_music()

            # 2. Запускаем mpv в фоновом режиме с привязкой IPC-сокета
            cmd = (
                f"nohup mpv --no-video --input-ipc-server={IPC_SOCKET_PATH} "
                f"\"{track_url}\" >/dev/null 2>&1 &"
            )
            os.system(cmd)

            track_info = {
                "success": True,
                "title": title,
                "artist": artist_name,
                "url": track_url,
                "video_id": video_id,
                "source": "ytmusic"
            }

            # Сохраняем состояние
            os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
            with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(track_info, f, ensure_ascii=False, indent=2)

            return track_info

        except Exception as e:
            return {"success": False, "error": f"Сбой поиска YouTube Music: {e}"}

    # --- Управление воспроизведением (Гибридный роутер) ---

    def play_pause(self) -> str:
        """Переключает Воспроизведение/Паузу. Если запущен mpv — шлет в сокет, иначе в playerctl."""
        if self._is_ipc_socket_active():
            self.send_mpv_ipc_command(["cycle", "pause"])
            return "mpv_toggled"
        else:
            try:
                subprocess.run(["playerctl", "play-pause"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "playerctl_toggled"
            except Exception:
                return "failed"

    def next_track(self) -> str:
        """Следующий трек."""
        if self._is_ipc_socket_active():
            self.send_mpv_ipc_command(["playlist-next"])
            return "mpv_next"
        else:
            try:
                subprocess.run(["playerctl", "next"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "playerctl_next"
            except Exception:
                return "failed"

    def prev_track(self) -> str:
        """Предыдущий трек или перемотка в начало."""
        if self._is_ipc_socket_active():
            # Если трек проиграл больше 3 сек — сбрасываем на начало
            self.send_mpv_ipc_command(["seek", "0", "absolute"])
            return "mpv_prev"
        else:
            try:
                subprocess.run(["playerctl", "previous"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "playerctl_prev"
            except Exception:
                return "failed"

    def stop_all(self) -> str:
        """Полная остановка любого воспроизведения (mpv, радио и playerctl)."""
        self._stop_radio_if_playing()
        self.stop_mpv_music()
        try:
            subprocess.run(["playerctl", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return "stopped"

    def get_status(self) -> Dict[str, Any]:
        """
        Возвращает текущий статус плеера для отображения в GUI:
        название трека, источник (ytmusic/system/idle) и состояние паузы.
        """
        # 1. Проверяем наш mpv
        if self._is_ipc_socket_active():
            title_resp = self.send_mpv_ipc_command(["get_property", "media-title"])
            pause_resp = self.send_mpv_ipc_command(["get_property", "pause"])

            media_title = title_resp.get("data") if title_resp and title_resp.get("error") == "success" else None
            is_paused = pause_resp.get("data", False) if pause_resp and pause_resp.get("error") == "success" else False

            # Пытаемся взять сохраненные имя/артиста из файла
            saved_title = None
            if os.path.exists(STATE_FILE_PATH):
                try:
                    with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        saved_title = f"{data.get('artist', '')} — {data.get('title', '')}".strip(" —")
                except Exception:
                    pass

            display_name = saved_title or media_title or "Воспроизведение YouTube Music"
            return {
                "active": True,
                "source": "YouTube Music (mpv)",
                "track": display_name,
                "paused": is_paused
            }

        # 2. Проверяем радио
        if os.path.exists(RADIO_PID_PATH):
            return {
                "active": True,
                "source": "Радио Record",
                "track": "Прямой эфир Record",
                "paused": False
            }

        # 3. Фоллбэк на системный playerctl
        try:
            res_status = subprocess.run(["playerctl", "status"], capture_output=True, text=True, timeout=0.5)
            status_text = res_status.stdout.strip().lower()

            if status_text in ["playing", "paused"]:
                res_meta = subprocess.run(
                    ["playerctl", "metadata", "--format", "{{ artist }} - {{ title }}"],
                    capture_output=True, text=True, timeout=0.5
                )
                track_meta = res_meta.stdout.strip().strip("- ") or "Системное аудио"
                return {
                    "active": True,
                    "source": "Системный плеер",
                    "track": track_meta,
                    "paused": status_text == "paused"
                }
        except Exception:
            pass

        return {
            "active": False,
            "source": "Пауза",
            "track": "Тишина",
            "paused": True
        }


if __name__ == "__main__":
    player = MediaPlayer()
    print("=== Тестирование гибридного медиа-плеера ===")
    status = player.get_status()
    print("Текущий статус:", json.dumps(status, ensure_ascii=False, indent=2))
