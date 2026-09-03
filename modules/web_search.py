import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

def search_web(query: str, max_results: int = 5) -> list:
    """Выполняет поиск в DuckDuckGo и возвращает краткие результаты."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return results
    except Exception as e:
        return [{"error": f"Ошибка поиска: {str(e)}"}]

def fetch_page_content(url: str, max_chars: int = 4000) -> str:
    """Загружает страницу по URL и очищает её от мусора для анализа."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Удаляем навигацию, скрипты и стили
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()

        text = soup.get_text(separator="\n")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        clean_text = "\n".join(chunk for chunk in chunks if chunk)

        return clean_text[:max_chars]
    except Exception as e:
        return f"Ошибка чтения страницы: {str(e)}"
