from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings
from .models import NewsItem, SummarizedNewsItem

LOGGER = logging.getLogger(__name__)
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
FALLBACK_SUMMARY = "Краткое содержание недоступно"
BATCH_SIZE = 5
BATCH_DELAY_SECONDS = 12.0
SNIPPET_MAX_CHARS = 220
MAX_RETRIES = 5
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

SYSTEM_PROMPT = (
    "Ты аналитик новостей о платёжных системах Visa и Mastercard. "
    "По заголовку, источнику и короткому сниппету из RSS составь "
    "поверхностное краткое содержание на русском языке (1–2 предложения). "
    "Не выдумывай факты, которых нет во входных данных. "
    'Верни только JSON-объект вида {"items":[{"key":"...","summary":"..."}]} '
    "без пояснений."
)


def _fallback_items(items: list[NewsItem]) -> list[SummarizedNewsItem]:
    return [
        SummarizedNewsItem(
            item=item,
            summary=item.snippet or FALLBACK_SUMMARY,
        )
        for item in items
    ]


def _truncate(text: str, limit: int = SNIPPET_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_user_payload(items: list[NewsItem]) -> str:
    # Без link: он не нужен для саммари и раздувает запрос (Google News URL длинные).
    payload = [
        {
            "key": item.key,
            "brand": item.brand,
            "title": _truncate(item.title, 180),
            "source": _truncate(item.source, 60),
            "snippet": _truncate(item.snippet),
        }
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False)


def _extract_json_text(content: str) -> str:
    text = content.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return text


def _parse_summaries(content: str) -> dict[str, str]:
    data = json.loads(_extract_json_text(content))
    if isinstance(data, dict):
        data = data.get("items", data.get("summaries", []))
    if not isinstance(data, list):
        raise ValueError("Ответ Groq должен содержать JSON-массив items")

    summaries: dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        summary = str(entry.get("summary", "")).strip()
        if key and summary:
            summaries[key] = summary
    if not summaries:
        raise ValueError("В ответе Groq нет валидных summary")
    return summaries


def _retry_after_seconds(error: HTTPError, attempt: int) -> float:
    header = error.headers.get("Retry-After") if error.headers else None
    if header:
        try:
            return max(float(header), 1.0)
        except ValueError:
            pass
    # Free-tier Groq часто упирается в TPM (~6k/min) — ждём дольше.
    return min(60.0, 8.0 * (2 ** attempt))


def _http_error_details(error: HTTPError) -> str:
    body = ""
    try:
        body = error.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        body = ""
    if body:
        return f"{error} | {body}"
    return str(error)


def _call_groq_once(
    settings: Settings,
    items: list[NewsItem],
    timeout: int = 60,
) -> dict[str, str]:
    body = {
        "model": settings.groq_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Составь краткие содержания для новостей. "
                    'Верни JSON-объект {"items":[{"key":"...","summary":"..."}, ...]}.\n\n'
                    + _build_user_payload(items)
                ),
            },
        ],
    }
    request = Request(
        GROQ_CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "payment-news-agent/0.1",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))

    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("Пустой ответ Groq")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise ValueError("В ответе Groq нет content")
    return _parse_summaries(content)


def _call_groq(
    settings: Settings,
    items: list[NewsItem],
    timeout: int = 60,
) -> dict[str, str]:
    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            return _call_groq_once(settings, items, timeout=timeout)
        except HTTPError as exc:
            details = _http_error_details(exc)
            last_error = ValueError(details)
            if exc.code != 429 or attempt >= MAX_RETRIES - 1:
                raise ValueError(details) from exc
            delay = _retry_after_seconds(exc, attempt)
            LOGGER.warning(
                "Groq 429 Too Many Requests, повтор через %.0f с (попытка %d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def _chunked(items: list[NewsItem], size: int) -> list[list[NewsItem]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def summarize_items(
    items: list[NewsItem],
    settings: Settings,
    caller: Optional[Callable[[Settings, list[NewsItem]], dict[str, str]]] = None,
    batch_size: int = BATCH_SIZE,
    batch_delay_seconds: float = BATCH_DELAY_SECONDS,
) -> list[SummarizedNewsItem]:
    if not items:
        return []

    if not settings.summarize_enabled:
        LOGGER.info("Саммаризация отключена, используем сниппеты/fallback")
        return _fallback_items(items)

    if not settings.groq_api_key:
        LOGGER.warning("GROQ_API_KEY не задан, используем сниппеты/fallback")
        return _fallback_items(items)

    call = caller or _call_groq
    summaries: dict[str, str] = {}
    batches = _chunked(items, batch_size)
    for index, batch in enumerate(batches):
        if index > 0 and batch_delay_seconds > 0 and caller is None:
            LOGGER.info(
                "Пауза %.0f с перед следующим batch Groq (%d/%d)",
                batch_delay_seconds,
                index + 1,
                len(batches),
            )
            time.sleep(batch_delay_seconds)
        try:
            LOGGER.info("Саммаризация batch %d/%d (%d шт.)", index + 1, len(batches), len(batch))
            summaries.update(call(settings, batch))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Саммаризация batch (%d шт.) через Groq не удалась: %s",
                len(batch),
                exc,
            )

    result: list[SummarizedNewsItem] = []
    for item in items:
        summary = summaries.get(item.key) or item.snippet or FALLBACK_SUMMARY
        result.append(SummarizedNewsItem(item=item, summary=summary))
    return result
