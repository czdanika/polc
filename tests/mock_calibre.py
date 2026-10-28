"""Calibre-Web-szerű OPDS mock szerver teszteléshez (basic auth: kindle / titok)."""
from __future__ import annotations

import base64
import io
import zipfile

from fastapi import FastAPI, Request, Response
from PIL import Image, ImageDraw

USER, PASS = "kindle", "titok"

BOOKS = [
    {"id": i, "title": t, "author": a, "series": s, "idx": n, "lang": "hun", "tags": tags,
     "desc": d, "fmts": f}
    for i, (t, a, s, n, tags, d, f) in enumerate([
        ("Egri csillagok", "Gárdonyi Géza", None, None, ["Történelmi", "Klasszikus"],
         "<p>Bornemissza Gergely és <b>Eger ostroma</b> 1552-ben.</p><script>alert(1)</script>", ["epub"]),
        ("A Pál utcai fiúk", "Molnár Ferenc", None, None, ["Ifjúsági"], "<p>Grund, Nemecsek, gittegylet.</p>", ["epub", "mobi"]),
        ("Az ember tragédiája", "Madách Imre", None, None, ["Dráma"], "<div>Ádám és Éva története.</div>", ["epub", "pdf"]),
        ("Alapítvány", "Isaac Asimov", "Alapítvány", "1.0", ["Sci-fi"], "Pszichotörténelem.", ["epub", "azw3"]),
        ("Alapítvány és Birodalom", "Isaac Asimov", "Alapítvány", "2.0", ["Sci-fi"], "Az Öszvér.", ["epub"]),
        ("Második Alapítvány", "Isaac Asimov", "Alapítvány", "3.0", ["Sci-fi"], "", ["epub"]),
        ("Légy jó mindhalálig", "Móricz Zsigmond", None, None, ["Klasszikus"], "Nyilas Misi.", ["epub"]),
        ("Tüskevár", "Fekete István", None, None, ["Ifjúsági"], "Tutajos és Bütyök.", ["epub"]),
    ])
]
MIME = {"epub": "application/epub+zip", "mobi": "application/x-mobipocket-ebook",
        "pdf": "application/pdf", "azw3": "application/vnd.amazon.ebook"}

app = FastAPI()


def authed(req: Request) -> bool:
    h = req.headers.get("authorization", "")
    if not h.startswith("Basic "):
        return False
    return base64.b64decode(h[6:]).decode() == f"{USER}:{PASS}"


def deny():
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="calibre-web"'})


def feed(title: str, body: str, links: str = "") -> Response:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<id>urn:uuid:root</id><title>{title}</title><updated>2026-10-27T10:00:00+00:00</updated>
<link rel="start" href="/opds" type="application/atom+xml;profile=opds-catalog;type=feed;kind=navigation"/>
<link rel="search" href="/opds/osd" type="application/opensearchdescription+xml"/>
<link type="application/atom+xml" rel="search" title="Search" href="/opds/search/{{searchTerms}}"/>
{links}
{body}
</feed>"""
    return Response(xml, media_type="application/atom+xml")


def nav(title: str, href: str, desc: str) -> str:
    return f"""<entry><title>{title}</title><id>{href}</id><content type="text">{desc}</content>
<link type="application/atom+xml;profile=opds-catalog" href="{href}" rel="subsection"/></entry>"""


def book_entry(b: dict) -> str:
    cats = "".join(f'<category scheme="http://www.bisg.org/standards/bisac_subject/index.html" term="{t}" label="{t}"/>' for t in b["tags"])
    acq = "".join(f'<link rel="http://opds-spec.org/acquisition" href="/opds/download/{b["id"]}/{f}/" length="123456" title="{f.upper()}" type="{MIME[f]}"/>' for f in b["fmts"])
    from html import escape
    return f"""<entry><title>{b['title']}</title><id>urn:uuid:book-{b['id']}</id><updated>2026-10-01T00:00:00+00:00</updated>
<author><name>{b['author']}</name></author><publisher><name>Móra</name></publisher>
<dcterms:language>{b['lang']}</dcterms:language><dcterms:issued>1901-01-01</dcterms:issued>{cats}
<summary type="html">{escape(b['desc'] + (f"<p>SERIES: {b['series']} [{b['idx']}]</p>" if b['series'] else ''))}</summary>
<link type="image/jpeg" href="/opds/cover/{b['id']}" rel="http://opds-spec.org/image"/>
<link type="image/jpeg" href="/opds/cover/{b['id']}" rel="http://opds-spec.org/image/thumbnail"/>
{acq}</entry>"""


@app.get("/opds")
def root(req: Request):
    if not authed(req):
        return deny()
    return feed("Calibre-Web", "".join([
        nav("Hot Books", "/opds/hot", "Popular publications from this catalog based on Downloads."),
        nav("Recently added Books", "/opds/new", "The latest Books"),
        nav("Authors", "/opds/author", "Books ordered by Author"),
        nav("Series", "/opds/series", "Books ordered by series"),
        nav("Categories", "/opds/category", "Books ordered by category"),
        nav("Languages", "/opds/language", "Books ordered by Languages"),
    ]))


@app.get("/opds/osd")
def osd():
    return Response("""<?xml version="1.0"?><OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<Url type="application/atom+xml" template="/opds/search?query={searchTerms}"/></OpenSearchDescription>""",
                    media_type="application/opensearchdescription+xml")


@app.get("/opds/new")
def new(req: Request, offset: int = 0):
    if not authed(req):
        return deny()
    page = BOOKS[offset:offset + 5]
    links = ""
    if offset + 5 < len(BOOKS):
        links += f'<link rel="next" href="/opds/new?offset={offset + 5}" type="application/atom+xml;profile=opds-catalog"/>'
    if offset > 0:
        links += f'<link rel="previous" href="/opds/new?offset={max(0, offset - 5)}" type="application/atom+xml;profile=opds-catalog"/>'
    return feed("Recently added Books", "".join(book_entry(b) for b in page), links)


@app.get("/opds/hot")
def hot(req: Request):
    if not authed(req):
        return deny()
    return feed("Hot Books", "".join(book_entry(b) for b in BOOKS[:3]))


@app.get("/opds/author")
def authors(req: Request):
    if not authed(req):
        return deny()
    names = sorted({b["author"] for b in BOOKS})
    return feed("Authors", "".join(nav(n, f"/opds/author/{i}", f"{sum(b['author']==n for b in BOOKS)} books") for i, n in enumerate(names)))


@app.get("/opds/author/{i}")
def author(req: Request, i: int):
    if not authed(req):
        return deny()
    n = sorted({b["author"] for b in BOOKS})[i]
    return feed(n, "".join(book_entry(b) for b in BOOKS if b["author"] == n))


@app.get("/opds/series")
@app.get("/opds/category")
@app.get("/opds/language")
def misc(req: Request):
    if not authed(req):
        return deny()
    return feed("Series", nav("Alapítvány", "/opds/author/2", "3 books"))


@app.get("/opds/search")
@app.get("/opds/search/{query}")
def search(req: Request, query: str = ""):
    if not authed(req):
        return deny()
    query = query or req.query_params.get("query", "")
    hits = [b for b in BOOKS if query.lower() in (b["title"] + b["author"]).lower()]
    return feed(f"Search: {query}", "".join(book_entry(b) for b in hits))


@app.get("/opds/cover/{i}")
def cover(req: Request, i: int):
    if not authed(req):
        return deny()
    img = Image.new("RGB", (600, 900), (40 + i * 25, 90, 160))
    d = ImageDraw.Draw(img)
    d.rectangle([30, 30, 570, 870], outline=(255, 255, 255), width=8)
    d.text((60, 400), BOOKS[i]["title"], fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "WEBP")  # a Calibre-Web is gyakran WebP-t ad
    return Response(out.getvalue(), media_type="image/webp")


def make_epub(b: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")
        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{b['title']}</dc:title><dc:creator>{b['author']}</dc:creator><dc:language>hu</dc:language><dc:identifier id="id">book-{b['id']}</dc:identifier></metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="c1"/></spine></package>""")
        z.writestr("OEBPS/toc.ncx", f"""<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="book-{b['id']}"/></head><docTitle><text>{b['title']}</text></docTitle><navMap><navPoint id="n1" playOrder="1"><navLabel><text>Első fejezet</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>""")
        z.writestr("OEBPS/c1.xhtml", f"""<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{b['title']}</title></head><body><h1>{b['title']}</h1><p>Árvíztűrő tükörfúrógép. Őszi szél fúj, ősz hajú öreg úr.</p></body></html>""")
    return out.getvalue()


@app.get("/opds/download/{i}/{fmt}/")
def download(req: Request, i: int, fmt: str):
    if not authed(req):
        return deny()
    data = make_epub(BOOKS[i]) if fmt == "epub" else f"{fmt} file {i}".encode()
    return Response(data, media_type=MIME[fmt])


# --- Calibre-Web-szerű indexek (szerző / címke / sorozat / kiadó) -------------

def _names(kind):
    if kind == "author":
        return sorted({b["author"] for b in BOOKS})
    if kind == "category":
        return sorted({t for b in BOOKS for t in b["tags"]})
    if kind == "series":
        return sorted({b["series"] for b in BOOKS if b["series"]})
    return ["Móra"]


def _match(kind, name, b):
    return {"author": b["author"] == name, "category": name in b["tags"],
            "series": b["series"] == name, "publisher": True}[kind]


def _index(req, kind, offset):
    if not authed(req):
        return deny()
    names = _names(kind)
    page = names[offset:offset + 3]  # kis oldalméret, hogy a lapozás is tesztelve legyen
    links = ""
    if offset + 3 < len(names):
        base = "/opds/publisher" if kind == "publisher" else f"/opds/{kind}/letter/00"
        links = f'<link rel="next" href="{base}?offset={offset + 3}" type="application/atom+xml;profile=opds-catalog"/>'
    body = "".join(nav(n, f"/opds/{kind}/{names.index(n)}", "") for n in page)
    return feed(kind, body, links)


@app.get("/opds/author/letter/00")
def idx_author(req: Request, offset: int = 0):
    return _index(req, "author", offset)


@app.get("/opds/category/letter/00")
def idx_category(req: Request, offset: int = 0):
    return _index(req, "category", offset)


@app.get("/opds/series/letter/00")
def idx_series(req: Request, offset: int = 0):
    return _index(req, "series", offset)


@app.get("/opds/publisher")
def idx_publisher(req: Request, offset: int = 0):
    return _index(req, "publisher", offset)


@app.get("/opds/category/{i}")
@app.get("/opds/series/{i}")
@app.get("/opds/publisher/{i}")
def by_kind(req: Request, i: int):
    if not authed(req):
        return deny()
    kind = req.url.path.split("/")[2]
    name = _names(kind)[i]
    return feed(name, "".join(book_entry(b) for b in BOOKS if _match(kind, name, b)))
