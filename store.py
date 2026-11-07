"""Database, settings and activity log – shared by the bot and the dashboard API."""
import json
import os
import re
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
    CREATE TABLE IF NOT EXISTS points (       -- bug-bounty points, one row per reward (never given twice)
        kind TEXT NOT NULL,                    -- bug_accepted | bug_fixed | suggestion
        ref INTEGER NOT NULL,                  -- bug / suggestion number
        user_id INTEGER NOT NULL,
        pts INTEGER NOT NULL,
        ts INTEGER NOT NULL,
        PRIMARY KEY (kind, ref)
    );
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
    if fields.get("status") in ("passed", "being_investigated", "fixed"):
        r = get_bug(bug_id)
        if r is not None:
            award_bug(r)
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
    if fields.get("status") == "accepted":
        r = get_suggestion(sid)
        if r is not None:
            award_suggestion(r)


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
    "bounty_enabled": {"type": "bool", "default": True},
    "bounty_channel_id": {"type": "channel", "default": None},
    "points_accepted": {"type": "int", "default": 1, "min": 0, "max": 100},
    "points_fixed": {"type": "int", "default": 3, "min": 0, "max": 100},
    "points_suggestion": {"type": "int", "default": 2, "min": 0, "max": 100},
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
    "giftcodes_enabled": {"type": "bool", "default": False},
    "giftcodes_channel_id": {"type": "channel", "default": None},
    "giftcodes_ping": {"type": "choice", "default": "none", "choices": ["none", "here", "everyone", "role"]},
    "giftcodes_role_id": {"type": "text", "default": "", "min": 0, "max": 25},
    "giftcodes_new_hours": {"type": "int", "default": 24, "min": 1, "max": 168},
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


# ------------------------------------------------------------ activity log
LOG_KINDS = ("bug", "backup", "link", "ticket", "mod", "admin", "suggestion", "welcome", "patch", "block", "panel", "settings",
             "profile", "system")
LOG_KEEP = 5000


def log_event(kind: str, text: str, actor_id: Optional[int] = None, actor_name: Optional[str] = None):
    db.execute(
        "INSERT INTO logs (ts, kind, actor_id, actor_name, text) VALUES (?,?,?,?,?)",
        (int(time.time()), kind, actor_id, actor_name, text[:500]),
    )
    db.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) FROM logs) - ?", (LOG_KEEP,))
    db.commit()


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


def query_bugs(status=None, q=None, limit=50, offset=0, area=None, version=None, severity=None):
    where, args = [], []
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
    return [{"id": r["id"], "title": bug_summary(r), "area": r["area"], "status": r["status"],
             "status_label": PUBLIC_STATUS[r["status"]], "reported_in": r["game_version"] or None,
             "fixed_in": r["fixed_version"] or None, "updated": last_change_ts(r)} for r in rows]


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
_STOP = set("the and for with that this when after before have has had not but are was were from into over under "
            "there their then than they them your you our its it's cant can't dont don't does doesn't just very "
            "again still some any all bug game issue problem error".split())


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9ąćęłńóśźż]{3,}", (text or "").lower())
    return {w[:6] for w in words if w not in _STOP}        # crude stemming: first 6 letters


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


# --------------------------------------------------------- bug-bounty points
def award(kind: str, ref: int, user_id: int, pts: int, ts: Optional[int] = None) -> bool:
    """Give points once per (kind, ref). Returns True when points were newly given."""
    if not user_id or pts <= 0 or not cfg("bounty_enabled"):
        return False
    cur = db.execute("INSERT OR IGNORE INTO points (kind, ref, user_id, pts, ts) VALUES (?,?,?,?,?)",
                     (kind, ref, user_id, pts, ts or int(time.time())))
    db.commit()
    return cur.rowcount > 0


def award_bug(r, ts: Optional[int] = None):
    """Called whenever a bug reaches 'passed' / 'being_investigated' / 'fixed': 1 reward for the first two
    (accepted by the moderators), another when it is fixed."""
    if not r["reporter_id"]:
        return
    award("bug_accepted", r["id"], r["reporter_id"], cfg("points_accepted"), ts)
    if r["status"] == "fixed":
        award("bug_fixed", r["id"], r["reporter_id"], cfg("points_fixed"), ts)


def award_suggestion(r, ts: Optional[int] = None):
    if r["status"] == "accepted" and r["author_id"]:
        award("suggestion", r["id"], r["author_id"], cfg("points_suggestion"), ts)


def backfill_points():
    """Reward reports/suggestions that were handled before points existed (runs at start, safe to repeat)."""
    if not cfg("bounty_enabled"):
        return
    for r in db.execute("SELECT * FROM bug_reports WHERE reporter_id<>0 AND status IN "
                        "('passed','being_investigated','fixed')").fetchall():
        fixed_ts = db.execute("SELECT MAX(ts) FROM actions WHERE kind='bug' AND action='fixed' AND item_id=?",
                              (r["id"],)).fetchone()[0]
        award_bug(r, fixed_ts or r["created_at"])
    for r in db.execute("SELECT * FROM suggestions WHERE status='accepted'").fetchall():
        award_suggestion(r, r["created_at"])


def bug_points(bug_id: int) -> int:
    return db.execute("SELECT COALESCE(SUM(pts),0) FROM points WHERE kind IN ('bug_accepted','bug_fixed') AND ref=?",
                      (bug_id,)).fetchone()[0]


def leaderboard(period: str = "all", limit: int = 10, month: Optional[str] = None) -> list:
    """[{user_id, points, accepted, fixed, suggestions}] best first. period: 'all' | 'month' (month = 'YYYY-MM')."""
    where, args = "", []
    if period == "month":
        a, b, _ = month_range(month)
        where, args = "WHERE ts>=? AND ts<?", [a, b]
    rows = db.execute(f"SELECT user_id, SUM(pts) AS p, SUM(kind='bug_accepted') AS a, SUM(kind='bug_fixed') AS f, "
                      f"SUM(kind='suggestion') AS s, MAX(ts) AS last FROM points {where} GROUP BY user_id "
                      f"ORDER BY p DESC, last ASC LIMIT ?", (*args, limit)).fetchall()
    return [{"user_id": r["user_id"], "points": r["p"], "accepted": r["a"] or 0, "fixed": r["f"] or 0,
             "suggestions": r["s"] or 0} for r in rows]


def points_of(user_id: int) -> dict:
    a, b, _ = month_range()
    one = lambda sql, *x: db.execute(sql, x).fetchone()[0] or 0
    total = one("SELECT SUM(pts) FROM points WHERE user_id=?", user_id)
    rank = None
    if total:
        rank = 1 + one("SELECT COUNT(*) FROM (SELECT user_id FROM points GROUP BY user_id HAVING SUM(pts)>?)", total)
    return {"total": total, "month": one("SELECT SUM(pts) FROM points WHERE user_id=? AND ts>=? AND ts<?", user_id, a, b),
            "rank": rank, "accepted": one("SELECT COUNT(*) FROM points WHERE user_id=? AND kind='bug_accepted'", user_id),
            "fixed": one("SELECT COUNT(*) FROM points WHERE user_id=? AND kind='bug_fixed'", user_id),
            "suggestions": one("SELECT COUNT(*) FROM points WHERE user_id=? AND kind='suggestion'", user_id)}


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


backfill_points()


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
    """Deletes bugs completely: the report, its files, history, points and duplicate links.
    Returns the deleted rows (so the bot can also remove the Discord messages)."""
    import shutil
    ids = sorted({int(i) for i in ids})
    rows = [r for r in (get_bug(i) for i in ids) if r is not None]
    for r in rows:
        i = r["id"]
        shutil.rmtree(ATTACH_DIR / str(i), ignore_errors=True)
        db.execute("DELETE FROM bug_files WHERE bug_id=?", (i,))
        db.execute("DELETE FROM points WHERE kind IN ('bug_accepted','bug_fixed') AND ref=?", (i,))
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
        db.execute("DELETE FROM points WHERE kind='suggestion' AND ref=?", (r["id"],))
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
