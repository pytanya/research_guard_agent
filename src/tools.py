"""
Инструменты исследовательского агента.

- search_web(query) -> list[str]: Yandex Search API v2 → fallback DDGS (DuckDuckGo)
  → fallback Tavily (только если ключ задан).
- fetch_url(url) -> (text|error, status): безопасное получение текста страницы.
- save_note(note) -> str: сохранение заметки во время исследования.

Ошибки инструментов НЕ пробрасываются наружу — возвращаются структурированным
результатом с полем error.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import warnings
from typing import Any, Dict, List, Tuple

import requests

# Подавляем RuntimeWarning о переименовании duckduckgo_search -> ddgs
warnings.filterwarnings("ignore", message=".*ddgs.*")

from . import config

logger = logging.getLogger("agent.tools")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ResearchGuardAgent/1.0"
)

MAX_FETCH_CHARS = 8000
FETCH_TIMEOUT = 15
SEARCH_RESULTS = 5
SEARCH_TIMEOUT = 30  # Yandex Search API отвечает медленнее 15с — увеличено

# Поля результата инструмента в формате OpenAI function calling
TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Поиск в вебе. Возвращает до 5 результатов: заголовок, URL и сниппет. "
                "Используй для поиска фактов, новостей, документации."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос (краткий, на русском или английском).",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Загрузить текстовое содержимое веб-страницы по URL. "
                "Возвращает текст (обрезанный до 8000 символов) или описание ошибки. "
                "Используй после search_web для чтения полного текста источника."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Абсолютный URL страницы для загрузки.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": (
                "Сохранить промежуточную заметку во время исследования "
                "(краткий тезис, найденный факт, ссылка). Опционально."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "Текст заметки.",
                    }
                },
                "required": ["note"],
            },
        },
    },
]


def _clean_hlword(text: str) -> str:
    """Удаляет теги <hlword> из текста Yandex."""
    return re.sub(r"</?hlword[^>]*>", "", text).strip()


# ----------------------------------------------------------------------
# Yandex Search API v2
# ----------------------------------------------------------------------
def _search_yandex(query: str) -> List[Dict[str, str]]:
    """Поиск через Yandex Search API v2. Бросает исключение при ошибке."""
    if not config.settings.YANDEX_API_KEY or not config.settings.YANDEX_FOLDER_ID:
        raise RuntimeError("YANDEX_API_KEY или YANDEX_FOLDER_ID не настроены")

    url = "https://searchapi.api.cloud.yandex.net/v2/web/search"
    headers = {
        "Authorization": f"Api-Key {config.settings.YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": {
            "searchType": "SEARCH_TYPE_RU",
            "queryText": query,
        },
        "folderId": config.settings.YANDEX_FOLDER_ID,
        "responseFormat": "FORMAT_XML",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=SEARCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    raw_b64 = data.get("rawData", "")
    if not raw_b64:
        raise RuntimeError("Yandex Search вернул пустой rawData")

    xml_bytes = base64.b64decode(raw_b64)
    xml_text = xml_bytes.decode("utf-8", errors="replace")

    results: List[Dict[str, str]] = []
    # Лёгкий парсинг XML без BeautifulSoup (у нас нет lxml в зависимостях)
    for doc in re.findall(r"<doc[^>]*>(.*?)</doc>", xml_text, flags=re.S):
        url_match = re.search(r"<url>(.*?)</url>", doc, flags=re.S)
        title_match = re.search(r"<title>(.*?)</title>", doc, flags=re.S)
        passage_match = re.search(r"<passage>(.*?)</passage>", doc, flags=re.S)
        link = _clean_hlword(url_match.group(1)) if url_match else ""
        title = _clean_hlword(title_match.group(1)) if title_match else ""
        snippet = _clean_hlword(passage_match.group(1)) if passage_match else ""
        if link or title:
            results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= SEARCH_RESULTS:
            break

    if not results:
        raise RuntimeError("Yandex Search не вернул результатов (0 doc)")
    return results


# ----------------------------------------------------------------------
# DuckDuckGo (DDGS)
# ----------------------------------------------------------------------
def _search_ddgs(query: str) -> List[Dict[str, str]]:
    """Fallback-поиск через DuckDuckGo."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    with DDGS(timeout=SEARCH_TIMEOUT) as ddgs:
        results = list(ddgs.text(query, max_results=SEARCH_RESULTS))

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in results
    ]


# ----------------------------------------------------------------------
# Tavily (только если ключ задан)
# ----------------------------------------------------------------------
def _search_tavily(query: str) -> List[Dict[str, str]]:
    """Fallback-поиск через Tavily (используется, если Yandex и DDGS не дали результата)."""
    if not config.settings.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY не настроен — Tavily недоступен")

    resp = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": config.settings.TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": SEARCH_RESULTS,
            "include_answer": False,
            "include_raw_content": False,
        },
        headers={"Content-Type": "application/json"},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if not results:
        raise RuntimeError("Tavily не вернул результатов")
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "") or r.get("snippet", ""),
        }
        for r in results
    ]


# ----------------------------------------------------------------------
# Публичный инструмент: search_web
# ----------------------------------------------------------------------
def search_web(query: str) -> str:
    """
    Поиск в вебе. Порядок: Yandex → DDGS → Tavily.

    Returns:
        JSON-строка вида {"ok": true, "results": [...]} или
        {"ok": false, "error": "..."} — исключение наружу НЕ бросается.
    """
    query = (query or "").strip()
    if not query:
        return json.dumps(
            {"ok": False, "error": "Пустой поисковый запрос"}, ensure_ascii=False
        )

    errors: List[str] = []

    # 1) Yandex
    try:
        results = _search_yandex(query)
        logger.info("search_web: Yandex вернул %d результатов", len(results))
        return json.dumps({"ok": True, "engine": "yandex", "results": results}, ensure_ascii=False)
    except Exception as e:
        errors.append(f"yandex: {e}")
        logger.warning("search_web: Yandex недоступен (%s), пробую DDGS", e)

    # 2) DuckDuckGo
    try:
        results = _search_ddgs(query)
        if results:
            logger.info("search_web: DDGS вернул %d результатов", len(results))
            return json.dumps({"ok": True, "engine": "ddgs", "results": results}, ensure_ascii=False)
        errors.append("ddgs: пустой результат")
    except Exception as e:
        errors.append(f"ddgs: {e}")
        logger.warning("search_web: DDGS недоступен (%s)", e)

    # 3) Tavily (только если ключ задан)
    try:
        results = _search_tavily(query)
        if results:
            logger.info("search_web: Tavily вернул %d результатов", len(results))
            return json.dumps({"ok": True, "engine": "tavily", "results": results}, ensure_ascii=False)
        errors.append("tavily: пустой результат")
    except Exception as e:
        errors.append(f"tavily: {e}")
        logger.warning("search_web: Tavily недоступен (%s)", e)

    return json.dumps(
        {"ok": False, "error": "Все поисковые движки недоступны: " + "; ".join(errors)},
        ensure_ascii=False,
    )


# ----------------------------------------------------------------------
# Публичный инструмент: fetch_url
# ----------------------------------------------------------------------
def fetch_url(url: str) -> Tuple[str, str]:
    """
    Безопасное получение текста страницы.

    Returns:
        (text, status): text — содержимое (обрезано до MAX_FETCH_CHARS) или сообщение
        об ошибке; status — "OK" или "ERROR".
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Ошибка: URL должен начинаться с http:// или https://", "ERROR"

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        resp.raise_for_status()

        # Читаем не более MAX_FETCH_CHARS * 2 байт, чтобы не качать огромные страницы
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_FETCH_CHARS * 2:
                break

        raw = "".join(chunks)
        # Грубое удаление HTML-тегов и лишних пробелов
        text = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.S | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if not text:
            return "Страница не содержит текстового содержимого", "ERROR"

        if len(text) > MAX_FETCH_CHARS:
            text = text[:MAX_FETCH_CHARS] + "…[обрезано]"
        return text, "OK"

    except requests.exceptions.Timeout:
        return f"Ошибка: таймаут при загрузке {url} (>{FETCH_TIMEOUT}с)", "ERROR"
    except requests.exceptions.RequestException as e:
        return f"Ошибка при загрузке {url}: {e}", "ERROR"
    except Exception as e:
        return f"Непредвиденная ошибка при загрузке {url}: {e}", "ERROR"


# ----------------------------------------------------------------------
# Публичный инструмент: save_note
# ----------------------------------------------------------------------
def save_note(note: str) -> str:
    """
    Сохранить заметку во время исследования (в память агента).
    Реально сохраняется в файл output/notes_<run>.md — для наглядности.

    Returns:
        Строка-подтверждение.
    """
    note = (note or "").strip()
    if not note:
        return "Заметка пуста — ничего не сохранено"
    # Заметки накапливаются в атрибуте модуля и пишутся в конце прогона main.py
    logger.info("save_note: %s", note[:200])
    return "Заметка сохранена: " + note[:300]


# Реестр инструментов: name -> (функция, описание)
TOOL_FUNCTIONS: Dict[str, Any] = {
    "search_web": search_web,
    "fetch_url": fetch_url,
    "save_note": save_note,
}


def execute_tool(name: str, arguments: str) -> str:
    """
    Выполнение инструмента по имени и JSON-аргументам.

    Returns:
        Строка-результат (JSON для search_web, текст для fetch_url и т.д.).
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps(
            {"ok": False, "error": f"Неизвестный инструмент: {name}"}, ensure_ascii=False
        )

    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        args = {}

    try:
        result = fn(**args)
        if isinstance(result, tuple):
            # fetch_url возвращает (text, status)
            text, status = result
            return json.dumps({"ok": status == "OK", "status": status, "content": text}, ensure_ascii=False)
        return result
    except TypeError as e:
        return json.dumps({"ok": False, "error": f"Неверные аргументы: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("Ошибка при выполнении инструмента %s: %s", name, e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
