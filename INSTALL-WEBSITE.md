# WOS EU1 – Website & dashboard install guide

This guide puts three things on your existing website (for example `http://167.233.219.217`):

| Address | What it is | Who uses it |
|---|---|---|
| `/bugs/` | public pages: home, **report form**, known issues, changelog | players |
| `/staff/` | the **dashboard** (settings, bugs, insights …) | you, admins, moderators |
| `/public/…` and `/api/…` | the hidden "phone line" from the website to the bot | the pages themselves |

A script does the work on your website server and asks before every change. If your web server can't accept a change,
it puts everything back exactly as it was.

**Time:** about 30 minutes.

## Before you start

You need:
- the **bot running** – finish `INSTALL-BOT.html` first,
- the bot server's **External IP** (bot guide, step 5) and your **dashboard key** (bot guide, step 8),
- the **bot server's SSH window** (bot guide, step 6) – you paste one line there in step 4,
- a login to your **website server** with admin rights (`root`, or a user that may use `sudo`), and `bugbot.zip`.

> 💡 The script works with **nginx** and **Apache** on Ubuntu/Debian – the most common setups. If your site is on shared hosting
> without a terminal, see *No terminal on your website host?* at the end.

## Step 1 – Open a terminal on your website server

**On Windows:** press Start, type **Terminal** (or *PowerShell*), open it, and type:

```bash
ssh root@167.233.219.217
```

**On Mac:** open the **Terminal** app and type the same.
**On a phone:** install **Termius** (free), add a host with the server's address, user and password, and connect.

Type `yes` if it asks about a fingerprint, then your password (it isn't shown while you type).
If you log in with another user than `root`, that's fine – the script asks for your password when it needs admin rights.

> ✅ You see a prompt such as `root@yourserver:~#`.

## Step 2 – Upload the zip to the website server

Open a **second** terminal **on your PC** (not logged in to the server) in the folder where `bugbot.zip` is
(usually *Downloads*), and send the file:

```bash
cd Downloads
scp bugbot.zip root@167.233.219.217:~
```

> 💡 Prefer clicking? Use **WinSCP** (Windows) or **FileZilla** (any PC): connect with *SFTP* to the same address, user and password,
> and drag `bugbot.zip` into the right-hand window.

Back in the **server** terminal, unpack it:

```bash
python3 -m zipfile -e bugbot.zip . && ls bugbot
```

> ✅ You see `bot.py`, `dashboard.html`, `deploy`, `site` …

## Step 3 – Start the website script

```bash
sudo bash bugbot/deploy/connect-website.sh
```

It first checks which web server you use and says, for example, **"Your website runs on nginx"**.
Then it asks for the **bot server address** – type the **External IP** from the bot guide and press Enter.

> ✅ The script shows a long line that starts with `sudo bash /opt/wosbot/deploy/install.sh add-website`.

## Step 4 – Give the website a key to the bot

The website needs permission to talk to the bot. The script has made a key for it, and the bot server has to accept that key:

1. Select and copy the **whole** line the script shows – from `sudo` to the last `'`.
2. Go to the **bot server's SSH window** (Google Cloud → VM instances → **SSH**), paste the line and press Enter.
   It answers **"Done. The website server may now connect"**.
3. Go back to the website terminal and press **Enter**.

> ✅ The script says **"Link works – the bot answered"**.

The key can only reach the bot – it cannot open a terminal or see any files on the bot server. The link is encrypted and
starts again by itself after a reboot.

## Step 5 – Answer the last questions

The script now copies the pages and asks:

1. **Your Discord invite link** – e.g. `https://discord.gg/abc123` (shown as a "Join Discord" button). Enter = skip.
2. **Link to your Server status page** – Enter = skip.

The staff page has no shared password: everyone logs in with their own Discord account. Admin, Boss and Developer
get full access; Senior Moderator and Moderator get the bug dashboard (role names can be changed under Settings → Bot profile).
Before Discord login is switched on, run `sudo wosbot-login` on the server – it prints a one-time admin link.

Then it adds the bot settings to your web server, tests them and reloads the web server. Your existing pages are not changed.

> ✅ The script ends with **"Finished."** and a list of addresses.

**If it said "Please add the bot settings by hand"** – your site already uses one of the addresses `/api/`, `/public/`, `/bugs/` or `/staff/`,
or it couldn't find your site's settings. Nothing was changed. Open the file it names (`…/wos-bot.conf`), and ask whoever set up your
website to add it to the site:
- **nginx:** add `include /etc/nginx/snippets/wos-bot.conf;` inside your site's `server { … }` block, then `sudo nginx -t && sudo systemctl reload nginx`.
- **Apache:** add `Include /etc/apache2/conf-available/wos-bot.conf` inside your site's `<VirtualHost …>`, then `sudo apache2ctl configtest && sudo systemctl reload apache2`.
- If the clash is `/api/`, the lines must go on a separate (sub)domain instead – or rename your own `/api/`.

## Step 6 – Check it in your browser

Open these (use your own address):

1. `http://167.233.219.217/public/config` → text starting with `{"enabled"` means the website reaches the bot.
2. `http://167.233.219.217/bugs/` → the new public pages.
3. `http://167.233.219.217/staff/` → the staff password (if you chose one), then the dashboard login.
   Paste your **dashboard key** and click **Connect**.

> ✅ The dashboard opens and shows **Overview** with your bot's name and server.

## Step 7 – Switch everything on (on the dashboard)

1. **Channels** → choose the bug report, mod, suggestions and admin channels (plus any optional ones you made) → **Save**.
   **Overview** shows a checklist – fix anything red or yellow (it says exactly what is missing).
2. **Panel message** → write the text players see → **Publish panel**. The message gets two buttons: **Report a bug** and
   **Suggest an idea**. Each opens a short form (only the player sees it) – with an area menu and a screenshot upload for bugs.
3. **Features** →
   - set the **current game version** (e.g. `V5 · 1.33.9`) and check the **Areas** list,
   - turn on **Accept bug reports from the website**,
   - turn on **Public known-issues list** and **Public changelog** when you're ready,
   - set your **time zone** (`Europe/Copenhagen`) for the weekly digest.
4. **Welcome** → write a welcome message (optional).
5. **Gift codes** (optional) → on **Channels** choose a gift-codes channel, on **Features → Gift codes** switch them on and pick who is tagged.
   Then add codes on the **Gift codes** page: each one is announced in Discord at once, shown as **New** at the top of `/bugs/codes.html`
   for 24 hours, and listed there until it expires. Players can also type `/codes` in Discord.
6. **Translations** → players get the bot's messages in their own Discord language (or what they pick with `/language`).
   With a free Google key (bot guide, “Automatic translation”) all 18 languages are filled in automatically, and anyone can
   right-click a message → **Apps → Translate**. Change any text there.
7. **Bot profile → Use server look** gives the bot the server's icon and banner.

## Step 8 – Link the new pages from your site

On your existing site, point the **Report a bug** button to `/bugs/report.html`, and add links to `/bugs/` (home),
`/bugs/known-issues.html` and `/bugs/changelog.html`. The new pages have their own menu (and an English/Polish switch), so a single link to `/bugs/` also works.

> 💡 Want the colours to match your site exactly? They are at the top of `/var/www/wos/bugs/style.css` (`--bg`, `--card`, `--accent` …).

## Step 9 – Go-live test

- [ ] Press **Report a bug** in the report channel → fill in the form (add a screenshot) → **Submit** → the bug appears in `#mod-bugs`.
- [ ] A moderator presses ✅ → it appears in `#admin-bugs`; an admin presses ✅ **Fixed**.
- [ ] Open `/bugs/report.html`, send a test report with a screenshot → it appears in `#mod-bugs` marked "website".
- [ ] On the dashboard open that bug → the screenshot shows. Tick **Show on the public known-issues list** → it appears on `/bugs/known-issues.html` (within a minute).
- [ ] Dashboard → **Data** → **Back up now** works.
- [ ] Reject your test reports, then check **Moderation → Paused website senders** is empty.

All ticked? You're live. 🎉

## HTTPS (the padlock)

If your site opens with `http://` (no padlock), the dashboard key and staff password travel unencrypted. Fix it once you have a
domain name pointing to the server (e.g. `wos-europa.com`; certificates can't be issued for a bare IP address):

```bash
sudo apt install -y certbot python3-certbot-nginx      # Apache: python3-certbot-apache
sudo certbot --nginx -d yourdomain.com                  # Apache: --apache
```

Then run the website script again (step 3) – it notices the padlock and uses `https://` in its checks.

## Everyday tasks

| Task | How |
|---|---|
| Update the pages/dashboard to a new version | If the website is on the bot server, it's updated together with the bot (**Update bugbot** – see the bot guide). Otherwise upload and unpack the new zip (step 2) and run `sudo bash bugbot/deploy/connect-website.sh` again. Your `config.js` and staff password are kept. |
| Bot server got a new IP | Run the script again and type the new address. |
| Is the link up? | `systemctl status wos-tunnel` (should say *active (running)*) |
| Can't log in to the staff page | `sudo wosbot-login` – prints a one-time admin link (works once, for 15 minutes). |
| New dashboard key | On the bot server: edit `DASHBOARD_API_KEY` in `/opt/wosbot/.env` (`sudo nano /opt/wosbot/.env`), then `sudo systemctl restart wosbot`. |

## Troubleshooting

| Problem | What to do |
|---|---|
| "Not yet (Permission denied)" in step 4 | The line wasn't run on the bot server, or was cut off. Copy the whole line again (from `sudo` to the last `'`). |
| "Not yet (timed out / No route)" | Wrong address, or the bot server is stopped. Check the External IP in Google Cloud. |
| `/public/config` shows your normal site or "404" | The web-server lines weren't added – see *"add the bot settings by hand"* in step 5. |
| `/public/config` shows **502 Bad Gateway** | The link or the bot is down: `systemctl status wos-tunnel` here, and `sudo bash /opt/wosbot/deploy/install.sh status` on the bot server. |
| Dashboard: "can't connect" | Check the key (copy it again from the bot server). |
| Report form says "closed" | Dashboard → Features → *Accept bug reports from the website* is off, or no mod channel is chosen. |
| Everyone seems to be "one visitor" (spam pauses hit the wrong people) | Your site is behind Cloudflare: on the bot server set `CLIENT_IP_HEADER=CF-Connecting-IP` in `/opt/wosbot/.env`, and make nginx pass that header. Ask for help with this one. |

## No terminal on your website host?

If your website is on simple web hosting (only a file manager/FTP, no terminal), the website can't open the secure link itself.
Use a free **Cloudflare Tunnel** on the bot server instead:
1. Put your domain on Cloudflare (free). In *Zero Trust → Networks → Tunnels* create a tunnel that sends `bot.yourdomain.com` to `http://localhost:8080`, and run the install command it shows **in the bot server's SSH window**.
2. On the bot server set `CLIENT_IP_HEADER=CF-Connecting-IP` and `DASHBOARD_ORIGINS=https://yourdomain.com` in `/opt/wosbot/.env`, then `sudo systemctl restart wosbot`.
3. In `site/config.js` set `API_BASE: 'https://bot.yourdomain.com'`, in `dashboard.html` set `apiBase: "https://bot.yourdomain.com"`, and upload the `site` folder (as `bugs`) and `dashboard.html` (as `staff/index.html`) with your host's file manager.

## Try the dashboard without anything installed

Open `dashboard.html` from the zip in your browser and add `?demo` to the end of the address. It shows made-up data, so you can look around safely.
