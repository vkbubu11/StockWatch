#!/usr/bin/env bash
# StockWatch Gyakorlas (oktatasi kiadas) telepito / frissito - Ubuntu 22.04+ vagy Debian 12+ szerverre, rootkent.
#
#   sudo bash deploy/install.sh --domain gyakorlas.pelda.hu    # HTTPS-sel (Caddy, Let's Encrypt)
#   sudo bash deploy/install.sh                                # csak SSH-alagutas eleres
#
# A korabbi StockWatch MELLETT fut ugyanazon a gepen (Oracle): sajat konyvtar, felhasznalo, titokfajl,
# szolgaltatas (stockwatch-learn) es port (8766). A Caddy-ba csak egy sajat oldalblokkot illeszt be
# (import) - a meglevo StockWatch-oldalt nem irja felul. Ujrafuttathato; frissiteskor csak a kodot es a
# fuggosegeket csereli, a szerveren levo data/, config/ es titokfajl erintetlen marad.
#
#   /opt/stockwatch-learn                        kod (root, irasvedett) + .venv
#   /opt/stockwatch-learn/{data,config,logs}     a szolgaltatas irhatja
#   /etc/stockwatch-learn/stockwatch-learn.env   titkok (DATABASE_URL, SMTP...), 0600
#   /var/lib/stockwatch-learn                    a szolgaltatas HOME-ja
set -euo pipefail

NAME=stockwatch-learn
APP_DIR=/opt/$NAME
ENV_DIR=/etc/$NAME
ENV_FILE=$ENV_DIR/$NAME.env
STATE_DIR=/var/lib/$NAME
SVC_USER=$NAME
PORT=8766                                       # a korabbi StockWatch a 8765-on fut
SITE_FILE=/etc/caddy/$NAME.caddy
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN=""

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mFIGYELEM: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mHIBA: %s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "Ismeretlen kapcsolo: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Rootkent futtasd: sudo bash deploy/install.sh"
[[ -f "$SRC_DIR/cli.py" && -f "$SRC_DIR/requirements.txt" ]] || die "Nem talalom a forrast itt: $SRC_DIR"
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then die "Ervenytelen domain: $DOMAIN"; fi
as_svc() { (cd "$APP_DIR" && runuser -u "$SVC_USER" -- env STOCKWATCH_ENV_FILE="$ENV_FILE" HOME="$STATE_DIR" "$@"); }

# --- 1. Rendszercsomagok ---------------------------------------------------
say "Rendszercsomagok"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip rsync ca-certificates

# Kis memoriaju (1 GB-os, ingyenes) gepen a pip telepites swap nelkul elfogyhat
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [[ $MEM_KB -lt 2000000 ]] && ! swapon --noheadings | grep -q .; then
  say "Keves a memoria ($((MEM_KB / 1024)) MB): 2 GB swapfajl"
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || die "Python 3.11+ kell (ez: $(python3 --version)). Ubuntu 24.04 vagy Debian 12+ ajanlott."

# --- 2. Felhasznalo es mappak ---------------------------------------------
say "Szolgaltatas-felhasznalo: $SVC_USER"
id "$SVC_USER" >/dev/null 2>&1 \
  || useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 "$STATE_DIR" "$ENV_DIR"

# --- 3. Kod -----------------------------------------------------------------
say "Kod masolasa: $APP_DIR"
FIRST_INSTALL=0
[[ -d "$APP_DIR/config" ]] || FIRST_INSTALL=1
install -d -m 755 "$APP_DIR"
# --delete: a regi kodfajlok torlodnek; a kizart mappak (data, config, logs, .venv) vedettek
rsync -a --delete --chown=root:root \
  --exclude '/.venv/' --exclude '/data/' --exclude '/config/' --exclude '/logs/' \
  --exclude '/.env' --exclude '/.env.*.tmp' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude '*.pyc' --exclude '/start.bat' \
  "$SRC_DIR/" "$APP_DIR/"
if [[ $FIRST_INSTALL -eq 1 ]]; then
  say "Elso telepites: config/ atvetele a csomagbol"
  cp -a "$SRC_DIR/config" "$APP_DIR/config"
fi
install -d "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR/data" "$APP_DIR/config" "$APP_DIR/logs"
chmod 700 "$APP_DIR/data" "$APP_DIR/config" "$APP_DIR/logs"

# --- 4. Titkok ----------------------------------------------------------------
# a titkok soha nem jonnek a feltoltott csomagbol (a helyi .env a fo StockWatch kulcsait is tartalmazza)
if [[ ! -f "$ENV_FILE" ]]; then
  say "Ures titokfajl a minta alapjan -> $ENV_FILE"
  sed 's/\r$//' "$SRC_DIR/.env.example" > "$ENV_FILE"
fi
chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# --- 5. Python kornyezet ------------------------------------------------------
say "Python fuggosegek (elso alkalommal par perc)"
[[ -x "$APP_DIR/.venv/bin/python" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/src" "$APP_DIR/cli.py" >/dev/null || true

# --- 6. Naplo-plafon (minden irt fajlnak legyen felso korlatja) ---------------
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/$NAME.conf <<'EOF'
# StockWatch Gyakorlas telepito: a rendszernaplo ne nojon korlatlanul
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald

# --- 7. Adatbazis es levelezes -------------------------------------------------
# DATABASE_URL (Neon), SMTP_HOST / MAIL_FROM (ellenorzo levelek), PUBLIC_URL (a levelek linkje) a titokfajlban
for key in DATABASE_URL SMTP_HOST MAIL_FROM PUBLIC_URL; do
  grep -qE "^$key=.+" "$ENV_FILE" || warn "$key nincs beallitva: sudo -u $SVC_USER nano $ENV_FILE (lasd README)"
done
if grep -qE '^DATABASE_URL=.+' "$ENV_FILE"; then
  say "Adatbazis-migraciok"
  as_svc "$APP_DIR/.venv/bin/python" cli.py db migrate || warn "A migracio nem sikerult (sudo $NAME-cli db migrate)"
fi
cat > /usr/local/sbin/$NAME-cli <<EOF
#!/bin/sh
# Barmely cli.py parancs a szolgaltatas felhasznalojakent, pl.: sudo $NAME-cli practice-pool
cd $APP_DIR && exec runuser -u $SVC_USER -- env STOCKWATCH_ENV_FILE=$ENV_FILE HOME=$STATE_DIR $APP_DIR/.venv/bin/python $APP_DIR/cli.py "\$@"
EOF
chmod 755 /usr/local/sbin/$NAME-cli

# --- 8. Kezdo adat ------------------------------------------------------------
if [[ ! -f "$APP_DIR/data/practice/pool.json.gz" ]]; then
  say "Feladatkeszlet (szintetikus, ~1-2 perc)"
  as_svc "$APP_DIR/.venv/bin/python" cli.py practice-pool \
    || warn "A feladatkeszlet nem keszult el; kesobb: sudo $NAME-cli practice-pool"
fi

# --- 9. Szolgaltatas -------------------------------------------------------------
say "systemd szolgaltatas: $NAME"
install -m 644 "$APP_DIR/deploy/$NAME.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$NAME" >/dev/null
if grep -qE '^DATABASE_URL=.+' "$ENV_FILE"; then
  systemctl restart "$NAME"
else
  # adatbazis nelkul a felulet nem indulhat - ne porogjon feleslegesen ujrainditasban
  systemctl stop "$NAME"
  warn "A felulet DATABASE_URL nelkul nem indul. Beallitas utan: sudo $NAME-cli db migrate && sudo systemctl restart $NAME"
fi

# --- 10. HTTPS (Caddy) ------------------------------------------------------------
if [[ -n "$DOMAIN" ]]; then
  say "Caddy HTTPS: https://$DOMAIN"
  if ! command -v caddy >/dev/null; then
    if ! apt-get install -y -q caddy; then
      # a disztribucio tarolojaban nincs (pl. Ubuntu 22.04): a Caddy hivatalos tarolojabol
      apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -q && apt-get install -y -q caddy
    fi
  fi
  # sajat oldalblokk kulon fajlban; a fo Caddyfile-ba csak egy import sor kerul (a tobbi oldal erintetlen)
  cat > "$SITE_FILE" <<EOF
# StockWatch Gyakorlas - a deploy/install.sh irta. Tanusitvany: automatikusan (Let's Encrypt).
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
	header {
		-Server
	}
	log {
		output file /var/log/caddy/$NAME.log {
			roll_size 10MiB
			roll_keep 3
		}
	}
}
EOF
  touch /etc/caddy/Caddyfile
  if ! grep -qxF "import $SITE_FILE" /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
    printf '\nimport %s\n' "$SITE_FILE" >> /etc/caddy/Caddyfile
  fi
  # Oracle Cloud Ubuntu kepei iptables-szaballyal mindent eldobnak a 22-es porton kivul
  if iptables -S INPUT 2>/dev/null | grep -q -- '-j REJECT'; then
    for port in 80 443; do
      iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null \
        || iptables -I INPUT 5 -m state --state NEW -p tcp --dport $port -j ACCEPT
    done
    command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
    warn "Oracle Cloud: a VCN Security List-ben is legyen nyitva a 80-as es 443-as port (Ingress, 0.0.0.0/0)."
  fi
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  systemctl enable caddy >/dev/null
  systemctl reload caddy || systemctl restart caddy
fi

# --- Osszegzes ------------------------------------------------------------------
sleep 3
say "Kesz"
systemctl --no-pager --lines=0 status "$NAME" | grep -E '●|Active:' || true
cat <<EOF

  Naplo:          journalctl -u $NAME -f      (es: $APP_DIR/logs/app.log)
  Ujrainditas:    sudo systemctl restart $NAME
  Migracio:       sudo $NAME-cli db migrate
  CLI parancs:    sudo $NAME-cli <parancs>   (pl. practice-pool, data-usage)
  Titkok:         sudo -u $SVC_USER nano $ENV_FILE   (utana: ujrainditas)
EOF
if [[ -n "$DOMAIN" ]]; then
  echo "  Felulet:        https://$DOMAIN   (a domain A rekordja erre a szerverre mutasson, 80/443 nyitva)"
else
  echo "  Felulet:        a sajat gepeden: ssh -L $PORT:127.0.0.1:$PORT <felhasznalo>@<szerver>"
  echo "                  majd bongeszoben: http://127.0.0.1:$PORT"
fi
