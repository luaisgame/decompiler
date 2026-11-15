"""Welcoming: customizable greeting, welcome DM and autoroles on join."""
import asyncio
import copy

import discord
from discord import app_commands

from . import db
from .core import (action_embed, deny_unless_mod, guild_slash, mod_only, modbot,
                   parse_color, render, text_or_none)

WELCOME_COLOR = 0x2ECC71
MAX_AUTOROLE_DELAY = 600  # seconds; keep a runaway config from parking a task forever

# `!welcome reset` resets the greeting/look but keeps the operational setup
# (channel, switches, autoroles) so a formatting reset can't break a server.
_WELCOME_KEEP_ON_RESET = ("enabled", "channel", "autorole", "autoroles",
                          "autorole_delay")


# --- pure helpers ----------------------------------------------------------

def welcome_values(member):
    return {
        "user": member.mention,
        "server": member.guild.name,
        "count": member.guild.member_count,
        "name": member.display_name,
    }


def welcome_text(cfg, member, limit=2000):
    """Render the configured greeting for a member (pure, unit-tested)."""
    template = cfg.get("message") or db.DEFAULT_WELCOME
    return render(template, **welcome_values(member))[:limit]


def welcome_dm_text(cfg, member):
    template = cfg.get("dm_message") or db.DEFAULT_WELCOME_DM
    return render(template, **welcome_values(member))[:2000]


def welcome_embed(cfg, member):
    """Build the configured welcome embed, or None when embeds are off."""
    if not cfg.get("embed_enabled"):
        return None
    values = welcome_values(member)
    title = render(cfg.get("embed_title") or "", **values).strip()
    embed = discord.Embed(
        title=title[:256] or None,
        description=welcome_text(cfg, member, limit=4000),
        color=parse_color(cfg.get("embed_color"), WELCOME_COLOR),
    )
    thumbnail = str(cfg.get("embed_thumbnail") or "off").strip().lower()
    if thumbnail == "user":
        avatar = getattr(member, "display_avatar", None)
        if avatar is not None:
            embed.set_thumbnail(url=avatar.url)
    elif thumbnail == "server" and member.guild.icon is not None:
        embed.set_thumbnail(url=member.guild.icon.url)
    image = text_or_none(cfg.get("embed_image"))
    if image:
        embed.set_image(url=image)
    footer = render(cfg.get("embed_footer") or "", **values).strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


def autorole_ids(cfg):
    """Merged autorole list (new `autoroles` first, legacy `autorole` last)."""
    ids = []
    for raw in list(cfg.get("autoroles") or []) + [cfg.get("autorole")]:
        if raw is None:
            continue
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            continue
        if role_id not in ids:
            ids.append(role_id)
    return ids


def autorole_delay(cfg):
    try:
        seconds = int(cfg.get("autorole_delay") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_AUTOROLE_DELAY, seconds))


def reset_welcome_config(cfg):
    """Default welcome config, keeping the operational (where/who) keys."""
    fresh = copy.deepcopy(db.DEFAULT_CONFIG["welcome"])
    for key in _WELCOME_KEEP_ON_RESET:
        fresh[key] = (cfg or {}).get(key)
    return fresh


def resolve_roles(guild, text):
    """Resolve role mentions / IDs / exact names; returns (roles, unknown)."""
    roles, unknown = [], []
    for token in str(text or "").replace(",", " ").split():
        cleaned = token.strip("<@&!>")
        role = None
        if cleaned.isdigit():
            role = guild.get_role(int(cleaned))
        if role is None:
            name = token.lstrip("@").lower()
            role = next((r for r in guild.roles if r.name.lower() == name), None)
        if role is None:
            unknown.append(token)
        elif role not in roles:
            roles.append(role)
    return roles, unknown


def role_problem(guild, role):
    """Why we can't grant `role`, or None when it's fine."""
    me = guild.me
    if role.is_default():
        return "that's the @everyone role"
    if role.managed:
        return "that role is managed by an integration"
    if me is not None and role >= me.top_role:
        return "that role is at or above my top role"
    return None


# --- join handling ---------------------------------------------------------

async def send_welcome(guild, cfg, member):
    """Post the greeting to the configured channel; returns the text or None."""
    ch_id = cfg.get("channel")
    if not ch_id:
        return None
    channel = guild.get_channel(int(ch_id))
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(ch_id))
        except Exception:
            return None
    text = welcome_text(cfg, member)
    embed = welcome_embed(cfg, member)
    try:
        if embed is not None:
            await channel.send(embed=embed)
        else:
            await channel.send(text)
    except Exception as exc:
        print(f"[MODBOT] Welcome message failed: {exc}")
        return None
    return embed.description if embed is not None else text


async def send_welcome_dm(cfg, member):
    """DM the configured welcome text; returns the text or None."""
    if not cfg.get("dm_enabled"):
        return None
    text = welcome_dm_text(cfg, member)
    try:
        await member.send(text)
    except Exception as exc:
        print(f"[MODBOT] Welcome DM failed for {member.id}: {exc}")
        return None
    return text


async def grant_autoroles(guild, cfg, member):
    """Grant every configured autorole the bot is allowed to hand out."""
    me = guild.me
    if me is None and modbot.user is not None:
        me = guild.get_member(modbot.user.id)
    granted = []
    for role_id in autorole_ids(cfg):
        role = guild.get_role(role_id)
        if role is None:
            continue
        problem = role_problem(guild, role)
        if problem:
            print(f"[MODBOT] Skipping autorole {role.name}: {problem}.")
            continue
        if role in member.roles:
            continue
        try:
            await member.add_roles(role, reason="Welcome autorole")
            granted.append(role)
        except Exception as exc:
            print(f"[MODBOT] Autorole {role.name} failed: {exc}")
    return granted


@modbot.listen("on_member_join")
async def welcome_listener(member):
    if member.bot or member.guild is None:
        return
    cfg = db.get_config(member.guild.id)["welcome"]
    try:
        # `enabled` only gates the channel greeting; DMs and autoroles have
        # their own switches so they keep working if the greeting is off.
        if cfg.get("enabled"):
            await send_welcome(member.guild, cfg, member)
        await send_welcome_dm(cfg, member)
        delay = autorole_delay(cfg)
        if delay:
            await asyncio.sleep(delay)
            # The member may have left while we waited.
            if member.guild.get_member(member.id) is None:
                return
        await grant_autoroles(member.guild, cfg, member)
    except Exception as exc:
        print(f"[MODBOT] Welcome handler failed: {exc}")


# --- prefix commands -------------------------------------------------------

def _summary(guild_id):
    cfg = db.get_config(guild_id)["welcome"]
    channel = f"<#{cfg['channel']}>" if cfg.get("channel") else "not set"
    role_ids = autorole_ids(cfg)
    roles = ", ".join(f"<@&{r}>" for r in role_ids) or "none"
    delay = autorole_delay(cfg)
    embed = action_embed(
        "Welcome", parse_color(cfg.get("embed_color"), WELCOME_COLOR),
        status=("on" if cfg.get("enabled") else "off"),
        channel=channel,
        autoroles=roles,
        autorole_delay=(f"{delay}s" if delay else "instant"),
        welcome_dm=("on" if cfg.get("dm_enabled") else "off"),
        embed=("on" if cfg.get("embed_enabled") else "off (plain text)"),
        message=cfg.get("message") or db.DEFAULT_WELCOME,
    )
    embed.description = (
        "Variables: `{user}` `{server}` `{count}` `{name}`\n"
        "`!welcome channel #ch|off` \u00b7 `!welcome message <text|reset>` \u00b7 "
        "`!welcome on|off`\n"
        "Autoroles: `!welcome autorole add|remove|list|off` \u00b7 "
        "`!welcome autorole_delay <seconds|off>`\n"
        "DM: `!welcome dm on|off` \u00b7 `!welcome dm_message <text|reset>`\n"
        "Embed: `!welcome embed on|off` \u00b7 `embed_color` \u00b7 `embed_title` \u00b7 "
        "`embed_thumbnail user|server|off` \u00b7 `embed_image` \u00b7 `embed_footer`\n"
        "`!welcome test` \u00b7 `!welcome reset`"
    )
    return embed


@modbot.group(name="welcome", invoke_without_command=True)
@mod_only()
async def welcome_group(ctx):
    await ctx.send(embed=_summary(ctx.guild.id))


@welcome_group.command(name="channel")
@mod_only()
async def welcome_channel(ctx, channel: discord.TextChannel = None):
    cfg_all = db.get_config(ctx.guild.id)
    if channel is None:
        cfg_all["welcome"]["channel"] = None
        msg = "Welcome messages turned off (no channel)."
    else:
        cfg_all["welcome"]["channel"] = channel.id
        cfg_all["welcome"]["enabled"] = True
        msg = f"Welcome messages will be posted in {channel.mention}."
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


@welcome_group.command(name="message")
@mod_only()
async def welcome_message(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        await ctx.send(f"Current message:\n{cfg_all['welcome'].get('message')}")
        return
    if text.lower() == "reset":
        cfg_all["welcome"]["message"] = db.DEFAULT_WELCOME
    else:
        cfg_all["welcome"]["message"] = text[:1800]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome message updated. Use `!welcome test` to preview it.")


@welcome_group.command(name="on")
@mod_only()
async def welcome_on(ctx):
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["welcome"]["enabled"] = True
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome messages are now **on**.")


@welcome_group.command(name="off")
@mod_only()
async def welcome_off(ctx):
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["welcome"]["enabled"] = False
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome messages are now **off**.")


@welcome_group.command(name="autorole")
@mod_only()
async def welcome_autorole(ctx, *, args: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    cfg = cfg_all["welcome"]

    if not args:
        ids = autorole_ids(cfg)
        text = ", ".join(f"<@&{r}>" for r in ids) or "none"
        await ctx.send(f"Autoroles: {text}\n"
                       "Usage: `!welcome autorole add|remove @role` \u00b7 "
                       "`!welcome autorole @role` \u00b7 `!welcome autorole off`")
        return

    parts = args.split()
    action = parts[0].lower()
    current = list(cfg.get("autoroles") or [])

    if action == "list":
        ids = autorole_ids(cfg)
        text = "\n".join(f"\u2022 <@&{r}>" for r in ids) or "No autoroles set."
        await ctx.send(text)
        return

    if action in ("off", "none", "clear", "reset") and len(parts) == 1:
        cfg["autoroles"] = []
        cfg["autorole"] = None
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send("Autoroles cleared.")
        return

    if action in ("add", "remove", "rm", "del", "delete") and len(parts) == 1:
        await ctx.send("Usage: `!welcome autorole add|remove @role` "
                       "(you can list several roles).")
        return

    if action in ("add", "remove", "rm", "del", "delete") and len(parts) > 1:
        roles, unknown = resolve_roles(ctx.guild, " ".join(parts[1:]))
        if unknown:
            await ctx.send("Couldn't find: " + ", ".join(f"`{u}`" for u in unknown))
            return

        if action == "add":
            problems = []
            added = []
            for role in roles:
                problem = role_problem(ctx.guild, role)
                if problem:
                    problems.append(f"{role.mention}: {problem}")
                    continue
                if role.id not in current:
                    current.append(role.id)
                added.append(role)
            cfg["autoroles"] = current
            db.save_config(ctx.guild.id, cfg_all)
            msg = ("New members will now get " +
                   ", ".join(r.mention for r in added) + "." if added else "")
            if problems:
                msg = (msg + "\n" if msg else "") + "\n".join(problems)
            await ctx.send(msg or "Nothing to add.")
            return

        removed = [role for role in roles if role.id in current]
        cfg["autoroles"] = [r for r in current if r not in {x.id for x in removed}]
        try:
            legacy = int(cfg.get("autorole") or 0)
        except (TypeError, ValueError):
            legacy = None
        if legacy in {x.id for x in roles}:
            cfg["autorole"] = None
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send("Removed " + ", ".join(r.mention for r in removed) + "."
                       if removed else "Those roles weren't in the autorole list.")
        return

    # Bare `!welcome autorole @role` keeps the old single-role behaviour by
    # replacing the list with the mentioned role(s).
    roles, unknown = resolve_roles(ctx.guild, args)
    if unknown or not roles:
        await ctx.send("Couldn't find that role. Use a mention, an ID or the exact "
                       "role name \u2014 or `!welcome autorole add @role`.")
        return
    valid, problems = [], []
    for role in roles:
        problem = role_problem(ctx.guild, role)
        if problem:
            problems.append(f"{role.mention}: {problem}")
        else:
            valid.append(role)
    if not valid:
        await ctx.send("\n".join(problems) or "That role can't be granted.")
        return
    cfg["autoroles"] = [role.id for role in valid]
    cfg["autorole"] = None
    db.save_config(ctx.guild.id, cfg_all)
    msg = "New members will get " + ", ".join(r.mention for r in valid) + "."
    if problems:
        msg += "\n" + "\n".join(problems)
    await ctx.send(msg)


@welcome_group.command(name="autorole_delay")
@mod_only()
async def welcome_autorole_delay(ctx, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    current = autorole_delay(cfg_all["welcome"])
    if value is None:
        await ctx.send(f"Autorole delay: **{current}s**" if current else
                       "Autoroles are granted instantly. Usage: "
                       "`!welcome autorole_delay <seconds|off>`")
        return
    if value.lower() in ("off", "none", "instant", "0"):
        seconds = 0
    else:
        try:
            seconds = int(value)
        except ValueError:
            await ctx.send("Usage: `!welcome autorole_delay <seconds|off>` "
                           "(0\u2013600).")
            return
    if seconds < 0 or seconds > MAX_AUTOROLE_DELAY:
        await ctx.send(f"Pick a delay between 0 and {MAX_AUTOROLE_DELAY} seconds.")
        return
    cfg_all["welcome"]["autorole_delay"] = seconds
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Autoroles will be granted {seconds} seconds after a member "
                   "joins." if seconds else "Autoroles are granted instantly.")


@welcome_group.command(name="dm")
@mod_only()
async def welcome_dm(ctx, state: str = None):
    if state is None or state.lower() not in ("on", "off"):
        await ctx.send("Usage: `!welcome dm on|off`")
        return
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["welcome"]["dm_enabled"] = state.lower() == "on"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("New members will get a welcome DM."
                   if cfg_all["welcome"]["dm_enabled"] else
                   "Welcome DMs are now **off**.")


@welcome_group.command(name="dm_message")
@mod_only()
async def welcome_dm_message(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        await ctx.send(f"Current DM message:\n{cfg_all['welcome'].get('dm_message')}")
        return
    if text.lower() == "reset":
        cfg_all["welcome"]["dm_message"] = db.DEFAULT_WELCOME_DM
    else:
        cfg_all["welcome"]["dm_message"] = text[:1800]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome DM updated. Use `!welcome test` to check the setup.")


@welcome_group.command(name="embed")
@mod_only()
async def welcome_embed_cmd(ctx, state: str = None):
    if state is None or state.lower() not in ("on", "off"):
        cfg = db.get_config(ctx.guild.id)["welcome"]
        await ctx.send(f"Welcome embeds are **{'on' if cfg.get('embed_enabled') else 'off'}**. "
                       "Usage: `!welcome embed on|off`")
        return
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["welcome"]["embed_enabled"] = state.lower() == "on"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome messages will use an embed."
                   if cfg_all["welcome"]["embed_enabled"] else
                   "Welcome messages will be plain text.")


@welcome_group.command(name="embed_color")
@mod_only()
async def welcome_embed_color(ctx, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if value is None:
        await ctx.send(f"Embed colour: `{cfg_all['welcome'].get('embed_color')}`")
        return
    if value.lower() == "reset":
        cfg_all["welcome"]["embed_color"] = db.DEFAULT_CONFIG["welcome"]["embed_color"]
    else:
        parsed = parse_color(value, default=None)
        if parsed is None:
            await ctx.send("Use a hex colour like `#57F287`.")
            return
        cfg_all["welcome"]["embed_color"] = f"#{parsed:06x}"
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Embed colour set to `{cfg_all['welcome']['embed_color']}`.")


@welcome_group.command(name="embed_title")
@mod_only()
async def welcome_embed_title(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        await ctx.send(f"Embed title: `{cfg_all['welcome'].get('embed_title') or '(none)'}`")
        return
    lowered = text.strip().lower()
    if lowered == "reset":
        cfg_all["welcome"]["embed_title"] = ""
    elif lowered in ("off", "none"):
        cfg_all["welcome"]["embed_title"] = ""
    else:
        cfg_all["welcome"]["embed_title"] = text[:256]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Embed title updated.")


@welcome_group.command(name="embed_thumbnail")
@mod_only()
async def welcome_embed_thumbnail(ctx, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if value is None:
        await ctx.send(f"Embed thumbnail: **{cfg_all['welcome'].get('embed_thumbnail')}** "
                       "(`user`, `server` or `off`)")
        return
    value = value.lower()
    if value not in ("user", "server", "off", "none", "reset"):
        await ctx.send("Use `user`, `server` or `off`.")
        return
    cfg_all["welcome"]["embed_thumbnail"] = ("off" if value in ("off", "none", "reset")
                                             else value)
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Embed thumbnail set to **{cfg_all['welcome']['embed_thumbnail']}**.")


@welcome_group.command(name="embed_image")
@mod_only()
async def welcome_embed_image(ctx, *, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if value is None:
        await ctx.send(f"Embed image: `{cfg_all['welcome'].get('embed_image') or '(none)'}`")
        return
    lowered = value.strip().lower()
    if lowered in ("off", "none", "reset"):
        cfg_all["welcome"]["embed_image"] = ""
    elif not value.startswith(("http://", "https://")):
        await ctx.send("Pass an image URL (or `off`).")
        return
    else:
        cfg_all["welcome"]["embed_image"] = value.strip()
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Embed image updated.")


@welcome_group.command(name="embed_footer")
@mod_only()
async def welcome_embed_footer(ctx, *, text: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if text is None:
        await ctx.send(f"Embed footer: `{cfg_all['welcome'].get('embed_footer') or '(none)'}`")
        return
    if text.strip().lower() in ("reset", "off", "none"):
        cfg_all["welcome"]["embed_footer"] = ""
    else:
        cfg_all["welcome"]["embed_footer"] = text[:2000]
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Embed footer updated.")


@welcome_group.command(name="reset")
@mod_only()
async def welcome_reset(ctx):
    cfg_all = db.get_config(ctx.guild.id)
    cfg_all["welcome"] = reset_welcome_config(cfg_all["welcome"])
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send("Welcome formatting reset to defaults (channel, switches and "
                   "autoroles kept).")


@welcome_group.command(name="test")
@mod_only()
async def welcome_test(ctx, member: discord.Member = None):
    member = member or ctx.author
    cfg = db.get_config(ctx.guild.id)["welcome"]
    embed = welcome_embed(cfg, member)
    if embed is not None:
        await ctx.send(embed=embed)
    else:
        await ctx.send(welcome_text(cfg, member))

    notes = []
    ids = autorole_ids(cfg)
    if ids:
        delay = autorole_delay(cfg)
        notes.append("autoroles: " + ", ".join(f"<@&{r}>" for r in ids) +
                     (f" (after {delay}s)" if delay else ""))
    if cfg.get("dm_enabled"):
        notes.append(f"welcome DM: {welcome_dm_text(cfg, member)}")
    if notes:
        await ctx.send(("-# Test: " + " \u00b7 ".join(notes))[:2000])


# --- slash commands --------------------------------------------------------

@modbot.tree.command(name="welcome", description="Preview or configure welcomes")
@guild_slash
@app_commands.describe(action="What to do")
@app_commands.choices(action=[
    app_commands.Choice(name="preview (send a test greeting here)", value="preview"),
    app_commands.Choice(name="show settings", value="settings"),
])
async def welcome_slash(interaction: discord.Interaction, action: str = "preview"):
    if not await deny_unless_mod(interaction):
        return
    if action == "preview":
        cfg = db.get_config(interaction.guild.id)["welcome"]
        embed = welcome_embed(cfg, interaction.user)
        if embed is not None:
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                welcome_text(cfg, interaction.user))
    else:
        await interaction.response.send_message(embed=_summary(interaction.guild.id),
                                                ephemeral=True)
