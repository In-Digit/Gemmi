#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль Proactive Engine v1.0
Автономный проактивный ассистент, который анализирует контекст пользователя
и при определённых условиях инициирует короткий диалог.

Логика:
- Сбор данных сенсоров (активное окно, температура/загрузка CPU, батарея, геолокация, погода, тревоги, аудио).
- Определение Silence Guard (блокировка инициативы при активной работе).
- Накопление баллов событий за последние N минут.
- Сравнение с динамическим порогом (учитывается расписание, частота, реакции пользователя).
- Генерация реплики через LLM и запись в файл-очередь для app.py.

Конфигурация загружается из proactive_config.json (должен лежать в ~/gemini_companion/config/).
"""

import os
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
import re
import sys

# Попытка импорта requests (нужен для HTTP-запросов к API)
try:
    import requests
except ImportError:
    requests = None  # Будем обрабатывать отсутствие

# Пути к данным
DATA_DIR = os.path.expanduser("~/gemini_companion/data")
CONFIG_DIR = os.path.expanduser("~/gemini_companion/config")
QUEUE_FILE = os.path.join(DATA_DIR, "proactive_queue.json")
STATE_FILE = os.path.join(DATA_DIR, "proactive_state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "proactive_history.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "instant_snapshot.json")
REACTION_FILE = os.path.join(DATA_DIR, "proactive_reaction.json")

# Импорт функций из llm_client (для генерации реплик)
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from modules.llm_client import ask_gemini


class ProactiveEngine:
    """
    Главный класс проактивного движка.
    Отвечает за сбор данных, принятие решений, генерацию реплик и взаимодействие с очередью.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Инициализация движка.
        :param config_path: путь к proactive_config.json (если None, используется стандартный).
        """
        if config_path is None:
            config_path = os.path.join(CONFIG_DIR, "proactive_config.json")
        self.config_path = config_path
        self.config = self._load_config()

        # Внутреннее состояние
        self.state = self._load_state()
        self.last_tick_time = None
        self.current_events = []  # события, обнаруженные на текущем тике

        # Проверка наличия API ключей
        self.weather_api_key = os.getenv(self.config.get("api", {}).get("weather_api_key_env", "OPENWEATHER_API_KEY"))
        if not self.weather_api_key and self.config.get("sensors", {}).get("weather", {}).get("enabled", True):
            print("[ProactiveEngine] Внимание: ключ OpenWeatherMap не задан. Погодные события будут недоступны.")

    # ------------------------------------------------------------
    # Загрузка конфигурации и состояния
    # ------------------------------------------------------------
    def _load_config(self) -> Dict[str, Any]:
        """Загружает конфигурацию из JSON-файла."""
        if not os.path.exists(self.config_path):
            print(f"[ProactiveEngine] Конфиг не найден: {self.config_path}. Использую значения по умолчанию.")
            return self._default_config()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка чтения конфига: {e}. Использую значения по умолчанию.")
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        """Возвращает минимальную конфигурацию, если файл отсутствует."""
        return {
            "sensors": {
                "active_window": {"work_apps": ["code", "cursor", "okular", "evince", "dolphin"],
                                 "work_window_title_patterns": ["Работа"],
                                 "rest_apps": ["firefox", "chromium", "chrome", "vlc", "mpv", "jemi"],
                                 "rest_window_title_patterns": ["YouTube", "VK Video", "Яндекс Музыка", "Radio Record"]},
                "cpu_temperature": {"load_high_threshold": 70, "load_low_threshold": 20,
                                   "temp_low_when_high_load": 45, "temp_high_when_low_load": 70},
                "battery": {"enabled": True, "low_battery_threshold": 20},
                "geolocation": {"home_ssids": ["Tri-AL", "Tri-Al", "Tri-Al-5G"], "work_ssids": ["KbKb"]},
                "weather": {"enabled": True, "poll_interval_minutes": 15},
                "alerts": {"enabled": True, "poll_interval_minutes": 15, "keywords": ["БПЛА", "тревога", "ракетная", "атака"]},
                "audio": {"use_existing_logic": True}
            },
            "triggers": {
                "silence_guard": {"idle_time_less_than_seconds": 300, "block_proactive": True},
                "weights": {"weather.rain_start": 4, "weather.snow_start": 4, "weather.precipitation_end": 3,
                            "weather.temp_change_5c_per_hour": 3, "weather.wind_gust_storm": 4,
                            "temperature.cold": 3, "temperature.hot": 3, "alerts.keyword_detected": 10,
                            "silence_long": 1, "battery_low": 2, "location_changed": 1,
                            "schedule_work_to_evening": 1},
                "threshold": {"base_value": 3, "window_minutes": 30, "min_threshold": 1, "max_threshold": 15},
                "critical_events": {"always_trigger": ["alerts.keyword_detected"]},
                "frequency": {"min_interval_between_initiatives_seconds": 1800,
                              "awaiting_response_timeout_seconds": 1800},
                "idle": {"silence_long_threshold_seconds": 9000}
            },
            "dynamic_thresholds": {
                "reaction_effects": {"positive": {"change": -1, "min_threshold": 1},
                                     "neutral": {"change": 0},
                                     "negative_ignore": {"change": 2, "forgive_after_hours": 3},
                                     "negative_explicit": {"change": 4, "forgive_after_hours": 6}},
                "recovery": {"decrease_per_hour_without_negative": 1, "min_threshold": 1, "max_threshold": 15}
            },
            "generation": {"use_llm": True, "model_cascade": "default",
                           "style_variation_by_user_state": True, "max_replica_length": 0,
                           "system_prompt_suffix": "Ты — проактивный ассистент. Твоя задача — мягко и уместно начать короткий диалог."},
            "schedule": {"work_days": [1,2,3,4,5], "possible_work_saturdays": True,
                         "work_hours": {"start": "08:00", "end": "18:00"},
                         "silence_windows": [{"days": [1,2,3,4,5], "start": "17:45", "end": "21:30"}],
                         "sunday_silence": True, "timezone": "Europe/Moscow"},
            "interaction": {"queue_file": QUEUE_FILE, "if_app_not_running": "do_not_queue", "tts_enabled": False},
            "state": {"history_file": HISTORY_FILE, "history_retention_days": 30,
                      "state_file": STATE_FILE, "hot_reload_config": True}
        }

    def _load_state(self) -> Dict[str, Any]:
        """Загружает состояние движка из файла (если есть)."""
        state_path = self.config.get("state", {}).get("state_file", STATE_FILE)
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ProactiveEngine] Ошибка чтения state-файла {state_path}: {e}")
        return {
            "last_initiative_time": None,
            "awaiting_response": False,
            "last_prompt_time": None,
            "prompt_status": "idle",  # idle, awaiting_response, ignored, blocked
            "current_threshold": self.config["triggers"]["threshold"]["base_value"],
            "last_negative_time": None,
            "event_scores": [],  # список (timestamp, score) для скользящего окна
            "sensor_cache": {}   # кэш последних значений сенсоров для избежания частых запросов
        }

    def _save_state(self):
        """Сохраняет текущее состояние в файл."""
        state_path = self.config.get("state", {}).get("state_file", STATE_FILE)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка сохранения state: {e}")

    # ------------------------------------------------------------
    # Вспомогательные функции для сенсоров
    # ------------------------------------------------------------
    def _run_cmd(self, cmd: str, timeout: int = 5) -> str:
        """Выполняет shell-команду и возвращает stdout (или пустую строку)."""
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return res.stdout.strip()
        except Exception:
            return ""

    def _get_active_window_info(self) -> Tuple[str, str]:
        """
        Возвращает (wm_class, window_title) активного окна.
        Использует xdotool, если доступен, иначе wmctrl.
        """
        # Способ 1: xdotool
        active_id = self._run_cmd("xdotool getactivewindow")
        if active_id:
            wm_class_raw = self._run_cmd(f"xprop -id {active_id} WM_CLASS")
            title = self._run_cmd(f"xdotool getwindowname {active_id}")
            match = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]+)",\s*"([^"]+)"', wm_class_raw)
            if match:
                class1, class2 = match.group(1).lower(), match.group(2).lower()
                wm_class = class2 if class2 else class1
                return wm_class, title
            else:
                return "", title

        # Способ 2: wmctrl через _NET_ACTIVE_WINDOW
        active_id_net = self._run_cmd("xprop -root _NET_ACTIVE_WINDOW")
        if active_id_net:
            match = re.search(r'0x[0-9a-fA-F]+', active_id_net)
            if match:
                win_id = match.group(0)
                wm_class_raw = self._run_cmd(f"xprop -id {win_id} WM_CLASS")
                title_raw = self._run_cmd(f"xprop -id {win_id} _NET_WM_NAME")
                title_match = re.search(r'_NET_WM_NAME\(UTF8_STRING\)\s*=\s*"(.*)"', title_raw)
                title = title_match.group(1) if title_match else ""
                match_class = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]+)",\s*"([^"]+)"', wm_class_raw)
                if match_class:
                    class1, class2 = match_class.group(1).lower(), match_class.group(2).lower()
                    wm_class = class2 if class2 else class1
                    return wm_class, title
        return "", ""

    def _get_cpu_load_and_temp(self) -> Tuple[float, float]:
        """
        Возвращает (загрузка CPU в %, температура ядра в °C).
        Загрузка: средняя за 1 минуту из /proc/loadavg (нормализовано к числу ядер).
        Температура: из /sys/class/thermal/thermal_zone*/temp (первое найденное).
        """
        load = 0.0
        temp = None
        try:
            with open("/proc/loadavg", "r") as f:
                lavg = float(f.readline().split()[0])
            ncores = os.cpu_count() or 1
            load = min(100.0, (lavg / ncores) * 100.0)
        except Exception:
            pass

        # Температура: thermal_zone
        try:
            for zone in sorted(os.listdir("/sys/class/thermal")):
                if zone.startswith("thermal_zone"):
                    path = f"/sys/class/thermal/{zone}/temp"
                    if os.path.exists(path):
                        with open(path, "r") as f:
                            temp_raw = f.read().strip()
                        temp = float(temp_raw) / 1000.0
                        break
        except Exception:
            pass

        if temp is None:
            # Попытка через sensors
            sensors_out = self._run_cmd("sensors -j", timeout=5)
            if sensors_out:
                try:
                    data = json.loads(sensors_out)
                    for chip, entries in data.items():
                        if "coretemp" in chip or "k10temp" in chip or "zenpower" in chip:
                            for key, val in entries.items():
                                if isinstance(val, dict) and "temp1_input" in val:
                                    temp = val["temp1_input"]
                                    break
                            if temp is not None:
                                break
                except Exception:
                    pass
        return load, temp if temp is not None else 0.0

    def _get_battery_status(self) -> Tuple[bool, int, bool]:
        """
        Возвращает (наличие батареи, процент заряда, подключено ли зарядное).
        Если батареи нет, возвращает (False, -1, False).
        """
        battery_dir = "/sys/class/power_supply/BAT0"
        if not os.path.exists(battery_dir):
            battery_dir = "/sys/class/power_supply/BAT1"
        if not os.path.exists(battery_dir):
            return False, -1, False

        try:
            with open(os.path.join(battery_dir, "capacity"), "r") as f:
                percent = int(f.read().strip())
            with open(os.path.join(battery_dir, "status"), "r") as f:
                status = f.read().strip().lower()
            is_charging = status in ["charging", "full"]
            return True, percent, is_charging
        except Exception:
            return False, -1, False

    def _get_current_ssid(self) -> str:
        """Возвращает SSID текущей Wi-Fi сети (или пустую строку)."""
        out = self._run_cmd("nmcli -t -f ACTIVE,SSID dev wifi | grep '^yes:'")
        if out:
            parts = out.split(":", 1)
            if len(parts) > 1:
                return parts[1]
        return ""

    def _get_location(self) -> str:
        """
        Определяет местоположение по SSID: 'дом', 'работа', 'на ходу'.
        """
        ssid = self._get_current_ssid().lower()
        home_ssids = [s.lower() for s in self.config["sensors"]["geolocation"].get("home_ssids", [])]
        work_ssids = [s.lower() for s in self.config["sensors"]["geolocation"].get("work_ssids", [])]
        if ssid in home_ssids:
            return "дом"
        elif ssid in work_ssids:
            return "работа"
        else:
            return "на ходу"

    def _get_weather_events(self) -> List[Dict[str, Any]]:
        """
        Получает текущую погоду и определяет события (начало/конец осадков, изменение температуры, ветер).
        Возвращает список событий с типами.
        """
        if not self.config["sensors"]["weather"].get("enabled", True):
            return []
        if not self.weather_api_key:
            return []
        if not requests:
            return []

        # Используем кэш, чтобы не опрашивать чаще, чем задано
        cache_key = "weather_data"
        cached = self.state["sensor_cache"].get(cache_key)
        now = time.time()
        if cached and now - cached.get("timestamp", 0) < self.config["sensors"]["weather"].get("poll_interval_minutes", 15) * 60:
            return []

        # Запрос к OpenWeatherMap
        city = self.config["sensors"]["weather"].get("city", "Ломоносов, Санкт-Петербург")
        url = self.config.get("api", {}).get("weather_base_url", "https://api.openweathermap.org/data/2.5/weather")
        params = {
            "q": city,
            "appid": self.weather_api_key,
            "units": "metric",
            "lang": "ru"
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return []
            data = resp.json()
            # Извлекаем: погодные условия, температуру, ветер
            weather_main = data["weather"][0]["main"]
            temp = data["main"]["temp"]
            wind_speed = data.get("wind", {}).get("speed", 0)
            # Сохраняем в кэш
            self.state["sensor_cache"]["weather_data"] = {
                "timestamp": now,
                "weather_main": weather_main,
                "temp": temp,
                "wind_speed": wind_speed
            }
            # Определяем события: если погода изменилась с прошлого раза
            prev = self.state["sensor_cache"].get("weather_prev")
            events = []
            if prev:
                # Проверка осадков
                prev_weather = prev["weather_main"]
                if prev_weather not in ["Rain", "Snow"] and weather_main in ["Rain", "Snow"]:
                    if weather_main == "Rain":
                        events.append({"type": "weather.rain_start", "weight": self.config["triggers"]["weights"].get("weather.rain_start", 4)})
                    elif weather_main == "Snow":
                        events.append({"type": "weather.snow_start", "weight": self.config["triggers"]["weights"].get("weather.snow_start", 4)})
                elif prev_weather in ["Rain", "Snow"] and weather_main not in ["Rain", "Snow"]:
                    events.append({"type": "weather.precipitation_end", "weight": self.config["triggers"]["weights"].get("weather.precipitation_end", 3)})
                # Изменение температуры на 5°C за час
                temp_diff = abs(temp - prev["temp"])
                if temp_diff >= self.config["sensors"]["weather"]["events"].get("temp_change_5c_per_hour", {}).get("threshold", 5):
                    events.append({"type": "weather.temp_change_5c_per_hour", "weight": self.config["triggers"]["weights"].get("weather.temp_change_5c_per_hour", 3)})
                # Резкое усиление ветра
                if wind_speed >= 20 and prev.get("wind_speed", 0) < 20:
                    events.append({"type": "weather.wind_gust_storm", "weight": self.config["triggers"]["weights"].get("weather.wind_gust_storm", 4)})
            # Обновляем предыдущее состояние
            self.state["sensor_cache"]["weather_prev"] = {
                "weather_main": weather_main,
                "temp": temp,
                "wind_speed": wind_speed,
                "timestamp": now
            }
            return events
        except Exception:
            return []

    def _get_alerts_events(self) -> List[Dict[str, Any]]:
        """
        Проверяет RSS-ленты и Telegram-каналы на ключевые слова (БПЛА, тревога и т.п.).
        Возвращает событие alerts.keyword_detected при обнаружении.
        """
        if not self.config["sensors"]["alerts"].get("enabled", True):
            return []
        if not requests:
            return []

        cache_key = "alerts_data"
        cached = self.state["sensor_cache"].get(cache_key)
        now = time.time()
        if cached and now - cached.get("timestamp", 0) < self.config["sensors"]["alerts"].get("poll_interval_minutes", 15) * 60:
            return []

        keywords = [k.lower() for k in self.config["sensors"]["alerts"].get("keywords", [])]
        events = []
        # RSS-ленты
        for feed_url in self.config["sensors"]["alerts"].get("rss_feeds", []):
            try:
                resp = requests.get(feed_url, timeout=10)
                if resp.status_code == 200:
                    content = resp.text.lower()
                    for kw in keywords:
                        if kw in content:
                            events.append({"type": "alerts.keyword_detected", "weight": self.config["triggers"]["weights"].get("alerts.keyword_detected", 10)})
                            break
            except Exception:
                continue
        # Telegram-каналы (публичный просмотр)
        for tg_url in self.config["sensors"]["alerts"].get("telegram_channels", []):
            try:
                resp = requests.get(tg_url, timeout=10)
                if resp.status_code == 200:
                    content = resp.text.lower()
                    for kw in keywords:
                        if kw in content:
                            events.append({"type": "alerts.keyword_detected", "weight": self.config["triggers"]["weights"].get("alerts.keyword_detected", 10)})
                            break
            except Exception:
                continue

        self.state["sensor_cache"]["alerts_data"] = {"timestamp": now}
        return events

    def _get_audio_playing(self) -> bool:
        """Определяет, воспроизводится ли аудио (из instant_snapshot логика)."""
        # Используем существующий модуль
        try:
            from modules.instant_snapshot import capture_snapshot
            snap = capture_snapshot()
            return snap.get("media_playing", False)
        except Exception:
            # Резервный вариант через playerctl/pactl
            player_status = self._run_cmd("playerctl -a status 2>/dev/null")
            if "playing" in player_status.lower():
                return True
            pactl_out = self._run_cmd("pactl list sink-inputs 2>/dev/null").lower()
            if 'pulse.corked = "false"' in pactl_out or 'corked: no' in pactl_out:
                return True
            return False

    # ------------------------------------------------------------
    # Определение Silence Guard
    # ------------------------------------------------------------
    def _is_silence_guard_active(self, current_window_class: str, current_window_title: str, idle_time_seconds: float) -> bool:
        """
        Silence Guard: блокирует инициативу, если пользователь активно работает.
        Условия: активное окно в списке work_apps или заголовок содержит паттерн работы,
        и время последней активности пользователя меньше порога.
        """
        sg_config = self.config["triggers"]["silence_guard"]
        if not sg_config.get("block_proactive", True):
            return False
        idle_threshold = sg_config.get("idle_time_less_than_seconds", 300)
        if idle_time_seconds >= idle_threshold:
            return False  # пользователь давно не активен, можно проявить инициативу

        # Проверка, является ли текущее окно рабочим
        work_apps = [app.lower() for app in self.config["sensors"]["active_window"].get("work_apps", [])]
        work_patterns = [p.lower() for p in self.config["sensors"]["active_window"].get("work_window_title_patterns", [])]

        is_work_app = current_window_class.lower() in work_apps
        is_work_title = any(pattern in current_window_title.lower() for pattern in work_patterns)

        return is_work_app or is_work_title

    # ------------------------------------------------------------
    # Сбор всех событий
    # ------------------------------------------------------------
    def _collect_all_events(self) -> Tuple[List[Dict[str, Any]], bool, float]:
        """
        Собирает все события на основе сенсоров и текущего состояния.
        Возвращает (events, silence, idle_time).
        """
        events = []
        sensors_cfg = self.config["sensors"]
        triggers_cfg = self.config["triggers"]

        # Активное окно и idle time
        wm_class, title = self._get_active_window_info()
        # Время последнего взаимодействия с пользователем (можно из history.json или из app.py)
        # Здесь используем сохранённое значение из state, если есть, иначе берём текущее время.
        # В реальном приложении нужно получать из context_manager или общего файла состояния.
        # Для простоты будем читать last_interaction из interaction_state.json, если есть.
        last_interaction = time.time()  # по умолчанию сейчас
        interaction_file = os.path.join(DATA_DIR, "interaction_state.json")
        if os.path.exists(interaction_file):
            try:
                with open(interaction_file, "r", encoding="utf-8") as f:
                    inter_data = json.load(f)
                    last_inter_str = inter_data.get("last_interaction")
                    if last_inter_str:
                        last_interaction = datetime.fromisoformat(last_inter_str).timestamp()
            except Exception:
                pass
        idle_time = max(0.0, time.time() - last_interaction)

        # Проверка Silence Guard
        silence = self._is_silence_guard_active(wm_class, title, idle_time)
        if silence:
            # Блокируем все события, кроме, возможно, критических? По спецификации - все блокируются.
            # Но для критических (БПЛА) мы всё равно должны проверить, но не генерировать реплику.
            # Мы просто не будем добавлять события, а Silence Guard обработается в should_trigger.
            pass
        else:
            # Собираем события по сенсорам
            # 1. Температура/нагрузка
            load, temp = self._get_cpu_load_and_temp()
            cpu_cfg = sensors_cfg.get("cpu_temperature", {})
            high_load = cpu_cfg.get("load_high_threshold", 70)
            low_load = cpu_cfg.get("load_low_threshold", 20)
            temp_low = cpu_cfg.get("temp_low_when_high_load", 45)
            temp_high = cpu_cfg.get("temp_high_when_low_load", 70)
            if load > high_load and temp < temp_low:
                events.append({"type": "temperature.cold", "weight": triggers_cfg["weights"].get("temperature.cold", 3)})
            elif load < low_load and temp > temp_high:
                events.append({"type": "temperature.hot", "weight": triggers_cfg["weights"].get("temperature.hot", 3)})

            # 2. Батарея
            if sensors_cfg.get("battery", {}).get("enabled", True):
                has_bat, percent, charging = self._get_battery_status()
                if has_bat and percent < sensors_cfg["battery"].get("low_battery_threshold", 20) and not charging:
                    events.append({"type": "battery_low", "weight": triggers_cfg["weights"].get("battery_low", 2)})

            # 3. Геолокация (изменение местоположения)
            current_location = self._get_location()
            prev_location = self.state["sensor_cache"].get("location")
            if prev_location and prev_location != current_location:
                events.append({"type": "location_changed", "weight": triggers_cfg["weights"].get("location_changed", 1)})
            self.state["sensor_cache"]["location"] = current_location

            # 4. Погодные события
            weather_events = self._get_weather_events()
            events.extend(weather_events)

            # 5. Тревоги (БПЛА)
            alert_events = self._get_alerts_events()
            events.extend(alert_events)

            # 6. Долгое молчание (если пользователь долго не взаимодействовал)
            silence_long_threshold = triggers_cfg["idle"].get("silence_long_threshold_seconds", 9000)
            if idle_time > silence_long_threshold:
                events.append({"type": "silence_long", "weight": triggers_cfg["weights"].get("silence_long", 1)})

            # 7. Смена времени с работы на вечер (если сейчас период тишины закончился и раньше была работа)
            # Простейшая реализация: если текущее время в пределах расписания "вечер" и до этого было "работа",
            # то добавляем событие schedule_work_to_evening. Это можно вычислить по расписанию.
            # Для простоты опустим, но добавим позже при необходимости.

        return events, silence, idle_time

    # ------------------------------------------------------------
    # Управление баллами и порогом
    # ------------------------------------------------------------
    def _update_event_scores(self, events: List[Dict[str, Any]]):
        """
        Добавляет новые события в скользящее окно и удаляет устаревшие.
        """
        now = time.time()
        window_seconds = self.config["triggers"]["threshold"].get("window_minutes", 30) * 60
        # Удаляем старые
        self.state["event_scores"] = [ev for ev in self.state.get("event_scores", [])
                                      if now - ev["timestamp"] < window_seconds]
        for event in events:
            self.state["event_scores"].append({
                "timestamp": now,
                "score": event.get("weight", 0),
                "type": event["type"]
            })

    def _get_total_score(self) -> int:
        """Возвращает сумму баллов в текущем окне."""
        return sum(ev["score"] for ev in self.state.get("event_scores", []))

    def _get_current_threshold(self) -> int:
        """Возвращает текущий динамический порог."""
        return self.state.get("current_threshold", self.config["triggers"]["threshold"]["base_value"])

    def _update_threshold(self, reaction: Optional[str] = None):
        """
        Обновляет динамический порог на основе реакции пользователя.
        reaction может быть None (нет новой реакции), 'positive', 'neutral', 'negative_ignore', 'negative_explicit'.
        Также учитывается восстановление порога со временем без негатива.
        """
        dyn_cfg = self.config["dynamic_thresholds"]
        current = self.state.get("current_threshold", self.config["triggers"]["threshold"]["base_value"])
        min_thr = self.config["triggers"]["threshold"].get("min_threshold", 1)
        max_thr = self.config["triggers"]["threshold"].get("max_threshold", 15)

        # Восстановление со временем (если нет негативных реакций)
        now = time.time()
        last_negative = self.state.get("last_negative_time")
        if last_negative and reaction not in ["negative_ignore", "negative_explicit"]:
            hours_since = (now - last_negative) / 3600
            decrease = dyn_cfg["recovery"].get("decrease_per_hour_without_negative", 1) * int(hours_since)
            if decrease > 0:
                current = max(min_thr, current - decrease)
                self.state["last_negative_time"] = None  # сбрасываем, так как уже применили
        else:
            self.state["last_negative_time"] = None

        # Применение реакции
        if reaction:
            effect = dyn_cfg["reaction_effects"].get(reaction, {})
            change = effect.get("change", 0)
            current += change
            if reaction.startswith("negative"):
                self.state["last_negative_time"] = now
                # Для негативных реакций также учитываем "прощение"
                # Но пока просто фиксируем время, восстановление будет через decrease_per_hour_without_negative
                # после того, как пройдёт forgive_after_hours? Лучше: прощение означает, что негатив больше не действует.
                # Реализуем просто: если прошло forgive_after_hours, то last_negative_time сбрасывается.
                forgive_hours = effect.get("forgive_after_hours")
                if forgive_hours:
                    # Можно запланировать сброс в будущем, но для простоты будем проверять в начале _update_threshold
                    pass

        # Ограничение диапазона
        current = max(min_thr, min(max_thr, current))
        self.state["current_threshold"] = current
        self._save_state()

    # ------------------------------------------------------------
    # Проверка условий и генерация реплики
    # ------------------------------------------------------------
    def _should_trigger(self, total_score: int, silence_guard: bool) -> bool:
        """
        Решает, нужно ли проявить инициативу.
        Учитывает:
        - silence_guard (если True, то только критические события могут пройти, но и они блокируются по условию)
        - расписание (окна тишины, воскресенье)
        - минимальный интервал между инициативами
        - статус ожидания ответа (awaiting_response, ignored)
        """
        # Проверка расписания
        if not self._is_allowed_by_schedule():
            return False

        # Проверка минимального интервала
        now = time.time()
        last_init = self.state.get("last_initiative_time")
        if last_init:
            interval = self.config["triggers"]["frequency"].get("min_interval_between_initiatives_seconds", 1800)
            if now - last_init < interval:
                return False

        # Проверка статуса ожидания ответа
        status = self.state.get("prompt_status", "idle")
        if status == "awaiting_response":
            # Если ещё ждём ответа, не повторяем
            return False
        if status == "ignored":
            # Игнор на 30 минут, не беспокоим
            return False

        # Если silence_guard активен, то инициатива запрещена (кроме критических, но по ТЗ тоже запрещены)
        if silence_guard:
            return False

        # Проверка порога
        threshold = self._get_current_threshold()
        if total_score > threshold:
            return True

        # Проверка критических событий (всегда срабатывают, кроме silence guard)
        critical_types = self.config["triggers"].get("critical_events", {}).get("always_trigger", [])
        for ev in self.state.get("event_scores", []):
            if ev["type"] in critical_types and ev["timestamp"] > now - 60:  # только свежие
                return True

        return False

    def _is_allowed_by_schedule(self) -> bool:
        """
        Проверяет, разрешена ли инициатива в текущее время согласно расписанию.
        """
        schedule = self.config.get("schedule", {})
        tz_name = schedule.get("timezone", "Europe/Moscow")
        try:
            tz = timezone(timedelta(hours=3))  # Москва UTC+3
        except Exception:
            tz = timezone.utc
        now = datetime.now(tz)
        weekday = now.weekday()  # 0-6 (пн=0)
        current_time = now.strftime("%H:%M")

        # Воскресенье - полный запрет
        if schedule.get("sunday_silence", True) and weekday == 6:
            return False

        # Проверка окон тишины
        silence_windows = schedule.get("silence_windows", [])
        for window in silence_windows:
            days = window.get("days", [])
            if weekday in days:
                start = window.get("start", "00:00")
                end = window.get("end", "23:59")
                # Если start < end, то обычный интервал; если start > end, то переход через полночь
                if start <= current_time <= end:
                    return False

        # Проверка рабочей субботы: если сегодня суббота и possible_work_saturdays True,
        # и окно тишины на весь день (start=00:00, end=23:59), то тоже запрет.
        if weekday == 5 and schedule.get("possible_work_saturdays", True):
            for window in silence_windows:
                days = window.get("days", [])
                if 5 in days and window.get("start") == "00:00" and window.get("end") == "23:59":
                    return False

        # Если текущее время в рабочих часах и это будний день, то можно (но это не запрещает)
        return True

    def _generate_replica(self, events: List[Dict[str, Any]], context_snapshot: str = "") -> Optional[str]:
        """
        Генерирует короткую реплику через LLM (или шаблон, если LLM недоступен).
        Возвращает текст реплики.
        """
        gen_cfg = self.config.get("generation", {})
        if not gen_cfg.get("use_llm", True):
            return None
        # Формируем промпт для LLM с учётом событий и контекста
        # Используем ask_gemini с системным промптом
        system_prompt = gen_cfg.get("system_prompt_suffix", "")
        event_descriptions = [f"- {ev['type']}" for ev in events]
        prompt = (
            f"События, которые произошли: {', '.join(event_descriptions) if event_descriptions else 'нет'}\n"
            f"Текущий контекст системы: {context_snapshot if context_snapshot else 'недоступен'}\n"
            "Сгенерируй одну короткую, естественную реплику, чтобы начать диалог. "
            "Не упоминай события напрямую, если это не критично (например, не говори 'я заметил дождь', "
            "если можно сказать 'Погода портится, может, заварить чай?')."
        )
        try:
            response = ask_gemini(prompt)
            if isinstance(response, str) and not response.startswith("Ошибка"):
                return response.strip()
        except Exception:
            pass
        # Запасной вариант
        return "Как дела?"

    def _queue_replica(self, text: str):
        """Добавляет реплику в файл-очередь для app.py."""
        if not text:
            return
        queue_file = self.config["interaction"].get("queue_file", QUEUE_FILE)
        if self.config["interaction"].get("if_app_not_running", "do_not_queue") == "do_not_queue":
            # Проверяем, запущен ли app.py? Пока не реализовано, просто пишем.
            pass
        os.makedirs(os.path.dirname(queue_file), exist_ok=True)
        try:
            with open(queue_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": time.time(), "text": text}, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка записи в очередь: {e}")

    # ------------------------------------------------------------
    # Основной цикл (вызывается извне)
    # ------------------------------------------------------------
    def tick(self):
        """
        Выполняет один цикл проверки.
        Вызывается периодически (например, каждые 60 секунд) из демона или потока.
        """
        now = time.time()
        if self.last_tick_time and now - self.last_tick_time < 60:  # минимум раз в минуту
            return
        self.last_tick_time = now

        # Горячая перезагрузка конфига (если включена)
        if self.config.get("state", {}).get("hot_reload_config", False):
            self.config = self._load_config()

        # Обработка реакции пользователя (если есть)
        if os.path.exists(REACTION_FILE):
            try:
                with open(REACTION_FILE, "r", encoding="utf-8") as f:
                    reaction_data = json.load(f)
                reaction = reaction_data.get("reaction")
                if reaction:
                    self.process_user_reaction(reaction)
                os.remove(REACTION_FILE)
            except Exception as e:
                print(f"[ProactiveEngine] Ошибка чтения реакции: {e}")

        # Собираем события
        events, silence, idle_time = self._collect_all_events()

        # Обновляем баллы
        self._update_event_scores(events)

        # Проверяем, нужно ли проявить инициативу
        total_score = self._get_total_score()
        if self._should_trigger(total_score, silence):
            # Генерируем реплику
            context_snapshot = json.dumps(self.state["sensor_cache"], ensure_ascii=False)
            replica = self._generate_replica(events, context_snapshot)
            if replica:
                self._queue_replica(replica)
                self.state["last_initiative_time"] = now
                self.state["prompt_status"] = "awaiting_response"
                self.state["last_prompt_time"] = now.isoformat()
                self._save_state()
                print(f"[ProactiveEngine] Отправлена реплика: {replica}")

        # Обработка таймаута ожидания ответа
        if self.state["prompt_status"] == "awaiting_response":
            last_prompt = self.state.get("last_prompt_time")
            if last_prompt:
                last_prompt_dt = datetime.fromisoformat(last_prompt)
                if (datetime.now() - last_prompt_dt).total_seconds() > self.config["triggers"]["frequency"].get("awaiting_response_timeout_seconds", 1800):
                    self.state["prompt_status"] = "ignored"
                    self._save_state()

        # Обновление порога на основе реакций (если пользователь ответил, это должно вызываться отдельным методом)
        # Здесь можно проверять наличие новых сообщений пользователя в истории и корректировать порог,
        # но для простоты оставим для внешнего вызова process_user_reaction().

    def process_user_reaction(self, reaction: str):
        """
        Вызывается, когда пользователь реагирует на проактивную реплику.
        reaction: 'positive', 'neutral', 'negative_ignore', 'negative_explicit'
        """
        self._update_threshold(reaction)
        self.state["prompt_status"] = "idle"
        self._save_state()


# ------------------------------------------------------------
# Точка входа для тестирования
# ------------------------------------------------------------
if __name__ == "__main__":
    engine = ProactiveEngine()
    print("Proactive Engine запущен. Нажмите Ctrl+C для выхода.")
    try:
        while True:
            engine.tick()
            time.sleep(60)  # раз в минуту
    except KeyboardInterrupt:
        print("Остановлено.")
