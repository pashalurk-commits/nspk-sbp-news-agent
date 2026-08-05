from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings
from .models import NewsItem, SummarizedNewsItem

LOGGER = logging.getLogger(__name__)
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
FALLBACK_SUMMARY = "Краткое содержание недоступно"
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


def _build_user_payload(items: list[NewsItem]) -> str:
    payload = [
        {
            "key": item.key,
            "brand": item.brand,
            "title": item.title,
            "source": item.source,
            "link": item.link,
            "snippet": item.snippet,
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


def _call_groq(
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


def summarize_items(
    items: list[NewsItem],
    settings: Settings,
    caller: Optional[Callable[[Settings, list[NewsItem]], dict[str, str]]] = None,
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
    try:
        summaries = call(settings, items)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("Саммаризация через Groq не удалась: %s", exc)
        return _fallback_items(items)

    result: list[SummarizedNewsItem] = []
    for item in items:
        summary = summaries.get(item.key) or item.snippet or FALLBACK_SUMMARY
        result.append(SummarizedNewsItem(item=item, summary=summary))
    return result
