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

    @staticmethod
    def meta_args(cover: Path | None, title: str | None, authors: str | None,
                  series: str | None, index: str | None) -> list[str]:
        """Borító és metaadat beégetése (a Kindle a fájlba ágyazott borítót mutatja)."""
        args: list[str] = []
        if cover and cover.exists() and cover.stat().st_size > 0:
            args += ["--cover", str(cover)]
        if title:
            args += ["--title", title]
        if authors:
            args += ["--authors", authors.replace(", ", " & ")]
        if series:
            args += ["--series", series]
            if index:
                args += ["--series-index", index]
        return args

    async def _run(self, cmd: list[str]) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.s.convert_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("A feldolgozás túl sokáig tartott.")
        if proc.returncode != 0:
            raise RuntimeError("Sikertelen feldolgozás. " + (out or b"").decode(errors="replace")[-400:])
        return out

    async def embed_meta(self, book: Path, extra: list[str]) -> None:
        """EPUB-ba ágyazza a borítót (e-mailes küldéshez), ebook-meta-val."""
        tool = shutil.which(str(Path(self.s.ebook_convert).with_name("ebook-meta"))) or shutil.which("ebook-meta")
        if not tool or not extra:
            return
        async with self.lock:
            await self._run([tool, str(book), *extra])

    async def convert(self, src: Path, dst: Path, extra: list[str] | None = None) -> None:
        async with self.lock:
            if dst.exists():
                return
            tmp = dst.with_suffix(".tmp" + dst.suffix)
            proc = await asyncio.create_subprocess_exec(
                self.s.ebook_convert, str(src), str(tmp), *(extra or []),
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


# ---------------------------------------------------------------- Kindle személyes dokumentum

def kindle_personal_doc(path: Path) -> bool:
    """MOBI/AZW3 átjelölése személyes dokumentumra (PDOC), helyben, a fájlméret változtatása nélkül.

    A Calibre-ből jövő Kindle-fájlok "EBOK" típusúak, ál-ASIN-nel (113/504 EXTH). Ilyenkor a Kindle
    az Amazontól kéri a borítót, nem kapja meg, és üres marad. PDOC-ként a beágyazott borítót mutatja.
    """
    import struct

    data = bytearray(path.read_bytes())
    if len(data) < 86 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
        return False
    nrec = struct.unpack_from(">H", data, 76)[0]
    changed = False
    for i in range(nrec):
        off = struct.unpack_from(">I", data, 78 + i * 8)[0]
        if off + 24 > len(data) or data[off + 16:off + 20] != b"MOBI":
            continue
        mobi_len = struct.unpack_from(">I", data, off + 20)[0]
        p = off + 16 + mobi_len
        if data[p:p + 4] != b"EXTH":
            continue
        count = struct.unpack_from(">I", data, p + 8)[0]
        q = p + 12
        for _ in range(count):
            if q + 8 > len(data):
                break
            code, length = struct.unpack_from(">II", data, q)
            if length < 8:
                break
            if code == 501 and data[q + 8:q + 12] == b"EBOK":
                data[q + 8:q + 12] = b"PDOC"
                changed = True
            elif code in (113, 504):
                # ál-ASIN "kikapcsolása": ismeretlen kódra írjuk, amit a Kindle figyelmen kívül hagy
                struct.pack_into(">I", data, q, 65000 + (code % 1000))
                changed = True
            q += length
    if changed:
        path.write_bytes(bytes(data))
    return changed


# ---------------------------------------------------------------- jelszavak

PBKDF2_ITER = 260_000


def hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITER) -> str:
    """pbkdf2-<iter>-<só hex>-<hash hex> (nincs benne $ vagy :, így env fájlban is biztonságos)."""
    import os as _os
    salt = salt or _os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2-{iterations}-{salt.hex()}-{dk.hex()}"


def verify_password(password: str, secret: str) -> bool:
    import hmac
    if secret.startswith("pbkdf2-"):
        try:
            _, it, salt, want = secret.split("-", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
        except ValueError:
            return False
        return hmac.compare_digest(dk.hex(), want)
    return hmac.compare_digest(password.encode(), secret.encode())


def secret_fingerprint(secret: str) -> str:
    """Rövid ujjlenyomat: jelszócsere után a régi munkamenetek érvénytelenek."""
    return hashlib.sha256(("polc|" + secret).encode()).hexdigest()[:16]


def to_jpeg(data: bytes, max_height: int = 1920) -> bytes:
    """Bármilyen borítóképet (WebP, PNG, ...) RGB JPEG-gé alakít a Kindle-fájlokba ágyazáshoz."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if img.height > max_height:
        img = img.resize((max(1, int(img.width * max_height / img.height)), max_height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
