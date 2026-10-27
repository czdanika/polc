"""Süti-titkosítás, konverzió, e-mail küldés, borító-feldolgozás."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import shutil
import smtplib
import ssl
import unicodedata
from email.message import EmailMessage
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings


# ---------------------------------------------------------------- sütik

class Vault:
    def __init__(self, secret: str) -> None:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self.f = Fernet(key)

    def dump(self, data: dict) -> str:
        return self.f.encrypt(json.dumps(data).encode()).decode()

    def load(self, token: str | None) -> dict:
        if not token:
            return {}
        try:
            return json.loads(self.f.decrypt(token.encode()))
        except (InvalidToken, ValueError):
            return {}


# ---------------------------------------------------------------- fájlnév

def ascii_name(text: str, maxlen: int = 80) -> str:
    """Ékezetek nélküli, Kindle-barát fájlnév (á->a, ő->o ...)."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9 ._,()\-]+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return (t[:maxlen].strip() or "konyv")


# ---------------------------------------------------------------- konverzió

class Converter:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.dir = s.data_dir / "konvertalt"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Semaphore(1)

    @property
    def available(self) -> bool:
        return self.s.convert_enabled and shutil.which(self.s.ebook_convert) is not None

    def cached(self, key: str, ext: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.dir / f"{h}.{ext}"

    async def convert(self, src: Path, dst: Path) -> None:
        async with self.lock:
            if dst.exists():
                return
            tmp = dst.with_suffix(".tmp" + dst.suffix)
            proc = await asyncio.create_subprocess_exec(
                self.s.ebook_convert, str(src), str(tmp),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.s.convert_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("A konvertálás túl sokáig tartott.")
            if proc.returncode != 0 or not tmp.exists():
                tail = (out or b"").decode(errors="replace")[-400:]
                raise RuntimeError(f"Sikertelen konvertálás. {tail}")
            tmp.replace(dst)
        self._prune()

    def _prune(self, keep_mb: int = 500) -> None:
        files = sorted(self.dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for f in files:
            total += f.stat().st_size
            if total > keep_mb * 1024 * 1024:
                f.unlink(missing_ok=True)


# ---------------------------------------------------------------- e-mail

KINDLE_OK = ("epub", "pdf", "docx", "txt", "rtf", "htm", "html")


def send_mail(s: Settings, to: str, filename: str, data: bytes, mime: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Polc – könyv küldése"
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg.set_content("A könyvet a Polc küldte. Jó olvasást!")
    maintype, _, subtype = (mime or "application/octet-stream").partition("/")
    msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                       filename=filename)
    ctx = ssl.create_default_context()
    if s.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=60)
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60)
    try:
        if s.smtp_security == "starttls":
            server.starttls(context=ctx)
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ---------------------------------------------------------------- borítók

class Covers:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.dir = s.data_dir / "boritok"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, key: str, w: int) -> Path:
        h = hashlib.sha256(f"{key}|{w}|{self.s.cover_grayscale}".encode()).hexdigest()[:24]
        return self.dir / f"{h}.jpg"

    def process(self, data: bytes, w: int) -> bytes:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img = img.convert("L" if self.s.cover_grayscale else "RGB")
        if img.width > w:
            h = int(img.height * w / img.width)
            img = img.resize((w, max(1, h)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=78, optimize=True, progressive=False)
        return out.getvalue()

    def prune(self, keep_mb: int = 200) -> None:
        files = sorted(self.dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for f in files:
            total += f.stat().st_size
            if total > keep_mb * 1024 * 1024:
                f.unlink(missing_ok=True)
