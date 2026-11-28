#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – copies the player website and the staff dashboard into $WWW.
#  Called by update.sh after a new version is checked out (so this file is always
#  the new version's), with SRC and WWW set.
# =============================================================================
set -uo pipefail
: "${SRC:?}" "${WWW:?}"
keep="$(cat $WWW/bugs/config.js 2>/dev/null || true)"
cp -r $SRC/site/. $WWW/bugs/
[[ -n "$keep" ]] && printf '%s\n' "$keep" > $WWW/bugs/config.js
[[ -d $SRC/staff ]] && cp -r $SRC/staff/. $WWW/staff/      # staff app icons + manifest
sed 's|apiBase: "",|apiBase: "same-origin",|' $SRC/dashboard-v2.html > $WWW/staff/index.html      # the staff dashboard
mkdir -p $WWW/staff/classic && sed -e 's|apiBase: "",|apiBase: "same-origin",|' -e 's|href="icons/|href="../icons/|g' -e '/rel="manifest"/d' -e "s|new URL('../', location.href)|new URL('../../', location.href)|" "$SRC/dashboard.html" > $WWW/staff/classic/index.html      # old dashboard, kept one release as a fallback
mkdir -p $WWW/staff/v2 && printf '%s\n' '<!DOCTYPE html><meta charset="utf-8"><title>WOS EU1 staff</title><script>location.replace("../" + location.hash)</script><a href="../">The dashboard moved to /staff/</a>' > $WWW/staff/v2/index.html
chmod -R a+rX $WWW
