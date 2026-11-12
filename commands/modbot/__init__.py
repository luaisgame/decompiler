"""Standalone moderation bot.

Runs on its own Discord account (MOD_BOT_TOKEN in .env) inside the same
process as the main bot, reusing this code system's loader, storage layout
and helpers. bot.py starts it with schedule_start(); when no token is
configured it simply stays offline.
"""
import asyncio

from .core import MOD_BOT_TOKEN, modbot
from . import bans, config, db, help as modhelp, purge, tickets, warns, welcome  # noqa: F401

__all__ = ["modbot", "MOD_BOT_TOKEN", "schedule_start"]


def schedule_start():
    """Create the task that logs the moderation bot in; returns it (or None)."""
    if not MOD_BOT_TOKEN:
        print("[MODBOT] MOD_BOT_TOKEN not set \u2014 moderation bot disabled.")
        return None
    masked = f"{MOD_BOT_TOKEN[:6]}...{MOD_BOT_TOKEN[-4:]}" if len(MOD_BOT_TOKEN) > 12 else "***"
    print(f"[MODBOT] Starting moderation bot (token {masked}).")
    task = asyncio.create_task(modbot.start(MOD_BOT_TOKEN))

    def _done(t):
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc:
            print(f"[MODBOT] Moderation bot stopped: {exc!r}")

    task.add_done_callback(_done)
    return task
