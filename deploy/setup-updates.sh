#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – one-time setup of updates from GitHub. Run on the bot server:
#
#      sudo bash bugbot/deploy/setup-updates.sh
#
#  Afterwards you never upload zips again: write "Update bugbot" to Claude, or
#  type  Update bugbot  in the SSH window to install the released version at once.
# =============================================================================
set -euo pipefail
REPO="${1:-kschmidtjohansen/wos-eu1-bot}"
URL="${WOS_REPO_URL:-git@github.com:$REPO.git}"
SRC=/opt/wos-src
STATE=/etc/wos-update

if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B='' G='' Y='' R='' C='' N=''; fi
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$1"; }
info() { printf '  %s\n' "$1"; }
die()  { printf '\n  %s✘ %s%s\n\n' "$R" "$1" "$N"; exit 1; }
if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi

printf '\n%sWOS EU1 – automatic updates from GitHub%s\n\n' "$B" "$N"
[[ -f /opt/wosbot/.env ]] || die "Install the bot first:  sudo bash bugbot/deploy/install.sh"
command -v git >/dev/null || { info "Installing git…"; apt-get -o DPkg::Lock::Timeout=600 -qq install -y git >/dev/null; }
ok "git is installed"

install -d -m 700 $STATE
[[ -f $STATE/id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C wos-bot-updates -f $STATE/id_ed25519
export GIT_SSH_COMMAND="ssh -i $STATE/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE/known_hosts"

if git ls-remote "$URL" >/dev/null 2>&1; then
  ok "This server can already read the repository"
else
  printf '  %sGive this server permission to READ the code on GitHub:%s\n\n' "$B" "$N"
  info "1. Open:  ${C}https://github.com/$REPO/settings/keys/new${N}"
  info "2. Title: ${B}wos-bot${N}"
  info "3. Key:   paste this whole line:"
  printf '\n%s%s%s\n\n' "$C" "$(cat $STATE/id_ed25519.pub)" "$N"
  info "4. Leave ${B}Allow write access${N} UNticked → click ${B}Add key${N}"
  info "   (Or paste the line to Claude in the chat and Claude adds it for you.)"
  echo
  while true; do
    read -rp "  Press Enter when the key is added… " _ </dev/tty
    if git ls-remote "$URL" >/dev/null 2>&1; then ok "GitHub accepted the key"; break; fi
    printf '  %s!%s Not yet – check that the whole line was pasted and the key was saved, then press Enter again.\n' "$Y" "$N"
  done
fi

if [[ -d $SRC/.git ]]; then
  git -C $SRC remote set-url origin "$URL"; ok "Code folder already exists ($SRC)"
else
  rm -rf $SRC; git clone -q "$URL" $SRC 2>/dev/null || { mkdir -p $SRC && git -C $SRC init -q && git -C $SRC remote add origin "$URL"; }
  ok "Code folder ready ($SRC)"
fi
git config --global --add safe.directory $SRC 2>/dev/null || true

# The "Update bugbot" command (both spellings work)
cat > /usr/local/bin/Update <<'EOF'
#!/usr/bin/env bash
case "${1,,}" in
  bugbot) exec sudo bash /opt/wos-src/deploy/update.sh --now ;;
  "")     echo "Type:  Update bugbot" ;;
  *)      echo "Unknown: '$1'. Type:  Update bugbot" ;;
esac
EOF
chmod 755 /usr/local/bin/Update; ln -sf /usr/local/bin/Update /usr/local/bin/update
ok "Command ready: ${B}Update bugbot${N}"

cat > /etc/systemd/system/wos-update.service <<'EOF'
[Unit]
Description=WOS EU1 – install the released version from GitHub
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/wos-src/deploy/update.sh
EOF
cat > /etc/systemd/system/wos-update.timer <<'EOF'
[Unit]
Description=WOS EU1 – check for a released version every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable -q --now wos-update.timer
ok "Checks for released versions every 5 minutes"

# Nothing released yet counts as "current" so the first release installs cleanly.
echo
info "Installing the released version now (if there is one)…"
bash $SRC/deploy/update.sh --now 2>/dev/null || bash /opt/wosbot/deploy/update.sh --now || true

cat <<EOF

  ${G}${B}Done – updates are set up.${N}

  From now on:
   • Claude saves changes to GitHub, but nothing changes on the server yet.
   • When you write ${B}Update bugbot${N} to Claude, the version is released and
     this server installs it within 5 minutes.
   • To install it at once, type in this window:   ${B}Update bugbot${N}
   • Update history:   cat /var/log/wos-update.log

EOF
