"""Beállítások környezeti változókból."""
from __future__ import annotations

import os
import re
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
class User:
    name: str
    secret: str  # sima jelszó vagy pbkdf2-... hash (python -m app.jelszo)
    kindle: str | None = None


@dataclass
class Settings:
    title: str = "Polc"
    feeds: list[Feed] = field(default_factory=list)
    data_dir: Path = Path("/data")
    secret_key: str = ""
    convert_enabled: bool = True
    ebook_convert: str = "ebook-convert"
    convert_timeout: int = 300
    convert_profile: str = "kindle_pw3"
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
    users: dict[str, User] = field(default_factory=dict)

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


def _parse_users(text: str) -> dict[str, User]:
    """Formátum: nev:jelszo[:kindle@kindle.com] – vesszővel, pontosvesszővel vagy soronként elválasztva."""
    users: dict[str, User] = {}
    for raw in re.split(r"[,;\n]", text or ""):
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        parts = line.split(":")
        name, secret = parts[0].strip(), parts[1].strip()
        kindle = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        if name and secret:
            users[name.lower()] = User(name, secret, kindle)
    return users


def _load_users() -> dict[str, User]:
    users = _parse_users(os.environ.get("USERS", ""))
    path = os.environ.get("USERS_FILE")
    if path and Path(path).exists():
        users.update(_parse_users(Path(path).read_text(encoding="utf-8")))
    return users


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
        convert_profile=os.environ.get("CONVERT_PROFILE", "kindle_pw3"),
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
        users=_load_users(),
    )
