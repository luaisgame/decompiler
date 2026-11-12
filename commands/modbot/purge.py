"""Purge with filters and a fully customizable confirmation message."""
import re

import discord
from discord import app_commands

from . import db
from .core import (action_embed, deny_unless_mod, guild_slash, mod_log, mod_only,
                   modbot, render)

PURGE_COLOR = 0x95A5A6
_LINK_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
MAX_PURGE = 500


# --- filters (pure, unit-tested) -------------------------------------------

def parse_filters(tokens):
    """Parse filter tokens into a predicate config.

    Supported: bots, links, attachments, user:<mention|id>, contains:<text>
    Raises ValueError on an unknown token.
    """
    filters = {"bots": False, "links": False, "attachments": False,
               "user": None, "contains": None}
    for token in tokens:
        low = token.lower()
        if low == "bots":
            filters["bots"] = True
        elif low == "links":
            filters["links"] = True
        elif low in ("attachments", "files"):
            filters["attachments"] = True
        elif low.startswith(("user:", "from:")):
            value = token.split(":", 1)[1].strip()
            if not value:
                raise ValueError(token)
            filters["user"] = value
        elif low.startswith("contains:"):
            value = token.split(":", 1)[1]
            if not value:
                raise ValueError(token)
            filters["contains"] = value
        else:
            raise ValueError(token)
    return filters


def describe_filters(filters):
    parts = []
    if filters.get("bots"):
        parts.append("bots")
    if filters.get("links"):
        parts.append("links")
    if filters.get("attachments"):
        parts.append("attachments")
    if filters.get("user"):
        parts.append(f"user:{filters['user']}")
    if filters.get("contains"):
        parts.append(f"contains:{filters['contains']}")
    return ", ".join(parts) or "none"


def matches(msg, filters):
    """True when a message passes every configured filter."""
    if filters.get("bots") and not msg.author.bot:
        return False
    if filters.get("links"):
        content = msg.content or ""
        if not _LINK_RE.search(content):
            return False
    if filters.get("attachments") and not msg.attachments:
        return False
    user = filters.get("user")
    if user:
        wanted = user.strip().lstrip("<@!").rstrip(">")
        if str(msg.author.id) != wanted:
            return False
    needle = filters.get("contains")
    if needle and needle.lower() not in (msg.content or "").lower():
        return False
    return True


# --- config ----------------------------------------------------------------

def purge_text(cfg, **values):
    """Render the configured purge confirmation (or '' when disabled)."""
    template = cfg.get("purge_message")
    if template is None or str(template).strip().lower() in ("", "off", "none"):
        return ""
    return render(template, **values)[:2000]


# --- prefix commands -------------------------------------------------------

@modbot.command(name="purge")
@mod_only()
async def purge_cmd(ctx, amount: int, *, filters: str = ""):
    if not 1 <= amount <= MAX_PURGE:
        await ctx.send(f"Pick a number between 1 and {MAX_PURGE}.")
        return
    perms = ctx.channel.permissions_for(ctx.guild.me)
    if not perms.manage_messages:
        await ctx.send("I need the **Manage Messages** permission to purge.")
        return
    try:
        parsed = parse_filters(filters.split())
    except ValueError as exc:
        await ctx.send(
            f"Unknown filter `{exc.args[0]}`. Available: `bots`, `links`, "
            "`attachments`, `user:<mention|id>`, `contains:<text>`.")
        return

    def _check(msg):
        if msg.id == ctx.message.id or msg.pinned:
            return False
        return matches(msg, parsed)

    try:
        deleted = await ctx.channel.purge(
            limit=amount, check=_check, before=ctx.message,
            reason=f"Purge by {ctx.author} ({ctx.author.id})")
    except discord.Forbidden:
        await ctx.send("I don't have permission to delete messages here.")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Purge failed: {exc}")
        return

    cfg = db.get_config(ctx.guild.id)
    text = purge_text(
        cfg,
        count=len(deleted),
        channel=ctx.channel.mention,
        moderator=ctx.author.mention,
        filter=describe_filters(parsed),
    )
    if text:
        try:
            await ctx.channel.send(text)
        except Exception:
            pass

    embed = action_embed(
        "Messages Purged", PURGE_COLOR,
        moderator=ctx.author.mention,
        channel=f"#{ctx.channel.name}",
        deleted=str(len(deleted)),
        filter=describe_filters(parsed),
    )
    await mod_log(ctx.guild, embed)


@modbot.command(name="purgeconfig")
@mod_only()
async def purgeconfig_cmd(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        current = cfg_all.get("purge_message")
        embed = action_embed(
            "Purge Message", PURGE_COLOR,
            current=(current or "`off`"),
        )
        embed.description = (
            "Variables: `{count}` `{channel}` `{moderator}` `{filter}`\n"
            "Commands: `!purgeconfig <text>` \u00b7 `!purgeconfig off` \u00b7 "
            "`!purgeconfig reset`"
        )
        await ctx.send(embed=embed)
        return
    low = text.strip().lower()
    if low in ("off", "none"):
        cfg_all["purge_message"] = "off"
        msg = "Purge confirmation messages are now **off**."
    elif low == "reset":
        cfg_all["purge_message"] = db.DEFAULT_PURGE_MESSAGE
        msg = "Purge message reset to the default."
    else:
        cfg_all["purge_message"] = text[:2000]
        msg = f"Purge message updated:\n{text[:2000]}"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


# --- slash commands --------------------------------------------------------

@modbot.tree.command(name="purge", description="Delete recent messages (with filters)")
@guild_slash
@app_commands.describe(amount="How many messages to scan/delete (max 500)",
                       filters="e.g. `bots links` or `user:@someone contains:spam`")
async def purge_slash(interaction: discord.Interaction, amount: int,
                      filters: str = ""):
    if not await deny_unless_mod(interaction):
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Purge only works in text channels.", ephemeral=True)
        return
    if not 1 <= amount <= MAX_PURGE:
        await interaction.response.send_message(
            f"Pick a number between 1 and {MAX_PURGE}.", ephemeral=True)
        return
    perms = channel.permissions_for(interaction.guild.me)
    if not perms.manage_messages:
        await interaction.response.send_message(
            "I need the **Manage Messages** permission to purge.", ephemeral=True)
        return
    try:
        parsed = parse_filters(filters.split())
    except ValueError as exc:
        await interaction.response.send_message(
            f"Unknown filter `{exc.args[0]}`. Available: `bots`, `links`, "
            "`attachments`, `user:<mention|id>`, `contains:<text>`.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        deleted = await channel.purge(
            limit=amount, check=lambda m: (not m.pinned) and matches(m, parsed),
            reason=f"Purge by {interaction.user} ({interaction.user.id})")
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to delete messages here.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Purge failed: {exc}", ephemeral=True)
        return

    cfg = db.get_config(interaction.guild.id)
    text = purge_text(
        cfg,
        count=len(deleted),
        channel=channel.mention,
        moderator=interaction.user.mention,
        filter=describe_filters(parsed),
    )
    if text:
        try:
            await channel.send(text)
        except Exception:
            pass
    await interaction.followup.send(
        f"Deleted **{len(deleted)}** message(s).", ephemeral=True)
    await mod_log(interaction.guild, action_embed(
        "Messages Purged", PURGE_COLOR,
        moderator=interaction.user.mention,
        channel=f"#{channel.name}",
        deleted=str(len(deleted)),
        filter=describe_filters(parsed),
    ))
