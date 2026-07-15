from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import NewsItem


def load_sent_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать файл состояния {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Некорректный формат файла состояния {path}")
    return set(data)


def only_new(items: list[NewsItem], sent_keys: set[str]) -> list[NewsItem]:
    return [item for item in items if item.key not in sent_keys]


def save_sent_items(
    path: Path,
    previous: dict[str, str],
    sent_items: list[NewsItem],
    now: Optional[datetime] = None,
    retention_days: int = 90,
) -> None:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    retained: dict[str, str] = {}
    for key, value in previous.items():
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if timestamp >= cutoff:
            retained[key] = timestamp.isoformat()

    retained.update({item.key: now.isoformat() for item in sent_items})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_sent_history(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать файл состояния {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Некорректный формат файла состояния {path}")
    return {str(key): str(value) for key, value in data.items()}
