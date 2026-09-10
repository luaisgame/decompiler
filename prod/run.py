import subprocess
import sys
import os
import time
import urllib.request
import json

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

def get_file_last_commit(path):
    url = f"https://api.github.com/repos/{REPO}/commits?path={path}&sha={BRANCH}&per_page=1"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("sha")
    except Exception:
        pass
    return None

def get_saved_commits():
    try:
        if os.path.exists(COMMIT_FILE):
            with open(COMMIT_FILE, "r") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return {}

def save_commits(data):
    try:
        with open(COMMIT_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def get_changed_files():
    saved = get_saved_commits()
    changed = []
    for path in FILES_TO_FETCH:
        remote_sha = get_file_last_commit(path)
        if remote_sha is None:
            changed.append(path)
            continue
        local_sha = saved.get(path)
        if local_sha != remote_sha:
            changed.append(path)
    return changed

def update_saved_commits(file_list):
    saved = get_saved_commits()
    for path in file_list:
        sha = get_file_last_commit(path)
        if sha:
            saved[path] = sha
    save_commits(saved)

def fetch_files(file_list=None):
    to_fetch = file_list if file_list else FILES_TO_FETCH
    print(f"[SYNC] Fetching {len(to_fetch)} file(s) from GitHub...")
    files = {}
    for path in to_fetch:
        content = fetch_file(path)
        if content is None:
            print(f"[SYNC] FATAL: Could not fetch {path}")
            return None
        files[path] = content
        print(f"[SYNC] {path}")
    print(f"[SYNC] {len(files)} file(s) loaded.")
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
    return subprocess.Popen(
        [python, "-c", LAUNCHER],
        stdin=subprocess.PIPE,
        stdout=sys.stdout,
        stderr=sys.stderr,
    ), payload

def main():
    print("=" * 50)
    print("  Decompiler Bot - GitHub Runner")
    print("=" * 50)

    all_files = fetch_files()
    if all_files is None:
        print("[RUNNER] Initial fetch failed. Retrying in 5 seconds...")
        time.sleep(5)
        all_files = fetch_files()
        if all_files is None:
            print("[RUNNER] Cannot start without code.")
            return

    update_saved_commits(FILES_TO_FETCH)

    while True:
        process, payload = run_bot(all_files)
        process.stdin.write(payload.encode())
        process.stdin.write(b"\n")
        process.stdin.flush()

        while process.poll() is None:
            time.sleep(10)
            if not os.path.exists(CHECK_FILE):
                continue

            os.remove(CHECK_FILE)
            print("[RUNNER] Command used, checking for updates...")

            changed = get_changed_files()
            if changed:
                print(f"[RUNNER] Changed: {', '.join(changed)}")
                new_files = fetch_files(changed)
                if new_files:
                    all_files.update(new_files)
                    update_saved_commits(changed)
                    print(f"[RUNNER] Updated {len(changed)} file(s). Restarting...")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except Exception:
                        process.kill()
                    break
            else:
                print("[RUNNER] Already up to date.")

        exit_code = process.returncode
        print(f"[RUNNER] Bot exited with code {exit_code}")
        print("[RUNNER] Restarting in 3 seconds...")
        time.sleep(3)

if __name__ == "__main__":
    main()
