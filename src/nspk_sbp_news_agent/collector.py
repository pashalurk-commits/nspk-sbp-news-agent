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

# Публичный поисковый RSS Яндекс.Новостей закрыт (редирект на Dzen SSO).
# Используем Google News RSS с русскоязычными запросами — тот же механизм,
# что и в исходном агенте Visa/Mastercard.
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TAG_RE = re.compile(r"<[^>]+>")
QUERIES = {
    "МИР": (
        ('"МИР" карта платежи НСПК', "ru", "RU", "RU:ru"),
        ('"платёжная система МИР"', "ru", "RU", "RU:ru"),
    ),
    "СБП": (
        ('"СБП" платежи НСПК', "ru", "RU", "RU:ru"),
        ('"Система быстрых платежей"', "ru", "RU", "RU:ru"),
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


def parse_feed(
    content: Union[bytes, str, feedparser.FeedParserDict],
    brand: str,
    cutoff: datetime,
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

        source_data = entry.get("source") or {}
        source = source_data.get("title", "").strip() or "Неизвестный источник"
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


def collect_news(
    max_age_hours: int,
    limit_per_brand: int,
    now: Optional[datetime] = None,
) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    by_key: dict[str, NewsItem] = {}
    successful_feeds = 0

    for brand, queries in QUERIES.items():
        for query in queries:
            url = build_feed_url(*query)
            LOGGER.info("Загрузка RSS для %s (%s)", brand, query[0])
            try:
                feed = _fetch_feed(url)
            except OSError as exc:
                LOGGER.warning("RSS недоступен: %s", exc)
                continue
            if feed.bozo and not feed.entries:
                LOGGER.warning("RSS недоступен: %s", feed.bozo_exception)
                continue
            successful_feeds += 1
            for item in parse_feed(feed, brand, cutoff):
                current = by_key.get(item.key)
                if current is None or item.published_at > current.published_at:
                    by_key[item.key] = item

    if successful_feeds == 0:
        raise RuntimeError("Не удалось загрузить ни один RSS-источник")

    result: list[NewsItem] = []
    for brand in QUERIES:
        brand_items = sorted(
            (item for item in by_key.values() if item.brand == brand),
            key=lambda item: item.published_at,
            reverse=True,
        )
        result.extend(brand_items[:limit_per_brand])
    return result
