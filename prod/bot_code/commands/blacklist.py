import discord
from discord import app_commands
from .core import bot, user_has_role, BLACKLIST_ROLE_IDS, load_blacklisted_games, save_blacklisted_games

@bot.command(name="blacklist")
async def blacklist_prefix(ctx, place_id: str, *, reason: str = "No Information Provided"):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    if not place_id.isdigit():
        await ctx.send("Place ID must contain numbers only.")
        return
    blacklisted = load_blacklisted_games()
    target_id = str(place_id).strip()
    if target_id in blacklisted:
        await ctx.send(f"Place ID `{target_id}` is already blacklisted.")
        return
    blacklisted[target_id] = reason
    save_blacklisted_games(blacklisted)
    await ctx.send(f"Blacklisted place ID: `{target_id}` with reason: `{reason}`.")

@bot.tree.command(name="blacklist", description="Blacklist a Roblox Place ID from being decompiled")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    place_id="The Roblox Place ID to blacklist",
    reason="Optional reason for the blacklist"
)
async def blacklist_slash(interaction: discord.Interaction, place_id: str, reason: str = "No Information Provided"):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    if not place_id.isdigit():
        await interaction.response.send_message("Place ID must contain numbers only.", ephemeral=True)
        return
    blacklisted = load_blacklisted_games()
    target_id = str(place_id).strip()
    if target_id in blacklisted:
        await interaction.response.send_message(f"Place ID `{target_id}` is already blacklisted.", ephemeral=True)
        return
    blacklisted[target_id] = reason
    save_blacklisted_games(blacklisted)
    await interaction.response.send_message(f"Blacklisted place ID: `{target_id}` with reason: `{reason}`.")

@bot.command(name="unblacklist")
async def unblacklist_prefix(ctx, place_id: str):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    if not place_id.isdigit():
        await ctx.send("Place ID must contain numbers only.")
        return
    blacklisted = load_blacklisted_games()
    target_id = str(place_id).strip()
    if target_id not in blacklisted:
        await ctx.send(f"Place ID `{place_id}` is not currently blacklisted.")
        return
    del blacklisted[target_id]
    save_blacklisted_games(blacklisted)
    await ctx.send(f"Successfully unblacklisted place ID: `{place_id}`.")

@bot.tree.command(name="unblacklist", description="Remove a Roblox Place ID from the blacklist")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    place_id="The Roblox Place ID to unblacklist"
)
async def unblacklist_slash(interaction: discord.Interaction, place_id: str):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    if not place_id.isdigit():
        await interaction.response.send_message("Place ID must contain numbers only.", ephemeral=True)
        return
    blacklisted = load_blacklisted_games()
    target_id = str(place_id).strip()
    if target_id not in blacklisted:
        await interaction.response.send_message(f"Place ID `{place_id}` is not currently blacklisted.", ephemeral=True)
        return
    del blacklisted[target_id]
    save_blacklisted_games(blacklisted)
    await interaction.response.send_message(f"Successfully unblacklisted place ID: `{place_id}`.")
