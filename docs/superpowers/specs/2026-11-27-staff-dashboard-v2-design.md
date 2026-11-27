# Staff dashboard v2 – design

## Why

The current staff dashboard grew one feature at a time: 9 sections, 30 pages, and daily work sits next to
settings that are touched once a year. Admins find it crowded and hard to find their way around, especially on
a phone. The team uses most of the daily functions every day, so they have to be quick to reach.

v2 is built around the work instead of around the features. Opening it answers one question first:
**what needs a person right now?**

## Goals

- One **To do** list with everything waiting for a person, from every feature, each with its actions at hand.
- Every daily job is at most two taps from opening the app, on a phone and on a computer.
- Settings live in one place and stay out of the way of daily work.
- It works well on a phone (installed as an app) and on a computer, from the same page.
- Nothing the current dashboard can do is lost.

## Non-goals

- No new bot features. v2 is a new way to use what exists (small API additions only where the new screens need them).
- No change to the players' website.
- No change to who can see what: moderators keep the moderator view, admins see everything.

## Anti-goals (a failure even if it works)

- A prettier copy of the same 30 pages.
- Staff having to learn two dashboards for long: the old one goes away as soon as v2 covers everything.
- Losing data or breaking the current dashboard while v2 is being built.

## Approaches considered

1. **New look, same structure** – restyle every page. Least work, nothing moves, but the crowding (structure)
   stays. Not chosen.
2. **Rebuild around the daily work** – To do list, detail pane, quick actions, one Settings area. **Chosen by Kasper.**
   Built next to the current dashboard so it can be tried before it replaces it.

## How it is organised

### Navigation (5 places)

| Place | What's there |
|---|---|
| **To do** | Everything waiting for a person (see below). The start screen. Count badge. |
| **Bugs** | All bugs: search, filters, status. Also the developers' list. |
| **Ideas** | All suggestions, including website ideas and their chats. |
| **Community** | Gift codes (incl. auto-redeem), posts (announcements, patch notes, timed messages, giveaways, info channel, promoter servers), members (players, warnings, invites, welcome, access, role menus), moderation lists (deleted messages, reports & appeals history). Shown as a grid of cards, each opening its own screen. |
| **More** | Stats (insights, moderators, status, activity log) and **Settings** (all settings grouped by feature, with a search box). |

Desktop: a narrow left rail with these 5. Phone: the same 5 in a bottom bar (installed app) or top bar (browser).
Quick actions everywhere (header button "+ New"): gift code, announcement, game version.
Search (Ctrl K / search icon) finds pages, settings, bugs (#64), ideas and players.

### To do – what appears there

Each item: what it is, who/when, one line of content, and its main actions as buttons.

| Item | Shown to | Main actions |
|---|---|---|
| New bug report | mods + admins | Pass to admins · Ask the player · Reject… · Open |
| Possible duplicate | mods + admins | Merge into #x · Not a duplicate |
| Bug passed to admins | admins | Being investigated · Fixed · Reject… |
| Player answered (ticket, website chat, developer question) | mods + admins | Reply · Open |
| Info ticket with no answer for a while | mods + admins | Remind · Close |
| New idea | admins | Accept · Reject… |
| Idea chat: player wrote | admins | Reply |
| Reported chat message | mods + admins | Delete · Warn · Timeout · Nothing wrong |
| Appeal | mods (timeouts), admins (bans) | Accept · Decline |
| Report spike ("6 reports about Alliance") | mods + admins | Post as known issue · Dismiss |
| Raid mode on | mods + admins | Lift · Lock chat |
| Gift code ending soon / expired | admins | Extend · End |
| Setup problem (missing permission, channel gone) | admins | Fix (opens the setting) |
| Background job failing | admins | Open status |

Grouped by urgency: **Needs you now** (critical bugs, raid, spike, failing job), then **Waiting** (oldest first),
then **Done today** (collapsed, so staff see their progress). Filter chips: All · Bugs · Ideas · Moderation · Mine.

### The detail pane

Selecting an item (or a bug/idea anywhere) opens it in a pane on the right (desktop) or full screen (phone, Back
closes it). It holds everything the current bug window holds: the report, translate, status actions, the chat with
the player (Discord ticket or website), similar bugs, the player's record, history. Same for ideas, reports and appeals.
Keyboard on desktop: J/K next/previous, Enter open, Esc close.

### Settings

One screen with feature groups on the left (Bug reports, Ideas, Website, Gift codes, Posts, Members & access,
Moderation & safety, Translations, Bot profile, Data & backups, Channels & permissions) and a search box.
Folded one-time setup, settings that hide until their switch is on, and one save bar – as in the current clean-up.

## Look (Claude's call – no house style exists)

- A new, calmer visual system: light and dark theme following the device, one accent for the single main action
  per screen, status colours always paired with a word or icon.
- System font stack (fast, no downloads), tabular numbers for counts and times, 16 px minimum in inputs on phones.
- One icon set (the current SVG line icons), no emoji as icons.
- Generous spacing, fewer borders and cards inside cards; lists rather than boxes.

## Constraints

- Same single-file setup as today (one HTML file, no build step), served at **/staff/v2/** next to the current
  dashboard at /staff/. Same login, same API, same demo mode (`?demo`, `?demo=mod`) for screenshots and tests.
- Moderator restrictions (MOD_READ / MOD_WRITE) apply unchanged.
- The deploy script copies both files; the phone app (manifest) keeps pointing at /staff/ until v2 replaces it.

## Rollout

1. **Phase 1 – daily work:** shell, To do, detail pane (bugs, ideas, reports, appeals, chats), Bugs, Ideas,
   quick actions, search. Settings and Community link to the current dashboard for now.
2. **Phase 2 – Community:** gift codes, posts, members, moderation lists rebuilt in the new style.
3. **Phase 3 – Settings and Stats**, then v2 moves to /staff/ and the old dashboard is removed.

Each phase is tested (unit tests for any API addition, browser tests in demo mode on phone and desktop sizes) and
goes live only on "Update bugbot".

## Left to the builder

- API: one new read-only endpoint `GET /api/todo` that gathers the To do items (counts and the first items of each
  kind) so the start screen needs one request; everything else uses the existing endpoints.
- Exact grouping thresholds (e.g. when an info ticket counts as "no answer for a while" – reuse the existing reminder settings).
- Visual details within the rules above.
