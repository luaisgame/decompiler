import discord
from .core import bot, BOT_OWNER_ID, mc_processes, _mc_run_command_capture, _valid_mc_player_name


def _find_running_server():
    for name, proc in mc_processes.items():
        if proc is not None and proc.returncode is None:
            return name
    return None


@bot.command()
async def fly(ctx, player: str = None):
    if ctx.author.id != BOT_OWNER_ID:
        return
    if not player or not _valid_mc_player_name(player):
        await ctx.send("Usage: `!fly <player>`")
        return
    server = _find_running_server()
    if not server:
        await ctx.send("No running Minecraft server found.")
        return
    output = await _mc_run_command_capture(server, f"ability {player} mayfly true")
    await ctx.send(f"Flight enabled for **{player}**" + (f"\n```\n{chr(10).join(output)}```" if output else ""))


@bot.command()
async def unfly(ctx, player: str = None):
    if ctx.author.id != BOT_OWNER_ID:
        return
    if not player or not _valid_mc_player_name(player):
        await ctx.send("Usage: `!unfly <player>`")
        return
    server = _find_running_server()
    if not server:
        await ctx.send("No running Minecraft server found.")
        return
    output = await _mc_run_command_capture(server, f"ability {player} mayfly false")
    await ctx.send(f"Flight disabled for **{player}**" + (f"\n```\n{chr(10).join(output)}```" if output else ""))
