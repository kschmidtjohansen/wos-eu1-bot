# Staff dashboard v2 – phase 1 (daily work) Implementation Plan

> **For agentic workers:** Implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new staff dashboard at `/staff/v2/` whose start screen is one **To do** list of everything waiting
for a person, with a detail pane, Bugs and Ideas lists, quick actions and search – next to the current dashboard.

**Architecture:** One new self-contained page `dashboard-v2.html` (HTML + CSS + JS, no build step, same pattern as
`dashboard.html`) that talks to the existing API. One new read-only endpoint `GET /api/todo` gathers the To do
items in one request. Demo mode (`?demo`, `?demo=mod`) serves mock data from inside the page, as v1 does.

**Tech Stack:** Python 3 / aiohttp (api.py), SQLite (store.py), vanilla JS with tagged-template rendering, Playwright for browser tests.

**Spec:** `docs/superpowers/specs/2026-11-27-staff-dashboard-v2-design.md`

## Global Constraints

- Served at **/staff/v2/** next to the current dashboard at /staff/; same login, same API, same demo mode (`?demo`, `?demo=mod`).
- Moderator restrictions (MOD_READ / MOD_WRITE) apply unchanged; moderators never see ideas, settings or admin-only items.
- 5 places: **To do · Bugs · Ideas · Community · More**. Desktop: left rail. Phone: bottom bar when installed (`.w-app`), top bar in a browser.
- Light and dark theme following the device; one accent colour for the single main action per screen; status always shown with a word or icon, never colour alone; no emoji as icons (SVG line icons only).
- System font stack; tabular numbers for counts and times; inputs ≥ 16 px on phones; tap targets ≥ 40 px on phones.
- Phase 1: Community and More link to the matching page of the current dashboard (`/staff/#<tab>`).
- Nothing goes live until "Update bugbot"; the current dashboard keeps working unchanged.

## Review Focus

- **Empty To do** (nothing waiting) → a calm "All done" state with "Done today" still reachable, not a blank page. (Task 3 test)
- **Item handled elsewhere** (in Discord or by another moderator while the list is open) → the action returns an error or a changed status; the item leaves the list with a short message instead of a broken pane. (Task 3/4 test: 400/404 from the action refreshes the list)
- **Moderator logged in** → no idea items, no admin actions, no 403 toasts on load. (Task 1 test + Task 3 `?demo=mod` test)
- **Phone Back button** with the pane open → closes the pane, stays in the app. (Task 4 test)
- **Long or foreign text** (Arabic, Chinese, 1,800-character reports, long URLs) → wraps inside the item and the pane, no sideways scroll at 390 px. (Task 3 test with a long mock item)

---

### Task 1: `GET /api/todo` and login return path

**Files:**
- Modify: `api.py` (new route next to `/api/queue`; MOD_READ; `auth_login` / `auth_callback`)
- Test: `/tmp/test_todo_api.py` (same pattern as the other `/tmp/test_*.py`, run with the usual env)

**Interfaces:**
- Produces: `GET /api/todo` →
  `{"generated": int, "counts": {kind: int}, "items": [Item]}` where
  `Item = {"kind": str, "id": int|str, "ts": int, "title": str, "text": str, "who": str, "urgent": bool, "ref": dict}`.
  Kinds and sources (admins get all; moderators only those marked *mod*):
  `bug_new` *mod* (status new, not a duplicate), `bug_dup` *mod* (`dup_suggest` set, open), `bug_info` *mod* (status investigating),
  `bug_admin` (passed / being_investigated), `chat` *mod* (bug web chat unread, `store.web_unread()` positive keys) and admin-only idea chats (negative keys),
  `dev_reply` (dev_reply_ts > dev_ts), `idea_new` (suggestion status new), `report` *mod* (`store.msg_reports("open")`),
  `appeal` *mod* (`store.appeals("open")`; ban appeals admin-only), `spike` *mod* (`hooks["active_spikes"]()` not known), `raid` *mod* (raid active),
  `code_ending` (active codes expiring within `giftcodes_remind_hours`), `setup` (health channels with `missing` or not found), `job` (`hooks["job_health"]()` with fails > 0).
  `urgent` = critical severity, raid, spike, job. Items sorted: urgent first, then oldest first. `ref` carries what the client needs for actions
  (e.g. bug: `status`, `severity`, `dup_suggest`, `dup_title`, `dup_score`; report: `user_id`, `channel`; appeal: `kind`).
- Produces: `GET /api/auth/login?next=/staff/v2/` – remembered in the state cookie; after login the callback redirects there.
  Only paths starting with `/staff/` are accepted (anything else falls back to the configured `return_to`).

- [ ] **Step 1: Write the failing test** – `test_todo_api.py` builds the app with `api.build_app` (hooks: `active_spikes`, `job_health` stubs) and seeds:
  one new bug, one critical new bug, one passed bug, one bug with `dup_suggest`, one open report, one ban appeal, one timeout appeal, one new suggestion,
  one unread website message on a bug and one on an idea (`store.idea_key`). Assertions:
  admin: every kind above present, `items[0]["urgent"] is True` (the critical bug), counts match;
  moderator session (`store.create_session` + mod role stub as in existing auth tests): no `idea_new`, no idea chat, no `bug_admin`, no ban appeal, timeout appeal present;
  `GET /api/auth/login?next=https://evil.example` keeps the default return; `?next=/staff/v2/` is stored.
- [ ] **Step 2: Run** `cd /tmp/t && rm -rf bugs.db* data && DISCORD_TOKEN=x GUILD_ID=1 MOD_ROLE_ID=6 ADMIN_ROLE_ID=7 python3 -I /tmp/test_todo_api.py` – expect 404 on `/api/todo`.
- [ ] **Step 3: Implement** `todo_payload(role: str) -> dict` inside `build_app` and the route; add `r"/api/todo"` to MOD_READ; `next` handling in login/callback.
- [ ] **Step 4: Run** the test – PASS; run the full `/tmp/test_*.py` loop – all pass.
- [ ] **Step 5: Commit** `feat(api): /api/todo for the v2 start screen; login can return to /staff/v2/`

### Task 2: v2 shell – page, theme, navigation, routing, quick actions

**Files:**
- Create: `dashboard-v2.html`
- Modify: `deploy/update.sh:64`, `deploy/connect-website.sh:154` (also write `$WWW/staff/v2/index.html` with the same `apiBase` sed), `README.md` (one paragraph)
- Test: `/tmp/test_v2_shell.py` (Playwright, file:// with `?demo`)

**Interfaces:**
- Produces (JS, used by later tasks): `html` tagged template + `Raw`; `call(method, path, body)`; `icon(name)`; `toast(text, kind)`;
  `S` state; `go(place, sub?)` and hash routes `#todo`, `#bugs`, `#ideas`, `#community`, `#more`, `#bug-<id>`, `#idea-<id>`, `#report-<id>`, `#appeal-<id>`;
  `openPane(kind, id)` / `closePane()` (implemented in Task 4); `demoMock(method, path, body)` returning mock data for every endpoint phase 1 calls;
  CSS tokens `--bg --surface --surface-2 --text --muted --line --accent --accent-ink --ok --warn --bad` for light and dark.
- Consumes: `GET /api/auth/me`, `/api/overview` (bot name/avatar, counts), `/api/todo` (badge count).

- [ ] **Step 1: Write the failing test** – loads `dashboard-v2.html?demo`: rail shows exactly 5 places with these labels; `#bugs` deep link opens Bugs;
  history Back returns to To do; at 390×844 with `?demo&app` the bar is at the bottom (`getBoundingClientRect().bottom == 844`); no element wider than the viewport;
  "+ New" opens a sheet with *Gift code*, *Announcement*, *Game version*; saving a gift code in demo shows a toast; `?demo=mod` hides *Ideas* and the quick actions; no page errors.
- [ ] **Step 2: Run** `python3 /tmp/test_v2_shell.py` – fails (file missing).
- [ ] **Step 3: Implement** the shell. Visual system (Claude's call, per spec "Look"): light "snowfield" (`--bg #f4f6f9`, text `#111827`) and dark "polar night" (`--bg #0b1018`, text `#e6ebf2`),
  one ember accent (`#e8590c` light / `#ff7a2f` dark) used only for the main action, ice blue `#0e7490` / `#67d4e8` for links and focus; 8 px spacing scale; radius 10 px controls / 14 px panes;
  lists with dividers instead of nested cards. Quick actions use `POST /api/giftcodes {code, reward, hours}`, `POST /api/announce` (same body as v1 `qOpen` ann form), `PUT /api/settings {game_version}`.
  Login: when `/api/auth/me` is 401, show one "Log in with Discord" button → `/api/auth/login?next=/staff/v2/`.
- [ ] **Step 4: Run** the test – PASS. Run `node /tmp/jscheck.js dashboard-v2.html` – "syntax ok".
- [ ] **Step 5: Commit** `feat(v2): shell, theme, navigation, quick actions`

### Task 3: To do screen

**Files:**
- Modify: `dashboard-v2.html`
- Test: `/tmp/test_v2_todo.py`

**Interfaces:**
- Consumes: `GET /api/todo`; actions (exact bodies as the current dashboard sends them):
  bugs `PUT /api/bugs/{id}` with `{status}` / `{status, reason}` / `{duplicate_of, reason}` / `{dup_dismiss: true}`, `POST /api/bugs/{id}/ticket` (ask the player);
  ideas `PUT /api/suggestions/{id}` `{status, reason?}`; reports `POST /api/reports/{id}` `{action: delete|warn|timeout|dismiss, reason?}`;
  appeals `POST /api/appeals/{id}` `{accept, note?}`; spikes `POST /api/spikes` `{known: id}` / `{dismiss: key}`; raid `POST /api/raid` `{active:false}`, `POST /api/lockdown` `{active:true}`;
  codes `PUT /api/giftcodes/{id}` `{action:'end'}`.
- Produces: `todoItem(item) -> Raw`, `ACTIONS[kind] -> [{label, icon, run(item), tone, confirm?, needsReason?}]`, `S.doneToday: [{kind,id,label,ts}]`.

- [ ] **Step 1: Write the failing test** – demo To do shows the groups *Needs you now*, *Waiting*, *Done today* (collapsed); chips *All · Bugs · Ideas · Moderation · Mine* filter the list;
  "Pass to admins" on a new bug removes it and adds it to *Done today*; "Reject…" asks for a reason before sending; a mocked 400 on an action shows the error and reloads the list;
  with every mock list empty the screen says "All done" and *Done today* is still there; a 1,800-character Arabic item wraps at 390 px (no sideways scroll); `?demo=mod` shows no idea items and no 403.
- [ ] **Step 2: Run** – FAIL.
- [ ] **Step 3: Implement.** Refresh `/api/todo` every 20 s and on focus (skip while a reason box is open); the badge on *To do* = total count.
- [ ] **Step 4: Run** – PASS.
- [ ] **Step 5: Commit** `feat(v2): To do list with inline actions`

### Task 4: Detail pane (bug, idea, report, appeal) with chat

**Files:**
- Modify: `dashboard-v2.html`
- Test: `/tmp/test_v2_pane.py`

**Interfaces:**
- Consumes: `GET /api/bugs/{id}`, `GET /api/bugs/{id}/similar`, `POST /api/bugs/{id}/translate`, `POST /api/bugs/{id}/reply {text}`,
  `GET|POST /api/bugs/{id}/ticket`, `POST /api/bugs/{id}/ticket/send` (multipart `text`, `files`), `POST /api/bugs/{id}/ticket/close {note}`,
  `GET /api/bugs/{id}/files/{fid}`; `GET /api/suggestions` (find by id), `PUT /api/suggestions/{id} {changes}`, `GET|POST /api/suggestions/{id}/chat`, `POST /api/suggestions/{id}/translate`;
  `GET /api/reports`, `GET /api/appeals`; `GET /api/replies` (ready-made replies in the chat box).
- Produces: `openPane(kind: 'bug'|'idea'|'report'|'appeal', id)`, `closePane()`; keyboard J/K/Enter/Esc on To do, Bugs and Ideas.

- [ ] **Step 1: Write the failing test** – clicking a To do bug opens the pane on the right at 1280 px (list still visible) and full screen at 390 px;
  URL becomes `#bug-64`; Back closes it and the URL returns to `#todo`; the pane shows report, status actions, chat (demo messages) and similar bugs;
  sending a chat message appends it; J/K moves the selection and Enter opens it; Esc closes; idea pane shows its chat for website ideas.
- [ ] **Step 2: Run** – FAIL.
- [ ] **Step 3: Implement.** Poll the open chat every 6 s (as v1); stop polling when the pane closes.
- [ ] **Step 4: Run** – PASS.
- [ ] **Step 5: Commit** `feat(v2): detail pane with actions and chat`

### Task 5: Bugs and Ideas lists

**Files:**
- Modify: `dashboard-v2.html`
- Test: `/tmp/test_v2_lists.py`

**Interfaces:**
- Consumes: `GET /api/bugs?status&q&area&severity&version&mine&dups&limit&offset` (status `closed` = both rejections), `GET /api/suggestions?status&q`,
  `GET /api/export/{bugs.csv|suggestions.csv}`; `openPane` from Task 4.

- [ ] **Step 1: Write the failing test** – Bugs: status chips (New, Info ticket, With admins, Investigating, Fixed, Rejected) filter; search "#61" finds bug 61; "Load more" appends;
  row click opens the pane; More → Export bugs (CSV) downloads. Ideas: Open / Accepted / Rejected filter, website ideas marked, unread badge; `?demo=mod` has no Ideas.
- [ ] **Step 2: Run** – FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** – PASS.
- [ ] **Step 5: Commit** `feat(v2): bugs and ideas lists`

### Task 6: Search, Community and More (links to the current dashboard)

**Files:**
- Modify: `dashboard-v2.html`
- Test: `/tmp/test_v2_search.py`

**Interfaces:**
- Consumes: `GET /api/players/find?q`, `GET /api/bugs?q`, `GET /api/suggestions?q`.
- Produces: `openSearch()` (Ctrl/⌘ K and the search icon).

- [ ] **Step 1: Write the failing test** – Ctrl K opens search; "#64" offers bug 64 and Enter opens its pane; "gift" offers *Gift codes* (→ `/staff/#codes`);
  a player name offers the player (→ `/staff/#players`); Community shows cards for Gift codes, Posts, Members, Moderation lists; More shows Stats and Settings cards;
  every card links to `/staff/#<tab>` and opens in the same tab; moderators only see cards for pages they can open.
- [ ] **Step 2: Run** – FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** – PASS; then the full `/tmp/test_*.py` loop and `python3 /tmp/menu/allpages.py` (v1 unchanged) – all pass.
- [ ] **Step 5: Commit** `feat(v2): search, Community and More`; push `main` (not `live`).
