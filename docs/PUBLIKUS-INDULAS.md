# Publikus indulás – lépésről lépésre

A StockWatch Gyakorlás (oktatási kiadás) nyilvános elindítása: új Oracle-gép, ingyenes domain, HTTPS, e-mail,
Google-belépés, jogi oldalak, mentés, felügyelet. A lépések sorrendje számít – a ✅ sorok az ellenőrzések.

Időigény: kb. 3–4 óra, a jogász nélkül. Ami a jogászon múlik (7. lépés), az az indulás feltétele.

---

## 0. Döntések előre

| Kérdés | Javaslat |
|---|---|
| Domain | **`stocklearn.ddns.net`** (No-IP, ingyenes; 30 naponként meg kell erősíteni). Később egy saját domain (~10 USD/év) hitelesebb, és jobb a levelek kézbesítése. |
| Levelezés (SMTP) | Egy **külön Gmail-fiók** alkalmazásjelszóval (pl. `stockwatch.gyakorlas@gmail.com`) – ingyenes, kb. 500 levél/nap, jól kézbesít. Ugyanez lehet a kapcsolati cím is. |
| Szerver | Új Oracle Always Free gép, ugyanolyan, mint a mostani: **Ampere A1, 1 OCPU, 6 GB, Ubuntu 24.04** (a 22.04 Pythonja túl régi). Belefér az ingyenes A1-keretbe. |
| Cloudflare | Induláskor **ne** proxyzd (szürke felhő / ne is használd). A proxy mögött minden kérés a Cloudflare IP-jéről jönne, és a gyakoriság-korlát (belépés, regisztráció) rossz IP-re számolna. |
| Üzemeltető | Ki az adatkezelő / szolgáltató (magánszemély, egyéni vállalkozó, cég)? A neve és a címe a jogi oldalakra kerül – a jogásszal együtt döntsd el (7. lépés). |

---

## 1. Oracle-gép létrehozása

A mostani StockWatch-gép (Ampere A1, 1 OCPU, 6 GB, Ubuntu 24.04) az ingyenes A1-keretből (összesen 4 OCPU + 24 GB)
csak 1 OCPU-t és 6 GB-ot használ – egy ugyanilyen második gép belefér.

1. <https://cloud.oracle.com> → belépés ugyanabba a fiókba. Jobb fent a régió a **home region** legyen (ingyenes gép
   csak ott hozható létre; ugyanaz, ahol a mostani StockWatch fut).
2. Bal felső ☰ menü → **Compute → Instances** → bal oldalt a **Compartment** ugyanaz, mint a mostani gépé →
   **Create instance**.
3. **Name:** `stocklearn`.
4. **Placement:** maradhat az alapértelmezett Availability Domain.
5. **Image and shape → Edit:**
   - **Change image** → *Ubuntu* → **Canonical Ubuntu 24.04** (nem a *Minimal*) → *Select image*.
   - **Change shape** → *Virtual machine* → **Ampere** → **VM.Standard.A1.Flex** → *Number of OCPUs*: **1**,
     *Amount of memory (GB)*: **6** → *Select shape*. Mellette az „Always Free-eligible” címke látszik.
   - (A képet a konzol az ARM-hez illő aarch64 változatra cseréli – ez rendben van.)
6. **Networking → Edit:**
   - *Primary network*: **Select existing virtual cloud network** → ugyanaz a VCN, mint a mostani gépé.
   - *Subnet*: **Select existing subnet** → a publikus alhálózat (*public subnet*).
   - **Automatically assign public IPv4 address**: bekapcsolva.
7. **Add SSH keys:** **Upload public key files (.pub)** → `C:\Users\biote\.ssh\oracle-stockwatch.key.pub`
   (ugyanaz a kulcs, mint a mostani gépnél; a privát fele a gépeden marad).
8. **Boot volume:** maradhat az alapértelmezett (~47 GB). Az ingyenes keret 200 GB összesen; a mostani gép ~47 GB-ot
   foglal.
9. **Create.** Pár perc, amíg az állapot *Running* lesz. Az *Instance information* oldalon jegyezd fel a
   **Public IP address**-t.
   - **„Out of capacity for shape VM.Standard.A1.Flex”** hiba: válassz másik Availability Domaint (4. pont), vagy
     próbáld újra órák múlva. Tartós hiánynál segít a fiók *Pay As You Go*-ra váltása (az ingyenes keretig ott sem
     kell fizetni, de bankkártya kell, és a kereten felüli használat már pénzbe kerül).
10. **Tűzfal (VCN):** ☰ → **Networking → Virtual cloud networks** → a VCN → **Subnets** → a publikus alhálózat →
    **Security Lists** → *Default Security List* → **Ingress Rules**. Ha még nincs szabály a **80**-as és a
    **443**-as portra, **Add Ingress Rules**, kétszer:
    - *Source CIDR*: `0.0.0.0/0`, *IP Protocol*: **TCP**, *Destination Port Range*: `80`;
    - ugyanígy `443`-mal.
    (A gépen belüli iptables-szabályt a telepítő nyitja meg; a 22-es SSH-port alapból nyitva van.)
11. A saját gépeden a `C:\Users\biote\.ssh\config` végére:

    ```
    # StockWatch Gyakorlas (oktatasi kiadas)
    Host stockwatch-learn
        HostName <PUBLIC IP>
        User ubuntu
        IdentityFile ~/.ssh/oracle-stockwatch.key
        IdentitiesOnly yes
        ServerAliveInterval 30
    ```

12. Első belépés és frissítés:

    ```powershell
    ssh stockwatch-learn        # első alkalommal: "Are you sure you want to continue connecting?" -> yes
    ```
    ```bash
    lsb_release -d && uname -m                      # Ubuntu 24.04..., aarch64
    sudo apt update && sudo apt -y full-upgrade
    sudo reboot                                     # utána pár perc múlva újra: ssh stockwatch-learn
    ```

✅ `ssh stockwatch-learn` belép, a gép Ubuntu 24.04, aarch64.

Megjegyzés: az Oracle a nagyon kihasználatlan *Always Free* gépeket (7 napig alacsony CPU-, hálózat- és
memóriahasználat) leállíthatja. Ilyenkor a konzolból újraindítható; *Pay As You Go* fióknál ez nem fordul elő.

---

## 2. Domain (No-IP)

A domain: **`stocklearn.ddns.net`** (No-IP, ingyenes).

1. <https://my.noip.com> → *DNS Records* → a `stocklearn.ddns.net` sor → szerkesztés (ceruza).
2. **IPv4 Address** (Content): az 1. lépés **Public IP**-je (nem a saját otthoni IP-d – a No-IP létrehozáskor azt
   írja be automatikusan) → *Save*.
3. Ehhez a hostnévhez **ne** telepíts No-IP DUC-programot, és a routeren se állítsd be: az visszaírná az otthoni IP-t.
4. **30 naponként megerősítés:** az ingyenes No-IP hostnevet havonta meg kell erősíteni – a No-IP e-mailt küld
   („Confirm your hostname”), a benne lévő gombra kell kattintani, különben a hostnév megszűnik és az oldal
   elérhetetlenné válik. Tedd a naptáradba is.

✅ A saját gépen: `nslookup stocklearn.ddns.net` az Oracle-gép IP-jét adja (a TTL 60 mp, pár perc alatt átáll).

A továbbiakban `<DOMAIN>` = `stocklearn.ddns.net`, `<URL>` = `https://stocklearn.ddns.net`.

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
a Caddy-t (HTTPS, Let's Encrypt) és a napi mentés időzítőjét, legenerálja a szintetikus feladatkészletet, és
létrehozza az üres titokfájlt.
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
sudo systemctl enable --now stockwatch-learn-backup.timer   # napi mentés (első telepítéskor a titkok még hiányoztak)
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
   - **Authorized domains:** `<DOMAIN>`. Ha a Google nem fogadja el (a `ddns.net` közös No-IP-domain), a
     Google-belépés egy saját domainig várhat – a szolgáltatás nélküle is teljes (e-mail + jelszó).
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

Állapot (2026-10-23): ellenőrizve – a `production` ág `StockWatch_Learning` adatbázisa **üres** (0 fiók, minden tábla
üres, nincs ottmaradt teszt-séma), a régió `eu-central-1` (AWS Frankfurt), Postgres 18. Indulás előtt csak akkor kell
újra megnézni, ha azóta valaki próbafiókot hozott létre.

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

A Neon saját visszaállítási pontjai mellett **napi mentés a szerveren** – ezt a telepítő már beállította
(`stockwatch-learn-backup.timer`, minden éjjel 03:30 körül). A mentés az alkalmazás része (`cli.py db backup`), a
gépre nem kell `pg_dump` vagy más adatbázis-szoftver.

- Hely: `/opt/stockwatch-learn/data/backup/learn-<dátum>.zip` (csak a szolgáltatás felhasználója olvashatja).
- Megőrzés: **14 nap** – a régebbiek egy sikeres mentés után automatikusan törlődnek; lemezplafon 50 MB (felette a
  legrégebbi törlődik, a legújabb mindig megmarad). Az adatkezelési tájékoztató 30 napot ígér: a saját gépre
  letöltött másolatokra is ez vonatkozik.
- Sikertelen mentés nem töröl régit.

### 8.1 Ellenőrzés a telepítés után

```bash
systemctl list-timers stockwatch-learn-backup        # mikor fut legközelebb
sudo systemctl start stockwatch-learn-backup         # most azonnal egy mentés
systemctl status stockwatch-learn-backup --no-pager  # "Mentes: ... sor" a naplóban
sudo ls -l /opt/stockwatch-learn/data/backup
```

✅ Van egy `learn-<mai dátum>.zip`, és a `systemctl --failed` nem listázza a mentést.

### 8.2 Heti másolat a saját gépre (gépen kívüli példány)

```powershell
mkdir $HOME\stockwatch-learn-backup -Force
ssh stockwatch-learn "sudo sh -c 'install -m 600 -o ubuntu \$(ls -t /opt/stockwatch-learn/data/backup/learn-*.zip | head -1) /home/ubuntu/latest.zip'"
scp stockwatch-learn:latest.zip "$HOME\stockwatch-learn-backup\learn-$(Get-Date -Format yyyy-MM-dd).zip"
ssh stockwatch-learn "rm /home/ubuntu/latest.zip"
```

A 30 napnál régebbi helyi másolatokat töröld (személyes adat: e-mail-címek). Ütemezhető a Windows Feladatütemezővel.

### 8.3 Visszaállítás kipróbálása (indulás előtt egyszer, utána havonta)

A visszaállítás csak **üres** adatbázisba megy, és előtte lefuttatja a migrációkat – az élő adatbázist nem
írhatja felül.

1. Neon konzol → *Databases → New database*: `restore_test` (ugyanazon az ágon). A *Connect* ablakból másold ki a
   kapcsolati szövegét (ennél az adatbázisnál).
2. A szerveren egy ideiglenes titokfájl csak erre az egy parancsra:

   ```bash
   sudo -u stockwatch-learn sh -c 'umask 077; cat > /var/lib/stockwatch-learn/restore-test.env' <<'END'
   DATABASE_URL=<a restore_test kapcsolati szövege>
   END
   cd /opt/stockwatch-learn && sudo -u stockwatch-learn env STOCKWATCH_ENV_FILE=/var/lib/stockwatch-learn/restore-test.env \
       HOME=/var/lib/stockwatch-learn .venv/bin/python cli.py db restore \
       "$(sudo sh -c 'ls -t /opt/stockwatch-learn/data/backup/learn-*.zip | head -1')"
   sudo rm /var/lib/stockwatch-learn/restore-test.env
   ```
3. A kimenet: `Visszaallitva: N sor, 13 tabla`. Neon SQL Editor, `restore_test`: `SELECT count(*) FROM users;` –
   egyezzen az élővel (a mentés óta regisztráltak nélkül).
4. Töröld a `restore_test` adatbázist.

Valódi katasztrófánál (az élő adatbázis elveszett): új, üres Neon-adatbázis → a titokfájlban az új `DATABASE_URL`
→ `sudo stockwatch-learn-cli db restore <a legutóbbi learn-*.zip>` → `sudo systemctl restart stockwatch-learn`.
A mentést azzal a kódverzióval állítsd vissza, amelyik készítette (a parancs szól, ha a migrációs állapot eltér).

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
- [ ] UptimeRobot zöld; az első mentés elkészült (8.1); a visszaállítás egyszer kipróbálva (8.3).

Ha minden pipa megvan: a `<URL>` megosztható.
