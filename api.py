"""Small JSON API (aiohttp) that lets the dashboard page manage the bot.

Runs inside the bot process. Every /api route needs `Authorization: Bearer <DASHBOARD_API_KEY>`.
All Discord IDs are sent as strings (they are too large for JavaScript numbers).
"""
import csv
import hmac
import os
import io
import collections
import json
import re
import time
import asyncio
import shutil
import platform
from pathlib import Path
from collections import defaultdict

STARTED = time.time()
SESSION_COOKIE = "wos_session"
DISCORD_API = "https://discord.com/api/v10"   # when the bot process started (for the uptime on the Server status page)

import discord
from aiohttp import web

import store

PERM_LABELS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "add_reactions": "Add Reactions",
    "read_message_history": "Read Message History",
    "manage_messages": "Manage Messages",
    "create_private_threads": "Create Private Threads",
    "send_messages_in_threads": "Send Messages in Threads",
    "manage_threads": "Manage Threads",
}
_BASE = ["view_channel", "send_messages", "embed_links"]
CHANNEL_NEEDS = {
    "report_channel_id": ("Bug report channel", _BASE + [
        "add_reactions", "read_message_history", "manage_messages",
        "create_private_threads", "send_messages_in_threads", "manage_threads"]),
    "mod_channel_id": ("Mod channel", _BASE + [
        "attach_files", "add_reactions", "read_message_history", "manage_messages",
        "create_private_threads", "send_messages_in_threads", "manage_threads"]),
    "suggestions_channel_id": ("Suggestions channel", list(_BASE)),
    "admin_channel_id": ("Admin channel", _BASE + ["attach_files", "add_reactions", "read_message_history",
                                                   "manage_messages"]),
    "welcome_channel_id": ("Welcome channel", list(_BASE)),
    "patchnotes_channel_id": ("Patch notes channel", list(_BASE)),
    "backup_channel_id": ("Backup channel", _BASE + ["attach_files"]),
    "digest_channel_id": ("Weekly digest channel", _BASE),
    "giftcodes_channel_id": ("Gift codes channel", _BASE + ["read_message_history"]),
    "board_channel_id": ("Known issues board channel", _BASE + ["read_message_history"]),
    "log_channel_id": ("Bot log channel", list(_BASE)),
    "invites_channel_id": ("Invite log channel", list(_BASE)),
}
OPTIONAL_CHANNELS = ("welcome_channel_id", "patchnotes_channel_id", "backup_channel_id", "digest_channel_id",
                     "giftcodes_channel_id", "board_channel_id", "log_channel_id", "invites_channel_id")
# accepted suggestions are only posted here; moderators decide in the mod channel


MEDIA_VIDEO = (".mp4", ".mov", ".webm")
MEDIA_ALLOWED = (".png", ".jpg", ".jpeg", ".gif", ".webp") + MEDIA_VIDEO
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
MAX_FILES, MAX_FILE, MAX_TOTAL = 4, 8 * 2**20, 24 * 2**20


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def build_app(client: discord.Client, guild_id: int, api_key: str, origins: list,
              hooks: dict, roles: dict) -> web.Application:
    allow_all = not origins or "*" in origins
    failures = defaultdict(list)

    def get_guild() -> discord.Guild:
        g = client.get_guild(guild_id)
        if g is None:
            raise ApiError(503, "The bot is not connected to the server yet. Try again in a moment.")
        return g

    # ---------------------------------------------------------- middleware
    @web.middleware
    async def cors_mw(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as exc:
                resp = exc
        origin = request.headers.get("Origin")
        if origin and (allow_all or origin in origins):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Max-Age"] = "600"
            resp.headers["Vary"] = "Origin"
        return resp

    @web.middleware
    async def error_mw(request, handler):
        try:
            return await handler(request)
        except ApiError as exc:
            return web.json_response({"error": exc.message}, status=exc.status)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 – never leak internals to the browser
            print(f"[dashboard api] {request.method} {request.path} failed: {exc!r}")
            return web.json_response({"error": "Something went wrong in the bot. Check its console."}, status=500)

    AUTH_OPEN = {"/api/auth/info", "/api/auth/login", "/api/auth/callback", "/api/auth/me", "/api/auth/logout"}
    MOD_WRITE = [("POST", r"/api/cases/warn"), ("POST", r"/api/cases/\d+/pardon"), (None, r"/api/giveaways(/\d+/(end|reroll))?"),
                 ("PUT", r"/api/bugs/\d+"), ("POST", r"/api/bugs/\d+/reply"), ("POST", r"/api/(bugs|suggestions)/\d+/translate"), ("PUT", r"/api/suggestions/\d+"),
                 (None, r"/api/replies(/\d+)?"), (None, r"/api/blocklist(/\d+)?"), ("DELETE", r"/api/webblocks")]
    MOD_HIDDEN = [r"/api/backups/.*", r"/api/auth/settings", r"/api/translate", r"/api/drive/.*", r"/api/redeem.*",
                  r"/api/modstats", r"/api/stickers.*", r"/api/emojis.*", r"/api/push.*", r"/api/weekly"]

    async def staff_role(user_id: int):
        """'admin', 'mod' or None – checked live against the member's Discord roles."""
        g = client.get_guild(guild_id)
        if g is None:
            return None
        m = g.get_member(user_id)
        if m is None:
            try:
                m = await g.fetch_member(user_id)
            except discord.HTTPException:
                return None
        ids = {r.id for r in getattr(m, "roles", [])}
        if m.guild_permissions.administrator or roles.get("Admin role") in ids:
            return "admin"
        if roles.get("Moderator role") in ids:
            return "mod"
        return None

    def mod_may(method: str, path: str) -> bool:
        if method == "GET":
            return not any(re.fullmatch(p, path) for p in MOD_HIDDEN)
        return any((m is None or m == method) and re.fullmatch(p, path) for m, p in MOD_WRITE)

    @web.middleware
    async def auth_mw(request, handler):
        if request.method == "OPTIONS" or not request.path.startswith("/api/") or request.path in AUTH_OPEN:
            return await handler(request)
        ip = request.headers.get("CF-Connecting-IP") or request.remote or "?"
        now = time.time()
        failures[ip] = [t for t in failures[ip] if now - t < 300]
        if len(failures[ip]) >= 10:
            raise ApiError(429, "Too many wrong keys. Wait a few minutes.")
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if token:
            if not hmac.compare_digest(token.encode(), api_key.encode()):
                failures[ip].append(now)
                raise ApiError(401, "Wrong or missing API key.")
            request["actor_name"], request["actor_id"], request["role"] = "Dashboard", None, "admin"
            return await handler(request)
        sess = store.get_session(request.cookies.get(SESSION_COOKIE, ""))
        if sess is None:
            raise ApiError(401, "Please log in.")
        role = await staff_role(sess["user_id"])
        if role is None:
            store.end_sessions(sess["user_id"])
            raise ApiError(401, "You no longer have the Moderator or Admin role.")
        if request.method != "GET" and request.headers.get("X-Requested-With") != "wos":
            raise ApiError(403, "Missing request header.")          # blocks cross-site form posts
        if role == "mod" and not mod_may(request.method, request.path):
            raise ApiError(403, "Only admins can do this.")
        request["actor_name"], request["actor_id"], request["role"] = sess["name"], sess["user_id"], role
        return await handler(request)

    def who(request) -> str:
        return request.get("actor_name") or "Dashboard"

    def who_id(request):
        return request.get("actor_id")

    async def body_json(request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise ApiError(400, "Request body must be JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return data

    # ------------------------------------------------------------ helpers
    def channel_name(cid):
        g = client.get_guild(guild_id)
        ch = g.get_channel(cid) if (g and cid) else None
        return f"#{ch.name}" if ch else "(not set)"

    def settings_payload() -> dict:
        out = {}
        for key, spec in store.SCHEMA.items():
            v = store.cfg(key)
            out[key] = (str(v) if v else None) if spec["type"] == "channel" else v
        return {
            "settings": out,
            "panel": {"message_id": store.get_raw("panel_msg_id"),
                      "channel_id": store.get_raw("panel_channel_id")},
        }

    def health_payload() -> dict:
        g = get_guild()
        chans = []
        for key, (label, needs) in CHANNEL_NEEDS.items():
            cid = store.cfg(key)
            ch = g.get_channel(cid) if cid else None
            missing = []
            if ch is not None:
                perms = ch.permissions_for(g.me)
                missing = [PERM_LABELS[p] for p in needs if not getattr(perms, p, False)]
            optional = key in OPTIONAL_CHANNELS
            enabled = {"welcome_channel_id": bool(store.cfg("welcome_enabled")),
                       "patchnotes_channel_id": bool(cid), "backup_channel_id": bool(cid),
                       "digest_channel_id": bool(cid)}.get(key, True)
            chans.append({"key": key, "label": label, "optional": optional,
                          "enabled": enabled,
                          "configured": bool(cid), "found": ch is not None,
                          "name": ch.name if ch else None, "missing": missing,
                          "ok": ch is not None and not missing})
        role_items = []
        for label, rid in roles.items():
            r = g.get_role(rid)
            role_items.append({"label": label, "found": r is not None, "name": r.name if r else None})
        panel_ch = store.get_raw("panel_channel_id")
        pch = g.get_channel(int(panel_ch)) if panel_ch else None
        return {"channels": chans, "roles": role_items,
                "panel": {"published": bool(store.get_raw("panel_msg_id")),
                          "channel_name": pch.name if pch else None}}

    # ------------------------------------------------------------- routes
    routes = web.RouteTableDef()

    @routes.get("/api/overview")
    async def overview(request):
        g = get_guild()
        lat = client.latency
        items, _ = store.query_logs(limit=6)
        return web.json_response({
            "bot": {"id": str(client.user.id), "name": client.user.name,
                    "avatar_url": str(client.user.display_avatar.replace(size=128).url),
                    "latency_ms": int(lat * 1000) if lat == lat and lat != float("inf") else None,
                    "uptime_s": int(time.time() - getattr(client, "started_at", time.time()))},
            "guild": {"name": g.name, "members": g.member_count,
                      "icon_url": str(g.icon.url) if g.icon else None},
            "stats": store.stats(),
            "recent": items,
        })

    @routes.get("/api/channels")
    async def channels(request):
        g = get_guild()
        out = []
        for ch in sorted(g.text_channels, key=lambda c: ((c.category.position if c.category else -1), c.position)):
            out.append({"id": str(ch.id), "name": ch.name, "category": ch.category.name if ch.category else None})
        return web.json_response({"channels": out})

    @routes.get("/api/health")
    async def health(request):
        return web.json_response(health_payload())

    @routes.get("/api/settings")
    async def get_settings(request):
        return web.json_response(settings_payload())

    @routes.put("/api/settings")
    async def put_settings(request):
        data = await body_json(request)
        g = get_guild()
        clean = {}
        for key, value in data.items():
            v = store.coerce(key, value)           # raises ValueError if invalid
            if store.SCHEMA[key]["type"] == "channel" and v is not None:
                if not isinstance(g.get_channel(v), discord.TextChannel):
                    raise ApiError(400, f"{key.replace('_', ' ')}: that text channel does not exist.")
            clean[key] = v
        changes = []
        for key, v in clean.items():
            old = store.cfg(key)
            if old == v:
                continue
            store.set_cfg(key, v)
            name = key.replace("_id", "").replace("_", " ")
            if store.SCHEMA[key]["type"] == "channel":
                changes.append(f"{name} → {channel_name(v)}")
            elif store.SCHEMA[key]["type"] == "bool":
                changes.append(f"{name} {'on' if v else 'off'}")
            else:
                changes.append(f"{name} → {str(v)[:40]}")
        if changes:
            store.log_event("settings", "Dashboard: " + "; ".join(changes), who_id(request), who(request))
        if {"bot_status", "activity_type", "activity_text"} & set(clean):
            await hooks["apply_presence"]()
        return web.json_response(settings_payload())

    @routes.post("/api/panel")
    async def publish_panel(request):
        data = await body_json(request)
        title = store.coerce("panel_title", data.get("title"))
        text = store.coerce("panel_text", data.get("text"))
        cid = store.coerce("report_channel_id", data.get("channel_id"))
        if cid is None:
            raise ApiError(400, "Choose the bug report channel first.")
        try:
            msg = await hooks["publish_panel"](title, text, cid, who_id(request), who(request))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response({"ok": True, "message_id": str(msg.id), "channel_id": str(cid),
                                  "channel_name": channel_name(cid), "jump_url": msg.jump_url})

    @routes.get("/api/bot")
    async def get_bot(request):
        return web.json_response({"profile": hooks["profile_info"]()})

    @routes.put("/api/bot")
    async def put_bot(request):
        data = await body_json(request)
        warnings = await hooks["update_profile"](data)
        return web.json_response({"profile": hooks["profile_info"](), "warnings": warnings})

    @routes.get("/api/logs")
    async def logs(request):
        q = request.query
        try:
            limit = max(1, min(int(q.get("limit", 50)), 200))
            offset = max(0, int(q.get("offset", 0)))
        except ValueError:
            raise ApiError(400, "limit and offset must be numbers.")
        kind = q.get("kind") or None
        if kind and kind not in store.LOG_KINDS:
            raise ApiError(400, "Unknown log type.")
        items, total = store.query_logs(limit, offset, kind, (q.get("q") or "").strip()[:100] or None)
        return web.json_response({"items": items, "total": total})

    # ------------------------------------------------------ bug list & details
    def person(uid, stored=None):
        g = client.get_guild(guild_id)
        m = g.get_member(int(uid)) if (g and uid) else None
        return m.display_name if m else (stored or None)

    def jump(channel_key, msg_id):
        cid = store.cfg(channel_key)
        return f"https://discord.com/channels/{guild_id}/{cid}/{msg_id}" if (cid and msg_id) else None

    def bug_dict(r, full=False):
        d = {"id": r["id"], "summary": store.bug_summary(r), "status": r["status"],
             "reporter_id": str(r["reporter_id"]), "reporter": person(r["reporter_id"], r["reporter_name"]),
             "created_at": r["created_at"], "duplicate_of": r["duplicate_of"],
             "announced": bool(r["patched_at"]), "has_custom_summary": bool(r["summary"]),
             "area": r["area"] or "", "version": r["game_version"] or "", "severity": r["severity"] or "",
             "source": r["source"] or "discord", "public": bool(r["public"]),
             "fixed_version": r["fixed_version"] or "",
             "duplicates": [d["id"] for d in store.duplicates_of(r["id"])], "votes": store.vote_count(r["id"]),
             "reason": r["reject_reason"] or "", "dup_suggest": r["dup_suggest"], "dup_score": r["dup_score"]}
        if full:
            linked = [] if not r["player_uid"] else [
                {"id": str(x["discord_id"]), "name": person(x["discord_id"]) or f"User …{str(x['discord_id'])[-4:]}"}
                for x in store.links_for_uid(r["player_uid"])]
            d.update({"text": r["text"], "extra": r["extra"], "chief_name": r["chief_name"] or "",
                      "uid": r["player_uid"] or "", "contact": r["contact"] or "", "linked": linked,
                      "files": [{"id": f["id"], "name": f["filename"], "size": f["size"],
                                 "kind": "video" if f["filename"].lower().endswith(MEDIA_VIDEO) else "image"}
                                for f in store.bug_files(r["id"])],
                      "mod_url": jump("mod_channel_id", r["msg_id"]),
                      "admin_url": jump("admin_channel_id", r["admin_msg_id"]),
                      "history": [{"ts": x["ts"], "action": x["action"],
                                   "actor": person(x["actor_id"], x["actor_name"]) or x["actor_name"] or "Unknown"}
                                  for x in store.bug_actions(r["id"])]})
        return d

    def bug_or_404(bug_id):
        r = store.get_bug(bug_id)
        if r is None:
            raise ApiError(404, "Bug not found.")
        return r

    def int_param(request, name):
        try:
            return int(request.match_info[name])
        except ValueError:
            raise ApiError(400, "Invalid number.")

    @routes.get("/api/bugs")
    async def list_bugs(request):
        q = request.query
        status = q.get("status") or None
        if status and status not in store.BUG_STATUSES:
            raise ApiError(400, "Unknown status.")
        try:
            limit = max(1, min(int(q.get("limit", 25)), 100))
            offset = max(0, int(q.get("offset", 0)))
        except ValueError:
            raise ApiError(400, "limit and offset must be numbers.")
        severity = q.get("severity") or None
        if severity and severity not in store.SEVERITIES:
            raise ApiError(400, "Unknown severity.")
        rows, total, counts = store.query_bugs(status, (q.get("q") or "").strip()[:100] or None, limit, offset,
                                               (q.get("area") or "")[:40] or None, (q.get("version") or "")[:30] or None,
                                               severity)
        return web.json_response({"items": [bug_dict(r) for r in rows], "total": total, "counts": counts})

    @routes.get("/api/bugs/{id}")
    async def get_bug(request):
        return web.json_response(bug_dict(bug_or_404(int_param(request, "id")), full=True))

    @routes.put("/api/bugs/{id}")
    async def put_bug(request):
        r = bug_or_404(int_param(request, "id"))
        data = await body_json(request)
        if "summary" in data:
            s = str(data["summary"] or "").strip()
            if len(s) > 100:
                raise ApiError(400, "The summary can be at most 100 characters.")
            store.set_bug(r["id"], summary=s or None)
            r = store.get_bug(r["id"])
        if "area" in data:
            area = str(data["area"] or "").strip()
            if area and area not in store.area_list():
                raise ApiError(400, "Pick one of the areas from the Features tab.")
            store.set_bug(r["id"], area=area or None)
        if "public" in data:
            store.set_bug(r["id"], public=1 if data["public"] else 0)
        if "fixed_version" in data:
            store.set_bug(r["id"], fixed_version=str(data["fixed_version"] or "").strip()[:30] or None)
        if "game_version" in data:
            store.set_bug(r["id"], game_version=str(data["game_version"] or "").strip()[:30] or None)
        r = store.get_bug(r["id"])
        if "severity" in data:
            sev = data["severity"] or None
            try:
                r = await hooks["set_severity"](r, sev, who(request), who_id(request))
            except discord.HTTPException as exc:
                raise ApiError(502, f"Discord refused: {exc.text or exc}")
        status, dup = data.get("status"), data.get("duplicate_of")
        if request.get("role") == "mod" and status in ("being_investigated", "fixed", "admin_rejected"):
            raise ApiError(403, "Only admins can mark bugs as investigated, fixed or rejected by admins.")
        if dup not in (None, ""):
            if not str(dup).lstrip("#").isdigit():
                raise ApiError(400, "The original bug must be a number.")
            dup = int(str(dup).lstrip("#"))
        else:
            dup = None
        if status or dup:
            try:
                reason = str(data.get("reason") or "").strip()[:900] or None
                r = await hooks["set_bug_status"](r, status or "rejected", who(request), who_id(request),
                                                  duplicate_of=dup, log_kind="admin", reason=reason)
            except discord.HTTPException as exc:
                raise ApiError(502, f"Discord refused: {exc.text or exc}")
        if "public" in data and "refresh_board" in hooks:
            await hooks["refresh_board"]()
        return web.json_response(bug_dict(r, full=True))

    @routes.delete("/api/bugs/{id}")
    async def delete_bug(request):
        r = bug_or_404(int_param(request, "id"))
        n = await hooks["delete_bugs"]([r["id"]], request.query.get("discord", "1") != "0", who(request), who_id(request))
        return web.json_response({"deleted": n})

    @routes.post("/api/bugs/delete")
    async def delete_many(request):
        data = await body_json(request)
        if data.get("all"):
            if str(data.get("confirm") or "").strip().upper() != "DELETE":
                raise ApiError(400, "Type DELETE to confirm.")
            ids = store.all_bug_ids()
        else:
            raw = data.get("ids") or []
            if not isinstance(raw, list) or not raw or len(raw) > 500:
                raise ApiError(400, "Choose 1–500 bugs.")
            try:
                ids = [int(i) for i in raw]
            except (TypeError, ValueError):
                raise ApiError(400, "Bug numbers must be numbers.")
        n = await hooks["delete_bugs"](ids, bool(data.get("discord", True)), who(request), who_id(request),
                                       reset_numbers=bool(data.get("all") and data.get("reset_numbers")))
        return web.json_response({"deleted": n})

    # ------------------------------------------------------------ Discord login for the dashboard
    pending_logins = {}       # state -> time; the state is also kept in a cookie for the same browser

    def oauth_conf():
        return {"secret": store.get_raw("oauth_secret") or "", "redirect": store.get_raw("oauth_redirect") or "",
                "return_to": store.get_raw("oauth_return") or "/staff/"}

    def client_id():
        return str(client.application_id or (client.user.id if client.user else ""))

    @routes.get("/api/auth/info")
    async def auth_info(request):
        c = oauth_conf()
        return web.json_response({"discord": bool(c["secret"] and c["redirect"])})

    @routes.get("/api/auth/login")
    async def auth_login(request):
        c = oauth_conf()
        if not (c["secret"] and c["redirect"]):
            raise ApiError(404, "Discord login is not set up.")
        import secrets
        state = secrets.token_urlsafe(24)
        now = time.time()
        for k in [k for k, t in pending_logins.items() if now - t > 600]:
            pending_logins.pop(k, None)
        pending_logins[state] = now
        from urllib.parse import urlencode
        url = "https://discord.com/oauth2/authorize?" + urlencode({"client_id": client_id(), "response_type": "code",
              "scope": "identify", "redirect_uri": c["redirect"], "state": state, "prompt": "none"})
        resp = web.HTTPFound(url)
        resp.set_cookie("wos_state", state, max_age=600, httponly=True, samesite="Lax", path="/api/auth/",
                        secure=c["redirect"].startswith("https://"))
        raise resp

    def page(msg: str, status: int = 403):
        body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Login</title>"
                "<body style='font:16px system-ui;background:#0b1220;color:#eef3fb;display:grid;place-items:center;min-height:90vh'>"
                f"<div style='max-width:420px;text-align:center'><p>{msg}</p><p><a style='color:#8cb8f2' href='{oauth_conf()['return_to']}'>Back to the dashboard</a></p></div>")
        return web.Response(text=body, content_type="text/html", status=status)

    @routes.get("/api/auth/callback")
    async def auth_callback(request):
        import aiohttp
        c = oauth_conf()
        state, code = request.query.get("state", ""), request.query.get("code", "")
        if request.query.get("error"):
            return page("Login was cancelled.", 400)
        if not state or state != request.cookies.get("wos_state") or pending_logins.pop(state, None) is None:
            return page("This login link expired. Please try again.", 400)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(f"{DISCORD_API}/oauth2/token", data={
                    "client_id": client_id(), "client_secret": c["secret"], "grant_type": "authorization_code",
                    "code": code, "redirect_uri": c["redirect"]}) as r:
                tok = await r.json(content_type=None)
            if r.status != 200 or "access_token" not in tok:
                return page("Discord didn't accept the login (check the Client Secret and the redirect address).", 400)
            async with s.get(f"{DISCORD_API}/users/@me", headers={"Authorization": "Bearer " + tok["access_token"]}) as r:
                user = await r.json(content_type=None)
        uid = int(user["id"])
        role = await staff_role(uid)
        if role is None:
            store.log_event("system", f"Dashboard login refused for {user.get('global_name') or user.get('username')} (not staff)", uid,
                            user.get("username"))
            return page("You need the Moderator or Admin role in the server to use the dashboard.")
        g = client.get_guild(guild_id)
        m = g.get_member(uid) if g else None
        name = (m.display_name if m else None) or user.get("global_name") or user.get("username") or str(uid)
        avatar = str(m.display_avatar.replace(size=64).url) if m else ""
        token = store.create_session(uid, name, avatar)
        store.log_event("settings", f"{name} logged in to the dashboard ({role})", uid, name)
        resp = web.HTTPFound(c["return_to"])
        resp.set_cookie(SESSION_COOKIE, token, max_age=store.SESSION_DAYS * 86400, httponly=True, samesite="Lax", path="/",
                        secure=c["redirect"].startswith("https://"))
        resp.del_cookie("wos_state", path="/api/auth/")
        raise resp

    @routes.get("/api/auth/me")
    async def auth_me(request):
        sess = store.get_session(request.cookies.get(SESSION_COOKIE, ""))
        if sess is None:
            raise ApiError(401, "Not logged in.")
        role = await staff_role(sess["user_id"])
        if role is None:
            store.end_sessions(sess["user_id"])
            raise ApiError(401, "You no longer have the Moderator or Admin role.")
        return web.json_response({"name": sess["name"], "id": str(sess["user_id"]), "avatar": sess["avatar"], "role": role})

    @routes.post("/api/auth/logout")
    async def auth_logout(request):
        store.end_session(request.cookies.get(SESSION_COOKIE, ""))
        resp = web.json_response({"ok": True})
        resp.del_cookie(SESSION_COOKIE, path="/")
        return resp

    @routes.get("/api/auth/settings")
    async def auth_settings(request):
        if request.get("role") != "admin":
            raise ApiError(403, "Only admins can do this.")
        c = oauth_conf()
        return web.json_response({"enabled": bool(c["secret"] and c["redirect"]), "secret_set": bool(c["secret"]),
                                  "redirect": c["redirect"], "return_to": c["return_to"], "client_id": client_id(),
                                  "sessions": [{"name": s["name"], "id": str(s["user_id"]), "last_seen": s["last_seen"]}
                                               for s in store.list_sessions()]})

    @routes.put("/api/auth/settings")
    async def put_auth_settings(request):
        if request.get("role") != "admin":
            raise ApiError(403, "Only admins can do this.")
        data = await body_json(request)
        if "secret" in data:
            sec = str(data["secret"] or "").strip()
            if sec and not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", sec):
                raise ApiError(400, "That doesn't look like a Client Secret.")
            store.put_raw("oauth_secret", sec)
        for k, raw_key in (("redirect", "oauth_redirect"), ("return_to", "oauth_return")):
            if k in data:
                v = str(data[k] or "").strip()
                if v and not re.fullmatch(r"https?://[^\s]+" if k == "redirect" else r"(https?://[^\s]+|/[^\s]*)", v):
                    raise ApiError(400, f"The {k.replace('_', ' ')} address is not valid.")
                store.put_raw(raw_key, v)
        store.log_event("settings", "Dashboard: Discord login settings changed", who_id(request), who(request))
        return await auth_settings(request)

    @routes.delete("/api/auth/sessions")
    async def end_all_sessions(request):
        if request.get("role") != "admin":
            raise ApiError(403, "Only admins can do this.")
        store.end_sessions()
        store.log_event("settings", "Dashboard: everyone was logged out", who_id(request), who(request))
        return web.json_response({"ok": True})

    # ---------------------------------------------------------- server status
    def meminfo():
        try:
            d = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":", 1)
                d[k] = int(v.split()[0]) * 1024
            return {"total": d["MemTotal"], "available": d.get("MemAvailable", d.get("MemFree", 0)),
                    "swap_total": d.get("SwapTotal", 0), "swap_free": d.get("SwapFree", 0)}
        except (OSError, KeyError, ValueError):
            return None

    async def service_state(name):
        if not shutil.which("systemctl"):
            return None
        try:
            p = await asyncio.create_subprocess_exec("systemctl", "is-active", name, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), 3)
            return out.decode().strip() or "unknown"
        except (OSError, asyncio.TimeoutError):
            return None

    async def website_check():
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                async with s.get("http://127.0.0.1/public/config") as r:
                    return r.status == 200 and "enabled" in await r.text()
        except Exception:  # noqa: BLE001 - no local web server, or the link is down
            return None

    @routes.get("/api/status")
    async def server_status(request):
        g = client.get_guild(guild_id)
        now = time.time()
        try:
            up = float(Path("/proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            up = None
        try:
            load = os.getloadavg()
        except OSError:
            load = None
        disk = shutil.disk_usage(store.DATA_DIR if store.DATA_DIR.exists() else ".")
        names = ["wosbot", "nginx", "apache2", "wos-tunnel", "wos-update.timer"]
        states = dict(zip(names, await asyncio.gather(*(service_state(n) for n in names))))
        services = [{"name": n, "label": lbl, "state": states[n]} for n, lbl in
                    (("wosbot", "Discord bot"), ("nginx", "Web server (nginx)"), ("apache2", "Web server (Apache)"),
                     ("wos-tunnel", "Website ↔ bot link"), ("wos-update.timer", "Update checker"))
                    if states[n] not in (None, "inactive") or n in ("wosbot",)]
        site_ok = await website_check() if any(states.get(n) == "active" for n in ("nginx", "apache2")) else None
        backups = store.list_backups()
        ver = hooks["version_info"]() if "version_info" in hooks else {"version": "", "changes": ""}
        try:
            upd = [x for x in Path("/var/log/wos-update.log").read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()][-6:]
        except OSError:
            upd = []
        since = int(now - 86400)
        errs = store.db.execute("SELECT ts, text FROM logs WHERE kind='system' AND ts>=? AND (text LIKE 'Could not%' OR text LIKE '%failed%') "
                                "ORDER BY id DESC LIMIT 5", (since,)).fetchall()
        lat = client.latency
        return web.json_response({
            "checked_at": int(now),
            "bot": {"online": g is not None and not client.is_closed(), "latency_ms": None if lat is None or lat != lat else round(lat * 1000),
                    "uptime_s": int(now - STARTED), "version": ver["version"], "changes": ver["changes"],
                    "members": g.member_count if g else None, "python": platform.python_version()},
            "system": {"uptime_s": int(up) if up else None, "load": [round(x, 2) for x in load] if load else None,
                       "cpus": os.cpu_count(), "memory": meminfo(),
                       "disk": {"total": disk.total, "free": disk.free}},
            "services": services, "website_ok": site_ok,
            "data": {"db_bytes": Path("bugs.db").stat().st_size if Path("bugs.db").exists() else 0,
                     "last_backup": backups[0]["ts"] if backups else None, "backups": len(backups)},
            "updates": upd,
            "errors": [{"ts": r["ts"], "text": r["text"]} for r in errs],
        })

    # ---------------------------------------------------------- gift codes
    def code_dict(r, now=None):
        return {"id": r["id"], "code": r["code"], "reward": r["reward"] or "", "created_at": r["created_at"],
                "expires_at": r["expires_at"], "state": store.gift_code_state(r, now), "by": r["created_by"] or "",
                "url": jump("giftcodes_channel_id", r["msg_id"]) or (jump("giftcodes_channel_id", store.get_raw("gift_list_msg_id"))
                                                                    if store.gift_code_state(r, now) != "expired" else None),
                "where": "announcement" if r["msg_id"] and store.gift_code_state(r, now) == "new" else "list"}

    @routes.get("/api/giftcodes")
    async def list_codes(request):
        now = int(time.time())
        return web.json_response({"items": [code_dict(r, now) for r in store.gift_codes()],
                                  "durations": [{"hours": h, "label": store.gift_duration_label(h)} for h in store.GIFT_DURATIONS]})

    @routes.post("/api/giftcodes")
    async def post_code(request):
        data = await body_json(request)
        hours = data.get("hours")
        if hours not in (None, "", 0) and not (isinstance(hours, int) or str(hours).isdigit()):
            raise ApiError(400, "Choose how long the code works.")
        try:
            r, warn = await hooks["add_gift_code"](str(data.get("code") or ""), str(data.get("reward") or ""),
                                                   int(hours) if hours not in (None, "", 0) else None, who(request), who_id(request),
                                                   announce=bool(data.get("announce", True)))
        except ValueError as exc:
            raise ApiError(400, str(exc))
        return web.json_response({"item": code_dict(r), "warning": warn})

    def code_or_404(cid):
        r = store.get_gift_code(cid)
        if r is None:
            raise ApiError(404, "Gift code not found.")
        return r

    @routes.put("/api/giftcodes/{id}")
    async def put_code(request):
        r = code_or_404(int_param(request, "id"))
        if (await body_json(request)).get("action") != "end":
            raise ApiError(400, "Unknown action.")
        await hooks["end_gift_code"](r["id"], who(request), who_id(request))
        return web.json_response({"item": code_dict(store.get_gift_code(r["id"]))})

    @routes.delete("/api/giftcodes/{id}")
    async def delete_code(request):
        r = code_or_404(int_param(request, "id"))
        await hooks["remove_gift_code"](r["id"], request.query.get("discord", "1") != "0", who(request), who_id(request))
        return web.json_response({"ok": True})

    # ---------------------------------------------------------- translations
    @routes.get("/api/translations")
    async def get_translations(request):
        import texts
        langs = store.languages()
        lang = (request.query.get("lang") or (langs[1] if len(langs) > 1 else "en")).lower()
        if lang not in langs:
            raise ApiError(400, "That language is not switched on.")
        import translate
        over, counts, auto = store.translations(), store.language_counts(), translate.auto_texts()
        keys = [{"key": k, "group": texts.GROUPS[k], "en": texts.DEFAULTS["en"][k],
                 "builtin": texts.DEFAULTS.get(lang, {}).get(k) or (auto.get(lang) or {}).get(k),
                 "auto": k not in texts.DEFAULTS.get(lang, {}) and k in (auto.get(lang) or {}),
                 "custom": (over.get(lang) or {}).get(k),
                 "placeholders": sorted(texts.placeholders(texts.DEFAULTS["en"][k]))} for k in texts.KEYS]
        return web.json_response({
            "lang": lang, "default": store.default_language(), "keys": keys,
            "languages": [{"code": c, "name": texts.lang_name(c), "builtin": c in texts.DEFAULTS,
                           "auto": len((auto.get(c) or {})),
                           "custom": len(over.get(c) or {}), "players": counts.get(c, 0)} for c in langs],
            "known": [{"code": c, "name": n} for c, n in texts.LANGUAGE_NAMES.items()]})

    def translate_info():
        import translate
        k = translate.key()
        return {"key_set": bool(k), "key_hint": ("…" + k[-4:]) if k else "", "usage": translate.usage(),
                "feed_on": bool(store.cfg("translate_feed_on")),
                "limit": store.cfg("translate_monthly_limit"), "feed": {**translate.feed(),
                "channel_id": str(translate.feed()["channel_id"]) if translate.feed()["channel_id"] else None,
                "sources": [str(x) for x in translate.feed()["sources"]]}}

    @routes.get("/api/translate")
    async def get_translate(request):
        return web.json_response(translate_info())

    @routes.put("/api/translate")
    async def put_translate(request):
        import translate
        data = await body_json(request)
        if "key" in data:
            k = str(data["key"] or "").strip()
            if k and not re.fullmatch(r"[A-Za-z0-9_\-]{20,100}", k):
                raise ApiError(400, "That doesn't look like a Google API key (it starts with AIza…).")
            store.put_raw("translate_key", k)
            if k:
                try:
                    out, _ = await translate.text("Hi", "da")
                except translate.TranslateError as exc:
                    store.put_raw("translate_key", "")
                    raise ApiError(400, f"The key didn't work: {exc}")
            store.log_event("settings", "Google translation key " + ("saved" if k else "removed"), who_id(request), who(request))
        if "feed_on" in data:
            store.set_cfg("translate_feed_on", bool(data["feed_on"]))
        if "limit" in data:
            try:
                store.set_cfg("translate_monthly_limit", int(data["limit"]))
            except (ValueError, TypeError) as exc:
                raise ApiError(400, str(exc))
        if "feed" in data:
            f = data["feed"] or {}
            ids = [str(x) for x in (f.get("sources") or [])]
            if not all(x.isdigit() for x in ids) or (f.get("channel_id") and not str(f["channel_id"]).isdigit()):
                raise ApiError(400, "Choose channels from the list.")
            target = str(f.get("target") or "en").lower()
            if target not in texts_langs():
                raise ApiError(400, "Unknown language.")
            translate.save_feed(f.get("channel_id"), ids, target)
            store.log_event("settings", f"Translation feed updated ({len(ids)} channel(s))", who_id(request), who(request))
        return web.json_response(translate_info())

    def texts_langs():
        import texts
        return list(texts.LANGUAGE_NAMES)

    @routes.post("/api/translate/fill")
    async def fill_translate(request):
        import translate
        if not translate.key():
            raise ApiError(400, "Add a Google translation key first.")
        lang = str((await body_json(request)).get("lang") or "").lower() if request.can_read_body else ""
        if lang and lang not in store.languages():
            raise ApiError(400, "That language is not switched on.")
        n = await hooks["fill_translations"](lang or None)
        return web.json_response({"filled": n, **translate_info()})

    @routes.put("/api/translations")
    async def put_translations(request):
        import texts
        data = await body_json(request)
        lang = str(data.get("lang") or "").lower()
        if lang not in store.languages():
            raise ApiError(400, "That language is not switched on.")
        items = data.get("items")
        if not isinstance(items, dict) or len(items) > len(texts.KEYS):
            raise ApiError(400, "Nothing to save.")
        over = store.translations()
        mine = dict(over.get(lang) or {})
        for k, v in items.items():
            v = "" if v is None else str(v).strip()
            if k not in texts.GROUPS:
                raise ApiError(400, f"Unknown text '{k}'.")
            if not v or v == texts.DEFAULTS.get(lang, {}).get(k):
                mine.pop(k, None)                       # empty / same as built-in = use the built-in text
                continue
            problem = texts.check_translation(k, v)
            if problem:
                raise ApiError(400, f"{texts.GROUPS[k]} – “{texts.DEFAULTS['en'][k][:40]}…”: {problem}")
            mine[k] = v
        over[lang] = mine
        store.save_translations(over)
        store.log_event("settings", f"Dashboard: translations for {texts.lang_name(lang)} saved ({len(items)} text(s))",
                        who_id(request), who(request))
        return web.json_response({"ok": True, "custom": len(mine)})

    @routes.post("/api/giftcodes/import")
    async def import_codes(request):
        data = await body_json(request)
        lines = str(data.get("codes") or "").replace(",", "\n").splitlines()
        if not any(x.strip() for x in lines) or len(lines) > 300:
            raise ApiError(400, "Paste 1–300 codes, one per line.")
        hours = data.get("hours")
        hours = int(hours) if str(hours or "").isdigit() and int(hours) > 0 else None
        if data.get("announce") and "add_gift_codes" in hooks:
            items = [(x.split("#", 1)[0].strip(), "", hours) for x in lines if x.split("#", 1)[0].strip()]
            rows, bad, warn = await hooks["add_gift_codes"](items, who(request), who_id(request), announce=True)
            return web.json_response({"added": [r["code"] for r in rows], "skipped": [c for c, _ in bad], "warning": warn})
        added, skipped = store.import_gift_codes(lines, who(request), hours, backdate=not data.get("as_new"))
        store.log_event("admin", f"Added {len(added)} gift codes at once", who_id(request), who(request))
        if "refresh_gift_list" in hooks:
            await hooks["refresh_gift_list"]()
        if "start_redeem" in hooks:
            hooks["start_redeem"]()
        return web.json_response({"added": added, "skipped": skipped})

    # ---------------------------------------------------------- linked players
    @routes.get("/api/links")
    async def get_links(request):
        rows = store.all_links((request.query.get("q") or "")[:50])
        return web.json_response({"total": store.link_count(), "items": [
            {"discord_id": str(r["discord_id"]), "name": person(r["discord_id"]) or None, "uid": r["player_uid"],
             "chief": r["chief_name"] or "", "ts": r["ts"], "bugs": r["bugs"], "redeemed": r["redeemed"]} for r in rows]})

    @routes.delete("/api/links/{id}")
    async def delete_link(request):
        did = int_param(request, "id")
        lk = store.get_link(did)
        if lk is None or not store.delete_link(did):
            raise ApiError(404, "That link doesn't exist.")
        store.log_event("link", f"Link to UID {lk['player_uid']} removed from the dashboard", who_id(request), who(request))
        return web.json_response({"ok": True})

    # ---------------------------------------------------------- automatic gift-code redeem
    REDEEM_KEYS = ("redeem_enabled", "redeem_url", "redeem_method", "redeem_body", "redeem_ok_text",
                   "redeem_already_text", "redeem_delay", "redeem_dm")

    def redeem_payload():
        summary = store.redemption_summary()
        codes = [{"code": r["code"], **summary.get(r["code"], {"ok": 0, "already": 0, "failed": 0})}
                 for r in store.active_gift_codes()]
        return {"settings": {k: store.cfg(k) for k in REDEEM_KEYS}, "header_set": bool(store.get_raw("redeem_header")),
                "linked": store.link_count(), "codes": codes,
                "failures": [{"code": f["code"], "uid": f["uid"], "chief": f["chief_name"] or "", "message": f["message"] or "",
                              "ts": f["ts"]} for f in store.redemption_failures()]}

    @routes.get("/api/redeem")
    async def get_redeem(request):
        return web.json_response(redeem_payload())

    @routes.put("/api/redeem")
    async def put_redeem(request):
        data = await body_json(request)
        url = str(data.get("redeem_url", store.cfg("redeem_url")) or "").strip()
        if url and not re.match(r"https?://", url, re.I):
            raise ApiError(400, "The redeem address must start with https:// (or http://).")
        if data.get("redeem_enabled") and not url:
            raise ApiError(400, "Type the redeem address before turning automatic redeem on.")
        for k in REDEEM_KEYS:
            if k in data:
                store.set_cfg(k, data[k])
        if "header" in data:
            h = str(data["header"] or "").strip()
            if len(h) > 1000:
                raise ApiError(400, "The secret header is too long.")
            store.put_raw("redeem_header", h)
        store.log_event("settings", "Dashboard: automatic gift-code redeem settings saved"
                        + (" (on)" if store.cfg("redeem_enabled") else " (off)"), who_id(request), who(request))
        if hooks.get("start_redeem"):
            hooks["start_redeem"]()
        return web.json_response(redeem_payload())

    @routes.post("/api/redeem/test")
    async def test_redeem(request):
        data = await body_json(request)
        uid, code = str(data.get("uid") or "").strip(), str(data.get("code") or "").strip()
        if not uid or not code:
            raise ApiError(400, "Type a player UID and a code to test with.")
        if not (store.cfg("redeem_url") or "").strip():
            raise ApiError(400, "Save the redeem address first.")
        status, message = await hooks["redeem_one"](code, uid)
        return web.json_response({"status": status, "message": message})

    @routes.post("/api/redeem/retry")
    async def retry_redeem(request):
        n = store.db.execute("DELETE FROM redemptions WHERE status='failed'").rowcount
        store.db.commit()
        if hooks.get("start_redeem"):
            hooks["start_redeem"]()
        return web.json_response({"retrying": n})

    # ---------------------------------------------------------- moderator stats
    @routes.get("/api/modstats")
    async def modstats(request):
        try:
            days = int(request.query.get("days", 30))
        except ValueError:
            raise ApiError(400, "days must be a number.")
        s = store.mod_stats(max(0, min(days, 3650)))
        for p in s["staff"]:
            p["name"] = (person(int(p["id"]), p["name"]) if p["id"] and p["id"].isdigit() else None) or p["name"]
        return web.json_response(s)

    # ---------------------------------------------------------- sticker pack
    @routes.get("/api/invites")
    async def invites(request):
        now = int(time.time())
        name = lambda uid, stored=None: (person(uid, stored) if uid else None) or (stored or (f"User …{str(uid)[-4:]}" if uid else None))
        top = [{**r, "inviter_id": str(r["inviter_id"]), "name": name(r["inviter_id"])} for r in store.invite_top(25)]
        recent = [{"member_id": str(r["member_id"]), "member": name(r["member_id"], r["member_name"]),
                   "inviter_id": str(r["inviter_id"]) if r["inviter_id"] else None, "inviter": name(r["inviter_id"]) if r["inviter_id"] else None,
                   "code": r["code"], "ts": r["ts"], "left": r["left_ts"], "fake": bool(r["fake"])} for r in store.recent_joins(60)]
        one = lambda sql, *a: store.db.execute(sql, a).fetchone()[0] or 0
        return web.json_response({
            "enabled": bool(store.cfg("invites_enabled")), "can_read": bool(hooks.get("invite_perm", lambda: True)()),
            "joins30": one("SELECT COUNT(*) FROM invite_joins WHERE ts>=?", now - 30 * 86400),
            "known30": one("SELECT COUNT(*) FROM invite_joins WHERE ts>=? AND inviter_id IS NOT NULL", now - 30 * 86400),
            "inviters": one("SELECT COUNT(DISTINCT inviter_id) FROM invite_joins WHERE inviter_id IS NOT NULL"),
            "top": top, "recent": recent, "carried": store.invite_base_info()})

    @routes.delete("/api/invites/base")
    async def clear_invite_base(request):
        n = store.clear_invite_base()
        store.log_event("invite", f"Invite counts carried over from Invite Tracker removed ({n} player(s))", who_id(request), who(request))
        return web.json_response({"removed": n})

    @routes.get("/api/push")
    async def push_info(request):
        return web.json_response({"enabled": bool(store.cfg("push_enabled")), "ready": bool(hooks.get("push_key") and hooks["push_key"]()),
                                  **store.push_count()})

    @routes.post("/api/push/send")
    async def push_now(request):
        data = await body_json(request)
        title, body = str(data.get("title") or "").strip(), str(data.get("body") or "").strip()
        topic = data.get("topic") if data.get("topic") in store.PUSH_TOPICS else "news"
        if not 3 <= len(title) <= 80 or len(body) > 200:
            raise ApiError(400, "Write a title (3–80 characters) and at most 200 characters of text.")
        res = await hooks["push_send"](topic, title, body, "index.html")
        store.log_event("admin", f"Phone notification sent from the dashboard: “{title}”", who_id(request), who(request))
        return web.json_response(res)

    @routes.post("/api/weekly")
    async def weekly_now(request):
        try:
            ch = await hooks["post_weekly"](True)
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response({"ok": True, "channel": ch.name})

    # ---------------------------------------------------------- community tools
    @routes.get("/api/roles")
    async def roles_list(request):
        g = get_guild()
        top = g.me.top_role.position if g and g.me else 0
        return web.json_response({"roles": [{"id": str(r.id), "name": r.name, "color": f"#{r.color.value:06x}" if r.color.value else None,
                                             "assignable": (not r.managed) and r.position < top}
                                            for r in sorted(g.roles, key=lambda r: -r.position) if not r.is_default()]})

    def menu_dict(m):
        return {"id": m["id"], "channel_id": str(m["channel_id"]) if m["channel_id"] else None, "message_id": str(m["message_id"]) if m["message_id"] else None,
                "title": m["title"], "description": m["description"] or "", "style": m["style"], "mode": m["mode"],
                "roles": json.loads(m["roles"] or "[]"), "url": f"https://discord.com/channels/{guild_id}/{m['channel_id']}/{m['message_id']}" if m["message_id"] else None}

    @routes.get("/api/rolemenus")
    async def rolemenus(request):
        return web.json_response({"items": [menu_dict(m) for m in store.role_menus()]})

    @routes.post("/api/rolemenus")
    async def rolemenu_save(request):
        d = await body_json(request)
        title = str(d.get("title") or "").strip()
        if not 2 <= len(title) <= 200:
            raise ApiError(400, "Give the menu a title (2–200 characters).")
        if not str(d.get("channel_id") or "").isdigit():
            raise ApiError(400, "Choose the channel to post it in.")
        roles = []
        for r in (d.get("roles") or [])[:25]:
            rid = str(r.get("role_id") or "")
            if not rid.isdigit():
                continue
            roles.append({"role_id": rid, "label": str(r.get("label") or "").strip()[:80] or "Role",
                          "emoji": str(r.get("emoji") or "").strip()[:60], "desc": str(r.get("desc") or "").strip()[:100]})
        if not roles:
            raise ApiError(400, "Add at least one role.")
        mid = int(d["id"]) if str(d.get("id") or "").isdigit() else None
        old = store.get_role_menu(mid) if mid else None
        f = {"channel_id": int(d["channel_id"]), "title": title, "description": str(d.get("description") or "").strip()[:2000],
             "style": "select" if d.get("style") == "select" else "buttons", "mode": "single" if d.get("mode") == "single" else "multi",
             "roles": json.dumps(roles)}
        if old and old["channel_id"] != f["channel_id"]:
            await hooks["remove_role_menu"](mid)          # moved to another channel: post it again there
            mid = None
        mid = store.save_role_menu(mid, **f)
        try:
            await hooks["publish_role_menu"](mid)
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        store.log_event("panel", f"Role menu “{title}” published", who_id(request), who(request))
        return web.json_response(menu_dict(store.get_role_menu(mid)))

    @routes.delete("/api/rolemenus/{id}")
    async def rolemenu_delete(request):
        await hooks["remove_role_menu"](int_param(request, "id"))
        return web.json_response({"ok": True})

    def case_dict(r):
        return {"id": r["id"], "user_id": str(r["user_id"]), "user": person(r["user_id"], r["user_name"]) or r["user_name"], "kind": r["kind"],
                "reason": r["reason"] or "", "moderator": r["moderator_name"] or "", "ts": r["ts"], "minutes": r["minutes"],
                "active": bool(r["active"]), "auto": bool(r["auto"])}

    @routes.get("/api/cases")
    async def cases(request):
        q = (request.query.get("q") or "").strip()[:60]
        rows = store.recent_cases(80, q)
        users = {}
        for r in rows:
            users.setdefault(r["user_id"], store.active_warnings(r["user_id"]))
        return web.json_response({"items": [dict(case_dict(r), warnings=users[r["user_id"]]) for r in rows],
                                  "rules": {k: store.cfg(k) for k in ("warn_timeout_at", "warn_timeout_minutes", "warn_ban_at", "warn_expire_days")}})

    @routes.post("/api/cases/{id}/pardon")
    async def case_pardon(request):
        r = store.pardon_case(int_param(request, "id"))
        if r is None:
            raise ApiError(400, "That isn't an active warning.")
        store.add_case(r["user_id"], r["user_name"], "pardon", f"Warning #{r['id']} removed", who_id(request), who(request))
        store.log_event("mod", f"Warning #{r['id']} for {r['user_name']} removed from the dashboard", who_id(request), who(request))
        return web.json_response({"ok": True})

    @routes.post("/api/cases/warn")
    async def case_warn(request):
        d = await body_json(request)
        g = get_guild()
        m = g.get_member(int(d["user_id"])) if str(d.get("user_id") or "").isdigit() else None
        reason = str(d.get("reason") or "").strip()
        if m is None:
            raise ApiError(400, "That member isn't on the server.")
        if len(reason) < 3:
            raise ApiError(400, "Write why (the member sees it).")
        return web.json_response(await hooks["warn_member"](m, reason[:400], who_id(request), who(request)))

    def give_dict(r):
        return {"id": r["id"], "prize": r["prize"], "winners": r["winners"], "ends_at": r["ends_at"], "ended": bool(r["ended"]),
                "channel_id": str(r["channel_id"]), "role_id": str(r["role_id"]) if r["role_id"] else None, "host": r["host_name"] or "",
                "entries": r["entries"], "winner_names": [person(i) or f"User …{str(i)[-4:]}" for i in json.loads(r["winner_ids"] or "[]")],
                "url": f"https://discord.com/channels/{guild_id}/{r['channel_id']}/{r['message_id']}" if r["message_id"] else None}

    @routes.get("/api/giveaways")
    async def giveaways(request):
        return web.json_response({"items": [give_dict(r) for r in store.giveaways()]})

    @routes.post("/api/giveaways")
    async def giveaway_new(request):
        d = await body_json(request)
        try:
            gid = await hooks["start_giveaway"](int(d.get("channel_id") or 0), str(d.get("prize") or ""), int(d.get("minutes") or 0),
                                                int(d.get("winners") or 1), int(d["role_id"]) if str(d.get("role_id") or "").isdigit() else None,
                                                who_id(request), who(request))
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response(give_dict(store.get_giveaway(gid)))

    @routes.post("/api/giveaways/{id}/{action}")
    async def giveaway_action(request):
        gid, action = int_param(request, "id"), request.match_info["action"]
        if action not in ("end", "reroll"):
            raise ApiError(404, "Unknown action.")
        try:
            await hooks["end_giveaway"](gid, reroll=action == "reroll")
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response(give_dict(store.get_giveaway(gid)))

    def timer_dict(t):
        return {"id": t["id"], "name": t["name"], "channel_id": str(t["channel_id"]), "content": t["content"], "kind": t["kind"], "at": t["at"] or "",
                "days": (t["days"] or "").split(",") if t["days"] else [], "every_minutes": t["every_minutes"], "role_id": str(t["role_id"]) if t["role_id"] else None,
                "delete_previous": bool(t["delete_previous"]), "enabled": bool(t["enabled"]), "next_run": t["next_run"], "last_run": t["last_run"]}

    @routes.get("/api/timers")
    async def timers(request):
        return web.json_response({"items": [timer_dict(t) for t in store.timers()], "timezone": store.cfg("timezone")})

    @routes.post("/api/timers")
    async def timer_save(request):
        d = await body_json(request)
        name, content = str(d.get("name") or "").strip()[:60], str(d.get("content") or "").strip()
        if not name or not 2 <= len(content) <= 1900:
            raise ApiError(400, "Give it a name and a message (up to 1,900 characters).")
        if not str(d.get("channel_id") or "").isdigit():
            raise ApiError(400, "Choose the channel.")
        kind = "interval" if d.get("kind") == "interval" else "daily"
        f = {"name": name, "content": content, "channel_id": int(d["channel_id"]), "kind": kind,
             "role_id": int(d["role_id"]) if str(d.get("role_id") or "").isdigit() else None,
             "delete_previous": 1 if d.get("delete_previous") else 0, "enabled": 0 if d.get("enabled") is False else 1}
        if kind == "daily":
            at = str(d.get("at") or "")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
                raise ApiError(400, "Write the time like 18:30.")
            days = [x for x in (d.get("days") or []) if x in store.WEEKDAYS]
            f.update(at=at, days=",".join(days or store.WEEKDAYS), every_minutes=None)
        else:
            every = int(d.get("every_minutes") or 0)
            if not 15 <= every <= 10080:
                raise ApiError(400, "Repeat every 15 minutes to 7 days.")
            f.update(every_minutes=every, at=None, days=None)
        tid = store.save_timer(int(d["id"]) if str(d.get("id") or "").isdigit() else None, **f)
        store.log_event("settings", f"Timed message “{name}” saved", who_id(request), who(request))
        return web.json_response(timer_dict(store.get_timer(tid)))

    @routes.delete("/api/timers/{id}")
    async def timer_delete(request):
        store.db.execute("DELETE FROM timers WHERE id=?", (int_param(request, "id"),)); store.db.commit()
        return web.json_response({"ok": True})

    @routes.post("/api/timers/{id}/send")
    async def timer_send(request):
        t = store.get_timer(int_param(request, "id"))
        if t is None:
            raise ApiError(404, "Not found.")
        nxt = t["next_run"]
        ok = await hooks["send_timer"](t)
        store.db.execute("UPDATE timers SET next_run=? WHERE id=?", (nxt, t["id"])); store.db.commit()   # a test doesn't move the schedule
        if not ok:
            raise ApiError(502, "Discord didn't accept the message – check the channel and the bot's permissions.")
        return web.json_response({"ok": True})

    @routes.get("/api/reasons")
    async def reasons(request):
        return web.json_response({"items": [{"key": k, "text": v} for k, v in store.REJECT_PRESETS.items() if v]})

    @routes.get("/api/emojis")
    async def emojis(request):
        return web.json_response(hooks["emoji_status"]())

    @routes.get("/api/emojis/{id}.gif")
    async def emoji_gif(request):
        eid = request.match_info["id"]
        path = Path(__file__).with_name("assets") / "emojis" / f"{eid}.gif"
        if not re.fullmatch(r"[a-z0-9_]{2,40}", eid) or not path.is_file():
            raise ApiError(404, "Not found.")
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    @routes.post("/api/emojis/install")
    async def emojis_install(request):
        try:
            return web.json_response(await hooks["install_emojis"](None, who_id(request), who(request)))
        except ValueError as exc:
            raise ApiError(400, str(exc))

    @routes.get("/api/stickers")
    async def stickers(request):
        return web.json_response(hooks["sticker_status"]())

    @routes.get("/api/stickers/{id}.png")
    async def sticker_png(request):
        sid = request.match_info["id"]
        if not re.fullmatch(r"[a-z0-9-]{2,40}", sid):
            raise ApiError(404, "Not found.")
        path = Path(__file__).with_name("assets") / "stickers" / f"{sid}.png"
        if not path.is_file():
            raise ApiError(404, "Not found.")
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    @routes.post("/api/stickers/install")
    async def stickers_install(request):
        data = await body_json(request)
        ids = data.get("ids") if isinstance(data.get("ids"), list) else None
        try:
            return web.json_response(await hooks["install_stickers"](ids, who_id(request), who(request)))
        except ValueError as exc:
            raise ApiError(400, str(exc))

    @routes.post("/api/info")
    async def post_info(request):
        data = await body_json(request)
        if "rules" in data:
            store.set_cfg("info_rules", data["rules"])
        cid = str(data.get("channel_id") or "")
        if not cid.isdigit():
            raise ApiError(400, "Choose the info channel.")
        try:
            ids = await hooks["publish_info"](int(cid), who_id(request), who(request))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response({"ok": True, "messages": len(ids)})

    # ---------------------------------------------------------- suggestions
    def sug_dict(r):
        return {"id": r["id"], "text": r["text"], "status": r["status"], "created_at": r["created_at"],
                "author_id": str(r["author_id"]), "author": person(r["author_id"], r["author_name"]) or "Unknown player",
                "mod_url": jump("mod_channel_id", r["msg_id"]), "public_url": jump("suggestions_channel_id", r["public_msg_id"]),
                "reason": r["reject_reason"] or "", "changes": r["changes"] or "", "changed_at": r["changed_at"], "changed_by": r["changed_by"] or "",
                "changes_url": jump("suggestions_channel_id", r["changes_msg_id"])}

    @routes.get("/api/suggestions")
    async def list_suggestions(request):
        q = request.query
        status = q.get("status") or None
        if status and status not in ("new", "accepted", "rejected"):
            raise ApiError(400, "Unknown status.")
        try:
            limit = max(1, min(int(q.get("limit", 25)), 100)); offset = max(0, int(q.get("offset", 0)))
        except ValueError:
            raise ApiError(400, "limit and offset must be numbers.")
        rows, total, counts = store.query_suggestions(status, (q.get("q") or "").strip()[:100] or None, limit, offset)
        return web.json_response({"items": [sug_dict(r) for r in rows], "total": total, "counts": counts})

    def sug_or_404(sid):
        r = store.get_suggestion(sid)
        if r is None:
            raise ApiError(404, "Suggestion not found.")
        return r

    @routes.post("/api/bugs/{id}/translate")
    async def translate_bug(request):
        return await translate_report(request, "bugs")

    @routes.post("/api/suggestions/{id}/translate")
    async def translate_suggestion(request):
        return await translate_report(request, "suggestions")

    async def translate_report(request, kind):
        """Translate one bug report or suggestion – only when a staff member presses Translate."""
        import translate
        import texts
        r = (bug_or_404 if kind == "bugs" else sug_or_404)(int_param(request, "id"))
        if not translate.key():
            raise ApiError(400, "Translation isn't set up yet – an admin adds the Google key on the Translations page.")
        data = await body_json(request)
        target = str(data.get("target") or "en").strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,4})?", target):
            raise ApiError(400, "Unknown language.")
        if len(re.sub(r"[\W\d_]+", "", r["text"] or "")) < 2:
            return web.json_response({"text": "", "source": "", "same": True})
        try:
            out, src = await translate.text(r["text"], target)
        except translate.TranslateError as exc:
            raise ApiError(400, str(exc))
        return web.json_response({"text": out, "source": src, "source_name": texts.lang_name(src.split("-")[0]) if src else "",
                                  "same": translate.same_language(src, target)})

    @routes.put("/api/suggestions/{id}")
    async def put_suggestion(request):
        r = sug_or_404(int_param(request, "id"))
        data = await body_json(request)
        if "changes" in data:
            try:
                r = await hooks["suggestion_changes"](r["id"], str(data.get("changes") or ""), who_id(request), who(request))
            except discord.HTTPException as exc:
                raise ApiError(502, f"Discord refused: {exc.text or exc}")
            return web.json_response(sug_dict(r))
        status = data.get("status")
        if status not in ("accepted", "rejected"):
            raise ApiError(400, "Status must be accepted or rejected.")
        if r["status"] != "new":
            raise ApiError(409, "This suggestion has already been decided.")
        r = await hooks["decide_suggestion"](r, status, who_id(request), who(request),
                                             reason=str(data.get("reason") or "").strip()[:900] or None)
        return web.json_response(sug_dict(r))

    @routes.delete("/api/suggestions/{id}")
    async def delete_suggestion(request):
        r = sug_or_404(int_param(request, "id"))
        n = await hooks["delete_suggestions"]([r["id"]], request.query.get("discord", "1") != "0", who(request), who_id(request))
        return web.json_response({"deleted": n})

    @routes.post("/api/suggestions/delete")
    async def delete_many_suggestions(request):
        data = await body_json(request)
        if data.get("all"):
            if str(data.get("confirm") or "").strip().upper() != "DELETE":
                raise ApiError(400, "Type DELETE to confirm.")
            ids = store.all_suggestion_ids()
        else:
            raw = data.get("ids") or []
            if not isinstance(raw, list) or not raw or len(raw) > 500:
                raise ApiError(400, "Choose 1–500 suggestions.")
            try:
                ids = [int(i) for i in raw]
            except (TypeError, ValueError):
                raise ApiError(400, "Suggestion numbers must be numbers.")
        n = await hooks["delete_suggestions"](ids, bool(data.get("discord", True)), who(request), who_id(request),
                                              reset_numbers=bool(data.get("all") and data.get("reset_numbers")))
        return web.json_response({"deleted": n})

    @routes.post("/api/bugs/{id}/reply")
    async def reply_bug(request):
        r = bug_or_404(int_param(request, "id"))
        data = await body_json(request)
        text = str(data.get("text") or "").strip()
        if not 1 <= len(text) <= 1500:
            raise ApiError(400, "The message must be 1–1500 characters.")
        ok = await hooks["send_reply"](r["id"], text, who(request), who_id(request))
        return web.json_response({"ok": ok, "error": None if ok else "I couldn't DM them – their DMs are closed."})

    # ------------------------------------------------------------- insights
    @routes.get("/api/insights")
    async def insights(request):
        d = store.insights()
        for rep in d["reporters"]:
            if rep["id"].startswith("uid:"):             # website reports have no Discord account
                rep["name"] = (rep["name"] or "Website player") + (f" (UID {rep['id'][4:]})" if rep["id"] != "uid:?" else "")
                rep["web"] = True
                continue
            rep["name"] = person(rep["id"], rep["name"]) or f"User …{rep['id'][-4:]}"
        for st in d["staff"]:
            st["name"] = (person(st["id"], st["name"]) if st["id"] else st["name"]) or st["name"]
        d["members"] = store.member_growth(90)
        return web.json_response(d)

    # --------------------------------------------------------------- export
    def safe_cell(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s   # no spreadsheet formulas

    @routes.get("/api/export/{what}")
    async def export(request):
        what = request.match_info["what"]
        out = io.StringIO()
        w = csv.writer(out)
        iso = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else ""
        if what == "bugs.csv":
            w.writerow(["id", "created (UTC)", "reporter_id", "reporter", "status", "summary", "text",
                        "duplicate_of", "announced_in_patch_notes", "area", "game_version", "severity", "fixed_in",
                        "chief_name", "player_uid", "source"])
            for r in store.db.execute("SELECT * FROM bug_reports ORDER BY id"):
                w.writerow([r["id"], iso(r["created_at"]), r["reporter_id"],
                            safe_cell(person(r["reporter_id"], r["reporter_name"])), r["status"],
                            safe_cell(store.bug_summary(r)), safe_cell(r["text"]), r["duplicate_of"] or "",
                            "yes" if r["patched_at"] else "", r["area"] or "", r["game_version"] or "",
                            r["severity"] or "", r["fixed_version"] or "", safe_cell(r["chief_name"]),
                            r["player_uid"] or "", r["source"] or "discord"])
        elif what == "suggestions.csv":
            w.writerow(["id", "created (UTC)", "author_id", "author", "status", "text"])
            for r in store.db.execute("SELECT * FROM suggestions ORDER BY id"):
                w.writerow([r["id"], iso(r["created_at"]), r["author_id"],
                            safe_cell(person(r["author_id"], r["author_name"])), r["status"], safe_cell(r["text"])])
        else:
            raise ApiError(404, "Unknown export.")
        return web.Response(body=("﻿" + out.getvalue()).encode("utf-8"), content_type="text/csv",
                            charset="utf-8", headers={"Content-Disposition": f'attachment; filename="{what}"'})

    # -------------------------------------------------------- canned replies
    @routes.get("/api/replies")
    async def get_replies(request):
        return web.json_response({"items": [{"id": r["id"], "title": r["title"], "text": r["text"]}
                                            for r in store.list_replies()]})

    @routes.post("/api/replies")
    async def new_reply(request):
        data = await body_json(request)
        rid = store.add_reply(data.get("title"), data.get("text"))
        return web.json_response({"id": rid})

    @routes.put("/api/replies/{id}")
    async def edit_reply(request):
        data = await body_json(request)
        try:
            store.update_reply(int_param(request, "id"), data.get("title"), data.get("text"))
        except LookupError as exc:
            raise ApiError(404, str(exc))
        return web.json_response({"ok": True})

    @routes.delete("/api/replies/{id}")
    async def remove_reply(request):
        store.delete_reply(int_param(request, "id"))
        return web.json_response({"ok": True})

    # ------------------------------------------------------------- blocklist
    @routes.get("/api/blocklist")
    async def get_blocklist(request):
        return web.json_response({"items": [
            {"user_id": str(r["user_id"]), "name": person(r["user_id"]), "reason": r["reason"],
             "until": r["until"], "by": r["by_name"], "created_at": r["created_at"]}
            for r in store.list_blocks()]})

    @routes.post("/api/blocklist")
    async def add_block(request):
        data = await body_json(request)
        uid = str(data.get("user_id") or "").strip()
        if not uid.isdigit() or not 15 <= len(uid) <= 21:
            raise ApiError(400, "Enter the player's Discord user ID (a long number).")
        days = data.get("days")
        if days not in (None, ""):
            try:
                days = int(days)
            except (TypeError, ValueError):
                raise ApiError(400, "Days must be a number.")
            if not 1 <= days <= 365:
                raise ApiError(400, "Days must be between 1 and 365.")
        else:
            days = None
        member = get_guild().get_member(int(uid))
        if member and (member.bot or any(x.id in roles.values() for x in member.roles)):
            raise ApiError(400, "You can't block staff or bots.")
        reason = str(data.get("reason") or "")[:200]
        store.block_user(int(uid), reason, days, who(request))
        store.log_event("block", f"{person(uid) or 'User ' + uid} blocked from the dashboard"
                        + (f" for {days} day(s)" if days else "") + (f": {reason}" if reason else ""),
                        who_id(request), who(request))
        return web.json_response({"ok": True})

    @routes.delete("/api/blocklist/{user_id}")
    async def remove_block(request):
        uid = request.match_info["user_id"]
        if not uid.isdigit():
            raise ApiError(400, "Invalid user ID.")
        if store.unblock_user(int(uid)):
            store.log_event("block", f"{person(uid) or 'User ' + uid} unblocked from the dashboard", who_id(request), who(request))
        return web.json_response({"ok": True})

    # ------------------------------------------------------------ patch notes
    @routes.get("/api/patchnotes")
    async def get_patchnotes(request):
        return web.json_response({
            "unreleased": [bug_dict(r) for r in store.unreleased_fixed()],
            "history": [{"id": r["id"], "ts": r["ts"], "title": r["title"], "by": r["by_name"],
                         "count": len([x for x in r["bug_ids"].split(",") if x])}
                        for r in store.patchnote_history()],
            "default_title": hooks["default_patch_title"](),
        })

    @routes.post("/api/patchnotes/publish")
    async def publish_patch(request):
        data = await body_json(request)
        try:
            bugs, msg = await hooks["publish_patchnotes"](str(data.get("title") or ""), who(request), who_id(request))
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response({"ok": True, "count": len(bugs), "jump_url": msg.jump_url})

    # ------------------------------------------------- kept screenshots / videos
    @routes.get("/api/bugs/{id}/files/{fid}")
    async def bug_file(request):
        row = store.get_file(int_param(request, "fid"))
        if row is None or row["bug_id"] != int_param(request, "id"):
            raise ApiError(404, "File not found.")
        try:
            path = store.file_path(row)
        except ValueError:
            raise ApiError(404, "File not found.")
        if not path.is_file():
            raise ApiError(404, "The file has been removed from the bot's computer.")
        ctype = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return web.FileResponse(path, headers={"X-Content-Type-Options": "nosniff", "Content-Type": ctype,
                                               "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------ weekly digest
    @routes.post("/api/digest")
    async def send_digest(request):
        try:
            ch = await hooks["post_digest"](True)
        except discord.HTTPException as exc:
            raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response({"ok": True, "channel": ch.name})

    # --------------------------------------------- automatic website pauses
    @routes.get("/api/webblocks")
    async def web_blocks(request):
        return web.json_response({"items": [{"key": x["key"], "until": x["until"], "reason": x["reason"], "ts": x["ts"]}
                                            for x in store.list_web_blocks()]})

    @routes.delete("/api/webblocks")
    async def web_unblock(request):
        key = (request.query.get("key") or "")[:60]
        if not store.remove_web_block(key):
            raise ApiError(404, "That pause no longer exists.")
        store.log_event("block", f"Website pause {key[:12]}… lifted from the dashboard", who_id(request), who(request))
        return web.json_response({"ok": True})

    # ------------------------------------------------------------ data & backups
    @routes.get("/api/data")
    async def data_info(request):
        import drive
        size = (Path('bugs.db').stat().st_size if Path('bugs.db').exists() else 0)
        return web.json_response({"db_bytes": size, "storage": store.storage_info(), "backups": store.list_backups(),
                                  "drive": drive.status()})

    @routes.post("/api/drive/upload")
    async def drive_upload(request):
        import drive
        if not drive.status()["configured"]:
            raise ApiError(400, "Google Drive is not set up yet – see the bot guide, “Backups to Google Drive”.")
        try:
            name = await hooks["drive_backup"]()
        except drive.DriveError as exc:
            raise ApiError(502, str(exc))
        return web.json_response({"ok": True, "name": name, "drive": drive.status()})

    @routes.post("/api/backup")
    async def make_backup(request):
        dest = await hooks["run_backup"](True)
        return web.json_response({"ok": True, "name": dest.name if dest else None})

    @routes.get("/api/backups/{name}")
    async def get_backup(request):
        name = request.match_info["name"]
        if not store.BACKUP_RE.match(name) or not (store.BACKUP_DIR / name).is_file():
            raise ApiError(404, "Backup not found.")
        return web.FileResponse(store.BACKUP_DIR / name, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Content-Type": "application/octet-stream"})

    # ======================================================== PUBLIC (no key) ==
    # Used by report.html / known-issues.html on the website. Everything here is untrusted input.
    hits = defaultdict(list)       # ip -> timestamps of accepted reports
    sim_hits = defaultdict(list)
    all_hits = []

    ip_header = os.environ.get("CLIENT_IP_HEADER", "X-Real-IP").strip()

    def client_ip(request):
        """The visitor's address. Behind a reverse proxy every request comes from 127.0.0.1, so we read the header
        the proxy sets (CLIENT_IP_HEADER) – but only when the request really comes from this computer."""
        if request.remote in ("127.0.0.1", "::1") and ip_header and request.headers.get(ip_header):
            return request.headers[ip_header].split(",")[0].strip()[:64]
        return request.remote or "?"

    def clean(v, n):
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()[:n]

    def sniff_ok(name, data):
        ext = name.lower().rsplit(".", 1)[-1]
        head = data[:16]
        if ext in ("png",): return head.startswith(b"\x89PNG")
        if ext in ("jpg", "jpeg"): return head.startswith(b"\xff\xd8")
        if ext == "gif": return head[:4] == b"GIF8"
        if ext == "webp": return head[:4] == b"RIFF" and data[8:12] == b"WEBP"
        if ext in ("mp4", "mov"): return data[4:8] == b"ftyp"
        if ext == "webm": return head.startswith(b"\x1a\x45\xdf\xa3")
        return False

    @routes.get("/public/config")
    async def public_config(request):
        return web.json_response({"enabled": bool(store.cfg("web_form_enabled") and store.cfg("mod_channel_id")),
                                  "known_issues": bool(store.cfg("known_issues_enabled")),
                                  "changelog": bool(store.cfg("changelog_enabled")),
                                  "giftcodes": bool(store.cfg("giftcodes_enabled")),
                                  "ideas": bool(store.cfg("changelog_enabled")),
                                  "areas": store.area_list(), "version": store.cfg("game_version") or "",
                                  "limits": {"files": MAX_FILES, "file_mb": MAX_FILE // 2**20,
                                             "title": [5, 100], "description": [20, 1800]}},
                                 headers={"Cache-Control": "max-age=60"})

    @routes.post("/public/report")
    async def public_report(request):
        if not (store.cfg("web_form_enabled") and store.cfg("mod_channel_id")):
            raise ApiError(503, "Bug reports from the website are switched off right now.")
        if request.content_length and request.content_length > MAX_TOTAL + 65536:
            raise ApiError(413, "The upload is too large.")
        fields, files, total = {}, [], 0
        ctype = request.content_type
        if ctype == "application/json":
            data = await body_json(request)
            fields = {k: v for k, v in data.items() if isinstance(v, (str, int, float))}
        elif ctype.startswith("multipart/"):
            try:
                reader = await request.multipart()
                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    if part.filename:
                        if len(files) >= MAX_FILES:
                            raise ApiError(400, f"At most {MAX_FILES} files.")
                        buf = bytearray()
                        while True:
                            chunk = await part.read_chunk(65536)
                            if not chunk:
                                break
                            buf += chunk
                            total += len(chunk)
                            if len(buf) > MAX_FILE or total > MAX_TOTAL:
                                raise ApiError(413, f"Each file can be at most {MAX_FILE // 2**20} MB.")
                        if buf:
                            files.append((part.filename, bytes(buf)))
                    else:
                        fields[part.name] = (await part.text())[:4000]
            except ApiError:
                raise
            except Exception:  # noqa: BLE001
                raise ApiError(400, "Could not read the form.")
        elif ctype == "application/x-www-form-urlencoded":
            fields = {k: str(v)[:4000] for k, v in (await request.post()).items()}
        else:
            raise ApiError(415, "Send the form as multipart/form-data or JSON.")

        # bots: a hidden field humans never fill in, and forms submitted impossibly fast
        if fields.get("website"):
            return web.json_response({"ok": True, "id": None})
        try:
            if fields.get("t") and float(fields["t"]) < 3000:      # t = milliseconds the form was open
                return web.json_response({"ok": True, "id": None})
        except ValueError:
            pass

        now = time.time()
        ip = client_ip(request)
        ip_hash = hmac.new(api_key.encode(), ip.encode(), "sha256").hexdigest()[:16]    # never store raw addresses
        uid_key = ("uid:" + re.sub(r"\D", "", fields.get("uid") or "")) if fields.get("uid") else None
        if store.web_blocked(["ip:" + ip_hash, uid_key]):
            raise ApiError(429, "Reports from this connection are paused for a few days. Please contact us on Discord.")
        hits[ip] = [t for t in hits[ip] if now - t < 3600]
        all_hits[:] = [t for t in all_hits if now - t < 3600]
        if len(hits[ip]) >= 5 or len(all_hits) >= 60:
            raise ApiError(429, "Too many reports right now. Please try again later.")

        f = {"ip_hash": ip_hash, "chief_name": clean(fields.get("chief_name"), 50), "uid": clean(fields.get("uid"), 30),
             "area": clean(fields.get("area"), 40), "version": clean(fields.get("version"), 30),
             "title": clean(fields.get("title"), 100), "description": clean(fields.get("description"), 1800),
             "contact": clean(fields.get("contact"), 100)}
        if f["uid"]:
            try:
                f["uid"] = store.clean_uid(f["uid"])
            except ValueError as exc:
                raise ApiError(400, str(exc))
        if f["area"] not in store.area_list():
            raise ApiError(400, "Please pick an area.")
        if len(f["title"]) < 5:
            raise ApiError(400, "Please write a short title (at least 5 characters).")
        if len(f["description"]) < 20:
            raise ApiError(400, "Please describe what happened (at least 20 characters).")
        for name, data in files:
            if not name.lower().endswith(MEDIA_ALLOWED) or not sniff_ok(name, data):
                raise ApiError(400, "Only PNG, JPG, GIF, WEBP, MP4, MOV or WEBM files can be attached.")
        files = [(re.sub(r"[^A-Za-z0-9._-]", "_", n)[-60:] or "file", d) for n, d in files]
        hits[ip].append(now)
        all_hits.append(now)
        bug_id = await hooks["submit_web_report"](f, files)
        return web.json_response({"ok": True, "id": bug_id})

    @routes.get("/public/similar")
    async def public_similar(request):
        now = time.time()
        ip = client_ip(request)
        sim_hits[ip] = [t for t in sim_hits[ip] if now - t < 60]
        if len(sim_hits[ip]) >= 30:
            raise ApiError(429, "Slow down a little.")
        sim_hits[ip].append(now)
        return web.json_response({"items": store.similar_bugs(clean(request.query.get("q"), 200),
                                                              area=clean(request.query.get("area"), 40) or None)})

    @routes.get("/public/changelog")
    async def public_changelog(request):
        if not store.cfg("changelog_enabled"):
            raise ApiError(404, "The changelog is switched off.")
        return web.json_response({"items": store.changelog(20)}, headers={"Cache-Control": "max-age=60"})

    push_hits = collections.defaultdict(list)

    @routes.get("/public/push/key")
    async def push_key(request):
        key = hooks["push_key"]() if "push_key" in hooks else None
        return web.json_response({"enabled": bool(key), "key": key}, headers={"Cache-Control": "max-age=300"})

    @routes.post("/public/push/subscribe")
    async def push_subscribe(request):
        ip = request.headers.get("X-Real-IP") or request.headers.get("CF-Connecting-IP") or request.remote or "?"
        now = time.time()
        push_hits[ip] = [t for t in push_hits[ip] if now - t < 600]
        if len(push_hits[ip]) >= 20:
            raise ApiError(429, "Too many tries. Please wait a few minutes.")
        push_hits[ip].append(now)
        if "push_key" not in hooks or not hooks["push_key"]():
            raise ApiError(404, "Notifications are switched off.")
        data = await body_json(request)
        topics = [t for t in (data.get("topics") or []) if isinstance(t, str)]
        try:
            store.push_subscribe(data.get("subscription") or {}, topics, str(data.get("lang") or "en"))
        except ValueError as exc:
            raise ApiError(400, str(exc))
        return web.json_response({"ok": True})

    @routes.post("/public/push/unsubscribe")
    async def push_unsubscribe(request):
        data = await body_json(request)
        return web.json_response({"ok": store.push_unsubscribe(str(data.get("endpoint") or ""))})

    @routes.get("/public/ideas")
    async def public_ideas(request):
        if not store.cfg("changelog_enabled"):
            raise ApiError(404, "The changelog is switched off.")
        return web.json_response({"items": store.ideas_added()}, headers={"Cache-Control": "max-age=60"})

    @routes.get("/public/known-issues")
    async def public_known(request):
        if not store.cfg("known_issues_enabled"):
            raise ApiError(404, "The known-issues list is switched off.")
        return web.json_response({"items": store.known_issues(), "version": store.cfg("game_version") or ""},
                                 headers={"Cache-Control": "max-age=60"})

    @routes.get("/public/giftcodes")
    async def public_codes(request):
        if not store.cfg("giftcodes_enabled"):
            raise ApiError(404, "Gift codes are switched off.")
        now = int(time.time())
        return web.json_response({"new_hours": store.cfg("giftcodes_new_hours"), "now": now,
                                  "items": [{"id": r["id"], "code": r["code"], "reward": r["reward"] or "",
                                             "added": r["created_at"], "expires": r["expires_at"],
                                             "new": store.gift_code_state(r, now) == "new"}
                                            for r in store.active_gift_codes(now)]},
                                 headers={"Cache-Control": "max-age=30"})

    app = web.Application(middlewares=[cors_mw, error_mw, auth_mw], client_max_size=MAX_TOTAL + 1024 * 1024)
    app.add_routes(routes)
    return app


async def start_api(client, guild_id, api_key, origins, host, port, hooks, roles):
    app = build_app(client, guild_id, api_key, origins, hooks, roles)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
