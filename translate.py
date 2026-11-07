"""Machine translation with Google Cloud Translation (Basic, v2).

Free for the first 500,000 characters a month; the dashboard sets a monthly limit (default 450,000) so the bot
stops translating before anything could cost money. Used for:
  * filling in bot texts that nobody has translated yet (each language once),
  * the "Translate" option on any message (right-click / long-press → Apps → Translate),
  * the translation feed: messages from chosen channels are posted in English in one feed channel.
"""
import hashlib
import html
import json
import re
import time
from typing import List, Optional, Tuple

import aiohttp

import store
import texts

URL = "https://translation.googleapis.com/language/translate/v2"
_GOOGLE = {"zh-cn": "zh-CN", "zh-tw": "zh-TW", "pt-br": "pt", "fil": "tl", "nb": "no", "he": "iw"}

store.db.executescript("""
CREATE TABLE IF NOT EXISTS translate_cache (   -- message translations, so the same text is only paid for once
    h TEXT PRIMARY KEY,
    out TEXT NOT NULL,
    src TEXT,
    ts INTEGER NOT NULL
);
""")


class TranslateError(Exception):
    pass


def google_code(lang: str) -> str:
    lang = (lang or "en").lower()
    return _GOOGLE.get(lang, lang.split("-")[0])


def key() -> str:
    return (store.get_raw("translate_key") or "").strip()


def month() -> str:
    return time.strftime("%Y-%m")


def usage() -> dict:
    try:
        u = json.loads(store.get_raw("translate_usage") or "{}")
    except ValueError:
        u = {}
    if u.get("month") != month():
        u = {"month": month(), "chars": 0}
    return u


def _count(n: int):
    u = usage()
    u["chars"] += n
    store.put_raw("translate_usage", json.dumps(u))


def remaining() -> int:
    return max(0, store.cfg("translate_monthly_limit") - usage()["chars"])


def feed() -> dict:
    try:
        f = json.loads(store.get_raw("translate_feed") or "{}")
    except ValueError:
        f = {}
    return {"channel_id": f.get("channel_id"), "sources": [int(x) for x in f.get("sources", []) if str(x).isdigit()],
            "target": f.get("target") or "en"}


def save_feed(channel_id, sources, target):
    store.put_raw("translate_feed", json.dumps({"channel_id": int(channel_id) if channel_id else None,
                                                "sources": [int(x) for x in sources][:50], "target": target or "en"}))


async def _call(q: List[str], target: str, fmt: str = "text", source: Optional[str] = None) -> List[dict]:
    if not key():
        raise TranslateError("No Google translation key is set (dashboard → Translations).")
    n = sum(len(x) for x in q)
    if n > remaining():
        raise TranslateError("This month's translation limit is reached (dashboard → Translations).")
    body = {"q": q, "target": google_code(target), "format": fmt}
    if source:
        body["source"] = google_code(source)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.post(URL, params={"key": key()}, json=body) as r:
            j = await r.json(content_type=None)
    if r.status != 200:
        msg = (j.get("error") or {}).get("message") if isinstance(j, dict) else None
        raise TranslateError(f"Google refused the translation ({r.status}): {msg or 'unknown error'}")
    _count(n)
    return j["data"]["translations"]


async def text(t: str, target: str) -> Tuple[str, str]:
    """Translate a chat message. Returns (translated text, detected source language). Cached."""
    t = (t or "").strip()[:2000]
    h = hashlib.sha1(f"{google_code(target)}\0{t}".encode()).hexdigest()
    row = store.db.execute("SELECT out, src FROM translate_cache WHERE h=?", (h,)).fetchone()
    if row:
        return row["out"], row["src"] or ""
    res = (await _call([t], target))[0]
    out, src = html.unescape(res["translatedText"]), res.get("detectedSourceLanguage", "")
    store.db.execute("INSERT OR REPLACE INTO translate_cache (h, out, src, ts) VALUES (?,?,?,?)", (h, out, src, int(time.time())))
    store.db.execute("DELETE FROM translate_cache WHERE ts < ?", (int(time.time()) - 30 * 86400,))
    store.db.commit()
    return out, src


def same_language(src: str, target: str) -> bool:
    return (src or "").lower().split("-")[0] == google_code(target).lower().split("-")[0]


# ------------------------------------------------------------- bot texts
_KEEP = re.compile(r"(\{\w+\}|`[^`]*`|&lt;(?:[@#][!&]?\d+|a?:\w+:\d+|t:\d+(?::\w)?)&gt;|(?<![\w/])/[a-z]+\b)")


def _to_html(s: str) -> str:
    s = html.escape(s, quote=False)
    s = _KEEP.sub(lambda m: f'<span class="notranslate">{m.group(0)}</span>', s)     # before adding any tags
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    return s.replace("\n", "<br>")


def _from_html(s: str) -> str:
    s = re.sub(r"\s*<br\s*/?>\s*", "\n", s)
    s = re.sub(r'<span class="notranslate">(.*?)</span>', r"\1", s)
    s = re.sub(r"<b>\s*(.+?)\s*</b>", r"**\1**", s)
    s = re.sub(r"<i>\s*(.+?)\s*</i>", r"*\1*", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def auto_texts() -> dict:
    try:
        d = json.loads(store.get_raw("translations_auto") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


async def fill_language(lang: str) -> int:
    """Machine-translate every bot text that has no translation in this language yet. Returns how many."""
    if lang == "en":
        return 0
    over, auto = store.translations(), auto_texts()
    have = set((over.get(lang) or {})) | set(texts.DEFAULTS.get(lang, {})) | set(auto.get(lang) or {})
    todo = [k for k in texts.KEYS if k not in have]
    done = 0
    for i in range(0, len(todo), 60):
        chunk = todo[i:i + 60]
        res = await _call([_to_html(store.T(k, "en")) for k in chunk], lang, fmt="html", source="en")
        mine = auto.setdefault(lang, {})
        for k, r in zip(chunk, res):
            t = _from_html(r["translatedText"])
            if texts.check_translation(k, t) is None:      # never keep a text with broken {placeholders}
                mine[k] = t
                done += 1
        store.put_raw("translations_auto", texts.dumps(auto))
    return done
