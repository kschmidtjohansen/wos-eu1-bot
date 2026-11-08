"""WOS EU1 – reaction-based bug & suggestion bot with a web dashboard API.

Players:   react 🐛 (Report a bug) or 💡 (Create a suggestion) on the panel message.
           🐛  -> a private ticket thread opens with the bug questions. The player
                  answers in one message, gets a preview and presses
                  Submit (send to mods + close ticket) / Edit (restart, old text
                  shown for copy/paste) / Delete (discard + close ticket).
           💡  -> a private ticket thread opens; the player writes the suggestion freehand,
                  gets the same preview (Submit / Edit / Delete) and it goes to the MOD channel.
Players:   /mybugs shows the status of their own reports.
Everyone:  /members, /mybugs, /codes, /link, /unlink
Admins:    /digest (weekly summary now)
Mods:      /duplicate, /reply, /block, /unblock, /blocklist; react on a bug report:  ✅ Pass  |  🔍 Investigate  |  ❌ Duplicate/Reject
           react on a suggestion (mod channel):  ✅ Accept (also posted in the suggestions channel)  |  ❌ Reject
Admins:    react on a bug in the admin channel:  🔍 Being investigated | ✅ Fixed | ❌ Rejected
           /patchnotes publish posts everything fixed since the last time.
Reminders: a digest is posted when bugs wait too long for mods / admins.
Welcome:   new members are greeted (and tagged) in the welcome channel; edit the text with
           /welcome message or on the dashboard.
           manage everything from the dashboard page (see api.py / dashboard.html),
           or run /setup_panel in Discord.

Channels, toggles and limits are stored in the database and edited from the dashboard.
"""
import asyncio
from datetime import timedelta
import json
import re
import base64
import math
import os
import gzip
import hashlib
import io
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

import store
from store import (cfg, db, get_bug, get_bug_by_admin_msg, get_bug_by_msg, get_raw, get_suggestion,
                   get_suggestion_by_msg, get_ticket, log_event, put_raw, render_welcome, set_bug,
                   set_cfg, set_suggestion, set_ticket)
from store import record_action
import texts
import drive
import translate


def BACKUP_PATH(name):  # noqa: N802
    return store.BACKUP_DIR / name

BOT_NAME = "WOS EU1"  # fallback; the live bot username is used when available
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
MOD_ROLE_ID = int(os.environ["MOD_ROLE_ID"])
ADMIN_ROLE_ID = int(os.environ["ADMIN_ROLE_ID"])

DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "").strip()
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1").strip()
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
DASHBOARD_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("DASHBOARD_ORIGINS", "").split(",") if o.strip()]

BUG_EMOJI = "🐛"
SUGGEST_EMOJI = "💡"
PASS_EMOJI = "✅"
INVESTIGATE_EMOJI = "🔍"
REJECT_EMOJI = "❌"
# admin channel (same symbols, different channel): 🔍 being investigated · ✅ fixed · ❌ rejected
ADMIN_INVESTIGATING_EMOJI, ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI = "🔍", "✅", "❌"
# suggestions channel: ✅ accept · ❌ reject (moderators and admins only)
SUGGEST_ACCEPT_EMOJI, SUGGEST_REJECT_EMOJI = "✅", "❌"



class NotConfigured(Exception):
    """A channel needed for this action has not been selected on the dashboard."""


# ------------------------------------------------------------------ helpers
def brand() -> str:
    return bot.user.name if bot.user else BOT_NAME


def chan(key: str):
    cid = cfg(key)
    return bot.get_channel(cid) if cid else None


STATUS = {
    "new": ("🆕 New", 0x5865F2),
    "investigating": ("🔍 Investigating – ticket open", 0xFEE75C),
    "passed": ("✅ Passed to admins", 0x57F287),
    "rejected": ("❌ Rejected – duplicate / working as intended", 0xED4245),
    "being_investigated": ("🔍 Being investigated by the admins", 0xE67E22),
    "fixed": ("✔️ Fixed", 0x2ECC71),
    "admin_rejected": ("❌ Rejected by the admins", 0xED4245),
}
MOD_OPEN = ("new", "investigating")          # moderators can only act on these
ADMIN_OPEN = ("passed", "being_investigated")  # admins can act on these


SEVERITY = {"critical": "🚨 Critical", "major": "🟠 Major", "minor": "🟡 Minor"}


def bug_embed(r, note: Optional[str] = None) -> discord.Embed:
    label, color = STATUS[r["status"]]
    e = discord.Embed(title=f"Bug #{r['id']} – {store.bug_summary(r)}"[:256], description=r["text"][:4000], color=color)
    if r["extra"]:
        e.add_field(name="Attachment links", value=r["extra"], inline=False)
    e.add_field(name="Reporter", value=(f"<@{r['reporter_id']}>" if r["reporter_id"]
                                        else f"{r['chief_name'] or 'Unknown'} (website)"), inline=True)
    e.add_field(name="Status", value=label, inline=True)
    if r["severity"]:
        e.add_field(name="Severity", value=SEVERITY[r["severity"]], inline=True)
    if r["area"]:
        e.add_field(name="Area", value=r["area"], inline=True)
    if r["game_version"]:
        e.add_field(name="Game version", value=r["game_version"], inline=True)
    if r["chief_name"] or r["player_uid"]:
        e.add_field(name="In-game account", value=" · ".join(
            x for x in (r["chief_name"], f"UID {r['player_uid']}" if r["player_uid"] else None) if x), inline=True)
    if r["contact"]:
        e.add_field(name="Contact", value=r["contact"][:200], inline=True)
    if r["duplicate_of"]:
        e.add_field(name="Duplicate of", value=f"Bug #{r['duplicate_of']}", inline=True)
    if r["status"] in ("rejected", "admin_rejected") and r["reject_reason"]:
        e.add_field(name="💬 Reason (sent to the player)", value=r["reject_reason"][:1024], inline=False)
    if r["dup_suggest"] and r["status"] in store.OPEN_STATUSES and not r["duplicate_of"]:
        e.add_field(name="🔁 Looks like a duplicate", value=f"of bug #{r['dup_suggest']} ({r['dup_score']}% match)", inline=False)
    votes = store.vote_count(r["id"])
    if votes:
        e.add_field(name="👥 Players affected", value=f"{votes} more said “I have this too”", inline=True)
    dups = store.duplicates_of(r["id"])
    if dups:
        shown = ", ".join(f"#{d['id']}" for d in dups[:8]) + (f" +{len(dups) - 8}" if len(dups) > 8 else "")
        e.add_field(name=f"Also reported by {len(dups)} more", value=shown, inline=True)
    e.set_footer(text=brand() + (f" · {note}" if note else ""))
    return e


def has_role(user, role_id: int) -> bool:
    return isinstance(user, discord.Member) and any(x.id == role_id for x in user.roles)


def is_mod(user) -> bool:
    return has_role(user, MOD_ROLE_ID) or has_role(user, ADMIN_ROLE_ID)


def is_admin(user) -> bool:
    return has_role(user, ADMIN_ROLE_ID) or (
        isinstance(user, discord.Member) and user.guild_permissions.administrator)


async def dm(user_id: int, text: str):
    """DM a reporter about their report (can be switched off on the dashboard)."""
    if not cfg("dm_notifications") or not user_id:   # reporter_id 0 = report from the website
        return
    try:
        user = await bot.fetch_user(user_id)
        await user.send(text)
    except discord.HTTPException:
        pass


T, lang_for = store.T, store.lang_for


def ilang(interaction: discord.Interaction) -> str:
    """The player's language, learning it from their Discord settings the first time."""
    try:
        store.remember_locale(interaction.user.id, interaction.locale)
    except Exception:  # noqa: BLE001
        pass
    return lang_for(interaction.user.id)


async def dm_t(user_id: int, key: str, reason: Optional[str] = None, **kw):
    """DM a player in their own language (texts.py / the dashboard's Translations page). A reason from the
    team (why a report was closed) is added underneath."""
    if not user_id:
        return
    lg = lang_for(user_id)
    await dm(user_id, T(key, lg, **kw) + (T("dm.reason", lg, reason=reason[:900]) if reason else ""))


# ------------------------------------------- player: bug ticket (private thread)
def questions_embed(edit: bool = False, kind: str = "bug", lang: Optional[str] = None) -> discord.Embed:
    if kind == "suggestion":
        return discord.Embed(title=T("sug.title_edit" if edit else "sug.title", lang),
                             description=T("sug.body", lang, max=cfg("suggestion_max_length")), color=0xFEE75C)
    e = discord.Embed(title=T("bug.title_edit" if edit else "bug.title", lang),
                      description=T("bug.body", lang, again=T("bug.again", lang) if edit else ""), color=0x5865F2)
    e.add_field(name=T("bug.template_name", lang), value=f"```\n{T('bug.template', lang)}\n```", inline=False)
    return e


def preview_embed(text: str, atts, t=None, lang: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=T("pv.title", lang), description=text or T("pv.no_text", lang), color=0xFEE75C)
    if t is not None and t["kind"] == "bug":
        e.add_field(name=T("pv.f_title", lang), value=t["title"] or T("pv.f_title_missing", lang), inline=False)
        e.add_field(name=T("pv.f_area", lang), value=t["area"] or T("pv.f_area_missing", lang), inline=True)
        e.add_field(name=T("pv.f_version", lang), value=t["game_version"] or "–", inline=True)
        e.add_field(name=T("pv.f_chief", lang), value=f"{t['chief_name'] or '–'} · {t['player_uid'] or '–'}", inline=True)
    if atts:
        e.add_field(name=T("pv.f_files", lang), value="\n".join(f"📎 {a.filename}" for a in atts)[:1000], inline=False)
    e.set_footer(text=T("pv.footer", lang))
    return e


async def refresh_preview(thread, thread_id: int):
    """Re-draw the preview after the player changed the area or details."""
    t = get_ticket(thread_id)
    if t is None or t["state"] != "preview" or not t["preview_msg_id"] or not t["draft_msg_id"]:
        return
    try:
        draft = await thread.fetch_message(t["draft_msg_id"])
        await thread.get_partial_message(t["preview_msg_id"]).edit(
            embed=preview_embed(draft.content.strip(), draft.attachments, t, lang_for(t["user_id"])))
    except discord.HTTPException:
        pass


async def close_ticket(thread: discord.Thread, keep: bool = False):
    """Mark ticket closed and remove the thread (or archive+lock it if keep=True)."""
    set_ticket(thread.id, closed=1)
    if not keep:
        try:
            await thread.delete()
            return
        except discord.HTTPException:
            pass
    try:
        await thread.edit(archived=True, locked=True)
    except discord.HTTPException:
        pass


async def get_open_ticket_thread(user_id: int) -> Optional[discord.Thread]:
    """The player's open ticket thread, or None (stale tickets are cleaned up)."""
    row = db.execute("SELECT * FROM tickets WHERE user_id=? AND closed=0", (user_id,)).fetchone()
    if row is None:
        return None
    try:
        thread = await bot.fetch_channel(row["thread_id"])
    except discord.HTTPException:          # deleted by someone
        set_ticket(row["thread_id"], closed=1)
        return None
    if getattr(thread, "archived", False):  # went idle and was archived
        await close_ticket(thread)
        return None
    return thread


def report_gate(member, kind: str, lg: Optional[str]) -> Optional[str]:
    """Why this player can't report right now (blocked, switched off, cooldown…), or None."""
    blk = store.get_block(member.id)
    if blk:
        when = T("open.until", lg, time=f"<t:{blk['until']}:f>") if blk["until"] else ""
        return T("open.blocked", lg, until=when)
    if not cfg("bugs_enabled" if kind == "bug" else "suggestions_enabled"):
        return T("open.bugs_off" if kind == "bug" else "open.sugs_off", lg)
    if chan("mod_channel_id") is None:
        return T("open.not_setup", lg)
    cooldown = cfg("report_cooldown_minutes") if kind == "bug" else 0
    if cooldown:
        last = db.execute("SELECT MAX(created_at) AS m FROM bug_reports WHERE reporter_id=?",
                          (member.id,)).fetchone()["m"]
        if last and time.time() - last < cooldown * 60:
            wait = math.ceil((cooldown * 60 - (time.time() - last)) / 60)
            return T("open.cooldown", lg, minutes=wait)
    return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


async def open_ticket(member: discord.Member, channel: discord.TextChannel, kind: str = "bug"):
    lg = lang_for(member.id)

    async def tell(msg: str):
        await channel.send(f"{member.mention} {msg}", delete_after=15)

    why = report_gate(member, kind, lg)
    if why:
        return await tell(why)
    existing = await get_open_ticket_thread(member.id)
    if existing:
        return await tell(T("open.exists", lg, ticket=existing.mention))

    thread = await channel.create_thread(
        name=f"{'bug' if kind == 'bug' else 'idea'}-{member.display_name}"[:90],
        type=discord.ChannelType.private_thread,
        invitable=False, auto_archive_duration=1440,
    )
    await thread.add_user(member)
    db.execute("INSERT INTO tickets (thread_id, user_id, kind) VALUES (?,?,?)", (thread.id, member.id, kind))
    db.commit()
    log_event("ticket", f"{member.display_name} opened a {kind} ticket", member.id, member.display_name)
    if kind == "bug":
        link = store.get_link(member.id)
        set_ticket(thread.id, game_version=cfg("game_version") or None,
                   chief_name=link["chief_name"] if link else None, player_uid=link["player_uid"] if link else None)
    view = DetailsView(lg) if kind == "bug" else (LangView(lg) if len(store.languages()) > 1 else None)
    await thread.send(content=member.mention, embed=questions_embed(kind=kind, lang=lg), view=view)
    await channel.send(f"{member.mention} {T('open.ready', lg, ticket=thread.mention)}", delete_after=15)


async def handle_ticket_message(message: discord.Message):
    """The player sent their answers (or a new version) in the ticket -> show a preview."""
    t = get_ticket(message.channel.id)
    if t is None or t["closed"] or message.author.id != t["user_id"]:
        return
    if t["state"] not in ("waiting", "preview"):
        return
    text = message.content.strip()
    if not text and not message.attachments:
        return
    # a newer message replaces the previous preview
    if t["preview_msg_id"]:
        try:
            await message.channel.get_partial_message(t["preview_msg_id"]).edit(view=None)
        except discord.HTTPException:
            pass
    lg = lang_for(message.author.id)
    preview = await message.reply(embed=preview_embed(text, message.attachments, t, lg),
                                  view=PreviewView(lg), mention_author=False)
    set_ticket(message.channel.id, state="preview", draft_msg_id=message.id,
               preview_msg_id=preview.id)


async def _owner_guard(interaction: discord.Interaction) -> bool:
    t = get_ticket(interaction.channel_id)
    lg = ilang(interaction)
    if t is None or t["closed"]:
        msg = T("guard.closed", lg)
    elif interaction.user.id != t["user_id"]:
        msg = T("guard.owner", lg)
    else:
        return True
    await interaction.response.send_message(msg, ephemeral=True)
    return False


class DetailsModal(discord.ui.Modal, title="Bug details"):
    """Title, chief name, UID and version – built per player so it is in their language."""

    def __init__(self, t, lang: Optional[str] = None):
        super().__init__(title=T("modal.title", lang)[:45])
        self.lang = lang
        required = bool(cfg("ticket_details_required"))
        self.title_in = discord.ui.TextInput(placeholder=T("modal.short_ph", lang)[:100], max_length=100,
                                             min_length=5 if required else None, required=required, default=t["title"])
        self.chief = discord.ui.TextInput(required=False, max_length=50, default=t["chief_name"])
        self.uid = discord.ui.TextInput(required=False, max_length=20, default=t["player_uid"])
        self.version = discord.ui.TextInput(required=False, max_length=30, default=t["game_version"] or cfg("game_version") or None)
        for key, item in (("modal.short", self.title_in), ("modal.chief", self.chief), ("modal.uid", self.uid),
                          ("modal.version", self.version)):
            self.add_item(discord.ui.Label(text=T(key, lang)[:45], component=item))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            uid = store.clean_uid(str(self.uid))
        except ValueError:
            return await interaction.response.send_message(T("modal.uid_bad", self.lang), ephemeral=True)
        set_ticket(interaction.channel_id, title=str(self.title_in).strip() or None,
                   chief_name=str(self.chief).strip() or None, player_uid=uid or None,
                   game_version=str(self.version).strip() or None)
        lg = self.lang
        text = T("modal.saved", lg)
        similar = store.similar_bugs(str(self.title_in), area=None)
        if similar:
            text += ("\n\n" + T("modal.similar", lg) + "\n"
                     + "\n".join(f"• {s['title']} – *{T('st.' + s['status'], lg)}*"
                                  + (T("modal.similar_fixed", lg, version=s["fixed_in"]) if s["fixed_in"] else "") for s in similar)
                     + "\n" + T("modal.similar_tail", lg))
        await interaction.response.send_message(text, ephemeral=True)
        await refresh_preview(interaction.channel, interaction.channel_id)


class DetailsView(discord.ui.View):
    """Area menu + 'Add details' button under the questions (persistent across restarts)."""

    def __init__(self, lang: Optional[str] = None):
        super().__init__(timeout=None)
        areas = store.area_list()[:25] or ["Other"]
        self.area.options = [discord.SelectOption(label=a) for a in areas]
        self.area.placeholder = T("bug.area_ph", lang)[:150]
        self.details.label = T("bug.details_btn", lang)[:80]
        if len(store.languages()) > 1 or lang is None:      # lang None = the copy registered for restarts
            self.add_item(LangSelect(lang))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_guard(interaction)

    @discord.ui.select(placeholder="Where is the problem? (area)", custom_id="ticket:area",
                       options=[discord.SelectOption(label="Other")])
    async def area(self, interaction: discord.Interaction, select: discord.ui.Select):
        value, lg = select.values[0], ilang(interaction)
        if value not in store.area_list():
            return await interaction.response.send_message(T("bug.area_gone", lg), ephemeral=True)
        set_ticket(interaction.channel_id, area=value)
        await interaction.response.send_message(T("bug.area_set", lg, area=value), ephemeral=True)
        await refresh_preview(interaction.channel, interaction.channel_id)

    @discord.ui.button(label="Add details", style=discord.ButtonStyle.secondary, custom_id="ticket:details")
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DetailsModal(get_ticket(interaction.channel_id), ilang(interaction)))


class LangSelect(discord.ui.Select):
    """🌍 Language menu under the ticket questions: re-writes the ticket in the chosen language."""

    def __init__(self, lang: Optional[str] = None):
        langs = store.languages()
        cur = lang or store.default_language()
        super().__init__(custom_id="ticket:lang", placeholder=T("open.language", lang)[:150], row=2,
                         options=[discord.SelectOption(label=texts.lang_name(x), value=x, default=(x == cur)) for x in langs[:25]])

    async def callback(self, interaction: discord.Interaction):
        t = get_ticket(interaction.channel_id)
        lg0 = ilang(interaction)
        if t is None or t["closed"] or interaction.user.id != t["user_id"]:
            return await interaction.response.send_message(T("guard.owner" if t else "guard.closed", lg0), ephemeral=True)
        lg = self.values[0] if self.values[0] in store.languages() else store.default_language()
        store.set_user_language(interaction.user.id, lg)
        view = DetailsView(lg) if t["kind"] == "bug" else LangView(lg)
        await interaction.response.edit_message(embed=questions_embed(kind=t["kind"], lang=lg), view=view)
        await interaction.followup.send(T("open.language_set", lg), ephemeral=True)
        await refresh_preview(interaction.channel, interaction.channel_id)


class LangView(discord.ui.View):
    """Only the language menu (suggestion tickets)."""

    def __init__(self, lang: Optional[str] = None):
        super().__init__(timeout=None)
        self.add_item(LangSelect(lang))


class PreviewView(discord.ui.View):
    """Submit / Edit / Delete buttons under the preview (persistent across restarts)."""

    def __init__(self, lang: Optional[str] = None):
        super().__init__(timeout=None)
        self.submit.label, self.edit.label, self.delete.label = (T("pv.submit", lang)[:80], T("pv.edit", lang)[:80],
                                                                 T("pv.delete", lang)[:80])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        t = get_ticket(interaction.channel_id)
        lg = ilang(interaction)
        if t is None or t["closed"]:
            msg = T("guard.closed", lg)
        elif interaction.user.id != t["user_id"]:
            msg = T("guard.owner", lg)
        elif t["state"] != "preview" or t["preview_msg_id"] != interaction.message.id:
            msg = T("guard.outdated", lg)
        else:
            return True
        await interaction.response.send_message(msg, ephemeral=True)
        return False

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success, custom_id="ticket:submit")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        lg = ilang(interaction)
        if t["kind"] == "bug" and cfg("ticket_details_required"):
            missing = [T(x, lg) for x, v in (("sub.need_area", t["area"]), ("sub.need_title", t["title"])) if not v]
            if missing:
                return await interaction.response.send_message(T("sub.need", lg, missing=T("sub.and", lg).join(missing)),
                                                               ephemeral=True)
        set_ticket(t["thread_id"], state="submitting")   # blocks double clicks
        await interaction.response.defer()
        thread = interaction.channel
        if store.get_block(interaction.user.id):
            set_ticket(t["thread_id"], state="preview")
            await thread.send(T("sub.blocked", lg))
            return
        try:
            draft = await thread.fetch_message(t["draft_msg_id"])
        except discord.NotFound:
            set_ticket(t["thread_id"], state="waiting")
            await interaction.message.edit(view=None)
            await thread.send(T("sub.lost", lg))
            return

        name = interaction.user.display_name
        if t["kind"] == "suggestion":
            text = draft.content.strip()
            if not text:
                set_ticket(t["thread_id"], state="preview")
                await thread.send(T("sub.sug_text", lg))
                return
            if len(text) > cfg("suggestion_max_length"):
                set_ticket(t["thread_id"], state="waiting")
                await interaction.message.edit(view=None)
                await thread.send(T("sub.sug_long", lg, length=len(text), max=cfg("suggestion_max_length")))
                return
            try:
                s = await submit_suggestion(interaction.user, text)
            except (NotConfigured, discord.HTTPException) as exc:
                set_ticket(t["thread_id"], state="preview")
                await thread.send(T("sub.sug_fail", lg))
                log_event("system", f"Could not deliver a suggestion from {name}: {exc}",
                          interaction.user.id, name)
                return
            log_event("suggestion", f"Suggestion #{s['id']} submitted by {name}", interaction.user.id, name)
            done = interaction.message.embeds[0]
            done.title = T("sub.sug_done_title", lg, id=s["id"])
            done.colour = discord.Colour(0x57F287)
            await interaction.message.edit(embed=done, view=None)
            await thread.send(T("sub.sug_done", lg, id=s["id"]))
            await asyncio.sleep(5)
            await close_ticket(thread, keep=not cfg("delete_ticket_on_close"))
            return
        try:
            r, used_links = await submit_bug(interaction.user, draft.content.strip(), draft.attachments[:4], t)
        except (NotConfigured, discord.HTTPException) as exc:
            set_ticket(t["thread_id"], state="preview")  # let them press Submit again
            await thread.send(T("sub.bug_fail", lg))
            log_event("system", f"Could not deliver a bug report from {name}: {exc}",
                      interaction.user.id, name)
            return
        log_event("bug", f"Bug #{r['id']} submitted by {name}", interaction.user.id, name)
        await safe_check_duplicate(r["id"])

        done = interaction.message.embeds[0]
        done.title = T("sub.bug_done_title", lg, id=r["id"])
        done.colour = discord.Colour(0x57F287)
        await interaction.message.edit(embed=done, view=None)
        await thread.send(T("sub.bug_done", lg, id=r["id"]))
        await asyncio.sleep(5)
        await close_ticket(thread, keep=used_links or not cfg("delete_ticket_on_close"))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, custom_id="ticket:edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        set_ticket(t["thread_id"], state="waiting")
        previous = ""
        if interaction.message.embeds:
            previous = interaction.message.embeds[0].description or ""
        lg = ilang(interaction)
        if previous in ("*(no text)*", T("pv.no_text", lg)):
            previous = ""
        await interaction.response.edit_message(view=None)
        embeds = [questions_embed(edit=True, kind=t['kind'], lang=lg)]
        if previous:
            safe = previous.replace("```", "`​``")[:4000]
            embeds.append(discord.Embed(
                title=T("pv.previous", lg),
                description=f"```\n{safe}\n```", color=0x99AAB5))
        await interaction.channel.send(embeds=embeds)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket:delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        set_ticket(t["thread_id"], state="closing")
        await interaction.response.send_message(T("sub.discarded", ilang(interaction)))
        log_event("ticket", f"{interaction.user.display_name} discarded their {t['kind']} draft",
                  interaction.user.id, interaction.user.display_name)
        await asyncio.sleep(3)
        await close_ticket(interaction.channel)


# ------------------------------------------- player: report form (buttons on the panel)
def split_who(text: str):
    """'Ragnar 123456789' -> ('Ragnar', '123456789'). Raises ValueError for a UID that isn't a number."""
    import re as _re
    text = (text or "").strip()
    m = _re.search(r"(?<!\w)\d[\d\s]{2,24}\d(?!\w)", text)
    uid = store.clean_uid(m.group(0)) if m else ""
    name = (text[:m.start()] + text[m.end():]).strip(" ,;·-/|") if m else text
    if not m and _re.search(r"\d{4,}", text):
        raise ValueError("uid")
    return name[:50] or None, uid or None


class BugFormModal(discord.ui.Modal):
    """The whole bug report in one form: area, title, what happened, chief/UID and screenshots."""

    def __init__(self, lg: Optional[str], link=None):
        super().__init__(title=T("form.bug_title", lg)[:45], timeout=1800)
        self.lg = lg
        areas = store.area_list()[:25] or ["Other"]
        required = bool(cfg("ticket_details_required"))
        self.area = discord.ui.Select(placeholder=T("form.area_ph", lg)[:150], required=required, min_values=1 if required else 0,
                                      options=[discord.SelectOption(label=a) for a in areas])
        self.title_in = discord.ui.TextInput(placeholder=T("form.title_ph", lg)[:100], max_length=100,
                                             min_length=5 if required else None, required=required)
        self.desc = discord.ui.TextInput(style=discord.TextStyle.paragraph, min_length=10, max_length=1800)
        who = " ".join(x for x in ((link["chief_name"] if link else None), (link["player_uid"] if link else None)) if x)
        self.who = discord.ui.TextInput(placeholder=T("form.who_ph", lg)[:100], required=False, max_length=80, default=who or None)
        self.files = discord.ui.FileUpload(required=False, min_values=0, max_values=4)
        self.add_item(discord.ui.Label(text=T("form.area", lg)[:45], component=self.area))
        self.add_item(discord.ui.Label(text=T("form.title", lg)[:45], component=self.title_in))
        self.add_item(discord.ui.Label(text=T("form.desc", lg)[:45], description=T("form.desc_hint", lg)[:100], component=self.desc))
        self.add_item(discord.ui.Label(text=T("form.who", lg)[:45], component=self.who))
        self.add_item(discord.ui.Label(text=T("form.files", lg)[:45], description=T("form.files_hint", lg)[:100], component=self.files))

    async def on_submit(self, interaction: discord.Interaction):
        lg = self.lg
        try:
            chief, uid = split_who(str(self.who.value or ""))
        except ValueError:
            return await interaction.response.send_message(T("form.uid_bad", lg), ephemeral=True)
        title = str(self.title_in.value or "").strip()
        if cfg("ticket_details_required") and len(title) < 5:
            return await interaction.response.send_message(T("form.need_title", lg), ephemeral=True)
        report = {"title": title or None, "area": (self.area.values or [None])[0], "game_version": cfg("game_version") or None,
                  "chief_name": chief, "player_uid": uid, "text": str(self.desc.value).strip(), "files": list(self.files.values)[:4]}
        similar = store.similar_bugs(title, area=None) if title else []
        if similar:
            text = (T("form.similar", lg) + "\n" + "\n".join(
                f"• {s['title']} – *{T('st.' + s['status'], lg)}*" + (T("modal.similar_fixed", lg, version=s["fixed_in"]) if s["fixed_in"] else "")
                for s in similar) + "\n\n" + T("form.similar_tail", lg))
            return await interaction.response.send_message(text, view=SimilarView(lg, report, similar), ephemeral=True)
        await interaction.response.send_message(T("form.sending", lg), ephemeral=True)
        await send_form_report(interaction, report, lg)


async def send_form_report(interaction: discord.Interaction, report: dict, lg: Optional[str]):
    """Post a report from the form in the mod channel and tell the player (edits the ephemeral reply)."""
    member = interaction.user
    why = report_gate(member, "bug", lg)
    if why:
        return await interaction.edit_original_response(content=_cap(why), view=None)
    try:
        r, _ = await submit_bug(member, report["text"], report["files"], report)
    except (NotConfigured, discord.HTTPException) as exc:
        log_event("system", f"Could not deliver a bug report from {member.display_name}: {exc}", member.id, member.display_name)
        return await interaction.edit_original_response(content=T("sub.bug_fail", lg).replace("**Submit**", "**" + T("form.bug_btn", lg) + "**"), view=None)
    log_event("bug", f"Bug #{r['id']} submitted by {member.display_name} (form)", member.id, member.display_name)
    done = T("form.bug_done", lg, id=r["id"])
    if await safe_check_duplicate(r["id"]) == "auto":
        dup = get_bug(r["id"])
        done = T("form.bug_dup", lg, id=r["id"], original=dup["duplicate_of"])
    if report.get("player_uid") and store.get_link(member.id) is None:
        try:
            store.set_link(member.id, report["player_uid"], report.get("chief_name") or "")
            log_event("link", f"{member.display_name} linked to UID {report['player_uid']} (from a bug report)",
                      member.id, member.display_name)
            done += "\n" + T("link.saved", lg)
            start_redeem()
        except ValueError:
            pass
    await interaction.edit_original_response(content=done, view=None)


class SimilarView(discord.ui.View):
    """'This may already be reported' – send anyway or drop it."""

    def __init__(self, lg, report, similar=None):
        super().__init__(timeout=900)
        self.lg, self.report = lg, report
        self.send.label, self.drop.label = T("form.send_anyway", lg)[:80], T("form.cancel", lg)[:80]
        if any(s["status"] in ("passed", "being_investigated") for s in (similar or [])):
            self.add_item(SimilarVoteSelect(lg, similar, self))

    @discord.ui.button(style=discord.ButtonStyle.primary, label="Send anyway")
    async def send(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=T("form.sending", self.lg), view=None)
        await send_form_report(interaction, self.report, self.lg)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.secondary, label="Don't send")
    async def drop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=T("form.cancelled", self.lg), view=None)
        self.stop()


class IdeaFormModal(discord.ui.Modal):
    def __init__(self, lg: Optional[str]):
        super().__init__(title=T("form.idea_title", lg)[:45], timeout=1800)
        self.lg = lg
        self.idea = discord.ui.TextInput(style=discord.TextStyle.paragraph, min_length=10,
                                         max_length=min(4000, cfg("suggestion_max_length")))
        self.add_item(discord.ui.Label(text=T("form.idea", lg)[:45], description=T("form.idea_hint", lg)[:100], component=self.idea))

    async def on_submit(self, interaction: discord.Interaction):
        lg, member = self.lg, interaction.user
        why = report_gate(member, "suggestion", lg)
        if why:
            return await interaction.response.send_message(_cap(why), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            s = await submit_suggestion(member, str(self.idea.value).strip())
        except (NotConfigured, discord.HTTPException) as exc:
            log_event("system", f"Could not deliver a suggestion from {member.display_name}: {exc}", member.id, member.display_name)
            return await interaction.followup.send(T("sub.sug_fail", lg), ephemeral=True)
        log_event("suggestion", f"Suggestion #{s['id']} submitted by {member.display_name} (form)", member.id, member.display_name)
        await interaction.followup.send(T("form.idea_done", lg, id=s["id"]), ephemeral=True)


class PanelView(discord.ui.View):
    """The two buttons under the panel message (persistent across restarts)."""

    def __init__(self, lg: Optional[str] = None):
        super().__init__(timeout=None)
        self.bug.label, self.idea.label = T("form.bug_btn", lg)[:80], T("form.idea_btn", lg)[:80]

    async def _start(self, interaction: discord.Interaction, kind: str):
        lg = ilang(interaction)
        member = interaction.user
        why = report_gate(member, kind, lg)
        if why:
            return await interaction.response.send_message(_cap(why), ephemeral=True)
        if cfg("report_style") == "ticket":            # the older step-by-step ticket, if the admins prefer it
            await interaction.response.defer(ephemeral=True, thinking=True)
            existing = await get_open_ticket_thread(member.id)
            if existing:
                return await interaction.followup.send(_cap(T("open.exists", lg, ticket=existing.mention)), ephemeral=True)
            await open_ticket(member, interaction.channel, kind)
            return await interaction.followup.send("✅", ephemeral=True)
        if kind == "bug":
            await interaction.response.send_modal(BugFormModal(lg, store.get_link(member.id)))
        else:
            await interaction.response.send_modal(IdeaFormModal(lg))

    @discord.ui.button(emoji=BUG_EMOJI, label="Report a bug", style=discord.ButtonStyle.primary, custom_id="panel:bug")
    async def bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "bug")

    @discord.ui.button(emoji=SUGGEST_EMOJI, label="Suggest an idea", style=discord.ButtonStyle.secondary, custom_id="panel:idea")
    async def idea(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "suggestion")


async def refresh_panel_buttons():
    """Once after updating: give the existing panel its buttons (instead of reactions) and the new default text."""
    if get_raw("panel_buttons") == "1":
        return
    ch_id, msg_id = get_raw("panel_channel_id"), get_raw("panel_msg_id")
    if cfg("panel_text") == store.OLD_PANEL_TEXT:
        set_cfg("panel_text", store.DEFAULT_PANEL_TEXT)
    ch = bot.get_channel(int(ch_id)) if ch_id else None
    if ch is not None and msg_id:
        try:
            msg = await ch.fetch_message(int(msg_id))
            await msg.edit(embed=panel_embed(cfg("panel_title"), cfg("panel_text")), view=PanelView(store.default_language()))
            try:
                await msg.clear_reactions()
            except discord.HTTPException:
                pass
            log_event("panel", "Panel message now has buttons (Report a bug / Suggest an idea)", None, brand())
        except discord.HTTPException as exc:
            log_event("system", f"Could not add buttons to the panel message: {exc}", None, brand())
            return
    put_raw("panel_buttons", "1")


async def keep_files(bug_id: int, atts):
    """Save a copy of the screenshots/videos (Discord links expire). Never blocks the report."""
    for a in atts:
        try:
            store.save_file(bug_id, a.filename, await a.read())
        except Exception as exc:  # noqa: BLE001
            log_event("system", f"Could not keep attachment {getattr(a, 'filename', '?')} of bug #{bug_id}: {exc}")


async def submit_bug(member: discord.Member, text: str, atts, details=None):
    """Store the report and post it in the mod channel. Returns (row, used_links)."""
    channel = chan("mod_channel_id")
    if channel is None:
        raise NotConfigured("mod channel")
    d = {k: (details[k] if details is not None else None) for k in ("title", "area", "game_version", "chief_name", "player_uid")}
    guild = member.guild
    small = [a for a in atts if a.size <= guild.filesize_limit]
    big = [a for a in atts if a.size > guild.filesize_limit]
    used_links = bool(big)
    extra = "\n".join(f"[{a.filename}]({a.url})" for a in big)[:1000] or None

    cur = db.execute(
        "INSERT INTO bug_reports (reporter_id, text, extra, created_at, reporter_name, summary, area, "
        "game_version, chief_name, player_uid) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (member.id, text or "*(no text)*", extra, int(time.time()), member.display_name,
         d["title"], d["area"], d["game_version"], d["chief_name"], d["player_uid"]),
    )
    db.commit()
    bug_id = cur.lastrowid
    r = get_bug(bug_id)

    def drop():
        db.execute("DELETE FROM bug_reports WHERE id=?", (bug_id,))
        db.commit()

    try:
        files = [await a.to_file() for a in small]
        msg = await channel.send(embed=bug_embed(r), files=files, view=DupView())
    except discord.Forbidden:
        drop()
        raise
    except discord.HTTPException:
        # upload failed -> fall back to links to the ticket's attachments
        used_links = True
        links = "\n".join(f"[{a.filename}]({a.url})" for a in atts)[:1000] or None
        set_bug(bug_id, extra=links)
        r = get_bug(bug_id)
        try:
            msg = await channel.send(embed=bug_embed(r), view=DupView())
        except discord.HTTPException:
            drop()
            raise

    set_bug(bug_id, msg_id=msg.id)
    emojis = [PASS_EMOJI] + ([INVESTIGATE_EMOJI] if cfg("investigate_enabled") else []) + [REJECT_EMOJI]
    for emoji in emojis:
        await msg.add_reaction(emoji)
    await keep_files(bug_id, atts)
    return get_bug(bug_id), used_links


# ---------------------------------------------------- player: suggestion flow
async def submit_suggestion(member: discord.Member, text: str):
    """Store the suggestion and post it in the mod channel for moderators to handle."""
    channel = chan("mod_channel_id")
    if channel is None:
        raise NotConfigured("mod channel")
    cur = db.execute("INSERT INTO suggestions (author_id, text, created_at, author_name) VALUES (?,?,?,?)",
                     (member.id, text, int(time.time()), member.display_name))
    db.commit()
    sid = cur.lastrowid
    try:
        msg = await channel.send(embed=suggestion_embed(store.get_suggestion(sid)), view=TranslateView())
    except discord.HTTPException:
        db.execute("DELETE FROM suggestions WHERE id=?", (sid,))
        db.commit()
        raise
    db.execute("UPDATE suggestions SET msg_id=? WHERE id=?", (msg.id, sid))
    db.commit()
    for e in (SUGGEST_ACCEPT_EMOJI, SUGGEST_REJECT_EMOJI):
        try:
            await msg.add_reaction(e)
        except discord.HTTPException:
            pass
    return store.get_suggestion(sid)


# --------------------------------------------------------- reaction handlers
async def handle_panel(payload: discord.RawReactionActionEvent, emoji: str):
    channel = bot.get_channel(payload.channel_id)
    member = payload.member
    # remove the player's reaction so the button can be pressed again
    try:
        await channel.get_partial_message(payload.message_id).remove_reaction(payload.emoji, member)
    except discord.HTTPException:
        pass
    if member is None:
        return

    await open_ticket(member, channel, "bug" if emoji == BUG_EMOJI else "suggestion")


async def handle_mod(payload: discord.RawReactionActionEvent, emoji: str):
    channel = bot.get_channel(payload.channel_id)
    partial = channel.get_partial_message(payload.message_id)
    r = get_bug_by_msg(payload.message_id)
    if r is None:
        return
    # always remove the mod's reaction: the bot's own reactions act as buttons
    try:
        await partial.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
    except discord.HTTPException:
        pass

    member = payload.member
    if member is None or not is_mod(member) or r["status"] not in MOD_OPEN:
        return
    msg = await partial.fetch()
    who = member.display_name

    if emoji == PASS_EMOJI:
        admin = chan("admin_channel_id")
        if admin is None:
            await channel.send("⚠️ The admin channel isn't selected yet – set it on the dashboard "
                               "before passing bugs on.", delete_after=15)
            return
        set_bug(r["id"], status="passed")
        record_action("bug", r["id"], "passed", member.id, who)
        r = get_bug(r["id"])
        note = f"Passed by {who}"
        try:
            files = [await a.to_file() for a in msg.attachments]
            admin_msg = await admin.send(embed=bug_embed(r, note), files=files, view=DupView())
        except discord.HTTPException:
            admin_msg = await admin.send(embed=bug_embed(r, note), view=DupView())
        set_bug(r["id"], admin_msg_id=admin_msg.id)
        for e in (ADMIN_INVESTIGATING_EMOJI, ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI):
            await admin_msg.add_reaction(e)
        await msg.edit(embed=bug_embed(r, note))
        await msg.clear_reactions()
        log_event("mod", f"Bug #{r['id']} passed to admins by {who}", member.id, who)
        if r["severity"] == "critical":
            await ping_critical(r, who)
        await dm_t(r["reporter_id"], "dm.passed", id=r["id"])

    elif emoji == REJECT_EMOJI:
        await prompt_reason(channel, "bug", r["id"], member, "rejected")

    elif emoji == INVESTIGATE_EMOJI:
        if not cfg("investigate_enabled") or r["ticket_thread_id"]:
            return  # switched off, or ticket already open
        if not r["reporter_id"]:
            await channel.send("🔍 This report came from the website, so there is no Discord account to open a "
                               "ticket with." + (f" Contact: {r['contact']}" if r["contact"] else ""), delete_after=20)
            return
        guild = channel.guild
        reporter = guild.get_member(r["reporter_id"])
        if reporter is None:
            try:
                reporter = await guild.fetch_member(r["reporter_id"])
            except discord.HTTPException:
                reporter = None
        thread = await channel.create_thread(
            name=f"ticket-bug-{r['id']}", type=discord.ChannelType.private_thread,
            invitable=False, auto_archive_duration=4320,
        )
        await thread.add_user(member)
        if reporter:
            await thread.add_user(reporter)
        mention = reporter.mention if reporter else f"<@{r['reporter_id']}> (left the server)"
        await thread.send(T("info.ticket", lang_for(r["reporter_id"]), id=r["id"], mod=member.mention, player=mention,
                            link=msg.jump_url))
        set_bug(r["id"], status="investigating", ticket_thread_id=thread.id)
        record_action("bug", r["id"], "investigating", member.id, who)
        r = get_bug(r["id"])
        await msg.edit(embed=bug_embed(r, f"Ticket opened by {who}"))
        log_event("mod", f"Bug #{r['id']}: info ticket opened by {who}", member.id, who)
        await dm_t(r["reporter_id"], "dm.investigating", id=r["id"], link=thread.jump_url)


async def handle_admin(payload: discord.RawReactionActionEvent, emoji: str):
    """Admins mark a passed bug: 🔍 being investigated · ✅ fixed · ❌ rejected."""
    channel = bot.get_channel(payload.channel_id)
    partial = channel.get_partial_message(payload.message_id)
    r = get_bug_by_admin_msg(payload.message_id)
    if r is None:
        return
    try:
        await partial.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
    except discord.HTTPException:
        pass
    member = payload.member
    if member is None or not is_admin(member) or r["status"] not in ADMIN_OPEN:
        return

    new = {ADMIN_INVESTIGATING_EMOJI: "being_investigated", ADMIN_FIXED_EMOJI: "fixed",
           ADMIN_REJECT_EMOJI: "admin_rejected"}[emoji]
    if new == "admin_rejected":
        return await prompt_reason(channel, "bug", r["id"], member, "admin_rejected")
    if new == "being_investigated" and r["status"] == "being_investigated":
        return
    set_bug(r["id"], status=new)
    if new == "fixed":
        stamp_fixed_version(r["id"])
    record_action("bug", r["id"], new, member.id, member.display_name)
    r = get_bug(r["id"])
    who = member.display_name
    note = {"being_investigated": f"Investigating – {who}", "fixed": f"Fixed – marked by {who}",
            "admin_rejected": f"Rejected by {who}"}[new]
    embed = bug_embed(r, note)
    await partial.edit(embed=embed)
    try:
        if new == "being_investigated":
            await partial.clear_reaction(ADMIN_INVESTIGATING_EMOJI)   # keep ✅ and ❌ for the next step
        else:
            await partial.clear_reactions()
    except discord.HTTPException:
        pass
    mod = chan("mod_channel_id")                    # keep the moderators' copy in sync
    if mod is not None and r["msg_id"]:
        try:
            await mod.get_partial_message(r["msg_id"]).edit(embed=embed)
        except discord.HTTPException:
            pass
    label = STATUS[new][0]
    log_event("admin", f"Bug #{r['id']} marked “{label.split(' ', 1)[1]}” by {who}", member.id, who)
    await dm_t(r["reporter_id"], "dm." + new, id=r["id"])
    if new == "fixed":
        await notify_followers(r)


def suggestion_embed(r, note: Optional[str] = None) -> discord.Embed:
    label, color = {"new": ("🆕 Open", 0x3498DB), "accepted": ("✅ Accepted", 0x2ECC71),
                    "rejected": ("❌ Rejected", 0xED4245)}[r["status"]]
    e = discord.Embed(title=f"Suggestion #{r['id']}", description=r["text"], color=color)
    e.add_field(name="From", value=f"<@{r['author_id']}>", inline=True)
    e.add_field(name="Status", value="🛠️ Added – see what changed" if r["changes"] else label, inline=True)
    if r["status"] == "rejected" and r["reject_reason"]:
        e.add_field(name="💬 Reason", value=r["reject_reason"][:1024], inline=False)
    if r["changes"]:
        e.add_field(name="🛠️ What we changed", value=r["changes"][:1024], inline=False)
        e.color = 0x9B59B6
    e.set_footer(text=brand() + (f" · {note}" if note else ""))
    return e


async def post_suggestion_changes(sid: int, text: str, actor_id, who: str):
    """An accepted suggestion was put into the game: save what changed, show it on the Discord posts,
    reply under the public post and tell the author. Saving again updates the same reply."""
    r = get_suggestion(sid)
    if r is None:
        raise ValueError("Suggestion not found.")
    if r["status"] != "accepted":
        raise ValueError("Only accepted suggestions can get a “what we changed” update.")
    text = (text or "").strip()
    if len(text) < 3:
        raise ValueError("Write what you changed (at least a few words).")
    if len(text) > 1000:
        raise ValueError("Keep it under 1,000 characters.")
    first = not r["changes"]
    set_suggestion(sid, changes=text, changed_at=int(time.time()), changed_by=who)
    record_action("suggestion", sid, "changed", actor_id, who)
    r = get_suggestion(sid)
    for key, mid in (("mod_channel_id", r["msg_id"]), ("suggestions_channel_id", r["public_msg_id"])):
        ch = chan(key)
        if ch is not None and mid:
            try:
                await ch.get_partial_message(mid).edit(embed=suggestion_embed(r))
            except discord.HTTPException:
                pass
    pub = chan("suggestions_channel_id")
    if pub is not None:
        e = discord.Embed(title=f"🛠️ Suggestion #{r['id']} is in the game!", description=text, color=0x9B59B6)
        e.add_field(name="The idea", value=(r["text"][:300] + ("…" if len(r["text"]) > 300 else "")), inline=False)
        e.set_footer(text=f"{brand()} · thanks for the idea!")
        content = f"<@{r['author_id']}>"
        try:
            if r["changes_msg_id"]:
                await pub.get_partial_message(r["changes_msg_id"]).edit(content=content, embed=e,
                                                                         allowed_mentions=discord.AllowedMentions.none())
            else:
                ref = (discord.MessageReference(message_id=r["public_msg_id"], channel_id=pub.id, fail_if_not_exists=False)
                       if r["public_msg_id"] else None)
                msg = await pub.send(content=content, embed=e, reference=ref,
                                     allowed_mentions=discord.AllowedMentions(users=True, replied_user=False))
                set_suggestion(sid, changes_msg_id=msg.id)
        except discord.HTTPException as exc:
            log_event("system", f"Could not post the update for suggestion #{sid}: {exc}", actor_id, who)
    if first:
        await dm_t(r["author_id"], "dm.sug_changed", id=r["id"], changes=text)
    log_event("mod", f"Suggestion #{sid}: “what we changed” {'posted' if first else 'updated'} by {who}", actor_id, who)
    return get_suggestion(sid)


async def handle_suggestion(payload: discord.RawReactionActionEvent, emoji: str):
    """Mod channel: moderators/admins accept (✅, also posted publicly) or reject (❌) a suggestion."""
    channel = bot.get_channel(payload.channel_id)
    partial = channel.get_partial_message(payload.message_id)
    r = get_suggestion_by_msg(payload.message_id)
    if r is None:
        return
    try:
        await partial.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
    except discord.HTTPException:
        pass
    member = payload.member
    if member is None or not is_mod(member) or r["status"] != "new":
        return
    if emoji != SUGGEST_ACCEPT_EMOJI:
        return await prompt_reason(channel, "suggestion", r["id"], member, "rejected")
    await decide_suggestion(r, "accepted", member.id, member.display_name)


async def decide_suggestion(r, new: str, actor_id, who: str, reason: Optional[str] = None):
    """Accept or reject a suggestion (from a reaction or the dashboard): updates Discord, logs and tells the author."""
    set_suggestion(r["id"], status=new, reject_reason=(reason or None) if new == "rejected" else None)
    record_action("suggestion", r["id"], new, actor_id, who)
    await drop_prompt("suggestion", r["id"])
    r = get_suggestion(r["id"])
    mod = chan("mod_channel_id")
    if mod is not None and r["msg_id"]:
        partial = mod.get_partial_message(r["msg_id"])
        try:
            await partial.edit(embed=suggestion_embed(r, f"{new.capitalize()} by {who}"))
            for e in (SUGGEST_ACCEPT_EMOJI, SUGGEST_REJECT_EMOJI):
                try:
                    await partial.clear_reaction(e)
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            pass
    if new == "accepted":
        target = chan("suggestions_channel_id")
        if target is not None:
            try:
                pub = await target.send(embed=suggestion_embed(r))
                set_suggestion(r["id"], public_msg_id=pub.id)
            except discord.HTTPException as exc:
                log_event("system", f"Could not post accepted suggestion #{r['id']}: {exc}", actor_id, who)
    log_event("mod", f"Suggestion #{r['id']} {new} by {who}" + (f" – “{reason[:120]}”" if reason else ""), actor_id, who)
    await dm_t(r["author_id"], "dm.sug_accepted" if new == "accepted" else "dm.sug_rejected", id=r["id"],
               reason=reason if new == "rejected" else None)
    return get_suggestion(r["id"])


async def delete_suggestions(ids, discord_too: bool, by_name: str, by_id=None, reset_numbers: bool = False) -> int:
    rows = store.delete_suggestions(ids, reset_numbers)
    if discord_too:
        for r in rows:
            for key, mid in (("mod_channel_id", r["msg_id"]), ("suggestions_channel_id", r["public_msg_id"])):
                ch = chan(key)
                if ch is not None and mid:
                    try:
                        await ch.get_partial_message(mid).delete()
                    except discord.HTTPException:
                        pass
    if rows:
        nums = ", ".join(f"#{r['id']}" for r in rows[:15]) + (" …" if len(rows) > 15 else "")
        log_event("admin", f"Deleted {len(rows)} suggestion{'s' if len(rows) != 1 else ''}: {nums}", by_id, by_name)
    return len(rows)


# ------------------------------------------------------------- welcome message
def welcome_text(member) -> str:
    return render_welcome(cfg("welcome_message"), member.mention, member.display_name,
                          member.guild.name, member.guild.member_count)


async def send_welcome(member: discord.Member) -> bool:
    """Greet and tag a new member in the welcome channel. Returns True if a message was sent."""
    channel = chan("welcome_channel_id")
    if not cfg("welcome_enabled") or channel is None:
        return False
    await channel.send(welcome_text(member), allowed_mentions=discord.AllowedMentions(users=[member]))
    return True


class WelcomeModal(discord.ui.Modal, title="Edit the welcome message"):
    text = discord.ui.TextInput(label="Message ({user} tags the new member)",
                                style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self):
        super().__init__()
        self.text.default = cfg("welcome_message")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            set_cfg("welcome_message", str(self.text))
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        log_event("welcome", "Welcome message edited", interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(
            "✅ Welcome message saved. Preview:\n\n" + welcome_text(interaction.user)
            + "\n\n*Placeholders: `{user}` `{username}` `{server}` `{member_count}`*", ephemeral=True)


welcome = app_commands.Group(name="welcome", description="Welcome message for new members (admins only)",
                             guild_ids=[GUILD_ID], guild_only=True,
                             default_permissions=discord.Permissions(administrator=True))


@welcome.command(name="message", description="Edit the welcome message")
async def welcome_message(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.send_modal(WelcomeModal())


@welcome.command(name="channel", description="Choose the channel where new members are welcomed")
@app_commands.describe(channel="The welcome channel")
async def welcome_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    set_cfg("welcome_channel_id", channel.id)
    log_event("welcome", f"Welcome channel set to #{channel.name}", interaction.user.id,
              interaction.user.display_name)
    await interaction.response.send_message(f"Welcome messages will be posted in {channel.mention}.", ephemeral=True)


@welcome.command(name="toggle", description="Turn welcome messages on or off")
@app_commands.describe(enabled="On or off")
async def welcome_toggle(interaction: discord.Interaction, enabled: bool):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    set_cfg("welcome_enabled", enabled)
    log_event("welcome", f"Welcome messages turned {'on' if enabled else 'off'}", interaction.user.id,
              interaction.user.display_name)
    note = "" if chan("welcome_channel_id") else "\n⚠️ No welcome channel yet – use `/welcome channel`."
    await interaction.response.send_message(f"Welcome messages are now **{'on' if enabled else 'off'}**.{note}",
                                            ephemeral=True)


@welcome.command(name="preview", description="Show the current welcome message (only you see it)")
async def welcome_preview(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    state = "on" if cfg("welcome_enabled") else "off"
    ch = chan("welcome_channel_id")
    await interaction.response.send_message(
        f"**Welcome messages are {state}** · channel: {ch.mention if ch else '(not set)'}\n\n"
        + welcome_text(interaction.user), ephemeral=True)



# ------------------------------------------------------------- panel message
def panel_embed(title: str, text: str) -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=0x5865F2)
    e.set_footer(text=brand())
    return e


async def publish_panel(title: str, text: str, channel_id: int,
                        actor_id: Optional[int] = None, actor_name: str = "Dashboard") -> discord.Message:
    """Post the panel in the bug report channel, or edit it in place if it is already there."""
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Pick a normal text channel for the panel.")
    perms = channel.permissions_for(channel.guild.me)
    need = {"view_channel": "View Channel", "send_messages": "Send Messages", "embed_links": "Embed Links",
            "read_message_history": "Read Message History",
            "manage_messages": "Manage Messages"}
    missing = [label for attr, label in need.items() if not getattr(perms, attr)]
    if missing:
        raise ValueError(f"The bot is missing permissions in #{channel.name}: {', '.join(missing)}.")

    embed = panel_embed(title, text)
    old_ch, old_msg = get_raw("panel_channel_id"), get_raw("panel_msg_id")
    msg = None
    if old_ch and old_msg and int(old_ch) == channel.id:
        try:
            msg = await channel.fetch_message(int(old_msg))
            await msg.edit(embed=embed, view=PanelView(store.default_language()))
            try:
                await msg.clear_reactions()
            except discord.HTTPException:
                pass
        except discord.NotFound:
            msg = None
    if msg is None:
        if old_ch and old_msg:                      # remove the panel from its previous place
            old_channel = bot.get_channel(int(old_ch))
            if old_channel:
                try:
                    await old_channel.get_partial_message(int(old_msg)).delete()
                except discord.HTTPException:
                    pass
        msg = await channel.send(embed=embed, view=PanelView(store.default_language()))
    put_raw("panel_buttons", "1")

    set_cfg("panel_title", title)
    set_cfg("panel_text", text)
    set_cfg("report_channel_id", channel.id)
    put_raw("panel_channel_id", str(channel.id))
    put_raw("panel_msg_id", str(msg.id))
    bot.panel_msg_id = msg.id
    log_event("panel", f"Panel message published in #{channel.name}", actor_id, actor_name)
    return msg


# ------------------------------------------------------------- info channel
PLAYER_COMMANDS = [
    ("/mybugs", "See the status of your bug reports and ideas"),
    ("/codes", "Show the active gift codes"),
    ("/link", "Link your in-game account (chief name + UID) – fills in your bug reports and lets the bot redeem gift codes for you"),
    ("/unlink", "Remove that link"),
    ("/invites show", "See how many people you (or someone else) invited"),
    ("/invites top", "See who has invited the most people"),
    ("/language", "Choose the language the bot uses with you"),
    ("/members", "How many members the server has"),
    ("Translate", "Long-press (phone) or right-click any message → Apps → Translate, to read it in your language"),
]


def info_embeds() -> list:
    report = chan("report_channel_id")
    where = report.mention if report else "the bug report channel"
    rules = discord.Embed(title="📜 Server rules", description=cfg("info_rules")[:4000], color=0xE67E22)
    cmds = discord.Embed(title="⌨️ Commands you can use", color=0x5865F2,
                         description="\n".join(f"`{c}` – {d}" if c.startswith("/") else f"**{c}** – {d}" for c, d in PLAYER_COMMANDS))
    how = discord.Embed(title="🐛 How to report a bug or suggest an idea", color=0x2ECC71, description=(
        f"1️⃣ Go to {where}\n"
        "2️⃣ Press **🐛 Report a bug** – or **💡 Suggest an idea**\n"
        "3️⃣ Fill in the short form – where it happened, a short title, what happened, and a screenshot if you have one\n"
        "4️⃣ Press **Submit** – done ✅\n\n"
        "Only you can see the form. If the bug is already known, the bot tells you straight away.\n"
        "📬 Keep your DMs open: the bot tells you when your report is accepted, investigated or fixed."))
    for e in (rules, cmds, how):
        e.set_footer(text=brand())
    return [rules, cmds, how]


async def publish_info(channel_id: int, actor_id=None, actor_name: str = "Dashboard"):
    """Post the info messages (rules, commands, how to report) – or update them in place."""
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Pick a normal text channel for the info messages.")
    perms = channel.permissions_for(channel.guild.me)
    missing = [l for a, l in (("view_channel", "View Channel"), ("send_messages", "Send Messages"), ("embed_links", "Embed Links"))
               if not getattr(perms, a)]
    if missing:
        raise ValueError(f"The bot is missing permissions in #{channel.name}: {', '.join(missing)}.")
    embeds = info_embeds()
    try:
        old = json.loads(get_raw("info_msgs") or "{}")
    except ValueError:
        old = {}
    ids = []
    if old.get("channel") == channel.id and len(old.get("ids", [])) == len(embeds):
        try:
            for mid, e in zip(old["ids"], embeds):
                await channel.get_partial_message(mid).edit(embed=e)
            ids = old["ids"]
        except discord.NotFound:
            ids = []
    if not ids:
        if old.get("channel") and old.get("ids"):
            prev = bot.get_channel(old["channel"])
            for mid in old["ids"]:
                try:
                    await prev.get_partial_message(mid).delete()
                except (discord.HTTPException, AttributeError):
                    pass
        for e in embeds:
            ids.append((await channel.send(embed=e)).id)
    put_raw("info_msgs", json.dumps({"channel": channel.id, "ids": ids}))
    set_cfg("info_channel_id", channel.id)
    log_event("panel", f"Info messages published in #{channel.name}", actor_id, actor_name)
    return ids


# ------------------------------------------------------------- bot profile
def profile_info() -> dict:
    guild = bot.get_guild(GUILD_ID)
    return {
        "username": bot.user.name,
        "avatar_url": str(bot.user.display_avatar.replace(size=256).url),
        "banner_url": str(bot.user.banner.replace(size=1024).url) if getattr(bot.user, "banner", None) else None,
        "server_icon": bool(guild and guild.icon), "server_banner": bool(guild and guild.banner),
        "nickname": guild.me.nick if guild and guild.me else None,
        "status": cfg("bot_status"),
        "activity_type": cfg("activity_type"),
        "activity_text": cfg("activity_text"),
    }


async def update_profile(data: dict) -> list:
    """Apply profile changes from the dashboard. Returns a list of warning strings."""
    warnings, changed = [], []
    guild = bot.get_guild(GUILD_ID)

    username = (data.get("username") or "").strip()
    if username and username != bot.user.name:
        if not 2 <= len(username) <= 32:
            raise ValueError("Username must be 2–32 characters.")
        try:
            await bot.user.edit(username=username)
            changed.append("username")
        except discord.HTTPException as exc:
            warnings.append(f"Username not changed: {exc.text or exc} (Discord allows 2 changes per hour).")

    def image(value, label):
        try:
            raw = base64.b64decode(value.split(",", 1)[-1], validate=True)
        except Exception:  # noqa: BLE001
            raise ValueError(f"The {label} image could not be read.")
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError(f"The {label} image is larger than 8 MB.")
        return raw

    if data.get("use_server_art") and guild is not None:
        # the server's own icon and banner (full quality); the bundled banner if the server has none
        assets = Path(__file__).with_name("assets")
        icon = await guild.icon.replace(size=1024, static_format="png").read() if guild.icon else (assets / "icon-fallback.png").read_bytes()
        banner = await guild.banner.replace(size=1024, static_format="png").read() if guild.banner else (assets / "banner.png").read_bytes()
        try:
            await bot.user.edit(avatar=icon, banner=banner)
            changed.append("avatar and banner (from the server)")
        except discord.HTTPException as exc:
            warnings.append(f"Icon/banner not changed: {exc.text or exc} (Discord limits how often this can change – try again in an hour).")

    if data.get("banner"):
        try:
            await bot.user.edit(banner=image(data["banner"], "banner"))
            changed.append("banner")
        except discord.HTTPException as exc:
            warnings.append(f"Banner not changed: {exc.text or exc}")

    avatar = data.get("avatar")
    if avatar:
        try:
            raw = base64.b64decode(avatar.split(",", 1)[-1], validate=True)
        except Exception:  # noqa: BLE001
            raise ValueError("The avatar image could not be read.")
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("The avatar image is larger than 8 MB.")
        try:
            await bot.user.edit(avatar=raw)
            changed.append("avatar")
        except discord.HTTPException as exc:
            warnings.append(f"Avatar not changed: {exc.text or exc}")

    if "nickname" in data and guild is not None:
        nick = (data.get("nickname") or "").strip() or None
        if nick and len(nick) > 32:
            raise ValueError("Server nickname must be at most 32 characters.")
        if nick != guild.me.nick:
            try:
                await guild.me.edit(nick=nick)
                changed.append("server nickname")
            except discord.HTTPException as exc:
                warnings.append(f"Nickname not changed: {exc.text or exc}")

    keymap = {"status": "bot_status", "activity_type": "activity_type", "activity_text": "activity_text"}
    wanted = {keymap[k]: store.coerce(keymap[k], data[k]) for k in keymap if k in data}  # validate first
    if wanted:
        if any(cfg(k) != v for k, v in wanted.items()):
            changed.append("status/activity")
        for k, v in wanted.items():
            set_cfg(k, v)
        await bot.apply_presence()

    if changed:
        log_event("profile", "Bot profile updated from the dashboard: " + ", ".join(sorted(set(changed))),
                  None, "Dashboard")
    return warnings


# ---------------------------------------------------------------------- bot
class WosBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # needed to read ticket answers (enable in the dev portal!)
        intents.members = True          # needed to see new members join (enable in the dev portal!)
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.panel_msg_id: Optional[int] = None
        self.started_at = time.time()
        self._announced = False
        self.api_runner = None

    async def apply_presence(self):
        text = cfg("activity_text")
        activity = discord.Activity(type=getattr(discord.ActivityType, cfg("activity_type")),
                                    name=text) if text else None
        await self.change_presence(status=discord.Status(cfg("bot_status")), activity=activity)

    async def setup_hook(self):
        value = get_raw("panel_msg_id")
        self.panel_msg_id = int(value) if value else None
        self.add_view(PreviewView())   # keep Submit/Edit/Delete working after a restart
        self.add_view(DetailsView())   # ...and the area menu / Add details button
        self.add_view(LangView())      # ...and the language menu in suggestion tickets
        self.add_view(PanelView())     # ...and the Report a bug / Suggest an idea buttons
        self.add_view(GiftCopyView())  # ...and the Copy button under gift codes
        self.add_view(GiftListView())  # ...and the Link button under the gift-codes list
        self.add_view(BoardView())     # ...and the Known issues menu
        self.add_view(DupView("all"))  # ...and the duplicate + Translate buttons on bug posts
        await self.tree.sync(guild=discord.Object(id=GUILD_ID))
        if not reminder_loop.is_running():
            reminder_loop.start()
        if not backup_loop.is_running():
            backup_loop.start()
        if not schedule_loop.is_running():
            schedule_loop.start()
        if not gift_loop.is_running():
            gift_loop.start()
        if not log_loop.is_running():
            log_loop.start()

        if DASHBOARD_API_KEY:
            if len(DASHBOARD_API_KEY) < 20:
                print("Dashboard API NOT started: DASHBOARD_API_KEY must be at least 20 characters.")
            else:
                from api import start_api
                hooks = {"publish_panel": publish_panel, "profile_info": profile_info,
                         "update_profile": update_profile, "apply_presence": self.apply_presence,
                         "set_bug_status": apply_bug_status, "send_reply": send_bug_reply,
                         "publish_patchnotes": publish_patchnotes,
                         "default_patch_title": default_patch_title, "submit_web_report": submit_web_report,
                         "set_severity": set_severity, "dup_dismissed": dup_dismissed, "run_backup": run_backup, "post_digest": post_digest,
                         "delete_bugs": delete_bugs, "delete_suggestions": delete_suggestions,
                         "decide_suggestion": decide_suggestion, "version_info": version_info,
                         "add_gift_code": add_gift_code, "end_gift_code": end_gift_code, "remove_gift_code": remove_gift_code,
                         "drive_backup": drive_backup, "fill_translations": fill_translations,
                         "access_status": access_status, "setup_languages": setup_languages, "setup_access_roles": setup_access_roles,
                         "post_gate": post_gate, "start_backfill": start_backfill, "lock_server": lock_server, "unlock_server": unlock_server,
                         "publish_info": publish_info, "publish_role_menu": publish_role_menu, "remove_role_menu": remove_role_menu,
                         "warn_member": warn_member, "start_giveaway": start_giveaway, "end_giveaway": end_giveaway, "send_timer": send_timer, "push_key": lambda: vapid_keys()[1] if push_ready() else None, "push_send": push_send, "post_weekly": post_weekly, "invite_perm": lambda: bool(_invite_uses) or bool(bot.get_guild(GUILD_ID) and bot.get_guild(GUILD_ID).me.guild_permissions.manage_guild), "sticker_status": sticker_status, "install_stickers": install_stickers, "emoji_status": emoji_status, "install_emojis": install_emojis, "suggestion_changes": post_suggestion_changes, "refresh_gift_list": safe_refresh_gift_list, "add_gift_codes": add_gift_codes,
                         "refresh_board": safe_refresh_board, "redeem_one": redeem_one, "start_redeem": start_redeem,
                         "redeem_ready": redeem_ready}
                roles = {"Moderator role": MOD_ROLE_ID, "Admin role": ADMIN_ROLE_ID}
                self.api_runner = await start_api(self, GUILD_ID, DASHBOARD_API_KEY, DASHBOARD_ORIGINS,
                                                  DASHBOARD_HOST, DASHBOARD_PORT, hooks, roles)
                print(f"Dashboard API listening on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
        else:
            print("Dashboard API is off (set DASHBOARD_API_KEY in .env to turn it on).")

    async def on_ready(self):
        print(f"{brand()}: logged in as {self.user} – ready.")
        await self.apply_presence()
        if not self._announced:
            self._announced = True
            ver = Path(__file__).with_name("VERSION")
            log_event("system", "Bot started" + (f" – version {ver.read_text().strip()}" if ver.exists() else ""), None, brand())
            store.add_server_languages()
            try:
                import_seed_codes()
            except Exception as exc:  # noqa: BLE001
                print(f"[codes] {exc!r}")
            await safe_refresh_gift_list()
            await safe_refresh_board()
            start_redeem()
            asyncio.create_task(safe_task(add_translate_buttons(), "translate buttons"))
            if (access_cfg().get("backfill") or {}).get("state") == "running":
                _backfill_task[0] = asyncio.create_task(safe_task(run_backfill(), "member backfill"))
            if not await cache_invites():
                log_event("system", "Invite tracker: give the bot the “Manage Server” permission so it can see who invited whom.", None, brand())
            try:
                await refresh_panel_buttons()
            except Exception as exc:  # noqa: BLE001
                print(f"[panel] {exc!r}")
            try:
                await announce_update()
            except Exception as exc:  # noqa: BLE001 – never stop the bot over a notice
                print(f"[update notice] {exc!r}")

    async def on_interaction(self, interaction: discord.Interaction):
        cid = (interaction.data or {}).get("custom_id", "") if interaction.type == discord.InteractionType.component else ""
        if cid == "tr:item":
            try:
                await handle_translate_button(interaction)
            except Exception as exc:  # noqa: BLE001
                print(f"[translate button] {exc!r}")
        if cid.startswith("gate:"):
            try:
                await handle_gate(interaction)
            except Exception as exc:  # noqa: BLE001
                print(f"[device question] {exc!r}")
        if cid.startswith("rm:"):
            try:
                await handle_role_menu(interaction)
            except Exception as exc:  # noqa: BLE001
                print(f"[role menu] {exc!r}")
        # talk to each player in their Discord language (unless they picked another one)
        if interaction.user and interaction.guild_id == GUILD_ID and not getattr(interaction.user, "bot", False):
            try:
                store.remember_locale(interaction.user.id, interaction.locale)
            except Exception:  # noqa: BLE001
                pass

    async def on_member_join(self, member: discord.Member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        store.record_members(member.guild.member_count, joined=1)
        try:
            await track_join(member)
        except Exception as exc:  # noqa: BLE001 – never block the welcome over invites
            print(f"[invites] {exc!r}")
        try:
            if await send_welcome(member):
                log_event("welcome", f"Welcomed {member.display_name}", member.id, member.display_name)
        except discord.HTTPException as exc:
            log_event("system", f"Could not send the welcome message: {exc}", member.id, member.display_name)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if _import_watch:
            try:
                await on_leaderboard_edit(payload)
            except Exception as exc:  # noqa: BLE001
                print(f"[invite import] {exc!r}")

    async def on_member_remove(self, member: discord.Member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        store.record_members(member.guild.member_count, left=1)
        try:
            await track_leave(member)
        except Exception as exc:  # noqa: BLE001
            print(f"[invites] {exc!r}")

    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild and invite.guild.id == GUILD_ID:
            _invite_uses[invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None, invite.max_uses or 0)
            _snapshot_invites()

    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild and invite.guild.id == GUILD_ID and invite.code in _invite_uses:
            v = _invite_uses.pop(invite.code)               # keep it for 2 minutes: a used-up one-time link is deleted before the join arrives
            _invite_gone[invite.code] = (*v, time.time())
            _snapshot_invites()

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        try:
            if await guard_message(message) or await automod_message(message):
                return
        except Exception as exc:  # noqa: BLE001 – the guard must never break normal chat
            print(f"[guard] {exc!r}")
        try:
            if await typed_reason(message):
                return
        except Exception as exc:  # noqa: BLE001
            print(f"[reason reply] {exc!r}")
        if message.guild and cfg("translate_feed_on") and message.channel.id in translate.feed()["sources"]:
            try:
                await feed_translation(message)
            except Exception as exc:  # noqa: BLE001 – translation must never break the rest
                print(f"[translate feed] {exc!r}")
        if not isinstance(message.channel, discord.Thread):
            return
        await handle_ticket_message(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id != GUILD_ID or payload.user_id == self.user.id:
            return
        emoji = str(payload.emoji)
        if payload.message_id == self.panel_msg_id and emoji in (BUG_EMOJI, SUGGEST_EMOJI):
            await handle_panel(payload, emoji)
        elif payload.channel_id == cfg("mod_channel_id") and emoji in (
                SUGGEST_ACCEPT_EMOJI, SUGGEST_REJECT_EMOJI) and get_suggestion_by_msg(payload.message_id):
            await handle_suggestion(payload, emoji)
        elif payload.channel_id == cfg("mod_channel_id") and emoji in (
                PASS_EMOJI, INVESTIGATE_EMOJI, REJECT_EMOJI):
            await handle_mod(payload, emoji)
        elif payload.channel_id == cfg("admin_channel_id") and emoji in (
                ADMIN_INVESTIGATING_EMOJI, ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI):
            await handle_admin(payload, emoji)


bot = WosBot()
bot.tree.add_command(welcome)


class PanelModal(discord.ui.Modal, title="Set the reaction message"):
    panel_title = discord.ui.TextInput(label="Title", max_length=100)
    panel_text = discord.ui.TextInput(label="Message players will see",
                                      style=discord.TextStyle.paragraph, max_length=2000)

    def __init__(self):
        super().__init__()
        self.panel_title.default = cfg("panel_title")
        self.panel_text.default = cfg("panel_text")

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Run /setup_panel in a normal text channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await publish_panel(str(self.panel_title).strip(), str(self.panel_text).strip(), channel.id,
                                interaction.user.id, interaction.user.display_name)
        except ValueError as exc:
            return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"Panel posted in {channel.mention} and set as the bug report channel.", ephemeral=True)


@bot.tree.command(name="setup_panel",
                  description="Set the message players react to for bugs/suggestions (admins only)",
                  guild=discord.Object(id=GUILD_ID))
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_panel(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.send_modal(PanelModal())


# ============================================================ status changes
PLAYER_STATUS = {
    "new": "⏳ Waiting for a moderator", "investigating": "🔍 A moderator needs more info – check your ticket",
    "passed": "📨 With the admins", "being_investigated": "🔍 Being investigated",
    "fixed": "✔️ Fixed", "rejected": "❌ Closed", "admin_rejected": "❌ Closed",
}


async def sync_bug_messages(r, note: Optional[str] = None):
    """Make the mod-channel and admin-channel copies match the bug's status (embed + reaction buttons)."""
    embed = bug_embed(r, note)
    mod_buttons = ([PASS_EMOJI] + ([INVESTIGATE_EMOJI] if cfg("investigate_enabled") else [])
                   + [REJECT_EMOJI]) if r["status"] in MOD_OPEN else []
    admin_buttons = {"passed": [ADMIN_INVESTIGATING_EMOJI, ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI],
                     "being_investigated": [ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI]}.get(r["status"], [])
    for key, mid, buttons in (("mod_channel_id", r["msg_id"], mod_buttons),
                              ("admin_channel_id", r["admin_msg_id"], admin_buttons)):
        ch = chan(key)
        if ch is None or not mid:
            continue
        p = ch.get_partial_message(mid)
        try:
            await p.edit(embed=embed)
            await p.clear_reactions()
            for e in buttons:
                await p.add_reaction(e)
        except discord.HTTPException:
            pass


async def apply_bug_status(r, new: str, actor_name: str, actor_id: Optional[int] = None,
                           duplicate_of: Optional[int] = None, log_kind: str = "admin", reason: Optional[str] = None):
    """Change a bug's status from the dashboard or a command and keep Discord in sync.
    new: passed | being_investigated | fixed | rejected | admin_rejected.  Returns the updated row."""
    if new not in ("passed", "being_investigated", "fixed", "rejected", "admin_rejected"):
        raise ValueError("That status can't be set here.")
    dup = None
    if duplicate_of is not None:
        dup = get_bug(int(duplicate_of))
        if dup is None or dup["id"] == r["id"]:
            raise ValueError("Pick another, existing bug as the original.")
        new = "rejected"
    elif r["status"] == new:
        raise ValueError(f"Bug #{r['id']} already has that status.")
    if new != "rejected" and not r["admin_msg_id"] and chan("admin_channel_id") is None:
        raise ValueError("Choose the admin channel first (Channels tab).")

    closing = new in ("rejected", "admin_rejected")
    set_bug(r["id"], status=new, duplicate_of=dup["id"] if dup else None,
            reject_reason=((reason or "").strip()[:900] or None) if closing else None, dup_suggest=None)
    if new == "fixed":
        stamp_fixed_version(r["id"])
    record_action("bug", r["id"], new, actor_id, actor_name)
    if closing or new in ("passed", "fixed"):
        await drop_prompt("bug", r["id"])
    r = get_bug(r["id"])
    note = {"passed": f"Passed by {actor_name}", "being_investigated": f"Investigating – {actor_name}",
            "fixed": f"Fixed – marked by {actor_name}", "rejected": f"Rejected by {actor_name}",
            "admin_rejected": f"Rejected by {actor_name}"}[new]
    if new != "rejected" and not r["admin_msg_id"]:     # never passed through the admin channel yet
        admin = chan("admin_channel_id")
        files = []
        mod = chan("mod_channel_id")
        if mod is not None and r["msg_id"]:
            try:
                files = [await a.to_file() for a in (await mod.fetch_message(r["msg_id"])).attachments]
            except discord.HTTPException:
                files = []
        try:
            admin_msg = await admin.send(embed=bug_embed(r, note), files=files, view=DupView())
        except discord.HTTPException:
            admin_msg = await admin.send(embed=bug_embed(r, note), view=DupView())
        set_bug(r["id"], admin_msg_id=admin_msg.id)
        r = get_bug(r["id"])
    await sync_bug_messages(r, note)
    if new == "passed" and r["severity"] == "critical":
        await ping_critical(r, actor_name)

    label = STATUS[new][0].split(" ", 1)[1]
    suffix = f" (duplicate of #{dup['id']})" if dup else ""
    why = f" – “{reason.strip()[:120]}”" if reason and closing else ""
    log_event(log_kind, f"Bug #{r['id']} marked “{label}”{suffix} by {actor_name}{why}", actor_id, actor_name)
    if dup:
        lg = lang_for(r["reporter_id"])
        await dm_t(r["reporter_id"], "dm.duplicate", id=r["id"], original=dup["id"],
                   status=T("st." + dup["status"], lg).split(" ", 1)[-1], reason=reason if closing else None)
    else:
        await dm_t(r["reporter_id"], "dm." + new, id=r["id"], reason=reason if closing else None)
    if dup:
        await sync_bug_messages(dup)             # the original now shows how many others hit the same bug
    if new == "fixed":
        await notify_followers(r)
    await safe_refresh_board()
    return r


async def notify_followers(r):
    """A bug was fixed: tell the players whose reports were closed as duplicates of it."""
    told = {r["reporter_id"]}
    for d in store.duplicates_of(r["id"]):
        if d["reporter_id"] and d["reporter_id"] not in told:
            told.add(d["reporter_id"])
            await dm_t(d["reporter_id"], "dm.follow_fixed", id=d["id"], original=r["id"])
    if len(told) > 1:
        log_event("system", f"Told {len(told) - 1} player(s) who reported duplicates that bug #{r['id']} is fixed",
                  None, brand())
    n = 0
    for uid in store.voters(r["id"]):
        if uid not in told:
            told.add(uid)
            n += 1
            await dm_t(uid, "dm.vote_fixed", id=r["id"], title=store.bug_summary(r)[:100])
    if n:
        log_event("system", f"Told {n} player(s) who also had bug #{r['id']} that it is fixed", None, brand())


async def send_bug_reply(bug_id: int, text: str, actor_name: str, actor_id: Optional[int] = None) -> bool:
    """DM the reporter a message from the moderators (always sent, even if status DMs are off)."""
    r = get_bug(bug_id)
    if r is None:
        raise ValueError("Bug not found.")
    if not r["reporter_id"]:
        raise ValueError("This report came from the website – there is no Discord account to message."
                         + (f" Contact: {r['contact']}" if r["contact"] else ""))
    text = (text or "").strip()
    if not text:
        raise ValueError("The message is empty.")
    text = text.replace("{bug}", f"#{r['id']}").replace("{summary}", store.bug_summary(r))[:1800]
    try:
        user = await bot.fetch_user(r["reporter_id"])
        await user.send(T("dm.reply_head", lang_for(r["reporter_id"]), id=r["id"]) + f"\n\n{text}")
    except discord.HTTPException:
        log_event("system", f"Could not DM the reporter of bug #{r['id']} (DMs closed?)", actor_id, actor_name)
        return False
    record_action("bug", r["id"], "replied", actor_id, actor_name)
    log_event("mod", f"Reply sent to the reporter of bug #{r['id']} by {actor_name}", actor_id, actor_name)
    return True


# ============================================================ slash commands
_GUILD = discord.Object(id=GUILD_ID)


def stamp_fixed_version(bug_id: int):
    """Remember in which game version a bug was fixed (the 'current version' setting)."""
    v = cfg("game_version")
    if v:
        set_bug(bug_id, fixed_version=v)


async def ping_critical(r, who: str):
    admin = chan("admin_channel_id")
    if admin is None:
        return
    mod = cfg("mod_channel_id")
    link = (f"\nhttps://discord.com/channels/{GUILD_ID}/{mod}/{r['msg_id']}" if mod and r["msg_id"] else "")
    try:
        await admin.send(f"<@&{ADMIN_ROLE_ID}> 🚨 **Critical bug #{r['id']}** – {store.bug_summary(r)} "
                         f"(marked by {who}){link}",
                         allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=ADMIN_ROLE_ID)]))
    except discord.HTTPException as exc:
        log_event("system", f"Could not send the critical-bug ping: {exc}", None, brand())


async def set_severity(r, level: Optional[str], actor_name: str, actor_id: Optional[int] = None):
    """Set (or clear) a bug's severity, update the Discord messages, ping admins for critical bugs."""
    if level not in (None, *store.SEVERITIES):
        raise ValueError("Severity must be critical, major or minor.")
    if r["severity"] == level:
        return r
    set_bug(r["id"], severity=level)
    r = get_bug(r["id"])
    await sync_bug_messages(r)
    log_event("mod", f"Bug #{r['id']} severity → {level or 'none'} by {actor_name}", actor_id, actor_name)
    if level == "critical":
        await ping_critical(r, actor_name)
    return r


@bot.tree.command(name="severity", description="Set how serious a bug is (moderators)", guild=_GUILD)
@app_commands.describe(bug="The bug number", level="How serious is it?")
@app_commands.choices(level=[app_commands.Choice(name="🚨 Critical (pings the admins)", value="critical"),
                             app_commands.Choice(name="🟠 Major", value="major"),
                             app_commands.Choice(name="🟡 Minor", value="minor"),
                             app_commands.Choice(name="No severity", value="none")])
@app_commands.guild_only()
async def severity_cmd(interaction: discord.Interaction, bug: int, level: app_commands.Choice[str]):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    r = get_bug(bug)
    if r is None:
        return await interaction.response.send_message(f"Bug #{bug} doesn't exist.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await set_severity(r, None if level.value == "none" else level.value, interaction.user.display_name,
                       interaction.user.id)
    await interaction.followup.send(f"Bug #{bug}: severity is now **{level.name}**.", ephemeral=True)


# ------------------------------------------------- link Discord <-> game account
@bot.tree.command(name="link", description="Link your Discord account to your in-game account", guild=_GUILD)
@app_commands.describe(uid="Your player UID (numbers only)", chief_name="Your chief name in the game")
@app_commands.guild_only()
async def link_cmd(interaction: discord.Interaction, uid: str, chief_name: str):
    await interaction.response.send_message(await do_link(interaction.user, uid, chief_name, ilang(interaction)), ephemeral=True)


@bot.tree.command(name="unlink", description="Remove the link to your in-game account", guild=_GUILD)
@app_commands.guild_only()
async def unlink_cmd(interaction: discord.Interaction):
    ok = store.delete_link(interaction.user.id)
    await interaction.response.send_message("Link removed." if ok else "You had no link.", ephemeral=True)


@bot.tree.command(name="whois", description="Look up a linked in-game account (moderators)", guild=_GUILD)
@app_commands.describe(user="A Discord user", uid="…or a player UID")
@app_commands.guild_only()
async def whois_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None, uid: Optional[str] = None):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    if bool(user) == bool(uid):
        return await interaction.response.send_message("Give either a user or a UID.", ephemeral=True)
    if user:
        lk = store.get_link(user.id)
        text = (f"{user.mention} is linked to **{lk['chief_name'] or '?'}** · UID `{lk['player_uid']}`" if lk
                else f"{user.mention} hasn't linked an in-game account.")
        n = db.execute("SELECT COUNT(*) FROM bug_reports WHERE reporter_id=?", (user.id,)).fetchone()[0]
        text += f"\nBug reports: {n}"
    else:
        rows = store.links_for_uid(store.clean_uid(uid))
        text = ("\n".join(f"<@{x['discord_id']}> · {x['chief_name'] or '?'}" for x in rows) if rows
                else "Nobody has linked that UID.")
    await interaction.response.send_message(text + "\n*Links are typed in by the players – they are not verified.*",
                                            ephemeral=True)


# ---------------------------------------------------------- reports from the website
async def submit_web_report(f: dict, files: list) -> int:
    """A report sent through the public website form. f = validated fields, files = [(name, bytes)].
    Posts it in the mod channel exactly like a Discord report. Returns the new bug number."""
    channel = chan("mod_channel_id")
    if channel is None or not cfg("web_form_enabled"):
        raise ValueError("Bug reports from the website are switched off right now.")
    cur = db.execute(
        "INSERT INTO bug_reports (reporter_id, text, created_at, summary, area, game_version, chief_name, "
        "player_uid, source, contact, ip_hash) VALUES (0,?,?,?,?,?,?,?, 'web', ?, ?)",
        (f["description"], int(time.time()), f["title"], f["area"], f["version"], f["chief_name"] or None,
         f["uid"] or None, f["contact"] or None, f.get("ip_hash")))
    db.commit()
    bug_id = cur.lastrowid
    r = get_bug(bug_id)
    try:
        try:
            msg = await channel.send(embed=bug_embed(r), files=[discord.File(io.BytesIO(d), filename=n) for n, d in files], view=DupView())
        except discord.HTTPException:
            msg = await channel.send(embed=bug_embed(r), view=DupView())      # file upload refused: still deliver the report
        set_bug(bug_id, msg_id=msg.id)
        for emoji in [PASS_EMOJI] + ([INVESTIGATE_EMOJI] if cfg("investigate_enabled") else []) + [REJECT_EMOJI]:
            await msg.add_reaction(emoji)
    except discord.HTTPException as exc:
        db.execute("DELETE FROM bug_reports WHERE id=?", (bug_id,))
        db.commit()
        log_event("system", f"Could not deliver a website report: {exc}", None, "Website")
        raise ValueError("Sorry, the report could not be delivered. Please try again later.")
    for n, d in files:
        store.save_file(bug_id, n, d)
    who = f["chief_name"] or "an anonymous player"
    log_event("bug", f"Bug #{bug_id} submitted from the website by {who}", None, "Website")
    await safe_check_duplicate(bug_id)
    return bug_id


# ---------------------------------------------------------------- backups
async def run_backup(force: bool = False, force_drive: bool = False):
    """Copy the database to data/backups (and to the backup channel, if one is set). Returns the path or None."""
    latest = store.list_backups()
    if not force and latest and time.time() - latest[0]["ts"] < 20 * 3600:
        if drive.due():                                   # today's backup exists – just copy it to Drive
            try:
                await drive_backup(BACKUP_PATH(latest[0]["name"]))
            except drive.DriveError:
                pass
        return None
    dest = store.make_backup()
    removed = store.cleanup_files(cfg("attachment_keep_days"))
    log_event("backup", f"Backup {dest.name} created ({dest.stat().st_size // 1024} KB)"
              + (f"; removed {removed} old attachment(s)" if removed else ""), None, brand())
    ch = chan("backup_channel_id")
    if ch is not None:
        data = gzip.compress(dest.read_bytes())
        if len(data) <= ch.guild.filesize_limit:
            try:
                await ch.send("💾 Daily database backup (contains player data – keep this channel private)",
                              file=discord.File(io.BytesIO(data), filename=dest.name + ".gz"))
            except discord.HTTPException as exc:
                log_event("system", f"Could not post the backup: {exc}", None, brand())
        else:
            log_event("system", "The backup is too large to post in Discord; it is kept on the server only.", None, brand())
    if force_drive or drive.due():
        try:
            await drive_backup(dest)
        except drive.DriveError:
            pass                                          # logged; the local backup is fine
    return dest


async def drive_backup(dest=None) -> str:
    """Copy a backup to Google Drive (weekly by default). Returns the file name; raises drive.DriveError."""
    dest = dest or store.make_backup()
    try:
        name = await drive.upload(dest)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) if isinstance(exc, drive.DriveError) else f"Upload failed: {exc!r}"
        drive.remember(error=msg)
        log_event("system", f"Could not copy the backup to Google Drive: {msg}", None, brand())
        raise drive.DriveError(msg)
    drive.remember(name=name)
    log_event("backup", f"Backup {name} copied to Google Drive", None, brand())
    return name


@tasks.loop(hours=24)
async def backup_loop():
    try:
        if cfg("backups_enabled"):
            await run_backup()
    except Exception as exc:  # noqa: BLE001
        print(f"[backup] {exc!r}")


@backup_loop.before_loop
async def _before_backup():
    await bot.wait_until_ready()


@bot.tree.command(name="backup", description="Make a backup of the bug database now (admins)", guild=_GUILD)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def backup_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    dest = await run_backup(force=True)
    await interaction.followup.send(f"✅ Backup saved as `{dest.name}` on the bot's computer.", ephemeral=True)


@bot.tree.command(name="members", description="Show how many members the server has", guild=_GUILD)
@app_commands.guild_only()
async def members_cmd(interaction: discord.Interaction):
    g = interaction.guild
    e = discord.Embed(title=f"👥 {g.name}", color=0x5865F2)
    e.add_field(name="Members", value=f"**{g.member_count:,}**", inline=True)
    cached = [m for m in g.members]
    if g.chunked and len(cached) >= g.member_count - 2:        # only exact when the full member list is loaded
        now = discord.utils.utcnow()
        bots = sum(1 for m in cached if m.bot)
        e.add_field(name="Players", value=f"{len(cached) - bots:,}", inline=True)
        e.add_field(name="Bots", value=f"{bots:,}", inline=True)
        for days in (7, 30):
            n = sum(1 for m in cached if m.joined_at and (now - m.joined_at).days < days and not m.bot)
            e.add_field(name=f"Joined, last {days} days", value=f"{n:,}", inline=True)
    g30 = store.member_growth(30)
    if len(g30["items"]) >= 2 and not g30["estimated"]:
        net = g30["current"] - g30["items"][0]["members"]
        e.add_field(name="Change, last 30 days", value=f"{net:+,}  (+{g30['joined']} / −{g30['left']})", inline=True)
    await interaction.response.send_message(embed=e)


# ============================================================ weekly digest
def digest_embed(d: dict) -> discord.Embed:
    diff = d["reported"] - d["reported_prev"]
    trend = f" ({'+' if diff >= 0 else ''}{diff} vs last week)" if d["reported_prev"] or diff else ""
    e = discord.Embed(title=f"📊 Weekly bug digest – last {d['days']} days", color=0x5865F2)
    e.description = (f"🐛 **{d['reported']}** new reports{trend}" + (f" · {d['web']} from the website" if d["web"] else "")
                     + f"\n✅ **{d['fixed']}** fixed · ❌ **{d['rejected']}** closed"
                     + (f"\n⏱️ Average time to fix: **{d['fix_hours']} h**" if d["fix_hours"] is not None else "")
                     + (f"\n💡 **{d['suggestions']}** suggestions ({d['suggestions_accepted']} accepted)" if d["suggestions"] else ""))
    o = d["open"]
    e.add_field(name="Waiting right now", value=f"Moderators: **{o['new'] + o['investigating']}**\n"
                                                f"Admins: **{o['passed']}** · investigating: **{o['being_investigated']}**", inline=True)
    if d["areas"]:
        e.add_field(name="Busiest areas", value="\n".join(f"{a} – {n}" for a, n in d["areas"]), inline=True)
    if d["reporters"]:
        e.add_field(name="Top reporters", value="\n".join(f"<@{u}> – {n}" for u, n in d["reporters"]), inline=True)
    if d["critical"]:
        e.add_field(name="🚨 Critical and still open", inline=False,
                    value="\n".join(f"#{c['id']} {c['title'][:70]}" for c in d["critical"][:5]))
    if d["oldest"]:
        e.add_field(name="Waiting longest (with the admins)", inline=False,
                    value="\n".join(f"#{x['id']} {x['title'][:60]} – **{x['age_days']} days**" for x in d["oldest"]))
    e.set_footer(text=brand())
    return e


async def post_digest(force: bool = False):
    """Post the weekly summary to the digest channel (default: the admin channel). Returns the channel or None."""
    ch = chan("digest_channel_id") or chan("admin_channel_id")
    if ch is None:
        raise ValueError("Choose the admin channel (or a digest channel) first.")
    await ch.send(embed=digest_embed(store.digest_data()), allowed_mentions=discord.AllowedMentions.none())
    log_event("system", "Weekly digest posted" + (" (sent manually)" if force else ""), None, brand())
    return ch


async def delete_bugs(ids, discord_too: bool, by_name: str, by_id=None, reset_numbers: bool = False) -> int:
    """Delete bugs from the database and (optionally) their messages and info tickets in Discord."""
    rows = store.delete_bugs(ids, reset_numbers)
    if discord_too:
        mod, adm = chan("mod_channel_id"), chan("admin_channel_id")
        for r in rows:
            for ch, mid in ((mod, r["msg_id"]), (adm, r["admin_msg_id"])):
                if ch is not None and mid:
                    try:
                        await ch.get_partial_message(mid).delete()
                    except discord.HTTPException:
                        pass                      # already gone or no permission – the bug is deleted anyway
            if r["ticket_thread_id"]:
                try:
                    th = bot.get_channel(r["ticket_thread_id"]) or await bot.fetch_channel(r["ticket_thread_id"])
                    await th.delete()
                except discord.HTTPException:
                    pass
    if rows:
        nums = ", ".join(f"#{r['id']}" for r in rows[:15]) + (" …" if len(rows) > 15 else "")
        log_event("admin", f"Deleted {len(rows)} bug{'s' if len(rows) != 1 else ''}: {nums}"
                  + (" (numbering restarts at #1)" if reset_numbers and not store.all_bug_ids() else ""), by_id, by_name)
    return len(rows)


def version_info() -> dict:
    """The installed version, written by deploy/update.sh (empty when installed by hand)."""
    here = Path(__file__).parent
    read = lambda n: (here / n).read_text(encoding="utf-8").strip() if (here / n).exists() else ""  # noqa: E731
    return {"version": read("VERSION"), "changes": read("CHANGES")}


async def announce_update():
    """After "Update bugbot": post the new version and what changed in the admin channel (once per version)."""
    info = version_info()
    if not info["version"] or get_raw("version_announced") == info["version"]:
        return
    put_raw("version_announced", info["version"])
    ch = chan("admin_channel_id")
    if not cfg("update_notice_enabled") or ch is None:
        return
    lines = [x for x in info["changes"].splitlines() if x.strip()]
    if lines and lines[0].startswith("ROLLBACK"):
        e = discord.Embed(title="⚠️ Update failed – previous version restored",
                          description=f"Version **{lines[0].split(' ', 1)[-1]}** did not start, so the bot went back to "
                                      f"**{info['version']}**. Nothing was lost. Tell Claude the update failed.",
                          color=0xF1C40F)
    else:
        body = "\n".join(f"• {x.lstrip('•- ').strip()}" for x in lines[:15]) or "Small fixes and improvements."
        if len(lines) > 15:
            body += f"\n…and {len(lines) - 15} more"
        e = discord.Embed(title=f"🔄 {brand()} updated to version {info['version']}", description=body[:4000], color=0x5865F2)
    e.set_footer(text="Installed with “Update bugbot”")
    await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
    log_event("system", f"Update notice posted for version {info['version']}", None, brand())


# ------------------------------------------------------------- translation (Google)
_ONLY_NOISE = re.compile(r"(<a?:\w+:\d+>|<[@#][!&]?\d+>|https?://\S+|[\W\d_])+", re.UNICODE)


def lang_label(code: str) -> str:
    code = (code or "").lower()
    return texts.LANGUAGE_NAMES.get(code) or texts.LANGUAGE_NAMES.get(code.split("-")[0]) or code.upper() or "?"


def worth_translating(text: str) -> bool:
    return len(_ONLY_NOISE.sub("", text or "")) >= 2


async def feed_translation(message: discord.Message):
    """Messages in the chosen language channels are posted, translated, in the feed channel."""
    f = translate.feed()
    feed_ch = bot.get_channel(f["channel_id"]) if f["channel_id"] else None
    if feed_ch is None or not translate.key() or not worth_translating(message.content):
        return
    try:
        out, src = await translate.text(message.content, f["target"])
    except translate.TranslateError as exc:
        if get_raw("translate_warned") != translate.month():
            put_raw("translate_warned", translate.month())
            log_event("system", f"Translation feed paused: {exc}", None, brand())
        return
    if translate.same_language(src, f["target"]):
        return                                           # already in the feed's language – nothing to translate
    e = discord.Embed(description=out[:4000], color=0x5865F2, url=message.jump_url)
    e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url, url=message.jump_url)
    e.set_footer(text=f"#{message.channel.name} · {lang_label(src)} → {lang_label(f['target'])}")
    await feed_ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.context_menu(name="Translate", guild=_GUILD)
async def translate_message(interaction: discord.Interaction, message: discord.Message):
    """Right-click (or long-press) a message → Apps → Translate: only you see the translation, in your Discord language."""
    lg = ilang(interaction)
    target = str(interaction.locale or "en").lower()
    if not translate.key():
        return await interaction.response.send_message(T("tr.off", lg), ephemeral=True)
    content = message.content or " ".join(e.description or "" for e in message.embeds)
    if not worth_translating(content):
        return await interaction.response.send_message(T("tr.empty", lg), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        out, src = await translate.text(content, target)
    except translate.TranslateError as exc:
        return await interaction.followup.send(T("tr.limit", lg) if "limit" in str(exc) else f"⚠️ {exc}", ephemeral=True)
    if translate.same_language(src, target):
        return await interaction.followup.send(T("tr.same", lg, language=lang_label(src)), ephemeral=True)
    e = discord.Embed(description=out[:4000], color=0x5865F2)
    e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url, url=message.jump_url)
    e.set_footer(text=T("tr.from", lg, language=lang_label(src)))
    await interaction.followup.send(embed=e, ephemeral=True)


async def fill_translations(lang: Optional[str] = None):
    """Machine-translate the bot texts that are still missing – only when an admin presses the button
    (one language, or all switched-on languages)."""
    if not translate.key():
        return 0
    total = 0
    for lg in ([lang] if lang else store.languages()):
        try:
            n = await translate.fill_language(lg)
        except translate.TranslateError as exc:
            log_event("system", f"Could not translate the bot texts into {lang_label(lg)}: {exc}", None, brand())
            break
        if n:
            total += n
            log_event("settings", f"Bot texts translated into {lang_label(lg)} automatically ({n} texts)", None, brand())
    return total


# ------------------------------------------------------------- info tickets nobody answers
async def check_info_tickets(now: Optional[float] = None):
    """Remind the reporter when a moderator's question waits too long, and close the ticket after a while.
    The bug then goes back to the moderators (status New) – nothing is rejected automatically."""
    remind_h, close_h = cfg("info_remind_hours"), cfg("info_close_hours")
    if not remind_h and not close_h:
        return
    now = now or time.time()
    try:
        state = json.loads(get_raw("info_tickets") or "{}")
    except ValueError:
        state = {}
    rows = db.execute("SELECT * FROM bug_reports WHERE status='investigating' AND ticket_thread_id IS NOT NULL").fetchall()
    for r in rows:
        key = str(r["id"])
        st = state.get(key, {})
        try:
            thread = bot.get_channel(r["ticket_thread_id"]) or await bot.fetch_channel(r["ticket_thread_id"])
            history = [m async for m in thread.history(limit=25)]
        except discord.HTTPException:
            continue
        waiting_since = None
        for m in history:                                   # newest first
            if m.id in st.get("reminders", []):
                continue                                    # our own reminder doesn't restart the clock
            if m.author.id == r["reporter_id"]:
                break                                       # the player answered last – nothing to do
            waiting_since = m.created_at.timestamp()
            break
        if waiting_since is None:
            state.pop(key, None)
            continue
        hours = (now - waiting_since) / 3600
        lg = lang_for(r["reporter_id"])
        if close_h and hours >= close_h and (st.get("since") == waiting_since or not remind_h):
            try:
                await thread.send(T("info.closed", lg, id=r["id"]))
                await thread.edit(archived=True, locked=True)
            except discord.HTTPException:
                pass
            set_bug(r["id"], status="new", ticket_thread_id=None)
            record_action("bug", r["id"], "ticket_closed", None, brand())
            await sync_bug_messages(get_bug(r["id"]), "Info ticket closed – no answer from the reporter")
            log_event("mod", f"Bug #{r['id']}: info ticket closed after {round(hours)} h without an answer – back to the moderators",
                      None, brand())
            state.pop(key, None)
        elif remind_h and hours >= remind_h and st.get("since") != waiting_since:
            mention = f"<@{r['reporter_id']}>"
            left = max(1, round(close_h - hours)) if close_h else 0
            try:
                msg = await thread.send(T("info.reminder", lg, player=mention, id=r["id"], hours=left) if close_h
                                        else T("info.reminder", lg, player=mention, id=r["id"], hours="?").split(". ")[0] + ".",
                                        allowed_mentions=discord.AllowedMentions(users=True))
            except discord.HTTPException:
                continue
            await dm_t(r["reporter_id"], "dm.info_reminder", id=r["id"], link=thread.jump_url)
            state[key] = {"since": waiting_since, "reminders": (st.get("reminders", []) + [msg.id])[-5:]}
            log_event("mod", f"Bug #{r['id']}: reminded the reporter to answer the info ticket", None, brand())
    put_raw("info_tickets", json.dumps(state))


# ------------------------------------------------------------- gift codes
# Flow: new codes get their own announcement (code blocks + Copy button) for the "new" period (24 h by default,
# the same as the website's "New" section). After that they leave the announcement – which is deleted when it
# is empty – and join the "Active gift codes" list. An expired code disappears from wherever it is.

def gift_embed(r, state: Optional[str] = None, lang: Optional[str] = None) -> discord.Embed:
    """Embed for one code (used by /codes-style displays and tests)."""
    state = state or store.gift_code_state(r)
    if state == "expired":
        e = discord.Embed(title=T("gift.expired", lang), description=f"~~`{r['code']}`~~", color=0x5C6370)
    else:
        e = discord.Embed(title=T("gift.new", lang), description=f"## `{r['code']}`", color=0xF1C40F)
    if r["reward"]:
        e.add_field(name=T("gift.reward", lang), value=r["reward"], inline=False)
    e.add_field(name=T("gift.valid", lang), value=(f"<t:{r['expires_at']}:f>" if r["expires_at"] else T("gift.never", lang)), inline=False)
    e.set_footer(text=f"{brand()} · {T('gift.footer', lang)}")
    return e


def gift_ping():
    """(content, allowed_mentions) for the announcement, from the dashboard setting."""
    p, role = cfg("giftcodes_ping"), (cfg("giftcodes_role_id") or "").strip()
    if p == "everyone":
        return "@everyone", discord.AllowedMentions(everyone=True)
    if p == "here":
        return "@here", discord.AllowedMentions(everyone=True)
    if p == "role" and role.isdigit():
        return f"<@&{role}>", discord.AllowedMentions(roles=[discord.Object(id=int(role))])
    return None, discord.AllowedMentions.none()


def gift_block(r) -> str:
    """One code in code format, with its reward and time left underneath."""
    info = []
    if r["reward"]:
        info.append(f"🎁 {r['reward']}")
    if r["expires_at"]:
        t = r["expires_at"]
        info.append("⏳ " + T("gift.until", time=f"<t:{t}:f>", relative=f"<t:{t}:R>"))
    else:
        info.append("♾️ " + T("gift.never"))
    return f"```\n{r['code']}\n```-# {' · '.join(info)}\n"


def _chunks(head: str, blocks: list, tail: str = "", limit: int = 1950) -> list:
    """Split blocks over as few messages as possible (Discord allows 2,000 characters per message)."""
    out, cur = [], head
    for b in blocks:
        if len(cur) + len(b) > limit - len(tail):
            out.append(cur)
            cur = ""
        cur += b
    out.append(cur + tail)
    return out


def site_url() -> Optional[str]:
    u = (cfg("giftcodes_site_url") or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else None


def announced_rows(msg_id: int, now=None):
    """Codes that still belong in this announcement: active and still new."""
    now = now or int(time.time())
    return [r for r in db.execute("SELECT * FROM gift_codes WHERE msg_id=? ORDER BY id", (msg_id,)).fetchall()
            if store.gift_code_state(r, now) == "new"]


def announcement_text(rows, ping: Optional[str] = None) -> str:
    head = (f"{ping}\n" if ping else "") + (T("gift.new_one") if len(rows) == 1 else T("gift.new_many", n=len(rows))) + "\n"
    return head + "".join(gift_block(r) for r in rows)


class GiftCopyView(discord.ui.View):
    """📋 Copy under an announcement (persistent): sends each code privately as plain text, one per
    message, so phone players can hold it and copy it without any formatting."""

    def __init__(self):
        super().__init__(timeout=None)
        self.copy.label = T("gift.copy_btn")[:80]
        url = site_url()
        if url:
            self.add_item(discord.ui.Button(label=T("gift.website")[:80], url=url, emoji="🌐"))

    @discord.ui.button(label="Copy code", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="gift:copy")
    async def copy(self, interaction: discord.Interaction, button: discord.ui.Button):
        codes = gift_codes_from_message(interaction.message)
        active = {r["code"] for r in store.active_gift_codes()}
        codes = [c for c in codes if c in active] or codes
        if not codes:
            return await interaction.response.send_message(T("gift.none", ilang(interaction)), ephemeral=True)
        await interaction.response.send_message(codes[0], ephemeral=True)
        for c in codes[1:10]:
            await interaction.followup.send(c, ephemeral=True)


def gift_codes_from_message(msg) -> list:
    if msg is None:
        return []
    found = re.findall(r"```\s*([^`\s]+)\s*```", msg.content or "")
    if not found:
        for e in msg.embeds:
            found += re.findall(r"`([^`\s]+)`", e.description or "")
    return found


def gift_code_from_message(msg) -> Optional[str]:
    codes = gift_codes_from_message(msg)
    return codes[0] if codes else None


async def add_gift_codes(items, by_name: str, by_id=None, announce: bool = True):
    """Save one or more codes [(code, reward, hours)] and announce them together.
    Returns (rows, skipped [(code, why)], warning or None)."""
    rows, skipped = [], []
    for code, reward, hours in items:
        try:
            rows.append(store.add_gift_code(code, reward, hours, by_name))
        except ValueError as exc:
            skipped.append((code, str(exc)))
    warning = None
    ch = chan("giftcodes_channel_id")
    if rows and announce and ch is None:
        warning = "Saved, but no gift-codes channel is chosen, so it wasn't announced (Channels page)."
    elif rows and announce:
        ping, mentions = gift_ping()
        parts = _chunks((f"{ping}\n" if ping else "") + (T("gift.new_one") if len(rows) == 1 else T("gift.new_many", n=len(rows))) + "\n",
                        [gift_block(r) for r in rows])
        i = 0
        try:
            for n, part in enumerate(parts):
                count = part.count("```") // 2
                msg = await ch.send(content=part, view=GiftCopyView(), allowed_mentions=mentions if n == 0 else discord.AllowedMentions.none())
                for r in rows[i:i + count]:
                    store.set_gift_code(r["id"], msg_id=msg.id)
                i += count
        except discord.HTTPException as exc:
            warning = f"Saved, but Discord refused the announcement: {exc.text or exc}"
    for r in rows:
        log_event("admin", f"Gift code {r['code']} added ({store.gift_duration_label((r['expires_at'] - r['created_at']) // 3600 if r['expires_at'] else None)})",
                  by_id, by_name)
    if rows:
        await safe_refresh_gift_list()
        start_redeem()
        if announce:
            first = rows[0]
            push_soon("codes", "🎁 New gift code" + ("s" if len(rows) > 1 else "") + "!",
                      (first["code"] + (f" – {first['reward']}" if first["reward"] else "")) if len(rows) == 1
                      else ", ".join(r["code"] for r in rows[:4]) + (" …" if len(rows) > 4 else ""), "codes.html")
    return [store.get_gift_code(r["id"]) for r in rows], skipped, warning


async def add_gift_code(code: str, reward: str, hours, by_name: str, by_id=None, announce: bool = True):
    """Save a code and announce it. Returns (row, warning or None)."""
    rows, skipped, warning = await add_gift_codes([(code, reward, hours)], by_name, by_id, announce)
    if skipped:
        raise ValueError(skipped[0][1])
    return rows[0], warning


async def sync_announcement(msg_id: int, now=None):
    """Make one announcement show only the codes that are still new and active; delete it when none are left."""
    ch = chan("giftcodes_channel_id")
    rows = announced_rows(msg_id, now)
    if ch is None:
        return
    keep = [r["id"] for r in rows]                       # codes that left the announcement no longer point at it
    db.execute(f"UPDATE gift_codes SET msg_id=NULL WHERE msg_id=? AND id NOT IN ({','.join('?' * len(keep)) or 'NULL'})",
               (msg_id, *keep))
    db.commit()
    p = ch.get_partial_message(msg_id)
    if not rows:
        try:
            await p.delete()
        except discord.HTTPException:
            pass
        db.execute("UPDATE gift_codes SET msg_id=NULL WHERE msg_id=?", (msg_id,))
        db.commit()
        return
    try:
        msg = await ch.fetch_message(msg_id) if hasattr(ch, "fetch_message") else None
    except discord.NotFound:
        db.execute("UPDATE gift_codes SET msg_id=NULL WHERE msg_id=?", (msg_id,))   # deleted by hand – codes go to the list
        db.commit()
        return
    except discord.HTTPException:
        msg = None
    content = (msg.content if msg is not None else "") or ""
    first = content.split("\n", 1)[0] if content else ""
    ping = first if first.startswith(("@everyone", "@here", "<@&")) else None
    want = announcement_text(rows, ping)
    if msg is not None and content == want and not msg.embeds:
        return
    try:
        await p.edit(content=want, embed=None, view=GiftCopyView(), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def sync_gift_messages(now=None):
    """Called every minute: move codes whose "new" time is over into the list, remove expired codes."""
    now = now or int(time.time())
    delete = cfg("giftcodes_delete_expired")
    for r in db.execute("SELECT * FROM gift_codes WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall():
        if delete:
            store.delete_gift_code(r["id"])
            log_event("system", f"Gift code {r['code']} expired and was removed", None, brand())
        elif not r["expired_noted"]:
            store.set_gift_code(r["id"], expired_noted=1)
            log_event("system", f"Gift code {r['code']} expired", None, brand())
        if r["msg_id"]:
            await sync_announcement(r["msg_id"], now)
    for (mid,) in db.execute("SELECT DISTINCT msg_id FROM gift_codes WHERE msg_id IS NOT NULL").fetchall():
        rows = db.execute("SELECT * FROM gift_codes WHERE msg_id=?", (mid,)).fetchall()
        if any(store.gift_code_state(r, now) != "new" for r in rows):
            await sync_announcement(mid, now)
    await safe_refresh_gift_list()


async def expire_gift_codes():
    await sync_gift_messages()


def import_seed_codes():
    """Once: add the codes in seed/giftcodes.txt (permanent, without announcing each one)."""
    f = Path(__file__).with_name("seed") / "giftcodes.txt"
    if not f.exists() or get_raw("seed_codes") == str(int(f.stat().st_size)):
        return
    added, skipped = store.import_gift_codes(f.read_text(encoding="utf-8").splitlines(), "Import")
    put_raw("seed_codes", str(int(f.stat().st_size)))
    if added:
        log_event("admin", f"Imported {len(added)} permanent gift codes" + (f" ({len(skipped)} already there)" if skipped else ""),
                  None, brand())


async def end_gift_code(cid: int, by_name: str, by_id=None):
    r = store.get_gift_code(cid)
    store.set_gift_code(cid, expires_at=int(time.time()), expired_noted=1)
    if r["msg_id"]:
        await sync_announcement(r["msg_id"])
    await safe_refresh_gift_list()
    log_event("admin", f"Gift code {r['code']} ended early", by_id, by_name)


async def remove_gift_code(cid: int, discord_too: bool, by_name: str, by_id=None):
    r = store.get_gift_code(cid)
    store.delete_gift_code(cid)
    if discord_too and r["msg_id"]:
        await sync_announcement(r["msg_id"])
    log_event("admin", f"Gift code {r['code']} deleted", by_id, by_name)
    await safe_refresh_gift_list()


def list_rows(now=None):
    """Codes for the "Active gift codes" list: every active code except those still in their announcement."""
    now = now or int(time.time())
    return [r for r in store.active_gift_codes(now) if not (r["msg_id"] and store.gift_code_state(r, now) == "new")]


def gift_list_parts(now=None) -> list:
    rows = list_rows(now)
    head = T("gift.list_head", n=len(rows)) + "\n"
    tail = "-# " + T("gift.list_note", hours=cfg("giftcodes_new_hours"))
    if not rows:
        return [head + T("gift.none") + "\n" + tail]
    return _chunks(head, [gift_block(r) for r in rows], "\n" + tail)


class GiftListView(discord.ui.View):
    """Under the "Active gift codes" list: copy a code, link your game account, open the website."""

    def __init__(self):
        super().__init__(timeout=None)
        self.copy.label = T("gift.copy_btn")[:80]
        self.link.label = T("link.btn")[:80]
        url = site_url()
        if url:
            self.add_item(discord.ui.Button(label=T("gift.website")[:80], url=url, emoji="🌐"))

    @discord.ui.button(label="Copy code", emoji="📋", style=discord.ButtonStyle.primary, custom_id="gift:copylist")
    async def copy(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = list_rows()
        if not rows:
            return await interaction.response.send_message(T("gift.none", ilang(interaction)), ephemeral=True)
        await interaction.response.send_message(T("gift.copy_pick", ilang(interaction)), view=CodePickView(rows), ephemeral=True)

    @discord.ui.button(label="Link my game account", emoji="🔗", style=discord.ButtonStyle.secondary, custom_id="gift:link")
    async def link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkModal(ilang(interaction), store.get_link(interaction.user.id)))


class CodePickView(discord.ui.View):
    """Private menu (up to 125 codes): pick one and get it as plain text to copy."""

    def __init__(self, rows):
        super().__init__(timeout=600)
        for i in range(0, min(len(rows), 125), 25):
            part = rows[i:i + 25]
            sel = discord.ui.Select(placeholder=f"{part[0]['code']} … {part[-1]['code']}"[:150] if len(rows) > 25 else T("gift.copy_pick")[:150],
                                    options=[discord.SelectOption(label=r["code"][:100], value=r["code"][:100],
                                                                  description=(r["reward"] or None) and r["reward"][:100]) for r in part])
            sel.callback = self._make(sel)
            self.add_item(sel)

    def _make(self, sel):
        async def cb(interaction: discord.Interaction):
            await interaction.response.send_message(sel.values[0], ephemeral=True)
        return cb


async def refresh_gift_list():
    """Create or update the "Active gift codes" list (one or more messages). Only edits Discord when it changed."""
    ch = chan("giftcodes_channel_id")
    if ch is None or not cfg("giftcodes_enabled") or not cfg("giftcodes_list"):
        return
    parts = gift_list_parts()
    sig = hashlib.sha1(json.dumps([parts, site_url(), T("gift.copy_btn"), T("link.btn")]).encode()).hexdigest()
    try:
        ids = [int(x) for x in json.loads(get_raw("gift_list_msgs") or "[]")]
    except ValueError:
        ids = []
    if not ids and get_raw("gift_list_msg_id"):
        ids = [int(get_raw("gift_list_msg_id"))]                 # the single embed message from before
    if get_raw("gift_list_chan") != str(ch.id):
        ids = []
    if ids and len(ids) == len(parts) and get_raw("gift_list_sig") == sig:
        return
    new_ids = []
    for n, part in enumerate(parts):
        view = GiftListView() if n == len(parts) - 1 else None
        mid = ids[n] if n < len(ids) else None
        if mid:
            try:
                await ch.get_partial_message(mid).edit(content=part, embed=None, view=view,
                                                       allowed_mentions=discord.AllowedMentions.none())
                new_ids.append(mid)
                continue
            except discord.NotFound:
                pass
        msg = await ch.send(content=part, view=view, allowed_mentions=discord.AllowedMentions.none())
        new_ids.append(msg.id)
    for mid in ids[len(parts):]:                                  # the list got shorter
        try:
            await ch.get_partial_message(mid).delete()
        except discord.HTTPException:
            pass
    put_raw("gift_list_msgs", json.dumps(new_ids))
    put_raw("gift_list_msg_id", str(new_ids[0]))
    put_raw("gift_list_chan", str(ch.id))
    put_raw("gift_list_sig", sig)


async def safe_refresh_gift_list():
    try:
        await refresh_gift_list()
    except Exception as exc:  # noqa: BLE001 – never break adding/removing codes over the list
        print(f"[gift list] {exc!r}")


def codes_embed(lang: Optional[str] = None, uid: Optional[str] = None) -> discord.Embed:
    rows = store.active_gift_codes()
    mine = store.redeemed_codes_for(uid) if uid else set()
    e = discord.Embed(title=T("gift.list", lang), color=0xF1C40F)
    if not rows:
        e.description = T("gift.none", lang)
    for r in rows[:20]:
        new = store.gift_code_state(r) == "new"
        until = (T("gift.until", lang, time=f"<t:{r['expires_at']}:f>", relative=f"<t:{r['expires_at']}:R>")
                 if r["expires_at"] else T("gift.never", lang))
        e.add_field(name=("✅ " if r["code"] in mine else "🆕 " if new else "") + r["code"], value=(f"{r['reward']}\n" if r["reward"] else "") + until, inline=False)
    e.set_footer(text=brand() + (f" · {T('gift.redeemed_note', lang)}" if mine else ""))
    return e


# ------------------------------------------------------- linking the game account (button + form)
class LinkModal(discord.ui.Modal):
    """Chief name + UID. Opened from the Link button under the gift-codes list (or anywhere else)."""

    def __init__(self, lg: Optional[str], link=None):
        super().__init__(title=T("link.title", lg)[:45], timeout=900)
        self.lg = lg
        self.uid = discord.ui.TextInput(max_length=24, min_length=4, default=(link["player_uid"] if link else None))
        self.name = discord.ui.TextInput(max_length=50, required=False, default=(link["chief_name"] if link else None) or None)
        self.add_item(discord.ui.Label(text=T("link.uid", self.lg)[:45], component=self.uid))
        self.add_item(discord.ui.Label(text=T("link.name", self.lg)[:45], component=self.name))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(await do_link(interaction.user, str(self.uid.value), str(self.name.value or ""), self.lg),
                                                ephemeral=True)


async def do_link(user, uid: str, chief: str, lg: Optional[str]) -> str:
    """Save the link, start redeeming active codes for the player, return the reply text."""
    try:
        store.set_link(user.id, uid, chief)
    except ValueError:
        return T("link.bad", lg)
    lk = store.get_link(user.id)
    log_event("link", f"{user.display_name} linked to UID {lk['player_uid']} ({lk['chief_name'] or '?'})", user.id, user.display_name)
    text = T("link.done", lg, name=lk["chief_name"] or user.display_name, uid=lk["player_uid"])
    if redeem_ready():
        text += "\n" + T("link.done_redeem", lg)
        start_redeem()
    return text


# ------------------------------------------------------------ known issues board + "I have this too"
def board_embed() -> discord.Embed:
    rows = store.board_bugs(25)
    e = discord.Embed(title=T("board.title"), color=0x5865F2)
    lines = []
    for r in rows:
        st = T("st." + r["status"]).split(" ", 1)
        votes = f" · 👥 {r['votes']}" if r["votes"] else ""
        lines.append(f"{st[0]} **#{r['id']}** {store.bug_summary(r)[:90]}{votes}")
    e.description = "\n".join(lines)[:3800] if lines else T("board.empty")
    fixed = store.recently_fixed_public(7, 8)
    if fixed:
        e.add_field(name=T("board.fixed"), value="\n".join(
            f"✔️ **#{r['id']}** {store.bug_summary(r)[:80]}" + (f" ({r['fixed_version']})" if r["fixed_version"] else "")
            for r in fixed)[:1024], inline=False)
    e.set_footer(text=f"{brand()} · {T('board.footer')}"[:2048])
    return e


class BoardSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"#{r['id']} {store.bug_summary(r)}"[:100], value=str(r["id"]),
                                     description=(T("board.affected", n=r["votes"]) if r["votes"] else None))
                for r in rows[:25]] or [discord.SelectOption(label="–", value="0")]
        super().__init__(custom_id="board:metoo", placeholder=T("board.pick")[:150], min_values=1, max_values=1,
                         options=opts, disabled=not rows)

    async def callback(self, interaction: discord.Interaction):
        lg = ilang(interaction)
        bug_id = int(self.values[0]) if self.values and self.values[0].isdigit() else 0
        r = get_bug(bug_id) if bug_id else None
        if r is None or not r["public"] or r["status"] not in ("passed", "being_investigated"):
            return await interaction.response.send_message(T("board.gone", lg), ephemeral=True)
        on = store.toggle_vote(bug_id, interaction.user.id)
        n = store.vote_count(bug_id)
        await interaction.response.send_message(
            T("board.voted", lg, n=n, id=bug_id) if on else T("board.unvoted", lg, id=bug_id), ephemeral=True)
        log_event("bug", f"{interaction.user.display_name} {'also has' if on else 'no longer has'} bug #{bug_id} ({n} affected)",
                  interaction.user.id, interaction.user.display_name)
        await sync_bug_messages(get_bug(bug_id))
        await safe_refresh_board(force=True)       # also resets the menu


class BoardView(discord.ui.View):
    def __init__(self, rows=None):
        super().__init__(timeout=None)
        self.add_item(BoardSelect(store.board_bugs(25) if rows is None else rows))
        self.report.label = T("board.report")[:80]

    @discord.ui.button(label="Report a new bug", emoji=BUG_EMOJI, style=discord.ButtonStyle.secondary, custom_id="board:report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        lg = ilang(interaction)
        why = report_gate(interaction.user, "bug", lg)
        if why:
            return await interaction.response.send_message(_cap(why), ephemeral=True)
        await interaction.response.send_modal(BugFormModal(lg, store.get_link(interaction.user.id)))


async def refresh_board(force: bool = False):
    """Create or update the Known issues board message. Only edits Discord when something changed."""
    ch = chan("board_channel_id")
    if ch is None:
        return
    rows = store.board_bugs(25)
    e = board_embed()
    sig = hashlib.sha1((json.dumps(e.to_dict(), sort_keys=True) + T("board.pick")).encode()).hexdigest()
    mid, where = get_raw("board_msg_id"), get_raw("board_chan")
    if mid and where == str(ch.id):
        if get_raw("board_sig") == sig and not force:
            return
        try:
            await ch.get_partial_message(int(mid)).edit(embed=e, view=BoardView(rows))
            put_raw("board_sig", sig)
            return
        except discord.NotFound:
            pass
    msg = await ch.send(embed=e, view=BoardView(rows), allowed_mentions=discord.AllowedMentions.none())
    put_raw("board_msg_id", str(msg.id))
    put_raw("board_chan", str(ch.id))
    put_raw("board_sig", sig)


async def safe_refresh_board(force: bool = False):
    try:
        await refresh_board(force)
    except Exception as exc:  # noqa: BLE001 – the board must never break a status change
        print(f"[board] {exc!r}")


class SimilarVoteSelect(discord.ui.Select):
    """In the "this may already be reported" answer: pick the matching bug instead of sending a new report."""

    def __init__(self, lg, similar, parent):
        opts = [discord.SelectOption(label=f"#{s['id']} {s['title']}"[:100], value=str(s["id"]))
                for s in similar if s["status"] in ("passed", "being_investigated")][:25]
        super().__init__(placeholder=T("form.metoo", lg)[:150], options=opts or [discord.SelectOption(label="–", value="0")],
                         disabled=not opts, row=1)
        self.lg, self.owner = lg, parent

    async def callback(self, interaction: discord.Interaction):
        bug_id = int(self.values[0])
        r = get_bug(bug_id) if bug_id else None
        if r is None or r["status"] not in ("passed", "being_investigated"):
            return await interaction.response.edit_message(content=T("board.gone", self.lg), view=self.owner)
        store.add_vote(bug_id, interaction.user.id)
        n = store.vote_count(bug_id)
        await interaction.response.edit_message(content=T("board.voted", self.lg, n=n, id=bug_id), view=None)
        log_event("bug", f"{interaction.user.display_name} also has bug #{bug_id} (from the report form, {n} affected)",
                  interaction.user.id, interaction.user.display_name)
        self.owner.stop()
        await sync_bug_messages(r)
        await safe_refresh_board()


# ------------------------------------------------------------ automatic gift-code redeem
_redeem_lock = asyncio.Lock()
_redeem_task = None


def redeem_ready() -> bool:
    return bool(cfg("redeem_enabled") and (cfg("redeem_url") or "").strip())


def _redeem_headers() -> dict:
    h = {}
    for line in (get_raw("redeem_header") or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip():
                h[k.strip()] = v.strip()
    return h


async def redeem_one(code: str, uid: str, session=None):
    """Ask the game server to redeem one code for one player. Returns (status, message):
    ok | already | failed."""
    import aiohttp
    from urllib.parse import quote
    url = (cfg("redeem_url") or "").strip().replace("{uid}", quote(uid)).replace("{code}", quote(code))
    if not url.lower().startswith(("http://", "https://")):
        return "failed", "The redeem address must start with http:// or https://"
    esc = lambda v: json.dumps(v)[1:-1]
    body = (cfg("redeem_body") or "").replace("{uid}", esc(uid)).replace("{code}", esc(code))
    headers = _redeem_headers()
    method = cfg("redeem_method")
    kw = {}
    if method == "POST" and body.strip():
        try:
            kw["json"] = json.loads(body)
        except ValueError:
            kw["data"] = body
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    try:
        async with session.request(method, url, headers=headers, **kw) as resp:
            text = (await resp.text(errors="replace"))[:2000]
            status = resp.status
    except Exception as exc:  # noqa: BLE001 – network trouble is a normal "failed"
        return "failed", f"Could not reach the game server: {exc.__class__.__name__}"
    finally:
        if own:
            await session.close()
    low = text.lower()
    already, ok_text = (cfg("redeem_already_text") or "").strip().lower(), (cfg("redeem_ok_text") or "").strip().lower()
    if already and already in low:
        return "already", text[:200]
    if 200 <= status < 300 and (not ok_text or ok_text in low):
        return "ok", text[:200]
    return "failed", f"HTTP {status}: {text[:200]}"


def redeem_queue(limit: int = 250) -> list:
    """(code, uid, discord_id) pairs still to do: every active code × every linked player.
    Failed tries are retried after 6 hours."""
    codes = [r["code"] for r in store.active_gift_codes()]
    if not codes:
        return []
    links = db.execute("SELECT discord_id, player_uid FROM links").fetchall()
    retry_before = int(time.time()) - 6 * 3600
    done = {(r[0], r[1]) for r in db.execute(
        "SELECT code, uid FROM redemptions WHERE status IN ('ok','already') OR ts>?", (retry_before,))}
    out = []
    for lk in links:
        for c in codes:
            if (c, lk["player_uid"]) not in done:
                out.append((c, lk["player_uid"], lk["discord_id"]))
                if len(out) >= limit:
                    return out
    return out


async def run_redeem(limit: int = 250) -> dict:
    """Work through the queue (one at a time, with the delay from the dashboard). Returns counts."""
    import aiohttp
    counts = {"ok": 0, "already": 0, "failed": 0}
    if not redeem_ready() or _redeem_lock.locked():
        return counts
    async with _redeem_lock:
        todo = redeem_queue(limit)
        if not todo:
            return counts
        per_code = {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            for i, (code, uid, did) in enumerate(todo):
                if not redeem_ready():
                    break
                if i:
                    await asyncio.sleep(cfg("redeem_delay"))
                status, msg = await redeem_one(code, uid, session)
                store.save_redemption(code, uid, did, status, msg)
                counts[status] += 1
                per_code.setdefault(code, {"ok": 0, "already": 0, "failed": 0})[status] += 1
                if status == "ok" and cfg("redeem_dm") and did:
                    await dm_t(did, "dm.redeemed", code=code)
        for code, c in per_code.items():
            log_event("system", f"Gift code {code} auto-redeemed: {c['ok']} done, {c['already']} already had it"
                      + (f", {c['failed']} failed" if c["failed"] else ""), None, brand())
    return counts


def start_redeem():
    """Kick off a redeem run in the background (no-op if one is running)."""
    global _redeem_task
    if not redeem_ready() or (_redeem_task is not None and not _redeem_task.done()):
        return
    _redeem_task = asyncio.get_running_loop().create_task(_redeem_bg())


async def _redeem_bg():
    try:
        await run_redeem()
    except Exception as exc:  # noqa: BLE001
        print(f"[redeem] {exc!r}")


# ------------------------------------------------------------ moderator stats
def fmt_hours(h) -> str:
    if h is None:
        return "–"
    return f"{h / 24:.1f} days" if h >= 48 else (f"{h:g} h" if h >= 1 else f"{round(h * 60)} min")


def modstats_embed(days: int = 30) -> discord.Embed:
    s = store.mod_stats(days)
    e = discord.Embed(title=f"🛡️ Moderator stats – last {days} days", color=0x5865F2)
    e.add_field(name="First answer (mods)", value=f"{fmt_hours(s['mod_hours'])} median · {s['mod_count']} bugs", inline=True)
    e.add_field(name="Admin answer", value=f"{fmt_hours(s['admin_hours'])} median · {s['admin_count']} bugs", inline=True)
    e.add_field(name="Answered within 24 h", value=f"{s['within_day']}%" if s["within_day"] is not None else "–", inline=True)
    if s["staff"]:
        e.add_field(name="Who did the work", value="\n".join(
            f"**{p['name']}** – {p['handled']} handled" + (f" · first answer {fmt_hours(p['first_hours'])}" if p["first_count"] else "")
            for p in s["staff"][:10])[:1024], inline=False)
    if s["waiting"]:
        e.add_field(name=f"⏰ Waiting over {s['wait_hours']} h ({s['waiting_total']})", value="\n".join(
            f"{'🐛' if w['kind'] == 'bug' else '💡'} #{w['id']} {w['title'][:50]} – {w['who']}, <t:{w['since']}:R>"
            for w in s["waiting"][:10])[:1024], inline=False)
    else:
        e.add_field(name="⏰ Waiting", value="Nothing has waited over 24 h. 🎉", inline=False)
    e.set_footer(text=brand())
    return e


@bot.tree.command(name="modstats", description="How fast reports are answered and who handles them (admins)", guild=_GUILD)
@app_commands.describe(days="How many days back (default 30)")
@app_commands.guild_only()
async def modstats_cmd(interaction: discord.Interaction, days: Optional[app_commands.Range[int, 1, 365]] = 30):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.send_message(embed=modstats_embed(days or 30), ephemeral=True)


@bot.tree.command(name="changed", description="Tell players what you changed for an accepted suggestion (moderators)", guild=_GUILD)
@app_commands.describe(suggestion="The suggestion number", text="What you actually changed in the game")
@app_commands.guild_only()
async def changed_cmd(interaction: discord.Interaction, suggestion: int, text: app_commands.Range[str, 3, 1000]):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        await post_suggestion_changes(suggestion, text, interaction.user.id, interaction.user.display_name)
    except ValueError as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"✅ Suggestion #{suggestion}: update posted in the suggestions channel and sent to the author.",
                                    ephemeral=True)


# ------------------------------------------------------------ Discord log channel
# Every entry of the activity log is also posted to a staff channel (if one is chosen): who did what, and when.
# Entries are collected for a few seconds and sent together, so a busy minute doesn't hit Discord's limits.
LOG_STYLE = {"bug": ("🐛", 0x4F46E5), "ticket": ("🎫", 0x0284C7), "mod": ("🛡️", 0xD97706), "admin": ("👑", 0xEA580C),
             "suggestion": ("💡", 0x0D9488), "welcome": ("👋", 0x0891B2), "panel": ("📌", 0x7C3AED), "settings": ("⚙️", 0x64748B),
             "patch": ("🩹", 0x16A34A), "block": ("⛔", 0xBE123C), "profile": ("🤖", 0xDB2777), "system": ("🖥️", 0xDC2626),
             "backup": ("💾", 0x475569), "link": ("🔗", 0x0F766E), "invite": ("📨", 0x7DE3F4), "guard": ("🛡️", 0xFF6B81)}
_log_queue: list = []


def queue_log(entry: dict):
    if not cfg("log_channel_id"):
        return
    kinds = [k.strip().lower() for k in (cfg("log_channel_kinds") or "all").splitlines() if k.strip()]
    if "all" not in kinds and entry["kind"] not in kinds:
        return
    if len(_log_queue) < 500:
        _log_queue.append(entry)


store.log_listeners.append(queue_log)


def log_line(e: dict) -> str:
    emoji = LOG_STYLE.get(e["kind"], ("•", 0))[0]
    who = (f"<@{e['actor_id']}>" if e["actor_id"] else (e["actor_name"] or "")) or "—"
    return f"<t:{e['ts']}:T> {emoji} {e['text'][:300]}\n-# {who}"


async def flush_logs():
    """Send what is waiting: one embed per kind of change, up to 10 embeds per message."""
    ch = chan("log_channel_id")
    if ch is None:
        _log_queue.clear()
        return 0
    if not _log_queue:
        return 0
    batch = _log_queue[:40]
    del _log_queue[:40]
    embeds, cur, cur_kind = [], [], None
    for e in batch + [None]:
        if e is None or (cur and (e["kind"] != cur_kind or sum(len(x) for x in cur) > 3500)):
            if cur:
                label = cur_kind.capitalize()
                em = discord.Embed(description="\n".join(cur)[:4000], color=LOG_STYLE.get(cur_kind, ("", 0x5865F2))[1])
                em.set_author(name=f"{label} · {len(cur)}" if len(cur) > 1 else label)
                embeds.append(em)
            cur = []
        if e is not None:
            cur_kind = e["kind"]
            cur.append(log_line(e))
    for i in range(0, len(embeds), 10):
        try:
            await ch.send(embeds=embeds[i:i + 10], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            print(f"[log channel] {exc!r}")              # never log this (it would loop)
            break
    return len(batch)


@tasks.loop(seconds=6)
async def log_loop():
    try:
        await flush_logs()
    except Exception as exc:  # noqa: BLE001
        print(f"[log channel] {exc!r}")


@log_loop.before_loop
async def _before_log_loop():
    await bot.wait_until_ready()


# ------------------------------------------------------------ sticker pack ("Frostbit")
STICKER_DIR = Path(__file__).with_name("assets") / "stickers"


def sticker_pack() -> list:
    try:
        return json.loads((STICKER_DIR / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def sticker_name(s) -> str:
    return " ".join(w.capitalize() if w.isalpha() else w for w in s["name"].replace("!", "").replace("?", "").replace(".", "").split())[:30] or s["id"]


def sticker_status() -> dict:
    g = bot.get_guild(GUILD_ID)
    have = {x.name.lower() for x in (g.stickers if g else [])}
    return {"items": [{**s, "title": sticker_name(s), "installed": sticker_name(s).lower() in have} for s in sticker_pack()],
            "used": len(g.stickers) if g else 0, "limit": g.sticker_limit if g else 0}


async def install_stickers(ids=None, actor_id=None, actor_name: str = "Dashboard") -> dict:
    """Upload the pack's stickers that the server doesn't have yet (needs "Manage Expressions")."""
    g = bot.get_guild(GUILD_ID)
    if g is None:
        raise ValueError("The bot can't see the server right now.")
    if not g.me.guild_permissions.manage_expressions:
        raise ValueError("Give the bot the “Manage Expressions” permission (Server Settings → Roles) and try again.")
    have = {x.name.lower() for x in g.stickers}
    free = g.sticker_limit - len(g.stickers)
    added, skipped, failed = [], [], []
    for s in sticker_pack():
        if ids and s["id"] not in ids:
            continue
        name = sticker_name(s)
        if name.lower() in have:
            skipped.append(name)
            continue
        if free <= 0:
            failed.append(f"{name}: no free sticker slots")
            continue
        try:
            await g.create_sticker(name=name, description=s.get("desc") or name, emoji=s.get("emoji") or "🙂",
                                   file=discord.File(STICKER_DIR / f"{s['id']}.png"), reason=f"Frostbit pack – {actor_name}")
            added.append(name)
            free -= 1
        except discord.HTTPException as exc:
            failed.append(f"{name}: {exc.text or exc}")
    if added:
        log_event("profile", f"Added {len(added)} sticker{'s' if len(added) != 1 else ''} to the server: {', '.join(added[:8])}"
                  + (" …" if len(added) > 8 else ""), actor_id, actor_name)
    return {"added": added, "skipped": skipped, "failed": failed}


# ------------------------------------------------------------ reasons when closing bugs / suggestions
REASON_LABELS = {"duplicate": "Already reported (duplicate)", "intended": "Works as intended", "cant_repro": "Can't reproduce",
                 "not_bug": "Not a bug – game design", "missing_info": "Missing information", "game_side": "Comes from the game itself",
                 "planned": "Already planned", "not_now": "Not right now", "other": "Other – I'll write it"}
BUG_REASONS = ("duplicate", "intended", "cant_repro", "not_bug", "missing_info", "game_side", "other")
SUG_REASONS = ("duplicate", "planned", "not_now", "game_side", "other")


async def close_with_reason(kind: str, item_id: int, new: str, preset: Optional[str], message: str,
                            duplicate_of: Optional[int], actor_id, who: str):
    """Close a bug/suggestion and tell the player why. Returns a short confirmation for the moderator."""
    try:
        return await _close_with_reason(kind, item_id, new, preset, message, duplicate_of, actor_id, who)
    finally:
        if not item_still_open(kind, item_id, new):
            await drop_prompt(kind, item_id)


async def _close_with_reason(kind: str, item_id: int, new: str, preset: Optional[str], message: str,
                             duplicate_of: Optional[int], actor_id, who: str):
    text = " ".join(x for x in (store.REJECT_PRESETS.get(preset or "", ""), (message or "").strip()) if x).strip()
    if kind == "suggestion":
        r = get_suggestion(item_id)
        if r is None or r["status"] != "new":
            raise ValueError("That suggestion has already been decided.")
        await decide_suggestion(r, "rejected", actor_id, who, reason=text or None)
        return f"Suggestion #{item_id} closed" + (" – the reason was sent to the player." if text else ".")
    r = get_bug(item_id)
    if r is None:
        raise ValueError("That bug no longer exists.")
    if duplicate_of:
        await apply_bug_status(r, "rejected", who, actor_id, duplicate_of=duplicate_of, log_kind="mod", reason=text or None)
        return f"Bug #{item_id} closed as a duplicate of #{duplicate_of}."
    if (new == "rejected" and r["status"] not in MOD_OPEN) or (new == "admin_rejected" and r["status"] not in ADMIN_OPEN):
        raise ValueError("That bug has already moved on.")
    await apply_bug_status(r, new, who, actor_id, log_kind="mod" if new == "rejected" else "admin", reason=text or None)
    return f"Bug #{item_id} closed" + (" – the reason was sent to the player." if text else ".")


class ReasonModal(discord.ui.Modal):
    def __init__(self, kind: str, item_id: int, new: str, prompt_msg=None, dup_first: bool = False):
        super().__init__(title=(f"Close bug #{item_id}" if kind == "bug" else f"Close suggestion #{item_id}")[:45], timeout=900)
        self.kind, self.item_id, self.new, self.prompt_msg = kind, item_id, new, prompt_msg
        keys = BUG_REASONS if kind == "bug" else SUG_REASONS
        self.preset = discord.ui.Select(options=[discord.SelectOption(label=REASON_LABELS[k], value=k, default=(dup_first and k == "duplicate"))
                                                 for k in keys], min_values=1, max_values=1)
        self.msg = discord.ui.TextInput(style=discord.TextStyle.paragraph, required=False, max_length=600,
                                        placeholder="Optional: a few words for the player, e.g. what to try instead")
        self.add_item(discord.ui.Label(text="Why are you closing it?", component=self.preset))
        self.add_item(discord.ui.Label(text="Message to the player", component=self.msg))
        self.dup = None
        if kind == "bug":
            r = get_bug(item_id)
            self.dup = discord.ui.TextInput(required=False, max_length=8, placeholder="e.g. 41",
                                            default=str(r["dup_suggest"]) if r and r["dup_suggest"] else None)
            self.add_item(discord.ui.Label(text="Duplicate of bug number", description="Only when it's already reported",
                                           component=self.dup))

    async def on_submit(self, interaction: discord.Interaction):
        preset = (self.preset.values or ["other"])[0]
        dup_raw = str(self.dup.value or "").strip().lstrip("#") if self.dup else ""
        if dup_raw and not dup_raw.isdigit():
            return await interaction.response.send_message("The duplicate must be a bug number, e.g. 41.", ephemeral=True)
        if preset == "other" and not str(self.msg.value or "").strip():
            return await interaction.response.send_message("Write a short message for the player when you pick “Other”.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            done = await close_with_reason(self.kind, self.item_id, self.new, preset, str(self.msg.value or ""),
                                           int(dup_raw) if dup_raw and self.kind == "bug" else None,
                                           interaction.user.id, interaction.user.display_name)
        except ValueError as exc:
            return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ {done}", ephemeral=True)
        await drop_prompt(self.kind, self.item_id)


class ReasonPromptView(discord.ui.View):
    """Shown after ❌ on a bug or suggestion: the player should learn why it was closed."""

    def __init__(self, kind: str, item_id: int, new: str):
        super().__init__(timeout=900)
        self.kind, self.item_id, self.new = kind, item_id, new
        if kind != "bug":
            self.remove_item(self.dup)
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ok = is_admin(interaction.user) if self.new == "admin_rejected" else is_mod(interaction.user)
        if not ok:
            await interaction.response.send_message("Only staff can close reports.", ephemeral=True)
        return ok

    @discord.ui.button(label="Give a reason", emoji="💬", style=discord.ButtonStyle.primary)
    async def reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal(self.kind, self.item_id, self.new, interaction.message))

    @discord.ui.button(label="Duplicate of…", emoji="🔁", style=discord.ButtonStyle.secondary)
    async def dup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReasonModal(self.kind, self.item_id, self.new, interaction.message, dup_first=True))

    @discord.ui.button(label="Close without a reason", style=discord.ButtonStyle.secondary)
    async def plain(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            done = await close_with_reason(self.kind, self.item_id, self.new, None, "", None, interaction.user.id, interaction.user.display_name)
        except ValueError as exc:
            return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ {done}", ephemeral=True)
        await drop_prompt(self.kind, self.item_id)


_prompts: dict = {}   # (kind, id) -> {"msg", "member_id", "channel_id", "new", "ts"} – one open question per report


def item_still_open(kind: str, item_id: int, new: str) -> bool:
    if kind == "suggestion":
        r = get_suggestion(item_id)
        return r is not None and r["status"] == "new"
    r = get_bug(item_id)
    return r is not None and r["status"] in (ADMIN_OPEN if new == "admin_rejected" else MOD_OPEN)


async def drop_prompt(kind: str, item_id: int):
    """The report was closed (with or without a reason, from Discord or the staff page): remove the question."""
    p = _prompts.pop((kind, item_id), None)
    if p is not None:
        try:
            await p["msg"].delete()
        except Exception:  # noqa: BLE001 – already gone; closing must never fail over this
            pass


async def sweep_prompts():
    """Every minute: questions about reports that were closed some other way, or that expired, disappear."""
    for (kind, item_id), p in list(_prompts.items()):
        if time.time() - p["ts"] > 900 or not item_still_open(kind, item_id, p["new"]):
            await drop_prompt(kind, item_id)


async def prompt_reason(channel, kind: str, item_id: int, member, new: str):
    """Ask once why a report is being rejected. Never asks twice for the same report."""
    label = f"bug #{item_id}" if kind == "bug" else f"suggestion #{item_id}"
    if not item_still_open(kind, item_id, new):
        return
    if (kind, item_id) in _prompts:
        if time.time() - _prompts[(kind, item_id)]["ts"] < 900:
            return                                        # already asked – don't ask again
        await drop_prompt(kind, item_id)
    try:
        msg = await channel.send(f"{member.mention} why are you rejecting **{label}**? Pick a reason below – or just reply to this "
                                 "message with your reason. The player gets it in their DM.",
                                 view=ReasonPromptView(kind, item_id, new), delete_after=900,
                                 allowed_mentions=discord.AllowedMentions(users=[member]))
        _prompts[(kind, item_id)] = {"msg": msg, "member_id": member.id, "channel_id": channel.id, "new": new, "ts": time.time()}
    except discord.HTTPException as exc:
        log_event("system", f"Could not ask for a reason for {label}: {exc}", None, brand())


async def typed_reason(message: discord.Message) -> bool:
    """A moderator answers the question by writing the reason in chat: a reply to the question,
    or their next message in that channel within 5 minutes. Closes the report with that text."""
    if not _prompts or not message.guild or not is_mod(message.author):
        return False
    text = (message.content or "").strip()
    if len(text) < 3 or text.startswith(("/", "!")):
        return False
    ref = message.reference.message_id if message.reference else None
    hit = None
    for key, p in _prompts.items():
        if p["channel_id"] != message.channel.id:
            continue
        if ref and ref == p["msg"].id:
            hit = key; break
        if not ref and p["member_id"] == message.author.id and time.time() - p["ts"] < 300:
            hit = key
    if hit is None:
        return False
    p = _prompts[hit]
    try:
        done = await close_with_reason(hit[0], hit[1], p["new"], None, text[:600], None, message.author.id, message.author.display_name)
    except ValueError as exc:
        await drop_prompt(*hit)
        await message.channel.send(f"⚠️ {exc}", delete_after=20)
        return True
    await drop_prompt(*hit)
    try:
        await message.add_reaction("✅")
    except discord.HTTPException:
        pass
    await message.channel.send(f"✅ {done}", delete_after=20)
    return True


# ------------------------------------------------------------ automatic duplicate detection
def translate_button() -> discord.ui.Button:
    """🌐 Translate under bug and suggestion posts – handled in on_interaction (custom_id tr:item)."""
    return discord.ui.Button(label="Translate", emoji="🌐", style=discord.ButtonStyle.secondary, custom_id="tr:item", row=1)


class TranslateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(translate_button())


def item_for_message(msg_id: int):
    """('bug' | 'suggestion', row) for a bot post in the mod/admin channel, or (None, None)."""
    r = get_bug_by_msg(msg_id) or store.get_bug_by_admin_msg(msg_id)
    if r is not None:
        return "bug", r
    r = store.get_suggestion_by_msg(msg_id)
    return ("suggestion", r) if r is not None else (None, None)


async def translate_item(kind: str, r, target: str):
    """Translate a report's own text (not the embed around it). Only runs when someone presses Translate."""
    text = r["text"] or ""
    if not worth_translating(text):
        return None, ""
    return await translate.text(text, target)


async def handle_translate_button(interaction: discord.Interaction):
    lg = ilang(interaction)
    kind, r = item_for_message(interaction.message.id) if interaction.message else (None, None)
    if r is None:
        return await interaction.response.send_message("I can't find this report any more.", ephemeral=True)
    if not translate.key():
        return await interaction.response.send_message(
            "Translation isn't set up yet – an admin adds the Google key on the dashboard's Translations page.", ephemeral=True)
    target = str(interaction.locale or "en").lower()
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        out, src = await translate_item(kind, r, target)
    except translate.TranslateError as exc:
        return await interaction.followup.send(T("tr.limit", lg) if "limit" in str(exc) else f"⚠️ {exc}", ephemeral=True)
    if out is None:
        return await interaction.followup.send(T("tr.empty", lg), ephemeral=True)
    if translate.same_language(src, target):
        return await interaction.followup.send(T("tr.same", lg, language=lang_label(src)), ephemeral=True)
    name = f"Bug #{r['id']}" if kind == "bug" else f"Suggestion #{r['id']}"
    e = discord.Embed(title=f"🌐 {name}", description=out[:4000], color=0x5865F2, url=interaction.message.jump_url)
    e.set_footer(text=f"{lang_label(src)} → {lang_label(target)} · only you can see this")
    await interaction.followup.send(embed=e, ephemeral=True)


async def safe_task(coro, label: str):
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] {exc!r}")


async def add_translate_buttons():
    """Once after the update: put 🌐 Translate under the open bugs and suggestions that were posted before it."""
    if get_raw("translate_buttons_v1"):
        return
    put_raw("translate_buttons_v1", "1")
    mod, admin = chan("mod_channel_id"), chan("admin_channel_id")
    marks = ",".join("?" * len(store.OPEN_STATUSES))
    for r in store.db.execute(f"SELECT * FROM bug_reports WHERE status IN ({marks}) ORDER BY id DESC LIMIT 150",
                              store.OPEN_STATUSES).fetchall():
        for ch, mid, view in ((mod, r["msg_id"], DupView("confirm", r["dup_suggest"])
                               if r["dup_suggest"] and not r["duplicate_of"] and r["status"] in MOD_OPEN else DupView()),
                              (admin, r["admin_msg_id"], DupView())):
            if ch is not None and mid:
                try:
                    await ch.get_partial_message(mid).edit(view=view)
                except discord.HTTPException:
                    pass
    for r in store.db.execute("SELECT * FROM suggestions WHERE status='new' ORDER BY id DESC LIMIT 100").fetchall():
        if mod is not None and r["msg_id"]:
            try:
                await mod.get_partial_message(r["msg_id"]).edit(view=TranslateView())
            except discord.HTTPException:
                pass


class DupView(discord.ui.View):
    """Buttons on a bug in the mod channel: confirm a suspected duplicate, or reopen one closed automatically.
    Always carries the 🌐 Translate button too."""

    def __init__(self, show: Optional[str] = None, target: Optional[int] = None):
        super().__init__(timeout=None)
        if target:
            self.confirm.label = f"Close as duplicate of #{target}"
        if show != "all":
            if show != "confirm":
                self.remove_item(self.confirm)
            if show != "reopen":
                self.remove_item(self.reopen)
        self.add_item(translate_button())

    @discord.ui.button(label="Close as duplicate", emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="dup:confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        r = get_bug_by_msg(interaction.message.id)
        if not is_mod(interaction.user) or r is None:
            return await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
        if r["status"] not in MOD_OPEN or not r["dup_suggest"]:
            return await interaction.response.edit_message(view=DupView())
        await interaction.response.send_modal(ReasonModal("bug", r["id"], "rejected", dup_first=True))

    @discord.ui.button(label="Not a duplicate – reopen", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dup:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        r = get_bug_by_msg(interaction.message.id)
        if not is_mod(interaction.user) or r is None:
            return await interaction.response.send_message("Only moderators can do this.", ephemeral=True)
        await interaction.response.defer()
        await reopen_bug(r, interaction.user.display_name, interaction.user.id)
        try:
            await interaction.message.edit(view=DupView())
        except discord.HTTPException:
            pass


async def reopen_bug(r, who: str, actor_id=None):
    set_bug(r["id"], status="new", duplicate_of=None, reject_reason=None, dup_suggest=None, mod_pinged=None)
    record_action("bug", r["id"], "reopened", actor_id, who)
    r = get_bug(r["id"])
    await sync_bug_messages(r, f"Reopened by {who}")
    log_event("mod", f"Bug #{r['id']} reopened by {who} – not a duplicate", actor_id, who)
    await dm_t(r["reporter_id"], "dm.reopened", id=r["id"])
    return r


async def scan_duplicates() -> int:
    """Every few minutes: look at open bugs that have no duplicate suggestion yet and compare them with the
    reports that came before. Likely duplicates get flagged (never closed) – on the staff page and in #mod."""
    if cfg("dup_mode") == "off":
        return 0
    mod, n = chan("mod_channel_id"), 0
    for r in store.bugs_needing_dup_check():
        best, pct = store.find_duplicate(r)
        if best is None or pct < 45:
            continue
        set_bug(r["id"], dup_suggest=best["id"], dup_score=pct)
        n += 1
        r = get_bug(r["id"])
        if mod is not None and r["msg_id"] and r["status"] in MOD_OPEN:
            try:
                await mod.get_partial_message(r["msg_id"]).edit(embed=bug_embed(r), view=DupView("confirm", best["id"]))
            except discord.HTTPException:
                pass
    if n:
        log_event("bug", f"Found {n} possible duplicate(s) among the open bugs", None, brand())
    return n


async def dup_dismissed(r):
    """A moderator said 'not a duplicate' on the staff page: take the suggestion off the Discord post too."""
    mod = chan("mod_channel_id")
    if mod is not None and r["msg_id"]:
        try:
            await mod.get_partial_message(r["msg_id"]).edit(embed=bug_embed(r), view=DupView())
        except discord.HTTPException:
            pass


async def check_duplicate(r):
    """Right after a report arrives: close clear duplicates of an open bug (the player is told which one and gets
    a message when it's fixed), or mark likely ones for the moderators. Returns 'auto', 'flag' or None."""
    mode = cfg("dup_mode")
    if mode == "off" or r is None:
        return None
    best, pct = store.find_duplicate(r)
    if best is None or pct < 45:
        return None
    mod = chan("mod_channel_id")
    if mode == "auto" and pct >= cfg("dup_auto_percent"):
        await apply_bug_status(r, "rejected", brand(), None, duplicate_of=best["id"], log_kind="bug",
                               reason=store.REJECT_PRESETS["duplicate"])
        log_event("bug", f"Bug #{r['id']} closed automatically as a duplicate of #{best['id']} ({pct}% match)", None, brand())
        if mod is not None and r["msg_id"]:
            try:
                await mod.get_partial_message(r["msg_id"]).edit(view=DupView("reopen"))
            except discord.HTTPException:
                pass
        return "auto"
    set_bug(r["id"], dup_suggest=best["id"], dup_score=pct)
    r = get_bug(r["id"])
    if mod is not None and r["msg_id"]:
        try:
            await mod.get_partial_message(r["msg_id"]).edit(embed=bug_embed(r), view=DupView("confirm", best["id"]))
        except discord.HTTPException:
            pass
    return "flag"


async def safe_check_duplicate(bug_id: int):
    try:
        return await check_duplicate(get_bug(bug_id))
    except Exception as exc:  # noqa: BLE001 – the report itself is already safe
        print(f"[duplicates] {exc!r}")
        return None


# ------------------------------------------------------------ ping moderators about untouched bugs
async def ping_waiting_bugs(now: Optional[float] = None) -> int:
    """Tag the moderator role once per bug that nobody has reacted to within N hours (default 4)."""
    hours = cfg("mod_ping_hours")
    ch = chan("mod_channel_id")
    if not hours or ch is None:
        return 0
    now = int(now or time.time())
    rows = db.execute("SELECT * FROM bug_reports WHERE status='new' AND msg_id IS NOT NULL AND mod_pinged IS NULL "
                      "AND created_at<=? ORDER BY id", (now - hours * 3600,)).fetchall()
    if not rows:
        return 0
    role = discord.Object(id=MOD_ROLE_ID)
    link = lambda r: f"https://discord.com/channels/{GUILD_ID}/{ch.id}/{r['msg_id']}"
    try:
        if len(rows) == 1:
            r = rows[0]
            await ch.send(f"<@&{MOD_ROLE_ID}> ⏰ **Bug #{r['id']}** has waited {hours} h without a moderator – {store.bug_summary(r)[:80]}",
                          reference=discord.MessageReference(message_id=r["msg_id"], channel_id=ch.id, fail_if_not_exists=False),
                          allowed_mentions=discord.AllowedMentions(roles=[role]))
        else:
            lines = "\n".join(f"• [#{r['id']}]({link(r)}) {store.bug_summary(r)[:60]}" for r in rows[:15])
            more = f"\n…and {len(rows) - 15} more" if len(rows) > 15 else ""
            await ch.send(f"<@&{MOD_ROLE_ID}> ⏰ **{len(rows)} bugs** have waited {hours} h without a moderator:\n{lines}{more}"[:1990],
                          allowed_mentions=discord.AllowedMentions(roles=[role]))
    except discord.HTTPException as exc:
        log_event("system", f"Could not tag the moderators about waiting bugs: {exc}", None, brand())
        return 0
    db.executemany("UPDATE bug_reports SET mod_pinged=? WHERE id=?", [(now, r["id"]) for r in rows])
    db.commit()
    log_event("system", f"Tagged the moderators: {len(rows)} bug(s) waited over {hours} h", None, brand())
    return len(rows)


# ------------------------------------------------------------ scam & phishing guard
# Deletes messages that look like the usual Discord scams (fake Nitro/Steam/gems, look-alike links, hacked
# accounts spamming the same link in several channels), times the sender out, and logs it for the staff.
_URL = re.compile(r"(?:https?://|www\.)([^\s/<>\]\)\"']+)", re.I)
_INVITE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg)/([a-z0-9-]+)", re.I)
_SCAM_WORDS = re.compile(r"(free\s*nitro|nitro\s*(?:for\s*)?free|discord\s*nitro.*(?:free|gift|claim)|steam\s*(?:gift|community).*(?:free|claim)"
                         r"|claim\s+(?:your|the)\s+(?:gift|reward|prize|nitro)|free\s*(?:gems|diamonds|gift\s*codes?|skins?)\s*(?:here|now|at|:)"
                         r"|gift\s*code\s*generator|crypto\s*airdrop|airdrop.*(?:claim|free)|i'?m\s+leaving\s+cs|someone\s+gifted\s+you)", re.I)
_LOOKALIKE = ("discord.com", "discord.gg", "discordapp.com", "discord.gift", "steamcommunity.com", "steampowered.com",
              "centurygame.com", "whiteoutsurvival.com")
_recent_posts: dict = {}          # user_id -> [(ts, channel_id, message_id, fingerprint)]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _host(d: str) -> str:
    d = d.lower().split("@")[-1].split(":")[0].strip(".")
    return d[4:] if d.startswith("www.") else d


def scam_reason(text: str) -> Optional[str]:
    """Why this message looks like a scam, or None."""
    text = text or ""
    allow = {x.strip().lower() for x in (cfg("guard_allow_domains") or "").splitlines() if x.strip()}
    own = re.match(r"https?://([^/:]+)", cfg("giftcodes_site_url") or "")
    if own:
        allow.add(own.group(1).lower())                  # the server's own website is always fine
    hosts = [_host(m.group(1)) for m in _URL.finditer(text)]
    ok = lambda h: any(h == a or h.endswith("." + a) for a in allow)
    for h in hosts:
        if ok(h):
            continue
        if h.startswith("xn--") or ".xn--" in h:
            return f"disguised link ({h})"
        for real in _LOOKALIKE:
            if h != real and (_edit_distance(h, real) <= 3 or (real.split(".")[0] in h and not h.endswith(real))):
                return f"look-alike of {real} ({h})"
        if re.search(r"(nitro|free-?gift|steam-?gift|gift-?code|claim|airdrop)", h):
            return f"scam-style address ({h})"
    if hosts and _SCAM_WORDS.search(text):
        return "fake giveaway / free Nitro or gems with a link"
    if hosts and re.search(r"@(everyone|here)", text) and re.search(r"(free|gift|nitro|giveaway|claim)", text, re.I):
        return "mass ping with a giveaway link"
    if cfg("guard_block_invites"):
        for code in _INVITE.findall(text):
            if code.lower() not in {c.lower() for c in _invite_uses} | {"wos-eu1"}:
                return f"invite to another server ({code})"
    return None


def _burst(message) -> list:
    """The same link posted in 3+ channels within a minute (typical for hacked accounts): returns those messages."""
    if not _URL.search(message.content or ""):
        return []
    now = time.time()
    fp = re.sub(r"\s+", " ", (message.content or "").lower())[:200]
    items = [x for x in _recent_posts.get(message.author.id, []) if now - x[0] < 60] + [(now, message.channel.id, message.id, fp)]
    _recent_posts[message.author.id] = items[-10:]
    same = [x for x in items if x[3] == fp]
    return same if len({x[1] for x in same}) >= 3 else []


def _defang(s: str) -> str:
    return re.sub(r"https?://", "hxxp://", s).replace(".", "[.]")[:300]


async def guard_message(message) -> bool:
    """True when the message was removed."""
    if not cfg("guard_enabled") or message.guild is None or message.guild.id != GUILD_ID:
        return False
    if is_mod(message.author) or getattr(message.author, "bot", False):
        return False
    reason = scam_reason(message.content)
    burst = _burst(message) if not reason else []
    if burst:
        reason = f"same link posted in {len({x[1] for x in burst})} channels within a minute (hacked account?)"
    if not reason:
        return False
    removed = 0
    for cid, mid in ({(message.channel.id, message.id)} | {(x[1], x[2]) for x in burst}):
        ch = bot.get_channel(cid)
        try:
            await (ch.get_partial_message(mid) if ch else message).delete()
            removed += 1
        except discord.HTTPException:
            pass
    minutes = cfg("guard_timeout_minutes")
    timed = False
    if minutes and isinstance(message.author, discord.Member):
        try:
            await message.author.timeout(timedelta(minutes=minutes), reason=f"Scam guard: {reason}")
            timed = True
        except discord.HTTPException:
            pass
    await dm_t(message.author.id, "dm.guard", minutes=minutes if timed else 0)
    log_event("guard", f"Removed a message from {message.author.display_name} in #{getattr(message.channel, 'name', '?')}: {reason}"
              + (f" – timed out for {minutes} min" if timed else "") + f". Text: {_defang(message.content)}",
              message.author.id, message.author.display_name)
    return True


# ------------------------------------------------------------ invite tracker
# Remembers how often each invite link was used; when someone joins, the link whose count went up is theirs.
# Needs the bot permission "Manage Server" to read the invite list.
_invite_uses: dict = {}            # code -> (uses, inviter_id, max_uses)
_vanity_uses = [None]


_invite_gone: dict = {}            # code -> (uses, inviter_id, max_uses, deleted_at) – a one-time link is deleted the moment it's used
_invite_pool: list = []            # [(code, inviter_id)] uses we have seen but not yet matched to a join
_invite_lock = asyncio.Lock()


def _snapshot_invites():
    """Kept in the database, so joins while the bot was restarting can still be counted afterwards."""
    put_raw("invite_snapshot", json.dumps({"ts": int(time.time()), "codes": {c: list(v) for c, v in _invite_uses.items()},
                                          "vanity": _vanity_uses[0]}))


async def _fetch_invites(g) -> Optional[dict]:
    try:
        return {i.code: (i.uses or 0, i.inviter.id if i.inviter else None, i.max_uses or 0) for i in await g.invites()}
    except discord.HTTPException:
        return None


async def cache_invites(guild=None) -> bool:
    """On start: read every invite link. Joins that happened while the bot was offline are counted from the saved snapshot."""
    g = guild or bot.get_guild(GUILD_ID)
    if g is None:
        return False
    now = await _fetch_invites(g)
    if now is None:
        return False
    try:
        snap = json.loads(get_raw("invite_snapshot") or "{}")
    except ValueError:
        snap = {}
    _invite_uses.clear(); _invite_uses.update(now)
    if "VANITY_URL" in g.features:
        try:
            _vanity_uses[0] = (await g.vanity_invite()).uses
        except discord.HTTPException:
            pass
    if snap.get("codes") and cfg("invites_enabled"):
        try:
            await catch_up_joins(g, snap, now)
        except Exception as exc:  # noqa: BLE001
            print(f"[invites catch-up] {exc!r}")
    _snapshot_invites()
    return True


async def catch_up_joins(g, snap: dict, now: dict):
    """Members who joined while the bot was offline: give each one to an invite link that was used in that time."""
    before = {c: tuple(v) for c, v in snap["codes"].items()}
    credits = []
    for c, v in now.items():
        credits += [(c, v[1])] * max(0, v[0] - before.get(c, (0,))[0])
    for c, v in before.items():                          # one-time links used up while offline
        if c not in now and v[2] and v[0] + 1 >= v[2]:
            credits.append((c, v[1]))
    since = snap.get("ts") or 0
    known = {r["member_id"] for r in store.db.execute("SELECT member_id FROM invite_joins WHERE ts>=?", (since - 60,)).fetchall()}
    missed = sorted((m for m in g.members if not m.bot and m.joined_at and m.joined_at.timestamp() > since and m.id not in known),
                    key=lambda m: m.joined_at)
    days = cfg("invites_fake_days")
    n = 0
    for m in missed:
        code, inviter = credits.pop(0) if credits else (None, None)
        fake = bool(days) and (m.joined_at - m.created_at).days < days or (inviter == m.id)
        store.record_join(m.id, m.display_name, inviter, code, fake)
        n += 1
    if n:
        log_event("invite", f"Counted {n} member(s) who joined while the bot was restarting", None, brand())


def _take_credit(member_id: int):
    """Match the oldest unclaimed invite use to this join. When several people joined at the same moment the
    totals per inviter stay right even if the exact pairing can't be known."""
    if not _invite_pool:
        return None, None
    return _invite_pool.pop(0)


async def find_inviter(member):
    """(inviter_id, code) for a member who just joined, or (None, None) / (None, 'vanity')."""
    g = member.guild
    async with _invite_lock:                             # joins at the same moment are handled one by one
        before = dict(_invite_uses)
        now = await _fetch_invites(g)
        if now is None:
            return None, None
        _invite_uses.clear(); _invite_uses.update(now)
        for c, v in now.items():
            _invite_pool.extend([(c, v[1])] * max(0, v[0] - before.get(c, (0,))[0]))
        cutoff = time.time() - 120
        for c, v in list(_invite_gone.items()):          # a one-time link that ran out (Discord deletes it on use)
            if v[3] < cutoff:
                _invite_gone.pop(c, None)
        if not _invite_pool:
            gone = [c for c, v in before.items() if c not in now and v[2] and v[0] + 1 >= v[2]]
            gone += [c for c, v in _invite_gone.items() if c not in gone and v[2] and v[0] + 1 >= v[2]]
            if gone:
                c = gone[0]
                v = before.get(c) or _invite_gone.get(c)
                _invite_gone.pop(c, None)
                _snapshot_invites()
                return v[1], c
        if _invite_pool:
            res = _take_credit(member.id)
            _snapshot_invites()
            return res[1], res[0]
        if "VANITY_URL" in g.features:
            try:
                v = (await g.vanity_invite()).uses
                old = _vanity_uses[0]
                _vanity_uses[0] = v
                if old is not None and v > old:
                    _snapshot_invites()
                    return None, "vanity"
            except discord.HTTPException:
                pass
        _snapshot_invites()
        return None, None


async def track_join(member):
    if not cfg("invites_enabled"):
        return
    inviter_id, code = await find_inviter(member)
    days = cfg("invites_fake_days")
    fake = bool(days) and (discord.utils.utcnow() - member.created_at).days < days or inviter_id == member.id   # new account or self-invite
    store.record_join(member.id, member.display_name, inviter_id, code, fake)
    if inviter_id:
        c = store.invite_counts(inviter_id)
        text = f"{member.display_name} joined – invited by <@{inviter_id}> ({c['regular']} invite{'s' if c['regular'] != 1 else ''})"
        im = member.guild.get_member(inviter_id)
        name = im.display_name if im else f"user …{str(inviter_id)[-4:]}"
        log_event("invite", f"{member.display_name} joined – invited by {name} (now {c['regular']} invites, link {code})", inviter_id, name)
    else:
        text = f"{member.display_name} joined" + (" through the server's own link" if code == "vanity" else " – inviter unknown")
        log_event("invite", text, member.id, member.display_name)
    ch = chan("invites_channel_id")
    if ch is not None:
        age = (discord.utils.utcnow() - member.created_at).days
        why = "invited themselves" if inviter_id == member.id else f"account is only {age} day{'s' if age != 1 else ''} old"
        e = discord.Embed(description=f"📥 {member.mention} joined" + (f" – invited by <@{inviter_id}>" if inviter_id else
                          " via the server link" if code == "vanity" else " – inviter unknown"),
                          color=0xF0A33A if fake else 0x5BE0A8)
        e.add_field(name="Account", value=f"⚠️ **Fake** – {why}" if fake else f"✅ Real – {age} day{'s' if age != 1 else ''} old", inline=True)
        if code and code != "vanity":
            e.add_field(name="Link", value=f"`{code}`", inline=True)
        if inviter_id:
            e.set_footer(text=f"{c['regular']} invites · {c['total']} joined · {c['left']} left · {c['fake']} fake"
                              + (f" · {c['bonus']} bonus" if c.get("bonus") else ""))
        try:
            await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


async def track_leave(member):
    if not cfg("invites_enabled"):
        return
    r = store.record_leave(member.id)
    if r is not None and r["inviter_id"]:
        im = member.guild.get_member(r["inviter_id"])
        name = im.display_name if im else f"user …{str(r['inviter_id'])[-4:]}"
        log_event("invite", f"{member.display_name} left – had been invited by {name}", r["inviter_id"], name)
    ch = chan("invites_channel_id")
    if ch is not None and r is not None:
        try:
            await ch.send(embed=discord.Embed(description=f"📤 {member.mention} left" + (f" – had been invited by <@{r['inviter_id']}>" if r["inviter_id"] else "")
                                              + (" · ⚠️ was a fake account" if r["fake"] else ""),
                                              color=0x5C6370), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


invites_group = app_commands.Group(name="invites", description="Who invited whom", guild_ids=[GUILD_ID], guild_only=True)


@invites_group.command(name="show", description="How many people someone has invited")
@app_commands.describe(user="Someone else (optional)")
async def invites_show(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    who = user or interaction.user
    c = store.invite_counts(who.id)
    await interaction.response.send_message(
        f"📨 {who.mention} has **{c['regular']}** invite{'s' if c['regular'] != 1 else ''}"
        f" ({c['total']} joined · {c['left']} left" + (f" · {c['fake']} new accounts" if c["fake"] else "")
        + (f" · {c['bonus']} bonus" if c.get("bonus") else "") + ")",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@invites_group.command(name="bonus", description="Admins: give or take bonus invites")
@app_commands.describe(user="Who gets the bonus", amount="How many – use a minus to take away, e.g. -2")
async def invites_bonus(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, -1000, 1000]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Only admins can give bonus invites.", ephemeral=True)
    c = store.add_invite_bonus(user.id, amount, user.display_name)
    who = interaction.user.display_name
    log_event("invite", f"{who} gave {user.display_name} {amount:+d} bonus invite(s) – now {c['regular']} invites", interaction.user.id, who)
    await interaction.response.send_message(f"✅ {user.mention} {'got' if amount >= 0 else 'lost'} **{abs(amount)}** bonus invite"
                                            f"{'s' if abs(amount) != 1 else ''} – now **{c['regular']}** invites ({c['bonus']} bonus).",
                                            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@invites_group.command(name="top", description="Who has invited the most people")
async def invites_top(interaction: discord.Interaction):
    rows = store.invite_top(10)
    e = discord.Embed(title="📨 Top inviters", color=0x7DE3F4)
    e.description = "\n".join(f"**{i + 1}.** <@{r['inviter_id']}> – **{r['regular']}**" + (f" ({r['left']} left)" if r["left"] else "")
                              for i, r in enumerate(rows)) or "Nobody has invited anyone yet."
    e.set_footer(text="Counts people who are still here" + (" – including the counts carried over from Invite Tracker."
                     if store.invite_base_info()["players"] else ", from the day the bot started tracking."))
    await interaction.response.send_message(embed=e, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


# ---- carry over the counts from the old Invite Tracker bot, so nobody starts from zero
_IT_LINE = re.compile(r"<@!?(\d{15,21})>[^\n(]*?\(\s*(-?\d+)\s*regular\s*,\s*(-?\d+)\s*left\s*,\s*(-?\d+)\s*fake\s*,\s*(-?\d+)\s*bonus\s*\)", re.I)
_import_watch: dict = {}      # leaderboard message id -> (until, interaction) – next pages are imported as they are opened


def parse_invite_tracker(message_or_embeds) -> list:
    """[(user_id, joined, left, fake, bonus)] from an Invite Tracker leaderboard or /invites message."""
    embeds = getattr(message_or_embeds, "embeds", message_or_embeds) or []
    parts = [getattr(message_or_embeds, "content", "") or ""]
    for e in embeds:
        parts += [e.title or "", e.description or ""] + [f"{f.name}\n{f.value}" for f in e.fields]
    text = re.sub(r"[*_`]", "", "\n".join(parts))
    found = {}
    for m in _IT_LINE.finditer(text):
        found[int(m.group(1))] = tuple(int(x) for x in m.groups()[1:])
    return [(uid, *v) for uid, v in found.items()]


def import_invite_rows(rows, guild=None) -> list:
    out = []
    for uid, joined, left, fake, bonus in rows:
        m = guild.get_member(uid) if guild else None
        c = store.set_invite_base(uid, joined, left, fake, bonus, m.display_name if m else "")
        out.append((uid, c["regular"]))
    return out


@bot.tree.context_menu(name="Import invite counts", guild=_GUILD)
async def import_invites_menu(interaction: discord.Interaction, message: discord.Message):
    """Admins: right-click (long-press) Invite Tracker's leaderboard → Apps → Import invite counts."""
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Only admins can import invite counts.", ephemeral=True)
    rows = parse_invite_tracker(message)
    if not rows:
        return await interaction.response.send_message(
            "I couldn't find any invite counts in that message. Use it on Invite Tracker's `/leaderboard invites` "
            "(or `/invites`) message – the lines that say “… regular, … left, … fake, … bonus”.", ephemeral=True)
    done = import_invite_rows(rows, interaction.guild)
    _import_watch[message.id] = (time.time() + 14 * 60, interaction)
    who = interaction.user.display_name
    log_event("invite", f"{who} carried over the invite counts of {len(done)} player(s) from Invite Tracker", interaction.user.id, who)
    lines = "\n".join(f"<@{u}> – **{n}** invites" for u, n in done[:10])
    await interaction.response.send_message(
        f"✅ Carried over **{len(done)}** player(s):\n{lines}\n\nNow press ▶ on the leaderboard to go through the next pages – "
        "I pick up every page you open for the next 14 minutes. Importing a page twice is safe.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def on_leaderboard_edit(payload: discord.RawMessageUpdateEvent):
    w = _import_watch.get(payload.message_id)
    if not w:
        return
    until, interaction = w
    if time.time() > until:
        _import_watch.pop(payload.message_id, None)
        return
    data = payload.data or {}
    embeds = [discord.Embed.from_dict(e) for e in data.get("embeds") or []]
    rows = parse_invite_tracker(embeds)
    if not rows:
        return
    done = import_invite_rows(rows, bot.get_guild(GUILD_ID))
    who = interaction.user.display_name
    log_event("invite", f"{who} carried over the invite counts of {len(done)} more player(s) from Invite Tracker", interaction.user.id, who)
    try:
        await interaction.followup.send(f"✅ Next page: carried over **{len(done)}** more player(s) "
                                        f"({', '.join(f'<@{u}> {n}' for u, n in done[:10])}).",
                                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


bot.tree.add_command(invites_group)


# ------------------------------------------------------------ phone notifications (web push)
# Players who press "Notify me" on the website (or in the installed app) get a notification on their phone for
# new gift codes and the weekly post. The keys are made once and kept in the database.
try:
    from pywebpush import webpush, WebPushException
except ImportError:  # the update installs it; until then notifications are simply off
    webpush = None


def vapid_keys():
    """(private PEM, public key for browsers) – created the first time they are needed."""
    if get_raw("push_vapid_private") and get_raw("push_vapid_public"):
        return get_raw("push_vapid_private"), get_raw("push_vapid_public")
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    pub = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    put_raw("push_vapid_private", pem)
    put_raw("push_vapid_public", pub)
    return pem, pub


def push_ready() -> bool:
    return webpush is not None and bool(cfg("push_enabled"))


async def push_send(topic: str, title: str, body: str, url: str = "index.html") -> dict:
    """Send one notification to every device signed up for the topic. Dead devices are forgotten."""
    counts = {"sent": 0, "gone": 0, "failed": 0}
    if not push_ready():
        return counts
    rows = store.push_targets(topic)
    if not rows:
        return counts
    pem, _ = vapid_keys()
    from py_vapid import Vapid01
    vapid = Vapid01.from_pem(pem.encode())
    data = json.dumps({"title": title[:80], "body": body[:200], "url": url, "tag": topic})
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(8)

    def one(r):
        try:
            webpush({"endpoint": r["endpoint"], "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}}, data,
                    vapid_private_key=vapid, vapid_claims={"sub": "mailto:noreply@wos-eu1.invalid"}, ttl=86400, timeout=10)
            return "sent"
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            return "gone" if code in (404, 410) else "failed"
        except Exception:  # noqa: BLE001
            return "failed"

    async def run(r):
        async with sem:
            res = await loop.run_in_executor(None, one, r)
        counts[res] += 1
        if res == "gone":
            store.push_unsubscribe(r["endpoint"])

    await asyncio.gather(*(run(r) for r in rows))
    log_event("system", f"Phone notification “{title[:60]}” sent to {counts['sent']} device(s)"
              + (f", {counts['gone']} removed" if counts["gone"] else "") + (f", {counts['failed']} failed" if counts["failed"] else ""), None, brand())
    return counts


def push_soon(topic: str, title: str, body: str, url: str = "index.html"):
    """Fire and forget (never slows down what triggered it)."""
    if not push_ready():
        return
    async def go():
        try:
            await push_send(topic, title, body, url)
        except Exception as exc:  # noqa: BLE001
            print(f"[push] {exc!r}")
    asyncio.get_running_loop().create_task(go())


# ------------------------------------------------------------ weekly public post ("This week in WOS EU1")
def weekly_embed(d: dict) -> discord.Embed:
    from datetime import datetime
    start = datetime.fromtimestamp(d["since"], store.tz()).strftime("%d %b")
    end = datetime.now(store.tz()).strftime("%d %b")
    e = discord.Embed(title=f"❄️ This week in {brand()}", description=f"{start} – {end} · thanks for playing, chiefs!", color=0x7DE3F4)
    fx = f"**{d['fixed_total']}** bug{'s' if d['fixed_total'] != 1 else ''} fixed"
    if d["fixed_public"]:
        fx += "\n" + "\n".join(f"✔️ {x['title'][:80]}" for x in d["fixed_public"][:8])
        if d["fixed_total"] > len(d["fixed_public"][:8]):
            fx += f"\n-# …and {d['fixed_total'] - len(d['fixed_public'][:8])} more"
    e.add_field(name="🩹 Fixes", value=fx[:1024], inline=False)
    if d["codes"]:
        e.add_field(name="🎁 New gift codes", value="\n".join(f"`{c['code']}`" + (f" – {c['reward']}" if c["reward"] else "")
                                                       + (f" · until <t:{c['expires']}:R>" if c["expires"] else "") for c in d["codes"])[:1024]
                    + f"\n-# {d['codes_active']} codes are active right now – /codes", inline=False)
    if d["ideas"]:
        e.add_field(name="💡 Your ideas we added", value="\n".join(f"• {x['changes'][:120]}" for x in d["ideas"][:5])[:1024], inline=False)
    elif d["ideas_accepted"]:
        e.add_field(name="💡 Ideas", value=f"{d['ideas_accepted']} player idea{'s' if d['ideas_accepted'] != 1 else ''} accepted this week", inline=False)
    if d["known"]:
        e.add_field(name="🔍 Still working on", value="\n".join(f"• {k['title'][:80]}" + (f" (👥 {k['votes']})" if k["votes"] else "")
                                                         for k in d["known"])[:1024], inline=False)
    e.add_field(name="🐛 Reports", value=f"{d['reports']} received – keep them coming", inline=True)
    if d["members"]:
        e.add_field(name="👥 Members", value=f"{d['members']:,}" + (f" ({d['joined'] - d['left']:+})" if d["joined"] or d["left"] else ""), inline=True)
    site = (cfg("giftcodes_site_url") or "").rsplit("/", 1)[0]
    e.set_footer(text=brand() + (f" · {site}/" if site.startswith("http") else ""))
    return e


async def post_weekly(force: bool = False):
    ch = chan("weekly_channel_id")
    if ch is None:
        raise ValueError("Choose a channel for the weekly post first (Channels page).")
    await ch.send(embed=weekly_embed(store.weekly_data()), allowed_mentions=discord.AllowedMentions.none())
    log_event("system", "Weekly post published" + (" (sent manually)" if force else ""), None, brand())
    if not force:
        push_soon("news", "❄️ This week in " + brand(), "Fixes, new gift codes and more – have a look.", "index.html")
    return ch


# ------------------------------------------------------------ animated emoji pack
EMOJI_DIR = Path(__file__).with_name("assets") / "emojis"


def emoji_pack() -> list:
    try:
        return json.loads((EMOJI_DIR / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def emoji_status() -> dict:
    g = bot.get_guild(GUILD_ID)
    have = {e.name.lower() for e in (g.emojis if g else [])}
    animated = sum(1 for e in (g.emojis if g else []) if e.animated)
    return {"items": [{**e, "installed": e["name"].lower() in have} for e in emoji_pack()],
            "used": animated, "limit": g.emoji_limit if g else 0}


async def install_emojis(ids=None, actor_id=None, actor_name: str = "Dashboard") -> dict:
    g = bot.get_guild(GUILD_ID)
    if g is None:
        raise ValueError("The bot can't see the server right now.")
    if not g.me.guild_permissions.manage_expressions:
        raise ValueError("Give the bot the “Manage Expressions” permission (Server Settings → Roles) and try again.")
    have = {e.name.lower() for e in g.emojis}
    free = g.emoji_limit - sum(1 for e in g.emojis if e.animated)
    added, skipped, failed = [], [], []
    for e in emoji_pack():
        if ids and e["id"] not in ids:
            continue
        if e["name"].lower() in have:
            skipped.append(e["name"])
            continue
        if free <= 0:
            failed.append(f"{e['name']}: no free animated emoji slots")
            continue
        try:
            await g.create_custom_emoji(name=e["name"], image=(EMOJI_DIR / f"{e['id']}.gif").read_bytes(), reason=f"Frostline emoji pack – {actor_name}")
            added.append(e["name"])
            free -= 1
        except discord.HTTPException as exc:
            failed.append(f"{e['name']}: {exc.text or exc}")
    if added:
        log_event("profile", f"Added {len(added)} animated emoji{'s' if len(added) != 1 else ''}: {', '.join(':' + a + ':' for a in added[:8])}"
                  + (" …" if len(added) > 8 else ""), actor_id, actor_name)
    return {"added": added, "skipped": skipped, "failed": failed}


# ============================================================ community tools (MEE6-style, all in this one bot)
# ------------------------------------------------------------ role menus: buttons or a dropdown to pick roles
def role_menu_message(m):
    roles = json.loads(m["roles"] or "[]")
    e = discord.Embed(title=m["title"][:256], description=(m["description"] or "")[:3000], color=0x7DE3F4)
    lines = [f"{r.get('emoji') or '•'} <@&{r['role_id']}>" + (f" – {r['desc']}" if r.get("desc") else "") for r in roles]
    if lines:
        e.add_field(name="Roles", value="\n".join(lines)[:1024], inline=False)
    e.set_footer(text=f"{brand()} · " + ("pick one – picking another swaps it" if m["mode"] == "single" else "tap again to remove a role"))
    view = discord.ui.View(timeout=None)
    if m["style"] == "select" and roles:
        opts = [discord.SelectOption(label=(r.get("label") or "Role")[:100], value=str(r["role_id"]), description=(r.get("desc") or None) and r["desc"][:100],
                                     emoji=(discord.PartialEmoji.from_str(r["emoji"]) if r.get("emoji") else None)) for r in roles[:25]]
        view.add_item(discord.ui.Select(custom_id=f"rm:{m['id']}:sel", placeholder="Pick your roles…", options=opts,
                                        min_values=0, max_values=1 if m["mode"] == "single" else len(opts)))
    else:
        for r in roles[:25]:
            view.add_item(discord.ui.Button(custom_id=f"rm:{m['id']}:{r['role_id']}", label=(r.get("label") or "Role")[:80],
                                            emoji=(discord.PartialEmoji.from_str(r["emoji"]) if r.get("emoji") else None),
                                            style=discord.ButtonStyle.secondary))
    return e, view


async def publish_role_menu(mid: int):
    m = store.get_role_menu(mid)
    ch = bot.get_channel(m["channel_id"]) if m and m["channel_id"] else None
    if ch is None:
        raise ValueError("Choose a channel the bot can see.")
    e, view = role_menu_message(m)
    if m["message_id"]:
        try:
            await ch.get_partial_message(m["message_id"]).edit(embed=e, view=view)
            return m["message_id"]
        except discord.NotFound:
            pass
    msg = await ch.send(embed=e, view=view)
    store.save_role_menu(mid, message_id=msg.id)
    return msg.id


async def remove_role_menu(mid: int):
    m = store.get_role_menu(mid)
    if m and m["message_id"]:
        ch = bot.get_channel(m["channel_id"])
        if ch is not None:
            try:
                await ch.get_partial_message(m["message_id"]).delete()
            except discord.HTTPException:
                pass
    store.delete_role_menu(mid)


async def handle_role_menu(interaction: discord.Interaction):
    parts = (interaction.data or {}).get("custom_id", "").split(":")
    m = store.get_role_menu(int(parts[1])) if len(parts) == 3 and parts[1].isdigit() else None
    member = interaction.user
    if m is None or not isinstance(member, discord.Member):
        return await interaction.response.send_message("This role menu isn't active any more.", ephemeral=True)
    menu_ids = [int(r["role_id"]) for r in json.loads(m["roles"] or "[]")]
    have = {r.id for r in member.roles}
    if parts[2] == "sel":
        want = {int(v) for v in (interaction.data.get("values") or []) if str(v).isdigit()} & set(menu_ids)
        add, remove = [i for i in want if i not in have], [i for i in menu_ids if i in have and i not in want]
    else:
        rid = int(parts[2]) if parts[2].isdigit() else 0
        if rid not in menu_ids:
            return await interaction.response.send_message("That role isn't in this menu any more.", ephemeral=True)
        if rid in have:
            add, remove = [], [rid]
        else:
            add, remove = [rid], ([i for i in menu_ids if i in have and i != rid] if m["mode"] == "single" else [])
    g = interaction.guild
    try:
        if add:
            await member.add_roles(*[g.get_role(i) for i in add if g.get_role(i)], reason=f"Role menu: {m['title']}")
        if remove:
            await member.remove_roles(*[g.get_role(i) for i in remove if g.get_role(i)], reason=f"Role menu: {m['title']}")
    except discord.Forbidden:
        return await interaction.response.send_message("I'm not allowed to give that role – an admin needs to move my role above it.", ephemeral=True)
    parts_txt = ([f"✅ Added {' '.join(f'<@&{i}>' for i in add)}"] if add else []) + ([f"➖ Removed {' '.join(f'<@&{i}>' for i in remove)}"] if remove else [])
    await interaction.response.send_message("\n".join(parts_txt) or "Nothing changed.", ephemeral=True)


# ------------------------------------------------------------ language chats + "pick your device" gate
# Everything here only runs when an admin presses a button on the staff page (Access & languages).
LANG_GUESS = {  # channel name (without flag and "-chat") -> (role name, button label)
    "eng": ("English", "English"), "en": ("English", "English"), "english": ("English", "English"),
    "arab": ("Arabic", "العربية"), "ar": ("Arabic", "العربية"), "id": ("Indonesian", "Bahasa Indonesia"),
    "pl": ("Polish", "Polski"), "de": ("German", "Deutsch"), "fr": ("French", "Français"), "ph": ("Filipino", "Filipino"),
    "sp": ("Spanish", "Español"), "es": ("Spanish", "Español"), "tr": ("Turkish", "Türkçe"), "nl": ("Dutch", "Nederlands"),
    "ind": ("India", "India"), "in": ("India", "India"), "hi": ("Hindi", "हिन्दी"), "br": ("Brazil", "Português (BR)"),
    "pt": ("Portuguese", "Português"), "cn": ("Chinese", "中文"), "zh": ("Chinese", "中文"), "ru": ("Russian", "Русский"),
    "it": ("Italian", "Italiano"), "jp": ("Japanese", "日本語"), "kr": ("Korean", "한국어"), "vn": ("Vietnamese", "Tiếng Việt"),
    "th": ("Thai", "ไทย"), "ua": ("Ukrainian", "Українська"), "ro": ("Romanian", "Română"),
}
_LEAD_EMOJI = re.compile(r"^([^\w\s-]+)[-\s]*")


def guess_language(channel_name: str) -> dict:
    """'🇩🇪de-chat' -> {'emoji': '🇩🇪', 'role': 'German', 'label': 'Deutsch', 'chat': True}."""
    m = _LEAD_EMOJI.match(channel_name)
    emoji = m.group(1) if m else ""
    rest = channel_name[m.end():] if m else channel_name
    key = re.sub(r"[-_\s]*chat$", "", rest.lower()).strip("-_ ")
    role, label = LANG_GUESS.get(key, (key.replace("-", " ").title() or channel_name, key.replace("-", " ").title() or channel_name))
    return {"emoji": emoji, "role": role, "label": label, "chat": bool(re.search(r"chat$", rest.lower())) and key not in ("general", "")}


def access_cfg() -> dict:
    try:
        return json.loads(get_raw("access") or "{}")
    except ValueError:
        return {}


def save_access(d: dict):
    put_raw("access", json.dumps(d))


async def _role(g, rid, name: str, **kw):
    r = g.get_role(int(rid)) if rid else None
    if r is None:
        r = discord.utils.get(g.roles, name=name)
    if r is None:
        r = await g.create_role(name=name, reason="Staff page: Access & languages", **kw)
    return r


def _need(g, *perms):
    me = g.me
    missing = [p.replace("_", " ").title() for p in perms if not getattr(me.guild_permissions, p)]
    if missing:
        raise ValueError("The bot needs these permissions first: " + ", ".join(missing) + ".")


async def setup_languages(rows: list, menu_channel_id: int, actor_id=None, actor="Dashboard") -> dict:
    """rows: [{"channel_id", "label", "role", "emoji"}]. One role per language chat + "All languages".
    The chats are hidden for everyone else; a button menu lets players pick (tap again to leave)."""
    g = bot.get_guild(GUILD_ID)
    if g is None:
        raise ValueError("The bot isn't connected to the server.")
    _need(g, "manage_roles", "manage_channels")
    if not rows:
        raise ValueError("Pick at least one language chat.")
    a = access_cfg()
    old = {int(x["channel_id"]): x for x in a.get("langs", [])}
    all_role = await _role(g, a.get("all_role"), "🌍 All languages")
    staff = [r for r in (g.get_role(MOD_ROLE_ID), g.get_role(ADMIN_ROLE_ID)) if r]
    langs, menu_roles = [], []
    for row in rows:
        ch = g.get_channel(int(row["channel_id"]))
        if not isinstance(ch, discord.TextChannel):
            continue
        name = f"{row.get('emoji') or ''} {row.get('role') or ch.name}".strip()[:100]
        prev = old.get(ch.id)
        role = await _role(g, prev and prev.get("role_id"), name)
        if role.name != name:
            await role.edit(name=name, reason="Language chat renamed")
        ow = dict(ch.overwrites)
        ow[g.default_role] = discord.PermissionOverwrite(**{**dict(ow.get(g.default_role, discord.PermissionOverwrite())), "view_channel": False})
        for r in (role, all_role, *staff):
            ow[r] = discord.PermissionOverwrite(**{**dict(ow.get(r, discord.PermissionOverwrite())), "view_channel": True})
        ow[g.me] = discord.PermissionOverwrite(**{**dict(ow.get(g.me, discord.PermissionOverwrite())), "view_channel": True,
                                                  "send_messages": True, "read_message_history": True})
        await ch.edit(overwrites=ow, reason=f"Language chat set up by {actor}")
        langs.append({"channel_id": str(ch.id), "role_id": str(role.id), "label": (row.get("label") or row.get("role") or ch.name)[:80],
                      "role": row.get("role") or "", "emoji": row.get("emoji") or ""})
        menu_roles.append({"role_id": str(role.id), "label": langs[-1]["label"], "emoji": row.get("emoji") or "", "desc": ""})
    menu_roles.append({"role_id": str(all_role.id), "label": "All languages", "emoji": "🌍", "desc": "See every language chat"})
    fields = dict(channel_id=int(menu_channel_id), title="🌍 Pick your language chats",
                  description="Tap the languages you want to chat in – their channels show up for you. Tap again to hide them.\n"
                              "🌍 **All languages** shows every language chat.", style="buttons", mode="multi", roles=json.dumps(menu_roles))
    menu = store.get_role_menu(a["lang_menu"]) if a.get("lang_menu") else None
    if menu and menu["channel_id"] != int(menu_channel_id):
        await remove_role_menu(menu["id"]); menu = None
    mid = store.save_role_menu(menu["id"] if menu else None, **fields)
    await publish_role_menu(mid)
    # chats that were set up before but are now unticked: show them to everyone again
    keep = {int(x["channel_id"]) for x in langs}
    for cid, x in old.items():
        ch = g.get_channel(cid)
        if cid not in keep and isinstance(ch, discord.TextChannel):
            ow = dict(ch.overwrites)
            if g.default_role in ow:
                o = ow[g.default_role]; o.view_channel = None
                ow[g.default_role] = o
            await ch.edit(overwrites=ow, reason=f"No longer a language chat ({actor})")
    a.update(langs=langs, all_role=str(all_role.id), lang_menu=mid, lang_menu_channel=str(menu_channel_id))
    save_access(a)
    log_event("settings", f"Language chats set up by {actor}: {len(langs)} chat(s) + All languages", actor_id, actor)
    return a


# ---- the device question: new members see nothing until they answer
GATE_TEXT = ("Welcome to **{server}**! 👋\n\nBefore you can see the channels, tell us which device you play on. "
             "Tap a button below – it takes one second.")


def gate_message(a: dict):
    e = discord.Embed(title="📱 Which device do you play on?", color=0x7DE3F4,
                      description=(a.get("gate_text") or GATE_TEXT).replace("{server}", bot.get_guild(GUILD_ID).name if bot.get_guild(GUILD_ID) else ""))
    e.set_footer(text=f"{brand()} · you can change it later by tapping the other button")
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(custom_id="gate:ios", label="iOS", emoji="🍎", style=discord.ButtonStyle.primary))
    v.add_item(discord.ui.Button(custom_id="gate:android", label="Android / Emulator", emoji="🤖", style=discord.ButtonStyle.success))
    return e, v


async def setup_access_roles(member_role_id=None, ios_role_id=None, android_role_id=None, actor_id=None, actor="Dashboard") -> dict:
    g = bot.get_guild(GUILD_ID)
    if g is None:
        raise ValueError("The bot isn't connected to the server.")
    _need(g, "manage_roles")
    a = access_cfg()
    member = await _role(g, member_role_id or a.get("member_role"), "Member")
    ios = await _role(g, ios_role_id or a.get("ios_role"), "🍎 iOS")
    android = await _role(g, android_role_id or a.get("android_role"), "🤖 Android / Emulator")
    a.update(member_role=str(member.id), ios_role=str(ios.id), android_role=str(android.id))
    save_access(a)
    log_event("settings", f"Access roles set by {actor}: {member.name}, {ios.name}, {android.name}", actor_id, actor)
    return a


async def post_gate(channel_id: int, text: Optional[str] = None, actor_id=None, actor="Dashboard") -> dict:
    g = bot.get_guild(GUILD_ID)
    a = access_cfg()
    if not a.get("member_role"):
        raise ValueError("Set the roles first (step 1).")
    ch = g.get_channel(int(channel_id)) if g else None
    if not isinstance(ch, discord.TextChannel):
        raise ValueError("Pick a normal text channel for the question.")
    if text is not None:
        a["gate_text"] = text.strip()[:1500] or None
    e, v = gate_message(a)
    msg = None
    if a.get("gate_msg") and str(a.get("gate_channel")) == str(ch.id):
        try:
            msg = await ch.get_partial_message(int(a["gate_msg"])).edit(embed=e, view=v)
        except discord.NotFound:
            msg = None
    if msg is None:
        msg = await ch.send(embed=e, view=v)
    a.update(gate_channel=str(ch.id), gate_msg=str(msg.id))
    save_access(a)
    log_event("settings", f"Device question posted in #{ch.name} by {actor}", actor_id, actor)
    return a


async def handle_gate(interaction: discord.Interaction):
    a = access_cfg()
    member, g = interaction.user, interaction.guild
    if g is None or not hasattr(member, "roles") or not a.get("member_role"):
        return await interaction.response.send_message("This isn't set up yet – please ask a moderator.", ephemeral=True)
    pick = interaction.data.get("custom_id", "").split(":")[1]
    want, other = (a.get("ios_role"), a.get("android_role")) if pick == "ios" else (a.get("android_role"), a.get("ios_role"))
    add = [r for r in (g.get_role(int(want)) if want else None, g.get_role(int(a["member_role"]))) if r and r not in member.roles]
    remove = [r for r in (g.get_role(int(other)) if other else None,) if r and r in member.roles]
    new = g.get_role(int(a["member_role"])) not in member.roles
    try:
        if add:
            await member.add_roles(*add, reason="Picked their device")
        if remove:
            await member.remove_roles(*remove, reason="Picked their device")
    except discord.Forbidden:
        return await interaction.response.send_message("I'm not allowed to give that role – an admin needs to move my role higher.", ephemeral=True)
    device = "🍎 iOS" if pick == "ios" else "🤖 Android / Emulator"
    menu = f" Now pick your language chats in <#{a['lang_menu_channel']}>." if a.get("lang_menu_channel") else ""
    await interaction.response.send_message((f"✅ Welcome in! You're set as **{device}** – the channels are open for you now.{menu}"
                                             if new else f"✅ Changed to **{device}**."), ephemeral=True)
    if new:
        log_event("welcome", f"{member.display_name} picked {device} and got access", member.id, member.display_name)


async def run_backfill():
    """Give the Member role to everyone who was already here (and to all bots), so nobody loses access."""
    a = access_cfg()
    g = bot.get_guild(GUILD_ID)
    b = a.get("backfill") or {}
    role = g.get_role(int(a["member_role"])) if g and a.get("member_role") else None
    if role is None:
        return
    if not g.chunked:
        await g.chunk()
    cutoff = b.get("cutoff") or time.time()
    todo = [m for m in g.members if role not in m.roles and (m.bot or (m.joined_at and m.joined_at.timestamp() <= cutoff))]
    start = b.get("done", 0)
    b.update(state="running", total=start + len(todo), cutoff=cutoff)
    a["backfill"] = b; save_access(a)
    failed = 0
    for i, m in enumerate(todo, 1):
        try:
            await m.add_roles(role, reason="Already a member before the device question")
        except discord.NotFound:
            pass
        except discord.HTTPException:
            failed += 1
        if i % 20 == 0 or i == len(todo):
            a = access_cfg(); a["backfill"].update(done=start + i, failed=failed); save_access(a)
        await asyncio.sleep(0.25)
    a = access_cfg(); a["backfill"].update(state="done", finished=int(time.time())); save_access(a)
    log_event("settings", f"Member role given to {len(todo) - failed} existing member(s)" + (f" ({failed} failed)" if failed else ""), None, brand())


_backfill_task = [None]


def start_backfill(actor_id=None, actor="Dashboard") -> dict:
    a = access_cfg()
    if not a.get("member_role"):
        raise ValueError("Set the roles first (step 1).")
    if _backfill_task[0] and not _backfill_task[0].done():
        return a
    b = a.get("backfill") or {}
    if b.get("state") != "running":
        a["backfill"] = {"state": "running", "done": 0, "total": 0, "cutoff": time.time()}
    a["gate_on"] = True                                  # from now on, new members answer the question first
    save_access(a)
    _backfill_task[0] = asyncio.create_task(safe_task(run_backfill(), "member backfill"))
    log_event("settings", f"{actor} started giving the Member role to everyone already here", actor_id, actor)
    return a


async def lock_server(actor_id=None, actor="Dashboard") -> dict:
    """New members only see the device question: View Channels moves from @everyone to the Member role.
    Everything changed is remembered, so 'Undo' puts it back exactly."""
    g = bot.get_guild(GUILD_ID)
    a = access_cfg()
    if not (a.get("member_role") and a.get("gate_msg")):
        raise ValueError("Do steps 1 and 2 first.")
    if (a.get("backfill") or {}).get("state") != "done":
        raise ValueError("Wait until everyone already here has the Member role (step 3) – otherwise they'd lose the channels.")
    if a.get("locked"):
        return a
    _need(g, "manage_roles", "manage_channels")
    member = g.get_role(int(a["member_role"]))
    if g.me not in member.members:
        await g.me.add_roles(member, reason="The bot must keep seeing the channels")
    gate_ch = g.get_channel(int(a["gate_channel"]))
    undo = {"everyone_perms": g.default_role.permissions.value, "member_perms": member.permissions.value, "channels": {}}
    p = g.default_role.permissions; p.update(view_channel=False)
    mp = member.permissions; mp.update(view_channel=True)
    await member.edit(permissions=mp, reason=f"Member role sees the channels ({actor})")
    await g.default_role.edit(permissions=p, reason=f"New members answer the device question first ({actor})")
    for ch in g.channels:
        if ch.id == gate_ch.id:
            continue
        o = ch.overwrites.get(g.default_role)
        if o is not None and o.view_channel is True:       # explicitly open to everyone → open to members instead
            undo["channels"][str(ch.id)] = True
            ow = dict(ch.overwrites)
            o.view_channel = None
            ow[g.default_role] = o
            mo = ow.get(member, discord.PermissionOverwrite()); mo.view_channel = True; ow[member] = mo
            await ch.edit(overwrites=ow, reason=f"Visible to members only ({actor})")
    ow = dict(gate_ch.overwrites)
    undo["gate"] = {str(k.id): [v.pair()[0].value, v.pair()[1].value] for k, v in ow.items() if isinstance(k, discord.Role)}
    eo = ow.get(g.default_role, discord.PermissionOverwrite())
    eo.update(view_channel=True, read_message_history=True, send_messages=False, add_reactions=False)
    ow[g.default_role] = eo
    await gate_ch.edit(overwrites=ow, reason=f"Device question visible to new members ({actor})")
    a.update(locked=True, lock_undo=undo, locked_at=int(time.time()))
    save_access(a)
    log_event("settings", f"{actor} switched on the device question: new members only see #{gate_ch.name} until they answer", actor_id, actor)
    return a


async def unlock_server(actor_id=None, actor="Dashboard") -> dict:
    g = bot.get_guild(GUILD_ID)
    a = access_cfg()
    u = a.get("lock_undo")
    if not a.get("locked") or not u:
        return a
    member = g.get_role(int(a["member_role"])) if a.get("member_role") else None
    await g.default_role.edit(permissions=discord.Permissions(u["everyone_perms"]), reason=f"Device question switched off ({actor})")
    if member is not None:
        await member.edit(permissions=discord.Permissions(u["member_perms"]), reason=f"Device question switched off ({actor})")
    for cid in u.get("channels", {}):
        ch = g.get_channel(int(cid))
        if ch is None:
            continue
        ow = dict(ch.overwrites)
        o = ow.get(g.default_role, discord.PermissionOverwrite()); o.view_channel = True; ow[g.default_role] = o
        if member is not None and member in ow:
            mo = ow[member]; mo.view_channel = None
            if mo.is_empty():
                ow.pop(member)
            else:
                ow[member] = mo
        await ch.edit(overwrites=ow, reason=f"Device question switched off ({actor})")
    gate_ch = g.get_channel(int(a["gate_channel"])) if a.get("gate_channel") else None
    if gate_ch is not None and "gate" in u:
        ow = {k: v for k, v in gate_ch.overwrites.items() if not isinstance(k, discord.Role)}
        for rid, (al, de) in u["gate"].items():
            r = g.get_role(int(rid))
            if r is not None:
                ow[r] = discord.PermissionOverwrite.from_pair(discord.Permissions(al), discord.Permissions(de))
        await gate_ch.edit(overwrites=ow, reason=f"Device question switched off ({actor})")
    a.update(locked=False, gate_on=False, lock_undo=None)
    save_access(a)
    log_event("settings", f"{actor} switched off the device question – everyone sees the channels again", actor_id, actor)
    return a


def access_status() -> dict:
    g = bot.get_guild(GUILD_ID)
    a = access_cfg()
    langs = {int(x["channel_id"]): x for x in a.get("langs", [])}
    detected = []
    if g is not None:
        for ch in sorted(g.text_channels, key=lambda c: (c.category.position if c.category else -1, c.position)):
            gl = guess_language(ch.name)
            cur = langs.get(ch.id)
            detected.append({"channel_id": str(ch.id), "name": ch.name, "category": ch.category.name if ch.category else "",
                             "emoji": (cur or {}).get("emoji") or gl["emoji"], "role": (cur or {}).get("role") or gl["role"],
                             "label": (cur or {}).get("label") or gl["label"], "checked": bool(cur) if langs else gl["chat"],
                             "role_id": (cur or {}).get("role_id")})
    me = g.me.guild_permissions if g else None
    return {"config": {k: v for k, v in a.items() if k != "lock_undo"}, "detected": detected,
            "perms": {"manage_roles": bool(me and me.manage_roles), "manage_channels": bool(me and me.manage_channels)},
            "members": g.member_count if g else 0}


# ------------------------------------------------------------ warnings with escalation + cases
async def warn_member(member, reason: str, mod_id, mod_name: str, auto: bool = False) -> dict:
    """Give a warning; at the thresholds the member is timed out / banned automatically. Returns what happened."""
    cid = store.add_case(member.id, member.display_name, "warn", reason, mod_id, mod_name, auto=auto)
    n = store.active_warnings(member.id)
    out = {"case": cid, "warnings": n, "action": None}
    t_at, b_at = cfg("warn_timeout_at"), cfg("warn_ban_at")
    if b_at and n >= b_at:
        try:
            await dm_t(member.id, "dm.banned", reason=f"{n} warnings")
            await member.ban(reason=f"{n} warnings – last: {reason}"[:500], delete_message_seconds=0)
            store.add_case(member.id, member.display_name, "ban", f"Automatic: {n} warnings", None, brand(), auto=True)
            out["action"] = "ban"
        except discord.HTTPException:
            out["action"] = "ban_failed"
    elif t_at and n >= t_at:
        minutes = cfg("warn_timeout_minutes")
        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"{n} warnings – last: {reason}"[:500])
            store.add_case(member.id, member.display_name, "timeout", f"Automatic: {n} warnings", None, brand(), minutes=minutes, auto=True)
            out["action"] = "timeout"
        except discord.HTTPException:
            out["action"] = "timeout_failed"
    if out["action"] != "ban":
        await dm_t(member.id, "dm.warned", reason=None, n=n, why=reason or "–",
                   next=T("warn.next_timeout", lang_for(member.id), n=t_at) if t_at and n < t_at else "")
    extra = {"timeout": f" → timed out for {cfg('warn_timeout_minutes')} min", "ban": " → banned",
             "timeout_failed": " (couldn't time out – check the bot's permissions)", "ban_failed": " (couldn't ban – check the bot's permissions)"}.get(out["action"], "")
    log_event("mod", f"{member.display_name} warned by {mod_name} ({n} active): {reason or '–'}{extra}", mod_id, mod_name)
    return out


def cases_embed(user, rows) -> discord.Embed:
    icon = {"warn": "⚠️", "timeout": "🔇", "kick": "👢", "ban": "⛔", "unban": "♻️", "pardon": "🕊️"}
    e = discord.Embed(title=f"Cases – {getattr(user, 'display_name', user)}", color=0xFFC15E)
    e.description = "\n".join(f"`#{r['id']}` {icon.get(r['kind'], '•')} **{r['kind']}**" + (f" {r['minutes']} min" if r["minutes"] else "")
                              + (" ~~(pardoned)~~" if r["kind"] == "warn" and not r["active"] else "")
                              + f" – {r['reason'] or 'no reason'} · {r['moderator_name'] or '?'} · <t:{r['ts']}:R>" for r in rows)[:4000] or "No cases."
    e.set_footer(text=f"Active warnings: {store.active_warnings(user.id)}")
    return e


@bot.tree.command(name="warn", description="Warn a member (moderators) – they get a DM, repeat offenders are timed out or banned", guild=_GUILD)
@app_commands.describe(member="Who", reason="Why – the member sees this")
@app_commands.guild_only()
async def warn_cmd(interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 3, 400]):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    if is_mod(member) or member.bot:
        return await interaction.response.send_message("You can't warn staff or bots.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    r = await warn_member(member, reason, interaction.user.id, interaction.user.display_name)
    extra = {"timeout": f" They were timed out for {cfg('warn_timeout_minutes')} minutes.", "ban": " They were banned.",
             "timeout_failed": " ⚠️ I couldn't time them out (permissions).", "ban_failed": " ⚠️ I couldn't ban them (permissions)."}.get(r["action"], "")
    await interaction.followup.send(f"⚠️ {member.mention} warned – case #{r['case']}, {r['warnings']} active warning(s).{extra}", ephemeral=True)


@bot.tree.command(name="warnings", description="See a member's warnings and other cases (moderators)", guild=_GUILD)
@app_commands.guild_only()
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    await interaction.response.send_message(embed=cases_embed(member, store.cases_for(member.id)), ephemeral=True)


@bot.tree.command(name="pardon", description="Remove a warning (moderators)", guild=_GUILD)
@app_commands.describe(case="The case number, e.g. 12")
@app_commands.guild_only()
async def pardon_cmd(interaction: discord.Interaction, case: int):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    r = store.pardon_case(case)
    if r is None:
        return await interaction.response.send_message("That isn't an active warning.", ephemeral=True)
    store.add_case(r["user_id"], r["user_name"], "pardon", f"Warning #{case} removed", interaction.user.id, interaction.user.display_name)
    log_event("mod", f"Warning #{case} for {r['user_name']} removed by {interaction.user.display_name}", interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(f"🕊️ Warning #{case} removed – {r['user_name']} now has {store.active_warnings(r['user_id'])}.", ephemeral=True)


@bot.tree.command(name="timeout", description="Time a member out (moderators)", guild=_GUILD)
@app_commands.describe(member="Who", minutes="How long", reason="Why")
@app_commands.guild_only()
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: Optional[str] = None):
    if not is_mod(interaction.user) or is_mod(member):
        return await interaction.response.send_message("Moderators only – and not on staff.", ephemeral=True)
    try:
        await member.timeout(timedelta(minutes=minutes), reason=(reason or "")[:500] or None)
    except discord.HTTPException:
        return await interaction.response.send_message("I couldn't – give me the “Moderate Members” permission and put my role above theirs.", ephemeral=True)
    c = store.add_case(member.id, member.display_name, "timeout", reason, interaction.user.id, interaction.user.display_name, minutes=minutes)
    log_event("mod", f"{member.display_name} timed out for {minutes} min by {interaction.user.display_name}: {reason or '–'}", interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(f"🔇 {member.mention} timed out for {minutes} min – case #{c}.", ephemeral=True)


@bot.tree.command(name="ban", description="Ban a member (admins)", guild=_GUILD)
@app_commands.guild_only()
async def ban_cmd(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not is_admin(interaction.user) or is_mod(member):
        return await interaction.response.send_message("Admins only – and not on staff.", ephemeral=True)
    await dm_t(member.id, "dm.banned", reason=reason)
    try:
        await member.ban(reason=reason[:500], delete_message_seconds=0)
    except discord.HTTPException:
        return await interaction.response.send_message("I couldn't – I need the “Ban Members” permission and a role above theirs.", ephemeral=True)
    c = store.add_case(member.id, member.display_name, "ban", reason, interaction.user.id, interaction.user.display_name)
    log_event("mod", f"{member.display_name} banned by {interaction.user.display_name}: {reason}", interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(f"⛔ {member.display_name} banned – case #{c}.", ephemeral=True)


# ------------------------------------------------------------ more auto-mod rules
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_recent_text: dict = {}           # user_id -> [(ts, normalized text)]


def _badword_re():
    words = [w.strip().lower() for w in (cfg("automod_badwords") or "").splitlines() if w.strip()]
    if not words:
        return None
    parts = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) for w in words]
    return re.compile(r"(?<![a-z0-9])(" + "|".join(parts) + r")(?![a-z0-9])")


def automod_reason(message) -> Optional[tuple]:
    """(rule, what the member is told) or None."""
    text = message.content or ""
    if cfg("automod_badwords_on"):
        rx = _badword_re()
        norm = re.sub(r"(.)\1{2,}", r"\1\1", text.lower().translate(_LEET))
        if rx and rx.search(norm):
            return "bad words", "please keep the language friendly"
    if cfg("automod_mentions_on"):
        n = len({u.id for u in message.mentions if not u.bot}) + len(message.role_mentions)
        if n >= cfg("automod_mentions_max"):
            return f"mass mention ({n})", "please don't mention that many people at once"
    if cfg("automod_caps_on"):
        clean = re.sub(r"<[^>]+>|https?://\S+|:\w+:", "", text)
        letters = [c for c in clean if c.isalpha()]
        if len(letters) >= 15 and sum(c.isupper() for c in letters) / len(letters) >= 0.75:
            return "ALL CAPS", "please don't write in ALL CAPS"
    if cfg("automod_repeat_on"):
        norm = re.sub(r"\s+", " ", text.lower()).strip()
        if len(norm) >= 3:
            now = time.time()
            items = [x for x in _recent_text.get(message.author.id, []) if now - x[0] < 60] + [(now, norm)]
            _recent_text[message.author.id] = items[-8:]
            if sum(1 for x in items if x[1] == norm) >= 3:
                return "repeated message", "please don't send the same message again and again"
    return None


async def automod_message(message) -> bool:
    if message.guild is None or message.guild.id != GUILD_ID or is_mod(message.author) or getattr(message.author, "bot", False):
        return False
    hit = automod_reason(message)
    if not hit:
        return False
    rule, tell = hit
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    try:
        await message.channel.send(f"{message.author.mention}, {tell}.", delete_after=8,
                                   allowed_mentions=discord.AllowedMentions(users=[message.author]))
    except discord.HTTPException:
        pass
    log_event("guard", f"Auto-mod removed a message from {message.author.display_name} in #{getattr(message.channel, 'name', '?')} ({rule})",
              message.author.id, message.author.display_name)
    if cfg("automod_warn") and isinstance(message.author, discord.Member):
        await warn_member(message.author, f"Auto-mod: {rule}", None, brand(), auto=True)
    return True


# ------------------------------------------------------------ giveaways
GIVEAWAY_EMOJI = "🎉"


def parse_duration(text: str) -> int:
    """'30m', '2h', '1d 12h', '90' (minutes) -> minutes."""
    text = (text or "").strip().lower()
    if text.isdigit():
        return int(text)
    total = 0
    for n, u in re.findall(r"(\d+)\s*([dhm])", text):
        total += int(n) * {"d": 1440, "h": 60, "m": 1}[u]
    if not total:
        raise ValueError("Write the time like 30m, 2h or 1d.")
    return total


def giveaway_embed(g, winners=None) -> discord.Embed:
    ended = bool(g["ended"])
    e = discord.Embed(title=f"🎉 {g['prize'][:200]}", color=0x5C6370 if ended else 0xFF8A3D)
    if ended:
        w = winners if winners is not None else json.loads(g["winner_ids"] or "[]")
        e.description = (f"**Winner{'s' if len(w) != 1 else ''}:** " + " ".join(f"<@{i}>" for i in w)) if w else "No valid entries – nobody won."
        e.description += f"\n-# Ended <t:{g['ends_at']}:R> · {g['entries'] or 0} entries"
    else:
        e.description = (f"React with {GIVEAWAY_EMOJI} to enter!\nEnds <t:{g['ends_at']}:R> (<t:{g['ends_at']}:f>)\n"
                         f"**{g['winners']}** winner{'s' if g['winners'] != 1 else ''}"
                         + (f"\nOnly for <@&{g['role_id']}>" if g["role_id"] else ""))
    e.set_footer(text=f"{brand()} · hosted by {g['host_name'] or 'the team'}")
    return e


async def start_giveaway(channel_id: int, prize: str, minutes: int, winners: int, role_id, host_id, host_name: str):
    ch = bot.get_channel(int(channel_id))
    if ch is None:
        raise ValueError("Choose a channel the bot can see.")
    if not (1 <= minutes <= 60 * 24 * 60) or not (1 <= winners <= 20) or not (2 <= len(prize.strip()) <= 200):
        raise ValueError("Use 1 minute to 60 days, 1–20 winners and a prize of 2–200 characters.")
    gid = store.db.execute("INSERT INTO giveaways (channel_id, prize, winners, ends_at, role_id, host_id, host_name, created) VALUES (?,?,?,?,?,?,?,?)",
                           (ch.id, prize.strip(), winners, int(time.time()) + minutes * 60, role_id, host_id, host_name, int(time.time()))).lastrowid
    store.db.commit()
    msg = await ch.send(embed=giveaway_embed(store.get_giveaway(gid)))
    await msg.add_reaction(GIVEAWAY_EMOJI)
    store.db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid)); store.db.commit()
    log_event("admin", f"Giveaway #{gid} started by {host_name}: {prize} ({winners} winner(s), {minutes} min)", host_id, host_name)
    return gid


async def end_giveaway(gid: int, reroll: bool = False) -> list:
    """Draw the winners (or new ones). Returns the winner IDs."""
    import random
    g = store.get_giveaway(gid)
    if g is None:
        raise ValueError("Giveaway not found.")
    ch = bot.get_channel(g["channel_id"])
    if ch is None:
        raise ValueError("The giveaway's channel is gone.")
    msg = await ch.fetch_message(g["message_id"])
    users = []
    for r in msg.reactions:
        if str(r.emoji) == GIVEAWAY_EMOJI:
            users = [u async for u in r.users(limit=None)]
    guild = ch.guild
    old = set(json.loads(g["winner_ids"] or "[]")) if reroll else set()
    pool = []
    for u in users:
        if u.bot or u.id in old:
            continue
        m = guild.get_member(u.id)
        if m is None or (g["role_id"] and not any(r.id == g["role_id"] for r in m.roles)):
            continue
        pool.append(u.id)
    winners = random.sample(pool, min(g["winners"], len(pool))) if pool else []
    store.db.execute("UPDATE giveaways SET ended=1, winner_ids=?, entries=? WHERE id=?", (json.dumps(winners), len(pool) + len(old), gid))
    store.db.commit()
    g = store.get_giveaway(gid)
    await msg.edit(embed=giveaway_embed(g, winners))
    if winners:
        await ch.send(f"🎉 Congratulations {' '.join(f'<@{i}>' for i in winners)}! You won **{g['prize']}**" + (" (reroll)" if reroll else "") + ".",
                      reference=msg, allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=i) for i in winners]))
    else:
        await ch.send("Nobody could win this giveaway – there were no valid entries.", reference=msg)
    log_event("admin", f"Giveaway #{gid} {'rerolled' if reroll else 'ended'}: {len(winners)} winner(s) from {len(pool)} entries", None, brand())
    return winners


giveaway_group = app_commands.Group(name="giveaway", description="Giveaways (moderators)", guild_ids=[GUILD_ID], guild_only=True)


@giveaway_group.command(name="start", description="Start a giveaway in this channel")
@app_commands.describe(prize="What people can win", duration="How long, e.g. 30m, 2h, 1d", winners="How many winners", role="Only members with this role can win")
async def giveaway_start(interaction: discord.Interaction, prize: str, duration: str, winners: app_commands.Range[int, 1, 20] = 1,
                         role: Optional[discord.Role] = None):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    try:
        gid = await start_giveaway(interaction.channel_id, prize, parse_duration(duration), winners, role.id if role else None,
                                   interaction.user.id, interaction.user.display_name)
    except (ValueError, discord.HTTPException) as exc:
        return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
    await interaction.response.send_message(f"🎉 Giveaway #{gid} started.", ephemeral=True)


@giveaway_group.command(name="end", description="End a giveaway now and draw the winners")
async def giveaway_end_cmd(interaction: discord.Interaction, giveaway: int):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        w = await end_giveaway(giveaway)
    except (ValueError, discord.HTTPException) as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"Done – {len(w)} winner(s).", ephemeral=True)


@giveaway_group.command(name="reroll", description="Draw new winners for an ended giveaway")
async def giveaway_reroll_cmd(interaction: discord.Interaction, giveaway: int):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        w = await end_giveaway(giveaway, reroll=True)
    except (ValueError, discord.HTTPException) as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"Rerolled – {len(w)} new winner(s).", ephemeral=True)


bot.tree.add_command(giveaway_group)


# ------------------------------------------------------------ timed messages
async def send_timer(t, now: Optional[int] = None) -> bool:
    ch = bot.get_channel(t["channel_id"])
    now = int(now or time.time())
    ok = False
    if ch is not None:
        if t["delete_previous"] and t["last_msg_id"]:
            try:
                await ch.get_partial_message(t["last_msg_id"]).delete()
            except discord.HTTPException:
                pass
        content = (f"<@&{t['role_id']}> " if t["role_id"] else "") + t["content"]
        try:
            msg = await ch.send(content[:2000], allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=t["role_id"])] if t["role_id"] else []))
            store.db.execute("UPDATE timers SET last_run=?, last_msg_id=? WHERE id=?", (now, msg.id, t["id"]))
            ok = True
        except discord.HTTPException as exc:
            log_event("system", f"Timed message “{t['name']}” could not be posted: {exc}", None, brand())
    else:
        log_event("system", f"Timed message “{t['name']}”: its channel is gone", None, brand())
    store.db.execute("UPDATE timers SET next_run=? WHERE id=?", (store.timer_next_run(t, now), t["id"]))
    store.db.commit()
    return ok


async def run_timers(now: Optional[int] = None) -> int:
    n = 0
    for t in store.due_timers(now):
        if await send_timer(t, now):
            n += 1
    return n


async def community_tick():
    """Every minute: finish giveaways and post timed messages."""
    for g in store.due_giveaways():
        try:
            await end_giveaway(g["id"])
        except Exception as exc:  # noqa: BLE001
            store.db.execute("UPDATE giveaways SET ended=1 WHERE id=?", (g["id"],)); store.db.commit()
            log_event("system", f"Giveaway #{g['id']} could not be drawn: {exc}", None, brand())
    await run_timers()


async def run_schedules(now=None):
    """Called every few minutes: posts the weekly digest when it is due.
    Returns the names of what was posted."""
    from datetime import datetime, timedelta
    now = now or datetime.now(store.tz())
    done = []
    try:
        if await scan_duplicates():
            done.append("duplicates")
    except Exception as exc:  # noqa: BLE001
        print(f"[duplicate scan] {exc!r}")
    if cfg("digest_enabled"):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        due = monday + timedelta(days=days.index(cfg("digest_weekday")), hours=cfg("digest_hour"))
        week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        if due <= now < due + timedelta(hours=36) and get_raw("digest_last") != week:
            try:
                await post_digest()
                put_raw("digest_last", week)
                done.append("digest")
            except (ValueError, discord.HTTPException) as exc:
                put_raw("digest_last", week)               # don't retry every 10 minutes
                log_event("system", f"Could not post the weekly digest: {exc}", None, brand())
    if cfg("weekly_enabled") and cfg("weekly_channel_id"):
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        due = monday + timedelta(days=days.index(cfg("weekly_weekday")), hours=cfg("weekly_hour"))
        week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        if due <= now < due + timedelta(hours=36) and get_raw("weekly_last") != week:
            put_raw("weekly_last", week)
            try:
                await post_weekly()
                done.append("weekly")
            except (ValueError, discord.HTTPException) as exc:
                log_event("system", f"Could not publish the weekly post: {exc}", None, brand())
    return done


def snapshot_members():
    """Keeps today's member count current; on the very first run rebuilds the last 90 days from join dates."""
    g = bot.get_guild(GUILD_ID)
    if g is None or not g.member_count:
        return
    joined = [m.joined_at.timestamp() for m in g.members if m.joined_at and not m.bot]
    if g.chunked and len(joined) >= g.member_count * 0.9:
        store.backfill_members(joined, g.member_count)
    store.record_members(g.member_count)


@tasks.loop(minutes=1)
async def gift_loop():
    """Keeps the gift-code announcements and list exact to the minute; giveaways and timed messages too."""
    try:
        await sweep_prompts()
    except Exception as exc:  # noqa: BLE001
        print(f"[reason prompts] {exc!r}")
    try:
        if cfg("giftcodes_enabled"):
            await sync_gift_messages()
    except Exception as exc:  # noqa: BLE001
        print(f"[gift codes] {exc!r}")
    try:
        await community_tick()
    except Exception as exc:  # noqa: BLE001
        print(f"[community] {exc!r}")


@gift_loop.before_loop
async def _before_gift():
    await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def schedule_loop():
    try:
        snapshot_members()
        await safe_refresh_board()
        start_redeem()
        await ping_waiting_bugs()
        await check_info_tickets()
        await run_schedules()
    except Exception as exc:  # noqa: BLE001
        print(f"[schedule] {exc!r}")


@schedule_loop.before_loop
async def _before_schedule():
    await bot.wait_until_ready()


@bot.tree.command(name="digest", description="Post the weekly bug digest now (admins)", guild=_GUILD)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def digest_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        ch = await post_digest(force=True)
    except ValueError as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"✅ Posted in {ch.mention}.", ephemeral=True)


@bot.tree.command(name="duplicate", description="Close a bug as a duplicate of another bug (moderators)",
                  guild=_GUILD)
@app_commands.describe(bug="The bug number to close", of="The number of the original bug")
@app_commands.guild_only()
async def duplicate_cmd(interaction: discord.Interaction, bug: int, of: int):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    r = get_bug(bug)
    if r is None:
        return await interaction.response.send_message(f"Bug #{bug} doesn't exist.", ephemeral=True)
    if r["status"] not in MOD_OPEN and not is_admin(interaction.user):
        return await interaction.response.send_message(
            f"Bug #{bug} has already been handled – only admins can change it now.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        await apply_bug_status(r, "rejected", interaction.user.display_name, interaction.user.id,
                               duplicate_of=of, log_kind="mod")
    except ValueError as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"Bug #{bug} closed as a duplicate of #{of}. The reporter was told.",
                                    ephemeral=True)


@bot.tree.command(name="reply", description="Send the reporter of a bug a message by DM (moderators)",
                  guild=_GUILD)
@app_commands.describe(bug="The bug number", canned="A ready-made reply (edit them on the dashboard)",
                       custom="Your own text (added after the ready-made reply)")
@app_commands.guild_only()
async def reply_cmd(interaction: discord.Interaction, bug: int, canned: Optional[str] = None,
                    custom: Optional[str] = None):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    parts = []
    if canned:
        row = next((x for x in store.list_replies() if str(x["id"]) == canned), None)
        if row is None:
            return await interaction.response.send_message("I don't know that ready-made reply.", ephemeral=True)
        parts.append(row["text"])
    if custom:
        parts.append(custom)
    if not parts:
        return await interaction.response.send_message("Pick a ready-made reply or write your own text.",
                                                       ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        ok = await send_bug_reply(bug, "\n\n".join(parts), interaction.user.display_name, interaction.user.id)
    except ValueError as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send("✅ Sent." if ok else "⚠️ I couldn't DM them (their DMs are closed).",
                                    ephemeral=True)


@reply_cmd.autocomplete("canned")
async def reply_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=x["title"][:100], value=str(x["id"]))
            for x in store.list_replies() if current.lower() in x["title"].lower()][:25]


@bot.tree.command(name="mybugs", description="See the status of your bug reports and suggestions", guild=_GUILD)
@app_commands.guild_only()
async def mybugs_cmd(interaction: discord.Interaction):
    uid = interaction.user.id
    lg = lang_for(uid)
    bugs = db.execute("SELECT * FROM bug_reports WHERE reporter_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    sugs = db.execute("SELECT * FROM suggestions WHERE author_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
    if not bugs and not sugs:
        return await interaction.response.send_message(T("my.none", lg), ephemeral=True)
    e = discord.Embed(title=T("my.title", lg), color=0x5865F2)
    if bugs:
        lines = []
        for x in bugs:
            extra = f" (duplicate of #{x['duplicate_of']})" if x["duplicate_of"] else ""
            lines.append(f"**#{x['id']}** {store.bug_summary(x)[:70]}\n└ {T('st.' + x['status'], lg)}{extra}")
        e.add_field(name="🐛 " + T("my.bugs", lg), value="\n".join(lines)[:1024], inline=False)
    if sugs:
        lines = [f"**#{x['id']}** {x['text'].splitlines()[0][:70]}\n└ {T('st.sug_' + x['status'], lg)}" for x in sugs]
        e.add_field(name="💡 " + T("my.sugs", lg), value="\n".join(lines)[:1024], inline=False)
    e.set_footer(text="Only the latest ones are shown · " + brand())
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="block", description="Stop a player from sending reports/suggestions (moderators)",
                  guild=_GUILD)
@app_commands.describe(user="The player", reason="Why (only staff see this)",
                       days="For how many days (leave empty = until you unblock them)")
@app_commands.guild_only()
async def block_cmd(interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = None,
                    days: Optional[app_commands.Range[int, 1, 365]] = None):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    if user.bot or is_mod(user) or user.id == interaction.user.id:
        return await interaction.response.send_message("You can't block that person.", ephemeral=True)
    store.block_user(user.id, reason or "", days, interaction.user.display_name)
    log_event("block", f"{user.display_name} blocked by {interaction.user.display_name}"
              + (f" for {days} day(s)" if days else "") + (f": {reason}" if reason else ""),
              interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(
        f"🚫 {user.mention} can no longer open tickets" + (f" for {days} day(s)." if days else " until you unblock them."),
        ephemeral=True)


@bot.tree.command(name="unblock", description="Let a blocked player send reports again (moderators)", guild=_GUILD)
@app_commands.describe(user="The player")
@app_commands.guild_only()
async def unblock_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    if store.unblock_user(user.id):
        log_event("block", f"{user.display_name} unblocked by {interaction.user.display_name}",
                  interaction.user.id, interaction.user.display_name)
        return await interaction.response.send_message(f"✅ {user.mention} can send reports again.", ephemeral=True)
    await interaction.response.send_message("That person isn't blocked.", ephemeral=True)


@bot.tree.command(name="blocklist", description="Show who is blocked (moderators)", guild=_GUILD)
@app_commands.guild_only()
async def blocklist_cmd(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        return await interaction.response.send_message("Moderators only.", ephemeral=True)
    rows = store.list_blocks()
    if not rows:
        return await interaction.response.send_message("Nobody is blocked.", ephemeral=True)
    lines = [f"<@{x['user_id']}>" + (f" · until <t:{x['until']}:d>" if x["until"] else " · until removed")
             + (f" · {x['reason']}" if x["reason"] else "") for x in rows[:25]]
    await interaction.response.send_message("**Blocked players**\n" + "\n".join(lines)[:1900], ephemeral=True)


# ------------------------------------------------------------- patch notes
def patchnotes_embeds(bugs, title: str) -> list:
    lines = [f"• {store.bug_summary(x)} *(#{x['id']})*" for x in bugs]
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    embeds = []
    for i, c in enumerate(chunks[:10]):
        e = discord.Embed(title=f"🛠️ {title}" if i == 0 else None, description=c.strip(), color=0x2ECC71)
        if i == 0:
            e.add_field(name="Fixed bugs", value=str(len(bugs)), inline=True)
        embeds.append(e)
    embeds[-1].set_footer(text="Thanks to everyone who reported these! · " + brand())
    return embeds


def default_patch_title() -> str:
    return "Patch notes – " + (cfg("game_version") or time.strftime("%d %b %Y"))


async def publish_patchnotes(title: Optional[str], actor_name: str, actor_id: Optional[int] = None):
    """Post all fixed-but-unannounced bugs as patch notes. Returns (bugs, message)."""
    bugs = store.unreleased_fixed()
    if not bugs:
        raise ValueError("There are no fixed bugs waiting to be announced.")
    ch = chan("patchnotes_channel_id")
    if ch is None:
        raise ValueError("Choose a patch notes channel first (Channels tab).")
    title = (title or "").strip()[:100] or default_patch_title()
    msg = await ch.send(embeds=patchnotes_embeds(bugs, title))
    store.mark_patched([x["id"] for x in bugs], title, actor_name)
    log_event("patch", f"Patch notes “{title}” published with {len(bugs)} fixed bug(s) by {actor_name}",
              actor_id, actor_name)
    push_soon("news", f"🩹 {title}"[:80], f"{len(bugs)} bug{'s' if len(bugs) != 1 else ''} fixed – see what changed.", "changelog.html")
    return bugs, msg


patchnotes = app_commands.Group(name="patchnotes", description="Announce fixed bugs (admins only)",
                                guild_ids=[GUILD_ID], guild_only=True,
                                default_permissions=discord.Permissions(administrator=True))


@patchnotes.command(name="preview", description="Show the patch notes that would be posted (only you see it)")
async def patchnotes_preview(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    bugs = store.unreleased_fixed()
    if not bugs:
        return await interaction.response.send_message("No fixed bugs are waiting to be announced.", ephemeral=True)
    ch = chan("patchnotes_channel_id")
    await interaction.response.send_message(
        f"Would be posted in {ch.mention if ch else '**(no patch notes channel set yet)**'}:",
        embeds=patchnotes_embeds(bugs, default_patch_title()), ephemeral=True)


@patchnotes.command(name="publish", description="Post the patch notes for all bugs fixed since the last post")
@app_commands.describe(title="Title, e.g. “Update 1.4” (default: today's date)")
async def patchnotes_publish(interaction: discord.Interaction, title: Optional[str] = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        bugs, msg = await publish_patchnotes(title, interaction.user.display_name, interaction.user.id)
    except (ValueError, discord.HTTPException) as exc:
        return await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
    await interaction.followup.send(f"✅ Posted {len(bugs)} fixed bug(s): {msg.jump_url}", ephemeral=True)


bot.tree.add_command(patchnotes)


@bot.tree.command(name="codes", description="Show the active gift codes", guild=_GUILD)
@app_commands.guild_only()
async def codes_cmd(interaction: discord.Interaction):
    lg = ilang(interaction)
    if not cfg("giftcodes_enabled"):
        return await interaction.response.send_message(T("gift.off", lg), ephemeral=True)
    lk = store.get_link(interaction.user.id) if redeem_ready() else None
    await interaction.response.send_message(embed=codes_embed(lg, lk["player_uid"] if lk else None), ephemeral=True)


giftcode = app_commands.Group(name="giftcode", description="Gift codes (admins only)", guild_ids=[GUILD_ID], guild_only=True,
                              default_permissions=discord.Permissions(administrator=True))
_DUR_CHOICES = [app_commands.Choice(name=store.gift_duration_label(h), value=h or 0) for h in store.GIFT_DURATIONS]


@giftcode.command(name="add", description="Add and announce a gift code")
@app_commands.describe(code="The code players type in the game", duration="How long it works",
                       reward="What players get (optional)")
@app_commands.choices(duration=_DUR_CHOICES)
async def giftcode_add(interaction: discord.Interaction, code: str, duration: app_commands.Choice[int],
                       reward: Optional[str] = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    try:
        r, warn = await add_gift_code(code, reward or "", duration.value or None, interaction.user.display_name, interaction.user.id)
    except ValueError as exc:
        return await interaction.response.send_message(str(exc), ephemeral=True)
    await interaction.response.send_message(f"✅ Gift code **{r['code']}** added ({duration.name})." + (f"\n⚠️ {warn}" if warn else ""),
                                            ephemeral=True)


@giftcode.command(name="remove", description="Remove a gift code (deletes its announcement)")
@app_commands.describe(code="The code to remove")
async def giftcode_remove(interaction: discord.Interaction, code: str):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    r = next((x for x in store.gift_codes() if x["code"].lower() == code.strip().lower()), None)
    if r is None:
        return await interaction.response.send_message("I don't know that code.", ephemeral=True)
    await remove_gift_code(r["id"], True, interaction.user.display_name, interaction.user.id)
    await interaction.response.send_message(f"🗑️ Gift code **{r['code']}** removed.", ephemeral=True)


bot.tree.add_command(giftcode)


@bot.tree.command(name="language", description="Choose the language the bot uses with you", guild=_GUILD)
@app_commands.describe(language="Your language – or “Automatic” to follow your Discord language")
async def language_cmd(interaction: discord.Interaction, language: str):
    if language == "auto":
        store.set_user_language(interaction.user.id, None)
        lg = ilang(interaction)
        return await interaction.response.send_message(T("lang.auto", lg, name=texts.lang_name(lg)), ephemeral=True)
    if language not in store.languages():
        language = store.default_language()
    store.set_user_language(interaction.user.id, language)
    await interaction.response.send_message(T("lang.set", language, name=texts.lang_name(language)), ephemeral=True)


@language_cmd.autocomplete("language")
async def _language_ac(interaction: discord.Interaction, current: str):
    opts = [("🌍 Automatic (Discord language)", "auto")] + [(texts.lang_name(x), x) for x in store.languages()]
    return [app_commands.Choice(name=n, value=v) for n, v in opts if current.lower() in n.lower() or current.lower() == v][:25]


# --------------------------------------------------------------- reminders
def _age(seconds: float) -> str:
    h = int(seconds // 3600)
    return f"{h} h" if h < 48 else f"{h // 24} d"


async def run_reminders(now: Optional[float] = None) -> list:
    """Post one digest per channel about bugs/suggestions that have waited too long (critical bugs sooner).
    Returns the digest texts that were sent."""
    if not cfg("reminders_enabled"):
        return []
    now = now or time.time()
    crit_h = cfg("reminder_critical_hours")
    sent = []
    jobs = (("mod", "mod_channel_id", cfg("reminder_mod_hours"), MOD_ROLE_ID, MOD_OPEN, "msg_id"),
            ("admin", "admin_channel_id", cfg("reminder_admin_hours"), ADMIN_ROLE_ID, ADMIN_OPEN, "admin_msg_id"))
    for name, key, hours, role_id, statuses, col in jobs:
        ch = chan(key)
        if ch is None:
            continue
        marks = ",".join("?" * len(statuses))
        items = []                                   # (critical?, since, text)
        for r in db.execute(f"SELECT * FROM bug_reports WHERE status IN ({marks}) AND {col} IS NOT NULL", statuses):
            crit = r["severity"] == "critical"
            ts = store.last_change_ts(r)
            if ts and ts <= now - (crit_h if crit else hours) * 3600:
                items.append((crit, ts, f"• {'🚨 ' if crit else ''}[#{r['id']}](https://discord.com/channels/"
                                        f"{GUILD_ID}/{ch.id}/{r[col]}) {store.bug_summary(r)[:60]} – {_age(now - ts)}"))
        if name == "mod":
            for s in db.execute("SELECT * FROM suggestions WHERE status='new' AND msg_id IS NOT NULL "
                                "AND created_at IS NOT NULL AND created_at<=?", (now - hours * 3600,)):
                items.append((False, s["created_at"], f"• 💡 [Suggestion #{s['id']}](https://discord.com/channels/"
                              f"{GUILD_ID}/{ch.id}/{s['msg_id']}) – {_age(now - s['created_at'])}"))
        if not items:
            continue
        gap = (crit_h if any(i[0] for i in items) else hours) * 3600     # critical bugs are reminded sooner
        if now - float(get_raw(f"reminder_{name}_ts") or 0) < gap:
            continue
        items.sort(key=lambda i: (not i[0], i[1]))
        lines = [t for _, _, t in items[:10]] + ([f"…and {len(items) - 10} more"] if len(items) > 10 else [])
        text = (f"<@&{role_id}> ⏰ **{len(items)} item(s) are waiting for you**\n" + "\n".join(lines))[:1990]
        try:
            await ch.send(text, allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=role_id)]))
        except discord.HTTPException as exc:
            log_event("system", f"Could not send a reminder: {exc}", None, brand())
            continue
        put_raw(f"reminder_{name}_ts", str(now))
        log_event("system", f"Reminder sent in the {name} channel ({len(items)} item(s))", None, brand())
        sent.append(text)
    return sent


@tasks.loop(minutes=30)
async def reminder_loop():
    try:
        await run_reminders()
    except Exception as exc:  # noqa: BLE001 – a failed reminder must never stop the loop
        print(f"[reminders] {exc!r}")


@reminder_loop.before_loop
async def _before_reminders():
    await bot.wait_until_ready()



if __name__ == "__main__":
    bot.run(TOKEN)
