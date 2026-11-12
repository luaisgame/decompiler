"""Welcoming: configurable greeting message + autorole on join."""
import discord
from discord import app_commands

from . import db
from .core import (action_embed, deny_unless_mod, guild_slash, mod_log, mod_only,
                   modbot, render)

WELCOME_COLOR = 0x2ECC71


def welcome_text(cfg, member):
    """Render the configured greeting for a member (pure, unit-tested)."""
    template = cfg.get("message") or db.DEFAULT_WELCOME
    return render(
        template,
        user=member.mention,
        server=member.guild.name,
        count=member.guild.member_count,
        name=member.display_name,
    )[:2000]


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
    try:
        await channel.send(text)
    except Exception as exc:
        print(f"[MODBOT] Welcome message failed: {exc}")
        return None
    return text


async def grant_autorole(guild, cfg, member):
    role_id = cfg.get("autorole")
    if not role_id:
        return
    role = guild.get_role(int(role_id))
    if role is None:
        return
    me = guild.me
    if me is None:
        print("[MODBOT] Guild me not cached; skipping autorole.")
        return
    if role >= me.top_role:
        print(f"[MODBOT] Autorole {role.name} is above my top role; skipping.")
        return
    try:
        await member.add_roles(role, reason="Welcome autorole")
    except Exception as exc:
        print(f"[MODBOT] Autorole failed: {exc}")


@modbot.listen("on_member_join")
async def welcome_listener(member):
    if member.bot or member.guild is None:
        return
    cfg = db.get_config(member.guild.id)["welcome"]
    if not cfg.get("enabled"):
        return
    try:
        await grant_autorole(member.guild, cfg, member)
        await send_welcome(member.guild, cfg, member)
    except Exception as exc:
        print(f"[MODBOT] Welcome handler failed: {exc}")


def _summary(guild_id):
    cfg = db.get_config(guild_id)["welcome"]
    channel = f"<#{cfg['channel']}>" if cfg.get("channel") else "not set"
    role = f"<@&{cfg['autorole']}>" if cfg.get("autorole") else "none"
    embed = action_embed(
        "Welcome", WELCOME_COLOR,
        status=("on" if cfg.get("enabled") else "off"),
        channel=channel,
        autorole=role,
        message=cfg.get("message") or db.DEFAULT_WELCOME,
    )
    embed.description = (
        "Variables: `{user}` `{server}` `{count}` `{name}`\n"
        "Commands: `!welcome channel #ch|off` \u00b7 `!welcome message <text>` \u00b7 "
        "`!welcome on|off` \u00b7 `!welcome autorole @role|off` \u00b7 `!welcome test`"
    )
    return embed


# --- prefix commands -------------------------------------------------------

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
async def welcome_autorole(ctx, role: discord.Role = None):
    cfg_all = db.get_config(ctx.guild.id)
    if role is None:
        cfg_all["welcome"]["autorole"] = None
        msg = "Autorole removed."
    else:
        if role >= ctx.guild.me.top_role:
            await ctx.send("That role is at or above my top role \u2014 move it below "
                           "mine and try again.")
            return
        cfg_all["welcome"]["autorole"] = role.id
        msg = f"New members will get {role.mention}."
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(msg)


@welcome_group.command(name="test")
@mod_only()
async def welcome_test(ctx, member: discord.Member = None):
    member = member or ctx.author
    cfg = db.get_config(ctx.guild.id)["welcome"]
    await ctx.send(welcome_text(cfg, member))
    if cfg.get("autorole"):
        await ctx.send(f"(autorole would also grant <@&{cfg['autorole']}>)")


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
        await interaction.response.send_message(
            welcome_text(cfg, interaction.user))
    else:
        await interaction.response.send_message(embed=_summary(interaction.guild.id),
                                                ephemeral=True)
