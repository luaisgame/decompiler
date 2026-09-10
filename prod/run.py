import subprocess
import sys
import os
import time
import urllib.request
import shutil
import json

REPO = "luaisgame/decompiler"
BRANCH = "main"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "bot_code")
COMMIT_FILE = os.path.join(SCRIPT_DIR, ".last_commit")

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
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}"
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
    except Exception as e:
        print(f"[SYNC] Failed to get latest commit: {e}")
        return None

def get_saved_commit():
    if os.path.exists(COMMIT_FILE):
        with open(COMMIT_FILE, "r") as f:
            return f.read().strip()
    return None

def save_commit(sha):
    with open(COMMIT_FILE, "w") as f:
        f.write(sha)

def sync_from_github(force=False):
    latest_sha = get_latest_commit()
    if not latest_sha:
        print("[SYNC] Could not determine latest commit, fetching anyway...")

    saved_sha = get_saved_commit()

    if not force and latest_sha and saved_sha and latest_sha == saved_sha:
        print(f"[SYNC] Already up to date ({latest_sha[:8]}), skipping.")
        return True

    if latest_sha and saved_sha:
        print(f"[SYNC] New commit detected: {saved_sha[:8]} -> {latest_sha[:8]}")
    else:
        print("[SYNC] Fetching latest code from GitHub...")

    os.makedirs(os.path.join(WORK_DIR, "commands"), exist_ok=True)
    for path in FILES_TO_FETCH:
        content = fetch_file(path)
        if content is None:
            print(f"[SYNC] ERROR: Could not fetch {path}")
            return False
        local_path = os.path.join(WORK_DIR, path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[SYNC] {path}")

    env_src = os.path.join(SCRIPT_DIR, ".env")
    env_dst = os.path.join(WORK_DIR, ".env")
    if os.path.exists(env_src):
        shutil.copy2(env_src, env_dst)
        print("[SYNC] .env copied")

    if latest_sha:
        save_commit(latest_sha)

    print("[SYNC] All files synced.")
    return True

def run_bot():
    print("[RUNNER] Starting bot.py...")
    bot_path = os.path.join(WORK_DIR, "bot.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = WORK_DIR
    process = subprocess.Popen(
        [sys.executable, bot_path],
        cwd=WORK_DIR,
        env=env
    )
    return process

def check_for_updates():
    latest_sha = get_latest_commit()
    saved_sha = get_saved_commit()
    if latest_sha and saved_sha and latest_sha != saved_sha:
        print(f"[RUNNER] Update detected: {saved_sha[:8]} -> {latest_sha[:8]}")
        return True
    return False

def main():
    print("=" * 50)
    print("  Decompiler Bot - GitHub Runner")
    print("=" * 50)

    while True:
        if not sync_from_github():
            print("[RUNNER] Sync failed. Retrying in 5 seconds...")
            time.sleep(5)
            continue

        process = run_bot()

        while process.poll() is None:
            time.sleep(60)
            if check_for_updates():
                print("[RUNNER] Restarting bot for update...")
                process.terminate()
                process.wait()
                if sync_from_github(force=True):
                    print("[RUNNER] Restarting with new code...")
                    break
                else:
                    print("[RUNNER] Sync failed, continuing with current code.")

        exit_code = process.returncode
        print(f"[RUNNER] Bot exited with code {exit_code}")

        print("[RUNNER] Restarting in 3 seconds...")
        time.sleep(3)

if __name__ == "__main__":
    main()
