import discord
from discord import app_commands
from .core import (bot, user_has_role, BLACKLIST_ROLE_IDS, load_blacklisted_servers,
                   add_server_to_blacklist, remove_server_from_blacklist)

@bot.command(name="blacklistserver")
async def blacklistserver_prefix(ctx, server_id: str = None):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    sid = int(server_id) if server_id and server_id.isdigit() else (ctx.guild.id if ctx.guild else None)
    if sid is None:
        await ctx.send("Please provide a server ID or use this command in a server.")
        return
    blacklisted = load_blacklisted_servers()
    if sid in blacklisted:
        await ctx.send(f"Server `{sid}` is already blacklisted.")
        return
    add_server_to_blacklist(sid)
    await ctx.send(f"Blacklisted server `{sid}` permanently. Use `!unblacklistserver {sid}` to undo.")

@bot.command(name="unblacklistserver")
async def unblacklistserver_prefix(ctx, server_id: str = None):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    sid = int(server_id) if server_id and server_id.isdigit() else (ctx.guild.id if ctx.guild else None)
    if sid is None:
        await ctx.send("Please provide a server ID or use this command in a server.")
        return
    if remove_server_from_blacklist(sid):
        await ctx.send(f"Successfully unblacklisted server `{sid}`.")
    else:
        await ctx.send(f"Server `{sid}` is not currently blacklisted.")

@bot.tree.command(name="blacklistserver", description="Bot-owner: permanently blacklist a server from decompiling")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    server_id="The server ID to blacklist (leave empty for current server)"
)
async def blacklistserver_slash(interaction: discord.Interaction, server_id: str = None):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    sid = int(server_id) if server_id and server_id.isdigit() else (interaction.guild.id if interaction.guild else None)
    if sid is None:
        await interaction.response.send_message("Please provide a server ID or use this command in a server.", ephemeral=True)
        return
    blacklisted = load_blacklisted_servers()
    if sid in blacklisted:
        await interaction.response.send_message(f"Server `{sid}` is already blacklisted.", ephemeral=True)
        return
    add_server_to_blacklist(sid)
    await interaction.response.send_message(f"Blacklisted server `{sid}` permanently. Use `/unblacklistserver server_id:{sid}` to undo.", ephemeral=True)

@bot.tree.command(name="unblacklistserver", description="Bot-owner: remove a server from the blacklist")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    server_id="The server ID to unblacklist (leave empty for current server)"
)
async def unblacklistserver_slash(interaction: discord.Interaction, server_id: str = None):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    sid = int(server_id) if server_id and server_id.isdigit() else (interaction.guild.id if interaction.guild else None)
    if sid is None:
        await interaction.response.send_message("Please provide a server ID or use this command in a server.", ephemeral=True)
        return
    if remove_server_from_blacklist(sid):
        await interaction.response.send_message(f"Successfully unblacklisted server `{sid}`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Server `{sid}` is not currently blacklisted.", ephemeral=True)
