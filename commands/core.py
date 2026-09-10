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
queue_file = os.path.join(BASE_DIR, "queue.txt")

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
    return _write_roblox_cookies("\n".join(new_lines))

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

DISABLED_FLAG_FILE = os.path.join(BASE_DIR, "decompile_disabled.txt")
decompile_disabled = os.path.exists(DISABLED_FLAG_FILE)

def set_decompile_disabled(state: bool):
    global decompile_disabled
    decompile_disabled = state
    if state:
        with open(DISABLED_FLAG_FILE, "w") as _f:
            _f.write("disabled")
    elif os.path.exists(DISABLED_FLAG_FILE):
        os.remove(DISABLED_FLAG_FILE)

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

def save_queue():
    lines = [f"COUNTER:{queue_counter}"]
    for it in queue_list:
        d = it.task_data
        lines.append(f"{it.counter}|{it.is_priority}|{d.get('author_id', 0)}|{d.get('place_id', '')}|{d.get('game_id', '') or ''}|{d.get('is_ephemeral', False)}")
    with open(queue_file, "w") as f:
        f.write("\n".join(lines))

def load_queue():
    global queue_counter
    if not os.path.exists(queue_file):
        return
    try:
        with open(queue_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("COUNTER:"):
                    c = int(line.split(":", 1)[1])
                    if c > queue_counter:
                        queue_counter = c
        os.remove(queue_file)
        print(f"[QUEUE] Cleared stale queue entries on startup")
    except Exception as e:
        print(f"[QUEUE] Failed to load queue: {e}")

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

async def enqueue_decompile_task(send_func, user: discord.User | discord.Member, guild, channel, place_id: str, game_id: str, is_ephemeral: bool, on_status_update=None):
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
            save_queue()
            await _renumber_queue(notify_bumps=True)
            await send_msg(send_func, f"<@{author_id}> Priority granted! Your decompile is starting now (a lower-priority job was paused).", ephemeral=is_ephemeral)
        elif is_priority:
            insert_at = len(queue_list)
            for i, it in enumerate(queue_list):
                if not it.is_priority:
                    insert_at = i
                    break
            queue_list.insert(insert_at, item)
            save_queue()
            await _renumber_queue(notify_bumps=True)
            pos = item.task_data["last_pos"]
            await send_msg(send_func, f"<@{author_id}> Added to Priority Queue at position **{pos}**.", ephemeral=is_ephemeral)
        else:
            queue_list.append(item)
            save_queue()
            await _renumber_queue(notify_bumps=True)
            pos = item.task_data["last_pos"]
            await send_msg(send_func, f"<@{author_id}> Added to Queue at position **{pos}**.", ephemeral=is_ephemeral)
    else:
        queue_list.append(item)
        save_queue()
    _get_queue_event().set()
    await item.task_data["future"]

async def decompile_queue_worker():
    while True:
        if not queue_list:
            await _get_queue_event().wait()
            _get_queue_event().clear()
            continue
        item = queue_list.pop(0)
        save_queue()
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
                on_status_update=data.get("on_status_update")
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

async def start_local_server(host="127.0.0.1", port=5000):
    app = web.Application()
    app.router.add_post("/decompile", handle_post)
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

def _upload_file_sync(file_path: str, place_id: str) -> str | None:
    r2_endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=r2_endpoint,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )
    filename = f"{place_id}_{os.path.basename(file_path)}"
    try:
        s3.upload_file(
            Filename=file_path,
            Bucket=R2_BUCKET_NAME,
            Key=filename,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        return f"{R2_PUBLIC_DOMAIN}/{filename}"
    except Exception as e:
        print(f"[DEBUG] R2 Upload Error: {e}")
        return None

async def upload_file(file_path: str, place_id: str) -> dict | None:
    print(f"[DEBUG] Uploading {os.path.basename(file_path)} to Cloudflare R2...")
    public_url = await asyncio.to_thread(_upload_file_sync, file_path, place_id)
    if public_url:
        return {"url": public_url}
    return None

async def send_msg(send_func, content=None, embed=None, ephemeral=False):
    if embed:
        return await send_func(content=content, embed=embed, ephemeral=ephemeral)
    return await send_func(content, ephemeral=ephemeral)

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

async def _wait_with_pause(event, total, job_data):
    step = 0.5
    waited = 0.0
    while waited < total:
        if job_data and job_data.get("aborted"):
            return False
        if event.is_set():
            return True
        await asyncio.sleep(step)
        waited += step
    return event.is_set()

async def process_file(send_func, process, game_name, timeout=60, ephemeral=False, info_msg=None, embed=None, rec_ev=None, fin_ev=None, job_data=None):
    decompile_dir = os.path.join(BASE_DIR, "decompile")
    os.makedirs(decompile_dir, exist_ok=True)

    if rec_ev is None:
        rec_ev = decompile_recieved
    if fin_ev is None:
        fin_ev = decompile_event

    rec_ev.clear()
    fin_ev.clear()

    print("[DEBUG] Waiting for local POST request on http://127.0.0.1:5000/decompile...")

    try:
        if not await _wait_with_pause(rec_ev, 45, job_data):
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
        if not await _wait_with_pause(fin_ev, timeout, job_data):
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

    if SKIP_PROCESSFILE:
        print("[DEBUG] SKIP_PROCESSFILE is enabled; skipping oracle-postprocess post-processor.")
    else:
        proc_script = os.path.join(decompile_dir, "oracle-postprocess.exe")
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

                    returncode = await proc_task.wait()

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

async def run_decompile_logic(send_func, user: discord.User | discord.Member, guild, channel, place_id: str, game_id: str = None, is_ephemeral: bool = False, on_status_update=None):
    try:
        open(os.path.join(BASE_DIR, ".check_update"), "w").close()
    except Exception:
        pass
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

    await enqueue_decompile_task(send_func, user, guild, channel, place_id, game_id, is_ephemeral)

async def execute_decompile_job(send_func, author_id: int, guild, channel, place_id: str, game_id: str = None, is_ephemeral: bool = False, is_priority: bool = False, resume_info_msg=None, resume_embed=None, on_status_update=None, cookie_retries=0, original_cookie_index=None):
    global is_decompiling, running_jobs, active_events, current_active_data, active_cookie_index, default_cookie_index
    is_retry = False
    if original_cookie_index is None:
        original_cookie_index = active_cookie_index
    print(f"\n[DEBUG] --- Executing Decompile Job ---")
    print(f"[DEBUG] Input place_id: {place_id}")
    print(f"[DEBUG] Input game_id: {game_id}")
    print(f"[DEBUG] priority={is_priority}")

    link = game_id if game_id and game_id.startswith("http") else f"https://www.roblox.com/games/{place_id}/about"
    join_url = f"roblox://experiences/start?placeId={place_id}"

    if game_id:
        print(f"[DEBUG] Processing game_id argument...")
        if game_id.startswith("http"):
            parsed_input = urlparse(game_id)
            query_params = parse_qs(parsed_input.query)
            ps_code = query_params.get("privateServerLinkCode", [None])[0]
            share_code = query_params.get("code", [None])[0]
            link_type = query_params.get("type", ["Server"])[0]
            if ps_code:
                launch_json = json.dumps({"psCode": ps_code})
                encoded_launch = quote(launch_json, safe='')
                join_url = f"roblox://experiences/start?placeId={place_id}&launchData={encoded_launch}"
            elif share_code:
                join_url = f"roblox://navigation/share_links?code={quote(share_code)}&type={quote(link_type)}"
        else:
            join_url += f"&gameInstanceId={game_id}"

    print(f"[DEBUG] Final launch join_url: {join_url}")

    if resume_info_msg is not None and resume_embed is not None:
        info_msg = resume_info_msg
        embed = resume_embed
        game_name = embed.title
    else:
        game_info = await get_place_info(place_id)
        if game_info.get("error"):
            error_reason = game_info.get("reason", "")
            if "Game is unplayable" in error_reason:
                cookies = load_cookies()
                if len(cookies) > 1 and cookie_retries < len(cookies):
                    old_index = active_cookie_index
                    active_cookie_index = (active_cookie_index + 1) % len(cookies)
                    if active_cookie_index == old_index:
                        active_cookie_index = 0
                    new_cookie = cookies[active_cookie_index]
                    _replace_roblox_security_cookie(new_cookie)
                    preview = new_cookie[:30] + "..." if len(new_cookie) > 30 else new_cookie
                    print(f"[DEBUG] Banned. Auto-switched to cookie {active_cookie_index}: {preview}")
                    await send_msg(send_func, f"Account banned. Switched to cookie `{active_cookie_index}`. Retrying...", ephemeral=is_ephemeral)
                    await asyncio.sleep(2)
                    is_retry = True
                    await execute_decompile_job(send_func, author_id, guild, channel, place_id, game_id, is_ephemeral, is_priority=is_priority, on_status_update=on_status_update, cookie_retries=cookie_retries + 1, original_cookie_index=original_cookie_index)
                    return
                elif cookie_retries >= len(cookies):
                    await send_msg(send_func, f"<@{str(author_id)}> All cookies are banned or invalid for this game.", ephemeral=is_ephemeral)
                    return
            await send_msg(send_func, f"<@{str(author_id)}> {error_reason}", ephemeral=is_ephemeral)
            return

        game_name = game_info.get("name", f"Place {place_id}")
        icon_url = game_info.get("icon_url")

        embed = discord.Embed(title=game_name, url=link, color=0x3498DB)
        embed.add_field(name="Place ID", value=place_id, inline=True)
        if game_id:
            embed.add_field(name="Job ID", value=game_id, inline=True)
        embed.add_field(name="Link", value=f"[Game Page]({link})", inline=False)
        if icon_url:
            embed.set_thumbnail(url=icon_url)

        info_msg = await send_msg(send_func, embed=embed, ephemeral=is_ephemeral)

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
    
    saved_cookie_index = save_and_switch_to_default()
    print(f"[COOKIE] Saved cookie {saved_cookie_index}, switched to default (0)")
    
    try:
        roblox = find_roblox()
        if not roblox:
            print("[DEBUG] Roblox executable not found on host!")
            if not data["aborted"]:
                await update_status(info_msg, embed, "error")
            await send_msg(send_func, "Roblox installation not found.", ephemeral=is_ephemeral)
            return

        print(f"[DEBUG] Launching Roblox binary at: {roblox}")
        process = subprocess.Popen([roblox, "--cmd", join_url])
        data["process"] = process
        await update_status(info_msg, embed, "joining")

        game_file = None
        try:
            file_path, game_file = await process_file(send_func, process, game_name, timeout=60, ephemeral=is_ephemeral, info_msg=info_msg, embed=embed, rec_ev=rec_ev, fin_ev=fin_ev, job_data=data)

            global SKIP_PROCESSFILE
            SKIP_PROCESSFILE = False

            if file_path:
                await update_status(info_msg, embed, "uploading")
                await update_bot_presence(game_name, text="Uploading File...")
                upload_result = await upload_file(file_path, place_id)

                if upload_result:
                    download_url = upload_result["url"]
                    embed.add_field(name="Download", value=f"[Click here]({download_url})", inline=False)
                    embed.color = 0x2ECC71

                    await send_msg(send_func, content=f"<@{author_id}>", embed=embed, ephemeral=is_ephemeral)
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
            if saved_cookie_index is not None:
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
