# Polc

Magyar nyelvű, Kindle-barát OPDS proxy. A Calibre / Calibre-Web OPDS katalógusát egyszerű, e-ink-re optimalizált weboldallá alakítja, így a **Kindle beépített böngészőjéből** közvetlenül böngészhetsz, kereshetsz és tölthetsz le könyveket.

Az [evan-buss/opds-proxy](https://github.com/evan-buss/opds-proxy) ötletére épül, saját kóddal és több funkcióval.

## Funkciók

- **Magyar felület**, a Calibre-Web angol menüpontjait is magyarra fordítja (Népszerű könyvek, Szerzők, Sorozatok…)
- **Három nézet:** Lista (borító + adatok + gyors letöltés), Borítók (rács), Tömör (csak szöveg, leggyorsabb e-inken). A választást megjegyzi.
- **Keresés** minden oldalon (OpenSearch alapján)
- **Könyv adatlap:** borító, szerző, sorozat és sorszám, kiadó, nyelv, címkék, formátumok és méret, leírás
- **Letöltés Kindle-re:** EPUB → **AZW3** konverzió Calibre `ebook-convert`-tel (szebb, mint a régi MOBI). Ha a könyvtárban már van AZW3/MOBI, azt adja.
- **Küldés Kindle-re e-mailben** (Amazon Send-to-Kindle): EPUB-ot küld, felhőbe szinkronizál
- **Ékezetmentes fájlnevek** (a Kindle böngészője nem szereti az ékezetes neveket)
- **Szürkeárnyalatos, átméretezett borítók** gyorsítótárazva, így gyorsabban tölt az e-inken
- **Belépés** egyszer, titkosított sütiben megjegyezve; vagy előre beállított felhasználó a stackben
- Nincs flexbox és nincs JavaScript, így régi Kindle böngészőn is működik

## Telepítés (Portainer)

1. Töltsd fel ezt a mappát egy GitHub repóba (pl. `czdanika/polc`). A GitHub Actions lefuttatja a teszteket, és felteszi az image-et a `ghcr.io/czdanika/polc:latest` címre (amd64 + arm64).
2. Ha a csomag privát: GitHub → Packages → polc → Package settings → *Change visibility: Public*. Ha privát marad, Portainerben add hozzá a ghcr.io registry-t.
3. Portainerben állítsd le a régi `opds-proxy` stacket, és hozz létre új stacket a `stack.yml` tartalmával.
4. Kindle-en: `http://192.168.0.100:18100`. Érdemes könyvjelzőzni.

## Beállítások (környezeti változók)

| Változó | Alapérték | Leírás |
|---|---|---|
| `FEED_URL` | – | OPDS katalógus címe, pl. `http://192.168.0.100:18082/opds` |
| `FEED_NAME` | Könyvtár 1 | Megjelenített név |
| `FEED_USERNAME`, `FEED_PASSWORD` | – | Ha meg van adva, nincs belépés a Kindle-en |
| `FEED_2_URL` … `FEED_9_URL` | – | További könyvtárak (`_NAME`, `_USERNAME`, `_PASSWORD` is) |
| `TITLE` | Polc | Az oldal neve |
| `DEFAULT_VIEW` | lista | `lista`, `racs` vagy `tomor` |
| `CONVERT_ENABLED` | true | EPUB → AZW3 konverzió |
| `CONVERT_TIMEOUT` | 300 | Konverzió időkorlátja (mp) |
| `COVER_GRAYSCALE` | true | Szürkeárnyalatos borítók |
| `CACHE_TTL` | 120 | Katalógus-gyorsítótár (mp) |
| `SECRET_KEY` | automatikus | Süti-titkosítás kulcsa. Ha üres, a `/data/secret.key`-be generálja. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | – | E-mail küldés (`SMTP_SECURITY`: `starttls`, `ssl` vagy `none`) |
| `KINDLE_EMAIL` | – | Alapértelmezett Kindle cím (a Beállítások oldalon felülírható) |

### Küldés Kindle-re e-mailben

1. Amazon: *Tartalom és eszközök → Beállítások → Személyes dokumentumok beállításai*. Itt találod a Kindle e-mail címedet (`…@kindle.com`), és itt kell a `SMTP_FROM` címet felvenni a **jóváhagyott feladók** közé.
2. Gmail esetén kétlépcsős azonosítás mellett **alkalmazásjelszót** kell készíteni az `SMTP_PASSWORD`-höz.

## Fejlesztés

```bash
pip install -r requirements.txt pytest
pytest -q tests                      # mock Calibre-Web szerver ellen fut
FEED_URL=http://192.168.0.100:18082/opds DATA_DIR=./data \
  uvicorn --factory app.main:create_app --reload --port 8080
```

A `WITH_CALIBRE=false` build-arggal Calibre nélküli, sokkal kisebb image készül, ilyenkor nincs konverzió.
