#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – bot installer.  Run it ON THE BOT SERVER (the free cloud server):
#
#      sudo bash deploy/install.sh                 install, or update to a new version
#      sudo bash deploy/install.sh add-website 'ssh-ed25519 AAAA… wos-website'
#                                                  let your website server connect (website manual)
#      sudo bash deploy/install.sh status          is everything running?
#
#  It asks you a few questions and does the rest. Running it again is safe:
#  your settings (.env), database (bugs.db) and files (data/) are always kept.
# =============================================================================
set -euo pipefail

APP=/opt/wosbot
SVC=wosbot
TUNNEL_USER=wostunnel
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOTAL=6

if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B='' G='' Y='' R='' C='' N=''; fi
STEP=0
step()  { STEP=$((STEP+1)); printf '\n%s━━ Step %s of %s: %s ━━%s\n' "$B$C" "$STEP" "$TOTAL" "$1" "$N"; }
ok()    { printf '  %s✔%s %s\n' "$G" "$N" "$1"; }
info()  { printf '  %s\n' "$1"; }
warn()  { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()   { printf '\n  %s✘ %s%s\n\n' "$R" "$1" "$N"; exit 1; }
pause() { read -rp "  ${B}Press Enter when you are ready…${N} " _ </dev/tty; }
yes_no(){ local a; read -rp "  $1 [${2:-y}] " a </dev/tty; a="${a:-${2:-y}}"; [[ "$a" =~ ^[YyJj] ]]; }

if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi

# ----------------------------------------------------------------- helpers
# Small Discord API client (python3 is always there on Ubuntu/Debian). Prints tab-separated lines.
discord() {   # discord <what> <token> [guild]
  python3 - "$@" <<'PY'
import json, os, sys, urllib.request, urllib.error
what, token = sys.argv[1], sys.argv[2]
def get(path):
    req = urllib.request.Request(os.environ.get("WOS_DISCORD_API", "https://discord.com/api/v10") + path,
        headers={"Authorization": "Bot " + token, "User-Agent": "DiscordBot (https://github.com/wos-eu1, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("ERROR\t%s" % e.code); sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print("ERROR\tnet %s" % e); sys.exit(0)
if what == "me":
    u = get("/users/@me"); a = get("/applications/@me")
    f = a.get("flags", 0)
    members = bool(f & (1 << 14 | 1 << 15)); content = bool(f & (1 << 18 | 1 << 19))
    print("OK\t%s\t%s\t%d\t%d" % (u["username"], u["id"], members, content))
elif what == "guilds":
    for g in get("/users/@me/guilds"):
        print("%s\t%s" % (g["id"], g["name"]))
elif what == "roles":
    roles = sorted(get("/guilds/%s/roles" % sys.argv[3]), key=lambda r: -r["position"])
    for r in roles:
        if r["name"] != "@everyone" and not r.get("managed"):
            print("%s\t%s" % (r["id"], r["name"]))
PY
}

pick() {   # pick "<question>" <lines "id<TAB>name">  → echoes the chosen id
  local q="$1"; shift
  local -a ids=() names=(); local i=1 n
  while IFS=$'\t' read -r a b; do [[ -n "$a" ]] && ids+=("$a") && names+=("$b"); done <<<"$1"
  ((${#ids[@]})) || return 1
  for n in "${names[@]}"; do printf '     %s%2d%s  %s\n' "$B" "$i" "$N" "$n" >&2; i=$((i+1)); done
  while true; do
    read -rp "  $q (number): " n </dev/tty
    if [[ "$n" =~ ^[0-9]+$ ]] && (( n >= 1 && n <= ${#ids[@]} )); then echo "${ids[$((n-1))]}"; return 0; fi
    printf '  Type a number from 1 to %s.\n' "${#ids[@]}" >&2
  done
}

envget() { [[ -f "$APP/.env" ]] && grep -E "^$1=" "$APP/.env" | tail -1 | cut -d= -f2- || true; }

# ----------------------------------------------------------------- add-website
add_website() {
  local key="${1:-}"
  [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+ ]] || die "That doesn't look like the key from the website server. It must start with ssh-ed25519 – copy the whole line again."
  if ! id "$TUNNEL_USER" &>/dev/null; then
    useradd --system --create-home --home-dir /var/lib/$TUNNEL_USER --shell /usr/sbin/nologin $TUNNEL_USER
  fi
  install -d -m 700 -o $TUNNEL_USER -g $TUNNEL_USER /var/lib/$TUNNEL_USER/.ssh
  # This key may ONLY open a link to the bot's port – no shell, no files, nothing else.
  local port; port="$(envget DASHBOARD_PORT)"; port="${port:-8080}"
  printf 'restrict,port-forwarding,permitopen="127.0.0.1:%s" %s\n' "$port" "$key" > /var/lib/$TUNNEL_USER/.ssh/authorized_keys
  chown $TUNNEL_USER:$TUNNEL_USER /var/lib/$TUNNEL_USER/.ssh/authorized_keys
  chmod 600 /var/lib/$TUNNEL_USER/.ssh/authorized_keys
  printf '\n  %s✔ Done.%s The website server may now connect (it can only reach the bot, nothing else).\n' "$G" "$N"
  printf '  Go back to the website server window and press Enter there.\n\n'
}

status() {
  systemctl is-active --quiet $SVC && ok "Bot is running" || warn "Bot is NOT running – see: journalctl -u $SVC -n 50"
  if [[ -f /var/lib/$TUNNEL_USER/.ssh/authorized_keys ]]; then ok "Website server is allowed to connect"; else info "No website server connected yet (website manual, step 3)"; fi
  journalctl -u $SVC -n 8 --no-pager 2>/dev/null | sed 's/^/    /' || true
}

case "${1:-install}" in
  add-website) add_website "${2:-}"; exit 0 ;;
  status)      status; exit 0 ;;
  install|update) ;;
  *) die "Unknown command '$1'. Use: install | add-website '<key>' | status" ;;
esac

[[ -f "$SRC/bot.py" ]] || die "Run this from the unzipped bugbot folder:  sudo bash bugbot/deploy/install.sh"
UPDATE=0; [[ -f "$APP/.env" ]] && UPDATE=1

printf '\n%sWOS EU1 bot installer%s\n' "$B" "$N"
((UPDATE)) && info "Found an existing installation – I'll update it and keep your settings and data." \
           || info "This takes about 5 minutes. I'll ask a few questions on the way."

# ----------------------------------------------------------------- 1 system
step "Checking this server"
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" =~ ^(ubuntu|debian)$ || "${ID_LIKE:-}" =~ (ubuntu|debian) ]] || die "This installer needs Ubuntu or Debian (found: ${PRETTY_NAME:-unknown})."
ok "${PRETTY_NAME:-Linux} ($(uname -m))"
mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
if (( mem_kb < 1600000 )) && ! swapon --show | grep -q .; then
  info "This server has little memory ($((mem_kb/1024)) MB) – adding 1 GB of swap so it never runs out."
  (fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none)
  chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "Swap added"
else
  ok "Memory: $((mem_kb/1024)) MB"
fi

# ----------------------------------------------------------------- 2 packages
step "Installing Python (the language the bot is written in)"
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -o DPkg::Lock::Timeout=600 -qq)
if ((UPDATE)) && [[ -x $APP/venv/bin/python ]] && python3 -c 'import venv, ensurepip' 2>/dev/null; then
  :   # update: Python is already there – don't touch apt (Ubuntu's own updates often hold its lock)
else
  for _ in $(seq 1 60); do   # wait (max 10 min) while Ubuntu's own updater is busy
    fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
    info "Ubuntu is installing its own updates – waiting…"; sleep 10
  done
  "${APT[@]}" update >/dev/null || warn "Couldn't refresh the package list – carrying on"
  "${APT[@]}" install -y python3 python3-venv python3-pip ca-certificates >/dev/null || die "Couldn't install Python – Ubuntu's package tool is busy or broken. Try again in a few minutes."
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is needed (found $(python3 -V)). Use Ubuntu 22.04/24.04 or Debian 12."
ok "$(python3 -V)"

# ----------------------------------------------------------------- 3 files
step "Copying the bot to $APP"
id $SVC &>/dev/null || useradd --system --create-home --home-dir $APP --shell /usr/sbin/nologin $SVC
mkdir -p $APP
tar -C "$SRC" --exclude=./.git --exclude=./.env --exclude=./bugs.db --exclude=./data --exclude=./venv --exclude='__pycache__' -cf - . | tar -C $APP -xf -
chown -R $SVC:$SVC $APP
[[ -x $APP/venv/bin/python ]] || sudo -u $SVC python3 -m venv $APP/venv
info "Installing the bot's libraries (1–3 minutes)…"
free_mb="$(df -Pm $APP | awk 'NR==2{print $4}')"
[[ -n "$free_mb" && "$free_mb" -lt 300 ]] && die "The disk is almost full (${free_mb} MB free). Free up space and run this again."
pip_log="$(sudo -u $SVC $APP/venv/bin/pip install -q --disable-pip-version-check -r $APP/requirements.txt 2>&1)" || {
  printf '%s\n' "$pip_log" | tail -8 | sed 's/^/    ! /'; die "Installing the bot's libraries failed – the lines above say why."; }
ok "Bot files and libraries installed"

# ----------------------------------------------------------------- 4 settings
step "Connecting to your Discord bot"
KEEP=0
if ((UPDATE)) && [[ "$(envget DISCORD_TOKEN)" != "" && "$(envget DISCORD_TOKEN)" != "paste-bot-token-here" ]]; then
  if [[ "${WOS_AUTO:-}" == 1 ]]; then KEEP=1      # automatic update: never ask
  else yes_no "You already have settings. Keep them? (y = keep, n = enter everything again)" y && KEEP=1; fi
fi
[[ "${WOS_AUTO:-}" == 1 && $KEEP == 0 ]] && die "Automatic update needs a finished installation (run install.sh by hand first)."
if ((KEEP)); then
  ok "Keeping your current settings"
  API_KEY="$(envget DASHBOARD_API_KEY)"
else
  info "You need the bot token from the Discord Developer Portal (bot manual, step 2)."
  info "When you paste it nothing is shown on the screen – that is normal. Paste, then press Enter."
  while true; do
    read -rsp "  Bot token: " TOKEN </dev/tty; echo
    TOKEN="$(echo "$TOKEN" | tr -d '[:space:]')"
    [[ -n "$TOKEN" ]] || continue
    res="$(discord me "$TOKEN")"
    if [[ "$res" == OK* ]]; then break; fi
    if [[ "$res" == *net* ]]; then die "This server can't reach Discord ($res). Check the server's internet connection."; fi
    warn "Discord didn't accept that token. Go to the Bot page → Reset Token → copy it again."
  done
  IFS=$'\t' read -r _ BOTNAME BOTID HAS_MEMBERS HAS_CONTENT <<<"$res"
  ok "Token works – the bot is called ${B}$BOTNAME${N}"
  while [[ "$HAS_MEMBERS" != 1 || "$HAS_CONTENT" != 1 ]]; do
    warn "Two switches are still OFF on the Developer Portal → Bot page → Privileged Gateway Intents:"
    [[ "$HAS_MEMBERS" == 1 ]] || info "   • SERVER MEMBERS INTENT"
    [[ "$HAS_CONTENT" == 1 ]] || info "   • MESSAGE CONTENT INTENT"
    info "Turn them on, press Save Changes, then come back here."
    pause
    IFS=$'\t' read -r _ BOTNAME BOTID HAS_MEMBERS HAS_CONTENT <<<"$(discord me "$TOKEN")"
  done
  ok "Both required switches (intents) are on"

  while true; do
    guilds="$(discord guilds "$TOKEN")"
    if [[ -n "$guilds" && "$guilds" != ERROR* ]]; then break; fi
    warn "The bot isn't in any server yet. Invite it with this link (open it on your phone or PC):"
    printf '\n     %shttps://discord.com/oauth2/authorize?client_id=%s&scope=bot+applications.commands&permissions=360777509952%s\n\n' "$C" "$BOTID" "$N"
    info "Choose your server → Authorize. Then come back here."
    pause
  done
  if [[ $(grep -c . <<<"$guilds") -eq 1 ]]; then
    GUILD_ID="$(cut -f1 <<<"$guilds")"; ok "Server: $(cut -f2 <<<"$guilds")"
  else
    info "The bot is in several servers. Which one is WOS EU1?"
    GUILD_ID="$(pick "Server" "$guilds")"
  fi

  roles="$(discord roles "$TOKEN" "$GUILD_ID")"
  [[ "$roles" != ERROR* && -n "$roles" ]] || die "I couldn't read the server's roles. Create a Moderator and an Admin role first (bot manual, step 4), then run this again."
  info "Which role do your ${B}moderators${N} have?"
  MOD_ROLE_ID="$(pick "Moderator role" "$roles")"
  info "Which role do your ${B}admins${N} have?"
  ADMIN_ROLE_ID="$(pick "Admin role" "$roles")"
  ok "Roles saved"

  API_KEY="$(envget DASHBOARD_API_KEY)"
  [[ ${#API_KEY} -ge 20 ]] || API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  [[ -f $APP/.env ]] && cp -p $APP/.env "$APP/.env.backup-$(date +%Y%m%d-%H%M%S)"
  umask 077
  cat > $APP/.env <<EOF
# Written by deploy/install.sh on $(date '+%Y-%m-%d %H:%M'). Keep this file secret.
DISCORD_TOKEN=$TOKEN
GUILD_ID=$GUILD_ID
MOD_ROLE_ID=$MOD_ROLE_ID
ADMIN_ROLE_ID=$ADMIN_ROLE_ID

# Dashboard login key (the website manual tells you where to use it)
DASHBOARD_API_KEY=$API_KEY
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
DASHBOARD_ORIGINS=
CLIENT_IP_HEADER=X-Real-IP
DATA_DIR=data
EOF
  umask 022
  chown $SVC:$SVC $APP/.env; chmod 600 $APP/.env
  ok "Settings saved in $APP/.env"
fi

# ----------------------------------------------------------------- 5 service
step "Starting the bot (and making it start by itself after a reboot)"
cp $APP/deploy/wosbot.service /etc/systemd/system/$SVC.service
systemctl daemon-reload
systemctl enable -q $SVC
since="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart $SVC
info "Waiting for the bot to log in…"
ready=0
for _ in $(seq 1 45); do
  sleep 2
  if journalctl -u $SVC --since "$since" --no-pager 2>/dev/null | grep -q "ready\."; then ready=1; break; fi
  systemctl is-active --quiet $SVC || break
done
if ((ready)); then
  ok "The bot is online in Discord"
else
  log="$(journalctl -u $SVC --since "$since" --no-pager -n 40 2>/dev/null || true)"
  printf '%s\n' "$log" | tail -15 | sed 's/^/    ! /'
  if grep -q "PrivilegedIntentsRequired" <<<"$log"; then die "Discord refused the intents. Turn on SERVER MEMBERS and MESSAGE CONTENT intents (bot manual, step 2) and run this again."
  elif grep -qi "LoginFailure\|Improper token" <<<"$log"; then die "The token is wrong. Run this again and choose 'n' to enter a new one."
  else die "The bot didn't start. The lines above say why – send them to whoever helps you."; fi
fi

# ----------------------------------------------------------------- 6 done
step "Done"
cat <<EOF

  ${G}${B}The bot is installed and running.${N}

  ${B}Your dashboard key${N} (you need it in the website manual – keep it secret):

      ${C}$API_KEY${N}

  You can always see it again with:   sudo grep DASHBOARD_API_KEY $APP/.env

  ${B}Next:${N} open ${B}INSTALL-WEBSITE.html${N} and follow it on your website server.

  Useful commands on this server:
    sudo systemctl status $SVC          is it running?
    sudo journalctl -u $SVC -f          live log (Ctrl+C to stop watching)
    sudo systemctl restart $SVC         restart
    sudo bash $APP/deploy/install.sh status

EOF
