"""WOS EU1 – reaction-based bug & suggestion bot with a web dashboard API.

Players:   react 🐛 (Report a bug) or 💡 (Create a suggestion) on the panel message.
           🐛  -> a private ticket thread opens with the bug questions. The player
                  answers in one message, gets a preview and presses
                  Submit (send to mods + close ticket) / Edit (restart, old text
                  shown for copy/paste) / Delete (discard + close ticket).
           💡  -> a private ticket thread opens; the player writes the suggestion freehand,
                  gets the same preview (Submit / Edit / Delete) and it goes to the MOD channel.
Players:   /mybugs shows the status of their own reports.
Everyone:  /members, /mybugs, /points, /leaderboard, /link, /unlink
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
import base64
import math
import os
import gzip
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


# ------------------------------------------- player: bug ticket (private thread)
def questions_embed(edit: bool = False, kind: str = "bug") -> discord.Embed:
    if kind == "suggestion":
        return discord.Embed(
            title="✏️ Edit your suggestion" if edit else "💡 Create a suggestion",
            description=(f"Write your suggestion **in one message** (max {cfg('suggestion_max_length')} characters). "
                         "You can add a picture too.\n\nWhen you send it, I'll show you a preview where you can "
                         "**Submit**, **Edit** or **Delete** it."),
            color=0xFEE75C)
    e = discord.Embed(
        title="✏️ Edit your bug report" if edit else "🐛 Bug report",
        description=(
            "**Step 1** – pick the **area** in the menu, then press **Add details** "
            "(short title, chief name, player UID, game version).\n"
            "**Step 2** – answer these questions **in one message**:\n\n"
            "**1. What is the bug?**\n"
            "**2. Do you know why it is happening?**\n"
            "**3. What is the correct way the feature should work?**\n"
            "**4. Attach a picture or video** to the same message"
            + (" (attachments have to be added again)" if edit else "") + "\n\n"
            "When you press **Send**, I'll show you a preview where you can "
            "**Submit**, **Edit** or **Delete** your report."
        ),
        color=0x5865F2,
    )
    e.add_field(
        name="Template you can copy",
        value="```\nWhat is the bug?\n\nDo you know why it is happening?\n\n"
              "What is the correct way the feature should work?\n```",
        inline=False,
    )
    return e


def preview_embed(text: str, atts, t=None) -> discord.Embed:
    e = discord.Embed(
        title="📋 Preview – is this ready to send?",
        description=text or "*(no text)*",
        color=0xFEE75C,
    )
    if t is not None and t["kind"] == "bug":
        e.add_field(name="Title", value=t["title"] or "⚠️ *(missing – press Add details)*", inline=False)
        e.add_field(name="Area", value=t["area"] or "⚠️ *(missing – use the menu)*", inline=True)
        e.add_field(name="Game version", value=t["game_version"] or "–", inline=True)
        e.add_field(name="Chief / UID", value=f"{t['chief_name'] or '–'} · {t['player_uid'] or '–'}", inline=True)
    if atts:
        e.add_field(name="Attachments",
                    value="\n".join(f"📎 {a.filename}" for a in atts)[:1000], inline=False)
    e.set_footer(text="Submit = send to the moderators · Edit = change it · Delete = discard")
    return e


async def refresh_preview(thread, thread_id: int):
    """Re-draw the preview after the player changed the area or details."""
    t = get_ticket(thread_id)
    if t is None or t["state"] != "preview" or not t["preview_msg_id"] or not t["draft_msg_id"]:
        return
    try:
        draft = await thread.fetch_message(t["draft_msg_id"])
        await thread.get_partial_message(t["preview_msg_id"]).edit(
            embed=preview_embed(draft.content.strip(), draft.attachments, t))
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


async def open_ticket(member: discord.Member, channel: discord.TextChannel, kind: str = "bug"):
    async def tell(msg: str):
        await channel.send(f"{member.mention} {msg}", delete_after=15)

    blk = store.get_block(member.id)
    if blk:
        when = f" until <t:{blk['until']}:f>" if blk["until"] else ""
        return await tell(f"you can't send reports or suggestions right now{when}. "
                          "Please contact a moderator if you think this is a mistake.")
    if not cfg("bugs_enabled" if kind == "bug" else "suggestions_enabled"):
        return await tell("bug reports are currently turned off." if kind == "bug"
                          else "suggestions are currently turned off.")
    if chan("mod_channel_id") is None:
        return await tell("this isn't fully set up yet – please let an admin know.")
    existing = await get_open_ticket_thread(member.id)
    if existing:
        return await tell(f"you already have an open ticket: {existing.mention}")
    cooldown = cfg("report_cooldown_minutes") if kind == "bug" else 0
    if cooldown:
        last = db.execute("SELECT MAX(created_at) AS m FROM bug_reports WHERE reporter_id=?",
                          (member.id,)).fetchone()["m"]
        if last and time.time() - last < cooldown * 60:
            wait = math.ceil((cooldown * 60 - (time.time() - last)) / 60)
            return await tell(f"please wait {wait} more minute(s) before sending another report.")

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
    await thread.send(content=member.mention, embed=questions_embed(kind=kind),
                      view=DetailsView() if kind == "bug" else None)
    await channel.send(f"{member.mention} your ticket is ready: {thread.mention}", delete_after=15)


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
    preview = await message.reply(embed=preview_embed(text, message.attachments, t),
                                  view=PreviewView(), mention_author=False)
    set_ticket(message.channel.id, state="preview", draft_msg_id=message.id,
               preview_msg_id=preview.id)


async def _owner_guard(interaction: discord.Interaction) -> bool:
    t = get_ticket(interaction.channel_id)
    if t is None or t["closed"] or t["kind"] != "bug":
        msg = "This ticket is closed."
    elif interaction.user.id != t["user_id"]:
        msg = "Only the person who opened this ticket can use this."
    else:
        return True
    await interaction.response.send_message(msg, ephemeral=True)
    return False


class DetailsModal(discord.ui.Modal, title="Bug details"):
    title_in = discord.ui.TextInput(label="Short title (what is wrong, in a few words)", max_length=100,
                                    min_length=5, placeholder="e.g. Horse sinks when mounted near water")
    chief = discord.ui.TextInput(label="Chief name (optional)", required=False, max_length=50)
    uid = discord.ui.TextInput(label="Player UID (optional)", required=False, max_length=20)
    version = discord.ui.TextInput(label="Game version", required=False, max_length=30)

    def __init__(self, t):
        super().__init__()
        self.title_in.default = t["title"]
        self.chief.default = t["chief_name"]
        self.uid.default = t["player_uid"]
        self.version.default = t["game_version"] or cfg("game_version") or None
        if not cfg("ticket_details_required"):
            self.title_in.required = False
            self.title_in.min_length = None

    async def on_submit(self, interaction: discord.Interaction):
        try:
            uid = store.clean_uid(str(self.uid))
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc} Press **Add details** to try again.",
                                                           ephemeral=True)
        set_ticket(interaction.channel_id, title=str(self.title_in).strip() or None,
                   chief_name=str(self.chief).strip() or None, player_uid=uid or None,
                   game_version=str(self.version).strip() or None)
        text = "✅ Details saved. Now write your answers in one message."
        similar = store.similar_bugs(str(self.title_in), area=None)
        if similar:
            text += ("\n\n🔎 **This may already be reported:**\n"
                     + "\n".join(f"• {s['title']} – *{s['status_label']}*"
                                  + (f" (fixed in {s['fixed_in']})" if s["fixed_in"] else "") for s in similar)
                     + "\nIf it is the same problem you don't need to report it again – you can delete this ticket. "
                       "If it is different, just carry on.")
        await interaction.response.send_message(text, ephemeral=True)
        await refresh_preview(interaction.channel, interaction.channel_id)


class DetailsView(discord.ui.View):
    """Area menu + 'Add details' button under the questions (persistent across restarts)."""

    def __init__(self):
        super().__init__(timeout=None)
        areas = store.area_list()[:25] or ["Other"]
        self.area.options = [discord.SelectOption(label=a) for a in areas]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _owner_guard(interaction)

    @discord.ui.select(placeholder="Where is the problem? (area)", custom_id="ticket:area",
                       options=[discord.SelectOption(label="Other")])
    async def area(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        if value not in store.area_list():
            return await interaction.response.send_message("That area doesn't exist any more.", ephemeral=True)
        set_ticket(interaction.channel_id, area=value)
        await interaction.response.send_message(f"✅ Area: **{value}**", ephemeral=True)
        await refresh_preview(interaction.channel, interaction.channel_id)

    @discord.ui.button(label="Add details", style=discord.ButtonStyle.secondary, custom_id="ticket:details")
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DetailsModal(get_ticket(interaction.channel_id)))


class PreviewView(discord.ui.View):
    """Submit / Edit / Delete buttons under the preview (persistent across restarts)."""

    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        t = get_ticket(interaction.channel_id)
        if t is None or t["closed"]:
            msg = "This ticket is closed."
        elif interaction.user.id != t["user_id"]:
            msg = "Only the person who opened this ticket can use these buttons."
        elif t["state"] != "preview" or t["preview_msg_id"] != interaction.message.id:
            msg = "This preview is outdated – please use the latest one."
        else:
            return True
        await interaction.response.send_message(msg, ephemeral=True)
        return False

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success, custom_id="ticket:submit")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        if t["kind"] == "bug" and cfg("ticket_details_required"):
            missing = [x for x, v in (("an **area** (menu)", t["area"]), ("a **short title** (Add details)", t["title"])) if not v]
            if missing:
                return await interaction.response.send_message("Please add " + " and ".join(missing) + " first.",
                                                               ephemeral=True)
        set_ticket(t["thread_id"], state="submitting")   # blocks double clicks
        await interaction.response.defer()
        thread = interaction.channel
        if store.get_block(interaction.user.id):
            set_ticket(t["thread_id"], state="preview")
            await thread.send("You can't send reports or suggestions right now. Please contact a moderator.")
            return
        try:
            draft = await thread.fetch_message(t["draft_msg_id"])
        except discord.NotFound:
            set_ticket(t["thread_id"], state="waiting")
            await interaction.message.edit(view=None)
            await thread.send("I can't find your report message any more (was it deleted?). "
                              "Please send it again.")
            return

        name = interaction.user.display_name
        if t["kind"] == "suggestion":
            text = draft.content.strip()
            if not text:
                set_ticket(t["thread_id"], state="preview")
                await thread.send("Please write your suggestion as text (a picture alone isn't enough).")
                return
            if len(text) > cfg("suggestion_max_length"):
                set_ticket(t["thread_id"], state="waiting")
                await interaction.message.edit(view=None)
                await thread.send(f"Your suggestion is {len(text)} characters – the limit is "
                                  f"{cfg('suggestion_max_length')}. Please send a shorter version.")
                return
            try:
                s = await submit_suggestion(interaction.user, text)
            except (NotConfigured, discord.HTTPException) as exc:
                set_ticket(t["thread_id"], state="preview")
                await thread.send("⚠️ I couldn't deliver your suggestion to the moderators. Please tell an admin, "
                                  "then press **Submit** again.")
                log_event("system", f"Could not deliver a suggestion from {name}: {exc}",
                          interaction.user.id, name)
                return
            log_event("suggestion", f"Suggestion #{s['id']} submitted by {name}", interaction.user.id, name)
            done = interaction.message.embeds[0]
            done.title = f"✅ Submitted as suggestion #{s['id']}"
            done.colour = discord.Colour(0x57F287)
            await interaction.message.edit(embed=done, view=None)
            await thread.send(f"✅ Your suggestion was sent to the moderators as **#{s['id']}**. "
                              "This ticket closes in a few seconds.")
            await asyncio.sleep(5)
            await close_ticket(thread, keep=not cfg("delete_ticket_on_close"))
            return
        try:
            r, used_links = await submit_bug(interaction.user, draft.content.strip(), draft.attachments[:4], t)
        except (NotConfigured, discord.HTTPException) as exc:
            set_ticket(t["thread_id"], state="preview")  # let them press Submit again
            await thread.send("⚠️ I couldn't deliver your report to the moderators. "
                              "Please tell an admin (the mod channel may be missing or I lack access), "
                              "then press **Submit** again.")
            log_event("system", f"Could not deliver a bug report from {name}: {exc}",
                      interaction.user.id, name)
            return
        log_event("bug", f"Bug #{r['id']} submitted by {name}", interaction.user.id, name)

        done = interaction.message.embeds[0]
        done.title = f"✅ Submitted as bug #{r['id']}"
        done.colour = discord.Colour(0x57F287)
        await interaction.message.edit(embed=done, view=None)
        await thread.send(
            f"✅ Your report was sent to the moderators as **bug #{r['id']}**. "
            "I'll DM you when a moderator handles it (keep your DMs open).\n"
            "This ticket closes in a few seconds."
        )
        await asyncio.sleep(5)
        await close_ticket(thread, keep=used_links or not cfg("delete_ticket_on_close"))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, custom_id="ticket:edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        set_ticket(t["thread_id"], state="waiting")
        previous = ""
        if interaction.message.embeds:
            previous = interaction.message.embeds[0].description or ""
        if previous == "*(no text)*":
            previous = ""
        await interaction.response.edit_message(view=None)
        embeds = [questions_embed(edit=True, kind=t['kind'])]
        if previous:
            safe = previous.replace("```", "`​``")[:4000]
            embeds.append(discord.Embed(
                title="Your previous message (copy & edit)",
                description=f"```\n{safe}\n```", color=0x99AAB5))
        await interaction.channel.send(embeds=embeds)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket:delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = get_ticket(interaction.channel_id)
        set_ticket(t["thread_id"], state="closing")
        await interaction.response.send_message("🗑️ Draft discarded. Closing this ticket…")
        log_event("ticket", f"{interaction.user.display_name} discarded their {t['kind']} draft",
                  interaction.user.id, interaction.user.display_name)
        await asyncio.sleep(3)
        await close_ticket(interaction.channel)


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
        msg = await channel.send(embed=bug_embed(r), files=files)
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
            msg = await channel.send(embed=bug_embed(r))
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
        msg = await channel.send(embed=suggestion_embed(store.get_suggestion(sid)))
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
            admin_msg = await admin.send(embed=bug_embed(r, note), files=files)
        except discord.HTTPException:
            admin_msg = await admin.send(embed=bug_embed(r, note))
        set_bug(r["id"], admin_msg_id=admin_msg.id)
        for e in (ADMIN_INVESTIGATING_EMOJI, ADMIN_FIXED_EMOJI, ADMIN_REJECT_EMOJI):
            await admin_msg.add_reaction(e)
        await msg.edit(embed=bug_embed(r, note))
        await msg.clear_reactions()
        log_event("mod", f"Bug #{r['id']} passed to admins by {who}", member.id, who)
        if r["severity"] == "critical":
            await ping_critical(r, who)
        await dm(r["reporter_id"],
                 f"✅ Your bug report **#{r['id']}** was accepted and passed on to the admins. Thank you!"
                 + points_note(r["id"]))

    elif emoji == REJECT_EMOJI:
        set_bug(r["id"], status="rejected")
        record_action("bug", r["id"], "rejected", member.id, who)
        r = get_bug(r["id"])
        await msg.edit(embed=bug_embed(r, f"Rejected by {who}"))
        await msg.clear_reactions()
        log_event("mod", f"Bug #{r['id']} rejected (duplicate / as intended) by {who}", member.id, who)
        await dm(r["reporter_id"],
                 f"❌ Your bug report **#{r['id']}** was closed – it's a duplicate of an existing report "
                 "or the feature is working as intended. Thanks for taking the time to report it!")

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
        await thread.send(
            f"🎫 **Ticket for bug #{r['id']}** – {member.mention} needs more information.\n"
            f"{mention}, please answer the moderator's questions here.\n"
            f"Original report: {msg.jump_url}"
        )
        set_bug(r["id"], status="investigating", ticket_thread_id=thread.id)
        record_action("bug", r["id"], "investigating", member.id, who)
        r = get_bug(r["id"])
        await msg.edit(embed=bug_embed(r, f"Ticket opened by {who}"))
        log_event("mod", f"Bug #{r['id']}: info ticket opened by {who}", member.id, who)
        await dm(r["reporter_id"],
                 f"🔍 A moderator needs more information about your bug report **#{r['id']}**. "
                 f"Please reply in your ticket: {thread.jump_url}")


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
    texts = {
        "being_investigated": f"🔍 Your bug report **#{r['id']}** is now being investigated by the admins.",
        "fixed": f"✔️ Good news! Your bug report **#{r['id']}** has been fixed. Thank you for reporting it!"
                 + points_note(r["id"]),
        "admin_rejected": f"❌ After a closer look, the admins closed your bug report **#{r['id']}**. Thanks for reporting it!",
    }
    await dm(r["reporter_id"], texts[new])
    if new == "fixed":
        await notify_followers(r)


def suggestion_embed(r, note: Optional[str] = None) -> discord.Embed:
    label, color = {"new": ("🆕 Open", 0x3498DB), "accepted": ("✅ Accepted", 0x2ECC71),
                    "rejected": ("❌ Rejected", 0xED4245)}[r["status"]]
    e = discord.Embed(title=f"Suggestion #{r['id']}", description=r["text"], color=color)
    e.add_field(name="From", value=f"<@{r['author_id']}>", inline=True)
    e.add_field(name="Status", value=label, inline=True)
    e.set_footer(text=brand() + (f" · {note}" if note else ""))
    return e


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
    new = "accepted" if emoji == SUGGEST_ACCEPT_EMOJI else "rejected"
    set_suggestion(r["id"], status=new)
    record_action("suggestion", r["id"], new, member.id, member.display_name)
    r = get_suggestion(r["id"])
    who = member.display_name
    await partial.edit(embed=suggestion_embed(r, f"{new.capitalize()} by {who}"))
    for e in (SUGGEST_ACCEPT_EMOJI, SUGGEST_REJECT_EMOJI):
        try:
            await partial.clear_reaction(e)
        except discord.HTTPException:
            pass
    if new == "accepted":
        target = chan("suggestions_channel_id")
        if target is not None:
            try:
                pub = await target.send(embed=suggestion_embed(r))
                set_suggestion(r["id"], public_msg_id=pub.id)
            except discord.HTTPException as exc:
                log_event("system", f"Could not post accepted suggestion #{r['id']}: {exc}",
                          member.id, who)
    log_event("mod", f"Suggestion #{r['id']} {new} by {who}", member.id, who)
    await dm(r["author_id"],
             f"💡 Your suggestion **#{r['id']}** was accepted – thank you!" if new == "accepted"
             else f"💡 Your suggestion **#{r['id']}** was reviewed but won't be taken forward this time. "
                  "Thanks for sharing your idea!")


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
    e.set_footer(text=f"{BUG_EMOJI} Report a bug  ·  {SUGGEST_EMOJI} Create a suggestion  ·  {brand()}")
    return e


async def publish_panel(title: str, text: str, channel_id: int,
                        actor_id: Optional[int] = None, actor_name: str = "Dashboard") -> discord.Message:
    """Post the panel in the bug report channel, or edit it in place if it is already there."""
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Pick a normal text channel for the panel.")
    perms = channel.permissions_for(channel.guild.me)
    need = {"view_channel": "View Channel", "send_messages": "Send Messages", "embed_links": "Embed Links",
            "add_reactions": "Add Reactions", "read_message_history": "Read Message History",
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
            await msg.edit(embed=embed)
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
        msg = await channel.send(embed=embed)
    for emoji in (BUG_EMOJI, SUGGEST_EMOJI):
        await msg.add_reaction(emoji)

    set_cfg("panel_title", title)
    set_cfg("panel_text", text)
    set_cfg("report_channel_id", channel.id)
    put_raw("panel_channel_id", str(channel.id))
    put_raw("panel_msg_id", str(msg.id))
    bot.panel_msg_id = msg.id
    log_event("panel", f"Panel message published in #{channel.name}", actor_id, actor_name)
    return msg


# ------------------------------------------------------------- bot profile
def profile_info() -> dict:
    guild = bot.get_guild(GUILD_ID)
    return {
        "username": bot.user.name,
        "avatar_url": str(bot.user.display_avatar.replace(size=256).url),
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
        await self.tree.sync(guild=discord.Object(id=GUILD_ID))
        if not reminder_loop.is_running():
            reminder_loop.start()
        if not backup_loop.is_running():
            backup_loop.start()
        if not schedule_loop.is_running():
            schedule_loop.start()

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
                         "set_severity": set_severity, "run_backup": run_backup, "post_digest": post_digest}
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

    async def on_member_join(self, member: discord.Member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        store.record_members(member.guild.member_count, joined=1)
        try:
            if await send_welcome(member):
                log_event("welcome", f"Welcomed {member.display_name}", member.id, member.display_name)
        except discord.HTTPException as exc:
            log_event("system", f"Could not send the welcome message: {exc}", member.id, member.display_name)

    async def on_member_remove(self, member: discord.Member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        store.record_members(member.guild.member_count, left=1)

    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.channel, discord.Thread):
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
                           duplicate_of: Optional[int] = None, log_kind: str = "admin"):
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

    set_bug(r["id"], status=new, duplicate_of=dup["id"] if dup else None)
    if new == "fixed":
        stamp_fixed_version(r["id"])
    record_action("bug", r["id"], new, actor_id, actor_name)
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
            admin_msg = await admin.send(embed=bug_embed(r, note), files=files)
        except discord.HTTPException:
            admin_msg = await admin.send(embed=bug_embed(r, note))
        set_bug(r["id"], admin_msg_id=admin_msg.id)
        r = get_bug(r["id"])
    await sync_bug_messages(r, note)
    if new == "passed" and r["severity"] == "critical":
        await ping_critical(r, actor_name)

    label = STATUS[new][0].split(" ", 1)[1]
    suffix = f" (duplicate of #{dup['id']})" if dup else ""
    log_event(log_kind, f"Bug #{r['id']} marked “{label}”{suffix} by {actor_name}", actor_id, actor_name)
    if dup:
        text = (f"❌ Your bug report **#{r['id']}** is a duplicate of **#{dup['id']}** "
                f"({PLAYER_STATUS[dup['status']].split(' ', 1)[1]}). We already have it on our list – "
                "thanks for reporting!")
    else:
        text = {
            "passed": f"✅ Your bug report **#{r['id']}** was accepted and passed on to the admins. Thank you!"
                      + points_note(r["id"]),
            "being_investigated": f"🔍 Your bug report **#{r['id']}** is now being investigated by the admins.",
            "fixed": f"✔️ Good news! Your bug report **#{r['id']}** has been fixed. Thank you for reporting it!"
                 + points_note(r["id"]),
            "rejected": f"❌ Your bug report **#{r['id']}** was closed – it's a duplicate of an existing report "
                        "or the feature is working as intended. Thanks for taking the time to report it!",
            "admin_rejected": f"❌ After a closer look, the admins closed your bug report **#{r['id']}**. "
                              "Thanks for reporting it!",
        }[new]
    await dm(r["reporter_id"], text)
    if dup:
        await sync_bug_messages(dup)             # the original now shows how many others hit the same bug
    if new == "fixed":
        await notify_followers(r)
    return r


async def notify_followers(r):
    """A bug was fixed: tell the players whose reports were closed as duplicates of it."""
    told = {r["reporter_id"]}
    for d in store.duplicates_of(r["id"]):
        if d["reporter_id"] and d["reporter_id"] not in told:
            told.add(d["reporter_id"])
            await dm(d["reporter_id"], f"✔️ Good news! The problem from your report **#{d['id']}** "
                                       f"(same as **#{r['id']}**) has been fixed. Thank you for reporting it!")
    if len(told) > 1:
        log_event("system", f"Told {len(told) - 1} player(s) who reported duplicates that bug #{r['id']} is fixed",
                  None, brand())


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
        await user.send(f"💬 **Message from the moderators about your bug report #{r['id']}**\n\n{text}")
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
    try:
        store.set_link(interaction.user.id, uid, chief_name)
    except ValueError as exc:
        return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
    log_event("link", f"{interaction.user.display_name} linked to UID {store.clean_uid(uid)} ({chief_name.strip()[:50]})",
              interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(
        "✅ Linked. Your chief name and UID are now filled in automatically when you report a bug. "
        "Use `/unlink` to remove it.", ephemeral=True)


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
            msg = await channel.send(embed=bug_embed(r), files=[discord.File(io.BytesIO(d), filename=n) for n, d in files])
        except discord.HTTPException:
            msg = await channel.send(embed=bug_embed(r))      # file upload refused: still deliver the report
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
    return bug_id


# ---------------------------------------------------------------- backups
async def run_backup(force: bool = False):
    """Copy the database to data/backups (and to the backup channel, if one is set). Returns the path or None."""
    latest = store.list_backups()
    if not force and latest and time.time() - latest[0]["ts"] < 20 * 3600:
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
    return dest


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


# ============================================================ points, leaderboard, weekly digest
def points_note(bug_id: int) -> str:
    p = store.bug_points(bug_id) if cfg("bounty_enabled") else 0
    return f"\n🏆 You have earned **{p} point{'s' if p != 1 else ''}** for this report – see `/points`." if p else ""


def _medal(i: int) -> str:
    return ("🥇", "🥈", "🥉")[i] if i < 3 else f"**{i + 1}.**"


def leaderboard_embed(period: str, month: Optional[str] = None, limit: int = 10) -> discord.Embed:
    rows = store.leaderboard(period, limit, month)
    title = "🏆 Bug hunters – all time" if period == "all" else f"🏆 Bug hunters – {store.month_range(month)[2]}"
    e = discord.Embed(title=title, color=0xF1C40F)
    if not rows:
        e.description = "No points yet. Report a bug with the 🐛 reaction to get on the list!"
    else:
        e.description = "\n".join(f"{_medal(i)} <@{r['user_id']}> – **{r['points']}** pts"
                                  f" · {r['accepted']} accepted · {r['fixed']} fixed"
                                  + (f" · {r['suggestions']} ideas" if r["suggestions"] else "")
                                  for i, r in enumerate(rows))
    e.set_footer(text=f"{cfg('points_accepted')} pt accepted · {cfg('points_fixed')} pts fixed · "
                      f"{cfg('points_suggestion')} pts accepted suggestion")
    return e


@bot.tree.command(name="points", description="See your bug-hunter points", guild=_GUILD)
@app_commands.describe(user="Look at someone else (optional)")
@app_commands.guild_only()
async def points_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    if not cfg("bounty_enabled"):
        return await interaction.response.send_message("Points are switched off on this server.", ephemeral=True)
    who = user or interaction.user
    p = store.points_of(who.id)
    if not p["total"]:
        text = (f"{who.mention} has no points yet. You get points when a moderator accepts your bug report, "
                f"more when it is fixed (**{cfg('points_accepted')}** / **{cfg('points_fixed')}** pts), "
                f"and **{cfg('points_suggestion')}** for an accepted suggestion.")
    else:
        text = (f"🏆 {who.mention}: **{p['total']}** points (rank **#{p['rank']}**) · {p['month']} this month\n"
                f"{p['accepted']} accepted · {p['fixed']} fixed · {p['suggestions']} accepted suggestions")
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="leaderboard", description="The best bug hunters", guild=_GUILD)
@app_commands.describe(period="This month or all time")
@app_commands.choices(period=[app_commands.Choice(name="This month", value="month"),
                              app_commands.Choice(name="All time", value="all")])
@app_commands.guild_only()
async def leaderboard_cmd(interaction: discord.Interaction, period: Optional[app_commands.Choice[str]] = None):
    if not cfg("bounty_enabled"):
        return await interaction.response.send_message("Points are switched off on this server.", ephemeral=True)
    await interaction.response.send_message(embed=leaderboard_embed(period.value if period else "month"),
                                            allowed_mentions=discord.AllowedMentions.none())


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


async def run_schedules(now=None):
    """Called every few minutes: posts the weekly digest and the monthly leaderboard when they are due.
    Returns the names of what was posted."""
    from datetime import datetime, timedelta
    now = now or datetime.now(store.tz())
    done = []
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
    ch = chan("bounty_channel_id")
    if cfg("bounty_enabled") and ch is not None and now.day <= 3 and now.hour >= 10:
        month = store.previous_month()
        if get_raw("leaderboard_last") != month:
            put_raw("leaderboard_last", month)
            if store.leaderboard("month", 1, month):
                try:
                    await ch.send(f"🎉 **Top bug hunters of {month}** – thank you all!",
                                  embed=leaderboard_embed("month", month, 5),
                                  allowed_mentions=discord.AllowedMentions.none())
                    log_event("system", f"Monthly leaderboard for {month} posted", None, brand())
                    done.append("leaderboard")
                except discord.HTTPException as exc:
                    log_event("system", f"Could not post the monthly leaderboard: {exc}", None, brand())
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


@tasks.loop(minutes=10)
async def schedule_loop():
    try:
        snapshot_members()
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
    bugs = db.execute("SELECT * FROM bug_reports WHERE reporter_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    sugs = db.execute("SELECT * FROM suggestions WHERE author_id=? ORDER BY id DESC LIMIT 5", (uid,)).fetchall()
    if not bugs and not sugs:
        return await interaction.response.send_message(
            "You haven't sent any bug reports or suggestions yet.", ephemeral=True)
    e = discord.Embed(title="Your reports", color=0x5865F2)
    if bugs:
        lines = []
        for x in bugs:
            extra = f" (duplicate of #{x['duplicate_of']})" if x["duplicate_of"] else ""
            lines.append(f"**#{x['id']}** {store.bug_summary(x)[:70]}\n└ {PLAYER_STATUS[x['status']]}{extra}")
        e.add_field(name="🐛 Bug reports", value="\n".join(lines)[:1024], inline=False)
    if sugs:
        label = {"new": "⏳ Waiting for a moderator", "accepted": "✅ Accepted", "rejected": "❌ Not taken forward"}
        lines = [f"**#{x['id']}** {x['text'].splitlines()[0][:70]}\n└ {label[x['status']]}" for x in sugs]
        e.add_field(name="💡 Suggestions", value="\n".join(lines)[:1024], inline=False)
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
