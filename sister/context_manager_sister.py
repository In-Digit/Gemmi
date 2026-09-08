# /home/mrak/gemini_companion/sister/context_manager_sister.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Контекст-менеджер для «Старшей сестры».
Хранит и передает историю диалога, автоматически находит и внедряет
аппаратные спецификации плат (BOARD.md), рассчитывает здоровье контекста
и предоставляет детальную разбивку токенов по компонентам.
Поддерживает web_search.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from modules.web_search import search_web, fetch_page_content

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")


def get_moscow_time_str() -> str:
    tz_mow = timezone(timedelta(hours=3))
    now = datetime.now(tz_mow)
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return f"Текущие дата и время (Москва): {now.strftime('%d.%m.%Y %H:%M')}, {days[now.weekday()]}."


class SisterContextManager:
    def __init__(self, config_filepath: str = CONFIG_FILE, history_filepath: str = HISTORY_FILE):
        self.config_filepath = config_filepath
        self.history_filepath = history_filepath
        self.config: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.load_config()
        self.load_history()

    def load_config(self):
        if os.path.exists(self.config_filepath):
            try:
                with open(self.config_filepath, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[SisterContext] Ошибка загрузки config.json: {e}")

    def load_history(self):
        if os.path.exists(self.history_filepath):
            try:
                with open(self.history_filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.history = data.get("history", [])
            except Exception as e:
                print(f"[SisterContext] Ошибка загрузки history.json: {e}")

    def save_history(self):
        data = {"history": self.history}
        try:
            with open(self.history_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[SisterContext] Ошибка сохранения history.json: {e}")

    def add_user_message(self, text: str):
        self.history.append({"role": "user", "parts": [{"text": text}]})
        self.save_history()

    def add_model_message(self, text: str):
        self.history.append({"role": "model", "parts": [{"text": text}]})
        self.save_history()

    def clear_all(self):
        self.history = []
        self.save_history()

    def find_board_files(self, project_folders: Optional[List[str]] = None,
                         selected_files: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
        board_data = []
        searched_dirs = set()

        if project_folders:
            for folder in project_folders:
                if folder and os.path.exists(folder):
                    searched_dirs.add(os.path.abspath(folder))

        if selected_files:
            for file_info in selected_files:
                fpath = file_info.get("path", "")
                if fpath:
                    dname = os.path.dirname(os.path.abspath(fpath))
                    searched_dirs.add(dname)

        for base_dir in searched_dirs:
            try:
                for root, dirs, files in os.walk(base_dir):
                    rel_path = os.path.relpath(root, base_dir)
                    if rel_path.count(os.sep) > 2:
                        continue
                    for fname in files:
                        if fname.lower() == "board.md":
                            f_path = os.path.join(root, fname)
                            try:
                                with open(f_path, "r", encoding="utf-8", errors="ignore") as f:
                                    content = f.read()
                                board_data.append({
                                    "folder": base_dir,
                                    "filename": fname,
                                    "path": f_path,
                                    "content": content
                                })
                            except Exception:
                                pass
            except Exception:
                pass

        return board_data

    def _build_system_instruction(self, selected_files: Optional[List[Dict[str, str]]] = None,
                                  project_folders: Optional[List[str]] = None) -> str:
        persona = self.config.get("persona", {})
        base_prompt = persona.get("system_prompt", "Ты — Старшая сестра, ИИ-помощник для разработки.")

        time_info = f"\n[{get_moscow_time_str()}]"

        web_instruction = (
            "\n\n--- Поиск в интернете ---\n"
            "Если нужна актуальная документация или свежая информация, используй блок:\n"
            "```web_search\nзапрос или URL\n```\n"
            "Для чтения конкретной страницы укажи её URL.\n"
        )

        board_instruction = ""
        boards = self.find_board_files(project_folders=project_folders, selected_files=selected_files)
        if boards:
            board_instruction = "\n\n--- АППАРАТНАЯ СПЕЦИФИКАЦИЯ И ОГРАНИЧЕНИЯ ПЛАТЫ (BOARD.md) ---\n"
            board_instruction += (
                "ВНИМАНИЕ: Ниже приведено жесткое описание целевого железа, распиновки, чипа и версии ESP-IDF.\n"
                "Строго следуй указанной конфигурации пинов, интерфейсов и ограничений памяти.\n\n"
            )
            for b in boards:
                board_instruction += f"### Паспорт модуля: `{b['folder']}/{b['filename']}`\n```markdown\n{b['content']}\n```\n\n"

        files_instruction = ""
        if selected_files:
            files_instruction = "\n\n--- Выбранные файлы проекта (актуальная версия с диска) ---\n"
            for file_info in selected_files:
                files_instruction += f"\n### Файл: {file_info['path']}\n```\n{file_info['content']}\n```\n"

        return f"{base_prompt}{time_info}{web_instruction}{board_instruction}{files_instruction}"

    def get_payload(self, selected_files: Optional[List[Dict[str, str]]] = None,
                    project_folders: Optional[List[str]] = None) -> Dict[str, Any]:
        system_instruction = self._build_system_instruction(
            selected_files=selected_files,
            project_folders=project_folders
        )
        payload = {
            "contents": self.history,
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            }
        }
        return payload

    def get_estimated_tokens(self, selected_files: Optional[List[Dict[str, str]]] = None,
                             project_folders: Optional[List[str]] = None) -> int:
        payload = self.get_payload(selected_files=selected_files, project_folders=project_folders)
        raw_text = json.dumps(payload, ensure_ascii=False)
        return max(1, int(len(raw_text) / 2.2))

    def get_token_breakdown(self, selected_files: Optional[List[Dict[str, str]]] = None,
                            project_folders: Optional[List[str]] = None) -> Dict[str, int]:
        """Возвращает детальную разбивку токенов по частям контекста."""
        # 1. Базовый системный промпт и время
        base_inst = self._build_system_instruction(selected_files=None, project_folders=None)
        base_tokens = max(1, int(len(base_inst) / 2.2))

        # 2. BOARD.md
        boards = self.find_board_files(project_folders=project_folders, selected_files=selected_files)
        board_text = "".join([b["content"] for b in boards])
        board_tokens = max(0, int(len(board_text) / 2.2))

        # 3. Выбранные файлы проекта
        files_text = "".join([f.get("content", "") for f in (selected_files or [])])
        files_tokens = max(0, int(len(files_text) / 2.2))

        # 4. История диалога
        history_text = json.dumps(self.history, ensure_ascii=False)
        history_tokens = max(0, int(len(history_text) / 2.2))

        return {
            "system": base_tokens,
            "board": board_tokens,
            "files": files_tokens,
            "history": history_tokens,
            "total": base_tokens + board_tokens + files_tokens + history_tokens
        }

    def get_context_health(self, selected_files: Optional[List[Dict[str, str]]] = None,
                           project_folders: Optional[List[str]] = None,
                           max_tokens: int = 2000000) -> Dict[str, Any]:
        tokens = self.get_estimated_tokens(selected_files=selected_files, project_folders=project_folders)
        percentage = min(100.0, (tokens / max_tokens) * 100)

        if tokens < 100000:
            status = "Бодра и свежа ⚡"
        elif tokens < 500000:
            status = "В отличном тонусе 👍"
        elif tokens < 1200000:
            status = "В плотной работе ☕"
        elif tokens < 1700000:
            status = "Контекст перегружен ⚠️"
        else:
            status = "Нужен новый чат / резюме 😴"

        return {
            "tokens": tokens,
            "max_tokens": max_tokens,
            "percentage": percentage,
            "status": status
        }
