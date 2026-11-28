# Staff dashboard v2 – phase 2 (Community) Implementation Plan

> **For agentic workers:** Implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Community place of `dashboard-v2.html` natively: gift codes, posts, members and moderation lists,
so daily community work no longer opens the current dashboard.

**Architecture:** Same single file and patterns as phase 1 (`html` templates, `call()`, demo mocks in `demoApi`, the
detail pane). Community gets four tabs; a new pane kind `player`. Only existing API endpoints are used.

**Tech Stack:** vanilla JS in `dashboard-v2.html`; Playwright tests in `/tmp/test_v2_*_ui.py` (demo mode).

**Spec:** `docs/superpowers/specs/2026-11-27-staff-dashboard-v2-design.md` (Community row, rollout phase 2)

## Global Constraints

- All phase 1 constraints (look, phone sizes, moderator limits, demo mode, nothing live until "Update bugbot").
- Community tabs: **Gift codes · Posts · Members · Moderation**; URL `#community/<tab>`; the Community place opens the last tab used.
- Pages that are set up once (Welcome, Access & languages, Role menus, Info channel, auto-redeem connection) move to
  Settings in phase 3 – until then a "Set up" line at the bottom of the tab links to the current dashboard. (Claude's call, stated to Kasper.)
- Moderators see: Members (Players, Warnings) and Moderation; not Gift codes, Posts or Invites (same as today).

## Review Focus

- **Empty lists** (no giveaways, no timed messages, no promoters) → a short line saying what the section is for and its add button. (each tab test)
- **Destructive actions** (delete code, remove promoter server, ban) → asked to confirm with the name/number shown. (Task 1, 2, 3 tests)
- **Timed message in the past / wrong time** → the bot's error shown at the field, the form keeps what was typed. (Task 2 test via `?fail=/api/timers`)
- **Player not on the server any more** → profile still opens, member-only actions hidden. (Task 3 test)
- **Phone** → every tab readable at 390 px, sheets as bottom sheets, nothing sideways. (each tab test at 390 px)

---

### Task 1: Community tabs and Gift codes
**Files:** Modify `dashboard-v2.html`; Test `/tmp/test_v2_codes_ui.py`
**Interfaces:** Consumes `GET /api/giftcodes` (`items[{id,code,reward,state:new|active|expired,expires_at,created_at,by,url}]`, `durations[{hours,label}]`),
`PUT /api/giftcodes/{id}` (end), `DELETE /api/giftcodes/{id}`, `POST /api/giftcodes/import {codes, announce}`, `GET /api/redeem`
(`linked, codes[{code,ok,already,failed}], failures[{code,uid,chief,message,ts}], settings.redeem_enabled`), `POST /api/redeem/retry`.
Produces `viewCommunity()` with tabs, `S.comm.tab`.
- [ ] Test: tabs shown (admin 4, mod 2); codes grouped Active/Expired; End and Delete ask to confirm; Import adds codes; auto-redeem card shows results and Retry; phone 390 px no sideways scroll.
- [ ] Run – fail. Implement. Run – pass. Commit `feat(v2): community tabs and gift codes`.

### Task 2: Posts – patch notes, timed messages, giveaways, promoter servers
**Files:** Modify `dashboard-v2.html`; Test `/tmp/test_v2_posts_ui.py`
**Interfaces:** `GET /api/patchnotes` (`unreleased[bug], history[{id,ts,title,by,count}], default_title`), `POST /api/patchnotes/publish {title}`;
`GET /api/timers` (`items[{id,name,channel_id,content,kind:once|daily|weekly|interval,at,days[],every_minutes,role_id,delete_previous,enabled,next_run,last_run}], timezone`),
`POST /api/timers` (whole timer, with `id` to edit), `DELETE /api/timers/{id}`, `POST /api/timers/{id}/send`;
`GET /api/giveaways` (`items[{id,prize,winners,ends_at,ended,channel_id,role_id,host,entries,winner_names,url}]`), `POST /api/giveaways {prize,channel_id,minutes,winners,role_id}`, `POST /api/giveaways/{id}/{end|reroll}`;
`GET /api/partners` (`summary{servers,members,posts_30d,attention}, items[partner_info], invites[], left[]`), `POST /api/partners/invite {promoter}`, `DELETE /api/partners/invites/{iid}`,
`PUT /api/partners/{id} {paused}`, `POST /api/partners/{id}/test`, `POST /api/partners/{id}/dm`, `DELETE /api/partners/{id}`; `GET /api/channels`, `GET /api/roles`.
- [ ] Test: patch notes list fixed bugs and publish; timed message add (sheet), pause toggle, send now, delete with confirm; error keeps the form; giveaway start, end, reroll; promoter list with problems first, pause, test, invite link create/revoke, remove asks to confirm; empty states.
- [ ] Run – fail. Implement. Run – pass. Commit `feat(v2): posts`.

### Task 3: Members – players (pane), warnings, invites
**Files:** Modify `dashboard-v2.html`; Test `/tmp/test_v2_members_ui.py`
**Interfaces:** `GET /api/players/find?q`, `GET /api/players/{id}` (profile with `cases, bugs, notes, link, invited_by, on_server, timeout_until, banned`),
`POST /api/players/{id}/notes {text}`, `DELETE /api/players/{id}/notes/{nid}`, `POST /api/players/{id}/action {action, reason, minutes, days}`
(actions: warn, timeout, untimeout, kick, ban, unban); `GET /api/cases?q`, `POST /api/cases/{id}/pardon`, `POST /api/cases/warn {user_id, reason}`; `GET /api/invites`.
Produces pane kind `player` (`#player-<id>`), opened from search, reports, appeals, warnings and invites.
- [ ] Test: search a player opens the pane; notes add/delete; warn asks for a reason; ban asks to confirm; a player who left shows no kick/timeout; warnings list with pardon; invites summary and lists; moderator sees Players and Warnings only.
- [ ] Run – fail. Implement. Run – pass. Commit `feat(v2): members`.

### Task 4: Moderation lists, wiring and verification
**Files:** Modify `dashboard-v2.html`; Test `/tmp/test_v2_moderation_ui.py`
**Interfaces:** `GET /api/reports?status=done`, `GET /api/appeals?status=done`, `GET /api/msglog?kind=deleted|edited&q`, `GET /api/queue` (raid), `POST /api/raid {active}`, `POST /api/lockdown {active, reason}`.
- [ ] Test: Reports & appeals history; deleted/edited messages with search; raid card switch on/lift and lock/unlock; search (Ctrl K) offers the new pages and opens them in v2; To do "Open gift codes" goes to `#community/codes`.
- [ ] Run – fail. Implement. Run – pass; full `/tmp/test_*.py` loop and v1 `allpages.py` pass. Commit and push `main`.
