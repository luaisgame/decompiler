"""JSON storage for the moderation bot.

Everything lives in storage/modbot/ next to the other bot data:

  config.json   per-guild settings (read back merged with DEFAULT_CONFIG)
  warns.json    warning records per guild
  bans.json     temp-ban schedule per guild
  tickets.json  open tickets per guild
  transcripts/  saved ticket transcripts
"""
import json
import os

BASE_DIR = (
    os.environ.get("BOT_BASE_DIR")
    or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
STORAGE_DIR = os.path.join(BASE_DIR, "storage", "modbot")
TRANSCRIPT_DIR = os.path.join(STORAGE_DIR, "transcripts")

DEFAULT_WELCOME = "Welcome to {server}, {user}! You are member #{count}."
DEFAULT_PURGE_MESSAGE = "\U0001f9f9 Purged {count} message(s) in {channel} (filter: {filter})."
DEFAULT_PANEL_MESSAGE = "Need help with something? Click the button below to open a ticket."

DEFAULT_CONFIG = {
    "mod_roles": [],
    "log_channel": None,
    "ban_dm": True,
    "warns": {
        "threshold": 0,        # auto-action fires at this many active warns (0 = never)
        "action": "none",      # none | kick | mute | ban
        "expire_days": 0,      # warns expire after N days (0 = never)
        "mute_hours": 24,      # length of the automatic timeout
    },
    "welcome": {
        "enabled": True,
        "channel": None,
        "message": DEFAULT_WELCOME,
        "autorole": None,
    },
    "purge_message": DEFAULT_PURGE_MESSAGE,
    "tickets": {
        "category": None,
        "prefix": "ticket",
        "panel_channel": None,
        "log_channel": None,
        "panel_message": DEFAULT_PANEL_MESSAGE,
    },
}


def _path(name):
    return os.path.join(STORAGE_DIR, f"{name}.json")


def _load(name, default):
    try:
        with open(_path(name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _save(name, data):
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, _path(name))
    except Exception as exc:
        print(f"[MODBOT] Failed to save {name}.json: {exc}")


def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


# --- config ----------------------------------------------------------------

def get_config(guild_id):
    """Per-guild config merged over DEFAULT_CONFIG (not written back)."""
    all_cfg = _load("config", {})
    return _merge(DEFAULT_CONFIG, all_cfg.get(str(guild_id), {}))


def save_config(guild_id, cfg):
    all_cfg = _load("config", {})
    all_cfg[str(guild_id)] = cfg
    _save("config", all_cfg)


# --- warns -----------------------------------------------------------------

def get_warns(guild_id):
    return _load("warns", {}).get(str(guild_id), [])


def save_warns(guild_id, warns):
    all_warns = _load("warns", {})
    all_warns[str(guild_id)] = warns
    _save("warns", all_warns)


# --- temp bans -------------------------------------------------------------

def get_bans(guild_id):
    return _load("bans", {}).get(str(guild_id), {})


def save_bans(guild_id, bans):
    all_bans = _load("bans", {})
    all_bans[str(guild_id)] = bans
    _save("bans", all_bans)


def all_bans():
    """{guild_id: {user_id: record}} across every guild (for timer resume)."""
    raw = _load("bans", {})
    out = {}
    for guild_id, recs in raw.items():
        try:
            out[int(guild_id)] = {int(uid): rec for uid, rec in (recs or {}).items()}
        except Exception:
            continue
    return out


# --- tickets ---------------------------------------------------------------

def get_tickets(guild_id):
    return _load("tickets", {}).get(str(guild_id), {})


def save_tickets(guild_id, tickets):
    all_tickets = _load("tickets", {})
    all_tickets[str(guild_id)] = tickets
    _save("tickets", all_tickets)
