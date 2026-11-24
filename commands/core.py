import asyncio
import base64
from urllib.parse import parse_qs, urlparse, quote, urlencode
import ctypes
import os
import sys
import io
import random
import string
import shutil
import uuid
import subprocess
import time
import json
import re
import zipfile
import hashlib
import struct
import gzip
import zlib
import secrets
import ipaddress
import aiohttp
import discord
import requests
import psutil
import boto3

from botocore.config import Config
from discord.ext import commands
from discord import app_commands
from aiohttp import web

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
elif "BOT_BASE_DIR" in os.environ:
    _BASE_DIR = os.environ["BOT_BASE_DIR"]
elif os.path.isfile(__file__) and not __file__.startswith("<"):
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
else:
    _BASE_DIR = os.getcwd()
load_dotenv(os.path.join(_BASE_DIR, ".env"))

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "")
R2_PUBLIC_DOMAIN = os.getenv("R2_PUBLIC_DOMAIN", "")
ROBLOX_COOKIE = os.getenv("ROBLOX_COOKIE", "")

BLACKLIST_ROLE_IDS = {}
for _g, _v in json.loads(os.getenv("BLACKLIST_ROLE_IDS", "{}")).items():
    BLACKLIST_ROLE_IDS[int(_g)] = [int(_v)] if isinstance(_v, int) else [int(x) for x in _v]

QUEUE_ROLE_IDS = {}
for _g, _v in json.loads(os.getenv("QUEUE_ROLE_IDS", "{}")).items():
    QUEUE_ROLE_IDS[int(_g)] = [int(_v)] if isinstance(_v, int) else [int(x) for x in _v]

BASE_DIR = _BASE_DIR

class LogBuffer:
    def __init__(self, max_lines=500):
        self.lines = []
        self.max_lines = max_lines
    def write(self, text):
        for line in text.rstrip("\n").split("\n"):
            if line.strip():
                ts = time.strftime("%H:%M:%S")
                self.lines.append(f"[{ts}] {line}")
        self.lines = self.lines[-self.max_lines:]
    def get(self, count=100):
        return "\n".join(self.lines[-count:])
    def clear(self):
        self.lines.clear()

py_log = LogBuffer()
tunnel_log = LogBuffer()

class TeeWriter:
    def __init__(self, original, buffer):
        self.original = original
        self.buffer = buffer
    def write(self, text):
        self.original.write(text)
        self.buffer.write(text)
    def flush(self):
        self.original.flush()

sys.stdout = TeeWriter(sys.stdout, py_log)
sys.stderr = TeeWriter(sys.stderr, py_log)

blacklistedtxt = os.path.join(BASE_DIR, "blacklistedgames.txt")
user_blacklisted_file = os.path.join(BASE_DIR, "blacklistedusers.txt")
server_blacklisted_file = os.path.join(BASE_DIR, "blacklistedservers.txt")
channels_file = os.path.join(BASE_DIR, "allowed_channels.txt")
cookies_file = os.path.join(BASE_DIR, "cookies.txt")
storage_dir = os.path.join(BASE_DIR, "storage")
# 10 GB uploads on dm.* need headroom, and the old 25 GB ceiling started
# evicting files far too quickly.
STORAGE_MAX_BYTES = 100 * 1024 * 1024 * 1024
banned_ips_file = os.path.join(BASE_DIR, "banned_ips.txt")
ips_file = os.path.join(BASE_DIR, "tracked_ips.json")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "1532820804402806844")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "xN_MmWZKUhswwN3jSe2XnCQKBnBviKti")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "https://storage.luaisgame.com/api/auth/callback")
# The MC console lives on its own hostname (mc.luaisgame.com) served by the same
# web server. Logging in from that host must use its own registered OAuth callback
# so the session cookie is issued for that domain.
DISCORD_REDIRECT_URI_MC = os.environ.get("DISCORD_REDIRECT_URI_MC", "https://mc.luaisgame.com/api/auth/callback")
STORAGE_SITE_URL = os.environ.get("STORAGE_SITE_URL", "https://storage.luaisgame.com/")
MC_SITE_URL = os.environ.get("MC_SITE_URL", "https://mc.luaisgame.com/")

def _request_is_mc_host(request) -> bool:
    """True when the request came in on the mc.* hostname."""
    host = (request.host or "").lower().split(":")[0]
    return host.startswith("mc.")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "1293889121374179328"))
DISCORD_REDIRECT_URI_DM = os.environ.get("DISCORD_REDIRECT_URI_DM",
                                         "https://dm.luaisgame.com/api/auth/callback")

# Sessions and OAuth states used to live in these dicts, which meant every
# restart signed everybody out and dropped half-finished logins. They now live
# in accounts.db (see commands/dm/auth.py) and survive restarts.
_auth_sessions = {}
_oauth_states = {}
MC_PERMISSIONS_FILE = os.path.join(BASE_DIR, "mc_permissions.json")

from .dm import auth as dm_auth            # noqa: E402
from .dm import api as dm_api              # noqa: E402
from .dm import web as dm_web              # noqa: E402


def _request_is_dm_host(request) -> bool:
    """True when the request came in on the dm.* hostname."""
    host = (request.host or "").lower().split(":")[0]
    return host.startswith("dm.")

def _cleanup_storage():
    try:
        os.makedirs(storage_dir, exist_ok=True)
        files = []
        total = 0
        for f in os.listdir(storage_dir):
            fp = os.path.join(storage_dir, f)
            if os.path.isfile(fp) and not f.endswith(".lock"):
                size = os.path.getsize(fp)
                files.append((os.path.getmtime(fp), size, fp))
                total += size
        files.sort(key=lambda x: x[0])
        while total > STORAGE_MAX_BYTES and files:
            _, size, old_file = files.pop(0)
            os.remove(old_file)
            total -= size
            print(f"[STORAGE] Removed oldest: {os.path.basename(old_file)} ({size} bytes)")
    except Exception as e:
        print(f"[STORAGE] Cleanup error: {e}")

active_cookie_index = 0
default_cookie_index = 0

def _dpapi_protect(data_bytes: bytes) -> bytes | None:
    import ctypes, ctypes.wintypes
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data_bytes, len(data_bytes))
    blob_in = DATA_BLOB(len(data_bytes), buf)
    blob_out = DATA_BLOB()
    if crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        kernel32.LocalFree(blob_out.pbData)
        return result
    return None

def _dpapi_unprotect(data_bytes: bytes) -> bytes | None:
    import ctypes, ctypes.wintypes
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data_bytes, len(data_bytes))
    blob_in = DATA_BLOB(len(data_bytes), buf)
    blob_out = DATA_BLOB()
    if crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        kernel32.LocalFree(blob_out.pbData)
        return result
    return None

def _get_roblox_cookie_file() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "LocalStorage", "RobloxCookies.dat")

def _get_roblox_cookie_file_alt() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "LocalStorage", "_RobloxCookies.dat")

def _read_roblox_cookies() -> str | None:
    fpath = _get_roblox_cookie_file()
    if not os.path.exists(fpath):
        print(f"[COOKIE] _read_roblox_cookies: file not found at {fpath}")
        return None
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
        encrypted = base64.b64decode(data["CookiesData"])
        decrypted = _dpapi_unprotect(encrypted)
        return decrypted.decode("utf-8", errors="replace") if decrypted else None
    except Exception as e:
        print(f"[COOKIE] _read_roblox_cookies error: {e}")
        return None

def _write_roblox_cookies(cookie_text: str) -> bool:
    import ctypes, ctypes.wintypes
    fpath = _get_roblox_cookie_file()
    try:
        protected = _dpapi_protect(cookie_text.encode("utf-8"))
        if not protected:
            return False
        data = {"CookiesVersion": "1", "CookiesData": base64.b64encode(protected).decode()}
        with open(fpath, "w") as f:
            json.dump(data, f)
        return True
    except Exception:
        return False

def _replace_roblox_security_cookie(new_cookie_value: str) -> bool:
    cookie_text = _read_roblox_cookies()
    if not cookie_text:
        print(f"[COOKIE] _replace_roblox_security_cookie: _read_roblox_cookies() returned empty")
        return False
    lines = cookie_text.split("\n")
    new_lines = []
    replaced = False
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 6 and parts[5] == ".ROBLOSECURITY":
            parts[6] = new_cookie_value if new_cookie_value.startswith("_|") else f"_{{}}{new_cookie_value}"
            new_lines.append("\t".join(parts))
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        print(f"[COOKIE] _replace_roblox_security_cookie: no .ROBLOSECURITY line found in cookie file")
        return False
    result = _write_roblox_cookies("\n".join(new_lines))
    print(f"[COOKIE] _replace_roblox_security_cookie: _write_roblox_cookies returned {result}")
    alt_path = _get_roblox_cookie_file_alt()
    if os.path.exists(alt_path):
        try:
            with open(alt_path, "r") as f:
                alt_data = json.load(f)
            alt_encrypted = base64.b64decode(alt_data["CookiesData"])
            alt_decrypted = _dpapi_unprotect(alt_encrypted)
            if alt_decrypted:
                alt_text = alt_decrypted.decode("utf-8", errors="replace")
                alt_lines = alt_text.split("\n")
                alt_new_lines = []
                for line in alt_lines:
                    parts = line.split("\t")
                    if len(parts) >= 6 and parts[5] == ".ROBLOSECURITY":
                        parts[6] = new_cookie_value if new_cookie_value.startswith("_|") else f"_{{}}{new_cookie_value}"
                        alt_new_lines.append("\t".join(parts))
                    else:
                        alt_new_lines.append(line)
                protected = _dpapi_protect("\n".join(alt_new_lines).encode("utf-8"))
                if protected:
                    with open(alt_path, "w") as f:
                        json.dump({"CookiesVersion": "1", "CookiesData": base64.b64encode(protected).decode()}, f)
                    print(f"[COOKIE] Also updated _RobloxCookies.dat")
        except Exception as e:
            print(f"[COOKIE] Failed to update _RobloxCookies.dat: {e}")
    else:
        print(f"[COOKIE] _RobloxCookies.dat not found at {alt_path}")
    return result

def load_cookies() -> list[str]:
    cookies = []
    if os.path.exists(cookies_file):
        with open(cookies_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    cookies.append(line)
    return cookies

def save_cookies(cookies: list[str]):
    with open(cookies_file, "w") as f:
        for c in cookies:
            f.write(f"{c}\n")

def get_active_cookie() -> str:
    global active_cookie_index
    cookies = load_cookies()
    if not cookies:
        return ROBLOX_COOKIE
    if active_cookie_index >= len(cookies):
        active_cookie_index = 0
    return cookies[active_cookie_index]

def set_active_cookie(index: int) -> bool:
    global active_cookie_index
    cookies = load_cookies()
    if 0 <= index < len(cookies):
        active_cookie_index = index
        return True
    return False

def switch_to_default_cookie():
    global active_cookie_index
    cookies = load_cookies()
    if cookies:
        active_cookie_index = 0
        _replace_roblox_security_cookie(cookies[0])
        print(f"[COOKIE] Switched to default cookie (index 0)")

def save_and_switch_to_default():
    global active_cookie_index
    saved = active_cookie_index
    switch_to_default_cookie()
    return saved

decompile_event = asyncio.Event()
decompile_recieved = asyncio.Event()
post_data = {}

user_decompile_history = {}
is_decompiling = False
running_jobs = 0
active_events = None
current_active_data = None
_on_status_update = None

decompile_disabled = False

def set_decompile_disabled(state: bool):
    global decompile_disabled
    decompile_disabled = state

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

LOCAL_APP_DATA = os.environ.get("LOCALAPPDATA", "")
ROAMING_APP_DATA = os.environ.get("APPDATA", "")
WORKSPACE_NAME = os.getenv("WORKSPACE_NAME", "Volt")
WORKSPACE_DIR = os.path.join(LOCAL_APP_DATA, WORKSPACE_NAME, "workspace")
SKIP_PROCESSFILE = os.getenv("SKIP_PROCESSFILE", "0").lower() in ("1", "true", "yes")

ONE_MB = 1024 * 1024
MAX_LIMIT = 200 * ONE_MB

API_BASE = os.getenv("API_BASE", "https://luaisgame.com/api/owner")
OWNER_KEY = os.getenv("OWNER_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "1293889121374179328"))
ORACLE_KEY = os.getenv("ORACLE_KEY", "")

async def user_has_role(user: discord.User | discord.Member, role_ids: dict) -> bool:
    user_id = user.id
    for guild_id, target_role_id in role_ids.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        targets = target_role_id if isinstance(target_role_id, list) else [target_role_id]
        member = guild.get_member(user_id)
        if member and any(role.id in targets for role in member.roles):
            return True
        try:
            member = await guild.fetch_member(user_id)
        except (discord.HTTPException, discord.NotFound):
            continue
        if member and any(role.id in targets for role in member.roles):
            return True
    return False

queue_list = []
queue_counter = 0
_queue_event = None

def load_queue():
    old_file = os.path.join(BASE_DIR, "queue.txt")
    if os.path.exists(old_file):
        try:
            os.remove(old_file)
        except Exception:
            pass

def _get_queue_event():
    global _queue_event
    if _queue_event is None:
        _queue_event = asyncio.Event()
    return _queue_event

class QueueItem:
    def __init__(self, is_priority: bool, counter: int, task_data: dict):
        self.is_priority = is_priority
        self.counter = counter
        self.task_data = task_data

async def _renumber_queue(notify_bumps: bool = False):
    for index, it in enumerate(queue_list, start=1):
        data = it.task_data
        old_pos = data.get("last_pos")
        data["last_pos"] = index
        if notify_bumps and old_pos is not None and index > old_pos:
            try:
                await send_msg(
                    data["send_func"],
                    f"<@{data['author_id']}> Your spot in line has been moved to queue position **{index}**.",
                    ephemeral=data["is_ephemeral"]
                )
            except Exception as e:
                print(f"[DEBUG] Failed to notify user of queue shift: {e}")

async def enqueue_decompile_task(send_func, user: discord.User | discord.Member, 
guild, channel, place_id: str, game_id: str, is_ephemeral: bool, on_status_update=None, user_cookie: str = None, raw: bool = False):
    global queue_counter, current_active_data
    queue_counter += 1
    author_id = user.id
    is_priority = await user_has_role(user, QUEUE_ROLE_IDS)
    item = QueueItem(
        is_priority=is_priority,
        counter=queue_counter,
        task_data={
            "send_func": send_func,
            "author_id": author_id,
            "guild": guild,
            "channel": channel,
            "place_id": place_id,
            "game_id": game_id,
            "is_ephemeral": is_ephemeral,
            "on_status_update": on_status_update,
            "user_cookie": user_cookie,
            "raw": raw,
            "future": asyncio.get_running_loop().create_future(),
            "last_pos": None
        }
    )
    if is_decompiling or len(queue_list) > 0:
        if is_priority and is_decompiling and len(queue_list) == 0 and current_active_data and not current_active_data.get("is_priority") and not current_active_data.get("aborted"):
            print("[DEBUG] Priority preemption: pausing active non-priority job.")
            current_active_data["aborted"] = True
            _terminate_live_roblox()
            await update_status(current_active_data["info_msg"], current_active_data["embed"], "paused")
            queue_counter += 1
            paused_item = QueueItem(
                is_priority=current_active_data.get("is_priority", False),
                counter=queue_counter,
                task_data={
                    **current_active_data,
                    "aborted": False,
                    "joined": False,
                    "resume_info_msg": current_active_data["info_msg"],
                    "resume_embed": current_active_data["embed"],
                    "future": asyncio.get_running_loop().create_future(),
                    "last_pos": None,
                }
            )
            queue_list.insert(0, item)
            insert_at = len(queue_list)
            for i, it in enumerate(queue_list):
                if not it.is_priority:
                    insert_at = i
                    break
            queue_list.insert(insert_at, paused_item)
            await _renumber_queue(notify_bumps=True)
            await send_msg(send_func, f"<@{author_id}> Priority granted! Your decompile is starting now (a lower-priority job was paused).", ephemeral=is_ephemeral)
        elif is_priority:
            insert_at = len(queue_list)
            for i, it in enumerate(queue_list):
                if not it.is_priority:
                    insert_at = i
                    break
            queue_list.insert(insert_at, item)
            await _renumber_queue(notify_bumps=True)
            pos = item.task_data["last_pos"]
            await send_msg(send_func, f"<@{author_id}> Added to Priority Queue at position **{pos}**.", ephemeral=is_ephemeral)
        else:
            queue_list.append(item)
            await _renumber_queue(notify_bumps=True)
            pos = item.task_data["last_pos"]
            await send_msg(send_func, f"<@{author_id}> Added to Queue at position **{pos}**.", ephemeral=is_ephemeral)
    else:
        queue_list.append(item)
    _get_queue_event().set()
    await item.task_data["future"]

async def decompile_queue_worker():
    while True:
        if not queue_list:
            await _get_queue_event().wait()
            _get_queue_event().clear()
            continue
        item = queue_list.pop(0)
        await _renumber_queue(notify_bumps=False)
        data = item.task_data
        try:
            await execute_decompile_job(
                data["send_func"],
                data["author_id"],
                data["guild"],
                data["channel"],
                data["place_id"],
                data["game_id"],
                data["is_ephemeral"],
                is_priority=item.is_priority,
                resume_info_msg=data.get("resume_info_msg"),
                resume_embed=data.get("resume_embed"),
                on_status_update=data.get("on_status_update"),
                user_cookie=data.get("user_cookie"),
                raw=data.get("raw", False)
            )
        except Exception as e:
            print(f"[DEBUG] Queue worker error processing task: {e}")
        finally:
            if not data["future"].done():
                data["future"].set_result(True)

def load_blacklisted_users() -> dict:
    blacklisted = {}
    now = time.time()
    if os.path.exists(user_blacklisted_file):
        with open(user_blacklisted_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and "," in line:
                    try:
                        uid, expire_time = line.split(",", 1)
                        uid = int(uid.strip())
                        expire_time = float(expire_time.strip())
                        if expire_time > now:
                            blacklisted[uid] = expire_time
                    except ValueError:
                        continue
        save_blacklisted_users(blacklisted)
    return blacklisted

def save_blacklisted_users(blacklisted: dict):
    with open(user_blacklisted_file, "w") as f:
        for uid, expire_time in blacklisted.items():
            f.write(f"{uid},{expire_time}\n")

def add_user_to_blacklist(user_id: int, duration_seconds: int = 300):
    blacklisted = load_blacklisted_users()
    expire_time = time.time() + duration_seconds
    blacklisted[user_id] = expire_time
    save_blacklisted_users(blacklisted)

def parse_user_id(raw: str) -> int | None:
    raw = raw.strip()
    if raw.startswith("<@") and raw.endswith(">"):
        raw = raw[2:-1]
        if raw.startswith("!"):
            raw = raw[1:]
    if raw.isdigit():
        return int(raw)
    return None

def parse_duration(raw: str) -> int | None:
    raw = raw.strip().lower()
    multipliers = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
        'w': 604800,
        'mo': 2592000,
        'y': 31536000,
    }
    if raw.isdigit():
        return int(raw) * 60
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if raw.endswith(suffix):
            num_part = raw[:-len(suffix)]
            if num_part.isdigit():
                return int(num_part) * mult
    return None

def load_blacklisted_servers() -> set:
    blacklisted = set()
    if os.path.exists(server_blacklisted_file):
        with open(server_blacklisted_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    blacklisted.add(int(line))
    return blacklisted

def save_blacklisted_servers(blacklisted: set):
    with open(server_blacklisted_file, "w") as f:
        for sid in blacklisted:
            f.write(f"{sid}\n")

def add_server_to_blacklist(server_id: int):
    blacklisted = load_blacklisted_servers()
    blacklisted.add(server_id)
    save_blacklisted_servers(blacklisted)

def remove_server_from_blacklist(server_id: int) -> bool:
    blacklisted = load_blacklisted_servers()
    if server_id in blacklisted:
        blacklisted.remove(server_id)
        save_blacklisted_servers(blacklisted)
        return True
    return False

def load_blacklisted_games() -> dict:
    blacklisted = {}
    if os.path.exists(blacklistedtxt):
        with open(blacklistedtxt, "r") as f:
            content = f.read().strip()
            if content:
                items = content.replace("\n", ",").split(",")
                for item in items:
                    item = item.strip()
                    if item:
                        if ":" in item:
                            pid, reason = item.split(":", 1)
                            blacklisted[pid.strip()] = reason.strip()
                        else:
                            blacklisted[item] = "No Reason Provided"
    return blacklisted

def save_blacklisted_games(blacklisted: dict):
    with open(blacklistedtxt, "w") as f:
        lines = [f"{pid}:{reason}" for pid, reason in blacklisted.items()]
        f.write("\n".join(lines))

def load_allowed_channels() -> dict:
    channels = {}
    if os.path.exists(channels_file):
        with open(channels_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and ":" in line:
                    guild_id, channel_id = line.split(":", 1)
                    channels[int(guild_id)] = int(channel_id)
    return channels

def save_allowed_channel(guild_id: int, channel_id: int):
    channels = load_allowed_channels()
    channels[guild_id] = channel_id
    with open(channels_file, "w") as f:
        for g_id, c_id in channels.items():
            f.write(f"{g_id}:{c_id}\n")

class ChannelSelect(discord.ui.Select):
    def __init__(self, channels: list[discord.abc.GuildChannel]):
        options = [
            discord.SelectOption(
                label=f"#{ch.name}",
                value=str(ch.id),
                description=f"Type: {ch.type.name.capitalize()} | ID: {ch.id}"
            ) for ch in channels[:25]
        ]
        super().__init__(
            placeholder="Select a channel or forum for decompile commands...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = int(self.values[0])
        save_allowed_channel(interaction.guild.id, selected_id)
        embed = discord.Embed(
            title="Channel Configured",
            description=f"Successfully set <#{selected_id}> as the official decompile channel.",
            color=0x2ECC71
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)

class ChannelSelectView(discord.ui.View):
    def __init__(self, channels: list[discord.abc.GuildChannel], author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.add_item(ChannelSelect(channels))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return True

class CookieInputModal(discord.ui.Modal, title="Enter Your Cookie"):
    cookie = discord.ui.TextInput(
        label=".ROBLOSECURITY Cookie",
        placeholder="_|WARNING:...",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000
    )

    def __init__(self, view: "CookieBannedView"):
        super().__init__()
        self.banned_view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_cookie = self.cookie.value.strip()
        v = self.banned_view
        result = await validate_cookie(user_cookie)
        if not result.get("valid"):
            await interaction.followup.send(f"Invalid cookie: {result.get('reason', 'Unknown error')}", ephemeral=True)
            return
        for child in v.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=v)
        except Exception:
            pass
        try:
            await interaction.followup.send("Cookie accepted. Queuing decompile...", ephemeral=True)
        except Exception:
            pass
        try:
            user = await v.guild.fetch_member(v.author_id)
            await user.send(f"Using your cookie ({result.get('username')}) for decompilation.")
        except Exception:
            pass
        await enqueue_decompile_task(
            v.send_func, interaction.user, v.guild, v.channel, v.place_id, v.game_id,
            v.is_ephemeral, on_status_update=v.on_status_update, user_cookie=user_cookie
        )

class CookieBannedView(discord.ui.View):
    def __init__(self, send_func, author_id, guild, channel, place_id, game_id, is_ephemeral, is_priority, on_status_update):
        super().__init__(timeout=120)
        self.send_func = send_func
        self.author_id = author_id
        self.guild = guild
        self.channel = channel
        self.place_id = place_id
        self.game_id = game_id
        self.is_ephemeral = is_ephemeral
        self.is_priority = is_priority
        self.on_status_update = on_status_update

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This button is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CookieInputModal(self))

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger)
    async def ignore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

# Discord only routes a button click back to a view it still knows about. Views
# sent in this process are remembered by message id, but anything sent before a
# restart is gone — so interactive decompile buttons carry a fixed custom_id and
# the state they need (the join URL) is mirrored to disk.
JOIN_URLS_FILE = os.path.join(BASE_DIR, "join_urls.json")
_join_urls: dict = {}


def _load_join_urls() -> None:
    global _join_urls
    try:
        with open(JOIN_URLS_FILE, "r") as f:
            data = json.load(f)
        _join_urls = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        _join_urls = {}


def _save_join_urls() -> None:
    try:
        with open(JOIN_URLS_FILE, "w") as f:
            json.dump(_join_urls, f)
    except Exception as e:
        print(f"[DECOMPILE] Could not persist join urls: {e}")


def remember_join_url(message_id, join_url: str) -> None:
    if not message_id or not join_url:
        return
    _join_urls[str(message_id)] = join_url
    if len(_join_urls) > 1000:            # keep the file bounded
        for stale in list(_join_urls)[:-1000]:
            _join_urls.pop(stale, None)
    _save_join_urls()


def lookup_join_url(message_id) -> str:
    return _join_urls.get(str(message_id or ""), "")


_load_join_urls()


class JoinGameView(discord.ui.View):
    def __init__(self, join_url: str = None, message_id: int = None):
        super().__init__(timeout=None)
        self.join_url = join_url
        self.message_id = message_id
        btn = discord.ui.Button(
            label="Bot has not joined yet" if not join_url else "Copy Join URL",
            style=discord.ButtonStyle.secondary,
            disabled=not join_url,
            custom_id="decompile:join_url",   # stable -> survives restarts
        )
        btn.callback = self._join_callback
        self.add_item(btn)
        self.join_button = btn
        chrome = discord.ui.Button(
            label="Chrome Extension",
            url="https://chromewebstore.google.com/detail/roblox-jobid-join/pdeebkpgdaflejgihpbniammmelkdnac",
            style=discord.ButtonStyle.link,
            emoji="🌐"
        )
        self.add_item(chrome)
        firefox = discord.ui.Button(
            label="Firefox Extension",
            url="https://addons.mozilla.org/en-US/firefox/addon/roblox-jobid-join/",
            style=discord.ButtonStyle.link,
            emoji="🦊"
        )
        self.add_item(firefox)

    async def _join_callback(self, interaction: discord.Interaction):
        # After a restart the live view is a freshly registered template, so the
        # URL always comes from the persisted map keyed by this message id.
        url = lookup_join_url(getattr(interaction.message, "id", None)) or self.join_url
        if url:
            await interaction.response.send_message(f"**Join URL (copy this):**\n```\n{url}\n```", ephemeral=True)
        else:
            await interaction.response.send_message("Bot has not joined the game yet.", ephemeral=True)

    def set_join_url(self, join_url: str):
        self.join_url = join_url
        self.join_button.label = "Copy Join URL"
        self.join_button.disabled = False
        if self.message_id:
            remember_join_url(self.message_id, join_url)

class DownloadView(discord.ui.View):
    def __init__(self, download_url: str, pack_id: str = None):
        super().__init__(timeout=None)
        button = discord.ui.Button(label="Download", url=download_url, emoji="⬇️")
        self.add_item(button)
        if pack_id:
            try:
                from .assets import add_assets_button
                add_assets_button(self, pack_id)
            except Exception as e:
                print(f"[ASSETS] Could not add assets button: {e}")

def generate_random_filename() -> str:
    rand_bytes = os.urandom(16)
    return base64.b32encode(rand_bytes).decode().rstrip("=").lower()

async def get_best_join_url(place_id: str, game_id: str = None) -> str:
    if game_id:
        return f"roblox://experiences/start?placeId={place_id}&gameInstanceId={game_id}"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession(headers=headers) as session:
            servers_url = f"https://games.roblox.com/v1/games/{place_id}/servers/Public?sortOrder=Asc&limit=100"
            async with session.get(servers_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    servers = data.get("data", [])
                    best = None
                    best_heartbeat = ""
                    best_ping = float("inf")
                    for srv in servers:
                        if not srv.get("id"):
                            continue
                        heartbeat = srv.get("lastHeartbeat", "")
                        ping = srv.get("ping", float("inf"))
                        if heartbeat > best_heartbeat or (heartbeat == best_heartbeat and ping < best_ping):
                            best = srv
                            best_heartbeat = heartbeat
                            best_ping = ping
                    if best:
                        print(f"[DEBUG] Best server: id={best['id']} heartbeat={best_heartbeat} ping={best_ping}")
                        return f"roblox://experiences/start?placeId={place_id}&gameInstanceId={best['id']}"
    except Exception as e:
        print(f"[DEBUG] Failed to get best join URL: {e}")
    return f"roblox://experiences/start?placeId={place_id}"

async def build_setup_dropdown(guild: discord.Guild, author_id: int):
    allowed_types = (discord.TextChannel, discord.ForumChannel)
    valid_channels = [
        ch for ch in guild.channels
        if isinstance(ch, allowed_types) and ch.permissions_for(guild.me).send_messages
    ]
    if not valid_channels:
        embed = discord.Embed(
            title="Setup Error",
            description="I don't have access to send messages in any channels or forums in this server.",
            color=0xE74C3C
        )
        return embed, None
    embed = discord.Embed(
        title="Select Decompile Channel",
        description="Please select the text channel or forum from the dropdown below where you want decompile commands to be locked to.",
        color=0x3498DB
    )
    view = ChannelSelectView(valid_channels, author_id)
    return embed, view

async def update_bot_presence(game_name: str, text: str = None):
    activity = discord.Activity(
        type=discord.ActivityType.playing,
        name=game_name,
        details=f"Decompiling: {game_name}",
        state=f"{text or 'Launching Roblox'}",
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

async def reset_bot_presence():
    activity = discord.Activity(
        type=discord.ActivityType.playing,
        name="Roblox Experiences",
        details="Idle / Ready",
        state="Fastest Roblox Decompiler"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

_place_info_cache = {}

async def get_place_info(place_id: str, cookie: str = None) -> dict:
    place_id = str(place_id).strip()
    if place_id in _place_info_cache:
        return _place_info_cache[place_id]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    use_cookie = cookie or get_active_cookie()
    if use_cookie:
        headers["Cookie"] = f".ROBLOSECURITY={use_cookie}"
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                universe_url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
                async with session.get(universe_url) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 2))
                        print(f"[DEBUG] Rate limited, retrying in {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status in (403, 404):
                        result = {"error": True, "reason": "Place is banned, content-deleted, or moderated by Roblox."}
                        _place_info_cache[place_id] = result
                        return result
                    if resp.status != 200:
                        result = {"error": True, "reason": f"Roblox API returned status code {resp.status}."}
                        _place_info_cache[place_id] = result
                        return result
                    data = await resp.json()
                    if not isinstance(data, dict):
                        result = {"error": True, "reason": "Invalid response from universe resolution API."}
                        _place_info_cache[place_id] = result
                        return result
                    universe_id = data.get("universeId")
                if not universe_id:
                    result = {"error": True, "reason": "Universe ID not found for this place."}
                    _place_info_cache[place_id] = result
                    return result

                game_name = f"Place {place_id}"
                details_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
                async with session.get(details_url) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 2))
                        print(f"[DEBUG] Rate limited, retrying in {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 200:
                        details_data = await resp.json()
                        if isinstance(details_data, dict):
                            games = details_data.get("data", [])
                            if games and isinstance(games, list):
                                game = games[0] or {}
                                if game.get("isArchived", False):
                                    result = {"error": True, "reason": "Game has been archived or deleted."}
                                    _place_info_cache[place_id] = result
                                    return result
                                game_name = game.get("name", game_name)

                # Fallback: the games API can silently return nothing (rate limit, 403,
                # cookie issues). Without this, embeds would show "Place <id>" instead of
                # the real game name, so try Roblox's dedicated place-name endpoint.
                if game_name == f"Place {place_id}":
                    try:
                        name_url = f"https://apis.roblox.com/search-api/place-name?placeIds={place_id}"
                        async with session.get(name_url) as name_resp:
                            if name_resp.status == 200:
                                name_data = await name_resp.json()
                                if isinstance(name_data, list) and name_data and isinstance(name_data[0], dict):
                                    fetched_name = name_data[0].get("name")
                                    if isinstance(fetched_name, str) and fetched_name.strip():
                                        game_name = fetched_name.strip()
                                        print(f"[DEBUG] Resolved game name via place-name API: {game_name}")
                    except Exception as e:
                        print(f"[DEBUG] place-name fallback error: {e}")

                icon_url = None
                thumb_url = f"https://thumbnails.roblox.com/v1/places/gameicons?placeIds={place_id}&size=512x512&format=Png&isCircular=false"
                async with session.get(thumb_url) as thumb_resp:
                    if thumb_resp.status == 429:
                        retry_after = float(thumb_resp.headers.get("Retry-After", 2))
                        print(f"[DEBUG] Rate limited, retrying in {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue
                    if thumb_resp.status == 200:
                        thumb_data = await thumb_resp.json()
                        if isinstance(thumb_data, dict):
                            data_list = thumb_data.get("data", [])
                            if data_list and isinstance(data_list, list) and len(data_list) > 0:
                                first = data_list[0]
                                if isinstance(first, dict):
                                    icon_url = first.get("imageUrl")

                if use_cookie:
                    play_url = f"https://games.roblox.com/v1/games/multiget-playability-status?universeIds={universe_id}"
                    async with session.get(play_url) as play_resp:
                        if play_resp.status == 429:
                            retry_after = float(play_resp.headers.get("Retry-After", 2))
                            print(f"[DEBUG] Rate limited, retrying in {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        if play_resp.status == 200:
                            play_data = await play_resp.json()
                            if isinstance(play_data, list) and len(play_data) > 0:
                                status_info = play_data[0] or {}
                                is_playable = status_info.get("isPlayable", True)
                                unplayable_text = status_info.get("unplayableDisplayText", "")
                                if not is_playable:
                                    body_text = None
                                    ux_treatment = status_info.get("playableUxTreatment")
                                    if isinstance(ux_treatment, dict):
                                        ux_data = ux_treatment.get("data")
                                        if isinstance(ux_data, dict):
                                            body_text = ux_data.get("bodyText")
                                    ban_reason = body_text or unplayable_text or "UNKNOWN"
                                    return {
                                        "error": True,
                                        "reason": f"Game is unplayable: {ban_reason}",
                                        "name": game_name,
                                        "icon_url": icon_url
                                    }

                result = {"name": game_name, "icon_url": icon_url}
                _place_info_cache[place_id] = result
                return result
        except Exception as e:
            print(f"[DEBUG] get_place_info error: {e}")
            if attempt == 2:
                result = {"error": True, "reason": f"Connection error: {str(e)}"}
                _place_info_cache[place_id] = result
                return result
            await asyncio.sleep(1)
    result = {"error": True, "reason": "Failed to get place info."}
    _place_info_cache[place_id] = result
    return result

async def get_current_user_id() -> int | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        cookie = get_active_cookie()
        if cookie:
            headers["Cookie"] = f".ROBLOSECURITY={cookie}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id")
    except Exception:
        pass
    return None

async def validate_cookie(cookie: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Cookie": f".ROBLOSECURITY={cookie}",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://users.roblox.com/v1/users/authenticated") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"valid": True, "username": data.get("name"), "user_id": data.get("id")}
                elif resp.status == 401:
                    return {"valid": False, "reason": "Cookie is expired or invalid."}
                elif resp.status == 403:
                    return {"valid": False, "reason": "Account is banned or requires verification."}
                else:
                    return {"valid": False, "reason": f"Unexpected status code {resp.status}."}
    except Exception as e:
        return {"valid": False, "reason": f"Connection error: {str(e)}"}

async def is_user_banned_from_game(user_id: int, place_id: str) -> bool:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        cookie = get_active_cookie()
        if cookie:
            headers["Cookie"] = f".ROBLOSECURITY={cookie}"
        async with aiohttp.ClientSession(headers=headers) as session:
            url = f"https://games.roblox.com/v1/games/{place_id}/users/{user_id}/status"
            async with session.get(url) as resp:
                if resp.status == 403:
                    return True
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("isBanned"):
                        return True
    except Exception:
        pass
    return False

async def _safe_json(resp):
    try:
        return await resp.json()
    except Exception:
        return {}

async def resolve_place_or_universe(raw_id: str, cookie: str = None) -> str:
    """Return a joinable place id for a numeric id.

    Accepts a place id (returned unchanged) or a universe id, which is
    resolved to the universe's root place — so private experiences can be
    decompiled straight from their universe id as well as their place id.
    Never raises; on any doubt the original id is returned so the normal
    error path handles it."""
    raw_id = str(raw_id).strip()
    if not raw_id.isdigit():
        return raw_id
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    use_cookie = cookie or get_active_cookie()
    if use_cookie:
        headers["Cookie"] = f".ROBLOSECURITY={use_cookie}"
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # a valid place id answers this with a real universe id
            # (a universe id passed here answers 200 with universeId: null!)
            async with session.get(
                f"https://apis.roblox.com/universes/v1/places/{raw_id}/universe",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await _safe_json(resp)
                    if data.get("universeId"):
                        return raw_id
            # otherwise: maybe it is a universe id -> its root place
            async with session.get(
                f"https://games.roblox.com/v1/games?universeIds={raw_id}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await _safe_json(resp)
                    games = (data or {}).get("data") or []
                    if games and isinstance(games, list) and isinstance(games[0], dict):
                        root = str(games[0].get("rootPlaceId") or "").strip()
                        if root.isdigit():
                            print(f"[DEBUG] Universe {raw_id} -> root place {root}")
                            return root
    except Exception as e:
        print(f"[DEBUG] resolve_place_or_universe({raw_id}) error: {e}")
    return raw_id

async def resolve_game_by_name(name: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    if get_active_cookie():
        headers["Cookie"] = f".ROBLOSECURITY={get_active_cookie()}"

    def _extract(data):
        if not isinstance(data, dict):
            return None, None
        results = data.get("searchResults")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                contents = item.get("contents")
                game = contents[0] if isinstance(contents, list) and contents else (item.get("game") or item)
                if isinstance(game, dict):
                    pid = game.get("rootPlaceId") or game.get("placeId")
                    if pid:
                        return str(pid), game.get("name")
        items = data.get("data")
        if isinstance(items, list):
            for item in items:
                pid = item.get("rootPlaceId") or item.get("placeId") or item.get("id")
                if pid:
                    return str(pid), item.get("name")
        return None, None

    async with aiohttp.ClientSession(headers=headers) as session:
        session_id = str(uuid.uuid4())
        url = (
            f"https://apis.roblox.com/search-api/omni-search"
            f"?searchQuery={quote(name)}&pageToken=eyJzdGFydCI6MCwiY291bnQiOjQwLCJlbmRPZlBhZ2UiOmZhbHNlfQ==&sessionId={session_id}&pageType=all"
        )
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    place_id, game_name = _extract(await _safe_json(resp))
                    if place_id:
                        return {"place_id": place_id, "name": game_name or name}
        except Exception as e:
            print(f"[DEBUG] Omni search error: {e}")

        url2 = f"https://games.roblox.com/v1/games/list?keyword={quote(name)}&sort=1&limit=10"
        try:
            async with session.get(url2) as resp:
                if resp.status == 200:
                    place_id, game_name = _extract(await _safe_json(resp))
                    if place_id:
                        return {"place_id": place_id, "name": game_name or name}
        except Exception as e:
            print(f"[DEBUG] Games list search error: {e}")

    return None

async def handle_post(request):
    global post_data
    if active_events is None:
        print("[DEBUG] Received POST request but no game is currently joining/decompiling. Returning 404.")
        return web.Response(text="No active decompile session found", status=404)
    try:
        if request.content_type == "application/json":
            payload = await request.json()
        else:
            payload = {"raw": await request.text()}
        # Merge instead of replace: the client sends several messages per job
        # (joined / assets / finished) and keys like game_version must survive.
        post_data = {**(post_data if isinstance(post_data, dict) else {}), **payload}
        print(f"[DEBUG] Received POST request: {post_data}")
        if post_data.get("status") == "assets":
            try:
                from .assets import on_client_manifest
                on_client_manifest(post_data)
            except Exception as e:
                print(f"[ASSETS] Could not handle client manifest: {e}")
        rec_ev, fin_ev = active_events
        if post_data.get("status") == "finished":
            fin_ev.set()
        else:
            rec_ev.set()
        return web.Response(text="Success", status=200)
    except Exception as e:
        print(f"[DEBUG] POST handle error: {e}")
        return web.Response(text=str(e), status=400)

def _track_download(ip, filename):
    dl_file = os.path.join(BASE_DIR, "downloads.json")
    entries = []
    if os.path.exists(dl_file):
        with open(dl_file, "r") as f:
            entries = json.load(f)
    entries.insert(0, {"ip": ip, "filename": filename, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    entries = entries[:500]
    with open(dl_file, "w") as f:
        json.dump(entries, f, indent=2)

async def handle_download(request):
    filename = request.match_info.get("filename", "")
    # Catch-all over storage/: never expose bookkeeping files (tickets.json,
    # downloads.json, tracked_ips.json, lock/partial files) by guessing names.
    lowered = filename.lower()
    if ("/" in filename or "\\" in filename or filename.startswith(".")
            or lowered.endswith((".json", ".txt", ".lock", ".part"))
            or not filename):
        return web.Response(text="Not found", status=404)
    filepath = os.path.join(storage_dir, filename)
    if not os.path.isfile(filepath):
        return web.Response(text="Not found", status=404)
    ip = request.headers.get("X-Forwarded-For", request.remote)
    _track_download(ip, filename)
    return web.FileResponse(filepath, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

def _read_games_json() -> list:
    """storage/games.json, BOM-tolerant. A stray BOM used to 500 the homepage."""
    games_json = os.path.join(storage_dir, "games.json")
    if not os.path.isfile(games_json):
        return []
    try:
        with open(games_json, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[STORAGE] Could not read games.json: {e}")
        return []


def _write_games_json(entries):
    games_json = os.path.join(storage_dir, "games.json")
    # explicit utf-8 (never the locale codepage) and never a BOM
    with open(games_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(entries, f, indent=2, ensure_ascii=True)


async def handle_games_json(request):
    return web.json_response(_read_games_json())

def _get_admin_password():
    return ""

def _get_banned_ips():
    if not os.path.exists(banned_ips_file):
        return set()
    with open(banned_ips_file, "r") as f:
        return {line.strip() for line in f if line.strip()}

def _ban_ip(ip):
    banned = _get_banned_ips()
    banned.add(ip)
    with open(banned_ips_file, "w") as f:
        for i in banned:
            f.write(f"{i}\n")

def _unban_ip(ip):
    banned = _get_banned_ips()
    banned.discard(ip)
    with open(banned_ips_file, "w") as f:
        for i in banned:
            f.write(f"{i}\n")

def _track_ip(ip):
    entries = []
    if os.path.exists(ips_file):
        with open(ips_file, "r") as f:
            entries = json.load(f)
    for e in entries:
        if e.get("ip") == ip:
            e["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            e["visits"] = e.get("visits", 0) + 1
            with open(ips_file, "w") as f:
                json.dump(entries, f, indent=2)
            return
    entries.append({"ip": ip, "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"), "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"), "visits": 1})
    with open(ips_file, "w") as f:
        json.dump(entries, f, indent=2)

def _safe_next_url(value) -> str:
    """Only allow same-site relative redirects (blocks open-redirect abuse)."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return ""


async def handle_discord_auth(request):
    # One OAuth entry point for every hostname: the redirect URI (and therefore
    # the domain the session cookie lands on) is chosen from the host header.
    next_url = _safe_next_url(request.query.get("next", ""))
    resp = web.Response(status=302)
    resp.headers["Location"] = dm_auth.discord_auth_url(request, next_url or "/")
    return resp


async def handle_discord_callback(request):
    code = request.query.get("code", "")
    state = request.query.get("state", "")
    state_data = dm_auth.consume_oauth_state(state)
    if not code or not state_data:
        return web.Response(text="Invalid or expired Discord login.", status=400)

    redirect_uri = state_data.get("redirect") or dm_auth.redirect_for_host(
        dm_auth.request_host(request))
    dest = _safe_next_url(state_data.get("next") or "") or "/"
    link_user = int(state_data.get("link_user") or 0)

    user_data = await dm_auth.exchange_discord_code(code, redirect_uri)
    if not user_data:
        return web.Response(text="Discord authentication failed.", status=401)

    if link_user:
        ok, message = dm_auth.link_discord(link_user, user_data)
        if not ok:
            return web.Response(text=message, status=400)
        user = dm_auth.get_user(link_user)
    else:
        user = dm_auth.login_or_link_discord(user_data)
    if not user:
        return web.Response(text="Could not read Discord account.", status=401)
    if user.get("banned"):
        return web.Response(text="This account is banned.", status=403)

    token = dm_auth.create_session(user["id"], request.remote or "",
                                   request.headers.get("User-Agent", ""))
    resp = web.Response(status=302)
    resp.headers["Location"] = dest
    dm_auth.set_session_cookie(resp, token, dm_auth.request_host(request))
    return resp


async def handle_discord_verify(request):
    return web.json_response({"error": "legacy authentication endpoint"}, status=410)

async def handle_auth_me(request):
    user = _get_user_info(request)
    if not user:
        return web.json_response({"authenticated": False}, status=401)
    user_id = str(user.get("id", ""))
    acl = _mc_load_acl()
    admin = _is_mc_admin(request)
    return web.json_response({
        "authenticated": True,
        "mc_authorized": _is_mc_authorized(request),
        "mc_admin": admin,
        "mc_owner": user_id == str(BOT_OWNER_ID),
        "mc_create": admin or user_id in acl["create"],
        **user,
    })

def _get_user_info(request):
    """Signed-in user for any luaisgame.com host, backed by accounts.db.

    ``id`` stays the Discord snowflake (or "0") so every existing storage/mc
    permission check keeps working; ``uid`` is the internal account id.
    """
    return dm_auth.request_user_public(request)

def _is_admin(request):
    user = _get_user_info(request)
    if not user:
        return False
    return int(user.get("id", 0)) == BOT_OWNER_ID

# ---------------------------------------------------------------------------
# Minecraft console permissions
#
# mc_permissions.json is a small ACL:
#   {
#     "version": 2,
#     "admins": ["discord-id"],          # global: every server + access grants
#     "create": ["discord-id"],          # global: may create servers
#     "servers": {
#       "survival": {
#         "owner": "discord-id",         # full access to this server
#         "users": {"discord-id": ["start", "console", ...]}
#       }
#     }
#   }
#
# The old layout ({server: [user_id, ...]}) is migrated on read: every listed
# user keeps the full access they had before.
# ---------------------------------------------------------------------------
MC_PRIVILEGES = (
    "start", "stop", "console", "command", "files", "mods",
    "backup", "version", "players", "icon", "delete", "access",
)
MC_GLOBAL_PRIVILEGES = ("create", "admin")
MC_PERMISSION_LABELS = {
    "start": "Start server",
    "stop": "Stop server",
    "console": "View console",
    "command": "Send commands",
    "files": "Files & folders",
    "mods": "Mods",
    "backup": "Backups",
    "version": "Change version",
    "players": "Players & bans",
    "icon": "Server icon",
    "delete": "Delete server",
    "access": "Manage access",
    "create": "Create servers",
    "admin": "Global admin",
}

def _mc_normalize_user_id(value):
    value = str(value or "").strip()
    return value if value.isdigit() else ""

def _mc_normalize_privileges(values):
    if not isinstance(values, (list, tuple, set)):
        return []
    given = {str(value).strip().lower() for value in values}
    return [privilege for privilege in MC_PRIVILEGES if privilege in given]

def _mc_empty_acl():
    return {"admins": [], "create": [], "servers": {}}

def _mc_acl_from_raw(raw):
    acl = _mc_empty_acl()
    if not isinstance(raw, dict):
        return acl

    admins = set()
    create = set()
    if isinstance(raw.get("admins"), list):
        admins = {user_id for user_id in (_mc_normalize_user_id(value) for value in raw["admins"]) if user_id}
    if isinstance(raw.get("create"), list):
        create = {user_id for user_id in (_mc_normalize_user_id(value) for value in raw["create"]) if user_id}

    servers = {}
    raw_servers = raw.get("servers")
    if isinstance(raw_servers, dict):
        for name, entry in raw_servers.items():
            if not isinstance(entry, dict):
                continue
            users = {}
            raw_users = entry.get("users")
            if isinstance(raw_users, dict):
                for user_id, privileges in raw_users.items():
                    user_id = _mc_normalize_user_id(user_id)
                    if user_id:
                        users[user_id] = _mc_normalize_privileges(privileges)
            servers[str(name)] = {
                "owner": _mc_normalize_user_id(entry.get("owner", "")),
                "users": users,
            }

    # Legacy per-server lists: everyone listed had unrestricted access.
    for name, value in raw.items():
        if name in ("version", "admins", "create", "servers") or not isinstance(value, list):
            continue
        server = servers.setdefault(str(name), {"owner": "", "users": {}})
        for raw_user_id in value:
            user_id = _mc_normalize_user_id(raw_user_id)
            if user_id:
                server["users"].setdefault(user_id, list(MC_PRIVILEGES))

    acl["admins"] = sorted(admins)
    acl["create"] = sorted(create)
    acl["servers"] = servers
    return acl

def _mc_load_acl():
    if not os.path.exists(MC_PERMISSIONS_FILE):
        return _mc_empty_acl()
    try:
        with open(MC_PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            return _mc_acl_from_raw(json.load(f))
    except Exception as e:
        print(f"[MINECRAFT] Could not read permissions: {e}")
        return _mc_empty_acl()

def _mc_save_acl(acl):
    data = {
        "version": 2,
        "admins": sorted(set(acl.get("admins", []))),
        "create": sorted(set(acl.get("create", []))),
        "servers": {},
    }
    for name, entry in (acl.get("servers") or {}).items():
        users = {}
        for user_id, privileges in (entry.get("users") or {}).items():
            user_id = _mc_normalize_user_id(user_id)
            if user_id:
                users[user_id] = _mc_normalize_privileges(privileges)
        data["servers"][str(name)] = {
            "owner": _mc_normalize_user_id(entry.get("owner", "")),
            "users": users,
        }
    temp_path = MC_PERMISSIONS_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, MC_PERMISSIONS_FILE)

def _mc_user_id(request):
    user = _get_user_info(request)
    return str(user.get("id", "")) if user else ""

def _is_mc_owner(request):
    """Only the bot owner may view/control this PC (screencast)."""
    return _mc_user_id(request) == str(BOT_OWNER_ID)

def _is_mc_admin(request):
    user_id = _mc_user_id(request)
    if not user_id:
        return False
    if user_id == str(BOT_OWNER_ID):
        return True
    return user_id in _mc_load_acl()["admins"]

def _mc_has_any_access(user_id, acl=None):
    user_id = str(user_id or "")
    if not user_id:
        return False
    if user_id == str(BOT_OWNER_ID):
        return True
    acl = acl or _mc_load_acl()
    if user_id in acl["admins"] or user_id in acl["create"]:
        return True
    for server in acl["servers"].values():
        if user_id == server.get("owner") or user_id in server.get("users", {}):
            return True
    return False

def _mc_can_create(request):
    user_id = _mc_user_id(request)
    if not user_id:
        return False
    if user_id == str(BOT_OWNER_ID):
        return True
    acl = _mc_load_acl()
    return user_id in acl["admins"] or user_id in acl["create"]

def _mc_server_privileges(request, server_name, acl=None):
    user_id = _mc_user_id(request)
    if not user_id:
        return frozenset()
    if user_id == str(BOT_OWNER_ID):
        return frozenset(MC_PRIVILEGES)
    acl = acl or _mc_load_acl()
    if user_id in acl["admins"]:
        return frozenset(MC_PRIVILEGES)
    server = acl["servers"].get(str(server_name)) or {}
    if user_id == server.get("owner"):
        return frozenset(MC_PRIVILEGES)
    return frozenset(server.get("users", {}).get(user_id, []))

def _mc_can(request, server_name, privilege, acl=None):
    return privilege in _mc_server_privileges(request, server_name, acl)

def _can_access_mc_server(request, server_name):
    return bool(_mc_server_privileges(request, server_name))

def _is_mc_authorized(request):
    user_id = _mc_user_id(request)
    if not user_id:
        return False
    acl = _mc_load_acl()
    if user_id == str(BOT_OWNER_ID) or user_id in acl["admins"] or user_id in acl["create"]:
        return True
    return _mc_has_any_access(user_id, acl)

async def handle_admin_data(request):
    if not _is_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    ips = []
    if os.path.exists(ips_file):
        with open(ips_file, "r") as f:
            ips = json.load(f)
    banned = list(_get_banned_ips())
    downloads = []
    dl_file = os.path.join(BASE_DIR, "downloads.json")
    if os.path.exists(dl_file):
        with open(dl_file, "r") as f:
            downloads = json.load(f)
    return web.json_response({"ips": ips, "banned": banned, "downloads": downloads})

async def handle_ban(request):
    if not _is_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    ip = data.get("ip", "")
    action = data.get("action", "ban")
    if action == "ban":
        _ban_ip(ip)
    else:
        _unban_ip(ip)
    return web.json_response({"ok": True})

async def handle_logs(request):
    if not _is_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    log_type = request.query.get("type", "py")
    count = int(request.query.get("count", "100"))
    if log_type == "tunnel":
        return web.json_response({"logs": tunnel_log.get(count)})
    return web.json_response({"logs": py_log.get(count)})

async def handle_thumbnail(request):
    place_id = request.query.get("placeId", "")
    if not place_id or not place_id.isdigit():
        return web.Response(text="", status=400)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe") as r:
                if r.status != 200:
                    return web.Response(text="", status=404)
                udata = await r.json()
                universe_id = udata.get("universeId")
            if not universe_id:
                return web.Response(text="", status=404)
            async with session.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={universe_id}&size=420x420&format=Png&isCircular=false") as r:
                tdata = await r.json()
                data_list = tdata.get("data", [])
                thumb_url = ""
                if data_list and isinstance(data_list, list) and len(data_list) > 0:
                    first = data_list[0]
                    if isinstance(first, dict):
                        thumb_url = first.get("imageUrl", "")
            if thumb_url:
                async with session.get(thumb_url) as img:
                    return web.Response(body=await img.read(), content_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        print(f"[DEBUG] Thumbnail error: {e}")
    return web.Response(text="", status=404)

async def handle_index(request):
    # dm.luaisgame.com is the chat platform: same app, different HTML shell.
    if _request_is_dm_host(request):
        return await dm_web.index_page(request)
    # mc.luaisgame.com is served by this same app: its root shows the MC console.
    if _request_is_mc_host(request):
        return _mc_page_response(request)
    ip = request.headers.get("X-Forwarded-For", request.remote)
    _track_ip(ip)
    if ip in _get_banned_ips():
        return web.Response(text="Access denied.", status=403)
    admin = _is_admin(request)
    mc_authorized = _is_mc_authorized(request)
    user_info = _get_user_info(request)
    entries = _read_games_json()
    games_html = ""
    for e in entries:
        games_html += f'''<div class="game-card" data-name="{e.get("game_name","").lower()}" data-user="{e.get("display_name","").lower()}">
            <div class="game-card-inner">
                <img class="game-thumb" src="{e.get('icon_url') or ''}" alt="thumb" onerror="this.style.display='none'">
                <div class="game-info">
                    <a class="game-title" href="https://www.roblox.com/games/{e.get("place_id","")}" target="_blank">{e.get("game_name","Unknown")}</a>
                    <div class="game-meta">
                        <span class="label">Place ID:</span> <span class="value">{e.get("place_id","")}</span>
                        <span class="label">Version:</span> <span class="value">{e.get("game_version","N/A")}</span>
                        <span class="label">Requested by:</span> <span class="value">{e.get("display_name","Unknown")} ({e.get("user_id","")})</span>
                        <span class="label">Downloaded:</span> <span class="value">{e.get("timestamp","")}</span>
                    </div>
                    <div class="game-buttons">
                        <a class="download-btn" href="/{e.get("filename","")}" download>Download</a>
                        <button class="copy-btn" data-url="https://storage.luaisgame.com/{e.get("filename","")}">Copy Link</button>
                        {f'<a class="assets-btn" href="/assets/{e.get("assets_pack")}">🎞 Assets</a>' if e.get("assets_pack") else ''}
                    </div>
                </div>
            </div>
        </div>'''
    admin_block = ""
    if admin:
        admin_block = '''
        <div class="admin-panel" id="adminPanel" style="display:none">
            <div class="admin-header">
                <span class="admin-title">Console</span>
                <div class="admin-tabs">
                    <button class="tab active" data-tab="ips">IPs</button>
                    <button class="tab" data-tab="banips">Ban IPs</button>
                    <button class="tab" data-tab="pyconsole">Python Console</button>
                    <button class="tab" data-tab="tunnelconsole">Tunnel Console</button>
                </div>
            </div>
            <div class="tab-content" id="tab-ips">
                <div class="admin-section">
                    <h3>Tracked IPs</h3>
                    <div id="ipList" class="ip-list"></div>
                </div>
            </div>
            <div class="tab-content hidden" id="tab-banips">
                <div class="admin-section">
                    <h3>Banned IPs</h3>
                    <div id="banList" class="ip-list"></div>
                    <div class="ban-form">
                        <input type="text" id="banIpInput" placeholder="IP to ban/unban">
                        <button class="btn-ban" id="banBtn">Ban</button>
                        <button class="btn-unban" id="unbanBtn">Unban</button>
                    </div>
                </div>
            </div>
            <div class="tab-content hidden" id="tab-pyconsole">
                <div class="admin-section">
                    <h3>Python Console (Live)</h3>
                    <div id="pyLog" class="console-log"></div>
                </div>
            </div>
            <div class="tab-content hidden" id="tab-tunnelconsole">
                <div class="admin-section">
                    <h3>Tunnel Console (Live)</h3>
                    <div id="tunnelLog" class="console-log"></div>
                </div>
            </div>
        </div>'''
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lua is game</title>
<link rel="icon" href="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png" type="image/png">
<meta property="og:type" content="website">
<meta property="og:title" content="Lua is game">
<meta property="og:description" content="Decompiled Roblox games archive. Download and browse decompiled game files.">
<meta property="og:image" content="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png">
<meta property="og:url" content="https://storage.luaisgame.com">
<meta name="theme-color" content="#0d1117">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#050507; --bg-elev:rgba(18,21,28,.72); --bg-elev-2:rgba(24,28,36,.6);
  --line:rgba(255,255,255,.07); --line-soft:rgba(255,255,255,.045);
  --txt:#e6edf3; --txt-dim:#8b949e; --txt-faint:#5a626d;
  --accent:#00dc78; --accent-soft:rgba(0,220,120,.12); --accent-line:rgba(0,220,120,.28);
  --blue:#58a6ff; --radius:14px; --radius-sm:9px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  background:var(--bg); color:var(--txt);
  font-family:'Inter','SF Pro Display',system-ui,-apple-system,sans-serif;
  min-height:100vh; padding-bottom:70px; position:relative;
  -webkit-font-smoothing:antialiased; letter-spacing:-0.01em;
}}
.bg-grid {{ position:fixed; inset:0; z-index:0; pointer-events:none; opacity:.5;
  background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);
  background-size:52px 52px;
  mask-image:radial-gradient(ellipse 90% 60% at 50% 0%,#000 40%,transparent 100%);
  -webkit-mask-image:radial-gradient(ellipse 90% 60% at 50% 0%,#000 40%,transparent 100%); }}
.bg-glow {{ position:fixed; width:720px; height:720px; border-radius:50%; z-index:0; pointer-events:none;
  background:radial-gradient(circle,rgba(0,220,120,.055),transparent 68%);
  top:-260px; left:50%; transform:translateX(-50%); animation:pulse 9s ease-in-out infinite alternate; }}
@keyframes pulse {{ 0%{{opacity:.65;transform:translateX(-50%) scale(1)}} 100%{{opacity:1;transform:translateX(-50%) scale(1.12)}} }}

/* ---- header ---- */
.header {{ position:sticky; top:0; z-index:100; display:flex; align-items:center; justify-content:space-between; gap:20px;
  padding:14px 32px; background:rgba(8,9,13,.72); border-bottom:1px solid var(--line);
  backdrop-filter:blur(18px) saturate(160%); -webkit-backdrop-filter:blur(18px) saturate(160%); }}
.header-left {{ display:flex; align-items:center; gap:22px; min-width:0; }}
.header-right {{ display:flex; align-items:center; gap:10px; flex-shrink:0; }}
.logo {{ font-size:21px; font-weight:700; letter-spacing:-0.6px; display:flex; align-items:center; gap:10px; white-space:nowrap; }}
.roblox-icon {{ width:28px; height:28px; border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,.4); }}
.lua {{ color:var(--blue); }} .is {{ color:var(--txt-faint); font-weight:500; }} .game {{ color:var(--accent); }}
.search {{ background:rgba(255,255,255,.035); border:1px solid var(--line); color:var(--txt);
  padding:10px 16px 10px 38px; border-radius:10px; width:300px; font-size:13.5px; outline:none;
  transition:border-color .18s, box-shadow .18s, background .18s;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='%238b949e' stroke-width='2.2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m21 21-4.3-4.3'/%3E%3C/svg%3E");
  background-repeat:no-repeat; background-position:13px center; }}
.search::placeholder {{ color:var(--txt-faint); }}
.search:focus {{ border-color:var(--accent-line); background-color:rgba(0,220,120,.05); box-shadow:0 0 0 3px rgba(0,220,120,.08); }}

/* ---- buttons ---- */
.btn {{ padding:9px 17px; border-radius:var(--radius-sm); border:1px solid transparent; font-size:13px; font-weight:600;
  cursor:pointer; transition:transform .16s, filter .16s, background .18s, border-color .18s; text-decoration:none;
  display:inline-flex; align-items:center; gap:7px; line-height:1; white-space:nowrap; }}
.btn:hover {{ transform:translateY(-1px); }}
.btn:active {{ transform:translateY(0) scale(.985); }}
.btn-primary {{ background:var(--accent); color:#04140b; box-shadow:0 2px 14px rgba(0,220,120,.22); }}
.btn-primary:hover {{ filter:brightness(1.08); }}
.btn-secondary {{ background:rgba(255,255,255,.04); color:var(--txt); border-color:var(--line); }}
.btn-secondary:hover {{ background:rgba(255,255,255,.075); border-color:rgba(255,255,255,.14); }}
.btn-discord {{ background:rgba(88,101,242,.16); color:#c3cbff; border-color:rgba(88,101,242,.32); }}
.btn-discord:hover {{ background:rgba(88,101,242,.26); border-color:rgba(88,101,242,.5); }}
.btn-minecraft {{ background:rgba(0,220,120,.1); color:var(--accent); border-color:var(--accent-line); }}
.btn-minecraft:hover {{ background:rgba(0,220,120,.18); }}
.btn svg {{ flex-shrink:0; }}

/* ---- layout ---- */
.container {{ max-width:1160px; margin:0 auto; padding:30px 24px; position:relative; z-index:1; }}
.count {{ color:var(--txt-dim); font-size:13px; margin-bottom:18px; display:flex; align-items:center; gap:8px; }}
.count::before {{ content:""; width:7px; height:7px; border-radius:50%; background:var(--accent); box-shadow:0 0 9px var(--accent); }}

/* ---- game cards ---- */
.game-card {{ display:flex; gap:0; background:var(--bg-elev); border:1px solid var(--line); border-radius:var(--radius);
  margin-bottom:14px; overflow:hidden; transition:transform .22s, border-color .22s, box-shadow .22s;
  backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px); }}
.game-card:hover {{ border-color:var(--accent-line); transform:translateY(-3px); box-shadow:0 14px 40px rgba(0,0,0,.45); }}
.game-card-inner {{ display:flex; gap:0; width:100%; }}
.game-thumb {{ width:148px; height:148px; object-fit:cover; flex-shrink:0; background:#0d1117; }}
.game-info {{ padding:18px 22px; flex:1; display:flex; flex-direction:column; justify-content:center; min-width:0; }}
.game-title {{ font-size:19px; font-weight:650; color:#f0f6fc; margin-bottom:12px; text-decoration:none;
  display:inline-block; transition:color .18s; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:100%; }}
.game-title:hover {{ color:var(--accent); }}
.game-meta {{ font-size:12.5px; color:var(--txt-dim); margin-bottom:16px; line-height:1.9; }}
.label {{ color:var(--accent); font-weight:500; }}
.value {{ color:var(--txt); margin-right:16px; }}
.game-buttons {{ display:flex; gap:9px; }}
.download-btn {{ display:inline-flex; align-items:center; gap:7px; background:linear-gradient(135deg,#238636,#2ea043); color:#fff;
  padding:9px 18px; border-radius:var(--radius-sm); text-decoration:none; font-size:13px; font-weight:600;
  transition:transform .16s, box-shadow .2s; box-shadow:0 2px 12px rgba(35,134,54,.28); }}
.download-btn:hover {{ transform:translateY(-1px); box-shadow:0 6px 20px rgba(35,134,54,.42); }}
.copy-btn {{ background:rgba(255,255,255,.04); color:var(--txt); border:1px solid var(--line); padding:9px 18px;
  border-radius:var(--radius-sm); font-size:13px; font-weight:600; cursor:pointer; transition:all .16s; }}
.copy-btn:hover {{ background:rgba(255,255,255,.075); border-color:var(--accent-line); color:var(--accent); }}
.assets-btn {{ display:inline-flex; align-items:center; gap:7px; background:rgba(88,101,242,.14); color:#c3cbff;
  border:1px solid rgba(88,101,242,.34); padding:9px 18px; border-radius:var(--radius-sm); font-size:13px;
  font-weight:600; text-decoration:none; transition:all .16s; }}
.assets-btn:hover {{ background:rgba(88,101,242,.26); border-color:rgba(88,101,242,.55); transform:translateY(-1px); }}

/* ---- footer ---- */
.footer {{ text-align:center; padding:22px; color:var(--txt-faint); font-size:12.5px; border-top:1px solid var(--line-soft);
  position:fixed; bottom:0; left:0; right:0; background:rgba(5,5,7,.82); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); z-index:50; }}
.footer a {{ color:var(--blue); text-decoration:none; }}
.footer a:hover {{ text-decoration:underline; }}

/* ---- admin console ---- */
.admin-panel {{ background:var(--bg-elev); border:1px solid var(--line); border-radius:var(--radius); padding:20px;
  margin-bottom:24px; backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); }}
.admin-header {{ display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:16px; flex-wrap:wrap; }}
.admin-title {{ font-size:16px; font-weight:650; color:#f0f6fc; display:flex; align-items:center; gap:8px; }}
.admin-title::before {{ content:""; width:8px; height:8px; border-radius:50%; background:#f85149; box-shadow:0 0 8px #f85149; }}
.admin-tabs {{ display:flex; gap:7px; flex-wrap:wrap; }}
.tab {{ padding:7px 14px; border-radius:8px; border:1px solid var(--line); background:rgba(255,255,255,.02);
  color:var(--txt-dim); cursor:pointer; font-size:12.5px; font-weight:500; transition:all .16s; }}
.tab.active {{ background:var(--accent-soft); color:var(--accent); border-color:var(--accent-line); }}
.tab:hover:not(.active) {{ border-color:rgba(255,255,255,.16); color:var(--txt); }}
.tab-content.hidden {{ display:none; }}
.admin-section h3 {{ font-size:13px; color:var(--txt-dim); margin-bottom:12px; font-weight:500; text-transform:uppercase; letter-spacing:.5px; }}
.ip-list {{ max-height:300px; overflow-y:auto; }}
.ip-item {{ display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:rgba(255,255,255,.02);
  border:1px solid var(--line-soft); border-radius:var(--radius-sm); margin-bottom:7px; font-size:13px; }}
.ip-item .ip {{ color:var(--accent); font-family:ui-monospace,monospace; }}
.ip-item .meta {{ color:var(--txt-faint); font-size:12px; }}
.console-log {{ background:rgba(255,255,255,.02); border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:12px;
  max-height:400px; overflow-y:auto; font-family:ui-monospace,'Cascadia Code','Fira Code',monospace; font-size:12px;
  line-height:1.6; color:var(--txt-dim); white-space:pre-wrap; word-break:break-all; }}
.ban-form {{ display:flex; gap:8px; margin-top:12px; }}
.ban-form input {{ background:rgba(255,255,255,.03); border:1px solid var(--line); color:var(--txt); padding:9px 13px;
  border-radius:var(--radius-sm); font-size:13px; outline:none; flex:1; transition:border-color .16s; }}
.ban-form input:focus {{ border-color:var(--accent-line); }}
.btn-ban {{ background:rgba(218,54,51,.16); color:#ff8b88; padding:9px 16px; border:1px solid rgba(218,54,51,.34);
  border-radius:var(--radius-sm); cursor:pointer; font-weight:600; font-size:13px; transition:all .16s; }}
.btn-ban:hover {{ background:rgba(218,54,51,.26); }}
.btn-unban {{ background:rgba(35,134,54,.16); color:#7ee2a8; padding:9px 16px; border:1px solid rgba(35,134,54,.34);
  border-radius:var(--radius-sm); cursor:pointer; font-weight:600; font-size:13px; transition:all .16s; }}
.btn-unban:hover {{ background:rgba(35,134,54,.26); }}

.login-overlay {{ position:fixed; inset:0; background:rgba(0,0,0,.8); display:none; align-items:center; justify-content:center; z-index:1000; }}
.login-overlay.active {{ display:flex; }}
.login-box {{ background:var(--bg-elev); border:1px solid var(--accent-line); border-radius:var(--radius); padding:32px;
  width:360px; text-align:center; backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px); }}
.login-box h2 {{ color:#f0f6fc; margin-bottom:20px; font-size:20px; }}
.login-box input {{ width:100%; background:rgba(255,255,255,.03); border:1px solid var(--line); color:var(--txt);
  padding:12px 16px; border-radius:var(--radius-sm); font-size:14px; outline:none; margin-bottom:16px; }}
.login-box input:focus {{ border-color:var(--accent-line); }}
.login-box .btn {{ width:100%; justify-content:center; }}

@media (max-width:768px) {{
  .header {{ padding:12px 16px; flex-wrap:wrap; gap:12px; }}
  .header-left {{ width:100%; justify-content:space-between; gap:12px; }}
  .header-right {{ width:100%; justify-content:center; flex-wrap:wrap; }}
  .search {{ width:100%; }}
  .container {{ padding:20px 14px; margin-bottom:70px; }}
  .game-card-inner {{ flex-direction:column; }}
  .game-thumb {{ width:100%; height:180px; }}
  .game-info {{ padding:16px; }}
  .game-title {{ font-size:16px; }}
  .game-meta {{ font-size:12px; line-height:1.8; }}
  .value {{ margin-right:8px; }}
  .game-buttons {{ flex-direction:column; }}
  .game-buttons .download-btn, .game-buttons .copy-btn {{ text-align:center; justify-content:center; }}
  .btn {{ padding:8px 14px; font-size:12px; }}
  .admin-panel {{ padding:14px; }}
  .admin-header {{ flex-direction:column; gap:10px; }}
  .admin-tabs {{ justify-content:center; }}
  .ip-item {{ flex-direction:column; align-items:flex-start; gap:4px; }}
  .ban-form {{ flex-direction:column; }}
  .logo {{ font-size:18px; }}
  .roblox-icon {{ width:24px; height:24px; }}
}}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-glow"></div>
<div class="header">
    <div class="header-left">
        <div class="logo"><img src="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png" class="roblox-icon" alt="Roblox"><span class="lua">Lua</span> <span class="is">is</span> <span class="game">game</span></div>
        <input class="search" type="text" placeholder="Search games..." id="search">
    </div>
    <div class="header-right">
        <a class="btn btn-primary" href="https://dm.luaisgame.com/" target="_blank" rel="noopener">💬 Chat</a>
        <a class="btn btn-primary" href="https://discord.com/api/oauth2/authorize?client_id=1532820804402806844&permissions=8&scope=bot%20applications.commands" target="_blank">Add Bot</a>
        {"<button class='btn btn-secondary' data-action='toggle-console'>Console</button>" if admin else ""}
        {"<a class='btn btn-minecraft' href='" + MC_SITE_URL + "' target='_blank' rel='noopener'><svg width='16' height='16' viewBox='0 0 24 24' fill='none' aria-hidden='true'><path d='M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6ZM10 7h4v3h-4V7Zm0 7h4v3h-4v-3Z' fill='currentColor'/></svg> MC Console</a>" if mc_authorized else ""}
        {"<a class='btn btn-discord' href='/api/auth/login'><svg width='18' height='14' viewBox='0 0 71 55' fill='none' xmlns='http://www.w3.org/2000/svg'><path d='M60.1 4.9A58.5 58.5 0 0 0 45.4.2a.2.2 0 0 0-.2.1 40.8 40.8 0 0 0-1.8 3.7 54 54 0 0 0-16.2 0 26.5 26.5 0 0 0-1.8-3.7.2.2 0 0 0-.2-.1A58.4 58.4 0 0 0 10.9 4.9a.2.2 0 0 0-.1.1C1.6 18.4-.5 31.7.5 44.8a.2.2 0 0 0 .1.1 58.7 58.7 0 0 0 17.7 9 .2.2 0 0 0 .2-.1 42 42 0 0 0 3.6-5.9.2.2 0 0 0-.1-.3 38.7 38.7 0 0 1-5.5-2.6.2.2 0 0 1 0-.4c.4-.3.7-.6 1.1-.9a.2.2 0 0 1 .2 0c11.5 5.3 24 5.3 35.4 0a.2.2 0 0 1 .2 0l1.1.9a.2.2 0 0 1 0 .4c-1.8 1-3.6 1.9-5.6 2.6a.2.2 0 0 0-.1.3 47.2 47.2 0 0 0 3.7 5.9.2.2 0 0 0 .2.1 58.5 58.5 0 0 0 17.7-9 .2.2 0 0 0 .1-.1c1.2-15-2-28.3-8.5-39.8a.2.2 0 0 0-.1-.1ZM23.7 36.3c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Zm23.6 0c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Z' fill='white'/></svg> " + user_info["username"] + "</a>" if user_info else "<a class='btn btn-discord' href='/api/auth/login'><svg width='18' height='14' viewBox='0 0 71 55' fill='none' xmlns='http://www.w3.org/2000/svg'><path d='M60.1 4.9A58.5 58.5 0 0 0 45.4.2a.2.2 0 0 0-.2.1 40.8 40.8 0 0 0-1.8 3.7 54 54 0 0 0-16.2 0 26.5 26.5 0 0 0-1.8-3.7.2.2 0 0 0-.2-.1A58.4 58.4 0 0 0 10.9 4.9a.2.2 0 0 0-.1.1C1.6 18.4-.5 31.7.5 44.8a.2.2 0 0 0 .1.1 58.7 58.7 0 0 0 17.7 9 .2.2 0 0 0 .2-.1 42 42 0 0 0 3.6-5.9.2.2 0 0 0-.1-.3 38.7 38.7 0 0 1-5.5-2.6.2.2 0 0 1 0-.4c.4-.3.7-.6 1.1-.9a.2.2 0 0 1 .2 0c11.5 5.3 24 5.3 35.4 0a.2.2 0 0 1 .2 0l1.1.9a.2.2 0 0 1 0 .4c-1.8 1-3.6 1.9-5.6 2.6a.2.2 0 0 0-.1.3 47.2 47.2 0 0 0 3.7 5.9.2.2 0 0 0 .2.1 58.5 58.5 0 0 0 17.7-9 .2.2 0 0 0 .1-.1c1.2-15-2-28.3-8.5-39.8a.2.2 0 0 0-.1-.1ZM23.7 36.3c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Zm23.6 0c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Z' fill='white'/></svg> Login with Discord</a>"}
    </div>
</div>
<div class="container">
    {admin_block}
    <div class="count">{len(entries)} game(s) decompiled</div>
    <div id="games">{games_html}</div>
</div>
<div class="footer">
    Created by: <strong>iispeaklua</strong> (Crimson) &bull; <a href="https://discord.gg/robloxdecompiler">Discord</a>
</div>
<script>
document.addEventListener("click", function(ev) {{
    var btn = ev.target.closest(".copy-btn");
    if (btn) {{
        ev.preventDefault();
        ev.stopPropagation();
        var url = btn.getAttribute("data-url");
        if (!url) return;
        var tmp = document.createElement("textarea");
        tmp.value = url;
        tmp.style.position = "fixed";
        tmp.style.opacity = "0";
        document.body.appendChild(tmp);
        tmp.select();
        document.execCommand("copy");
        document.body.removeChild(tmp);
        var orig = btn.textContent;
        btn.textContent = "Copied!";
        btn.style.background = "#58a6ff";
        btn.style.color = "#0d1117";
        btn.style.borderColor = "#58a6ff";
        setTimeout(function() {{ btn.textContent = orig; btn.style.background = ""; btn.style.color = ""; btn.style.borderColor = ""; }}, 2000);
    }}
}});
document.getElementById("search").addEventListener("input", function() {{
    var q = this.value.toLowerCase();
    document.querySelectorAll(".game-card").forEach(function(c) {{
        c.style.display = (c.dataset.name.indexOf(q) !== -1 || c.dataset.user.indexOf(q) !== -1) ? "" : "none";
    }});
}});
var adminPanelEl = document.getElementById("adminPanel");
if (adminPanelEl) {{
    document.querySelector("[data-action='toggle-console']").addEventListener("click", function() {{
        if (adminPanelEl.style.display === "none" || adminPanelEl.style.display === "" || adminPanelEl.style.display === undefined) {{
            adminPanelEl.style.display = "block";
            loadAdmin();
        }} else {{
            adminPanelEl.style.display = "none";
        }}
    }});
}}
document.addEventListener("click", function(ev) {{
    var tab = ev.target.closest(".tab");
    if (tab) {{
        var name = tab.getAttribute("data-tab");
        if (!name) return;
        document.querySelectorAll(".tab-content").forEach(function(t) {{ t.classList.add("hidden"); }});
        document.querySelectorAll(".tab").forEach(function(t) {{ t.classList.remove("active"); }});
        var el = document.getElementById("tab-" + name);
        if (el) el.classList.remove("hidden");
        tab.classList.add("active");
    }}
}});
async function loadAdmin() {{
    try {{
        var r = await fetch("/api/admin");
        if (!r.ok) return;
        var d = await r.json();
        var ipEl = document.getElementById("ipList");
        if (ipEl) {{
            var ipHtml = "";
            (d.ips||[]).forEach(function(e) {{
                var banned = (d.banned||[]).indexOf(e.ip) !== -1;
                ipHtml += '<div class="ip-item"><span class="ip">' + e.ip + (banned ? ' <span style="color:#da3633">(BANNED)</span>' : '') + '</span><span class="meta">Visits: ' + e.visits + ' | Last: ' + e.last_seen + '</span></div>';
            }});
            ipEl.innerHTML = ipHtml || "<p style='color:#484f58'>No IPs tracked yet</p>";
        }}
        var banEl = document.getElementById("banList");
        if (banEl) {{
            var banHtml = "";
            (d.banned||[]).forEach(function(ip) {{
                banHtml += '<div class="ip-item"><span class="ip">' + ip + '</span><span class="meta">Banned</span></div>';
            }});
            banEl.innerHTML = banHtml || "<p style='color:#484f58'>No banned IPs</p>";
        }}
        var pyEl = document.getElementById("pyLog");
        if (pyEl) {{
            var pr = await fetch("/api/logs?type=py&count=200");
            if (pr.ok) {{
                var pd = await pr.json();
                pyEl.textContent = pd.logs || "No logs yet";
                pyEl.scrollTop = pyEl.scrollHeight;
            }}
        }}
        var tEl = document.getElementById("tunnelLog");
        if (tEl) {{
            var tr = await fetch("/api/logs?type=tunnel&count=200");
            if (tr.ok) {{
                var td = await tr.json();
                tEl.textContent = td.logs || "No logs yet";
                tEl.scrollTop = tEl.scrollHeight;
            }}
        }}
    }} catch(err) {{ console.error("loadAdmin error:", err); }}
}}
document.addEventListener("click", function(ev) {{
    var btn = ev.target.closest("#banBtn");
    if (btn) {{
        var ip = document.getElementById("banIpInput").value;
        if (!ip) return;
        fetch("/api/ban", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{ip:ip, action:"ban"}})}}).then(function() {{ document.getElementById("banIpInput").value = ""; loadAdmin(); }});
    }}
    var btn2 = ev.target.closest("#unbanBtn");
    if (btn2) {{
        var ip2 = document.getElementById("banIpInput").value;
        if (!ip2) return;
        fetch("/api/ban", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{ip:ip2, action:"unban"}})}}).then(function() {{ document.getElementById("banIpInput").value = ""; loadAdmin(); }});
    }}
}});
function buildGameCards(data) {{
    var h = "";
    data.forEach(function(e) {{
        h += '<div class="game-card" data-name="'+(e.game_name||'').toLowerCase()+'" data-user="'+(e.display_name||'').toLowerCase()+'"><div class="game-card-inner"><img class="game-thumb" src="'+(e.icon_url||'')+'" alt="thumb" onerror="this.parentElement.removeChild(this)"><div class="game-info"><a class="game-title" href="https://www.roblox.com/games/'+e.place_id+'" target="_blank">'+(e.game_name||'Unknown')+'</a><div class="game-meta"><span class="label">Place ID:</span> <span class="value">'+e.place_id+'</span><span class="label">Version:</span> <span class="value">'+(e.game_version||'N/A')+'</span><span class="label">Requested by:</span> <span class="value">'+(e.display_name||'Unknown')+' ('+e.user_id+')</span><span class="label">Downloaded:</span> <span class="value">'+e.timestamp+'</span></div><div class="game-buttons"><a class="download-btn" href="/'+e.filename+'" download>Download</a><button class="copy-btn" data-url="https://storage.luaisgame.com/'+e.filename+'">Copy Link</button>' + (e.assets_pack ? '<a class="assets-btn" href="/assets/'+e.assets_pack+'">🎞 Assets</a>' : '') + '</div></div></div></div>';
    }});
    return h;
}}
setInterval(function() {{
    fetch("/games.json").then(function(r) {{ return r.json(); }}).then(function(data) {{
        var c = document.getElementById("games");
        if (!c) return;
        c.innerHTML = buildGameCards(data);
        document.querySelector(".count").textContent = data.length + " game(s) decompiled";
        var q = document.getElementById("search").value.toLowerCase();
        if (q) {{
            document.querySelectorAll(".game-card").forEach(function(card) {{
                card.style.display = (card.dataset.name.indexOf(q) !== -1 || card.dataset.user.indexOf(q) !== -1) ? "" : "none";
            }});
        }}
    }});
}}, 5000);
if (document.getElementById("adminPanel")) {{ setInterval(loadAdmin, 3000); }}
</script>
</body>
</html>'''
    return web.Response(text=html, content_type="text/html")


MC_DIR = os.path.join(os.path.dirname(BASE_DIR), "minecraft")
mc_processes = {}
mc_console_buffers = {}
mc_ws_clients = {}
mc_command_capture_states = {}
mc_command_locks = {}
mc_live_player_cache = {}
mc_live_player_locks = {}
mc_players_cache = {}
mc_players_locks = {}
mc_discord_profile_cache = {}
mc_bans_cache = {}
mc_skin_cache = {}
mc_line_counters = {}        # server name -> monotonic console line number
mc_restart_tasks = {}        # server name -> crash-restart task
mc_restart_attempts = {}     # server name -> consecutive restart failures

RESTART_DELAYS = (5, 15, 30, 60, 120)


def _mc_keepalive_path(name):
    return os.path.join(MC_DIR, name, ".keepalive")


def _mc_mark_keepalive(name, enabled=True):
    """A .keepalive marker means 'this server should be running'."""
    try:
        path = _mc_keepalive_path(name)
        if enabled:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("1")
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _mc_ram_ok():
    """Is there plausibly enough free RAM to start another server?"""
    try:
        vm = psutil.virtual_memory()
        return vm.available >= 2 * (1024 ** 3) and vm.available >= vm.total * 0.15
    except Exception:
        return True


async def _mc_broadcast(name, payload):
    """Send a JSON payload to every live console client for `name`."""
    dead = []
    for ws in list(mc_ws_clients.get(name, [])):
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=3)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_list = mc_ws_clients.get(name, [])
        if ws in ws_list:
            ws_list.remove(ws)


def _mc_buf_push(name, entry, send=True):
    """Append a formatted line to the buffer and broadcast it.

    Returns the line's monotonic number (used by clients to dedupe).
    """
    n = mc_line_counters.get(name, 0) + 1
    mc_line_counters[name] = n
    buf = mc_console_buffers.setdefault(name, [])
    buf.append((n, entry))
    if len(buf) > 500:
        buf[:] = buf[-500:]
    if send:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_mc_broadcast(name, {"type": "output", "line": entry, "n": n}))
    return n


async def _mc_restart_with_backoff(name):
    """Auto-restart a crashed server while its .keepalive marker exists."""
    attempt = mc_restart_attempts.get(name, 0)
    try:
        while True:
            if not os.path.exists(_mc_keepalive_path(name)):
                break
            proc = mc_processes.get(name)
            if proc is not None and proc.returncode is None:
                break
            if not _mc_ram_ok():
                _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Not enough free RAM to restart \u2014 retrying in 60s.")
                await asyncio.sleep(60)
                continue
            delay = RESTART_DELAYS[min(attempt, len(RESTART_DELAYS) - 1)]
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Server crashed \u2014 restarting in {delay}s (attempt {attempt + 1}).")
            await asyncio.sleep(delay)
            if not os.path.exists(_mc_keepalive_path(name)):
                break
            ok, err = await _mc_start_server(name, "auto", autostart=True)
            if ok:
                mc_restart_attempts[name] = 0
                _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Server back online (auto-restart succeeded).")
                return
            attempt += 1
            mc_restart_attempts[name] = attempt
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Auto-restart failed: {err}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[MINECRAFT] Restart supervisor for {name} failed: {exc}")


def schedule_mc_autostart():
    """Recover servers that were online before a power/bot outage."""
    try:
        return asyncio.get_event_loop().create_task(_mc_autostart())
    except Exception as exc:
        print(f"[MINECRAFT] Could not schedule auto-start: {exc}")
        return None


async def _mc_autostart():
    await asyncio.sleep(5)
    for server in _mc_get_servers():
        name = server["name"]
        if not os.path.exists(_mc_keepalive_path(name)):
            continue
        proc = mc_processes.get(name)
        if proc is not None and proc.returncode is None:
            continue
        if not _mc_ram_ok():
            print(f"[MINECRAFT] Skipping auto-start of {name}: not enough free RAM")
            continue
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Machine came back \u2014 auto-starting {name}.")
        ok, err = await _mc_start_server(name, "auto", autostart=True)
        if not ok:
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Auto-start failed: {err}")

def _mc_memory_limit():
    configured = os.environ.get("MC_MEMORY", "auto").strip()
    if configured and configured.lower() != "auto":
        return configured
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    allocated_gb = max(1, int(total_gb * 0.90))
    print(f"[MINECRAFT] Automatic memory allocation: {allocated_gb}G of {total_gb:.1f}G")
    return f"{allocated_gb}G"

def _mc_get_servers():
    if not os.path.isdir(MC_DIR):
        return []
    servers = []
    for name in sorted(os.listdir(MC_DIR)):
        if name == "backups" or name.startswith("."):
            continue
        path = os.path.join(MC_DIR, name)
        if os.path.isdir(path):
            running = name in mc_processes and mc_processes[name] is not None and mc_processes[name].returncode is None
            loader = "fabric"
            loader_file = os.path.join(path, ".loader")
            if os.path.exists(loader_file):
                with open(loader_file, "r") as f:
                    loader = f.read().strip()
            elif _mc_is_forge(path):
                loader = "forge"
            version = "auto"
            version_file = os.path.join(path, ".mc_version")
            if os.path.exists(version_file):
                with open(version_file, "r") as f:
                    version = f.read().strip() or "auto"
            servers.append({
                "name": name,
                "running": running,
                "loader": loader,
                "version": version,
                "icon": os.path.isfile(os.path.join(path, "server-icon.png")),
            })
    return servers

def _mc_valid_server_name(name):
    """Server names double as directory names, so keep them to one safe segment."""
    return bool(
        name
        and name == os.path.basename(name)
        and all(c.isalnum() or c in "-_" for c in name)
    )

def _mc_safe_server_dir(name):
    if not _mc_valid_server_name(name):
        return None
    server_dir = os.path.join(MC_DIR, name)
    return server_dir if os.path.isdir(server_dir) else None

def _mc_offline_uuid(player_name):
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{player_name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))

def _find_texture_url(value):
    if isinstance(value, dict):
        textures = value.get("textures")
        if isinstance(textures, dict):
            skin = textures.get("SKIN")
            if isinstance(skin, dict) and skin.get("url"):
                return skin["url"]
        for child in value.values():
            result = _find_texture_url(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _find_texture_url(child)
            if result:
                return result
    elif isinstance(value, str) and len(value) > 20:
        try:
            decoded = base64.b64decode(value).decode("utf-8")
            return _find_texture_url(json.loads(decoded))
        except Exception:
            pass
    return None

def _mc_find_skin_url(server_dir, player_name):
    cache_key = (server_dir, player_name.lower())
    cached = mc_skin_cache.get(cache_key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    offline_id = _mc_offline_uuid(player_name).hex
    roots = [
        os.path.join(server_dir, "config", "skinrestorer"),
        os.path.join(server_dir, "config", "SkinRestorer"),
        os.path.join(server_dir, "plugins", "SkinsRestorer"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for current, _, files in os.walk(root):
            for filename in files:
                if not filename.endswith((".playerskin", ".skin", ".json")):
                    continue
                stem = os.path.splitext(filename)[0].replace("-", "").lower()
                if offline_id not in stem and player_name.lower() not in filename.lower():
                    continue
                try:
                    with open(os.path.join(current, filename), "r", encoding="utf-8") as f:
                        url = _find_texture_url(json.load(f))
                    if url:
                        mc_skin_cache[cache_key] = (now + 30, url)
                        return url
                except Exception:
                    continue
    mc_skin_cache[cache_key] = (now + 30, None)
    return None

def _mc_change_server_version(server_dir, loader_type, version):
    from minecraft_setup import setup_server
    backup_dir = os.path.join(MC_DIR, f".{os.path.basename(server_dir)}-version-backup-{uuid.uuid4().hex}")
    preserved = ["world", "mods", "config", "server.properties", "eula.txt", "server-icon.png"]
    os.makedirs(backup_dir, exist_ok=True)
    ok = False
    try:
        for name in preserved:
            source = os.path.join(server_dir, name)
            if os.path.exists(source):
                shutil.move(source, os.path.join(backup_dir, name))
        for name in os.listdir(server_dir):
            path = os.path.join(server_dir, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        ok = setup_server(server_dir, loader_type, version)
    finally:
        for name in preserved:
            source = os.path.join(backup_dir, name)
            if os.path.exists(source):
                destination = os.path.join(server_dir, name)
                if os.path.exists(destination):
                    if os.path.isdir(destination):
                        shutil.rmtree(destination)
                    else:
                        os.remove(destination)
                shutil.move(source, destination)
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
    if not ok:
        return False
    with open(os.path.join(server_dir, ".loader"), "w") as f:
        f.write(loader_type)
    with open(os.path.join(server_dir, ".mc_version"), "w") as f:
        f.write(version)
    return True

async def mc_api_versions(request):
    if not _is_mc_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json") as response:
                if response.status != 200:
                    return web.json_response({"error": "Could not fetch Minecraft versions"}, status=502)
                manifest = await response.json()
        versions = [v["id"] for v in manifest.get("versions", []) if v.get("type") == "release"]
        return web.json_response({"versions": versions})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)

async def mc_api_change_version(request):
    data = await request.json()
    name = data.get("name", "").strip()
    version = data.get("version", "").strip()
    if not _mc_can(request, name, "version"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    if not re.match(r"^\d+\.\d+(?:\.\d+)?$", version):
        return web.json_response({"error": "Invalid Minecraft version"}, status=400)
    proc = mc_processes.get(name)
    if proc and proc.returncode is None:
        return web.json_response({"error": "Stop the server before changing its version"}, status=409)
    loader_type = "fabric"
    loader_file = os.path.join(server_dir, ".loader")
    if os.path.exists(loader_file):
        with open(loader_file, "r") as f:
            loader_type = f.read().strip() or loader_type
    try:
        ok = await asyncio.to_thread(_mc_change_server_version, server_dir, loader_type, version)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    if not ok:
        return web.json_response({"error": "Could not install the selected version"}, status=500)
    return web.json_response({"ok": True, "version": version})

async def mc_api_delete_server(request):
    data = await request.json()
    name = data.get("name", "").strip()
    if not _mc_can(request, name, "delete"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    _mc_mark_keepalive(name, False)
    task = mc_restart_tasks.pop(name, None)
    if task is not None and not task.done():
        task.cancel()
    mc_restart_attempts.pop(name, None)
    proc = mc_processes.get(name)
    if proc and proc.returncode is None:
        proc.kill()
        await proc.wait()
    mc_processes.pop(name, None)
    mc_console_buffers.pop(name, None)
    mc_line_counters.pop(name, None)
    mc_ws_clients.pop(name, None)
    shutil.rmtree(server_dir)
    acl = _mc_load_acl()
    if name in acl["servers"]:
        acl["servers"].pop(name, None)
        _mc_save_acl(acl)
    return web.json_response({"ok": True})


async def mc_api_rename(request):
    data = await request.json()
    name = data.get("name", "")
    new_name = str(data.get("new_name", "")).strip()
    if not _mc_can(request, name, "delete"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_valid_server_name(name):
        return web.json_response({"error": "Server not found"}, status=404)
    if not _mc_valid_server_name(new_name):
        return web.json_response({"error": "Invalid name \u2014 use letters, numbers, - or _ only"}, status=400)
    if new_name == name:
        return web.json_response({"ok": True, "name": name})
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    if os.path.exists(os.path.join(MC_DIR, new_name)):
        return web.json_response({"error": "That name is already taken"}, status=409)
    proc = mc_processes.get(name)
    if proc is not None and proc.returncode is None:
        return web.json_response({"error": "Stop the server before renaming it"}, status=400)
    _mc_mark_keepalive(name, False)
    task = mc_restart_tasks.pop(name, None)
    if task is not None and not task.done():
        task.cancel()
    mc_restart_attempts.pop(name, None)
    mc_processes.pop(name, None)
    mc_console_buffers.pop(name, None)
    mc_line_counters.pop(name, None)
    mc_ws_clients.pop(name, None)
    try:
        os.rename(server_dir, os.path.join(MC_DIR, new_name))
    except OSError as exc:
        return web.json_response({"error": f"Rename failed: {exc}"}, status=500)
    old_backups = os.path.join(MC_DIR, "backups", name)
    new_backups = os.path.join(MC_DIR, "backups", new_name)
    if os.path.isdir(old_backups) and not os.path.exists(new_backups):
        try:
            os.rename(old_backups, new_backups)
        except OSError:
            pass
    acl = _mc_load_acl()
    if name in acl.get("servers", {}):
        acl["servers"][new_name] = acl["servers"].pop(name)
        _mc_save_acl(acl)
    return web.json_response({"ok": True, "name": new_name})

def _mc_create_backup(server_name, server_dir):
    if not _mc_valid_server_name(server_name):
        raise ValueError("Invalid server name")
    backup_dir = os.path.join(MC_DIR, "backups", server_name)
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"{server_name}-{stamp}.zip")
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(server_dir):
            dirs[:] = [directory for directory in dirs if directory not in ("backups",) and not directory.startswith(".")]
            for filename in files:
                if filename.endswith((".lock", ".tmp")) or filename.startswith("."):
                    continue
                path = os.path.join(root, filename)
                archive.write(path, os.path.relpath(path, server_dir))
    return backup_path

async def mc_api_backup(request):
    data = await request.json()
    name = data.get("name", "").strip()
    if not _mc_can(request, name, "backup"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    try:
        backup_path = await asyncio.to_thread(_mc_create_backup, name, server_dir)
        return web.json_response({"ok": True, "name": os.path.basename(backup_path)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def mc_api_backups(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "backup"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_valid_server_name(name):
        return web.json_response({"error": "Invalid server name"}, status=400)
    backup_dir = os.path.join(MC_DIR, "backups", name)
    if not os.path.isdir(backup_dir):
        return web.json_response({"backups": []})
    backups = []
    for filename in sorted(os.listdir(backup_dir), reverse=True):
        path = os.path.join(backup_dir, filename)
        if filename.endswith(".zip") and os.path.isfile(path):
            backups.append({"name": filename, "size": os.path.getsize(path), "created": os.path.getmtime(path)})
    return web.json_response({"backups": backups})

async def mc_api_backup_download(request):
    name = request.query.get("name", "")
    filename = os.path.basename(request.query.get("file", ""))
    if not _mc_can(request, name, "backup"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_valid_server_name(name):
        return web.json_response({"error": "Invalid server name"}, status=400)
    if not filename.endswith(".zip"):
        return web.json_response({"error": "Invalid backup"}, status=400)
    path = os.path.join(MC_DIR, "backups", name, filename)
    if not os.path.isfile(path):
        return web.json_response({"error": "Backup not found"}, status=404)
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

def _mc_find_jar(server_dir):
    for f in os.listdir(server_dir):
        if f.endswith(".jar") and "installer" not in f.lower():
            if "forge" in f.lower() or "fabric" in f.lower() or "server" in f.lower():
                return os.path.join(server_dir, f)
    for f in os.listdir(server_dir):
        if f.endswith(".jar") and "installer" not in f.lower():
            return os.path.join(server_dir, f)
    return None

def _mc_is_forge(server_dir):
    if os.path.exists(os.path.join(server_dir, "run.bat")) or os.path.exists(os.path.join(server_dir, "run.sh")):
        return True
    libs = os.path.join(server_dir, "libraries", "net", "minecraftforge")
    if os.path.isdir(libs):
        return True
    return False

def _mc_detect_ver(server_dir):
    version_file = os.path.join(server_dir, ".mc_version")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            saved_version = f.read().strip()
        if re.match(r"^\d+\.\d+(?:\.\d+)?$", saved_version):
            return saved_version
    libs_dir = os.path.join(server_dir, "libraries", "net", "minecraftforge", "forge")
    if os.path.isdir(libs_dir):
        for d in os.listdir(libs_dir):
            parts = d.split("-")
            if len(parts) >= 2:
                return parts[0]
    versions_dir = os.path.join(server_dir, "versions")
    if os.path.isdir(versions_dir):
        versions = [
            name for name in os.listdir(versions_dir)
            if os.path.isdir(os.path.join(versions_dir, name)) and re.match(r"^\d+\.\d+(?:\.\d+)?$", name)
        ]
        if versions:
            return sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")), reverse=True)[0]
    server_libs = os.path.join(server_dir, "libraries", "net", "minecraft", "server")
    if os.path.isdir(server_libs):
        versions = [
            name for name in os.listdir(server_libs)
            if os.path.isdir(os.path.join(server_libs, name)) and re.match(r"^\d+\.\d+(?:\.\d+)?$", name)
        ]
        if versions:
            return sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")), reverse=True)[0]
    launcher_props = os.path.join(server_dir, "fabric-server-launcher.properties")
    if os.path.exists(launcher_props):
        with open(launcher_props, "r") as f:
            for line in f:
                if line.startswith("serverJar="):
                    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", line)
                    if match:
                        return match.group(1)
    return None

def _mc_java_from_jar(server_dir):
    jar_path = os.path.join(server_dir, "server.jar")
    if not os.path.isfile(jar_path):
        return None
    try:
        with zipfile.ZipFile(jar_path) as archive:
            class_data = archive.read("net/minecraft/bundler/Main.class")
        if len(class_data) < 8 or class_data[:4] != b"\xca\xfe\xba\xbe":
            return None
        class_version = int.from_bytes(class_data[6:8], "big")
        if class_version >= 70:
            return 26
        if class_version >= 65:
            return 21
        if class_version >= 61:
            return 17
        if class_version >= 52:
            return 8
    except (KeyError, OSError, zipfile.BadZipFile) as e:
        print(f"[MINECRAFT] Could not inspect server JAR Java version: {e}")
    return None

def _mc_get_java(server_dir):
    try:
        from minecraft_setup import _mc_ver_to_java, _find_java
        mc_ver = _mc_detect_ver(server_dir)
        if mc_ver:
            java_ver = _mc_ver_to_java(mc_ver)
            jar_java = _mc_java_from_jar(server_dir)
            if jar_java and jar_java > java_ver:
                java_ver = jar_java
            java = _find_java(java_ver)
            print(f"[MINECRAFT] MC {mc_ver} requires Java {java_ver}; using {java}")
            return java
        java_ver = _mc_java_from_jar(server_dir)
        if java_ver:
            java = _find_java(java_ver)
            print(f"[MINECRAFT] Server JAR requires Java {java_ver}; using {java}")
            return java
        print(f"[MINECRAFT] Could not detect Minecraft version in {server_dir}; using default Java")
    except Exception as e:
        print(f"[MINECRAFT] Java compatibility check failed: {e}")
    return "java"

FORGE_JVM_ARGS = [
    "--add-opens", "java.base/java.lang=ALL-UNNAMED",
    "--add-opens", "java.base/java.lang.invoke=ALL-UNNAMED",
    "--add-opens", "java.base/java.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.nio=ALL-UNNAMED",
    "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    "--add-opens", "java.base/java.io=ALL-UNNAMED",
    "--add-opens", "java.base/sun.security.ssl=ALL-UNNAMED",
    "--add-opens", "java.base/sun.security.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.net=ALL-UNNAMED",
]

def _mc_find_user_jvm_args(server_dir):
    for name in ["user_jvm_args.txt", ".javaargs"]:
        p = os.path.join(server_dir, name)
        if os.path.exists(p):
            return p
    return None

def _mc_find_forge_args(server_dir):
    forge_dir = os.path.join(server_dir, "libraries", "net", "minecraftforge", "forge")
    if not os.path.isdir(forge_dir):
        return None
    for root, dirs, files in os.walk(forge_dir):
        for f in files:
            if f == "win_args.txt":
                return os.path.join(root, f)
    return None

async def _mc_read_output(name, proc):
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n\r")
            ts = time.strftime("%H:%M:%S")
            entry = f"[{ts}] {text}"
            capture = mc_command_capture_states.get(name)
            if capture and (not capture.get("silent") or _mc_command_result_matches(capture.get("command", ""), text)):
                capture["lines"].append(entry)
                capture["event"].set()
                if capture.get("silent"):
                    continue
            _mc_buf_push(name, entry)
    except Exception:
        pass
    code = proc.returncode
    if name in mc_processes:
        mc_processes[name] = None
    if os.path.exists(_mc_keepalive_path(name)):
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Server process exited (code {code}) \u2014 auto-restart is enabled.")
        task = asyncio.create_task(_mc_restart_with_backoff(name))
        mc_restart_tasks[name] = task
    else:
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Server stopped (code {code}). Use Start to bring it back.")

async def mc_api_servers(request):
    if not _is_mc_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    acl = _mc_load_acl()
    admin = _is_mc_admin(request)
    user_id = _mc_user_id(request)
    servers = []
    for server in _mc_get_servers():
        name = server["name"]
        entry = acl["servers"].get(name) or {}
        owner_id = entry.get("owner", "")
        if admin or user_id == owner_id:
            privileges = list(MC_PRIVILEGES)
        else:
            privileges = _mc_normalize_privileges((entry.get("users") or {}).get(user_id, []))
            if not privileges:
                continue
        server["owner"] = bool(owner_id and owner_id == user_id)
        server["privileges"] = privileges
        servers.append(server)
    return web.json_response({
        "servers": servers,
        "can_create": _mc_can_create(request),
        "is_admin": admin,
    })

async def _mc_discord_profile(user_id):
    user_id = str(user_id)
    cached = mc_discord_profile_cache.get(user_id)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    try:
        user = await bot.fetch_user(int(user_id))
        avatar = str(user.display_avatar.url) if user.display_avatar else ""
        banner = str(user.banner.url) if getattr(user, "banner", None) else ""
        profile = {"id": str(user.id), "username": str(user), "avatar": avatar, "banner": banner}
        mc_discord_profile_cache[user_id] = (now + 300, profile)
        return profile
    except Exception:
        profile = {"id": str(user_id), "username": "Unknown Discord user", "avatar": "", "banner": ""}
        mc_discord_profile_cache[user_id] = (now + 30, profile)
        return profile

async def _mc_access_profile(user_id, owner_id, privileges):
    profile = dict(await _mc_discord_profile(user_id))
    profile["privileges"] = list(privileges)
    profile["owner"] = bool(owner_id and user_id == owner_id)
    return profile

async def mc_api_permissions(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "access"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_safe_server_dir(name):
        return web.json_response({"error": "Server not found"}, status=404)
    acl = _mc_load_acl()
    entry = acl["servers"].get(name) or {"owner": "", "users": {}}
    owner_id = entry.get("owner", "")
    profiles = []
    if owner_id:
        profiles.append(await _mc_access_profile(owner_id, owner_id, list(MC_PRIVILEGES)))
    for user_id in sorted((entry.get("users") or {}).keys()):
        if user_id == owner_id:
            continue
        profiles.append(await _mc_access_profile(user_id, owner_id, entry["users"].get(user_id, [])))
    payload = {
        "server": name,
        "owner": owner_id,
        "users": profiles,
        "privileges": list(MC_PRIVILEGES),
        "is_admin": _is_mc_admin(request),
        "can_grant_admin": _is_mc_owner(request),
    }
    if _is_mc_admin(request):
        payload["global"] = {
            "admins": [await _mc_discord_profile(user_id) for user_id in acl["admins"]],
            "create": [await _mc_discord_profile(user_id) for user_id in acl["create"]],
        }
    return web.json_response(payload)

async def mc_api_add_permission(request):
    data = await request.json()
    name = data.get("name", "").strip()
    user_id = _mc_normalize_user_id(data.get("user_id", ""))
    if not _mc_can(request, name, "access"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_safe_server_dir(name):
        return web.json_response({"error": "Server not found"}, status=404)
    if not user_id:
        return web.json_response({"error": "Enter a numeric Discord user ID"}, status=400)
    raw_privileges = data.get("privileges")
    privileges = list(MC_PRIVILEGES) if raw_privileges is None else _mc_normalize_privileges(raw_privileges)
    if not privileges:
        return web.json_response({"error": "Choose at least one privilege"}, status=400)
    actor_privileges = _mc_server_privileges(request, name)
    if not set(privileges).issubset(actor_privileges):
        return web.json_response({"error": "You cannot grant privileges you do not have"}, status=403)
    acl = _mc_load_acl()
    entry = acl["servers"].setdefault(name, {"owner": "", "users": {}})
    if user_id == entry.get("owner"):
        return web.json_response({"ok": True, "profile": await _mc_access_profile(user_id, user_id, list(MC_PRIVILEGES))})
    entry.setdefault("users", {})[user_id] = privileges
    _mc_save_acl(acl)
    return web.json_response({"ok": True, "profile": await _mc_access_profile(user_id, entry.get("owner", ""), privileges)})

async def mc_api_remove_permission(request):
    data = await request.json()
    name = data.get("name", "").strip()
    user_id = _mc_normalize_user_id(data.get("user_id", ""))
    if not _mc_can(request, name, "access"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not user_id:
        return web.json_response({"error": "Enter a numeric Discord user ID"}, status=400)
    acl = _mc_load_acl()
    entry = acl["servers"].get(name) or {}
    if user_id == entry.get("owner"):
        return web.json_response({"error": "The owner always has access. Transfer ownership first."}, status=400)
    users = entry.setdefault("users", {}) if entry else None
    if users is not None and user_id in users:
        users.pop(user_id, None)
        _mc_save_acl(acl)
    return web.json_response({"ok": True})

async def mc_api_set_server_owner(request):
    data = await request.json()
    name = data.get("name", "").strip()
    user_id = _mc_normalize_user_id(data.get("user_id", ""))
    if not _mc_can(request, name, "access"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _mc_safe_server_dir(name):
        return web.json_response({"error": "Server not found"}, status=404)
    if not user_id:
        return web.json_response({"error": "Enter a numeric Discord user ID"}, status=400)
    acl = _mc_load_acl()
    entry = acl["servers"].setdefault(name, {"owner": "", "users": {}})
    actor_id = _mc_user_id(request)
    if not (_is_mc_admin(request) or actor_id == entry.get("owner")):
        return web.json_response({"error": "Only the server owner or a global admin can transfer ownership"}, status=403)
    entry["owner"] = user_id
    entry.setdefault("users", {}).pop(user_id, None)
    _mc_save_acl(acl)
    return web.json_response({"ok": True, "profile": await _mc_access_profile(user_id, user_id, list(MC_PRIVILEGES))})

async def mc_api_global_access(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    acl = _mc_load_acl()
    return web.json_response({
        "admins": [await _mc_discord_profile(user_id) for user_id in acl["admins"]],
        "create": [await _mc_discord_profile(user_id) for user_id in acl["create"]],
        "can_grant_admin": _is_mc_owner(request),
    })

async def mc_api_set_global_access(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    user_id = _mc_normalize_user_id(data.get("user_id", ""))
    scope = str(data.get("scope", "create")).strip().lower()
    action = str(data.get("action", "add")).strip().lower()
    if not user_id:
        return web.json_response({"error": "Enter a numeric Discord user ID"}, status=400)
    if scope not in MC_GLOBAL_PRIVILEGES:
        return web.json_response({"error": "Unknown global privilege"}, status=400)
    if action not in ("add", "remove"):
        return web.json_response({"error": "Unknown action"}, status=400)
    if user_id == str(BOT_OWNER_ID):
        return web.json_response({"error": "The bot owner always has access"}, status=400)
    if scope == "admin" and not _is_mc_owner(request):
        return web.json_response({"error": "Only the bot owner can grant global admin"}, status=403)
    acl = _mc_load_acl()
    key = "admins" if scope == "admin" else "create"
    values = set(acl[key])
    if action == "add":
        values.add(user_id)
    else:
        values.discard(user_id)
    acl[key] = sorted(values)
    _mc_save_acl(acl)
    return web.json_response({"ok": True, key: [await _mc_discord_profile(value) for value in acl[key]]})

def _mc_command_result_matches(command, text):
    lowered = text.lower()
    if command.strip().lower() == "list":
        return bool(re.search(r"there are \d+ of a max of \d+ players online:", lowered))
    if command.strip().lower().startswith("data get entity "):
        return "following entity data:" in lowered or any(phrase in lowered for phrase in (
            "no entity was found", "entity was not found", "unable to get entity data",
            "cannot get entity data", "couldn't get entity data", "could not get entity data",
        ))
    return True

def _mc_parse_online_players(lines):
    pattern = re.compile(r"There are \d+ of a max of \d+ players online:\s*(.*)", re.IGNORECASE)
    for line in reversed(lines):
        match = pattern.search(line)
        if match:
            return [value.strip() for value in match.group(1).split(",") if value.strip()]
    return None

async def _mc_get_live_players(server_name):
    proc = mc_processes.get(server_name)
    if not proc or proc.returncode is not None or not proc.stdin:
        return []
    now = time.monotonic()
    cached = mc_players_cache.get(server_name)
    if cached and now - cached[0] < 0.08:
        return cached[1]
    lock = mc_players_locks.setdefault(server_name, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = mc_players_cache.get(server_name)
        if cached and now - cached[0] < 0.08:
            return cached[1]
        lines = await _mc_run_command_capture(server_name, "list", timeout=1.0, silent=True)
        players = _mc_parse_online_players(lines)
        if players is None:
            cached = mc_players_cache.get(server_name)
            if cached and time.monotonic() - cached[0] < 2:
                return cached[1]
            players = []
        mc_players_cache[server_name] = (time.monotonic(), players)
        return players

def _mc_normalize_ip(value):
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return str(value).strip()

def _mc_read_json_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []

def _mc_write_json_list(path, values):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2)
    os.replace(temp_path, path)

def _mc_player_uuid_from_cache(server_dir, player_name):
    cache_path = os.path.join(server_dir, "usercache.json")
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                if str(entry.get("name", "")).lower() == player_name.lower():
                    return str(entry.get("uuid", ""))
    except Exception:
        pass
    return ""

def _mc_get_server_bans(server_name):
    server_dir = _mc_safe_server_dir(server_name)
    if not server_dir:
        return {"banned_players": [], "banned_ips": []}
    now = time.monotonic()
    cached = mc_bans_cache.get(server_name)
    if cached and now - cached[0] < 1:
        return cached[1]

    usercache_names = {}
    try:
        with open(os.path.join(server_dir, "usercache.json"), "r", encoding="utf-8") as f:
            for entry in json.load(f):
                usercache_names[str(entry.get("uuid", "")).replace("-", "").lower()] = str(entry.get("name", ""))
    except Exception:
        pass

    banned_players = []
    for entry in _mc_read_json_list(os.path.join(server_dir, "banned-players.json")):
        if not isinstance(entry, dict):
            continue
        player_uuid = str(entry.get("uuid", ""))
        name = str(entry.get("name", "")) or usercache_names.get(player_uuid.replace("-", "").lower(), "")
        head_target = name or player_uuid
        banned_players.append({
            "uuid": player_uuid,
            "name": name or "Unknown player",
            "head": f"https://mc-heads.net/avatar/{quote(head_target)}/48" if head_target else "",
            "created": str(entry.get("created", "")),
            "source": str(entry.get("source", "")),
            "expires": str(entry.get("expires", "")),
            "reason": str(entry.get("reason", "")),
        })

    owners_path = os.path.join(server_dir, ".ip_ban_owners.json")
    try:
        with open(owners_path, "r", encoding="utf-8") as f:
            ip_owners = json.load(f)
        if not isinstance(ip_owners, dict):
            ip_owners = {}
    except Exception:
        ip_owners = {}
    normalized_owners = {_mc_normalize_ip(key): value for key, value in ip_owners.items()}
    banned_ips = []
    for entry in _mc_read_json_list(os.path.join(server_dir, "banned-ips.json")):
        if not isinstance(entry, dict):
            continue
        address = _mc_normalize_ip(entry.get("ip", ""))
        owner = normalized_owners.get(address, {})
        if not isinstance(owner, dict):
            owner = {}
        player_name = str(owner.get("name", "") or entry.get("name", ""))
        player_uuid = str(owner.get("uuid", ""))
        head_target = player_name or player_uuid
        banned_ips.append({
            "ip": address,
            "name": player_name,
            "uuid": player_uuid,
            "head": f"https://mc-heads.net/avatar/{quote(head_target)}/48" if head_target else "",
            "created": str(entry.get("created", "")),
            "source": str(entry.get("source", "")),
            "expires": str(entry.get("expires", "")),
            "reason": str(entry.get("reason", "")),
        })

    result = {"banned_players": banned_players, "banned_ips": banned_ips}
    mc_bans_cache[server_name] = (time.monotonic(), result)
    return result

async def mc_api_players(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    online = await _mc_get_live_players(name)
    server_dir = _mc_safe_server_dir(name)
    known = set(online)
    if server_dir:
        try:
            with open(os.path.join(server_dir, "usercache.json"), "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    n = str(entry.get("name", ""))
                    if n:
                        known.add(n)
        except Exception:
            pass
    players = []
    for player_name in known:
        players.append({
            "name": player_name,
            "head": f"https://mc-heads.net/avatar/{quote(player_name)}/48",
            "skin": bool(_mc_find_skin_url(server_dir, player_name)) if server_dir else False,
            "online": player_name in online,
        })
    players.sort(key=lambda p: (not p["online"], p["name"].lower()))
    access = None
    if _mc_can(request, name, "access"):
        acl = _mc_load_acl()
        entry = acl["servers"].get(name) or {"owner": "", "users": {}}
        owner_id = entry.get("owner", "")
        users = []
        if owner_id:
            users.append(await _mc_access_profile(owner_id, owner_id, list(MC_PRIVILEGES)))
        for user_id in sorted((entry.get("users") or {}).keys()):
            if user_id != owner_id:
                users.append(await _mc_access_profile(user_id, owner_id, entry["users"].get(user_id, [])))
        access = {
            "owner": owner_id,
            "users": users,
            "privileges": list(MC_PRIVILEGES),
            "is_admin": _is_mc_admin(request),
            "can_grant_admin": _is_mc_owner(request),
        }
    bans = _mc_get_server_bans(name)
    return web.json_response({
        "players": players,
        "access": access,
        **bans,
    })

def _valid_mc_player_name(name):
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,16}", name or ""))

def _parse_nbt_items(lines):
    text = " ".join(lines)
    compounds = []
    depth = 0
    start = None
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if char == '"' and not escaped:
            quoted = not quoted
        escaped = char == "\\" and not escaped
        if quoted:
            continue
        if char == "{" and depth == 0:
            start = index
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                compounds.append(text[start:index + 1])
                start = None
    items = []
    for raw in compounds:
        item_id = re.search(r"\bid:\s*\"?([a-z0-9_.:-]+)", raw, re.IGNORECASE)
        if not item_id:
            items.append({"id": None, "count": 0, "slot": None, "enchants": [], "raw": raw})
            continue
        count_match = re.search(r"\b(?:count|Count):\s*(\d+)", raw)
        slot_match = re.search(r"\bSlot:\s*(-?\d+)", raw)
        enchantments = []
        for match in re.finditer(r"id:\s*\"minecraft:([a-z0-9_]+)\"\s*,\s*(?:lvl|level):\s*(\d+)", raw, re.IGNORECASE):
            enchantments.append({"name": match.group(1).replace("_", " "), "level": int(match.group(2))})
        for match in re.finditer(r"\"minecraft:([a-z0-9_]+)\"\s*:\s*(\d+)", raw, re.IGNORECASE):
            entry = {"name": match.group(1).replace("_", " "), "level": int(match.group(2))}
            if entry not in enchantments:
                enchantments.append(entry)
        items.append({
            "id": item_id.group(1),
            "count": int(count_match.group(1)) if count_match else 1,
            "slot": int(slot_match.group(1)) if slot_match else None,
            "enchants": enchantments,
            "raw": raw,
        })
    return items

class _NBTReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, size):
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def number(self, fmt):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read(size))[0]

    def string(self):
        size = self.number(">H")
        return self.read(size).decode("utf-8", errors="replace")

    def payload(self, tag_type):
        if tag_type == 1:
            return self.number(">b")
        if tag_type == 2:
            return self.number(">h")
        if tag_type == 3:
            return self.number(">i")
        if tag_type == 4:
            return self.number(">q")
        if tag_type == 5:
            return self.number(">f")
        if tag_type == 6:
            return self.number(">d")
        if tag_type == 7:
            return self.read(self.number(">i"))
        if tag_type == 8:
            return self.string()
        if tag_type == 9:
            child_type = self.number(">B")
            count = self.number(">i")
            return [self.payload(child_type) for _ in range(max(0, count))]
        if tag_type == 10:
            result = {}
            while True:
                child_type = self.number(">B")
                if child_type == 0:
                    return result
                result[self.string()] = self.payload(child_type)
        if tag_type == 11:
            return [self.number(">i") for _ in range(max(0, self.number(">i")))]
        if tag_type == 12:
            return [self.number(">q") for _ in range(max(0, self.number(">i")))]
        raise ValueError(f"Unknown NBT tag {tag_type}")

def _read_nbt_file(path):
    with open(path, "rb") as f:
        compressed = f.read()
    for decoder in (gzip.decompress, zlib.decompress, lambda value: value):
        try:
            data = decoder(compressed)
            reader = _NBTReader(data)
            tag_type = reader.number(">B")
            if tag_type != 10:
                return None
            reader.string()
            return reader.payload(tag_type)
        except Exception:
            continue
    return None

async def _mc_online_uuid(player_name):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.mojang.com/users/profiles/minecraft/{quote(player_name)}") as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("id", "")
    except Exception:
        pass
    return ""

async def _mc_read_playerdata(server_dir, player_name):
    level_name = "world"
    props_path = os.path.join(server_dir, "server.properties")
    if os.path.exists(props_path):
        with open(props_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("level-name="):
                    level_name = line.split("=", 1)[1].strip() or level_name
                    break
    playerdata_dirs = []
    for world_root in (os.path.join(server_dir, level_name), os.path.join(server_dir, "world")):
        playerdata_dir = os.path.join(world_root, "playerdata")
        if os.path.isdir(playerdata_dir) and playerdata_dir not in playerdata_dirs:
            playerdata_dirs.append(playerdata_dir)
    if not playerdata_dirs:
        for current, dirs, _ in os.walk(server_dir):
            if "playerdata" in dirs:
                playerdata_dirs.append(os.path.join(current, "playerdata"))
                break
    if not playerdata_dirs:
        return None
    candidate_ids = {_mc_offline_uuid(player_name).hex}
    usercache_path = os.path.join(server_dir, "usercache.json")
    if os.path.exists(usercache_path):
        try:
            with open(usercache_path, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    if str(entry.get("name", "")).lower() == player_name.lower():
                        candidate_ids.add(str(entry.get("uuid", "")).replace("-", "").lower())
        except Exception:
            pass
    for playerdata_dir in playerdata_dirs:
        for filename in os.listdir(playerdata_dir):
            if filename.lower().endswith(".dat") and filename[:-4].replace("-", "").lower() in candidate_ids:
                return _read_nbt_file(os.path.join(playerdata_dir, filename))
    online_uuid = await _mc_online_uuid(player_name)
    if online_uuid:
        candidate_ids.add(online_uuid.replace("-", "").lower())
    for playerdata_dir in playerdata_dirs:
        for filename in os.listdir(playerdata_dir):
            if filename.lower().endswith(".dat") and filename[:-4].replace("-", "").lower() in candidate_ids:
                return _read_nbt_file(os.path.join(playerdata_dir, filename))
    return None


class _SNBTParser:
    def __init__(self, text):
        self.text = text
        self.index = 0

    def _skip_space(self):
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _quoted(self):
        quote_char = self.text[self.index]
        self.index += 1
        result = []
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if char == quote_char:
                return "".join(result)
            if char == "\\" and self.index < len(self.text):
                escaped = self.text[self.index]
                self.index += 1
                result.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
            else:
                result.append(char)
        raise ValueError("Unterminated SNBT string")

    def _scalar(self):
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ",}]":
            self.index += 1
        token = self.text[start:self.index].strip()
        lowered = token.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        number = re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)([bBsSlLfFdD]?)", token)
        if number:
            raw, suffix = number.groups()
            if suffix.lower() in ("f", "d") or "." in raw or "e" in raw.lower():
                return float(raw)
            return int(raw)
        return token

    def _value(self):
        self._skip_space()
        if self.index >= len(self.text):
            raise ValueError("Missing SNBT value")
        char = self.text[self.index]
        if char in "\"'":
            return self._quoted()
        if char == "{":
            self.index += 1
            result = {}
            self._skip_space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return result
            while self.index < len(self.text):
                self._skip_space()
                key = self._quoted() if self.text[self.index] in "\"'" else self._read_key()
                self._skip_space()
                if self.index >= len(self.text) or self.text[self.index] != ":":
                    raise ValueError("Expected ':' in SNBT compound")
                self.index += 1
                result[key] = self._value()
                self._skip_space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "}":
                    self.index += 1
                    return result
                raise ValueError("Expected ',' or '}' in SNBT compound")
            raise ValueError("Unterminated SNBT compound")
        if char == "[":
            self.index += 1
            self._skip_space()
            if re.match(r"[BILbil]\s*;", self.text[self.index:]):
                self.index = self.text.index(";", self.index) + 1
            values = []
            self._skip_space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return values
            while self.index < len(self.text):
                values.append(self._value())
                self._skip_space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "]":
                    self.index += 1
                    return values
                raise ValueError("Expected ',' or ']' in SNBT list")
            raise ValueError("Unterminated SNBT list")
        return self._scalar()

    def _read_key(self):
        start = self.index
        while self.index < len(self.text) and self.text[self.index] != ":":
            self.index += 1
        if self.index >= len(self.text):
            raise ValueError("Unterminated SNBT key")
        return self.text[start:self.index].strip()

    def parse(self):
        value = self._value()
        self._skip_space()
        if self.index != len(self.text):
            raise ValueError("Unexpected trailing SNBT data")
        return value


def _mc_parse_live_player_data(lines):
    for line in reversed(lines):
        marker = re.search(r"following entity data:\s*", line, re.IGNORECASE)
        if not marker:
            continue
        snbt = line[marker.end():].strip()
        if len(snbt) > 2_000_000:
            return None
        try:
            parsed = _SNBTParser(snbt).parse()
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            print(f"[MINECRAFT] Could not parse live player NBT: {e}")
    return None


async def _mc_read_live_playerdata(server_name, player_name):
    key = (server_name.lower(), player_name.lower())
    now = time.monotonic()
    cached = mc_live_player_cache.get(key)
    if cached and now - cached[0] < 0.08:
        return cached[1], cached[2]
    lock = mc_live_player_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = mc_live_player_cache.get(key)
        if cached and now - cached[0] < 0.08:
            return cached[1], cached[2]
        lines = await _mc_run_command_capture(
            server_name, f"data get entity {player_name}", timeout=1.0, silent=True
        )
        player_data = _mc_parse_live_player_data(lines)
        player_online = any("following entity data:" in line.lower() for line in lines)
        mc_live_player_cache[key] = (time.monotonic(), player_data, player_online)
        return player_data, player_online


def _structured_nbt_item(item):
    if not isinstance(item, dict) or not item.get("id"):
        return None
    enchantments = []
    legacy = item.get("Enchantments", [])
    if isinstance(legacy, list):
        for enchantment in legacy:
            if isinstance(enchantment, dict) and enchantment.get("id"):
                enchantments.append({"name": str(enchantment["id"]).replace("minecraft:", "").replace("_", " "), "level": enchantment.get("lvl", 1)})
    components = item.get("components", {})
    if isinstance(components, dict):
        for key in ("minecraft:enchantments", "minecraft:stored_enchantments"):
            levels = components.get(key, {}).get("levels", {}) if isinstance(components.get(key), dict) else {}
            if isinstance(levels, dict):
                for enchantment, level in levels.items():
                    entry = {"name": str(enchantment).replace("minecraft:", "").replace("_", " "), "level": level}
                    if entry not in enchantments:
                        enchantments.append(entry)
    return {
        "id": str(item.get("id")),
        "count": int(item.get("Count", item.get("count", 1))),
        "enchants": enchantments,
        "raw": json.dumps(item, default=str),
    }

async def _mc_run_command_capture(server_name, command, timeout=2.0, silent=False):
    proc = mc_processes.get(server_name)
    if not proc or proc.returncode is not None or not proc.stdin:
        return ["Server is not running."]
    lock = mc_command_locks.setdefault(server_name, asyncio.Lock())
    async with lock:
        proc = mc_processes.get(server_name)
        if not proc or proc.returncode is not None or not proc.stdin:
            return ["Server is not running."]
        state = {"command": command, "lines": [], "event": asyncio.Event(), "silent": silent}
        mc_command_capture_states[server_name] = state
        try:
            proc.stdin.write((command + "\n").encode("utf-8"))
            await proc.stdin.drain()
            try:
                await asyncio.wait_for(state["event"].wait(), timeout=max(0.05, timeout))
                await asyncio.sleep(0.02)
            except asyncio.TimeoutError:
                pass
            return list(state["lines"])
        except Exception as e:
            return [str(e)]
        finally:
            if mc_command_capture_states.get(server_name) is state:
                mc_command_capture_states.pop(server_name, None)

async def mc_api_player(request):
    server_name = request.query.get("server", "")
    player_name = request.query.get("player", "").strip()
    if not _mc_can(request, server_name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _valid_mc_player_name(player_name):
        return web.json_response({"error": "Invalid player name"}, status=400)
    server_dir = _mc_safe_server_dir(server_name)
    live_player_data, player_online = await _mc_read_live_playerdata(server_name, player_name) if server_dir else (None, False)
    player_data = live_player_data
    if not player_data and server_dir:
        player_data = await _mc_read_playerdata(server_dir, player_name)
    if not player_data:
        return web.json_response({"error": "No live player data or saved player data was found. The player may be offline or not yet saved.", "online": player_online}, status=404)
    inventory = {}
    for item in player_data.get("Inventory", []) if isinstance(player_data.get("Inventory", []), list) else []:
        structured = _structured_nbt_item(item)
        if structured and item.get("Slot") is not None and 0 <= int(item["Slot"]) <= 35:
            slot_number = int(item["Slot"])
            slot_name = f"hotbar.{slot_number}" if slot_number < 9 else f"inventory.{slot_number - 9}"
            inventory[slot_name] = structured
    armor_slots = ["armor.feet", "armor.legs", "armor.chest", "armor.head"]
    for index, item in enumerate(player_data.get("ArmorItems", [])[:4]):
        structured = _structured_nbt_item(item)
        if structured:
            inventory[armor_slots[index]] = structured
    hands = player_data.get("HandItems", [])
    if hands and _structured_nbt_item(hands[0]):
        inventory["weapon.mainhand"] = _structured_nbt_item(hands[0])
    if len(hands) > 1 and _structured_nbt_item(hands[1]):
        inventory["weapon.offhand"] = _structured_nbt_item(hands[1])
    has_custom_skin = bool(server_dir and _mc_find_skin_url(server_dir, player_name))
    asset_version = _mc_detect_ver(server_dir) if server_dir else None
    details = {
        "Health": [str(player_data.get("Health", "Unknown"))],
        "Position": [json.dumps(player_data.get("Pos", []), default=str)],
        "Dimension": [str(player_data.get("Dimension", "Unknown"))],
        "Experience": [str(player_data.get("XpLevel", "Unknown"))],
        "Inventory": [json.dumps(player_data.get("Inventory", []), default=str)],
        "ArmorItems": [json.dumps(player_data.get("ArmorItems", []), default=str)],
        "HandItems": [json.dumps(player_data.get("HandItems", []), default=str)],
    }
    return web.json_response({
        "name": player_name,
        "skin": has_custom_skin,
        "skin_url": f"/api/mc/player-skin?server={quote(server_name)}&player={quote(player_name)}" if has_custom_skin else "",
        "asset_version": asset_version or "1.21.11",
        "inventory": inventory,
        "details": details,
        "live": live_player_data is not None,
        "online": player_online,
    })

async def mc_api_inventory_move(request):
    data = await request.json()
    server_name = data.get("server", "")
    player_name = str(data.get("player", "")).strip()
    source = data.get("source", "")
    destination = data.get("destination", "")
    if not _mc_can(request, server_name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    slot_pattern = r"(?:hotbar\.\d+|inventory\.\d+|armor\.(?:head|chest|legs|feet)|weapon\.(?:mainhand|offhand))"
    if not _valid_mc_player_name(player_name) or not re.fullmatch(slot_pattern, source or "") or not re.fullmatch(slot_pattern, destination or ""):
        return web.json_response({"error": "Invalid inventory slot"}, status=400)
    if source == destination:
        return web.json_response({"ok": True})
    proc = mc_processes.get(server_name)
    if not proc or proc.returncode is not None:
        return web.json_response({"error": "Server not running"}, status=400)
    def nbt_path(slot):
        if slot.startswith("hotbar."):
            return f"Inventory[{{Slot:{slot.split('.')[1]}b}}]"
        if slot.startswith("inventory."):
            return f"Inventory[{{Slot:{int(slot.split('.')[1]) + 9}b}}]"
        if slot == "armor.feet":
            return "ArmorItems[0]"
        if slot == "armor.legs":
            return "ArmorItems[1]"
        if slot == "armor.chest":
            return "ArmorItems[2]"
        if slot == "armor.head":
            return "ArmorItems[3]"
        return f"HandItems[{0 if slot == 'weapon.mainhand' else 1}]"

    storage = "website:inventory"
    source_path = nbt_path(source)
    destination_path = nbt_path(destination)
    proc_check = mc_processes.get(server_name)
    if not proc_check or proc_check.returncode is not None:
        return web.json_response({"error": "Server not running"}, status=400)
    await _mc_run_command_capture(server_name, f"data remove storage {storage} source")
    await _mc_run_command_capture(server_name, f"data remove storage {storage} destination")
    await _mc_run_command_capture(server_name, f"execute if data entity {player_name} {source_path} run data modify storage {storage} source set from entity {player_name} {source_path}")
    await _mc_run_command_capture(server_name, f"execute if data entity {player_name} {destination_path} run data modify storage {storage} destination set from entity {player_name} {destination_path}")
    proc_check = mc_processes.get(server_name)
    if not proc_check or proc_check.returncode is not None:
        return web.json_response({"error": "Server stopped during move; items were not modified"}, status=400)
    await _mc_run_command_capture(server_name, f"item replace entity {player_name} {source} with air")
    await _mc_run_command_capture(server_name, f"item replace entity {player_name} {destination} with air")
    await _mc_run_command_capture(server_name, f"execute if data storage {storage} source run data modify entity {player_name} {destination_path} set from storage {storage} source")
    await _mc_run_command_capture(server_name, f"execute if data storage {storage} destination run data modify entity {player_name} {source_path} set from storage {storage} destination")
    await _mc_run_command_capture(server_name, "save-all flush")
    return web.json_response({"ok": True})

async def mc_api_player_action(request):
    data = await request.json()
    server_name = data.get("server", "")
    player_name = str(data.get("player", "")).strip()
    action = data.get("action", "")
    reason = str(data.get("reason", "Website action")).replace("\n", " ").strip()[:200]
    if not _mc_can(request, server_name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _valid_mc_player_name(player_name):
        return web.json_response({"error": "Invalid player name"}, status=400)
    commands = {
        "kick": f"kick {player_name} {reason}",
        "ban": f"ban {player_name} {reason}",
        "ban-ip": f"ban-ip {player_name} {reason}",
        "op": f"op {player_name}",
        "unop": f"deop {player_name}",
        "fly": f"ability {player_name} mayfly true",
        "unfly": f"ability {player_name} mayfly false",
    }
    if action not in commands:
        return web.json_response({"error": "Invalid player action"}, status=400)
    server_dir = _mc_safe_server_dir(server_name)
    before_ips = set()
    if action == "ban-ip" and server_dir:
        before_ips = {
            _mc_normalize_ip(entry.get("ip", ""))
            for entry in _mc_read_json_list(os.path.join(server_dir, "banned-ips.json"))
            if isinstance(entry, dict) and entry.get("ip")
        }
    output = await _mc_run_command_capture(server_name, commands[action])
    if action == "ban-ip" and server_dir:
        after_ips = [
            _mc_normalize_ip(entry.get("ip", ""))
            for entry in _mc_read_json_list(os.path.join(server_dir, "banned-ips.json"))
            if isinstance(entry, dict) and entry.get("ip")
        ]
        added_ips = [address for address in after_ips if address not in before_ips]
        if added_ips:
            owners_path = os.path.join(server_dir, ".ip_ban_owners.json")
            try:
                with open(owners_path, "r", encoding="utf-8") as f:
                    owners = json.load(f)
                if not isinstance(owners, dict):
                    owners = {}
            except Exception:
                owners = {}
            owner = {"name": player_name, "uuid": _mc_player_uuid_from_cache(server_dir, player_name)}
            for address in added_ips:
                owners[address] = owner
            with open(owners_path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(owners, f, indent=2)
            os.replace(owners_path + ".tmp", owners_path)
    mc_bans_cache.pop(server_name, None)
    return web.json_response({"ok": True, "output": output})

def _mc_ban_player_matches(entry, target):
    name = str(entry.get("name", "")).lower()
    entry_uuid = str(entry.get("uuid", "")).replace("-", "").lower()
    target_lower = target.lower()
    target_uuid = target.replace("-", "").lower()
    return name == target_lower or (entry_uuid and entry_uuid == target_uuid)

async def mc_api_unban(request):
    data = await request.json()
    server_name = str(data.get("server", "")).strip()
    ban_type = str(data.get("type", "")).strip().lower()
    target = str(data.get("target", "")).strip()
    if not _mc_can(request, server_name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(server_name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)

    if ban_type == "player":
        if not (_valid_mc_player_name(target) or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", target)):
            return web.json_response({"error": "Invalid player or UUID"}, status=400)
        path = os.path.join(server_dir, "banned-players.json")
        entries = _mc_read_json_list(path)
        matching = [entry for entry in entries if isinstance(entry, dict) and _mc_ban_player_matches(entry, target)]
        if not matching:
            return web.json_response({"error": "Player is not banned"}, status=404)
        proc = mc_processes.get(server_name)
        if proc and proc.returncode is None and proc.stdin:
            command_target = str(matching[0].get("name", "") or target)
            output = await _mc_run_command_capture(server_name, f"pardon {command_target}", timeout=2.0)
            result = "\n".join(output).lower()
            if "not banned" in result or "is not banned" in result:
                return web.json_response({"error": "Player was not banned by the running server."}, status=404)
        else:
            removed_ids = {
                (str(entry.get("uuid", "")).replace("-", "").lower(), str(entry.get("name", "")).lower())
                for entry in matching
            }
            remaining = [entry for entry in entries if not isinstance(entry, dict) or (
                str(entry.get("uuid", "")).replace("-", "").lower(), str(entry.get("name", "")).lower()
            ) not in removed_ids]
            _mc_write_json_list(path, remaining)
    elif ban_type == "ip":
        try:
            target = str(ipaddress.ip_address(target))
        except ValueError:
            return web.json_response({"error": "Enter a valid IPv4 or IPv6 address"}, status=400)
        path = os.path.join(server_dir, "banned-ips.json")
        entries = _mc_read_json_list(path)
        matching = [entry for entry in entries if isinstance(entry, dict) and _mc_normalize_ip(entry.get("ip", "")) == target]
        if not matching:
            return web.json_response({"error": "IP address is not banned"}, status=404)
        proc = mc_processes.get(server_name)
        if proc and proc.returncode is None and proc.stdin:
            output = await _mc_run_command_capture(server_name, f"pardon-ip {target}", timeout=2.0)
            result = "\n".join(output).lower()
            if "not banned" in result or "is not banned" in result:
                return web.json_response({"error": "IP address was not banned by the running server."}, status=404)
        else:
            _mc_write_json_list(path, [entry for entry in entries if not isinstance(entry, dict) or _mc_normalize_ip(entry.get("ip", "")) != target])
        owners_path = os.path.join(server_dir, ".ip_ban_owners.json")
        try:
            with open(owners_path, "r", encoding="utf-8") as f:
                owners = json.load(f)
            if isinstance(owners, dict):
                owners = {key: value for key, value in owners.items() if _mc_normalize_ip(key) != target}
                with open(owners_path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(owners, f, indent=2)
                os.replace(owners_path + ".tmp", owners_path)
        except Exception:
            pass
    else:
        return web.json_response({"error": "Ban type must be player or ip"}, status=400)

    mc_bans_cache.pop(server_name, None)
    return web.json_response({"ok": True})

async def mc_api_player_skin(request):
    name = request.query.get("server", "")
    player = request.query.get("player", "").strip()
    if not player or not _mc_can(request, name, "players"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    skin_url = _mc_find_skin_url(server_dir, player) if server_dir else None
    if not skin_url:
        return web.Response(status=404)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(skin_url) as response:
                if response.status != 200:
                    return web.Response(status=404)
                return web.Response(body=await response.read(), content_type="image/png", headers={"Cache-Control": "no-cache"})
    except Exception:
        return web.Response(status=404)

async def mc_api_start(request):
    data = await request.json()
    name = data.get("name", "")
    if not _mc_can(request, name, "start"):
        return web.json_response({"error": "unauthorized"}, status=401)
    loader_type = data.get("loader", "fabric")
    ok, err = await _mc_start_server(name, loader_type)
    if not ok:
        status = 400 if err in ("Already running", "Server folder not found") else 500
        return web.json_response({"error": err}, status=status)
    proc = mc_processes.get(name)
    return web.json_response({"ok": True, "pid": proc.pid if proc else None})


async def _mc_start_server(name, loader_type="fabric", autostart=False):
    """Start (installing if needed) a server; returns (ok, error)."""
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return False, "Server folder not found"
    if name in mc_processes and mc_processes[name] is not None and mc_processes[name].returncode is None:
        return False, "Already running"
    loader_file = os.path.join(server_dir, ".loader")
    if os.path.exists(loader_file):
        with open(loader_file, "r") as f:
            loader_type = f.read().strip()
    version_file = os.path.join(server_dir, ".mc_version")
    selected_version = None
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            selected_version = f.read().strip() or None
    jar = _mc_find_jar(server_dir)
    is_forge = _mc_is_forge(server_dir)
    if not jar and not is_forge:
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] No server.jar found, installing {loader_type.title()} server...")
        try:
            from minecraft_setup import setup_server
            ok = await asyncio.to_thread(setup_server, server_dir, loader_type, selected_version)
            if not ok:
                _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] Failed to install {loader_type.title()} server.")
                return False, f"{loader_type.title()} install failed"
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] {loader_type.title()} installed successfully.")
        except Exception as e:
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] Setup error: {e}")
            return False, str(e)
        jar = _mc_find_jar(server_dir)
        is_forge = _mc_is_forge(server_dir)
        if not jar and not is_forge:
            _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] Still no server.jar after install.")
            return False, "No server jar after install"
    mem = _mc_memory_limit()
    java = _mc_get_java(server_dir)
    forge_jvm_args_str = " ".join(FORGE_JVM_ARGS)
    try:
        if is_forge:
            run_bat = os.path.join(server_dir, "run.bat")
            run_sh = os.path.join(server_dir, "run.sh")
            forge_args = _mc_find_forge_args(server_dir)
            user_jvm = _mc_find_user_jvm_args(server_dir)
            if os.name == "nt" and os.path.exists(run_bat):
                env = {**os.environ, "JAVA_FLAGS": f"-Xmx{mem} -Xms{mem} {forge_jvm_args_str}"}
                proc = await asyncio.create_subprocess_exec(
                    "cmd", "/c", "run.bat",
                    cwd=server_dir,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            elif os.path.exists(run_sh):
                env = {**os.environ, "JAVA_FLAGS": f"-Xmx{mem} -Xms{mem} {forge_jvm_args_str}"}
                proc = await asyncio.create_subprocess_exec(
                    "bash", "run.sh",
                    cwd=server_dir,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            elif forge_args:
                extra_args = []
                if user_jvm:
                    with open(user_jvm, "r") as uf:
                        extra_args = [l.strip() for l in uf.readlines() if l.strip() and not l.strip().startswith("#")]
                else:
                    extra_args = FORGE_JVM_ARGS
                proc = await asyncio.create_subprocess_exec(
                    java, f"-Xmx{mem}", f"-Xms{mem}", *extra_args, f"@{forge_args}", "nogui",
                    cwd=server_dir,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            elif jar:
                proc = await asyncio.create_subprocess_exec(
                    java, f"-Xmx{mem}", f"-Xms{mem}", *FORGE_JVM_ARGS, "-jar", jar, "nogui",
                    cwd=server_dir,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                return False, "No Forge launch method found"
        else:
            proc = await asyncio.create_subprocess_exec(
                java, f"-Xmx{mem}", f"-Xms{mem}", "-jar", jar, "nogui",
                cwd=server_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
    except Exception as exc:
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] Launch failed: {exc}")
        return False, str(exc)
    mc_processes[name] = proc
    mc_console_buffers.setdefault(name, [])
    _mc_mark_keepalive(name, True)
    asyncio.create_task(_mc_read_output(name, proc))
    if autostart:
        _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] >>> Auto-start engaged \u2014 this server will stay online.")
    return True, ""

async def mc_api_create(request):
    if not _mc_can_create(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "").strip()
    loader = data.get("loader", "fabric")
    version = data.get("version", "").strip()
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        return web.json_response({"error": "Invalid server name (alphanumeric, - _) only"}, status=400)
    if version and not re.match(r"^\d+\.\d+(?:\.\d+)?$", version):
        return web.json_response({"error": "Invalid Minecraft version"}, status=400)
    server_dir = os.path.join(MC_DIR, name)
    if os.path.exists(server_dir):
        return web.json_response({"error": "Server folder already exists"}, status=409)
    os.makedirs(server_dir, exist_ok=True)
    owner_id = _mc_user_id(request)
    if owner_id:
        acl = _mc_load_acl()
        entry = acl["servers"].setdefault(name, {"owner": "", "users": {}})
        entry["owner"] = owner_id
        entry.setdefault("users", {}).pop(owner_id, None)
        _mc_save_acl(acl)
    with open(os.path.join(server_dir, ".loader"), "w") as f:
        f.write(loader)
    if version:
        with open(os.path.join(server_dir, ".mc_version"), "w") as f:
            f.write(version)
    return web.json_response({"ok": True})

async def mc_api_stop(request):
    data = await request.json()
    name = data.get("name", "")
    if not _mc_can(request, name, "stop"):
        return web.json_response({"error": "unauthorized"}, status=401)
    # Manual stop: take the server off the auto-restart list FIRST so the
    # process exit below never triggers the crash supervisor.
    _mc_mark_keepalive(name, False)
    task = mc_restart_tasks.pop(name, None)
    if task is not None and not task.done():
        task.cancel()
    mc_restart_attempts.pop(name, None)
    proc = mc_processes.get(name)
    if not proc or proc.returncode is not None:
        return web.json_response({"error": "Not running"}, status=400)
    try:
        proc.stdin.write(b"save-all flush\n")
        await proc.stdin.drain()
    except Exception:
        pass
    await asyncio.sleep(1)
    try:
        proc.stdin.write(b"stop\n")
        await proc.stdin.drain()
    except Exception:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
    mc_processes[name] = None
    return web.json_response({"ok": True})

async def mc_api_command(request):
    data = await request.json()
    name = data.get("name", "")
    cmd = data.get("command", "")
    if not _mc_can(request, name, "command"):
        return web.json_response({"error": "unauthorized"}, status=401)
    proc = mc_processes.get(name)
    if not proc or proc.returncode is not None:
        return web.json_response({"error": "Server not running"}, status=400)
    try:
        proc.stdin.write((cmd + "\n").encode("utf-8"))
        await proc.stdin.drain()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True})

async def mc_api_console(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "console"):
        return web.json_response({"error": "unauthorized"}, status=401)
    buf = mc_console_buffers.get(name, [])
    lines = [text for _n, text in buf[-200:]]
    return web.json_response({"lines": lines, "n": mc_line_counters.get(name, 0)})


async def mc_api_mods(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "mods"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    mods_dir = _mc_resolve_server_path(server_dir, "mods")
    if not mods_dir:
        return web.json_response({"mods": []})
    if not os.path.isdir(mods_dir):
        return web.json_response({"mods": []})
    mods = []
    for f in sorted(os.listdir(mods_dir)):
        if f.endswith(".jar"):
            fp = os.path.join(mods_dir, f)
            mods.append({"name": f, "size": os.path.getsize(fp)})
    return web.json_response({"mods": mods})

async def mc_api_icon(request):
    name = request.query.get("name", "")
    if not _can_access_mc_server(request, name):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    icon_path = _mc_resolve_server_path(server_dir, "server-icon.png")
    if not icon_path or not os.path.isfile(icon_path):
        return web.Response(status=404)
    return web.FileResponse(icon_path, headers={"Cache-Control": "no-cache"})

async def mc_api_upload_icon(request):
    reader = await request.multipart()
    server_name = None
    server_dir = None
    icon_data = None
    while True:
        field = await reader.next()
        if not field:
            break
        if field.name == "server":
            server_name = (await field.read()).decode(errors="replace").strip()
            # Check permission before buffering the image.
            if not _mc_can(request, server_name, "icon"):
                return web.json_response({"error": "unauthorized"}, status=401)
            server_dir = _mc_safe_server_dir(server_name)
            if not server_dir:
                return web.json_response({"error": "Server not found"}, status=404)
        elif field.name == "icon":
            if not server_dir:
                return web.json_response({"error": "Send the server before the icon"}, status=400)
            if not field.filename or not field.filename.lower().endswith(".png"):
                return web.json_response({"error": "Upload a PNG image."}, status=400)
            icon_data = await field.read()
    if not _mc_can(request, server_name or "", "icon"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    if not icon_data or len(icon_data) > 2 * 1024 * 1024:
        return web.json_response({"error": "Icon must be smaller than 2 MB."}, status=400)
    if icon_data[:8] != b"\x89PNG\r\n\x1a\n" or len(icon_data) < 24:
        return web.json_response({"error": "Invalid PNG image."}, status=400)
    if icon_data[12:16] != b"IHDR":
        return web.json_response({"error": "Invalid PNG image."}, status=400)
    width = int.from_bytes(icon_data[16:20], "big")
    height = int.from_bytes(icon_data[20:24], "big")
    if width != 64 or height != 64:
        return web.json_response({"error": "Minecraft icons must be exactly 64x64 pixels."}, status=400)
    icon_path = _mc_resolve_server_path(server_dir, "server-icon.png")
    if not icon_path:
        return web.json_response({"error": "Invalid path"}, status=400)
    with open(icon_path, "wb") as f:
        f.write(icon_data)
    return web.json_response({"ok": True})


async def mc_api_upload_mod(request):
    reader = await request.multipart()
    server_name = None
    server_dir = None
    filename = None
    temp_path = None
    max_size = 512 * 1024 * 1024

    def cleanup():
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    while True:
        part = await reader.next()
        if not part:
            break
        if part.name == "server":
            server_name = (await part.read()).decode(errors="replace").strip()
            # Authorize before accepting any mod bytes.
            if not _mc_can(request, server_name, "mods"):
                cleanup()
                return web.json_response({"error": "unauthorized"}, status=401)
            server_dir = _mc_safe_server_dir(server_name)
            if not server_dir:
                cleanup()
                return web.json_response({"error": "Server not found"}, status=404)
        elif part.name == "file":
            if not server_dir:
                return web.json_response({"error": "Send the server before the file"}, status=400)
            filename = os.path.basename(part.filename or "")
            if not filename or not filename.lower().endswith(".jar"):
                return web.json_response({"error": "Only .jar files allowed"}, status=400)
            temp_path = os.path.join(MC_DIR, f".mod-upload-{uuid.uuid4().hex}.tmp")
            total = 0
            try:
                with open(temp_path, "wb") as temp_file:
                    while True:
                        chunk = await part.read_chunk(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_size:
                            cleanup()
                            return web.json_response({"error": "Mod must be smaller than 512 MB."}, status=400)
                        temp_file.write(chunk)
            except Exception:
                cleanup()
                raise
    if not _mc_can(request, server_name or "", "mods"):
        cleanup()
        return web.json_response({"error": "unauthorized"}, status=401)
    if not server_dir:
        cleanup()
        return web.json_response({"error": "Server not found"}, status=404)
    if not filename or not temp_path or not os.path.isfile(temp_path):
        cleanup()
        return web.json_response({"error": "No mod file"}, status=400)
    mods_dir = _mc_resolve_server_path(server_dir, "mods")
    dest = _mc_resolve_server_path(server_dir, _mc_join_rel_path("mods", filename))
    if not mods_dir or not dest or (os.path.exists(mods_dir) and not os.path.isdir(mods_dir)):
        cleanup()
        return web.json_response({"error": "Invalid path"}, status=400)
    try:
        os.makedirs(mods_dir, exist_ok=True)
        os.replace(temp_path, dest)
    except OSError as e:
        cleanup()
        return web.json_response({"error": str(e)}, status=500)
    buf = mc_console_buffers.setdefault(server_name, [])
    _mc_buf_push(server_name, f"[{time.strftime('%H:%M:%S')}] Mod installed: {filename}")
    return web.json_response({"ok": True, "name": filename})


async def mc_api_delete_mod(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    mod = os.path.basename(str(data.get("mod", "")).strip())
    if not _mc_can(request, name, "mods"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not name or not mod or mod in (".", ".."):
        return web.json_response({"error": "Missing params"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    mod_path = _mc_resolve_server_path(server_dir, _mc_join_rel_path("mods", mod))
    if not mod_path:
        return web.json_response({"error": "Invalid mod name"}, status=400)
    if not os.path.isfile(mod_path):
        return web.json_response({"error": "Mod not found"}, status=404)
    os.remove(mod_path)
    _mc_buf_push(name, f"[{time.strftime('%H:%M:%S')}] Mod removed: {mod}")
    return web.json_response({"ok": True})

async def mc_ws_console(request):
    name = request.match_info.get("name", "")
    if not _mc_can(request, name, "console"):
        return web.Response(status=401)
    can_send = _mc_can(request, name, "command")
    # heartbeat: aiohttp pings every 15s and closes sockets that never pong,
    # so a half-dead wifi connection can't linger looking alive forever.
    ws = web.WebSocketResponse(heartbeat=15.0)
    await ws.prepare(request)
    ws_list = mc_ws_clients.setdefault(name, [])
    ws_list.append(ws)
    # Backfill anything the client missed while it was away.
    buf = mc_console_buffers.get(name, [])
    if buf:
        try:
            await ws.send_json({
                "type": "history",
                "lines": [text for _n, text in buf[-200:]],
                "n": mc_line_counters.get(name, 0),
            })
        except Exception:
            pass
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    d = json.loads(msg.data)
                    if d.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
                    elif d.get("type") == "command" and can_send:
                        proc = mc_processes.get(name)
                        if proc and proc.returncode is None and proc.stdin:
                            proc.stdin.write((d.get("command", "") + "\n").encode("utf-8"))
                            await proc.stdin.drain()
                        else:
                            await ws.send_json({"type": "error", "message": "Server not running"})
                except Exception:
                    pass
            elif msg.type == web.WSMsgType.ERROR:
                break
    except Exception:
        pass
    if ws in ws_list:
        ws_list.remove(ws)
    return ws

MC_PAGE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MC Console</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:#050508;color:#e0e0e0;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
  height:100vh;display:flex;flex-direction:column;overflow:hidden;position:relative
}
button{font-family:inherit;cursor:pointer;transition:filter .15s ease,transform .15s ease,border-color .15s ease,background-color .15s ease}
button:not(:disabled):hover{filter:brightness(1.12);transform:translateY(-1px)}
button:not(:disabled):active{transform:translateY(0) scale(.98)}
button:focus-visible{outline:2px solid rgba(0,220,120,.75);outline-offset:2px}
button:disabled{opacity:.5;cursor:not-allowed}
.bg-grid{
  position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;
  background-image:
    linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);
  background-size:60px 60px;
}
.bg-glow{
  position:fixed;width:600px;height:600px;border-radius:50%;
  background:radial-gradient(circle,rgba(0,200,120,.06),transparent 70%);
  top:50%;left:50%;transform:translate(-50%,-50%);z-index:0;pointer-events:none;
  animation:pulse 6s ease-in-out infinite alternate
}
@keyframes pulse{
  0%{opacity:.6;transform:translate(-50%,-50%) scale(1)}
  100%{opacity:1;transform:translate(-50%,-50%) scale(1.15)}
}
.topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 20px;background:rgba(13,13,20,.8);
  border-bottom:1px solid rgba(0,220,120,.1);flex-shrink:0;position:relative;z-index:10;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)
}
.topbar .logo{font-size:18px;font-weight:700;color:#fff}
.topbar .logo .lua{color:#00dc78}
.topbar .user{display:flex;align-items:center;gap:10px;font-size:13px;color:rgba(255,255,255,.5)}
.topbar .user img{width:28px;height:28px;border-radius:50%;border:1.5px solid rgba(0,220,120,.2)}
.topbar .user .name{color:#fff;font-weight:600}
.topbar .nav-link{padding:7px 10px;border-radius:7px;border:1px solid rgba(0,220,120,.25);color:#00dc78;text-decoration:none;font-size:12px;font-weight:600}
.topbar .nav-link:hover{background:rgba(0,220,120,.1)}
.main{display:flex;flex:1;overflow:hidden;position:relative;z-index:1}
.sidebar{
  width:220px;background:rgba(13,13,20,.8);
  border-right:1px solid rgba(0,220,120,.08);display:flex;flex-direction:column;flex-shrink:0;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)
}
.sidebar .title{padding:14px 16px 10px;font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:rgba(0,220,120,.4);font-weight:600}
.server-list{flex:1;overflow-y:auto;padding:0 8px}
.server-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 12px;border-radius:8px;cursor:pointer;transition:all .15s;margin-bottom:2px;
  border:1px solid transparent
}
.server-item:hover{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.04)}
.server-item.active{background:rgba(0,220,120,.06);border-color:rgba(0,220,120,.15)}
.server-item .name{font-size:13px;font-weight:500;color:rgba(255,255,255,.8)}
.server-item .server-icon{width:32px;height:32px;border-radius:7px;object-fit:cover;background:rgba(255,255,255,.05);flex-shrink:0;margin-right:9px}
.loader-tag{font-size:10px;padding:1px 5px;border-radius:4px;background:rgba(255,255,255,.05);color:rgba(255,255,255,.3);text-transform:uppercase;letter-spacing:.5px;margin-left:auto;margin-right:6px}
.server-item.active .loader-tag{color:rgba(255,255,255,.5)}
.server-item.active .name{color:#fff}
.server-item .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.server-item .dot.on{background:#22c55e;box-shadow:0 0 6px rgba(34,197,94,.5)}
.server-item .dot.off{background:rgba(255,255,255,.15)}
.toggle{position:relative;display:inline-block;width:40px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background-color:rgba(255,255,255,.1);border-radius:22px;transition:.3s}
.toggle-slider:before{position:absolute;content:"";height:16px;width:16px;left:3px;bottom:3px;background-color:#8b949e;border-radius:50%;transition:.3s}
.toggle input:checked+.toggle-slider{background-color:rgba(0,220,120,.3)}
.toggle input:checked+.toggle-slider:before{transform:translateX(18px);background-color:#00dc78}
.sidebar-bottom{padding:8px}
.sidebar-bottom input{
  width:100%;padding:8px 10px;border-radius:8px;
  border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.03);
  color:#fff;font-size:12px;font-family:inherit;outline:none;margin-bottom:6px;
  transition:border-color .2s
}
.sidebar-bottom input:focus{border-color:rgba(0,220,120,.3)}
.sidebar-bottom input::placeholder{color:rgba(255,255,255,.2)}
.sidebar-bottom button{
  width:100%;padding:8px;border-radius:8px;
  border:1px solid rgba(0,220,120,.2);background:rgba(0,220,120,.04);
  color:rgba(0,220,120,.8);font-size:12px;font-family:inherit;cursor:pointer;
  transition:all .2s;font-weight:500
}
.sidebar-bottom button:hover{background:rgba(0,220,120,.08);border-color:rgba(0,220,120,.35);color:#00dc78}
.sidebar-bottom select{
  width:100%;padding:7px 10px;border-radius:8px;
  border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.03);
  color:#fff;font-size:12px;font-family:inherit;outline:none;margin-bottom:6px;
  transition:border-color .2s;cursor:pointer;appearance:auto
}
.sidebar-bottom select:focus{border-color:rgba(0,220,120,.3)}
.sidebar-bottom select option{background:#0d0d14;color:#fff}
.console-wrap{flex:1;display:flex;flex-direction:column;overflow:hidden}
.console-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 20px;background:rgba(13,13,20,.8);
  border-bottom:1px solid rgba(0,220,120,.08);flex-shrink:0;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)
}
.console-header .server-name{font-size:15px;font-weight:600;color:#fff}
.console-header .actions{display:flex;gap:8px}
.console-header .actions button{
  padding:6px 14px;border-radius:8px;border:1px solid rgba(255,255,255,.06);
  background:rgba(255,255,255,.02);color:rgba(255,255,255,.5);font-size:12px;
  cursor:pointer;font-family:inherit;transition:all .2s;font-weight:500
}
.console-header .actions button:hover{background:rgba(255,255,255,.05);color:#fff}
.console-header .actions .start{border-color:rgba(34,197,94,.25);color:#22c55e}
.console-header .actions .files{border-color:rgba(0,220,120,.25);color:#00dc78}
.console-header .actions .files:hover{background:rgba(0,220,120,.08);border-color:rgba(0,220,120,.35);color:#00dc78}
.console-header .actions .version{border-color:rgba(168,85,247,.3);color:#c084fc}
.console-header .actions .version:hover{background:rgba(168,85,247,.1);color:#d8b4fe}
.console-header .actions .people{border-color:rgba(45,212,191,.3);color:#5eead4}
.console-header .actions .people:hover{background:rgba(45,212,191,.1);color:#99f6e4}
.console-header .actions .access{border-color:rgba(251,191,36,.3);color:#fbbf24}
.console-header .actions .access:hover{background:rgba(251,191,36,.1);color:#fde68a}
.console-header .actions .delete{border-color:rgba(239,68,68,.3);color:#f87171}
.console-header .actions .delete:hover{background:rgba(239,68,68,.1);color:#fca5a5}
.console-header .actions .backup{border-color:rgba(96,165,250,.3);color:#93c5fd}
.console-header .actions .backup:hover{background:rgba(96,165,250,.1);color:#bfdbfe}
.console-header .actions .icon-upload{border-color:rgba(88,101,242,.3);color:#8b9cf7}
.console-header .actions .icon-upload:hover{background:rgba(88,101,242,.1);color:#b7c0ff}
.console-header .actions .start:hover{background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.4)}
.console-header .actions .stop{border-color:rgba(239,68,68,.25);color:#ef4444}
.console-header .actions .stop:hover{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.4)}
.console-header .actions .mods{border-color:rgba(88,101,242,.25);color:#5865f2}
.console-header .actions .mods:hover{background:rgba(88,101,242,.08);border-color:rgba(88,101,242,.4)}
.console-header .actions .mods.active{background:rgba(88,101,242,.1);border-color:rgba(88,101,242,.4)}
.mods-panel{
  border-bottom:1px solid rgba(88,101,242,.08);background:rgba(13,13,20,.9);
  padding:12px 20px;flex-shrink:0;max-height:200px;overflow-y:auto;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)
}
.mods-panel .mods-title{font-size:12px;color:rgba(255,255,255,.4);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}
.mods-panel .mods-title label{
  padding:4px 10px;border-radius:6px;border:1px solid rgba(88,101,242,.25);
  background:rgba(88,101,242,.05);color:#5865f2;font-size:11px;cursor:pointer;
  font-family:inherit;transition:all .2s;font-weight:500;text-transform:none;letter-spacing:0
}
.mods-panel .mods-title label:hover{background:rgba(88,101,242,.1);border-color:rgba(88,101,242,.4)}
.mods-panel .mod-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:6px 10px;border-radius:6px;margin-bottom:2px;
  transition:background .15s;border:1px solid transparent
}
.mods-panel .mod-item:hover{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.04)}
.mods-panel .mod-item .mod-name{font-size:12px;color:rgba(255,255,255,.7)}
.mods-panel .mod-item .mod-size{font-size:11px;color:rgba(255,255,255,.25);margin-left:8px}
.mods-panel .mod-item .mod-del{
  padding:2px 8px;border-radius:4px;border:1px solid rgba(239,68,68,.2);
  background:transparent;color:rgba(239,68,68,.5);font-size:10px;cursor:pointer;
  font-family:inherit;transition:all .2s
}
.mods-panel .mod-item .mod-del:hover{background:rgba(239,68,68,.1);color:#ef4444;border-color:rgba(239,68,68,.4)}
.mods-panel .no-mods{font-size:12px;color:rgba(255,255,255,.2);padding:8px 0}
.console-output{
  flex:1;overflow-y:auto;padding:12px 20px;font-size:12.5px;line-height:1.7;
  color:rgba(255,255,255,.65);white-space:pre-wrap;word-break:break-all;
  font-family:'Consolas','Courier New',monospace;background:rgba(5,5,8,.5)
}
.console-output .ts{color:rgba(0,220,120,.35);margin-right:6px}
.console-output .err{color:rgba(239,68,68,.7)}
.console-input-wrap{
  display:flex;align-items:center;padding:10px 20px;
  background:rgba(13,13,20,.8);
  border-top:1px solid rgba(0,220,120,.08);flex-shrink:0;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)
}
.console-input-wrap .prompt{color:#00dc78;font-weight:700;margin-right:8px;font-size:13px}
.console-input-wrap input{
  flex:1;background:transparent;border:none;color:#fff;font-size:13px;
  font-family:'Consolas','Courier New',monospace;outline:none
}
.console-input-wrap input::placeholder{color:rgba(255,255,255,.15)}
.no-servers{display:flex;align-items:center;justify-content:center;flex:1;color:rgba(255,255,255,.15);font-size:14px}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.06);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.1)}
.icon-crop-modal{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.78);z-index:300;padding:20px}
.icon-crop-modal.open{display:flex}
.icon-crop-card{width:min(420px,100%);background:#0d0d14;border:1px solid rgba(0,220,120,.2);border-radius:16px;padding:20px;box-shadow:0 20px 80px rgba(0,0,0,.6)}
.icon-crop-card h2{font-size:17px;color:#fff;margin-bottom:6px}
.icon-crop-card p{font-size:12px;color:rgba(255,255,255,.45);margin-bottom:14px}
.icon-crop-stage{width:320px;height:320px;max-width:100%;margin:0 auto 16px;position:relative;overflow:hidden;background:#050508;border-radius:10px;touch-action:none}
.icon-crop-stage img{position:absolute;max-width:none;user-select:none;pointer-events:none}
.icon-crop-selection{position:absolute;border:2px solid #00dc78;box-shadow:0 0 0 9999px rgba(0,0,0,.55);cursor:move;touch-action:none}
.icon-crop-actions{display:flex;gap:8px;justify-content:flex-end}
.icon-crop-actions button{padding:9px 15px;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:#c9d1d9;cursor:pointer;font-family:inherit;font-weight:600}
.icon-crop-actions .apply{background:#00dc78;color:#050508;border-color:#00dc78}
.inventory-section{margin:18px 0}
.inventory-section h3{color:#00dc78;font-size:13px;margin-bottom:8px}
.inventory-grid{display:grid;grid-template-columns:repeat(9,52px);gap:5px;width:max-content;max-width:100%;padding:10px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.08);border-radius:10px}
.inventory-slot{width:52px;height:52px;border:1px solid rgba(255,255,255,.1);border-radius:7px;background:rgba(13,17,23,.9);display:flex;align-items:center;justify-content:center;position:relative;cursor:grab;padding:4px}
.inventory-slot:hover{border-color:#00dc78;background:rgba(0,220,120,.08)}
.inventory-slot img{width:42px;height:42px;image-rendering:pixelated;object-fit:contain}
.inventory-slot .item-count{position:absolute;right:3px;bottom:1px;color:#fff;font-size:11px;font-weight:700;text-shadow:1px 1px #000}
.inventory-slot.empty{cursor:default;opacity:.45}
.inventory-row{display:flex;gap:5px;padding:10px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.08);border-radius:10px;width:max-content;max-width:100%}
.player-actions{display:flex;flex-wrap:wrap;gap:8px}
.mc-action-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 13px;border-radius:9px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.045);color:#e5e7eb;font-size:12px;font-weight:650;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.16)}
.mc-action-neutral:hover{border-color:rgba(255,255,255,.22);background:rgba(255,255,255,.09)}
.mc-action-danger{border-color:rgba(248,113,113,.25);background:rgba(239,68,68,.08);color:#fca5a5}
.mc-action-danger:hover{border-color:rgba(248,113,113,.55);background:rgba(239,68,68,.16)}
.mc-action-success{border-color:rgba(34,197,94,.28);background:rgba(34,197,94,.08);color:#86efac}
.mc-action-success:hover{border-color:rgba(34,197,94,.55);background:rgba(34,197,94,.16)}
.player-live-status{margin:10px 0;padding:8px 11px;border-radius:8px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.025);color:#a1a1aa;font-size:12px}
.player-live-status.is-live{border-color:rgba(34,197,94,.2);background:rgba(34,197,94,.06);color:#86efac}
.ban-section{margin-top:26px;padding-top:16px;border-top:1px solid rgba(255,255,255,.07)}
.ban-section h3{display:flex;align-items:center;gap:8px;color:#e5e7eb;font-size:14px;margin-bottom:10px}
.ban-section h3 span{padding:2px 7px;border-radius:999px;background:rgba(255,255,255,.07);color:#a1a1aa;font-size:10px}
.ban-list{display:grid;gap:7px}
.ban-row{display:flex;align-items:center;gap:11px;min-width:0;padding:10px 12px;border:1px solid rgba(255,255,255,.07);border-radius:11px;background:linear-gradient(120deg,rgba(255,255,255,.04),rgba(255,255,255,.018));transition:border-color .15s,background .15s}
.ban-row:hover{border-color:rgba(248,113,113,.2);background:rgba(255,255,255,.05)}
.ban-head,.ban-head-placeholder{width:42px;height:42px;border-radius:10px;flex:0 0 42px;object-fit:cover;image-rendering:pixelated;background:rgba(255,255,255,.05)}
.ban-head-placeholder{display:flex;align-items:center;justify-content:center;color:#a1a1aa;font-size:20px}
.ban-row-info{display:grid;gap:3px;min-width:0;flex:1}
.ban-row-info strong{color:#f4f4f5;font-size:13px;overflow-wrap:anywhere}
.ban-row-info small{color:#8b949e;font-size:11px;overflow-wrap:anywhere}
.ban-row-info .ban-reason{color:#71717a}
.ban-empty{padding:12px;border:1px dashed rgba(255,255,255,.1);border-radius:10px;color:#71717a;font-size:12px}
@media(max-width:480px){
  .sidebar{width:160px}
  .sidebar .title{font-size:10px}
  .server-item .name{font-size:12px}
  .ban-row{flex-wrap:wrap}
  .ban-row-info{min-width:calc(100% - 60px)}
  .ban-row .mc-action-btn{margin-left:auto}
}

/* ============================================================
   CONSOLE RELIABILITY + COMMAND PALETTE
   ============================================================ */
.ws-status{display:inline-flex;align-items:center;gap:6px;margin-left:12px;padding:3px 10px;border-radius:999px;
  font-size:11px;font-weight:600;letter-spacing:.3px;border:1px solid var(--mc-line);
  background:rgba(255,255,255,.02);color:var(--mc-dim);transition:all .2s}
.ws-status .ws-dot{width:7px;height:7px;border-radius:50%;background:#8b949e;transition:all .2s}
.ws-status.live{color:#86efac;border-color:rgba(34,197,94,.3);background:rgba(34,197,94,.06)}
.ws-status.live .ws-dot{background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.7)}
.ws-status.connecting,.ws-status.reconnecting{color:#fbbf24;border-color:rgba(251,191,36,.3);background:rgba(251,191,36,.06)}
.ws-status.reconnecting .ws-dot{background:#f59e0b;animation:blink 1.2s infinite}
.console-header .actions .rename{border-color:rgba(251,191,36,.3);color:#fbbf24}
.console-header .actions .rename:hover{background:rgba(251,191,36,.12);border-color:rgba(251,191,36,.5);color:#fde68a}
.console-output .cmd-echo{color:rgba(0,220,120,.65);font-weight:500}
.cmd-box{position:relative;flex:1;display:flex;min-width:0}
.cmd-box input{width:100%;min-width:0}
.cmd-palette{position:absolute;left:0;right:0;bottom:calc(100% + 10px);display:none;max-height:240px;overflow-y:auto;
  background:rgba(13,15,22,.98);border:1px solid var(--mc-line);border-radius:10px;
  box-shadow:0 18px 50px rgba(0,0,0,.55);padding:6px;z-index:50;animation:palIn .12s ease}
@keyframes palIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.cmd-palette .pal-head{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--mc-faint);padding:4px 8px 6px}
.pal-item{display:flex;align-items:baseline;gap:10px;padding:7px 10px;border-radius:7px;cursor:pointer;transition:background .1s}
.pal-item:hover{background:var(--mc-accent-soft)}
.pal-item .pal-cmd{font-family:'Consolas','Courier New',monospace;font-size:12.5px;color:#7ee7b0;white-space:nowrap}
.pal-item:hover .pal-cmd{color:#00ff8a}
.pal-item .pal-desc{font-size:11px;color:var(--mc-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ============================================================
   REDESIGN OVERRIDES — cleaner, modern dark UI
   ============================================================ */
:root{
  --mc-bg:#050507; --mc-panel:rgba(16,18,25,.82); --mc-panel-2:rgba(22,25,33,.7);
  --mc-line:rgba(255,255,255,.07); --mc-line-soft:rgba(255,255,255,.045);
  --mc-txt:#e6edf3; --mc-dim:#8b949e; --mc-faint:#5a626d;
  --mc-accent:#00dc78; --mc-accent-soft:rgba(0,220,120,.12); --mc-accent-line:rgba(0,220,120,.28);
  --mc-blue:#5865f2; --mc-r:14px; --mc-rs:9px;
  --mc-font:'Inter','SF Pro Display',system-ui,-apple-system,sans-serif;
}
body{background:var(--mc-bg);color:var(--mc-txt);font-family:var(--mc-font);letter-spacing:-.01em;-webkit-font-smoothing:antialiased}
.bg-grid{opacity:.45;background-size:52px 52px;
  background-image:linear-gradient(rgba(255,255,255,.02) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px);
  mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 40%,transparent 100%);
  -webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 40%,transparent 100%)}
.bg-glow{width:680px;height:680px;top:-260px;left:50%;transform:translateX(-50%);
  background:radial-gradient(circle,rgba(0,220,120,.05),transparent 68%);animation:pulse 9s ease-in-out infinite alternate}
@keyframes pulse{0%{opacity:.6;transform:translateX(-50%) scale(1)}100%{opacity:1;transform:translateX(-50%) scale(1.1)}}

/* topbar */
.topbar{padding:12px 22px;background:rgba(8,9,13,.72);border-bottom:1px solid var(--mc-line);
  backdrop-filter:blur(18px) saturate(160%);-webkit-backdrop-filter:blur(18px) saturate(160%)}
.topbar .logo{font-size:18px;font-weight:700;color:#fff;display:flex;align-items:center;gap:9px;letter-spacing:-.4px}
.topbar .logo::before{content:"";width:9px;height:9px;border-radius:3px;background:var(--mc-accent);box-shadow:0 0 12px var(--mc-accent)}
.topbar .user{gap:11px;font-size:13px;color:var(--mc-dim)}
.topbar .user img{width:30px;height:30px;border-radius:50%;border:1.5px solid var(--mc-accent-line)}
.topbar .user .name{color:#fff;font-weight:600}
.topbar .nav-link{padding:7px 13px;border-radius:8px;border:1px solid var(--mc-line);color:var(--mc-dim);
  text-decoration:none;font-size:12px;font-weight:600;transition:all .16s}
.topbar .nav-link:hover{background:rgba(255,255,255,.05);color:#fff;border-color:rgba(255,255,255,.16)}

/* sidebar */
.sidebar{width:236px;background:rgba(10,11,16,.78);border-right:1px solid var(--mc-line);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.sidebar .title{padding:16px 16px 10px;font-size:10.5px;text-transform:uppercase;letter-spacing:1.6px;color:var(--mc-faint);font-weight:700}
.server-list{flex:1;overflow-y:auto;padding:0 10px 10px}
.server-item{padding:10px 12px;border-radius:11px;margin-bottom:4px;border:1px solid transparent;gap:0}
.server-item:hover{background:rgba(255,255,255,.035);border-color:var(--mc-line-soft)}
.server-item.active{background:var(--mc-accent-soft);border-color:var(--mc-accent-line)}
.server-item .name{font-size:13px;font-weight:550;color:rgba(255,255,255,.82)}
.server-item .server-icon{width:34px;height:34px;border-radius:9px;margin-right:10px}
.server-item.active .name{color:#fff}
.loader-tag{font-size:9.5px;padding:2px 6px;border-radius:5px;background:rgba(255,255,255,.05);color:var(--mc-faint);
  text-transform:uppercase;letter-spacing:.6px;font-weight:600;margin-left:auto;margin-right:7px}
.server-item.active .loader-tag{color:var(--mc-dim)}
.server-item .dot.on{background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.6)}
.server-item .dot.off{background:rgba(255,255,255,.14)}

.sidebar-bottom{padding:10px;border-top:1px solid var(--mc-line-soft)}
.sidebar-bottom input,.sidebar-bottom select{width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--mc-line);
  background:rgba(255,255,255,.03);color:#fff;font-size:12px;font-family:inherit;outline:none;margin-bottom:7px;transition:border-color .16s}
.sidebar-bottom input:focus,.sidebar-bottom select:focus{border-color:var(--mc-accent-line)}
.sidebar-bottom input::placeholder{color:var(--mc-faint)}
.sidebar-bottom select option{background:#0d0d14;color:#fff}
.sidebar-bottom button{width:100%;padding:9px;border-radius:9px;border:1px solid var(--mc-accent-line);background:var(--mc-accent-soft);
  color:var(--mc-accent);font-size:12px;font-family:inherit;font-weight:600;transition:all .16s}
.sidebar-bottom button:hover{background:rgba(0,220,120,.16);color:#00ff8a}

/* console header toolbar */
.console-header{padding:12px 20px;background:rgba(10,11,16,.72);border-bottom:1px solid var(--mc-line);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);gap:12px;flex-wrap:wrap}
.console-header .server-name{font-size:16px;font-weight:650;color:#fff;display:flex;align-items:center;gap:9px}
.console-header .server-name::before{content:"";width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.6)}
.console-header .actions{display:flex;gap:7px;flex-wrap:wrap}
.console-header .actions button{padding:7px 13px;border-radius:8px;border:1px solid var(--mc-line);background:rgba(255,255,255,.02);
  color:var(--mc-dim);font-size:12px;font-weight:550;transition:all .16s}
.console-header .actions button:hover{background:rgba(255,255,255,.06);color:#fff;border-color:rgba(255,255,255,.16)}
.console-header .actions .start{border-color:rgba(34,197,94,.3);color:#4ade80;background:rgba(34,197,94,.07)}
.console-header .actions .start:hover{background:rgba(34,197,94,.16);border-color:rgba(34,197,94,.5);color:#86efac}
.console-header .actions .stop{border-color:rgba(239,68,68,.3);color:#f87171;background:rgba(239,68,68,.07)}
.console-header .actions .stop:hover{background:rgba(239,68,68,.16);border-color:rgba(239,68,68,.5);color:#fca5a5}
.console-header .actions .files{border-color:var(--mc-accent-line);color:var(--mc-accent);background:var(--mc-accent-soft)}
.console-header .actions .files:hover{background:rgba(0,220,120,.16);color:#00ff8a}
.console-header .actions .version{border-color:rgba(168,85,247,.3);color:#c084fc}
.console-header .actions .version:hover{background:rgba(168,85,247,.12);color:#d8b4fe}
.console-header .actions .people{border-color:rgba(45,212,191,.3);color:#5eead4}
.console-header .actions .people:hover{background:rgba(45,212,191,.12);color:#99f6e4}
.console-header .actions .access{border-color:rgba(251,191,36,.3);color:#fbbf24}
.console-header .actions .access:hover{background:rgba(251,191,36,.12);color:#fde68a}
.console-header .actions .delete{border-color:rgba(239,68,68,.28);color:#f87171}
.console-header .actions .delete:hover{background:rgba(239,68,68,.14);color:#fca5a5}
.console-header .actions .backup{border-color:rgba(96,165,250,.3);color:#93c5fd}
.console-header .actions .backup:hover{background:rgba(96,165,250,.12);color:#bfdbfe}
.console-header .actions .icon-upload{border-color:rgba(88,101,242,.3);color:#8b9cf7}
.console-header .actions .icon-upload:hover{background:rgba(88,101,242,.12);color:#b7c0ff}
.console-header .actions .mods{border-color:rgba(88,101,242,.28);color:#7d85f0}
.console-header .actions .mods:hover,.console-header .actions .mods.active{background:rgba(88,101,242,.14);border-color:rgba(88,101,242,.5)}
.console-header .actions .live{border-color:rgba(236,72,153,.32);color:#f472b6;background:rgba(236,72,153,.07);display:inline-flex;align-items:center;gap:7px}
.console-header .actions .live:hover{background:rgba(236,72,153,.16);border-color:rgba(236,72,153,.5);color:#f9a8d4}
.console-header .actions .live .live-dot{width:7px;height:7px;border-radius:50%;background:#ec4899;box-shadow:0 0 8px #ec4899;animation:blink 1.4s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}

/* mods panel */
.mods-panel{background:rgba(10,11,16,.9);border-bottom:1px solid rgba(88,101,242,.1);padding:14px 20px;max-height:220px;
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.mods-panel .mods-title{font-size:11px;color:var(--mc-faint);text-transform:uppercase;letter-spacing:1.1px;margin-bottom:10px}
.mods-panel .mods-title label{padding:5px 11px;border-radius:7px;border:1px solid rgba(88,101,242,.3);background:rgba(88,101,242,.06);
  color:#7d85f0;font-size:11px;font-weight:600;text-transform:none;letter-spacing:0;transition:all .16s}
.mods-panel .mods-title label:hover{background:rgba(88,101,242,.14);border-color:rgba(88,101,242,.5)}
.mods-panel .mod-item{padding:8px 11px;border-radius:8px;margin-bottom:3px;border:1px solid transparent;transition:all .15s}
.mods-panel .mod-item:hover{background:rgba(255,255,255,.03);border-color:var(--mc-line-soft)}
.mods-panel .mod-item .mod-name{font-size:12.5px;color:rgba(255,255,255,.78)}
.mods-panel .mod-item .mod-size{font-size:11px;color:var(--mc-faint);margin-left:9px}
.mods-panel .mod-item .mod-del{padding:3px 9px;border-radius:6px;border:1px solid rgba(239,68,68,.24);background:transparent;
  color:rgba(239,68,68,.6);font-size:10.5px;font-weight:600;transition:all .16s}
.mods-panel .mod-item .mod-del:hover{background:rgba(239,68,68,.14);color:#f87171;border-color:rgba(239,68,68,.5)}
.mods-panel .no-mods{font-size:12px;color:var(--mc-faint);padding:8px 0}

/* console output + input */
.console-output{padding:14px 20px;font-size:12.5px;line-height:1.75;color:rgba(255,255,255,.66);
  font-family:ui-monospace,'Cascadia Code','Consolas',monospace;background:rgba(5,5,8,.4)}
.console-output .ts{color:rgba(0,220,120,.4);margin-right:7px}
.console-output .err{color:rgba(239,68,68,.75)}
.console-input-wrap{padding:12px 20px;background:rgba(10,11,16,.78);border-top:1px solid var(--mc-line);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.console-input-wrap .prompt{color:var(--mc-accent);font-weight:700;margin-right:9px;font-size:13px}
.console-input-wrap input{flex:1;background:transparent;border:none;color:#fff;font-size:13px;
  font-family:ui-monospace,'Cascadia Code','Consolas',monospace;outline:none}
.console-input-wrap input::placeholder{color:var(--mc-faint)}
.no-servers{color:var(--mc-faint);font-size:14px}

/* scrollbars */
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.08);border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.14)}

/* icon crop modal */
.icon-crop-card{background:#0c0e14;border:1px solid var(--mc-accent-line);border-radius:16px;padding:22px;box-shadow:0 24px 90px rgba(0,0,0,.65)}
.icon-crop-card h2{font-size:17px;color:#fff;margin-bottom:6px;font-weight:650}
.icon-crop-card p{font-size:12.5px;color:var(--mc-dim);margin-bottom:16px}
.icon-crop-stage{background:#050508;border-radius:12px}
.icon-crop-actions button{padding:9px 16px;border-radius:9px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);color:var(--mc-txt);font-weight:600}
.icon-crop-actions .apply{background:var(--mc-accent);color:#04140b;border-color:var(--mc-accent)}

/* inventory + players + bans */
.inventory-section h3{color:var(--mc-accent);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:9px}
.inventory-grid,.inventory-row{background:rgba(255,255,255,.02);border:1px solid var(--mc-line);border-radius:12px}
.inventory-slot{border:1px solid var(--mc-line);border-radius:8px;background:rgba(13,17,23,.9)}
.inventory-slot:hover{border-color:var(--mc-accent);background:rgba(0,220,120,.08)}
.mc-action-btn{border-radius:9px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);font-weight:600}
.player-live-status{border:1px solid var(--mc-line);background:rgba(255,255,255,.02);border-radius:9px;color:var(--mc-dim)}
.player-live-status.is-live{border-color:rgba(34,197,94,.24);background:rgba(34,197,94,.06);color:#86efac}
.ban-section h3{color:var(--mc-txt);font-size:14px;font-weight:600}
.ban-section h3 span{background:rgba(255,255,255,.06);color:var(--mc-dim)}
.ban-row{border:1px solid var(--mc-line);border-radius:12px;background:rgba(255,255,255,.025)}
.ban-row:hover{border-color:rgba(248,113,113,.22);background:rgba(255,255,255,.045)}
.ban-row-info strong{color:#f4f4f5}
.ban-empty{border:1px dashed var(--mc-line);border-radius:11px;color:var(--mc-faint)}

/* full-screen overlay panels (files/people) */
#filesPanel,#peoplePanel{background:rgba(5,5,8,.96);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);padding:32px}

/* file manager */
.files-shell{max-width:1120px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.files-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.files-head h2{color:#fff;font-size:18px;font-weight:650}
.files-crumb{color:var(--mc-dim);font-size:12px;margin-top:5px;word-break:break-all}
.files-crumb button{background:none;border:0;color:var(--mc-dim);font-size:12px;font-family:inherit;padding:0;cursor:pointer}
.files-crumb button:hover{color:var(--mc-accent);text-decoration:underline}
.files-toolbar{display:flex;gap:8px;flex-wrap:wrap}
.files-toolbar button,.files-toolbar label{padding:8px 12px;border-radius:8px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);
  color:var(--mc-txt);font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.files-toolbar button:hover,.files-toolbar label:hover{background:rgba(0,220,120,.1);border-color:var(--mc-accent-line);color:#fff}
.files-toolbar .primary{background:rgba(0,220,120,.12);border-color:var(--mc-accent-line);color:var(--mc-accent)}
.files-list{display:flex;flex-direction:column;gap:6px}
.file-row{display:flex;align-items:center;gap:10px;padding:9px 12px;background:rgba(255,255,255,.025);
  border:1px solid var(--mc-line);border-radius:9px}
.file-row:hover{background:rgba(255,255,255,.05)}
.file-row .ficon{width:20px;text-align:center;font-size:14px;flex-shrink:0}
.file-row .fname{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mc-txt);font-size:13px;background:none;border:0;text-align:left;padding:0;cursor:pointer}
.file-row .fname:hover{color:var(--mc-accent)}
.file-row .fmeta{font-size:11px;color:var(--mc-faint);flex-shrink:0}
.file-row .factions{display:flex;gap:5px;flex-shrink:0}
.file-row .factions button,.file-row .factions a{padding:4px 8px;border-radius:6px;border:1px solid var(--mc-line);
  background:rgba(255,255,255,.03);color:var(--mc-dim);font-size:11px;font-weight:600;text-decoration:none;cursor:pointer}
.file-row .factions button:hover,.file-row .factions a:hover{color:#fff;border-color:var(--mc-accent-line)}
.file-editor{margin-top:6px;background:#0a0c11;border:1px solid var(--mc-line);border-radius:12px;padding:16px}
.file-editor-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
.file-editor-head h3{color:#fff;font-size:14px;font-weight:650;word-break:break-all}
.file-editor textarea{width:100%;min-height:46vh;background:#05070b;color:#d7dde5;border:1px solid var(--mc-line);
  border-radius:9px;padding:12px;font-family:Consolas,monospace;font-size:12.5px;line-height:1.55;resize:vertical;outline:none}
.prop-row{display:flex;align-items:center;gap:10px;padding:7px 10px;border:1px solid rgba(255,255,255,.05);
  border-radius:8px;margin-bottom:5px;background:rgba(13,17,23,.55)}
.prop-row .prop-key{flex:1;min-width:0;color:var(--mc-txt);font-size:12.5px;word-break:break-all}
.prop-row .prop-help{display:block;color:var(--mc-faint);font-size:11px;margin-top:2px;font-weight:400}
.prop-row input[type=text],.prop-row input[type=number]{width:min(260px,45%);background:#05070b;border:1px solid var(--mc-line);
  color:var(--mc-txt);padding:6px 9px;border-radius:6px;font-size:12.5px;outline:none}
.prop-row input[type=text]:focus,.prop-row input[type=number]:focus{border-color:var(--mc-accent-line)}
.prop-row.comment{justify-content:flex-start;background:transparent;border-color:transparent;padding:2px 10px}
.prop-row.comment span{color:var(--mc-faint);font-size:11.5px;font-style:italic;white-space:pre-wrap}
.prop-row.blank{height:8px;padding:0;border:0;background:transparent;margin:0}
.prop-row.raw{padding:2px 10px;border-color:transparent;background:transparent;color:var(--mc-faint);font-family:Consolas,monospace;font-size:11.5px;white-space:pre-wrap}
.access-user{display:flex;gap:10px;align-items:flex-start;background:rgba(255,255,255,.04);padding:10px;border-radius:10px;border:1px solid rgba(255,255,255,.05)}
.access-user .access-avatar{width:36px;height:36px;border-radius:50%;flex-shrink:0;background:rgba(255,255,255,.08)}
.access-user .access-main{flex:1;min-width:0}
.access-user .access-main strong{color:#fff;font-size:13px}
.access-user .access-main small{display:block;color:var(--mc-faint);font-size:11px;margin-top:1px}
.priv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:4px 10px;margin-top:8px}
.priv-grid label{display:flex;align-items:center;gap:5px;font-size:11.5px;color:var(--mc-dim);cursor:pointer}
.priv-grid input{accent-color:#00dc78}
.access-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.access-actions button{padding:5px 9px;border-radius:6px;border:1px solid var(--mc-line);background:rgba(255,255,255,.03);
  color:var(--mc-dim);font-size:11px;font-weight:600;cursor:pointer}
.access-actions button:hover{color:#fff;border-color:var(--mc-accent-line)}
.access-actions .danger{color:#f87171;border-color:rgba(239,68,68,.28)}
.access-actions .gold{color:#fbbf24;border-color:rgba(251,191,36,.28)}

/* ---- TOASTS ---- */
#toastWrap{position:fixed;top:16px;right:16px;z-index:500;display:flex;flex-direction:column;gap:9px;pointer-events:none}
.toast{background:rgba(18,21,28,.96);border:1px solid var(--mc-line);border-left:3px solid var(--mc-accent);color:var(--mc-txt);
  padding:11px 16px;border-radius:10px;font-size:13px;font-weight:500;box-shadow:0 12px 40px rgba(0,0,0,.5);
  animation:toastIn .22s ease;max-width:340px;backdrop-filter:blur(12px)}
.toast.error{border-left-color:#f85149}
.toast.warn{border-left-color:#fbbf24}
@keyframes toastIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:none}}

/* ---- CONFIRM MODAL ---- */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:600;padding:20px;
  backdrop-filter:blur(4px)}
.modal-overlay.open{display:flex}
.modal-card{background:#0c0e14;border:1px solid var(--mc-line);border-radius:16px;padding:26px;width:min(400px,100%);
  box-shadow:0 24px 90px rgba(0,0,0,.7)}
.modal-card h3{color:#fff;font-size:17px;font-weight:650;margin-bottom:10px}
.modal-card p{color:var(--mc-dim);font-size:13.5px;line-height:1.6;margin-bottom:20px}
.modal-actions{display:flex;gap:10px;justify-content:flex-end}
.modal-actions button{padding:9px 18px;border-radius:9px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);
  color:var(--mc-txt);font-weight:600;font-size:13px}
.modal-actions .confirm-danger{background:rgba(239,68,68,.16);border-color:rgba(239,68,68,.4);color:#fca5a5}
.modal-actions .confirm-danger:hover{background:rgba(239,68,68,.28)}
.modal-actions .confirm-ok{background:var(--mc-accent);border-color:var(--mc-accent);color:#04140b}
.modal-actions .confirm-ok:hover{filter:brightness(1.08)}

/* ============================================================
   SCREENSHARE — live PC view
   ============================================================ */
.ss-pip{position:fixed;right:18px;bottom:18px;width:224px;z-index:250;border-radius:14px;overflow:hidden;
  border:1px solid var(--mc-line);background:#0a0b10;box-shadow:0 18px 60px rgba(0,0,0,.6);cursor:pointer;
  transition:transform .2s,border-color .2s,box-shadow .2s;display:none}
.ss-pip:hover{transform:translateY(-3px) scale(1.02);border-color:rgba(236,72,153,.5);box-shadow:0 22px 70px rgba(236,72,153,.25)}
.ss-pip img{width:100%;height:126px;object-fit:cover;display:block;background:#050508}
.ss-pip .ss-pip-bar{display:flex;align-items:center;justify-content:space-between;padding:7px 10px;
  background:rgba(10,11,16,.95);border-top:1px solid var(--mc-line)}
.ss-pip .ss-pip-title{font-size:11px;font-weight:650;color:var(--mc-txt);display:flex;align-items:center;gap:6px}
.ss-pip .ss-pip-title .d{width:6px;height:6px;border-radius:50%;background:#ec4899;box-shadow:0 0 7px #ec4899}
.ss-pip .ss-pip-open{font-size:10.5px;color:var(--mc-faint);font-weight:600}

/* Discord-style viewer: black full-bleed video, floating header + dock */
.ss-overlay{position:fixed;inset:0;z-index:450;background:#000;display:none;flex-direction:column}
.ss-overlay.open{display:flex}
.ss-top{position:absolute;top:0;left:0;right:0;z-index:3;display:flex;align-items:center;gap:12px;padding:16px 20px;
  background:linear-gradient(to bottom,rgba(0,0,0,.75),rgba(0,0,0,0));pointer-events:none}
.ss-top>*{pointer-events:auto}
.ss-live{background:#f23f43;color:#fff;font-size:10.5px;font-weight:800;letter-spacing:.8px;padding:3px 8px;border-radius:5px}
.ss-top .ss-title{font-size:14px;font-weight:700;color:#fff;display:flex;align-items:center;gap:9px}
.ss-top .ss-title .d{width:8px;height:8px;border-radius:50%;background:#ec4899;box-shadow:0 0 9px #ec4899;animation:blink 1.4s infinite}
.ss-top .ss-meta{font-size:12px;color:rgba(255,255,255,.62);display:flex;align-items:center;gap:4px;font-weight:600}
.ss-top .ss-meta .p-ok{color:#4ade80}
.ss-top .ss-meta .p-warn{color:#fbbf24}
.ss-top .ss-meta .p-bad{color:#f87171}
.ss-spacer{flex:1}
.ss-x{background:rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.14);color:#fff;width:34px;height:34px;border-radius:50%;
  font-size:14px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s}
.ss-x:hover{background:#f23f43;border-color:#f23f43}
.ss-dock{position:absolute;bottom:24px;left:50%;transform:translateX(-50%);z-index:3;display:flex;align-items:center;gap:8px;
  padding:10px 12px;border-radius:16px;background:rgba(24,25,28,.88);border:1px solid rgba(255,255,255,.08);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:0 16px 48px rgba(0,0,0,.6);
  max-width:calc(100% - 32px);flex-wrap:wrap;justify-content:center}
.ss-select{padding:8px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:rgba(0,0,0,.35);
  color:#dbdee1;font-size:12.5px;font-family:inherit;font-weight:600;outline:none;cursor:pointer}
.ss-select option{background:#1e1f22}
.ss-btn{padding:8px 15px;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:rgba(0,0,0,.35);
  color:#dbdee1;font-size:12.5px;font-weight:600;cursor:pointer;transition:all .16s;font-family:inherit}
.ss-btn:hover{background:rgba(255,255,255,.08);color:#fff}
.ss-btn.control-on{background:#5865f2;border-color:#5865f2;color:#fff}
.ss-btn.exit:hover{background:#f23f43;color:#fff;border-color:#f23f43}
.ss-stage{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;background:#000}
.ss-stage canvas{max-width:100%;max-height:100%;display:block;transition:filter .45s ease}
.ss-stage canvas.ss-spoiler{filter:blur(26px) saturate(1.25) brightness(.95)}
.ss-stage canvas.control-live{cursor:crosshair;outline:2px solid #5865f2;outline-offset:-2px}
.ss-hint{position:absolute;bottom:88px;left:50%;transform:translateX(-50%);font-size:12px;color:rgba(255,255,255,.75);
  background:rgba(0,0,0,.6);padding:7px 15px;border-radius:20px;border:1px solid rgba(255,255,255,.1);pointer-events:none}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-glow"></div>
<div class="topbar">
  <div class="logo"><span class="lua">MC</span> Console</div>
  <a class="nav-link" href="https://storage.luaisgame.com/" target="_blank" rel="noopener">&#8592; Storage</a>
  <a class="nav-link" href="https://dm.luaisgame.com/" target="_blank" rel="noopener">💬 DM</a>
  <a class="nav-link" id="globalAccessBtn" href="#" style="display:none" onclick="openGlobalAccess();return false">Access</a>
  <div class="user" id="userInfo"></div>
</div>
<div class="main">
  <div class="sidebar">
    <div class="title">Servers</div>
    <div class="server-list" id="serverList"></div>
    <div class="sidebar-bottom" id="createServer" style="display:none">
      <input type="text" id="newServerName" placeholder="New server name..." onkeydown="if(event.key==='Enter')createServer()">
      <select id="loaderSelect"><option value="fabric">Fabric</option><option value="forge">Forge</option></select>
      <select id="versionSelect"><option value="">Latest version</option></select>
      <button onclick="createServer()">+ Create Server</button>
    </div>
  </div>
  <div class="console-wrap" id="consoleWrap">
    <div class="no-servers" id="noSelect">Select a server</div>
  </div>
</div>
<div class="icon-crop-modal" id="iconCropModal">
  <div class="icon-crop-card">
    <h2>Crop server icon</h2>
    <p>Drag the square to choose the part of the image to use. It will be saved as a 64x64 Minecraft icon.</p>
    <div class="icon-crop-stage" id="iconCropStage">
      <img id="iconCropImage" alt="Icon preview">
      <div class="icon-crop-selection" id="iconCropSelection"></div>
    </div>
    <div class="icon-crop-actions">
      <button onclick="closeIconCrop()">Cancel</button>
      <button class="apply" onclick="applyIconCrop()">Use Crop</button>
    </div>
  </div>
</div>
<script>
var userInfo=null,activeServer=null,ws=null,autoScroll=true;
var serversByName={},canCreateServer=false;
var MC_PRIVILEGES=['start','stop','console','command','files','mods','backup','version','players','icon','delete','access'];
var MC_PRIVILEGE_LABELS={start:'Start',stop:'Stop',console:'View console',command:'Send commands',files:'Files',mods:'Mods',backup:'Backups',version:'Version',players:'Players & bans',icon:'Icon',delete:'Delete server',access:'Manage access'};
function canPriv(privilege){
  if(!userInfo)return false;
  if(userInfo.mc_admin)return true;
  var server=serversByName[activeServer];
  return !!(server&&server.privileges&&server.privileges.indexOf(privilege)!==-1);
}
async function checkAuth(){
  try{
    var response=await fetch('/api/auth/me',{credentials:'include'});
    if(!response.ok)throw new Error('not authenticated');
    userInfo=await response.json();
  }catch(e){
    document.getElementById('consoleWrap').innerHTML='<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;gap:16px"><div style="font-size:18px;color:rgba(255,255,255,.4)">Login to access servers</div><a href="/api/auth/login" style="padding:12px 24px;border-radius:12px;border:1.5px solid rgba(88,101,242,.4);background:transparent;color:rgba(88,101,242,.8);font-size:14px;font-weight:600;text-decoration:none;font-family:inherit;transition:all .3s;display:flex;align-items:center;gap:8px">Login with Discord</a></div>';
    return false;
  }
  var avatar=userInfo.avatar?'<img src="https://cdn.discordapp.com/avatars/'+userInfo.id+'/'+userInfo.avatar+'.png">':'';
  var websiteLink=userInfo.mc_authorized?'<a class="nav-link" href="/">Website</a>':'';
  document.getElementById('userInfo').innerHTML=avatar+'<span class="name">'+userInfo.username+'</span>'+websiteLink;
  var createEl=document.getElementById('createServer');if(createEl)createEl.style.display=(userInfo.mc_create||userInfo.mc_admin)?'':'none';
  var accessEl=document.getElementById('globalAccessBtn');if(accessEl)accessEl.style.display=userInfo.mc_admin?'':'none';
  return true;
}
 function loadServers(after){
  fetch('/api/mc/servers',{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(d.error)return;
    canCreateServer=!!d.can_create;
    serversByName={};
    var el=document.getElementById('serverList');if(!el)return;el.innerHTML='';
    (d.servers||[]).forEach(function(s){
      serversByName[s.name]=s;
      var item=document.createElement('div');
      item.className='server-item'+(activeServer===s.name?' active':'');
       var icon=s.icon?'<img class="server-icon" src="/api/mc/icon?name='+encodeURIComponent(s.name)+'" alt="">':'';
       item.innerHTML=icon+'<span class="name">'+escapeHtml(s.name)+'<small style="display:block;color:rgba(255,255,255,.35);font-size:10px">'+(s.version||'auto')+'</small></span><span class="loader-tag">'+s.loader+'</span><span class="dot '+(s.running?'on':'off')+'"></span>';
      item.onclick=function(){selectServer(s.name)};
      el.appendChild(item);
    });
    var createEl=document.getElementById('createServer');
    if(createEl)createEl.style.display=(userInfo&&(userInfo.mc_create||userInfo.mc_admin))?'':'none';
    if(typeof after==='function')after();
  });
}
function createServer(){
  var inp=document.getElementById('newServerName');
  var sel=document.getElementById('loaderSelect');
  var versionSel=document.getElementById('versionSelect');
  var name=inp.value.trim();
  var loader=sel?sel.value:'fabric';
  var version=versionSel?versionSel.value:'';
  if(!name)return;
  fetch('/api/mc/create',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:name,loader:loader,version:version})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}
    inp.value='';loadServers(function(){selectServer(name)});
  });
}
function loadVersions(){
  fetch('/api/mc/versions',{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var select=document.getElementById('versionSelect');if(!select||!d.versions)return;
    select.innerHTML='';
    d.versions.forEach(function(version,index){
      var option=document.createElement('option');option.value=version;option.textContent=version+(index===0?' (latest)':'');select.appendChild(option);
    });
  });
}
var lastConsoleN=0;
var lastWSServer='';
function consoleShell(name){
  var h='<div class="console-header"><div class="server-name">'+escapeHtml(name)+'<span class="ws-status" id="wsStatus"><span class="ws-dot"></span>Connecting\u2026</span></div><div class="actions">';
  if(canPriv('start'))h+='<button class="start" onclick="startServer()">Start</button>';
  if(canPriv('stop'))h+='<button class="stop" onclick="stopServer()">Stop</button>';
  if(userInfo&&userInfo.mc_owner)h+='<button class="live" id="ssBtn" onclick="openSS()"><span class="live-dot"></span>Screenshare</button>';
  if(canPriv('mods'))h+='<button class="mods" id="modsBtn" onclick="toggleMods()">Mods</button>';
  if(canPriv('files'))h+='<button class="files" onclick="openFiles()">Folders</button>';
  if(canPriv('players'))h+='<button class="people" onclick="openPeople()">People</button>';
  if(canPriv('access'))h+='<button class="access" onclick="openAccess()">Access</button>';
  if(canPriv('backup'))h+='<button class="backup" onclick="createBackup()">Backup</button>';
  if(canPriv('version'))h+='<button class="version" onclick="changeServerVersion()">Version</button>';
  if(canPriv('icon'))h+='<button class="icon-upload" onclick="document.getElementById(\'serverIconInput\').click()">Icon</button>';
  if(canPriv('delete'))h+='<button class="rename" onclick="renameServer()">Rename</button>';
  if(canPriv('delete'))h+='<button class="delete" onclick="deleteServer()">Delete</button>';
  h+='<input id="serverIconInput" type="file" accept="image/*" style="display:none" onchange="openIconCrop(this)"></div></div>';
  h+='<div class="mods-panel" id="modsPanel" style="display:none"></div>';
  h+='<div class="console-output" id="consoleOutput"></div>';
  var canSend=canPriv('command');
  h+='<div class="console-input-wrap"><span class="prompt">&gt;</span><div class="cmd-box"><input type="text" id="cmdInput" placeholder="'+(canSend?'Type a command... (Tab autocomplete \u00b7 \u2191/\u2193 history)':'Command access not granted')+'" autocomplete="off" '+(canSend?'':'disabled')+' onkeydown="cmdKey(event)" oninput="cmdFilter()" onfocus="cmdFocus(true)" onblur="cmdFocus(false)"><div class="cmd-palette" id="cmdPalette"></div></div></div>';
  return h;
}
function selectServer(name){
  activeServer=name;
  autoScroll=true;
  modsVisible=false;
  closeFiles();
  closePeople();
  loadServers();
  var viewConsole=canPriv('console');
  if(viewConsole){
    if(lastWSServer!==name){connectWS(name);lastWSServer=name;lastConsoleN=0}
  }else{
    if(ws){ws.close();ws=null}
    lastWSServer='';lastConsoleN=0;
  }
  function render(lines,n){
    var wrap=document.getElementById('consoleWrap');
    var out=document.getElementById('consoleOutput');
    if(out&&out.dataset.server===name){
      renderDiff(out,lines||[],n||0);
      var existingInput=document.getElementById('cmdInput');
      if(existingInput&&!existingInput.disabled)existingInput.focus();
      return;
    }
    wrap.innerHTML=consoleShell(name);
    var ssBtn=document.getElementById('ssBtn');if(ssBtn)ssStartIdle();
    out=document.getElementById('consoleOutput');
    out.dataset.server=name;
    out.addEventListener('scroll',function(){
      var atBottom=out.scrollHeight-out.scrollTop-out.clientHeight<50;
      autoScroll=atBottom;
    });
    renderDiff(out,lines||[],n||0,true);
    if(!viewConsole)appendLine(out,'Console access is not granted for this server.',false);
    out.scrollTop=out.scrollHeight;
    var input=document.getElementById('cmdInput');
    if(input&&!input.disabled)input.focus();
    if(canPriv('mods'))loadMods();
  }
  if(viewConsole){
    fetch('/api/mc/console?name='+encodeURIComponent(name),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
      render(d.lines||[],d.n||0);
    }).catch(function(){render([],0)});
  }else{
    render([],0);
  }
}
var modsVisible=false;
function toggleMods(){
  modsVisible=!modsVisible;
  var panel=document.getElementById('modsPanel');
  var btn=document.getElementById('modsBtn');
  if(!panel)return;
  if(modsVisible){panel.style.display='';btn.classList.add('active');loadMods()}
  else{panel.style.display='none';btn.classList.remove('active')}
}
function loadMods(){
  if(!activeServer||!canPriv('mods'))return;
  fetch('/api/mc/mods?name='+encodeURIComponent(activeServer),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var panel=document.getElementById('modsPanel');if(!panel||d.error)return;
    var mods=d.mods||[];
    var h='<div class="mods-title">Installed Mods ('+mods.length+')<label>Upload Mod<input type="file" accept=".jar" style="display:none" onchange="uploadMod(this)"></label></div>';
    if(mods.length===0){h+='<div class="no-mods">No mods installed</div>'}
    else{mods.forEach(function(m){
      h+='<div class="mod-item"><span class="mod-name">'+m.name+'<span class="mod-size">'+formatSize(m.size)+'</span></span><button class="mod-del" onclick="deleteMod(\''+m.name.replace(/'/g,"\\'")+'\')">Remove</button></div>'
    })}
    panel.innerHTML=h;
  });
}
function formatSize(b){if(b>1048576)return(b/1048576).toFixed(1)+'MB';return(b/1024).toFixed(0)+'KB'}
function uploadMod(input){
  var file=input.files[0];if(!file)return;
  var fd=new FormData();fd.append('server',activeServer);fd.append('file',file);
  fetch('/api/mc/upload-mod',{method:'POST',body:fd,credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}loadMods();
    var out=document.getElementById('consoleOutput');if(out)appendLine(out,'['+new Date().toTimeString().slice(0,8)+'] Mod installed: '+d.name,false);
  });
}
function deleteMod(mod){
  if(!confirm('Remove '+mod+'?'))return;
  fetch('/api/mc/delete-mod',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,mod:mod})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}loadMods();
  });
}
function appendLine(out,line,isWs){
  var div=document.createElement('div');
  var tsMatch=line.match(/^\[(\d{2}:\d{2}:\d{2})\]/);
  if(tsMatch){div.innerHTML='<span class="ts">'+tsMatch[1]+'</span>'+escapeHtml(line.substring(10))}
  else{div.textContent=line}
  if(line.indexOf('[ERROR]')!==-1||line.indexOf('[WARN]')!==-1){div.className='err'}
  out.appendChild(div);
  if(autoScroll||!isWs){out.scrollTop=out.scrollHeight}
}
function escapeHtml(t){var d=document.createElement('div');d.textContent=t==null?'':String(t);return d.innerHTML}
function escapeAttr(t){return escapeHtml(t).replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
/* ---- console reliability: watchdog, history, command palette, rename ---- */
var wsAlive=false,wsPingTimer=null;
var cmdHistory=[],cmdHistIndex=-1,cmdDraft='',cmdPalItems=[];
var cmdTabBase='',cmdTabIndex=-1,cmdTabCands=[];
var CMD_SUGGESTIONS=[
  {c:'list',d:'List online players'},
  {c:'say <message>',d:'Broadcast a message to everyone'},
  {c:'kick <player> [reason]',d:'Kick a player (mod)',t:[['<player>'],['<reason>']]},
  {c:'ban <player> [reason]',d:'Ban a player (mod)',t:[['<player>'],['<reason>']]},
  {c:'pardon <player>',d:'Unban a player (mod)',t:[['<player>']]},
  {c:'op <player>',d:'Grant operator (mod)',t:[['<player>']]},
  {c:'deop <player>',d:'Remove operator (mod)',t:[['<player>']]},
  {c:'gamemode <mode> [player]',d:'Set a gamemode (mod)',t:[['survival','creative','adventure','spectator'],['<player>']]},
  {c:'tp <player> [target]',d:'Teleport a player (mod)',t:[['<player>'],['<player>']]},
  {c:'time set <value>',d:'Set the time',t:[['set'],['day','night','noon','midnight','sunrise','sunset']]},
  {c:'weather <type>',d:'Set the weather',t:[['clear','rain','thunder']]},
  {c:'difficulty <level>',d:'Set the difficulty',t:[['peaceful','easy','normal','hard']]},
  {c:'whitelist <action> [player]',d:'Whitelist a player',t:[['add','remove','list','on','off'],['<player>']]},
  {c:'seed',d:'Show the world seed'},
  {c:'save-all',d:'Force a world save'},
  {c:'stop',d:'Stop the server'}
];
function setWSStatus(state){
  var el=document.getElementById('wsStatus');if(!el)return;
  el.className='ws-status '+state;
  el.innerHTML='<span class="ws-dot"></span>'+(state==='live'?'Live':state==='connecting'?'Connecting\u2026':'Reconnecting\u2026');
}
function loadCmdHistory(){
  try{cmdHistory=JSON.parse(localStorage.getItem('mcCmdHistory:'+activeServer)||'[]')}catch(e){cmdHistory=[]}
  cmdHistIndex=-1;cmdDraft='';
}
function pushCmdHistory(cmd){
  cmdHistory=cmdHistory.filter(function(c){return c!==cmd});
  cmdHistory.unshift(cmd);
  if(cmdHistory.length>50)cmdHistory.length=50;
  try{localStorage.setItem('mcCmdHistory:'+activeServer,JSON.stringify(cmdHistory))}catch(e){}
  cmdHistIndex=-1;cmdDraft='';
}
function cmdKey(e){
  if(e.key==='Enter'){sendCmd();return}
  if(e.key==='Tab'){e.preventDefault();cmdComplete(e.target);return}
  if(e.key==='ArrowUp'){
    e.preventDefault();
    if(cmdHistIndex===-1)cmdDraft=e.target.value;
    if(cmdHistIndex<cmdHistory.length-1){cmdHistIndex++;e.target.value=cmdHistory[cmdHistIndex]}
    cmdFilter();return;
  }
  if(e.key==='ArrowDown'){
    e.preventDefault();
    if(cmdHistIndex>0){cmdHistIndex--;e.target.value=cmdHistory[cmdHistIndex]}
    else{cmdHistIndex=-1;e.target.value=cmdDraft}
    cmdFilter();return;
  }
  if(e.key==='Escape'){var pal=document.getElementById('cmdPalette');if(pal)pal.style.display='none'}
}
function cmdFocus(show){
  if(show){
    loadCmdHistory();cmdFilter();
    loadKnownPlayers();
    if(!mcPlayersTimer)mcPlayersTimer=setInterval(loadKnownPlayers,60000);
  }else{
    if(mcPlayersTimer){clearInterval(mcPlayersTimer);mcPlayersTimer=null}
    setTimeout(function(){var pal=document.getElementById('cmdPalette');if(pal)pal.style.display='none'},130);
  }
}
function cmdFilter(){
  var inp=document.getElementById('cmdInput'),pal=document.getElementById('cmdPalette');
  if(!inp||!pal)return;
  var q=inp.value.trim().toLowerCase();
  cmdPalItems=CMD_SUGGESTIONS.filter(function(s){
    return !q||s.c.toLowerCase().indexOf(q)!==-1||s.d.toLowerCase().indexOf(q)!==-1;
  }).slice(0,8);
  if(!cmdPalItems.length){pal.style.display='none';return}
  var html='<div class="pal-head">Quick commands</div>';
  cmdPalItems.forEach(function(s,i){
    html+='<div class="pal-item" onmousedown="event.preventDefault();palPick('+i+')"><span class="pal-cmd">'+escapeHtml(s.c)+'</span><span class="pal-desc">'+escapeHtml(s.d)+'</span></div>';
  });
  pal.innerHTML=html;
  if(document.activeElement===inp)pal.style.display='block';
}
function palPick(i){
  var inp=document.getElementById('cmdInput');
  if(!inp||!cmdPalItems[i])return;
  inp.value=cmdPalItems[i].c;
  inp.focus();
  cmdFilter();
}
/* ---- in-game style autocomplete: Tab completes commands AND arguments ---- */
var mcKnownPlayers=[],mcPlayersTimer=null;
function loadKnownPlayers(){
  if(!activeServer)return;
  fetch('/api/mc/players?name='+encodeURIComponent(activeServer),{credentials:'include'})
    .then(function(r){return r.json()})
    .then(function(d){if(d&&d.players)mcKnownPlayers=d.players.map(function(p){return p.name})})
    .catch(function(){});
}
function cmdTokensFor(name){
  name=name.toLowerCase();
  for(var i=0;i<CMD_SUGGESTIONS.length;i++){
    if(CMD_SUGGESTIONS[i].c.split(' ')[0].toLowerCase()===name)return CMD_SUGGESTIONS[i];
  }
  return null;
}
function lcpCmd(list){
  if(!list.length)return '';
  var p=list[0];
  var pl=p.toLowerCase();
  for(var i=1;i<list.length;i++){
    var q=list[i].toLowerCase();
    while(q.indexOf(pl)!==0){p=p.slice(0,-1);pl=pl.slice(0,-1);if(!p)return ''}
  }
  return p;
}
function cmdComplete(inp){
  var val=inp.value||'';
  /* Cycling: Tab pressed again right after a completion. */
  if(cmdTabIndex!==-1&&cmdTabBase===val&&cmdTabCands.length){
    var cw=val.trim().split(/\s+/);
    cw.pop();
    var crest=cw.join(' ');
    cmdTabIndex=(cmdTabIndex+1)%cmdTabCands.length;
    inp.value=crest+(crest?' ':'')+cmdTabCands[cmdTabIndex];
    cmdTabBase=inp.value;
    inp.setSelectionRange(inp.value.length,inp.value.length);
    cmdFilter();
    return;
  }
  /* Fresh completion. */
  var trailing=/\s$/.test(val);
  var words=val.trim().split(/\s+/);
  var cur=trailing?'':(words.pop()||'');
  var rest=words.join(' ');
  var cands=[];
  var fromHistory=false;
  if(!rest){
    /* completing the command name */
    CMD_SUGGESTIONS.forEach(function(s){var w=s.c.split(' ')[0];if(cands.indexOf(w)===-1)cands.push(w)});
    cmdHistory.forEach(function(h){var w=h.trim().split(/\s+/)[0];if(w&&cands.indexOf(w)===-1)cands.push(w)});
  }else{
    var s=cmdTokensFor(rest.split(' ')[0]);
    var argIndex=rest.split(' ').length-1;
    if(s&&s.t&&s.t[argIndex]){
      var tok=s.t[argIndex];
      if(tok.indexOf('<player>')!==-1)cands=cands.concat(mcKnownPlayers);
      else cands=cands.concat(tok);
    }
    if(!cands.length){
      /* fall back to whole-input history matches (filtered by the full input) */
      fromHistory=true;
      cmdHistory.forEach(function(h){if(h.toLowerCase().indexOf(val.toLowerCase())===0&&h.toLowerCase()!==val.toLowerCase())cands.push(h)});
      cands=uniqueCmd(cands);
    }
  }
  if(fromHistory){
    if(!cands.length)return;
  }else{
    cands=cands.filter(function(c){
      if(cur===''&&c.charAt(0)==='<')return false;  /* never auto-insert placeholders */
      return c.toLowerCase().indexOf(cur.toLowerCase())===0&&c.toLowerCase()!==cur.toLowerCase();
    });
    cands=uniqueCmd(cands);
    if(!cands.length)return;
  }
  cmdTabBase=val;cmdTabIndex=-1;
  cmdTabCands=cands.slice();
  var common=lcpCmd(cands);
  if(fromHistory){
    /* candidates are full commands, not single words */
    if(common.length>val.length){
      inp.value=common;
    }else{
      cmdTabIndex=0;
      inp.value=cmdTabCands[0];
    }
  }else if(common.length>cur.length){
    inp.value=rest+(rest?' ':'')+common;
  }else{
    cmdTabIndex=0;
    inp.value=rest+(rest?' ':'')+cmdTabCands[0];
  }
  cmdTabBase=inp.value;
  inp.setSelectionRange(inp.value.length,inp.value.length);
  cmdFilter();
}
function uniqueCmd(list){
  var seen={},out=[];
  list.forEach(function(c){var k=c.toLowerCase();if(!seen[k]){seen[k]=1;out.push(c)}});
  return out;
}
function appendEcho(cmd){
  var out=document.getElementById('consoleOutput');if(!out)return;
  var div=document.createElement('div');div.className='cmd-echo';div.textContent='> '+cmd;
  out.appendChild(div);
  if(autoScroll)out.scrollTop=out.scrollHeight;
}
function sendCmd(){
  var inp=document.getElementById('cmdInput');
  if(!inp)return;
  var cmd=inp.value.trim();
  if(!cmd)return;
  if(!canPriv('command')){toast('Command access not granted.','error');return}
  appendEcho(cmd);
  // Commands always travel over a fresh HTTP request: a silently-dead
  // websocket can never swallow them, and errors surface as toasts.
  fetch('/api/mc/command',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,command:cmd})})
    .then(function(r){return r.json()})
    .then(function(d){if(d&&d.error)toast(d.error,'error')});
  pushCmdHistory(cmd);
  inp.value='';
  cmdFilter();
}
function renderDiff(out,lines,n,forceAll){
  if(!out)return;
  if(forceAll){
    for(var i=0;i<lines.length;i++)appendLine(out,lines[i],false);
    if(n>lastConsoleN)lastConsoleN=n;
    return;
  }
  if(n>lastConsoleN){
    var k=lines.length;
    var start=Math.max(0,k-(n-lastConsoleN));
    for(var j=start;j<k;j++)appendLine(out,lines[j],false);
    lastConsoleN=n;
  }
}
function connectWS(name){
  if(ws){try{ws.close()}catch(e){}ws=null}
  setWSStatus('connecting');
  var proto=location.protocol==='https:'?'wss:':'ws:';
  try{ws=new WebSocket(proto+'//'+location.host+'/ws/mc/'+encodeURIComponent(name))}catch(e){ws=null}
  if(!ws){setWSStatus('reconnecting');return}
  ws.onopen=function(){
    setWSStatus('live');wsAlive=true;
    if(wsPingTimer)clearInterval(wsPingTimer);
    wsPingTimer=setInterval(wsWatchdog,10000);
  };
  ws.onmessage=function(ev){
    try{
      var d=JSON.parse(ev.data);
      wsAlive=true;
      if(d.type==='output'){
        var out=document.getElementById('consoleOutput');
        if(out&&d.n>lastConsoleN){appendLine(out,d.line,true);lastConsoleN=d.n}
      }else if(d.type==='history'){
        renderDiff(document.getElementById('consoleOutput'),d.lines||[],d.n||0);
      }else if(d.type==='error'){toast(d.message||'Server error.','error')}
    }catch(e){}
  };
  ws.onerror=function(){wsAlive=false};
  ws.onclose=function(){
    setWSStatus('reconnecting');
    wsAlive=false;
    if(wsPingTimer){clearInterval(wsPingTimer);wsPingTimer=null}
    setTimeout(function(){if(activeServer===name&&lastWSServer===name&&canPriv('console'))connectWS(name)},3000);
  };
}
function wsWatchdog(){
  if(!ws||ws.readyState!==1)return;
  if(!wsAlive){
    setWSStatus('reconnecting');
    try{ws.close()}catch(e){}
    return;
  }
  wsAlive=false;
  try{ws.send(JSON.stringify({type:'ping'}))}catch(e){}
}
function renameServer(){
  if(!activeServer)return;
  var name=prompt('Rename server "'+activeServer+'" to:',activeServer);
  if(!name||name===activeServer)return;
  fetch('/api/mc/rename',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,new_name:name})})
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.error){alert(d.error);return}
      if(ws){ws.close();ws=null}
      activeServer=d.name;lastWSServer='';lastConsoleN=0;
      loadServers();selectServer(d.name);
    });
}
function changeServerVersion(){
  if(!activeServer)return;
  var select=document.getElementById('versionSelect');
  var version=select?select.value:'';
  if(!version){alert('Choose a Minecraft version first.');return}
  if(!confirm('Change '+activeServer+' to Minecraft '+version+'? The server must be stopped.'))return;
  fetch('/api/mc/version',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,version:version})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}
    loadServers();selectServer(activeServer);alert('Server version changed to '+version+'.');
  });
}
function deleteServer(){
  if(!activeServer)return;
  if(!confirm('Delete '+activeServer+' and all of its files? This cannot be undone.'))return;
  var deleting=activeServer;
  fetch('/api/mc/delete',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:deleting})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}
    if(ws){ws.close();ws=null}activeServer=null;lastWSServer='';loadServers();closeFiles();closePeople();
    document.getElementById('consoleWrap').innerHTML='<div class="no-servers" id="noSelect">Select a server</div>';
  });
}
function createBackup(){
  if(!activeServer)return;
  if(!confirm('Create a backup of '+activeServer+' now?'))return;
  fetch('/api/mc/backup',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){alert(d.error);return}
    alert('Backup created: '+d.name);
  });
}
var iconCrop={img:null,fileInput:null,scale:1,left:0,top:0,width:0,height:0,size:0,x:0,y:0,dragging:false,startX:0,startY:0,startCropX:0,startCropY:0};
function positionIconCrop(){
  var image=document.getElementById('iconCropImage');
  var selection=document.getElementById('iconCropSelection');
  image.style.left=iconCrop.left+'px';image.style.top=iconCrop.top+'px';
  image.style.width=iconCrop.width+'px';image.style.height=iconCrop.height+'px';
  selection.style.left=iconCrop.x+'px';selection.style.top=iconCrop.y+'px';
  selection.style.width=iconCrop.size+'px';selection.style.height=iconCrop.size+'px';
}
function openIconCrop(input){
  if(!activeServer||!input.files||!input.files[0])return;
  var file=input.files[0];
  if((file.type||'').indexOf('image/')!==0&&!/\.(png|jpe?g|webp|gif|bmp|avif)$/i.test(file.name)){
    alert('Choose an image file.');input.value='';return;
  }
  var url=URL.createObjectURL(file);
  var image=document.getElementById('iconCropImage');
  image.onload=function(){
    var stage=document.getElementById('iconCropStage');
    var stageSize=stage.clientWidth||320;
    iconCrop.img=image;iconCrop.fileInput=input;
    iconCrop.scale=Math.min(stageSize/image.naturalWidth,stageSize/image.naturalHeight);
    iconCrop.width=image.naturalWidth*iconCrop.scale;
    iconCrop.height=image.naturalHeight*iconCrop.scale;
    iconCrop.left=(stageSize-iconCrop.width)/2;
    iconCrop.top=(stageSize-iconCrop.height)/2;
    iconCrop.size=Math.min(iconCrop.width,iconCrop.height,stageSize*.68);
    iconCrop.x=iconCrop.left+(iconCrop.width-iconCrop.size)/2;
    iconCrop.y=iconCrop.top+(iconCrop.height-iconCrop.size)/2;
    positionIconCrop();
    document.getElementById('iconCropModal').classList.add('open');
    URL.revokeObjectURL(url);
  };
  image.src=url;
  var selection=document.getElementById('iconCropSelection');
  selection.onpointerdown=function(e){
    e.preventDefault();iconCrop.dragging=true;iconCrop.startX=e.clientX;iconCrop.startY=e.clientY;
    iconCrop.startCropX=iconCrop.x;iconCrop.startCropY=iconCrop.y;selection.setPointerCapture(e.pointerId);
  };
  selection.onpointermove=function(e){
    if(!iconCrop.dragging)return;
    var dx=e.clientX-iconCrop.startX,dy=e.clientY-iconCrop.startY;
    iconCrop.x=Math.max(iconCrop.left,Math.min(iconCrop.left+iconCrop.width-iconCrop.size,iconCrop.startCropX+dx));
    iconCrop.y=Math.max(iconCrop.top,Math.min(iconCrop.top+iconCrop.height-iconCrop.size,iconCrop.startCropY+dy));
    positionIconCrop();
  };
  selection.onpointerup=function(){iconCrop.dragging=false};
}
function closeIconCrop(){
  document.getElementById('iconCropModal').classList.remove('open');
  if(iconCrop.fileInput)iconCrop.fileInput.value='';
  iconCrop.img=null;
}
function applyIconCrop(){
  if(!iconCrop.img||!activeServer)return;
  var canvas=document.createElement('canvas');canvas.width=64;canvas.height=64;
  var ctx=canvas.getContext('2d');
  var sx=(iconCrop.x-iconCrop.left)/iconCrop.scale;
  var sy=(iconCrop.y-iconCrop.top)/iconCrop.scale;
  var sw=iconCrop.size/iconCrop.scale;
  ctx.drawImage(iconCrop.img,sx,sy,sw,sw,0,0,64,64);
  canvas.toBlob(function(blob){
    if(!blob){alert('Could not process image.');return}
    var form=new FormData();form.append('server',activeServer);form.append('icon',blob,'server-icon.png');
    fetch('/api/mc/icon',{method:'POST',body:form,credentials:'include'}).then(function(r){return r.json()}).then(function(d){
      if(d.error){alert(d.error);return}
      closeIconCrop();loadServers();alert('Server icon saved.');
    });
  },'image/png');
}
function startServer(){
  if(!activeServer)return;
  var sel=document.getElementById('loaderSelect');
  var loader=sel?sel.value:'fabric';
  fetch('/api/mc/start',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,loader:loader})}).then(function(r){return r.json()}).then(function(){loadServers()});
}
function stopServer(){
  if(!activeServer)return;
  fetch('/api/mc/stop',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer})}).then(function(r){return r.json()}).then(function(){loadServers()});
}
function closeFiles(){filesPath='';var panel=document.getElementById('filesPanel');if(panel)panel.style.display='none'}
var peopleMode='people',peopleGlobal=false;
var peopleTimer=null,peopleRefreshBusy=false,playerDetailsTimer=null,playerDetailsRefreshBusy=false;
function closePeople(){if(peopleTimer){clearInterval(peopleTimer);peopleTimer=null}if(playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}peopleGlobal=false;document.getElementById('peoplePanel').style.display='none'}
function renderSkinHeads(){
  document.querySelectorAll('.player-head[data-skin]').forEach(function(canvas){
    var image=new Image();image.onload=function(){
      var ctx=canvas.getContext('2d');ctx.imageSmoothingEnabled=false;
      ctx.clearRect(0,0,48,48);ctx.drawImage(image,8,8,8,8,0,0,48,48);ctx.drawImage(image,40,8,8,8,0,0,48,48);
    };image.src=canvas.dataset.skin;
  });
}
// Kick off data loads immediately (parallel) instead of waiting on auth, so the
// sidebar fills in one round-trip. If unauthorized, these 401 harmlessly and
// checkAuth() swaps in the login screen.
loadVersions();
loadServers();
checkAuth();
var filesPath='',filesParentPath='',filesEntries=[],filesCrumbPaths=[],filesEditorPath='',filesEditorKind='',filesEditorData=null;
function postJson(url,payload){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify(payload)}).then(function(r){return r.json()});
}
function openFiles(){
  if(!activeServer)return;
  if(!canPriv('files')){toast('File access is not granted for this server.','error');return}
  filesPath='';filesParentPath='';filesEditorPath='';filesEditorKind='';filesEditorData=null;
  document.getElementById('filesEditorWrap').style.display='none';
  document.getElementById('filesPanel').style.display='flex';
  loadFiles('');
}
function loadFiles(path,after){
  if(!activeServer||!canPriv('files'))return;
  fetch('/api/mc/files?name='+encodeURIComponent(activeServer)+'&path='+encodeURIComponent(path||''),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    filesPath=d.path||'';
    filesParentPath=d.parent||'';
    filesEntries=d.entries||[];
    renderFiles(d);
    if(typeof after==='function')after(d);
  });
}
function loadFilesParent(){loadFiles(filesParentPath)}
function loadFilesPathIndex(index){
  var path=filesCrumbPaths[index];
  if(path!==undefined)loadFiles(path);
}
function enterFolder(index){
  var entry=filesEntries[index];
  if(entry&&entry.type==='dir')loadFiles(entry.path);
}
function renderFiles(data){
  var crumb='<button onclick="loadFiles(\'\')">'+escapeHtml(activeServer)+'</button>';
  filesCrumbPaths=[];
  var accumulated='';
  (data.path?data.path.split('/'):[]).forEach(function(part){
    accumulated=accumulated?accumulated+'/'+part:part;
    filesCrumbPaths.push(accumulated);
    crumb+=' <span style="opacity:.35">/</span> <button onclick="loadFilesPathIndex('+(filesCrumbPaths.length-1)+')">'+escapeHtml(part)+'</button>';
  });
  var crumbEl=document.getElementById('filesCrumb');
  if(crumbEl)crumbEl.innerHTML=crumb;
  var list=document.getElementById('filesList');
  if(!list)return;
  if(!filesEntries.length){list.innerHTML='<div class="ban-empty" style="padding:18px">This folder is empty.</div>';return}
  var h='';
  if(data.path)h+='<div class="file-row"><span class="ficon">↰</span><button class="fname" onclick="loadFilesParent()">..</button><span class="fmeta">Parent folder</span></div>';
  filesEntries.forEach(function(entry,index){
    var icon=entry.type==='dir'?'📁':entry.type==='link'?'🔗':'📄';
    var meta=entry.type==='dir'?'Folder':entry.type==='link'?'Link':formatSize(entry.size||0);
    h+='<div class="file-row"><span class="ficon">'+icon+'</span><button class="fname" onclick="'+(entry.type==='dir'?'enterFolder('+index+')':'openFileEntry('+index+')')+'">'+escapeHtml(entry.name)+'</button><span class="fmeta">'+meta+'</span><span class="factions">';
    if(entry.type==='file')h+='<a href="/api/mc/file-download?name='+encodeURIComponent(activeServer)+'&path='+encodeURIComponent(entry.path)+'">Download</a>';
    h+='<button onclick="renameFileEntry('+index+')">Rename</button><button onclick="deleteFileEntry('+index+')">Delete</button></span></div>';
  });
  list.innerHTML=h;
}
function promptNewFolder(){
  var name=prompt('New folder name:');if(!name)return;
  var target=filesPath?filesPath+'/'+name.trim():name.trim();
  postJson('/api/mc/files/folder',{name:activeServer,path:target}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Folder created.');loadFiles(filesPath);
  });
}
function promptNewFile(){
  var name=prompt('New file name:');if(!name)return;
  var target=filesPath?filesPath+'/'+name.trim():name.trim();
  postJson('/api/mc/files/create',{name:activeServer,path:target}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('File created.');loadFiles(filesPath,function(){openFileEntryByName(target)});
  });
}
function openFileEntryByName(path){
  var entry=filesEntries.filter(function(item){return item.path===path})[0];
  if(entry)openFileEntry(filesEntries.indexOf(entry));
}
function uploadFiles(input){
  if(!input||!input.files||!input.files.length)return;
  var form=new FormData();
  form.append('server',activeServer);form.append('path',filesPath);
  Array.prototype.forEach.call(input.files,function(file){form.append('file',file,file.name)});
  var count=input.files.length;
  toast('Uploading '+count+(count===1?' file...':' files...'));
  fetch('/api/mc/files/upload',{method:'POST',credentials:'include',body:form}).then(function(r){return r.json()}).then(function(d){
    input.value='';
    if(d.error){toast(d.error,'error');return}
    toast('Uploaded '+(d.files||[]).length+' file(s).');loadFiles(filesPath);
  }).catch(function(){input.value='';toast('Upload failed.','error')});
}
function renameFileEntry(index){
  var entry=filesEntries[index];if(!entry)return;
  var name=prompt('Rename '+entry.name+' to:',entry.name);
  if(!name||name===entry.name)return;
  postJson('/api/mc/files/rename',{name:activeServer,path:entry.path,new_name:name.trim()}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Renamed.');loadFiles(filesPath);
  });
}
function deleteFileEntry(index){
  var entry=filesEntries[index];if(!entry)return;
  if(!confirm('Delete '+(entry.type==='dir'?'folder':'file')+' "'+entry.name+'"'+(entry.type==='dir'?' and everything inside it':'')+'? This cannot be undone.'))return;
  postJson('/api/mc/files/delete',{name:activeServer,path:entry.path}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Deleted.');loadFiles(filesPath);
  });
}
function openFileEntry(index){
  var entry=filesEntries[index];if(!entry||entry.type==='dir')return;
  fetch('/api/mc/file?name='+encodeURIComponent(activeServer)+'&path='+encodeURIComponent(entry.path),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    filesEditorPath=entry.path;filesEditorKind=d.kind;filesEditorData=d;
    renderFileEditor(d);
  });
}
function closeFileEditor(){
  filesEditorPath='';filesEditorKind='';filesEditorData=null;
  var wrap=document.getElementById('filesEditorWrap');if(wrap){wrap.style.display='none';wrap.innerHTML=''}
}
function renderConfigRows(rows){
  var h='';
  (rows||[]).forEach(function(row,index){
    if(row.type==='blank'){h+='<div class="prop-row blank"></div>'}
    else if(row.type==='comment'){h+='<div class="prop-row comment"><span>'+escapeHtml(row.text)+'</span></div>'}
    else if(row.type==='raw'){h+='<div class="prop-row raw">'+escapeHtml(row.text)+'</div>'}
    else{
      var help=row.help?'<span class="prop-help">'+escapeHtml(row.help)+'</span>':'';
      h+='<div class="prop-row"><span class="prop-key">'+escapeHtml(row.key)+help+'</span>';
      if(row.value_type==='bool'){
        h+='<label class="toggle"><input type="checkbox" data-row="'+index+'" '+(String(row.value).toLowerCase()==='true'?'checked':'')+'><span class="toggle-slider"></span></label>';
      }else if(row.value_type==='number'){
        h+='<input type="number" step="any" data-row="'+index+'" value="'+escapeAttr(row.value)+'">';
      }else{
        h+='<input type="text" data-row="'+index+'" value="'+escapeAttr(row.value)+'">';
      }
      h+='</div>';
    }
  });
  return h;
}
function renderFileEditor(data){
  var wrap=document.getElementById('filesEditorWrap');if(!wrap)return;
  var body='';
  if(data.kind==='config'){
    body='<div style="max-height:52vh;overflow:auto;padding-right:4px">'+renderConfigRows(data.rows)+'</div>';
  }else if(data.kind==='text'){
    body='<textarea id="fileTextEditor" spellcheck="false">'+escapeHtml(data.content||'')+'</textarea>';
  }else{
    body='<div class="ban-empty" style="padding:18px">'+escapeHtml(data.reason||'This file cannot be edited in the browser.')+'</div>';
  }
  var save=data.editable?'<button onclick="saveFileEditor()" style="padding:8px 16px;border-radius:8px;border:1px solid rgba(0,220,120,.35);background:rgba(0,220,120,.14);color:#00dc78;font-weight:700;cursor:pointer">Save file</button>':'';
  wrap.innerHTML='<div class="file-editor"><div class="file-editor-head"><h3>'+escapeHtml(data.path)+'</h3><div style="display:flex;gap:8px;flex-shrink:0">'+
    '<a href="/api/mc/file-download?name='+encodeURIComponent(activeServer)+'&path='+encodeURIComponent(data.path)+'" style="padding:7px 11px;border-radius:7px;border:1px solid var(--mc-line);color:var(--mc-dim);font-size:11px;font-weight:600;text-decoration:none">Download</a>'+
    '<button onclick="closeFileEditor()" style="padding:7px 11px;border-radius:7px;border:1px solid var(--mc-line);background:rgba(255,255,255,.03);color:var(--mc-dim);font-size:11px;font-weight:600;cursor:pointer">Close</button>'+
    save+'</div></div>'+body+'</div>';
  wrap.style.display='block';
}
function saveFileEditor(){
  if(!filesEditorPath)return;
  var payload={name:activeServer,path:filesEditorPath};
  if(filesEditorKind==='config'){
    var rows=JSON.parse(JSON.stringify(filesEditorData&&filesEditorData.rows||[]));
    document.querySelectorAll('#filesEditorWrap input[data-row]').forEach(function(input){
      var row=rows[parseInt(input.dataset.row,10)];
      if(!row)return;
      row.value=input.type==='checkbox'?(input.checked?'true':'false'):input.value;
    });
    payload.rows=rows;
  }else if(filesEditorKind==='text'){
    var textarea=document.getElementById('fileTextEditor');
    payload.content=textarea?textarea.value:'';
  }else{
    return;
  }
  postJson('/api/mc/file',payload).then(function(d){
    if(d.error){toast(d.error,'error');return}
    if(filesEditorData)filesEditorData.size=d.size;
    toast('Saved '+filesEditorPath);
  });
}
function openPeople(){
  if(!activeServer)return;
  if(!canPriv('players')){toast('Player access is not granted for this server.','error');return}
  peopleMode='people';
  if(playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}
  currentPlayerName='';
  var content=document.getElementById('peopleContent');
  content.dataset.peopleServer='';content.dataset.accessSnapshot='';
  document.getElementById('peoplePanel').style.display='flex';
  refreshPeople();
  if(peopleTimer)clearInterval(peopleTimer);
  peopleTimer=setInterval(refreshPeople,100);
}
function openAccess(){
  if(!activeServer)return;
  if(!canPriv('access')){toast('Access management is not granted for this server.','error');return}
  peopleMode='access';
  peopleGlobal=false;
  if(playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}
  currentPlayerName='';
  var content=document.getElementById('peopleContent');
  content.dataset.peopleServer='';content.dataset.accessSnapshot='';
  document.getElementById('peoplePanel').style.display='flex';
  refreshPeople();
  if(peopleTimer)clearInterval(peopleTimer);
  peopleTimer=setInterval(refreshPeople,100);
}
function openGlobalAccess(){
  if(!userInfo||!userInfo.mc_admin){toast('Admin only.','error');return}
  peopleMode='access';
  peopleGlobal=true;
  if(playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}
  currentPlayerName='';
  var content=document.getElementById('peopleContent');
  content.dataset.peopleServer='';content.dataset.accessSnapshot='';
  document.getElementById('peoplePanel').style.display='flex';
  refreshPeople();
  if(peopleTimer)clearInterval(peopleTimer);
  peopleTimer=setInterval(refreshPeople,100);
}
function onlinePlayersHtml(players){
  var h='';
  (players||[]).forEach(function(player){
    var head=player.skin?'<canvas class="player-head" width="48" height="48" data-skin="/api/mc/player-skin?server='+encodeURIComponent(activeServer)+'&player='+encodeURIComponent(player.name)+'" style="width:40px;height:40px;border-radius:8px;image-rendering:pixelated"></canvas>':'<img src="'+player.head+'" style="width:40px;height:40px;border-radius:8px'+(player.online?'':';opacity:.4')+'" onerror="this.style.display=\'none\'">';
    var dot=player.online?'<span style="color:#00dc78;font-size:10px">●</span>':'<span style="color:#8b949e;font-size:10px">○</span>';
    h+='<button onclick="showPlayerDetails(\''+escapeHtml(player.name)+'\')" style="display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.04);padding:8px 10px;border-radius:10px;border:0;cursor:pointer'+(player.online?'':';opacity:.6')+'">'+head+'<span style="color:#fff">'+escapeHtml(player.name)+'</span>'+dot+'</button>';
  });
  if(!(players||[]).length)h='<span style="color:#8b949e">No players reported.</span>';
  return h;
}
function myServerPrivileges(){
  if(userInfo&&userInfo.mc_admin)return MC_PRIVILEGES;
  var server=serversByName[activeServer];
  return (server&&server.privileges)||[];
}
function privilegeCheckboxes(profile){
  var selected=profile.privileges||[];
  var allowed=myServerPrivileges();
  var h='<div class="priv-grid">';
  MC_PRIVILEGES.forEach(function(privilege){
    var blocked=allowed.indexOf(privilege)===-1&&selected.indexOf(privilege)===-1;
    h+='<label'+(blocked?' title="You do not have this privilege" style="opacity:.45"':'')+'><input type="checkbox" data-access-user="'+profile.id+'" data-privilege="'+privilege+'" '+(selected.indexOf(privilege)!==-1?'checked':'')+' '+(blocked?'disabled':'')+'>'+escapeHtml(MC_PRIVILEGE_LABELS[privilege]||privilege)+'</label>';
  });
  return h+'</div>';
}
function canTransferOwner(access){
  if(!userInfo)return false;
  return !!userInfo.mc_admin || (access&&access.owner===userInfo.id);
}
function accessUsersHtml(access){
  if(!access)return '';
  var h='';
  var users=access.users||[];
  var transfer=canTransferOwner(access);
  if(!users.length)h+='<div class="ban-empty" style="padding:14px">No one has access yet.</div>';
  users.forEach(function(profile){
    var avatar=profile.avatar?'<img class="access-avatar" src="'+escapeAttr(profile.avatar)+'" alt="">':'<span class="access-avatar"></span>';
    h+='<div class="access-user">'+avatar+'<div class="access-main"><strong>'+escapeHtml(profile.username||profile.id)+'</strong><small>'+escapeHtml(profile.id)+(profile.owner?' · Owner':'')+'</small>';
    if(profile.owner){
      h+='<div class="access-actions"><span style="font-size:11px;color:#fbbf24">Full access (owner)</span></div>';
    }else{
      h+=privilegeCheckboxes(profile);
      h+='<div class="access-actions"><button onclick="saveAccessUser(\''+profile.id+'\')">Save privileges</button>'+(transfer?'<button class="gold" onclick="setServerOwner(\''+profile.id+'\')">Make owner</button>':'')+'<button class="danger" onclick="removeServerAccess(\''+profile.id+'\')">Remove</button></div>';
    }
    h+='</div></div>';
  });
  h+='<div style="display:flex;gap:8px;margin-top:10px"><input id="accessUserId" placeholder="Discord user ID" style="flex:1;background:#0d1117;border:1px solid var(--mc-line);color:#fff;padding:9px;border-radius:8px"><button onclick="addServerAccess()" style="background:#fbbf24;color:#211600;border:0;border-radius:8px;padding:9px 12px;font-weight:600;cursor:pointer">Add user</button></div>';
  h+='<p style="color:var(--mc-faint);font-size:11px;margin-top:7px">New users inherit the privileges you have; untick anything they should not have, then save.</p>';
  return h;
}
function globalListHtml(title, users, scope, canRemove){
  var h='<div style="margin-top:12px"><strong style="color:var(--mc-txt);font-size:12.5px">'+escapeHtml(title)+'</strong>';
  if(!users||!users.length)h+='<div class="ban-empty" style="padding:10px;margin-top:6px">None</div>';
  (users||[]).forEach(function(profile){
    h+='<div class="access-user" style="margin-top:6px">'+(profile.avatar?'<img class="access-avatar" src="'+escapeAttr(profile.avatar)+'" alt="">':'<span class="access-avatar"></span>')+'<div class="access-main"><strong>'+escapeHtml(profile.username||profile.id)+'</strong><small>'+escapeHtml(profile.id)+'</small></div>'+(canRemove?'<button onclick="removeGlobalAccess(\''+profile.id+'\',\''+scope+'\')" style="align-self:center;padding:5px 9px;border-radius:6px;border:1px solid rgba(239,68,68,.28);background:rgba(255,255,255,.03);color:#f87171;font-size:11px;font-weight:600;cursor:pointer">Remove</button>':'')+'</div>';
  });
  return h+'</div>';
}
function globalAccessHtml(global, canGrantAdmin, standalone){
  if(!global)return '';
  var h='<div'+(standalone?'':' style="margin-top:28px;border-top:1px solid var(--mc-line);padding-top:18px"')+'><h3 style="color:#fff;font-size:14px;margin-bottom:4px">Global access</h3><p style="color:var(--mc-faint);font-size:11.5px">Global admins can manage every server. The create permission only allows making new servers.</p>';
  h+=globalListHtml('Global admins',global.admins,'admin',!!canGrantAdmin);
  h+=globalListHtml('Can create servers',global.create,'create',true);
  h+='<div style="display:flex;gap:8px;margin-top:12px"><input id="globalUserId" placeholder="Discord user ID" style="flex:1;background:#0d1117;border:1px solid var(--mc-line);color:#fff;padding:9px;border-radius:8px"><select id="globalScope" style="background:#0d1117;border:1px solid var(--mc-line);color:#fff;padding:9px;border-radius:8px"><option value="create">Can create servers</option>'+(canGrantAdmin?'<option value="admin">Global admin</option>':'')+'</select><button onclick="addGlobalAccess()" style="background:#fbbf24;color:#211600;border:0;border-radius:8px;padding:9px 12px;font-weight:600;cursor:pointer">Add</button></div>';
  return h+'</div>';
}
function renderAccessPanel(data, server){
  var h='<div style="display:flex;justify-content:space-between;align-items:center"><h2 style="color:#fff;font-size:20px">'+escapeHtml(server)+' · Access</h2><button onclick="closePeople()" style="background:none;border:none;color:#8b949e;font-size:24px;cursor:pointer">X</button></div>';
  h+='<p style="color:var(--mc-faint);font-size:12px;margin:8px 0 16px">Owners have full access automatically. Everyone else only gets the privileges you tick.</p>';
  h+=accessUsersHtml(data);
  if(data.is_admin)h+=globalAccessHtml(data.global,data.can_grant_admin);
  return h;
}
function renderGlobalAccessPanel(data){
  var h='<div style="display:flex;justify-content:space-between;align-items:center"><h2 style="color:#fff;font-size:20px">Global access</h2><button onclick="closePeople()" style="background:none;border:none;color:#8b949e;font-size:24px;cursor:pointer">X</button></div>';
  h+='<p style="color:var(--mc-faint);font-size:12px;margin:8px 0 16px">Global admins can manage every server. The create permission only allows making new servers.</p>';
  h+=globalAccessHtml(data,data.can_grant_admin,true);
  return h;
}
function bannedPlayersHtml(bans){
  if(!bans||!bans.length)return '<div class="ban-empty">No banned players.</div>';
  return bans.map(function(ban){
    var target=ban.name&&ban.name!=='Unknown player'?ban.name:ban.uuid;
    var head=ban.head?'<img class="ban-head" src="'+ban.head+'" alt="">':'<span class="ban-head-placeholder">👤</span>';
    var meta=[ban.source,ban.expires&&ban.expires!=='forever'?'Expires '+ban.expires:''].filter(Boolean).join(' · ');
    return '<div class="ban-row">'+head+'<div class="ban-row-info"><strong>'+escapeHtml(ban.name||'Unknown player')+'</strong><small>'+escapeHtml(meta||ban.uuid||'Player ban')+'</small><small class="ban-reason">'+escapeHtml(ban.reason||'No reason provided')+'</small></div><button class="mc-action-btn mc-action-danger" onclick="unbanServerItem(\'player\',\''+escapeHtml(target)+'\')">Unban</button></div>';
  }).join('');
}
function bannedIpsHtml(bans){
  if(!bans||!bans.length)return '<div class="ban-empty">No banned IPs.</div>';
  return bans.map(function(ban){
    var head=ban.head?'<img class="ban-head" src="'+ban.head+'" alt="">':'<span class="ban-head-placeholder">⌁</span>';
    var meta=ban.name?escapeHtml(ban.name):'Player not recorded for this IP';
    var extra=[ban.source,ban.expires&&ban.expires!=='forever'?'Expires '+ban.expires:''].filter(Boolean).join(' · ');
    return '<div class="ban-row">'+head+'<div class="ban-row-info"><strong>'+escapeHtml(ban.ip)+'</strong><small>'+meta+(extra?' · '+escapeHtml(extra):'')+'</small><small class="ban-reason">'+escapeHtml(ban.reason||'No reason provided')+'</small></div><button class="mc-action-btn mc-action-danger" onclick="unbanServerItem(\'ip\',\''+escapeHtml(ban.ip)+'\')">Unban IP</button></div>';
  }).join('');
}
function refreshPeople(){
  if(peopleRefreshBusy||currentPlayerName)return;
  if(!peopleGlobal&&!activeServer)return;
  var server=activeServer;
  peopleRefreshBusy=true;
  if(peopleMode==='access'&&peopleGlobal){
    fetch('/api/mc/global-access',{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
      var panel=document.getElementById('peoplePanel');
      if(!peopleGlobal||!panel||panel.style.display==='none'||currentPlayerName)return;
      if(d.error){toast(d.error,'error');closePeople();return}
      var content=document.getElementById('peopleContent');
      var snapshot=JSON.stringify(d);
      if(content.dataset.accessSnapshot!==snapshot){
        content.innerHTML=renderGlobalAccessPanel(d);
        content.dataset.accessSnapshot=snapshot;
      }
    }).catch(function(err){console.error('Global access refresh failed:',err)}).finally(function(){peopleRefreshBusy=false});
    return;
  }
  if(peopleMode==='access'){
    fetch('/api/mc/permissions?name='+encodeURIComponent(server),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
      var panel=document.getElementById('peoplePanel');
      if(server!==activeServer||!panel||panel.style.display==='none'||currentPlayerName)return;
      if(d.error){toast(d.error,'error');closePeople();return}
      var content=document.getElementById('peopleContent');
      var snapshot=JSON.stringify(d);
      if(content.dataset.accessSnapshot!==snapshot){
        content.innerHTML=renderAccessPanel(d,server);
        content.dataset.accessSnapshot=snapshot;
      }
    }).catch(function(err){console.error('Access refresh failed:',err)}).finally(function(){peopleRefreshBusy=false});
    return;
  }
  fetch('/api/mc/players?name='+encodeURIComponent(server),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var panel=document.getElementById('peoplePanel');
    if(server!==activeServer||!panel||panel.style.display==='none'||currentPlayerName)return;
    if(d.error){toast(d.error,'error');closePeople();return}
    var content=document.getElementById('peopleContent');
    var players=d.players||[],bannedPlayers=d.banned_players||[],bannedIps=d.banned_ips||[],access=d.access||null;
    var playerSnapshot=JSON.stringify(players),bannedPlayersSnapshot=JSON.stringify(bannedPlayers),bannedIpsSnapshot=JSON.stringify(bannedIps),accessSnapshot=JSON.stringify(access);
    var online=document.getElementById('onlinePlayers'),accessEl=document.getElementById('discordAccess'),playerBans=document.getElementById('bannedPlayers'),ipBans=document.getElementById('bannedIps');
    if(content.dataset.peopleServer===server&&online&&playerBans&&ipBans&&(accessEl||!access)){
      if(online.dataset.snapshot!==playerSnapshot){online.innerHTML=onlinePlayersHtml(players);online.dataset.snapshot=playerSnapshot;renderSkinHeads()}
      if(accessEl&&accessEl.dataset.snapshot!==accessSnapshot){accessEl.innerHTML=accessUsersHtml(access);accessEl.dataset.snapshot=accessSnapshot}
      if(playerBans.dataset.snapshot!==bannedPlayersSnapshot){playerBans.innerHTML=bannedPlayersHtml(bannedPlayers);playerBans.dataset.snapshot=bannedPlayersSnapshot}
      if(ipBans.dataset.snapshot!==bannedIpsSnapshot){ipBans.innerHTML=bannedIpsHtml(bannedIps);ipBans.dataset.snapshot=bannedIpsSnapshot}
      var playerCount=document.getElementById('bannedPlayerCount'),ipCount=document.getElementById('bannedIpCount');
      if(playerCount)playerCount.textContent=bannedPlayers.length;
      if(ipCount)ipCount.textContent=bannedIps.length;
      return;
    }
    var h='<div style="display:flex;justify-content:space-between;align-items:center"><h2 style="color:#fff;font-size:20px">'+escapeHtml(server)+'</h2><button onclick="closePeople()" style="background:none;border:none;color:#8b949e;font-size:24px;cursor:pointer">X</button></div>';
    h+='<p style="color:#8b949e;font-size:12px;margin:8px 0 20px">All players <span style="color:#00dc78">Live · 0.1s</span></p><div id="onlinePlayers" style="display:flex;flex-wrap:wrap;gap:10px"></div>';
    if(access)h+='<div style="display:flex;justify-content:space-between;align-items:center;margin:24px 0 10px"><p style="color:#8b949e;font-size:12px">Discord access</p><button onclick="openAccess()" style="background:none;border:1px solid var(--mc-line);color:var(--mc-accent);border-radius:7px;padding:4px 9px;font-size:11px;font-weight:600;cursor:pointer">Manage</button></div><div id="discordAccess" style="display:grid;gap:8px"></div>';
    h+='<section class="ban-section"><h3>Banned players <span id="bannedPlayerCount">'+bannedPlayers.length+'</span></h3><div id="bannedPlayers" class="ban-list"></div></section>';
    h+='<section class="ban-section"><h3>Banned IPs <span id="bannedIpCount">'+bannedIps.length+'</span></h3><div id="bannedIps" class="ban-list"></div></section>';
    content.innerHTML=h;content.dataset.peopleServer=server;content.dataset.accessSnapshot='';
    online=document.getElementById('onlinePlayers');online.innerHTML=onlinePlayersHtml(players);online.dataset.snapshot=playerSnapshot;
    if(access){accessEl=document.getElementById('discordAccess');accessEl.innerHTML=accessUsersHtml(access);accessEl.dataset.snapshot=accessSnapshot}
    playerBans=document.getElementById('bannedPlayers');playerBans.innerHTML=bannedPlayersHtml(bannedPlayers);playerBans.dataset.snapshot=bannedPlayersSnapshot;
    ipBans=document.getElementById('bannedIps');ipBans.innerHTML=bannedIpsHtml(bannedIps);ipBans.dataset.snapshot=bannedIpsSnapshot;
    renderSkinHeads();
  }).catch(function(err){console.error('Player list refresh failed:',err)}).finally(function(){peopleRefreshBusy=false});
}
var currentPlayerName='';
var currentPlayerInventory={};
var currentInventoryAssetVersion='1.21.11';
function inventorySlot(slot,item){
  var label=item?item.id.replace(/^minecraft:/,'').replace(/_/g,' '):'';
  var enchantText=item&&item.enchants&&item.enchants.length?' Enchants: '+item.enchants.map(function(e){return e.name+' '+e.level}).join(', '):'';
  var title=item?escapeHtml(label+' x'+item.count+enchantText+'\n'+(item.raw||'')):'Empty slot';
  var itemPath=item?item.id.replace(/^minecraft:/,''):'';
  var icon=item?'<img src="https://assets.mcasset.cloud/'+currentInventoryAssetVersion+'/assets/minecraft/textures/item/'+itemPath+'.png" data-fallback="https://assets.mcasset.cloud/'+currentInventoryAssetVersion+'/assets/minecraft/textures/block/'+itemPath+'.png" onerror="if(this.dataset.fallback&&this.src.indexOf(\'/block/\')===-1){this.src=this.dataset.fallback}else{this.remove()}" alt="">':'';
  var enchantBadge=item&&item.enchants&&item.enchants.length?'<span style="position:absolute;left:3px;top:1px;color:#c084fc;font-size:9px;font-weight:700">E</span>':'';
  return '<div class="inventory-slot '+(item?'':'empty')+'" '+(item?'draggable="true" ondragstart="inventoryDrag(event,\''+slot+'\')"':'')+' ondragover="event.preventDefault()" ondrop="inventoryDrop(event,\''+slot+'\')" title="'+title+'">'+(item?icon+enchantBadge+'<span style="color:#e8e8e8;text-align:center;font-size:9px;line-height:1.1;text-transform:capitalize">'+escapeHtml(label)+'</span><span class="item-count">'+item.count+'</span>':'')+'</div>';
}
function renderInventory(inventory){
  currentPlayerInventory=inventory||{};
  var h='<div class="inventory-section"><h3>Armor and offhand</h3><div class="inventory-row">';
  ['armor.head','armor.chest','armor.legs','armor.feet','weapon.offhand'].forEach(function(slot){h+=inventorySlot(slot,currentPlayerInventory[slot])});
  h+='</div></div><div class="inventory-section"><h3>Main inventory</h3><div class="inventory-grid">';
  for(var row=0;row<3;row++)for(var col=0;col<9;col++){var slot='inventory.'+(row*9+col);h+=inventorySlot(slot,currentPlayerInventory[slot])}
  h+='</div></div><div class="inventory-section"><h3>Hotbar</h3><div class="inventory-grid">';
  for(var hot=0;hot<9;hot++)h+=inventorySlot('hotbar.'+hot,currentPlayerInventory['hotbar.'+hot]);
  return h+'</div></div>';
}
function inventoryDrag(event,slot){event.dataTransfer.setData('slot',slot)}
function inventoryDrop(event,destination){
  event.preventDefault();
  var source=event.dataTransfer.getData('slot');
  if(!source||source===destination)return;
  fetch('/api/mc/inventory-move',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({server:activeServer,player:currentPlayerName,source:source,destination:destination})}).then(function(r){return r.json()}).then(function(d){if(d.error)alert(d.error);else showPlayerDetails(currentPlayerName)});
}
function playerTechnicalHtml(details){
  var h='';
  Object.keys(details||{}).forEach(function(key){h+='<section style="margin:14px 0"><h3 style="color:#00dc78;font-size:14px">'+escapeHtml(key)+'</h3><pre style="white-space:pre-wrap;word-break:break-word;background:#0d1117;border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:12px;color:#c9d1d9;font-size:12px">'+escapeHtml((details[key]||[]).join('\n'))+'</pre></section>'});
  return h;
}
function showPlayerDetails(player){
  if(peopleTimer){clearInterval(peopleTimer);peopleTimer=null}
  if(playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}
  currentPlayerName=player;
  fetch('/api/mc/player?server='+encodeURIComponent(activeServer)+'&player='+encodeURIComponent(player),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(currentPlayerName!==player||document.getElementById('peoplePanel').style.display==='none')return;
    if(d.error&&!d.online){alert(d.error);return}
    if(d.error&&d.online)d={name:player,skin:false,asset_version:'1.21.11',inventory:{},details:{},live:false,online:true};
    currentInventoryAssetVersion=d.asset_version||'1.21.11';
    var h='<div style="display:flex;justify-content:space-between;align-items:center"><h2 style="color:#fff;font-size:20px">'+escapeHtml(player)+'</h2><button class="mc-action-btn mc-action-neutral" onclick="openPeople()">Back to Players</button></div>';
    h+='<div style="display:flex;gap:18px;align-items:center;margin:18px 0">'+(d.skin?'<img src="'+d.skin_url+'" style="width:128px;height:128px;image-rendering:pixelated;object-fit:contain;background:#111">':'<img src="https://mc-heads.net/avatar/'+encodeURIComponent(player)+'/128" style="width:128px;height:128px;border-radius:12px">')+'<div class="player-actions"><button class="mc-action-btn mc-action-neutral" onclick="playerAction(\'kick\')">Kick</button><button class="mc-action-btn mc-action-danger" onclick="playerAction(\'ban\')">Ban Player</button><button class="mc-action-btn mc-action-danger" onclick="playerAction(\'ban-ip\')">Ban IP</button><button class="mc-action-btn mc-action-success" onclick="playerAction(\'op\')">OP</button><button class="mc-action-btn mc-action-neutral" onclick="playerAction(\'unop\')">UnOP</button><button class="mc-action-btn mc-action-success" onclick="playerAction(\'fly\')">Fly</button><button class="mc-action-btn mc-action-neutral" onclick="playerAction(\'unfly\')">No Fly</button></div></div>';
    h+='<div id="playerLiveStatus" class="player-live-status '+(d.live?'is-live':'')+'">'+(d.live?'● Live · server data, 0.1s refresh':d.online?'● Player online · waiting for live data':'● Last saved data · player is offline')+'</div>';
    h+='<div id="playerInventoryView"></div><details style="margin-top:20px"><summary style="color:#00dc78;cursor:pointer">Technical NBT details</summary><div id="playerTechnicalDetails"></div></details>';
    var content=document.getElementById('peopleContent');content.innerHTML=h;
    var inventoryView=document.getElementById('playerInventoryView');
    inventoryView.innerHTML=renderInventory(d.inventory);inventoryView.dataset.snapshot=JSON.stringify(d.inventory||{});
    var technical=document.getElementById('playerTechnicalDetails');
    technical.innerHTML=playerTechnicalHtml(d.details);technical.dataset.snapshot=JSON.stringify(d.details||{});
    if(d.online)playerDetailsTimer=setInterval(refreshPlayerDetails,100);
  });
}
function refreshPlayerDetails(){
  if(!currentPlayerName||playerDetailsRefreshBusy||!document.getElementById('playerInventoryView'))return;
  var server=activeServer,player=currentPlayerName;playerDetailsRefreshBusy=true;
  fetch('/api/mc/player?server='+encodeURIComponent(server)+'&player='+encodeURIComponent(player),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    if(server!==activeServer||player!==currentPlayerName||!document.getElementById('playerInventoryView'))return;
    if(d.error){var status=document.getElementById('playerLiveStatus');if(status){status.textContent=d.online?'Player online · waiting for live data':'Live sync unavailable';status.classList.remove('is-live')}if(!d.online&&playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}return}
    currentInventoryAssetVersion=d.asset_version||currentInventoryAssetVersion;
    var status=document.getElementById('playerLiveStatus');
    if(status){status.textContent=d.live?'● Live · server data, 0.1s refresh':d.online?'● Player online · waiting for live data':'● Last saved data · player is offline';status.classList.toggle('is-live',!!d.live)}
    var inventoryView=document.getElementById('playerInventoryView'),inventorySnapshot=JSON.stringify(d.inventory||{});
    if(inventoryView&&inventoryView.dataset.snapshot!==inventorySnapshot){inventoryView.innerHTML=renderInventory(d.inventory);inventoryView.dataset.snapshot=inventorySnapshot}
    var technical=document.getElementById('playerTechnicalDetails'),detailsSnapshot=JSON.stringify(d.details||{});
    if(technical&&technical.dataset.snapshot!==detailsSnapshot){technical.innerHTML=playerTechnicalHtml(d.details);technical.dataset.snapshot=detailsSnapshot}
    if(!d.online&&playerDetailsTimer){clearInterval(playerDetailsTimer);playerDetailsTimer=null}
  }).catch(function(err){console.error('Live player refresh failed:',err)}).finally(function(){playerDetailsRefreshBusy=false});
}
function playerAction(action){
  var reason=(action==='op'||action==='unop')?'Website action':(prompt('Reason:','Website action')||'Website action');
  fetch('/api/mc/player-action',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({server:activeServer,player:currentPlayerName,action:action,reason:reason})}).then(function(r){return r.json()}).then(function(d){if(d.error)alert(d.error);else if(action==='ban'||action==='ban-ip')openPeople()});
}
function unbanServerItem(type,target){
  if(!confirm('Unban '+target+'?'))return;
  fetch('/api/mc/unban',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({server:activeServer,type:type,target:target})}).then(function(r){return r.json()}).then(function(d){if(d.error){alert(d.error);return}refreshPeople()}).catch(function(){alert('Could not unban '+target+'.')});
}
function saveAccessUser(userId){
  var selected=[];
  document.querySelectorAll('input[data-access-user="'+userId+'"]').forEach(function(input){
    if(input.checked)selected.push(input.dataset.privilege);
  });
  if(!selected.length){toast('Choose at least one privilege.','error');return}
  postJson('/api/mc/permissions',{name:activeServer,user_id:userId,privileges:selected}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Privileges saved.');refreshPeople();
  });
}
function addServerAccess(){
  var input=document.getElementById('accessUserId');if(!input||!input.value.trim())return;
  var privileges=myServerPrivileges().slice();
  if(!privileges.length){toast('You have no privileges to grant.','error');return}
  postJson('/api/mc/permissions',{name:activeServer,user_id:input.value.trim(),privileges:privileges}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Access granted.');refreshPeople();
  });
}
function removeServerAccess(userId){
  if(!confirm('Remove access for this user?'))return;
  fetch('/api/mc/permissions',{method:'DELETE',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,user_id:userId})}).then(function(r){return r.json()}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Access removed.');refreshPeople();
  });
}
function setServerOwner(userId){
  if(!confirm('Make this user the owner? They will have full access.'))return;
  postJson('/api/mc/permissions/owner',{name:activeServer,user_id:userId}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Ownership transferred.');refreshPeople();
  });
}
function addGlobalAccess(){
  var input=document.getElementById('globalUserId');
  var scope=document.getElementById('globalScope');
  if(!input||!input.value.trim())return;
  postJson('/api/mc/global-access',{user_id:input.value.trim(),scope:scope?scope.value:'create',action:'add'}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Global access granted.');refreshPeople();
  });
}
function removeGlobalAccess(userId,scope){
  if(!confirm('Remove this global permission?'))return;
  postJson('/api/mc/global-access',{user_id:userId,scope:scope,action:'remove'}).then(function(d){
    if(d.error){toast(d.error,'error');return}
    toast('Global permission removed.');refreshPeople();
  });
}
</script>
<div id="filesPanel" style="display:none;position:fixed;inset:0;width:auto;height:auto;flex-direction:column;background:rgba(5,5,8,.98);z-index:200;overflow-y:auto;padding:28px;backdrop-filter:blur(12px)">
  <div class="files-shell">
    <div class="files-head">
      <div>
        <h2 id="filesTitle">Files</h2>
        <div class="files-crumb" id="filesCrumb"></div>
      </div>
      <div style="display:flex;gap:8px;flex-shrink:0">
        <button onclick="loadFiles(filesPath)" style="padding:8px 12px;border-radius:8px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);color:var(--mc-txt);font-size:12px;font-weight:600;cursor:pointer">Refresh</button>
        <button onclick="closeFiles()" style="padding:8px 12px;border-radius:8px;border:1px solid var(--mc-line);background:rgba(255,255,255,.04);color:var(--mc-dim);font-size:12px;font-weight:600;cursor:pointer">Close</button>
      </div>
    </div>
    <div class="files-toolbar">
      <button class="primary" onclick="promptNewFolder()">+ Folder</button>
      <button class="primary" onclick="promptNewFile()">+ File</button>
      <label for="fileUploadInput">Upload files</label>
      <input id="fileUploadInput" type="file" multiple style="display:none" onchange="uploadFiles(this)">
    </div>
    <p style="color:var(--mc-faint);font-size:11px;margin:0">Text and config files up to 2 MB can be edited in place; uploads are limited to 1 GB per file.</p>
    <div class="files-list" id="filesList"></div>
    <div id="filesEditorWrap" style="display:none"></div>
  </div>
</div>
<div id="peoplePanel" style="display:none;position:fixed;inset:0;width:auto;height:auto;flex-direction:column;background:rgba(5,5,8,.98);z-index:200;overflow-y:auto;padding:28px;backdrop-filter:blur(12px)">
  <div id="peopleContent" style="max-width:900px;width:100%;margin:0 auto"></div>
</div>

<div id="toastWrap"></div>

<div class="modal-overlay" id="confirmModal">
  <div class="modal-card">
    <h3 id="confirmTitle">Are you sure?</h3>
    <p id="confirmText"></p>
    <div class="modal-actions">
      <button onclick="confirmResolve(false)">Cancel</button>
      <button class="confirm-danger" id="confirmOkBtn" onclick="confirmResolve(true)">Confirm</button>
    </div>
  </div>
</div>

<div class="ss-pip" id="ssPip" onclick="openSS()" title="Open live view">
  <img id="ssPipImg" alt="PC screen">
  <div class="ss-pip-bar">
    <span class="ss-pip-title"><span class="d"></span>PC Live</span>
    <span class="ss-pip-open">Click to open</span>
  </div>
</div>

<div class="ss-overlay" id="ssOverlay">
  <div class="ss-stage">
    <canvas id="ssCanvas"></canvas>
    <div class="ss-hint" id="ssHint">Click the image to reveal the live view &middot; ESC to close</div>
  </div>
  <div class="ss-top">
    <span class="ss-live">LIVE</span>
    <span class="ss-title"><span class="d"></span>PC Screen</span>
    <span class="ss-meta"><span id="ssRes">-</span>&nbsp;&middot;&nbsp;<span id="ssFpsInfo">-</span>&nbsp;&middot;&nbsp;<span id="ssViewers"></span>&nbsp;&middot;&nbsp;<span id="ssPing" title="Latency to the PC">-</span></span>
    <span class="ss-spacer"></span>
    <button class="ss-x" onclick="closeSS()" title="Close (ESC)">&#10005;</button>
  </div>
  <div class="ss-dock">
    <select class="ss-select" id="ssQuality" onchange="ssSendQuality()">
      <option value="full">Full quality</option>
      <option value="720p" selected>720p</option>
      <option value="480p">480p</option>
    </select>
    <select class="ss-select" id="ssFps" onchange="ssSendFps()">
      <option value="full" selected>Full FPS (100)</option>
      <option value="60">60 FPS</option>
      <option value="30">30 FPS</option>
      <option value="20">20 FPS</option>
      <option value="15">15 FPS</option>
      <option value="10">10 FPS</option>
      <option value="5">5 FPS</option>
    </select>
    <button class="ss-btn" id="ssCtrlBtn" onclick="ssToggleControl()" style="display:none">Control this PC</button>
  </div>
</div>

<script>
/* ============ TOASTS + CONFIRM ============ */
function toast(msg,type){
  var w=document.getElementById('toastWrap');if(!w)return;
  var t=document.createElement('div');t.className='toast'+(type?' '+type:'');t.textContent=msg;
  w.appendChild(t);setTimeout(function(){t.style.opacity='0';t.style.transition='opacity .35s';setTimeout(function(){t.remove()},380)},3400);
}
var confirmResolve=null;
function confirmDialog(title,text,danger){
  return new Promise(function(res){
    document.getElementById('confirmTitle').textContent=title;
    document.getElementById('confirmText').textContent=text;
    document.getElementById('confirmOkBtn').className=danger===false?'confirm-ok':'confirm-danger';
    document.getElementById('confirmModal').classList.add('open');
    confirmResolve=function(v){document.getElementById('confirmModal').classList.remove('open');confirmResolve=null;res(v)};
  });
}
function confirmResolveFn(v){if(confirmResolve)confirmResolve(v)}

/* ============ SCREENSHARE ============ */
var ssWS=null,ssViewing=false,ssControl=false,ssOpen=false,ssReconTimer=null,ssIdleTimer=null,ssQueue=[],ssCanControl=null,ssPingTimer=null;
var ssCanvas=document.getElementById('ssCanvas'),ssCtx=ssCanvas?ssCanvas.getContext('2d'):null;
var ssScreenW=0,ssScreenH=0;

function ssWSUrl(){var loc=window.location;return (loc.protocol==='https:'?'wss://':'ws://')+loc.host+'/ws/screencast'}

function ssConnect(){
  if(ssWS&&(ssWS.readyState===0||ssWS.readyState===1))return;
  try{ssWS=new WebSocket(ssWSUrl())}catch(e){return}
  ssWS.binaryType='arraybuffer';
  ssWS.onmessage=function(ev){
    if(typeof ev.data==='string'){
      var m;try{m=JSON.parse(ev.data)}catch(e){return}
      if(m.type==='hello'){
        if(m.screen){ssScreenW=m.screen.width;ssScreenH=m.screen.height;ssUpdateRes()}
        /* Server is authoritative about whether THIS session may control. */
        ssCanControl=(m.owner!==false)&&(m.control!==false);
        var cb=document.getElementById('ssCtrlBtn');
        if(cb&&ssOpen)cb.style.display=ssCanControl?'':'none';
        if(ssOpen&&!ssCanControl&&m.control===false)toast('Remote control unavailable: pyautogui not installed on the PC.','error');
      }
      else if(m.type==='control_error'){toast(m.error||'Remote control error.','error');ssSetControl(false)}
      else if(m.type==='pong'){ssShowPing(typeof m.t==='number'?performance.now()-m.t:0)}
      else if(m.type==='viewers'){var v=document.getElementById('ssViewers');if(v)v.textContent=m.count+(m.count===1?' viewer':' viewers')}
    }else{
      var img=new Image();
      img.onload=function(){
        if(!ssCanvas)return;
        if(ssCanvas.width!==img.width||ssCanvas.height!==img.height){ssCanvas.width=img.width;ssCanvas.height=img.height}
        ssCtx.drawImage(img,0,0);
        ssCanvas.classList.remove('ss-spoiler');
        var f=document.getElementById('ssFpsInfo');if(f)f.textContent=img.width+'×'+img.height;
        URL.revokeObjectURL(img.src);
      };
      img.src=URL.createObjectURL(new Blob([ev.data],{type:'image/jpeg'}));
    }
  };
  ssWS.onopen=function(){
    // Flush anything queued while the socket was still connecting, then re-sync
    // view state so frames start immediately — one click on the PiP is enough.
    var q=ssQueue.splice(0);
    for(var i=0;i<q.length;i++){try{ssWS.send(JSON.stringify(q[i]))}catch(e){}}
    if(ssOpen){
      ssSend({type:'view',active:true});ssViewing=true;
      ssSendQuality();ssSendFps();
    }
    /* ping the server every 2s so the header shows live latency */
    if(ssPingTimer)clearInterval(ssPingTimer);
    ssPingTimer=setInterval(ssPingTick,2000);ssPingTick();
  };
  ssWS.onclose=function(){
    ssWS=null;ssViewing=false;
    if(ssPingTimer){clearInterval(ssPingTimer);ssPingTimer=null}
    var pe=document.getElementById('ssPing');if(pe){pe.textContent='-';pe.className=''}
    if(ssOpen)ssReconTimer=setTimeout(ssConnect,2500);
  };
  ssWS.onerror=function(){try{ssWS.close()}catch(e){}};
}

function ssSend(obj){
  if(ssWS&&ssWS.readyState===1){try{ssWS.send(JSON.stringify(obj))}catch(e){}return}
  // Socket still connecting (or closed): queue instead of silently dropping.
  ssQueue.push(obj);
  if(ssQueue.length>32)ssQueue.shift();
}
function ssSendQuality(){ssSend({type:'quality',quality:document.getElementById('ssQuality').value})}
function ssSendFps(){var v=document.getElementById('ssFps').value;var n=v==='full'?100:Math.max(1,Math.min(100,parseInt(v,10)||100));ssSend({type:'fps',fps:n})}
function ssPingTick(){if(ssWS&&ssWS.readyState===1)ssSend({type:'ping',t:performance.now()})}
function ssShowPing(ms){var el=document.getElementById('ssPing');if(!el)return;var v=Math.max(0,Math.round(ms));el.textContent=v+' ms';el.className=v<80?'p-ok':v<200?'p-warn':'p-bad'}
function ssUpdateRes(){var r=document.getElementById('ssRes');if(r)r.textContent=ssScreenW?ssScreenW+'×'+ssScreenH:'-'}

function ssSeedFromPip(){
  // Paint the exact thumbnail the user clicked (bottom-right PiP) onto the big
  // canvas, blurred as a spoiler, so opening feels instant and continuous.
  var pipImg=document.getElementById('ssPipImg');
  if(!ssCanvas||!ssCtx||!pipImg||!pipImg.src||!pipImg.complete||!pipImg.naturalWidth)return;
  ssCanvas.width=pipImg.naturalWidth;ssCanvas.height=pipImg.naturalHeight;
  ssCtx.drawImage(pipImg,0,0,ssCanvas.width,ssCanvas.height);
  ssCanvas.classList.add('ss-spoiler');
}

function openSS(){
  if(!userInfo||!userInfo.mc_owner){toast('Owner only.','error');return}
  document.getElementById('ssOverlay').classList.add('open');
  ssOpen=true;
  ssSeedFromPip();
  ssConnect();
  ssSend({type:'view',active:true});ssViewing=true;
  ssSendQuality();ssSendFps();
  /* Remote control is owner-only: hide the button for everyone else. */
  var cb=document.getElementById('ssCtrlBtn');
  if(cb)cb.style.display=(userInfo&&userInfo.mc_owner)?'':'none';
  var pip=document.getElementById('ssPip');if(pip)pip.style.display='none';
  ssStopIdle();
}
function closeSS(){
  document.getElementById('ssOverlay').classList.remove('open');
  ssOpen=false;ssSetControl(false);
  ssQueue.length=0;
  ssSend({type:'view',active:false});ssViewing=false;
  ssStartIdle();
  if(ssWS){try{ssWS.close()}catch(e){}ssWS=null}
}

function ssToggleControl(){
  if(!userInfo||!userInfo.mc_owner){toast('Only the owner can control this PC.','error');return}
  if(ssCanControl===false){toast('Remote control unavailable: pyautogui not installed on the PC.','error');return}
  if(!ssControl){
    confirmDialog('Enable remote control?','You will fully control this PC (mouse + keyboard) through the browser. The toolbar stays on top so you can turn it off anytime.',false).then(function(ok){
      if(ok)ssSetControl(true);
    });
  }else ssSetControl(false);
}
function ssSetControl(on){
  ssControl=on;
  var b=document.getElementById('ssCtrlBtn');
  if(b){b.textContent=on?'Control ON — ESC to stop':'Control this PC';b.className='ss-btn'+(on?' control-on':'')}
  if(ssCanvas)ssCanvas.classList.toggle('control-live',on);
  var h=document.getElementById('ssHint');
  if(h)h.textContent=on?'Control is LIVE — mouse & keyboard are being forwarded to the PC':'Click the image to reveal the live view · ESC to close';
}

/* --- remote input forwarding --- */
function ssCanvasPos(ev){
  var r=ssCanvas.getBoundingClientRect();
  return{x:(ev.clientX-r.left)/r.width,y:(ev.clientY-r.top)/r.height};
}
function ssBindInput(){
  if(!ssCanvas)return;
  /* Clicking the image reveals the live view (removes the spoiler blur). Remote
     control is separate — only the owner gets it, via the toolbar button. */
  ssCanvas.addEventListener('click',function(){
    if(!ssOpen)return;
    ssCanvas.classList.remove('ss-spoiler');
    if(ssControl)return;
    /* Re-kick the stream in case frames haven't started flowing yet. */
    ssSend({type:'view',active:true});ssViewing=true;
    ssSendQuality();ssSendFps();
  });
  ssCanvas.addEventListener('contextmenu',function(e){if(ssControl)e.preventDefault()});
  ssCanvas.addEventListener('mousedown',function(e){
    if(!ssControl)return;e.preventDefault();
    var p=ssCanvasPos(e);
    ssSend({type:'input',kind:'mouse',action:'down',x:p.x,y:p.y,button:e.button===2?'right':e.button===1?'middle':'left'});
  });
  ssCanvas.addEventListener('mouseup',function(e){
    if(!ssControl)return;e.preventDefault();
    var p=ssCanvasPos(e);
    ssSend({type:'input',kind:'mouse',action:'up',x:p.x,y:p.y,button:e.button===2?'right':e.button===1?'middle':'left'});
  });
  ssCanvas.addEventListener('mousemove',function(e){
    if(!ssControl)return;
    var p=ssCanvasPos(e);
    ssSend({type:'input',kind:'mouse',action:'move',x:p.x,y:p.y});
  });
  ssCanvas.addEventListener('wheel',function(e){
    if(!ssControl)return;e.preventDefault();
    var p=ssCanvasPos(e);
    ssSend({type:'input',kind:'mouse',action:'wheel',x:p.x,y:p.y,dy:e.deltaY<0?3:-3});
  },{passive:false});
  document.addEventListener('keydown',function(e){
    if(!ssOpen)return;
    if(e.key==='Escape'){
      if(ssControl){ssSetControl(false);return}
      closeSS();return;
    }
    if(!ssControl)return;
    e.preventDefault();
    ssSend({type:'input',kind:'key',action:'down',key:e.key,code:e.code});
  });
  document.addEventListener('keyup',function(e){
    if(!ssControl||!ssOpen)return;
    e.preventDefault();
    ssSend({type:'input',kind:'key',action:'up',key:e.key,code:e.code});
  });
  /* paste support so you can paste text into the remote PC */
  document.addEventListener('paste',function(e){
    if(!ssControl||!ssOpen)return;
    var txt=(e.clipboardData||window.clipboardData).getData('text');
    if(txt)ssSend({type:'input',kind:'key',action:'type',text:txt.slice(0,500)});
  });
}

/* --- 30s idle thumbnail (pip) --- */
function ssStartIdle(){
  ssStopIdle();
  ssIdleTick();
  ssIdleTimer=setInterval(ssIdleTick,30000);
}
function ssStopIdle(){if(ssIdleTimer){clearInterval(ssIdleTimer);ssIdleTimer=null}}
function ssIdleTick(){
  if(ssOpen)return;
  if(!userInfo||!userInfo.mc_owner)return;
  fetch('/api/screencast/frame',{credentials:'include',cache:'no-store'}).then(function(r){
    if(!r.ok)throw 0;return r.blob();
  }).then(function(b){
    var img=document.getElementById('ssPipImg'),pip=document.getElementById('ssPip');
    if(!img)return;
    img.src=URL.createObjectURL(b);
    if(pip&&pip.style.display!=='flex')pip.style.display='block';
  }).catch(function(){});
}

/* boot */
ssBindInput();
setTimeout(function(){
  if(userInfo&&userInfo.mc_owner){
    fetch('/api/screencast/status',{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
      if(d.available)ssStartIdle();
    }).catch(function(){});
  }
},1500);
</script>
</body>
</html>'''

MC_PAGE_ETAG = '"' + hashlib.md5(MC_PAGE_HTML.encode("utf-8")).hexdigest() + '"'

def _mc_page_response(request):
    # Instant repeat loads: serve the console with a strong ETag so the browser
    # gets a cheap 304 Not Modified instead of re-downloading the ~80KB page.
    # no-cache still revalidates on every visit, so deploys show up immediately.
    if request.headers.get("If-None-Match") == MC_PAGE_ETAG:
        return web.Response(status=304, headers={"ETag": MC_PAGE_ETAG, "Cache-Control": "no-cache"})
    return web.Response(text=MC_PAGE_HTML, content_type="text/html",
                        headers={"ETag": MC_PAGE_ETAG, "Cache-Control": "no-cache"})

async def handle_mc_page(request):
    # The console moved to its own hostname; keep /mc working via redirect.
    if _request_is_mc_host(request):
        return _mc_page_response(request)
    resp = web.Response(status=302)
    resp.headers["Location"] = MC_SITE_URL
    return resp


# ---------------------------------------------------------------------------
# Server file manager
#
# Every path is relative to the server folder and is resolved through
# realpath() so symlinks cannot escape the sandbox. Config-like files are
# returned as rows (bool/number/string) with `#` comments surfaced as help.
# ---------------------------------------------------------------------------
MC_FILE_EDIT_LIMIT = 2 * 1024 * 1024
MC_FILE_UPLOAD_LIMIT = 1024 * 1024 * 1024

def _mc_clean_rel_path(value):
    text = str(value or "").replace("\\", "/").strip()
    if text.startswith("/"):
        return None
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or "\x00" in part or ":" in part:
            return None
        parts.append(part)
    return "/".join(parts)

def _mc_resolve_server_path(server_dir, rel_path):
    rel_path = _mc_clean_rel_path(rel_path)
    if rel_path is None:
        return None
    root = os.path.realpath(server_dir)
    target = os.path.realpath(os.path.join(root, rel_path)) if rel_path else root
    try:
        if os.path.commonpath([root, target]) != root:
            return None
    except ValueError:
        return None
    return target

def _mc_join_rel_path(parent, name):
    return f"{parent}/{name}" if parent else name

def _mc_detect_value_type(value):
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "number"
    return "string"

def _mc_split_inline_comment(value):
    match = re.search(r"\s#", value)
    if not match:
        return value.rstrip(), ""
    return value[:match.start()].rstrip(), value[match.end():].strip()

def _mc_parse_config_rows(text):
    rows = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            rows.append({"type": "blank"})
        elif stripped.startswith("#"):
            rows.append({"type": "comment", "text": stripped})
        elif "=" in raw_line:
            key, value = raw_line.split("=", 1)
            value, help_text = _mc_split_inline_comment(value.strip())
            rows.append({
                "type": "property",
                "key": key.strip(),
                "value": value,
                "value_type": _mc_detect_value_type(value),
                "help": help_text,
            })
        else:
            rows.append({"type": "raw", "text": raw_line})
    return rows

def _mc_serialize_config_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("Invalid property rows")
    lines = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind == "blank":
            lines.append("")
        elif kind == "comment":
            text = str(row.get("text", "")).strip()
            if text and not text.startswith("#"):
                text = "# " + text
            if "\n" in text or "\r" in text:
                raise ValueError("Comments cannot contain newlines")
            lines.append(text)
        elif kind == "raw":
            text = str(row.get("text", ""))
            if "\n" in text or "\r" in text:
                raise ValueError("Lines cannot contain newlines")
            lines.append(text)
        elif kind == "property":
            key = str(row.get("key", "")).strip()
            value = str(row.get("value", "")).strip()
            help_text = str(row.get("help", "")).strip()
            if not key or "=" in key or "\n" in key or "\r" in key:
                raise ValueError("Property names cannot be empty or contain '='")
            if "\n" in value or "\r" in value:
                raise ValueError("Property values cannot contain newlines")
            line = f"{key}={value}"
            if help_text:
                line += " # " + help_text.replace("\n", " ").replace("\r", " ")
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")

def _mc_config_like(path, rows, text):
    extension = os.path.splitext(path)[1].lower()
    if extension in (".properties", ".conf", ".cfg", ".ini", ".env", ".toml"):
        return True
    if len(text) > 512 * 1024:
        return False
    property_rows = [row for row in rows if row.get("type") == "property"]
    other_rows = [row for row in rows if row.get("type") not in ("property", "comment", "blank")]
    return bool(property_rows) and not other_rows

def _mc_file_entry(abs_path, rel_path):
    try:
        is_link = os.path.islink(abs_path)
        is_dir = os.path.isdir(abs_path) and not is_link
        info = os.lstat(abs_path)
        return {
            "name": os.path.basename(abs_path),
            "path": rel_path,
            "type": "link" if is_link else ("dir" if is_dir else "file"),
            "size": 0 if is_dir or is_link else int(info.st_size),
            "modified": float(info.st_mtime),
        }
    except OSError:
        return None

def _mc_remove_uploads(files):
    for item in files:
        temp_path = item.get("temp") if isinstance(item, dict) else None
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

async def mc_api_files(request):
    name = request.query.get("name", "")
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    rel_path = _mc_clean_rel_path(request.query.get("path", ""))
    if rel_path is None:
        return web.json_response({"error": "Invalid path"}, status=400)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target or not os.path.isdir(target):
        return web.json_response({"error": "Folder not found"}, status=404)
    entries = []
    try:
        with os.scandir(target) as iterator:
            for item in iterator:
                entry = _mc_file_entry(item.path, _mc_join_rel_path(rel_path, item.name))
                if entry:
                    entries.append(entry)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    parent = "/".join(rel_path.split("/")[:-1]) if rel_path else ""
    return web.json_response({"path": rel_path, "parent": parent, "entries": entries})

async def mc_api_file_read(request):
    name = request.query.get("name", "")
    rel_path = _mc_clean_rel_path(request.query.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid path"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target or not os.path.isfile(target):
        return web.json_response({"error": "File not found"}, status=404)
    size = os.path.getsize(target)
    result = {
        "path": rel_path,
        "name": os.path.basename(target),
        "size": size,
        "editable": False,
        "kind": "binary",
    }
    if size > MC_FILE_EDIT_LIMIT:
        result["reason"] = "File is larger than 2 MB. Download it instead."
        return web.json_response(result)
    try:
        with open(target, "rb") as f:
            data = f.read(MC_FILE_EDIT_LIMIT + 1)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    if len(data) > MC_FILE_EDIT_LIMIT:
        result["reason"] = "File is larger than 2 MB. Download it instead."
        return web.json_response(result)
    if b"\x00" in data:
        result["reason"] = "This is a binary file. Download it instead."
        return web.json_response(result)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        result["reason"] = "This file is not valid UTF-8. Download it instead."
        return web.json_response(result)
    rows = _mc_parse_config_rows(text)
    config_like = _mc_config_like(target, rows, text)
    result.update({
        "editable": True,
        "kind": "config" if config_like else "text",
        "content": text,
        "rows": rows if config_like else None,
    })
    return web.json_response(result)

async def mc_api_file_save(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    rel_path = _mc_clean_rel_path(data.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid path"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target:
        return web.json_response({"error": "Invalid path"}, status=400)
    if os.path.isdir(target):
        return web.json_response({"error": "That path is a folder"}, status=400)
    try:
        if "rows" in data:
            text = _mc_serialize_config_rows(data.get("rows"))
        elif "content" in data:
            text = str(data.get("content", ""))
        else:
            return web.json_response({"error": "Nothing to save"}, status=400)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    encoded = text.encode("utf-8")
    if len(encoded) > MC_FILE_EDIT_LIMIT:
        return web.json_response({"error": "File is larger than 2 MB"}, status=413)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        return web.json_response({"error": "Parent folder not found"}, status=404)
    temp_path = os.path.join(parent, f".mc-save-{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(temp_path, target)
    except OSError as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True, "size": len(encoded)})

async def mc_api_create_folder(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    rel_path = _mc_clean_rel_path(data.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid folder name"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target:
        return web.json_response({"error": "Invalid folder name"}, status=400)
    if os.path.exists(target):
        return web.json_response({"error": "Something with that name already exists"}, status=409)
    if not os.path.isdir(os.path.dirname(target)):
        return web.json_response({"error": "Parent folder not found"}, status=404)
    try:
        os.mkdir(target)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True, "entry": _mc_file_entry(target, rel_path)})

async def mc_api_create_file(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    rel_path = _mc_clean_rel_path(data.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid file name"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target:
        return web.json_response({"error": "Invalid file name"}, status=400)
    if os.path.exists(target):
        return web.json_response({"error": "Something with that name already exists"}, status=409)
    if not os.path.isdir(os.path.dirname(target)):
        return web.json_response({"error": "Parent folder not found"}, status=404)
    content = str(data.get("content", ""))
    if len(content.encode("utf-8")) > MC_FILE_EDIT_LIMIT:
        return web.json_response({"error": "File is larger than 2 MB"}, status=413)
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True, "entry": _mc_file_entry(target, rel_path)})

async def mc_api_rename_file(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    rel_path = _mc_clean_rel_path(data.get("path", ""))
    new_name = str(data.get("new_name", "")).strip()
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path or not new_name:
        return web.json_response({"error": "Invalid path"}, status=400)
    if "/" in new_name or "\\" in new_name or new_name in (".", "..") or "\x00" in new_name or ":" in new_name:
        return web.json_response({"error": "Invalid new name"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target or not os.path.exists(target):
        return web.json_response({"error": "File or folder not found"}, status=404)
    parent_rel = "/".join(rel_path.split("/")[:-1])
    new_rel = _mc_join_rel_path(parent_rel, new_name)
    new_target = _mc_resolve_server_path(server_dir, new_rel)
    if not new_target:
        return web.json_response({"error": "Invalid new name"}, status=400)
    if os.path.exists(new_target):
        return web.json_response({"error": "Something with that name already exists"}, status=409)
    try:
        os.rename(target, new_target)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True, "entry": _mc_file_entry(new_target, new_rel)})

async def mc_api_delete_file(request):
    data = await request.json()
    name = str(data.get("name", "")).strip()
    rel_path = _mc_clean_rel_path(data.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid path"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target or not os.path.exists(target):
        return web.json_response({"error": "File or folder not found"}, status=404)
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"ok": True})

async def mc_api_upload_files(request):
    reader = await request.multipart()
    server_name = None
    server_dir = None
    rel_path = None
    path_seen = False
    target_dir = None
    files = []

    def fail(status, message):
        _mc_remove_uploads(files)
        return web.json_response({"error": message}, status=status)

    def resolve_target():
        nonlocal target_dir
        if target_dir is None and server_dir is not None and path_seen:
            target_dir = _mc_resolve_server_path(server_dir, rel_path)
        return target_dir

    try:
        while True:
            part = await reader.next()
            if not part:
                break
            if part.name == "server":
                server_name = (await part.read()).decode(errors="replace").strip()
                # Authorize before any file bytes are written to disk.
                if not _mc_can(request, server_name, "files"):
                    return fail(401, "unauthorized")
                server_dir = _mc_safe_server_dir(server_name)
                if not server_dir:
                    return fail(404, "Server not found")
            elif part.name == "path":
                path_seen = True
                rel_path = _mc_clean_rel_path((await part.read()).decode(errors="replace").strip() or "")
                if rel_path is None:
                    return fail(400, "Invalid path")
                target_dir = None
            elif part.name == "file":
                if not server_dir or not path_seen:
                    raise ValueError("Send the server and path before the files")
                if not resolve_target() or not os.path.isdir(target_dir):
                    return fail(404, "Folder not found")
                filename = os.path.basename(part.filename or "").strip()
                if not filename or filename in (".", "..") or ":" in filename or "\x00" in filename:
                    return fail(400, "Invalid file name")
                if len(files) >= 50:
                    raise ValueError("Upload at most 50 files at a time")
                temp_path = os.path.join(MC_DIR, f".file-upload-{uuid.uuid4().hex}.tmp")
                total = 0
                try:
                    with open(temp_path, "wb") as temp_file:
                        while True:
                            chunk = await part.read_chunk(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > MC_FILE_UPLOAD_LIMIT:
                                raise ValueError("Each file must be smaller than 1 GB")
                            temp_file.write(chunk)
                except Exception:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise
                files.append({"name": filename, "temp": temp_path, "size": total})
    except ValueError as e:
        return fail(400, str(e))
    except Exception as e:
        return fail(400, str(e))

    if not server_dir:
        return fail(400, "Missing server")
    if not path_seen:
        return fail(400, "Missing path")
    if not resolve_target() or not os.path.isdir(target_dir):
        return fail(404, "Folder not found")
    written = []
    for item in files:
        destination = os.path.join(target_dir, item["name"])
        if not _mc_resolve_server_path(server_dir, _mc_join_rel_path(rel_path, item["name"])):
            return fail(400, f"Invalid file name: {item['name']}")
        try:
            os.replace(item["temp"], destination)
        except OSError as e:
            return fail(500, str(e))
        written.append({
            "name": item["name"],
            "path": _mc_join_rel_path(rel_path, item["name"]),
            "size": item["size"],
        })
    return web.json_response({"ok": True, "files": written})

async def mc_api_file_download(request):
    name = request.query.get("name", "")
    rel_path = _mc_clean_rel_path(request.query.get("path", ""))
    if not _mc_can(request, name, "files"):
        return web.json_response({"error": "unauthorized"}, status=401)
    if rel_path is None or not rel_path:
        return web.json_response({"error": "Invalid path"}, status=400)
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    target = _mc_resolve_server_path(server_dir, rel_path)
    if not target or not os.path.isfile(target):
        return web.json_response({"error": "File not found"}, status=404)
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(target)) or "file"
    return web.FileResponse(target, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

# ==========================================
#      SCREENCAST (LIVE PC VIEW)
# ==========================================
# Streams this PC's primary monitor to the web console.
#  - While someone is watching (viewing=True) it captures at full resolution and
#    up to SCREENCAST_MAX_FPS.
#  - When nobody is watching it takes a single screenshot every 30 seconds so the
#    preview thumbnail stays fresh without wasting CPU.
# Bot-owner only (BOT_OWNER_ID); global MC admins cannot view or control the PC.

SCREENCAST_IDLE_INTERVAL = 30.0
SCREENCAST_MAX_FPS = 100
SCREENCAST_QUALITIES = {
    "full": {"max_width": 0, "quality": 72},
    "720p": {"max_width": 1280, "quality": 68},
    "480p": {"max_width": 854, "quality": 62},
}
_SCREENCAST_QUALITY_RANK = {"full": 3, "720p": 2, "480p": 1}

screencast_viewers = set()      # connected websockets
screencast_meta = {}            # ws -> {"viewing": bool, "quality": str, "fps": int}
screencast_latest = {"jpeg": None, "width": 0, "height": 0, "screen_w": 0, "screen_h": 0, "ts": 0}
screencast_task = None
screencast_available = None
screencast_control_ok = None     # pyautogui importable (remote control usable)
SCREENCAST_QUEUE_MAX = 60        # per-viewer frame backlog (~0.6s at 100fps)
screencast_queues = {}           # ws -> {"q": asyncio.Queue, "task": asyncio.Task}

def _screencast_check_available() -> bool:
    global screencast_available
    if screencast_available is None:
        try:
            import mss  # noqa: F401
            from PIL import Image  # noqa: F401
            screencast_available = True
        except Exception as e:
            print(f"[SCREENCAST] Disabled (missing dependency): {e}. Install with: pip install pillow mss")
            screencast_available = False
    return screencast_available

def _screencast_check_control() -> bool:
    """Remote control needs pyautogui; report it so the UI can explain instead of silently doing nothing."""
    global screencast_control_ok
    if screencast_control_ok is None:
        try:
            import pyautogui  # noqa: F401
            screencast_control_ok = True
        except Exception as e:
            print(f"[SCREENCAST] Remote control unavailable (missing dependency): {e}. Install with: pip install pyautogui")
            screencast_control_ok = False
    return screencast_control_ok

_KEY_MAP = {
    "control": "ctrl", "ctrl": "ctrl", "alt": "alt", "shift": "shift",
    "meta": "win", "win": "win", "osleft": "win", "osright": "win",
    "escape": "esc", "esc": "esc", " ": "space", "spacebar": "space", "space": "space",
    "enter": "enter", "return": "enter", "tab": "tab", "backspace": "backspace",
    "delete": "delete", "del": "delete", "insert": "insert", "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown", "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right", "up": "up", "down": "down",
    "left": "left", "right": "right", "capslock": "capslock",
}

_MOD_KEYS = ("shift", "ctrl", "alt", "win")

# Layout-independent KeyboardEvent.code -> pyautogui key, used as a fallback
# when e.key is something the backend does not understand.
_CODE_KEYS = {
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Enter": "enter", "NumpadEnter": "enter", "Space": "space", "Escape": "esc",
    "Tab": "tab", "Backspace": "backspace", "Delete": "delete", "Insert": "insert",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "CapsLock": "capslock", "ShiftLeft": "shift", "ShiftRight": "shift",
    "ControlLeft": "ctrl", "ControlRight": "ctrl", "AltLeft": "alt", "AltRight": "alt",
    "MetaLeft": "win", "MetaRight": "win",
    "Semicolon": ";", "Quote": "'", "Comma": ",", "Period": ".", "Slash": "/",
    "Backslash": "\\", "Minus": "-", "Equal": "=", "Backquote": "`",
    "BracketLeft": "[", "BracketRight": "]",
    "NumpadAdd": "+", "NumpadSubtract": "-", "NumpadMultiply": "*",
    "NumpadDivide": "/", "NumpadDecimal": ".", "NumpadEqual": "=",
}

def _code_candidates(code: str):
    out = []
    if code.startswith("Key") and len(code) == 4:
        out.append(code[3].lower())
    elif code.startswith("Digit") and len(code) == 6:
        out.append(code[5])
    elif code.startswith("Numpad") and len(code) == 7 and code[6].isdigit():
        out.append("num" + code[6])
    elif len(code) > 1 and code[0] == "F" and code[1:].isdigit():
        out.append(code.lower())
    if code in _CODE_KEYS:
        out.append(_CODE_KEYS[code])
    return out

def _map_browser_key(pyautogui, key: str, code: str = ""):
    """Browser KeyboardEvent -> a key pyautogui's backend will actually send.

    e.key is layout/shift dependent ('Shift', 'Enter', 'A', 'ArrowUp') while the
    Windows backend only knows its own lowercase names and *silently drops*
    everything else — so validate before use:
      1. translate via _KEY_MAP (case-insensitive)
      2. single characters: lowercase letters (the separately-forwarded Shift
         key event provides shift), symbols passed through as-is
      3. lowercase the rest ('F5' -> 'f5')
      4. fall back to the layout-independent e.code
      5. None = unsupported key; callers skip it instead of erroring.
    """
    cands = []
    if key:
        low = key.lower()
        mapped = _KEY_MAP.get(low)
        if mapped:
            cands.append(mapped)
        if len(key) == 1:
            cands.append(key.lower() if key.isalpha() else key)
        cands.append(low)
    cands.extend(_code_candidates(code))
    valid = getattr(pyautogui, "KEYBOARD_KEYS", ())
    for c in cands:
        if c in valid:
            return c
        if len(c) == 1 and c.isprintable() and ord(c) < 128:
            return c  # VkKeyScan covers printable ASCII on Windows
    return None

def _assert_mods(pyautogui, meta):
    """pyautogui's own modifier handling (pressing '!' presses AND releases
    SHIFT) can release a modifier the viewer's browser still reports as held.
    Re-assert the modifiers the viewer's keyboard actually has so Shift/Ctrl/
    Alt keep working after symbols and uppercase letters."""
    if not meta:
        return
    for m in meta.get("mods"):
        try:
            pyautogui.keyDown(m)
        except Exception:
            pass

def _write_text(pyautogui, text: str):
    """Type pasted text; pyautogui.write() silently drops newlines, so press
    Enter between lines instead."""
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, part in enumerate(parts):
        if part:
            pyautogui.write(part, interval=0)
        if i < len(parts) - 1:
            pyautogui.press("enter")

def _screencast_capture_sync(quality: str):
    """Grab the primary monitor and JPEG-encode it. Runs in a worker thread."""
    try:
        import mss
        from PIL import Image
    except Exception:
        return None, 0, 0, 0, 0
    cfg = SCREENCAST_QUALITIES.get(quality, SCREENCAST_QUALITIES["full"])
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            monitor = monitors[1] if len(monitors) > 1 else monitors[0]
            screen_w, screen_h = monitor["width"], monitor["height"]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as e:
        print(f"[SCREENCAST] Capture error: {e}")
        return None, 0, 0, 0, 0
    if cfg["max_width"] and img.width > cfg["max_width"]:
        ratio = cfg["max_width"] / img.width
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), Image.BILINEAR)
    buf = io.BytesIO()
    try:
        img.save(buf, format="JPEG", quality=cfg["quality"], optimize=False, progressive=True)
    except Exception as e:
        print(f"[SCREENCAST] Encode error: {e}")
        return None, 0, 0, 0, 0
    return buf.getvalue(), img.width, img.height, screen_w, screen_h

async def _screencast_broadcast_viewers():
    if not screencast_viewers:
        return
    payload = {"type": "viewers", "count": len(screencast_viewers)}
    for ws in list(screencast_viewers):
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=1.0)
        except Exception:
            pass

def _enqueue_frame(ws, jpeg):
    """Queue a frame for this viewer instead of dropping it while the previous
    frame is still in flight. Each viewer has one drain task that sends FIFO, so
    frames are never skipped out of order. Only when a viewer falls more than
    SCREENCAST_QUEUE_MAX frames behind is the OLDEST frame evicted — latency
    stays bounded and the stream never jumps ahead over pending frames."""
    if getattr(ws, "closed", True):
        return
    entry = screencast_queues.get(ws)
    if entry is None:
        entry = {"q": asyncio.Queue(maxsize=SCREENCAST_QUEUE_MAX), "task": None}
        screencast_queues[ws] = entry
        entry["task"] = asyncio.ensure_future(_drain_frames(ws, entry))
    q = entry["q"]
    try:
        q.put_nowait(jpeg)
    except asyncio.QueueFull:
        # Viewer too slow: evict the oldest frame, keep the freshest.
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(jpeg)
        except asyncio.QueueFull:
            pass

async def _drain_frames(ws, entry):
    """Single sender per viewer: pops queued frames in order and writes them.
    aiohttp serializes WS writes internally, so JSON messages can interleave
    safely between frames."""
    try:
        while True:
            jpeg = await entry["q"].get()
            await ws.send_bytes(jpeg)
    except asyncio.CancelledError:
        raise  # cancellation must propagate so the task reports cancelled
    except Exception:
        pass  # socket died — the ws handler's finally cleans up
    finally:
        if screencast_queues.get(ws) is entry:
            screencast_queues.pop(ws, None)

def _drop_frame_queue(ws):
    """Remove a viewer's frame queue and cancel its drain task."""
    entry = screencast_queues.pop(ws, None)
    if entry and entry.get("task"):
        entry["task"].cancel()

async def _screencast_loop():
    """Idle: one screenshot every 30s. Watching: full-res stream up to max FPS."""
    # Windows quantizes asyncio.sleep to the 15.6ms system timer, capping the
    # stream at ~64fps. Raise resolution to 1ms — but only while someone watches,
    # so the idle server keeps normal timer behavior.
    timer_tick = False

    def tick(on: bool):
        nonlocal timer_tick
        if os.name != "nt" or on == timer_tick:
            return
        try:
            if on:
                ctypes.windll.winmm.timeBeginPeriod(1)
            else:
                ctypes.windll.winmm.timeEndPeriod(1)
            timer_tick = on
        except Exception:
            pass

    try:
        while True:
            try:
                active = [ws for ws in screencast_viewers if screencast_meta.get(ws, {}).get("viewing")]
                if not screencast_viewers or not active:
                    tick(False)
                    await asyncio.sleep(SCREENCAST_IDLE_INTERVAL)
                    jpeg, w, h, sw, sh = await asyncio.to_thread(_screencast_capture_sync, "720p")
                    if jpeg:
                        screencast_latest.update({"jpeg": jpeg, "width": w, "height": h,
                                                  "screen_w": sw, "screen_h": sh, "ts": time.time()})
                    continue
                tick(True)

                # Use the highest quality / fps any active viewer asked for.
                quality = max((screencast_meta.get(ws, {}).get("quality", "720p") for ws in active),
                              key=lambda q: _SCREENCAST_QUALITY_RANK.get(q, 0))
                fps = min(SCREENCAST_MAX_FPS, max(int(screencast_meta.get(ws, {}).get("fps", 15) or 15) for ws in active))

                # Deadline-based pacing: capture+encode time counts *against* the frame
                # budget, so the requested FPS is the real achieved FPS (not slower).
                frame_start = time.monotonic()
                jpeg, w, h, sw, sh = await asyncio.to_thread(_screencast_capture_sync, quality)
                if jpeg:
                    screencast_latest.update({"jpeg": jpeg, "width": w, "height": h,
                                              "screen_w": sw, "screen_h": sh, "ts": time.time()})
                    for ws in list(active):
                        _enqueue_frame(ws, jpeg)
                remaining = (1.0 / fps) - (time.monotonic() - frame_start)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"[SCREENCAST] Loop error: {e}")
                await asyncio.sleep(1)
    finally:
        tick(False)

async def _screencast_input(msg: dict, meta: dict = None):
    """Apply a remote-control event (mouse / keyboard) on this PC.

    Returns None on success or a human-readable error string for the client."""
    if not _screencast_check_control():
        return "Remote control unavailable on this PC (pyautogui not installed)"
    try:
        import pyautogui
    except Exception as e:
        return f"Remote control unavailable: {e}"
    try:
        # pyautogui sleeps 0.1s after EVERY call by default — that throttles input
        # to ~10 events/s and stalls the event loop. Disable it for real-time control.
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
        kind = msg.get("kind")
        if kind == "mouse":
            # Map normalized 0..1 coords into pyautogui's own coordinate space.
            # mss reports physical pixels while pyautogui may use DPI-scaled
            # logical pixels — using its size() keeps clicks landing correctly
            # at 125%/150% display scaling.
            try:
                pw, ph = pyautogui.size()
            except Exception:
                pw, ph = screencast_latest.get("screen_w") or 0, screencast_latest.get("screen_h") or 0
            if not pw or not ph:
                return "Screen size unknown yet"
            x = max(0, min(pw - 1, float(msg.get("x", 0)) * pw))
            y = max(0, min(ph - 1, float(msg.get("y", 0)) * ph))
            button = {"left": "left", "right": "right", "middle": "middle"}.get(str(msg.get("button", "left")), "left")
            action = msg.get("action")
            if action == "move":
                pyautogui.moveTo(x, y)
            elif action == "down":
                pyautogui.mouseDown(x, y, button=button)
            elif action == "up":
                pyautogui.mouseUp(x, y, button=button)
            elif action == "click":
                pyautogui.click(x, y, button=button)
            elif action == "wheel":
                pyautogui.scroll(int(max(-20, min(20, msg.get("dy", 0)))), x, y)
        elif kind == "key":
            # Keys never surface as control_error: one unsupported key must not
            # shut control off — skip it and keep the input stream alive.
            try:
                action = msg.get("action")
                key = str(msg.get("key", ""))
                code = str(msg.get("code", ""))
                if action == "type":
                    _write_text(pyautogui, str(msg.get("text", ""))[:500])
                    _assert_mods(pyautogui, meta)
                    return None
                mapped = _map_browser_key(pyautogui, key, code)
                if mapped is None:
                    return None
                mods = meta.setdefault("mods", set()) if meta is not None else set()
                is_mod = mapped in _MOD_KEYS
                if action == "down":
                    if is_mod:
                        mods.add(mapped)
                    pyautogui.keyDown(mapped)
                elif action == "up":
                    if is_mod:
                        mods.discard(mapped)
                    pyautogui.keyUp(mapped)
                _assert_mods(pyautogui, meta)
            except Exception as e:
                print(f"[SCREENCAST] Key input error: {e}")
            return None
        return None
    except Exception as e:
        print(f"[SCREENCAST] Input error: {e}")
        return f"Input error: {e}"

async def _screencast_on_message(ws, msg: dict):
    kind = msg.get("type")
    meta = screencast_meta.setdefault(ws, {"viewing": False, "quality": "720p", "fps": 15, "owner": False})
    if kind == "view":
        was_viewing = meta.get("viewing")
        meta["viewing"] = bool(msg.get("active"))
        if meta["viewing"] and not was_viewing:
            # Fresh full-res frame immediately instead of waiting a tick.
            quality = meta.get("quality", "720p")
            jpeg, w, h, sw, sh = await asyncio.to_thread(_screencast_capture_sync, quality)
            if jpeg:
                screencast_latest.update({"jpeg": jpeg, "width": w, "height": h,
                                          "screen_w": sw, "screen_h": sh, "ts": time.time()})
                _enqueue_frame(ws, jpeg)
    elif kind == "quality":
        q = str(msg.get("quality", "720p"))
        meta["quality"] = q if q in SCREENCAST_QUALITIES else "720p"
    elif kind == "fps":
        try:
            meta["fps"] = max(1, min(SCREENCAST_MAX_FPS, int(msg.get("fps", 15))))
        except Exception:
            pass
    elif kind == "input":
        # Remote control is owner-only, enforced server-side (client just hides the button).
        if not meta.get("owner"):
            return
        err = await _screencast_input(msg, meta)
        if err:
            try:
                await ws.send_json({"type": "control_error", "error": err})
            except Exception:
                pass
    elif kind == "ping":
        try:
            await ws.send_json({"type": "pong", "t": msg.get("t")})
        except Exception:
            pass

async def screencast_status(request):
    if not _is_mc_owner(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({
        "available": _screencast_check_available(),
        "control": _screencast_check_control(),
        "screen": {"width": screencast_latest.get("screen_w", 0), "height": screencast_latest.get("screen_h", 0)},
        "viewers": len(screencast_viewers),
        "max_fps": SCREENCAST_MAX_FPS,
    })

async def screencast_frame(request):
    """Latest screenshot (30s cadence when idle, live frame when someone watches)."""
    if not _is_mc_owner(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not _screencast_check_available():
        return web.json_response({"error": "Screencast unavailable (install pillow + mss)"}, status=503)
    if not screencast_latest.get("jpeg") or time.time() - screencast_latest.get("ts", 0) > SCREENCAST_IDLE_INTERVAL:
        jpeg, w, h, sw, sh = await asyncio.to_thread(_screencast_capture_sync, "720p")
        if jpeg:
            screencast_latest.update({"jpeg": jpeg, "width": w, "height": h,
                                      "screen_w": sw, "screen_h": sh, "ts": time.time()})
    jpeg = screencast_latest.get("jpeg")
    if not jpeg:
        return web.json_response({"error": "No frame captured yet"}, status=503)
    return web.Response(body=jpeg, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Screen-Width": str(screencast_latest.get("screen_w", 0)),
                                 "X-Screen-Height": str(screencast_latest.get("screen_h", 0))})

async def screencast_ws(request):
    if not _is_mc_owner(request):
        return web.Response(status=403)
    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)
    screencast_viewers.add(ws)
    screencast_meta[ws] = {"viewing": False, "quality": "720p", "fps": 15, "owner": _is_mc_owner(request)}
    try:
        await ws.send_json({
            "type": "hello",
            "available": _screencast_check_available(),
            "control": _screencast_check_control(),
            "owner": _is_mc_owner(request),
            "screen": {"width": screencast_latest.get("screen_w", 0), "height": screencast_latest.get("screen_h", 0)},
            "max_fps": SCREENCAST_MAX_FPS,
        })
        await _screencast_broadcast_viewers()
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                try:
                    await _screencast_on_message(ws, data)
                except Exception as e:
                    print(f"[SCREENCAST] WS message error: {e}")
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
    finally:
        screencast_viewers.discard(ws)
        screencast_meta.pop(ws, None)
        _drop_frame_queue(ws)
        await _screencast_broadcast_viewers()
    return ws

async def handle_fallback(request):
    path = request.path or "/"
    if path.startswith("/api/") or path.startswith("/ws/"):
        return web.json_response({"error": "not found"}, status=404)
    if _request_is_dm_host(request):
        return await dm_web.index_page(request)
    return web.Response(text="Not found", status=404)


async def start_local_server(host="127.0.0.1", port=5000):
    # 64 MB lets a single upload chunk land without tripping aiohttp's default
    # 1 MB request cap (dm.* sends 16 MB chunks).
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/decompile", handle_post)
    app.router.add_get("/games.json", handle_games_json)
    app.router.add_get("/api/auth/login", handle_discord_auth)
    app.router.add_get("/api/auth/verify", handle_discord_verify)
    app.router.add_get("/api/auth/me", handle_auth_me)
    app.router.add_get("/api/auth/callback", handle_discord_callback)
    app.router.add_get("/api/admin", handle_admin_data)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_post("/api/ban", handle_ban)
    app.router.add_get("/", handle_index)
    app.router.add_get("/mc", handle_mc_page)
    app.router.add_get("/api/mc/versions", mc_api_versions)
    app.router.add_post("/api/mc/version", mc_api_change_version)
    app.router.add_post("/api/mc/delete", mc_api_delete_server)
    app.router.add_post("/api/mc/rename", mc_api_rename)
    app.router.add_post("/api/mc/backup", mc_api_backup)
    app.router.add_get("/api/mc/backups", mc_api_backups)
    app.router.add_get("/api/mc/backup-download", mc_api_backup_download)
    app.router.add_get("/api/mc/servers", mc_api_servers)
    app.router.add_get("/api/mc/permissions", mc_api_permissions)
    app.router.add_post("/api/mc/permissions", mc_api_add_permission)
    app.router.add_delete("/api/mc/permissions", mc_api_remove_permission)
    app.router.add_get("/api/mc/players", mc_api_players)
    app.router.add_get("/api/mc/player-skin", mc_api_player_skin)
    app.router.add_get("/api/mc/player", mc_api_player)
    app.router.add_post("/api/mc/player-action", mc_api_player_action)
    app.router.add_post("/api/mc/unban", mc_api_unban)
    app.router.add_post("/api/mc/inventory-move", mc_api_inventory_move)
    app.router.add_post("/api/mc/create", mc_api_create)
    app.router.add_post("/api/mc/start", mc_api_start)
    app.router.add_post("/api/mc/stop", mc_api_stop)
    app.router.add_post("/api/mc/command", mc_api_command)
    app.router.add_get("/api/mc/console", mc_api_console)
    app.router.add_get("/api/mc/mods", mc_api_mods)
    app.router.add_get("/api/mc/icon", mc_api_icon)
    app.router.add_post("/api/mc/icon", mc_api_upload_icon)
    app.router.add_post("/api/mc/upload-mod", mc_api_upload_mod)
    app.router.add_post("/api/mc/delete-mod", mc_api_delete_mod)
    app.router.add_get("/ws/mc/{name}", mc_ws_console)
    app.router.add_get("/api/screencast/status", screencast_status)
    app.router.add_get("/api/screencast/frame", screencast_frame)
    app.router.add_get("/ws/screencast", screencast_ws)
    app.router.add_post("/api/mc/permissions/owner", mc_api_set_server_owner)
    app.router.add_get("/api/mc/global-access", mc_api_global_access)
    app.router.add_post("/api/mc/global-access", mc_api_set_global_access)
    app.router.add_get("/api/mc/files", mc_api_files)
    app.router.add_get("/api/mc/file", mc_api_file_read)
    app.router.add_post("/api/mc/file", mc_api_file_save)
    app.router.add_get("/api/mc/file-download", mc_api_file_download)
    app.router.add_post("/api/mc/files/folder", mc_api_create_folder)
    app.router.add_post("/api/mc/files/create", mc_api_create_file)
    app.router.add_post("/api/mc/files/rename", mc_api_rename_file)
    app.router.add_post("/api/mc/files/delete", mc_api_delete_file)
    app.router.add_post("/api/mc/files/upload", mc_api_upload_files)
    # Missing route: the archived/local pages still request /api/thumb for game icons.
    app.router.add_get("/api/thumb", handle_thumbnail)
    # Asset pack pages + downloads (must come before the catch-all below).
    from .assets import register_routes as _register_asset_routes
    _register_asset_routes(app)
    # dm.luaisgame.com: shared accounts + chat platform routes.
    dm_auth.init(BASE_DIR, BOT_OWNER_ID, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, {
        "storage.luaisgame.com": DISCORD_REDIRECT_URI,
        "mc.luaisgame.com": DISCORD_REDIRECT_URI_MC,
        "dm.luaisgame.com": DISCORD_REDIRECT_URI_DM,
    })
    dm_api.set_paths(BASE_DIR)
    dm_api.register(app)
    dm_web.register(app)
    app.router.add_get("/{filename}", handle_download)
    # Deep links (/settings/profile, /servers/1/...) belong to the dm SPA; every
    # other host keeps a plain 404.
    app.router.add_get("/{rest:.*}", handle_fallback)
    runner = web.AppRunner(app)
    await runner.setup()
    for p in range(port, port + 10):
        try:
            site = web.TCPSite(runner, host, p)
            await site.start()
            print(f"[DEBUG] Local HTTP server listening on http://{host}:{p}/decompile")
            # Kick off the screencast engine: 30s idle thumbnails + live stream
            # while someone is viewing. Without this task no frames are pushed.
            global screencast_task
            if screencast_task is None or screencast_task.done():
                screencast_task = asyncio.create_task(_screencast_loop())
            return p
        except OSError:
            continue
    print(f"[DEBUG] Failed to bind to any port in range {port}-{port+9}")
    return None

def find_roblox():
    if not LOCAL_APP_DATA:
        return None
    r_dir = os.path.join(LOCAL_APP_DATA, "Roblox", "Versions")
    if not os.path.exists(r_dir):
        return None
    installs = []
    for root, _, files in os.walk(r_dir):
        if "RobloxPlayerBeta.exe" in files:
            installs.append(os.path.join(root, "RobloxPlayerBeta.exe"))
    if not installs:
        return None
    installs.sort(key=os.path.getmtime, reverse=True)
    return installs[0]

def _terminate_live_roblox():
    targets = {"robloxplayerbeta.exe", "robloxplayerlauncher.exe", "roblox.exe"}
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() in targets:
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                p.kill()
                print(f"[DEBUG] Terminated live Roblox process: {p.info.get('name')}")
        except Exception:
            continue

def _upload_file_sync(file_path: str, place_id: str, game_name: str = None, user_id: str = None, display_name: str = None, game_version: str = None, icon_url: str = None) -> str | None:
    safe_name = "".join(c for c in (game_name or place_id) if c.isalnum() or c in " _-").strip().replace(" ", "_")
    rand_suffix = uuid.uuid4().hex[:8]
    filename = f"{safe_name}_{place_id}_{user_id or 'anon'}_{rand_suffix}{os.path.splitext(file_path)[1]}"
    dest = os.path.join(storage_dir, filename)
    try:
        shutil.copy2(file_path, dest)
        entries = _read_games_json()
        entries.insert(0, {
            "game_name": game_name or f"Place {place_id}",
            "place_id": place_id,
            "filename": filename,
            "user_id": user_id or "Unknown",
            "display_name": display_name or "Unknown",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "game_version": game_version or "N/A",
            "icon_url": icon_url or ""
        })
        _write_games_json(entries)
        return f"https://storage.luaisgame.com/{filename}"
    except Exception as e:
        print(f"[DEBUG] Storage copy error: {e}")
        return None

async def upload_file(file_path: str, place_id: str, game_name: str = None, user_id: str = None, display_name: str = None, game_version: str = None, icon_url: str = None) -> dict | None:
    print(f"[DEBUG] Copying {os.path.basename(file_path)} to storage...")
    public_url = await asyncio.to_thread(_upload_file_sync, file_path, place_id, game_name, user_id, display_name, game_version, icon_url)
    if public_url:
        return {"url": public_url}
    return None

async def send_msg(send_func, content=None, embed=None, ephemeral=False, view=discord.utils.MISSING):
    kwargs = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not discord.utils.MISSING:
        kwargs["view"] = view
    return await send_func(**kwargs)

STATUS_STAGES = {
    "launching":           (0xE74C3C, "Launching Roblox"),
    "joining":             (0xE67E22, "Joining the Game"),
    "decompiling_assets":  (0xF1C40F, "Decompiling Assets"),
    "decompiling_scripts": (0x2ECC71, "Decompiling Scripts"),
    "uploading":           (0x1E90FF, "Uploading File"),
    "error":               (0xE74C3C, "Failed"),
    "paused":              (0x9B59B6, "Paused (Priority)"),
    "kill_switch":         (0xFF0000, "Kill Switch Enabled"),
}

async def update_status(info_msg, embed, stage):
    color, label = STATUS_STAGES.get(stage, (0x3498DB, stage))
    embed.color = color
    field_index = None
    for i, f in enumerate(embed.fields):
        if f.name == "Status":
            field_index = i
            break
    if field_index is not None:
        embed.set_field_at(field_index, name="Status", value=label, inline=False)
    else:
        embed.add_field(name="Status", value=label, inline=False)
    try:
        if info_msg is not None:
            await info_msg.edit(embed=embed)
    except Exception as e:
        print(f"[DEBUG] Failed to edit status embed: {e}")
    if _on_status_update:
        try:
            await _on_status_update(embed)
        except Exception as e:
            print(f"[DEBUG] Failed to call on_status_update: {e}")

async def _wait_with_pause(event, total, job_data, process=None):
    step = 0.5
    waited = 0.0
    while waited < total:
        if job_data and job_data.get("aborted"):
            return False
        if process and process.poll() is not None:
            print("[DEBUG] Roblox process closed unexpectedly.")
            return False
        if event.is_set():
            return True
        await asyncio.sleep(step)
        waited += step
    return event.is_set()


def _cleanup_decompile_artifacts():
    for filename in ("processed.rbxl", "processed.rbxlx", "game.rbxl", "game.rbxlx"):
        path = os.path.join(storage_dir, filename)
        try:
            if os.path.isfile(path):
                os.remove(path)
                print(f"[DEBUG] Removed temporary decompile artifact: {filename}")
        except OSError as e:
            print(f"[DEBUG] Could not remove temporary artifact {path}: {e}")

    try:
        if os.path.isdir(WORKSPACE_DIR):
            for filename in os.listdir(WORKSPACE_DIR):
                lower_name = filename.lower()
                if not lower_name.startswith(("game", "place")) or not lower_name.endswith((".rbxl", ".rbxlx")):
                    continue
                path = os.path.join(WORKSPACE_DIR, filename)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                        print(f"[DEBUG] Removed leftover workspace game file: {filename}")
                    except OSError as e:
                        print(f"[DEBUG] Could not remove workspace game file {path}: {e}")
    except OSError as e:
        print(f"[DEBUG] Could not inspect Roblox workspace for leftover game files: {e}")


async def process_file(send_func, process, game_name, timeout=60, ephemeral=False, info_msg=None, embed=None, rec_ev=None, fin_ev=None, job_data=None, raw=False):
    decompile_dir = os.path.join(BASE_DIR, "storage")
    os.makedirs(decompile_dir, exist_ok=True)

    _cleanup_storage()

    if rec_ev is None:
        rec_ev = decompile_recieved
    if fin_ev is None:
        fin_ev = decompile_event

    rec_ev.clear()
    fin_ev.clear()

    print("[DEBUG] Waiting for local POST request on http://127.0.0.1:5000/decompile...")

    try:
        if not await _wait_with_pause(rec_ev, 45, job_data, process=process):
            print("[DEBUG] Timed out or failed waiting for join event.")
            process.kill()
            return None, None
        if job_data.get("aborted"):
            print("[DEBUG] Job aborted while waiting for join event.")
            return None, None
        if not job_data.get("aborted") and info_msg is not None and embed is not None:
            await update_status(info_msg, embed, "decompiling_assets")
        if job_data is not None:
            job_data["joined"] = True
        print("[DEBUG] Received game join signal.")
        await update_bot_presence(game_name, text="Decompiling Assets...")
    except Exception as e:
        print(f"[DEBUG] Timed out or failed waiting for join event: {e}")
        process.kill()
        return None, None

    try:
        if not await _wait_with_pause(fin_ev, timeout, job_data, process=process):
            print("[DEBUG] Timed out waiting for decompile event.")
            process.kill()
            return None, None
        if job_data.get("aborted"):
            print("[DEBUG] Job aborted while waiting for decompile event.")
            return None, None
        print("[DEBUG] Received game decompile finished signal.")
        process.kill()
        if not job_data.get("aborted") and info_msg is not None and embed is not None:
            await update_status(info_msg, embed, "decompiling_scripts")
    except Exception as e:
        print(f"[DEBUG] Error waiting for decompile finish: {e}")
        process.kill()
        return None, None

    await update_bot_presence(game_name, text="Decompiling Scripts...")

    target_file = None
    target_name = None

    if os.path.exists(WORKSPACE_DIR):
        for f in os.listdir(WORKSPACE_DIR):
            if f.lower().startswith("game") or f.lower().startswith("place"):
                src = os.path.join(WORKSPACE_DIR, f)
                if os.path.isfile(src) and not f.endswith(".lock"):
                    dest = os.path.join(decompile_dir, f)
                    shutil.move(src, dest)
                    target_file, target_name = dest, f
                    print(f"[DEBUG] Moved target file: {f}")
                    break

    if not target_file:
        print("[DEBUG] Target file not found in workspace directory.")
        return None, None

    await asyncio.sleep(0.5)

    is_rbxl = target_name.lower().endswith(".rbxl")
    file_format = "rbxl" if is_rbxl else "rbxlx"
    out_ext = ".rbxl" if is_rbxl else ".rbxlx"
    out_path = os.path.join(decompile_dir, f"processed{out_ext}")

    game_std = os.path.join(decompile_dir, f"game{out_ext}")
    if os.path.abspath(target_file) != os.path.abspath(game_std):
        shutil.move(target_file, game_std)
        target_file = game_std
        target_name = f"game{out_ext}"
        print(f"[DEBUG] Renamed to: {target_name}")

    if SKIP_PROCESSFILE or raw:
        print(f"[DEBUG] {'SKIP_PROCESSFILE is enabled' if SKIP_PROCESSFILE else 'Raw mode enabled'}; skipping oracle-postprocess post-processor.")
    else:
        proc_script = os.path.join(BASE_DIR, "decompile", "oracle-postprocess.exe")
        if not os.path.exists(proc_script):
            print(f"[DEBUG] oracle-postprocess.exe not found at {proc_script}")
            print("[DEBUG] Place oracle-postprocess.exe in the decompile/ folder.")
        else:
            success = False
            max_retries = 5

            for attempt in range(1, max_retries + 1):
                try:
                    print(f"[DEBUG] Running oracle-postprocess (Attempt {attempt}/{max_retries})...")
                    proc_task = await asyncio.create_subprocess_exec(
                        "cmd.exe", "/c", proc_script, "-k", ORACLE_KEY, "-v", "2", file_format, target_name,
                        cwd=decompile_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )

                    websocket_busy = False
                    try:
                        await asyncio.wait_for(proc_task.wait(), timeout=120)
                    except asyncio.TimeoutError:
                        print(f"[DEBUG] oracle-postprocess timed out after 120s, killing...")
                        proc_task.kill()
                        await proc_task.wait()
                        continue

                    while True:
                        line = await proc_task.stdout.readline()
                        if not line:
                            break
                        decoded_line = line.decode('utf-8', errors='ignore').rstrip()
                        if "oracle-postprocess" in decoded_line.lower() or "-k " in decoded_line:
                            continue
                        if "websocket connection" in decoded_line.lower():
                            websocket_busy = True
                        print(decoded_line)

                    returncode = proc_task.returncode

                    if returncode == 0 or os.path.exists(out_path):
                        print(f"[DEBUG] oracle-postprocess succeeded on attempt {attempt}.")
                        success = True
                        break
                    elif websocket_busy:
                        print(f"[DEBUG] Websocket busy, waiting longer before retry...")
                        await asyncio.sleep(10)
                    else:
                        print(f"[DEBUG] oracle-postprocess attempt {attempt} failed with exit code {returncode}")
                except Exception as e:
                    print(f"[DEBUG] oracle-postprocess error on attempt {attempt}: {e}")

                if attempt < max_retries:
                    await asyncio.sleep(3)

            if not success:
                print("[DEBUG] All oracle-postprocess attempts failed.")
                return None, None

            if info_msg is not None and embed is not None:
                await update_status(info_msg, embed, "decompiling_scripts")
            await update_bot_presence(game_name, text="Sending To User...")

    game_raw_path = os.path.join(decompile_dir, target_name)

    for _ in range(timeout):
        if os.path.exists(out_path):
            print(f"[DEBUG] Found processed output file: {out_path}")
            return out_path, game_raw_path
        await asyncio.sleep(1)

    print("[DEBUG] Processed file check timed out.")
    return None, None

async def run_decompile_logic(send_func, user: discord.User | discord.Member, guild, channel, place_id: str, game_id: str = None, is_ephemeral: bool = False, on_status_update=None, user_cookie: str = None, raw: bool = False):
    author_id = user.id
    now = time.time()

    if decompile_disabled:
        await send_msg(send_func, "Decompiling is currently disabled by the bot owner.", ephemeral=is_ephemeral)
        return

    if guild:
        blacklisted_servers = load_blacklisted_servers()
        if guild.id in blacklisted_servers:
            await send_msg(send_func, "This server has been blacklisted from using the bot.", ephemeral=is_ephemeral)
            return

    blacklisted_users = load_blacklisted_users()

    if author_id in blacklisted_users:
        if now < blacklisted_users[author_id]:
            print(f"[DEBUG] Ignored command from blacklisted user {author_id}.")
            return
        else:
            del blacklisted_users[author_id]
            save_blacklisted_users(blacklisted_users)
            user_decompile_history[author_id] = []

    history = user_decompile_history.get(author_id, [])
    history = [ts for ts in history if now - ts < 60]

    if len(history) >= 5:
        add_user_to_blacklist(author_id, duration_seconds=300)
        user_decompile_history[author_id] = []
        await send_msg(send_func, f"<@{author_id}> You have been blacklisted for spamming (5 decompiles in under 1 minute). Your commands will be ignored for 5 minutes.", ephemeral=is_ephemeral)
        return

    history.append(now)
    user_decompile_history[author_id] = history

    place_id = place_id.strip().strip("[]")

    if not place_id.isdigit():
        resolved = await resolve_game_by_name(place_id)
        if resolved is None:
            await send_msg(send_func, f"Could not find a game matching `{place_id}`.", ephemeral=is_ephemeral)
            return
        place_id = resolved["place_id"]
    else:
        # numeric id: may be a place id or a universe id -> normalize to place
        place_id = await resolve_place_or_universe(place_id, cookie=user_cookie)

    blacklisted_games = load_blacklisted_games()
    if str(place_id).strip() in blacklisted_games:
        reason = blacklisted_games[str(place_id).strip()]
        game_info = await get_place_info(place_id, cookie=user_cookie)
        game_name = game_info.get("name") if not game_info.get("error") else f"Place {place_id}"

        await send_msg(send_func, f"**Unable to decompile game:** {game_name}\n**Reason:** {reason}", ephemeral=is_ephemeral)
        return

    await enqueue_decompile_task(send_func, user, guild, channel, place_id, game_id, is_ephemeral, on_status_update=on_status_update, user_cookie=user_cookie, raw=raw)

async def execute_decompile_job(send_func, author_id: int, guild, channel, place_id: str, game_id: str = None, is_ephemeral: bool = False, is_priority: bool = False, resume_info_msg=None, resume_embed=None, on_status_update=None, cookie_retries=0, original_cookie_index=None, user_cookie: str = None, cookie_ban_msg=None, raw: bool = False):
    global is_decompiling, running_jobs, active_events, current_active_data, active_cookie_index, default_cookie_index, post_data
    is_retry = False
    saved_user_cookie = None
    post_data = {}          # per-job client state (game_version etc.), never reuse the last job's
    if original_cookie_index is None:
        original_cookie_index = active_cookie_index
    print(f"\n[DEBUG] --- Executing Decompile Job ---")
    print(f"[DEBUG] Input place_id: {place_id}")
    print(f"[DEBUG] Input game_id: {game_id}")
    print(f"[DEBUG] priority={is_priority}")
    print(f"[DEBUG] user_cookie provided: {bool(user_cookie)}")

    cookie_info = None
    if user_cookie and cookie_retries == 0:
        result = await validate_cookie(user_cookie)
        if not result.get("valid"):
            await send_msg(send_func, f"Invalid cookie: {result.get('reason', 'Unknown error')}.", ephemeral=is_ephemeral)
            return
        saved_user_cookie = get_active_cookie()
        saved_user_cookie_index = active_cookie_index
        replaced = _replace_roblox_security_cookie(user_cookie)
        print(f"[COOKIE] _replace_roblox_security_cookie for user cookie returned: {replaced}")
        cookie_info = f"Custom ({result.get('username')})"
    elif not user_cookie and cookie_retries == 0:
        cookies = load_cookies()
        if cookies:
            _replace_roblox_security_cookie(cookies[0])
            active_cookie_index = 0
            cookie_info = f"Index 0"
            print(f"[COOKIE] No cookie provided. Defaulting to index 0.")

    link = game_id if game_id and game_id.startswith("http") else f"https://www.roblox.com/games/{place_id}/about"

    if game_id:
        print(f"[DEBUG] Processing game_id argument...")
        if game_id.startswith("http"):
            parsed_input = urlparse(game_id)
            query_params = parse_qs(parsed_input.query)
            ps_code = query_params.get("privateServerLinkCode", [None])[0]
            share_code = query_params.get("code", [None])[0]
            link_type = query_params.get("type", ["Server"])[0]
            join_url = f"https://www.roblox.com/games/start?placeId={place_id}"
            launch_url = f"roblox://experiences/start?placeId={place_id}"
            if ps_code:
                join_url = f"https://www.roblox.com/games/start?placeId={place_id}&privateServerLinkCode={ps_code}"
                ps_json = json.dumps({"psCode": ps_code})
                launch_url = f"roblox://experiences/start?placeId={place_id}&launchData={quote(ps_json, safe='')}"
            elif share_code:
                join_url = f"https://www.roblox.com/games/start?placeId={place_id}"
                launch_url = f"roblox://navigation/share_links?code={quote(share_code)}&type={quote(link_type)}"
        else:
            join_url = f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={game_id}"
            launch_url = f"roblox://experiences/start?placeId={place_id}&gameInstanceId={game_id}"
    else:
        join_url = await get_best_join_url(place_id)
        launch_url = join_url.replace("https://www.roblox.com/games/start?", "roblox://experiences/start?")

    print(f"[DEBUG] Final launch join_url: {launch_url}")

    # icon_url was only assigned on the fresh-embed path, so resumed jobs died
    # with NameError at upload time. Default it (and recover it from the embed).
    icon_url = None

    if resume_info_msg is not None and resume_embed is not None:
        info_msg = resume_info_msg
        embed = resume_embed
        game_name = embed.title
        try:
            if embed.thumbnail and embed.thumbnail.url:
                icon_url = embed.thumbnail.url
        except Exception:
            pass
    else:
        info_msg = None
        embed = None
        game_info = await get_place_info(place_id, cookie=user_cookie)
        if game_info.get("error"):
            error_reason = game_info.get("reason", "")
            if "Game is unplayable" in error_reason:
                if user_cookie and cookie_retries == 0:
                    print(f"[COOKIE] Roblox API says unplayable but user provided cookie, proceeding anyway...")
                else:
                    ban_keywords = ["banned", "not allowed", "unauthorized", "blocked", "kicked"]
                    is_account_ban = any(kw in error_reason.lower() for kw in ban_keywords)
                    if is_account_ban:
                        cookies = load_cookies()
                        if cookie_retries < len(cookies):
                            old_index = active_cookie_index
                            active_cookie_index = (active_cookie_index + 1) % len(cookies)
                            if active_cookie_index == old_index:
                                active_cookie_index = 0
                            new_cookie = cookies[active_cookie_index]
                            _replace_roblox_security_cookie(new_cookie)
                            print(f"[DEBUG] Banned. Auto-switched to cookie {active_cookie_index}")
                            if cookie_ban_msg is not None:
                                try:
                                    if embed is not None:
                                        embed.set_field_at(0, name="Cookie", value=f"Index {active_cookie_index}")
                                        await cookie_ban_msg.edit(embed=embed)
                                except Exception:
                                    pass
                            else:
                                cookie_ban_msg = await send_msg(send_func, content=f"<@{author_id}> Auto-switched to cookie {active_cookie_index}", ephemeral=is_ephemeral)
                            await asyncio.sleep(0.1)
                            is_retry = True
                            await execute_decompile_job(send_func, author_id, guild, channel, place_id, game_id, is_ephemeral, is_priority=is_priority, on_status_update=on_status_update, cookie_retries=cookie_retries + 1, original_cookie_index=original_cookie_index, user_cookie=user_cookie, cookie_ban_msg=cookie_ban_msg, raw=raw)
                            if original_cookie_index is not None and original_cookie_index < len(cookies):
                                active_cookie_index = original_cookie_index
                                _replace_roblox_security_cookie(cookies[original_cookie_index])
                            return
                        elif cookie_retries >= len(cookies):
                            if original_cookie_index is not None and original_cookie_index < len(cookies):
                                active_cookie_index = original_cookie_index
                                _replace_roblox_security_cookie(cookies[original_cookie_index])
                            view = CookieBannedView(send_func, author_id, guild, channel, place_id, game_id, is_ephemeral, is_priority, on_status_update)
                            embed = discord.Embed(title="All Cookies Banned", description="WARNING. Your account WILL be at risk of getting banned. Cookies will not be logged.", color=0xE74C3C)
                            await send_msg(send_func, f"<@{author_id}>", embed=embed, ephemeral=is_ephemeral, view=view)
                            return
                    else:
                        await send_msg(send_func, f"<@{str(author_id)}> {error_reason}", ephemeral=is_ephemeral)
                        return
            else:
                await send_msg(send_func, f"<@{str(author_id)}> {error_reason}", ephemeral=is_ephemeral)
                return

        if not game_info.get("error") or (user_cookie and cookie_retries == 0):
            game_name = game_info.get("name") or f"Place {place_id}"
            icon_url = game_info.get("icon_url")
        else:
            game_name = f"Place {place_id}"
            icon_url = None

        embed = discord.Embed(title=game_name, url=link, color=0x3498DB)
        if cookie_info:
            embed.add_field(name="Cookie", value=cookie_info, inline=False)
        embed.add_field(name="Place ID", value=place_id, inline=True)
        if game_id:
            embed.add_field(name="Job ID", value=game_id, inline=True)
        if icon_url:
            embed.set_thumbnail(url=icon_url)

        join_view = JoinGameView(join_url)
        info_msg = await send_msg(send_func, embed=embed, ephemeral=is_ephemeral, view=join_view)
        try:
            # Mirror the join URL to disk so the button still answers after a restart.
            join_view.message_id = getattr(info_msg, "id", None)
            remember_join_url(join_view.message_id, join_url)
        except Exception as e:
            print(f"[DECOMPILE] Could not remember join url: {e}")

    rec_ev = asyncio.Event()
    fin_ev = asyncio.Event()

    running_jobs += 1
    is_decompiling = running_jobs > 0

    if decompile_disabled:
        await update_status(info_msg, embed, "kill_switch")
        await send_msg(send_func, "Decompiling is currently disabled by the bot owner (Kill Switch).", ephemeral=is_ephemeral)
        running_jobs -= 1
        is_decompiling = running_jobs > 0
        return

    data = {
        "send_func": send_func,
        "author_id": author_id,
        "guild": guild,
        "channel": channel,
        "place_id": place_id,
        "game_id": game_id,
        "is_ephemeral": is_ephemeral,
        "is_priority": is_priority,
        "aborted": False,
        "joined": False,
        "info_msg": info_msg,
        "embed": embed,
        "process": None,
    }
    current_active_data = data
    active_events = (rec_ev, fin_ev)

    await update_bot_presence(game_name)
    await update_status(info_msg, embed, "launching")
    
    saved_cookie_index = active_cookie_index
    if user_cookie:
        print(f"[COOKIE] Using custom cookie for launch")
    else:
        print(f"[COOKIE] Using cookie {active_cookie_index} for launch")
    
    try:
        roblox = find_roblox()
        if not roblox:
            print("[DEBUG] Roblox executable not found on host!")
            if not data["aborted"]:
                await update_status(info_msg, embed, "error")
            await send_msg(send_func, "Roblox installation not found.", ephemeral=is_ephemeral)
            return

        print(f"[DEBUG] Launching Roblox binary at: {roblox}")
        process = subprocess.Popen([roblox, "--cmd", launch_url])
        data["process"] = process
        await update_status(info_msg, embed, "joining")

        game_file = None
        try:
            file_path, game_file = await process_file(send_func, process, game_name, timeout=60, ephemeral=is_ephemeral, info_msg=info_msg, embed=embed, rec_ev=rec_ev, fin_ev=fin_ev, job_data=data, raw=raw)

            global SKIP_PROCESSFILE
            SKIP_PROCESSFILE = False

            if file_path:
                await update_status(info_msg, embed, "uploading")
                await update_bot_presence(game_name, text="Uploading File...")
                game_version = post_data.get("game_version", "N/A")
                author = await bot.fetch_user(author_id) if author_id else None
                upload_result = await upload_file(
                    file_path, place_id,
                    game_name=game_name,
                    user_id=str(author_id),
                    display_name=author.display_name if author else "Unknown",
                    game_version=game_version,
                    icon_url=icon_url
                )

                if upload_result:
                    download_url = upload_result["url"]
                    embed.color = 0x2ECC71

                    # Build the Animations/Sounds pack for this place (download
                    # runs in the background with the cookie that joined the game).
                    pack_id = None
                    try:
                        from . import assets as assets_mod
                        # harvest rbxassetid:// references from the place file
                        # binary too — assets only referenced in scripts or
                        # properties never show up in the live client scan.
                        scanned_ids = []
                        for _p in (file_path, game_file):
                            if _p and os.path.isfile(_p):
                                scanned_ids += await asyncio.to_thread(
                                    assets_mod.scan_place_file, _p)
                        pack_id = await assets_mod.create_pack(
                            place_id,
                            author_id,
                            author.display_name if author else "Unknown",
                            game_name,
                            icon_url=icon_url,
                            game_version=game_version,
                            cookie=user_cookie or get_active_cookie(),
                            download=True,
                            scanned_ids=scanned_ids,
                        )
                        if pack_id:
                            await asyncio.to_thread(
                                assets_mod.link_pack_to_game,
                                pack_id,
                                download_url.rsplit("/", 1)[-1],
                            )
                    except Exception as e:
                        print(f"[ASSETS] Failed to create asset pack: {e}")
                        pack_id = None

                    download_view = DownloadView(download_url, pack_id=pack_id)
                    await send_msg(send_func, content=f"<@{author_id}>", embed=embed, ephemeral=is_ephemeral, view=download_view)
                else:
                    await send_msg(send_func, content="Decompilation complete but upload failed.", embed=embed, ephemeral=is_ephemeral)
            else:
                if not data["aborted"]:
                    await update_status(info_msg, embed, "error")
                    await send_msg(send_func, content="Decompilation failed or timed out.", embed=embed, ephemeral=is_ephemeral)

        except Exception as e:
            print(f"[DEBUG] Error during decompile process: {e}")
            if not data["aborted"]:
                await update_status(info_msg, embed, "error")
                await send_msg(send_func, content=f"An error occurred: {e}", embed=embed, ephemeral=is_ephemeral)
        finally:
            _cleanup_decompile_artifacts()
            running_jobs -= 1
            is_decompiling = running_jobs > 0
            if not data["aborted"]:
                current_active_data = None
            if saved_user_cookie is not None:
                _replace_roblox_security_cookie(saved_user_cookie)
                print(f"[COOKIE] Restored original cookie after user-provided cookie job")
            elif saved_cookie_index is not None:
                cookies = load_cookies()
                if saved_cookie_index < len(cookies):
                    active_cookie_index = saved_cookie_index
                    _replace_roblox_security_cookie(cookies[saved_cookie_index])
                    print(f"[COOKIE] Restored cookie to index {saved_cookie_index}")
            await reset_bot_presence()

    except Exception as e:
        print(f"[DEBUG] Critical error in execute_decompile_job: {e}")
        running_jobs -= 1
        is_decompiling = running_jobs > 0
        current_active_data = None
        await reset_bot_presence()

EVENT_GUILD_ID = 1540376749400137808
EVENT_CHANNEL_ID = 1545977764077903942
async def screenshare_auto_join():
    while True:
        await asyncio.sleep(30)
        try:
            guild = bot.get_guild(EVENT_GUILD_ID)
            if not guild:
                continue
            channel = guild.get_channel(EVENT_CHANNEL_ID)
            if not channel:
                print(f"[SCREENSHARE] Event channel {EVENT_CHANNEL_ID} was not found.")
                continue
            permissions = channel.permissions_for(guild.me)
            if not permissions.connect:
                print(f"[SCREENSHARE] Missing Connect permission in {channel.name}.")
                continue
            if not permissions.speak:
                print(f"[SCREENSHARE] Missing Speak permission in {channel.name}; grant it manually.")
            voice_state = guild.me.voice
            if voice_state and voice_state.channel and voice_state.channel.id == channel.id:
                continue
            if voice_state and voice_state.channel:
                await voice_state.channel.disconnect()
            await channel.connect()
            print(f"[SCREENSHARE] Joined {channel.name}. Discord bots cannot start Go Live screen sharing.")
        except Exception as e:
            print(f"[SCREENSHARE] Voice join error: {e}")

async def start_screenshare_on_ready():
    await bot.wait_until_ready()
    asyncio.create_task(screenshare_auto_join())
