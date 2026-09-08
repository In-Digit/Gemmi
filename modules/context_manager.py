# /home/mrak/gemini_companion/modules/context_manager.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль управления контекстом (ContextManager).
Собирает системный промпт, загружает историю, управляет блокнотом и активным состоянием,
подмешивает мгновенный слепок системы, время, суточный контекст, заметки, описание возможностей
и список радиостанций.
Поддерживает бесшовную синхронизацию истории при изменении файла на диске (для телефонов и планшетов).
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Callable

from modules.notebook_manager import NotebookManager
from modules.active_state_manager import ActiveStateManager

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "history.json")
TIMEOUT_SECONDS = 600  # 10 минут


def get_moscow_time_str() -> str:
    """Возвращает текущее московское время (UTC+3) и день недели."""
    tz_mow = timezone(timedelta(hours=3))
    now = datetime.now(tz_mow)
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return f"Текущие дата и время (Москва): {now.strftime('%d.%m.%Y %H:%M')}, {days[now.weekday()]}."


class ContextManager:
    def __init__(self, config_filepath: str = CONFIG_FILE, history_filepath: str = HISTORY_FILE):
        self.config_filepath = os.path.abspath(config_filepath)
        self.history_filepath = os.path.abspath(history_filepath)

        self.config: Dict[str, Any] = {}
        self.summary: str = ""
        self.history: List[Dict[str, Any]] = []
        self.last_activity_time: float = time.time()
        self.is_paused: bool = False
        self._last_loaded_mtime: float = 0.0

        self.notebook_mgr = NotebookManager()
        self.active_state_mgr = ActiveStateManager()

        self.load_config()
        self.load_history()

    def load_config(self):
        """Загрузка персонажа и профиля пользователя из config.json."""
        if os.path.exists(self.config_filepath):
            try:
                with open(self.config_filepath, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[ContextManager] Ошибка загрузки config.json: {e}")

    def load_history(self):
        """Загрузка состояния диалога из history.json с фиксацией времени mtime файла."""
        if os.path.exists(self.history_filepath):
            try:
                self._last_loaded_mtime = os.path.getmtime(self.history_filepath)
                with open(self.history_filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.summary = data.get("summary", "")
                    self.history = data.get("history", [])
                    self.last_activity_time = data.get("last_activity_time", time.time())
                    self.is_paused = data.get("is_paused", False)
            except Exception as e:
                print(f"[ContextManager] Ошибка загрузки history.json: {e}")

    def sync_if_modified(self):
        """Проверяет, изменился ли history.json на диске (например, при ответе с телефона). Если да — обновляет память."""
        if os.path.exists(self.history_filepath):
            try:
                current_mtime = os.path.getmtime(self.history_filepath)
                if current_mtime > self._last_loaded_mtime:
                    self.load_history()
            except Exception:
                pass

    def save_history(self):
        """Сохранение текущего состояния диалога в history.json."""
        data = {
            "summary": self.summary,
            "history": self.history,
            "last_activity_time": self.last_activity_time,
            "is_paused": self.is_paused
        }
        try:
            with open(self.history_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.history_filepath):
                self._last_loaded_mtime = os.path.getmtime(self.history_filepath)
        except Exception as e:
            print(f"[ContextManager] Ошибка сохранения history.json: {e}")

    def check_timeout(self, summarize_func: Optional[Callable[[List[Dict[str, Any]]], str]] = None):
        """Проверяет таймаут простоя."""
        self.sync_if_modified()
        now = time.time()

        if self.is_paused:
            self.is_paused = False
            self.last_activity_time = now
            self.save_history()
            return

        if self.history and (now - self.last_activity_time > TIMEOUT_SECONDS):
            if summarize_func:
                new_summary = summarize_func(self.history)
                if new_summary:
                    if self.summary:
                        self.summary += f"\n- {new_summary}"
                    else:
                        self.summary = f"- {new_summary}"
                    self.active_state_mgr.update_summary(self.summary)

        self.last_activity_time = now
        self.save_history()

    def set_paused(self, paused: bool = True):
        self.is_paused = paused
        self.save_history()

    def add_user_message(self, text: str):
        self.sync_if_modified()
        self.history.append({
            "role": "user",
            "parts": [{"text": text}]
        })
        self.last_activity_time = time.time()
        if NotebookManager.is_save_triggered(text):
            self.notebook_mgr.add_note(content=text, source_prompt=text)
        self.save_history()

    def add_model_message(self, text: str):
        self.sync_if_modified()
        self.history.append({
            "role": "model",
            "parts": [{"text": text}]
        })
        self.last_activity_time = time.time()
        self.save_history()

    def _backup_history(self):
        if not os.path.exists(self.history_filepath):
            return
        backup_dir = os.path.dirname(self.history_filepath)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"history_backup_{timestamp}.json")
        try:
            shutil.copy2(self.history_filepath, backup_path)
            print(f"[ContextManager] Резервная копия истории сохранена: {backup_path}")
        except Exception as e:
            print(f"[ContextManager] Ошибка создания резервной копии: {e}")

    def clear_all(self):
        self._backup_history()
        self.summary = ""
        self.history = []
        self.is_paused = False
        self.last_activity_time = time.time()
        self.active_state_mgr.update_summary("")
        self.save_history()

    def _build_system_instruction(self, private_mode: bool = False) -> str:
        persona = self.config.get("persona", {})
        user_profile = self.config.get("user_profile", {})
        fiona = self.config.get("fiona", {})

        base_prompt = persona.get("system_prompt", "Ты — Джеми, умный собеседник.")

        user_profile_safe = user_profile.copy()
        if private_mode:
            learned = user_profile_safe.get("learned_facts", [])
            filtered_learned = [
                fact for fact in learned
                if isinstance(fact, dict) and fact.get("sensitivity") != "private"
            ]
            user_profile_safe["learned_facts"] = filtered_learned

        profile_context = "\n\n--- Информация о собеседнике и его окружении ---\n"
        if user_profile_safe:
            profile_context += f"Профиль пользователя: {json.dumps(user_profile_safe, ensure_ascii=False)}\n"
        if fiona:
            profile_context += f"Автомобиль (Фиона): {json.dumps(fiona, ensure_ascii=False)}\n"

        try:
            from modules.instant_snapshot import capture_snapshot
            snapshot = capture_snapshot()
            snapshot_context = f"\n--- Мгновенный слепок системы ---\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n"
        except Exception as e:
            snapshot_context = f"\n--- Мгновенный слепок системы ---\n(Слепок недоступен: {e})\n"

        time_info = f"\n[{get_moscow_time_str()}]"

        command_instruction = (
            "\n\n--- Управление системой и Поиск ---\n"
            "Если пользователь просит выполнить системное действие, сгенерируй точную Bash-команду и помести её в специальный блок:\n"
            "```exec_bash\nкоманда\n```\n"
            "Если тебе нужны факты из интернета или нужно прочитать конкретный сайт по URL, сгенерируй запрос и помести в блок:\n"
            "```web_search\nтвой_запрос_или_http_ссылка\n```\n"
        )

        capabilities_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "capabilities.json")
        capabilities_text = ""
        if os.path.exists(capabilities_path):
            try:
                with open(capabilities_path, "r", encoding="utf-8") as f:
                    caps_data = json.load(f)
                    caps_list = caps_data.get("capabilities", [])
                    if caps_list:
                        capabilities_text = "\n\n--- Мои возможности (актуально) ---\n"
                        for cap in caps_list:
                            capabilities_text += f"- {cap['name']}: {cap['description']}\n"
            except Exception as e:
                capabilities_text = f"\n(Ошибка загрузки capabilities.json: {e})\n"

        radio_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "radio_stations.json")
        radio_text = ""
        if os.path.exists(radio_path):
            try:
                with open(radio_path, "r", encoding="utf-8") as f:
                    radio_data = json.load(f)
                stations = radio_data.get("stations", [])
                if stations:
                    radio_text = "\n\n--- Радио Record: доступные станции ---\n"
                    for s in stations:
                        radio_text += f"{s['name']}: {s['url']}\n"
                    radio_text += (
                        "\nДля включения радио используй команду:\n"
                        "if [ -f ~/.mpv_radio.pid ]; then kill \"$(cat ~/.mpv_radio.pid)\" 2>/dev/null; fi\n"
                        "nohup mpv --no-video --input-ipc-server=/tmp/mpv_socket \"URL\" >/dev/null 2>&1 &\n"
                        "echo $! > ~/.mpv_radio.pid\n"
                    )
            except Exception as e:
                radio_text = f"\n(Ошибка загрузки radio_stations.json: {e})\n"

        private_instruction = ""
        if private_mode:
            private_instruction = (
                "\n\n--- ВАЖНО: РЕЖИМ «МЫ НЕ ОДНИ» ---\n"
                "Рядом могут быть посторонние. Не упоминай прошлое, здоровье, медицинские данные и финансы.\n"
            )

        return (
            f"{base_prompt}{profile_context}{snapshot_context}{time_info}"
            f"{command_instruction}{capabilities_text}{radio_text}{private_instruction}"
        )

    def get_payload(self, private_mode: bool = False) -> Dict[str, Any]:
        self.sync_if_modified()
        full_system_instruction = self._build_system_instruction(private_mode=private_mode)

        daily_ctx = self.active_state_mgr.get_context_formatted()
        if daily_ctx:
            full_system_instruction += f"\n\n{daily_ctx}"
        elif self.summary:
            full_system_instruction += f"\n\nКонтекст предыдущих разговоров за день:\n{self.summary}"

        last_user_text = ""
        for msg in reversed(self.history):
            if msg.get("role") == "user":
                parts = msg.get("parts", [])
                if parts and isinstance(parts[0], dict):
                    last_user_text = parts[0].get("text", "")
                break

        if NotebookManager.is_recall_triggered(last_user_text):
            notes_data = self.notebook_mgr.get_all_notes_formatted()
            full_system_instruction += f"\n\n[Информация из блокнота по твоему запросу]:\n{notes_data}"

        if NotebookManager.is_save_triggered(last_user_text):
            full_system_instruction += "\n\n[Системное уведомление: Информация успешно сохранена в блокнот-справочник (notebook.json). Подтверди запись пользователю.]"

        payload = {
            "contents": self.history,
            "systemInstruction": {
                "parts": [{"text": full_system_instruction}]
            }
        }
        return payload

    def get_estimated_tokens(self) -> int:
        payload = self.get_payload()
        raw_text = json.dumps(payload, ensure_ascii=False)
        return max(1, int(len(raw_text) / 2.2))

    def get_context_health(self, max_tokens: int = 1000000) -> Dict[str, Any]:
        tokens = self.get_estimated_tokens()
        percentage = min(100.0, (tokens / max_tokens) * 100)

        if tokens < 50000:
            status = "Бодра и свежа ⚡"
        elif tokens < 200000:
            status = "В хорошем тонусе 👍"
        elif tokens < 500000:
            status = "Начала уставать ☕"
        else:
            status = "Нужен перезапуск 😴"

        return {
            "tokens": tokens,
            "max_tokens": max_tokens,
            "percentage": percentage,
            "status": status
        }
