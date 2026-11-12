"""Server-wide moderation settings: mod roles, log channel, overview."""
import discord

from . import db
from .core import action_embed, mod_only, modbot

SETTINGS_COLOR = 0x5865F2


@modbot.command(name="modconfig")
@mod_only()
async def modconfig(ctx, key: str = None, value: str = None):
    cfg_all = db.get_config(ctx.guild.id)

    if key is None:
        roles = cfg_all.get("mod_roles") or []
        role_text = ", ".join(f"<@&{r}>" for r in roles) or "none (admins count)"
        embed = action_embed(
            "Moderation Settings", SETTINGS_COLOR,
            mod_roles=role_text,
            mod_log_channel=(f"<#{cfg_all['log_channel']}>"
                             if cfg_all.get("log_channel") else "off"),
            ban_dm=("on" if cfg_all.get("ban_dm") else "off"),
            warn_policy=(f"threshold {cfg_all['warns']['threshold']} \u2192 "
                         f"{cfg_all['warns']['action']} \u00b7 "
                         f"expires {cfg_all['warns']['expire_days'] or 'never'}d"),
            welcome=("on" if cfg_all["welcome"].get("enabled") else "off")
                    + (f" \u00b7 <#{cfg_all['welcome']['channel']}>"
                       if cfg_all["welcome"].get("channel") else ""),
            tickets=(f"category <#{cfg_all['tickets']['category']}>"
                     if cfg_all["tickets"].get("category") else "not set up"),
        )
        embed.description = (
            "`!modrole add|remove @role` \u00b7 `!modlog #channel|off` \u00b7 "
            "`!modconfig ban_dm on|off`\n"
            "Warns: `!warnconfig` \u00b7 Welcomes: `!welcome` \u00b7 "
            "Tickets: `!tickets` \u00b7 Purge: `!purgeconfig`"
        )
        await ctx.send(embed=embed)
        return

    if key.lower() in ("ban_dm", "bandm"):
        if value is None or value.lower() not in ("on", "off"):
            await ctx.send("Usage: `!modconfig ban_dm on|off`")
            return
        cfg_all["ban_dm"] = value.lower() == "on"
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send(f"Ban DMs are now **{'on' if cfg_all['ban_dm'] else 'off'}**.")
        return

    await ctx.send("Unknown setting. Try `!modconfig` for the overview.")


@modbot.command(name="modrole")
@mod_only()
async def modrole_cmd(ctx, action: str = None, role: discord.Role = None):
    cfg_all = db.get_config(ctx.guild.id)
    roles = list(cfg_all.get("mod_roles") or [])

    if action is None or action.lower() == "list":
        text = ", ".join(f"<@&{r}>" for r in roles) or "none"
        await ctx.send(f"Moderator roles: {text}")
        return

    action = action.lower()
    if action in ("add", "set") and role is not None:
        if role.id not in roles:
            roles.append(role.id)
        cfg_all["mod_roles"] = roles
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send(f"{role.mention} can now use moderation commands.")
        return
    if action in ("remove", "rm", "delete") and role is not None:
        if role.id in roles:
            roles.remove(role.id)
        cfg_all["mod_roles"] = roles
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send(f"{role.mention} is no longer a moderator role.")
        return

    await ctx.send("Usage: `!modrole add|remove @role` (or `!modrole` to list).")


@modbot.command(name="modlog")
@mod_only()
async def modlog_cmd(ctx, target: str = None):
    cfg_all = db.get_config(ctx.guild.id)
    if target is None:
        current = cfg_all.get("log_channel")
        await ctx.send(f"Moderation log: <#{current}>" if current else
                       "Moderation log is **off**. Usage: `!modlog #channel` "
                       "or `!modlog off`.")
        return
    if target.lower() in ("off", "none", "clear"):
        cfg_all["log_channel"] = None
        db.save_config(ctx.guild.id, cfg_all)
        await ctx.send("Moderation log turned **off**.")
        return
    raw = target.strip().strip("<#>")
    if not raw.isdigit():
        await ctx.send("Usage: `!modlog #channel` or `!modlog off`.")
        return
    channel = ctx.guild.get_channel(int(raw))
    if not isinstance(channel, discord.TextChannel):
        await ctx.send("That isn't a text channel in this server.")
        return
    cfg_all["log_channel"] = channel.id
    db.save_config(ctx.guild.id, cfg_all)
    await ctx.send(f"Warns, bans, kicks, purges and tickets will be logged to "
                   f"{channel.mention}.")
