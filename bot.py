import asyncio
import os
import sys
import discord
import importlib
import urllib.request
import time

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

REPO = "luaisgame/decompiler"
BRANCH = "main"

COMMAND_FILES = [
    "core.py",
    "setup.py",
    "blacklist.py",
    "blacklistuser.py",
    "blacklistserver.py",
    "cookie.py",
    "decompile.py",
    "help.py",
]

MODULES = {}
COMMAND_MODULES = []

def fetch_file(path):
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"[SYNC] Failed to fetch {path}: {e}")
        return None

def sync_code():
    print("[SYNC] Fetching latest code from GitHub...")
    for path in COMMAND_FILES:
        content = fetch_file(path)
        if content is None:
            print(f"[SYNC] ERROR: Could not fetch {path}")
            return False
        local_path = os.path.join(_BASE_DIR, path)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[SYNC] {path}")
    print("[SYNC] All files synced.")
    return True

def load_modules():
    global MODULES, COMMAND_MODULES
    MODULES.clear()
    COMMAND_MODULES.clear()

    import core as core_module
    MODULES["core"] = core_module

    for name in ["setup", "blacklist", "blacklistuser", "blacklistserver", "cookie", "decompile", "help"]:
        try:
            mod = importlib.import_module(name)
            MODULES[name] = mod
            COMMAND_MODULES.append(mod)
        except Exception as e:
            print(f"[LOAD] Failed to load {name}: {e}")

    print(f"[LOAD] Loaded {len(COMMAND_MODULES)} command modules.")

def reload_modules():
    global MODULES, COMMAND_MODULES
    for name, mod in MODULES.items():
        try:
            importlib.reload(mod)
        except Exception as e:
            print(f"[AUTO-UPDATE] Failed to reload {name}: {e}")
    COMMAND_MODULES = [MODULES[n] for n in MODULES if n != "core"]

if not sync_code():
    print("[BOT] Initial sync failed. Using local files.")

load_modules()

from core import bot, BOT_TOKEN, start_local_server, decompile_queue_worker, reset_bot_presence

async def pull_latest():
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "BRANCH",
            cwd=_BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode().strip()
        if proc.returncode == 0:
            if "Already up to date" not in output:
                print(f"[AUTO-UPDATE] Pulled: {output}")
                return True
        return False
    except Exception:
        return False

async def sync_and_reload():
    if sync_code():
        reload_modules()

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    await start_local_server(port=5000)

    bot.loop.create_task(decompile_queue_worker())

    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="Waiting for requests...")
    )

    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] Synced {len(synced)} Slash Command(s).")
    except Exception as e:
        print(f"[DEBUG] Failed to sync slash commands: {e}")

@bot.event
async def on_command(ctx):
    await sync_and_reload()

    author = ctx.author
    guild = ctx.guild.name if ctx.guild else "DM"
    channel = ctx.channel.name if ctx.guild else "DM"
    print(
        f"[LOG] {author} (ID: {author.id}) used '!{ctx.command.name}' "
        f"in {guild} #{channel} (message {ctx.message.id})"
    )
    bot.loop.create_task(_delete_command_message(ctx.message, 1))

@bot.event
async def on_interaction(interaction):
    if interaction.type == discord.InteractionType.application_command:
        await sync_and_reload()

async def _delete_command_message(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as e:
        print(f"[DEBUG] Could not delete command message: {e}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
