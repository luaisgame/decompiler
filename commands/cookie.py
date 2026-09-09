import discord
from discord import app_commands
from .core import (bot, user_has_role, BLACKLIST_ROLE_IDS, load_cookies, save_cookies,
                   _replace_roblox_security_cookie)

# Will be set by bot.py after core loads
active_cookie_index = 0
default_cookie_index = 0

@bot.command(name="addcookie")
async def addcookie_prefix(ctx, cookie: str):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    cookies = load_cookies()
    cookies.append(cookie)
    save_cookies(cookies)
    await ctx.send(f"Cookie added. Total cookies: {len(cookies)} (index `{len(cookies) - 1}`)")

@bot.command(name="listcookies")
async def listcookies_prefix(ctx):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    cookies = load_cookies()
    if not cookies:
        await ctx.send("No cookies stored.")
        return
    lines = []
    for i, c in enumerate(cookies):
        marker = " <- active" if i == active_cookie_index else ""
        preview = c[:30] + "..." if len(c) > 30 else c
        lines.append(f"`{i}`: `{preview}`{marker}")
    await ctx.send("\n".join(lines))

@bot.command(name="removecookie")
async def removecookie_prefix(ctx, index: int):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    cookies = load_cookies()
    if 0 <= index < len(cookies):
        cookies.pop(index)
        save_cookies(cookies)
        global active_cookie_index
        if active_cookie_index >= len(cookies):
            active_cookie_index = 0
        await ctx.send(f"Removed cookie `{index}`. Total cookies: {len(cookies)}")
    else:
        await ctx.send("Invalid index.")

@bot.tree.command(name="addcookie", description="Bot-owner: add a Roblox cookie to the pool")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(cookie="The .ROBLOSECURITY cookie value")
async def addcookie_slash(interaction: discord.Interaction, cookie: str):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    cookies = load_cookies()
    cookies.append(cookie)
    save_cookies(cookies)
    await interaction.response.send_message(f"Cookie added. Total cookies: {len(cookies)} (index `{len(cookies) - 1}`)", ephemeral=True)

@bot.tree.command(name="listcookies", description="Bot-owner: list stored Roblox cookies")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def listcookies_slash(interaction: discord.Interaction):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    cookies = load_cookies()
    if not cookies:
        await interaction.response.send_message("No cookies stored.", ephemeral=True)
        return
    lines = []
    for i, c in enumerate(cookies):
        marker = " <- active" if i == active_cookie_index else ""
        preview = c[:30] + "..." if len(c) > 30 else c
        lines.append(f"`{i}`: `{preview}`{marker}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="removecookie", description="Bot-owner: remove a Roblox cookie from the pool")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(index="Cookie index to remove")
async def removecookie_slash(interaction: discord.Interaction, index: int):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    cookies = load_cookies()
    if 0 <= index < len(cookies):
        cookies.pop(index)
        save_cookies(cookies)
        global active_cookie_index
        if active_cookie_index >= len(cookies):
            active_cookie_index = 0
        await interaction.response.send_message(f"Removed cookie `{index}`. Total cookies: {len(cookies)}", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid index.", ephemeral=True)

@bot.command(name="switchcookie")
async def switchcookie_prefix(ctx, index: int):
    if not await user_has_role(ctx.author, BLACKLIST_ROLE_IDS):
        await ctx.send("You do not have permission to use this command.")
        return
    cookies = load_cookies()
    if not cookies:
        await ctx.send("No cookies stored.")
        return
    if index < 0 or index >= len(cookies):
        await ctx.send(f"Invalid index. Use `!listcookies` to see available cookies.")
        return

    global active_cookie_index, default_cookie_index
    active_cookie_index = index
    default_cookie_index = index

    _replace_roblox_security_cookie(cookies[index])
    preview = cookies[index][:30] + "..." if len(cookies[index]) > 30 else cookies[index]
    await ctx.send(f"Switched to cookie `{index}`: `{preview}`")

@bot.tree.command(name="switchcookie", description="Bot-owner: permanently switch active cookie")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(index="Cookie index to switch to")
async def switchcookie_slash(interaction: discord.Interaction, index: int):
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    cookies = load_cookies()
    if not cookies:
        await interaction.response.send_message("No cookies stored.", ephemeral=True)
        return
    if index < 0 or index >= len(cookies):
        await interaction.response.send_message(f"Invalid index. Use `/listcookies` to see available cookies.", ephemeral=True)
        return

    global active_cookie_index, default_cookie_index
    active_cookie_index = index
    default_cookie_index = index

    _replace_roblox_security_cookie(cookies[index])
    preview = cookies[index][:30] + "..." if len(cookies[index]) > 30 else cookies[index]
    await interaction.response.send_message(f"Switched to cookie `{index}`: `{preview}`", ephemeral=True)
