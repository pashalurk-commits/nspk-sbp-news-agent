from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    brand: str
    title: str
    link: str
    source: str
    published_at: datetime
    key: str
    snippet: str = ""


@dataclass(frozen=True)
class SummarizedNewsItem:
    item: NewsItem
    summary: str
