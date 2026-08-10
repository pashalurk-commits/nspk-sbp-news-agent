from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import feedparser

from .models import NewsItem

LOGGER = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TAG_RE = re.compile(r"<[^>]+>")
BRANDS = ("МИР", "СБП")

GOOGLE_QUERIES = {
    "МИР": (
        ('"МИР" карта платежи НСПК', "ru", "RU", "RU:ru"),
        ('"платёжная система МИР"', "ru", "RU", "RU:ru"),
    ),
    "СБП": (
        ('"СБП" платежи НСПК', "ru", "RU", "RU:ru"),
        ('"Система быстрых платежей"', "ru", "RU", "RU:ru"),
    ),
}

# Категорийные RSS без поиска: отбираем новости по ключевым словам локально.
CATEGORY_FEEDS = (
    ("Mail.ru", "https://news.mail.ru/rss/economics/"),
    ("Ведомости", "https://www.vedomosti.ru/rss/rubric/finance"),
)

BRAND_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "МИР": (
        re.compile(r"карт[а-я]*\s*«?\s*мир\s*»?", re.I),
        re.compile(r"плат[её]жн\w*\s+систем\w*\s+«?\s*мир\s*»?", re.I),
        re.compile(r"«\s*мир\s*»", re.I),
        re.compile(r"\bнспк\b", re.I),
    ),
    "СБП": (
        re.compile(r"\bсбп\b", re.I),
        re.compile(r"систем\w*\s+быстр\w*\s+платеж", re.I),
        re.compile(r"\bнспк\b", re.I),
    ),
}


def _fetch_feed(url: str, timeout: int = 20) -> feedparser.FeedParserDict:
    request = Request(url, headers={"User-Agent": "nspk-sbp-news-agent/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return feedparser.parse(response.read())


def build_feed_url(query: str, language: str, region: str, edition: str) -> str:
    return f"{GOOGLE_NEWS_RSS}?{urlencode({'q': query, 'hl': language, 'gl': region, 'ceid': edition})}"


def _published_at(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def _item_key(brand: str, title: str) -> str:
    normalized = re.sub(r"\W+", " ", title.casefold()).strip()
    payload = f"{brand.casefold()}:{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()


def _clean_snippet(raw: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def _entry_text(entry: feedparser.FeedParserDict) -> str:
    return " ".join(
        part
        for part in (
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("description", ""),
        )
        if part
    )


def matches_brand(text: str, brand: str) -> bool:
    return any(pattern.search(text) for pattern in BRAND_PATTERNS[brand])


def detect_brands(text: str) -> list[str]:
    return [brand for brand in BRANDS if matches_brand(text, brand)]


def parse_feed(
    content: Union[bytes, str, feedparser.FeedParserDict],
    brand: str,
    cutoff: datetime,
    *,
    default_source: str = "Неизвестный источник",
) -> list[NewsItem]:
    feed = content if isinstance(content, feedparser.FeedParserDict) else feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Не удалось разобрать RSS: {feed.bozo_exception}")

    channel_source = ""
    if getattr(feed, "feed", None):
        channel_source = feed.feed.get("title", "").strip()

    items: list[NewsItem] = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        published_at = _published_at(entry)
        if not title or not link or published_at is None or published_at < cutoff:
            continue

        source_data = entry.get("source") or {}
        source = (
            source_data.get("title", "").strip()
            or channel_source
            or default_source
        )
        snippet = _clean_snippet(entry.get("summary") or entry.get("description") or "")
        items.append(
            NewsItem(
                brand=brand,
                title=title,
                link=link,
                source=source,
                published_at=published_at,
                key=_item_key(brand, title),
                snippet=snippet,
            )
        )
    return items


def parse_category_feed(
    content: Union[bytes, str, feedparser.FeedParserDict],
    cutoff: datetime,
    *,
    default_source: str,
) -> list[NewsItem]:
    feed = content if isinstance(content, feedparser.FeedParserDict) else feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Не удалось разобрать RSS: {feed.bozo_exception}")

    items: list[NewsItem] = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        published_at = _published_at(entry)
        if not title or not link or published_at is None or published_at < cutoff:
            continue

        brands = detect_brands(_entry_text(entry))
        if not brands:
            continue

        source_data = entry.get("source") or {}
        channel_source = feed.feed.get("title", "").strip() if getattr(feed, "feed", None) else ""
        source = source_data.get("title", "").strip() or channel_source or default_source
        snippet = _clean_snippet(entry.get("summary") or entry.get("description") or "")

        for brand in brands:
            items.append(
                NewsItem(
                    brand=brand,
                    title=title,
                    link=link,
                    source=source,
                    published_at=published_at,
                    key=_item_key(brand, title),
                    snippet=snippet,
                )
            )
    return items


def _store_items(by_key: dict[str, NewsItem], items: list[NewsItem]) -> None:
    for item in items:
        current = by_key.get(item.key)
        if current is None or item.published_at > current.published_at:
            by_key[item.key] = item


def collect_news(
    max_age_hours: int,
    limit_per_brand: int,
    now: Optional[datetime] = None,
) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    by_key: dict[str, NewsItem] = {}
    successful_feeds = 0

    for brand, queries in GOOGLE_QUERIES.items():
        for query in queries:
            url = build_feed_url(*query)
            LOGGER.info("Загрузка Google News RSS для %s (%s)", brand, query[0])
            try:
                feed = _fetch_feed(url)
            except OSError as exc:
                LOGGER.warning("RSS недоступен: %s", exc)
                continue
            if feed.bozo and not feed.entries:
                LOGGER.warning("RSS недоступен: %s", feed.bozo_exception)
                continue
            successful_feeds += 1
            _store_items(by_key, parse_feed(feed, brand, cutoff))

    for source_name, url in CATEGORY_FEEDS:
        LOGGER.info("Загрузка категорийного RSS %s", source_name)
        try:
            feed = _fetch_feed(url)
        except OSError as exc:
            LOGGER.warning("RSS недоступен (%s): %s", source_name, exc)
            continue
        if feed.bozo and not feed.entries:
            LOGGER.warning("RSS недоступен (%s): %s", source_name, feed.bozo_exception)
            continue
        successful_feeds += 1
        items = parse_category_feed(feed, cutoff, default_source=source_name)
        LOGGER.info("Из %s отобрано %d релевантных новостей", source_name, len(items))
        _store_items(by_key, items)

    if successful_feeds == 0:
        raise RuntimeError("Не удалось загрузить ни один RSS-источник")

    result: list[NewsItem] = []
    for brand in BRANDS:
        brand_items = sorted(
            (item for item in by_key.values() if item.brand == brand),
            key=lambda item: item.published_at,
            reverse=True,
        )
        result.extend(brand_items[:limit_per_brand])
    return result
