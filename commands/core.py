import asyncio
import base64
from urllib.parse import parse_qs, urlparse, quote, urlencode
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
import secrets
import aiohttp
import discord
import requests
import psutil
import boto3
try:
    from mss import mss as mss_lib
except ImportError:
    mss_lib = None

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
STORAGE_MAX_BYTES = 25 * 1024 * 1024 * 1024
banned_ips_file = os.path.join(BASE_DIR, "banned_ips.txt")
ips_file = os.path.join(BASE_DIR, "tracked_ips.json")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "1532820804402806844")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "xN_MmWZKUhswwN3jSe2XnCQKBnBviKti")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "https://storage.luaisgame.com/api/auth/callback")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "1293889121374179328"))
MC_OWNER_ID = 1401271744597196831
_auth_sessions = {}
_oauth_states = {}

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

class JoinGameView(discord.ui.View):
    def __init__(self, join_url: str = None):
        super().__init__(timeout=None)
        self.join_url = join_url
        btn = discord.ui.Button(
            label="Bot has not joined yet" if not join_url else "Copy Join URL",
            style=discord.ButtonStyle.secondary,
            disabled=not join_url,
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
        if self.join_url:
            await interaction.response.send_message(f"**Join URL (copy this):**\n```\n{self.join_url}\n```", ephemeral=True)
        else:
            await interaction.response.send_message("Bot has not joined the game yet.", ephemeral=True)

    def set_join_url(self, join_url: str):
        self.join_url = join_url
        self.join_button.label = "Copy Join URL"
        self.join_button.disabled = False

class DownloadView(discord.ui.View):
    def __init__(self, download_url: str):
        super().__init__(timeout=None)
        button = discord.ui.Button(label="Download", url=download_url, emoji="⬇️")
        self.add_item(button)

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
            post_data = await request.json()
        else:
            post_data = {"raw": await request.text()}
        print(f"[DEBUG] Received POST request: {post_data}")
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
    filepath = os.path.join(storage_dir, filename)
    if not os.path.isfile(filepath):
        return web.Response(text="Not found", status=404)
    ip = request.headers.get("X-Forwarded-For", request.remote)
    _track_download(ip, filename)
    return web.FileResponse(filepath, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

async def handle_games_json(request):
    games_json = os.path.join(storage_dir, "games.json")
    if not os.path.exists(games_json):
        return web.json_response([])
    with open(games_json, "r") as f:
        return web.json_response(json.load(f))

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

async def handle_discord_auth(request):
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()
    query = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    resp = web.Response(status=302)
    resp.headers["Location"] = f"https://discord.com/api/oauth2/authorize?{query}"
    return resp


async def handle_discord_callback(request):
    code = request.query.get("code", "")
    state = request.query.get("state", "")
    state_time = _oauth_states.pop(state, None)
    if not code or not state_time or time.time() - state_time > 600:
        return web.Response(text="Invalid or expired Discord login.", status=400)

    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://discord.com/api/oauth2/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as token_resp:
            if token_resp.status != 200:
                return web.Response(text="Discord authentication failed.", status=401)
            token = await token_resp.json()
        access_token = token.get("access_token")
        if not access_token:
            return web.Response(text="Discord authentication failed.", status=401)
        async with session.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as user_resp:
            if user_resp.status != 200:
                return web.Response(text="Could not read Discord account.", status=401)
            user_data = await user_resp.json()

    session_id = secrets.token_urlsafe(48)
    _auth_sessions[session_id] = {
        "id": str(user_data.get("id", "")),
        "username": user_data.get("username", ""),
        "avatar": user_data.get("avatar", ""),
        "expires": time.time() + 30 * 86400,
    }
    resp = web.Response(status=302)
    resp.headers["Location"] = "/"
    resp.set_cookie(
        "session_id",
        session_id,
        max_age=30 * 86400,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    return resp


async def handle_discord_verify(request):
    return web.json_response({"error": "legacy authentication endpoint"}, status=410)

async def handle_auth_me(request):
    user = _get_user_info(request)
    if not user:
        return web.json_response({"authenticated": False}, status=401)
    authorized = int(user.get("id", 0)) in (BOT_OWNER_ID, MC_OWNER_ID)
    return web.json_response({"authenticated": True, "mc_authorized": authorized, **user})

def _get_user_info(request):
    session_id = request.cookies.get("session_id", "")
    session = _auth_sessions.get(session_id)
    if not session:
        return None
    if session.get("expires", 0) <= time.time():
        _auth_sessions.pop(session_id, None)
        return None
    return session

def _is_admin(request):
    user = _get_user_info(request)
    if not user:
        return False
    return int(user.get("id", 0)) == BOT_OWNER_ID

def _is_mc_admin(request):
    user = _get_user_info(request)
    if not user:
        return False
    return int(user.get("id", 0)) in (BOT_OWNER_ID, MC_OWNER_ID)

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
    ip = request.headers.get("X-Forwarded-For", request.remote)
    _track_ip(ip)
    if ip in _get_banned_ips():
        return web.Response(text="Access denied.", status=403)
    admin = _is_admin(request)
    mc_authorized = _is_mc_admin(request)
    user_info = _get_user_info(request)
    games_json = os.path.join(storage_dir, "games.json")
    entries = []
    if os.path.exists(games_json):
        with open(games_json, "r") as f:
            entries = json.load(f)
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
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#050508; color:#c9d1d9; font-family:'Inter','SF Pro Display',system-ui,-apple-system,sans-serif; min-height:100vh; padding-bottom:60px; position:relative; }}
.bg-grid {{ position:fixed; top:0; left:0; width:100%; height:100%; z-index:0; pointer-events:none;
  background-image: linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px), linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);
  background-size:60px 60px; }}
.bg-glow {{ position:fixed; width:600px; height:600px; border-radius:50%;
  background:radial-gradient(circle,rgba(0,200,120,.06),transparent 70%);
  top:50%; left:50%; transform:translate(-50%,-50%); z-index:0; pointer-events:none;
  animation:pulse 6s ease-in-out infinite alternate; }}
@keyframes pulse {{ 0%{{opacity:.6;transform:translate(-50%,-50%) scale(1)}} 100%{{opacity:1;transform:translate(-50%,-50%) scale(1.15)}} }}
.header {{ background:rgba(13,17,23,.85); padding:20px 40px; border-bottom:1px solid rgba(0,220,120,.08); display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:100; backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); }}
.header-left {{ display:flex; align-items:center; gap:20px; }}
.header-right {{ display:flex; align-items:center; gap:12px; }}
.logo {{ font-size:26px; font-weight:700; letter-spacing:-0.5px; display:flex; align-items:center; gap:8px; }}
.roblox-icon {{ width:32px; height:32px; }}
.lua {{ color:#58a6ff; }} .is {{ color:#8b949e; }} .game {{ color:#3fb950; }}
.search {{ background:rgba(13,17,23,.6); border:1px solid rgba(255,255,255,.06); color:#c9d1d9; padding:10px 16px; border-radius:8px; width:320px; font-size:14px; outline:none; transition:border-color 0.2s; }}
.search:focus {{ border-color:rgba(0,220,120,.3); box-shadow:0 0 0 3px rgba(0,220,120,.08); }}
.btn {{ padding:10px 20px; border-radius:8px; border:none; font-size:13px; font-weight:600; cursor:pointer; transition:all 0.2s; text-decoration:none; display:inline-flex; align-items:center; gap:6px; }}
.btn-primary {{ background:#00dc78; color:#050508; }}
.btn-primary:hover {{ background:#00ff8a; transform:translateY(-1px); }}
.btn-secondary {{ background:rgba(255,255,255,.04); color:#c9d1d9; border:1px solid rgba(255,255,255,.06); }}
.btn-secondary:hover {{ background:rgba(255,255,255,.08); }}
.btn-discord {{ background:rgba(88,101,242,.8); color:#fff; border:1px solid rgba(88,101,242,.3); }}
.btn-discord:hover {{ background:#5865f2; transform:translateY(-1px); }}
.btn-minecraft {{ background:rgba(0,220,120,.12); color:#00dc78; border:1px solid rgba(0,220,120,.3); }}
.btn-minecraft:hover {{ background:rgba(0,220,120,.22); transform:translateY(-1px); }}
.container {{ max-width:1200px; margin:30px auto; padding:0 20px; position:relative; z-index:1; }}
.game-card {{ background:rgba(22,27,34,.6); border:1px solid rgba(0,220,120,.08); border-radius:12px; padding:0; margin-bottom:16px; transition:all 0.3s; overflow:hidden; backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px); }}
.game-card:hover {{ border-color:rgba(0,220,120,.25); transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,0.3); }}
.game-card-inner {{ display:flex; gap:0; }}
.game-thumb {{ width:180px; height:180px; object-fit:cover; border-radius:12px 0 0 12px; flex-shrink:0; background:#0d1117; }}
.game-info {{ padding:20px; flex:1; display:flex; flex-direction:column; justify-content:center; }}
.game-title {{ font-size:20px; font-weight:600; color:#f0f6fc; margin-bottom:12px; text-decoration:none; display:inline-block; transition:color 0.2s; }}
.game-title:hover {{ color:#00dc78; }}
.game-meta {{ font-size:13px; color:#8b949e; margin-bottom:16px; line-height:2; }}
.label {{ color:#00dc78; font-weight:500; }}
.value {{ color:#c9d1d9; margin-right:16px; }}
.download-btn {{ display:inline-block; background:linear-gradient(135deg,#238636,#2ea043); color:#fff; padding:10px 20px; border-radius:8px; text-decoration:none; font-size:13px; font-weight:600; transition:all 0.2s; }}
.download-btn:hover {{ transform:translateY(-1px); box-shadow:0 4px 12px rgba(35,134,54,0.4); }}
.game-buttons {{ display:flex; gap:8px; }}
.copy-btn {{ background:rgba(255,255,255,.04); color:#c9d1d9; border:1px solid rgba(255,255,255,.06); padding:10px 20px; border-radius:8px; font-size:13px; font-weight:600; cursor:pointer; transition:all 0.2s; }}
.copy-btn:hover {{ background:rgba(255,255,255,.08); border-color:rgba(0,220,120,.2); }}
.count {{ color:#8b949e; font-size:14px; margin-bottom:20px; }}
.footer {{ text-align:center; padding:20px; color:#484f58; font-size:13px; border-top:1px solid rgba(0,220,120,.06); position:fixed; bottom:0; left:0; right:0; background:rgba(5,5,8,.85); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); z-index:50; }}
.footer a {{ color:#58a6ff; text-decoration:none; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.5; }} }}
@media (max-width:768px) {{
    .header {{ padding:12px 16px; flex-wrap:wrap; gap:10px; }}
    .header-left {{ width:100%; justify-content:space-between; }}
    .header-right {{ width:100%; justify-content:center; flex-wrap:wrap; }}
    .search {{ width:100%; }}
    .container {{ padding:0 12px; margin-top:16px; margin-bottom:60px; }}
    .game-card-inner {{ flex-direction:column; }}
    .game-thumb {{ width:100%; height:200px; border-radius:12px 12px 0 0; }}
    .game-info {{ padding:16px; }}
    .game-title {{ font-size:16px; }}
    .game-meta {{ font-size:12px; line-height:1.8; }}
    .value {{ margin-right:8px; }}
    .game-buttons {{ flex-direction:column; }}
    .game-buttons .download-btn, .game-buttons .copy-btn {{ text-align:center; }}
    .btn {{ padding:8px 14px; font-size:12px; }}
    .admin-panel {{ padding:12px; }}
    .admin-header {{ flex-direction:column; gap:10px; }}
    .admin-tabs {{ flex-wrap:wrap; justify-content:center; }}
    .ip-item {{ flex-direction:column; align-items:flex-start; gap:4px; }}
    .ban-form {{ flex-direction:column; }}
    .logo {{ font-size:20px; }}
    .roblox-icon {{ width:24px; height:24px; }}
}}
.admin-panel {{ background:rgba(22,27,34,.7); border:1px solid rgba(0,220,120,.08); border-radius:12px; padding:20px; margin-bottom:24px; backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); }}
.admin-header {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }}
.admin-title {{ font-size:18px; font-weight:600; color:#f0f6fc; }}
.admin-tabs {{ display:flex; gap:8px; }}
.tab {{ padding:8px 16px; border-radius:6px; border:1px solid rgba(255,255,255,.06); background:rgba(13,17,23,.6); color:#8b949e; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.2s; }}
.tab.active {{ background:rgba(0,220,120,.15); color:#00dc78; border-color:rgba(0,220,120,.3); }}
.tab:hover:not(.active) {{ border-color:rgba(0,220,120,.2); }}
.tab-content.hidden {{ display:none; }}
.admin-section h3 {{ font-size:14px; color:#8b949e; margin-bottom:12px; font-weight:500; }}
.ip-list {{ max-height:300px; overflow-y:auto; }}
.ip-item {{ display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:rgba(13,17,23,.6); border:1px solid rgba(255,255,255,.04); border-radius:8px; margin-bottom:8px; font-size:13px; }}
.ip-item .ip {{ color:#00dc78; font-family:monospace; }}
.ip-item .meta {{ color:#484f58; font-size:12px; }}
.console-log {{ background:rgba(13,17,23,.6); border:1px solid rgba(255,255,255,.04); border-radius:8px; padding:12px; max-height:400px; overflow-y:auto; font-family:'Cascadia Code','Fira Code',monospace; font-size:12px; line-height:1.6; color:#8b949e; white-space:pre-wrap; word-break:break-all; }}
.ban-form {{ display:flex; gap:8px; margin-top:12px; }}
.ban-form input {{ background:rgba(13,17,23,.6); border:1px solid rgba(255,255,255,.06); color:#c9d1d9; padding:8px 12px; border-radius:6px; font-size:13px; outline:none; flex:1; }}
.ban-form input:focus {{ border-color:rgba(0,220,120,.3); }}
.btn-ban {{ background:#da3633; color:#fff; padding:8px 16px; border:none; border-radius:6px; cursor:pointer; font-weight:500; font-size:13px; }}
.btn-ban:hover {{ background:#f85149; }}
.btn-unban {{ background:#238636; color:#fff; padding:8px 16px; border:none; border-radius:6px; cursor:pointer; font-weight:500; font-size:13px; }}
.btn-unban:hover {{ background:#2ea043; }}
.login-overlay {{ position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:1000; }}
.login-overlay.active {{ display:flex; }}
.login-box {{ background:rgba(22,27,34,.9); border:1px solid rgba(0,220,120,.15); border-radius:12px; padding:32px; width:360px; text-align:center; backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); }}
.login-box h2 {{ color:#f0f6fc; margin-bottom:20px; font-size:20px; }}
.login-box input {{ width:100%; background:rgba(13,17,23,.6); border:1px solid rgba(255,255,255,.06); color:#c9d1d9; padding:12px 16px; border-radius:8px; font-size:14px; outline:none; margin-bottom:16px; }}
.login-box input:focus {{ border-color:rgba(0,220,120,.3); }}
.login-box .btn {{ width:100%; justify-content:center; }}
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
        <a class="btn btn-primary" href="https://discord.com/api/oauth2/authorize?client_id=1532820804402806844&permissions=8&scope=bot%20applications.commands" target="_blank">Add Bot</a>
        {"<button class='btn btn-secondary' data-action='toggle-console'>Console</button>" if admin else ""}
        {"<a class='btn btn-minecraft' href='/mc'><svg width='16' height='16' viewBox='0 0 24 24' fill='none' aria-hidden='true'><path d='M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6ZM10 7h4v3h-4V7Zm0 7h4v3h-4v-3Z' fill='currentColor'/></svg> MC Console</a>" if mc_authorized else ""}
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
        h += '<div class="game-card" data-name="'+(e.game_name||'').toLowerCase()+'" data-user="'+(e.display_name||'').toLowerCase()+'"><div class="game-card-inner"><img class="game-thumb" src="'+(e.icon_url||'')+'" alt="thumb" onerror="this.parentElement.removeChild(this)"><div class="game-info"><a class="game-title" href="https://www.roblox.com/games/'+e.place_id+'" target="_blank">'+(e.game_name||'Unknown')+'</a><div class="game-meta"><span class="label">Place ID:</span> <span class="value">'+e.place_id+'</span><span class="label">Version:</span> <span class="value">'+(e.game_version||'N/A')+'</span><span class="label">Requested by:</span> <span class="value">'+(e.display_name||'Unknown')+' ('+e.user_id+')</span><span class="label">Downloaded:</span> <span class="value">'+e.timestamp+'</span></div><div class="game-buttons"><a class="download-btn" href="/'+e.filename+'" download>Download</a><button class="copy-btn" data-url="https://storage.luaisgame.com/'+e.filename+'">Copy Link</button></div></div></div></div>';
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

def _mc_get_servers():
    if not os.path.isdir(MC_DIR):
        return []
    servers = []
    for name in sorted(os.listdir(MC_DIR)):
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

def _mc_safe_server_dir(name):
    if not name or name != os.path.basename(name) or not all(c.isalnum() or c in "-_" for c in name):
        return None
    server_dir = os.path.join(MC_DIR, name)
    return server_dir if os.path.isdir(server_dir) else None

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
    if not _is_mc_admin(request):
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
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "").strip()
    version = data.get("version", "").strip()
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
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "").strip()
    server_dir = _mc_safe_server_dir(name)
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    proc = mc_processes.get(name)
    if proc and proc.returncode is None:
        proc.kill()
        await proc.wait()
    mc_processes.pop(name, None)
    mc_console_buffers.pop(name, None)
    mc_ws_clients.pop(name, None)
    shutil.rmtree(server_dir)
    return web.json_response({"ok": True})

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
    buf = mc_console_buffers.setdefault(name, [])
    ws_list = mc_ws_clients.setdefault(name, [])
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n\r")
            ts = time.strftime("%H:%M:%S")
            entry = f"[{ts}] {text}"
            buf.append(entry)
            if len(buf) > 500:
                buf[:] = buf[-500:]
            dead = []
            for ws in ws_list:
                try:
                    await ws.send_json({"type": "output", "line": entry})
                except Exception:
                    dead.append(ws)
            for ws in dead:
                ws_list.remove(ws)
    except Exception:
        pass
    if name in mc_processes:
        mc_processes[name] = None

async def mc_api_servers(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"servers": _mc_get_servers()})

async def mc_api_start(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "")
    loader_type = data.get("loader", "fabric")
    server_dir = os.path.join(MC_DIR, name)
    if not os.path.isdir(server_dir):
        return web.json_response({"error": "Server folder not found"}, status=404)
    if name in mc_processes and mc_processes[name] is not None and mc_processes[name].returncode is None:
        return web.json_response({"error": "Already running"}, status=400)
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
        buf = mc_console_buffers.setdefault(name, [])
        buf.append(f"[{time.strftime('%H:%M:%S')}] No server.jar found, installing {loader_type.title()} server...")
        try:
            from minecraft_setup import setup_server
            ok = await asyncio.to_thread(setup_server, server_dir, loader_type, selected_version)
            if not ok:
                buf.append(f"[{time.strftime('%H:%M:%S')}] Failed to install {loader_type.title()} server.")
                return web.json_response({"error": f"{loader_type.title()} install failed"}, status=500)
            buf.append(f"[{time.strftime('%H:%M:%S')}] {loader_type.title()} installed successfully.")
        except Exception as e:
            buf.append(f"[{time.strftime('%H:%M:%S')}] Setup error: {e}")
            return web.json_response({"error": str(e)}, status=500)
        jar = _mc_find_jar(server_dir)
        is_forge = _mc_is_forge(server_dir)
        if not jar and not is_forge:
            buf.append(f"[{time.strftime('%H:%M:%S')}] Still no server.jar after install.")
            return web.json_response({"error": "No server jar after install"}, status=500)
    mem = os.environ.get("MC_MEMORY", "2G")
    java = _mc_get_java(server_dir)
    forge_jvm_args_str = " ".join(FORGE_JVM_ARGS)
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
            return web.json_response({"error": "No Forge launch method found"}, status=500)
    else:
        proc = await asyncio.create_subprocess_exec(
            java, f"-Xmx{mem}", f"-Xms{mem}", "-jar", jar, "nogui",
            cwd=server_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    mc_processes[name] = proc
    mc_console_buffers.setdefault(name, [])
    asyncio.create_task(_mc_read_output(name, proc))
    return web.json_response({"ok": True, "pid": proc.pid})

async def mc_api_create(request):
    if not _is_mc_admin(request):
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
    with open(os.path.join(server_dir, ".loader"), "w") as f:
        f.write(loader)
    if version:
        with open(os.path.join(server_dir, ".mc_version"), "w") as f:
            f.write(version)
    return web.json_response({"ok": True})

async def mc_api_stop(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "")
    proc = mc_processes.get(name)
    if not proc or proc.returncode is not None:
        return web.json_response({"error": "Not running"}, status=400)
    try:
        proc.stdin.write(b"stop\n")
        await proc.stdin.drain()
    except Exception:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
    mc_processes[name] = None
    return web.json_response({"ok": True})

async def mc_api_command(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "")
    cmd = data.get("command", "")
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
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    name = request.query.get("name", "")
    buf = mc_console_buffers.get(name, [])
    return web.json_response({"lines": buf[-200:]})


async def mc_api_mods(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    name = request.query.get("name", "")
    server_dir = os.path.join(MC_DIR, name)
    mods_dir = os.path.join(server_dir, "mods")
    if not os.path.isdir(mods_dir):
        return web.json_response({"mods": []})
    mods = []
    for f in sorted(os.listdir(mods_dir)):
        if f.endswith(".jar"):
            fp = os.path.join(mods_dir, f)
            mods.append({"name": f, "size": os.path.getsize(fp)})
    return web.json_response({"mods": mods})

async def mc_api_icon(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    server_dir = _mc_safe_server_dir(request.query.get("name", ""))
    if not server_dir:
        return web.json_response({"error": "Server not found"}, status=404)
    icon_path = os.path.join(server_dir, "server-icon.png")
    if not os.path.isfile(icon_path):
        return web.Response(status=404)
    return web.FileResponse(icon_path, headers={"Cache-Control": "no-cache"})

async def mc_api_upload_icon(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    reader = await request.multipart()
    server_name = None
    icon_data = None
    while True:
        field = await reader.next()
        if not field:
            break
        if field.name == "server":
            server_name = (await field.read()).decode().strip()
        elif field.name == "icon":
            if not field.filename or not field.filename.lower().endswith(".png"):
                return web.json_response({"error": "Upload a PNG image."}, status=400)
            icon_data = await field.read()
    server_dir = _mc_safe_server_dir(server_name or "")
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
    with open(os.path.join(server_dir, "server-icon.png"), "wb") as f:
        f.write(icon_data)
    return web.json_response({"ok": True})


async def mc_api_upload_mod(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    reader = await request.multipart()
    server_name = None
    filename = None
    temp_path = None
    max_size = 512 * 1024 * 1024
    while True:
        part = await reader.next()
        if not part:
            break
        if part.name == "file":
            filename = os.path.basename(part.filename or "")
            if not filename or not filename.lower().endswith(".jar"):
                return web.json_response({"error": "Only .jar files allowed"}, status=400)
            os.makedirs(MC_DIR, exist_ok=True)
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
                            os.remove(temp_path)
                            return web.json_response({"error": "Mod must be smaller than 512 MB."}, status=400)
                        temp_file.write(chunk)
            except Exception:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        elif part.name == "server":
            server_name = (await part.read()).decode()
    server_name = (server_name or "").strip()
    server_dir = _mc_safe_server_dir(server_name)
    if not server_dir:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        return web.json_response({"error": "Server not found"}, status=404)
    if not filename or not temp_path or not os.path.isfile(temp_path):
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        return web.json_response({"error": "No mod file"}, status=400)
    mods_dir = os.path.join(server_dir, "mods")
    os.makedirs(mods_dir, exist_ok=True)
    dest = os.path.join(mods_dir, filename)
    os.replace(temp_path, dest)
    buf = mc_console_buffers.setdefault(server_name, [])
    buf.append(f"[{time.strftime('%H:%M:%S')}] Mod installed: {filename}")
    return web.json_response({"ok": True, "name": filename})


async def mc_api_delete_mod(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "")
    mod = data.get("mod", "")
    if not name or not mod:
        return web.json_response({"error": "Missing params"}, status=400)
    mod_path = os.path.join(MC_DIR, name, "mods", mod)
    if not os.path.isfile(mod_path):
        return web.json_response({"error": "Mod not found"}, status=404)
    os.remove(mod_path)
    buf = mc_console_buffers.setdefault(name, [])
    buf.append(f"[{time.strftime('%H:%M:%S')}] Mod removed: {mod}")
    return web.json_response({"ok": True})

async def mc_ws_console(request):
    name = request.match_info.get("name", "")
    if not _is_mc_admin(request):
        return web.Response(status=401)
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_list = mc_ws_clients.setdefault(name, [])
    ws_list.append(ws)
    buf = mc_console_buffers.get(name, [])
    for line in buf[-100:]:
        try:
            await ws.send_json({"type": "output", "line": line})
        except Exception:
            break
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    d = json.loads(msg.data)
                    if d.get("type") == "command":
                        proc = mc_processes.get(name)
                        if proc and proc.returncode is None and proc.stdin:
                            proc.stdin.write((d.get("command", "") + "\n").encode("utf-8"))
                            await proc.stdin.drain()
                except Exception:
                    pass
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
.console-header .actions .props{border-color:rgba(0,220,120,.25);color:#00dc78}
.console-header .actions .props:hover{background:rgba(0,220,120,.08);border-color:rgba(0,220,120,.35);color:#00dc78}
.console-header .actions .version{border-color:rgba(168,85,247,.3);color:#c084fc}
.console-header .actions .version:hover{background:rgba(168,85,247,.1);color:#d8b4fe}
.console-header .actions .delete{border-color:rgba(239,68,68,.3);color:#f87171}
.console-header .actions .delete:hover{background:rgba(239,68,68,.1);color:#fca5a5}
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
@media(max-width:480px){
  .sidebar{width:160px}
  .sidebar .title{font-size:10px}
  .server-item .name{font-size:12px}
}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-glow"></div>
<div class="topbar">
  <div class="logo"><span class="lua">MC</span> Console</div>
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
  var createEl=document.getElementById('createServer');if(createEl)createEl.style.display='';
  return true;
}
function loadServers(){
  fetch('/api/mc/servers',{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('serverList');if(!el)return;el.innerHTML='';
    (d.servers||[]).forEach(function(s){
      var item=document.createElement('div');
      item.className='server-item'+(activeServer===s.name?' active':'');
       var icon=s.icon?'<img class="server-icon" src="/api/mc/icon?name='+encodeURIComponent(s.name)+'" alt="">':'';
       item.innerHTML=icon+'<span class="name">'+s.name+'<small style="display:block;color:rgba(255,255,255,.35);font-size:10px">'+(s.version||'auto')+'</small></span><span class="loader-tag">'+s.loader+'</span><span class="dot '+(s.running?'on':'off')+'"></span>';
      item.onclick=function(){selectServer(s.name)};
      el.appendChild(item);
    });
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
    inp.value='';loadServers();selectServer(name);
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
var lastLineCount=0;
var lastWSServer='';
function selectServer(name){
  activeServer=name;
  autoScroll=true;
  modsVisible=false;
  loadServers();
  if(lastWSServer!==name){connectWS(name);lastWSServer=name;lastLineCount=0}
  fetch('/api/mc/console?name='+encodeURIComponent(name),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var wrap=document.getElementById('consoleWrap');
    var out=document.getElementById('consoleOutput');
    if(out&&out.dataset.server===name){
      var lines=d.lines||[];
      if(lines.length>lastLineCount){
        for(var i=lastLineCount;i<lines.length;i++)appendLine(out,lines[i],false);
        lastLineCount=lines.length;
        if(autoScroll)out.scrollTop=out.scrollHeight;
      }
      document.getElementById('cmdInput').focus();
      return;
    }
     wrap.innerHTML='<div class="console-header"><div class="server-name">'+name+'</div><div class="actions"><button class="start" onclick="startServer()">Start</button><button class="stop" onclick="stopServer()">Stop</button><button class="mods" id="modsBtn" onclick="toggleMods()">Mods</button><button class="props" onclick="openProps()">Properties</button><button class="version" onclick="changeServerVersion()">Version</button><button class="icon-upload" onclick="document.getElementById(\'serverIconInput\').click()">Icon</button><button class="delete" onclick="deleteServer()">Delete</button><input id="serverIconInput" type="file" accept="image/*" style="display:none" onchange="openIconCrop(this)"></div></div><div class="mods-panel" id="modsPanel" style="display:none"></div><div class="console-output" id="consoleOutput"></div><div class="console-input-wrap"><span class="prompt">\u003e</span><input type="text" id="cmdInput" placeholder="Type a command..." onkeydown="if(event.key===\'Enter\')sendCmd()"></div>';
    out=document.getElementById('consoleOutput');
    out.dataset.server=name;
    out.addEventListener('scroll',function(){
      var atBottom=out.scrollHeight-out.scrollTop-out.clientHeight<50;
      autoScroll=atBottom;
    });
    var lines=d.lines||[];
    lines.forEach(function(line){appendLine(out,line,false)});
    lastLineCount=lines.length;
    out.scrollTop=out.scrollHeight;
    document.getElementById('cmdInput').focus();
    if(userInfo)loadMods();
  });
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
  if(!activeServer)return;
  fetch('/api/mc/mods?name='+encodeURIComponent(activeServer),{credentials:'include'}).then(function(r){return r.json()}).then(function(d){
    var panel=document.getElementById('modsPanel');if(!panel)return;
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
  var fd=new FormData();fd.append('file',file);fd.append('server',activeServer);
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
function escapeHtml(t){var d=document.createElement('div');d.textContent=t;return d.innerHTML}
function connectWS(name){
  if(ws){ws.close();ws=null}
  var proto=location.protocol==='https:'?'wss:':'ws:';
  ws=new WebSocket(proto+'//'+location.host+'/ws/mc/'+encodeURIComponent(name));
  ws.onmessage=function(ev){
    try{
      var d=JSON.parse(ev.data);
      if(d.type==='output'){
        var out=document.getElementById('consoleOutput');
        if(out)appendLine(out,d.line,true);
      }
    }catch(e){}
  };
  ws.onclose=function(){setTimeout(function(){if(activeServer===name&&lastWSServer===name)connectWS(name)},3000)};
}
function sendCmd(){
  var inp=document.getElementById('cmdInput');
  if(!inp||!ws)return;
  var cmd=inp.value.trim();
  if(!cmd)return;
  ws.send(JSON.stringify({type:'command',command:cmd}));
  inp.value='';
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
    if(ws){ws.close();ws=null}activeServer=null;lastWSServer='';loadServers();
    document.getElementById('consoleWrap').innerHTML='<div class="no-servers" id="noSelect">Select a server</div>';
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
checkAuth().then(function(ok){
  if(ok){
    loadVersions();
    loadServers();
  }
});
function openProps(){
  if(!activeServer)return;
  fetch('/api/mc/properties?name='+encodeURIComponent(activeServer),{credentials:'include'})
    .then(r=>r.json()).then(d=>{
      if(d.properties){
        buildPropsEditor(d.properties);
        document.getElementById('propsPanel').style.display='block';
      }
    });
}
function buildPropsEditor(props){
  propsCache=JSON.parse(JSON.stringify(props||{}));
  var html='<h3 style="color:#00dc78;margin-bottom:12px">Server Properties</h3>';
  for(var k in props){
    var p=props[k];
    if(p.type==='bool'){
      html+='<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:rgba(13,17,23,.6);border-radius:8px;margin-bottom:6px;border:1px solid rgba(0,220,120,.1)">';
      html+='<span style="color:#c9d1d9;font-size:13px">'+k+'</span>';
      html+='<label class="toggle"><input type="checkbox" '+(p.value==='true'?'checked':'')+' onchange="setProp(\''+k+'\',this.checked)"><span class="toggle-slider"></span></label>';
      html+='</div>';
    } else if(p.type==='number'){
      html+='<div style="margin-bottom:6px"><label style="color:#8b949e;font-size:12px">'+k+'</label>';
      html+='<input type="number" value="'+p.value+'" style="width:100%;background:#0d1117;border:1px solid rgba(255,255,255,.06);color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:13px;margin-top:4px;outline:none" onchange="setProp(\''+k+'\',this.value)" oninput="this.value=this.value.replace(/[^0-9\-.]/g,\'\')"></div>';
    } else {
      html+='<div style="margin-bottom:6px"><label style="color:#8b949e;font-size:12px">'+k+'</label>';
      html+='<input type="text" value="'+p.value+'" style="width:100%;background:#0d1117;border:1px solid rgba(255,255,255,.06);color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:13px;margin-top:4px;outline:none" onchange="setProp(\''+k+'\',this.value)"></div>';
    }
  }
  html+='<button onclick="saveProps()" style="margin-top:12px;width:100%;background:#00dc78;color:#050508;padding:10px;border:none;border-radius:8px;font-weight:600;cursor:pointer">Save Properties</button>';
  document.getElementById('propsContent').innerHTML=html;
}
function setProp(k,v){
  if(typeof v==='boolean'){
    propsCache[k].value=v?'true':'false';
  } else {
    propsCache[k].value=v;
  }
}
var propsCache={};
function saveProps(){
  if(!activeServer)return;
  fetch('/api/mc/properties',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({name:activeServer,properties:propsCache})})
    .then(r=>r.json()).then(d=>{if(d.ok)alert('Saved!')});
}
</script>
<div id="propsPanel" style="display:none;position:fixed;top:0;right:0;width:360px;height:100vh;background:rgba(5,5,8,.95);border-left:1px solid rgba(0,220,120,.15);z-index:200;overflow-y:auto;padding:20px;backdrop-filter:blur(12px)">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <h2 style="color:#fff;font-size:16px">Server Properties</h2>
    <button onclick="document.getElementById('propsPanel').style.display='none'" style="background:none;border:none;color:#8b949e;font-size:20px;cursor:pointer">X</button>
  </div>
  <div id="propsContent"></div>
</div>
</body>
</html>'''

async def handle_mc_page(request):
    return web.Response(text=MC_PAGE_HTML, content_type="text/html")


def _parse_properties(server_dir):
    props_path = os.path.join(server_dir, "server.properties")
    props = {}
    if os.path.exists(props_path):
        with open(props_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if v.lower() in ("true", "false"):
                        props[k] = {"value": v, "type": "bool"}
                    else:
                        try:
                            int(v)
                            props[k] = {"value": v, "type": "number"}
                        except ValueError:
                            try:
                                float(v)
                                props[k] = {"value": v, "type": "number"}
                            except ValueError:
                                props[k] = {"value": v, "type": "string"}
    return props

def _save_properties(server_dir, props):
    props_path = os.path.join(server_dir, "server.properties")
    with open(props_path, "w") as f:
        for k, v in props.items():
            f.write(f"{k}={v['value']}\n")

async def mc_api_properties(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    name = request.query.get("name", "")
    server_dir = os.path.join(MC_DIR, name)
    props_path = os.path.join(server_dir, "server.properties")
    if not os.path.exists(props_path):
        return web.json_response({"error": "server.properties not found"}, status=404)
    props = _parse_properties(server_dir)
    return web.json_response({"properties": props})

async def mc_api_set_properties(request):
    if not _is_mc_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await request.json()
    name = data.get("name", "")
    server_dir = os.path.join(MC_DIR, name)
    props = data.get("properties", {})
    _save_properties(server_dir, props)
    return web.json_response({"ok": True})

async def start_local_server(host="127.0.0.1", port=5000):
    app = web.Application()
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
    app.router.add_get("/api/mc/servers", mc_api_servers)
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
    app.router.add_get("/api/mc/properties", mc_api_properties)
    app.router.add_post("/api/mc/properties", mc_api_set_properties)
    app.router.add_get("/{filename}", handle_download)
    runner = web.AppRunner(app)
    await runner.setup()
    for p in range(port, port + 10):
        try:
            site = web.TCPSite(runner, host, p)
            await site.start()
            print(f"[DEBUG] Local HTTP server listening on http://{host}:{p}/decompile")
            return
        except OSError:
            continue
    print(f"[DEBUG] Failed to bind to any port in range {port}-{port+9}")

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
    games_json = os.path.join(storage_dir, "games.json")
    try:
        shutil.copy2(file_path, dest)
        entries = []
        if os.path.exists(games_json):
            with open(games_json, "r") as f:
                entries = json.load(f)
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
        with open(games_json, "w") as f:
            json.dump(entries, f, indent=2)
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

    blacklisted_games = load_blacklisted_games()
    if str(place_id).strip() in blacklisted_games:
        reason = blacklisted_games[str(place_id).strip()]
        game_info = await get_place_info(place_id, cookie=user_cookie)
        game_name = game_info.get("name") if not game_info.get("error") else f"Place {place_id}"

        await send_msg(send_func, f"**Unable to decompile game:** {game_name}\n**Reason:** {reason}", ephemeral=is_ephemeral)
        return

    await enqueue_decompile_task(send_func, user, guild, channel, place_id, game_id, is_ephemeral, on_status_update=on_status_update, user_cookie=user_cookie, raw=raw)

async def execute_decompile_job(send_func, author_id: int, guild, channel, place_id: str, game_id: str = None, is_ephemeral: bool = False, is_priority: bool = False, resume_info_msg=None, resume_embed=None, on_status_update=None, cookie_retries=0, original_cookie_index=None, user_cookie: str = None, cookie_ban_msg=None, raw: bool = False):
    global is_decompiling, running_jobs, active_events, current_active_data, active_cookie_index, default_cookie_index
    is_retry = False
    saved_user_cookie = None
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

    if resume_info_msg is not None and resume_embed is not None:
        info_msg = resume_info_msg
        embed = resume_embed
        game_name = embed.title
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

                    download_view = DownloadView(download_url)
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
_screen_share_task = None
_mss = None

async def screenshare_auto_join():
    if mss_lib is None:
        print("[SCREENSHARE] mss not installed, skipping.")
        return
    global _screen_share_task, _mss
    while True:
        await asyncio.sleep(30)
        try:
            guild = bot.get_guild(EVENT_GUILD_ID)
            if not guild:
                continue
            channel = guild.get_channel(EVENT_CHANNEL_ID)
            if not channel:
                continue
            voice_state = guild.me.voice
            if voice_state and voice_state.channel and voice_state.channel.id == channel.id:
                if not guild.me.guild_permissions.speak:
                    try:
                        await channel.permission_synced
                        await channel.edit(slowmode_delay=0)
                    except Exception:
                        pass
                continue
            if voice_state and voice_state.channel:
                try:
                    await voice_state.channel.disconnect()
                except Exception:
                    pass
            try:
                vc = await channel.connect()
                print(f"[SCREENSHARE] Joined voice channel {channel.name}")
            except Exception as e:
                print(f"[SCREENSHARE] Failed to join: {e}")
                continue
            if not guild.me.guild_permissions.speak:
                try:
                    await channel.edit(slowmode_delay=0)
                    await vc.edit(speak=True, deafen=False)
                    print(f"[SCREENSHARE] Requested speak permission")
                except Exception as e:
                    print(f"[SCREENSHARE] Failed to request speak: {e}")
                    continue
            _screen_share_task = asyncio.create_task(screenshare_loop(vc))
        except Exception as e:
            print(f"[SCREENSHARE] Auto-join error: {e}")
            await asyncio.sleep(10)

async def screenshare_loop(vc):
    global _mss
    if mss_lib is None:
        print("[SCREENSHARE] mss not installed, screenshare disabled.")
        return
    try:
        _mss = mss_lib()
        monitor = _mss.monitors[1]
        while True:
            try:
                if not vc or not vc.is_connected():
                    print("[SCREENSHARE] Voice client disconnected, stopping.")
                    break
                img = _mss.grab(monitor)
                img_bytes = bytes(img.rgb)
                import wave
                import struct
                width = monitor["width"]
                height = monitor["height"]
                frame_size = width * height * 3
                data = bytearray()
                for y in range(0, height, 2):
                    for x in range(0, width, 2):
                        idx = (y * width + x) * 3
                        r = img_bytes[idx]
                        g = img_bytes[idx + 1]
                        b = img_bytes[idx + 2]
                        gray = int(0.299 * r + 0.587 * g + 0.114 * b)
                        data.extend(struct.pack('<h', gray - 128))
                if data:
                    try:
                        import numpy as np
                        audio_data = np.array(list(data), dtype=np.int16).tobytes()
                        await vc.send(audio_data)
                    except Exception:
                        pass
                await asyncio.sleep(1/60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[SCREENSHARE] Frame error: {e}")
                await asyncio.sleep(0.1)
    except Exception as e:
        print(f"[SCREENSHARE] Loop error: {e}")
    finally:
        if _mss:
            _mss.close()
        print("[SCREENSHARE] Stopped.")

async def start_screenshare_on_ready():
    await bot.wait_until_ready()
    asyncio.create_task(screenshare_auto_join())
