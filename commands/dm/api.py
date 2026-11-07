"""REST API for dm.luaisgame.com.

Everything is namespaced under /api/dm/* so it can live on the same aiohttp app
as storage.* and mc.* (the host decides which HTML shell is served, the path
decides which handler runs).

Permissions: every server grants a bitmask of PERM_* via the owner role and any
roles assigned to the member. The platform owner (you) always has all of them.
"""

import base64
import json
import mimetypes
import os
import re
import secrets
import string
import time

from aiohttp import web

from . import auth, db, realtime

MAX_CONTENT = 4000                      # message length
MAX_MEDIA_BYTES = 5 * 1024 * 1024       # avatars / server + role icons
UPLOAD_CHUNK = 16 * 1024 * 1024         # accepted per request (app limit is 64 MB)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024        # 10 GB per file
USER_UPLOAD_QUOTA = 50 * 1024 * 1024 * 1024       # 50 GB per account
MIN_FREE_DISK = 4 * 1024 * 1024 * 1024            # refuse writes under 4 GB free

PERM_ADMIN = 1
PERM_MANAGE_CHANNELS = 2
PERM_MANAGE_ROLES = 4
PERM_MANAGE_MESSAGES = 8
PERM_KICK = 16
PERM_BAN = 32
PERM_ALL = 0xFFFF

SLUG_RE = re.compile(r"^[a-z0-9\-]{2,32}$")
USERNAME_RE = re.compile(r"^[a-z0-9_.]{2,32}$")

MEDIA_TYPES = {"avatar", "server", "role", "banner", "badge"}

_root = {"base": ".", "media": ".", "uploads": "."}


def set_paths(base_dir: str) -> None:
    _root["base"] = base_dir
    _root["media"] = os.path.join(base_dir, "dm_media")
    _root["uploads"] = os.path.join(base_dir, "dm_uploads")
    for path in (_root["media"], _root["uploads"]):
        os.makedirs(path, exist_ok=True)
    # Partial uploads can't outlive the process that was writing them (the chunk
    # endpoint returns 410 and the client restarts), so sweep them at boot.
    try:
        for name in os.listdir(_root["uploads"]):
            if name.endswith(".part"):
                os.remove(os.path.join(_root["uploads"], name))
                print(f"[DM] Removed stale partial upload {name}")
    except OSError:
        pass


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

async def _body(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _need_user(request) -> dict:
    user = auth.user_from_request(request)
    if not user:
        raise web.HTTPUnauthorized(text='{"error":"not signed in"}',
                                   content_type="application/json")
    return user


def _need_owner(request) -> dict:
    user = _need_user(request)
    if not auth.is_owner_user(user):
        raise web.HTTPForbidden(text='{"error":"owner only"}',
                                content_type="application/json")
    return user


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return text[:32] or "server"


def _invite_code() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _disk_free() -> int:
    try:
        return os.statvfs(_root["uploads"]).f_bavail * os.statvfs(_root["uploads"]).f_frsize
    except Exception:
        try:
            import shutil
            return shutil.disk_usage(_root["uploads"]).free
        except Exception:
            return 1 << 40


def _save_media(data_url: str, kind: str, uid: int) -> str:
    """Persist a base64 data-URL image, return the public path."""
    if kind not in MEDIA_TYPES:
        raise ValueError("unsupported media kind")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise ValueError("expected an image data URL")
    header, _, payload = data_url.partition(",")
    if ";base64" not in header:
        raise ValueError("expected base64 image data")
    raw = base64.b64decode(payload)
    if len(raw) > MAX_MEDIA_BYTES:
        raise ValueError("image is too large (max 5 MB)")
    ext = "gif" if "gif" in header else "png"
    if "jpeg" in header or "jpg" in header:
        ext = "jpg"
    name = f"{kind}_{uid}_{secrets.token_hex(8)}.{ext}"
    os.makedirs(_root["media"], exist_ok=True)
    with open(os.path.join(_root["media"], name), "wb") as fh:
        fh.write(raw)
    return f"/api/dm/media/{name}"


# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------

def is_platform_owner(user: dict) -> bool:
    return auth.is_owner_user(user)


def member_of(user: dict, server_id: int):
    return db.row("SELECT * FROM server_members WHERE server_id=? AND user_id=?",
                  (server_id, user["id"]))


def server_perms(user: dict, server_id: int) -> int:
    if is_platform_owner(user):
        return PERM_ALL
    server = db.row("SELECT * FROM servers WHERE id=?", (server_id,))
    if not server:
        return 0
    member = member_of(user, server_id)
    if not member:
        return 0
    if server["owner_id"] == user["id"]:
        return PERM_ALL
    perms = 0
    for row in db.rows(
            "SELECT r.permissions FROM member_roles mr JOIN roles r ON r.id=mr.role_id "
            "WHERE mr.server_id=? AND mr.user_id=?", (server_id, user["id"])):
        perms |= int(row["permissions"] or 0)
    return perms


def has_perm(user: dict, server_id: int, perm: int) -> bool:
    return bool(server_perms(user, server_id) & perm) or bool(perm & PERM_ADMIN and
                                                              is_platform_owner(user))


def _require_server(request, server_id: int, perm: int = 0) -> tuple[dict, dict]:
    user = _need_user(request)
    server = db.row("SELECT * FROM servers WHERE id=?", (server_id,))
    if not server:
        raise web.HTTPNotFound(text='{"error":"server not found"}',
                               content_type="application/json")
    if perm and not (server_perms(user, server_id) & perm):
        raise web.HTTPForbidden(text='{"error":"missing permission"}',
                                content_type="application/json")
    if not perm and not member_of(user, server_id) and not is_platform_owner(user):
        raise web.HTTPForbidden(text='{"error":"not a member"}',
                                content_type="application/json")
    return user, server


def _require_channel(request, channel_id: int, perm: int = 0) -> tuple[dict, dict, dict | None]:
    """Returns (user, channel, server|None). DM channels have server None."""
    user = _need_user(request)
    channel = db.row("SELECT * FROM channels WHERE id=?", (channel_id,))
    if not channel:
        raise web.HTTPNotFound(text='{"error":"channel not found"}',
                               content_type="application/json")
    if channel["server_id"]:
        server = db.row("SELECT * FROM servers WHERE id=?", (channel["server_id"],))
        if perm:
            if not (server_perms(user, channel["server_id"]) & perm):
                raise web.HTTPForbidden(text='{"error":"missing permission"}',
                                        content_type="application/json")
        elif not member_of(user, channel["server_id"]) and not is_platform_owner(user):
            raise web.HTTPForbidden(text='{"error":"not a member"}',
                                    content_type="application/json")
        return user, channel, server
    # direct message / group chat
    if not db.row("SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?",
                  (channel_id, user["id"])):
        raise web.HTTPForbidden(text='{"error":"not a participant"}',
                                content_type="application/json")
    return user, channel, None


# --------------------------------------------------------------------------
# payload builders
# --------------------------------------------------------------------------

def _user_brief(uid: int) -> dict:
    user = auth.get_user(uid)
    if not user:
        return {"uid": uid, "username": "unknown", "display_name": "Unknown", "avatar": "",
                "badges": [], "id": "0", "presence": "offline"}
    out = auth.public_user(user)
    out.pop("bio", None)
    out.pop("created_at", None)
    return out


def _role_payload(role: dict) -> dict:
    return {"id": role["id"], "server_id": role["server_id"], "name": role["name"],
            "color": role["color"], "icon": role["icon"], "position": role["position"],
            "permissions": int(role["permissions"] or 0), "hoist": bool(role["hoist"])}


def _channel_payload(channel: dict, user: dict) -> dict:
    out = {"id": channel["id"], "server_id": channel["server_id"], "kind": channel["kind"],
           "name": channel["name"], "position": channel["position"],
           "topic": channel["topic"], "avatar": channel["avatar"],
           "created_at": channel["created_at"]}
    if not channel["server_id"]:
        members = [m["user_id"] for m in db.rows(
            "SELECT user_id FROM channel_members WHERE channel_id=?", (channel["id"],))]
        others = [m for m in members if m != user["id"]]
        out["members"] = [_user_brief(m) for m in others]
        if channel["kind"] == "group" or not channel["name"]:
            if not channel["name"]:
                names = [m["display_name"] for m in
                         [_user_brief(x) for x in others[:4]]]
                out["name"] = ", ".join(names) or "Group chat"
        out["member_ids"] = members
        out["unread"] = 0
    else:
        out["permission_overwrites"] = []
        mentions = 0
        out["mentions"] = mentions
    return out


def _attachment_payload(att: dict) -> dict:
    return {"id": att["id"], "filename": att["filename"], "size": att["size"],
            "mime": att["mime"],
            "url": f"/api/dm/files/{att['id']}/{att['filename']}"}


def _message_payload(msg: dict) -> dict:
    try:
        attachments = json.loads(msg.get("attachments") or "[]")
    except Exception:
        attachments = []
    return {"id": msg["id"], "channel": msg["channel_id"], "author": _user_brief(msg["author_id"]),
            "content": msg["content"], "attachments": attachments,
            "created_at": msg["created_at"], "edited_at": msg["edited_at"]}


def _server_brief(server: dict, user: dict) -> dict:
    member = member_of(user, server["id"])
    unread = db.one(
        "SELECT COUNT(*) FROM messages m WHERE m.channel_id IN "
        "(SELECT id FROM channels WHERE server_id=?) AND m.created_at > ?",
        (server["id"], (member or {}).get("joined_at", 0))) or 0
    return {"id": server["id"], "name": server["name"], "icon": server["icon"],
            "description": server["description"], "vanity": server["vanity"],
            "invite": server["invite"], "owner_id": server["owner_id"],
            "created_at": server["created_at"],
            "members": db.one("SELECT COUNT(*) FROM server_members WHERE server_id=?",
                              (server["id"],)) or 0,
            "channels": db.one("SELECT COUNT(*) FROM channels WHERE server_id=?",
                               (server["id"],)) or 0,
            "unread": int(unread),
            "owner": server["owner_id"] == user["id"],
            "permissions": server_perms(user, server["id"])}


def _server_full(server: dict, user: dict) -> dict:
    channels = db.rows("SELECT * FROM channels WHERE server_id=? ORDER BY position, id",
                       (server["id"],))
    roles = db.rows("SELECT * FROM roles WHERE server_id=? ORDER BY position DESC, id",
                    (server["id"],))
    members = db.rows(
        "SELECT sm.user_id, sm.nickname, sm.joined_at FROM server_members sm "
        "WHERE sm.server_id=? ORDER BY sm.joined_at", (server["id"],))
    member_payload = []
    for m in members:
        brief = _user_brief(m["user_id"])
        role_ids = [r["role_id"] for r in db.rows(
            "SELECT role_id FROM member_roles WHERE server_id=? AND user_id=?",
            (server["id"], m["user_id"]))]
        brief["nickname"] = m["nickname"]
        brief["roles"] = role_ids
        brief["joined_at"] = m["joined_at"]
        member_payload.append(brief)
    return {
        **_server_brief(server, user),
        "channels": [_channel_payload(c, user) for c in channels],
        "roles": [_role_payload(r) for r in roles],
        "members": member_payload,
        "my_permissions": server_perms(user, server["id"]),
        "my_roles": [r["role_id"] for r in db.rows(
            "SELECT role_id FROM member_roles WHERE server_id=? AND user_id=?",
            (server["id"], user["id"]))],
    }


# --------------------------------------------------------------------------
# account endpoints
# --------------------------------------------------------------------------

async def api_register(request):
    data = await _body(request)
    ok, message, user = auth.register(data.get("email", ""), data.get("password", ""),
                                      data.get("username", ""))
    if not ok:
        return _err(message)
    token = auth.create_session(user["id"], request.remote or "",
                                request.headers.get("User-Agent", ""))
    resp = web.json_response({"ok": True, "user": auth.public_user(user)})
    auth.set_session_cookie(resp, token, auth.request_host(request))
    return resp


async def api_login(request):
    data = await _body(request)
    ok, message, user = auth.login(data.get("email", ""), data.get("password", ""),
                                   request.remote or "")
    if not ok:
        return _err(message, 401)
    token = auth.create_session(user["id"], request.remote or "",
                                request.headers.get("User-Agent", ""))
    resp = web.json_response({"ok": True, "user": auth.public_user(user)})
    auth.set_session_cookie(resp, token, auth.request_host(request))
    return resp


async def api_logout(request):
    auth.destroy_session(auth.session_token(request))
    resp = web.json_response({"ok": True})
    auth.clear_session_cookie(resp, auth.request_host(request))
    return resp


async def api_me(request):
    user = _need_user(request)
    auth.touch_presence(user["id"], "online")
    return web.json_response({"user": auth.public_user(user),
                              "servers": [_server_brief(s, user) for s in db.rows(
                                  "SELECT s.* FROM servers s JOIN server_members m "
                                  "ON m.server_id=s.id WHERE m.user_id=? ORDER BY s.name",
                                  (user["id"],))]})


async def api_update_me(request):
    user = _need_user(request)
    data = await _body(request)
    fields, params = [], []
    if "display_name" in data:
        name = str(data.get("display_name") or "").strip()[:64]
        fields.append("display_name=?")
        params.append(name or user["display_name"])
    if "bio" in data:
        fields.append("bio=?")
        params.append(str(data.get("bio") or "")[:400])
    if "custom_status" in data:
        fields.append("custom_status=?")
        params.append(str(data.get("custom_status") or "")[:128])
    if "presence" in data and data.get("presence") in ("online", "idle", "dnd", "invisible"):
        fields.append("presence=?")
        params.append(data["presence"])
    if "avatar" in data and data.get("avatar"):
        try:
            fields.append("avatar=?")
            params.append(_save_media(data["avatar"], "avatar", user["id"]))
        except Exception as e:
            return _err(str(e))
    if fields:
        params.append(user["id"])
        db.run(f"UPDATE users SET {', '.join(fields)} WHERE id=?", params)
    return web.json_response({"user": auth.public_user(auth.get_user(user["id"]))})


async def api_change_password(request):
    user = _need_user(request)
    data = await _body(request)
    if not user.get("password_hash"):
        return _err("This account signs in with Discord, so it has no password yet.")
    if not auth.verify_password(str(data.get("current") or ""), user["password_hash"]):
        return _err("Current password is incorrect.", 403)
    if len(str(data.get("new") or "")) < auth.PASSWORD_MIN:
        return _err(f"New password must be at least {auth.PASSWORD_MIN} characters.")
    db.run("UPDATE users SET password_hash=? WHERE id=?",
           (auth.hash_password(data["new"]), user["id"]))
    return web.json_response({"ok": True})


async def api_discord_start(request):
    """?next=/...&link=1 — sign in with, or link, Discord."""
    user = auth.user_from_request(request)
    link_user = 0
    if request.query.get("link") == "1":
        if not user:
            return _err("Sign in first to link Discord.", 401)
        link_user = user["id"]
    next_url = request.query.get("next") or "/"
    url = auth.discord_auth_url(request, next_url, link_user)
    resp = web.Response(status=302)
    resp.headers["Location"] = url
    return resp


async def api_discord_unlink(request):
    user = _need_user(request)
    if not user.get("discord_id"):
        return _err("No Discord account is linked.")
    if not user.get("password_hash"):
        return _err("Set an email + password first, otherwise you could get locked out.", 400)
    db.run("UPDATE users SET discord_id=NULL, discord_name='', is_owner=0 WHERE id=?",
           (user["id"],))
    if str(user.get("discord_id")) == auth.OWNER_DISCORD_ID:
        db.run("UPDATE users SET is_owner=1 WHERE id=?", (user["id"],))
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# bootstrap / directory
# --------------------------------------------------------------------------

async def api_bootstrap(request):
    user = _need_user(request)
    servers = db.rows(
        "SELECT s.* FROM servers s JOIN server_members m ON m.server_id=s.id "
        "WHERE m.user_id=? ORDER BY s.name", (user["id"],))
    dms = db.rows(
        "SELECT c.* FROM channels c JOIN channel_members cm ON cm.channel_id=c.id "
        "WHERE cm.user_id=? AND c.server_id IS NULL ORDER BY c.id DESC", (user["id"],))
    return web.json_response({
        "user": auth.public_user(user),
        "servers": [_server_brief(s, user) for s in servers],
        "dms": [_channel_payload(c, user) for c in dms],
        "calls": realtime.active_calls(),
        "server_time": time.time(),
    })


async def api_directory(request):
    viewer = _need_user(request)
    query = (request.query.get("q") or "").strip()
    if not query:
        return web.json_response({"users": [], "servers": []})
    like = f"%{query.lower()}%"
    users = db.rows(
        "SELECT * FROM users WHERE banned=0 AND (username_low LIKE ? OR lower(display_name) LIKE ?) "
        "LIMIT 20", (like, like))
    servers = db.rows(
        "SELECT * FROM servers WHERE lower(name) LIKE ? OR vanity LIKE ? LIMIT 20",
        (like, query.lower()))
    return web.json_response({"users": [auth.public_user(u) for u in users],
                              "servers": [_server_brief(s, viewer) for s in servers]})


async def api_user_profile(request):
    viewer = _need_user(request)
    uid = int(request.match_info["uid"])
    user = auth.get_user(uid)
    if not user:
        raise web.HTTPNotFound(text='{"error":"user not found"}',
                               content_type="application/json")
    shared = [s for s in db.rows(
        "SELECT s.* FROM servers s JOIN server_members m ON m.server_id=s.id "
        "WHERE m.user_id=?", (viewer["id"],))
        if db.row("SELECT 1 FROM server_members WHERE server_id=? AND user_id=?",
                  (s["id"], uid))]
    return web.json_response({"user": auth.public_user(user),
                              "mutual_servers": [_server_brief(s, viewer) for s in shared],
                              "can_dm": True})


# --------------------------------------------------------------------------
# servers
# --------------------------------------------------------------------------

async def api_create_server(request):
    user = _need_user(request)
    data = await _body(request)
    name = str(data.get("name") or "").strip()[:64]
    if not name:
        return _err("Give your server a name.")
    icon = ""
    if data.get("icon"):
        try:
            icon = _save_media(data["icon"], "server", user["id"])
        except Exception as e:
            return _err(str(e))
    invite = _invite_code()
    while db.row("SELECT 1 FROM servers WHERE invite=?", (invite,)):
        invite = _invite_code()
    sid = db.run(
        "INSERT INTO servers(owner_id,name,icon,description,invite,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (user["id"], name, icon, str(data.get("description") or "")[:500], invite, time.time()))
    db.run("INSERT INTO server_members(server_id,user_id,joined_at) VALUES(?,?,?)",
           (sid, user["id"], time.time()))
    everyone = db.run(
        "INSERT INTO roles(server_id,name,color,position,permissions) VALUES(?,?,?,?,?)",
        (sid, "@everyone", "#99aab5", 0, 0))
    db.run("INSERT INTO channels(server_id,kind,name,position,created_by,created_at) "
           "VALUES(?,?,?,?,?,?)", (sid, "text", "general", 0, user["id"], time.time()))
    db.run("INSERT INTO channels(server_id,kind,name,position,created_by,created_at) "
           "VALUES(?,?,?,?,?,?)", (sid, "voice", "Voice", 1, user["id"], time.time()))
    db.run("INSERT INTO invites(code,server_id) VALUES(?,?)", (invite, sid))
    server = db.row("SELECT * FROM servers WHERE id=?", (sid,))
    return web.json_response({"server": _server_full(server, user), "everyone_role": everyone})


async def api_get_server(request):
    user, server = _require_server(request, int(request.match_info["sid"]))
    return web.json_response({"server": _server_full(server, user)})


async def api_patch_server(request):
    user, server = _require_server(request, int(request.match_info["sid"]), PERM_ADMIN)
    data = await _body(request)
    fields, params = [], []
    if "name" in data and str(data.get("name") or "").strip():
        fields.append("name=?")
        params.append(str(data["name"]).strip()[:64])
    if "description" in data:
        fields.append("description=?")
        params.append(str(data.get("description") or "")[:500])
    if data.get("icon"):
        try:
            fields.append("icon=?")
            params.append(_save_media(data["icon"], "server", user["id"]))
        except Exception as e:
            return _err(str(e))
    if fields:
        params.append(server["id"])
        db.run(f"UPDATE servers SET {', '.join(fields)} WHERE id=?", params)
        await realtime.broadcast_server(server["id"], "server.update",
                                        {"server": _server_brief(
                                            db.row("SELECT * FROM servers WHERE id=?",
                                                   (server["id"],)), user)})
    return web.json_response({"ok": True})


async def api_delete_server(request):
    user, server = _require_server(request, int(request.match_info["sid"]))
    if server["owner_id"] != user["id"] and not is_platform_owner(user):
        return _err("Only the server owner can delete it.", 403)
    sid = server["id"]
    channel_ids = [c["id"] for c in db.rows("SELECT id FROM channels WHERE server_id=?", (sid,))]
    for cid in channel_ids:
        db.run("DELETE FROM messages WHERE channel_id=?", (cid,))
    db.run("DELETE FROM channels WHERE server_id=?", (sid,))
    db.run("DELETE FROM server_members WHERE server_id=?", (sid,))
    db.run("DELETE FROM member_roles WHERE server_id=?", (sid,))
    db.run("DELETE FROM roles WHERE server_id=?", (sid,))
    db.run("DELETE FROM invites WHERE server_id=?", (sid,))
    db.run("DELETE FROM vanity_requests WHERE server_id=?", (sid,))
    db.run("DELETE FROM servers WHERE id=?", (sid,))
    return web.json_response({"ok": True})


async def api_join_server(request):
    user = _need_user(request)
    data = await _body(request)
    sid = int(request.match_info["sid"])
    server = db.row("SELECT * FROM servers WHERE id=?", (sid,))
    if not server:
        # allow joining straight from a vanity or invite string
        server = db.row("SELECT * FROM servers WHERE vanity=? OR invite=?",
                        (str(data.get("code") or ""), str(data.get("code") or "")))
        if not server:
            return _err("Server not found.", 404)
        sid = server["id"]
    if not member_of(user, sid):
        db.run("INSERT INTO server_members(server_id,user_id,joined_at) VALUES(?,?,?)",
               (sid, user["id"], time.time()))
        await realtime.broadcast_server(sid, "member.join",
                                        {"server_id": sid, "member": _user_brief(user["id"])})
    return web.json_response({"server": _server_full(server, user)})


async def api_join_by_code(request):
    user = _need_user(request)
    data = await _body(request)
    code = str(data.get("code") or "").strip().lower()
    if not code:
        return _err("Enter an invite or vanity link.")
    server = db.row("SELECT * FROM servers WHERE invite=? OR vanity=?", (code, code))
    if not server:
        return _err("That invite or vanity link doesn't exist.", 404)
    if not member_of(user, server["id"]):
        db.run("INSERT INTO server_members(server_id,user_id,joined_at) VALUES(?,?,?)",
               (server["id"], user["id"], time.time()))
        db.run("UPDATE invites SET uses=uses+1 WHERE code=?", (server["invite"],))
        await realtime.broadcast_server(server["id"], "member.join",
                                        {"server_id": server["id"],
                                         "member": _user_brief(user["id"])})
    return web.json_response({"server": _server_full(server, user)})


async def api_invite_preview(request):
    _need_user(request)
    code = (request.match_info.get("code") or "").strip().lower()
    server = db.row("SELECT * FROM servers WHERE invite=? OR vanity=?", (code, code))
    if not server:
        return _err("That invite doesn't exist.", 404)
    return web.json_response({"server": _server_brief(server, _need_user(request))})


async def api_leave_server(request):
    user, server = _require_server(request, int(request.match_info["sid"]))
    if server["owner_id"] == user["id"]:
        return _err("Transfer or delete the server first — you own it.", 400)
    db.run("DELETE FROM server_members WHERE server_id=? AND user_id=?",
           (server["id"], user["id"]))
    db.run("DELETE FROM member_roles WHERE server_id=? AND user_id=?",
           (server["id"], user["id"]))
    await realtime.broadcast_server(server["id"], "member.leave",
                                    {"server_id": server["id"], "uid": user["id"]})
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# channels
# --------------------------------------------------------------------------

async def api_create_channel(request):
    user, server = _require_server(request, int(request.match_info["sid"]), PERM_MANAGE_CHANNELS)
    data = await _body(request)
    name = str(data.get("name") or "").strip()[:64]
    if not name:
        return _err("Channel needs a name.")
    kind = data.get("kind") if data.get("kind") in ("text", "voice", "announce") else "text"
    position = db.one("SELECT COALESCE(MAX(position),0)+1 FROM channels WHERE server_id=?",
                      (server["id"],)) or 0
    cid = db.run("INSERT INTO channels(server_id,kind,name,position,topic,created_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (server["id"], kind, name, position, str(data.get("topic") or "")[:200],
                  user["id"], time.time()))
    channel = db.row("SELECT * FROM channels WHERE id=?", (cid,))
    await realtime.broadcast_server(server["id"], "channel.create",
                                    {"server_id": server["id"],
                                     "channel": _channel_payload(channel, user)})
    return web.json_response({"channel": _channel_payload(channel, user)})


async def api_patch_channel(request):
    user, channel, server = _require_channel(request, int(request.match_info["cid"]),
                                              PERM_MANAGE_CHANNELS)
    data = await _body(request)
    fields, params = [], []
    if "name" in data and str(data.get("name") or "").strip():
        fields.append("name=?")
        params.append(str(data["name"]).strip()[:64])
    if "topic" in data:
        fields.append("topic=?")
        params.append(str(data.get("topic") or "")[:200])
    if "kind" in data and data.get("kind") in ("text", "voice", "announce"):
        fields.append("kind=?")
        params.append(data["kind"])
    if "position" in data:
        try:
            fields.append("position=?")
            params.append(int(data.get("position")))
        except Exception:
            pass
    if fields:
        params.append(channel["id"])
        db.run(f"UPDATE channels SET {', '.join(fields)} WHERE id=?", params)
        updated = db.row("SELECT * FROM channels WHERE id=?", (channel["id"],))
        if server:
            await realtime.broadcast_server(server["id"], "channel.update",
                                            {"server_id": server["id"],
                                             "channel": _channel_payload(updated, user)})
    return web.json_response({"ok": True})


async def api_delete_channel(request):
    user, channel, server = _require_channel(request, int(request.match_info["cid"]),
                                              PERM_MANAGE_CHANNELS)
    if not server:
        return _err("Use the DM leave action instead.", 400)
    cid = channel["id"]
    db.run("DELETE FROM messages WHERE channel_id=?", (cid,))
    db.run("DELETE FROM channels WHERE id=?", (cid,))
    await realtime.broadcast_server(server["id"], "channel.delete",
                                    {"server_id": server["id"], "channel_id": cid})
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# roles
# --------------------------------------------------------------------------

async def api_create_role(request):
    user, server = _require_server(request, int(request.match_info["sid"]), PERM_MANAGE_ROLES)
    data = await _body(request)
    name = str(data.get("name") or "").strip()[:48]
    if not name:
        return _err("Role needs a name.")
    icon = ""
    if data.get("icon"):
        try:
            icon = _save_media(data["icon"], "role", user["id"])
        except Exception as e:
            return _err(str(e))
    perms = int(data.get("permissions") or 0) & PERM_ALL
    if is_platform_owner(user):
        perms = int(data.get("permissions") or 0) & PERM_ALL
    elif server["owner_id"] == user["id"]:
        pass
    else:
        perms &= ~PERM_ADMIN
    position = db.one("SELECT COALESCE(MAX(position),0)+1 FROM roles WHERE server_id=?",
                      (server["id"],)) or 0
    rid = db.run("INSERT INTO roles(server_id,name,color,icon,position,permissions,hoist) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (server["id"], name, str(data.get("color") or "#99aab5")[:20], icon,
                  position, perms, 1 if data.get("hoist") else 0))
    role = db.row("SELECT * FROM roles WHERE id=?", (rid,))
    await realtime.broadcast_server(server["id"], "role.create",
                                    {"server_id": server["id"], "role": _role_payload(role)})
    return web.json_response({"role": _role_payload(role)})


async def api_patch_role(request):
    rid = int(request.match_info["rid"])
    role = db.row("SELECT * FROM roles WHERE id=?", (rid,))
    if not role:
        raise web.HTTPNotFound(text='{"error":"role not found"}',
                               content_type="application/json")
    user, server = _require_server(request, role["server_id"], PERM_MANAGE_ROLES)
    data = await _body(request)
    fields, params = [], []
    if "name" in data and str(data.get("name") or "").strip():
        fields.append("name=?")
        params.append(str(data["name"]).strip()[:48])
    if "color" in data:
        fields.append("color=?")
        params.append(str(data.get("color") or "#99aab5")[:20])
    if data.get("icon"):
        try:
            fields.append("icon=?")
            params.append(_save_media(data["icon"], "role", user["id"]))
        except Exception as e:
            return _err(str(e))
    if "permissions" in data:
        perms = int(data.get("permissions") or 0) & PERM_ALL
        if not (is_platform_owner(user) or server["owner_id"] == user["id"]):
            perms &= ~PERM_ADMIN
        fields.append("permissions=?")
        params.append(perms)
    if "hoist" in data:
        fields.append("hoist=?")
        params.append(1 if data.get("hoist") else 0)
    if "position" in data:
        try:
            fields.append("position=?")
            params.append(int(data.get("position")))
        except Exception:
            pass
    if fields:
        params.append(rid)
        db.run(f"UPDATE roles SET {', '.join(fields)} WHERE id=?", params)
        updated = db.row("SELECT * FROM roles WHERE id=?", (rid,))
        await realtime.broadcast_server(server["id"], "role.update",
                                        {"server_id": server["id"],
                                         "role": _role_payload(updated)})
    return web.json_response({"ok": True})


async def api_delete_role(request):
    rid = int(request.match_info["rid"])
    role = db.row("SELECT * FROM roles WHERE id=?", (rid,))
    if not role:
        raise web.HTTPNotFound(text='{"error":"role not found"}',
                               content_type="application/json")
    user, server = _require_server(request, role["server_id"], PERM_MANAGE_ROLES)
    if role["name"] == "@everyone":
        return _err("You can't delete @everyone.")
    db.run("DELETE FROM member_roles WHERE role_id=?", (rid,))
    db.run("DELETE FROM roles WHERE id=?", (rid,))
    await realtime.broadcast_server(server["id"], "role.delete",
                                    {"server_id": server["id"], "role_id": rid})
    return web.json_response({"ok": True})


async def api_set_member_roles(request):
    user, server = _require_server(request, int(request.match_info["sid"]), PERM_MANAGE_ROLES)
    target_uid = int(request.match_info["uid"])
    data = await _body(request)
    wanted = []
    for rid in (data.get("role_ids") or []):
        try:
            rid = int(rid)
        except Exception:
            continue
        if db.row("SELECT 1 FROM roles WHERE id=? AND server_id=?", (rid, server["id"])):
            wanted.append(rid)
    db.run("DELETE FROM member_roles WHERE server_id=? AND user_id=?",
           (server["id"], target_uid))
    for rid in set(wanted):
        db.run("INSERT OR IGNORE INTO member_roles(server_id,user_id,role_id) VALUES(?,?,?)",
               (server["id"], target_uid, rid))
    await realtime.broadcast_server(server["id"], "member.update",
                                    {"server_id": server["id"], "uid": target_uid,
                                     "roles": sorted(set(wanted))})
    return web.json_response({"ok": True, "roles": sorted(set(wanted))})


async def api_kick_member(request):
    user, server = _require_server(request, int(request.match_info["sid"]), PERM_KICK)
    target_uid = int(request.match_info["uid"])
    if target_uid == server["owner_id"]:
        return _err("You can't kick the owner.")
    db.run("DELETE FROM server_members WHERE server_id=? AND user_id=?",
           (server["id"], target_uid))
    db.run("DELETE FROM member_roles WHERE server_id=? AND user_id=?",
           (server["id"], target_uid))
    await realtime.broadcast_server(server["id"], "member.leave",
                                    {"server_id": server["id"], "uid": target_uid})
    await realtime.send_to_user(target_uid, {"t": "event", "op": "server.kicked",
                                             "data": {"server_id": server["id"],
                                                      "server_name": server["name"]}})
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# direct messages / group chats
# --------------------------------------------------------------------------

async def api_list_dms(request):
    user = _need_user(request)
    dms = db.rows(
        "SELECT c.* FROM channels c JOIN channel_members cm ON cm.channel_id=c.id "
        "WHERE cm.user_id=? AND c.server_id IS NULL ORDER BY c.id DESC", (user["id"],))
    return web.json_response({"dms": [_channel_payload(c, user) for c in dms]})


async def api_create_dm(request):
    user = _need_user(request)
    data = await _body(request)
    members = []
    for raw in (data.get("members") or [])[:49]:
        try:
            uid = int(raw)
        except Exception:
            continue
        if uid != user["id"] and auth.get_user(uid):
            members.append(uid)
    group = bool(data.get("group")) or len(members) > 1
    if not members:
        return _err("Pick someone to message.")

    if not group:
        # reuse an existing 1:1 DM if there is one
        existing = db.rows(
            "SELECT c.id FROM channels c "
            "JOIN channel_members a ON a.channel_id=c.id AND a.user_id=? "
            "JOIN channel_members b ON b.channel_id=c.id AND b.user_id=? "
            "WHERE c.server_id IS NULL AND c.kind='dm'",
            (user["id"], members[0]))
        for row in existing:
            count = db.one("SELECT COUNT(*) FROM channel_members WHERE channel_id=?",
                           (row["id"],))
            if count == 2:
                channel = db.row("SELECT * FROM channels WHERE id=?", (row["id"],))
                return web.json_response({"channel": _channel_payload(channel, user)})

    name = str(data.get("name") or "").strip()[:64] if group else ""
    cid = db.run("INSERT INTO channels(server_id,kind,name,position,avatar,created_by,created_at) "
                 "VALUES(NULL,?,?,?,?,?,?)",
                 ("group" if group else "dm", name, 0, "", user["id"], time.time()))
    now = time.time()
    for uid in [user["id"], *members]:
        db.run("INSERT OR IGNORE INTO channel_members(channel_id,user_id,joined_at) VALUES(?,?,?)",
               (cid, uid, now))
    channel = db.row("SELECT * FROM channels WHERE id=?", (cid,))
    payload = _channel_payload(channel, user)
    for uid in [user["id"], *members]:
        await realtime.send_to_user(uid, {"t": "event", "op": "dm.create",
                                          "data": {"channel": payload}})
    return web.json_response({"channel": payload})


async def api_leave_dm(request):
    user, channel, _ = _require_channel(request, int(request.match_info["cid"]))
    if channel["server_id"]:
        return _err("Not a DM.")
    db.run("DELETE FROM channel_members WHERE channel_id=? AND user_id=?",
           (channel["id"], user["id"]))
    remaining = db.one("SELECT COUNT(*) FROM channel_members WHERE channel_id=?",
                       (channel["id"],))
    if not remaining:
        db.run("DELETE FROM messages WHERE channel_id=?", (channel["id"],))
        db.run("DELETE FROM channels WHERE id=?", (channel["id"],))
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

async def api_get_messages(request):
    user, channel, _ = _require_channel(request, int(request.match_info["cid"]))
    limit = min(max(int(request.query.get("limit") or 50), 1), 100)
    before = request.query.get("before")
    if before:
        msgs = db.rows(
            "SELECT * FROM messages WHERE channel_id=? AND id<? ORDER BY id DESC LIMIT ?",
            (channel["id"], int(before), limit))
    else:
        msgs = db.rows("SELECT * FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT ?",
                       (channel["id"], limit))
    return web.json_response({"messages": [_message_payload(m) for m in reversed(msgs)],
                              "channel": _channel_payload(channel, user)})


async def api_send_message(request):
    user, channel, _ = _require_channel(request, int(request.match_info["cid"]))
    data = await _body(request)
    content = str(data.get("content") or "")[:MAX_CONTENT].strip()
    attachments = []
    for att_id in (data.get("attachments") or [])[:10]:
        try:
            att_id = int(att_id)
        except Exception:
            continue
        att = db.row("SELECT * FROM attachments WHERE id=? AND owner_id=? AND message_id=0",
                     (att_id, user["id"]))
        if att:
            attachments.append(_attachment_payload(att))
    if not content and not attachments:
        return _err("Message is empty.")
    payload = json.dumps(attachments)
    mid = db.run("INSERT INTO messages(channel_id,author_id,content,attachments,created_at) "
                 "VALUES(?,?,?,?,?)", (channel["id"], user["id"], content, payload, time.time()))
    for att in attachments:
        db.run("UPDATE attachments SET message_id=? WHERE id=?", (mid, att["id"]))
    msg = db.row("SELECT * FROM messages WHERE id=?", (mid,))
    out = _message_payload(msg)
    await realtime.broadcast_channel(channel["id"], "message.create", out)
    return web.json_response({"message": out})


async def api_edit_message(request):
    user = _need_user(request)
    mid = int(request.match_info["mid"])
    msg = db.row("SELECT * FROM messages WHERE id=?", (mid,))
    if not msg:
        raise web.HTTPNotFound(text='{"error":"message not found"}',
                               content_type="application/json")
    user, channel, _ = _require_channel(request, msg["channel_id"])
    if msg["author_id"] != user["id"] and not has_perm(user, channel["server_id"] or 0,
                                                       PERM_MANAGE_MESSAGES) \
            and not is_platform_owner(user):
        return _err("You can only edit your own messages.", 403)
    data = await _body(request)
    content = str(data.get("content") or "")[:MAX_CONTENT]
    if not content.strip():
        return _err("Message can't be empty.")
    db.run("UPDATE messages SET content=?, edited_at=? WHERE id=?", (content, time.time(), mid))
    out = _message_payload(db.row("SELECT * FROM messages WHERE id=?", (mid,)))
    await realtime.broadcast_channel(channel["id"], "message.update", out)
    return web.json_response({"message": out})


async def api_delete_message(request):
    user = _need_user(request)
    mid = int(request.match_info["mid"])
    msg = db.row("SELECT * FROM messages WHERE id=?", (mid,))
    if not msg:
        raise web.HTTPNotFound(text='{"error":"message not found"}',
                               content_type="application/json")
    user, channel, _ = _require_channel(request, msg["channel_id"])
    if msg["author_id"] != user["id"] and not has_perm(user, channel["server_id"] or 0,
                                                       PERM_MANAGE_MESSAGES) \
            and not is_platform_owner(user):
        return _err("Missing permission.", 403)
    db.run("DELETE FROM messages WHERE id=?", (mid,))
    await realtime.broadcast_channel(channel["id"], "message.delete",
                                     {"id": mid, "channel": channel["id"]})
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
# uploads (resumable, up to 10 GB)
# --------------------------------------------------------------------------

async def api_upload_init(request):
    user = _need_user(request)
    data = await _body(request)
    filename = os.path.basename(str(data.get("filename") or "file.bin")).strip()[:180] or "file.bin"
    size = int(data.get("size") or 0)
    mime = str(data.get("mime") or "").strip()[:120] or \
        (mimetypes.guess_type(filename)[0] or "application/octet-stream")
    if size <= 0:
        return _err("File is empty.")
    if size > MAX_UPLOAD_BYTES:
        return _err("Max file size is 10 GB.")
    used = db.one("SELECT COALESCE(SUM(size),0) FROM attachments WHERE owner_id=?",
                  (user["id"],)) or 0
    if used + size > USER_UPLOAD_QUOTA:
        return _err("That would put you over your 50 GB storage quota.", 413)
    if _disk_free() - size < MIN_FREE_DISK:
        return _err("The server is low on disk space — try a smaller file.", 507)
    upload_id = secrets.token_hex(16)
    part = os.path.join(_root["uploads"], f"{upload_id}.part")
    with open(part, "wb") as fh:
        fh.truncate(size)
    db.run("INSERT INTO attachments(owner_id,filename,size,mime,path,created_at) "
           "VALUES(?,?,?,?,?,?)", (user["id"], filename, size, mime, upload_id, time.time()))
    # attachments row is created now so chunks can be verified by owner; it only
    # becomes visible to chat once /complete flips it to message_id>0 usage.
    return web.json_response({"upload_id": upload_id, "chunk_size": UPLOAD_CHUNK,
                              "received": 0})


async def api_upload_chunk(request):
    user = _need_user(request)
    upload_id = request.match_info["uid"]
    att = db.row("SELECT * FROM attachments WHERE path=? AND owner_id=?",
                 (upload_id, user["id"]))
    if not att:
        return _err("Unknown upload.", 404)
    part = os.path.join(_root["uploads"], f"{upload_id}.part")
    if not os.path.exists(part):
        return _err("Upload was cleaned up — start again.", 410)
    try:
        offset = int(request.query.get("offset") or 0)
    except Exception:
        return _err("Bad offset.")
    if offset < 0 or offset >= att["size"]:
        return _err("Bad offset.")
    blob = await request.read()
    if not blob:
        return _err("Empty chunk.")
    if offset + len(blob) > att["size"]:
        return _err("Chunk overruns the declared size.")

    def _write():
        with open(part, "r+b") as fh:
            fh.seek(offset)
            fh.write(blob)

    import asyncio
    await asyncio.to_thread(_write)
    received = int(request.query.get("received") or (offset + len(blob)))
    return web.json_response({"ok": True, "received": min(received, att["size"]),
                              "size": att["size"]})


async def api_upload_complete(request):
    user = _need_user(request)
    upload_id = request.match_info["uid"]
    att = db.row("SELECT * FROM attachments WHERE path=? AND owner_id=?",
                 (upload_id, user["id"]))
    if not att:
        return _err("Unknown upload.", 404)
    part = os.path.join(_root["uploads"], f"{upload_id}.part")
    final = os.path.join(_root["uploads"], upload_id)
    if not os.path.exists(part) or os.path.getsize(part) != att["size"]:
        if os.path.exists(final) and os.path.getsize(final) == att["size"]:
            # already completed by an earlier retry
            return web.json_response({"attachment": _attachment_payload(att),
                                      "url": f"/api/dm/files/{att['id']}/{att['filename']}"})
        return _err("Upload incomplete — send the missing chunks first.", 400)
    os.replace(part, final)               # .part -> final: no longer appendable
    return web.json_response({"attachment": _attachment_payload(att),
                              "url": f"/api/dm/files/{att['id']}/{att['filename']}"})


async def api_upload_abort(request):
    user = _need_user(request)
    upload_id = request.match_info["uid"]
    att = db.row("SELECT * FROM attachments WHERE path=? AND owner_id=?",
                 (upload_id, user["id"]))
    if att:
        db.run("DELETE FROM attachments WHERE id=?", (att["id"],))
        for suffix in (".part", ""):
            try:
                os.remove(os.path.join(_root["uploads"], f"{upload_id}{suffix}"))
            except OSError:
                pass
    return web.json_response({"ok": True})


async def api_serve_file(request):
    _need_user(request)
    att_id = int(request.match_info["aid"])
    att = db.row("SELECT * FROM attachments WHERE id=?", (att_id,))
    if not att:
        raise web.HTTPNotFound(text="not found")
    path = os.path.join(_root["uploads"], att["path"])
    if not os.path.exists(path):
        raise web.HTTPNotFound(text="file missing")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", att["filename"])[:120] or "file"
    return web.FileResponse(path, headers={
        "Content-Type": att["mime"],
        "Content-Disposition": f'inline; filename="{safe}"',
        "Cache-Control": "private, max-age=86400",
    })


async def api_serve_media(request):
    name = os.path.basename(request.match_info.get("name") or "")
    path = os.path.join(_root["media"], name)
    if not name or not os.path.exists(path):
        raise web.HTTPNotFound(text="not found")
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


# --------------------------------------------------------------------------
# badges + vanity (owner controlled) + admin
# --------------------------------------------------------------------------

async def api_list_badges(request):
    _need_user(request)
    return web.json_response({"badges": db.rows("SELECT * FROM badges ORDER BY id"),
                              "mine": request_user_badges(request)})


def request_user_badges(request):
    user = auth.user_from_request(request)
    return auth.badges_for(user["id"]) if user else []


async def api_create_badge(request):
    _need_owner(request)
    data = await _body(request)
    name = str(data.get("name") or "").strip()[:48]
    if not name:
        return _err("Badge needs a name.")
    slug = _slugify(str(data.get("slug") or name))
    icon = str(data.get("icon") or "")[:8]
    if not icon:
        icon = "⭐"
    try:
        bid = db.run(
            "INSERT INTO badges(slug,name,description,icon,color,created_at,created_by) "
            "VALUES(?,?,?,?,?,?,?)",
            (slug, name, str(data.get("description") or "")[:200], icon,
             str(data.get("color") or "#5865f2")[:20], time.time(),
             auth.user_from_request(request)["id"]))
    except Exception:
        return _err("A badge with that name/slug already exists.")
    return web.json_response({"badge": db.row("SELECT * FROM badges WHERE id=?", (bid,))})


async def api_delete_badge(request):
    _need_owner(request)
    bid = int(request.match_info["bid"])
    db.run("DELETE FROM user_badges WHERE badge_id=?", (bid,))
    db.run("DELETE FROM badges WHERE id=?", (bid,))
    return web.json_response({"ok": True})


async def api_grant_badge(request):
    owner = _need_owner(request)
    bid = int(request.match_info["bid"])
    data = await _body(request)
    target = auth.get_user(int(data.get("uid") or 0))
    if not target:
        return _err("User not found.", 404)
    if not db.row("SELECT 1 FROM badges WHERE id=?", (bid,)):
        return _err("Badge not found.", 404)
    db.run("INSERT OR IGNORE INTO user_badges(user_id,badge_id,granted_at,granted_by) "
           "VALUES(?,?,?,?)", (target["id"], bid, time.time(), owner["id"]))
    await realtime.send_to_user(target["id"], {"t": "event", "op": "badge.granted",
                                               "data": {"badges": auth.badges_for(target["id"])}})
    return web.json_response({"ok": True, "badges": auth.badges_for(target["id"])})


async def api_revoke_badge(request):
    _need_owner(request)
    bid = int(request.match_info["bid"])
    data = await _body(request)
    target = int(data.get("uid") or 0)
    db.run("DELETE FROM user_badges WHERE user_id=? AND badge_id=?", (target, bid))
    await realtime.send_to_user(target, {"t": "event", "op": "badge.granted",
                                         "data": {"badges": auth.badges_for(target)}})
    return web.json_response({"ok": True})


async def api_request_vanity(request):
    user = _need_user(request)
    data = await _body(request)
    sid = int(request.match_info["sid"])
    server = db.row("SELECT * FROM servers WHERE id=?", (sid,))
    if not server:
        return _err("Server not found.", 404)
    if not (server_perms(user, sid) & PERM_ADMIN):
        return _err("Only server admins can request a vanity link.", 403)
    slug = str(data.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug):
        return _err("2-32 chars: lowercase letters, numbers and dashes.")
    if db.row("SELECT 1 FROM servers WHERE vanity=?", (slug,)):
        return _err("That vanity link is already taken.", 409)
    pending = db.row("SELECT * FROM vanity_requests WHERE slug=? AND status='pending'", (slug,))
    if pending:
        return _err("That request is already waiting on the owner.")
    db.run("INSERT INTO vanity_requests(server_id,requester,slug,status,created_at) "
           "VALUES(?,?,?,?,?)", (sid, user["id"], slug, "pending", time.time()))
    return web.json_response({"ok": True,
                              "message": "Sent to the owner. You'll get a notification when it's decided."})


async def api_my_vanity_requests(request):
    user = _need_user(request)
    return web.json_response({"requests": db.rows(
        "SELECT v.*, s.name AS server_name FROM vanity_requests v "
        "JOIN servers s ON s.id=v.server_id "
        "WHERE v.requester=? OR s.owner_id=? ORDER BY v.created_at DESC LIMIT 50",
        (user["id"], user["id"]))})


async def api_admin_vanity(request):
    _need_owner(request)
    status = request.query.get("status") or "pending"
    return web.json_response({"requests": db.rows(
        "SELECT v.*, s.name AS server_name, u.username AS requester_name "
        "FROM vanity_requests v "
        "LEFT JOIN servers s ON s.id=v.server_id "
        "LEFT JOIN users u ON u.id=v.requester "
        "WHERE v.status=? ORDER BY v.created_at DESC LIMIT 100", (status,))})


async def api_admin_decide_vanity(request):
    owner = _need_owner(request)
    rid = int(request.match_info["rid"])
    req = db.row("SELECT * FROM vanity_requests WHERE id=?", (rid,))
    if not req:
        return _err("Request not found.", 404)
    data = await _body(request)
    action = data.get("action")
    if action not in ("approve", "deny"):
        return _err("action must be approve or deny.")
    slug = req["slug"]
    if action == "approve":
        override = str(data.get("slug") or "").strip().lower()
        if override and SLUG_RE.match(override):
            slug = override
        if db.row("SELECT 1 FROM servers WHERE vanity=? AND id!=?", (slug, req["server_id"])):
            return _err("That vanity is taken now.", 409)
        db.run("UPDATE servers SET vanity=? WHERE id=?", (slug, req["server_id"]))
        db.run("UPDATE vanity_requests SET status='approved', decided_by=?, decided_at=? "
               "WHERE id=?", (owner["id"], time.time(), rid))
        server = db.row("SELECT * FROM servers WHERE id=?", (req["server_id"],))
        await realtime.broadcast_server(req["server_id"], "server.update",
                                        {"server": _server_brief(server, owner)})
        await realtime.send_to_user(req["requester"], {
            "t": "event", "op": "vanity.decided",
            "data": {"status": "approved", "slug": slug, "server": server["name"]}})
    else:
        db.run("UPDATE vanity_requests SET status='denied', decided_by=?, decided_at=? "
               "WHERE id=?", (owner["id"], time.time(), rid))
        await realtime.send_to_user(req["requester"], {
            "t": "event", "op": "vanity.decided",
            "data": {"status": "denied", "slug": slug, "server": ""}})
    return web.json_response({"ok": True})


async def api_admin_users(request):
    _need_owner(request)
    q = (request.query.get("q") or "").strip().lower()
    if q:
        users = db.rows("SELECT * FROM users WHERE username_low LIKE ? OR lower(display_name) LIKE ? "
                        "OR email_lower LIKE ? OR discord_id=? LIMIT 50",
                        (f"%{q}%", f"%{q}%", f"%{q}%", q))
    else:
        users = db.rows("SELECT * FROM users ORDER BY id DESC LIMIT 50")
    return web.json_response({"users": [auth.public_user(u) for u in users]})


async def api_admin_patch_user(request):
    _need_owner(request)
    uid = int(request.match_info["uid"])
    target = auth.get_user(uid)
    if not target:
        return _err("User not found.", 404)
    data = await _body(request)
    if "banned" in data:
        banned = 1 if data.get("banned") else 0
        db.run("UPDATE users SET banned=? WHERE id=?", (banned, uid))
        if banned:
            auth.destroy_all_sessions(uid)
    if "is_owner" in data:
        db.run("UPDATE users SET is_owner=? WHERE id=?", (1 if data.get("is_owner") else 0, uid))
    if data.get("grant_badge"):
        badge = db.row("SELECT * FROM badges WHERE slug=? OR id=?",
                       (str(data["grant_badge"]), int(data.get("badge_id") or 0) or -1))
        if badge:
            db.run("INSERT OR IGNORE INTO user_badges(user_id,badge_id,granted_at,granted_by) "
                   "VALUES(?,?,?,?)", (uid, badge["id"], time.time(), 0))
            await realtime.send_to_user(uid, {"t": "event", "op": "badge.granted",
                                              "data": {"badges": auth.badges_for(uid)}})
    return web.json_response({"user": auth.public_user(auth.get_user(uid))})


async def api_admin_stats(request):
    _need_owner(request)
    return web.json_response({
        "users": db.one("SELECT COUNT(*) FROM users") or 0,
        "servers": db.one("SELECT COUNT(*) FROM servers") or 0,
        "messages": db.one("SELECT COUNT(*) FROM messages") or 0,
        "sessions": db.one("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (time.time(),)) or 0,
        "uploads_bytes": db.one("SELECT COALESCE(SUM(size),0) FROM attachments") or 0,
        "online": len(realtime.by_user),
        "calls": len(realtime.calls),
        "pending_vanity": db.one("SELECT COUNT(*) FROM vanity_requests WHERE status='pending'") or 0,
    })


async def api_calls(request):
    _need_user(request)
    return web.json_response({"calls": realtime.active_calls()})


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

ROUTES = [
    ("POST", "/api/dm/auth/register", api_register),
    ("POST", "/api/dm/auth/login", api_login),
    ("POST", "/api/dm/auth/logout", api_logout),
    ("GET", "/api/dm/me", api_me),
    ("POST", "/api/dm/me", api_update_me),
    ("POST", "/api/dm/me/password", api_change_password),
    ("GET", "/api/dm/auth/discord", api_discord_start),
    ("POST", "/api/dm/auth/discord/unlink", api_discord_unlink),

    ("GET", "/api/dm/bootstrap", api_bootstrap),
    ("GET", "/api/dm/directory", api_directory),
    ("GET", "/api/dm/users/{uid}", api_user_profile),

    ("POST", "/api/dm/servers", api_create_server),
    ("GET", "/api/dm/servers/{sid}", api_get_server),
    ("PATCH", "/api/dm/servers/{sid}", api_patch_server),
    ("DELETE", "/api/dm/servers/{sid}", api_delete_server),
    ("POST", "/api/dm/servers/{sid}/join", api_join_server),
    ("POST", "/api/dm/servers/{sid}/leave", api_leave_server),
    ("POST", "/api/dm/join", api_join_by_code),
    ("GET", "/api/dm/invite/{code}", api_invite_preview),

    ("POST", "/api/dm/servers/{sid}/channels", api_create_channel),
    ("PATCH", "/api/dm/channels/{cid}", api_patch_channel),
    ("DELETE", "/api/dm/channels/{cid}", api_delete_channel),

    ("POST", "/api/dm/servers/{sid}/roles", api_create_role),
    ("PATCH", "/api/dm/roles/{rid}", api_patch_role),
    ("DELETE", "/api/dm/roles/{rid}", api_delete_role),
    ("PUT", "/api/dm/servers/{sid}/members/{uid}/roles", api_set_member_roles),
    ("DELETE", "/api/dm/servers/{sid}/members/{uid}", api_kick_member),

    ("GET", "/api/dm/dms", api_list_dms),
    ("POST", "/api/dm/dms", api_create_dm),
    ("DELETE", "/api/dm/dms/{cid}", api_leave_dm),

    ("GET", "/api/dm/channels/{cid}/messages", api_get_messages),
    ("POST", "/api/dm/channels/{cid}/messages", api_send_message),
    ("PATCH", "/api/dm/messages/{mid}", api_edit_message),
    ("DELETE", "/api/dm/messages/{mid}", api_delete_message),

    ("POST", "/api/dm/uploads", api_upload_init),
    ("PUT", "/api/dm/uploads/{uid}", api_upload_chunk),
    ("POST", "/api/dm/uploads/{uid}/complete", api_upload_complete),
    ("DELETE", "/api/dm/uploads/{uid}", api_upload_abort),
    ("GET", "/api/dm/files/{aid}/{name}", api_serve_file),
    ("GET", "/api/dm/media/{name}", api_serve_media),

    ("GET", "/api/dm/badges", api_list_badges),
    ("POST", "/api/dm/badges", api_create_badge),
    ("DELETE", "/api/dm/badges/{bid}", api_delete_badge),
    ("POST", "/api/dm/badges/{bid}/grant", api_grant_badge),
    ("POST", "/api/dm/badges/{bid}/revoke", api_revoke_badge),

    ("POST", "/api/dm/servers/{sid}/vanity", api_request_vanity),
    ("GET", "/api/dm/vanity-requests", api_my_vanity_requests),
    ("GET", "/api/dm/admin/vanity", api_admin_vanity),
    ("POST", "/api/dm/admin/vanity/{rid}", api_admin_decide_vanity),
    ("GET", "/api/dm/admin/users", api_admin_users),
    ("POST", "/api/dm/admin/users/{uid}", api_admin_patch_user),
    ("GET", "/api/dm/admin/stats", api_admin_stats),

    ("GET", "/api/dm/calls", api_calls),
    ("GET", "/ws/dm", realtime.handle_ws),
]


def register(app) -> None:
    for method, path, handler in ROUTES:
        app.router.add_route(method, path, handler)
