import discord
from .core import bot, BOT_OWNER_ID


class SupportTicketModal(discord.ui.Modal, title="Support Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter your reason here...",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000
    )

    def __init__(self, ticket_type: str):
        super().__init__()
        self.ticket_type = ticket_type
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

        owner = await bot.fetch_user(BOT_OWNER_ID)
        try:
            await owner.send(embed=embed)
            await interaction.followup.send("Ticket submitted successfully. The owner has been notified.", ephemeral=True)
        except Exception:
            await interaction.followup.send("Failed to send ticket. Please try again later.", ephemeral=True)

        channel = interaction.channel
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass


class ReportUserModal(SupportTicketModal):
    def __init__(self):
        super().__init__("Report User")
        self.reason.label = "User & Reason"
        self.reason.placeholder = "Enter the user ID/username and your reason..."


class BlacklistGameModal(SupportTicketModal):
    def __init__(self):
        super().__init__("Blacklist Game")
        self.reason.label = "Game ID & Reason"
        self.reason.placeholder = "Enter the game/place ID and your reason..."


class ErrorModal(SupportTicketModal):
    def __init__(self):
        super().__init__("Error Report")
        self.reason.label = "Error Details"
        self.reason.placeholder = "Describe the error you encountered..."


class SupportView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This ticket system is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Report User", style=discord.ButtonStyle.danger, emoji="\U0001f6ab")
    async def report_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReportUserModal())

    @discord.ui.button(label="Blacklist Game", style=discord.ButtonStyle.primary, emoji="\U0001f6ab")
    async def blacklist_game_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BlacklistGameModal())

    @discord.ui.button(label="Error", style=discord.ButtonStyle.secondary, emoji="\u26a0\ufe0f")
    async def error_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ErrorModal())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey, emoji="\u274c")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await interaction.response.send_message("Ticket cancelled.", ephemeral=True)


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
    view = SupportView(ctx.author.id)
    await ctx.send(embed=embed, view=view)
