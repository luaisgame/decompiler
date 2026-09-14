import subprocess
import sys
import os
import time
import urllib.request
import shutil
import json

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
CHECK = os.path.join(SCRIPT_DIR, ".check_update")
CLOUDFLARED_CONFIG = os.path.expanduser(r"~\.cloudflared\config.yml")
TUNNEL_NAME = "storage"
TUNNEL_ID = None
TUNNEL_DOMAIN = "storage.luaisgame.com"

FILES = [
    "bot.py",
    "commands/__init__.py",
    "commands/core.py",
    "commands/setup.py",
    "commands/blacklist.py",
    "commands/blacklistuser.py",
    "commands/blacklistserver.py",
    "commands/cookie.py",
    "commands/decompile.py",
    "commands/help.py",
]

def ensure_cloudflared():
    if shutil.which("cloudflared"):
        return True
    print("[RUNNER] cloudflared not found, installing...")
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
    print("[RUNNER] Failed to install cloudflared automatically.")
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
    global TUNNEL_ID
    cf = get_cloudflared_path()
    if not cf:
        return False

    cert = os.path.expanduser(r"~\.cloudflared\cert.pem")
    if not os.path.exists(cert):
        print("[RUNNER] Cloudflare login required. Opening browser...")
        subprocess.run([cf, "tunnel", "login"], check=True)

    result = subprocess.run([cf, "tunnel", "list"], capture_output=True, text=True)
    if TUNNEL_NAME in (result.stdout or ""):
        for line in result.stdout.splitlines():
            if TUNNEL_NAME in line:
                TUNNEL_ID = line.split()[0]
                print(f"[RUNNER] Found existing tunnel: {TUNNEL_ID}")
                break
    else:
        print("[RUNNER] Creating tunnel...")
        result = subprocess.run(
            [cf, "tunnel", "create", TUNNEL_NAME],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if "Created tunnel" in line:
                TUNNEL_ID = line.split()[-1]
                break
        if not TUNNEL_ID:
            print("[RUNNER] Failed to create tunnel")
            return False
        subprocess.run(
            [cf, "tunnel", "route", "dns", TUNNEL_NAME, TUNNEL_DOMAIN],
            capture_output=True
        )

    config_dir = os.path.expanduser(r"~\.cloudflared")
    os.makedirs(config_dir, exist_ok=True)
    cred_file = os.path.join(config_dir, f"{TUNNEL_ID}.json")
    config_content = f"""tunnel: {TUNNEL_ID}
credentials-file: {cred_file}

ingress:
  - hostname: {TUNNEL_DOMAIN}
    service: http://127.0.0.1:5000
  - service: http_status:404
"""
    with open(CLOUDFLARED_CONFIG, "w") as f:
        f.write(config_content)
    print(f"[RUNNER] Tunnel config written to {CLOUDFLARED_CONFIG}")
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
            print(f"[RUNNER] Tunnel already running: https://{TUNNEL_DOMAIN}")
            return
    except Exception:
        pass
    subprocess.Popen(
        [cf, "tunnel", "run", TUNNEL_NAME],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"[RUNNER] Tunnel started: https://{TUNNEL_DOMAIN}")

def fetch():
    for p in FILES:
        url = f"{RAW}/{p}"
        dest = os.path.join(PARENT_DIR, p)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with urllib.request.urlopen(url, timeout=15) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            print(f"[SYNC] {p}")
        except Exception as e:
            print(f"[SYNC] FAILED {p}: {e}")
            return False
    return True

PY = sys.executable
if getattr(sys, "frozen", False):
    for n in ["python.exe", "python3.exe"]:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            c = os.path.join(d, n)
            if os.path.exists(c):
                PY = c
                break

print("=" * 50)
print("  Decompiler Bot - GitHub Runner")
print("=" * 50)

if ensure_cloudflared():
    if ensure_tunnel():
        start_tunnel()

while True:
    print("[RUNNER] Fetching latest files from GitHub...")
    if not fetch():
        print("[RUNNER] Fetch failed, retrying in 5s...")
        time.sleep(5)
        continue

    bot_py = os.path.join(PARENT_DIR, "bot.py")
    p = subprocess.Popen([PY, bot_py], stdout=sys.stdout, stderr=sys.stderr)

    while p.poll() is None:
        time.sleep(10)
        if os.path.exists(CHECK):
            os.remove(CHECK)
            print("[RUNNER] Command used, fetching latest from GitHub...")
            break

    print("[RUNNER] Restarting...")
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        p.kill()
    time.sleep(2)
