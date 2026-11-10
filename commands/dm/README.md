# dm.luaisgame.com

A free Discord-style chat platform that shares one process (and one account
database) with `storage.luaisgame.com` and `mc.luaisgame.com`.

```
commands/dm/
  db.py        SQLite schema + tiny query helpers  (accounts.db)
  auth.py      accounts: email+password, Discord OAuth, linking, sessions
  api.py       REST under /api/dm/*
  realtime.py  /ws/dm: chat events, presence, calls, WebRTC signaling, frame relay
  web.py       the SPA shell + /dm-assets/* static files
  static/      app.js + style.css (the whole frontend)
```

## How it fits into the existing app

Everything runs inside the single aiohttp app on port 5000 (`commands/core.py`).
The `Host:` header picks the page, the path picks the handler:

| host            | `/` serves            |
|-----------------|-----------------------|
| `storage.*`     | the storage site      |
| `mc.*`          | the MC console        |
| `dm.*`          | the chat SPA          |

DM routes are all namespaced (`/api/dm/*`, `/ws/dm`, `/dm-assets/*`,
`/invite/{code}`, `/s/{slug}`), plus `handle_fallback` serves the SPA for deep
links like `/settings/profile` on the `dm.*` host only.

## One account, three sites

* `accounts.db` (SQLite, WAL) holds users, sessions, OAuth states, servers,
  channels, roles, messages, badges, vanity requests.
* The session cookie is issued for **`Domain=.luaisgame.com`** so a login on any
  of the three subdomains authenticates all three.
* Sessions and OAuth states are rows, not process memory → **a restart logs
  nobody out** and half-finished Discord logins still complete.
* Passwords: PBKDF2-HMAC-SHA256, 300k iterations, per-user salt.
* A user is either email+password, Discord, or both (linked). `id` in the API
  stays the Discord snowflake (or `"0"`) so the pre-existing storage/MC
  permission files (`mc_permissions.json`) keep working; `uid` is the internal id.

### Owner

`is_owner` is set automatically for `BOT_OWNER_ID`'s Discord account (env var,
default `1293889121374179328`). The owner holds a seeded **Founder** badge and is
the only account that can create badges, grant/revoke them, and **approve vanity
links** (everyone else can only file a request).

## Screenshare

1. **WebRTC P2P first** — signaling goes over `/ws/dm` (`signal` messages),
   media goes straight between peers (~tens of ms). STUN only, no TURN.
2. If no ICE connection within 6 s, the viewer flips the call to `relay` mode:
   the broadcaster JPEG-encodes a canvas ~30 fps and pushes binary frames
   (`0x01 | uint16 header len | JSON header | JPEG`) over the same WebSocket.
   The server fans them out immediately, dropping frames for any socket whose
   write buffer is already >512 KB so one slow viewer can't stall the host.
3. The call HUD shows WS round-trip time and `currentRoundTripTime` from
   `pc.getStats()` when available.

Room ids: `c<channel_id>` for server voice channels, `d<channel_id>` for DM calls.

## Uploads

10 GB per file, 50 GB per account, resumable:

`POST /api/dm/uploads` → `PUT /api/dm/uploads/{id}?offset=` (16 MB chunks) →
`POST /api/dm/uploads/{id}/complete`. Partial files live in `dm_uploads/*.part`
and are swept at boot (the chunk endpoint answers 410 so the client restarts).
`aiohttp` runs with `client_max_size=64 MB`.

## Deploy checklist

1. **Discord developer portal → OAuth2 → Redirects**, add all three:
   ```
   https://storage.luaisgame.com/api/auth/callback
   https://mc.luaisgame.com/api/auth/callback
   https://dm.luaisgame.com/api/auth/callback
   ```
   (the first two already exist; only `dm` is new). Overridable via
   `DISCORD_REDIRECT_URI_DM` in `.env`.
2. Tunnel DNS is handled automatically — `ensure_tunnel()` in `bot.py` / `prod/run.py`
   now routes `dm.luaisgame.com` too and rewrites the cloudflared ingress config
   on startup (the tunnel restarts itself when the config changes).
3. Optional env: `BOT_OWNER_ID`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`.

## Files created at runtime (all gitignored)

| file | purpose |
|------|---------|
| `accounts.db` | accounts, sessions, all chat data |
| `dm_uploads/` | attachment bytes |
| `dm_media/` | avatars, server/role icons |
| `join_urls.json` | lets the decompile **Copy Join URL** button work after a restart |

## Tests

* `python _dm_test.py` — 48 checks against the DM routes only (accounts, servers,
  roles, messages, chunked upload round-trip, DMs, badges, vanity, persistence
  across a simulated restart, WebSocket fan-out, frame relay, HTML shell).
* `python _smoke_test.py` — boots the **real** `commands.core` server and checks
  host routing, the production cookie shape, one session working on all three
  hosts, OAuth redirect URIs per host, and deep links.

## Not built (deferred by request)

* Native desktop/mobile app (later).
* Nitro/boosts — intentionally absent; the platform is entirely free.
* Support ticket command — dropped by request (the existing ticket claim buttons
  were already re-registered on startup).
