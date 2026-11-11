import asyncio
import os
import sys
import urllib.request
import json
import shutil
import subprocess
import threading
import time
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
RAW_VERSION = "659daa0"

GITHUB_FILES = [
    "commands/__init__.py",
    "commands/core.py",
    "commands/assets.py",
    "commands/setup.py",
    "commands/support.py",
    "commands/blacklist.py",
    "commands/blacklistuser.py",
    "commands/blacklistserver.py",
    "commands/cookie.py",
    "commands/decompile.py",
    "commands/help.py",
    "commands/mc.py",
    "commands/dm/__init__.py",
    "commands/dm/db.py",
    "commands/dm/auth.py",
    "commands/dm/api.py",
    "commands/dm/realtime.py",
    "commands/dm/web.py",
    "minecraft_setup.py",
]

# The dm SPA's static assets aren't Python: with no checkout on disk they are
# written next to bot.py and handed to dm.web.set_static_dir() below.
GITHUB_STATIC = {
    "commands/dm/static/app.js": "app.js",
    "commands/dm/static/style.css": "style.css",
}

HAS_LOCAL = os.path.isdir(os.path.join(_BASE_DIR, "commands"))

if not HAS_LOCAL:
    print("[STARTUP] No local commands folder found, fetching from GitHub...")
    import importlib.abc
    import importlib.util

    def _fetch(path: str) -> bytes:
        url = f"{RAW_URL}/{path}?v={RAW_VERSION}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read()

    _sources = {}
    for _path in GITHUB_FILES:
        try:
            _sources[_path] = _fetch(_path).decode()
        except Exception as e:
            print(f"[STARTUP] Failed to fetch {_path}: {e}")
            sys.exit(1)

    # module name -> source, plus which of them are packages
    _code = {}
    _packages = set()
    for _path, _src in _sources.items():
        _name = _path[:-3].replace("/", ".")
        if _name.endswith(".__init__"):
            _name = _name[:-9]
            _packages.add(_name)
        _code[_name] = _src

    class _GitHubLoader(importlib.abc.Loader):
        """Compiles GitHub-fetched source into a normal module object."""

        def __init__(self, fullname):
            self.fullname = fullname

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            # spec_from_loader() doesn't give a module __file__, and the dm web
            # shell reads it to locate its static folder.
            module.__file__ = f"<github:{self.fullname}>"
            exec(compile(_code[self.fullname], f"<github:{self.fullname}>", "exec"),
                 module.__dict__)

    class _GitHubFinder(importlib.abc.MetaPathFinder):
        """Resolves commands.* from the fetched sources.

        Going through the import system (instead of exec'ing files in list
        order) is what makes the nested packages work: relative imports inside
        commands.dm.* resolve to commands.dm, __package__/__path__ are set
        correctly, and a module is only compiled when something imports it.
        """

        def find_spec(self, fullname, path=None, target=None):
            if fullname not in _code:
                return None
            return importlib.util.spec_from_loader(
                fullname, _GitHubLoader(fullname),
                origin=f"<github:{fullname}>",
                is_package=fullname in _packages)

    sys.meta_path.insert(0, _GitHubFinder())

    _static_dir = os.path.join(_BASE_DIR, "dm_static")
    os.makedirs(_static_dir, exist_ok=True)
    for _path, _name in GITHUB_STATIC.items():
        try:
            with open(os.path.join(_static_dir, _name), "wb") as _fh:
                _fh.write(_fetch(_path))
        except Exception as e:
            print(f"[STARTUP] Failed to fetch {_path}: {e}")
            sys.exit(1)
    print("[STARTUP] All files loaded from GitHub.")
else:
    print("[STARTUP] Local commands folder found.")

from commands.core import bot, BOT_TOKEN

if not HAS_LOCAL:
    from commands.dm import web as _dm_web
    _dm_web.set_static_dir(_static_dir)

TUNNEL_NAME = "storage"
TUNNEL_DOMAIN = "storage.luaisgame.com"
TUNNEL_DOMAIN_MC = "mc.luaisgame.com"
TUNNEL_DOMAIN_DM = "dm.luaisgame.com"

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

def ensure_tunnel(port=5000):
    global _tunnel_config_changed
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

    # Route DNS for ALL hostnames on every startup (idempotent). This runs even
    # when the tunnel already existed, so mc.* and dm.* get their CNAMEs without
    # needing a manual `cloudflared tunnel route dns`.
    for domain in (TUNNEL_DOMAIN, TUNNEL_DOMAIN_MC, TUNNEL_DOMAIN_DM):
        subprocess.run(
            [cf, "tunnel", "route", "dns", TUNNEL_NAME, domain],
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
    service: http://127.0.0.1:{port}
  - hostname: {TUNNEL_DOMAIN_MC}
    service: http://127.0.0.1:{port}
  - hostname: {TUNNEL_DOMAIN_DM}
    service: http://127.0.0.1:{port}
  - service: http_status:404
"""
    # Only rewrite + force a restart when the config actually changed, so normal
    # restarts don't bounce the tunnel unnecessarily.
    old = None
    try:
        with open(config_path, "r") as f:
            old = f.read()
    except OSError:
        pass
    _tunnel_config_changed = (old != config_content)
    with open(config_path, "w") as f:
        f.write(config_content)
    if _tunnel_config_changed:
        print("[STARTUP] Tunnel config changed (new hostname/ingress) — will restart tunnel.")
    return True

_tunnel_config_changed = False

def _cloudflared_running():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
            capture_output=True, text=True
        )
        return "cloudflared.exe" in result.stdout
    except Exception:
        return False

def start_tunnel():
    cf = get_cloudflared_path()
    if not cf:
        return
    if _cloudflared_running():
        if _tunnel_config_changed:
            # Config changed (e.g. mc.luaisgame.com added) but a tunnel is already
            # running with the old single-hostname config. Bounce it so it picks up
            # the new ingress.
            print("[STARTUP] Restarting cloudflared to apply new tunnel config...")
            try:
                subprocess.run(
                    ["taskkill", "/IM", "cloudflared.exe", "/F"],
                    capture_output=True, text=True
                )
            except Exception:
                pass
            time.sleep(2)
        else:
            return
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
    print(f"[STARTUP] Tunnel started: https://{TUNNEL_DOMAIN} + https://{TUNNEL_DOMAIN_MC} + https://{TUNNEL_DOMAIN_DM}")

@bot.event
async def on_ready():
    print(f"[DEBUG] Online as: {bot.user}")

    from commands.core import switch_to_default_cookie, load_queue, decompile_queue_worker, reset_bot_presence

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

    # Re-arm the decompile buttons (Copy Join URL) on messages sent before the
    # last restart; without this Discord answers "This interaction failed".
    try:
        from commands.core import JoinGameView, _join_urls
        bot.add_view(JoinGameView())
        print(f"[DEBUG] Registered decompile view ({len(_join_urls)} join URL(s) remembered).")
    except Exception as e:
        print(f"[DEBUG] Failed to register decompile view: {e}")

    try:
        from commands.assets import register_persistent_views
        register_persistent_views(bot)
    except Exception as e:
        print(f"[ASSETS] Failed to register asset views: {e}")

    try:
        from minecraft_setup import run_minecraft_setup
        run_minecraft_setup()
    except Exception as e:
        print(f"[MINECRAFT] Setup failed: {e}")

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

async def main():
    from commands.core import start_local_server
    port = await start_local_server(port=5000)

    async def setup_tunnel():
        try:
            if await asyncio.to_thread(ensure_cloudflared) and await asyncio.to_thread(ensure_tunnel, port or 5000):
                start_tunnel()
        except Exception as e:
            print(f"[STARTUP] Tunnel setup failed: {e}")

    asyncio.create_task(setup_tunnel())
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
