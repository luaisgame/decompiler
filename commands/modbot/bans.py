"""Ban / kick support: permanent bans, timed bans and unbans."""
import asyncio
import time

import discord
from discord import app_commands

from ..core import parse_duration, parse_user_id
from . import db
from .core import (action_embed, deny_unless_mod, fmt_ts, find_member, guild_slash,
                   hierarchy_error, humanize, mod_log, mod_only, modbot, resolve_user)

BAN_COLOR = 0xE74C3C

# (guild_id, user_id) -> asyncio.Task for pending temp unbans
_scheduled = {}


# --- temp-ban schedule -----------------------------------------------------

def schedule_unban(guild_id, user_id, until, reason="Temp ban expired"):
    """(Re)arm the timer that lifts a temp ban; runs unban immediately if due."""
    key = (guild_id, user_id)
    old = _scheduled.pop(key, None)
    if old is not None and not old.done():
        old.cancel()
    delay = max(0.0, float(until) - time.time())

    async def _runner():
        try:
            await asyncio.sleep(delay)
            await perform_unban(guild_id, user_id, reason, automatic=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[MODBOT] Temp unban failed for {user_id}: {exc}")
        finally:
            _scheduled.pop(key, None)

    _scheduled[key] = asyncio.create_task(_runner())


def cancel_unban(guild_id, user_id):
    task = _scheduled.pop((guild_id, user_id), None)
    if task is None or task.done():
        return
    # Never cancel the task we're currently running inside \u2014 that would
    # abort the unban HTTP call halfway through.
    try:
        if task is asyncio.current_task():
            return
    except RuntimeError:
        pass
    task.cancel()


def resume_tempbans():
    """Re-arm every stored temp ban after a restart (called from on_ready)."""
    now = time.time()
    count = 0
    for guild_id, recs in db.all_bans().items():
        for user_id, rec in recs.items():
            until = float(rec.get("until") or 0)
            if until <= 0:
                continue
            schedule_unban(guild_id, user_id, until,
                           rec.get("reason") or "Temp ban expired")
            count += 1
    if count:
        print(f"[MODBOT] Resumed {count} temp ban timer(s).")


def pending_bans(guild_id):
    """Temp bans still running for a guild: [(user_id, until, reason)]."""
    now = time.time()
    out = []
    for user_id, rec in db.get_bans(guild_id).items():
        until = float(rec.get("until") or 0)
        if until > now:
            out.append((int(user_id), until, rec.get("reason") or ""))
    return sorted(out, key=lambda item: item[1])


async def perform_unban(guild_id, user_id, reason, automatic=False, actor=None):
    """Lift a ban (timer or manual) and log it. Returns True when unbanned."""
    db_bans = db.get_bans(guild_id)
    db_bans.pop(str(user_id), None)
    db.save_bans(guild_id, db_bans)
    cancel_unban(guild_id, user_id)

    guild = modbot.get_guild(guild_id)
    if guild is None:
        return False
    try:
        await guild.unban(discord.Object(id=user_id), reason=reason)
    except discord.NotFound:
        return False
    except Exception as exc:
        print(f"[MODBOT] Unban failed for {user_id}: {exc}")
        return False

    embed = action_embed(
        "Member Unbanned", 0x2ECC71,
        user=f"<@{user_id}> (`{user_id}`)",
        moderator=(actor.mention if actor is not None else "Automatic (temp ban expired)"),
        reason=reason,
    )
    await mod_log(guild, embed)
    return True


# --- enforcement -----------------------------------------------------------

async def send_ban_dm(user, guild, reason, moderator, duration_text=None):
    if not db.get_config(guild.id).get("ban_dm", True):
        return
    try:
        embed = discord.Embed(
            title=f"You were banned from {guild.name}",
            color=BAN_COLOR,
            description=reason,
        )
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        if duration_text:
            embed.add_field(name="Duration", value=duration_text, inline=True)
        await user.send(embed=embed)
    except Exception:
        pass


async def perform_ban(guild, actor, user, reason, until=0, duration_text=None):
    """Ban `user`; when `until` is set, record and schedule the lift.

    Returns (ok, error_message).
    """
    member = user if isinstance(user, discord.Member) else None
    if member is not None:
        problem = hierarchy_error(guild, actor, member)
        if problem:
            return False, f"You can't ban {problem}."

    await send_ban_dm(user, guild, reason, actor, duration_text)
    try:
        await guild.ban(user, reason=f"Banned by {actor} ({actor.id}): {reason}",
                        delete_message_seconds=0)
    except discord.Forbidden:
        return False, "I don't have permission to ban that user."
    except discord.HTTPException as exc:
        return False, f"Ban failed: {exc}"

    cancel_unban(guild.id, user.id)
    if until:
        bans = db.get_bans(guild.id)
        bans[str(user.id)] = {"until": until, "mod": actor.id, "reason": reason,
                              "at": time.time()}
        db.save_bans(guild.id, bans)
        schedule_unban(guild.id, user.id, until, "Temp ban expired")
    else:
        bans = db.get_bans(guild.id)
        if str(user.id) in bans:
            bans.pop(str(user.id), None)
            db.save_bans(guild.id, bans)

    embed = action_embed(
        "Member Banned", BAN_COLOR,
        user=f"{user.mention} (`{user.id}`)",
        moderator=actor.mention,
        reason=reason,
    )
    if until:
        embed.add_field(name="Lifts", value=f"{duration_text} (auto-unban)", inline=True)
    await mod_log(guild, embed)
    return True, None


# --- prefix commands -------------------------------------------------------

@modbot.command(name="ban")
@mod_only()
async def ban_cmd(ctx, target: str, *, reason: str = "No reason provided"):
    user = await resolve_user(ctx.guild, target)
    if user is None:
        await ctx.send("Couldn't find that user \u2014 paste their ID.")
        return
    ok, err = await perform_ban(ctx.guild, ctx.author, user, reason)
    if not ok:
        await ctx.send(err)
        return
    await ctx.send(f"\U0001f6ab Banned {user.mention} \u2014 {reason}",
                   embed=action_embed(
                       "Member Banned", BAN_COLOR,
                       user=f"{user.mention} (`{user.id}`)",
                       moderator=ctx.author.mention,
                       reason=reason))


@modbot.command(name="tempban")
@mod_only()
async def tempban_cmd(ctx, target: str, duration: str, *, reason: str = "No reason provided"):
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send("Invalid duration. Use formats like `30m`, `2h`, `7d`, `4w`.")
        return
    user = await resolve_user(ctx.guild, target)
    if user is None:
        await ctx.send("Couldn't find that user \u2014 paste their ID.")
        return
    until = time.time() + seconds
    ok, err = await perform_ban(ctx.guild, ctx.author, user, reason,
                                until=until, duration_text=duration)
    if not ok:
        await ctx.send(err)
        return
    await ctx.send(
        f"\U0001f6ab Temp-banned {user.mention} for **{duration}** "
        f"({humanize(seconds)}) \u2014 {reason}")


@modbot.command(name="unban")
@mod_only()
async def unban_cmd(ctx, *, target: str):
    uid = parse_user_id(target)
    if uid is None:
        async for entry in ctx.guild.bans():
            name = entry.user.name
            if name.lower() == target.lower() or str(entry.user.id) == target:
                uid = entry.user.id
                break
    if uid is None:
        await ctx.send("No matching ban found \u2014 paste the user's ID.")
        return
    unbanned = await perform_unban(ctx.guild.id, uid,
                                   f"Unbanned by {ctx.author} ({ctx.author.id})",
                                   actor=ctx.author)
    if unbanned:
        await ctx.send(f"\u2705 Unbanned `<@{uid}>`.")
    else:
        await ctx.send("That user isn't banned (or I couldn't find the ban).")


@modbot.command(name="kick")
@mod_only()
async def kick_cmd(ctx, target: str, *, reason: str = "No reason provided"):
    member = await find_member(ctx.guild, target)
    if member is None:
        await ctx.send("Couldn't find that member \u2014 mention them or paste their ID.")
        return
    problem = hierarchy_error(ctx.guild, ctx.author, member)
    if problem:
        await ctx.send(f"You can't kick {problem}.")
        return
    try:
        await member.kick(reason=f"Kicked by {ctx.author} ({ctx.author.id}): {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to kick that member.")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Kick failed: {exc}")
        return
    await ctx.send(f"\U0001f44e Kicked {member.mention} \u2014 {reason}")
    await mod_log(ctx.guild, action_embed(
        "Member Kicked", 0xE67E22,
        user=f"{member.mention} (`{member.id}`)",
        moderator=ctx.author.mention,
        reason=reason,
    ))


@modbot.command(name="tempbans")
@mod_only()
async def tempbans_cmd(ctx):
    rows = pending_bans(ctx.guild.id)
    if not rows:
        await ctx.send("No active temp bans.")
        return
    lines = [f"<@{uid}> \u00b7 lifts {fmt_ts(until)} \u00b7 {reason or 'no reason'}"
             for uid, until, reason in rows[:25]]
    embed = action_embed("Active Temp Bans", BAN_COLOR)
    embed.description = "\n".join(lines)
    if len(rows) > 25:
        embed.set_footer(text=f"+{len(rows) - 25} more")
    await ctx.send(embed=embed)


# --- slash commands --------------------------------------------------------

@modbot.tree.command(name="ban", description="Ban a member from the server")
@guild_slash
@app_commands.describe(user="User to ban", reason="Reason for the ban")
async def ban_slash(interaction: discord.Interaction, user: discord.User,
                    reason: str = "No reason provided"):
    if not await deny_unless_mod(interaction):
        return
    ok, err = await perform_ban(interaction.guild, interaction.user, user, reason)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return
    await interaction.response.send_message(
        f"\U0001f6ab Banned {user.mention} \u2014 {reason}")


@modbot.tree.command(name="tempban", description="Ban a member for a set duration")
@guild_slash
@app_commands.describe(user="User to ban", duration="e.g. 30m, 2h, 7d",
                       reason="Reason for the ban")
async def tempban_slash(interaction: discord.Interaction, user: discord.User,
                        duration: str, reason: str = "No reason provided"):
    if not await deny_unless_mod(interaction):
        return
    seconds = parse_duration(duration)
    if not seconds:
        await interaction.response.send_message(
            "Invalid duration. Use formats like `30m`, `2h`, `7d`, `4w`.", ephemeral=True)
        return
    ok, err = await perform_ban(interaction.guild, interaction.user, user, reason,
                                until=time.time() + seconds, duration_text=duration)
    if not ok:
        await interaction.response.send_message(err, ephemeral=True)
        return
    await interaction.response.send_message(
        f"\U0001f6ab Temp-banned {user.mention} for **{duration}** ({humanize(seconds)}) "
        f"\u2014 {reason}")


@modbot.tree.command(name="unban", description="Unban a user")
@guild_slash
@app_commands.describe(user_id="The banned user's ID")
async def unban_slash(interaction: discord.Interaction, user_id: str):
    if not await deny_unless_mod(interaction):
        return
    uid = parse_user_id(user_id)
    if uid is None:
        await interaction.response.send_message("That isn't a valid user ID.", ephemeral=True)
        return
    unbanned = await perform_unban(interaction.guild.id, uid,
                                   f"Unbanned by {interaction.user} ({interaction.user.id})",
                                   actor=interaction.user)
    if unbanned:
        await interaction.response.send_message(f"\u2705 Unbanned `<@{uid}>`.")
    else:
        await interaction.response.send_message(
            "That user isn't banned (or I couldn't find the ban).", ephemeral=True)


@modbot.tree.command(name="kick", description="Kick a member from the server")
@guild_slash
@app_commands.describe(user="Member to kick", reason="Reason for the kick")
async def kick_slash(interaction: discord.Interaction, user: discord.User,
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
    problem = hierarchy_error(guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(f"You can't kick {problem}.", ephemeral=True)
        return
    try:
        await member.kick(reason=f"Kicked by {interaction.user} ({interaction.user.id}): {reason}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to kick that member.", ephemeral=True)
        return
    except discord.HTTPException as exc:
        await interaction.response.send_message(f"Kick failed: {exc}", ephemeral=True)
        return
    await interaction.response.send_message(f"\U0001f44e Kicked {member.mention} \u2014 {reason}")
    await mod_log(guild, action_embed(
        "Member Kicked", 0xE67E22,
        user=f"{member.mention} (`{member.id}`)",
        moderator=interaction.user.mention,
        reason=reason,
    ))
