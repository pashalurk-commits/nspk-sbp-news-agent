from __future__ import annotations

from datetime import datetime, timedelta, timezone

from payment_news_agent.collector import build_feed_url, parse_feed
from payment_news_agent.config import Settings
from payment_news_agent.mailer import build_message
from payment_news_agent.models import NewsItem
from payment_news_agent.state import load_sent_history, only_new, save_sent_items


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def _rss(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        {items}
      </channel>
    </rss>"""


def test_build_feed_url_encodes_query_and_locale() -> None:
    url = build_feed_url('"Visa" платежи', "ru", "RU", "RU:ru")
    assert "%22Visa%22+%D0%BF%D0%BB%D0%B0%D1%82%D0%B5%D0%B6%D0%B8" in url
    assert "ceid=RU%3Aru" in url


def test_empty_optional_smtp_values_use_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_USE_TLS", "")

    settings = Settings.from_env()

    assert settings.smtp_port == 587
    assert settings.smtp_use_tls is True


def test_parse_feed_filters_old_and_incomplete_entries() -> None:
    recent = "Wed, 15 Jul 2026 07:00:00 GMT"
    old = "Mon, 01 Jun 2026 07:00:00 GMT"
    content = _rss(
        f"""
        <item>
          <title>Visa launches a payment product</title>
          <link>https://example.com/recent</link>
          <pubDate>{recent}</pubDate>
          <source>Example News</source>
        </item>
        <item>
          <title>Old Visa news</title>
          <link>https://example.com/old</link>
          <pubDate>{old}</pubDate>
        </item>
        <item><title>No link</title><pubDate>{recent}</pubDate></item>
        """
    )

    items = parse_feed(content, "Visa", NOW - timedelta(hours=48))

    assert len(items) == 1
    assert items[0].title == "Visa launches a payment product"
    assert items[0].source == "Example News"


def test_state_removes_duplicates_and_persists(tmp_path) -> None:
    state_file = tmp_path / "state" / "sent.json"
    item = NewsItem(
        brand="Visa",
        title="News",
        link="https://example.com",
        source="Source",
        published_at=NOW,
        key="item-key",
    )

    assert only_new([item], {"another-key"}) == [item]
    assert only_new([item], {"item-key"}) == []

    save_sent_items(state_file, {}, [item], now=NOW)
    assert load_sent_history(state_file) == {"item-key": NOW.isoformat()}


def test_message_contains_plain_and_safe_html() -> None:
    item = NewsItem(
        brand="Mastercard",
        title="Payments & cards",
        link='https://example.com/?a=1&b="2"',
        source="News <Daily>",
        published_at=NOW,
        key="item-key",
    )

    message = build_message(
        [item],
        mail_from="sender@example.com",
        mail_to=("reader@example.com",),
        now=NOW,
    )

    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Payments & cards" in plain
    assert "Visa и Mastercard: 1 новых новостей" == message["Subject"]
    assert "Payments &amp; cards" in html
    assert "News &lt;Daily&gt;" in html
