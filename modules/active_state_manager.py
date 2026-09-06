import json
import os
from datetime import datetime
from typing import Dict, Any

DATA_DIR = os.path.expanduser("~/gemini_companion/data")
ACTIVE_STATE_FILE = os.path.join(DATA_DIR, "active_state.json")


class ActiveStateManager:
    """
    Модуль управления скользящим суточным контекстом (active_state.json).
    Хранит краткий дайджест текущего дня: задачи, состояние, фоновые процессы.
    Автоматически сбрасывается/обновляется при смене суток.
    """

    def __init__(self, filepath: str = ACTIVE_STATE_FILE):
        self.filepath = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        self.state: Dict[str, Any] = self._load_state()

    def _get_today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _load_state(self) -> Dict[str, Any]:
        default_state = {
            "date": self._get_today_str(),
            "daily_summary": "",
            "active_tasks": [],
            "last_updated": datetime.now().isoformat()
        }

        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Если наступил новый день — сбрасываем дневной контекст
                    if data.get("date") != self._get_today_str():
                        return default_state
                    return data
            except Exception as e:
                print(f"[ActiveStateManager] Ошибка чтения {self.filepath}: {e}")
                return default_state

        return default_state

    def save_state(self):
        self.state["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ActiveStateManager] Ошибка сохранения {self.filepath}: {e}")

    def update_summary(self, summary_text: str):
        """Обновляет суточную выжимку."""
        self.state["date"] = self._get_today_str()
        self.state["daily_summary"] = summary_text
        self.save_state()

    def get_context_formatted(self) -> str:
        """Возвращает отформатированный суточный контекст для подмешивания в промпт."""
        # Проверяем не сменились ли сутки
        if self.state.get("date") != self._get_today_str():
            self.state = self._load_state()

        summary = self.state.get("daily_summary", "").strip()
        if not summary:
            return ""

        return f"--- Суточный контекст дня ({self.state['date']}) ---\n{summary}"
