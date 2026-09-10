import asyncio
import os
import sys
import discord

from dotenv import load_dotenv
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["BOT_BASE_DIR"] = _BASE_DIR
load_dotenv(os.path.join(_BASE_DIR, ".env"))

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

    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] Synced {len(synced)} Slash Command(s).")
    except Exception as e:
        print(f"[DEBUG] Failed to sync slash commands: {e}")

@bot.event
async def on_command(ctx):
    try:
        open(os.path.join(os.environ.get("BOT_BASE_DIR", os.path.dirname(os.path.abspath(__file__))), ".check_update"), "w").close()
    except Exception:
        pass
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
