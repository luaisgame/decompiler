"""Configurable ticket system: panel button, claim/close, transcripts."""
import asyncio
import copy
import os
import time

import discord

from . import db
from .core import (action_embed, button_style, guild_slash, is_mod, mod_log,
                   mod_only, modbot, parse_color, parse_emoji, render,
                   text_or_none)

TICKET_COLOR = 0x5865F2
OPEN_CUSTOM_ID = "modbot_ticket_open"
CLAIM_CUSTOM_ID = "modbot_ticket_claim"
CLOSE_CUSTOM_ID = "modbot_ticket_close"

# Settings that describe where tickets live, not how they look. `!tickets reset`
# keeps these so a cosmetic reset can't break an active ticket setup.
_TICKET_KEEP_ON_RESET = ("category", "prefix", "panel_channel", "panel_message_id",
                         "log_channel")


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


def panel_embed(cfg, guild=None):
    """Build the configurable ticket panel embed."""
    values = {"server": guild.name if guild is not None else ""}
    title_raw = cfg.get("panel_title")
    title = render(db.DEFAULT_PANEL_TITLE if title_raw is None else title_raw,
                   **values).strip()
    description = render(cfg.get("panel_message") or db.DEFAULT_PANEL_MESSAGE,
                         **values).strip()
    embed = discord.Embed(
        title=title[:256] or None,
        description=description[:4096] or None,
        color=parse_color(cfg.get("panel_color"), TICKET_COLOR),
    )
    thumbnail = text_or_none(cfg.get("panel_thumbnail"))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    image = text_or_none(cfg.get("panel_image"))
    if image:
        embed.set_image(url=image)
    footer = render(cfg.get("panel_footer") or "", **values).strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


def ticket_embed(cfg, guild, user, channel, index, reason):
    """Build the embed posted at the top of a freshly created ticket."""
    values = {
        "user": user.mention,
        "name": user.display_name,
        "server": guild.name,
        "ticket": channel.name,
        "index": index,
    }
    title_raw = cfg.get("ticket_title")
    title = render(db.DEFAULT_TICKET_TITLE if title_raw is None else title_raw,
                   **values).strip()
    welcome_raw = cfg.get("ticket_welcome")
    description = render(db.DEFAULT_TICKET_WELCOME if welcome_raw is None
                         else welcome_raw, **values).strip()
    embed = discord.Embed(
        title=title[:256] or None,
        description=description[:4096] or None,
        color=parse_color(cfg.get("ticket_color"), TICKET_COLOR),
    )
    embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024],
                    inline=False)
    embed.add_field(name="Creator", value=user.mention, inline=True)
    footer = render(cfg.get("ticket_footer") or "", **values).strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


async def post_panel(channel, cfg, guild=None, preview=False):
    """Send the panel (embed + persistent button) and return the message."""
    guild = guild or getattr(channel, "guild", None)
    content = text_or_none(cfg.get("panel_ping"))
    return await channel.send(
        content=content,
        embed=panel_embed(cfg, guild),
        view=TicketOpenView(cfg),
        allowed_mentions=(discord.AllowedMentions.none() if preview
                          else discord.AllowedMentions(roles=True)),
    )


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

    try:
        await channel.send(user.mention,
                           embed=ticket_embed(cfg, guild, user, channel, index,
                                              reason),
                           view=TicketActionsView(cfg))
    except Exception as exc:
        print(f"[MODBOT] Failed to post ticket welcome: {exc}")

    await mod_log(guild, action_embed(
        "Ticket Opened", parse_color(cfg.get("ticket_color"), TICKET_COLOR),
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
    cfg = db.get_config(guild.id)["tickets"]

    tickets = _tickets(guild.id)
    tickets.pop(str(channel.id), None)
    _save(guild.id, tickets)

    close_text = render(
        cfg.get("close_message") or db.DEFAULT_CLOSE_MESSAGE,
        user=actor.mention,
        creator=f"<@{rec.get('creator')}>",
        ticket=channel.name,
        server=guild.name,
    )
    try:
        await channel.send(close_text[:2000])
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

    log_id = cfg.get("log_channel")
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
    """Panel button; label/emoji/style come from the guild config."""

    def __init__(self, cfg=None):
        super().__init__(timeout=None)
        cfg = cfg or {}
        button = discord.ui.Button(
            label=(cfg.get("panel_button_label") or db.DEFAULT_PANEL_BUTTON)[:80],
            style=button_style(cfg.get("panel_button_style")),
            emoji=parse_emoji(cfg.get("panel_button_emoji")),
            custom_id=OPEN_CUSTOM_ID,
        )
        button.callback = self.open_ticket
        self.add_item(button)

    async def open_ticket(self, interaction: discord.Interaction):
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
            TicketModal(interaction.guild.id, interaction.channel.id, cfg))


class TicketModal(discord.ui.Modal):
    """Reason modal; title and label come from the guild config."""

    def __init__(self, guild_id, panel_channel_id=None, cfg=None):
        cfg = cfg or {}
        super().__init__(
            title=(cfg.get("modal_title") or "Open a Ticket")[:45],
            timeout=300,
        )
        self.guild_id = guild_id
        self.panel_channel_id = panel_channel_id
        self.reason = discord.ui.TextInput(
            label=(cfg.get("modal_label") or "What do you need help with?")[:45],
            style=discord.TextStyle.long,
            required=True,
            max_length=1000,
            placeholder="Describe your issue\u2026",
        )
        self.add_item(self.reason)

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
    """Claim/Close buttons; labels and emoji come from the guild config."""

    def __init__(self, cfg=None):
        super().__init__(timeout=None)
        cfg = cfg or {}
        claim = discord.ui.Button(
            label=(cfg.get("claim_button_label") or "Claim")[:80],
            style=discord.ButtonStyle.success,
            emoji=parse_emoji(cfg.get("claim_button_emoji")),
            custom_id=CLAIM_CUSTOM_ID,
        )
        claim.callback = self.claim_ticket
        self.add_item(claim)
        close = discord.ui.Button(
            label=(cfg.get("close_button_label") or "Close")[:80],
            style=discord.ButtonStyle.danger,
            emoji=parse_emoji(cfg.get("close_button_emoji")),
            custom_id=CLOSE_CUSTOM_ID,
        )
        close.callback = self.close_ticket
        self.add_item(close)

    async def claim_ticket(self, interaction: discord.Interaction):
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
        cfg = db.get_config(guild.id)["tickets"]
        text = render(
            cfg.get("claim_message") or db.DEFAULT_CLAIM_MESSAGE,
            user=interaction.user.mention,
            creator=f"<@{rec.get('creator')}>",
            ticket=interaction.channel.name,
            server=guild.name,
        )
        try:
            await interaction.channel.send(text[:2000])
        except Exception:
            pass
        await interaction.response.send_message("You claimed this ticket.",
                                                ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction):
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


# --- config helpers --------------------------------------------------------

def _text_setting(ctx, key, text, default, limit, allow_off=False):
    """Read or write one text setting. Returns (value, action) where action is
    None when the command only displayed the current value."""
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if text is None:
        return cfg.get(key), None
    lowered = text.strip().lower()
    if lowered == "reset":
        cfg[key] = default
        action = "reset"
    elif allow_off and lowered in ("off", "none"):
        cfg[key] = ""
        action = "cleared"
    else:
        cfg[key] = text[:limit]
        action = "updated"
    db.save_config(ctx.guild.id, cfg_all)
    return cfg[key], action


def _parse_parts(value):
    return [part.strip() for part in value.split("|")]


def reset_tickets_config(cfg):
    """Fresh default ticket config, keeping the operational (where/how) keys."""
    fresh = copy.deepcopy(db.DEFAULT_CONFIG["tickets"])
    for key in _TICKET_KEEP_ON_RESET:
        fresh[key] = (cfg or {}).get(key)
    return fresh


# --- prefix commands -------------------------------------------------------

def _usage():
    embed = discord.Embed(
        title="\U0001f3ab Tickets",
        color=TICKET_COLOR,
        description=(
            "**Setup**\n"
            "`!tickets category #channel` \u2014 where ticket channels are created\n"
            "`!tickets setup [#channel]` \u2014 post the customizable panel\n"
            "`!tickets prefix <name>` \u2014 channel name prefix (default `ticket`)\n"
            "`!tickets log #channel|off` \u2014 open/close log channel\n"
            "`!tickets ping @role|off` \u2014 optional role ping above the panel\n"
            "\n**Panel look**\n"
            "`!tickets title|footer|message <text|reset>` \u00b7 "
            "`!tickets color <#hex|reset>`\n"
            "`!tickets thumbnail|image <url|off>` \u00b7 "
            "`!tickets button <label> | <emoji> | <style>`\n"
            "\n**Inside a ticket**\n"
            "`!tickets ticket_title|ticket_color|welcome <text|reset>`\n"
            "`!tickets claimmsg|closemsg <text|reset>` \u00b7 "
            "`!tickets claimbtn|closebtn <label> | <emoji>`\n"
            "`!tickets modal <title> | <label>`\n"
            "\n**Actions**\n"
            "`!tickets preview` \u00b7 `!tickets reset` (keeps category/prefix/log)\n"
            "`!tickets list` \u00b7 `!tickets close [#channel]`\n"
            "Variables: `{user}` `{name}` `{server}` `{ticket}` `{index}`"
        ),
    )
    return embed


@modbot.group(name="tickets", invoke_without_command=True)
@mod_only()
async def tickets_group(ctx):
    await ctx.send(embed=_usage())


@tickets_group.command(name="setup")
@mod_only()
async def tickets_setup(ctx, channel: discord.TextChannel = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if not cfg.get("category"):
        await ctx.send("Set a category first: `!tickets category #channel`.")
        return
    target = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel)
                         else None)
    if target is None:
        await ctx.send("Pass a text channel: `!tickets setup #channel`.")
        return

    old_id = cfg.get("panel_message_id")
    old_channel = ctx.guild.get_channel(int(cfg["panel_channel"])) \
        if cfg.get("panel_channel") else None
    if old_id and isinstance(old_channel, discord.TextChannel):
        try:
            await old_channel.get_partial_message(int(old_id)).delete()
        except Exception:
            pass

    try:
        message = await post_panel(target, cfg, ctx.guild)
    except Exception as exc:
        await ctx.send(f"Couldn't post the panel: `{type(exc).__name__}: {exc}`")
        return
    cfg["panel_channel"] = target.id
    cfg["panel_message_id"] = message.id
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Ticket panel posted in {target.mention}. "
                   "Use `!tickets preview` to check changes before reposting.")


@tickets_group.command(name="preview")
@mod_only()
async def tickets_preview(ctx):
    cfg = db.get_config(ctx.guild.id)["tickets"]
    if not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("Run this in a text channel.")
        return
    try:
        await post_panel(ctx.channel, cfg, ctx.guild, preview=True)
    except Exception as exc:
        await ctx.send(f"Couldn't build the panel: `{type(exc).__name__}: {exc}`")
        return
    await ctx.send("-# Preview only \u2014 the button still opens real tickets. "
                   "Run `!tickets setup` to post the official panel.")


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
    value, action = _text_setting(ctx, "panel_message", text,
                                  db.DEFAULT_PANEL_MESSAGE, 1000)
    if action is None:
        await ctx.send(f"Panel message:\n{value}")
    else:
        await ctx.send(f"Panel message {action}. "
                       "`!tickets preview` or `!tickets setup` to see it.")


@tickets_group.command(name="title")
@mod_only()
async def tickets_title(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "panel_title", text,
                                  db.DEFAULT_PANEL_TITLE, 256, allow_off=True)
    if action is None:
        await ctx.send(f"Panel title: `{value or '(none)'}`")
    else:
        await ctx.send(f"Panel title {action}.")


@tickets_group.command(name="footer")
@mod_only()
async def tickets_footer(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "panel_footer", text,
                                  db.DEFAULT_PANEL_FOOTER, 2000, allow_off=True)
    if action is None:
        await ctx.send(f"Panel footer: `{value or '(none)'}`")
    else:
        await ctx.send(f"Panel footer {action}.")


@tickets_group.command(name="color")
@mod_only()
async def tickets_color(ctx, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Panel colour: `{cfg.get('panel_color')}`")
        return
    if value.lower() == "reset":
        cfg["panel_color"] = db.DEFAULT_CONFIG["tickets"]["panel_color"]
    else:
        parsed = parse_color(value, default=None)
        if parsed is None:
            await ctx.send("Use a hex colour like `#57F287`.")
            return
        cfg["panel_color"] = f"#{parsed:06x}"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Panel colour set to `{cfg['panel_color']}`.")


@tickets_group.command(name="thumbnail")
@mod_only()
async def tickets_thumbnail(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Panel thumbnail: `{cfg.get('panel_thumbnail') or '(none)'}`")
        return
    lowered = value.strip().lower()
    if lowered in ("off", "none", "reset"):
        cfg["panel_thumbnail"] = ""
    elif not value.startswith(("http://", "https://")):
        await ctx.send("Pass an image URL (or `off`).")
        return
    else:
        cfg["panel_thumbnail"] = value.strip()
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Panel thumbnail updated.")


@tickets_group.command(name="image")
@mod_only()
async def tickets_image(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Panel image: `{cfg.get('panel_image') or '(none)'}`")
        return
    lowered = value.strip().lower()
    if lowered in ("off", "none", "reset"):
        cfg["panel_image"] = ""
    elif not value.startswith(("http://", "https://")):
        await ctx.send("Pass an image URL (or `off`).")
        return
    else:
        cfg["panel_image"] = value.strip()
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Panel image updated.")


@tickets_group.command(name="button")
@mod_only()
async def tickets_button(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(
            f"Panel button: **{cfg.get('panel_button_label')}** \u00b7 "
            f"emoji `{cfg.get('panel_button_emoji') or '(none)'}` \u00b7 "
            f"style `{cfg.get('panel_button_style')}`\n"
            "Usage: `!tickets button <label> | <emoji> | <style>` "
            "(`reset` for defaults)")
        return
    if value.strip().lower() == "reset":
        cfg["panel_button_label"] = db.DEFAULT_CONFIG["tickets"]["panel_button_label"]
        cfg["panel_button_emoji"] = db.DEFAULT_CONFIG["tickets"]["panel_button_emoji"]
        cfg["panel_button_style"] = db.DEFAULT_CONFIG["tickets"]["panel_button_style"]
    else:
        parts = _parse_parts(value)
        label = parts[0] or cfg.get("panel_button_label")
        emoji = parts[1] if len(parts) > 1 else cfg.get("panel_button_emoji")
        style = (parts[2].lower() if len(parts) > 2 and parts[2]
                 else cfg.get("panel_button_style"))
        if style not in db.BUTTON_STYLES:
            await ctx.send("Style must be one of: " +
                           ", ".join(f"`{s}`" for s in db.BUTTON_STYLES) + ".")
            return
        cfg["panel_button_label"] = label[:80]
        cfg["panel_button_emoji"] = ("" if parse_emoji(emoji) is None
                                     else str(emoji).strip())
        cfg["panel_button_style"] = style
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Panel button set to **{cfg['panel_button_label']}** "
                   f"({cfg['panel_button_style']}).")


@tickets_group.command(name="ping")
@mod_only()
async def tickets_ping(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Panel ping: {cfg.get('panel_ping') or 'off'}")
        return
    lowered = value.strip().lower()
    if lowered in ("off", "none", "reset"):
        cfg["panel_ping"] = ""
        msg = "Panel ping cleared."
    else:
        raw = value.strip().strip("<@&!>")
        role = ctx.guild.get_role(int(raw)) if raw.isdigit() else None
        if role is None:
            role = next((r for r in ctx.guild.roles
                         if r.name.lower() == value.strip().lstrip("@").lower()),
                        None)
        if role is None:
            await ctx.send("Pass a role mention/ID (or `off`).")
            return
        cfg["panel_ping"] = role.mention
        msg = f"The panel will ping {role.mention} when posted."
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


@tickets_group.command(name="ticket_title")
@mod_only()
async def tickets_ticket_title(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "ticket_title", text,
                                  db.DEFAULT_TICKET_TITLE, 256, allow_off=True)
    if action is None:
        await ctx.send(f"Ticket embed title: `{value or '(none)'}`")
    else:
        await ctx.send(f"Ticket embed title {action}.")


@tickets_group.command(name="ticket_color")
@mod_only()
async def tickets_ticket_color(ctx, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Ticket embed colour: `{cfg.get('ticket_color')}`")
        return
    if value.lower() == "reset":
        cfg["ticket_color"] = db.DEFAULT_CONFIG["tickets"]["ticket_color"]
    else:
        parsed = parse_color(value, default=None)
        if parsed is None:
            await ctx.send("Use a hex colour like `#57F287`.")
            return
        cfg["ticket_color"] = f"#{parsed:06x}"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Ticket embed colour set to `{cfg['ticket_color']}`.")


@tickets_group.command(name="welcome")
@mod_only()
async def tickets_welcome(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "ticket_welcome", text,
                                  db.DEFAULT_TICKET_WELCOME, 4000, allow_off=True)
    if action is None:
        await ctx.send(f"Ticket welcome text:\n{value or '(none)'}")
    else:
        await ctx.send(f"Ticket welcome text {action}.")


@tickets_group.command(name="claimmsg")
@mod_only()
async def tickets_claimmsg(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "claim_message", text,
                                  db.DEFAULT_CLAIM_MESSAGE, 2000)
    if action is None:
        await ctx.send(f"Claim message:\n{value}")
    else:
        await ctx.send(f"Claim message {action}.")


@tickets_group.command(name="closemsg")
@mod_only()
async def tickets_closemsg(ctx, *, text: str = None):
    value, action = _text_setting(ctx, "close_message", text,
                                  db.DEFAULT_CLOSE_MESSAGE, 2000)
    if action is None:
        await ctx.send(f"Close message:\n{value}")
    else:
        await ctx.send(f"Close message {action}.")


@tickets_group.command(name="claimbtn")
@mod_only()
async def tickets_claimbtn(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Claim button: **{cfg.get('claim_button_label')}** \u00b7 "
                       f"emoji `{cfg.get('claim_button_emoji') or '(none)'}`\n"
                       "Usage: `!tickets claimbtn <label> | <emoji>`")
        return
    if value.strip().lower() == "reset":
        cfg["claim_button_label"] = db.DEFAULT_CONFIG["tickets"]["claim_button_label"]
        cfg["claim_button_emoji"] = db.DEFAULT_CONFIG["tickets"]["claim_button_emoji"]
    else:
        parts = _parse_parts(value)
        cfg["claim_button_label"] = (parts[0] or
                                     cfg.get("claim_button_label"))[:80]
        if len(parts) > 1:
            cfg["claim_button_emoji"] = ("" if parse_emoji(parts[1]) is None
                                         else parts[1])
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Claim button set to **{cfg['claim_button_label']}**.")


@tickets_group.command(name="closebtn")
@mod_only()
async def tickets_closebtn(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Close button: **{cfg.get('close_button_label')}** \u00b7 "
                       f"emoji `{cfg.get('close_button_emoji') or '(none)'}`\n"
                       "Usage: `!tickets closebtn <label> | <emoji>`")
        return
    if value.strip().lower() == "reset":
        cfg["close_button_label"] = db.DEFAULT_CONFIG["tickets"]["close_button_label"]
        cfg["close_button_emoji"] = db.DEFAULT_CONFIG["tickets"]["close_button_emoji"]
    else:
        parts = _parse_parts(value)
        cfg["close_button_label"] = (parts[0] or
                                     cfg.get("close_button_label"))[:80]
        if len(parts) > 1:
            cfg["close_button_emoji"] = ("" if parse_emoji(parts[1]) is None
                                         else parts[1])
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Close button set to **{cfg['close_button_label']}**.")


@tickets_group.command(name="modal")
@mod_only()
async def tickets_modal(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["tickets"]
    if value is None:
        await ctx.send(f"Modal title: **{cfg.get('modal_title')}** \u00b7 "
                       f"input label: **{cfg.get('modal_label')}**\n"
                       "Usage: `!tickets modal <title> | <label>`")
        return
    if value.strip().lower() == "reset":
        cfg["modal_title"] = db.DEFAULT_CONFIG["tickets"]["modal_title"]
        cfg["modal_label"] = db.DEFAULT_CONFIG["tickets"]["modal_label"]
    else:
        parts = _parse_parts(value)
        cfg["modal_title"] = (parts[0] or cfg.get("modal_title"))[:45]
        if len(parts) > 1:
            cfg["modal_label"] = (parts[1] or cfg.get("modal_label"))[:45]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Modal set to **{cfg['modal_title']}** \u00b7 "
                   f"**{cfg['modal_label']}**.")


@tickets_group.command(name="reset")
@mod_only()
async def tickets_reset(ctx):
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["tickets"] = reset_tickets_config(cfg_all["tickets"])
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Ticket look and messages reset to defaults "
                   "(category, prefix, panel and log settings kept). "
                   "Run `!tickets preview` to check, then `!tickets setup`.")


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
    cfg = db.get_config(ctx.guild.id)["tickets"]
    tickets = _tickets(ctx.guild.id)
    rows = []
    for channel_id, rec in tickets.items():
        channel = ctx.guild.get_channel(int(channel_id))
        if channel is None:
            continue
        claimer = f" \u00b7 claimed <@{rec['claimer']}>" if rec.get("claimer") else ""
        rows.append(f"{channel.mention} \u00b7 <@{rec.get('creator')}>{claimer}")
    embed = action_embed("Open Tickets",
                         parse_color(cfg.get("ticket_color"), TICKET_COLOR))
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
        TicketModal(interaction.guild.id, interaction.channel.id, cfg))
