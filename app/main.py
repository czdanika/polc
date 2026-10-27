"""Polc – magyar nyelvű, Kindle-barát OPDS proxy."""
from __future__ import annotations

import asyncio
import time
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import __version__
from .config import Feed, Settings, load_settings
from .opds import Client, Entry, NeedLogin, UpstreamError, search_path
from .services import (KINDLE_OK, Converter, Covers, Vault, ascii_name, kindle_personal_doc, secret_fingerprint,
                       send_mail, verify_password)

BASE = Path(__file__).parent
COOKIE = "polc"
VIEW_COOKIE = "polc_nezet"
YEAR = 60 * 60 * 24 * 365
VIEWS = {"lista": "Lista", "racs": "Borítók", "tomor": "Tömör"}
DEVICE_COOKIE = "polc_eszkoz"
DEVICES = {"uj": "Új Kindle (2012 után, AZW3)", "regi": "Régi Kindle (csak MOBI)"}
CONVERT_SOURCES = ("epub", "azw3", "fb2", "docx", "rtf", "htm", "html", "kepub")
# Kindle 1/2/DX és Kindle Keyboard: "Kindle/1.0", "Kindle/2.0", "Kindle/3.0" (a "+" nélküli 3.0)
OLD_KINDLE_UA = __import__("re").compile(r"Kindle/(?:[12]\.\d|3\.0(?!\+))")
MIME = {
    "azw3": "application/vnd.amazon.ebook",
    "mobi": "application/x-mobipocket-ebook",
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "txt": "text/plain",
}


def disposition(filename: str) -> dict:
    # A Kindle böngészője a sima filename="..." formát érti (az RFC 5987-es filename*-ot nem)
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    s = settings or load_settings()
    client = Client(ttl=s.cache_ttl, transport=transport)
    vault = Vault(s.secret_key)
    conv = Converter(s)
    covers = Covers(s)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await client.close()

    app = FastAPI(title="Polc", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    tpl = Jinja2Templates(directory=str(BASE / "templates"))

    # ------------------------------------------------------------ segédek

    def q(p: str | None) -> str:
        return quote(p or "", safe="")

    def u_feed(fid: int, p: str | None) -> str:
        return f"/k/{fid}?p={q(p)}"

    def u_book(fid: int, p: str, eid: str) -> str:
        return f"/konyv/{fid}?" + urlencode({"p": p, "id": eid})

    def u_cover(fid: int, p: str | None, w: int = 200) -> str:
        return f"/borito/{fid}?" + urlencode({"p": p or "", "w": w})

    def u_dl(fid: int, e: Entry, ext: str, convert: str | None = None) -> str:
        a = e.fmt(ext)
        params = {"p": a.path if a else "", "e": ext, "n": dl_name(e)}
        if convert:
            params["k"] = convert
            params.update({"t": e.title, "a": e.author_str})
            if e.cover:
                params["c"] = e.cover
            if e.series:
                params["s"] = e.series
                if e.series_index:
                    params["i"] = e.series_index
        return f"/letolt/{fid}?" + urlencode(params)

    def dl_name(e: Entry) -> str:
        who = e.authors[0] if e.authors else ""
        return ascii_name(f"{who} - {e.title}" if who else e.title)

    def kindle_choice(fid: int, e: Entry, device: str = "uj") -> tuple[str, str] | None:
        """A legjobb közvetlen Kindle-letöltés: (felirat, url)."""
        if device == "regi":
            # régi Kindle böngészője csak .mobi / .azw / .prc / .txt fájlt tölt le, KF8-at (AZW3) nem
            if e.fmt("mobi"):
                return "Letöltés (MOBI)", u_dl(fid, e, "mobi")
            if conv.available:
                for src in CONVERT_SOURCES:
                    if e.fmt(src):
                        return "Letöltés Kindle-re (MOBI)", u_dl(fid, e, src, "mobi")
            if e.fmt("txt"):
                return "Letöltés (TXT)", u_dl(fid, e, "txt")
            return None
        if e.fmt("azw3"):
            return "Letöltés (AZW3)", u_dl(fid, e, "azw3")
        if e.fmt("epub") and conv.available:
            return "Letöltés Kindle-re", u_dl(fid, e, "epub", "azw3")
        if e.fmt("mobi"):
            return "Letöltés (MOBI)", u_dl(fid, e, "mobi")
        if e.fmt("pdf"):
            return "Letöltés (PDF)", u_dl(fid, e, "pdf")
        return None

    def mail_choice(e: Entry):
        for ext in KINDLE_OK:
            a = e.fmt(ext)
            if a:
                return a
        return None

    def get_feed(fid: int) -> Feed | None:
        return s.feeds[fid] if 0 <= fid < len(s.feeds) else None

    def session(request: Request) -> dict:
        return vault.load(request.cookies.get(COOKIE))

    def auth_for(request: Request, feed: Feed):
        if feed.username:
            return (feed.username, feed.password or "")
        cred = session(request).get("a", {}).get(str(feed.id))
        return tuple(cred) if cred else None

    def device_of(request: Request) -> str:
        d = request.cookies.get(DEVICE_COOKIE)
        if d in DEVICES:
            return d
        return "regi" if OLD_KINDLE_UA.search(request.headers.get("user-agent", "")) else "uj"

    def view_of(request: Request) -> str:
        v = request.query_params.get("nezet") or request.cookies.get(VIEW_COOKIE) or s.default_view
        return v if v in VIEWS else s.default_view

    def current_user(request: Request):
        """Bejelentkezett Polc-felhasználó (csak ha USERS be van állítva)."""
        if not s.users:
            return None
        sess = session(request)
        u = s.users.get(str(sess.get("u", "")).lower())
        if u and sess.get("uv") == secret_fingerprint(u.secret):
            return u
        return None

    def kindle_email(request: Request) -> str | None:
        u = current_user(request)
        return session(request).get("kindle") or (u.kindle if u else None) or s.kindle_email

    def save_session(request: Request, resp: Response, sess: dict) -> Response:
        resp.set_cookie(COOKIE, vault.dump(sess), max_age=YEAR, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
        return resp

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        base = {
            "s": s,
            "title": s.title,
            "views": VIEWS,
            "view": view_of(request),
            "u_feed": u_feed,
            "u_book": u_book,
            "u_cover": u_cover,
            "u_dl": u_dl,
            "kindle_choice": lambda fid, e: kindle_choice(fid, e, device_of(request)),
            "devices": DEVICES,
            "device": device_of(request),
            "mail_choice": mail_choice,
            "kindle_email": kindle_email(request),
            "user": current_user(request),
            "version": __version__,
            "here": str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        }
        base.update(ctx)
        resp = tpl.TemplateResponse(request, name, base, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        nv = request.query_params.get("nezet")
        if nv in VIEWS:
            resp.set_cookie(VIEW_COOKIE, nv, max_age=YEAR, samesite="lax")
        return resp

    def message(request: Request, title: str, text: str, status: int = 200, back: str | None = None):
        return render(request, "message.html", status=status, heading=title, text=text, back=back)

    def to_login(fid: int, nxt: str) -> RedirectResponse:
        return RedirectResponse(f"/belepes/{fid}?" + urlencode({"vissza": nxt}), status_code=303)

    # ------------------------------------------------------------ Polc-felhasználók

    PUBLIC = ("/bejelentkezes", "/egeszseg")
    failures: dict[str, list[float]] = {}

    @app.middleware("http")
    async def require_user(request: Request, call_next):
        if s.users and not request.url.path.startswith(PUBLIC) and not current_user(request):
            nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            if request.method != "GET":
                nxt = "/"
            return RedirectResponse("/bejelentkezes?" + urlencode({"vissza": nxt}), status_code=303)
        return await call_next(request)

    def client_ip(request: Request) -> str:
        return (request.headers.get("cf-connecting-ip")
                or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else "?"))

    def blocked(ip: str) -> bool:
        now = time.time()
        recent = [t for t in failures.get(ip, []) if now - t < 600]
        failures[ip] = recent
        return len(recent) >= 5

    @app.get("/bejelentkezes", response_class=HTMLResponse)
    async def user_login_form(request: Request, vissza: str = "/"):
        if not s.users or current_user(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "user_login.html", vissza=vissza, error=None)

    @app.post("/bejelentkezes", response_class=HTMLResponse)
    async def user_login(request: Request, felhasznalo: str = Form(""), jelszo: str = Form(""),
                         vissza: str = Form("/")):
        if not s.users:
            return RedirectResponse("/", status_code=303)
        if not vissza.startswith("/") or vissza.startswith("//"):
            vissza = "/"
        ip = client_ip(request)
        if blocked(ip):
            return render(request, "user_login.html", 429, vissza=vissza,
                          error="Túl sok sikertelen próbálkozás. Próbáld újra 10 perc múlva.")
        u = s.users.get(felhasznalo.strip().lower())
        if not u or not await asyncio.to_thread(verify_password, jelszo, u.secret):
            failures.setdefault(ip, []).append(time.time())
            await asyncio.sleep(1)
            return render(request, "user_login.html", 401, vissza=vissza,
                          error="Hibás felhasználónév vagy jelszó.")
        failures.pop(ip, None)
        sess = session(request)
        sess.update({"u": u.name.lower(), "uv": secret_fingerprint(u.secret)})
        return save_session(request, RedirectResponse(vissza, status_code=303), sess)

    # ------------------------------------------------------------ oldalak

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if len(s.feeds) == 1:
            return RedirectResponse(u_feed(0, s.feeds[0].root_path), status_code=302)
        return render(request, "home.html", feeds=s.feeds)

    @app.get("/k/{fid}", response_class=HTMLResponse)
    async def browse(request: Request, fid: int, p: str = ""):
        feed = get_feed(fid)
        if not feed:
            return message(request, "Nincs ilyen könyvtár", "Ellenőrizd a címet.", 404, "/")
        path = p or feed.root_path
        try:
            page = await client.feed(feed, path, auth_for(request, feed))
        except NeedLogin:
            return to_login(fid, str(request.url.path) + "?" + str(request.url.query))
        except UpstreamError as e:
            return message(request, "Hiba", str(e), 502, "/")
        is_root = path == feed.root_path
        return render(request, "feed.html", feed=feed, page=page, path=path, is_root=is_root,
                      heading=(feed.name if is_root else page.title) or feed.name)

    @app.get("/kereses/{fid}", response_class=HTMLResponse)
    async def search(request: Request, fid: int, q: str = ""):
        feed = get_feed(fid)
        if not feed:
            return message(request, "Nincs ilyen könyvtár", "", 404, "/")
        q = q.strip()
        if not q:
            return RedirectResponse(u_feed(fid, feed.root_path), status_code=303)
        auth = auth_for(request, feed)
        try:
            root = await client.feed(feed, feed.root_path, auth)
            if not root.search_template:
                return message(request, "A keresés nem támogatott",
                               "Ez a könyvtár nem kínál keresést az OPDS katalógusában.", 400,
                               u_feed(fid, feed.root_path))
            path = search_path(root.search_template, q)
            page = await client.feed(feed, path, auth)
        except NeedLogin:
            return to_login(fid, str(request.url.path) + "?" + str(request.url.query))
        except UpstreamError as e:
            return message(request, "Hiba", str(e), 502, "/")
        return render(request, "feed.html", feed=feed, page=page, path=path, is_root=False,
                      heading=f"Keresés: „{q}”", query=q)

    @app.get("/konyv/{fid}", response_class=HTMLResponse)
    async def book(request: Request, fid: int, p: str, id: str):
        feed = get_feed(fid)
        if not feed:
            return message(request, "Nincs ilyen könyvtár", "", 404, "/")
        try:
            page = await client.feed(feed, p, auth_for(request, feed))
        except NeedLogin:
            return to_login(fid, str(request.url.path) + "?" + str(request.url.query))
        except UpstreamError as e:
            return message(request, "Hiba", str(e), 502, "/")
        entry = next((e for e in page.entries if e.id == id), None)
        if not entry:
            return message(request, "Nem található", "A könyv már nincs ezen a listán.", 404, u_feed(fid, p))
        sent = request.query_params.get("kuldve")
        return render(request, "book.html", feed=feed, e=entry, path=p, list_title=page.title,
                      can_mail=s.mail_enabled, sent=sent)

    @app.get("/borito/{fid}")
    async def cover(request: Request, fid: int, p: str, w: int = 200):
        feed = get_feed(fid)
        if not feed or not p:
            return Response(status_code=404)
        w = max(40, min(w, 800))
        target = covers.path(f"{feed.origin}{p}", w)
        headers = {"Cache-Control": "public, max-age=604800"}
        if target.exists():
            return FileResponse(target, media_type="image/jpeg", headers=headers)
        try:
            data, _ = await client.get(feed, p, auth_for(request, feed), cache=False)
            out = await asyncio.to_thread(covers.process, data, w)
        except NeedLogin:
            return Response(status_code=401)
        except Exception:
            return Response(status_code=404)
        target.write_bytes(out)
        covers.prune()
        return Response(out, media_type="image/jpeg", headers=headers)

    async def fetch_to_file(feed: Feed, path: str, auth, suffix: str) -> Path:
        fd, name = tempfile.mkstemp(suffix="." + suffix, dir=s.data_dir)
        tmp = Path(name)
        try:
            with open(fd, "wb") as fh:
                async with client.stream(feed, path, auth) as r:
                    if r.status_code in (401, 403):
                        raise NeedLogin()
                    if r.status_code >= 400:
                        raise UpstreamError(f"A könyvtár hibát adott: HTTP {r.status_code}")
                    async for chunk in r.aiter_bytes(65536):
                        fh.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    async def fetch_cover(feed: Feed, path: str | None, auth) -> Path | None:
        if not path:
            return None
        try:
            data, _ = await client.get(feed, path, auth, cache=False)
        except (UpstreamError, NeedLogin):
            return None
        if len(data) < 100:
            return None
        fd, name = tempfile.mkstemp(suffix=".jpg", dir=s.data_dir)
        with open(fd, "wb") as fh:
            fh.write(data)
        return Path(name)

    @app.get("/letolt/{fid}")
    async def download(request: Request, fid: int, p: str, e: str = "epub", n: str = "konyv", k: str | None = None,
                       c: str | None = None, t: str | None = None, a: str | None = None,
                       s_: str | None = Query(None, alias="s"), i: str | None = None):
        feed = get_feed(fid)
        if not feed or not p:
            return message(request, "Hiba", "Hiányzó letöltési cím.", 400, "/")
        auth = auth_for(request, feed)
        ext = ascii_name(e, 8).lower() or "bin"
        name = ascii_name(n)
        try:
            if k in ("azw3", "mobi") and conv.available:
                out = conv.cached(f"v3|{feed.origin}{p}|{c}|{t}", k)
                if not out.exists():
                    src = await fetch_to_file(feed, p, auth, ext)
                    cov = await fetch_cover(feed, c, auth)
                    try:
                        opts = ["--toc-title", "Tartalom",
                                # nincs ál-ASIN/EBOK: a Kindle így a beágyazott borítót mutatja
                                # (EBOK esetén az Amazontól próbálja letölteni, és üres marad)
                                "--share-not-sync"]
                        if k == "mobi":  # régi Kindle: klasszikus MOBI (KF8 nélkül), 600x800 profil
                            opts += ["--output-profile", "kindle", "--mobi-file-type", "old"]
                        else:
                            opts += ["--output-profile", s.convert_profile]
                        await conv.convert(src, out, opts + conv.meta_args(cov, t, a, s_, i))
                        await asyncio.to_thread(kindle_personal_doc, out)
                    finally:
                        src.unlink(missing_ok=True)
                        if cov:
                            cov.unlink(missing_ok=True)
                return FileResponse(out, media_type=MIME[k], headers=disposition(f"{name}.{k}"))
            src = await fetch_to_file(feed, p, auth, ext)
            if ext in ("azw3", "azw", "mobi"):
                try:
                    await asyncio.to_thread(kindle_personal_doc, src)
                except Exception:
                    pass  # hibás fejléc esetén az eredeti fájl megy
        except NeedLogin:
            return to_login(fid, request.headers.get("referer") or "/")
        except UpstreamError as err:
            return message(request, "Letöltési hiba", str(err), 502, request.headers.get("referer"))
        except RuntimeError as err:
            return message(request, "Konvertálási hiba", str(err), 500, request.headers.get("referer"))
        return FileResponse(src, media_type=MIME.get(ext, "application/octet-stream"),
                            headers=disposition(f"{name}.{ext}"),
                            background=BackgroundTask(src.unlink, missing_ok=True))

    @app.post("/kuldes/{fid}")
    async def send(request: Request, fid: int, p: str = Form(...), id: str = Form(...), lista: str = Form(...)):
        feed = get_feed(fid)
        back = u_book(fid, lista, id)
        to = kindle_email(request)
        if not feed or not s.mail_enabled:
            return message(request, "Nem elérhető", "Az e-mail küldés nincs beállítva.", 400, back)
        if not to:
            return message(request, "Hiányzó Kindle e-mail cím",
                           "Add meg a Kindle e-mail címedet a Beállítások oldalon.", 400, "/beallitasok")
        auth = auth_for(request, feed)
        try:
            page = await client.feed(feed, lista, auth)
            entry = next((x for x in page.entries if x.id == id), None)
            acq = mail_choice(entry) if entry else None
            if not entry or not acq or acq.path != p:
                return message(request, "Hiba", "Ez a formátum nem küldhető Kindle-re.", 400, back)
            data, _ = await client.get(feed, acq.path, auth, cache=False)
            if len(data) > 49 * 1024 * 1024:
                return message(request, "Túl nagy fájl", "Az Amazon legfeljebb 50 MB-os mellékletet fogad.", 400, back)
            if acq.ext == "epub" and conv.available:
                # borító + metaadat beágyazása, hogy a Kindle könyvtárában is legyen borító
                fd, name = tempfile.mkstemp(suffix=".epub", dir=s.data_dir)
                book_tmp = Path(name)
                with open(fd, "wb") as fh:
                    fh.write(data)
                cov = await fetch_cover(feed, entry.cover, auth)
                try:
                    await conv.embed_meta(book_tmp, conv.meta_args(cov, entry.title, entry.author_str,
                                                                   entry.series, entry.series_index))
                    data = book_tmp.read_bytes()
                except RuntimeError:
                    pass  # ha nem sikerül, az eredeti fájlt küldjük
                finally:
                    book_tmp.unlink(missing_ok=True)
                    if cov:
                        cov.unlink(missing_ok=True)
            await asyncio.to_thread(send_mail, s, to, f"{dl_name(entry)}.{acq.ext}", data, acq.mime)
        except NeedLogin:
            return to_login(fid, back)
        except UpstreamError as err:
            return message(request, "Hiba", str(err), 502, back)
        except Exception as err:  # SMTP hibák
            return message(request, "Küldési hiba", f"Nem sikerült elküldeni: {err}", 500, back)
        return RedirectResponse(back + "&kuldve=1", status_code=303)

    # ------------------------------------------------------------ belépés, beállítások

    @app.get("/belepes/{fid}", response_class=HTMLResponse)
    async def login_form(request: Request, fid: int, vissza: str = "/"):
        feed = get_feed(fid)
        if not feed:
            return message(request, "Nincs ilyen könyvtár", "", 404, "/")
        return render(request, "login.html", feed=feed, vissza=vissza, error=None)

    @app.post("/belepes/{fid}", response_class=HTMLResponse)
    async def login(request: Request, fid: int, felhasznalo: str = Form(""), jelszo: str = Form(""),
                    vissza: str = Form("/")):
        feed = get_feed(fid)
        if not feed:
            return message(request, "Nincs ilyen könyvtár", "", 404, "/")
        if not vissza.startswith("/") or vissza.startswith("//"):
            vissza = "/"
        auth = (felhasznalo.strip(), jelszo)
        try:
            await client.get(feed, feed.root_path, auth, cache=False)
        except NeedLogin:
            return render(request, "login.html", 401, feed=feed, vissza=vissza,
                          error="Hibás felhasználónév vagy jelszó.")
        except UpstreamError as e:
            return render(request, "login.html", 502, feed=feed, vissza=vissza, error=str(e))
        sess = session(request)
        sess.setdefault("a", {})[str(fid)] = list(auth)
        return save_session(request, RedirectResponse(vissza, status_code=303), sess)

    @app.get("/kilepes")
    async def logout(request: Request):
        sess = session(request)
        for k in ("a", "u", "uv"):
            sess.pop(k, None)
        return save_session(request, RedirectResponse("/", status_code=303), sess)

    @app.get("/beallitasok", response_class=HTMLResponse)
    async def settings_page(request: Request):
        sess = session(request)
        return render(request, "settings.html", own_email=sess.get("kindle", ""),
                      ua=request.headers.get("user-agent", "")[:300],
                      logged_in=bool(sess.get("a")) or bool(current_user(request)), can_mail=s.mail_enabled, can_convert=conv.available,
                      saved=request.query_params.get("mentve"))

    @app.post("/beallitasok")
    async def settings_save(request: Request, kindle: str = Form(""), nezet: str = Form(""),
                            eszkoz: str = Form("")):
        sess = session(request)
        kindle = kindle.strip()
        if kindle:
            sess["kindle"] = kindle
        else:
            sess.pop("kindle", None)
        resp = save_session(request, RedirectResponse("/beallitasok?mentve=1", status_code=303), sess)
        if nezet in VIEWS:
            resp.set_cookie(VIEW_COOKIE, nezet, max_age=YEAR, samesite="lax")
        if eszkoz in DEVICES:
            resp.set_cookie(DEVICE_COOKIE, eszkoz, max_age=YEAR, samesite="lax")
        return resp

    @app.get("/egeszseg")
    async def health():
        return {"ok": True, "verzio": __version__, "konyvtarak": len(s.feeds), "konverzio": conv.available,
                "email": s.mail_enabled, "felhasznalok": len(s.users)}

    return app

