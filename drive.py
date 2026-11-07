"""Copies database backups to the admin's Google Drive (one folder, only files this bot created).

Set up once with  sudo bash /opt/wos-src/deploy/setup-drive.sh  – it stores the Google login in data/drive.json.
The bot only has the "drive.file" permission: it can see and change the files it uploaded itself, nothing else.
"""
import gzip
import json
import time
from pathlib import Path

import aiohttp

import store

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,size"


def conf_path() -> Path:
    return store.DATA_DIR / "drive.json"


def load_conf():
    try:
        c = json.loads(conf_path().read_text())
        return c if all(c.get(k) for k in ("client_id", "client_secret", "refresh_token", "folder_id")) else None
    except (OSError, ValueError):
        return None


def status() -> dict:
    c = load_conf()
    try:
        last = json.loads(store.get_raw("drive_last") or "{}")
    except ValueError:
        last = {}
    return {"configured": c is not None, "account": (c or {}).get("account") or "",
            "folder_url": f"https://drive.google.com/drive/folders/{c['folder_id']}" if c else None,
            "last_ok": last.get("ok_ts"), "last_name": last.get("name"), "last_error": last.get("error"),
            "last_try": last.get("ts")}


def due() -> bool:
    if load_conf() is None or not store.cfg("drive_enabled"):
        return False
    last = status()["last_ok"] or 0
    return time.time() - last >= store.cfg("drive_every_days") * 86400 - 3600


class DriveError(Exception):
    pass


async def _token(s: aiohttp.ClientSession, c: dict) -> str:
    async with s.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                       "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}) as r:
        j = await r.json(content_type=None)
        if r.status != 200 or "access_token" not in j:
            if j.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise DriveError("The Google login has expired or was removed – run setup-drive.sh again.")
            raise DriveError(f"Google refused the login ({j.get('error') or r.status}).")
        return j["access_token"]


async def upload(path: Path) -> str:
    """Upload a gzipped copy of the backup and keep only the newest N copies. Returns the uploaded file name."""
    c = load_conf()
    if c is None:
        raise DriveError("Google Drive is not set up.")
    name = path.name + ".gz"
    data = gzip.compress(path.read_bytes())
    meta = json.dumps({"name": name, "parents": [c["folder_id"]]})
    boundary = "wosbot" + str(int(time.time() * 1000))
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/gzip\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
        h = {"Authorization": "Bearer " + await _token(s, c)}
        async with s.post(UPLOAD, data=body, headers=dict(h, **{"Content-Type": f"multipart/related; boundary={boundary}"})) as r:
            if r.status not in (200, 201):
                t = await r.text()
                if r.status == 404:
                    raise DriveError("The backup folder is gone from Google Drive – run setup-drive.sh again.")
                raise DriveError(f"Upload failed ({r.status}): {t[:200]}")
        # keep the newest N backups in the folder
        q = f"'{c['folder_id']}' in parents and trashed=false"
        async with s.get(f"{API}/files", params={"q": q, "orderBy": "createdTime desc", "pageSize": "100",
                                                  "fields": "files(id,name,createdTime)"}, headers=h) as r:
            files = (await r.json(content_type=None)).get("files", []) if r.status == 200 else []
        for f in [f for f in files if f["name"].startswith("bugs-")][store.cfg("drive_keep"):]:
            async with s.delete(f"{API}/files/{f['id']}", headers=h):
                pass
    return name


def remember(name=None, error=None):
    st = status()
    rec = {"ts": int(time.time()), "ok_ts": int(time.time()) if name else st["last_ok"],
           "name": name or st["last_name"], "error": error}
    store.put_raw("drive_last", json.dumps(rec))
