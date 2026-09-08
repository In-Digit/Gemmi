#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль Proactive Engine v2.0
Автономный проактивный ассистент с поддержкой голосовой эскалации.
Анализирует контекст пользователя, при долгом простое проявляет инициативу,
а при игнорировании переключает систему в голосовой режим и запрашивает реакцию голосом.
"""

import os
import json
import subprocess
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
import re
import sys

try:
    import requests
except ImportError:
    requests = None

DATA_DIR = os.path.expanduser("~/gemini_companion/data")
CONFIG_DIR = os.path.expanduser("~/gemini_companion/config")
QUEUE_FILE = os.path.join(DATA_DIR, "proactive_queue.json")
STATE_FILE = os.path.join(DATA_DIR, "proactive_state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "proactive_history.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "instant_snapshot.json")
REACTION_FILE = os.path.join(DATA_DIR, "proactive_reaction.json")
INTERACTION_FILE = os.path.join(DATA_DIR, "interaction_state.json")

VOICE_CONFIG_FILE = os.path.join(CONFIG_DIR, "voice_config.json")
VOICE_RESPONSE_FILE = os.path.join(DATA_DIR, "voice_response.json")
FORCE_LISTEN_FLAG = os.path.join(DATA_DIR, "voice_force_listen.flag")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from modules.llm_client import ask_gemini


class ProactiveEngine:
    """
    Главный класс проактивного движка с поддержкой голосовой эскалации (побудки).
    """

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = os.path.join(CONFIG_DIR, "proactive_config.json")
        self.config_path = config_path
        self.config = self._load_config()
        self.state = self._load_state()
        self.last_tick_time = None

        self.weather_api_key = os.getenv(self.config.get("api", {}).get("weather_api_key_env", "OPENWEATHER_API_KEY"))

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return self._default_config()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
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
                            "silence_long": 1, "battery_low": 2, "location_changed": 1},
                "threshold": {"base_value": 3, "window_minutes": 30, "min_threshold": 1, "max_threshold": 15},
                "critical_events": {"always_trigger": ["alerts.keyword_detected"]},
                "frequency": {"min_interval_between_initiatives_seconds": 1800,
                              "awaiting_response_timeout_seconds": 1800},
                # Установили порог долгого молчания в 1 час (3600 секунд)
                "idle": {"silence_long_threshold_seconds": 3600}
            },
            "dynamic_thresholds": {
                "reaction_effects": {"positive": {"change": -1, "min_threshold": 1},
                                     "neutral": {"change": 0},
                                     "negative_ignore": {"change": 2, "forgive_after_hours": 3},
                                     "negative_explicit": {"change": 4, "forgive_after_hours": 6}},
                "recovery": {"decrease_per_hour_without_negative": 1, "min_threshold": 1, "max_threshold": 15}
            },
            "generation": {"use_llm": True,
                           "system_prompt_suffix": "Ты — проактивный ассистент. Твоя задача — мягко и уместно начать короткий диалог."},
            "schedule": {"work_days": [1,2,3,4,5], "possible_work_saturdays": True,
                         "work_hours": {"start": "08:00", "end": "18:00"},
                         "silence_windows": [],
                         "sunday_silence": False, "timezone": "Europe/Moscow"},
            "interaction": {"queue_file": QUEUE_FILE, "if_app_not_running": "do_not_queue", "tts_enabled": True},
            "state": {"history_file": HISTORY_FILE, "state_file": STATE_FILE, "hot_reload_config": True}
        }

    def _load_state(self) -> Dict[str, Any]:
        state_path = self.config.get("state", {}).get("state_file", STATE_FILE)
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_initiative_time": None,
            "awaiting_response": False,
            "last_prompt_time": None,
            "prompt_status": "idle",
            "current_threshold": self.config["triggers"]["threshold"]["base_value"],
            "last_negative_time": None,
            "event_scores": [],
            "sensor_cache": {}
        }

    def _save_state(self):
        state_path = self.config.get("state", {}).get("state_file", STATE_FILE)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка сохранения state: {e}")

    def _run_cmd(self, cmd: str, timeout: int = 5) -> str:
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return res.stdout.strip()
        except Exception:
            return ""

    def _get_active_window_info(self) -> Tuple[str, str]:
        active_id = self._run_cmd("xdotool getactivewindow")
        if active_id:
            wm_class_raw = self._run_cmd(f"xprop -id {active_id} WM_CLASS")
            title = self._run_cmd(f"xdotool getwindowname {active_id}")
            match = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]+)",\s*"([^"]+)"', wm_class_raw)
            if match:
                return match.group(2).lower(), title
            return "", title
        return "", ""

    def _is_silence_guard_active(self, current_window_class: str, current_window_title: str, idle_time_seconds: float) -> bool:
        sg_config = self.config["triggers"]["silence_guard"]
        if not sg_config.get("block_proactive", True):
            return False
        idle_threshold = sg_config.get("idle_time_less_than_seconds", 300)
        if idle_time_seconds >= idle_threshold:
            return False

        work_apps = [app.lower() for app in self.config["sensors"]["active_window"].get("work_apps", [])]
        work_patterns = [p.lower() for p in self.config["sensors"]["active_window"].get("work_window_title_patterns", [])]

        is_work_app = current_window_class.lower() in work_apps
        is_work_title = any(pattern in current_window_title.lower() for pattern in work_patterns)
        return is_work_app or is_work_title

    def _collect_all_events(self) -> Tuple[List[Dict[str, Any]], bool, float]:
        events = []
        triggers_cfg = self.config["triggers"]
        wm_class, title = self._get_active_window_info()

        last_interaction = time.time()
        if os.path.exists(INTERACTION_FILE):
            try:
                with open(INTERACTION_FILE, "r", encoding="utf-8") as f:
                    inter_data = json.load(f)
                    last_inter_str = inter_data.get("last_interaction")
                    if last_inter_str:
                        last_interaction = datetime.fromisoformat(last_inter_str).timestamp()
            except Exception:
                pass
        idle_time = max(0.0, time.time() - last_interaction)

        silence = self._is_silence_guard_active(wm_class, title, idle_time)
        if not silence:
            silence_long_threshold = triggers_cfg["idle"].get("silence_long_threshold_seconds", 3600)
            if idle_time > silence_long_threshold:
                events.append({"type": "silence_long", "weight": triggers_cfg["weights"].get("silence_long", 1)})

        return events, silence, idle_time

    def _update_event_scores(self, events: List[Dict[str, Any]]):
        now = time.time()
        window_seconds = self.config["triggers"]["threshold"].get("window_minutes", 30) * 60
        self.state["event_scores"] = [ev for ev in self.state.get("event_scores", [])
                                      if now - ev["timestamp"] < window_seconds]
        for event in events:
            self.state["event_scores"].append({
                "timestamp": now,
                "score": event.get("weight", 0),
                "type": event["type"]
            })

    def _get_total_score(self) -> int:
        return sum(ev["score"] for ev in self.state.get("event_scores", []))

    def _should_trigger(self, total_score: int, silence_guard: bool) -> bool:
        now = time.time()
        last_init = self.state.get("last_initiative_time")
        if last_init:
            interval = self.config["triggers"]["frequency"].get("min_interval_between_initiatives_seconds", 1800)
            if now - last_init < interval:
                return False

        status = self.state.get("prompt_status", "idle")
        if status == "awaiting_response":
            return False
        if status == "ignored":
            return False
        if silence_guard:
            return False

        threshold = self.state.get("current_threshold", 3)
        if total_score >= threshold:
            return True
        return False

    def _generate_replica(self, events: List[Dict[str, Any]]) -> Optional[str]:
        prompt = (
            "События: долгое молчание пользователя.\n"
            "Сгенерируй одну короткую, естественную реплику дружелюбной девушки Джемми, "
            "чтобы начать диалог (например, спросить, как дела или предложить чай)."
        )
        try:
            response = ask_gemini(prompt)
            if isinstance(response, str) and not response.startswith("Ошибка"):
                return response.strip()
        except Exception:
            pass
        return "Лёш, ты тут? Может, сделаем небольшой перерыв?"

    def _queue_replica(self, text: str):
        if not text:
            return
        queue_file = self.config["interaction"].get("queue_file", QUEUE_FILE)
        os.makedirs(os.path.dirname(queue_file), exist_ok=True)
        try:
            with open(queue_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": time.time(), "text": text}, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка записи в очередь: {e}")

    def _trigger_voice_escalation(self):
        """
        Включает тревожный голосовой поиск (побудку):
        1. Переключает режим голосового контура на 'voice_voice'.
        2. Записывает экстренный флаг voice_force_listen.flag для микрофона.
        3. Озвучивает побудку через voice_response.json.
        """
        print("[ProactiveEngine] Внимание! Нет ответа на проактив более 5 минут. Запускаю голосовую эскалацию (побудку)!")

        # 1. Переключаем конфиг голоса на voice_voice
        voice_cfg_path = os.path.join(CONFIG_DIR, "voice_config.json")
        voice_cfg = {"mode": "voice_voice"}
        if os.path.exists(voice_cfg_path):
            try:
                with open(voice_cfg_path, "r", encoding="utf-8") as f:
                    voice_cfg = json.load(f)
                voice_cfg["mode"] = "voice_voice"
            except Exception:
                pass
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(voice_cfg_path, "w", encoding="utf-8") as f:
            json.dump(voice_cfg, f, ensure_ascii=False, indent=2)

        # 2. Ставим флаг принудительного слушания без wake-word
        with open(FORCE_LISTEN_FLAG, "w", encoding="utf-8") as f:
            f.write("force_listen")

        # 3. Генерируем и отправляем фразу побудки в TTS
        wakeup_phrases = [
            "Лёш, ты там живой вообще? Чай остыл, а в чате тишина... Отзовись!",
            "Эй, капитан! Ты куда пропал? Совсем в работу ушел?",
            "Лёш, ответь мне голосом, я волнуюсь, всё в порядке?"
        ]
        import random
        chosen_phrase = random.choice(wakeup_phrases)

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(VOICE_RESPONSE_FILE, "w", encoding="utf-8") as f:
            json.dump({"text": chosen_phrase, "timestamp": datetime.now().isoformat()}, f, ensure_ascii=False)

        # Меняем статус в стейте, чтобы не спамить повторно
        self.state["prompt_status"] = "voice_escalated"
        self._save_state()

    def tick(self):
        now = time.time()
        if self.last_tick_time and now - self.last_tick_time < 30:
            return
        self.last_tick_time = now

        if os.path.exists(REACTION_FILE):
            try:
                with open(REACTION_FILE, "r", encoding="utf-8") as f:
                    reaction_data = json.load(f)
                reaction = reaction_data.get("reaction")
                if reaction:
                    self.state["prompt_status"] = "idle"
                    self._save_state()
                os.remove(REACTION_FILE)
            except Exception:
                pass

        events, silence, idle_time = self._collect_all_events()
        self._update_event_scores(events)
        total_score = self._get_total_score()

        # Если находимся в статусе ожидания ответа (awaiting_response) более 5 минут (300 сек) — запускаем голосовую эскалацию
        if self.state.get("prompt_status") == "awaiting_response":
            last_prompt_time = self.state.get("last_prompt_time")
            if last_prompt_time:
                try:
                    last_dt = datetime.fromisoformat(last_prompt_time)
                    if (datetime.now() - last_dt).total_seconds() > 300:
                        self._trigger_voice_escalation()
                except Exception:
                    pass

        # Если ждем голоса после эскалации и прошло еще 5 минут без ответа — повторяем побудку
        elif self.state.get("prompt_status") == "voice_escalated":
            last_prompt_time = self.state.get("last_prompt_time")
            if last_prompt_time:
                try:
                    last_dt = datetime.fromisoformat(last_prompt_time)
                    if (datetime.now() - last_dt).total_seconds() > 600:
                        # Повторяем побудку и ставим флаг микрофона
                        with open(FORCE_LISTEN_FLAG, "w", encoding="utf-8") as f:
                            f.write("force_listen")
                        self.state["last_prompt_time"] = datetime.now().isoformat()
                        self._save_state()
                except Exception:
                    pass

        # Обычный проактивный триггер
        if self.state.get("prompt_status") == "idle" and self._should_trigger(total_score, silence):
            replica = self._generate_replica(events)
            if replica:
                self._queue_replica(replica)
                self.state["last_initiative_time"] = now
                self.state["prompt_status"] = "awaiting_response"
                self.state["last_prompt_time"] = datetime.now().isoformat()
                self._save_state()
                print(f"[ProactiveEngine] Отправлена проактивная реплика: {replica}")


if __name__ == "__main__":
    engine = ProactiveEngine()
    print("Proactive Engine v2.0 (с голосовой эскалацией) запущен.")
    try:
        while True:
            engine.tick()
            time.sleep(30)
    except KeyboardInterrupt:
        print("Остановлено.")
