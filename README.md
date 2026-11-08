# WOS EU1 – bug & suggestion bot + manager page + website

> **New here? Two step-by-step guides (open them in your browser):**
> 1. **`INSTALL-BOT.html`** – put the bot on a free Google Cloud server (an installer script asks the questions).
> 2. **`INSTALL-WEBSITE.html`** – connect your website: public pages on `/bugs/`, dashboard on `/staff/`.
>
> The rest of this file is reference material.

Free, no database server. Everything runs on reactions and tickets, and an admin can
manage it all from a web page (`dashboard.html`).

```
PLAYER 🐛 → private ticket thread with the bug questions → one message → preview
            Submit (to mods, ticket closes) · Edit (old text shown to copy) · Delete (discard)
PLAYER 💡 → private ticket → they write freely → preview → Submit → MOD channel (accept → also posted in the suggestions channel)

MODS      each bug in the mod channel has ✅ 🔍 ❌
            ✅ Pass → admin channel · 🔍 Investigate → private ticket with the reporter · ❌ Reject

ADMINS    the manager page: panel message, channels, on/off switches, bot name & avatar, log
```

## Files
| File | What it is |
|---|---|
| `bot.py`, `store.py`, `api.py` | The bot, its database/settings/log, and the small API the page talks to |
| `dashboard.html` | The manager page (one self-contained file, no external libraries) |
| `site/` | The public website pages (report form, known issues, changelog) |
| `deploy/install.sh` | Interactive installer for the bot server (also used for updates) |
| `deploy/setup-drive.sh` | One-time setup of weekly backups to Google Drive |
| `texts.py` | Every player-facing text in English and Polish (edit them on the dashboard's Translations page) |
| `drive.py` | Google Drive backup upload |
| `deploy/setup-updates.sh`, `deploy/update.sh` | One-time setup of updates from GitHub; then `Update bugbot` installs the released version |
| `deploy/connect-website.sh` | Interactive script for the website server (secure link, nginx/Apache, pages) |
| `deploy/` (other) | `systemd`, nginx and Apache files for manual setups |
| `INSTALL-BOT.html` / `.md` | **Start here: bot on a free cloud server** |
| `INSTALL-WEBSITE.html` / `.md` | **Then: website + dashboard** |
| `.env.example` | Copy to `.env` and fill in |

## 1. Create the bot
1. https://discord.com/developers/applications → **New Application**, name it **WOS EU1**.
2. **Bot** tab → **Reset Token** → copy it. Then scroll to **Privileged Gateway Intents** and turn ON
   **Message Content Intent** (reads answers in tickets) and **Server Members Intent** (welcomes new members).
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, tick the permissions below,
   open the URL and add the bot to your server.

**Bot permissions (in the channels it uses):** View Channel, Send Messages, Embed Links, Attach Files,
Add Reactions, Read Message History, Manage Messages (in the admin and suggestions channels too), Create Private Threads, Send Messages in Threads,
Manage Threads. The dashboard's *Overview* page checks this for you and lists anything missing.

## 2. Create channels and roles in Discord
- **Bug report channel** (players react here): players need *Add Reactions*, *Read Message History*
  and *Send Messages in Threads*; deny *Send Messages* and *Create Threads*.
- **Mod channel** (private): mods need *Add Reactions*, *Read Message History*, *Send Messages in Threads*.
- **Suggestions channel** (public showcase for accepted suggestions) and **Admin channel** (private).
- A **Moderator** role and an **Admin** role.

Turn on *Settings → Advanced → Developer Mode*, then right-click a role / the server icon → **Copy ID**.
(You choose the channels on the dashboard, so you don't need their IDs.)

## 3. Configure and run
Copy `.env.example` to `.env` and fill in: `DISCORD_TOKEN`, `GUILD_ID`, `MOD_ROLE_ID`, `ADMIN_ROLE_ID`
and a `DASHBOARD_API_KEY` (generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
Needs Python 3.10+.
```
pip install -r requirements.txt
python bot.py
```
Open the dashboard (step 4), choose your channels, write the panel message and press **Publish panel**.
(`/setup_panel` in Discord still works too.)

## 4. Put the manager page on your site
`dashboard.html` is one file. Two ways to add it:

**A. As its own page:** upload `dashboard.html` as it is (or point a route at it).

**B. Inside your site's template:** copy everything between `copy from here` and `copy until here`
(the `<div id="wosd">` and the two `<script>` blocks) into your page, and the big `<style>` block
into your stylesheet or `<head>`. Skip the small block marked *STANDALONE ONLY*.

Everything is scoped to `.wosd`, so it won't affect the rest of your site. Settings at the top of the
page: `window.WOS_DASHBOARD_CONFIG = { apiBase: "https://bot.yoursite.com", productName: "WOS EU1", theme: "auto" }`.
To match your site's look, override the colours on `.wosd`:
```css
.wosd { --w-accent:#d9480f; --w-radius:8px; --w-surface:#fff; --w-text:#222; }
```
`theme` can be `"auto"` (follows the visitor's light/dark setting), `"light"` or `"dark"`.

**Preview without a bot:** open the page with `?demo` at the end of the address (or press
*Preview with demo data* on the login screen). Nothing is saved in demo mode.

### Connecting the page to the bot (important)
The page talks to the bot over HTTPS, so the bot's API needs an HTTPS address. The easiest free way is a
**Cloudflare Tunnel** (or any reverse proxy such as Caddy/nginx) pointing at `http://127.0.0.1:8080`.
Then:
1. Set `apiBase` in the page to that address, e.g. `https://bot.yoursite.com`.
2. In `.env`, set `DASHBOARD_ORIGINS` to your website's address (e.g. `https://www.yoursite.com`).
3. Open the page, paste the `DASHBOARD_API_KEY` and press **Connect**.

**Security:** anyone with the key can control the bot, so keep it secret and only put the page where
admins can reach it (behind your site's login, if it has one). The key stays in the browser tab only until
you press *Disconnect*. Wrong keys are rate-limited. If your site has its own admin login, the safest setup
is to proxy `/api` through your server and add the key there – tell me if you want that.

## Reactions and commands
- **Mod channel (mods/admins):** ✅ Pass to admins · 🔍 Investigate (ticket with the reporter) · ❌ Duplicate/Reject.
- **Admin channel (admins only):** 🔍 Being investigated · ✅ Fixed · ❌ Rejected. The reporter gets a DM
  (if enabled) and the mod-channel copy is updated.
- **Suggestions (mod channel):** players write them in a private ticket (preview, Submit/Edit/Delete), then they land in the mod channel. Mods react ✅ Accept (also posted in the suggestions channel) · ❌ Reject. The author gets a DM if enabled.
- **Players:** `/mybugs` shows the status of their own bug reports and suggestions (only they see it).
- **Moderators:** `/duplicate bug:12 of:7` closes a bug as a duplicate of another and tells the reporter;
  `/reply bug:12` DMs the reporter (pick a ready-made reply or write your own); `/block`, `/unblock`, `/blocklist`
  stop/allow a player from opening tickets (optionally for a number of days).
- **Reminders:** every 30 minutes the bot checks for items that waited too long and posts ONE digest in the mod
  channel (new bugs and suggestions, default 24 h) and in the admin channel (passed bugs not yet fixed/rejected,
  default 72 h), tagging the role. Times and on/off are on the dashboard's Features page.
- **Patch notes (admins):** `/patchnotes preview` and `/patchnotes publish title:"Update 1.4"` post every bug marked
  Fixed since the last announcement to the patch notes channel (each bug is announced once). Edit a bug's short
  title on the dashboard to word the entry nicely.
- **Slash commands (admins):** `/setup_panel`, `/welcome message` (opens a text box), `/welcome channel`,
  `/welcome toggle`, `/welcome preview`.

## New in this version
- **Same details in Discord as on the website:** a bug ticket has an *area* menu and an **Add details** button
  (short title, chief name, player UID, game version). Players who used `/link` get chief name and UID filled in.
  Area and title are required (switch off under Features).
- **Severity:** moderators set `/severity bug level` (or use the bug page). **Critical** pings the admin role in
  the admin channel and is reminded after 4 h (default) instead of 24/72 h.
- **`/link uid chief_name`, `/unlink`, `/whois user|uid` (mods):** connects Discord and in-game account.
  It is typed in by the player and **not verified** – treat it as a hint.
- **Screenshots and videos are kept** on the bot's computer (`data/attachments`), because Discord links expire.
  View them in a bug's page. Optional auto-delete for closed bugs (Features).
- **Daily backup** of `bugs.db` to `data/backups` (last 14). Optionally posted (gzipped) to `BACKUP_CHANNEL_ID`.
  `/backup` or the dashboard's **Data** page makes one now.
- **Fixed in version:** set the *current game version* under Features; it is stamped on bugs when marked fixed.
- **Area / version / severity filters** on the Bugs page and *Bugs by area / version* on Insights.

## The website (`site/`)
A small multi-page site with a shared menu (Home · Report a bug · Known issues · What's fixed), a mobile menu and an
English/Polish switch: `index.html`, `report.html`, `known-issues.html`, `changelog.html` (+ `style.css`, `app.js`,
`config.js`). **Full step-by-step setup: `INSTALL-BOT.html`, then `INSTALL-WEBSITE.html`.**

- **Report form:** same fields as Discord tickets; while typing the title it shows *"This may already be reported"*
  (matches only bugs a moderator put on the public list). Reports arrive in the mod channel marked "website".
  Moderators can pass/reject them; website reporters get no DMs.
- **Known issues:** a bug appears only if you tick **Show on the public known-issues list** in its dashboard window.
  Only title, area, status and versions are public – never text, names or UIDs.
- **Changelog:** every `/patchnotes publish` is shown on `changelog.html` (turn on *Public changelog* under Features).
- **Spam protection:** honeypot field, minimum time on the page, 5 reports per visitor per hour, size/type limits,
  and **automatic pauses**: if the same visitor (or UID) has their last 3 website reports all rejected (duplicates
  don't count) they are paused for 3 days. Lift a pause under Moderation → *Paused website senders*.
  Addresses are stored only as scrambled codes.
- **Discord tickets** show the same similar-bug hint after "Add details".

If you have another form already, it can post to `POST /public/report` (fields `chief_name, uid, area, version,
title, description, contact`, files as `file`).

## Also
- **`/members`** (everyone): how many members the server has (plus players/bots and joins in the last 7/30 days when
  Discord has the full member list loaded).
- **Duplicates follow the original:** when a bug is closed as a duplicate, the original's message shows
  "Also reported by N more" (a quick sign of how many players hit it), and when the original is marked **Fixed**
  every player whose report was closed as its duplicate gets a message too.

## Weekly digest
- **Weekly digest:** every Monday at 09:00 (day, hour and time zone are set under Features) the bot posts a summary in
  the admin channel (or a digest channel): new / fixed / closed bugs and the change vs last week, average time to fix,
  what is waiting at the moderators and admins, critical bugs still open, the bugs waiting longest, busiest areas and
  top reporters. `/digest` (admins) or the dashboard button posts it right now. If the bot was offline at the scheduled
  time it catches up within 36 hours, otherwise it skips that week.

## The website as a phone app
The player website (`site/`) can be installed like an app: an icon on the home screen, full screen, a bottom tab bar,
and it still opens offline with the gift codes and known issues it loaded last time.
- **Android (Chrome):** an *Install app* button appears in the header and on the home page.
- **iPhone (Safari):** tap Share → *Add to Home Screen* (the button shows these steps).
- **Needs HTTPS.** Browsers only allow installing and offline mode on `https://` addresses. With a plain IP address
  (`http://1.2.3.4/bugs/`) the site still works, but it can't be installed. Point a domain at the server and add a free
  certificate (for example `sudo certbot --nginx -d bugs.example.com`).
- After changing the list of files in `site/`, bump `VERSION` in `site/sw.js` so phones pick up the new files.

## What the dashboard does
- **Bugs:** all reports with search, status filter, a detail window (read, rename, change status, close as
  duplicate, message the reporter with a ready-made reply, history) and CSV export of bugs/suggestions.
- **Insights:** reports and fixes per week, average time to fix, where bugs are now, top reporters, and what each
  moderator/admin handled (staff numbers start from the day this version is installed).
- **Data:** database size, kept files, list/download of backups, *Back up now*.
- **Patch notes:** what is waiting to be announced, a preview, and a Publish button.
- **Moderation:** ready-made replies and the list of blocked players.
- **Overview:** status, bug counters (reported, awaiting mods, waiting for admins, being investigated, fixed,
  rejected), suggestion counters (submitted, awaiting decision, accepted, rejected), and a setup checklist that shows missing channel permissions.
- **Panel message:** edit the title/text with a live preview, choose the bug report channel and publish.
  If the panel already exists in that channel it is updated in place (reactions stay).
- **Channels:** bug report, mod, suggestions, admin and (optional) patch notes channel. New suggestions go to the mod channel; accepted ones are
  posted in the suggestions channel.
- **Welcome:** switch the welcome message on/off, pick the welcome channel and edit the text with a live
  preview. Placeholders: `{user}` (tags the new member), `{username}`, `{server}`, `{member_count}`.
  If `{user}` is left out, the member is tagged at the start anyway. Bots are not welcomed.
- **Features:** switch bug reports, suggestions, 🔍 Investigate tickets and DM updates on/off; set a
  cooldown between reports, the max suggestion length, and whether finished tickets are deleted or archived.
- **Bot profile:** avatar, username, server nickname, status and activity text.
  (Discord limits username changes to 2 per hour.)
- **Activity log:** every ticket, report, mod action, suggestion, panel/settings/profile change; filter and
  search. The last 5,000 entries are kept.

## Keep it running (free)
The bot and its API only work while `python bot.py` runs: an always-on PC/Raspberry Pi, or an Oracle Cloud
"Always Free" VM (run it with `systemd` or `tmux`). `bugs.db` (reports, settings, log) and the `data/` folder (kept files, backups) are what to copy if you move the bot.

## Notes
- One open bug ticket per player. Reacting 🐛 again points them to the open ticket.
- Players should keep DMs from server members on to receive status updates (reports and suggestions are written in tickets, not DMs).
- Don't remove the bot's own ✅ 🔍 ❌ reactions under reports – they are the mods' buttons.
- Updating from the previous version: keep your `bugs.db`, it is upgraded automatically. Remember to turn on *Message Content Intent* and *Server Members Intent*, and add the new `.env` lines (optional `WELCOME_CHANNEL_ID`).
