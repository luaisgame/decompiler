import subprocess
import sys
import os
import time

REPO_URL = "https://github.com/luaisgame/decompiler.git"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)

def run_command(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

def git_pull():
    print("[UPDATER] Pulling latest from GitHub...")
    code, out, err = run_command("git pull origin main", cwd=PARENT_DIR)
    if code == 0:
        print(f"[UPDATER] {out}")
        return True
    else:
        print(f"[UPDATER] Git pull failed: {err}")
        return False

def run_bot():
    print("[RUNNER] Starting bot.py...")
    bot_path = os.path.join(PARENT_DIR, "bot.py")
    process = subprocess.Popen(
        [sys.executable, bot_path],
        cwd=PARENT_DIR
    )
    return process

def main():
    print("=" * 50)
    print("  Decompiler Bot - Auto-Updating Runner")
    print("=" * 50)

    while True:
        git_pull()
        
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
