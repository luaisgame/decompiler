import subprocess
import sys
import os
import time
import urllib.request
import shutil

REPO = "luaisgame/decompiler"
BRANCH = "main"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "bot_code")

FILES_TO_FETCH = [
    "bot.py",
    "core.py",
    "setup.py",
    "blacklist.py",
    "blacklistuser.py",
    "blacklistserver.py",
    "cookie.py",
    "decompile.py",
    "help.py",
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

def sync_from_github():
    print("[SYNC] Fetching latest code from GitHub...")
    os.makedirs(WORK_DIR, exist_ok=True)

    for path in FILES_TO_FETCH:
        content = fetch_file(path)
        if content is None:
            print(f"[SYNC] ERROR: Could not fetch {path}")
            return False
        local_path = os.path.join(WORK_DIR, path)
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[SYNC] {path}")

    env_src = os.path.join(SCRIPT_DIR, ".env")
    env_dst = os.path.join(WORK_DIR, ".env")
    if os.path.exists(env_src):
        shutil.copy2(env_src, env_dst)
        print("[SYNC] .env copied")

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

        try:
            process.wait()
        except KeyboardInterrupt:
            print("\n[RUNNER] Shutting down...")
            process.terminate()
            process.wait()
            break

        exit_code = process.returncode
        print(f"[RUNNER] Bot exited with code {exit_code}")

        print("[RUNNER] Restarting in 3 seconds...")
        time.sleep(3)

if __name__ == "__main__":
    main()
