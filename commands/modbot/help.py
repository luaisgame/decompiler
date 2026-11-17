"""Help for the moderation bot (!modhelp / /modhelp)."""
import discord
from discord import app_commands

from .core import guild_slash, modbot

HELP_COLOR = 0x5865F2


def help_embed():
    embed = discord.Embed(
        title="Moderation Bot \u2014 Help",
        description=(
            "This is the **moderation bot** \u2014 it runs on its own account "
            "alongside the main bot. Everything below needs the **Manage "
            "Server** permission or a moderator role (`!modrole`)."
        ),
        color=HELP_COLOR,
    )
    embed.add_field(
        name="Warns",
        value=(
            "**`!warn <user> [reason]`** / `/warn` \u2014 warn a member "
            "(they get a DM).\n"
            "**`!warns <user>`** / `/warns` \u2014 look up someone's warns.\n"
            "**`!removewarn <user> <id>`** / `/removewarn` \u2014 remove one warn.\n"
            "**`!clearwarns <user>`** / `/clearwarns` \u2014 wipe their record.\n"
            "**`!warnconfig`** \u2014 auto-action at N warns "
            "(`threshold`, `action none|kick|mute|ban`, `expire`, `mute`)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Bans & Kicks",
        value=(
            "**`!ban <user> [reason]`** / `/ban` \u2014 ban (DMs the reason).\n"
            "**`!tempban <user> <duration> [reason]`** / `/tempban` \u2014 "
            "timed ban, auto-unbans (`30m`, `2h`, `7d`).\n"
            "**`!unban <user|id>`** / `/unban` \u2014 lift a ban.\n"
            "**`!kick <user> [reason]`** / `/kick` \u2014 kick a member.\n"
            "**`!tempbans`** \u2014 list active temp bans."
        ),
        inline=False,
    )
    embed.add_field(
        name="Tickets",
        value=(
            "**`!tickets setup [#channel]`** \u2014 post the customizable panel "
            "(or use `/ticket`).\n"
            "**`!tickets category #channel`** \u2014 where tickets are created.\n"
            "**Panel:** `!tickets title|message|footer|color|thumbnail|image|button|ping`\n"
            "**Ticket:** `!tickets ticket_title|ticket_color|welcome|claimmsg|closemsg|"
            "claimbtn|closebtn|modal`\n"
            "**`!tickets preview`** \u2014 see the panel \u00b7 "
            "**`!tickets reset`** \u2014 reset the look/messages.\n"
            "**`!tickets list|close|log|prefix`** \u2014 manage open tickets. "
            "Claim/Close buttons live inside each ticket; transcripts are saved "
            "automatically."
        ),
        inline=False,
    )
    embed.add_field(
        name="Welcoming",
        value=(
            "**`!welcome channel #ch|off`** \u00b7 **`!welcome message <text|reset>`** "
            "\u00b7 **`!welcome on|off`** \u00b7 **`!welcome test`** / `/welcome`.\n"
            "**Autoroles:** `!welcome autorole add|remove|list|off` (or "
            "`!welcome autorole @role`) \u00b7 `!welcome autorole_delay <seconds|off>`.\n"
            "**DM:** `!welcome dm on|off` \u00b7 `!welcome dm_message <text|reset>`.\n"
            "**Embed:** `!welcome embed on|off` \u00b7 `embed_color` \u00b7 "
            "`embed_title` \u00b7 `embed_thumbnail user|server|off` \u00b7 "
            "`embed_image` \u00b7 `embed_footer`.\n"
            "**`!welcome reset`** \u2014 restore the defaults (keeps channel and autoroles).\n"
            "Variables: `{user}` `{server}` `{count}` `{name}`."
        ),
        inline=False,
    )
    embed.add_field(
        name="Purge",
        value=(
            "**`!purge <amount> [filters]`** / `/purge` \u2014 filters: `bots`, "
            "`links`, `attachments`, `user:<mention>`, `contains:<text>`.\n"
            "**`!purgeconfig <text|off|reset>`** \u2014 customize the confirmation "
            "(`{count}` `{channel}` `{moderator}` `{filter}`)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Settings",
        value=(
            "**`!modconfig`** \u2014 overview (+ `ban_dm on|off`).\n"
            "**`!modrole add|remove @role`** \u2014 moderator roles.\n"
            "**`!modlog #channel|off`** \u2014 moderation log."
        ),
        inline=False,
    )
    return embed


@modbot.command(name="modhelp")
async def modhelp_cmd(ctx):
    await ctx.send(embed=help_embed())


@modbot.tree.command(name="modhelp", description="Help for the moderation bot")
@guild_slash
async def modhelp_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=help_embed(), ephemeral=True)
