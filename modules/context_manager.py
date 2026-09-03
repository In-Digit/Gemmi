import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Callable

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

        self.load_config()
        self.load_history()

    def load_config(self):
        """Загрузка персонажа и профиля пользователя из config.json"""
        if os.path.exists(self.config_filepath):
            try:
                with open(self.config_filepath, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[ContextManager] Ошибка загрузки config.json: {e}")

    def load_history(self):
        """Загрузка состояния диалога из history.json"""
        if os.path.exists(self.history_filepath):
            try:
                with open(self.history_filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.summary = data.get("summary", "")
                    self.history = data.get("history", [])
                    self.last_activity_time = data.get("last_activity_time", time.time())
                    self.is_paused = data.get("is_paused", False)
            except Exception as e:
                print(f"[ContextManager] Ошибка загрузки history.json: {e}")

    def save_history(self):
        """Сохранение текущего состояния диалога в history.json"""
        data = {
            "summary": self.summary,
            "history": self.history,
            "last_activity_time": self.last_activity_time,
            "is_paused": self.is_paused
        }
        try:
            with open(self.history_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ContextManager] Ошибка сохранения history.json: {e}")

    def check_timeout(self, summarize_func: Optional[Callable[[List[Dict[str, Any]]], str]] = None):
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
            self.history = []

        self.last_activity_time = now
        self.save_history()

    def set_paused(self, paused: bool = True):
        self.is_paused = paused
        self.save_history()

    def add_user_message(self, text: str):
        self.history.append({
            "role": "user",
            "parts": [{"text": text}]
        })
        self.last_activity_time = time.time()
        self.save_history()

    def add_model_message(self, text: str):
        self.history.append({
            "role": "model",
            "parts": [{"text": text}]
        })
        self.last_activity_time = time.time()
        self.save_history()

    def _build_system_instruction(self) -> str:
        """Собирает системный промпт из config.json, мгновенного слепка системы и актуального времени."""
        persona = self.config.get("persona", {})
        user_profile = self.config.get("user_profile", {})
        fiona = self.config.get("fiona", {})

        base_prompt = persona.get("system_prompt", "Ты — Джеми, умный собеседник.")

        profile_context = "\n\n--- Информация о собеседнике и его окружении ---\n"
        if user_profile:
            profile_context += f"Профиль пользователя: {json.dumps(user_profile, ensure_ascii=False)}\n"
        if fiona:
            profile_context += f"Автомобиль (Фиона): {json.dumps(fiona, ensure_ascii=False)}\n"

        # Динамический слепок среды
        try:
            from modules.instant_snapshot import capture_snapshot
            snapshot = capture_snapshot()
            snapshot_context = f"\n--- Мгновенный слепок системы ---\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n"
        except Exception as e:
            snapshot_context = f"\n--- Мгновенный слепок системы ---\n(Слепок недоступен: {e})\n"

        time_info = f"\n[{get_moscow_time_str()}]"

        return f"{base_prompt}{profile_context}{snapshot_context}{time_info}"

    def get_payload(self) -> Dict[str, Any]:
        full_system_instruction = self._build_system_instruction()

        if self.summary:
            full_system_instruction += f"\n\nКонтекст предыдущих разговоров за день:\n{self.summary}"

        payload = {
            "contents": self.history,
            "systemInstruction": {
                "parts": [{"text": full_system_instruction}]
            }
        }
        return payload

    def clear_all(self):
        self.summary = ""
        self.history = []
        self.is_paused = False
        self.last_activity_time = time.time()
        self.save_history()
