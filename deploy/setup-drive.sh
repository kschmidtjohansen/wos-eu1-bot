#!/usr/bin/env bash
# =============================================================================
#  WOS EU1 – connect weekly backups to your Google Drive (one time).
#  Run on the bot server:   sudo bash /opt/wos-src/deploy/setup-drive.sh
#  You need the Client ID and Client secret from the bot guide ("Backups to Google Drive").
# =============================================================================
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$0" "$@"; fi
APP=/opt/wosbot
[[ -f $APP/.env ]] || { echo "Install the bot first."; exit 1; }
DD="$(grep -E '^DATA_DIR=' $APP/.env | tail -1 | cut -d= -f2- || true)"; DD="${DD:-data}"
if [[ "$DD" == /* ]]; then DATA="$DD"; else DATA="$APP/$DD"; fi
mkdir -p "$DATA"

python3 - "$DATA/drive.json" <<'PY'
import json, sys, time, urllib.parse, urllib.request, urllib.error
out = sys.argv[1]
B, G, C, N = "\033[1m", "\033[32m", "\033[36m", "\033[0m"
def post(url, data, headers=None, raw=None):
    body = raw if raw is not None else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}
tty = open("/dev/tty")
def ask(q):
    print(q, end="", flush=True); return tty.readline().strip()
print(f"\n{B}WOS EU1 – backups to Google Drive{N}\n")
cid = ask("  Client ID: ")
sec = ask("  Client secret: ")
if not cid.endswith(".apps.googleusercontent.com") or not sec:
    sys.exit("\n  ✘ That doesn't look right. The Client ID ends with .apps.googleusercontent.com – copy both again.\n")
st, j = post("https://oauth2.googleapis.com/device/code", {"client_id": cid, "scope": "https://www.googleapis.com/auth/drive.file"})
if st != 200:
    sys.exit(f"\n  ✘ Google said: {j.get('error_description') or j.get('error') or st}\n"
             "    Check that the client type is “TVs and Limited Input devices” and the Drive API is enabled.\n")
print(f"\n  1. Open {C}{j['verification_url']}{N} on your phone or PC")
print(f"  2. Type this code: {B}{C}{j['user_code']}{N}")
print("  3. Choose the Google account that should keep the backups and press Continue / Allow.")
print("     (If Google says the app isn't verified: Advanced → Go to … – it's your own app.)\n")
print("  Waiting for you to finish…", flush=True)
interval, deadline = j.get("interval", 5), time.time() + j.get("expires_in", 1800)
while time.time() < deadline:
    time.sleep(interval)
    st, t = post("https://oauth2.googleapis.com/token", {"client_id": cid, "client_secret": sec, "device_code": j["device_code"],
                                                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    if st == 200 and t.get("refresh_token"):
        break
    if t.get("error") == "slow_down":
        interval += 5
    elif t.get("error") not in ("authorization_pending",):
        sys.exit(f"\n  ✘ Google said: {t.get('error_description') or t.get('error')}\n")
else:
    sys.exit("\n  ✘ Took too long – run this again.\n")
h = {"Authorization": "Bearer " + t["access_token"], "Content-Type": "application/json"}
st, f = post("https://www.googleapis.com/drive/v3/files?fields=id", None, h,
             json.dumps({"name": "WOS EU1 backups", "mimeType": "application/vnd.google-apps.folder"}).encode())
if st not in (200, 201):
    sys.exit(f"\n  ✘ Could not create the folder ({st}): {f.get('error', {}).get('message', '')}\n"
             "    Is the Google Drive API enabled in your Google Cloud project?\n")
acct = ""
try:
    req = urllib.request.Request("https://www.googleapis.com/drive/v3/about?fields=user", headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        acct = json.load(r)["user"].get("emailAddress", "")
except Exception:
    pass
json.dump({"client_id": cid, "client_secret": sec, "refresh_token": t["refresh_token"], "folder_id": f["id"], "account": acct},
          open(out, "w"))
print(f"\n  {G}✔ Connected{N}{' to ' + acct if acct else ''}. Backups go to the folder “WOS EU1 backups”:")
print(f"    https://drive.google.com/drive/folders/{f['id']}")
PY
chown wosbot:wosbot "$DATA/drive.json"; chmod 600 "$DATA/drive.json"
cat <<EOF

  Done. A copy of the backup goes to Google Drive once a week (the newest 8 are kept –
  change both on the dashboard: Features → Screenshots & backups).
  To send one right now: dashboard → Data → "Copy to Google Drive now".

EOF
