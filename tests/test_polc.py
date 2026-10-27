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
