import asyncio
import os
import sys
import types
import discord
import importlib
import urllib.request

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

REPO = "luaisgame/decompiler"
BRANCH = "main"

COMMAND_NAMES = [
    "setup", "blacklist", "blacklistuser", "blacklistserver",
    "cookie", "decompile", "help",
]

COMMAND_MODULES = {}

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

def reload_command_modules():
    global COMMAND_MODULES
    print("[SYNC] Fetching latest code from GitHub...")

    sys.modules["commands.core"] = core_module

    for name in COMMAND_NAMES:
        code = fetch_file(f"commands/{name}.py")
        if code is None:
            print(f"[SYNC] SKIP commands/{name}.py (fetch failed)")
            continue

        old_mod = COMMAND_MODULES.get(name)
        if old_mod:
            for attr_name in list(vars(old_mod)):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(old_mod, attr_name, None)
                if callable(attr) and hasattr(attr, "callback"):
                    try:
                        bot.remove_command(attr_name)
                    except Exception:
                        pass

        mod = load_module_from_code(f"commands.{name}", code, package="commands")
        COMMAND_MODULES[name] = mod
        print(f"[SYNC] commands/{name}.py")

    print(f"[SYNC] Reloaded {len(COMMAND_MODULES)} command modules.")
    return True

import commands.core as core_module
from commands.core import bot, BOT_TOKEN, start_local_server, decompile_queue_worker, reset_bot_presence, switch_to_default_cookie

for name in COMMAND_NAMES:
    try:
        mod = importlib.import_module(f"commands.{name}")
        COMMAND_MODULES[name] = mod
    except Exception as e:
        print(f"[LOAD] Failed to load commands.{name}: {e}")

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    switch_to_default_cookie()

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
        reload_command_modules()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
