# Auto-redeem on a WOS private server

Auto-redeem lets the Discord bot redeem every active gift code for every player
who linked their game account with `/link` (or the 🔗 button). New codes are
redeemed at once, and new players get all active codes as soon as they link.

The bot can't hand out rewards by itself. It asks **your game server** to redeem
a code for a player, exactly as if the player had typed it on the in-game
"Redeem code" screen. So the game server needs one small web address for this.

## 1. The code must exist on the game server

Add each code to the game server's own gift-code table first (same code, same
rewards, same end date). Then add it on the staff page → Gift codes. The bot
only triggers the redeem.

## 2. The web address the bot calls

```
POST https://<game-server>/api/redeem
Authorization: Bearer <long random secret>
Content-Type: application/json

{"uid": "123456789", "code": "WOSEU1GIFT"}
```

* `uid` is the in-game player ID (the number the player gave in `/link`).
* The bot calls it once per player and code, with a small pause between calls
  (2 seconds by default).
* Address, method, body and secret header can all be changed on the staff page
  (Gift codes → Auto-redeem). `{uid}` and `{code}` are filled in, also in the
  address itself for servers that want `GET …/redeem?uid={uid}&code={code}`.

## 3. What the game server should do

1. Check the secret header. Answer `401` if it's wrong, so only the bot can use it.
2. Find the player by `uid`. Not found → `404 {"error": "unknown player"}`.
3. Find the gift code. Unknown or expired → `400 {"error": "invalid code"}`.
4. Has this player redeemed it already? → `200 {"ok": false, "error": "already redeemed"}`.
5. Otherwise do the same as the in-game redeem: record that the player used the
   code and send the rewards (normally by in-game mail). Answer `200 {"ok": true}`.

The bot reads the answer like this:

| Answer | Shown on the staff page |
|---|---|
| contains the word `already` (setting "“Already redeemed” when it contains") | Already had it |
| a `2xx` status (and, if you set it, contains your "success" word) | Redeemed |
| anything else | Failed – tried again by itself after 6 hours |

## 4. Example (Python, aiohttp)

Replace the three `game.*` calls with your server's own functions – usually the
same ones the in-game redeem screen uses.

```python
from aiohttp import web
import game  # your game server's code

SECRET = "Bearer change-me-to-a-long-random-key"

async def redeem(request):
    if request.headers.get("Authorization") != SECRET:
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    uid, code = str(data.get("uid", "")), str(data.get("code", "")).strip()
    player = game.find_player(uid)
    if player is None:
        return web.json_response({"error": "unknown player"}, status=404)
    gift = game.find_gift_code(code)
    if gift is None or gift.expired:
        return web.json_response({"error": "invalid code"}, status=400)
    if game.has_redeemed(player, gift):
        return web.json_response({"ok": False, "error": "already redeemed"})
    game.redeem(player, gift)          # marks it used + mails the rewards
    return web.json_response({"ok": True})

app = web.Application()
app.router.add_post("/api/redeem", redeem)
web.run_app(app, port=8090)
```

Keep this address private: only allow the bot's server (IP allow-list or the
secret header above), and use https if it goes over the internet.

## 5. Switch it on

Staff page → Gift codes → Auto-redeem: paste the address and the secret header,
press **Save**, then **Test with one player** using your own UID. When the test
says "Redeemed", switch **Redeem codes automatically** on.
