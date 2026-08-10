from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nspk_sbp_news_agent.collector import (
    build_feed_url,
    detect_brands,
    matches_brand,
    parse_category_feed,
    parse_feed,
)
from nspk_sbp_news_agent.config import Settings
from nspk_sbp_news_agent.mailer import build_message
from nspk_sbp_news_agent.models import NewsItem, SummarizedNewsItem
from nspk_sbp_news_agent.state import load_sent_history, only_new, save_sent_items
from nspk_sbp_news_agent.summarizer import FALLBACK_SUMMARY, summarize_items


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)


def _rss(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>News RSS</title>
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
        smtp_use_ssl=False,
        smtp_timeout=60,
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
    url = build_feed_url('"СБП" платежи НСПК', "ru", "RU", "RU:ru")
    assert "%22%D0%A1%D0%91%D0%9F%22" in url
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


def test_matches_brand_filters_relevant_keywords() -> None:
    assert matches_brand("Льготы по карте «Мир» начали тестировать", "МИР")
    assert matches_brand("ЦБ обсуждает приватизацию НСПК", "МИР")
    assert matches_brand("Переводы по СБП выросли в регионах", "СБП")
    assert matches_brand("Система быстрых платежей расширила лимиты", "СБП")
    assert not matches_brand("Одиссея стала самым кассовым фильмом", "МИР")
    assert detect_brands("НСПК обновила правила для СБП и карты Мир") == ["МИР", "СБП"]


def test_parse_category_feed_assigns_brands_by_keywords() -> None:
    recent = "Wed, 15 Jul 2026 07:00:00 GMT"
    content = _rss(
        f"""
        <item>
          <title>Льготы по карте «Мир» начали тестировать</title>
          <link>https://example.com/mir</link>
          <pubDate>{recent}</pubDate>
          <description><![CDATA[НСПК расширяет программу льгот.]]></description>
        </item>
        <item>
          <title>Погода в Москве</title>
          <link>https://example.com/weather</link>
          <pubDate>{recent}</pubDate>
          <description>Ожидается дождь.</description>
        </item>
        <item>
          <title>Переводы по СБП выросли</title>
          <link>https://example.com/sbp</link>
          <pubDate>{recent}</pubDate>
          <description>Объём операций увеличился.</description>
        </item>
        """
    )

    items = parse_category_feed(content, NOW - timedelta(hours=48), default_source="Ведомости")

    assert len(items) == 2
    assert {item.brand for item in items} == {"МИР", "СБП"}
    assert all(item.source == "Ведомости" for item in items)


def test_parse_feed_filters_old_and_incomplete_entries() -> None:
    recent = "Wed, 15 Jul 2026 07:00:00 GMT"
    old = "Mon, 01 Jun 2026 07:00:00 GMT"
    content = _rss(
        f"""
        <item>
          <title>СБП расширяет переводы</title>
          <link>https://example.com/recent</link>
          <pubDate>{recent}</pubDate>
          <source>Example News</source>
          <description><![CDATA[<p>СБП запустила <b>новый</b> сервис.</p>]]></description>
        </item>
        <item>
          <title>Old SBP news</title>
          <link>https://example.com/old</link>
          <pubDate>{old}</pubDate>
        </item>
        <item><title>No link</title><pubDate>{recent}</pubDate></item>
        """
    )

    items = parse_feed(content, "СБП", NOW - timedelta(hours=48))

    assert len(items) == 1
    assert items[0].title == "СБП расширяет переводы"
    assert items[0].source == "Example News"
    assert items[0].snippet == "СБП запустила новый сервис."


def test_state_removes_duplicates_and_persists(tmp_path) -> None:
    state_file = tmp_path / "state" / "sent.json"
    item = NewsItem(
        brand="МИР",
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
        brand="МИР",
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
    assert "МИР и СБП: 1 новая новость" == message["Subject"]
    assert "<table>" in html
    assert "Заголовок" in html
    assert "Payments &amp; cards" in html
    assert "News &lt;Daily&gt;" in html
    assert "Кратко: платежи &amp; карты &lt;test&gt;" in html
    assert 'href="https://example.com/?a=1&amp;b=&quot;2&quot;">ссылка</a>' in html
    assert "https://example.com/?a=1&b=\"2\"" in plain


def test_summarize_items_uses_groq_response() -> None:
    item = NewsItem(
        brand="СБП",
        title="СБП растёт в регионах",
        link="https://example.com/sbp",
        source="Example",
        published_at=NOW,
        key="sbp-key",
        snippet="СБП расширяет платежи в регионах",
    )

    def fake_caller(settings, items):
        assert settings.groq_api_key == "test-key"
        assert len(items) == 1
        return {"sbp-key": "СБП расширяет платежи в регионах."}

    result = summarize_items([item], _settings(), caller=fake_caller)

    assert len(result) == 1
    assert result[0].summary == "СБП расширяет платежи в регионах."


def test_summarize_items_falls_back_on_error() -> None:
    item = NewsItem(
        brand="СБП",
        title="SBP news",
        link="https://example.com/sbp",
        source="Example",
        published_at=NOW,
        key="sbp-key",
        snippet="Короткий сниппет",
    )

    def broken_caller(settings, items):
        raise ValueError("bad json")

    result = summarize_items([item], _settings(), caller=broken_caller)

    assert result[0].summary == "Короткий сниппет"


def test_summarize_items_fallback_without_snippet() -> None:
    item = NewsItem(
        brand="МИР",
        title="MIR news",
        link="https://example.com/mir",
        source="Example",
        published_at=NOW,
        key="mir-key",
    )

    def broken_caller(settings, items):
        raise OSError("network down")

    result = summarize_items([item], _settings(), caller=broken_caller)

    assert result[0].summary == FALLBACK_SUMMARY


def test_summarize_items_batches_requests() -> None:
    items = [
        NewsItem(
            brand="МИР",
            title=f"News {index}",
            link=f"https://example.com/{index}",
            source="Example",
            published_at=NOW,
            key=f"key-{index}",
            snippet=f"Snippet {index}",
        )
        for index in range(10)
    ]
    seen_sizes: list[int] = []

    def fake_caller(settings, batch):
        seen_sizes.append(len(batch))
        return {item.key: f"Summary {item.key}" for item in batch}

    result = summarize_items(items, _settings(), caller=fake_caller, batch_size=4)

    assert seen_sizes == [4, 4, 2]
    assert [entry.summary for entry in result] == [f"Summary key-{i}" for i in range(10)]
