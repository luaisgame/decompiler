import discord
from discord import app_commands
from .core import bot, save_allowed_channel, build_setup_dropdown

@bot.command(name="setup")
async def setup_prefix(ctx, channel: discord.abc.GuildChannel = None):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You do not have permission to use this command.")
        return
    if not ctx.guild:
        await ctx.send("This command can only be used in a server.")
        return
    if channel:
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            await ctx.send("Please specify a valid text channel or forum.")
            return
        save_allowed_channel(ctx.guild.id, channel.id)
        await ctx.send(f"Successfully set {channel.mention} as the decompile channel for this server.")
    else:
        embed, view = await build_setup_dropdown(ctx.guild, ctx.author.id)
        if view:
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)

@bot.tree.command(name="setup", description="Configure the channel or forum where decompile slash commands can be used")
@app_commands.describe(channel="Optional text channel or forum to lock decompile commands to")
async def setup_slash(interaction: discord.Interaction, channel: discord.abc.GuildChannel = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    if channel:
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            await interaction.response.send_message("Please select a valid text channel or forum.", ephemeral=True)
            return
        save_allowed_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(f"Successfully set {channel.mention} as the decompile channel for this server.")
    else:
        embed, view = await build_setup_dropdown(interaction.guild, interaction.user.id)
        if view:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
