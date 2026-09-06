#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль llm_client.py
Отвечает за взаимодействие с API Gemini. Поддерживает каскад моделей
и опциональный callback для отображения прогресса (например, в UI).
"""

import os
import requests
from typing import Union, Dict, Any, Callable, Optional

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip('/')

# Каскад реальных доступных моделей с твоего API-сервера
MODELS_CASCADE = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


def ask_gemini(payload_or_prompt: Union[str, Dict[str, Any]],
               timeout: int = 15,
               progress_callback: Optional[Callable[[str, str], None]] = None) -> str:
    """
    Принимает либо строку (простой промпт), либо словарь (готовый payload с историей).
    Последовательно опрашивает каскад доступных моделей до первого успешного ответа.

    Если передан progress_callback, он будет вызываться на каждом шаге:
        progress_callback(model_name, status_text)
    где model_name — текущая модель, status_text — описание статуса запроса.
    """
    api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Ошибка: Не найден GEMINI_API_KEY в переменных окружения."

    if isinstance(payload_or_prompt, str):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": payload_or_prompt}]}]
        }
    else:
        payload = payload_or_prompt

    headers = {"Content-Type": "application/json"}

    for model in MODELS_CASCADE:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"

        if progress_callback:
            progress_callback(model, "отправка запроса...")

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    text = "".join(text_parts).strip()
                    if text:
                        if progress_callback:
                            progress_callback(model, "успех")
                        return text
                    else:
                        if progress_callback:
                            progress_callback(model, "ответ пустой, пробуем следующую")
                        continue
                else:
                    if progress_callback:
                        progress_callback(model, "нет кандидатов, пробуем следующую")
                    continue
            else:
                if progress_callback:
                    progress_callback(model, f"ошибка HTTP {response.status_code}")
                continue

        except requests.exceptions.RequestException as e:
            if progress_callback:
                progress_callback(model, f"сбой сети/таймаут ({type(e).__name__})")
            continue

    return "Ошибка: ни одна из моделей в каскаде не ответила."
