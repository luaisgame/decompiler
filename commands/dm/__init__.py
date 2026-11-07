"""dm.luaisgame.com — the free Discord-style chat platform.

Package layout:
    db.py        SQLite persistence (survives restarts)
    auth.py      shared accounts: email+password, Discord OAuth, linking, sessions
    api.py       REST endpoints (servers, channels, roles, messages, DMs, badges)
    realtime.py  /ws/dm hub: chat events, presence, calls, WebRTC signaling
    web.py       the SPA shell + static assets
"""
