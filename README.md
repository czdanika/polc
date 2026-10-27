# Polc

Magyar nyelvű, Kindle-barát OPDS proxy. A Calibre / Calibre-Web OPDS katalógusát egyszerű, e-ink-re optimalizált weboldallá alakítja, így a **Kindle beépített böngészőjéből** közvetlenül böngészhetsz, kereshetsz és tölthetsz le könyveket.

## Funkciók

- **Magyar felület**, a Calibre-Web angol menüpontjait is magyarra fordítja (Népszerű könyvek, Szerzők, Sorozatok…)
- **Három nézet:** Lista (borító + adatok + gyors letöltés), Borítók (rács), Tömör (csak szöveg, leggyorsabb e-inken). A választást megjegyzi.
- **Keresés** minden oldalon (OpenSearch alapján)
- **Könyv adatlap:** borító, szerző, sorozat és sorszám, kiadó, nyelv, címkék, formátumok és méret, leírás
- **Letöltés Kindle-re:** EPUB → **AZW3** konverzió Calibre `ebook-convert`-tel (szebb, mint a régi MOBI), **beágyazott borítóval**, címmel, szerzővel és sorozattal, így a Kindle könyvtárában is látszik a borító. Ha a könyvtárban már van AZW3/MOBI, azt adja, de személyes dokumentumra (PDOC) átjelölve, mert a Calibre-féle „bolti” (EBOK) jelölésnél a Kindle nem mutatja a borítót.
- **Régebbi Kindle-ök:** a régebbi böngészők nem engedik letölteni a `.azw3` fájlt. A Beállításokban eszközönként választható: *Paperwhite 1–3 / Touch* esetén AZW3-at kap `.azw` néven, a nagyon régi készülékek (Keyboard, 1, 2, DX) klasszikus **MOBI**-t (meglévőt, vagy EPUB/AZW3-ból konvertáltat), mindkettőt beágyazott borítóval. (Az új és a régebbi Kindle böngészője ugyanazzal az azonosítóval jelentkezik, ezért ezt kézzel kell beállítani.)
- **Küldés Kindle-re e-mailben** (Amazon Send-to-Kindle): EPUB-ot küld beágyazott borítóval, felhőbe szinkronizál
- **Ékezetmentes fájlnevek** (a Kindle böngészője nem szereti az ékezetes neveket)
- **Szürkeárnyalatos, átméretezett borítók** gyorsítótárazva, így gyorsabban tölt az e-inken
- **Saját felhasználók:** a Polcba külön felhasználókkal lehet belépni (`USERS`), a Calibre-fiók pedig csak a szerveren van (`FEED_USERNAME`/`FEED_PASSWORD`). Felhasználónként saját Kindle e-mail cím, jelszó-hash, brute-force védelem.
- **Belépés** egyszer, titkosított sütiben megjegyezve
- Nincs flexbox és nincs JavaScript, így régi Kindle böngészőn is működik

## Képernyőképek

Kindle méretben (600×800), teszt-könyvtárral:

| Főoldal | Lista nézet | Borítók nézet |
|---|---|---|
| ![Főoldal](docs/kepernyokepek/2_fooldal.png) | ![Lista](docs/kepernyokepek/3_lista.png) | ![Borítók](docs/kepernyokepek/4_racs.png) |
| **Tömör nézet** | **Könyv adatlap** | **Szerzők** |
| ![Tömör](docs/kepernyokepek/5_tomor.png) | ![Könyv](docs/kepernyokepek/6_konyv.png) | ![Szerzők](docs/kepernyokepek/7_szerzok.png) |

## Telepítés

Példa Docker Compose / Portainer stack: [`docker-compose.example.yml`](docker-compose.example.yml). Írd át benne a `FEED_URL`-t, majd `docker compose up -d`.

## Beállítások (környezeti változók)

| Változó | Alapérték | Leírás |
|---|---|---|
| `FEED_URL` | – | OPDS katalógus címe, pl. `http://192.168.0.100:18082/opds` |
| `FEED_NAME` | Könyvtár 1 | Megjelenített név |
| `FEED_USERNAME`, `FEED_PASSWORD` | – | Ha meg van adva, nincs belépés a Kindle-en |
| `FEED_2_URL` … `FEED_9_URL` | – | További könyvtárak (`_NAME`, `_USERNAME`, `_PASSWORD` is) |
| `PORT` | 8080 | Belső port a konténerben |
| `TITLE` | Polc | Az oldal neve |
| `DEFAULT_VIEW` | lista | `lista`, `racs` vagy `tomor` |
| `CONVERT_ENABLED` | true | EPUB → AZW3 konverzió |
| `CONVERT_TIMEOUT` | 300 | Konverzió időkorlátja (mp) |
| `CONVERT_PROFILE` | kindle_pw3 | Calibre kimeneti profil (pl. `kindle`, `kindle_oasis`, `kindle_scribe`) |
| `COVER_GRAYSCALE` | true | Szürkeárnyalatos borítók |
| `CACHE_TTL` | 120 | Katalógus-gyorsítótár (mp) |
| `SECRET_KEY` | automatikus | Süti-titkosítás kulcsa. Ha üres, a `/data/secret.key`-be generálja. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | – | E-mail küldés (`SMTP_SECURITY`: `starttls`, `ssl` vagy `none`) |
| `KINDLE_EMAIL` | – | Alapértelmezett Kindle cím (a Beállítások oldalon felülírható) |
| `USERS` | – | Polc-felhasználók: `nev:jelszo[:kindle@kindle.com]`, vesszővel elválasztva. Ha meg van adva, minden oldalhoz belépés kell. |
| `USERS_FILE` | – | Ugyanez fájlból, soronként egy felhasználó |

### Saját felhasználók

Ha a `USERS` meg van adva, a Polcba a saját felhasználóiddal lehet belépni, a Calibre-fiókot pedig csak a szerver ismeri:

```env
FEED_USERNAME=calibre-fiok
FEED_PASSWORD=calibre-jelszo
USERS=daniel:pbkdf2-260000-...:daniel@kindle.com,orsi:pbkdf2-260000-...
```

Jelszó-hash készítése (sima jelszó is működik, de a hash ajánlott):

```bash
docker exec -it polc python -m app.jelszo
```

Öt sikertelen próbálkozás után az adott IP-cím 10 percre kizárásra kerül. Jelszócsere után a felhasználó régi munkamenetei érvénytelenné válnak.

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
