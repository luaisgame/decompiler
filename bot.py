import asyncio
import os
import sys
import types
import discord
import urllib.request

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

def _self_update():
    url = f"{RAW_BASE}/bot.py"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            remote = resp.read().decode("utf-8")
        my_path = os.path.abspath(__file__)
        with open(my_path, "r", encoding="utf-8") as f:
            local = f.read()
        if remote.strip() != local.strip():
            print("[UPDATE] New bot.py found on GitHub, updating...")
            with open(my_path, "w", encoding="utf-8") as f:
                f.write(remote)
            print("[UPDATE] Restarting...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print("[UPDATE] bot.py is up to date.")
    except Exception as e:
        print(f"[UPDATE] Self-update check failed: {e}")

_self_update()

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["BOT_BASE_DIR"] = _BASE_DIR
load_dotenv(os.path.join(_BASE_DIR, ".env"))

REPO = "luaisgame/decompiler"
BRANCH = "main"

COMMAND_NAMES = [
    "setup", "blacklist", "blacklistuser", "blacklistserver",
    "cookie", "decompile", "help",
]

COMMAND_MODULES = {}
core_module = None

def fetch_file(path):
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"[SYNC] Failed to fetch {path}: {e}")
        return None

def load_module_from_code(name, code, package=None):
    mod = types.ModuleType(name, code)
    mod.__file__ = f"<github:{name}>"
    mod.__loader__ = None
    if package:
        mod.__package__ = package
    sys.modules[name] = mod
    exec(compile(code, f"<github:{name}>", "exec"), mod.__dict__)
    return mod

def sync_all():
    global core_module, COMMAND_MODULES
    print("[SYNC] Fetching latest code from GitHub...")

    if "commands" not in sys.modules:
        sys.modules["commands"] = types.ModuleType("commands")
        sys.modules["commands"].__path__ = []

    core_code = fetch_file("commands/core.py")
    if core_code is None:
        print("[SYNC] FATAL: Could not fetch commands/core.py")
        return False

    core_module = load_module_from_code("commands.core", core_code, package="commands")
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

        code = fetch_file(f"commands/{name}.py")
        if code is None:
            print(f"[SYNC] SKIP commands/{name}.py (fetch failed)")
            continue

        mod = load_module_from_code(f"commands.{name}", code, package="commands")
        COMMAND_MODULES[name] = mod
        print(f"[SYNC] commands/{name}.py")

    print(f"[SYNC] Loaded {len(COMMAND_MODULES)} command modules.")
    return True

if not sync_all():
    print("[BOT] FATAL: Could not fetch code from GitHub.")
    sys.exit(1)

bot = core_module.bot
BOT_TOKEN = core_module.BOT_TOKEN

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    core_module.switch_to_default_cookie()
    core_module.load_queue()

    await core_module.start_local_server(port=5000)

    bot.loop.create_task(core_module.decompile_queue_worker())

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
    sync_all()
    author = ctx.author
    guild = ctx.guild.name if ctx.guild else "DM"
    channel = ctx.channel.name if ctx.guild else "DM"
    print(
        f"[LOG] {author} (ID: {author.id}) used '!{ctx.command.name}' "
        f"in {guild} #{channel} (message {ctx.message.id})"
    )

@bot.event
async def on_command_completion(ctx):
    try:
        await asyncio.sleep(1)
        await ctx.message.delete()
    except Exception as e:
        print(f"[DEBUG] Could not delete command message: {e}")

@bot.event
async def on_interaction(interaction):
    if interaction.type == discord.InteractionType.application_command:
        sync_all()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
