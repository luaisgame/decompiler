"""Warning system: record, look up, remove and auto-act on warns."""
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ..core import parse_duration, parse_user_id
from . import db
from .core import (action_embed, deny_unless_mod, find_member, fmt_ts, guild_slash,
                   hierarchy_error, mod_log, mod_only, modbot, render)

ACTIONS = ("none", "kick", "mute", "ban")
WARN_COLOR = 0xF1C40F


# --- record layer (pure, unit-tested) --------------------------------------

def _is_active(rec, now):
    expires = rec.get("expires") or 0
    return not expires or expires > now


def split_warns(guild_id, user_id, now=None):
    """(active, expired) warn lists for a user."""
    now = now if now is not None else time.time()
    mine = [w for w in db.get_warns(guild_id) if w.get("user") == user_id]
    active = [w for w in mine if _is_active(w, now)]
    expired = [w for w in mine if not _is_active(w, now)]
    return active, expired


def add_warn(guild_id, user_id, mod_id, reason, expire_days=0, now=None):
    now = now if now is not None else time.time()
    warns = db.get_warns(guild_id)
    warn_id = max((w.get("id", 0) for w in warns), default=0) + 1
    rec = {
        "id": warn_id,
        "user": user_id,
        "mod": mod_id,
        "reason": reason or "No reason provided",
        "ts": now,
        "expires": (now + expire_days * 86400) if expire_days else 0,
    }
    warns.append(rec)
    db.save_warns(guild_id, warns)
    return rec


def remove_warn(guild_id, user_id, warn_id):
    """Delete one warn by id; True when something was removed."""
    warns = db.get_warns(guild_id)
    kept = [w for w in warns
            if not (w.get("user") == user_id and w.get("id") == warn_id)]
    if len(kept) == len(warns):
        return False
    db.save_warns(guild_id, kept)
    return True


def clear_warns(guild_id, user_id):
    """Drop every warn for a user; returns how many were removed."""
    warns = db.get_warns(guild_id)
    kept = [w for w in warns if w.get("user") != user_id]
    removed = len(warns) - len(kept)
    if removed:
        db.save_warns(guild_id, kept)
    return removed


def decide_action(count, policy):
    """Auto-action to fire once a member reaches the configured threshold."""
    try:
        threshold = int((policy or {}).get("threshold") or 0)
    except (TypeError, ValueError):
        return None
    if threshold <= 0 or count < threshold:
        return None
    action = str((policy or {}).get("action") or "none").lower()
    return action if action in ACTIONS else None


# --- enforcement -----------------------------------------------------------

async def apply_action(guild, member, action, policy, reason):
    """Kick / timeout / ban for the auto-action; returns a status line or None."""
    try:
        if action == "kick":
            await member.kick(reason=reason)
            return f"\U0001f528 Automatic kick applied to {member.mention}."
        if action == "ban":
            await guild.ban(member, reason=reason, delete_message_seconds=0)
            return f"\U0001f528 Automatic ban applied to {member.mention}."
        if action == "mute":
            hours = int((policy or {}).get("mute_hours") or 24)
            await member.timeout(timedelta(hours=hours), reason=reason)
            return f"\U0001f504 Automatic timeout applied to {member.mention} for {hours}h."
    except Exception as exc:
        return f"\u26a0\ufe0f Automatic `{action}` failed: {exc}"
    return None


def _warn_embed(guild, actor, member, rec, active_count):
    embed = action_embed(
        "Member Warned", WARN_COLOR,
        member=f"{member.mention} ({member.id})",
        moderator=actor.mention,
        reason=rec["reason"],
    )
    embed.add_field(name="Warns", value=f"#{rec['id']} \u00b7 {active_count} active", inline=True)
    if rec.get("expires"):
        embed.set_footer(text=f"Expires {fmt_ts(rec['expires'], 'R')}")
    return embed


async def perform_warn(guild, actor, member, reason):
    """Record a warn, notify/log it, fire any auto-action.

    Returns (text, embed) for the caller to send.
    """
    policy = db.get_config(guild.id)["warns"]
    rec = add_warn(guild.id, member.id, actor.id, reason,
                   int(policy.get("expire_days") or 0))
    active, _expired = split_warns(guild.id, member.id)
    embed = _warn_embed(guild, actor, member, rec, len(active))

    try:
        dm = discord.Embed(
            title=f"You were warned in {guild.name}",
            color=WARN_COLOR,
            description=rec["reason"],
        )
        dm.add_field(name="Active warns", value=str(len(active)), inline=True)
        dm.set_footer(text=f"Warn #{rec['id']}")
        await member.send(embed=dm)
    except Exception:
        pass

    await mod_log(guild, embed)

    text = f"Warned {member.mention} \u2014 now at **{len(active)}** active warn(s)."
    action = decide_action(len(active), policy)
    if action:
        outcome = await apply_action(
            guild, member, action, policy,
            f"Reached {len(active)} warns ({guild.name})")
        if outcome:
            text += f"\n{outcome}"
    return text, embed


def _warns_embed(label, active, expired):
    lines = []
    for rec in active[-15:]:
        line = f"**#{rec['id']}** \u2014 {rec['reason']}\n" \
               f"<@{rec['mod']}> \u00b7 {fmt_ts(rec['ts'])}"
        if rec.get("expires"):
            line += f" \u00b7 expires {fmt_ts(rec['expires'], 'R')}"
        lines.append(line)
    embed = discord.Embed(
        title=f"Warns for {label}",
        color=WARN_COLOR if active else 0x2ECC71,
        description="\n".join(lines) or "No active warns \u2705",
    )
    footer = f"{len(active)} active \u00b7 {len(expired)} expired"
    if len(active) > 15:
        footer += f" \u00b7 showing last 15 of {len(active)}"
    embed.set_footer(text=footer)
    return embed


# --- prefix commands -------------------------------------------------------

@modbot.command(name="warn")
@mod_only()
async def warn_cmd(ctx, target: str, *, reason: str = "No reason provided"):
    member = await find_member(ctx.guild, target)
    if member is None:
        await ctx.send("Couldn't find that member \u2014 mention them or paste their ID.")
        return
    if member.bot:
        await ctx.send("You can't warn a bot.")
        return
    problem = hierarchy_error(ctx.guild, ctx.author, member)
    if problem:
        await ctx.send(f"You can't warn {problem}.")
        return
    text, embed = await perform_warn(ctx.guild, ctx.author, member, reason)
    await ctx.send(text, embed=embed)


@modbot.command(name="warns")
@mod_only()
async def warns_cmd(ctx, target: str):
    member = await find_member(ctx.guild, target)
    uid = member.id if member else parse_user_id(target)
    if uid is None:
        await ctx.send("Couldn't find that user \u2014 mention them or paste their ID.")
        return
    active, expired = split_warns(ctx.guild.id, uid)
    if member is None:
        try:
            member = await modbot.fetch_user(uid)
        except Exception:
            member = None
    label = member.mention if member else f"<@{uid}> (`{uid}`)"
    await ctx.send(embed=_warns_embed(label, active, expired))


@modbot.command(name="removewarn")
@mod_only()
async def removewarn_cmd(ctx, target: str, warn_id: int):
    member = await find_member(ctx.guild, target)
    uid = member.id if member else parse_user_id(target)
    if uid is None:
        await ctx.send("Couldn't find that user \u2014 mention them or paste their ID.")
        return
    if remove_warn(ctx.guild.id, uid, warn_id):
        await ctx.send(f"Removed warn `#{warn_id}` from <@{uid}>.")
        await mod_log(ctx.guild, action_embed(
            "Warn Removed", 0x2ECC71,
            member=f"<@{uid}>",
            moderator=ctx.author.mention,
            warn=f"#{warn_id}",
        ))
    else:
        await ctx.send(f"No warn `#{warn_id}` found for <@{uid}>.")


@modbot.command(name="clearwarns")
@mod_only()
async def clearwarns_cmd(ctx, target: str):
    member = await find_member(ctx.guild, target)
    uid = member.id if member else parse_user_id(target)
    if uid is None:
        await ctx.send("Couldn't find that user \u2014 mention them or paste their ID.")
        return
    removed = clear_warns(ctx.guild.id, uid)
    await ctx.send(f"Cleared **{removed}** warn(s) from <@{uid}>.")
    if removed:
        await mod_log(ctx.guild, action_embed(
            "Warns Cleared", 0x2ECC71,
            member=f"<@{uid}>",
            moderator=ctx.author.mention,
            removed=str(removed),
        ))


@modbot.command(name="warnconfig")
@mod_only()
async def warnconfig_cmd(ctx, setting: str = None, value: str = None):
    cfg = db.get_config(ctx.guild.id)
    policy = cfg["warns"]
    if setting is None:
        embed = action_embed(
            "Warn Policy", WARN_COLOR,
            threshold=str(policy.get("threshold") or 0) + " (0 = disabled)",
            action=str(policy.get("action") or "none"),
            expire=(f"{policy.get('expire_days')} day(s)" if policy.get("expire_days") else "never"),
            mute=f"{policy.get('mute_hours')}h",
        )
        embed.description = (
            "Settings: `!warnconfig threshold <n>` \u00b7 "
            "`!warnconfig action none|kick|mute|ban` \u00b7 "
            "`!warnconfig expire <duration>` \u00b7 "
            "`!warnconfig mute <duration>`"
        )
        await ctx.send(embed=embed)
        return

    key = setting.lower()
    if key in ("threshold", "count"):
        if not (value or "").isdigit():
            await ctx.send("Usage: `!warnconfig threshold <number>`")
            return
        policy["threshold"] = int(value)
    elif key == "action":
        action = (value or "").lower()
        if action not in ACTIONS:
            await ctx.send("Usage: `!warnconfig action none|kick|mute|ban`")
            return
        policy["action"] = action
    elif key in ("expire", "expires"):
        if (value or "").lower() in ("off", "never", "0"):
            policy["expire_days"] = 0
        else:
            seconds = parse_duration(value or "")
            if not seconds:
                await ctx.send("Usage: `!warnconfig expire <duration>` (e.g. `30d`) or `off`.")
                return
            policy["expire_days"] = max(1, round(seconds / 86400))
    elif key in ("mute", "timeout"):
        if (value or "").lower() in ("off", "never", "0"):
            policy["mute_hours"] = 24
        else:
            seconds = parse_duration(value or "")
            if not seconds:
                await ctx.send("Usage: `!warnconfig mute <duration>` (e.g. `6h`).")
                return
            policy["mute_hours"] = max(1, round(seconds / 3600))
    else:
        await ctx.send("Unknown setting. Try `threshold`, `action`, `expire` or `mute`.")
        return

    cfg["warns"] = policy
    db.save_config(ctx.guild.id, cfg)
    await ctx.send(
        f"Warn policy updated \u2014 threshold **{policy['threshold']}**, "
        f"action **{policy['action']}**, "
        f"expires **{policy['expire_days'] or 'never'} day(s)**, "
        f"mute **{policy['mute_hours']}h**.")


# --- slash commands --------------------------------------------------------

@modbot.tree.command(name="warn", description="Warn a member")
@guild_slash
@app_commands.describe(user="Member to warn", reason="Reason for the warn")
async def warn_slash(interaction: discord.Interaction, user: discord.User,
                     reason: str = "No reason provided"):
    if not await deny_unless_mod(interaction):
        return
    guild = interaction.guild
    member = guild.get_member(user.id)
    if member is None:
        try:
            member = await guild.fetch_member(user.id)
        except Exception:
            await interaction.response.send_message(
                "That user isn't in this server.", ephemeral=True)
            return
    if member.bot:
        await interaction.response.send_message("You can't warn a bot.", ephemeral=True)
        return
    problem = hierarchy_error(guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(f"You can't warn {problem}.", ephemeral=True)
        return
    text, embed = await perform_warn(guild, interaction.user, member, reason)
    await interaction.response.send_message(text, embed=embed)


@modbot.tree.command(name="warns", description="Look up a member's warns")
@guild_slash
@app_commands.describe(user="Member to look up")
async def warns_slash(interaction: discord.Interaction, user: discord.User):
    if not await deny_unless_mod(interaction):
        return
    active, expired = split_warns(interaction.guild.id, user.id)
    embed = _warns_embed(str(user), active, expired)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@modbot.tree.command(name="clearwarns", description="Remove every warn from a member")
@guild_slash
@app_commands.describe(user="Member to clear")
async def clearwarns_slash(interaction: discord.Interaction, user: discord.User):
    if not await deny_unless_mod(interaction):
        return
    removed = clear_warns(interaction.guild.id, user.id)
    await interaction.response.send_message(
        f"Cleared **{removed}** warn(s) from {user.mention}.")
    if removed:
        await mod_log(interaction.guild, action_embed(
            "Warns Cleared", 0x2ECC71,
            member=user.mention,
            moderator=interaction.user.mention,
            removed=str(removed),
        ))


@modbot.tree.command(name="removewarn", description="Remove one warn by its ID")
@guild_slash
@app_commands.describe(user="Member", warn_id="The warn number from !warns")
async def removewarn_slash(interaction: discord.Interaction, user: discord.User,
                           warn_id: int):
    if not await deny_unless_mod(interaction):
        return
    if remove_warn(interaction.guild.id, user.id, warn_id):
        await interaction.response.send_message(
            f"Removed warn `#{warn_id}` from {user.mention}.")
        await mod_log(interaction.guild, action_embed(
            "Warn Removed", 0x2ECC71,
            member=user.mention,
            moderator=interaction.user.mention,
            warn=f"#{warn_id}",
        ))
    else:
        await interaction.response.send_message(
            f"No warn `#{warn_id}` found for {user.mention}.", ephemeral=True)
