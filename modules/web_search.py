# modules/web_search.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль для поиска в сети и извлечения контента веб-страниц.
Реализует каскадный отказоустойчивый поиск (DuckDuckGo -> SearXNG -> Fallback),
который стабильно работает под VPN и через приватные DNS.
Предоставляет чистые данные для фонового использования ассистентом.
"""

import re
import urllib.parse
from typing import List, Dict, Any
import requests
from bs4 import BeautifulSoup

# Попытка импорта DuckDuckGo
try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

# Список стабильных публичных инстансов SearXNG с поддержкой JSON API
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.ononoki.org",
    "https://baresearch.org",
    "https://searx.tiekoetter.com"
]

# Стандартный пул реалистичных браузерных заголовков для обхода 403 Forbidden
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Upgrade-Insecure-Requests": "1"
}


def _search_ddg(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Попытка поиска через библиотеку DuckDuckGo с региональными параметрами."""
    if DDGS is None:
        return []
    try:
        with DDGS(timeout=7) as ddgs:
            # region="ru-ru" критически важен при работе под зарубежным VPN
            raw_results = list(ddgs.text(query, region="ru-ru", safesearch="moderate", max_results=max_results))
            normalized = []
            for item in raw_results:
                title = item.get("title", "").strip()
                href = item.get("href", "").strip()
                body = item.get("body", "").strip()
                if title and href:
                    normalized.append({"title": title, "href": href, "body": body})
            return normalized
    except Exception:
        return []


def _search_searxng(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Запасной поиск через сеть публичных инстансов SearXNG (отлично дружат с VPN)."""
    params = {
        "q": query,
        "format": "json",
        "language": "ru",
        "safesearch": 1
    }
    for base_url in SEARXNG_INSTANCES:
        try:
            url = f"{base_url.rstrip('/')}/search"
            resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    normalized = []
                    for item in results[:max_results]:
                        title = item.get("title", "").strip()
                        href = item.get("url", "").strip()
                        body = item.get("content", "").strip()
                        if title and href:
                            normalized.append({"title": title, "href": href, "body": body})
                    if normalized:
                        return normalized
        except Exception:
            continue
    return []


def _get_direct_weather(query: str) -> List[Dict[str, str]]:
    """
    Специализированный фоллбэк на случай поиска погоды.
    Забирает детальный текстовый прогноз на сегодня и завтра без лишнего мусора.
    """
    query_lower = query.lower()
    if "погод" not in query_lower and "weather" not in query_lower:
        return []

    # Пытаемся определить город или используем домашний Ломоносов
    city = "Lomonosov"
    if "питер" in query_lower or "санкт-петербург" in query_lower:
        city = "Saint Petersburg"
    elif "москв" in query_lower:
        city = "Moscow"

    try:
        # Запрашиваем компактный прогноз на 2 дня в чистом текстовом виде
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=4&lang=ru"
        resp_current = requests.get(url, headers={"User-Agent": "curl/7.68.0"}, timeout=5)

        # Дополнительно берем расширенный прогноз с разбивкой по времени суток
        url_detailed = f"https://wttr.in/{urllib.parse.quote(city)}?0?T&lang=ru"
        resp_detailed = requests.get(url_detailed, headers={"User-Agent": "curl/7.68.0"}, timeout=5)

        weather_lines = []
        if resp_current.status_code == 200 and resp_current.text.strip():
            weather_lines.append(f"Сейчас в районе ({city}): {resp_current.text.strip()}")

        if resp_detailed.status_code == 200 and resp_detailed.text.strip():
            # Очищаем ANSI-цвета, если сервис вернул раскраску терминала
            raw_text = re.sub(r'\x1b\[[0-9;]*m', '', resp_detailed.text)
            # Берем первые 15 строк прогноза (суть дня)
            summary_lines = [l for l in raw_text.splitlines() if l.strip()][:15]
            weather_lines.append("\n".join(summary_lines))

        if weather_lines:
            return [{
                "title": f"Фактический прогноз погоды: {city}",
                "href": f"https://wttr.in/{urllib.parse.quote(city)}",
                "body": "\n\n".join(weather_lines)
            }]
    except Exception:
        pass
    return []


def search_web(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Основная функция веб-поиска с трехуровневым каскадом.
    Гарантированно возвращает список словарей формата {'title', 'href', 'body'}
    либо словарь с сообщением об ошибке в первом элементе.
    """
    clean_query = query.strip()
    if not clean_query:
        return [{"error": "Пустой поисковый запрос."}]

    # Если запрос явно про погоду, сначала проверяем точечную метеорологию
    query_lower = clean_query.lower()
    if "погод" in query_lower or "температур" in query_lower:
        weather_res = _get_direct_weather(clean_query)
        if weather_res:
            return weather_res

    # Уровень 1: DuckDuckGo
    results = _search_ddg(clean_query, max_results=max_results)
    if results:
        return results

    # Уровень 2: SearXNG (метапоиск без капчи)
    results = _search_searxng(clean_query, max_results=max_results)
    if results:
        return results

    # Уровень 3: Запасной вызов погоды (если ключевые слова были скрыты)
    results = _get_direct_weather(clean_query)
    if results:
        return results

    return [{"error": f"Ничего не найдено по запросу: '{clean_query}'. Сеть или сервисы временно недоступны."}]


def fetch_page_content(url: str, max_chars: int = 4000) -> str:
    """
    Загружает содержимое страницы по прямой ссылке, вычищает разметку,
    скрипты, стили и возвращает чистый текст для анализа моделью.
    """
    clean_url = url.strip()
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        return f"Ошибка: некорректный URL '{clean_url}'."

    try:
        response = requests.get(clean_url, headers=DEFAULT_HEADERS, timeout=10)
        response.raise_for_status()

        # Автоматическое определение кодировки для кириллических сайтов
        if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
            response.encoding = response.apparent_encoding or 'utf-8'

        soup = BeautifulSoup(response.text, "html.parser")

        # Удаляем мусорные теги
        for tag in soup(["script", "style", "header", "footer", "nav", "aside", "noscript", "form", "svg"]):
            tag.decompose()

        text = soup.get_text(separator="\n")

        # Очистка строк от лишних пробелов и пустых переводов
        cleaned_lines = []
        for line in text.splitlines():
            line_str = line.strip()
            if line_str:
                cleaned_lines.append(line_str)

        full_text = "\n".join(cleaned_lines)
        if not full_text:
            return "Страница загружена, но полезного текстового содержимого не обнаружено."

        return full_text[:max_chars]

    except requests.exceptions.Timeout:
        return f"Ошибка чтения страницы: истекло время ожидания ответа от сервера ({clean_url})."
    except requests.exceptions.HTTPError as e:
        return f"Ошибка HTTP при чтении страницы: {e.response.status_code} ({clean_url})."
    except Exception as e:
        return f"Ошибка чтения страницы: {str(e)}"
