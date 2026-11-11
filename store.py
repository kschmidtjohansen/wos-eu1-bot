"""Database, settings and activity log – shared by the bot and the dashboard API."""
import json
import os
import re
import io
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
ATTACH_DIR, BACKUP_DIR = DATA_DIR / "attachments", DATA_DIR / "backups"
for _d in (ATTACH_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)
DEFAULT_AREAS = "City\nAlliance\nCombat\nHeroes\nEvents\nShop & payments\nChat\nLogin & connection\nUI & display\nOther"

DEFAULT_PANEL_TITLE = "Bugs & suggestions"
DEFAULT_WELCOME_MESSAGE = (
    "Welcome to **{server}**, {user}! 🎉\n"
    "You're member #{member_count}. Have a look around – and if you find a bug, "
    "use the 🐛 reaction in the bug report channel."
)
DEFAULT_RULES = (
    "**1. Be respectful** – no insults, hate speech, harassment or threats.\n"
    "**2. No cheating or exploits** – report bugs instead of abusing them.\n"
    "**3. No spam or advertising** – this includes DMs to other members.\n"
    "**4. Keep it in the right channel** – use the language channels for chat in your language.\n"
    "**5. No selling accounts or items** for real money.\n"
    "**6. Listen to the moderators** – if you disagree, message an admin calmly.\n\n"
    "*Breaking the rules can lead to a warning, mute or ban.*"
)
OLD_PANEL_TEXT = (
    "Found a bug or have an idea? React below:\n\n"
    "🐛 **Report a bug** – a private ticket opens where you answer a few questions.\n"
    "💡 **Create a suggestion** – a private ticket opens where you write your idea freely.\n\n"
    "*Turn on DMs from server members to get updates on your reports.*"
)
DEFAULT_PANEL_TEXT = (
    "Found a bug or have an idea? Press a button below – a short form opens that only you can see.\n\n"
    "🐛 **Report a bug** – where it is, what happened, and a screenshot if you have one.\n"
    "💡 **Suggest an idea** – tell us what you would like to see in the game.\n\n"
    "*Turn on DMs from server members to get updates on your reports.*"
)

# ---------------------------------------------------------------- database
db = sqlite3.connect("bugs.db")
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS bug_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        extra TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        msg_id INTEGER,
        ticket_thread_id INTEGER,     -- the mods' "Investigate" ticket
        created_at INTEGER,
        admin_msg_id INTEGER          -- the copy in the admin channel
    );
    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        author_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        msg_id INTEGER,
        created_at INTEGER,
        status TEXT NOT NULL DEFAULT 'new'      -- new | accepted | rejected
    );
    -- the player's bug-report ticket (private thread)
    CREATE TABLE IF NOT EXISTS tickets (
        thread_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'waiting',   -- waiting | preview | submitting | closing
        draft_msg_id INTEGER,
        preview_msg_id INTEGER,
        closed INTEGER NOT NULL DEFAULT 0,
        kind TEXT NOT NULL DEFAULT 'bug'          -- bug | suggestion
    );
    -- who did what (feeds the staff view and "time to fix")
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,              -- bug | suggestion
        item_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER,
        actor_name TEXT
    );
    CREATE TABLE IF NOT EXISTS blocked (
        user_id INTEGER PRIMARY KEY,
        reason TEXT,
        until INTEGER,                   -- NULL = until removed
        by_name TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS replies (   -- canned replies for moderators
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        text TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS patchnotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        title TEXT NOT NULL,
        bug_ids TEXT NOT NULL,
        by_name TEXT
    );
    CREATE TABLE IF NOT EXISTS links (        -- /link: Discord account <-> in-game account
        discord_id INTEGER PRIMARY KEY,
        player_uid TEXT NOT NULL,
        chief_name TEXT,
        ts INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bug_files (    -- screenshots/videos kept on disk
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bug_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        size INTEGER NOT NULL,
        ts INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS web_blocks (   -- automatic pauses for website spam ("ip:<hash>" / "uid:<uid>")
        key TEXT PRIMARY KEY,
        until INTEGER NOT NULL,
        reason TEXT,
        ts INTEGER NOT NULL
    );
    DROP TABLE IF EXISTS points;               -- bug-hunter points were removed
    CREATE TABLE IF NOT EXISTS member_log (   -- one row per local day: member count at the end of the day
        day TEXT PRIMARY KEY,
        members INTEGER NOT NULL,
        joins INTEGER NOT NULL DEFAULT 0,
        leaves INTEGER NOT NULL DEFAULT 0,
        est INTEGER NOT NULL DEFAULT 0           -- 1 = estimated from join dates (before tracking started)
    );
    CREATE TABLE IF NOT EXISTS gift_codes (    -- in-game gift codes announced in Discord and listed on the website
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        reward TEXT,
        created_at INTEGER NOT NULL,
        expires_at INTEGER,                        -- NULL = permanent
        msg_id INTEGER,                            -- the announcement in the gift-codes channel
        created_by TEXT,
        expired_noted INTEGER NOT NULL DEFAULT 0   -- the announcement was marked "expired"
    );
    CREATE TABLE IF NOT EXISTS user_lang (     -- which language the bot uses with each player
        user_id INTEGER PRIMARY KEY,
        lang TEXT NOT NULL,
        chosen INTEGER NOT NULL DEFAULT 0          -- 1 = picked by the player, 0 = taken from their Discord language
    );
    CREATE TABLE IF NOT EXISTS sessions (      -- dashboard logins with Discord (only a hash of the cookie is stored)
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT,
        avatar TEXT,
        created INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        last_seen INTEGER
    );
    CREATE TABLE IF NOT EXISTS bug_votes (     -- "I have this too" on a known issue; voters get a DM when it is fixed
        bug_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        ts INTEGER NOT NULL,
        PRIMARY KEY (bug_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS redemptions (   -- automatic gift-code redeem for linked players
        code TEXT NOT NULL,
        uid TEXT NOT NULL,
        discord_id INTEGER,
        status TEXT NOT NULL,                      -- ok | already | failed
        message TEXT,
        ts INTEGER NOT NULL,
        PRIMARY KEY (code, uid)
    );
    CREATE TABLE IF NOT EXISTS invite_base (   -- counts carried over from the old Invite Tracker bot
        user_id INTEGER PRIMARY KEY,
        joins INTEGER NOT NULL DEFAULT 0,          -- Invite Tracker's "regular" (everyone who joined)
        left_n INTEGER NOT NULL DEFAULT 0,
        fake INTEGER NOT NULL DEFAULT 0,
        bonus INTEGER NOT NULL DEFAULT 0,
        ts INTEGER,
        name TEXT
    );
    CREATE TABLE IF NOT EXISTS invite_joins (  -- who invited whom (one row per join)
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        member_name TEXT,
        inviter_id INTEGER,                        -- NULL = unknown / vanity link
        code TEXT,
        ts INTEGER NOT NULL,
        left_ts INTEGER,
        fake INTEGER NOT NULL DEFAULT 0            -- account younger than the "new account" limit
    );
    CREATE TABLE IF NOT EXISTS push_subs (     -- phones/browsers that asked for notifications on the website
        endpoint TEXT PRIMARY KEY,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        topics TEXT NOT NULL DEFAULT 'codes,news',
        lang TEXT,
        ts INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS role_menus (    -- "pick your alliance / language / pings" messages
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER, message_id INTEGER,
        title TEXT NOT NULL, description TEXT,
        style TEXT NOT NULL DEFAULT 'buttons',     -- buttons | select
        mode TEXT NOT NULL DEFAULT 'multi',        -- multi | single (only one role of the menu at a time)
        roles TEXT NOT NULL DEFAULT '[]'           -- [{"role_id", "label", "emoji", "desc"}]
    );
    CREATE TABLE IF NOT EXISTS mod_cases (     -- warnings, timeouts, kicks and bans: the history per player
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, user_name TEXT,
        kind TEXT NOT NULL,                        -- warn | timeout | kick | ban | unban | pardon
        reason TEXT, moderator_id INTEGER, moderator_name TEXT,
        ts INTEGER NOT NULL, minutes INTEGER,
        active INTEGER NOT NULL DEFAULT 1,         -- a warning that still counts
        auto INTEGER NOT NULL DEFAULT 0            -- given by the bot (auto-mod / escalation)
    );
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER NOT NULL, message_id INTEGER,
        prize TEXT NOT NULL, winners INTEGER NOT NULL DEFAULT 1,
        ends_at INTEGER NOT NULL, role_id INTEGER,
        host_id INTEGER, host_name TEXT, created INTEGER NOT NULL,
        ended INTEGER NOT NULL DEFAULT 0, winner_ids TEXT, entries INTEGER
    );
    CREATE TABLE IF NOT EXISTS timers (        -- messages the bot posts on a schedule
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, channel_id INTEGER NOT NULL, content TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'daily',        -- once | daily | interval
        at TEXT, days TEXT,                        -- once: "2026-11-14T18:30"; daily: "18:30" and "mon,tue,…" – server time zone
        every_minutes INTEGER,                     -- interval
        role_id INTEGER, delete_previous INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1, next_run INTEGER, last_run INTEGER, last_msg_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,
        actor_id INTEGER,
        actor_name TEXT,
        text TEXT NOT NULL
    );
    """
)
for _table, _col in (("bug_reports", "created_at INTEGER"), ("suggestions", "created_at INTEGER"),
                     ("bug_reports", "admin_msg_id INTEGER"),
                     ("invite_base", "extra_bonus INTEGER NOT NULL DEFAULT 0"),
                     ("suggestions", "status TEXT NOT NULL DEFAULT 'new'"),
                     ("tickets", "kind TEXT NOT NULL DEFAULT 'bug'"),
                     ("suggestions", "public_msg_id INTEGER"),
                     ("bug_reports", "duplicate_of INTEGER"),
                     ("bug_reports", "summary TEXT"),
                     ("bug_reports", "patched_at INTEGER"),
                     ("bug_reports", "reporter_name TEXT"),
                     ("suggestions", "author_name TEXT"),
                     ("bug_reports", "area TEXT"), ("bug_reports", "game_version TEXT"),
                     ("bug_reports", "chief_name TEXT"), ("bug_reports", "player_uid TEXT"),
                     ("bug_reports", "source TEXT NOT NULL DEFAULT 'discord'"), ("bug_reports", "contact TEXT"),
                     ("bug_reports", "severity TEXT"), ("bug_reports", "fixed_version TEXT"),
                     ("bug_reports", "public INTEGER NOT NULL DEFAULT 0"),
                     ("bug_reports", "ip_hash TEXT"),
                     ("bug_reports", "reject_reason TEXT"), ("suggestions", "reject_reason TEXT"),
                     ("bug_reports", "dup_suggest INTEGER"), ("bug_reports", "dup_score INTEGER"), ("bug_reports", "dup_dismissed INTEGER"), ("bug_reports", "ai_title TEXT"), ("bug_reports", "ai_summary TEXT"),
                     ("bug_reports", "ai_checked INTEGER"), ("bug_reports", "ai_dup INTEGER"), ("bug_reports", "ai_conf INTEGER"),
                     ("bug_reports", "ai_reason TEXT"), ("bug_reports", "text_en TEXT"), ("bug_reports", "auto_closed INTEGER"), ("suggestions", "text_en TEXT"), ("bug_files", "img_hash TEXT"),
                     ("bug_reports", "mod_pinged INTEGER"),
                     ("suggestions", "changes TEXT"), ("suggestions", "changed_at INTEGER"),
                     ("suggestions", "changed_by TEXT"), ("suggestions", "changes_msg_id INTEGER"),
                     ("tickets", "area TEXT"), ("tickets", "title TEXT"), ("tickets", "chief_name TEXT"),
                     ("tickets", "player_uid TEXT"), ("tickets", "game_version TEXT")):   # upgrade older databases
    try:
        db.execute(f"ALTER TABLE {_table} ADD COLUMN {_col}")
    except sqlite3.OperationalError:
        pass
db.commit()


# ---------------------------------------------------------------- reports
def get_bug(bug_id: int):
    return db.execute("SELECT * FROM bug_reports WHERE id=?", (bug_id,)).fetchone()


def get_bug_by_msg(msg_id: int):
    return db.execute("SELECT * FROM bug_reports WHERE msg_id=?", (msg_id,)).fetchone()


def set_bug(bug_id: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE bug_reports SET {cols} WHERE id=?", (*fields.values(), bug_id))
    db.commit()
    if fields.get("status") in ("rejected", "admin_rejected") and not fields.get("duplicate_of"):
        r = get_bug(bug_id)
        if r is not None and r["source"] == "web":
            note_web_reject(r)


def get_bug_by_admin_msg(msg_id: int):
    return db.execute("SELECT * FROM bug_reports WHERE admin_msg_id=?", (msg_id,)).fetchone()


def get_suggestion(sid: int):
    return db.execute("SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()


def get_suggestion_by_msg(msg_id: int):
    return db.execute("SELECT * FROM suggestions WHERE msg_id=?", (msg_id,)).fetchone()


def set_suggestion(sid: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE suggestions SET {cols} WHERE id=?", (*fields.values(), sid))
    db.commit()


def get_ticket(thread_id: int):
    return db.execute("SELECT * FROM tickets WHERE thread_id=?", (thread_id,)).fetchone()


def set_ticket(thread_id: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE tickets SET {cols} WHERE thread_id=?", (*fields.values(), thread_id))
    db.commit()


def stats() -> dict:
    """Numbers for the dashboard overview."""
    bug = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM bug_reports GROUP BY status")}
    sug = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM suggestions GROUP BY status")}
    n = lambda d, *names: sum(d.get(k, 0) for k in names)
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0]
    return {
        "bugs": {
            "total": sum(bug.values()),
            "awaiting": n(bug, "new", "investigating"),     # still with the moderators
            "with_admins": n(bug, "passed"),                # passed on, admins haven't acted yet
            "investigating": n(bug, "being_investigated"),
            "fixed": n(bug, "fixed"),
            "rejected": n(bug, "rejected", "admin_rejected"),
            "dups": one(f"SELECT COUNT(*) FROM bug_reports WHERE dup_suggest IS NOT NULL AND duplicate_of IS NULL AND status IN "
                        f"({','.join('?' * len(OPEN_STATUSES))})", *OPEN_STATUSES),
        },
        "suggestions": {
            "total": sum(sug.values()),
            "pending": n(sug, "new"),
            "accepted": n(sug, "accepted"),
            "rejected": n(sug, "rejected"),
        },
        "open_tickets": one("SELECT COUNT(*) FROM tickets WHERE closed=0"),
        "last7": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=?", int(time.time()) - 7 * 86400),
    }


# --------------------------------------------------------------- settings
def _env_int(*names) -> Optional[int]:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v.isdigit() and int(v) > 0:
            return int(v)
    return None


from texts import SERVER_LANGUAGES as _texts_langs  # noqa: E402

# Everything the dashboard may change. Channel defaults come from .env (optional).
SCHEMA = {
    "report_channel_id": {"type": "channel", "default": _env_int("REPORT_CHANNEL_ID")},
    "mod_channel_id": {"type": "channel", "default": _env_int("MOD_CHANNEL_ID", "BUG_CHANNEL_ID")},
    "suggestions_channel_id": {"type": "channel", "default": _env_int("SUGGESTIONS_CHANNEL_ID", "SUGGESTION_CHANNEL_ID")},
    "admin_channel_id": {"type": "channel", "default": _env_int("ADMIN_CHANNEL_ID")},
    "welcome_channel_id": {"type": "channel", "default": _env_int("WELCOME_CHANNEL_ID")},
    "welcome_enabled": {"type": "bool", "default": False},
    "welcome_message": {"type": "text", "default": DEFAULT_WELCOME_MESSAGE, "min": 1, "max": 1500},
    "patchnotes_channel_id": {"type": "channel", "default": _env_int("PATCHNOTES_CHANNEL_ID")},
    "reminders_enabled": {"type": "bool", "default": True},
    "reminder_mod_hours": {"type": "int", "default": 24, "min": 1, "max": 720},
    "reminder_admin_hours": {"type": "int", "default": 72, "min": 1, "max": 720},
    "backup_channel_id": {"type": "channel", "default": _env_int("BACKUP_CHANNEL_ID")},
    "bug_areas": {"type": "lines", "default": DEFAULT_AREAS, "max_lines": 25, "max_len": 40},
    "game_version": {"type": "text", "default": "", "min": 0, "max": 30},
    "ticket_details_required": {"type": "bool", "default": True},
    "web_form_enabled": {"type": "bool", "default": False},
    "known_issues_enabled": {"type": "bool", "default": False},
    "reminder_critical_hours": {"type": "int", "default": 4, "min": 1, "max": 72},
    "keep_attachments": {"type": "bool", "default": True},
    "attachment_keep_days": {"type": "int", "default": 0, "min": 0, "max": 3650},
    "backups_enabled": {"type": "bool", "default": True},
    "auto_block_web": {"type": "bool", "default": True},
    "auto_block_rejects": {"type": "int", "default": 3, "min": 2, "max": 20},
    "auto_block_days": {"type": "int", "default": 3, "min": 1, "max": 90},
    "changelog_enabled": {"type": "bool", "default": False},
    "timezone": {"type": "text", "default": "Europe/Copenhagen", "min": 3, "max": 40},
    "digest_enabled": {"type": "bool", "default": True},
    "digest_channel_id": {"type": "channel", "default": None},
    "digest_weekday": {"type": "choice", "default": "monday",
                       "choices": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
    "digest_hour": {"type": "int", "default": 9, "min": 0, "max": 23},
    "update_notice_enabled": {"type": "bool", "default": True},
    "report_style": {"type": "choice", "default": "form", "choices": ["form", "ticket"]},
    "info_remind_hours": {"type": "int", "default": 48, "min": 0, "max": 720},
    "info_close_hours": {"type": "int", "default": 72, "min": 0, "max": 720},
    "default_language": {"type": "text", "default": "en", "min": 2, "max": 10},
    "drive_enabled": {"type": "bool", "default": True},
    "drive_every_days": {"type": "int", "default": 7, "min": 1, "max": 30},
    "drive_keep": {"type": "int", "default": 8, "min": 1, "max": 60},
    "languages": {"type": "lines", "default": "\n".join(_texts_langs), "max_lines": 30, "max_len": 10},
    "translate_monthly_limit": {"type": "int", "default": 450000, "min": 0, "max": 20000000},
    "translate_feed_on": {"type": "bool", "default": False},
    "giftcodes_enabled": {"type": "bool", "default": False},
    "giftcodes_channel_id": {"type": "channel", "default": None},
    "giftcodes_ping": {"type": "choice", "default": "none", "choices": ["none", "here", "everyone", "role"]},
    "giftcodes_role_id": {"type": "text", "default": "", "min": 0, "max": 25},
    "giftcodes_new_hours": {"type": "int", "default": 24, "min": 1, "max": 168},
    "giftcodes_delete_expired": {"type": "bool", "default": True},
    "giftcodes_list": {"type": "bool", "default": True},
    "giftcodes_site_url": {"type": "text", "default": "", "min": 0, "max": 300},
    "board_channel_id": {"type": "channel", "default": None},
    "log_channel_id": {"type": "channel", "default": None},
    "push_enabled": {"type": "bool", "default": True},
    "weekly_enabled": {"type": "bool", "default": False},
    "weekly_channel_id": {"type": "channel", "default": None},
    "weekly_weekday": {"type": "choice", "default": "sunday",
                       "choices": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
    "weekly_hour": {"type": "int", "default": 18, "min": 0, "max": 23},
    "warn_timeout_at": {"type": "int", "default": 3, "min": 0, "max": 20},
    "warn_timeout_minutes": {"type": "int", "default": 60, "min": 1, "max": 40320},
    "warn_ban_at": {"type": "int", "default": 0, "min": 0, "max": 50},
    "warn_expire_days": {"type": "int", "default": 30, "min": 0, "max": 365},
    "automod_badwords_on": {"type": "bool", "default": False},
    "automod_badwords": {"type": "lines", "default": "badword", "max_lines": 300, "max_len": 40},
    "automod_caps_on": {"type": "bool", "default": True},
    "automod_mentions_on": {"type": "bool", "default": True},
    "automod_mentions_max": {"type": "int", "default": 5, "min": 2, "max": 50},
    "automod_repeat_on": {"type": "bool", "default": True},
    "automod_warn": {"type": "bool", "default": True},
    "guard_enabled": {"type": "bool", "default": True},
    "guard_timeout_minutes": {"type": "int", "default": 60, "min": 0, "max": 10080},
    "guard_block_invites": {"type": "bool", "default": False},
    "guard_allow_domains": {"type": "lines", "default": "discord.com\ndiscord.gg\ndiscordapp.com\ndiscordapp.net\ndiscord.media\ndiscord.gift\ndiscord.new\nyoutube.com\nyoutu.be\ntwitch.tv\ntenor.com\ngiphy.com\nimgur.com\nreddit.com\nx.com\ntwitter.com\ngoogle.com\ncenturygame.com\nwhiteoutsurvival.com",
                            "max_lines": 60, "max_len": 80},
    "invites_enabled": {"type": "bool", "default": True},
    "invites_channel_id": {"type": "channel", "default": None},
    "invites_fake_days": {"type": "int", "default": 7, "min": 0, "max": 90},
    "dup_mode": {"type": "choice", "default": "auto", "choices": ["off", "flag", "auto"]},
    "dup_auto_percent": {"type": "int", "default": 60, "min": 50, "max": 100},
    "mod_ping_hours": {"type": "int", "default": 4, "min": 0, "max": 72},
    "log_channel_kinds": {"type": "lines", "default": "all", "max_lines": 20, "max_len": 20},
    "redeem_enabled": {"type": "bool", "default": False},
    "redeem_url": {"type": "text", "default": "", "min": 0, "max": 500},
    "redeem_method": {"type": "choice", "default": "POST", "choices": ["POST", "GET"]},
    "redeem_body": {"type": "text", "default": '{"uid": "{uid}", "code": "{code}"}', "min": 0, "max": 1000},
    "redeem_ok_text": {"type": "text", "default": "", "min": 0, "max": 100},
    "redeem_already_text": {"type": "text", "default": "already", "min": 0, "max": 100},
    "redeem_delay": {"type": "int", "default": 2, "min": 0, "max": 60},
    "redeem_dm": {"type": "bool", "default": False},
    "info_channel_id": {"type": "channel", "default": None},
    "info_rules": {"type": "text", "default": DEFAULT_RULES, "min": 1, "max": 4000},
    "bugs_enabled": {"type": "bool", "default": True},
    "suggestions_enabled": {"type": "bool", "default": True},
    "investigate_enabled": {"type": "bool", "default": True},
    "dm_notifications": {"type": "bool", "default": True},
    "delete_ticket_on_close": {"type": "bool", "default": True},
    "report_cooldown_minutes": {"type": "int", "default": 0, "min": 0, "max": 1440},
    "suggestion_max_length": {"type": "int", "default": 1800, "min": 50, "max": 1900},
    "panel_title": {"type": "text", "default": DEFAULT_PANEL_TITLE, "min": 1, "max": 100},
    "panel_text": {"type": "text", "default": DEFAULT_PANEL_TEXT, "min": 1, "max": 2000},
    "bot_status": {"type": "choice", "default": "online", "choices": ["online", "idle", "dnd", "invisible"]},
    "activity_type": {"type": "choice", "default": "watching",
                      "choices": ["playing", "watching", "listening", "competing"]},
    "activity_text": {"type": "text", "default": "", "min": 0, "max": 100},
}


def coerce(key: str, value: Any):
    """Validate/convert a value for a setting. Raises ValueError with a readable message."""
    if key not in SCHEMA:
        raise ValueError(f"Unknown setting '{key}'.")
    spec, kind = SCHEMA[key], SCHEMA[key]["type"]
    label = key.replace("_", " ")
    if kind == "channel":
        if value in (None, "", 0, "0"):
            return None
        if not str(value).isdigit():
            raise ValueError(f"{label}: not a valid channel.")
        return int(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{label}: must be true or false.")
    if kind == "int":
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: must be a number.")
        if not spec["min"] <= v <= spec["max"]:
            raise ValueError(f"{label}: must be between {spec['min']} and {spec['max']}.")
        return v
    if kind == "text":
        s = str(value if value is not None else "").strip()
        if not spec["min"] <= len(s) <= spec["max"]:
            raise ValueError(f"{label}: must be {spec['min']}–{spec['max']} characters.")
        return s
    if kind == "lines":
        items, seen = [], set()
        for line in str(value if value is not None else "").replace(",", "\n").splitlines():
            t = line.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                items.append(t)
        if not items or len(items) > spec["max_lines"] or any(len(t) > spec["max_len"] for t in items):
            raise ValueError(f"{label}: give 1–{spec['max_lines']} entries, each at most {spec['max_len']} characters.")
        return "\n".join(items)
    if kind == "choice":
        if value not in spec["choices"]:
            raise ValueError(f"{label}: must be one of {', '.join(spec['choices'])}.")
        return value
    raise ValueError(f"{label}: unsupported type.")


def cfg(key: str):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return SCHEMA[key]["default"]
    try:
        return json.loads(row["value"])
    except ValueError:
        return SCHEMA[key]["default"]


def set_cfg(key: str, value: Any):
    v = coerce(key, value)
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, json.dumps(v)))
    db.commit()
    return v


def all_cfg() -> dict:
    return {k: cfg(k) for k in SCHEMA}


def get_raw(key: str) -> Optional[str]:
    """Internal bot state that is not a user-facing setting (e.g. the panel message id)."""
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def put_raw(key: str, value: str):
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    db.commit()


# One-time switch (Nov 2026): no AI – duplicates matching 60 %+ are closed automatically by the built-in check.
if get_raw("dup_auto_60") != "1":
    set_cfg("dup_mode", "auto")
    set_cfg("dup_auto_percent", 60)
    for _k in ("ai_key", "ai_enabled", "ai_auto_close", "ai_auto_percent", "ai_model", "ai_monthly_limit", "ai_usage"):
        db.execute("DELETE FROM settings WHERE key=?", (_k,))
    put_raw("dup_auto_60", "1")


# ------------------------------------------------------------ activity log
LOG_KINDS = ("bug", "backup", "link", "ticket", "mod", "admin", "suggestion", "welcome", "patch", "block", "panel", "settings",
             "profile", "system", "invite", "guard")
LOG_KEEP = 5000
log_listeners = []          # the bot adds a function here to copy every entry to the Discord log channel


def log_event(kind: str, text: str, actor_id: Optional[int] = None, actor_name: Optional[str] = None):
    db.execute(
        "INSERT INTO logs (ts, kind, actor_id, actor_name, text) VALUES (?,?,?,?,?)",
        (int(time.time()), kind, actor_id, actor_name, text[:500]),
    )
    db.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) FROM logs) - ?", (LOG_KEEP,))
    db.commit()
    for f in log_listeners:
        try:
            f({"ts": int(time.time()), "kind": kind, "actor_id": actor_id, "actor_name": actor_name, "text": text[:500]})
        except Exception:  # noqa: BLE001 – the log must never break what is being logged
            pass


def query_logs(limit: int = 50, offset: int = 0, kind: Optional[str] = None, q: Optional[str] = None):
    where, args = [], []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(text LIKE ? ESCAPE '\\' OR actor_name LIKE ? ESCAPE '\\')")
        args += [f"%{esc}%", f"%{esc}%"]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM logs {clause}", args).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM logs {clause} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)
    ).fetchall()
    items = [{
        "id": r["id"], "ts": r["ts"], "kind": r["kind"],
        "actor": r["actor_name"], "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
        "text": r["text"],
    } for r in rows]
    return items, total


# --------------------------------------------------------- welcome message
def render_welcome(template: str, mention: str, name: str, server: str, count) -> str:
    """Fill in the placeholders. The new member is always tagged: if the text has no {user},
    the mention is put in front."""
    text = (template.replace("{user}", mention).replace("{username}", name)
            .replace("{server}", server).replace("{member_count}", str(count)))
    if "{user}" not in template:
        text = f"{mention} {text}"
    return text[:2000]


# ----------------------------------------------------------------- actions
def record_action(kind: str, item_id: int, action: str, actor_id=None, actor_name=None):
    db.execute("INSERT INTO actions (ts, kind, item_id, action, actor_id, actor_name) VALUES (?,?,?,?,?,?)",
               (int(time.time()), kind, item_id, action, actor_id, actor_name))
    db.commit()


def my_bugs(actor_id: int, limit: int = 15) -> dict:
    """What one moderator/admin has handled: each bug once, with its status now and their last action on it."""
    rows = db.execute("SELECT b.*, a.action AS my_action, a.ts AS my_ts FROM bug_reports b JOIN "
                      "(SELECT item_id, action, ts FROM actions WHERE id IN (SELECT MAX(id) FROM actions WHERE kind='bug' AND actor_id=? "
                      "GROUP BY item_id)) a ON a.item_id=b.id ORDER BY a.ts DESC", (actor_id,)).fetchall()
    now_status = {}
    for r in rows:
        now_status[r["status"]] = now_status.get(r["status"], 0) + 1
    did = {r["action"]: r["n"] for r in db.execute(
        "SELECT action, COUNT(*) AS n FROM actions WHERE kind='bug' AND actor_id=? GROUP BY action", (actor_id,))}
    week = db.execute("SELECT COUNT(DISTINCT item_id) FROM actions WHERE kind='bug' AND actor_id=? AND ts>=?",
                      (actor_id, int(time.time()) - 7 * 86400)).fetchone()[0]
    return {"total": len(rows), "week": week, "status_now": now_status, "actions": did, "recent": rows[:limit]}


def bug_actions(bug_id: int):
    return db.execute("SELECT * FROM actions WHERE kind='bug' AND item_id=? ORDER BY id", (bug_id,)).fetchall()


def last_change_ts(bug) -> int:
    row = db.execute("SELECT MAX(ts) FROM actions WHERE kind='bug' AND item_id=?", (bug["id"],)).fetchone()
    return row[0] or bug["created_at"] or 0


# ---------------------------------------------------------------- summaries
_TEMPLATE_LINES = ("what is the bug", "do you know why it is happening",
                   "what is the correct way the feature should work")


def bug_summary(r) -> str:
    """Short one-line title: the saved summary, or the first real line of the report."""
    if r["summary"]:
        return r["summary"]
    for line in (r["text"] or "").splitlines():
        t = line.strip().strip("*#>-: ").strip()
        if t and t.lower().rstrip("?:") not in _TEMPLATE_LINES and not t.lower().startswith(_TEMPLATE_LINES):
            return t[:100]
    # the player answered inline after the template question: "What is the bug? <answer>"
    for line in (r["text"] or "").splitlines():
        low = line.lower()
        for q in _TEMPLATE_LINES:
            if low.strip().startswith(q) and len(line) > len(q) + 3:
                return line[len(q) + 1:].strip(" ?:-")[:100]
    return (r["text"] or "").strip()[:100] or "(no text)"


# ------------------------------------------------------------------ bug list
BUG_STATUSES = ("new", "investigating", "passed", "rejected", "being_investigated", "fixed", "admin_rejected")


def query_bugs(status=None, q=None, limit=50, offset=0, area=None, version=None, severity=None, handled_by=None, dups=False):
    where, args = [], []
    if dups:                                # "Possible duplicates": open bugs the bot thinks were reported before
        where.append(f"dup_suggest IS NOT NULL AND duplicate_of IS NULL AND status IN ({','.join('?' * len(OPEN_STATUSES))})")
        args += list(OPEN_STATUSES)
    if handled_by is not None:              # "Handled by me": bugs this staff member did something with
        where.append("id IN (SELECT item_id FROM actions WHERE kind='bug' AND actor_id=?)")
        args.append(handled_by)
    for col, val in (('area', area), ('game_version', version), ('severity', severity)):
        if val:
            where.append(f'{col}=?')
            args.append(val)
    if status:
        where.append("status=?")
        args.append(status)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if q.lstrip("#").isdigit():
            where.append("(id=? OR text LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
            args += [int(q.lstrip("#")), f"%{esc}%", f"%{esc}%"]
        else:
            where.append("(text LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR reporter_name LIKE ? ESCAPE '\\' "
                         "OR chief_name LIKE ? ESCAPE '\\' OR player_uid LIKE ? ESCAPE '\\')")
            args += [f"%{esc}%"] * 5
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM bug_reports {clause}", args).fetchone()[0]
    rows = db.execute(f"SELECT * FROM bug_reports {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                      (*args, limit, offset)).fetchall()
    counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM bug_reports GROUP BY status")}
    counts["_dups"] = db.execute(f"SELECT COUNT(*) FROM bug_reports WHERE dup_suggest IS NOT NULL AND duplicate_of IS NULL AND status IN "
                                 f"({','.join('?' * len(OPEN_STATUSES))})", OPEN_STATUSES).fetchone()[0]
    return rows, total, counts


# ----------------------------------------------------------------- blocklist
def get_block(user_id: int):
    """The active block for a user, or None (expired blocks are removed)."""
    row = db.execute("SELECT * FROM blocked WHERE user_id=?", (user_id,)).fetchone()
    if row and row["until"] and row["until"] <= time.time():
        db.execute("DELETE FROM blocked WHERE user_id=?", (user_id,))
        db.commit()
        return None
    return row


def block_user(user_id: int, reason: str = "", days: Optional[float] = None, by_name: str = ""):
    until = int(time.time() + days * 86400) if days else None
    db.execute("INSERT OR REPLACE INTO blocked (user_id, reason, until, by_name, created_at) VALUES (?,?,?,?,?)",
               (user_id, (reason or "").strip()[:200], until, by_name, int(time.time())))
    db.commit()


def unblock_user(user_id: int) -> bool:
    cur = db.execute("DELETE FROM blocked WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def list_blocks():
    now = int(time.time())
    db.execute("DELETE FROM blocked WHERE until IS NOT NULL AND until<=?", (now,))
    db.commit()
    return db.execute("SELECT * FROM blocked ORDER BY created_at DESC").fetchall()


# ------------------------------------------------------------ canned replies
def list_replies():
    return db.execute("SELECT * FROM replies ORDER BY title COLLATE NOCASE").fetchall()


def clean_reply(title, text):
    title, text = str(title or "").strip(), str(text or "").strip()
    if not 1 <= len(title) <= 60:
        raise ValueError("Title must be 1–60 characters.")
    if not 1 <= len(text) <= 1500:
        raise ValueError("Reply text must be 1–1500 characters.")
    return title, text


def add_reply(title, text) -> int:
    title, text = clean_reply(title, text)
    if len(list_replies()) >= 50:
        raise ValueError("You can keep at most 50 canned replies.")
    cur = db.execute("INSERT INTO replies (title, text) VALUES (?,?)", (title, text))
    db.commit()
    return cur.lastrowid


def update_reply(rid: int, title, text):
    title, text = clean_reply(title, text)
    cur = db.execute("UPDATE replies SET title=?, text=? WHERE id=?", (title, text, rid))
    db.commit()
    if not cur.rowcount:
        raise LookupError("Reply not found.")


def delete_reply(rid: int):
    db.execute("DELETE FROM replies WHERE id=?", (rid,))
    db.commit()


# --------------------------------------------------------------- patch notes
def unreleased_fixed():
    return db.execute("SELECT * FROM bug_reports WHERE status='fixed' AND patched_at IS NULL ORDER BY id").fetchall()


def mark_patched(bug_ids, title: str, by_name: str):
    now = int(time.time())
    db.executemany("UPDATE bug_reports SET patched_at=? WHERE id=?", [(now, i) for i in bug_ids])
    db.execute("INSERT INTO patchnotes (ts, title, bug_ids, by_name) VALUES (?,?,?,?)",
               (now, title, ",".join(map(str, bug_ids)), by_name))
    db.commit()


def patchnote_history(limit=10):
    return db.execute("SELECT * FROM patchnotes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ----------------------------------------------------------------- insights
def insights(weeks: int = 8) -> dict:
    now = int(time.time())
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0] or 0
    series = []
    for i in range(weeks - 1, -1, -1):
        start, end = now - (i + 1) * 7 * 86400, now - i * 7 * 86400 + (1 if i == 0 else 0)   # newest week includes 'now'
        series.append({
            "start": start,
            "reported": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=? AND created_at<?", start, end),
            "fixed": one("SELECT COUNT(DISTINCT item_id) FROM actions WHERE kind='bug' AND action='fixed' "
                         "AND ts>=? AND ts<?", start, end),
            "suggestions": one("SELECT COUNT(*) FROM suggestions WHERE created_at>=? AND created_at<?", start, end),
        })
    row = db.execute(
        "SELECT AVG(f.t - b.created_at) AS avg, COUNT(*) AS n FROM bug_reports b "
        "JOIN (SELECT item_id, MAX(ts) AS t FROM actions WHERE kind='bug' AND action='fixed' GROUP BY item_id) f "
        "ON f.item_id=b.id WHERE b.status='fixed' AND b.created_at IS NOT NULL").fetchone()
    reporters = []
    for r in db.execute(
            "SELECT CASE WHEN reporter_id=0 THEN 'uid:' || COALESCE(player_uid,'?') ELSE CAST(reporter_id AS TEXT) END AS reporter_id, "
            "MAX(COALESCE(reporter_name, chief_name)) AS name, COUNT(*) AS total, "
            "SUM(status IN ('passed','being_investigated','fixed','admin_rejected')) AS accepted, "
            "SUM(status='fixed') AS fixed, SUM(status='rejected') AS rejected "
            "FROM bug_reports GROUP BY 1 ORDER BY total DESC, accepted DESC LIMIT 10"):
        reporters.append({"id": str(r["reporter_id"]), "name": r["name"], "total": r["total"],
                          "accepted": r["accepted"] or 0, "fixed": r["fixed"] or 0, "rejected": r["rejected"] or 0})
    staff = {}
    for r in db.execute("SELECT actor_id, actor_name, kind, action, COUNT(*) AS n FROM actions "
                        "GROUP BY actor_id, actor_name, kind, action"):
        key = r["actor_id"] or r["actor_name"] or "?"
        e = staff.setdefault(str(key), {"id": str(r["actor_id"]) if r["actor_id"] else None,
                                        "name": r["actor_name"] or "Unknown", "counts": {}, "total": 0})
        k = ("suggestion_" if r["kind"] == "suggestion" else "") + r["action"]
        e["counts"][k] = e["counts"].get(k, 0) + r["n"]
        e["total"] += r["n"]
    return {
        "weeks": series,
        "fix_hours": round(row["avg"] / 3600, 1) if row and row["avg"] is not None else None,
        "fix_count": row["n"] if row else 0,
        "duplicates": one("SELECT COUNT(*) FROM bug_reports WHERE duplicate_of IS NOT NULL"),
        "reporters": reporters,
        "areas": [{"name": r["a"], "total": r["n"], "open": r["o"] or 0, "fixed": r["f"] or 0} for r in db.execute(
            "SELECT COALESCE(area,'(none)') AS a, COUNT(*) AS n, "
            "SUM(status IN ('new','investigating','passed','being_investigated')) AS o, SUM(status='fixed') AS f "
            "FROM bug_reports GROUP BY 1 ORDER BY n DESC LIMIT 12")],
        "versions": [{"name": r["v"], "total": r["n"], "fixed": r["f"] or 0} for r in db.execute(
            "SELECT game_version AS v, COUNT(*) AS n, SUM(status='fixed') AS f FROM bug_reports "
            "WHERE game_version IS NOT NULL AND game_version<>'' GROUP BY 1 ORDER BY MAX(id) DESC LIMIT 8")],
        "staff": sorted(staff.values(), key=lambda e: -e["total"]),
    }


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


MOD_FIRST = ("passed", "rejected", "investigating")
ADMIN_FIRST = ("being_investigated", "fixed", "admin_rejected")


def mod_stats(days: int = 30, wait_hours: int = 24) -> dict:
    """Moderator / admin numbers for the dashboard: how fast reports get a first answer, who handles what,
    and what has been waiting too long right now."""
    now = int(time.time())
    since = now - days * 86400 if days else 0
    hours = lambda sec: round(sec / 3600, 1) if sec is not None else None
    people = {}

    def person(aid, name):
        key = str(aid or name or "?")
        return people.setdefault(key, {"id": str(aid) if aid else None, "name": name or "Unknown", "handled": 0,
                                       "bugs": 0, "suggestions": 0, "replies": 0, "first": [], "admin_first": [],
                                       "last": 0})

    for r in db.execute("SELECT * FROM actions WHERE ts>=? AND actor_name IS NOT NULL ORDER BY ts", (since,)):
        if r["action"] == "ticket_closed":          # done by the bot itself, not a person
            continue
        p = person(r["actor_id"], r["actor_name"])
        p["handled"] += 1
        p["last"] = max(p["last"], r["ts"])
        if r["action"] == "replied":
            p["replies"] += 1
        elif r["kind"] == "suggestion":
            p["suggestions"] += 1
        else:
            p["bugs"] += 1

    # first answer from a moderator, per bug reported in the period
    mod_times, admin_times = [], []
    rows = db.execute(
        "SELECT b.id, b.created_at, a.ts, a.actor_id, a.actor_name, a.action FROM bug_reports b JOIN actions a "
        "ON a.kind='bug' AND a.item_id=b.id WHERE b.created_at>=? ORDER BY b.id, a.ts", (since,)).fetchall()
    seen_mod, seen_admin, passed_at = set(), set(), {}
    for r in rows:
        if r["action"] in MOD_FIRST and r["id"] not in seen_mod and r["created_at"]:
            seen_mod.add(r["id"])
            t = max(0, r["ts"] - r["created_at"])
            mod_times.append(t)
            person(r["actor_id"], r["actor_name"])["first"].append(t)
        if r["action"] == "passed" and r["id"] not in passed_at:
            passed_at[r["id"]] = r["ts"]
        if r["action"] in ADMIN_FIRST and r["id"] not in seen_admin and r["id"] in passed_at:
            seen_admin.add(r["id"])
            t = max(0, r["ts"] - passed_at[r["id"]])
            admin_times.append(t)
            person(r["actor_id"], r["actor_name"])["admin_first"].append(t)
    sug_times = [max(0, r[1] - r[0]) for r in db.execute(
        "SELECT s.created_at, MIN(a.ts) FROM suggestions s JOIN actions a ON a.kind='suggestion' AND a.item_id=s.id "
        "WHERE s.created_at>=? GROUP BY s.id", (since,)) if r[0] and r[1]]

    staff = []
    for p in people.values():
        staff.append({"id": p["id"], "name": p["name"], "handled": p["handled"], "bugs": p["bugs"],
                      "suggestions": p["suggestions"], "replies": p["replies"], "last": p["last"] or None,
                      "first_count": len(p["first"]), "first_hours": hours(_median(p["first"])),
                      "admin_count": len(p["admin_first"]), "admin_hours": hours(_median(p["admin_first"]))})
    staff.sort(key=lambda e: -e["handled"])

    # waiting right now
    limit = now - wait_hours * 3600
    waiting = []
    for r in db.execute("SELECT * FROM bug_reports WHERE status IN ('new','investigating','passed','being_investigated') "
                        "ORDER BY created_at"):
        start = r["created_at"] or now
        who = "Moderators"
        if r["status"] in ("passed", "being_investigated"):
            who = "Admins"
            t = db.execute("SELECT MAX(ts) FROM actions WHERE kind='bug' AND item_id=? AND action=?",
                           (r["id"], r["status"])).fetchone()[0]
            start = t or start
        elif r["status"] == "investigating":
            who = "Info ticket"
        if start <= limit:
            waiting.append({"kind": "bug", "id": r["id"], "title": bug_summary(r), "status": r["status"], "who": who,
                            "since": start, "severity": r["severity"], "votes": vote_count(r["id"])})
    for r in db.execute("SELECT * FROM suggestions WHERE status='new' AND created_at<=? ORDER BY created_at", (limit,)):
        waiting.append({"kind": "suggestion", "id": r["id"], "title": (r["text"] or "")[:80], "status": "new",
                        "who": "Moderators", "since": r["created_at"], "severity": None, "votes": 0})
    waiting.sort(key=lambda w: w["since"])
    sev = {"critical": 0, "major": 1, "minor": 2}
    waiting.sort(key=lambda w: (w["who"] == "Info ticket", sev.get(w["severity"], 3), w["since"]))
    return {
        "days": days, "wait_hours": wait_hours,
        "mod_hours": hours(_median(mod_times)), "mod_count": len(mod_times),
        "admin_hours": hours(_median(admin_times)), "admin_count": len(admin_times),
        "sug_hours": hours(_median(sug_times)), "sug_count": len(sug_times),
        "within_day": round(sum(1 for t in mod_times if t <= 86400) / len(mod_times) * 100) if mod_times else None,
        "staff": staff,
        "waiting": waiting[:40], "waiting_total": len(waiting),
    }


# ----------------------------------------------------------- areas, severity
SEVERITIES = ("critical", "major", "minor")


def area_list() -> list:
    return [x for x in str(cfg("bug_areas") or "").splitlines() if x.strip()]


# ------------------------------------------------------------ account links
def get_link(discord_id: int):
    return db.execute("SELECT * FROM links WHERE discord_id=?", (discord_id,)).fetchone()


def links_for_uid(uid: str):
    return db.execute("SELECT * FROM links WHERE player_uid=?", (str(uid),)).fetchall()


def clean_uid(uid) -> str:
    u = re.sub(r"\s", "", str(uid or ""))
    if u and not re.fullmatch(r"\d{4,20}", u):
        raise ValueError("The player UID must be a number (4–20 digits).")
    return u


def set_link(discord_id: int, uid, chief_name: str):
    uid = clean_uid(uid)
    if not uid:
        raise ValueError("Enter your player UID.")
    db.execute("INSERT OR REPLACE INTO links (discord_id, player_uid, chief_name, ts) VALUES (?,?,?,?)",
               (discord_id, uid, (chief_name or "").strip()[:50], int(time.time())))
    db.commit()


def delete_link(discord_id: int) -> bool:
    cur = db.execute("DELETE FROM links WHERE discord_id=?", (discord_id,))
    db.commit()
    return cur.rowcount > 0


def all_links(q: str = "", limit: int = 200):
    """Linked accounts for the dashboard, newest first (search in chief name, UID or Discord name)."""
    q = (q or "").strip()
    sql = ("SELECT l.*, (SELECT COUNT(*) FROM bug_reports b WHERE b.reporter_id=l.discord_id) AS bugs, "
           "(SELECT COUNT(*) FROM redemptions r WHERE r.uid=l.player_uid AND r.status IN ('ok','already')) AS redeemed "
           "FROM links l")
    args = []
    if q:
        sql += " WHERE l.chief_name LIKE ? OR l.player_uid LIKE ? OR CAST(l.discord_id AS TEXT)=?"
        args = [f"%{q}%", f"%{q}%", q]
    sql += " ORDER BY l.ts DESC LIMIT ?"
    return db.execute(sql, (*args, limit)).fetchall()


def link_count() -> int:
    return db.execute("SELECT COUNT(*) FROM links").fetchone()[0]


# ------------------------------------------------------------ weekly public post
def weekly_data(days: int = 7) -> dict:
    """What happened for players in the last week – only public information."""
    now = int(time.time())
    since = now - days * 86400
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0] or 0
    fixed_ids = [r[0] for r in db.execute("SELECT DISTINCT item_id FROM actions WHERE kind='bug' AND action='fixed' AND ts>=?", (since,))]
    fixed = [get_bug(i) for i in fixed_ids]
    fixed = [r for r in fixed if r is not None and r["status"] == "fixed"]
    g = member_growth(30)
    week = [i for i in g["items"] if i["day"] >= time.strftime("%Y-%m-%d", time.localtime(since))]
    return {
        "days": days, "since": since,
        "reports": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=?", since),
        "fixed_total": len(fixed),
        "fixed_public": [{"id": r["id"], "title": bug_summary(r), "area": r["area"]} for r in fixed if r["public"]][:10],
        "codes": [{"code": r["code"], "reward": r["reward"], "expires": r["expires_at"]} for r in active_gift_codes(now) if r["created_at"] >= since][:10],
        "codes_active": len(active_gift_codes(now)),
        "ideas": [x for x in ideas_added(10) if (x["ts"] or 0) >= since],
        "ideas_accepted": one("SELECT COUNT(*) FROM actions WHERE kind='suggestion' AND action='accepted' AND ts>=?", since),
        "known": [{"id": r["id"], "title": bug_summary(r), "votes": r["votes"]} for r in board_bugs(5)],
        "members": g["current"], "joined": sum(i["joins"] for i in week), "left": sum(i["leaves"] for i in week),
    }


# ------------------------------------------------------------ phone notifications (web push)
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com",
              "notify.windows.com", "push.apple.com")
PUSH_TOPICS = ("codes", "news")


def push_endpoint_ok(url: str) -> bool:
    """Only the real push services – the bot must never be made to send requests anywhere else."""
    m = re.match(r"https://([a-z0-9.-]+)(?::443)?/", url or "", re.I)
    return bool(m) and any(m.group(1).lower() == h or m.group(1).lower().endswith("." + h) for h in PUSH_HOSTS)


def push_subscribe(sub: dict, topics, lang: str) -> None:
    ep, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
    if not push_endpoint_ok(ep) or len(ep) > 800:
        raise ValueError("This browser's notification address isn't supported.")
    p, a = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not (20 <= len(p) <= 200 and 8 <= len(a) <= 100):
        raise ValueError("Missing notification keys.")
    t = ",".join(x for x in PUSH_TOPICS if x in (topics or PUSH_TOPICS)) or "codes"
    if db.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0] >= 50000:
        raise ValueError("Too many devices are signed up right now.")
    db.execute("INSERT OR REPLACE INTO push_subs (endpoint, p256dh, auth, topics, lang, ts) VALUES (?,?,?,?,?,?)",
               (ep, p, a, t, (lang or "en")[:10], int(time.time())))
    db.commit()


def push_unsubscribe(endpoint: str) -> bool:
    cur = db.execute("DELETE FROM push_subs WHERE endpoint=?", (str(endpoint or ""),))
    db.commit()
    return cur.rowcount > 0


def push_targets(topic: str):
    return db.execute("SELECT * FROM push_subs WHERE ',' || topics || ',' LIKE ?", (f"%,{topic},%",)).fetchall()


def push_count() -> dict:
    rows = db.execute("SELECT topics FROM push_subs").fetchall()
    return {"total": len(rows), "codes": sum("codes" in r[0] for r in rows), "news": sum("news" in r[0] for r in rows)}


# ------------------------------------------------------------ role menus
def role_menus():
    return db.execute("SELECT * FROM role_menus ORDER BY id").fetchall()


def get_role_menu(mid: int):
    return db.execute("SELECT * FROM role_menus WHERE id=?", (mid,)).fetchone()


def save_role_menu(mid, **f) -> int:
    if mid:
        db.execute(f"UPDATE role_menus SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), mid))
    else:
        mid = db.execute(f"INSERT INTO role_menus ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())).lastrowid
    db.commit()
    return mid


def delete_role_menu(mid: int):
    db.execute("DELETE FROM role_menus WHERE id=?", (mid,))
    db.commit()


# ------------------------------------------------------------ warnings and cases
def add_case(user_id: int, user_name: str, kind: str, reason: str, mod_id, mod_name: str, minutes=None, auto: bool = False) -> int:
    cid = db.execute("INSERT INTO mod_cases (user_id, user_name, kind, reason, moderator_id, moderator_name, ts, minutes, active, auto) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (user_id, (user_name or "")[:60], kind, (reason or "")[:500] or None, mod_id,
                                                      (mod_name or "")[:60], int(time.time()), minutes, 1 if kind == "warn" else 0,
                                                      1 if auto else 0)).lastrowid
    db.commit()
    return cid


def active_warnings(user_id: int) -> int:
    days = cfg("warn_expire_days")
    since = int(time.time()) - days * 86400 if days else 0
    return db.execute("SELECT COUNT(*) FROM mod_cases WHERE user_id=? AND kind='warn' AND active=1 AND ts>=?", (user_id, since)).fetchone()[0]


def cases_for(user_id: int, limit: int = 25):
    return db.execute("SELECT * FROM mod_cases WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()


def recent_cases(limit: int = 60, q: str = ""):
    if q:
        return db.execute("SELECT * FROM mod_cases WHERE user_name LIKE ? OR CAST(user_id AS TEXT)=? OR reason LIKE ? ORDER BY id DESC LIMIT ?",
                          (f"%{q}%", q, f"%{q}%", limit)).fetchall()
    return db.execute("SELECT * FROM mod_cases ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def pardon_case(case_id: int):
    r = db.execute("SELECT * FROM mod_cases WHERE id=?", (case_id,)).fetchone()
    if r is None or r["kind"] != "warn" or not r["active"]:
        return None
    db.execute("UPDATE mod_cases SET active=0 WHERE id=?", (case_id,))
    db.commit()
    return r


# ------------------------------------------------------------ giveaways
def get_giveaway(gid: int):
    return db.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()


def giveaways(limit: int = 50):
    return db.execute("SELECT * FROM giveaways ORDER BY ended, ends_at DESC LIMIT ?", (limit,)).fetchall()


def due_giveaways(now=None):
    return db.execute("SELECT * FROM giveaways WHERE ended=0 AND ends_at<=?", (now or int(time.time()),)).fetchall()


# ------------------------------------------------------------ timed messages
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def timer_next_run(t, after: int) -> Optional[int]:
    """The next time a timer fires after `after` (unix seconds), in the server's time zone."""
    from datetime import datetime, timedelta
    if t["kind"] == "once":
        try:
            when = int(datetime.strptime(str(t["at"] or ""), "%Y-%m-%dT%H:%M").replace(tzinfo=tz()).timestamp())
        except ValueError:
            return None
        return when if when > after else None
    if t["kind"] == "interval":
        every = max(5, int(t["every_minutes"] or 60)) * 60
        return after + every
    try:
        hh, mm = (int(x) for x in str(t["at"] or "").split(":"))
    except ValueError:
        return None
    days = [d for d in str(t["days"] or "").split(",") if d in WEEKDAYS] or list(WEEKDAYS)
    z = tz()
    base = datetime.fromtimestamp(after, z)
    for i in range(0, 8):
        d = (base + timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if WEEKDAYS[d.weekday()] in days and d.timestamp() > after:
            return int(d.timestamp())
    return None


def timers():
    return db.execute("SELECT * FROM timers ORDER BY id").fetchall()


def get_timer(tid: int):
    return db.execute("SELECT * FROM timers WHERE id=?", (tid,)).fetchone()


def save_timer(tid, **f) -> int:
    if tid:
        db.execute(f"UPDATE timers SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), tid))
    else:
        tid = db.execute(f"INSERT INTO timers ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())).lastrowid
    db.commit()
    t = get_timer(tid)
    db.execute("UPDATE timers SET next_run=? WHERE id=?", (timer_next_run(t, int(time.time())), tid))
    db.commit()
    return tid


def due_timers(now=None):
    return db.execute("SELECT * FROM timers WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?", (now or int(time.time()),)).fetchall()


# ------------------------------------------------------------ invite tracker
def record_join(member_id: int, name: str, inviter_id, code, fake: bool) -> int:
    cur = db.execute("INSERT INTO invite_joins (member_id, member_name, inviter_id, code, ts, fake) VALUES (?,?,?,?,?,?)",
                     (member_id, (name or "")[:60], inviter_id, code, int(time.time()), 1 if fake else 0))
    db.commit()
    return cur.lastrowid


def record_leave(member_id: int):
    """Marks the member's latest join as left. Returns that row (to say who had invited them) or None."""
    r = db.execute("SELECT * FROM invite_joins WHERE member_id=? AND left_ts IS NULL ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()
    if r is not None:
        db.execute("UPDATE invite_joins SET left_ts=? WHERE id=?", (int(time.time()), r["id"]))
        db.commit()
    return r


def _tracked_counts(inviter_id: int) -> dict:
    """Only what this bot has seen itself, counting each member once (their latest join)."""
    rows = db.execute("SELECT j.* FROM invite_joins j JOIN (SELECT member_id, MAX(id) AS id FROM invite_joins WHERE inviter_id=? "
                      "GROUP BY member_id) l ON l.id=j.id", (inviter_id,)).fetchall()
    left = sum(1 for r in rows if r["left_ts"])
    fake = sum(1 for r in rows if r["fake"] and not r["left_ts"])
    return {"total": len(rows), "left": left, "fake": fake}


def _finish(c: dict) -> dict:
    c["regular"] = c["total"] - c["left"] - c["fake"] + c.get("bonus", 0)
    return c


def invite_counts(inviter_id: int) -> dict:
    """Invites = joined − left − new accounts + bonus, including the counts carried over from Invite Tracker.
    "regular" is the invite number shown everywhere."""
    c = _tracked_counts(inviter_id)
    b = db.execute("SELECT * FROM invite_base WHERE user_id=?", (inviter_id,)).fetchone()
    c["bonus"] = 0
    if b is not None:
        c["total"] += b["joins"]; c["left"] += b["left_n"]; c["fake"] += b["fake"]; c["bonus"] = b["bonus"] + b["extra_bonus"]
    return _finish(c)


def set_invite_base(user_id: int, joins: int, left: int, fake: int, bonus: int, name: str = "") -> dict:
    """Carry over someone's numbers from Invite Tracker. Whatever this bot already counted itself is taken off,
    so the totals match Invite Tracker right after the import and keep growing from there (importing twice is safe)."""
    t = _tracked_counts(user_id)
    db.execute("INSERT INTO invite_base (user_id, joins, left_n, fake, bonus, ts, name) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET joins=excluded.joins, left_n=excluded.left_n, fake=excluded.fake, "
               "bonus=excluded.bonus, ts=excluded.ts, name=excluded.name",   # keeps the bonus given with /invites bonus
               (user_id, joins - t["total"], left - t["left"], fake - t["fake"], bonus, int(time.time()), (name or "")[:60]))
    db.commit()
    return invite_counts(user_id)


def add_invite_bonus(user_id: int, amount: int, name: str = "") -> dict:
    db.execute("INSERT INTO invite_base (user_id, extra_bonus, name) VALUES (?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET extra_bonus=extra_bonus+excluded.extra_bonus", (user_id, amount, (name or "")[:60]))
    db.commit()
    return invite_counts(user_id)


def clear_invite_base() -> int:
    """Removes what was carried over from Invite Tracker (bonus given here with /invites bonus stays)."""
    n = db.execute("SELECT COUNT(*) FROM invite_base WHERE ts IS NOT NULL").fetchone()[0]
    db.execute("DELETE FROM invite_base WHERE extra_bonus=0")
    db.execute("UPDATE invite_base SET joins=0, left_n=0, fake=0, bonus=0, ts=NULL")
    db.commit()
    return n


def invite_base_info() -> dict:
    r = db.execute("SELECT COUNT(*) AS n, MAX(ts) AS ts FROM invite_base WHERE ts IS NOT NULL").fetchone()
    return {"players": r["n"] or 0, "ts": r["ts"]}


def invite_top(limit: int = 10, since: Optional[int] = None) -> list:
    sql = ("SELECT j.inviter_id, COUNT(*) AS total, SUM(j.left_ts IS NOT NULL) AS left_n, "
           "SUM(j.fake=1 AND j.left_ts IS NULL) AS fake_n FROM invite_joins j JOIN (SELECT member_id, MAX(id) AS id FROM invite_joins "
           "WHERE inviter_id IS NOT NULL" + (" AND ts>=?" if since else "") + " GROUP BY member_id) l ON l.id=j.id GROUP BY j.inviter_id")
    rows = db.execute(sql, (since,) if since else ()).fetchall()
    out = {r["inviter_id"]: {"inviter_id": r["inviter_id"], "total": r["total"], "left": r["left_n"] or 0, "fake": r["fake_n"] or 0, "bonus": 0}
           for r in rows}
    if not since:                                      # all-time lists include the counts carried over from Invite Tracker
        for b in db.execute("SELECT * FROM invite_base").fetchall():
            c = out.setdefault(b["user_id"], {"inviter_id": b["user_id"], "total": 0, "left": 0, "fake": 0, "bonus": 0})
            c["total"] += b["joins"]; c["left"] += b["left_n"]; c["fake"] += b["fake"]; c["bonus"] = b["bonus"] + b["extra_bonus"]
    res = [_finish(c) for c in out.values()]
    res.sort(key=lambda x: (-x["regular"], -x["total"]))
    return res[:limit]


def recent_joins(limit: int = 50):
    return db.execute("SELECT * FROM invite_joins ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ------------------------------------------------------------ "I have this too"
def toggle_vote(bug_id: int, user_id: int) -> bool:
    """Add or remove the player's vote. Returns True when they now have a vote on the bug."""
    if db.execute("SELECT 1 FROM bug_votes WHERE bug_id=? AND user_id=?", (bug_id, user_id)).fetchone():
        db.execute("DELETE FROM bug_votes WHERE bug_id=? AND user_id=?", (bug_id, user_id))
        db.commit()
        return False
    db.execute("INSERT INTO bug_votes (bug_id, user_id, ts) VALUES (?,?,?)", (bug_id, user_id, int(time.time())))
    db.commit()
    return True


def add_vote(bug_id: int, user_id: int):
    db.execute("INSERT OR IGNORE INTO bug_votes (bug_id, user_id, ts) VALUES (?,?,?)", (bug_id, user_id, int(time.time())))
    db.commit()


def vote_count(bug_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM bug_votes WHERE bug_id=?", (bug_id,)).fetchone()[0]


def vote_counts() -> dict:
    return {r[0]: r[1] for r in db.execute("SELECT bug_id, COUNT(*) FROM bug_votes GROUP BY bug_id")}


def voters(bug_id: int) -> list:
    return [r[0] for r in db.execute("SELECT user_id FROM bug_votes WHERE bug_id=? ORDER BY ts", (bug_id,))]


def board_bugs(limit: int = 25):
    """Open known issues for the Discord board: on the public list, confirmed or being investigated –
    most "me too" first."""
    return db.execute(
        "SELECT b.*, (SELECT COUNT(*) FROM bug_votes v WHERE v.bug_id=b.id) AS votes FROM bug_reports b "
        "WHERE b.public=1 AND b.status IN ('passed','being_investigated') ORDER BY votes DESC, b.id DESC LIMIT ?",
        (limit,)).fetchall()


def recently_fixed_public(days: int = 7, limit: int = 8):
    since = int(time.time()) - days * 86400
    return db.execute(
        "SELECT b.* FROM bug_reports b WHERE b.public=1 AND b.status='fixed' AND EXISTS "
        "(SELECT 1 FROM actions a WHERE a.kind='bug' AND a.item_id=b.id AND a.action='fixed' AND a.ts>=?) "
        "ORDER BY b.id DESC LIMIT ?", (since, limit)).fetchall()


# ------------------------------------------------------------ gift-code redeem
def redemption_done(code: str, uid: str) -> bool:
    return db.execute("SELECT 1 FROM redemptions WHERE code=? AND uid=? AND status IN ('ok','already')",
                      (code, uid)).fetchone() is not None


def save_redemption(code: str, uid: str, discord_id, status: str, message: str = ""):
    db.execute("INSERT OR REPLACE INTO redemptions (code, uid, discord_id, status, message, ts) VALUES (?,?,?,?,?,?)",
               (code, uid, discord_id, status, (message or "")[:300], int(time.time())))
    db.commit()


def redemption_summary() -> dict:
    """{code: {"ok": n, "already": n, "failed": n}}"""
    out = {}
    for r in db.execute("SELECT code, status, COUNT(*) AS n FROM redemptions GROUP BY code, status"):
        out.setdefault(r["code"], {"ok": 0, "already": 0, "failed": 0})[r["status"]] = r["n"]
    return out


def redemption_failures(limit: int = 30):
    return db.execute("SELECT r.*, l.chief_name FROM redemptions r LEFT JOIN links l ON l.discord_id=r.discord_id "
                      "WHERE r.status='failed' ORDER BY r.ts DESC LIMIT ?", (limit,)).fetchall()


def redeemed_codes_for(uid: str) -> set:
    return {r[0] for r in db.execute("SELECT code FROM redemptions WHERE uid=? AND status IN ('ok','already')", (uid,))}


# -------------------------------------------------------------- stored files
def safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(name or "file"))[:80].strip(". ") or "file"
    return n


def save_file(bug_id: int, filename: str, data: bytes, max_bytes: int = 25 * 1024 * 1024):
    """Keep a copy of an attachment on disk. Returns the row id, or None if skipped."""
    if not cfg("keep_attachments") or not data or len(data) > max_bytes:
        return None
    folder = ATTACH_DIR / str(bug_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(filename)
    n = db.execute("SELECT COUNT(*) FROM bug_files WHERE bug_id=?", (bug_id,)).fetchone()[0]
    path = folder / f"{n + 1}_{name}"
    path.write_bytes(data)
    cur = db.execute("INSERT INTO bug_files (bug_id, filename, path, size, ts) VALUES (?,?,?,?,?)",
                     (bug_id, name, str(path.relative_to(ATTACH_DIR)), len(data), int(time.time())))
    db.commit()
    return cur.lastrowid


def bug_files(bug_id: int):
    return db.execute("SELECT * FROM bug_files WHERE bug_id=? ORDER BY id", (bug_id,)).fetchall()


def get_file(file_id: int):
    return db.execute("SELECT * FROM bug_files WHERE id=?", (file_id,)).fetchone()


def file_path(row) -> Path:
    p = (ATTACH_DIR / row["path"]).resolve()
    if ATTACH_DIR.resolve() not in p.parents:       # never serve anything outside the attachments folder
        raise ValueError("bad path")
    return p


def storage_info() -> dict:
    n, size = db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM bug_files").fetchone()
    return {"files": n, "bytes": size}


def cleanup_files(days: int) -> int:
    """Delete kept files of finished bugs older than `days`. Returns how many were removed."""
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    rows = db.execute("SELECT f.* FROM bug_files f JOIN bug_reports b ON b.id=f.bug_id "
                      "WHERE f.ts<? AND b.status IN ('fixed','rejected','admin_rejected')", (cutoff,)).fetchall()
    for r in rows:
        try:
            file_path(r).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        db.execute("DELETE FROM bug_files WHERE id=?", (r["id"],))
    db.commit()
    return len(rows)


# ------------------------------------------------------------------ backups
BACKUP_RE = re.compile(r"^bugs-\d{8}-\d{6}\.db$")


def make_backup(keep: int = 14) -> Path:
    dest = BACKUP_DIR / time.strftime("bugs-%Y%m%d-%H%M%S.db")
    out = sqlite3.connect(dest)
    try:
        with out:
            db.backup(out)
    finally:
        out.close()
    for old in sorted(p for p in BACKUP_DIR.iterdir() if BACKUP_RE.match(p.name))[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def list_backups():
    items = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in BACKUP_DIR.iterdir() if BACKUP_RE.match(p.name)]
    return [{"name": n, "size": sz, "ts": ts} for n, sz, ts in sorted(items, reverse=True)]


# -------------------------------------------------------------- public list
PUBLIC_STATUS = {"passed": "Confirmed", "being_investigated": "Investigating", "fixed": "Fixed"}


def known_issues(limit_fixed: int = 30):
    rows = db.execute("SELECT * FROM bug_reports WHERE public=1 AND status IN ('passed','being_investigated') "
                      "ORDER BY id DESC LIMIT 100").fetchall()
    rows += db.execute("SELECT * FROM bug_reports WHERE public=1 AND status='fixed' ORDER BY id DESC LIMIT ?",
                       (limit_fixed,)).fetchall()
    votes = vote_counts()
    return [{"id": r["id"], "title": bug_summary(r), "area": r["area"], "status": r["status"],
             "status_label": PUBLIC_STATUS[r["status"]], "reported_in": r["game_version"] or None,
             "fixed_in": r["fixed_version"] or None, "updated": last_change_ts(r),
             "affected": votes.get(r["id"], 0) + len(duplicates_of(r["id"]))} for r in rows]


def ideas_added(limit: int = 30):
    """Accepted player suggestions that staff have marked as added, with what was changed (newest first)."""
    rows = db.execute("SELECT * FROM suggestions WHERE status='accepted' AND changes IS NOT NULL AND changes<>'' "
                      "ORDER BY changed_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "idea": (r["text"] or "")[:400], "changes": r["changes"], "ts": r["changed_at"]} for r in rows]


# ------------------------------------------------ website spam: automatic pauses
def web_blocked(keys) -> Optional[sqlite3.Row]:
    """The active pause for any of these keys ('ip:<hash>', 'uid:<uid>'), or None."""
    keys = [k for k in keys if k]
    if not keys:
        return None
    marks = ",".join("?" * len(keys))
    return db.execute(f"SELECT * FROM web_blocks WHERE key IN ({marks}) AND until>? ORDER BY until DESC LIMIT 1",
                      (*keys, int(time.time()))).fetchone()


def note_web_reject(r) -> list:
    """Called when a website report is rejected. If the same connection/UID has had its last N website
    reports all rejected, pause it for a few days. Returns the keys that were paused."""
    if not cfg("auto_block_web"):
        return []
    n, days = cfg("auto_block_rejects"), cfg("auto_block_days")
    paused = []
    for key, col, val in (("ip:" + (r["ip_hash"] or ""), "ip_hash", r["ip_hash"]),
                          ("uid:" + (r["player_uid"] or ""), "player_uid", r["player_uid"])):
        if not val:
            continue
        last = db.execute(f"SELECT status, duplicate_of FROM bug_reports WHERE source='web' AND {col}=? "
                          "ORDER BY id DESC LIMIT ?", (val, n)).fetchall()
        if len(last) == n and all(x["status"] in ("rejected", "admin_rejected") and not x["duplicate_of"] for x in last):
            until = int(time.time()) + days * 86400
            db.execute("INSERT OR REPLACE INTO web_blocks (key, until, reason, ts) VALUES (?,?,?,?)",
                       (key, until, f"{n} website reports in a row were rejected", int(time.time())))
            paused.append(key)
    if paused:
        db.commit()
        log_event("block", f"Website reports paused for {days} day(s) after {n} rejected reports in a row "
                           f"(latest: bug #{r['id']}, {', '.join(k[:12] for k in paused)})", None, "Auto-block")
    return paused


def list_web_blocks():
    return db.execute("SELECT * FROM web_blocks WHERE until>? ORDER BY until DESC", (int(time.time()),)).fetchall()


def remove_web_block(key: str) -> bool:
    cur = db.execute("DELETE FROM web_blocks WHERE key=?", (key,))
    db.commit()
    return cur.rowcount > 0


# --------------------------------------------------------- similar bugs / changelog
# ---------------------------------------------------------------- duplicate detection
# Words that say nothing about *which* bug it is (English + Polish + the ticket's own questions).
_STOP = set("""the and for with that this when after before have has had not but are was were from into over under there their then
than they them your you our its cant dont does doesnt just very again still some any all bug bugs game issue issues problem problems
error errors please help fix fixed work works working happen happens happening know why what correct way feature should would could
also only even get got can will one two time times now today yesterday since every everything something nothing thing things
make made see seen saw use used using player players server like same other another more less much many really
jest nie się że na do to co jak ale czy już tak mam gra błąd problem""".split())
# Game words that mean the same thing for this purpose.
_SYN = {}
for _group in ("lose lost loses losing disappear disappears disappeared vanish vanished missing gone deleted removed",
               "crash crashes crashed closes closing kicked kick freeze freezes frozen stuck hang hangs lag lags loading",
               "teleport teleported teleports relocate relocation tp",
               "reward rewards prize prizes chest chests gift gifts",
               "gem gems diamond diamonds", "march marches troop troops army armies", "rally rallies",
               "price prices cost costs expensive", "wrong incorrect invalid broken bugged", "alliance alli guild clan",
               "furnace fc", "hero heroes", "pet pets", "bear hunt bearhunt", "svs statevstate", "shop store",
               "upgrade upgrades upgrading level levels", "event events", "mail mails inbox", "chat chats message messages"):
    _w = _group.split()
    for _x in _w:
        _SYN[_x] = _w[0]
_TEMPLATE_RE = None


def _template_re():
    """The ticket's own questions in every language – removed before comparing, they're in every report."""
    global _TEMPLATE_RE
    if _TEMPLATE_RE is None:
        qs = set(_TEMPLATE_LINES)
        try:
            import texts
            for lang in texts.DEFAULTS.values():
                for line in str(lang.get("bug.template", "")).splitlines():
                    if line.strip():
                        qs.add(line.strip().lower().rstrip("?:"))
        except Exception:  # noqa: BLE001
            pass
        qs = {_fold(q) for q in qs}
        _TEMPLATE_RE = re.compile("|".join(re.escape(q) for q in sorted(qs, key=len, reverse=True)) + r"|chief:.*|uid:.*|\d+\.\s+", re.I)
    return _TEMPLATE_RE


def _fold(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c)).replace("ł", "l")


def _stem(w: str) -> str:
    w = _SYN.get(w, w)
    for suf in ("ings", "ing", "edly", "ed", "ies", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return _SYN.get(w, w)[:7]


def _tokens(text: str) -> set:
    t = _template_re().sub(" ", _fold(text))
    words = re.findall(r"[^\W\d_]{3,}|\d{3,}", t)                     # any alphabet (Latin, Cyrillic, Arabic …)
    out = {_stem(w) for w in words if w not in _STOP and _SYN.get(w, w) not in _STOP}
    for run in re.findall(r"[぀-ヿ㐀-鿿가-힯]{2,}", t):   # Chinese/Japanese/Korean: pairs of characters
        out |= {run[i:i + 2] for i in range(len(run) - 1)}
    return out


def _trigrams(text: str) -> set:
    t = " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", _fold(text))).strip() + " "
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) > 4 else set()


OPEN_STATUSES = ("new", "investigating", "passed", "being_investigated")


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


_FEAT: dict = {}


def _features(r) -> dict:
    """Cached per bug: title words, body words, title letter-triplets, screenshot fingerprints."""
    g = lambda k: r[k] if k in r.keys() else None
    key = (r["id"], len(r["text"] or ""), r["summary"] or "", g("ai_title"), len(g("text_en") or ""))
    f = _FEAT.get(r["id"])
    if f is None or f["key"] != key:
        title = r["summary"] or bug_summary(r)
        en = english_text(r)                       # the AI's English summary or a moderator's translation, if any
        f = {"key": key, "title": _tokens(title) | _tokens(g("ai_title") or ""),
             "body": _tokens((r["text"] or "")[:1500]) | _tokens(en[:1500]),
             "tri": _trigrams(title), "tri_en": _trigrams(g("ai_title") or ""),
             "norm": _norm(_template_re().sub(" ", (r["text"] or "").lower()))}
        _FEAT[r["id"]] = f
    if "img" not in f or f.get("img_n") != _img_count(r["id"]):
        f["img"], f["img_n"] = image_hashes(r["id"]), _img_count(r["id"])
    return f


_IDF: dict = {"n": 0, "df": {}, "ts": 0}


def _idf(tok: str) -> float:
    """Rare words count more ("furnace" says more than "village"). Rebuilt every 10 minutes from recent reports."""
    import math
    if time.time() - _IDF["ts"] > 600:
        df, n = {}, 0
        for row in db.execute("SELECT id, text, summary FROM bug_reports ORDER BY id DESC LIMIT 1500").fetchall():
            n += 1
            for w in _tokens((row["summary"] or "") + " " + (row["text"] or "")[:1500]):
                df[w] = df.get(w, 0) + 1
        _IDF.update(n=max(n, 1), df=df, ts=time.time())
    return math.log(1 + _IDF["n"] / (1 + _IDF["df"].get(tok, 0)))


def _wsim(a: set, b: set) -> float:
    """Weighted overlap of two word sets (0–1)."""
    if not a or not b:
        return 0.0
    common = sum(_idf(w) for w in a & b)
    return 2 * common / (sum(_idf(w) for w in a) + sum(_idf(w) for w in b))


def duplicate_score(a, b) -> int:
    """0–100: how much two bug reports look like the same problem – titles, descriptions (any language,
    typos and synonyms) and screenshots."""
    fa, fb = _features(a), _features(b)
    if fa["norm"] and len(fa["norm"]) > 12 and fa["norm"] == fb["norm"]:
        return 100
    title = _wsim(fa["title"], fb["title"]) if len(fa["title"]) >= 1 and len(fb["title"]) >= 1 else 0.0
    body = _wsim(fa["title"] | fa["body"], fb["title"] | fb["body"])
    tri = len(fa["tri"] & fb["tri"]) / len(fa["tri"] | fb["tri"]) if fa["tri"] and fb["tri"] else 0.0
    if fa["tri_en"] and fb["tri_en"]:                # English titles from the AI: reports in different languages
        tri = max(tri, len(fa["tri_en"] & fb["tri_en"]) / len(fa["tri_en"] | fb["tri_en"]))
    score = max(title * 0.9, body * 1.05, 0.45 * title + 0.35 * body + 0.3 * tri, tri * 0.85 if tri > 0.55 else 0)
    if a["area"] and b["area"] and a["area"] != b["area"]:
        score *= 0.85
    # screenshots: the same picture (or nearly) is a strong sign
    d = _img_distance(fa["img"], fb["img"])
    if d is not None:
        if d <= 4:
            score = max(score, 0.9)
        elif d <= 8:
            score = max(score + 0.25, 0.6)
        elif d <= 12:
            score += 0.12
    return int(round(min(score, 1.0) * 100))


# ---- screenshot fingerprints (difference hash: same screenshot ⇒ almost the same 64 bits, even resized or re-compressed)
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def clean_report(text: str) -> str:
    """The player's own words without the ticket's questions."""
    return " ".join(line.strip() for line in (text or "").splitlines()
                    if line.strip() and line.strip().strip("*#>-: ").lower().rstrip("?:") not in _TEMPLATE_LINES)


def english_text(r) -> str:
    """English text for matching: the AI summary, a moderator's 🌐 translation, or a cached translation."""
    g = lambda k: r[k] if k in r.keys() else None
    parts = [g("ai_summary") or "", g("text_en") or ""]
    if not parts[1] and r["text"]:
        import hashlib
        t = (r["text"] or "").strip()[:2000]
        for code in ("en", "en-US", "en-GB"):
            h = hashlib.sha1(f"{code}\0{t}".encode()).hexdigest()
            try:
                row = db.execute("SELECT out FROM translate_cache WHERE h=?", (h,)).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                parts[1] = row["out"]
                break
    return " ".join(p for p in parts if p)


def _img_count(bug_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM bug_files WHERE bug_id=?", (bug_id,)).fetchone()[0]


_VID_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")


def image_hashes(bug_id: int) -> list:
    out = []
    for f in bug_files(bug_id):
        name = f["filename"].lower()
        if not name.endswith(_IMG_EXT + _VID_EXT):
            continue
        h = f["img_hash"] if "img_hash" in f.keys() else None
        if not h:
            h = _dhash(ATTACH_DIR / f["path"]) if name.endswith(_IMG_EXT) else _video_hashes(ATTACH_DIR / f["path"])
            if h:
                db.execute("UPDATE bug_files SET img_hash=? WHERE id=?", (h, f["id"]))
                db.commit()
        if h and h != "-":
            out += [int(x, 16) for x in h.split(",") if x]
    return out


def video_frames(path, n: int = 3) -> list:
    """A few still frames from a video (start, middle, end) as PIL images. Empty when the video can't be read."""
    try:
        import av
    except ImportError:
        return []
    frames = []
    try:
        with av.open(str(path)) as c:
            s = c.streams.video[0]
            dur = float(s.duration * s.time_base) if s.duration else float(c.duration or 0) / 1_000_000
            points = [dur * x for x in (0.1, 0.5, 0.9)][:n] if dur > 0 else [0]
            for t in points:
                c.seek(int(t / s.time_base) if s.time_base else 0, stream=s, any_frame=False, backward=True)
                for fr in c.decode(s):
                    frames.append(fr.to_image())
                    break
    except Exception:  # noqa: BLE001 – broken or unsupported video
        return frames
    return frames


def _video_hashes(path) -> str:
    """Fingerprints of 3 frames (start, middle, end) of a video, comma-separated."""
    try:
        frames = video_frames(path)
    except Exception:  # noqa: BLE001
        frames = []
    hs = []
    for im in frames:
        b = io.BytesIO()
        im.save(b, "PNG")
        b.seek(0)
        h = _dhash(b)
        if h and h != "-":
            hs.append(h)
    return ",".join(hs) or "-"


def _dhash(path) -> Optional[str]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            if h > w * 1.2:                                    # phone screenshots: compare the middle (no status bars)
                im = im.crop((0, int(h * 0.08), w, int(h * 0.92)))
            px = list(im.resize((9, 8), Image.LANCZOS).getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
        return f"{bits:016x}"
    except Exception:  # noqa: BLE001 – not an image, or Pillow missing
        return "-"


def _img_distance(a: list, b: list) -> Optional[int]:
    if not a or not b:
        return None
    return min(bin(x ^ y).count("1") for x in a for y in b)


def find_duplicate(r, days: int = 120):
    """The open bug this new report most likely duplicates: (row, percent) or (None, 0)."""
    since = int(time.time()) - days * 86400
    rows = db.execute(f"SELECT * FROM bug_reports WHERE id<>? AND id<? AND duplicate_of IS NULL AND created_at>=? AND "
                      f"(status IN ({','.join('?' * len(OPEN_STATUSES))}) OR public=1 OR (status='fixed' AND id IN (SELECT item_id FROM actions WHERE kind='bug' AND action='fixed' AND ts>=?))) "
                      "ORDER BY id DESC LIMIT 500",
                      (r["id"], r["id"], since, *OPEN_STATUSES, int(time.time()) - 21 * 86400)).fetchall()
    best, best_s = None, 0
    for c in rows:
        s = duplicate_score(r, c)
        if r["reporter_id"] and c["reporter_id"] == r["reporter_id"]:
            s = min(100, s + 15)                       # the same player sending it again
        if s > best_s:
            best, best_s = c, s
    return best, best_s


def similar_reports(r, limit: int = 3, days: int = 180, min_score: int = 35) -> list:
    """Reports that look like this one (any status except closed duplicates), best first: [(row, percent)].
    Used on the staff page so a moderator can merge with one tap."""
    since = int(time.time()) - days * 86400
    rows = db.execute("SELECT * FROM bug_reports WHERE id<>? AND duplicate_of IS NULL AND (created_at>=? OR status IN "
                      f"({','.join('?' * len(OPEN_STATUSES))})) ORDER BY id DESC LIMIT 600",
                      (r["id"], since, *OPEN_STATUSES)).fetchall()
    out = []
    for c in rows:
        s = duplicate_score(r, c)
        if r["reporter_id"] and c["reporter_id"] == r["reporter_id"]:
            s = min(100, s + 15)
        if s >= min_score:
            out.append((c, s))
    out.sort(key=lambda x: (-x[1], x[0]["id"]))
    return out[:limit]


def flagged_dups(min_pct: int) -> list:
    """Bugs still waiting for a moderator that were marked as a likely duplicate at or above min_pct."""
    return db.execute(f"SELECT * FROM bug_reports WHERE status IN ({','.join('?' * len(OPEN_STATUSES))}) AND duplicate_of IS NULL "
                      "AND dup_suggest IS NOT NULL AND dup_score >= ? AND dup_dismissed IS NULL ORDER BY id",
                      (*OPEN_STATUSES, min_pct)).fetchall()


def bugs_needing_dup_check() -> list:
    """Open bugs without a duplicate suggestion that nobody has said 'not a duplicate' about."""
    return db.execute(f"SELECT * FROM bug_reports WHERE status IN ({','.join('?' * len(OPEN_STATUSES))}) AND duplicate_of IS NULL "
                      "AND dup_suggest IS NULL AND dup_dismissed IS NULL ORDER BY id", OPEN_STATUSES).fetchall()


REJECT_PRESETS = {
    "duplicate": "Already reported – we're tracking it in another report.",
    "intended": "This works as intended in the game.",
    "cant_repro": "We couldn't reproduce it with the information given.",
    "not_bug": "This isn't a bug – it's part of the game's design.",
    "missing_info": "The report is missing the details we need. Please send a new one with steps and a screenshot.",
    "game_side": "This comes from the game itself, not something our server team can change.",
    "planned": "We already have this planned.",
    "not_now": "We won't take this forward right now.",
    "other": "",
}


def similar_bugs(text: str, limit: int = 3, area: Optional[str] = None) -> list:
    """Public (known-issues) bugs that look like the text being typed. Only bugs a moderator has put on the
    public list are ever suggested, so no private titles leak."""
    q = _tokens(text)
    if len(q) < 2:
        return []
    rows = db.execute("SELECT * FROM bug_reports WHERE public=1 AND status IN "
                      "('passed','being_investigated','fixed') ORDER BY id DESC LIMIT 300").fetchall()
    scored = []
    for r in rows:
        t = _tokens(bug_summary(r))
        common = len(q & t)
        if common < 2 and not (common and len(t) <= 2):
            continue
        score = common / max(1, min(len(q), len(t)))
        if area and r["area"] == area:
            score += 0.1
        if score >= 0.5:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
    return [{"id": r["id"], "title": bug_summary(r), "area": r["area"], "status": r["status"],
             "status_label": PUBLIC_STATUS[r["status"]], "fixed_in": r["fixed_version"] or None}
            for _, r in scored[:limit]]


def changelog(limit: int = 20) -> list:
    """Published patch notes, newest first: [{id, title, ts, items:[{title, area}]}]."""
    out = []
    for p in patchnote_history(limit):
        ids = [int(x) for x in (p["bug_ids"] or "").split(",") if x.strip().isdigit()]
        items = []
        for i in ids:
            r = get_bug(i)
            if r is not None and r["status"] == "fixed":
                items.append({"title": bug_summary(r), "area": r["area"] or None})
        out.append({"id": p["id"], "title": p["title"], "ts": p["ts"], "items": items})
    return out


def duplicates_of(bug_id: int):
    """Reports that were closed as a duplicate of this bug (oldest first)."""
    return db.execute("SELECT * FROM bug_reports WHERE duplicate_of=? ORDER BY id", (bug_id,)).fetchall()


# ------------------------------------------------------------ timezone helpers
def tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(cfg("timezone"))
    except Exception:  # noqa: BLE001 - unknown name: fall back to UTC instead of crashing the loops
        from datetime import timezone
        return timezone.utc


def month_range(month: Optional[str] = None):
    """(start_ts, end_ts, 'YYYY-MM') of a calendar month in the configured timezone; default = this month."""
    from datetime import datetime
    now = datetime.now(tz())
    y, m = (int(month[:4]), int(month[5:7])) if month else (now.year, now.month)
    start = datetime(y, m, 1, tzinfo=tz())
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=tz())
    return int(start.timestamp()), int(end.timestamp()), f"{y:04d}-{m:02d}"


def previous_month() -> str:
    from datetime import datetime
    n = datetime.now(tz())
    return f"{n.year - (n.month == 1):04d}-{(n.month - 2) % 12 + 1:02d}"


# ------------------------------------------------------------- weekly digest
def digest_data(days: int = 7, now: Optional[int] = None) -> dict:
    now = now or int(time.time())
    start, prev = now - days * 86400, now - 2 * days * 86400
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0] or 0
    def acted(act, a, b):
        marks = ",".join("?" * len(act))
        return db.execute(f"SELECT COUNT(DISTINCT item_id) FROM actions WHERE kind='bug' AND action IN ({marks}) "
                          "AND ts>=? AND ts<?", (*act, a, b)).fetchone()[0]
    fixed_avg = db.execute(
        "SELECT AVG(f.t - b.created_at) FROM bug_reports b JOIN (SELECT item_id, MAX(ts) AS t FROM actions WHERE "
        "kind='bug' AND action='fixed' AND ts>=? GROUP BY item_id) f ON f.item_id=b.id WHERE b.created_at IS NOT NULL",
        (start,)).fetchone()[0]
    open_now = {k: one("SELECT COUNT(*) FROM bug_reports WHERE status=?", k)
                for k in ("new", "investigating", "passed", "being_investigated")}
    oldest = db.execute("SELECT * FROM bug_reports WHERE status IN ('passed','being_investigated') "
                        "AND created_at IS NOT NULL ORDER BY created_at ASC LIMIT 3").fetchall()
    critical = db.execute("SELECT * FROM bug_reports WHERE severity='critical' AND status IN "
                          "('new','investigating','passed','being_investigated') ORDER BY id").fetchall()
    return {
        "days": days, "start": start,
        "reported": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=?", start),
        "reported_prev": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=? AND created_at<?", prev, start),
        "web": one("SELECT COUNT(*) FROM bug_reports WHERE created_at>=? AND source='web'", start),
        "fixed": acted(("fixed",), start, now + 1),
        "rejected": acted(("rejected", "admin_rejected"), start, now + 1),
        "fix_hours": round(fixed_avg / 3600, 1) if fixed_avg is not None else None,
        "open": open_now,
        "oldest": [{"id": r["id"], "title": bug_summary(r), "age_days": int((now - r["created_at"]) // 86400),
                    "status": r["status"]} for r in oldest],
        "critical": [{"id": r["id"], "title": bug_summary(r)} for r in critical],
        "areas": [(r["a"], r["n"]) for r in db.execute(
            "SELECT COALESCE(area,'(none)') AS a, COUNT(*) AS n FROM bug_reports WHERE created_at>=? GROUP BY 1 "
            "ORDER BY n DESC LIMIT 3", (start,))],
        "reporters": [(r["reporter_id"], r["n"]) for r in db.execute(
            "SELECT reporter_id, COUNT(*) AS n FROM bug_reports WHERE created_at>=? AND reporter_id<>0 "
            "GROUP BY 1 ORDER BY n DESC LIMIT 3", (start,))],
        "suggestions": one("SELECT COUNT(*) FROM suggestions WHERE created_at>=?", start),
        "suggestions_accepted": db.execute("SELECT COUNT(DISTINCT item_id) FROM actions WHERE kind='suggestion' "
                                           "AND action='accepted' AND ts>=?", (start,)).fetchone()[0],
    }




# ----------------------------------------------------------------- member growth
def _local_day(ts=None) -> str:
    from datetime import datetime
    return (datetime.fromtimestamp(ts, tz()) if ts else datetime.now(tz())).strftime("%Y-%m-%d")


def record_members(count: int, joined: int = 0, left: int = 0) -> None:
    """Update today's row (called on every join/leave and by the 10-minute loop)."""
    db.execute("INSERT INTO member_log (day, members, joins, leaves) VALUES (?,?,?,?) "
               "ON CONFLICT(day) DO UPDATE SET members=excluded.members, joins=joins+excluded.joins, "
               "leaves=leaves+excluded.leaves, est=0", (_local_day(), count, joined, left))
    db.commit()


def backfill_members(join_times, current: int, days: int = 90) -> bool:
    """First run only: rebuild past days from the join dates of the members who are still here.
    Members who already left are unknown, so these days are marked as estimates."""
    if db.execute("SELECT 1 FROM member_log LIMIT 1").fetchone():
        return False
    from datetime import datetime, timedelta
    per = {}
    for t in join_times:
        per[_local_day(t)] = per.get(_local_day(t), 0) + 1
    today = datetime.now(tz()).date()
    running = current
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        n = per.get(d, 0)
        db.execute("INSERT OR IGNORE INTO member_log (day, members, joins, leaves, est) VALUES (?,?,?,0,1)",
                   (d, max(running, 0), n))
        running -= n
    db.commit()
    return True


def member_growth(days: int = 90) -> dict:
    from datetime import datetime, timedelta
    rows = {r["day"]: r for r in db.execute("SELECT * FROM member_log ORDER BY day")}
    if not rows:
        return {"current": None, "items": [], "joined": 0, "left": 0, "estimated": False}
    first = datetime.strptime(min(rows), "%Y-%m-%d").date()
    today = datetime.now(tz()).date()
    start = max(first, today - timedelta(days=days - 1))
    prev = None
    for d in sorted(rows):                       # the count just before the window starts
        if d < start.strftime("%Y-%m-%d"):
            prev = rows[d]["members"]
    items, day = [], start
    while day <= today:
        k = day.strftime("%Y-%m-%d")
        r = rows.get(k)
        if r is not None:
            prev = r["members"]
        items.append({"day": k, "members": prev if prev is not None else 0,
                      "joins": r["joins"] if r else 0, "leaves": r["leaves"] if r else 0,
                      "est": bool(r["est"]) if r else False})
        day += timedelta(days=1)
    last30 = items[-30:]
    return {"current": items[-1]["members"], "items": items,
            "joined": sum(i["joins"] for i in last30), "left": sum(i["leaves"] for i in last30),
            "estimated": any(i["est"] for i in items)}


# ----------------------------------------------------------------- deleting bugs (e.g. test reports)
def delete_bugs(ids, reset_numbers: bool = False) -> list:
    """Deletes bugs completely: the report, its files, history and duplicate links.
    Returns the deleted rows (so the bot can also remove the Discord messages)."""
    import shutil
    ids = sorted({int(i) for i in ids})
    rows = [r for r in (get_bug(i) for i in ids) if r is not None]
    for r in rows:
        i = r["id"]
        shutil.rmtree(ATTACH_DIR / str(i), ignore_errors=True)
        db.execute("DELETE FROM bug_files WHERE bug_id=?", (i,))
        db.execute("DELETE FROM bug_votes WHERE bug_id=?", (i,))
        db.execute("DELETE FROM actions WHERE kind='bug' AND item_id=?", (i,))
        db.execute("UPDATE bug_reports SET duplicate_of=NULL WHERE duplicate_of=?", (i,))
        if r["ticket_thread_id"]:
            db.execute("DELETE FROM tickets WHERE thread_id=?", (r["ticket_thread_id"],))
        db.execute("DELETE FROM bug_reports WHERE id=?", (i,))
    if reset_numbers and not db.execute("SELECT 1 FROM bug_reports LIMIT 1").fetchone():
        db.execute("DELETE FROM sqlite_sequence WHERE name='bug_reports'")   # next bug is #1 again
    db.commit()
    return rows


def all_bug_ids() -> list:
    return [r[0] for r in db.execute("SELECT id FROM bug_reports")]


# ----------------------------------------------------------------- suggestions list / delete
def query_suggestions(status=None, q=None, limit=50, offset=0):
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if q.lstrip("#").isdigit():
            where.append("(id=? OR text LIKE ? ESCAPE '\\')"); args += [int(q.lstrip("#")), f"%{esc}%"]
        else:
            where.append("(text LIKE ? ESCAPE '\\' OR author_name LIKE ? ESCAPE '\\')"); args += [f"%{esc}%"] * 2
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM suggestions {clause}", args).fetchone()[0]
    rows = db.execute(f"SELECT * FROM suggestions {clause} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM suggestions GROUP BY status")}
    return rows, total, counts


def delete_suggestions(ids, reset_numbers: bool = False) -> list:
    ids = sorted({int(i) for i in ids})
    rows = [r for r in (get_suggestion(i) for i in ids) if r is not None]
    for r in rows:
        db.execute("DELETE FROM actions WHERE kind='suggestion' AND item_id=?", (r["id"],))
        db.execute("DELETE FROM suggestions WHERE id=?", (r["id"],))
    if reset_numbers and not db.execute("SELECT 1 FROM suggestions LIMIT 1").fetchone():
        db.execute("DELETE FROM sqlite_sequence WHERE name='suggestions'")
    db.commit()
    return rows


def all_suggestion_ids() -> list:
    return [r[0] for r in db.execute("SELECT id FROM suggestions")]


# ----------------------------------------------------------------- gift codes
GIFT_DURATIONS = [24, 48, 72, 96, 120, 168, 336, 720, None]      # hours; None = permanent
GIFT_CODE_RE = re.compile(r"^[A-Za-z0-9_\-]{3,40}$")


def gift_duration_label(hours) -> str:
    if hours is None:
        return "Permanent"
    return "24 hours" if hours == 24 else (f"{hours // 24} days" if hours % 24 == 0 else f"{hours} hours")


def add_gift_code(code: str, reward: str, hours, by_name: str = ""):
    code = (code or "").strip()
    if not GIFT_CODE_RE.match(code):
        raise ValueError("A code is 3–40 letters, numbers, - or _ (no spaces).")
    if hours is not None and not (1 <= int(hours) <= 24 * 365):
        raise ValueError("The duration must be between 1 hour and 1 year, or permanent.")
    now = int(time.time())
    for r in active_gift_codes(now):
        if r["code"].lower() == code.lower():
            raise ValueError(f"The code {code} is already active.")
    cur = db.execute("INSERT INTO gift_codes (code, reward, created_at, expires_at, created_by) VALUES (?,?,?,?,?)",
                     (code, (reward or "").strip()[:200] or None, now, now + int(hours) * 3600 if hours else None, by_name))
    db.commit()
    return get_gift_code(cur.lastrowid)


def get_gift_code(cid: int):
    return db.execute("SELECT * FROM gift_codes WHERE id=?", (cid,)).fetchone()


def set_gift_code(cid: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE gift_codes SET {cols} WHERE id=?", (*fields.values(), cid))
    db.commit()


def delete_gift_code(cid: int):
    db.execute("DELETE FROM gift_codes WHERE id=?", (cid,))
    db.commit()


def gift_codes(limit: int = 200):
    return db.execute("SELECT * FROM gift_codes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def active_gift_codes(now=None):
    now = now or int(time.time())
    return db.execute("SELECT * FROM gift_codes WHERE expires_at IS NULL OR expires_at>? ORDER BY created_at DESC", (now,)).fetchall()


def gift_code_state(r, now=None) -> str:
    now = now or int(time.time())
    if r["expires_at"] is not None and r["expires_at"] <= now:
        return "expired"
    return "new" if now - r["created_at"] < cfg("giftcodes_new_hours") * 3600 else "active"


def expired_unnoted_gift_codes(now=None):
    now = now or int(time.time())
    return db.execute("SELECT * FROM gift_codes WHERE expires_at IS NOT NULL AND expires_at<=? AND expired_noted=0", (now,)).fetchall()


# ----------------------------------------------------------------- translations
import texts as _texts


def languages() -> list:
    """Languages players can use (English is always included)."""
    out = ["en"]
    for x in str(cfg("languages") or "").split("\n"):
        x = x.strip().lower()
        if re.fullmatch(r"[a-z]{2,3}(-[a-z]{2})?", x) and x not in out:
            out.append(x)
    return out


def default_language() -> str:
    d = (cfg("default_language") or "en").strip().lower()
    return d if d in languages() else "en"


def translations() -> dict:
    try:
        d = json.loads(get_raw("translations") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def save_translations(d: dict):
    put_raw("translations", _texts.dumps(d))


def T(key: str, lang: Optional[str] = None, **kw) -> str:
    """A player-facing text in the given language: the admins' version first, then the built-in one, then English."""
    lang = lang or default_language()
    over = translations()
    if lang != "en":
        try:
            auto = json.loads(get_raw("translations_auto") or "{}").get(lang) or {}
        except ValueError:
            auto = {}
    for lg in (lang, "en"):
        txt = ((over.get(lg) or {}).get(key) or _texts.DEFAULTS.get(lg, {}).get(key)
               or (auto.get(key) if lg != "en" else None))
        if txt:
            return _texts.fill(txt, **kw)
    return key


def lang_for(user_id) -> str:
    if user_id:
        row = db.execute("SELECT lang FROM user_lang WHERE user_id=?", (user_id,)).fetchone()
        if row and row["lang"] in languages():
            return row["lang"]
    return default_language()


def locale_lang(locale) -> Optional[str]:
    """Match a Discord locale such as 'pl', 'en-US' or 'pt-BR' to one of our languages."""
    loc = str(locale or "").lower().replace("_", "-")
    langs = languages()
    if loc in langs:
        return loc
    base = loc.split("-")[0]
    if base == "nb":
        base = "no"
    return base if base in langs else None


def remember_locale(user_id: int, locale):
    """Use the player's Discord language, unless they picked one themselves."""
    lg = locale_lang(locale)
    if not lg:
        return
    row = db.execute("SELECT chosen, lang FROM user_lang WHERE user_id=?", (user_id,)).fetchone()
    if row is None or (not row["chosen"] and row["lang"] != lg):
        db.execute("INSERT OR REPLACE INTO user_lang (user_id, lang, chosen) VALUES (?,?,0)", (user_id, lg))
        db.commit()


def set_user_language(user_id: int, lang: Optional[str]):
    if lang is None:
        db.execute("DELETE FROM user_lang WHERE user_id=?", (user_id,))
    else:
        db.execute("INSERT OR REPLACE INTO user_lang (user_id, lang, chosen) VALUES (?,?,1)", (user_id, lang))
    db.commit()


def language_counts() -> dict:
    return {r["lang"]: r["n"] for r in db.execute("SELECT lang, COUNT(*) AS n FROM user_lang GROUP BY lang")}


def add_server_languages():
    """Once: switch on the languages of the server's text channels (keeps any the admins added)."""
    if get_raw("langs_v2") == "1":
        return
    cur = languages()
    merged = cur + [x for x in _texts.SERVER_LANGUAGES if x not in cur]
    if merged != cur and db.execute("SELECT 1 FROM settings WHERE key='languages'").fetchone():
        set_cfg("languages", "\n".join(merged))
    put_raw("langs_v2", "1")


# ----------------------------------------------------------------- dashboard sessions (Discord login)
import hashlib as _hashlib
import secrets as _secrets

SESSION_DAYS = 7


def _th(token: str) -> str:
    return _hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, name: str, avatar: str = "") -> str:
    token = _secrets.token_urlsafe(32)
    now = int(time.time())
    db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    db.execute("INSERT INTO sessions (token_hash, user_id, name, avatar, created, expires, last_seen) VALUES (?,?,?,?,?,?,?)",
               (_th(token), user_id, name, avatar, now, now + SESSION_DAYS * 86400, now))
    db.commit()
    return token


def get_session(token: str):
    if not token:
        return None
    row = db.execute("SELECT * FROM sessions WHERE token_hash=?", (_th(token),)).fetchone()
    if row is None or row["expires"] < time.time():
        return None
    if time.time() - (row["last_seen"] or 0) > 300:
        db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (int(time.time()), row["token_hash"]))
        db.commit()
    return row


def end_session(token: str):
    db.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token or ""),))
    db.commit()


def end_sessions(user_id: Optional[int] = None):
    if user_id is None:
        db.execute("DELETE FROM sessions")
    else:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    db.commit()


def list_sessions():
    return db.execute("SELECT user_id, name, avatar, created, expires, last_seen FROM sessions WHERE expires > ? "
                      "ORDER BY last_seen DESC", (int(time.time()),)).fetchall()


def import_gift_codes(lines, by_name: str = "Import", hours=None, backdate: bool = True) -> tuple:
    """Add many codes at once (one per line). Skips blanks, comments, invalid and already active codes.
    Returns (added, skipped). Backdated so they don't all show as "New" on the website."""
    added, skipped = [], []
    for raw_line in lines:
        code = raw_line.split("#", 1)[0].strip()
        if not code:
            continue
        try:
            r = add_gift_code(code, "", hours, by_name)
            if backdate:
                set_gift_code(r["id"], created_at=int(time.time()) - 7 * 86400 if hours is None else r["created_at"])
            added.append(code)
        except ValueError:
            skipped.append(code)
    return added, skipped
