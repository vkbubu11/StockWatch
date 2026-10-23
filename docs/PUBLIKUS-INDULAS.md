# Publikus indulás – lépésről lépésre

A StockWatch Gyakorlás (oktatási kiadás) nyilvános elindítása: új Oracle-gép, ingyenes domain, HTTPS, e-mail,
Google-belépés, jogi oldalak, mentés, felügyelet. A lépések sorrendje számít – a ✅ sorok az ellenőrzések.

Időigény: kb. 3–4 óra, a jogász nélkül. Ami a jogászon múlik (7. lépés), az az indulás feltétele.

---

## 0. Döntések előre

| Kérdés | Javaslat |
|---|---|
| Domain | **DuckDNS** aldomain (ingyenes, pl. `stockwatch-gyakorlas.duckdns.org`). Később egy saját domain (~10 USD/év) hitelesebb, és jobb a levelek kézbesítése. |
| Levelezés (SMTP) | Egy **külön Gmail-fiók** alkalmazásjelszóval (pl. `stockwatch.gyakorlas@gmail.com`) – ingyenes, kb. 500 levél/nap, jól kézbesít. Ugyanez lehet a kapcsolati cím is. |
| Szerver | Új Oracle Always Free gép **Ubuntu 24.04**-gyel (a 22.04 Pythonja túl régi). Az Ampere A1 ingyenes kerete összesen 4 OCPU + 24 GB az összes gépre: ha a mostani StockWatch-gép az egészet használja, az új gép az **E2.1.Micro** legyen (1 GB; a telepítő swapot tesz rá), vagy telepítsd a meglévő gépre (a telepítő ezt is tudja, 8766-os port). |
| Cloudflare | Induláskor **ne** proxyzd (szürke felhő / ne is használd). A proxy mögött minden kérés a Cloudflare IP-jéről jönne, és a gyakoriság-korlát (belépés, regisztráció) rossz IP-re számolna. |
| Üzemeltető | Ki az adatkezelő / szolgáltató (magánszemély, egyéni vállalkozó, cég)? A neve és a címe a jogi oldalakra kerül – a jogásszal együtt döntsd el (7. lépés). |

---

## 1. Oracle-gép létrehozása

1. Oracle Cloud konzol → **Compute → Instances → Create instance**.
2. **Name:** `stockwatch-learn`. **Image:** *Canonical Ubuntu 24.04* (A1-hez az aarch64, E2-höz az x86 változat).
3. **Shape:** *Ampere A1.Flex* (1 OCPU, 6 GB – ha van szabad keret) vagy *VM.Standard.E2.1.Micro*. Mindkettő
   „Always Free-eligible” legyen.
   - „Out of capacity” hiba esetén: másik Availability Domain, vagy próbáld újra később.
4. **Networking:** a meglévő VCN publikus alhálózata, **Assign a public IPv4 address: igen**.
5. **SSH key:** *Upload public key* – a meglévő kulcs nyilvános fele (`oracle-stockwatch.key.pub`), vagy generálj
   újat. A privát kulcs maradjon a gépeden.
6. **Create**, majd jegyezd fel a **Public IP**-t.
   - Opcionális: *Networking → Reserved public IPs* – így a cím a gép újralétrehozásakor sem változik.
7. **Tűzfal (VCN):** *Networking → Virtual cloud networks → (a VCN) → Security Lists → Default* →
   **Add Ingress Rules** – két szabály: forrás `0.0.0.0/0`, TCP, cél port **80**, illetve **443**.
   (A gépen belüli iptables-szabályt a telepítő nyitja meg.)
8. A saját gépeden a `C:\Users\<te>\.ssh\config` végére:

   ```
   Host stockwatch-learn
       HostName <PUBLIC IP>
       User ubuntu
       IdentityFile ~/.ssh/oracle-stockwatch.key
       IdentitiesOnly yes
       ServerAliveInterval 30
   ```

✅ `ssh stockwatch-learn` belép, és a `lsb_release -d` kimenete `Ubuntu 24.04`.

---

## 2. Domain (DuckDNS)

1. <https://www.duckdns.org> → belépés (Google / GitHub).
2. **sub domain:** pl. `stockwatch-gyakorlas` → **add domain**.
3. A **current ip** mezőbe az Oracle-gép publikus IP-je → **update ip**.

✅ A saját gépen: `nslookup stockwatch-gyakorlas.duckdns.org` a szerver IP-jét adja (pár perc is lehet).

A továbbiakban `<DOMAIN>` = `stockwatch-gyakorlas.duckdns.org`, `<URL>` = `https://<DOMAIN>`.

---

## 3. Levelezés (Gmail SMTP)

1. Hozz létre egy új Gmail-fiókot a szolgáltatásnak.
2. Kapcsold be a **kétlépcsős azonosítást** (myaccount.google.com → Biztonság).
3. <https://myaccount.google.com/apppasswords> → új alkalmazásjelszó (név: `StockWatch`) → a 16 karakteres jelszót
   jegyezd fel **szóközök nélkül**. Ezt csak a szerver titokfájljába írd be (4. lépés), máshová ne.

---

## 4. Telepítés a szerverre

### 4.1 A kód a saját gépen

```powershell
cd C:\Users\biote\Desktop\Development\Stock
git switch oktatas          # vagy az az ág, amelyik az origin/oktatas-t követi
git pull
git status                  # legyen tiszta: a feltöltő a munkakönyvtárat csomagolja, a nem commitolt változást is
```

### 4.2 Első telepítés

```powershell
.\deploy\upload.ps1 -Server stockwatch-learn -Domain <DOMAIN>
```

Ez feltölti a kódot (a helyi `data/` és `.env` **soha** nem megy fel), telepíti a Pythont, a függőségeket,
a Caddy-t (HTTPS, Let's Encrypt), legenerálja a szintetikus feladatkészletet, és létrehozza az üres titokfájlt.
Az első futás figyelmeztet, hogy a `DATABASE_URL` és társai hiányoznak – ez várható.

### 4.3 Titkok a szerveren

```bash
ssh stockwatch-learn
sudo -u stockwatch-learn nano /etc/stockwatch-learn/stockwatch-learn.env
```

| Kulcs | Érték |
|---|---|
| `DATABASE_URL` | Neon konzol → *Connect* → ág `production`, adatbázis `StockWatch_Learning`, **Pooled connection** be (a hostban `-pooler`). |
| `CONTACT_EMAIL` | a 3. lépés Gmail-címe (az Információ oldalon és a jogi oldalakon jelenik meg) |
| `PUBLIC_URL` | `<URL>` (a levelekben ez a link alapja; `/` nélkül a végén) |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` |
| `SMTP_USER` | a Gmail-cím |
| `SMTP_PASSWORD` | az alkalmazásjelszó (szóközök nélkül) |
| `MAIL_FROM` | `StockWatch <a Gmail-cím>` |
| `MAIL_TO_LOG` | üres (éles üzemben a levél ne a naplóba menjen) |
| `GOOGLE_CLIENT_ID` / `_SECRET` | egyelőre üres – az 5. lépés tölti ki |

Mentés: `Ctrl+O`, `Enter`, `Ctrl+X`. Utána:

```bash
sudo stockwatch-learn-cli db migrate        # a Neonon már lefutott: "Nincs uj migracio - a sema naprakesz."
sudo systemctl restart stockwatch-learn
```

✅ `systemctl status stockwatch-learn` → `active (running)`.
✅ `sudo ss -tlnp | grep 8766` → csak `127.0.0.1:8766` (kívülről csak a Caddy éri el).
✅ Böngészőben `<URL>` → lakat ikon, angol kezdőoldal; `http://<DOMAIN>` átirányít https-re.
✅ Regisztrálj a saját címeddel → megjön a megerősítő levél (nézd a spam mappát is) → a link működik → belépés.

Ha nem jön levél: `journalctl -u stockwatch-learn -n 50` – az SMTP-hibát a napló mutatja.

---

## 5. Google-belépés (opcionális, de ajánlott)

1. <https://console.cloud.google.com> → új projekt: `StockWatch Gyakorlas`.
2. **Google Auth Platform → Branding** (régebben: *OAuth consent screen*):
   - App name: `StockWatch Practice`; User support email: a 3. lépés címe.
   - **Authorized domains:** `<DOMAIN>`.
   - Application home page: `<URL>`; Privacy policy: `<URL>/adatkezeles`; Terms of service: `<URL>/aszf`.
   - Developer contact: a 3. lépés címe.
3. **Audience:** User type *External* → **Publish app** (*In production*). Tesztmódban csak a felvett tesztfelhasználók
   léphetnének be. Mivel csak az `openid email profile` hatókör kell, Google-ellenőrzés nem szükséges (logót ne tölts
   fel, az ellenőrzést indítana).
4. **Data Access:** hatókörök: `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` – más ne.
5. **Clients → Create client** → *Web application* → **Authorized redirect URIs:** `<URL>/auth/google/callback`
   (pontosan így, https-sel).
6. A kapott Client ID és Client secret a szerver titokfájljába (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`), majd
   `sudo systemctl restart stockwatch-learn`.

✅ A belépő oldalon megjelenik a *Continue with Google* gomb; egy másik Google-fiókkal új fiók jön létre.

---

## 6. Az adatbázis tisztasága

Neon konzol → *SQL Editor* → ág `production`, adatbázis `StockWatch_Learning`:

```sql
SELECT id, email, created_at FROM users ORDER BY id;
```

A fejlesztés közben létrehozott próbafiókokat töröld (a saját élesben használt fiókodat hagyd meg). A legegyszerűbb:
belépve a *Profile → Delete account* gombbal. A tesztek ideiglenes `sw_test_*` sémában futnak, a `public` sémát
nem érintik.

GDPR: a Neon-projekt régiója legyen EU (pl. Frankfurt) – *Settings* oldal.

---

## 7. Jogi oldalak (indulás feltétele)

A három oldal (`app/web/legal/aszf.html`, `adatkezeles.html`, `sutik.html`) **tervezet**. Teendők:

1. **Jogász** nézze át mindhármat (magyar változat; az angol / német / spanyol fordítás tájékoztató, ezt az oldalak
   ki is írják). Külön kérdések hozzá: üzemeltetői forma; felelősségkorlátozás; fogyasztóvédelmi / békéltető testületi
   tájékoztatás; az `sw_lang` nyelvi süti „feltétlenül szükséges”-e; a 16 éves korhatár; a mentések megőrzési ideje.
2. A szögletes zárójeles helyek kitöltése:

   | Hely | Fájl |
   |---|---|
   | `[ÜZEMELTETŐ NEVE]`, `[CÍM]`, `[CÉGJEGYZÉKSZÁM / NYILVÁNTARTÁSI SZÁM, ADÓSZÁM]` | aszf, adatkezeles |
   | `[DÁTUM]` (hatálybalépés) | mindhárom |
   | `[A FELELŐSSÉGKORLÁTOZÁS PONTOS SZÖVEGE – JOGÁSZ]`, `[FOGYASZTÓVÉDELMI / BÉKÉLTETŐ TESTÜLETI TÁJÉKOZTATÁS]` | aszf |
   | `[ADATVÉDELMI TISZTVISELŐ, HA VAN]`, `[JELLEMZŐ IDŐTARTAM]` (szervernapló) | adatkezeles |
   | `[SZERZŐDÉS / DPA]`, `[E-MAIL SZOLGÁLTATÓ]` (→ Google / Gmail), `[SZERVER / TÁRHELY SZOLGÁLTATÓ]` (→ Oracle, régió) | adatkezeles |

   A `[KAPCSOLATI E-MAIL]` helyére a szerver automatikusan a `CONTACT_EMAIL` értékét írja – ezt nem kell kézzel.
3. A jóváhagyás után a három oldal tetejéről töröld a „TERVEZET – …” sort.
4. Ha a szöveg érdemben változott: a `Verzió:` dátum mindhárom oldalon **és** a `TERMS_VERSION` a
   `src/stockwatch/accounts.py`-ban legyen ugyanaz a dátum (a regisztráció ezt rögzíti elfogadott verzióként).
5. Commit, push, majd újratelepítés (lásd 10. lépés).

✅ `<URL>/aszf`, `/adatkezeles`, `/sutik`: nincs több `[...]` hely és „TERVEZET” sor.

---

## 8. Mentés

A Neon saját visszaállítási pontjai mellett napi `pg_dump` a szerveren, és hetente egy másolat gépen kívülre.
**A mentések megőrzése legfeljebb 30 nap** – az adatkezelési tájékoztató ezt ígéri (a törölt fiók a mentésekből is
30 napon belül eltűnik), a saját gépre letöltött másolatokra is.

### 8.1 Postgres-kliens (csak kliens, adatbázis-szerver nem kerül a gépre)

A Neon Postgres verziója a konzolban látszik (pl. 17); a `pg_dump` legalább ugyanilyen verziójú legyen:

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-client-17
```

### 8.2 Napi mentés

```bash
sudo tee /usr/local/sbin/stockwatch-learn-backup >/dev/null <<'EOF'
#!/bin/bash
# Napi pg_dump a Neonrol; 14 napig orzi. A kozvetlen (nem pooled) vegpontot hasznalja.
set -euo pipefail
url=$(sed -n 's/^DATABASE_URL=//p' /etc/stockwatch-learn/stockwatch-learn.env)
dir=/var/lib/stockwatch-learn/backup
install -d -m 700 "$dir"
pg_dump --format=custom --no-owner --dbname="${url/-pooler/}" --file="$dir/learn-$(date +%F).dump"
find "$dir" -name 'learn-*.dump' -mtime +14 -delete
EOF
sudo chmod 755 /usr/local/sbin/stockwatch-learn-backup
echo '30 3 * * * stockwatch-learn /usr/local/sbin/stockwatch-learn-backup' | sudo tee /etc/cron.d/stockwatch-learn-backup
sudo -u stockwatch-learn /usr/local/sbin/stockwatch-learn-backup && sudo ls -l /var/lib/stockwatch-learn/backup
```

✅ Van egy `learn-<dátum>.dump` fájl, és nem 0 bájt.

### 8.3 Heti másolat a saját gépre

```powershell
mkdir $HOME\stockwatch-learn-backup -Force
ssh stockwatch-learn "sudo sh -c 'install -m 600 -o ubuntu \$(ls -t /var/lib/stockwatch-learn/backup/*.dump | head -1) /home/ubuntu/latest.dump'"
scp stockwatch-learn:latest.dump "$HOME\stockwatch-learn-backup\learn-$(Get-Date -Format yyyy-MM-dd).dump"
ssh stockwatch-learn "rm /home/ubuntu/latest.dump"
```

A 30 napnál régebbi helyi másolatokat töröld (személyes adat: e-mail-címek). Ütemezhető a Windows Feladatütemezővel.

### 8.4 Visszaállítás kipróbálása (indulás előtt egyszer, utána havonta)

1. Neon konzol → *Databases → New database*: `restore_test` (ugyanazon az ágon).
2. A szerveren (a `<restore_test URL>` a Neon *Connect* ablakából, ennél az adatbázisnál, **pooler nélkül**):

   ```bash
   sudo -u stockwatch-learn pg_restore --no-owner --dbname='<restore_test URL>' \
       "$(sudo sh -c 'ls -t /var/lib/stockwatch-learn/backup/*.dump | head -1')"
   ```
3. Neon SQL Editor, `restore_test`: `SELECT count(*) FROM users;` – egyezzen az élővel.
4. Töröld a `restore_test` adatbázist.

---

## 9. Felügyelet

1. <https://uptimerobot.com> (ingyenes) → *New monitor* → HTTP(s), URL: `<URL>/login`, 5 perces ellenőrzés, riasztás
   e-mailben.
2. Hibák: `journalctl -u stockwatch-learn --since today | grep -i error` és `/opt/stockwatch-learn/logs/app.log`.
3. Lemez: `sudo stockwatch-learn-cli data-usage`.
4. A Neon ingyenes kerete (tárhely, compute-órák): Neon konzol → *Billing / Usage* – havonta érdemes ránézni.
   Tétlenség után a Neon elalszik; az első kérés így 1–2 másodperccel lassabb lehet – ez normális.
5. Biztonsági frissítések: `systemctl status unattended-upgrades` (Ubuntun alapból be van kapcsolva).

---

## 10. Frissítés (minden későbbi kiadásnál)

```powershell
git switch oktatas; git pull; git status        # tiszta munkakönyvtár
.\deploy\upload.ps1 -Server stockwatch-learn -Domain <DOMAIN>
```

A telepítő csak a kódot és a függőségeket cseréli, az új migrációkat lefuttatja; a titokfájl, a `data/` és a
`config/` érintetlen marad.

---

## 11. Indulás előtti végső ellenőrzés

Új, privát böngészőablakban, telefonon is:

- [ ] `<URL>` angolul nyílik; a nyelvválasztó mind a négy nyelvet kínálja; magyarra váltva magyar.
- [ ] Regisztráció → levél megérkezik (a választott nyelven) → megerősítés → belépés.
- [ ] *Didn't get the confirmation email?* → új link jön, a régi érvénytelen.
- [ ] Elfelejtett jelszó → levél → új jelszó → belépés; a többi eszközön kiléptet.
- [ ] Bevezető → próbafeladat → ítélet és levezetés.
- [ ] Tanulás 1. szint → feladatsor → összegzés.
- [ ] Napi kihívás → beadás → ranglista; becenév beállítása a profilban → a ranglistán a becenév látszik.
- [ ] Privát csoport létrehozása → meghívókód egy második fiókkal → közös csoport-ranglista.
- [ ] Google-belépés (ha be van állítva).
- [ ] Profil: adatletöltés (JSON), kilépés minden eszközön, fiók törlése.
- [ ] A jogi oldalak kitöltve, „TERVEZET” sor nélkül; a lábléc linkjei működnek.
- [ ] Információ oldal: a kapcsolati cím a jó.
- [ ] UptimeRobot zöld; az első napi mentés elkészült; a visszaállítás egyszer kipróbálva.

Ha minden pipa megvan: a `<URL>` megosztható.
