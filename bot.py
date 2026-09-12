import asyncio
import os
import sys
import types
import urllib.request
import json
import discord

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

os.environ["BOT_BASE_DIR"] = _BASE_DIR

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE_DIR, ".env"))

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

GITHUB_FILES = [
    "commands/core.py",
    "commands/setup.py",
    "commands/blacklist.py",
    "commands/blacklistuser.py",
    "commands/blacklistserver.py",
    "commands/cookie.py",
    "commands/decompile.py",
    "commands/help.py",
    "commands/__init__.py",
]

HAS_LOCAL = os.path.isdir(os.path.join(_BASE_DIR, "commands"))

if not HAS_LOCAL:
    print("[STARTUP] No local commands folder found, fetching from GitHub...")
    pkg = types.ModuleType("commands")
    pkg.__path__ = []
    pkg.__package__ = "commands"
    sys.modules["commands"] = pkg
    for path in GITHUB_FILES:
        try:
            url = f"{RAW_URL}/{path}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                code = resp.read().decode()
        except Exception as e:
            print(f"[STARTUP] Failed to fetch {path}: {e}")
            sys.exit(1)
        mn = path.replace("/", ".").replace(".py", "")
        if mn.endswith(".__init__"):
            mn = mn[:-9]
        if mn in sys.modules:
            m = sys.modules[mn]
        else:
            m = types.ModuleType(mn)
            sys.modules[mn] = m
        m.__file__ = f"<github:{mn}>"
        m.__loader__ = None
        if mn == "commands" or mn.startswith("commands."):
            m.__package__ = "commands"
        else:
            m.__package__ = None
        exec(compile(code, f"<github:{mn}>", "exec"), m.__dict__)
    print("[STARTUP] All files loaded from GitHub.")
else:
    print("[STARTUP] Local commands folder found.")

from commands.core import bot, BOT_TOKEN

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

    async def sync_commands():
        try:
            synced = await bot.tree.sync()
            print(f"[DEBUG] Synced {len(synced)} Slash Command(s).")
        except Exception as e:
            print(f"[DEBUG] Failed to sync slash commands: {e}")

    bot.loop.create_task(sync_commands())

@bot.event
async def on_command(ctx):
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

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
