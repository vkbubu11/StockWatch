#!/usr/bin/env bash
# StockWatch telepito / frissito - Ubuntu 22.04+ vagy Debian 12+ szerverre, rootkent.
#
#   sudo bash deploy/install.sh --domain stockwatch.pelda.hu    # HTTPS-sel (Caddy, Let's Encrypt)
#   sudo bash deploy/install.sh                                 # csak SSH-alagutas eleres
#
# Ujrafuttathato. Frissiteskor csak a kodot es a fuggosegeket csereli; a szerveren levo
# data/, config/ es a titokfajl (/etc/stockwatch/stockwatch.env) erintetlen marad.
#
#   /opt/stockwatch                  kod (root, irasvedett) + .venv
#   /opt/stockwatch/{data,config,logs}   a szolgaltatas irhatja
#   /etc/stockwatch/stockwatch.env   titkok, 0600, csak a stockwatch felhasznalo olvassa
#   /var/lib/stockwatch              a szolgaltatas HOME-ja (yfinance cache)
set -euo pipefail

APP_DIR=/opt/stockwatch
ENV_DIR=/etc/stockwatch
ENV_FILE=$ENV_DIR/stockwatch.env
STATE_DIR=/var/lib/stockwatch
SVC_USER=stockwatch
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN=""

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mFIGYELEM: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mHIBA: %s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
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
# Headless Chrome a Telegram chart-kepekhez (kaleido). Ha nem sikerul, a riasztas kep nelkul megy.
apt-get install -y -q --no-install-recommends libnss3 libatk-bridge2.0-0 libcups2 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 >/dev/null 2>&1 \
  || warn "A Chrome-fuggosegek egy resze nem telepult (chart-kepek nelkul is mukodik)."
apt-get install -y -q libasound2t64 >/dev/null 2>&1 || apt-get install -y -q libasound2 >/dev/null 2>&1 || true

# Kis memoriaju (1 GB-os, ingyenes) gepen a pip telepites es a chart-rendereles swap nelkul elfogyhat
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
  say "Elso telepites: config/ es data/ atvetele a feltoltott csomagbol"
  cp -a "$SRC_DIR/config" "$APP_DIR/config"
  if [[ -d "$SRC_DIR/data" ]]; then
    rsync -a --exclude '/research/' --exclude '/universe/' "$SRC_DIR/data/" "$APP_DIR/data/"
  fi
fi
install -d "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR/data" "$APP_DIR/config" "$APP_DIR/logs"
chmod 700 "$APP_DIR/data" "$APP_DIR/config" "$APP_DIR/logs"

# --- 4. Titkok ----------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$SRC_DIR/.env" ]]; then
    say "Titkok atvetele a feltoltott .env-bol -> $ENV_FILE"
    sed 's/\r$//' "$SRC_DIR/.env" > "$ENV_FILE"
  else
    say "Ures titokfajl a minta alapjan -> $ENV_FILE"
    sed 's/\r$//' "$SRC_DIR/.env.example" > "$ENV_FILE"
  fi
fi
chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# --- 5. Python kornyezet ------------------------------------------------------
say "Python fuggosegek (elso alkalommal par perc)"
[[ -x "$APP_DIR/.venv/bin/python" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/src" "$APP_DIR/cli.py" >/dev/null || true
# ARM gepen (pl. Oracle Ampere) a Chrome for Testing-nek nincs linux-arm64 valtozata: a Playwright
# projekt arm64-es Chromium headless shell-je kerul a /opt/chromium-ba, "chromium" neven a PATH-ra
# (a kaleido ezen a neven is keresi). Fix meret (~280 MB), nem no.
if [[ "$(uname -m)" == "aarch64" ]] && ! command -v chromium >/dev/null; then
  say "Chromium (headless shell) ARM-ra a chart-kepekhez"
  PW_VENV=$(mktemp -d)
  if python3 -m venv "$PW_VENV" && "$PW_VENV/bin/pip" install -q playwright \
     && PLAYWRIGHT_BROWSERS_PATH=/opt/chromium "$PW_VENV/bin/playwright" install --with-deps --only-shell chromium >/dev/null; then
    ln -sf "$(ls /opt/chromium/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell | tail -1)" \
      /usr/local/bin/chromium
  else
    warn "A Chromium telepitese nem sikerult."
  fi
  rm -rf "$PW_VENV"
fi
# a Chrome a futtato felhasznalo HOME-jaba kerul - ezert a szolgaltatas neveben toltjuk le
if [[ -x "$APP_DIR/.venv/bin/plotly_get_chrome" && "$(uname -m)" != "aarch64" ]]; then
  as_svc "$APP_DIR/.venv/bin/plotly_get_chrome" -y >/dev/null 2>&1 || true
fi
CHART_TEST='import plotly.graph_objects as go; go.Figure().to_image(format="png", width=50, height=50)'
as_svc timeout 120 "$APP_DIR/.venv/bin/python" -c "$CHART_TEST" >/dev/null 2>&1 \
  && echo "Chart-kep rendereles: rendben" \
  || warn "A chart-kep rendereles nem mukodik - a Telegram-riasztas kep nelkul megy (minden mas rendben)."

# --- 6. Naplo-plafon (minden irt fajlnak legyen felso korlatja) ---------------
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/stockwatch.conf <<'EOF'
# StockWatch telepito: a rendszernaplo ne nojon korlatlanul
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald

# --- 7. Webes jelszo ------------------------------------------------------------
if ! grep -qE '^STOCKWATCH_WEB_PASSWORD_HASH=.+' "$ENV_FILE"; then
  say "A webes felulet jelszava (min. 12 karakter)"
  if [[ -t 0 && -t 1 ]]; then
    as_svc "$APP_DIR/.venv/bin/python" "$APP_DIR/cli.py" web-password \
      || warn "Nem lett jelszo beallitva. Kesobb: sudo stockwatch-password"
  else
    warn "Nincs terminal - a jelszot kesobb allitsd be: sudo stockwatch-password"
  fi
fi
cat > /usr/local/sbin/stockwatch-password <<EOF
#!/bin/sh
# A StockWatch webes jelszo beallitasa / csereje
runuser -u $SVC_USER -- env STOCKWATCH_ENV_FILE=$ENV_FILE HOME=$STATE_DIR $APP_DIR/.venv/bin/python $APP_DIR/cli.py web-password "\$@" \
  && systemctl restart stockwatch-web && echo "A webes felulet fut."
EOF
chmod 755 /usr/local/sbin/stockwatch-password
cat > /usr/local/sbin/stockwatch-cli <<EOF
#!/bin/sh
# Barmely cli.py parancs a szolgaltatas felhasznalojakent, pl.: sudo stockwatch-cli discord
cd $APP_DIR && exec runuser -u $SVC_USER -- env STOCKWATCH_ENV_FILE=$ENV_FILE HOME=$STATE_DIR $APP_DIR/.venv/bin/python $APP_DIR/cli.py "\$@"
EOF
chmod 755 /usr/local/sbin/stockwatch-cli

# --- 8. Kezdo adat ------------------------------------------------------------
if [[ $FIRST_INSTALL -eq 1 ]] && ! compgen -G "$APP_DIR/data/ohlcv/*.csv" >/dev/null; then
  say "Arfolyamadat letoltese (nincs feltoltott data/)"
  as_svc "$APP_DIR/.venv/bin/python" cli.py fetch --symbols all \
    || warn "A letoltes nem sikerult; a monitor a kovetkezo ciklusban ujraprobalja."
fi

# --- 9. Szolgaltatasok ----------------------------------------------------------
say "systemd szolgaltatasok"
install -m 644 "$APP_DIR/deploy/stockwatch-monitor.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/stockwatch-web.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/stockwatch-lab.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stockwatch-monitor stockwatch-web stockwatch-lab >/dev/null
systemctl restart stockwatch-monitor stockwatch-lab
if grep -qE '^STOCKWATCH_WEB_PASSWORD_HASH=.+' "$ENV_FILE"; then
  systemctl restart stockwatch-web
else
  # jelszo nelkul a felulet nem indulhat - ne porogjon feleslegesen ujrainditasban
  systemctl stop stockwatch-web
  warn "A webes felulet jelszo nelkul nem indul. Beallitas: sudo stockwatch-password (utana magatol elindul)"
fi

# --- 10. HTTPS (Caddy) ------------------------------------------------------------
if [[ -n "$DOMAIN" ]]; then
  say "Caddy HTTPS: https://$DOMAIN"
  if ! apt-get install -y -q caddy; then
    # a disztribucio tarolojaban nincs (pl. Ubuntu 22.04): a Caddy hivatalos tarolojabol
    apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https curl gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q && apt-get install -y -q caddy
  fi
  if [[ -f /etc/caddy/Caddyfile ]] && ! grep -q '# StockWatch' /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
  fi
  cat > /etc/caddy/Caddyfile <<EOF
# StockWatch - a deploy/install.sh irta. Tanusitvany: automatikusan (Let's Encrypt).
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8765
	header {
		-Server
		X-Robots-Tag "noindex, nofollow"
	}
	log {
		output file /var/log/caddy/stockwatch.log {
			roll_size 10MiB
			roll_keep 3
		}
	}
}
EOF
  # Oracle Cloud Ubuntu kepei iptables-szaballyal mindent eldobnak a 22-es porton kivul
  if iptables -S INPUT 2>/dev/null | grep -q -- '-j REJECT'; then
    for port in 80 443; do
      iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null \
        || iptables -I INPUT 5 -m state --state NEW -p tcp --dport $port -j ACCEPT
    done
    command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
    warn "Oracle Cloud: a VCN Security List-ben is nyisd meg a 80-as es 443-as portot (Ingress, 0.0.0.0/0)."
  fi
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  systemctl enable caddy >/dev/null
  systemctl reload caddy || systemctl restart caddy
fi

# --- Osszegzes ------------------------------------------------------------------
sleep 3
say "Kesz"
systemctl --no-pager --lines=0 status stockwatch-monitor stockwatch-web stockwatch-lab | grep -E '●|Active:' || true
cat <<EOF

  Naplo:          journalctl -u stockwatch-monitor -f      (es: $APP_DIR/logs/app.log)
  Labor:          sudo stockwatch-cli lab status           (naplo: journalctl -u stockwatch-lab -f)
  Ujrainditas:    sudo systemctl restart stockwatch-monitor stockwatch-web
  Jelszocsere:    sudo stockwatch-password
  Discord:        sudo stockwatch-cli discord && sudo systemctl restart stockwatch-monitor
  CLI parancs:    sudo stockwatch-cli <parancs>   (pl. positions, broker, test-alert)
  Titkok:         sudo -u $SVC_USER nano $ENV_FILE   (utana: ujrainditas)
EOF
if [[ -n "$DOMAIN" ]]; then
  echo "  Felulet:        https://$DOMAIN   (a domain A rekordja erre a szerverre mutasson, 80/443 nyitva)"
else
  echo "  Felulet:        a sajat gepeden: ssh -L 8765:127.0.0.1:8765 <felhasznalo>@<szerver>"
  echo "                  majd bongeszoben: http://127.0.0.1:8765"
fi
