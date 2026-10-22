# StockWatch – oktatási kiadás (Gyakorlás)

Chart-olvasási gyakorlás szimulált (vagy licencelt múltbeli) adaton: rejtett helyzetek, döntés (kihagyás / long /
short, stoppal és céllal), utána felfedés, ítélet (jó / szerencsés / balszerencsés / rossz döntés) és lépésenkénti
levezetés, összevetve a stratégiákkal és egy véletlen alapvonallal.

- **Fiókok** (e-mail + jelszó, e-mail-megerősítés, jelszó-visszaállítás); a haladás felhasználónként a Neon Postgresben.
- **Kezdőoldal** bejelentkezés nélkül; első belépéskor 3 lépéses **bevezető** és egy próbafeladat.
- **Tanulási út**: 10 szint, rövid lecke ábrával és célzott feladatsorral; a következő szint a jó / balszerencsés
  döntések aránya (≥ 60%) szerint nyílik. A leckék szövege: `src/stockwatch/locale/lessons.hu.json`.
- **Szintetikus feladatok** (`practice.source: synthetic`, alapértelmezés): seedből generált árfolyamsorok
  (GARCH-volatilitás, rezsimváltás, vastag farok, beépített alakzatok) – adatlicenc nélkül. Valódi letöltött adat
  (`practice.source: real`) nyilvános szolgáltatásban csak licencelt forrásból.
- **Jogi keret**: ÁSZF, adatkezelési és sütitájékoztató (`app/web/legal/`, **tervezet – jogász nézze át**),
  16+ szolgáltatás, fiók törlése és adatexport a Profil oldalon.

> ⚠️ **Oktatási célú gyakorlás múltbeli adaton. Nem befektetési tanács.** Ezen az ágon (`oktatas`) nincs bróker,
> monitor, értesítés, labor – csak a Gyakorlás és a hozzá szükséges motorok (alakzatok, Visszahúzás, short).

## Telepítés (helyben)

```bash
python -m venv .venv
.venv\Scripts\activate            # Linuxon: source .venv/bin/activate
pip install -r requirements.txt
```

## Használat

```bash
python cli.py practice-pool       # a feladatkészlet felépítése (szintetikus: ~1-2 perc; --force: újra)
python cli.py fetch-universe      # csak practice.source: real mellett - a letöltött univerzum
python cli.py db migrate          # az adatbázis sémája (lásd: Adatbázis)
python cli.py web                 # http://127.0.0.1:8765 – regisztráció, belépés, gyakorlás
```

A felhasználók e-maillel és jelszóval regisztrálnak (min. 10 karakter, ÁSZF + 16 év elfogadása), az e-mail-címet
egy egyszer használható linkkel erősítik meg; ellenőrzés nélkül nincs belépés. Elfelejtett jelszó: visszaállító link
(1 óráig érvényes), utána minden munkamenet zárul. A levelek SMTP-n mennek (`SMTP_*`, `MAIL_FROM`, `PUBLIC_URL` a
`.env`-ben); helyi kipróbáláshoz `MAIL_TO_LOG=1`: a levél a linkkel a naplóba (`logs/app.log`) kerül.

A feladatkészletet csak a parancssor építi, a webes kérés soha. A Kitörés-összevetés a készlet építésekor kerül a
készletbe a labor kimeneti fájljaiból (`data/lab`), ha az adatkönyvtárban vannak – futás közben a labor nem kell.
Labor-adat nélkül a Kitörés „nem jelzett”; a többi stratégia és az ítélet ettől független.

> Ha ugyanazt a `data/` mappát a fő StockWatch is használja: a szintetikus készletet (SYN papírok) a fő StockWatch
> nem tudja megnyitni – ilyenkor az oktatási kiadást külön mappában (szerveren) futtasd.

## Adatbázis (Neon Postgres)

A felhasználók és a haladás a Neon Postgresben vannak (projekt `MARS`, adatbázis `StockWatch_Learning`).

1. A Neon konzolban: *Connect* → ág `production`, adatbázis `StockWatch_Learning`, **Pooled connection** bekapcsolva
   (a hostban `-pooler` van).
2. A kapcsolati szöveget csak a titokfájlba írd, verziókezelésbe soha.
3. Írd a `.env`-be (szerveren a `STOCKWATCH_ENV_FILE` által mutatott titokfájlba): `DATABASE_URL=postgresql://...`
4. Séma: `python cli.py db migrate` – üres adatbázison létrehozza, másodszor futtatva nem csinál semmit.

A migrációk a `migrations/` sorszámozott SQL-fájljai, egy tranzakcióban futnak, a `schema_migrations` tábla tartja
nyilván, mi futott le. A kapcsolati adat nem jelenik meg naplóban, hibaüzenetben, API-válaszban.
GDPR: a Neon-projekt régiója legyen EU (pl. Frankfurt – a konzol *Settings* oldalán látszik).

## Szerveren (Oracle, a korábbi StockWatch mellett)

Az oktatási kiadás ugyanazon a gépen fut, ahol a korábbi StockWatch, de attól teljesen külön:

| | korábbi StockWatch | oktatási kiadás |
|---|---|---|
| kód | `/opt/stockwatch` | `/opt/stockwatch-learn` |
| szolgáltatás / felhasználó | `stockwatch-web` (+ monitor, labor) / `stockwatch` | `stockwatch-learn` / `stockwatch-learn` |
| port | 8765 | 8766 |
| titkok | `/etc/stockwatch/stockwatch.env` | `/etc/stockwatch-learn/stockwatch-learn.env` |
| Caddy | `/etc/caddy/Caddyfile` | `/etc/caddy/stockwatch-learn.caddy` (a Caddyfile-ba csak egy `import` sor kerül) |
| CLI | `sudo stockwatch-cli` | `sudo stockwatch-learn-cli` |

Telepítés / frissítés a saját gépről (csak a kód megy fel – a helyi `data/` és `.env` soha):

```powershell
.\deploy\upload.ps1 -Server stockwatch -Domain gyakorlas.pelda.hu
```

Első alkalommal a szerveren a titokfájlba: `DATABASE_URL` (Neon), `PUBLIC_URL` (pl. `https://gyakorlas.pelda.hu`),
`SMTP_*`, `MAIL_FROM` – utána `sudo stockwatch-learn-cli db migrate && sudo systemctl restart stockwatch-learn`.
A domain (aldomain) A rekordja a szerverre mutasson. Domain nélkül: `.\deploy\open-web.ps1` (SSH-alagút, 18766).

> Ha a korábbi StockWatch telepítőjét (`main` ág, `deploy/install.sh`) újra lefuttatod, az a teljes Caddyfile-t
> újraírja, és az `import /etc/caddy/stockwatch-learn.caddy` sor elveszik – utána az oktatási telepítő újrafuttatása
> visszateszi.

## Lemezhasználat

`python cli.py data-usage` – komponensenként, a plafonokhoz viszonyítva; `python cli.py housekeeping` – takarítás.

## Tesztek

```bash
pytest -q
```

Az adatbázis-tesztek a Neonon, egy ideiglenes, a végén törölt sémában futnak (a `DATABASE_URL` közvetlen, nem
pooled végpontján; a `public` sémát nem érintik). A CI-ban a `TEST_DATABASE_URL` repo-titokkal futnak, nélküle
kimaradnak. A reszponzív felület tesztje Playwright + Chromium mellett fut (a CI telepíti), nélküle kimarad.
