"""Moderation bot core: the second Discord client plus shared helpers.

The moderation bot runs in the same process (and event loop) as the main
bot but on its own token, with its own command tree. Both bots share the
"!" prefix, so each side swallows CommandNotFound for the other's commands.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ..core import BOT_OWNER_ID, parse_duration, parse_user_id  # noqa: F401  (re-exported)
from . import db

log = logging.getLogger("discord.ext.commands")

MOD_BOT_TOKEN = os.getenv("MOD_BOT_TOKEN", "")

# Optional global role gate from .env, same shape as BLACKLIST_ROLE_IDS:
# {"<guild_id>": <role_id> | [<role_id>, ...]}
MOD_ROLE_IDS = {}
try:
    for _g, _v in json.loads(os.getenv("MOD_ROLE_IDS", "{}")).items():
        MOD_ROLE_IDS[int(_g)] = [int(_v)] if isinstance(_v, int) else [int(x) for x in _v]
except Exception:
    MOD_ROLE_IDS = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

modbot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# --- helpers ---------------------------------------------------------------

def render(template, **values):
    """Fill {name} placeholders; unknown placeholders are left untouched."""
    def _sub(match):
        key = match.group(1)
        return str(values[key]) if key in values else match.group(0)
    return _PLACEHOLDER.sub(_sub, str(template if template is not None else ""))


def humanize(seconds):
    """12345 -> '3h 25m' for log footers."""
    seconds = int(max(0, seconds))
    units = (("y", 31536000), ("mo", 2592000), ("w", 604800), ("d", 86400),
             ("h", 3600), ("m", 60), ("s", 1))
    parts = []
    for suffix, size in units:
        if seconds >= size:
            parts.append(f"{seconds // size}{suffix}")
            seconds %= size
        if len(parts) == 2:
            break
    return " ".join(parts) or "0s"


def fmt_ts(epoch, style="d"):
    return discord.utils.format_dt(datetime.fromtimestamp(epoch, tz=timezone.utc), style)


def get_cfg(guild):
    return db.get_config(guild.id)


async def find_member(guild, raw):
    """Resolve a mention / ID / username to a member of the guild."""
    uid = parse_user_id(str(raw))
    if uid is not None:
        member = guild.get_member(uid)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(uid)
        except Exception:
            pass
    needle = str(raw).strip().lower().lstrip("@")
    if needle:
        for member in guild.members:
            if member.name.lower() == needle or member.display_name.lower() == needle:
                return member
    return None


async def resolve_user(guild, raw):
    """Like find_member, but falls back to any Discord user (for unbans)."""
    member = await find_member(guild, raw)
    if member is not None:
        return member
    uid = parse_user_id(str(raw))
    if uid is None:
        return None
    try:
        return await modbot.fetch_user(uid)
    except Exception:
        return None


async def is_mod(member):
    """Admin / Manage Server / configured mod role / bot owner."""
    if member is None:
        return False
    if member.id == BOT_OWNER_ID:
        return True
    guild = getattr(member, "guild", None)
    if guild is None:
        return False
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    role_ids = set(get_cfg(guild).get("mod_roles") or [])
    role_ids.update(MOD_ROLE_IDS.get(guild.id, []))
    if role_ids and any(role.id in role_ids for role in member.roles):
        return True
    return False


def mod_only():
    """Permission check for prefix commands."""
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.CheckFailure("This command only works in a server.")
        if not await is_mod(ctx.author):
            raise commands.CheckFailure(
                "You need the **Manage Server** permission or a moderator role to use that.")
        return True
    return commands.check(predicate)


async def deny_unless_mod(interaction):
    """Permission check for slash commands; replies ephemerally and returns False on denial."""
    ok = interaction.guild is not None and await is_mod(interaction.user)
    if not ok:
        try:
            await interaction.response.send_message(
                "You need the **Manage Server** permission or a moderator role to use that.",
                ephemeral=True)
        except Exception:
            pass
    return ok


def guild_slash(fn):
    """Restrict a slash command to guild installs (moderation needs a guild)."""
    fn = app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)(fn)
    fn = app_commands.allowed_installs(guilds=True, users=False)(fn)
    return fn


def hierarchy_error(guild, actor, target):
    """None when `actor` may act on `target`, else a phrase for the error message."""
    me = guild.me
    if me is None and modbot.user is not None:
        me = guild.get_member(modbot.user.id)
    if target.id == guild.owner_id:
        return "the server owner"
    if target.id == actor.id:
        return "yourself"
    if me is not None and target.top_role >= me.top_role:
        return "someone with a role at or above mine"
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "someone at or above your top role"
    return None


async def mod_log(guild, embed):
    """Post an embed to the guild's configured moderation log channel."""
    ch_id = get_cfg(guild).get("log_channel")
    if not ch_id:
        return
    channel = guild.get_channel(int(ch_id))
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(ch_id))
        except Exception:
            return
    try:
        await channel.send(embed=embed)
    except Exception as exc:
        print(f"[MODBOT] Failed to post to mod log: {exc}")


def action_embed(title, color, **fields):
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    for name, value in fields.items():
        embed.add_field(name=name.replace("_", " ").title(), value=value, inline=False)
    return embed


# --- events ----------------------------------------------------------------

_views_registered = False
_tree_synced = False


@modbot.event
async def on_ready():
    global _views_registered, _tree_synced
    print(f"[MODBOT] Online as: {modbot.user}")

    if not _views_registered:
        from .tickets import TicketActionsView, TicketOpenView
        modbot.add_view(TicketOpenView())
        modbot.add_view(TicketActionsView())
        _views_registered = True
        print("[MODBOT] Registered persistent ticket views.")

    try:
        from .bans import resume_tempbans
        resume_tempbans()
    except Exception as exc:
        print(f"[MODBOT] Failed to resume temp bans: {exc}")

    if not _tree_synced:
        _tree_synced = True
        try:
            synced = await modbot.tree.sync()
            print(f"[MODBOT] Synced {len(synced)} Slash Command(s).")
        except Exception as exc:
            print(f"[MODBOT] Failed to sync slash commands: {exc}")

    try:
        await modbot.change_presence(
            status=discord.Status.idle,
            activity=discord.Game(name="Moderation \u2014 !modhelp"))
    except Exception:
        pass


@modbot.event
async def on_command(ctx):
    print(f"[MODBOT] {ctx.author} used '!{ctx.command.name}' in "
          f"{ctx.guild.name if ctx.guild else 'DM'}")


@modbot.event
async def on_command_error(ctx, error):
    # The main bot shares the "!" prefix, so its commands hit us as
    # CommandNotFound (and ours hit it) \u2014 stay quiet about those.
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send(str(error) or "You can't use that command.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        usage = f"{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}".rstrip()
        await ctx.send(f"Missing `{error.param.name}`. Usage: `{usage}`")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Invalid argument: {error}")
        return
    if isinstance(error, commands.CommandInvokeError):
        error = error.original
    print(f"[MODBOT] Error in {ctx.command}: {error!r}")
    try:
        await ctx.send(f"Something went wrong: `{type(error).__name__}: {error}`")
    except Exception:
        pass
