import discord
from discord import app_commands
from core import (bot, user_has_role, BLACKLIST_ROLE_IDS, parse_user_id,
                   parse_duration, add_user_to_blacklist, load_blacklisted_users,
                   save_blacklisted_users)

@bot.command(name="blacklistuser")
async def blacklistuser_prefix(ctx, user: str, duration: str = "5m"):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    uid = parse_user_id(user)
    if uid is None:
        await ctx.send("Please provide a valid user ID or mention.")
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("Invalid duration. Use format like: `5m`, `2h`, `30s`, `1y`, `2mo`")
        return
    add_user_to_blacklist(uid, duration_seconds=seconds)
    await ctx.send(f"Blacklisted user <@{uid}> for {duration}.")

@bot.command(name="unblacklistuser")
async def unblacklistuser_prefix(ctx, user: str):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    uid = parse_user_id(user)
    if uid is None:
        await ctx.send("Please provide a valid user ID or mention.")
        return
    blacklisted = load_blacklisted_users()
    if uid not in blacklisted:
        await ctx.send(f"User <@{uid}> is not currently blacklisted.")
        return
    del blacklisted[uid]
    save_blacklisted_users(blacklisted)
    await ctx.send(f"Successfully unblacklisted user <@{uid}>.")

@bot.tree.command(name="blacklistuser", description="Bot-owner: blacklist a user from decompiling")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    user="The user ID or mention to blacklist",
    duration="Duration (e.g. 5m, 2h, 30s, 1y, 2mo). Default: 5m"
)
async def blacklistuser_slash(interaction: discord.Interaction, user: str, duration: str = "5m"):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    uid = parse_user_id(user)
    if uid is None:
        await interaction.response.send_message("Please provide a valid user ID or mention.", ephemeral=True)
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await interaction.response.send_message("Invalid duration. Use format like: `5m`, `2h`, `30s`, `1y`, `2mo`", ephemeral=True)
        return
    add_user_to_blacklist(uid, duration_seconds=seconds)
    await interaction.response.send_message(f"Blacklisted user <@{uid}> for {duration}.", ephemeral=True)

@bot.tree.command(name="unblacklistuser", description="Bot-owner: remove a user from the blacklist")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    user="The user ID or mention to unblacklist"
)
async def unblacklistuser_slash(interaction: discord.Interaction, user: str):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    uid = parse_user_id(user)
    if uid is None:
        await interaction.response.send_message("Please provide a valid user ID or mention.", ephemeral=True)
        return
    blacklisted = load_blacklisted_users()
    if uid not in blacklisted:
        await interaction.response.send_message(f"User <@{uid}> is not currently blacklisted.", ephemeral=True)
        return
    del blacklisted[uid]
    save_blacklisted_users(blacklisted)
    await interaction.response.send_message(f"Successfully unblacklisted user <@{uid}>.", ephemeral=True)
