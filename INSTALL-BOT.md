# WOS EU1 – Bot install guide (free cloud server)

This guide puts the bot on a **free server in the cloud** that is on 24/7. You only copy, paste and answer
a few questions – an installer script does the technical work. Take it one step at a time and tick each step when it's done.

**Time:** about 45 minutes. **Cost:** free. **Best on:** a PC or laptop (a phone works, but copying is easier on a PC).

> When the bot runs, continue with the **website guide** (`INSTALL-WEBSITE.html`) to put the report pages and the staff dashboard on your website.

## Before you start

You need:
- the file **`bugbot.zip`** (on the device you use for this guide),
- a **Discord server** where you are the owner or an admin,
- a **Google account** and a **payment card**. Google only uses the card to check that you are a real person. The server in this guide is in Google's *Always Free* tier, so you are not charged as long as you follow the settings exactly (step 5). Step 4 adds an alarm that emails you if anything ever costs money.

> 💡 **Why Google Cloud?** It's the only big cloud with a server that is free forever *and* is never switched off for being "too quiet" (a bot uses very little power). It also gives you a terminal in the browser, so you don't need any extra programs. The server is in the USA – that is fine for a Discord bot (Discord's own servers are mostly in the USA too). Want the bot in Europe instead? See *Alternative: Oracle Cloud* at the end.

## Step 1 – Create the bot in Discord

1. Open <https://discord.com/developers/applications> and log in.
2. Click **New Application**, name it **WOS EU1**, accept the terms, click **Create**.
3. In the left menu click **Bot**.
4. Scroll down to **Privileged Gateway Intents** and switch **ON** both:
   - **Server Members Intent**
   - **Message Content Intent**

   Click **Save Changes**.
5. Scroll up and click **Reset Token** → **Yes, do it!** → **Copy**. Paste the token somewhere safe for a moment (for example a note on your PC). It is the bot's password – never share it or post it in Discord.
6. In the left menu click **General Information** and copy the **Application ID** (a long number). You need it in step 2.

> ✅ You have: a **token** (long text with dots) and an **Application ID** (a long number).

## Step 2 – Invite the bot to your server

1. Copy this link into your browser and replace `APPLICATION_ID` with the number from step 1:

   ```
   https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot+applications.commands&permissions=360777509952
   ```
2. Choose your server → **Continue** → **Authorize**.
3. In Discord: **Server Settings → Roles**. Drag the **WOS EU1** role **above** your Moderator and Admin roles.

> ✅ The bot is listed in your server's member list (still offline – that's normal).

## Step 3 – Prepare roles and channels in Discord

Make two roles if you don't have them: **Moderator** and **Admin**, and give them to the right people.
Then create these channels (the names are up to you):

| Channel | Who can see it | Notes |
|---|---|---|
| `#report-a-bug` | everyone | Players need *Add Reactions*, *Read Message History*, *Send Messages in Threads*. **Deny** *Send Messages* and *Create Threads*. |
| `#mod-bugs` | Moderators only | Mods need *Add Reactions*, *Read Message History*, *Send Messages in Threads*. |
| `#admin-bugs` | Admins only | |
| `#suggestions` | everyone (read-only) | Accepted suggestions are shown here. |
| `#welcome`, `#patch-notes`, `#top-bug-hunters` | everyone (read-only) | Optional. |
| `#weekly-digest`, `#bot-backups` | **Admins only** | Optional. Backups contain player data – keep them private. |

In every channel, also give the **WOS EU1** role: *View Channel, Send Messages, Send Messages in Threads, Create Private Threads, Manage Threads, Embed Links, Attach Files, Read Message History, Add Reactions, Manage Messages*.

> ✅ Roles and channels exist. You choose which channel is which later, on the dashboard – no IDs needed.

## Step 4 – Create your free Google Cloud account

1. Open <https://cloud.google.com/free> → **Get started for free** and log in with your Google account.
2. Choose your country, accept the terms, choose **Individual** as account type, and enter your card.
   (New accounts also get a free trial credit. You don't need it – the server we make is free after the trial too.)
3. When the console opens, a project called **My First Project** exists. That's fine – use it.
4. **Set a cost alarm** (recommended): in the search bar at the top type **Budgets** → **Budgets & alerts** → **Create budget** → name `alarm`, amount **1** (in your currency) → **Finish**. Google now emails you if anything ever starts costing money.

> ✅ You see the Google Cloud console with *My First Project* selected at the top.

## Step 5 – Create the free server

1. In the search bar type **VM instances** and open it. If asked, click **Enable** (Compute Engine API) and wait a minute.
2. Click **Create instance** and set **exactly** this (anything else may cost money):

   | Setting | Choose |
   |---|---|
   | Name | `wos-bot` |
   | Region | **us-central1 (Iowa)** – or us-west1 / us-east1. *Only these three are free.* |
   | Zone | any |
   | Machine configuration | Series **E2** → Machine type **e2-micro** |
   | OS and storage → **Change** | Operating system **Ubuntu**, version **Ubuntu 24.04 LTS** (x86/64), Boot disk type **Standard persistent disk**, Size **30** GB → **Select** |
   | Networking / Firewall | leave everything **off** (the bot needs no open web ports) |

3. Click **Create** and wait until a green tick appears next to `wos-bot`.
4. Write down the **External IP** shown in the list (for example `34.123.45.67`). The website guide needs it.

> ⚠️ The price box on the right may show about $7/month. That is the normal price *before* the free tier – the free e2-micro is subtracted on the bill. As long as you chose **e2-micro**, one of the **three US regions** and a **Standard** disk of max **30 GB**, it is free.

> ✅ `wos-bot` has a green tick and an External IP.

## Step 6 – Open the server's terminal

1. In the VM list, click **SSH** in the `wos-bot` row. A new browser window opens.
2. Click **Authorize** if asked. After a few seconds you see a black window ending with something like `you@wos-bot:~$`.

This is the server's terminal. You type (or paste) commands here and press **Enter**.
To paste: **Ctrl+V** (Windows), **Cmd+V** (Mac), or right-click → Paste.

> ✅ You see a prompt ending with `$`.

## Step 7 – Upload the bot

1. In the SSH window click **UPLOAD FILE** (at the top, or under the ⚙ / ⋮ menu) and choose **`bugbot.zip`**. Wait for "Transferred 1 file".
2. Paste this command and press Enter – it unpacks the zip:

   ```bash
   python3 -m zipfile -e bugbot.zip . && ls bugbot
   ```

> ✅ You see a list of files including `bot.py`, `dashboard.html` and `deploy`.

## Step 8 – Run the installer

Paste this and press Enter:

```bash
sudo bash bugbot/deploy/install.sh
```

The installer works through 6 steps and asks you only these things:

1. **Bot token** – paste the token from step 1 and press Enter. *Nothing appears on the screen while you paste – that is normal.*
   The installer checks it with Discord straight away and tells you if it is wrong.
2. If you forgot the two switches from step 1.4, it tells you which one – switch it on, save, press Enter.
3. **Which server** – only asked if the bot is in more than one.
4. **Moderator role** and **Admin role** – type the number in front of the right role.

Then it starts the bot and shows **your dashboard key**: a long random text. **Copy it and keep it safe** –
you log in to the dashboard with it (website guide). You can always show it again with:

```bash
sudo grep DASHBOARD_API_KEY /opt/wosbot/.env
```

> ✅ The installer ends with **"The bot is installed and running."** and in Discord the bot is now **online** (green dot).

## Step 9 – Test it in Discord

In any channel type `/members` and press Enter. The bot answers with your member count.

> ✅ The bot answers. The bot part is done! It restarts by itself after a crash or when Google restarts the server.
> You can close the SSH window – the bot keeps running.

**Next:** open **`INSTALL-WEBSITE.html`** and connect your website. Keep the **External IP** (step 5) and the **dashboard key** (step 8) at hand.

## Updates without uploading (one-time setup)

After this one-time setup you never upload a zip again. New versions come from your private GitHub repository
(`kschmidtjohansen/wos-eu1-bot`), and **nothing changes until you say so**.

1. In the SSH window run:
   ```bash
   sudo bash bugbot/deploy/setup-updates.sh
   ```
2. It shows a key and a link. Open the link (GitHub → *Deploy keys → Add deploy key*), title **wos-bot**, paste the key,
   leave **Allow write access** unticked, click **Add key**, and press Enter in the SSH window.
   (Or paste the key to Claude in the chat – Claude can add it for you.)

From then on:
- Claude saves changes to GitHub, but your server keeps running the current version.
- When you write **Update bugbot** to Claude, the new version is released and the server installs it **within 5 minutes**.
- To install a released version right away, type in the SSH window: `Update bugbot`
- If a new version doesn't start, the server **puts the previous version back by itself**. Ask Claude to fix it.
- See what happened: `cat /var/log/wos-update.log` (the dashboard's Activity log also shows "Bot started – version …").

## Backups to Google Drive (optional, about 10 minutes)

The bot already makes a backup every day on the server (and in `#bot-backups` if you chose that channel). This adds a copy in
**your own Google Drive** once a week, so nothing is lost even if the server is. The bot only gets access to the one folder it creates.

1. Open <https://console.cloud.google.com> – the same project as your server (*My First Project*).
2. In the search bar type **Google Drive API** → open it → **Enable**.
3. Search for **Google Auth Platform** → **Get started**:
   App name `WOS EU1 backups`, your e-mail as support e-mail → **Next** → Audience **External** → **Next** →
   your e-mail as contact → **Next** → tick the agreement → **Create**.
4. In the left menu click **Audience** → **Publish app** → **Confirm**.
   *(Without this, Google logs the bot out again after 7 days.)*
5. In the left menu click **Clients** → **Create client** → Application type **TVs and Limited Input devices**, name `wos-bot` → **Create**.
   Copy the **Client ID** and the **Client secret**.
6. In the server's SSH window run:
   ```bash
   sudo bash /opt/wosbot/deploy/setup-drive.sh
   ```
   Paste the Client ID and secret. It shows a short code: open <https://www.google.com/device> on your phone or PC, type the code,
   choose your Google account and allow access. If Google warns that the app isn't verified, click **Advanced → Go to WOS EU1 backups** –
   it is your own app.
7. On the dashboard: **Data → Copy to Google Drive now**. A folder **WOS EU1 backups** appears in your Google Drive.

How often and how many copies are kept: dashboard → **Features → Screenshots & backups** (default: weekly, newest 8).
To restore: download a file from the folder, unzip it (`.gz`), and follow *Restore a backup* below.

## Everyday tasks

Open the SSH window (step 6) first.

| Task | Command |
|---|---|
| Is the bot running? | `sudo bash /opt/wosbot/deploy/install.sh status` |
| Watch the live log | `sudo journalctl -u wosbot -f` (Ctrl+C to stop watching) |
| Restart the bot | `sudo systemctl restart wosbot` |
| Change the token or roles | `sudo bash bugbot/deploy/install.sh` → answer **n** to "Keep them?" |
| **Update to a new version** | Write **Update bugbot** to Claude, or type `Update bugbot` in the SSH window (after the one-time setup above). Your database, files and settings are kept. |
| Make a backup | Dashboard → **Data** → **Back up now** (and download it). The bot also makes one every day. |
| Restore a backup | `sudo systemctl stop wosbot`, copy a file from `/opt/wosbot/data/backups/` (or from Google Drive, unzipped) over `/opt/wosbot/bugs.db`, then `sudo systemctl start wosbot` |
| Is everything OK? | Dashboard → **Server status** (bot, CPU, memory, disk, website link, updates, problems) |

## Troubleshooting

| Problem | What to do |
|---|---|
| "Discord didn't accept that token" | Developer Portal → Bot → **Reset Token** → copy the new one and paste it again. |
| "The bot isn't in any server yet" | Do step 2 (the installer also shows the right invite link), then press Enter. |
| "I couldn't read the server's roles" | Create the Moderator and Admin roles (step 3) and run the installer again. |
| Bot is offline after a while | `sudo bash /opt/wosbot/deploy/install.sh status` shows the last log lines. Send them to whoever helps you. |
| Slash commands don't show | Wait a minute and press Ctrl+R in Discord. Still missing? Invite again with the link from step 2 (it includes `applications.commands`). |
| The bot can't open tickets | It is missing permissions in `#report-a-bug` – the dashboard's **Overview** checklist says exactly which. |
| The External IP changed | Only happens if you **Stop** the server in Google Cloud (restarts keep it). Run the website guide's step 4 again with the new IP. |
| Google sent a budget email | Check *Billing → Reports*. Usually a wrong region, machine type or disk type (step 5). |

## Alternative: Oracle Cloud (server in Europe)

Oracle's *Always Free* tier also gives you a free server, and you can pick a European location (Frankfurt, Amsterdam, Stockholm …).
Two things to know:
- Oracle may **reclaim "idle" free servers** (very low use for 7 days – a small bot can count as idle).
  You avoid that by upgrading the account to **Pay As You Go**. Always-free servers stay free after the upgrade, but set a budget alarm, as in step 4.
- Free servers are sometimes "out of capacity" – just try again later or in another availability domain.

Only steps 4–7 change; the installer (step 8) is the same:
1. Sign up at <https://www.oracle.com/cloud/free/> and choose a **home region in Europe** (you can't change it later).
2. **Compute → Instances → Create instance**: image **Canonical Ubuntu 24.04**, shape **VM.Standard.E2.1.Micro** (Always Free) or **Ampere A1** with 1 OCPU / 6 GB.
   Under *Add SSH keys* choose **Generate a key pair** and **download both keys**. Create.
3. Open **Cloud Shell** (the `>_` icon at the top), upload your private key and `bugbot.zip` with the ⚙ menu → **Upload**, then:
   ```bash
   chmod 600 ssh-key-*.key
   scp -i ssh-key-*.key bugbot.zip ubuntu@PUBLIC_IP:~
   ssh -i ssh-key-*.key ubuntu@PUBLIC_IP
   ```
4. Continue with **step 7.2** (unzip) and **step 8** (installer). Use the instance's **Public IP** wherever this guide says External IP.

## Alternative: run the bot on your website server

If your website runs on your own Linux server (VPS), you can also install the bot there: do steps 1–3, upload the zip
to that server, and run step 8 there. In the website guide, type `127.0.0.1` as the bot server address.
