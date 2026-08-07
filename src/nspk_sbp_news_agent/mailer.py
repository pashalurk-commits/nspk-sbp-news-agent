from __future__ import annotations

import html
import logging
import smtplib
from collections import defaultdict
from datetime import datetime
from email.message import EmailMessage
from typing import Union

from .config import Settings
from .models import NewsItem, SummarizedNewsItem

LOGGER = logging.getLogger(__name__)
BRANDS = ("МИР", "СБП")


def _as_summarized(
    items: list[Union[NewsItem, SummarizedNewsItem]],
) -> list[SummarizedNewsItem]:
    result: list[SummarizedNewsItem] = []
    for entry in items:
        if isinstance(entry, SummarizedNewsItem):
            result.append(entry)
        else:
            result.append(
                SummarizedNewsItem(
                    item=entry,
                    summary=entry.snippet or "Краткое содержание недоступно",
                )
            )
    return result


def build_message(
    items: list[Union[NewsItem, SummarizedNewsItem]],
    mail_from: str,
    mail_to: tuple[str, ...],
    now: datetime,
) -> EmailMessage:
    summarized = _as_summarized(items)
    grouped: dict[str, list[SummarizedNewsItem]] = defaultdict(list)
    for entry in summarized:
        grouped[entry.item.brand].append(entry)

    text_parts = [f"Новые новости о МИР и СБП — {now:%d.%m.%Y}"]
    html_parts = [
        "<html><body>",
        f"<h1>Новые новости о МИР и СБП — {now:%d.%m.%Y}</h1>",
        "<style>"
        "table{border-collapse:collapse;width:100%;margin-bottom:1.5em}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#f5f5f5}"
        "</style>",
    ]
    for brand in BRANDS:
        brand_items = grouped.get(brand, [])
        if not brand_items:
            continue
        text_parts.extend(("", brand, "Заголовок | Ссылка | Краткое содержание"))
        html_parts.append(f"<h2>{html.escape(brand)}</h2>")
        html_parts.append(
            "<table><thead><tr>"
            "<th>Заголовок</th><th>Ссылка</th><th>Краткое содержание</th>"
            "</tr></thead><tbody>"
        )
        for entry in brand_items:
            item = entry.item
            text_parts.append(
                f"- {item.title}\n  {item.link}\n  {entry.summary}"
            )
            html_parts.append(
                "<tr>"
                f"<td>{html.escape(item.title)}<br>"
                f"<small>{html.escape(item.source)}</small></td>"
                f'<td><a href="{html.escape(item.link, quote=True)}">'
                "ссылка</a></td>"
                f"<td>{html.escape(entry.summary)}</td>"
                "</tr>"
            )
        html_parts.append("</tbody></table>")
    html_parts.append("</body></html>")

    count = len(summarized)
    if count == 1:
        subject = "МИР и СБП: 1 новая новость"
    else:
        subject = f"МИР и СБП: {count} новых новостей"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = ", ".join(mail_to)
    message.set_content("\n".join(text_parts))
    message.add_alternative("".join(html_parts), subtype="html")
    return message


def send_message(message: EmailMessage, settings: Settings) -> None:
    LOGGER.info(
        "Подключение к SMTP %s:%s (ssl=%s, starttls=%s)",
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_use_ssl,
        settings.smtp_use_tls,
    )
    smtp_cls = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with smtp_cls(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout,
    ) as smtp:
        smtp.ehlo()
        if settings.smtp_use_tls:
            smtp.starttls()
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
