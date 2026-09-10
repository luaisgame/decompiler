import asyncio
import os
import sys
import json
import urllib.request
import types
import discord

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

COMMAND_NAMES = [
    "setup", "blacklist", "blacklistuser", "blacklistserver",
    "cookie", "decompile", "help",
]

COMMAND_MODULES = {}
core_module = None
_last_commit_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_commit") if not getattr(sys, "frozen", False) else os.path.join(os.path.dirname(os.path.abspath(sys.executable)), ".last_commit")

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["BOT_BASE_DIR"] = _BASE_DIR
load_dotenv(os.path.join(_BASE_DIR, ".env"))

def _fetch_json(url, headers=None):
    try:
        req = urllib.request.Request(url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def _fetch_raw(path):
    url = f"{RAW_BASE}/{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return None

def _get_saved_commit():
    try:
        if os.path.exists(_last_commit_file):
            with open(_last_commit_file, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return None

def _save_commit(sha):
    try:
        with open(_last_commit_file, "w") as f:
            f.write(sha)
    except Exception:
        pass

def _get_latest_commit():
    data = _fetch_json(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    if data and isinstance(data, dict):
        return data.get("sha")
    return None

def _load_module_from_code(name, code, package=None):
    mod = types.ModuleType(name, code)
    mod.__file__ = f"<github:{name}>"
    mod.__loader__ = None
    if package:
        mod.__package__ = package
    sys.modules[name] = mod
    exec(compile(code, f"<github:{name}>", "exec"), mod.__dict__)
    return mod

def load_commands():
    global core_module, COMMAND_MODULES
    if "commands" not in sys.modules:
        sys.modules["commands"] = types.ModuleType("commands")
        sys.modules["commands"].__path__ = []

    core_code = _fetch_raw("commands/core.py")
    if core_code is None:
        print("[SYNC] FATAL: Could not fetch commands/core.py")
        return False

    core_module = _load_module_from_code("commands.core", core_code, package="commands")
    sys.modules["commands.core"] = core_module
    print("[SYNC] commands/core.py")

    for name in COMMAND_NAMES:
        old_mod = COMMAND_MODULES.get(name)
        if old_mod:
            for attr_name in list(vars(old_mod)):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(old_mod, attr_name, None)
                if callable(attr) and hasattr(attr, "callback"):
                    try:
                        core_module.bot.remove_command(attr_name)
                    except Exception:
                        pass

        code = _fetch_raw(f"commands/{name}.py")
        if code is None:
            print(f"[SYNC] SKIP commands/{name}.py")
            continue

        mod = _load_module_from_code(f"commands.{name}", code, package="commands")
        COMMAND_MODULES[name] = mod
        print(f"[SYNC] commands/{name}.py")

    print(f"[SYNC] Loaded {len(COMMAND_MODULES)} command modules.")
    return True

if not load_commands():
    print("[BOT] FATAL: Could not fetch code from GitHub.")
    sys.exit(1)

bot = core_module.bot
BOT_TOKEN = core_module.BOT_TOKEN

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    from commands.core import switch_to_default_cookie, load_queue, start_local_server, decompile_queue_worker, reset_bot_presence

    switch_to_default_cookie()
    load_queue()

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

async def _check_for_updates():
    latest = _get_latest_commit()
    if not latest:
        return
    saved = _get_saved_commit()
    if latest == saved:
        return
    print(f"[UPDATE] New commit: {(saved or '?')[:8]} -> {latest[:8]}")
    _save_commit(latest)

    if "commands" not in sys.modules:
        sys.modules["commands"] = types.ModuleType("commands")
        sys.modules["commands"].__path__ = []

    global core_module, COMMAND_MODULES
    core_code = _fetch_raw("commands/core.py")
    if core_code is None:
        return
    core_module = _load_module_from_code("commands.core", core_code, package="commands")
    sys.modules["commands.core"] = core_module

    for name in COMMAND_NAMES:
        old_mod = COMMAND_MODULES.get(name)
        if old_mod:
            for attr_name in list(vars(old_mod)):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(old_mod, attr_name, None)
                if callable(attr) and hasattr(attr, "callback"):
                    try:
                        core_module.bot.remove_command(attr_name)
                    except Exception:
                        pass
        code = _fetch_raw(f"commands/{name}.py")
        if code is None:
            continue
        mod = _load_module_from_code(f"commands.{name}", code, package="commands")
        COMMAND_MODULES[name] = mod

    print("[UPDATE] Commands updated. Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@bot.event
async def on_command(ctx):
    asyncio.create_task(_check_for_updates())
    author = ctx.author
    guild = ctx.guild.name if ctx.guild else "DM"
    channel = ctx.channel.name if ctx.guild else "DM"
    print(
        f"[LOG] {author} (ID: {author.id}) used '!{ctx.command.name}' "
        f"in {guild} #{channel} (message {ctx.message.id})"
    )

@bot.event
async def on_interaction(interaction):
    if interaction.type == discord.InteractionType.application_command:
        asyncio.create_task(_check_for_updates())

@bot.event
async def on_command_completion(ctx):
    try:
        await asyncio.sleep(1)
        await ctx.message.delete()
    except Exception as e:
        print(f"[DEBUG] Could not delete command message: {e}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
