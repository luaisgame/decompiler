import asyncio
import base64
from urllib.parse import parse_qs, urlparse, quote
import os
import sys
import random
import string
import shutil
import uuid
import subprocess
import time
import json
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
        return None
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
        encrypted = base64.b64decode(data["CookiesData"])
        decrypted = _dpapi_unprotect(encrypted)
        return decrypted.decode("utf-8", errors="replace") if decrypted else None
    except Exception:
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
        return False
    result = _write_roblox_cookies("\n".join(new_lines))
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
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))
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

async def get_place_info(place_id: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if get_active_cookie():
        headers["Cookie"] = f".ROBLOSECURITY={get_active_cookie()}"
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            universe_url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
            async with session.get(universe_url) as resp:
                if resp.status in (403, 404):
                    return {"error": True, "reason": "Place is banned, content-deleted, or moderated by Roblox."}
                if resp.status != 200:
                    return {"error": True, "reason": f"Roblox API returned status code {resp.status}."}
                data = await resp.json()
                if not isinstance(data, dict):
                    return {"error": True, "reason": "Invalid response from universe resolution API."}
                universe_id = data.get("universeId")
            if not universe_id:
                return {"error": True, "reason": "Universe ID not found for this place."}
            if get_active_cookie():
                play_url = f"https://games.roblox.com/v1/games/multiget-playability-status?universeIds={universe_id}"
                async with session.get(play_url) as play_resp:
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
                                    "reason": f"Game is unplayable: {ban_reason}"
                                }
            game_name = f"Place {place_id}"
            details_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
            async with session.get(details_url) as resp:
                if resp.status == 200:
                    details_data = await resp.json()
                    if isinstance(details_data, dict):
                        games = details_data.get("data", [])
                        if games and isinstance(games, list):
                            game = games[0] or {}
                            if game.get("isArchived", False):
                                return {"error": True, "reason": "Game has been archived or deleted."}
                            game_name = game.get("name", game_name)
            icon_url = None
            thumb_url = f"https://thumbnails.roblox.com/v1/places/gameicons?placeIds={place_id}&size=512x512&format=Png&isCircular=false"
            async with session.get(thumb_url) as thumb_resp:
                if thumb_resp.status == 200:
                    thumb_data = await thumb_resp.json()
                    if isinstance(thumb_data, dict):
                        data_list = thumb_data.get("data", [])
                        if data_list and isinstance(data_list, list):
                            icon_url = data_list[0].get("imageUrl")
            return {
                "error": False,
                "name": game_name,
                "icon_url": icon_url
            }
        except Exception as e:
            print(f"[DEBUG] Error fetching Roblox place info: {e}")
            return {"error": True, "reason": f"Exception occurred: {str(e)}"}

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
    state = uuid.uuid4().hex
    resp = web.Response(status=302)
    resp.headers["Location"] = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify"
        f"&state={state}"
    )
    resp.set_cookie("oauth_state", state, max_age=600, samesite="Lax")
    return resp

async def handle_discord_callback(request):
    code = request.query.get("code")
    if not code:
        return web.Response(text="No code provided", status=400)
    async with aiohttp.ClientSession() as session:
        token_resp = await session.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = await token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return web.Response(text="Failed to get token", status=400)
        user_resp = await session.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_data = await user_resp.json()
    user_id = user_data.get("id", "")
    username = user_data.get("username", "")
    avatar = user_data.get("avatar", "")
    resp = web.Response(status=302)
    resp.headers["Location"] = "/"
    user_info = json.dumps({"id": user_id, "username": username, "avatar": avatar})
    resp.set_cookie("user_info", user_info, max_age=86400 * 30, samesite="Lax")
    return resp

def _get_user_info(request):
    raw = request.cookies.get("user_info", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _is_admin(request):
    user = _get_user_info(request)
    if not user:
        return False
    return int(user.get("id", 0)) == BOT_OWNER_ID

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
                thumb_url = tdata.get("data", [{}])[0].get("imageUrl", "")
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
                <img class="game-thumb" src="/api/thumb?placeId={e.get("place_id","")}" alt="thumb" onerror="this.style.display='none'">
                <div class="game-info">
                    <div class="game-title">{e.get("game_name","Unknown")}</div>
                    <div class="game-meta">
                        <span class="label">Place ID:</span> <span class="value">{e.get("place_id","")}</span>
                        <span class="label">Version:</span> <span class="value">{e.get("game_version","N/A")}</span>
                        <span class="label">Requested by:</span> <span class="value">{e.get("display_name","Unknown")} ({e.get("user_id","")})</span>
                        <span class="label">Downloaded:</span> <span class="value">{e.get("timestamp","")}</span>
                    </div>
                    <div class="game-buttons">
                        <a class="download-btn" href="/{e.get("filename","")}" download>Download</a>
                        <button class="copy-btn" onclick="copyUrl(this)" data-url="https://storage.luaisgame.com/{e.get("filename","")}">Copy Link</button>
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
                    <button class="tab active" onclick="showTab('main', this)">Main</button>
                    <button class="tab" onclick="showTab('website', this)">Website</button>
                    <button class="tab" onclick="showTab('downloads', this)">Downloads</button>
                </div>
            </div>
            <div class="tab-content" id="tab-main">
                <div class="admin-section">
                    <h3>Tracked IPs</h3>
                    <div id="ipList" class="ip-list"></div>
                </div>
            </div>
            <div class="tab-content hidden" id="tab-website">
                <div class="admin-section">
                    <h3>Banned IPs</h3>
                    <div id="banList" class="ip-list"></div>
                    <div class="ban-form">
                        <input type="text" id="banIpInput" placeholder="IP to ban/unban">
                        <button class="btn-ban" onclick="banIp()">Ban</button>
                        <button class="btn-unban" onclick="unbanIp()">Unban</button>
                    </div>
                </div>
            </div>
            <div class="tab-content hidden" id="tab-downloads">
                <div class="admin-section">
                    <h3>Recent Downloads</h3>
                    <div id="downloadList" class="ip-list"></div>
                </div>
            </div>
        </div>'''
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lua is game</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0e14; color:#c9d1d9; font-family:'Inter','SF Pro Display',system-ui,-apple-system,sans-serif; min-height:100vh; }}
.header {{ background:linear-gradient(135deg,#0d1117 0%,#161b22 100%); padding:20px 40px; border-bottom:1px solid #30363d; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:100; backdrop-filter:blur(10px); }}
.header-left {{ display:flex; align-items:center; gap:20px; }}
.header-right {{ display:flex; align-items:center; gap:12px; }}
.logo {{ font-size:26px; font-weight:700; letter-spacing:-0.5px; display:flex; align-items:center; gap:8px; }}
.roblox-icon {{ width:32px; height:32px; }}
.lua {{ color:#58a6ff; }} .is {{ color:#8b949e; }} .game {{ color:#3fb950; }}
.search {{ background:#0d1117; border:1px solid #30363d; color:#c9d1d9; padding:10px 16px; border-radius:8px; width:320px; font-size:14px; outline:none; transition:border-color 0.2s; }}
.search:focus {{ border-color:#58a6ff; box-shadow:0 0 0 3px rgba(88,166,255,0.15); }}
.btn {{ padding:10px 20px; border-radius:8px; border:none; font-size:13px; font-weight:600; cursor:pointer; transition:all 0.2s; text-decoration:none; display:inline-flex; align-items:center; gap:6px; }}
.btn-primary {{ background:#58a6ff; color:#0d1117; }}
.btn-primary:hover {{ background:#79b8ff; transform:translateY(-1px); }}
.btn-secondary {{ background:#21262d; color:#c9d1d9; border:1px solid #30363d; }}
.btn-secondary:hover {{ background:#30363d; }}
.btn-discord {{ background:#5865F2; color:#fff; }}
.btn-discord:hover {{ background:#4752C4; transform:translateY(-1px); }}
.container {{ max-width:1200px; margin:30px auto; padding:0 20px; }}
.game-card {{ background:linear-gradient(135deg,#161b22 0%,#1c2333 100%); border:1px solid #30363d; border-radius:12px; padding:0; margin-bottom:16px; transition:all 0.3s; overflow:hidden; }}
.game-card:hover {{ border-color:#58a6ff; transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,0.3); }}
.game-card-inner {{ display:flex; gap:0; }}
.game-thumb {{ width:180px; height:180px; object-fit:cover; border-radius:12px 0 0 12px; flex-shrink:0; background:#0d1117; }}
.game-info {{ padding:20px; flex:1; display:flex; flex-direction:column; justify-content:center; }}
.game-title {{ font-size:20px; font-weight:600; color:#f0f6fc; margin-bottom:12px; }}
.game-meta {{ font-size:13px; color:#8b949e; margin-bottom:16px; line-height:2; }}
.label {{ color:#58a6ff; font-weight:500; }}
.value {{ color:#c9d1d9; margin-right:16px; }}
.download-btn {{ display:inline-block; background:linear-gradient(135deg,#238636,#2ea043); color:#fff; padding:10px 20px; border-radius:8px; text-decoration:none; font-size:13px; font-weight:600; transition:all 0.2s; }}
.download-btn:hover {{ transform:translateY(-1px); box-shadow:0 4px 12px rgba(35,134,54,0.4); }}
.game-buttons {{ display:flex; gap:8px; }}
.copy-btn {{ background:#21262d; color:#c9d1d9; border:1px solid #30363d; padding:10px 20px; border-radius:8px; font-size:13px; font-weight:600; cursor:pointer; transition:all 0.2s; }}
.copy-btn:hover {{ background:#30363d; border-color:#58a6ff; }}
.count {{ color:#8b949e; font-size:14px; margin-bottom:20px; }}
.footer {{ text-align:center; padding:30px; color:#484f58; font-size:13px; border-top:1px solid #21262d; margin-top:40px; }}
.footer a {{ color:#58a6ff; text-decoration:none; }}
.admin-panel {{ background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; margin-bottom:24px; }}
.admin-header {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }}
.admin-title {{ font-size:18px; font-weight:600; color:#f0f6fc; }}
.admin-tabs {{ display:flex; gap:8px; }}
.tab {{ padding:8px 16px; border-radius:6px; border:1px solid #30363d; background:#0d1117; color:#8b949e; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.2s; }}
.tab.active {{ background:#58a6ff; color:#0d1117; border-color:#58a6ff; }}
.tab:hover:not(.active) {{ border-color:#58a6ff; }}
.tab-content.hidden {{ display:none; }}
.admin-section h3 {{ font-size:14px; color:#8b949e; margin-bottom:12px; font-weight:500; }}
.ip-list {{ max-height:300px; overflow-y:auto; }}
.ip-item {{ display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:#0d1117; border:1px solid #21262d; border-radius:8px; margin-bottom:8px; font-size:13px; }}
.ip-item .ip {{ color:#58a6ff; font-family:monospace; }}
.ip-item .meta {{ color:#484f58; font-size:12px; }}
.ban-form {{ display:flex; gap:8px; margin-top:12px; }}
.ban-form input {{ background:#0d1117; border:1px solid #30363d; color:#c9d1d9; padding:8px 12px; border-radius:6px; font-size:13px; outline:none; flex:1; }}
.ban-form input:focus {{ border-color:#58a6ff; }}
.btn-ban {{ background:#da3633; color:#fff; padding:8px 16px; border:none; border-radius:6px; cursor:pointer; font-weight:500; font-size:13px; }}
.btn-ban:hover {{ background:#f85149; }}
.btn-unban {{ background:#238636; color:#fff; padding:8px 16px; border:none; border-radius:6px; cursor:pointer; font-weight:500; font-size:13px; }}
.btn-unban:hover {{ background:#2ea043; }}
.login-overlay {{ position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:1000; }}
.login-overlay.active {{ display:flex; }}
.login-box {{ background:#161b22; border:1px solid #30363d; border-radius:12px; padding:32px; width:360px; text-align:center; }}
.login-box h2 {{ color:#f0f6fc; margin-bottom:20px; font-size:20px; }}
.login-box input {{ width:100%; background:#0d1117; border:1px solid #30363d; color:#c9d1d9; padding:12px 16px; border-radius:8px; font-size:14px; outline:none; margin-bottom:16px; }}
.login-box input:focus {{ border-color:#58a6ff; }}
.login-box .btn {{ width:100%; justify-content:center; }}
.pulse {{ animation:pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.5; }} }}
</style>
</head>
<body>
<div class="header">
    <div class="header-left">
        <div class="logo"><img src="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png" class="roblox-icon" alt="Roblox"><span class="lua">Lua</span> <span class="is">is</span> <span class="game">game</span></div>
        <input class="search" type="text" placeholder="Search games..." id="search" oninput="filterGames()">
    </div>
    <div class="header-right">
        <a class="btn btn-primary" href="https://discord.com/api/oauth2/authorize?client_id=1532820804402806844&permissions=8&scope=bot%20applications.commands" target="_blank">Add Bot</a>
        {"<button class='btn btn-secondary' onclick='toggleAdmin()'>Console</button>" if admin else ""}
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
function copyUrl(btn) {{
    navigator.clipboard.writeText(btn.dataset.url);
    var orig = btn.textContent;
    btn.textContent = "Copied!";
    btn.style.background = "#238636";
    btn.style.color = "#fff";
    btn.style.borderColor = "#238636";
    setTimeout(function() {{ btn.textContent = orig; btn.style.background = ""; btn.style.color = ""; btn.style.borderColor = ""; }}, 2000);
}}
function filterGames() {{
    var q = document.getElementById("search").value.toLowerCase();
    document.querySelectorAll(".game-card").forEach(function(c) {{
        c.style.display = (c.dataset.name.includes(q) || c.dataset.user.includes(q)) ? "" : "none";
    }});
}}
function toggleAdmin() {{
    var p = document.getElementById("adminPanel");
    if (!p) return;
    if (p.style.display === "none" || p.style.display === "") {{
        p.style.display = "block";
        loadAdmin();
    }} else {{
        p.style.display = "none";
    }}
}}
async function loadAdmin() {{
    var r = await fetch("/api/admin");
    if (!r.ok) return;
    var d = await r.json();
    var ipHtml = "";
    (d.ips||[]).forEach(function(e) {{
        var banned = (d.banned||[]).includes(e.ip);
        ipHtml += '<div class="ip-item"><span class="ip">' + e.ip + (banned ? ' <span style="color:#da3633">(BANNED)</span>' : '') + '</span><span class="meta">Visits: ' + e.visits + ' | Last: ' + e.last_seen + '</span></div>';
    }});
    document.getElementById("ipList").innerHTML = ipHtml || "<p style='color:#484f58'>No IPs tracked yet</p>";
    var banHtml = "";
    (d.banned||[]).forEach(function(ip) {{
        banHtml += '<div class="ip-item"><span class="ip">' + ip + '</span><span class="meta">Banned</span></div>';
    }});
    document.getElementById("banList").innerHTML = banHtml || "<p style='color:#484f58'>No banned IPs</p>";
    var dlHtml = "";
    (d.downloads||[]).forEach(function(e) {{
        dlHtml += '<div class="ip-item"><span class="ip">' + e.filename + '</span><span class="meta">From: ' + e.ip + ' | ' + e.timestamp + '</span></div>';
    }});
    document.getElementById("downloadList").innerHTML = dlHtml || "<p style='color:#484f58'>No downloads yet</p>";
}}
async function banIp() {{
    var ip = document.getElementById("banIpInput").value;
    if (!ip) return;
    await fetch("/api/ban", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{ip:ip, action:"ban"}})}});
    document.getElementById("banIpInput").value = "";
    loadAdmin();
}}
async function unbanIp() {{
    var ip = document.getElementById("banIpInput").value;
    if (!ip) return;
    await fetch("/api/ban", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{ip:ip, action:"unban"}})}});
    document.getElementById("banIpInput").value = "";
    loadAdmin();
}}
function showTab(name, btn) {{
    document.querySelectorAll(".tab-content").forEach(function(t) {{ t.classList.add("hidden"); }});
    document.querySelectorAll(".tab").forEach(function(t) {{ t.classList.remove("active"); }});
    document.getElementById("tab-"+name).classList.remove("hidden");
    if (btn) btn.classList.add("active");
}}
setInterval(function() {{
    fetch("/games.json").then(function(r) {{ return r.json(); }}).then(function(data) {{
        var c = document.getElementById("games");
        if (!c) return;
        var h = "";
        data.forEach(function(e) {{
            h += '<div class="game-card" data-name="'+(e.game_name||'').toLowerCase()+'" data-user="'+(e.display_name||'').toLowerCase()+'"><div class="game-card-inner"><img class="game-thumb" src="/api/thumb?placeId='+e.place_id+'" alt="thumb" onerror="this.style.display=\'none\'"><div class="game-info"><div class="game-title">'+(e.game_name||'Unknown')+'</div><div class="game-meta"><span class="label">Place ID:</span> <span class="value">'+e.place_id+'</span><span class="label">Version:</span> <span class="value">'+(e.game_version||'N/A')+'</span><span class="label">Requested by:</span> <span class="value">'+(e.display_name||'Unknown')+' ('+e.user_id+')</span><span class="label">Downloaded:</span> <span class="value">'+e.timestamp+'</span></div><div class="game-buttons"><a class="download-btn" href="/'+e.filename+'" download>Download</a><button class="copy-btn" onclick="copyUrl(this)" data-url="https://storage.luaisgame.com/'+e.filename+'">Copy Link</button></div></div></div></div>';
        }});
        c.innerHTML = h;
        document.querySelector(".count").textContent = data.length + " game(s) decompiled";
    }});
}}, 5000);
if (document.getElementById("adminPanel")) {{ setInterval(loadAdmin, 3000); }}
</script>
</body>
</html>'''
    return web.Response(text=html, content_type="text/html")

async def start_local_server(host="127.0.0.1", port=5000):
    app = web.Application()
    app.router.add_post("/decompile", handle_post)
    app.router.add_get("/games.json", handle_games_json)
    app.router.add_get("/api/thumb", handle_thumbnail)
    app.router.add_get("/api/auth/login", handle_discord_auth)
    app.router.add_get("/api/auth/callback", handle_discord_callback)
    app.router.add_get("/api/admin", handle_admin_data)
    app.router.add_post("/api/ban", handle_ban)
    app.router.add_get("/", handle_index)
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

def _upload_file_sync(file_path: str, place_id: str, game_name: str = None, user_id: str = None, display_name: str = None, game_version: str = None) -> str | None:
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
            "game_version": game_version or "N/A"
        })
        with open(games_json, "w") as f:
            json.dump(entries, f, indent=2)
        return f"https://storage.luaisgame.com/{filename}"
    except Exception as e:
        print(f"[DEBUG] Storage copy error: {e}")
        return None

async def upload_file(file_path: str, place_id: str, game_name: str = None, user_id: str = None, display_name: str = None, game_version: str = None) -> dict | None:
    print(f"[DEBUG] Copying {os.path.basename(file_path)} to storage...")
    public_url = await asyncio.to_thread(_upload_file_sync, file_path, place_id, game_name, user_id, display_name, game_version)
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
        game_info = await get_place_info(place_id)
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
        _replace_roblox_security_cookie(user_cookie)
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
        game_info = await get_place_info(place_id)
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

        if not game_info.get("error"):
            game_name = game_info.get("name", f"Place {place_id}")
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

        join_view = JoinGameView(join_url if game_id else None)
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
                    game_version=game_version
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
