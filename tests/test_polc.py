"""Polc tesztek a mock Calibre-Web feed ellen (pytest)."""
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Feed, Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import ascii_name  # noqa: E402
from tests import mock_calibre  # noqa: E402


@pytest.fixture
def client(tmp_path):
    s = Settings(feeds=[Feed(0, "Calibre", "http://calibre.local/opds")], data_dir=tmp_path,
                 secret_key="teszt", smtp_host="smtp.local", smtp_from="polc@example.com")
    app = create_app(s, transport=httpx.ASGITransport(app=mock_calibre.app))
    with TestClient(app) as c:
        yield c


def login(c):
    r = c.post("/belepes/0", data={"felhasznalo": "kindle", "jelszo": "titok", "vissza": "/"},
               follow_redirects=False)
    assert r.status_code == 303


def test_redirects_to_login(client):
    r = client.get("/k/0?p=%2Fopds", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/belepes/0")


def test_wrong_password(client):
    r = client.post("/belepes/0", data={"felhasznalo": "kindle", "jelszo": "rossz", "vissza": "/"})
    assert r.status_code == 401 and "Hibás" in r.text


def test_open_redirect_blocked(client):
    r = client.post("/belepes/0", data={"felhasznalo": "kindle", "jelszo": "titok", "vissza": "//evil.com"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_root_is_hungarian(client):
    login(client)
    r = client.get("/k/0?p=%2Fopds")
    for word in ("Népszerű könyvek", "Legújabb könyvek", "Szerzők", "Sorozatok"):
        assert word in r.text
    assert "Books ordered" not in r.text


def test_views_and_paging(client):
    login(client)
    for view, marker in (("lista", 'class="books"'), ("racs", 'class="grid"'), ("tomor", 'class="compact"')):
        r = client.get(f"/k/0?p=%2Fopds%2Fnew&nezet={view}")
        assert marker in r.text
    assert client.cookies.get("polc_nezet") == "tomor"
    assert "Következő" in r.text
    r2 = client.get("/k/0?p=%2Fopds%2Fnew%3Foffset%3D5")
    assert "Előző" in r2.text and "Tüskevár" in r2.text


def test_search_via_template(client):
    login(client)
    r = client.get("/kereses/0", params={"q": "alapítvány"})
    assert r.text.count('class="bt"') == 3


def test_book_detail_sanitized(client):
    login(client)
    r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-0"})
    assert "Eger ostroma" in r.text and "alert(1)" not in r.text
    r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-3"})
    assert "Alapítvány #1" in r.text and "SERIES:" not in r.text


def test_download_ascii_filename(client):
    login(client)
    r = client.get("/letolt/0", params={"p": "/opds/download/1/mobi/", "e": "mobi", "n": "Molnár Ferenc - A Pál utcai fiúk"})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="Molnar Ferenc - A Pal utcai fiuk.mobi"'


def test_ssrf_blocked(client):
    login(client)
    r = client.get("/k/0", params={"p": "http://evil.com/x"})
    assert r.status_code == 502


def test_cover_grayscale(client):
    login(client)
    r = client.get("/borito/0", params={"p": "/opds/cover/1", "w": 100})
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(r.content))
    assert im.mode == "L" and im.width == 100


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="Calibre nincs telepítve")
def test_convert_azw3(client):
    login(client)
    r = client.get("/letolt/0", params={"p": "/opds/download/0/epub/", "e": "epub", "n": "Egri csillagok", "k": "azw3"})
    assert r.status_code == 200 and r.content[60:68] == b"BOOKMOBI"


def test_send_mail(client, monkeypatch):
    sent = {}
    monkeypatch.setattr("app.main.send_mail", lambda s, to, fn, data, mime: sent.update(to=to, fn=fn, n=len(data)))
    login(client)
    client.post("/beallitasok", data={"kindle": "teszt@kindle.com", "nezet": "lista"})
    r = client.post("/kuldes/0", data={"p": "/opds/download/0/epub/", "id": "urn:uuid:book-0", "lista": "/opds/new"},
                    follow_redirects=False)
    assert r.status_code == 303 and "kuldve=1" in r.headers["location"]
    assert sent["to"] == "teszt@kindle.com" and sent["fn"] == "Gardonyi Geza - Egri csillagok.epub"


def test_ascii_name():
    assert ascii_name("Őszi éjjel izzik a galagonya / Ady") == "Oszi ejjel izzik a galagonya Ady"


@pytest.mark.skipif(not shutil.which("ebook-meta"), reason="Calibre nincs telepítve")
def test_azw3_has_cover_and_meta(client, tmp_path):
    import subprocess
    login(client)
    r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-3"})
    import re, html
    url = html.unescape(re.search(r'href="(/letolt/0\?[^"]*k=azw3[^"]*)"', r.text).group(1)) if "k=azw3" in r.text else None
    if url is None:  # az Alapítványnak van AZW3-ja, ezért az Egri csillagokat konvertáljuk
        r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-0"})
        url = html.unescape(re.search(r'href="(/letolt/0\?[^"]*k=azw3[^"]*)"', r.text).group(1))
    out = tmp_path / "k.azw3"
    out.write_bytes(client.get(url).content)
    cov = tmp_path / "c.jpg"
    meta = subprocess.run(["ebook-meta", str(out), f"--get-cover={cov}"], capture_output=True, text=True).stdout
    assert cov.exists() and cov.stat().st_size > 1000
    data = out.read_bytes()
    assert b"EBOK" not in data  # személyes dokumentum: a Kindle a beágyazott borítót használja
    assert "Egri csillagok" in meta and "Gárdonyi Géza" in meta


@pytest.mark.skipif(not shutil.which("ebook-meta"), reason="Calibre nincs telepítve")
def test_mail_epub_has_cover(client, monkeypatch):
    import io, zipfile
    sent = {}
    monkeypatch.setattr("app.main.send_mail", lambda s, to, fn, data, mime: sent.update(data=data))
    login(client)
    client.post("/beallitasok", data={"kindle": "teszt@kindle.com", "nezet": "lista"})
    client.post("/kuldes/0", data={"p": "/opds/download/0/epub/", "id": "urn:uuid:book-0", "lista": "/opds/new"})
    names = zipfile.ZipFile(io.BytesIO(sent["data"])).namelist()
    assert any("cover" in n.lower() and n.lower().endswith((".jpg", ".jpeg")) for n in names), names


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="Calibre nincs telepítve")
def test_existing_azw3_becomes_personal_doc(tmp_path):
    """A Calibre-ben már meglévő (EBOK, ál-ASIN-es) AZW3 PDOC-ra javítva, borító megmarad."""
    import subprocess
    from PIL import Image
    from app.services import kindle_personal_doc
    epub, cover, azw = tmp_path / "b.epub", tmp_path / "c.jpg", tmp_path / "b.azw3"
    epub.write_bytes(mock_calibre.make_epub(mock_calibre.BOOKS[0]))
    Image.new("RGB", (600, 900), (10, 120, 60)).save(cover)
    subprocess.run(["ebook-convert", str(epub), str(azw), "--cover", str(cover)], capture_output=True, check=True)
    assert b"EBOK" in azw.read_bytes()
    size = azw.stat().st_size
    assert kindle_personal_doc(azw)
    data = azw.read_bytes()
    assert b"EBOK" not in data and b"PDOC" in data and len(data) == size
    got = tmp_path / "got.jpg"
    subprocess.run(["ebook-meta", str(azw), f"--get-cover={got}"], capture_output=True)
    assert got.exists() and got.stat().st_size > 1000
    assert not kindle_personal_doc(azw)  # másodszor már nincs mit javítani


# ---------------------------------------------------------------- Polc-felhasználók

from app.config import User  # noqa: E402
from app.services import hash_password  # noqa: E402


def users_app(tmp_path, users):
    s = Settings(feeds=[Feed(0, "Calibre", "http://calibre.local/opds", "kindle", "titok")], data_dir=tmp_path,
                 secret_key="teszt", smtp_host="smtp.local", smtp_from="polc@example.com", users=users)
    return TestClient(create_app(s, transport=httpx.ASGITransport(app=mock_calibre.app)))


USERS = {"daniel": User("Daniel", hash_password("dani123"), "daniel@kindle.com"),
         "orsi": User("orsi", "orsi456")}


def test_users_gate_and_shared_calibre_login(tmp_path):
    with users_app(tmp_path, USERS) as c:
        r = c.get("/k/0?p=%2Fopds", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/bejelentkezes")
        assert c.get("/borito/0", params={"p": "/opds/cover/1"}, follow_redirects=False).status_code == 303
        assert c.get("/egeszseg").json()["felhasznalok"] == 2
        r = c.post("/bejelentkezes", data={"felhasznalo": "DANIEL", "jelszo": "dani123", "vissza": "/k/0?p=%2Fopds"},
                   follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/k/0?p=%2Fopds"
        r = c.get("/k/0?p=%2Fopds")  # Calibre-belépés nélkül, az env-ben megadott fiókkal
        assert "Népszerű könyvek" in r.text
        assert "Bejelentkezve: <b>Daniel</b>" in c.get("/beallitasok").text
        c.get("/kilepes")
        assert c.get("/k/0?p=%2Fopds", follow_redirects=False).status_code == 303


def test_users_plain_password_and_wrong(tmp_path):
    with users_app(tmp_path, USERS) as c:
        assert c.post("/bejelentkezes", data={"felhasznalo": "orsi", "jelszo": "rossz"}).status_code == 401
        r = c.post("/bejelentkezes", data={"felhasznalo": "orsi", "jelszo": "orsi456"}, follow_redirects=False)
        assert r.status_code == 303


def test_users_bruteforce_lockout(tmp_path, monkeypatch):
    async def nosleep(_):
        return None
    monkeypatch.setattr("app.main.asyncio.sleep", nosleep)
    with users_app(tmp_path, USERS) as c:
        for _ in range(5):
            c.post("/bejelentkezes", data={"felhasznalo": "daniel", "jelszo": "x"})
        r = c.post("/bejelentkezes", data={"felhasznalo": "daniel", "jelszo": "dani123"})
        assert r.status_code == 429


def test_users_password_change_invalidates_session(tmp_path):
    with users_app(tmp_path, USERS) as c:
        c.post("/bejelentkezes", data={"felhasznalo": "orsi", "jelszo": "orsi456"})
        cookie = c.cookies.get("polc")
    changed = {"orsi": User("orsi", "ujjelszo")}
    with users_app(tmp_path, changed) as c2:
        c2.cookies.set("polc", cookie)
        assert c2.get("/k/0?p=%2Fopds", follow_redirects=False).status_code == 303


def test_users_kindle_email_per_user(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr("app.main.send_mail", lambda s, to, fn, data, mime: sent.update(to=to))
    with users_app(tmp_path, USERS) as c:
        c.post("/bejelentkezes", data={"felhasznalo": "daniel", "jelszo": "dani123"})
        c.post("/kuldes/0", data={"p": "/opds/download/0/epub/", "id": "urn:uuid:book-0", "lista": "/opds/new"})
    assert sent["to"] == "daniel@kindle.com"


def test_parse_users_formats():
    from app.config import _parse_users
    u = _parse_users("a:1:a@kindle.com, b:2\n# komment\nc:3;")
    assert set(u) == {"a", "b", "c"} and u["a"].kindle == "a@kindle.com" and u["b"].kindle is None


# ---------------------------------------------------------------- régi Kindle (MOBI)

KEYBOARD_UA = ("Mozilla/5.0 (Linux; U; en-US) AppleWebKit/528.5+ (KHTML, like Gecko, Safari/528.5+) "
               "Version/4.0 Kindle/3.0 (screen 600x800; rotate)")
PW_UA = ("Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+ (KHTML, like Gecko) "
         "Version/5.0 Safari/533.2+ Kindle/3.0+")


def test_old_kindle_detected_by_user_agent(client):
    login(client)
    r = client.get("/k/0?p=%2Fopds%2Fnew&nezet=lista", headers={"User-Agent": KEYBOARD_UA})
    assert "Letöltés (AZW3)" not in r.text and "Letöltés (MOBI)" in r.text  # A Pál utcai fiúk: van MOBI
    r = client.get("/k/0?p=%2Fopds%2Fnew&nezet=lista", headers={"User-Agent": PW_UA})
    assert "Letöltés (AZW3)" in r.text


def test_device_setting_overrides(client):
    login(client)
    client.post("/beallitasok", data={"nezet": "lista", "eszkoz": "regi"})
    assert client.cookies.get("polc_eszkoz") == "regi"
    r = client.get("/k/0?p=%2Fopds%2Fnew", headers={"User-Agent": PW_UA})
    assert "Letöltés (AZW3)" not in r.text


@pytest.mark.skipif(not shutil.which("ebook-convert"), reason="Calibre nincs telepítve")
def test_old_kindle_gets_mobi6_with_cover(client, tmp_path):
    import html as h, re, struct, subprocess
    login(client)
    client.post("/beallitasok", data={"nezet": "lista", "eszkoz": "regi"})
    r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-0"})  # csak EPUB van
    assert "Letöltés Kindle-re (MOBI)" in r.text
    url = h.unescape(re.search(r'href="(/letolt/0\?[^"]*k=mobi[^"]*)"', r.text).group(1))
    resp = client.get(url)
    assert resp.headers["content-disposition"].endswith('.mobi"')
    out = tmp_path / "k.mobi"
    out.write_bytes(resp.content)
    data = out.read_bytes()
    r0 = struct.unpack_from(">I", data, 78)[0]
    assert data[r0 + 16:r0 + 20] == b"MOBI" and struct.unpack_from(">I", data, r0 + 36)[0] == 6  # MOBI6
    assert b"EBOK" not in data
    cov = tmp_path / "c.jpg"
    subprocess.run(["ebook-meta", str(out), f"--get-cover={cov}"], capture_output=True)
    assert cov.exists() and cov.stat().st_size > 1000


def test_legacy_azw_cookie_falls_back_to_mobi(client):
    login(client)
    client.cookies.set("polc_eszkoz", "azw")
    r = client.get("/k/0?p=%2Fopds%2Fnew&nezet=lista", headers={"User-Agent": PW_UA})
    assert "Letöltés (AZW3)" not in r.text and "Letöltés (MOBI)" in r.text


def test_old_device_hides_azw3_links(client):
    login(client)
    client.post("/beallitasok", data={"nezet": "lista", "eszkoz": "regi"})
    r = client.get("/konyv/0", params={"p": "/opds/new", "id": "urn:uuid:book-3"})  # EPUB + AZW3
    assert "e=azw3" not in r.text and "Letöltés Kindle-re (MOBI)" in r.text and "Régebbi Kindle" in r.text
