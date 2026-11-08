#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – updater. Installs the RELEASED version (the "live" branch on GitHub).
#  Runs by itself every 5 minutes (wos-update.timer) and when you type:
#
#      Update bugbot
#
#  Nothing is installed until a version is released (when you write "Update bugbot"
#  to Claude). Settings, database, files, config.js and the staff password are kept.
#  If the new version doesn't start, the previous version is put back automatically.
# =============================================================================
set -uo pipefail

# This file is replaced while updating, so run from a private copy.
if [[ "${BASH_SOURCE[0]}" != /run/wos-update-run.sh ]]; then
  cp "${BASH_SOURCE[0]}" /run/wos-update-run.sh && exec bash /run/wos-update-run.sh "$@"
fi

SRC=/opt/wos-src
STATE=/etc/wos-update
BRANCH="${WOS_BRANCH:-live}"
WWW=/var/www/wos
LOG=/var/log/wos-update.log
MANUAL=0; [[ "${1:-}" == --now ]] && MANUAL=1

say() { local m; m="$(date '+%Y-%m-%d %H:%M') $*"; echo "$m" >> "$LOG"; ((MANUAL)) && printf '  %s\n' "$*"; true; }
quiet_or_show() {   # full output goes to the log; on screen only the ✔ / ✘ / ! lines
  if ((MANUAL)); then "$@" 2>&1 | tee -a "$LOG" | grep --line-buffered -E '✔|✘|!' | sed 's/^ *//; s/^/  /'; return "${PIPESTATUS[0]}"
  else "$@" >> "$LOG" 2>&1; fi
}

exec 9>/run/wos-update.lock
flock -n 9 || { ((MANUAL)) && echo "  An update is already running – try again in a minute."; exit 0; }

[[ -d $SRC/.git ]] || { echo "Updates are not set up yet: sudo bash bugbot/deploy/setup-updates.sh"; exit 1; }
export GIT_SSH_COMMAND="ssh -i $STATE/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE/known_hosts"
git config --global --add safe.directory $SRC 2>/dev/null || true

((MANUAL)) && echo "  Checking GitHub for a released version…"
if ! git -C $SRC fetch -q origin "$BRANCH" 2>>"$LOG"; then
  if git -C $SRC ls-remote --exit-code origin >/dev/null 2>&1; then
    ((MANUAL)) && echo "  No version has been released yet. Ask Claude to release one (write: Update bugbot)."
    exit 0
  fi
  say "Could not reach GitHub (is the deploy key still added?)"; exit 1
fi
NEW="$(git -C $SRC rev-parse FETCH_HEAD)"
DEPLOYED="$(cat $STATE/deployed 2>/dev/null || true)"
FAILED="$(cat $STATE/failed 2>/dev/null || true)"
TITLE="$(git -C $SRC log -1 --format=%s "$NEW")"

if [[ "$NEW" == "$DEPLOYED" ]]; then
  ((MANUAL)) && echo "  ✔ Already up to date: ${NEW:0:7} – $TITLE"
  exit 0
fi
if [[ "$NEW" == "$FAILED" && $MANUAL == 0 ]]; then exit 0; fi   # don't retry a broken version every 5 minutes

refresh_web() {
  [[ -d $WWW/bugs ]] || return 0              # website not on this server
  local keep; keep="$(cat $WWW/bugs/config.js 2>/dev/null || true)"
  cp -r $SRC/site/. $WWW/bugs/
  [[ -n "$keep" ]] && printf '%s\n' "$keep" > $WWW/bugs/config.js
  [[ -d $SRC/staff ]] && cp -r $SRC/staff/. $WWW/staff/      # staff app icons + manifest
  sed 's|apiBase: "",|apiBase: "same-origin",|' $SRC/dashboard.html > $WWW/staff/index.html
  chmod -R a+rX $WWW
}
deploy() {   # deploy <commit> [changes text]
  git -C $SRC reset -q --hard "$1" || return 1
  printf '%s (%s)\n' "${1:0:7}" "$(git -C $SRC log -1 --format=%cs "$1")" > $SRC/VERSION
  printf '%s\n' "${2:-}" > $SRC/CHANGES        # the bot posts this in the admin channel after starting
  WOS_AUTO=1 quiet_or_show bash $SRC/deploy/install.sh update || return 1
  refresh_web
}

say "Installing ${NEW:0:7} – $TITLE"
CHANGES="$(git -C $SRC log --no-merges --format='%s' ${DEPLOYED:+"$DEPLOYED..$NEW"} -n 20 2>/dev/null || true)"
[[ -n "$DEPLOYED" ]] || CHANGES="$TITLE"
if deploy "$NEW" "$CHANGES"; then
  echo "$NEW" > $STATE/deployed; rm -f $STATE/failed
  say "✔ Updated to ${NEW:0:7} – $TITLE"
  exit 0
fi

echo "$NEW" > $STATE/failed
say "✘ Version ${NEW:0:7} did not start."
if [[ -n "$DEPLOYED" ]]; then
  say "Putting back the previous version ${DEPLOYED:0:7}…"
  if deploy "$DEPLOYED" "ROLLBACK ${NEW:0:7}"; then say "✔ Previous version is running again. Tell Claude the update failed (log: $LOG)."
  else say "✘ The previous version didn't start either – check: sudo journalctl -u wosbot -n 50"; fi
fi
exit 1
