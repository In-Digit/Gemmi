import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DATA_DIR = os.path.expanduser("~/gemini_companion/data")
NOTEBOOK_FILE = os.path.join(DATA_DIR, "notebook.json")


class NotebookManager:
    """
    Модуль управления блокнотом-справочником (notebook.json).
    Не загружается в контекст по умолчанию.
    Вызывается ТОЛЬКО при наличии точных ключевых фраз:
    - Запись: "запомни на будущее" или "запомни на потом"
    - Чтение: "вспомни из прошлого"
    """

    def __init__(self, filepath: str = NOTEBOOK_FILE):
        self.filepath = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        self.notes: List[Dict[str, Any]] = self._load_notes()

    def _load_notes(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[NotebookManager] Ошибка чтения {self.filepath}: {e}")
                return []
        return []

    def _save_notes(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.notes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[NotebookManager] Ошибка сохранения {self.filepath}: {e}")

    @staticmethod
    def is_save_triggered(text: str) -> bool:
        """Проверка наличия точных фраз для записи."""
        lowered = text.lower()
        return "запомни на будущее" in lowered or "запомни на потом" in lowered

    @staticmethod
    def is_recall_triggered(text: str) -> bool:
        """Проверка наличия точной фразы для считывания из прошлого."""
        lowered = text.lower()
        return "вспомни из прошлого" in lowered

    def add_note(self, content: str, source_prompt: str = ""):
        """Сохранение записи в блокнот."""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": content,
            "source_prompt": source_prompt
        }
        self.notes.append(entry)
        self._save_notes()

    def get_all_notes_formatted(self) -> str:
        """Форматирует записи из блокнота для подмешивания в контекст по запросу."""
        if not self.notes:
            return "Блокнот пока пуст."

        formatted = ["--- Записи из блокнота-справочника (notebook.json) ---"]
        for idx, note in enumerate(self.notes, 1):
            formatted.append(f"Запись №{idx} [{note['timestamp']}]:\n{note['content']}\n")
        return "\n".join(formatted)
