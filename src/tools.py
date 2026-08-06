"""
Инструменты исследовательского агента.

- search_web(query) -> list[str]: поиск по порядку SEARCH_PRIMARY (yandex/tavily,
  только настроенные) → DDGS (DuckDuckGo, всегда универсальный fallback).
- fetch_url(url) -> (text|error, status): безопасное получение текста страницы.
- save_note(note) -> str: сохранение заметки во время исследования.

Ошибки инструментов НЕ пробрасываются наружу — возвращаются структурированным
результатом с полем error.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import re
import socket
import threading
import warnings
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

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
SEARCH_RESULTS = 5
# Таймауты настраиваются через .env (SEARCH_TIMEOUT / FETCH_TIMEOUT)
FETCH_TIMEOUT = config.settings.FETCH_TIMEOUT
SEARCH_TIMEOUT = config.settings.SEARCH_TIMEOUT
# Таймаут установления соединения: если хост недоступен (DNS/сеть), не ждём
# SEARCH_TIMEOUT, а отваливаемся быстро; чтение ответа — полный SEARCH_TIMEOUT.
SEARCH_CONNECT_TIMEOUT = 10.0

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
    resp = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=(SEARCH_CONNECT_TIMEOUT, SEARCH_TIMEOUT),
    )
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
    """Fallback-поиск через DuckDuckGo с жёстким лимитом по времени.

    DDGS делает внутренние ретраи, поэтому таймаут отдельного запроса не
    гарантирует, что весь поиск не зависнет (например, при сбое DNS). Весь
    поиск выполняется в daemon-потоке с общим лимитом SEARCH_TIMEOUT.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results: List[Dict[str, str]] = []

    def _run() -> None:
        with DDGS(timeout=SEARCH_TIMEOUT) as ddgs:
            for r in ddgs.text(query, max_results=SEARCH_RESULTS):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    }
                )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(SEARCH_TIMEOUT)
    if t.is_alive():
        logger.warning(
            "DDGS: поиск не уложился в таймаут %ss — пропускаю fallback",
            SEARCH_TIMEOUT,
        )
        return []
    return results


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
        timeout=(SEARCH_CONNECT_TIMEOUT, SEARCH_TIMEOUT),
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
    Поиск в вебе. Порядок задаётся SEARCH_PRIMARY (config.search_engines):
    yandex → tavily → DDGS (DDGS — всегда универсальный fallback).
    Используются только настроенные движки (есть ключи).

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

    engines = {"yandex": _search_yandex, "tavily": _search_tavily, "ddgs": _search_ddgs}
    for engine in config.settings.search_engines:
        try:
            results = engines[engine](query)
            if results:
                logger.info("search_web: %s вернул %d результатов", engine, len(results))
                return json.dumps(
                    {"ok": True, "engine": engine, "results": results}, ensure_ascii=False
                )
            errors.append(f"{engine}: пустой результат")
        except Exception as e:
            errors.append(f"{engine}: {e}")
            logger.warning("search_web: %s недоступен (%s), пробую следующий", engine, e)

    return json.dumps(
        {"ok": False, "error": "Все поисковые движки недоступны: " + "; ".join(errors)},
        ensure_ascii=False,
    )


# ----------------------------------------------------------------------
# Публичный инструмент: fetch_url
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Denylist для fetch_url — защита от SSRF (внутренние сети)
# ----------------------------------------------------------------------
_FETCH_DENYLIST_PREFIXES: Tuple[str, ...] = (
    # loopback / localhost
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "http://0.0.0.0",
    "https://0.0.0.0",
    # частные IPv4
    "http://10.",
    "https://10.",
    "http://172.16.",
    "https://172.16.",
    "http://172.17.",
    "https://172.17.",
    "http://172.18.",
    "https://172.18.",
    "http://172.19.",
    "https://172.19.",
    "http://172.20.",
    "https://172.20.",
    "http://172.21.",
    "https://172.21.",
    "http://172.22.",
    "https://172.22.",
    "http://172.23.",
    "https://172.23.",
    "http://172.24.",
    "https://172.24.",
    "http://172.25.",
    "https://172.25.",
    "http://172.26.",
    "https://172.26.",
    "http://172.27.",
    "https://172.27.",
    "http://172.28.",
    "https://172.28.",
    "http://172.29.",
    "https://172.29.",
    "http://172.30.",
    "https://172.30.",
    "http://172.31.",
    "https://172.31.",
    "http://192.168.",
    "https://192.168.",
    # link-local
    "http://169.254.",
    "https://169.254.",
    # IPv6 loopback
    "http://[::1]",
    "https://[::1]",
    "http://[0:0:0:0:0:0:0:1]",
    "https://[0:0:0:0:0:0:0:1]",
    # metadata endpoints (AWS, GCP, Azure)
    "http://169.254.169.254",
    "https://169.254.169.254",
    "http://metadata.google.internal",
    "https://metadata.google.internal",
)


def _block_reason_for_ip(ip: str) -> Optional[str]:
    """Причина блокировки IP или None, если адрес публичный."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return f"IP {ip} относится к внутренним/приватным сетям"
    return None


def _parse_numeric_host(host: str) -> Optional[str]:
    """Нормализует числовые формы IPv4 в канонический адрес.

    Обрабатывает обходы SSRF-фильтров, которые многие резолверы (в т.ч. Windows)
    не трактуют как IP:
      - '2130706433'      (десятичный uint32),
      - '0x7f000001'      (hex),
      - '127.1'           (короткая dotted-форма = 127.0.0.1),
      - '127.0.1'         (= 127.0.0.1),
      - '0177.0.0.1'      (octal 0177 = 127),
      - '0x7f.0.0.1'      (hex-октеты).
    Возвращает канонический IPv4 или None, если это не чисто числовой host.
    """

    def _val(p: str) -> int:
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", p):
            return int(p, 16)
        if p.isdigit():
            if len(p) > 1 and p.startswith("0") and all(c in "01234567" for c in p):
                return int(p, 8)  # leading zero — octal
            return int(p)
        raise ValueError(p)

    h = host.strip().lower()
    if not h:
        return None

    # Целиком числовая форма (одно «слово») — uint32
    if re.fullmatch(r"(0[xX])?[0-9a-fA-F]+", h):
        try:
            val = _val(h)
        except ValueError:
            return None
        if 0 <= val <= 0xFFFFFFFF:
            return str(ipaddress.ip_address(val))
        return None

    # Dotted-форма (2..4 компонента)
    parts = h.split(".")
    if 2 <= len(parts) <= 4:
        try:
            vals = [_val(p) for p in parts]
        except ValueError:
            return None
        if any(v > 0xFFFFFFFF for v in vals):
            return None
        # Последний компонент заполняет октеты справа (может быть многобайтовым),
        # остальные — октеты слева; недостающие добиваем нулями.
        tail = vals.pop()
        if tail > 0xFFFFFF:
            return None
        tail_bytes: List[int] = [tail & 0xFF]
        t = tail >> 8
        while t:
            tail_bytes.append(t & 0xFF)
            t >>= 8
        if len(vals) + len(tail_bytes) > 4:
            return None
        octets = vals + [0] * (4 - len(vals) - len(tail_bytes)) + tail_bytes[::-1]
        return ".".join(str(o) for o in octets)
    return None


def _resolve_host_ips(host: str) -> List[str]:
    """Резолв host в IP-адреса (обрабатывает 127.1, 2130706433, hex/octal и т.д.)."""
    h = host.strip("[]")  # для IPv6 [::1]
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        return []
    ips: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    return ips


def is_url_blocked(url: str) -> Optional[str]:
    """Проверяет URL по denylist. Возвращает причину блокировки или None, если OK.

    Защита от SSRF в три уровня:
    1) строковый denylist (быстрый путь, перекрывает localhost/приватные префиксы);
    2) парсер числовых форм IPv4 (127.1, 2130706433, 0x7f000001, octal) — ловит
       обходы, которые не резолвятся через DNS на Windows;
    3) DNS-резолв hostname и проверка каждого IP через ipaddress
       (перекрывает остальные случаи и IPv6).
    """
    url_lower = url.lower().rstrip("/")
    for prefix in _FETCH_DENYLIST_PREFIXES:
        if url_lower.startswith(prefix.lower()):
            return f"URL заблокирован (внутренний/приватный адрес): {prefix}*"

    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL не удалось распарсить"
    host = parsed.hostname
    if not host:
        return "URL не содержит hostname"

    # 2) Числовые/короткие формы IPv4 (не требуют DNS)
    numeric = _parse_numeric_host(host)
    if numeric is not None:
        reason = _block_reason_for_ip(numeric)
        if reason:
            return f"URL заблокирован: {reason} (host: {host})"
        return None  # числовой публичный адрес — разрешаем

    # 3) DNS-резолв
    for ip in _resolve_host_ips(host):
        reason = _block_reason_for_ip(ip)
        if reason:
            return f"URL заблокирован: {reason} (host: {host})"
    return None


def fetch_url(url: str) -> Tuple[str, str]:
    """
    Безопасное получение текста страницы.

    Возвращает (text, status): text — содержимое (обрезано до MAX_FETCH_CHARS)
    или сообщение об ошибке; status — "OK" или "ERROR".

    SSRF-защита: каждый URL (исходный и каждый редирект) проверяется
    is_url_blocked() — строковый denylist + резолв IP.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Ошибка: URL должен начинаться с http:// или https://", "ERROR"

    current = url
    try:
        with requests.Session() as session:
            for hop in range(6):  # максимум 5 редиректов
                block_reason = is_url_blocked(current)
                if block_reason:
                    logger.warning("fetch_url: заблокирован %s — %s", current, block_reason)
                    return block_reason, "ERROR"

                resp = session.get(
                    current,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept-Language": "ru,en;q=0.8",
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    },
                    timeout=FETCH_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                )
                if resp.is_redirect or resp.is_permanent_redirect:
                    location = resp.headers.get("Location")
                    resp.close()
                    if not location:
                        return "Ошибка: редирект без Location", "ERROR"
                    current = urljoin(current, location)
                    continue
                break

            resp.raise_for_status()

            # Читаем не более MAX_FETCH_CHARS * 2 байт, чтобы не качать огромные страницы
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_FETCH_CHARS * 2:
                    break
            resp.close()

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
