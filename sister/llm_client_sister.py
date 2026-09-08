#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LLM-клиент для «Старшей сестры».
Использует только текстовые генеративные модели (без image/tts/special).
Поддерживает флаг use_pro_only: если ни одна PRO-модель не ответила,
автоматически переключается на полный пул и повторяет попытку.
"""

import os
import requests
from typing import Union, Dict, Any, Callable, Optional, Tuple

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip('/')

# Все текстовые генеративные модели (без специализированных)
ALL_TEXT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it"
]

# Только Pro-модели (текстовые)
PRO_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-2.5-pro",
    "gemini-pro-latest"
]


def ask_gemini_sister(payload_or_prompt: Union[str, Dict[str, Any]],
                      timeout: int = 30,
                      use_pro_only: bool = False,
                      progress_callback: Optional[Callable[[str, str], None]] = None) -> Tuple[str, bool]:
    """
    Отправляет запрос к Gemini с перебором моделей.
    Если use_pro_only=True и ни одна PRO-модель не ответила, автоматически
    происходит fallback на ALL_TEXT_MODELS.
    Возвращает кортеж: (текст_ответа, был_ли_автосброс_pro).
    """
    api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Ошибка: Не найден GEMINI_API_KEY в переменных окружения.", False

    if isinstance(payload_or_prompt, str):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": payload_or_prompt}]}]
        }
    else:
        payload = payload_or_prompt

    headers = {"Content-Type": "application/json"}

    # Определяем порядок перебора
    fallback_occurred = False
    models_to_try = PRO_MODELS if use_pro_only else ALL_TEXT_MODELS

    def _try_fetch(model_list):
        for model in model_list:
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
                                progress_callback(model, "ответ пустой")
                    else:
                        if progress_callback:
                            progress_callback(model, "нет кандидатов")
                else:
                    if progress_callback:
                        progress_callback(model, f"ошибка HTTP {response.status_code}")
            except requests.exceptions.RequestException as e:
                if progress_callback:
                    progress_callback(model, f"сбой ({type(e).__name__})")
            continue
        return None

    # Пытаемся получить ответ на текущем списке моделей
    result_text = _try_fetch(models_to_try)

    # Если стоял флаг use_pro_only, но PRO-модели не ответили — делаем fallback на все модели
    if not result_text and use_pro_only:
        fallback_occurred = True
        # Оставляем только те модели, которых не было в PRO_MODELS, чтобы не дублировать попытки
        remaining_models = [m for m in ALL_TEXT_MODELS if m not in PRO_MODELS]
        result_text = _try_fetch(remaining_models)

    if result_text:
        return result_text, fallback_occurred

    return "Ошибка: ни одна из моделей не ответила.", fallback_occurred
