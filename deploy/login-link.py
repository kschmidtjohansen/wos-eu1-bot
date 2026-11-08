#!/usr/bin/env python3
"""One-time admin login link for the staff page (sudo wosbot-login).

For when Discord login isn't set up yet, or nobody with a staff role can log in.
The link works once, for 15 minutes, and the login lasts 12 hours.
"""
import hashlib
import json
import secrets
import sqlite3
import sys
import time
import urllib.request
from urllib.parse import urlsplit

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/wosbot/bugs.db"
db = sqlite3.connect(DB, timeout=10)
get = lambda k: (db.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or [""])[0] or ""

base = ""
for key in ("oauth_redirect", "oauth_return"):
    u = urlsplit(get(key))
    if u.scheme and u.netloc:
        base = f"{u.scheme}://{u.netloc}"
        break
if not base:                                   # Google Cloud: ask for this VM's public address
    try:
        req = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip",
                                     headers={"Metadata-Flavor": "Google"})
        base = "http://" + urllib.request.urlopen(req, timeout=3).read().decode().strip()
    except Exception:  # noqa: BLE001
        base = "http://<your-server-address>"

code = secrets.token_urlsafe(24)
db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
           ("console_login", json.dumps({"hash": hashlib.sha256(code.encode()).hexdigest(), "exp": int(time.time()) + 900})))
db.commit()
print()
print("  One-time login link for the staff page (works once, for 15 minutes):")
print()
print(f"  {base}/api/auth/console?code={code}")
print()
print("  Open it in your browser. You're logged in as \"Server console\" with full access for 12 hours.")
print("  Then set up Discord login under Settings → Bot profile, so everyone logs in with Discord.")
print()
