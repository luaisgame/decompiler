import subprocess
import sys
import os
import time
import urllib.request
import json
import threading
import sysconfig

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMMIT_FILE = os.path.join(SCRIPT_DIR, ".last_commit")
CHECK_FILE = os.path.join(SCRIPT_DIR, ".check_update")

FILES_TO_FETCH = [
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

def fetch_file(path):
    url = f"{RAW_BASE}/{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"[FETCH] Failed to fetch {path}: {e}")
        return None

def get_latest_commit():
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("sha")
    except Exception:
        return None

def get_saved_commit():
    try:
        if os.path.exists(COMMIT_FILE):
            with open(COMMIT_FILE, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return None

def save_commit(sha):
    try:
        with open(COMMIT_FILE, "w") as f:
            f.write(sha)
    except Exception:
        pass

def fetch_all():
    print("[SYNC] Fetching code from GitHub...")
    files = {}
    for path in FILES_TO_FETCH:
        content = fetch_file(path)
        if content is None:
            print(f"[SYNC] FATAL: Could not fetch {path}")
            return None
        files[path] = content
        print(f"[SYNC] {path}")
    print(f"[SYNC] {len(files)} files loaded.")
    return files

LAUNCHER = r'''
import sys, os, json, types

os.environ["BOT_BASE_DIR"] = r"''' + SCRIPT_DIR.replace("\\", "\\\\") + '''"

_payload = json.loads(sys.stdin.readline())
sys.argv = [sys.argv[0]]

if "commands" not in sys.modules:
    pkg = types.ModuleType("commands")
    pkg.__path__ = []
    sys.modules["commands"] = pkg

for path, code in _payload.items():
    if path == "bot.py":
        continue
    mod_name = path.replace("/", ".").replace(".py", "")
    if mod_name.endswith(".__init__"):
        mod_name = mod_name[:-9]
    package = "commands" if mod_name.startswith("commands.") else None
    mod = types.ModuleType(mod_name, code)
    mod.__file__ = f"<github:{mod_name}>"
    mod.__loader__ = None
    if package:
        mod.__package__ = package
    sys.modules[mod_name] = mod
    exec(compile(code, f"<github:{mod_name}>", "exec"), mod.__dict__)

bot_code = _payload["bot.py"]
exec(compile(bot_code, "<github:bot>", "exec"), {"__name__": "__main__", "__file__": "<github:bot>"})
'''

def get_python():
    if getattr(sys, "frozen", False):
        for name in ["python.exe", "python3.exe", "python3.14.exe"]:
            for path_dir in os.environ.get("PATH", "").split(os.pathsep):
                candidate = os.path.join(path_dir, name)
                if os.path.exists(candidate):
                    return candidate
        return "python"
    return sys.executable

def run_bot(files):
    payload = json.dumps(files)
    python = get_python()
    print(f"[RUNNER] Using Python: {python}")
    process = subprocess.Popen(
        [python, "-c", LAUNCHER],
        stdin=subprocess.PIPE,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    process.stdin.write(payload.encode())
    process.stdin.close()
    return process

def main():
    print("=" * 50)
    print("  Decompiler Bot - GitHub Runner (Memory)")
    print("=" * 50)

    while True:
        files = fetch_all()
        if files is None:
            print("[RUNNER] Fetch failed. Retrying in 5 seconds...")
            time.sleep(5)
            continue

        process = run_bot(files)

        while process.poll() is None:
            time.sleep(10)
            if not os.path.exists(CHECK_FILE):
                continue

            os.remove(CHECK_FILE)
            print("[RUNNER] Command used, checking for updates...")

            latest_sha = get_latest_commit()
            if latest_sha:
                saved_sha = get_saved_commit()
                if latest_sha != saved_sha:
                    print(f"[RUNNER] Update: {(saved_sha or '?')[:8]} -> {latest_sha[:8]}")
                    new_files = fetch_all()
                    if new_files:
                        save_commit(latest_sha)
                        print("[RUNNER] Restarting for update...")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except Exception:
                            process.kill()
                        files = new_files
                        break

        exit_code = process.returncode
        print(f"[RUNNER] Bot exited with code {exit_code}")
        print("[RUNNER] Restarting in 3 seconds...")
        time.sleep(3)

if __name__ == "__main__":
    main()
