from __future__ import annotations

from datetime import datetime, timedelta, timezone

from payment_news_agent.collector import build_feed_url, parse_feed
from payment_news_agent.config import Settings
from payment_news_agent.mailer import build_message
from payment_news_agent.models import NewsItem, SummarizedNewsItem
from payment_news_agent.state import load_sent_history, only_new, save_sent_items
from payment_news_agent.summarizer import FALLBACK_SUMMARY, summarize_items


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def _rss(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        {items}
      </channel>
    </rss>"""


def _settings(**overrides) -> Settings:
    base = dict(
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        smtp_use_tls=True,
        mail_from="",
        mail_to=(),
        max_age_hours=48,
        limit_per_brand=20,
        state_file=__import__("pathlib").Path("state/sent.json"),
        dry_run=True,
        groq_api_key="test-key",
        groq_model="llama-3.1-8b-instant",
        summarize_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_build_feed_url_encodes_query_and_locale() -> None:
    url = build_feed_url('"Visa" платежи', "ru", "RU", "RU:ru")
    assert "%22Visa%22+%D0%BF%D0%BB%D0%B0%D1%82%D0%B5%D0%B6%D0%B8" in url
    assert "ceid=RU%3Aru" in url


def test_empty_optional_smtp_values_use_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_USE_TLS", "")
    monkeypatch.setenv("GROQ_MODEL", "")

    settings = Settings.from_env()

    assert settings.smtp_port == 587
    assert settings.smtp_use_tls is True
    assert settings.groq_model == "llama-3.1-8b-instant"
    assert settings.summarize_enabled is True


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
          <description><![CDATA[<p>Visa unveiled a <b>new</b> product.</p>]]></description>
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
    assert items[0].snippet == "Visa unveiled a new product."


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


def test_message_contains_table_and_safe_html() -> None:
    item = NewsItem(
        brand="Mastercard",
        title="Payments & cards",
        link='https://example.com/?a=1&b="2"',
        source="News <Daily>",
        published_at=NOW,
        key="item-key",
        snippet="snippet",
    )
    summarized = SummarizedNewsItem(
        item=item,
        summary="Кратко: платежи & карты <test>",
    )

    message = build_message(
        [summarized],
        mail_from="sender@example.com",
        mail_to=("reader@example.com",),
        now=NOW,
    )

    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Payments & cards" in plain
    assert "Кратко: платежи & карты <test>" in plain
    assert "Visa и Mastercard: 1 новая новость" == message["Subject"]
    assert "<table>" in html
    assert "Заголовок" in html
    assert "Payments &amp; cards" in html
    assert "News &lt;Daily&gt;" in html
    assert "Кратко: платежи &amp; карты &lt;test&gt;" in html


def test_summarize_items_uses_groq_response() -> None:
    item = NewsItem(
        brand="Visa",
        title="Visa expands in Europe",
        link="https://example.com/visa",
        source="Example",
        published_at=NOW,
        key="visa-key",
        snippet="Visa expands payments in Europe",
    )

    def fake_caller(settings, items):
        assert settings.groq_api_key == "test-key"
        assert len(items) == 1
        return {"visa-key": "Visa расширяет платежи в Европе."}

    result = summarize_items([item], _settings(), caller=fake_caller)

    assert len(result) == 1
    assert result[0].summary == "Visa расширяет платежи в Европе."


def test_summarize_items_falls_back_on_error() -> None:
    item = NewsItem(
        brand="Visa",
        title="Visa news",
        link="https://example.com/visa",
        source="Example",
        published_at=NOW,
        key="visa-key",
        snippet="Короткий сниппет",
    )

    def broken_caller(settings, items):
        raise ValueError("bad json")

    result = summarize_items([item], _settings(), caller=broken_caller)

    assert result[0].summary == "Короткий сниппет"


def test_summarize_items_fallback_without_snippet() -> None:
    item = NewsItem(
        brand="Mastercard",
        title="MC news",
        link="https://example.com/mc",
        source="Example",
        published_at=NOW,
        key="mc-key",
    )

    def broken_caller(settings, items):
        raise OSError("network down")

    result = summarize_items([item], _settings(), caller=broken_caller)

    assert result[0].summary == FALLBACK_SUMMARY
