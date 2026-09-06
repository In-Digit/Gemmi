#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль управления долговременным профилем пользователя (user_facts).
Периодически анализирует историю диалога, извлекает факты о пользователе,
семье, предпочтениях и т.д. и дополняет user_profile в config.json.

Факты сохраняются в виде объектов:
    {
        "text": "...",
        "sensitivity": "public" | "private"
    }

Приватные факты не будут передаваться в контекст при включённом режиме «Мы не одни».
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from modules.llm_client import ask_gemini

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
FACTS_HISTORY_FILE = os.path.expanduser("~/gemini_companion/data/facts_analysis_state.json")

# Стоп-слова для автоматической пометки приватных фактов
PRIVATE_MARKERS = [
    "колони", "тюрьм", "пытк", "гангрен", "позвоночник", "здоровь", "медицин",
    "паническ", "дочь", "потер", "смерт", "развод", "арми", "дедовщин",
    "инвалид", "лечен", "операци", "болезн", "травм"
]


class UserFactsManager:
    """
    Менеджер долговременных фактов о пользователе.
    Запускается в фоне (или по вызову) для анализа истории и обновления профиля.
    """

    def __init__(self, config_path: str = CONFIG_FILE, history_path: Optional[str] = None):
        self.config_path = config_path
        self.history_path = history_path if history_path else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "history.json"
        )
        self.state = self._load_analysis_state()
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[UserFacts] Ошибка загрузки config.json: {e}")
        return {}

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[UserFacts] Ошибка сохранения config.json: {e}")

    def _load_analysis_state(self) -> Dict[str, Any]:
        if os.path.exists(FACTS_HISTORY_FILE):
            try:
                with open(FACTS_HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_analyzed_index": 0,
            "last_analysis_time": None
        }

    def _save_analysis_state(self):
        os.makedirs(os.path.dirname(FACTS_HISTORY_FILE), exist_ok=True)
        try:
            with open(FACTS_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[UserFacts] Ошибка сохранения состояния: {e}")

    def _load_history(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("history", [])
            except Exception as e:
                print(f"[UserFacts] Ошибка загрузки истории: {e}")
        return []

    def _classify_sensitivity(self, fact_text: str, llm_sensitivity: Optional[str] = None) -> str:
        """
        Определяет чувствительность факта.
        Если LLM уже пометил как private, оставляем.
        Если в тексте есть маркеры приватности, принудительно private.
        Иначе public.
        """
        if llm_sensitivity == "private":
            return "private"
        lower = fact_text.lower()
        for marker in PRIVATE_MARKERS:
            if marker in lower:
                return "private"
        return "public"

    def _extract_facts(self, dialog_text: str) -> List[Dict[str, str]]:
        """
        Отправляет диалог в LLM с просьбой извлечь факты и указать sensitivity.
        Возвращает список объектов {text, sensitivity}.
        """
        prompt = (
            "Проанализируй следующий диалог и выдели из него факты о пользователе, "
            "его семье, предпочтениях, привычках, важных событиях и т.п. "
            "Каждый факт верни в формате JSON-объекта с полями:\n"
            "- \"text\": короткое утверждение от третьего лица (например, 'Пользователь любит кофе');\n"
            "- \"sensitivity\": \"public\", если факт можно упоминать в любом разговоре, "
            "или \"private\", если он касается личной истории, здоровья, тяжёлых событий, "
            "прошлого, финансовых трудностей и т.п.\n"
            "Верни список таких объектов в формате JSON, без дополнительных пояснений.\n\n"
            f"Диалог:\n{dialog_text}"
        )
        response = ask_gemini(prompt, timeout=30)
        if response.startswith("Ошибка:"):
            return []
        try:
            # Ищем JSON в ответе
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                facts = json.loads(json_match.group(0))
                if isinstance(facts, list):
                    result = []
                    for item in facts:
                        if isinstance(item, dict) and "text" in item:
                            text = item["text"].strip()
                            sensitivity = item.get("sensitivity", "public")
                            final_sens = self._classify_sensitivity(text, sensitivity)
                            result.append({"text": text, "sensitivity": final_sens})
                    return result
        except Exception as e:
            print(f"[UserFacts] Ошибка парсинга фактов: {e}")
        return []

    def _merge_facts(self, new_facts: List[Dict[str, str]]):
        """
        Добавляет новые факты в user_profile['learned_facts'] в config.json,
        избегая дубликатов по тексту.
        """
        user_profile = self.config.get("user_profile", {})
        learned = user_profile.get("learned_facts", [])
        existing_texts = [f.get("text", "") for f in learned if isinstance(f, dict)]
        for fact in new_facts:
            if fact["text"] not in existing_texts:
                learned.append(fact)
                existing_texts.append(fact["text"])
        user_profile["learned_facts"] = learned
        self.config["user_profile"] = user_profile
        self._save_config()

    def run_analysis_if_needed(self, force: bool = False):
        """
        Проверяет условия для запуска анализа:
        - Если с последнего анализа прошло более часа ИЛИ
        - Накопилось больше 100 новых сообщений с последнего анализа.
        Если условия выполнены, запускает извлечение фактов.
        """
        history = self._load_history()
        total_messages = len(history)
        if total_messages == 0:
            return

        last_index = self.state.get("last_analyzed_index", 0)
        new_messages_count = total_messages - last_index
        last_time = self.state.get("last_analysis_time")
        now = datetime.now()

        time_condition = False
        if last_time:
            try:
                last_dt = datetime.fromisoformat(last_time)
                time_condition = (now - last_dt).total_seconds() > 3600
            except Exception:
                time_condition = True
        else:
            time_condition = True

        count_condition = new_messages_count >= 100

        if not force and not (time_condition or count_condition):
            return

        # Собираем текст новых сообщений
        new_dialog = []
        for msg in history[last_index:]:
            role = "Пользователь" if msg["role"] == "user" else "Ассистент"
            text = msg.get("parts", [{}])[0].get("text", "")
            if text:
                new_dialog.append(f"{role}: {text}")
        dialog_text = "\n".join(new_dialog)

        if not dialog_text.strip():
            return

        facts = self._extract_facts(dialog_text)
        if facts:
            self._merge_facts(facts)

        self.state["last_analyzed_index"] = total_messages
        self.state["last_analysis_time"] = now.isoformat()
        self._save_analysis_state()


if __name__ == "__main__":
    manager = UserFactsManager()
    manager.run_analysis_if_needed(force=True)
    print("Анализ завершён. Проверьте user_profile в config.json.")
