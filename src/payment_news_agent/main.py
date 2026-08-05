from __future__ import annotations

import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

from .collector import collect_news
from .config import Settings
from .mailer import build_message, send_message
from .state import load_sent_history, only_new, save_sent_items
from .summarizer import summarize_items

LOGGER = logging.getLogger(__name__)


def run() -> int:
    load_dotenv()
    settings = Settings.from_env()
    now = datetime.now(timezone.utc)

    history = load_sent_history(settings.state_file)
    items = collect_news(
        max_age_hours=settings.max_age_hours,
        limit_per_brand=settings.limit_per_brand,
        now=now,
    )
    new_items = only_new(items, set(history))
    if not new_items:
        LOGGER.info("Новых новостей нет, письмо не отправляется")
        return 0

    summarized = summarize_items(new_items, settings)
    message = build_message(
        summarized,
        mail_from=settings.mail_from or "dry-run@example.com",
        mail_to=settings.mail_to or ("dry-run@example.com",),
        now=now,
    )
    if settings.dry_run:
        print(message.get_body(preferencelist=("plain",)).get_content())
        LOGGER.info("Dry-run: найдено %d новых новостей", len(new_items))
        return 0

    send_message(message, settings)
    save_sent_items(settings.state_file, history, new_items, now=now)
    LOGGER.info("Отправлено %d новых новостей", len(new_items))
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        raise SystemExit(run())
    except Exception:
        LOGGER.exception("Критическая ошибка агента")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
