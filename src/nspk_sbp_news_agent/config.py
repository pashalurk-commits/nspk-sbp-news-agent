from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    smtp_timeout: int
    mail_from: str
    mail_to: tuple[str, ...]
    max_age_hours: int
    limit_per_brand: int
    state_file: Path
    dry_run: bool
    groq_api_key: str
    groq_model: str
    summarize_enabled: bool

    @classmethod
    def from_env(cls) -> "Settings":
        dry_run = _as_bool(os.getenv("DRY_RUN", "false"))
        smtp_use_ssl = _as_bool(os.getenv("SMTP_USE_SSL") or "false")
        tls_raw = os.getenv("SMTP_USE_TLS")
        if tls_raw is None or tls_raw.strip() == "":
            smtp_use_tls = not smtp_use_ssl
        else:
            smtp_use_tls = _as_bool(tls_raw)
        settings = cls(
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT") or "587"),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_use_tls=smtp_use_tls,
            smtp_use_ssl=smtp_use_ssl,
            smtp_timeout=int(os.getenv("SMTP_TIMEOUT") or "60"),
            mail_from=os.getenv("MAIL_FROM", ""),
            mail_to=tuple(
                address.strip()
                for address in os.getenv("MAIL_TO", "").split(",")
                if address.strip()
            ),
            max_age_hours=int(os.getenv("NEWS_MAX_AGE_HOURS", "48")),
            limit_per_brand=int(os.getenv("NEWS_LIMIT_PER_BRAND", "20")),
            state_file=Path(os.getenv("STATE_FILE", "state/sent.json")),
            dry_run=dry_run,
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
            or "llama-3.1-8b-instant",
            summarize_enabled=_as_bool(os.getenv("SUMMARIZE_ENABLED", "true")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.max_age_hours <= 0:
            raise ValueError("NEWS_MAX_AGE_HOURS должен быть больше нуля")
        if self.limit_per_brand <= 0:
            raise ValueError("NEWS_LIMIT_PER_BRAND должен быть больше нуля")
        if self.smtp_timeout <= 0:
            raise ValueError("SMTP_TIMEOUT должен быть больше нуля")
        if self.smtp_use_ssl and self.smtp_use_tls:
            raise ValueError("Задайте либо SMTP_USE_SSL, либо SMTP_USE_TLS, не оба")
        if self.dry_run:
            return

        required = {
            "SMTP_HOST": self.smtp_host,
            "MAIL_FROM": self.mail_from,
            "MAIL_TO": self.mail_to,
        }
        missing = [name for name, value in required.items() if not value]
        if self.smtp_user and not self.smtp_password:
            missing.append("SMTP_PASSWORD")
        if self.summarize_enabled and not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if missing:
            raise ValueError(
                "Не заданы обязательные переменные: " + ", ".join(missing)
            )
