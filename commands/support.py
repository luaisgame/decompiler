import asyncio
import json
import os
import discord
from .core import bot, BOT_OWNER_ID

TICKETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "tickets.json")
TICKETS = {}

TYPE_MAP = {
    "Report User": "report-user",
    "Blacklist Game": "blacklist-game",
    "Error": "error",
}


def _save_tickets():
    try:
        os.makedirs(os.path.dirname(TICKETS_FILE), exist_ok=True)
        with open(TICKETS_FILE, "w") as f:
            json.dump({str(k): v for k, v in TICKETS.items()}, f, indent=2)
    except Exception:
        pass


def _load_tickets():
    global TICKETS
    try:
        if os.path.exists(TICKETS_FILE):
            with open(TICKETS_FILE, "r") as f:
                TICKETS = {int(k): v for k, v in json.load(f).items()}
    except Exception:
        TICKETS = {}


def _next_index(category, prefix):
    existing = [c.name for c in category.channels if c.name.startswith(f"{prefix}-")]
    indices = []
    for name in existing:
        parts = name.split("-")
        if parts[-1].isdigit():
            indices.append(int(parts[-1]))
    return max(indices, default=0) + 1


def _is_ticket(channel):
    return channel.id in TICKETS


def _ticket_embed(ticket_type, creator_mention, status="Unclaimed"):
    color_map = {"Report User": 0xE74C3C, "Blacklist Game": 0x3498DB, "Error": 0xE67E22}
    embed = discord.Embed(
        title=f"{ticket_type} Ticket",
        color=color_map.get(ticket_type, 0x5865F2),
        description=f"Created by {creator_mention}\n\n**Status:** {status}"
    )
    return embed


class ClaimButton(discord.ui.Button):
    def __init__(self, ticket_id):
        super().__init__(label="Claim", style=discord.ButtonStyle.success, emoji="\u2705",
                         custom_id=f"claim_{ticket_id}")
        self.ticket_id = ticket_id

    async def callback(self, interaction):
        t = TICKETS.get(self.ticket_id)
        if not t:
            await interaction.response.send_message("This ticket no longer exists.", ephemeral=True)
            return
        if t["claimer"]:
            await interaction.response.send_message(f"Already claimed by <@{t['claimer']}>.", ephemeral=True)
            return
        t["claimer"] = interaction.user.id
        _save_tickets()
        ch = interaction.guild.get_channel(self.ticket_id)
        if ch:
            await ch.set_permissions(interaction.user, view_channel=True, send_messages=True)
            embed = _ticket_embed(t["type"], f"<@{t['creator']}>", f"Claimed by {interaction.user.mention}")
            try:
                old_msg = await ch.fetch_message(t["panel_message"])
                await old_msg.delete()
            except Exception:
                pass
            await ch.send(f"{interaction.user.mention} claimed this ticket.", embed=embed)
        await interaction.response.send_message("You claimed this ticket.", ephemeral=True)


class PersistentClaimView(discord.ui.View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        self.add_item(ClaimButton(ticket_id))


class SupportTicketModal(discord.ui.Modal, title="Support Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter your reason here...",
        style=discord.TextStyle.long,
        required=True,
        max_length=2000
    )

    def __init__(self, ticket_type, ticket_channel=None):
        super().__init__()
        self.ticket_type = ticket_type
        self.ticket_channel = ticket_channel
        self.title = ticket_type

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        target = self.ticket_channel or interaction.channel
        embed = discord.Embed(
            title=f"Support Ticket - {self.ticket_type}",
            color=0xF1C40F,
            description=self.reason.value
        )
        embed.add_field(name="From", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)
        await target.send(embed=embed)
        try:
            await interaction.followup.send(f"Ticket submitted in {target.mention}", ephemeral=True)
        except Exception:
            try:
                await interaction.user.send(f"Ticket submitted in {target.mention}")
            except Exception:
                pass


class ReportUserModal(SupportTicketModal):
    def __init__(self, channel=None):
        super().__init__("Report User", channel)
        self.reason.label = "User & Reason"
        self.reason.placeholder = "Enter the user ID/username and your reason..."


class BlacklistGameModal(SupportTicketModal):
    def __init__(self, channel=None):
        super().__init__("Blacklist Game", channel)
        self.reason.label = "Game ID & Reason"
        self.reason.placeholder = "Enter the game/place ID and your reason..."


class ErrorModal(SupportTicketModal):
    def __init__(self, channel=None):
        super().__init__("Error", channel)
        self.reason.label = "Error Details"
        self.reason.placeholder = "Describe the error you encountered..."


async def _create_ticket_channel(interaction, ticket_type, modal_cls):
    category = interaction.channel.category
    prefix = TYPE_MAP[ticket_type]
    index = _next_index(category, prefix)
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    ch = await interaction.guild.create_text_channel(
        name=f"{prefix}-{index}",
        category=category,
        overwrites=overwrites,
        topic=f"{ticket_type} ticket by {interaction.user} ({interaction.user.id})"
    )
    TICKETS[ch.id] = {
        "creator": interaction.user.id,
        "claimer": None,
        "type": ticket_type,
    }
    embed = _ticket_embed(ticket_type, interaction.user.mention, "Unclaimed")
    msg = await ch.send(f"{interaction.user.mention}", embed=embed, view=PersistentClaimView(ch.id))
    TICKETS[ch.id]["panel_message"] = msg.id
    _save_tickets()
    try:
        await interaction.user.send(f"Ticket created: {ch.mention}")
    except Exception:
        pass
    await interaction.response.send_modal(modal_cls(ch))


class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Report User", style=discord.ButtonStyle.danger, emoji="\U0001f6ab")
    async def report_user_button(self, interaction, button):
        await _create_ticket_channel(interaction, "Report User", ReportUserModal)

    @discord.ui.button(label="Blacklist Game", style=discord.ButtonStyle.primary, emoji="\U0001f6ab")
    async def blacklist_game_button(self, interaction, button):
        await _create_ticket_channel(interaction, "Blacklist Game", BlacklistGameModal)

    @discord.ui.button(label="Error", style=discord.ButtonStyle.secondary, emoji="\u26a0\ufe0f")
    async def error_button(self, interaction, button):
        await _create_ticket_channel(interaction, "Error", ErrorModal)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey, emoji="\u274c")
    async def cancel_button(self, interaction, button):
        if interaction.user.id != BOT_OWNER_ID:
            try:
                await interaction.user.send("Only the bot owner can cancel the ticket panel.")
            except Exception:
                pass
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
        try:
            await ctx.author.send("Only the bot owner can use this command.")
        except Exception:
            pass
        return
    if not ctx.guild:
        return
    embed = discord.Embed(
        title="Support Ticket",
        description="Need help? Click a button below to open a support ticket.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=SupportView())


@bot.command(name="close")
async def close_ticket(ctx, channel_id: int = None):
    if not ctx.guild:
        return
    ch = ctx.channel
    if not _is_ticket(ch):
        try:
            await ctx.author.send("This command can only be used in a support ticket channel.")
        except Exception:
            pass
        return
    t = TICKETS[ch.id]
    if t["claimer"] is None:
        try:
            await ctx.author.send("This ticket has not been claimed yet.")
        except Exception:
            pass
        return
    if ctx.author.id not in (t["creator"], t["claimer"], BOT_OWNER_ID):
        try:
            await ctx.author.send("You do not have permission to close this ticket.")
        except Exception:
            pass
        return
    del TICKETS[ch.id]
    _save_tickets()
    await ch.send("Ticket closed. Deleting channel in 5 seconds...")
    await asyncio.sleep(5)
    await ch.delete(reason=f"Closed by {ctx.author}")


@bot.command(name="adduser")
async def add_user(ctx, user_id: int = None):
    if not ctx.guild:
        return
    ch = ctx.channel
    if not _is_ticket(ch):
        try:
            await ctx.author.send("This command can only be used in a support ticket channel.")
        except Exception:
            pass
        return
    t = TICKETS[ch.id]
    if ctx.author.id not in (t["creator"], t["claimer"], BOT_OWNER_ID):
        try:
            await ctx.author.send("You do not have permission to add users to this ticket.")
        except Exception:
            pass
        return
    if user_id is None:
        try:
            await ctx.author.send("Usage: `!adduser <user_id>`")
        except Exception:
            pass
        return
    try:
        member = await ctx.guild.fetch_member(user_id)
    except Exception:
        try:
            await ctx.author.send("User not found in this server.")
        except Exception:
            pass
        return
    await ch.set_permissions(member, view_channel=True, send_messages=True)
    await ch.send(f"{member.mention} has been added to this ticket.")


_load_tickets()
