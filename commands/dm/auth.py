"""Shared accounts for luaisgame.com (storage / mc / dm).

One account database backs every subdomain: a user can register with an email +
password, or sign in with Discord, then link the two together. Sessions are rows
in SQLite rather than process memory, so a restart never logs anybody out.

The public user dict returned by :func:`user_from_request` keeps ``id`` equal to
the Discord snowflake (or ``"0"`` when the account has no Discord linked) so the
existing storage/mc permission checks keep working unchanged.
"""

import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from . import db

SESSION_COOKIE = "session_id"
SESSION_TTL = 30 * 86400
PBKDF2_ITERATIONS = 300_000
PASSWORD_MIN = 8
USERNAME_RE = r"^[a-z0-9_.]{2,32}$"

DISCORD_CLIENT_ID = ""
DISCORD_CLIENT_SECRET = ""
REDIRECT_BY_HOST = {}          # host -> registered OAuth redirect uri
SITE_COOKIE_DOMAIN = ".luaisgame.com"
OWNER_DISCORD_ID = ""

_login_attempts = {}           # key -> [count, window_start]


def init(base_dir: str, owner_discord_id, client_id: str, client_secret: str,
         redirects: dict) -> None:
    global DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, OWNER_DISCORD_ID
    DISCORD_CLIENT_ID = client_id
    DISCORD_CLIENT_SECRET = client_secret
    OWNER_DISCORD_ID = str(owner_discord_id or "")
    REDIRECT_BY_HOST.clear()
    for host, uri in redirects.items():
        REDIRECT_BY_HOST[host.lower()] = uri
    db.init(os.path.join(base_dir, "accounts.db"))
    _seed_founder_badge()


def _seed_founder_badge() -> int | None:
    """Make sure the owner badge exists and every owner account holds it."""
    row = db.row("SELECT id FROM badges WHERE slug='founder'")
    if row:
        badge_id = row["id"]
    else:
        badge_id = db.run(
            "INSERT INTO badges(slug,name,description,icon,color,created_at,created_by) "
            "VALUES(?,?,?,?,?,?,0)",
            ("founder", "Founder",
             "Runs the platform. Linked to the owner's Discord account.",
             "👑", "#ffd700", time.time()))
    for u in db.rows("SELECT id FROM users WHERE is_owner=1 OR discord_id=?",
                     (OWNER_DISCORD_ID,)):
        db.run("INSERT OR IGNORE INTO user_badges(user_id,badge_id,granted_at,granted_by) "
               "VALUES(?,?,?,0)", (u["id"], badge_id, time.time()))
    return badge_id


def grant_owner_badge(user_id: int) -> None:
    try:
        badge_id = _seed_founder_badge()
        if badge_id:
            db.run("INSERT OR IGNORE INTO user_badges(user_id,badge_id,granted_at,granted_by) "
                   "VALUES(?,?,?,0)", (int(user_id), badge_id, time.time()))
    except Exception as e:
        print(f"[DM AUTH] owner badge grant failed: {e}")


# --------------------------------------------------------------------------
# password helpers
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                 PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                        int(iters)).hex()
        return secrets.compare_digest(candidate, digest)
    except Exception:
        return False


def _rate_limited(key: str) -> bool:
    now = time.time()
    count, start = _login_attempts.get(key, (0, now))
    if now - start > 300:
        _login_attempts.pop(key, None)
        return False
    return count >= 10


def _bump_attempt(key: str) -> None:
    now = time.time()
    count, start = _login_attempts.get(key, (0, now))
    if now - start > 300:
        count, start = 0, now
    _login_attempts[key] = (count + 1, start)


# --------------------------------------------------------------------------
# sessions (persisted -> survive restarts)
# --------------------------------------------------------------------------

def create_session(user_id: int, ip: str = "", ua: str = "") -> str:
    token = secrets.token_urlsafe(48)
    now = time.time()
    db.run(
        "INSERT OR REPLACE INTO sessions(token,user_id,created_at,expires_at,ip,ua)"
        " VALUES(?,?,?,?,?,?)",
        (token, int(user_id), now, now + SESSION_TTL, ip or "", ua or ""),
    )
    # opportunistic pruning keeps the table small
    db.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token


def destroy_session(token: str) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token=?", (token,))


def destroy_all_sessions(user_id: int) -> None:
    db.run("DELETE FROM sessions WHERE user_id=?", (int(user_id),))


def _cookie_domain(host: str) -> str | None:
    host = (host or "").lower().split(":")[0]
    return SITE_COOKIE_DOMAIN if host.endswith("luaisgame.com") else None


def set_session_cookie(response, token: str, host: str) -> None:
    kwargs = dict(max_age=SESSION_TTL, httponly=True, samesite="Lax", path="/")
    domain = _cookie_domain(host)
    if domain:
        kwargs["domain"] = domain
        kwargs["secure"] = True          # only over https://*.luaisgame.com
    response.set_cookie(SESSION_COOKIE, token, **kwargs)


def clear_session_cookie(response, host: str) -> None:
    kwargs = dict(httponly=True, samesite="Lax", path="/")
    domain = _cookie_domain(host)
    if domain:
        kwargs["domain"] = domain
        kwargs["secure"] = True
    response.del_cookie(SESSION_COOKIE, **kwargs)


def session_token(request) -> str:
    return request.cookies.get(SESSION_COOKIE, "") or request.query.get("token", "")


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

def get_user(uid) -> dict | None:
    if uid is None:
        return None
    return db.row("SELECT * FROM users WHERE id=?", (int(uid),))


def user_by_email(email: str) -> dict | None:
    return db.row("SELECT * FROM users WHERE email_lower=?", ((email or "").strip().lower(),))


def user_by_username(username: str) -> dict | None:
    return db.row("SELECT * FROM users WHERE username_low=?", ((username or "").strip().lower(),))


def user_by_discord(discord_id) -> dict | None:
    if not discord_id:
        return None
    return db.row("SELECT * FROM users WHERE discord_id=?", (str(discord_id),))


def create_user(email: str, password: str, username: str, display_name: str = "",
                discord_id: str = "", avatar: str = "", discord_name: str = "") -> dict:
    email = (email or "").strip()
    username = (username or "").strip()
    is_owner = 1 if str(discord_id) == OWNER_DISCORD_ID else 0
    uid = db.run(
        "INSERT INTO users(email,email_lower,password_hash,username,username_low,display_name,"
        "avatar,discord_id,discord_name,created_at,last_seen,is_owner) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (email, email.lower(), hash_password(password) if password else "",
         username, username.lower(), display_name or username, avatar,
         str(discord_id) if discord_id else None, discord_name or "",
         time.time(), time.time(), is_owner),
    )
    if is_owner:
        grant_owner_badge(uid)
    return get_user(uid)


def touch_presence(user_id: int, presence: str = "online") -> None:
    db.run("UPDATE users SET last_seen=?, presence=? WHERE id=?",
           (time.time(), presence or "online", int(user_id)))


def touch_last_seen(user_id: int) -> None:
    """Record that a tab connected/disconnected without touching their status.

    `presence` stores the user's *choice* (online/idle/dnd/invisible); whether
    they're actually reachable is derived from live sockets in
    display_presence(), so connections must not rewrite the choice.
    """
    db.run("UPDATE users SET last_seen=? WHERE id=?", (time.time(), int(user_id)))


_rt = None


def _realtime():
    """Late import: realtime imports auth, so auth can't import it at module level."""
    global _rt
    if _rt is None:
        from . import realtime
        _rt = realtime
    return _rt


def display_presence(user) -> str:
    """The presence everyone else should see.

    Offline is derived from the live socket table instead of the database, so
    a crashed or restarted process can never leave a user stuck "online".
    """
    if not user:
        return "offline"
    choice = user.get("presence") or "online"
    if choice == "invisible":
        return "offline"
    try:
        live = int(user["id"]) in _realtime().by_user
    except Exception:
        live = False
    if not live:
        return "offline"
    return choice if choice in ("idle", "dnd") else "online"


def call_presence(user) -> tuple:
    """(in a call right now, speaking right now)."""
    if not user:
        return (False, False)
    try:
        rt = _realtime()
        uid = int(user["id"])
        return (uid in rt.calling_users(), uid in rt.talking)
    except Exception:
        return (False, False)


def is_owner_user(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("is_owner"):
        return True
    return str(user.get("discord_id") or "") == OWNER_DISCORD_ID


def promote_owner(user_id: int) -> None:
    db.run("UPDATE users SET is_owner=1 WHERE id=?", (int(user_id),))
    grant_owner_badge(user_id)


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def badges_for(user_id: int) -> list:
    return db.rows(
        "SELECT b.id, b.slug, b.name, b.description, b.icon, b.color, ub.granted_at "
        "FROM user_badges ub JOIN badges b ON b.id=ub.badge_id "
        "WHERE ub.user_id=? ORDER BY ub.granted_at", (int(user_id),))


def public_user(user: dict | None) -> dict:
    if not user:
        return {}
    in_call, talking = call_presence(user)
    return {
        "uid": user["id"],
        "id": str(user.get("discord_id") or "0"),   # legacy Discord-snowflake field
        "username": user["username"],
        "display_name": user["display_name"],
        "avatar": user.get("avatar") or "",
        "bio": user.get("bio") or "",
        "discord_id": user.get("discord_id"),
        "discord_name": user.get("discord_name") or "",
        "is_owner": bool(user.get("is_owner")) or str(user.get("discord_id") or "") == OWNER_DISCORD_ID,
        "banned": bool(user.get("banned")),
        "presence": display_presence(user),
        "status_choice": user.get("presence") or "online",   # what the settings picker shows
        "in_call": in_call,
        "talking": talking,
        "custom_status": user.get("custom_status") or "",
        "created_at": user.get("created_at") or 0,
        "badges": badges_for(user["id"]),
    }


def user_from_request(request) -> dict | None:
    """Resolve the signed-in user for any luaisgame.com host."""
    token = session_token(request)
    if not token:
        return None
    session = db.row("SELECT * FROM sessions WHERE token=?", (token,))
    if not session:
        return None
    if session["expires_at"] <= time.time():
        destroy_session(token)
        return None
    user = get_user(session["user_id"])
    if not user or user.get("banned"):
        return None
    return user


def request_user_public(request) -> dict | None:
    user = user_from_request(request)
    return public_user(user) if user else None


# --------------------------------------------------------------------------
# discord oauth
# --------------------------------------------------------------------------

def redirect_for_host(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    return REDIRECT_BY_HOST.get(host) or REDIRECT_BY_HOST.get("storage.luaisgame.com") or ""


def request_host(request) -> str:
    return (request.host or "").lower().split(":")[0]


def make_oauth_state(redirect: str, next_url: str, host: str, link_user: int = 0) -> str:
    state = secrets.token_urlsafe(32)
    db.run(
        "INSERT OR REPLACE INTO oauth_states(state,ts,redirect,next,host,link_user) "
        "VALUES(?,?,?,?,?,?)",
        (state, time.time(), redirect, next_url or "/", host or "", int(link_user or 0)),
    )
    db.run("DELETE FROM oauth_states WHERE ts < ?", (time.time() - 900,))
    return state


def consume_oauth_state(state: str) -> dict | None:
    if not state:
        return None
    row_ = db.row("SELECT * FROM oauth_states WHERE state=?", (state,))
    if not row_:
        return None
    db.run("DELETE FROM oauth_states WHERE state=?", (state,))
    if time.time() - row_["ts"] > 600:
        return None
    return row_


def discord_auth_url(request, next_url: str = "/", link_user: int = 0) -> str:
    host = request_host(request)
    redirect = redirect_for_host(host)
    state = make_oauth_state(redirect, next_url, host, link_user)
    query = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    return f"https://discord.com/api/oauth2/authorize?{query}"


async def exchange_discord_code(code: str, redirect: str) -> dict | None:
    """Exchange the OAuth code and return the Discord user payload."""
    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://discord.com/api/oauth2/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as token_resp:
                if token_resp.status != 200:
                    return None
                token = await token_resp.json()
            access_token = token.get("access_token")
            if not access_token:
                return None
            async with session.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as user_resp:
                if user_resp.status != 200:
                    return None
                return await user_resp.json()
    except Exception as e:
        print(f"[DM AUTH] Discord exchange failed: {e}")
        return None


def login_or_link_discord(user_data: dict) -> dict:
    """Find (or create) the local account for a Discord identity."""
    discord_id = str(user_data.get("id", ""))
    username = user_data.get("username") or f"user{discord_id[-6:]}"
    avatar = ""
    if user_data.get("avatar"):
        avatar = (f"https://cdn.discordapp.com/avatars/{discord_id}/{user_data['avatar']}"
                  f".{'gif' if user_data.get('avatar','').startswith('a_') else 'png'}")
    elif user_data.get("avatar") is None:
        avatar = ""

    user = user_by_discord(discord_id)
    if user:
        db.run("UPDATE users SET discord_name=?, avatar=CASE WHEN avatar LIKE 'https://cdn.discordapp.com/%' "
               "THEN ? ELSE avatar END, last_seen=? WHERE id=?",
               (username, avatar, time.time(), user["id"]))
        return get_user(user["id"])

    # Brand new account created straight from Discord.
    email = f"discord:{discord_id}@discord.local"
    if user_by_email(email):
        email = f"discord:{discord_id}.{secrets.token_hex(3)}@discord.local"
    base = "".join(c if c.isalnum() or c in "_." else "_" for c in username).lower() or "user"
    handle = base
    n = 1
    while user_by_username(handle):
        n += 1
        handle = f"{base}{n}"
    return create_user(email, "", handle, user_data.get("global_name") or username,
                       discord_id=discord_id, avatar=avatar, discord_name=username)


def link_discord(uid: int, user_data: dict) -> tuple[bool, str]:
    """Attach a Discord identity to an existing email account."""
    discord_id = str(user_data.get("id", ""))
    owner = user_by_discord(discord_id)
    if owner and owner["id"] != int(uid):
        return False, "That Discord account is already linked to another account."
    avatar = ""
    if user_data.get("avatar"):
        avatar = (f"https://cdn.discordapp.com/avatars/{discord_id}/{user_data['avatar']}"
                  f".{'gif' if user_data.get('avatar','').startswith('a_') else 'png'}")
    db.run("UPDATE users SET discord_id=?, discord_name=?, "
           "is_owner=CASE WHEN ?=? THEN 1 ELSE is_owner END, last_seen=? WHERE id=?",
           (discord_id, user_data.get("username") or "", discord_id, OWNER_DISCORD_ID,
            time.time(), int(uid)))
    if avatar:
        db.run("UPDATE users SET avatar=? WHERE id=? AND (avatar='' OR avatar IS NULL)",
               (avatar, int(uid)))
    if discord_id == OWNER_DISCORD_ID:
        grant_owner_badge(uid)
    return True, "Discord account linked."


# --------------------------------------------------------------------------
# email + password auth
# --------------------------------------------------------------------------

def register(email: str, password: str, username: str) -> tuple[bool, str, dict | None]:
    email = (email or "").strip()
    username = (username or "").strip()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return False, "Enter a valid email address.", None
    if len(password or "") < PASSWORD_MIN:
        return False, f"Password must be at least {PASSWORD_MIN} characters.", None
    import re
    if not re.match(USERNAME_RE, username.lower()):
        return False, "Username must be 2-32 characters: a-z, 0-9, underscore or dot.", None
    if user_by_email(email):
        return False, "That email is already registered.", None
    if user_by_username(username):
        return False, "That username is taken.", None
    user = create_user(email, password, username, username)
    return True, "Account created.", user


def login(email: str, password: str, ip: str = "") -> tuple[bool, str, dict | None]:
    key = f"{ip}|{email.strip().lower()}"
    if _rate_limited(key):
        return False, "Too many attempts. Try again in a few minutes.", None
    user = user_by_email(email)
    if not user or not user.get("password_hash") or not verify_password(password, user["password_hash"]):
        _bump_attempt(key)
        return False, "Incorrect email or password.", None
    if user.get("banned"):
        return False, "This account is banned.", None
    _login_attempts.pop(key, None)
    return True, "Welcome back.", user
