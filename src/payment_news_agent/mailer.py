from __future__ import annotations

import html
import smtplib
from collections import defaultdict
from datetime import datetime
from email.message import EmailMessage

from .config import Settings
from .models import NewsItem


def build_message(
    items: list[NewsItem],
    mail_from: str,
    mail_to: tuple[str, ...],
    now: datetime,
) -> EmailMessage:
    grouped: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        grouped[item.brand].append(item)

    text_parts = [f"Новые новости о платёжных системах — {now:%d.%m.%Y}"]
    html_parts = [
        "<html><body>",
        f"<h1>Новые новости о платёжных системах — {now:%d.%m.%Y}</h1>",
    ]
    for brand in ("Visa", "Mastercard"):
        brand_items = grouped.get(brand, [])
        if not brand_items:
            continue
        text_parts.extend(("", brand))
        html_parts.append(f"<h2>{brand}</h2><ul>")
        for item in brand_items:
            text_parts.append(
                f"- {item.title} ({item.source})\n  {item.link}"
            )
            html_parts.append(
                "<li>"
                f'<a href="{html.escape(item.link, quote=True)}">'
                f"{html.escape(item.title)}</a>"
                f" — {html.escape(item.source)}"
                "</li>"
            )
        html_parts.append("</ul>")
    html_parts.append("</body></html>")

    message = EmailMessage()
    message["Subject"] = f"Visa и Mastercard: {len(items)} новых новостей"
    message["From"] = mail_from
    message["To"] = ", ".join(mail_to)
    message.set_content("\n".join(text_parts))
    message.add_alternative("".join(html_parts), subtype="html")
    return message


def send_message(message: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=30,
    ) as smtp:
        smtp.ehlo()
        if settings.smtp_use_tls:
            smtp.starttls()
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
