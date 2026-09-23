import asyncio
import os
import sys
import types
import urllib.request
import json
import shutil
import subprocess
import threading
import discord

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

os.environ["BOT_BASE_DIR"] = _BASE_DIR

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE_DIR, ".env"))

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
RAW_VERSION = "bc2d6e1"

GITHUB_FILES = [
    "commands/core.py",
    "commands/setup.py",
    "commands/support.py",
    "commands/blacklist.py",
    "commands/blacklistuser.py",
    "commands/blacklistserver.py",
    "commands/cookie.py",
    "commands/decompile.py",
    "commands/help.py",
    "commands/__init__.py",
    "minecraft_setup.py",
]

HAS_LOCAL = os.path.isdir(os.path.join(_BASE_DIR, "commands"))

if not HAS_LOCAL:
    print("[STARTUP] No local commands folder found, fetching from GitHub...")
    pkg = types.ModuleType("commands")
    pkg.__path__ = []
    pkg.__package__ = "commands"
    sys.modules["commands"] = pkg
    for path in GITHUB_FILES:
        try:
            url = f"{RAW_URL}/{path}?v={RAW_VERSION}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                code = resp.read().decode()
        except Exception as e:
            print(f"[STARTUP] Failed to fetch {path}: {e}")
            sys.exit(1)
        mn = path.replace("/", ".").replace(".py", "")
        if mn.endswith(".__init__"):
            mn = mn[:-9]
        if mn in sys.modules:
            m = sys.modules[mn]
        else:
            m = types.ModuleType(mn)
            sys.modules[mn] = m
        m.__file__ = f"<github:{mn}>"
        m.__loader__ = None
        if mn == "commands" or mn.startswith("commands."):
            m.__package__ = "commands"
        else:
            m.__package__ = None
        exec(compile(code, f"<github:{mn}>", "exec"), m.__dict__)
    print("[STARTUP] All files loaded from GitHub.")
else:
    print("[STARTUP] Local commands folder found.")

from commands.core import bot, BOT_TOKEN

TUNNEL_NAME = "storage"
TUNNEL_DOMAIN = "storage.luaisgame.com"

def ensure_cloudflared():
    if shutil.which("cloudflared"):
        return True
    print("[STARTUP] cloudflared not found, installing...")
    try:
        subprocess.run(
            ["winget", "install", "cloudflare.cloudflared",
             "--accept-package-agreements", "--accept-source-agreements"],
            check=True, capture_output=True
        )
    except Exception:
        pass
    if shutil.which("cloudflared"):
        return True
    for p in [
        os.path.expanduser(r"~\AppData\Local\cloudflared\cloudflared.exe"),
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ]:
        if os.path.exists(p):
            os.environ["PATH"] = os.path.dirname(p) + os.pathsep + os.environ.get("PATH", "")
            return True
    print("[STARTUP] Failed to install cloudflared automatically.")
    return False

def get_cloudflared_path():
    path = shutil.which("cloudflared")
    if path:
        return path
    for p in [
        os.path.expanduser(r"~\AppData\Local\cloudflared\cloudflared.exe"),
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ]:
        if os.path.exists(p):
            return p
    return None

def ensure_tunnel():
    cf = get_cloudflared_path()
    if not cf:
        return False

    cert = os.path.expanduser(r"~\.cloudflared\cert.pem")
    if not os.path.exists(cert):
        print("[STARTUP] Cloudflare login required. Opening browser...")
        subprocess.run([cf, "tunnel", "login"], check=True)

    result = subprocess.run([cf, "tunnel", "list"], capture_output=True, text=True)
    tunnel_id = None
    if TUNNEL_NAME in (result.stdout or ""):
        for line in result.stdout.splitlines():
            if TUNNEL_NAME in line:
                tunnel_id = line.split()[0]
                break
    else:
        print("[STARTUP] Creating tunnel...")
        result = subprocess.run(
            [cf, "tunnel", "create", TUNNEL_NAME],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if "Created tunnel" in line:
                tunnel_id = line.split()[-1]
                break
        if not tunnel_id:
            print("[STARTUP] Failed to create tunnel")
            return False
        subprocess.run(
            [cf, "tunnel", "route", "dns", TUNNEL_NAME, TUNNEL_DOMAIN],
            capture_output=True
        )

    config_dir = os.path.expanduser(r"~\.cloudflared")
    os.makedirs(config_dir, exist_ok=True)
    cred_file = os.path.join(config_dir, f"{tunnel_id}.json")
    config_path = os.path.join(config_dir, "config.yml")
    config_content = f"""tunnel: {tunnel_id}
credentials-file: {cred_file}

ingress:
  - hostname: {TUNNEL_DOMAIN}
    service: http://127.0.0.1:5000
  - service: http_status:404
"""
    with open(config_path, "w") as f:
        f.write(config_content)
    return True

def start_tunnel():
    cf = get_cloudflared_path()
    if not cf:
        return
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
            capture_output=True, text=True
        )
        if "cloudflared.exe" in result.stdout:
            return
    except Exception:
        pass
    proc = subprocess.Popen(
        [cf, "tunnel", "run", TUNNEL_NAME],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    def _read_tunnel(out):
        try:
            from commands.core import tunnel_log
            for line in iter(out.readline, b""):
                if line:
                    tunnel_log.write(line.decode(errors="replace").rstrip())
        except Exception:
            pass
    threading.Thread(target=_read_tunnel, args=(proc.stdout,), daemon=True).start()
    print(f"[STARTUP] Tunnel started: https://{TUNNEL_DOMAIN}")

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    from commands.core import switch_to_default_cookie, load_queue, start_local_server, decompile_queue_worker, reset_bot_presence

    try:
        from commands.core import start_screenshare_on_ready
        asyncio.create_task(start_screenshare_on_ready())
    except Exception as e:
        print(f"[SCREENSHARE] Failed to start: {e}")

    switch_to_default_cookie()
    load_queue()

    from commands.support import TICKETS, PersistentClaimView
    for ticket_id in TICKETS:
        bot.add_view(PersistentClaimView(ticket_id))
    print(f"[DEBUG] Registered {len(TICKETS)} persistent ticket views.")

    try:
        from minecraft_setup import run_minecraft_setup
        run_minecraft_setup()
    except Exception as e:
        print(f"[MINECRAFT] Setup failed: {e}")

    if ensure_cloudflared():
        if ensure_tunnel():
            start_tunnel()

    await start_local_server(port=5000)

    asyncio.create_task(decompile_queue_worker())

    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="Waiting for requests...")
    )

    async def sync_commands():
        try:
            synced = await bot.tree.sync()
            print(f"[DEBUG] Synced {len(synced)} Slash Command(s).")
        except Exception as e:
            print(f"[DEBUG] Failed to sync slash commands: {e}")

    asyncio.create_task(sync_commands())

@bot.event
async def on_command(ctx):
    try:
        open(os.path.join(os.environ.get("BOT_BASE_DIR", "."), ".check_update"), "w").close()
    except Exception:
        pass
    author = ctx.author
    guild = ctx.guild.name if ctx.guild else "DM"
    channel = ctx.channel.name if ctx.guild else "DM"
    print(
        f"[LOG] {author} (ID: {author.id}) used '!{ctx.command.name}' "
        f"in {guild} #{channel} (message {ctx.message.id})"
    )

@bot.event
async def on_interaction(interaction):
    if interaction.type == discord.InteractionType.application_command:
        try:
            open(os.path.join(os.environ.get("BOT_BASE_DIR", "."), ".check_update"), "w").close()
        except Exception:
            pass

@bot.event
async def on_command_completion(ctx):
    try:
        await asyncio.sleep(1)
        await ctx.message.delete()
    except Exception as e:
        print(f"[DEBUG] Could not delete command message: {e}")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
