import discord
from discord import app_commands
from .core import bot

bot.remove_command("help")

@bot.command(name="help")
async def help_prefix(ctx):
    embed = discord.Embed(
        title="Decompiler Bot \u2014 Help",
        description=(
            "Decompile Roblox experiences quickly and easily.\n"
            "Use `!decompile` (or `/decompile`) with a **Place ID** or a **game name** "
            "(e.g. `Forsaken`)."
        ),
        color=0x5865F2
    )
    embed.add_field(
        name="User Commands",
        value=(
            "**`!decompile <place_id | name> [game_id]`**\n"
            "Decompile a Roblox experience. Accepts a numeric Place ID or a game name. "
            "The optional `game_id` can be a Job ID or a private-server / share link.\n"
            "**`!comp <place_id | name> [game_id]`** \u2014 alias of `!decompile`.\n"
            "**`/decompile`** \u2014 slash-command version (same options)."
        ),
        inline=False
    )
    embed.add_field(
        name="Admin Commands",
        value="**`!setup`** \u2014 configure the channel or forum where decompile commands are allowed (requires Administrator permission).",
        inline=False
    )
    embed.add_field(
        name="Owner Commands",
        value=(
            "**`!blacklist <place_id> [reason]`** \u2014 block a game from being decompiled.\n"
            "**`!unblacklist <place_id>`** \u2014 remove a game from the blacklist.\n"
            "**`!blacklistuser <user> [duration]`** \u2014 block a user (e.g. `5m`, `2h`, `1y`).\n"
            "**`!unblacklistuser <user>`** \u2014 remove a user from the blacklist.\n"
            "**`!blacklistserver <server_id>`** \u2014 block a server.\n"
            "**`!unblacklistserver <server_id>`** \u2014 unblock a server.\n"
            "**`/blacklistuser`**, **`/unblacklistuser`**, **`/blacklistserver`**, **`/unblacklistserver`** \u2014 slash versions."
        ),
        inline=False
    )
    embed.add_field(
        name="Cookie Management (Owner)",
        value=(
            "**`!addcookie <cookie>`** \u2014 add a Roblox cookie to the pool.\n"
            "**`!listcookies`** \u2014 list stored cookies.\n"
            "**`!removecookie <index>`** \u2014 remove a cookie by index.\n"
            "**`/addcookie`**, **`/listcookies`**, **`/removecookie`** \u2014 slash versions.\n"
            "Auto-rotates on ban detection."
        ),
        inline=False
    )
    embed.add_field(
        name="Info",
        value="**`!help`** \u2014 show this message.",
        inline=False
    )
    embed.set_footer(text="Fastest Roblox Decompiler")
    await ctx.send(embed=embed)

@bot.tree.command(name="help", description="Show help for the Decompiler Bot")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Decompiler Bot \u2014 Help",
        description=(
            "Decompile Roblox experiences quickly and easily.\n"
            "Use `/decompile` with a **Place ID** or a **game name**."
        ),
        color=0x5865F2
    )
    embed.add_field(
        name="User Commands",
        value=(
            "**`/decompile <place_id | name> [game_id]`**\n"
            "Decompile a Roblox experience. Accepts a numeric Place ID or a game name."
        ),
        inline=False
    )
    embed.add_field(
        name="Admin Commands",
        value="**`/setup`** \u2014 configure allowed channel (requires Administrator).",
        inline=False
    )
    embed.add_field(
        name="Owner Commands",
        value=(
            "**`/blacklist <place_id> [reason]`** \u2014 block a game.\n"
            "**`/unblacklist <place_id>`** \u2014 unblock a game.\n"
            "**`/blacklistuser <user> [duration]`** \u2014 block a user (e.g. `5m`, `2h`, `1y`).\n"
            "**`/unblacklistuser <user>`** \u2014 unblock a user.\n"
            "**`/blacklistserver <server_id>`** \u2014 block a server.\n"
            "**`/unblacklistserver <server_id>`** \u2014 unblock a server."
        ),
        inline=False
    )
    embed.add_field(
        name="Cookie Management (Owner)",
        value=(
            "**`/addcookie <cookie>`** \u2014 add a Roblox cookie.\n"
            "**`/listcookies`** \u2014 list stored cookies.\n"
            "**`/removecookie <index>`** \u2014 remove a cookie.\n"
            "Auto-rotates on ban detection."
        ),
        inline=False
    )
    embed.set_footer(text="Fastest Roblox Decompiler")
    await interaction.response.send_message(embed=embed, ephemeral=True)
