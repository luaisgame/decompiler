import asyncio
import os
import sys
import discord
import importlib

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

from commands.core import bot, BOT_TOKEN, start_local_server, decompile_queue_worker, reset_bot_presence

import commands.core
import commands.setup
import commands.blacklist
import commands.blacklistuser
import commands.blacklistserver
import commands.cookie
import commands.decompile
import commands.help

MODULES = [
    commands.core,
    commands.setup,
    commands.blacklist,
    commands.blacklistuser,
    commands.blacklistserver,
    commands.cookie,
    commands.decompile,
    commands.help,
]

async def pull_latest():
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "main",
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
    except Exception as e:
        print(f"[AUTO-UPDATE] Pull failed: {e}")
        return False

def reload_modules():
    for mod in MODULES:
        try:
            importlib.reload(mod)
        except Exception as e:
            print(f"[AUTO-UPDATE] Failed to reload {mod.__name__}: {e}")

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
    await pull_latest()
    reload_modules()

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
        await pull_latest()
        reload_modules()

async def _delete_command_message(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as e:
        print(f"[DEBUG] Could not delete command message: {e}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
