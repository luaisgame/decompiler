"""Realtime layer for dm.luaisgame.com.

One WebSocket per browser tab (``/ws/dm``) carries chat events, presence,
typing, call state, WebRTC signaling and — as a fallback when peer-to-peer is
blocked by a NAT — the screenshare frames themselves.

Latency design:
  * Signaling/frames are relayed the instant they arrive; no batching, no acks.
  * If the client's socket write buffer is already backed up we drop the frame
    instead of queueing it (a stale frame is worse than a missing one).
  * Binary frame header is 3 bytes so per-frame overhead stays negligible.
  * WebRTC P2P bypasses the server entirely once negotiated, which is where the
    sub-80ms glass-to-glass number comes from.
"""

import asyncio
import json
import struct
import time

from aiohttp import WSMsgType, web

from . import auth, db

FRAME_MAGIC = 0x01          # binary message: magic | uint16 header len | header | payload
MAX_WRITE_BUFFER = 1 << 19  # 512 KB of unflushed socket data -> start dropping frames

clients: dict = {}          # ws -> {"uid", "user", "rooms": set, "meta": dict}
by_user: dict = {}          # uid -> set(ws)
calls: dict = {}            # room -> {"channel_id","kind","host","started_at","rooms_mode","members": set(uid)}

PING_INTERVAL = 20.0


# --------------------------------------------------------------------------
# connection lifecycle
# --------------------------------------------------------------------------

async def handle_ws(request):
    user = auth.user_from_request(request)
    if not user:
        return web.Response(status=401)
    auth.touch_presence(user["id"], "online")

    ws = web.WebSocketResponse(heartbeat=PING_INTERVAL, max_msg_size=8 * 1024 * 1024,
                               compress=False)
    await ws.prepare(request)

    meta = {"uid": user["id"], "user": auth.public_user(user), "rooms": set(),
            "last_ping": time.time()}
    clients[ws] = meta
    by_user.setdefault(user["id"], set()).add(ws)

    await ws.send_json({"t": "ready", "uid": user["id"],
                        "user": meta["user"], "server_time": time.time()})
    await _fanout_presence(user["id"], "online")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                try:
                    await _on_message(ws, meta, data)
                except Exception as e:
                    print(f"[DM WS] error handling {data.get('t') if isinstance(data, dict) else '?'}: {e}")
                    try:
                        await ws.send_json({"t": "error", "error": str(e)[:200]})
                    except Exception:
                        pass
            elif msg.type == WSMsgType.BINARY:
                await _on_binary(ws, meta, msg.data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                break
    finally:
        await _disconnect(ws, meta)
    return ws


async def _disconnect(ws, meta):
    clients.pop(ws, None)
    rooms = meta.get("rooms") or set()
    sockets = by_user.get(meta["uid"]) or set()
    sockets.discard(ws)
    if not sockets:
        by_user.pop(meta["uid"], None)
        await _fanout_presence(meta["uid"], "offline")
    for room in list(rooms):
        await _leave_room(ws, meta, room)


async def _fanout_presence(uid, presence):
    targets = set()
    for row in db.rows("SELECT server_id FROM server_members WHERE user_id=?", (uid,)):
        for m in db.rows("SELECT user_id FROM server_members WHERE server_id=? AND user_id!=?",
                         (row["server_id"], uid)):
            targets.add(m["user_id"])
    for row in db.rows("SELECT channel_id FROM channel_members WHERE user_id=?", (uid,)):
        for m in db.rows("SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?",
                         (row["channel_id"], uid)):
            targets.add(m["user_id"])
    for target in targets:
        await send_to_user(target, {"t": "event", "op": "presence.update",
                                    "data": {"uid": uid, "presence": presence,
                                             "last_seen": time.time()}})


# --------------------------------------------------------------------------
# outbound helpers
# --------------------------------------------------------------------------

async def send_to_user(uid, payload: dict) -> int:
    """Deliver an event to every live tab of a user. Returns # of sockets hit."""
    sent = 0
    for ws in list(by_user.get(int(uid), ())):
        try:
            await ws.send_json(payload)
            sent += 1
        except Exception:
            pass
    return sent


def channel_member_ids(channel_id: int) -> set:
    if channel_id is None:
        return set()
    chan = db.row("SELECT * FROM channels WHERE id=?", (channel_id,))
    if not chan:
        return set()
    if chan["server_id"]:
        return {m["user_id"] for m in db.rows(
            "SELECT user_id FROM server_members WHERE server_id=?", (chan["server_id"],))}
    return {m["user_id"] for m in db.rows(
        "SELECT user_id FROM channel_members WHERE channel_id=?", (channel_id,))}


async def broadcast_channel(channel_id, op: str, data: dict, exclude_ws=None) -> None:
    event = {"t": "event", "op": op, "data": data}
    for uid in channel_member_ids(channel_id):
        for ws in list(by_user.get(uid, ())):
            if ws is exclude_ws:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                pass


async def broadcast_server(server_id, op: str, data: dict, exclude_ws=None) -> None:
    event = {"t": "event", "op": op, "data": data}
    for row in db.rows("SELECT user_id FROM server_members WHERE server_id=?", (server_id,)):
        for ws in list(by_user.get(row["user_id"], ())):
            if ws is exclude_ws:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                pass


# --------------------------------------------------------------------------
# inbound messages
# --------------------------------------------------------------------------

async def _on_message(ws, meta, data):
    t = data.get("t")

    if t == "ping":
        meta["last_ping"] = time.time()
        await ws.send_json({"t": "pong", "ts": data.get("ts", 0), "server_time": time.time()})
        return

    if t == "typing":
        channel_id = int(data.get("channel") or 0)
        if channel_id in meta["rooms"] or channel_id in channel_member_ids(channel_id):
            await broadcast_channel(channel_id, "typing",
                                    {"channel": channel_id, "uid": meta["uid"],
                                     "username": meta["user"]["username"],
                                     "display_name": meta["user"]["display_name"]},
                                    exclude_ws=ws)
        return

    if t == "call.join":
        await _join_room(ws, meta, str(data.get("room") or ""))
        return

    if t == "call.leave":
        await _leave_room(ws, meta, str(data.get("room") or ""))
        return

    if t == "signal":
        # WebRTC relay: offer/answer/ICE between two viewers of the same call.
        room = str(data.get("room") or "")
        if room not in meta["rooms"]:
            return
        target = int(data.get("to") or 0)
        payload = {"t": "event", "op": "webrtc.signal",
                   "data": {"room": room, "from": meta["uid"],
                            "kind": data.get("kind"), "payload": data.get("payload")}}
        await send_to_user(target, payload)
        return

    if t == "call.mode":
        room = str(data.get("room") or "")
        if room in calls and calls[room]["host"] == meta["uid"]:
            calls[room]["mode"] = "p2p" if data.get("mode") == "p2p" else "relay"
            await _room_event(room, "call.update", _call_state(room), exclude_ws=ws)
        return

    if t == "call.host":
        # Whoever starts broadcasting takes the host seat (that is the identity
        # allowed to push relay frames); anyone in the room may claim it.
        room = str(data.get("room") or "")
        if room in meta["rooms"] and room in calls:
            calls[room]["host"] = meta["uid"]
            await _room_event(room, "call.update", _call_state(room), exclude_ws=ws)
            await ws.send_json({"t": "call.state", "call": _call_state(room),
                                "you": {"uid": meta["uid"], "is_host": True}})
        return


def _write_buffered(ws) -> int:
    """Bytes currently sitting in this socket's OS/TCP write buffer."""
    writer = getattr(ws, "_writer", None)
    transport = getattr(writer, "transport", None)
    if transport is None:
        transport = getattr(getattr(ws, "_req", None), "transport", None)
    if transport is None:
        return 0
    try:
        return int(transport.get_write_buffer_size() or 0)
    except Exception:
        return 0


async def _on_binary(ws, meta, blob: bytes):
    """Relay a screenshare frame to everyone else watching the same call."""
    if len(blob) < 3 or blob[0] != FRAME_MAGIC:
        return
    header_len = struct.unpack_from("!H", blob, 1)[0]
    if len(blob) < 3 + header_len:
        return
    try:
        header = json.loads(blob[3:3 + header_len].decode("utf-8"))
    except Exception:
        return
    room = str(header.get("room") or "")
    if room not in meta["rooms"] or calls.get(room, {}).get("host") != meta["uid"]:
        return

    # Never let one slow viewer stall the broadcaster: skip frames for sockets
    # that are already backed up (a stale frame is worse than a missing one).
    for other in list(clients):
        if other is ws or room not in clients[other]["rooms"]:
            continue
        if _write_buffered(other) > MAX_WRITE_BUFFER:
            continue
        try:
            await other.send_bytes(blob)
        except Exception:
            pass


# --------------------------------------------------------------------------
# call rooms
# --------------------------------------------------------------------------

def _call_state(room: str) -> dict:
    call = calls.get(room) or {}
    return {"room": room, "channel_id": call.get("channel_id"),
            "kind": call.get("kind"), "host": call.get("host"),
            "mode": call.get("mode", "p2p"), "started_at": call.get("started_at", 0),
            "members": sorted(call.get("members", set()))}


async def _room_event(room, op, data, exclude_ws=None):
    event = {"t": "event", "op": op, "data": data}
    for other, meta in list(clients.items()):
        if room in meta["rooms"] and other is not exclude_ws:
            try:
                await other.send_json(event)
            except Exception:
                pass


async def _join_room(ws, meta, room: str):
    if not room:
        return
    if not _may_join(meta, room):
        return
    meta["rooms"].add(room)
    call = calls.setdefault(room, {"channel_id": 0, "kind": "voice", "host": None,
                                   "started_at": time.time(), "mode": "p2p", "members": set()})
    call["members"].add(meta["uid"])
    if call["host"] is None:
        call["host"] = meta["uid"]
    await ws.send_json({"t": "call.state", "call": _call_state(room),
                        "you": {"uid": meta["uid"], "is_host": call["host"] == meta["uid"]}})
    await _room_event(room, "call.update", _call_state(room), exclude_ws=ws)


def _may_join(meta, room: str) -> bool:
    """room is either 'c<channel_id>' (server voice channel) or 'd<channel_id>' (DM call)."""
    try:
        channel_id = int(room[1:])
    except Exception:
        return False
    return meta["uid"] in channel_member_ids(channel_id)


async def _leave_room(ws, meta, room: str):
    if room not in meta["rooms"]:
        return
    meta["rooms"].discard(room)
    call = calls.get(room)
    if not call:
        return
    call["members"].discard(meta["uid"])
    if call["host"] == meta["uid"]:
        call["host"] = next(iter(call["members"]), None)
    if not call["members"]:
        calls.pop(room, None)
        await _room_event(room, "call.ended", {"room": room})
        return
    await _room_event(room, "call.update", _call_state(room))


def active_calls() -> list:
    return [_call_state(r) for r in list(calls)]


def reset() -> None:
    """Test/startup helper."""
    clients.clear()
    by_user.clear()
    calls.clear()
