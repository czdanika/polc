"""OPDS (Atom) feed letöltése és feldolgozása."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .config import Feed

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opds": "http://opds-spec.org/2010/catalog",
    "dc": "http://purl.org/dc/terms/",
    "dc11": "http://purl.org/dc/elements/1.1/",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "calibre": "http://calibre.kovidgoyal.net/2009/metadata",
}
A = "{%s}" % NS["atom"]

ACQ = "http://opds-spec.org/acquisition"
IMG = ("http://opds-spec.org/image", "http://opds-spec.org/cover", "x-stanza-cover-image")
THUMB = ("http://opds-spec.org/image/thumbnail", "http://opds-spec.org/thumbnail", "x-stanza-cover-image-thumbnail")

FORMATS = {
    "application/epub+zip": ("epub", "EPUB"),
    "application/x-mobipocket-ebook": ("mobi", "MOBI"),
    "application/vnd.amazon.ebook": ("azw3", "AZW3"),
    "application/x-mobi8-ebook": ("azw3", "AZW3"),
    "application/vnd.amazon.mobi8-ebook": ("azw3", "AZW3"),
    "application/pdf": ("pdf", "PDF"),
    "text/plain": ("txt", "TXT"),
    "application/x-cbz": ("cbz", "CBZ"),
    "application/vnd.comicbook+zip": ("cbz", "CBZ"),
    "application/x-cbr": ("cbr", "CBR"),
    "application/x-fictionbook+xml": ("fb2", "FB2"),
    "application/rtf": ("rtf", "RTF"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx", "DOCX"),
    "application/kepub+zip": ("kepub", "KEPUB"),
}

# Gyakori angol címek magyarítása (Calibre-Web, Calibre szerver, COPS)
TRANSLATE = {
    "hot books": "Népszerű könyvek",
    "recently added books": "Legújabb könyvek",
    "newest": "Legújabb könyvek",
    "random books": "Véletlenszerű könyvek",
    "authors": "Szerzők",
    "by author": "Szerzők szerint",
    "series": "Sorozatok",
    "by series": "Sorozatok szerint",
    "categories": "Kategóriák",
    "tags": "Címkék",
    "by tags": "Címkék szerint",
    "languages": "Nyelvek",
    "by language": "Nyelvek szerint",
    "publishers": "Kiadók",
    "by publisher": "Kiadók szerint",
    "read books": "Olvasott könyvek",
    "unread books": "Olvasatlan könyvek",
    "top rated books": "Legjobbra értékelt",
    "alphabetical books": "Könyvek ABC sorrendben",
    "title": "Cím szerint",
    "by title": "Cím szerint",
    "ratings": "Értékelések",
    "by rating": "Értékelés szerint",
    "file formats": "Fájlformátumok",
    "formats": "Formátumok",
    "books": "Könyvek",
    "all books": "Összes könyv",
    "shelves": "Könyvespolcok",
    "shelf": "Könyvespolc",
    "magic shelves": "Okos polcok",
    "library": "Könyvtár",
    "search": "Keresés",
    "discover": "Felfedezés",
    "most recently updated": "Legutóbb frissített",
    "newest books": "Legújabb könyvek",
}


def hu(title: str) -> str:
    return TRANSLATE.get((title or "").strip().lower(), title)


class NeedLogin(Exception):
    pass


class UpstreamError(Exception):
    pass


@dataclass
class Acq:
    path: str
    mime: str
    ext: str
    label: str
    size: int | None = None


@dataclass
class Entry:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    summary_html: str = ""
    summary_text: str = ""
    nav_path: str | None = None
    acquisitions: list[Acq] = field(default_factory=list)
    cover: str | None = None
    thumb: str | None = None
    categories: list[str] = field(default_factory=list)
    language: str | None = None
    publisher: str | None = None
    issued: str | None = None
    series: str | None = None
    series_index: str | None = None
    count: int | None = None

    @property
    def is_book(self) -> bool:
        return bool(self.acquisitions)

    def fmt(self, ext: str) -> Acq | None:
        for a in self.acquisitions:
            if a.ext == ext:
                return a
        return None

    @property
    def author_str(self) -> str:
        return ", ".join(self.authors)


@dataclass
class FeedPage:
    title: str
    entries: list[Entry]
    next: str | None = None
    prev: str | None = None
    first: str | None = None
    search_template: str | None = None  # útvonal, {searchTerms} helyőrzővel
    search_osd: str | None = None
    total: int | None = None

    @property
    def books(self) -> list[Entry]:
        return [e for e in self.entries if e.is_book]

    @property
    def navs(self) -> list[Entry]:
        return [e for e in self.entries if not e.is_book and e.nav_path]

    @property
    def is_acquisition(self) -> bool:
        return any(e.is_book for e in self.entries)


# ---------------------------------------------------------------- HTML tisztítás

class _Sanitizer(HTMLParser):
    ALLOWED = {"p", "br", "b", "strong", "i", "em", "ul", "ol", "li", "blockquote", "div", "span"}
    DROP_CONTENT = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self._drop = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP_CONTENT:
            self._drop += 1
        elif tag in self.ALLOWED and not self._drop:
            t = "p" if tag == "div" else tag
            if t == "span":
                return
            self.out.append(f"<{t}>" if t != "br" else "<br>")
            if t in ("p", "li", "br"):
                self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in self.DROP_CONTENT:
            self._drop = max(0, self._drop - 1)
        elif tag in self.ALLOWED and tag not in ("br", "span") and not self._drop:
            self.out.append(f"</{'p' if tag == 'div' else tag}>")

    def handle_data(self, data):
        if not self._drop:
            self.out.append(escape(data))
            self.text.append(data)


def sanitize(html_str: str) -> tuple[str, str]:
    s = _Sanitizer()
    try:
        s.feed(html_str)
        s.close()
    except Exception:  # pragma: no cover
        return escape(html_str), html_str
    text = re.sub(r"\s+", " ", "".join(s.text)).strip()
    return "".join(s.out).strip(), text


# ---------------------------------------------------------------- feldolgozás

def _txt(el, path: str) -> str | None:
    found = el.find(path, NS)
    if found is not None and found.text:
        return found.text.strip()
    return None


class Mapper:
    """Abszolút URL <-> feed-relatív útvonal; csak a feed saját szerverére."""

    def __init__(self, feed: Feed, base_url: str) -> None:
        self.origin = feed.origin
        self.base = base_url

    def path(self, href: str | None) -> str | None:
        if not href:
            return None
        absu = urljoin(self.base, href.strip())
        s = urlsplit(absu)
        if f"{s.scheme}://{s.netloc}" != self.origin:
            return None
        return (s.path or "/") + (f"?{s.query}" if s.query else "")


SERIES_RE = re.compile(r"SERIES:\s*(.+?)\s*\[(\d+(?:\.\d+)?)\]", re.I)


def _content_html(el) -> str:
    c = el.find("atom:content", NS)
    if c is None:
        c = el.find("atom:summary", NS)
    if c is None:
        return ""
    typ = c.get("type", "text")
    if typ == "xhtml":
        for node in c.iter():
            if isinstance(node.tag, str) and "}" in node.tag:
                node.tag = node.tag.split("}", 1)[1]
        return "".join(ET.tostring(child, encoding="unicode", method="html") for child in c)
    if typ == "html":
        return c.text or ""
    return escape(c.text or "").replace("\n", "<br>")


def parse_feed(data: bytes, feed: Feed, base_url: str) -> FeedPage:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise UpstreamError(f"A katalógus nem érvényes XML: {e}") from e
    if root.tag != A + "feed":
        raise UpstreamError("A válasz nem OPDS/Atom katalógus.")
    m = Mapper(feed, base_url)
    page = FeedPage(title=hu(_txt(root, "atom:title") or ""), entries=[])

    total = _txt(root, "os:totalResults")
    if total and total.isdigit():
        page.total = int(total)

    for link in root.findall("atom:link", NS):
        rel = link.get("rel", "")
        typ = link.get("type", "")
        href = link.get("href")
        if rel == "next":
            page.next = m.path(href)
        elif rel in ("previous", "prev"):
            page.prev = m.path(href)
        elif rel == "first":
            page.first = m.path(href)
        elif rel == "search":
            if "opensearchdescription" in typ:
                page.search_osd = m.path(href)
            elif href and "{searchTerms}" in href:
                p = m.path(href.replace("{searchTerms}", "__Q__"))
                if p:
                    page.search_template = p

    for el in root.findall("atom:entry", NS):
        title = _txt(el, "atom:title") or "(cím nélkül)"
        e = Entry(id=_txt(el, "atom:id") or title, title=title)
        e.authors = [a.findtext(A + "name", "").strip() for a in el.findall("atom:author", NS)]
        e.authors = [a for a in e.authors if a]
        e.language = _txt(el, "dc:language") or _txt(el, "dc11:language")
        e.issued = _txt(el, "dc:issued") or _txt(el, "dc11:date")
        e.publisher = _txt(el, "dc:publisher") or _txt(el, "dc11:publisher")
        if not e.publisher:
            pub = el.find("atom:publisher", NS)
            if pub is not None:
                e.publisher = (pub.findtext(A + "name") or pub.text or "").strip() or None
        e.series = _txt(el, "calibre:series")
        e.series_index = _txt(el, "calibre:series_index")
        for cat in el.findall("atom:category", NS):
            lab = cat.get("label") or cat.get("term")
            if lab:
                e.categories.append(lab)

        raw = _content_html(el)
        if raw:
            sm = SERIES_RE.search(re.sub(r"<[^>]+>", " ", raw))
            if sm and not e.series:
                e.series, e.series_index = sm.group(1), sm.group(2)
            # Calibre "SERIES: x [1]", "TAGS: ..." sorok kiszűrése (ezek külön mezőben jelennek meg)
            raw = re.sub(r"<(p|div)[^>]*>\s*(?:SERIES|TAGS|RATING|PUBLISHED|LANGUAGES?)\s*:.*?</\1>", "", raw,
                         flags=re.I | re.S)
            raw = re.sub(r"(?:SERIES|TAGS|RATING|PUBLISHED|LANGUAGES?)\s*:[^<\n]*(?:<br\s*/?>)?", "", raw)
            e.summary_html, e.summary_text = sanitize(raw)

        cnt = el.get("{http://purl.org/syndication/thread/1.0}count")
        if cnt and cnt.isdigit():
            e.count = int(cnt)

        for link in el.findall("atom:link", NS):
            rel = link.get("rel", "")
            typ = (link.get("type") or "").split(";")[0].strip()
            href = link.get("href")
            if rel.startswith(ACQ):
                p = m.path(href)
                if not p:
                    continue
                ext, label = FORMATS.get(typ, (None, None))
                if not ext:
                    t = (link.get("title") or "").strip().lower()
                    guess = t or (p.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower())
                    ext, label = guess[:6] or "bin", (guess[:6] or "fájl").upper()
                length = link.get("length")
                e.acquisitions.append(Acq(p, typ, ext, label, int(length) if length and length.isdigit() else None))
            elif rel in THUMB:
                e.thumb = m.path(href)
            elif rel in IMG:
                e.cover = m.path(href)
            elif "atom+xml" in typ and rel not in ("self", "up", "start", "search"):
                if not e.nav_path:
                    e.nav_path = m.path(href)
        if not e.is_book:
            translated = hu(e.title)
            if translated != e.title:  # angol leírás mellé ne kerüljön magyar cím
                e.summary_text = e.summary_html = ""
            e.title = translated
            cm = re.match(r"^\s*(\d+)\s+(?:books?|könyv|db)\s*$", e.summary_text, re.I)
            if cm and e.count is None:
                e.count = int(cm.group(1))
                e.summary_text = e.summary_html = ""
        if e.thumb is None:
            e.thumb = e.cover
        if e.cover is None:
            e.cover = e.thumb
        page.entries.append(e)
    return page


def parse_osd(data: bytes, feed: Feed, base_url: str) -> str | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    m = Mapper(feed, base_url)
    best = None
    for url in root.iter("{%s}Url" % NS["os"]):
        tpl = url.get("template", "")
        typ = url.get("type", "")
        if "{searchTerms}" not in tpl:
            continue
        p = m.path(tpl.replace("{searchTerms}", "__Q__"))
        if not p:
            continue
        if "atom" in typ or "opds" in typ:
            return p
        best = best or p
    return best


# ---------------------------------------------------------------- HTTP kliens

class Client:
    def __init__(self, ttl: int = 120, transport=None) -> None:
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True,
                                      headers={"User-Agent": "Polc/1.0 (OPDS proxy)"})
        self.ttl = ttl
        self._cache: dict[tuple, tuple[float, bytes, str]] = {}

    async def close(self) -> None:
        await self.http.aclose()

    def _key(self, url: str, auth) -> tuple:
        return (url, auth[0] if auth else None, hash(auth[1]) if auth else None)

    async def get(self, feed: Feed, path: str, auth, cache: bool = True) -> tuple[bytes, str]:
        if not path.startswith("/"):
            raise UpstreamError("Érvénytelen útvonal.")
        url = feed.origin + path
        key = self._key(url, auth)
        now = time.time()
        if cache and key in self._cache and now - self._cache[key][0] < self.ttl:
            _, data, final = self._cache[key]
            return data, final
        try:
            r = await self.http.get(url, auth=auth)
        except httpx.HTTPError as e:
            raise UpstreamError(f"Nem érhető el a könyvtár ({feed.name}): {e.__class__.__name__}") from e
        if r.status_code in (401, 403):
            raise NeedLogin()
        if r.status_code >= 400:
            raise UpstreamError(f"A könyvtár hibát adott: HTTP {r.status_code}")
        if cache:
            if len(self._cache) > 300:
                for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:100]:
                    self._cache.pop(k, None)
            self._cache[key] = (now, r.content, str(r.url))
        return r.content, str(r.url)

    async def feed(self, feed: Feed, path: str, auth) -> FeedPage:
        data, final = await self.get(feed, path, auth)
        page = parse_feed(data, feed, final)
        if not page.search_template and page.search_osd:
            try:
                osd, ofinal = await self.get(feed, page.search_osd, auth)
                page.search_template = parse_osd(osd, feed, ofinal)
            except (UpstreamError, NeedLogin):
                pass
        return page

    def stream(self, feed: Feed, path: str, auth):
        if not path.startswith("/"):
            raise UpstreamError("Érvénytelen útvonal.")
        return self.http.stream("GET", feed.origin + path, auth=auth)


def search_path(template: str, q: str) -> str:
    return template.replace("__Q__", quote(q, safe=""))
