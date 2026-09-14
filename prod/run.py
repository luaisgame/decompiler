import subprocess
import sys
import os
import time
import urllib.request

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
CHECK = os.path.join(SCRIPT_DIR, ".check_update")

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
