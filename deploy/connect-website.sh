#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – connect your WEBSITE SERVER to the bot.  Run it ON THE WEBSITE SERVER:
#
#      sudo bash deploy/connect-website.sh
#
#  What it does (it asks before each change):
#    1. makes a secure, encrypted link to the bot server (an SSH tunnel that can
#       only reach the bot – nothing else on that server),
#    2. tells your web server (nginx or Apache) to pass /public and /api to the bot,
#    3. puts the public pages on  /bugs/  and the staff dashboard on  /staff/.
#  Running it again is safe (for example after the bot server got a new address).
# =============================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF=/etc/wos-tunnel
KEY=$CONF/id_ed25519
WWW=/var/www/wos
TUNNEL_USER=wostunnel
BOT_PORT=8080
TOTAL=5

if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B='' G='' Y='' R='' C='' N=''; fi
STEP=0
step()  { STEP=$((STEP+1)); printf '\n%s━━ Step %s of %s: %s ━━%s\n' "$B$C" "$STEP" "$TOTAL" "$1" "$N"; }
ok()    { printf '  %s✔%s %s\n' "$G" "$N" "$1"; }
info()  { printf '  %s\n' "$1"; }
warn()  { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()   { printf '\n  %s✘ %s%s\n\n' "$R" "$1" "$N"; exit 1; }
pause() { read -rp "  ${B}Press Enter when you are ready…${N} " _ </dev/tty; }
yes_no(){ local a; read -rp "  $1 [${2:-y}] " a </dev/tty; a="${a:-${2:-y}}"; [[ "$a" =~ ^[YyJj] ]]; }
ask()   { local a; read -rp "  $1${2:+ [$2]}: " a </dev/tty; echo "${a:-${2:-}}"; }

if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi
[[ -f "$SRC/dashboard.html" && -d "$SRC/site" ]] || die "Run this from the unzipped bugbot folder:  sudo bash bugbot/deploy/connect-website.sh"

fetch() {  # fetch <url> [host] → prints the body (empty on failure)
  local host="${2:-}"; local -a hdr=()
  [[ -n "$host" ]] && hdr=(-H "Host: $host")
  if command -v curl >/dev/null; then curl -fsSkL --max-time 8 "${hdr[@]}" "$1" 2>/dev/null || true
  else python3 -c 'import sys,urllib.request,ssl
r=urllib.request.Request(sys.argv[1]);
len(sys.argv)>2 and sys.argv[2] and r.add_header("Host",sys.argv[2])
try: print(urllib.request.urlopen(r,timeout=8,context=ssl._create_unverified_context()).read().decode())
except Exception: pass' "$1" "$host"; fi
}
port_free() { ! ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }

printf '\n%sWOS EU1 – connect the website to the bot%s\n' "$B" "$N"
info "Keep a second window open with the bot server's SSH – you'll need it once in step 2."

# ----------------------------------------------------------------- 1 web server
step "Checking this server"
WEB=""
if command -v nginx >/dev/null && systemctl is-active --quiet nginx; then WEB=nginx
elif command -v apache2 >/dev/null && systemctl is-active --quiet apache2; then WEB=apache
fi
case "$WEB" in
  nginx)  ok "Your website runs on nginx" ;;
  apache) ok "Your website runs on Apache" ;;
  *) warn "I couldn't find nginx or Apache running here."
     info "I can still make the secure link; step 4 will then show you what to add by hand."
     yes_no "Continue?" y || exit 0 ;;
esac
command -v ssh >/dev/null || { apt-get install -y -qq openssh-client >/dev/null 2>&1 || die "Please install the ssh client first (apt install openssh-client)."; }

# ----------------------------------------------------------------- 2 secure link
step "Secure link to the bot server"
OLD_HOST="$(cat $CONF/host 2>/dev/null || true)"
BOT_HOST="$(ask "Bot server address (the External IP from the bot manual)" "$OLD_HOST")"
[[ -n "$BOT_HOST" ]] || die "I need the bot server's address."
LOCAL_PORT="$(cat $CONF/port 2>/dev/null || true)"
if [[ -z "$LOCAL_PORT" ]]; then
  for p in 8080 8787 8788 8789 18080; do port_free "$p" && { LOCAL_PORT=$p; break; }; done
  [[ -n "$LOCAL_PORT" ]] || die "No free local port found (tried 8080, 8787–8789, 18080)."
fi
systemctl stop wos-tunnel 2>/dev/null || true
install -d -m 700 $CONF
[[ -f $KEY ]] || ssh-keygen -q -t ed25519 -N '' -C wos-website -f $KEY
echo "$BOT_HOST" > $CONF/host; echo "$LOCAL_PORT" > $CONF/port

SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new
          -o "UserKnownHostsFile=$CONF/known_hosts" -o ServerAliveInterval=30 -o ServerAliveCountMax=3
          -o ExitOnForwardFailure=yes)
try_link() {   # opens the link for a moment and asks the bot for its public settings
  local pid out
  ssh "${SSH_OPTS[@]}" -N -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:$BOT_PORT" "$TUNNEL_USER@$BOT_HOST" 2>$CONF/last-error &
  pid=$!
  for _ in 1 2 3 4 5 6 7 8; do sleep 1; out="$(fetch "http://127.0.0.1:$LOCAL_PORT/public/config")"; [[ "$out" == *enabled* ]] && break; kill -0 $pid 2>/dev/null || break; done
  kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true
  [[ "$out" == *enabled* ]]
}

if try_link; then
  ok "The link already works"
elif [[ -f /opt/wosbot/deploy/install.sh && ( "$BOT_HOST" == 127.0.0.1 || "$BOT_HOST" == localhost ) ]]; then
  info "The bot is on this same server – giving the website its key automatically."
  bash /opt/wosbot/deploy/install.sh add-website "$(cat $KEY.pub)" >/dev/null
  if try_link; then ok "Link works – the bot answered"
  else die "The bot didn't answer. Is it running?  sudo bash /opt/wosbot/deploy/install.sh status"; fi
else
  info "Now go to the ${B}bot server${N} window and paste this ONE line there, then press Enter:"
  printf '\n%s%s%s\n\n' "$C" "sudo bash /opt/wosbot/deploy/install.sh add-website '$(cat $KEY.pub)'" "$N"
  info "(Tip: copy everything from ${B}sudo${N} to the last ${B}'${N} – it is one long line.)"
  while true; do
    pause
    if try_link; then ok "Link works – the bot answered"; break; fi
    err="$(tail -3 $CONF/last-error 2>/dev/null || true)"
    warn "Not yet. ${err:+(${err//$'\n'/ })}"
    if [[ "$err" == *"Permission denied"* ]]; then info "→ The line wasn't run on the bot server yet, or was cut off. Paste it again there."
    elif [[ "$err" == *"timed out"* || "$err" == *"No route"* || "$err" == *"refused"* ]]; then info "→ Is the address right ($BOT_HOST)? Is the bot server running?"
    else info "→ Is the bot running? On the bot server:  sudo systemctl status wosbot"; fi
    yes_no "Try again? (n = stop here)" y || exit 1
  done
fi

cat > /etc/systemd/system/wos-tunnel.service <<EOF
# WOS EU1 – keeps the encrypted link to the bot server open (made by deploy/connect-website.sh)
[Unit]
Description=WOS EU1 – secure link to the bot server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -NT ${SSH_OPTS[*]} -L 127.0.0.1:$LOCAL_PORT:127.0.0.1:$BOT_PORT $TUNNEL_USER@$BOT_HOST
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q --now wos-tunnel
sleep 3
[[ "$(fetch "http://127.0.0.1:$LOCAL_PORT/public/config")" == *enabled* ]] && ok "Link is running and starts by itself after a reboot" \
  || warn "The link service started but the bot didn't answer yet – check later with: systemctl status wos-tunnel"

# ----------------------------------------------------------------- 3 files
step "Putting the pages on this server ($WWW)"
mkdir -p $WWW/bugs $WWW/staff
CFG_OLD="$(cat $WWW/bugs/config.js 2>/dev/null || true)"
cp -r "$SRC/site/." $WWW/bugs/
if [[ -n "$CFG_OLD" ]]; then
  printf '%s\n' "$CFG_OLD" > $WWW/bugs/config.js; ok "Kept your existing config.js"
else
  INVITE="$(ask "Your Discord invite link, e.g. https://discord.gg/abc123 (Enter = skip)" "")"
  STATUS="$(ask "Link to your 'Server status' page (Enter = skip)" "")"
  esc() { printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g' -e "s/'/%27/g"; }
  sed -i -e "s|DISCORD_URL: ''|DISCORD_URL: '$(esc "$INVITE")'|" -e "s|STATUS_URL: ''|STATUS_URL: '$(esc "$STATUS")'|" $WWW/bugs/config.js
  ok "config.js filled in"
fi
[[ -d $SRC/staff ]] && cp -r "$SRC/staff/." $WWW/staff/      # staff app icons + manifest
sed 's|apiBase: "",|apiBase: "same-origin",|' "$SRC/dashboard.html" > $WWW/staff/index.html
grep -q 'apiBase: "same-origin"' $WWW/staff/index.html || warn "Couldn't set apiBase in the dashboard – set it by hand (website manual, troubleshooting)."
chmod -R a+rX $WWW
ok "Public pages → $WWW/bugs   Dashboard → $WWW/staff"

STAFF_AUTH=0     # the staff page is protected by Discord login (moderators and admins only) – no extra password

# ----------------------------------------------------------------- 4 web server
step "Telling the web server about the bot"
choose_file() {   # choose_file <glob…> → echoes the real path of the site's config file
  local -a files=(); local f i=1 n
  for f in "$@"; do [[ -f "$f" ]] && grep -qE "$PATTERN" "$f" && files+=("$f"); done
  ((${#files[@]})) || return 1
  if ((${#files[@]} == 1)); then readlink -f "${files[0]}"; return 0; fi
  info "Your server has several sites. Which one is the WOS website?" >&2
  for f in "${files[@]}"; do printf '     %s%2d%s  %s  %s\n' "$B" "$i" "$N" "$f" "$(grep -m1 -oE '(server_name|ServerName)[^;]*' "$f" | head -1)" >&2; i=$((i+1)); done
  while true; do read -rp "  Number: " n </dev/tty; [[ "$n" =~ ^[0-9]+$ ]] && ((n>=1 && n<=${#files[@]})) && break; done
  readlink -f "${files[$((n-1))]}"
}
manual_help() {
  warn "Please add the bot settings by hand: copy the lines from $1"
  info "into your site's settings, as described in the website manual, step 5 (\"By hand\")."
}

SITEFILE=""; HTTPS=0; HOSTNAME_=""
if [[ "$WEB" == nginx ]]; then
  SNIP=/etc/nginx/snippets/wos-bot.conf; mkdir -p /etc/nginx/snippets
  {
    echo "# WOS EU1 – made by deploy/connect-website.sh. Included in your site's server { } block."
    echo "location /public/ { proxy_pass http://127.0.0.1:$LOCAL_PORT; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header CF-Connecting-IP \"\"; client_max_body_size 26m; proxy_read_timeout 60s; }"
    echo "location /api/    { proxy_pass http://127.0.0.1:$LOCAL_PORT; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header CF-Connecting-IP \"\"; client_max_body_size 5m; }"
    echo "location = /bugs  { return 301 /bugs/; }"
    echo "location ^~ /bugs/ { alias $WWW/bugs/; index index.html; }"
    echo "location = /staff { return 301 /staff/; }"
    if ((STAFF_AUTH)); then
      echo "location ^~ /staff/ { alias $WWW/staff/; index index.html; auth_basic \"Staff\"; auth_basic_user_file /etc/wos-staff.htpasswd; }"
    else
      echo "location ^~ /staff/ { alias $WWW/staff/; index index.html; add_header Cache-Control \"no-cache\"; }"
    fi
  } > $SNIP
  PATTERN='server[[:space:]]*\{'
  if SITEFILE="$(choose_file /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf)"; then
    ok "Site settings: $SITEFILE"
    if grep -q "wos-bot.conf" "$SITEFILE"; then
      ok "Already connected earlier – only refreshing"
    else
      BK="/root/wos-backup-$(basename "$SITEFILE")-$(date +%Y%m%d-%H%M%S)"; cp -p "$SITEFILE" "$BK"
      sed -i -E '/^[[:space:]]*server[[:space:]]*\{/a\    include /etc/nginx/snippets/wos-bot.conf;' "$SITEFILE"
      info "(A copy of the original is saved as $BK)"
    fi
    if out="$(nginx -t 2>&1)"; then
      systemctl reload nginx; ok "nginx reloaded"
    else
      printf '%s\n' "$out" | tail -4 | sed 's/^/    /'
      [[ -n "${BK:-}" ]] && cp -p "$BK" "$SITEFILE" && systemctl reload nginx
      warn "nginx didn't accept it (often: your site already uses /api/, /public/, /bugs/ or /staff/). I put everything back as it was."
      manual_help "$SNIP"; SITEFILE=""
    fi
  else
    manual_help "$SNIP"
  fi
  [[ -n "$SITEFILE" ]] && grep -qE '^[[:space:]]*(listen[^;]*443|ssl_certificate[[:space:]])' "$SITEFILE" && HTTPS=1
  [[ -n "$SITEFILE" ]] && HOSTNAME_="$(grep -m1 -E '^[[:space:]]*server_name' "$SITEFILE" | awk '{print $2}' | tr -d ';' || true)"
elif [[ "$WEB" == apache ]]; then
  SNIP=/etc/apache2/conf-available/wos-bot.conf
  {
    echo "# WOS EU1 – made by deploy/connect-website.sh. Included in your site's <VirtualHost>."
    echo "ProxyPass /bugs/ !"
    echo "ProxyPass /staff/ !"
    echo "ProxyPass        /public/ http://127.0.0.1:$LOCAL_PORT/public/"
    echo "ProxyPassReverse /public/ http://127.0.0.1:$LOCAL_PORT/public/"
    echo "ProxyPass        /api/    http://127.0.0.1:$LOCAL_PORT/api/"
    echo "ProxyPassReverse /api/    http://127.0.0.1:$LOCAL_PORT/api/"
    echo "RequestHeader set X-Real-IP \"expr=%{REMOTE_ADDR}\""
    echo "RequestHeader unset CF-Connecting-IP"
    echo "<Location /public/>"; echo "  LimitRequestBody 27262976"; echo "</Location>"
    echo "RedirectMatch 301 ^/(bugs|staff)\$ /\$1/"
    echo "Alias /bugs/ $WWW/bugs/"
    echo "Alias /staff/ $WWW/staff/"
    echo "<Directory $WWW>"; echo "  Require all granted"; echo "  DirectoryIndex index.html"; echo "</Directory>"
    if ((STAFF_AUTH)); then
      echo "<Location /staff/>"; echo "  AuthType Basic"; echo "  AuthName \"Staff\""
      echo "  AuthUserFile /etc/wos-staff.htpasswd"; echo "  Require valid-user"; echo "</Location>"
    fi
  } > $SNIP
  a2enmod -q proxy proxy_http headers alias >/dev/null
  PATTERN='<VirtualHost'
  if SITEFILE="$(choose_file /etc/apache2/sites-enabled/*.conf)"; then
    ok "Site settings: $SITEFILE"
    if ! grep -q "wos-bot.conf" "$SITEFILE"; then
      BK="/root/wos-backup-$(basename "$SITEFILE")-$(date +%Y%m%d-%H%M%S)"; cp -p "$SITEFILE" "$BK"
      sed -i -E '/^[[:space:]]*<VirtualHost/a\    Include /etc/apache2/conf-available/wos-bot.conf' "$SITEFILE"
      info "(A copy of the original is saved as $BK)"
    fi
    if out="$(apache2ctl configtest 2>&1)"; then
      systemctl reload apache2; ok "Apache reloaded"
    else
      printf '%s\n' "$out" | tail -4 | sed 's/^/    /'
      [[ -n "${BK:-}" ]] && cp -p "$BK" "$SITEFILE" && systemctl reload apache2
      warn "Apache didn't accept it. I put everything back as it was."
      manual_help "$SNIP"; SITEFILE=""
    fi
  else
    manual_help "$SNIP"
  fi
  [[ -n "$SITEFILE" ]] && grep -qiE '^[[:space:]]*(<VirtualHost[^>]*:443|SSLEngine[[:space:]]+on)' "$SITEFILE" && HTTPS=1
  [[ -n "$SITEFILE" ]] && HOSTNAME_="$(grep -m1 -E '^[[:space:]]*ServerName' "$SITEFILE" | awk '{print $2}' || true)"
else
  info "No nginx/Apache: the bot is reachable on this server at http://127.0.0.1:$LOCAL_PORT"
  info "Forward /public/ and /api/ to that address and serve $WWW/bugs and $WWW/staff (website manual, step 5)."
fi

# ----------------------------------------------------------------- 5 check
step "Checking from the outside"
[[ "$HOSTNAME_" == "_" || "$HOSTNAME_" == "localhost" ]] && HOSTNAME_=""
# Cloud servers only know their private address; ask the cloud for the public one.
PUB="$(curl -fsS --max-time 3 -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null \
   || curl -fsS --max-time 3 http://169.254.169.254/opc/v1/vnics/ 2>/dev/null | grep -oE '"publicIp" *: *"[^"]+"' | head -1 | grep -oE '[0-9.]{7,}' || true)"
ADDR="${HOSTNAME_:-${PUB:-$(hostname -I 2>/dev/null | awk '{print $1}')}}"
SCHEME=http; ((HTTPS)) && SCHEME=https
if [[ -n "$SITEFILE" ]]; then
  sleep 2   # give the web server a moment to finish reloading
  if [[ "$(fetch "$SCHEME://127.0.0.1/public/config" "$HOSTNAME_")" == *enabled* || "$(fetch "http://127.0.0.1/public/config")" == *enabled* ]]; then ok "/public/config answers – the website can talk to the bot"
  else warn "I couldn't check it from here – open the first address below in your browser to check."; fi
fi
cat <<EOF

  ${G}${B}Finished.${N} Open these in your browser:

    Bot check     ${C}$SCHEME://$ADDR/public/config${N}    (shows text starting with {"enabled")
    Public pages  ${C}$SCHEME://$ADDR/bugs/${N}
    Report form   ${C}$SCHEME://$ADDR/bugs/report.html${N}
    Dashboard     ${C}$SCHEME://$ADDR/staff/${N}    (log in with your dashboard key)

EOF
if [[ -n "$PUB" ]]; then
  info "On Google Cloud / Oracle: the web ports must be open – Google: VM → Edit → tick ${B}Allow HTTP${N} and ${B}Allow HTTPS traffic${N}."
fi
((HTTPS)) || warn "Your site uses http (no padlock). The dashboard key is then sent unencrypted – get a free certificate soon (website manual, \"HTTPS\")."
info "If the bot server ever gets a new address, run this script again and type the new one."
echo
