"""Configurable ticket system: panel button, claim/close, transcripts."""
import asyncio
import os
import time

import discord
from discord import app_commands

from . import db
from .core import (action_embed, deny_unless_mod, guild_slash, is_mod, mod_log,
                   mod_only, modbot, render)

TICKET_COLOR = 0x5865F2
OPEN_CUSTOM_ID = "modbot_ticket_open"
CLAIM_CUSTOM_ID = "modbot_ticket_claim"
CLOSE_CUSTOM_ID = "modbot_ticket_close"


# --- pure helpers ----------------------------------------------------------

def next_index(names, prefix):
    """Highest existing `<prefix>-N` index + 1 (0 when none exist)."""
    indices = []
    for name in names:
        if name.startswith(f"{prefix}-"):
            tail = name.rsplit("-", 1)[-1]
            if tail.isdigit():
                indices.append(int(tail))
    return max(indices, default=0) + 1


def can_close(rec, user, moderator):
    """Creator, claimer, mods and the bot owner may close a ticket."""
    if moderator:
        return True
    return user.id in (rec.get("creator"), rec.get("claimer"))


# --- storage ---------------------------------------------------------------

def _tickets(guild_id):
    return db.get_tickets(guild_id)


def _save(guild_id, tickets):
    db.save_tickets(guild_id, tickets)


def _rec(guild, channel):
    if guild is None or channel is None:
        return None
    return _tickets(guild.id).get(str(channel.id))


# --- creation --------------------------------------------------------------

def _overwrites(guild, user, cfg):
    me = guild.me
    if me is None:
        me = guild.get_member(modbot.user.id) if modbot.user else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                          embed_links=True, read_message_history=True),
    }
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True,
            read_message_history=True)
    for role_id in db.get_config(guild.id).get("mod_roles") or []:
        role = guild.get_role(int(role_id))
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                read_message_history=True)
    return overwrites


async def create_ticket(guild, user, reason):
    """Create a ticket channel. Returns (channel, status).

    status is "created", "existing" (channel already open) or an error string
    when channel is None.
    """
    cfg = db.get_config(guild.id)["tickets"]
    cat_id = cfg.get("category")
    category = guild.get_channel(int(cat_id)) if cat_id else None
    if not isinstance(category, discord.CategoryChannel):
        return None, ("Tickets aren't set up here yet \u2014 a moderator can "
                      "run `!tickets category #channel`.")

    tickets = _tickets(guild.id)
    for channel_id, rec in tickets.items():
        if rec.get("creator") == user.id:
            existing = guild.get_channel(int(channel_id))
            if existing is not None:
                return existing, "existing"

    prefix = (cfg.get("prefix") or "ticket").strip() or "ticket"
    index = next_index([c.name for c in category.channels], prefix)
    channel = await guild.create_text_channel(
        name=f"{prefix}-{index}",
        category=category,
        overwrites=_overwrites(guild, user, cfg),
        topic=f"Ticket by {user} ({user.id})",
    )

    tickets[str(channel.id)] = {
        "creator": user.id,
        "claimer": None,
        "reason": reason or "No reason provided",
        "opened": time.time(),
    }
    _save(guild.id, tickets)

    embed = discord.Embed(
        title="\U0001f3ab Ticket opened",
        color=TICKET_COLOR,
        description=render(cfg.get("panel_message"),
                           user=user.mention, ticket=channel.name),
    )
    embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024],
                    inline=False)
    embed.add_field(name="Creator", value=user.mention, inline=True)
    embed.set_footer(text=f"Ticket #{index} \u00b7 staff will be with you shortly")
    try:
        await channel.send(user.mention, embed=embed, view=TicketActionsView())
    except Exception as exc:
        print(f"[MODBOT] Failed to post ticket welcome: {exc}")

    await mod_log(guild, action_embed(
        "Ticket Opened", TICKET_COLOR,
        user=user.mention,
        channel=channel.mention,
        reason=(reason or "No reason provided")[:1024],
    ))
    return channel, "created"


# --- transcripts -----------------------------------------------------------

async def write_transcript(channel, rec):
    """Dump the ticket's history to storage/modbot/transcripts/; returns path."""
    try:
        os.makedirs(db.TRANSCRIPT_DIR, exist_ok=True)
    except Exception:
        return None
    path = os.path.join(db.TRANSCRIPT_DIR, f"{channel.guild.id}-{channel.id}.txt")
    lines = [
        f"# Ticket {channel.name} (#{channel.id})",
        f"# Guild: {channel.guild.name} ({channel.guild.id})",
        f"# Creator: {rec.get('creator')}  opened: {rec.get('opened')}",
        "",
    ]
    try:
        async for msg in channel.history(limit=2000, oldest_first=True):
            stamp = discord.utils.format_dt(msg.created_at, "f")
            body = msg.content or ""
            if msg.attachments:
                body += (" " if body else "") + " ".join(a.url for a in msg.attachments)
            if msg.embeds and not body:
                body = "[embed]"
            lines.append(f"[{stamp}] {msg.author} ({msg.author.id}): {body}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path
    except Exception as exc:
        print(f"[MODBOT] Transcript failed for {channel.id}: {exc}")
        return None


async def finish_ticket(guild, channel, actor, rec):
    """Save a transcript, log the closure, then delete the channel."""
    path = await write_transcript(channel, rec)

    tickets = _tickets(guild.id)
    tickets.pop(str(channel.id), None)
    _save(guild.id, tickets)

    try:
        await channel.send(f"\U0001f512 Ticket closed by {actor.mention}. "
                           "Deleting in 5 seconds\u2026")
    except Exception:
        pass

    embed = action_embed(
        "Ticket Closed", 0x95A5A6,
        user=f"<@{rec.get('creator')}>",
        moderator=actor.mention,
        channel=f"#{channel.name}",
    )
    if path:
        embed.add_field(name="Transcript", value=os.path.basename(path), inline=True)
    await mod_log(guild, embed)

    log_id = db.get_config(guild.id)["tickets"].get("log_channel")
    if log_id:
        log_ch = guild.get_channel(int(log_id))
        if log_ch is not None:
            try:
                await log_ch.send(embed=embed)
            except Exception:
                pass

    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {actor}")
    except Exception as exc:
        print(f"[MODBOT] Could not delete ticket channel {channel.id}: {exc}")


# --- views (persistent, static custom_ids) ---------------------------------

class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary,
                       emoji="\U0001f3ab", custom_id=OPEN_CUSTOM_ID)
    async def open_ticket(self, interaction: discord.Interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tickets can only be opened in a server.", ephemeral=True)
            return
        cfg = db.get_config(interaction.guild.id)["tickets"]
        if not cfg.get("category"):
            await interaction.response.send_message(
                "Tickets aren't set up here yet \u2014 a moderator can run "
                "`!tickets category #channel`.", ephemeral=True)
            return
        for channel_id, rec in _tickets(interaction.guild.id).items():
            if rec.get("creator") == interaction.user.id:
                existing = interaction.guild.get_channel(int(channel_id))
                if existing is not None:
                    await interaction.response.send_message(
                        f"You already have an open ticket: {existing.mention}",
                        ephemeral=True)
                    return
        await interaction.response.send_modal(
            TicketModal(interaction.guild.id, interaction.channel.id))


class TicketModal(discord.ui.Modal, title="Open a Ticket"):
    reason = discord.ui.TextInput(
        label="What do you need help with?",
        style=discord.TextStyle.long,
        required=True,
        max_length=1000,
        placeholder="Describe your issue\u2026",
    )

    def __init__(self, guild_id, panel_channel_id=None):
        super().__init__()
        self.guild_id = guild_id
        self.panel_channel_id = panel_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel, status = await create_ticket(guild, interaction.user,
                                              self.reason.value)
        if channel is None:
            await interaction.response.send_message(status, ephemeral=True)
            return
        if status == "existing":
            await interaction.response.send_message(
                f"You already have an open ticket: {channel.mention}",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"Ticket created: {channel.mention}", ephemeral=True)


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success,
                       emoji="\u2705", custom_id=CLAIM_CUSTOM_ID)
    async def claim_ticket(self, interaction: discord.Interaction, button):
        guild = interaction.guild
        rec = _rec(guild, interaction.channel)
        if rec is None:
            await interaction.response.send_message(
                "This isn't a ticket channel.", ephemeral=True)
            return
        if rec.get("claimer"):
            await interaction.response.send_message(
                f"Already claimed by <@{rec['claimer']}>.", ephemeral=True)
            return
        if not (await is_mod(interaction.user)
                or interaction.user.id in (rec.get("creator"), rec.get("claimer"))):
            await interaction.response.send_message(
                "Only moderators can claim tickets.", ephemeral=True)
            return
        rec["claimer"] = interaction.user.id
        tickets = _tickets(guild.id)
        tickets[str(interaction.channel.id)] = rec
        _save(guild.id, tickets)
        try:
            await interaction.channel.set_permissions(
                interaction.user, view_channel=True, send_messages=True,
                embed_links=True, read_message_history=True)
        except Exception:
            pass
        try:
            await interaction.channel.send(
                f"{interaction.user.mention} claimed this ticket.")
        except Exception:
            pass
        await interaction.response.send_message("You claimed this ticket.",
                                                ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger,
                       emoji="\U0001f512", custom_id=CLOSE_CUSTOM_ID)
    async def close_ticket(self, interaction: discord.Interaction, button):
        guild = interaction.guild
        rec = _rec(guild, interaction.channel)
        if rec is None:
            await interaction.response.send_message(
                "This isn't a ticket channel.", ephemeral=True)
            return
        moderator = await is_mod(interaction.user)
        if not can_close(rec, interaction.user, moderator):
            await interaction.response.send_message(
                "Only the ticket creator, claimer or a moderator can close this.",
                ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket\u2026",
                                                ephemeral=True)
        await finish_ticket(guild, interaction.channel, interaction.user, rec)


# --- prefix commands -------------------------------------------------------

def _usage():
    embed = discord.Embed(
        title="\U0001f3ab Tickets",
        color=TICKET_COLOR,
        description=(
            "`!tickets setup` \u2014 post the ticket panel here\n"
            "`!tickets category #channel` \u2014 where ticket channels are created\n"
            "`!tickets prefix <name>` \u2014 channel name prefix (default `ticket`)\n"
            "`!tickets message <text>` \u2014 panel text (`reset` for default)\n"
            "`!tickets log #channel|off` \u2014 open/close log channel\n"
            "`!tickets list` \u2014 open tickets\n"
            "`!tickets close [#channel]` \u2014 close a ticket"
        ),
    )
    return embed


@modbot.group(name="tickets", invoke_without_command=True)
@mod_only()
async def tickets_group(ctx):
    await ctx.send(embed=_usage())


@tickets_group.command(name="setup")
@mod_only()
async def tickets_setup(ctx):
    cfg = db.get_config(ctx.guild.id)["tickets"]
    if not cfg.get("category"):
        await ctx.send("Set a category first: `!tickets category #channel`.")
        return
    embed = discord.Embed(
        title="\U0001f3ab Support Tickets",
        color=TICKET_COLOR,
        description=cfg.get("panel_message") or db.DEFAULT_PANEL_MESSAGE,
    )
    embed.set_footer(text="Click the button below \u2014 staff will see it instantly")
    await ctx.send(embed=embed, view=TicketOpenView())


@tickets_group.command(name="category")
@mod_only()
async def tickets_category(ctx, channel: discord.abc.GuildChannel = None):
    cfg_all = db.get_config(ctx.guild.id)
    if channel is None:
        cfg_all["tickets"]["category"] = None
        msg = "Ticket category cleared."
    else:
        category = channel if isinstance(channel, discord.CategoryChannel) else channel.category
        if category is None:
            await ctx.send("That channel has no category \u2014 pass a category, "
                           "or a channel that lives inside one.")
            return
        cfg_all["tickets"]["category"] = category.id
        msg = f"Tickets will be created under **{category.name}**."
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


@tickets_group.command(name="prefix")
@mod_only()
async def tickets_prefix(ctx, *, text: str = None):
    if not text:
        await ctx.send(f"Current prefix: `{db.get_config(ctx.guild.id)['tickets']['prefix']}`")
        return
    cleaned = "".join(ch for ch in text.strip().lower() if ch.isalnum() or ch in "-_")
    if not cleaned:
        await ctx.send("Use letters, numbers, `-` or `_` only.")
        return
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["tickets"]["prefix"] = cleaned[:24]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Ticket channels will be named `{cleaned}-1`, `{cleaned}-2`\u2026")


@tickets_group.command(name="message")
@mod_only()
async def tickets_message(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        await ctx.send(f"Panel message:\n{cfg_all['tickets'].get('panel_message')}")
        return
    if text.lower() == "reset":
        cfg_all["tickets"]["panel_message"] = db.DEFAULT_PANEL_MESSAGE
    else:
        cfg_all["tickets"]["panel_message"] = text[:1000]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Panel message updated. Re-run `!tickets setup` to repost it.")


@tickets_group.command(name="log")
@mod_only()
async def tickets_log(ctx, channel: discord.TextChannel = None):
    cfg_all = db.get_config(ctx.guild.id)
    if channel is None:
        cfg_all["tickets"]["log_channel"] = None
        msg = "Ticket log channel turned off."
    else:
        cfg_all["tickets"]["log_channel"] = channel.id
        msg = f"Ticket open/close events will be logged to {channel.mention}."
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


@tickets_group.command(name="list")
@mod_only()
async def tickets_list(ctx):
    tickets = _tickets(ctx.guild.id)
    rows = []
    for channel_id, rec in tickets.items():
        channel = ctx.guild.get_channel(int(channel_id))
        if channel is None:
            continue
        claimer = f" \u00b7 claimed <@{rec['claimer']}>" if rec.get("claimer") else ""
        rows.append(f"{channel.mention} \u00b7 <@{rec.get('creator')}>{claimer}")
    embed = action_embed("Open Tickets", TICKET_COLOR)
    embed.description = "\n".join(rows[:25]) or "No open tickets."
    if len(rows) > 25:
        embed.set_footer(text=f"+{len(rows) - 25} more")
    await ctx.send(embed=embed)


@tickets_group.command(name="close")
@mod_only()
async def tickets_close(ctx, channel: discord.TextChannel = None):
    target = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
    if target is None:
        await ctx.send("Pass a ticket channel: `!tickets close #channel`.")
        return
    rec = _rec(ctx.guild, target)
    if rec is None:
        await ctx.send("That isn't an open ticket channel.")
        return
    await ctx.send("Closing ticket\u2026")
    await finish_ticket(ctx.guild, target, ctx.author, rec)


# --- slash commands --------------------------------------------------------

@modbot.tree.command(name="ticket", description="Open a support ticket")
@guild_slash
async def ticket_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Tickets can only be opened in a server.", ephemeral=True)
        return
    cfg = db.get_config(interaction.guild.id)["tickets"]
    if not cfg.get("category"):
        await interaction.response.send_message(
            "Tickets aren't set up here yet \u2014 a moderator can run "
            "`!tickets category #channel`.", ephemeral=True)
        return
    for channel_id, rec in _tickets(interaction.guild.id).items():
        if rec.get("creator") == interaction.user.id:
            existing = interaction.guild.get_channel(int(channel_id))
            if existing is not None:
                await interaction.response.send_message(
                    f"You already have an open ticket: {existing.mention}",
                    ephemeral=True)
                return
    await interaction.response.send_modal(
        TicketModal(interaction.guild.id, interaction.channel.id))
