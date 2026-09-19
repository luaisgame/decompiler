import discord
from .core import bot, BOT_OWNER_ID


TYPE_MAP = {
    "Report User": "report-user",
    "Blacklist Game": "blacklist-game",
    "Error": "error",
}


def _next_index(category: discord.CategoryChannel, prefix: str) -> int:
    existing = [c.name for c in category.channels if c.name.startswith(f"{prefix}-")]
    indices = []
    for name in existing:
        parts = name.split("-")
        if parts[-1].isdigit():
            indices.append(int(parts[-1]))
    return max(indices, default=0) + 1


class SupportTicketModal(discord.ui.Modal, title="Support Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter your reason here...",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000
    )

    def __init__(self, ticket_type: str, channel: discord.TextChannel):
        super().__init__()
        self.ticket_type = ticket_type
        self.ticket_channel = channel
        self.title = f"{ticket_type}"

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title=f"Support Ticket — {self.ticket_type}",
            color=0xF1C40F,
            description=self.reason.value
        )
        embed.add_field(name="From", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)
        embed.set_footer(text=f"Ticket from {interaction.guild.name if interaction.guild else 'DM'}")

        await self.ticket_channel.send(embed=embed)

        owner = await bot.fetch_user(BOT_OWNER_ID)
        try:
            await owner.send(embed=embed)
            await interaction.followup.send(f"Ticket created: {self.ticket_channel.mention}", ephemeral=True)
        except Exception:
            await interaction.followup.send(f"Ticket created: {self.ticket_channel.mention} (could not DM owner)", ephemeral=True)


class ReportUserModal(SupportTicketModal):
    def __init__(self, channel: discord.TextChannel):
        super().__init__("Report User", channel)
        self.reason.label = "User & Reason"
        self.reason.placeholder = "Enter the user ID/username and your reason..."


class BlacklistGameModal(SupportTicketModal):
    def __init__(self, channel: discord.TextChannel):
        super().__init__("Blacklist Game", channel)
        self.reason.label = "Game ID & Reason"
        self.reason.placeholder = "Enter the game/place ID and your reason..."


class ErrorModal(SupportTicketModal):
    def __init__(self, channel: discord.TextChannel):
        super().__init__("Error", channel)
        self.reason.label = "Error Details"
        self.reason.placeholder = "Describe the error you encountered..."


class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Report User", style=discord.ButtonStyle.danger, emoji="\U0001f6ab")
    async def report_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        category = interaction.channel.category
        prefix = TYPE_MAP["Report User"]
        index = _next_index(category, prefix)
        overwrites = interaction.channel.overwrites
        ch = await interaction.guild.create_text_channel(
            name=f"{prefix}-{index}",
            category=category,
            overwrites=overwrites,
            topic=f"Report User ticket by {interaction.user}"
        )
        await interaction.response.send_modal(ReportUserModal(ch))

    @discord.ui.button(label="Blacklist Game", style=discord.ButtonStyle.primary, emoji="\U0001f6ab")
    async def blacklist_game_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        category = interaction.channel.category
        prefix = TYPE_MAP["Blacklist Game"]
        index = _next_index(category, prefix)
        overwrites = interaction.channel.overwrites
        ch = await interaction.guild.create_text_channel(
            name=f"{prefix}-{index}",
            category=category,
            overwrites=overwrites,
            topic=f"Blacklist Game ticket by {interaction.user}"
        )
        await interaction.response.send_modal(BlacklistGameModal(ch))

    @discord.ui.button(label="Error", style=discord.ButtonStyle.secondary, emoji="\u26a0\ufe0f")
    async def error_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        category = interaction.channel.category
        prefix = TYPE_MAP["Error"]
        index = _next_index(category, prefix)
        overwrites = interaction.channel.overwrites
        ch = await interaction.guild.create_text_channel(
            name=f"{prefix}-{index}",
            category=category,
            overwrites=overwrites,
            topic=f"Error ticket by {interaction.user}"
        )
        await interaction.response.send_modal(ErrorModal(ch))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey, emoji="\u274c")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("Only the bot owner can cancel tickets.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await interaction.response.send_message("Ticket panel cancelled.", ephemeral=True)


@bot.command(name="setup-support")
async def setup_support_prefix(ctx):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return
    if not ctx.guild:
        await ctx.send("This command can only be used in a server.")
        return
    embed = discord.Embed(
        title="Support Ticket",
        description="Need help? Click a button below to open a support ticket.",
        color=0x5865F2
    )
    view = SupportView()
    await ctx.send(embed=embed, view=view)
