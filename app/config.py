"""Beállítások környezeti változókból."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "igen", "on")


@dataclass
class Feed:
    id: int
    name: str
    url: str
    username: str | None = None
    password: str | None = None

    @property
    def origin(self) -> str:
        s = urlsplit(self.url)
        return f"{s.scheme}://{s.netloc}"

    @property
    def root_path(self) -> str:
        s = urlsplit(self.url)
        return (s.path or "/") + (f"?{s.query}" if s.query else "")


@dataclass
class Settings:
    title: str = "Polc"
    feeds: list[Feed] = field(default_factory=list)
    data_dir: Path = Path("/data")
    secret_key: str = ""
    convert_enabled: bool = True
    ebook_convert: str = "ebook-convert"
    convert_timeout: int = 300
    cover_grayscale: bool = True
    default_view: str = "lista"
    cache_ttl: int = 120
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: str = "starttls"  # starttls | ssl | none
    kindle_email: str | None = None

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


def _load_feeds() -> list[Feed]:
    feeds: list[Feed] = []
    # Első feed: FEED_URL, további: FEED_2_URL ... FEED_9_URL
    prefixes = ["FEED_"] + [f"FEED_{i}_" for i in range(2, 10)]
    for prefix in prefixes:
        url = os.environ.get(prefix + "URL")
        if not url:
            continue
        feeds.append(
            Feed(
                id=len(feeds),
                name=os.environ.get(prefix + "NAME") or f"Könyvtár {len(feeds) + 1}",
                url=url.strip(),
                username=os.environ.get(prefix + "USERNAME") or None,
                password=os.environ.get(prefix + "PASSWORD") or None,
            )
        )
    return feeds


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        key_file = data_dir / "secret.key"
        if key_file.exists():
            secret = key_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(48)
            key_file.write_text(secret)
            try:
                key_file.chmod(0o600)
            except OSError:
                pass
    view = os.environ.get("DEFAULT_VIEW", "lista")
    return Settings(
        title=os.environ.get("TITLE", "Polc"),
        feeds=_load_feeds(),
        data_dir=data_dir,
        secret_key=secret,
        convert_enabled=_bool("CONVERT_ENABLED", True),
        ebook_convert=os.environ.get("EBOOK_CONVERT", "ebook-convert"),
        convert_timeout=int(os.environ.get("CONVERT_TIMEOUT", "300")),
        cover_grayscale=_bool("COVER_GRAYSCALE", True),
        default_view=view if view in ("lista", "racs", "tomor") else "lista",
        cache_ttl=int(os.environ.get("CACHE_TTL", "120")),
        smtp_host=os.environ.get("SMTP_HOST") or None,
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        smtp_from=os.environ.get("SMTP_FROM") or None,
        smtp_security=(os.environ.get("SMTP_SECURITY") or "starttls").lower(),
        kindle_email=os.environ.get("KINDLE_EMAIL") or None,
    )
