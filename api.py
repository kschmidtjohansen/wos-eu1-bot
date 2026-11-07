"""Small JSON API (aiohttp) that lets the dashboard page manage the bot.

Runs inside the bot process. Every /api route needs `Authorization: Bearer <DASHBOARD_API_KEY>`.
All Discord IDs are sent as strings (they are too large for JavaScript numbers).
"""
import csv
import hmac
import os
import hmac
import io
import re
import time
from pathlib import Path
from collections import defaultdict

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
    "bounty_channel_id": ("Monthly leaderboard channel", _BASE),
}
OPTIONAL_CHANNELS = ("welcome_channel_id", "patchnotes_channel_id", "backup_channel_id", "digest_channel_id",
                     "bounty_channel_id")
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
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
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

    @web.middleware
    async def auth_mw(request, handler):
        if request.method == "OPTIONS" or not request.path.startswith("/api/"):
            return await handler(request)
        ip = request.headers.get("CF-Connecting-IP") or request.remote or "?"
        now = time.time()
        failures[ip] = [t for t in failures[ip] if now - t < 300]
        if len(failures[ip]) >= 10:
            raise ApiError(429, "Too many wrong keys. Wait a few minutes.")
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(token.encode(), api_key.encode()):
            failures[ip].append(now)
            raise ApiError(401, "Wrong or missing API key.")
        return await handler(request)

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
                       "digest_channel_id": bool(cid), "bounty_channel_id": bool(cid)}.get(key, True)
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
            store.log_event("settings", "Dashboard: " + "; ".join(changes), None, "Dashboard")
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
            msg = await hooks["publish_panel"](title, text, cid, None, "Dashboard")
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
             "duplicates": [d["id"] for d in store.duplicates_of(r["id"])]}
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
                r = await hooks["set_severity"](r, sev, "Dashboard", None)
            except discord.HTTPException as exc:
                raise ApiError(502, f"Discord refused: {exc.text or exc}")
        status, dup = data.get("status"), data.get("duplicate_of")
        if dup not in (None, ""):
            if not str(dup).lstrip("#").isdigit():
                raise ApiError(400, "The original bug must be a number.")
            dup = int(str(dup).lstrip("#"))
        else:
            dup = None
        if status or dup:
            try:
                r = await hooks["set_bug_status"](r, status or "rejected", "Dashboard", None,
                                                  duplicate_of=dup, log_kind="admin")
            except discord.HTTPException as exc:
                raise ApiError(502, f"Discord refused: {exc.text or exc}")
        return web.json_response(bug_dict(r, full=True))

    @routes.post("/api/bugs/{id}/reply")
    async def reply_bug(request):
        r = bug_or_404(int_param(request, "id"))
        data = await body_json(request)
        text = str(data.get("text") or "").strip()
        if not 1 <= len(text) <= 1500:
            raise ApiError(400, "The message must be 1–1500 characters.")
        ok = await hooks["send_reply"](r["id"], text, "Dashboard", None)
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
        store.block_user(int(uid), reason, days, "Dashboard")
        store.log_event("block", f"{person(uid) or 'User ' + uid} blocked from the dashboard"
                        + (f" for {days} day(s)" if days else "") + (f": {reason}" if reason else ""),
                        None, "Dashboard")
        return web.json_response({"ok": True})

    @routes.delete("/api/blocklist/{user_id}")
    async def remove_block(request):
        uid = request.match_info["user_id"]
        if not uid.isdigit():
            raise ApiError(400, "Invalid user ID.")
        if store.unblock_user(int(uid)):
            store.log_event("block", f"{person(uid) or 'User ' + uid} unblocked from the dashboard", None, "Dashboard")
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
            bugs, msg = await hooks["publish_patchnotes"](str(data.get("title") or ""), "Dashboard", None)
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

    # ------------------------------------------------ points and weekly digest
    @routes.get("/api/leaderboard")
    async def leaderboard(request):
        period = request.query.get("period", "all")
        if period not in ("all", "month"):
            raise ApiError(400, "period must be all or month.")
        g = get_guild()
        out = []
        for r in store.leaderboard(period, 10):
            m = g.get_member(r["user_id"])
            out.append({**r, "user_id": str(r["user_id"]), "name": person(r["user_id"]) or (m.display_name if m else None)
                        or f"User …{str(r['user_id'])[-4:]}"})
        return web.json_response({"period": period, "items": out})

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
        store.log_event("block", f"Website pause {key[:12]}… lifted from the dashboard", None, "Dashboard")
        return web.json_response({"ok": True})

    # ------------------------------------------------------------ data & backups
    @routes.get("/api/data")
    async def data_info(request):
        size = (Path('bugs.db').stat().st_size if Path('bugs.db').exists() else 0)
        return web.json_response({"db_bytes": size, "storage": store.storage_info(), "backups": store.list_backups()})

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

    @routes.get("/public/known-issues")
    async def public_known(request):
        if not store.cfg("known_issues_enabled"):
            raise ApiError(404, "The known-issues list is switched off.")
        return web.json_response({"items": store.known_issues(), "version": store.cfg("game_version") or ""},
                                 headers={"Cache-Control": "max-age=60"})

    app = web.Application(middlewares=[cors_mw, error_mw, auth_mw], client_max_size=MAX_TOTAL + 1024 * 1024)
    app.add_routes(routes)
    return app


async def start_api(client, guild_id, api_key, origins, host, port, hooks, roles):
    app = build_app(client, guild_id, api_key, origins, hooks, roles)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
