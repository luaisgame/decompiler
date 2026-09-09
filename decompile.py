import discord
from discord import app_commands
import core as core_module
from core import (bot, run_decompile_logic, load_allowed_channels, BOT_OWNER_ID,
                   set_decompile_disabled, current_active_data, update_status)

@bot.command()
async def decompile(ctx, place_id: str, game_id: str = None):
    if ctx.guild:
        allowed_channels = load_allowed_channels()
        allowed_channel_id = allowed_channels.get(ctx.guild.id)
        if allowed_channel_id is not None:
            active_id = ctx.channel.id
            if isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id:
                active_id = ctx.channel.parent_id
            if active_id != allowed_channel_id:
                await ctx.send(f"This command can only be used in <#{allowed_channel_id}>.")
                return

    owner_msg = {"msg": None}

    async def prefix_send(content=None, embed=None, ephemeral=False):
        result = await ctx.send(content=content, embed=embed)
        if BOT_OWNER_ID:
            try:
                owner = await bot.fetch_user(BOT_OWNER_ID)
                if embed:
                    owner_embed = embed.copy()
                    owner_embed.add_field(name="Requested by", value=f"{ctx.author.display_name} ({ctx.author.name}) \u2022 `{ctx.author.id}`", inline=False)
                    owner_msg["msg"] = await owner.send(embed=owner_embed)
                elif content:
                    await owner.send(content)
            except Exception as e:
                print(f"[DEBUG] Failed to DM owner: {e}")
        return result

    async def on_status_update(embed):
        if BOT_OWNER_ID and owner_msg["msg"]:
            try:
                await owner_msg["msg"].edit(embed=embed)
            except Exception as e:
                print(f"[DEBUG] Failed to update owner DM: {e}")

    core_module._on_status_update = on_status_update
    await run_decompile_logic(prefix_send, ctx.author, ctx.guild, ctx.channel, place_id, game_id, is_ephemeral=False)
    core_module._on_status_update = None

@bot.command()
async def decomp(ctx, a: str, b: str = None):
    return await decompile(ctx, a, b)

@bot.tree.command(name="decompile", description="Decompile a Roblox Experience by Place ID")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    place_id="The numeric Roblox Place ID to decompile",
    game_id="Optional Job ID or Private Server Link Code"
)
async def decompile_slash(interaction: discord.Interaction, place_id: str, game_id: str = None):
    if interaction.guild:
        allowed_channels = load_allowed_channels()
        allowed_channel_id = allowed_channels.get(interaction.guild.id)
        if allowed_channel_id is not None:
            active_id = interaction.channel_id
            if isinstance(interaction.channel, discord.Thread) and interaction.channel.parent_id:
                active_id = interaction.channel.parent_id
            if active_id != allowed_channel_id:
                await interaction.response.send_message(
                    f"This command can only be used in <#{allowed_channel_id}>.",
                    ephemeral=True
                )
                return

    await interaction.response.defer(ephemeral=False)

    owner_msg = {"msg": None}

    async def slash_send(content=None, embed=None, ephemeral=False):
        result = await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        if BOT_OWNER_ID:
            try:
                owner = await bot.fetch_user(BOT_OWNER_ID)
                if embed:
                    owner_embed = embed.copy()
                    owner_embed.add_field(name="Requested by", value=f"{interaction.user.display_name} ({interaction.user.name}) \u2022 `{interaction.user.id}`", inline=False)
                    owner_msg["msg"] = await owner.send(embed=owner_embed)
                elif content:
                    await owner.send(content)
            except Exception as e:
                print(f"[DEBUG] Failed to DM owner: {e}")
        return result

    async def on_status_update(embed):
        if BOT_OWNER_ID and owner_msg["msg"]:
            try:
                await owner_msg["msg"].edit(embed=embed)
            except Exception as e:
                print(f"[DEBUG] Failed to update owner DM: {e}")

    core_module._on_status_update = on_status_update
    await run_decompile_logic(
        slash_send,
        interaction.user,
        interaction.guild,
        interaction.channel,
        place_id,
        game_id,
        is_ephemeral=False
    )
    core_module._on_status_update = None

@bot.tree.command(name="decompile-off", description="Bot-owner: disable decompiling everywhere")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def decompile_off_slash(interaction: discord.Interaction):
    from core import user_has_role, BLACKLIST_ROLE_IDS
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    set_decompile_disabled(True)
    if current_active_data is not None and not current_active_data.get("aborted"):
        current_active_data["aborted"] = True
        try:
            await update_status(current_active_data["info_msg"], current_active_data["embed"], "kill_switch")
        except Exception as e:
            print(f"[DEBUG] Kill-switch embed update failed: {e}")
    await interaction.response.send_message("Decompiling has been **disabled** everywhere.", ephemeral=True)

@bot.tree.command(name="decompile-on", description="Bot-owner: re-enable decompiling")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def decompile_on_slash(interaction: discord.Interaction):
    from core import user_has_role, BLACKLIST_ROLE_IDS
    if not await user_has_role(interaction.user, BLACKLIST_ROLE_IDS):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    set_decompile_disabled(False)
    await interaction.response.send_message("Decompiling has been **re-enabled**.", ephemeral=True)
