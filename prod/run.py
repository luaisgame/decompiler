import subprocess
import sys
import os
import time
import urllib.request
import json

REPO = "luaisgame/decompiler"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
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

def get(path):
    try:
        with urllib.request.urlopen(f"{RAW}/{path}", timeout=15) as r:
            return r.read().decode()
    except Exception as e:
        print(f"[FETCH] {path}: {e}")
        return None

def fetch():
    d = {}
    for p in FILES:
        c = get(p)
        if c is None:
            return None
        d[p] = c
    return d

PY = sys.executable
if getattr(sys, "frozen", False):
    for n in ["python.exe", "python3.exe"]:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            c = os.path.join(d, n)
            if os.path.exists(c):
                PY = c
                break

LAUNCHER = r'''
import sys,os,json,types
os.environ["BOT_BASE_DIR"]=r"''' + SCRIPT_DIR.replace("\\","\\\\") + '''"
_p=json.loads(sys.stdin.readline())
sys.argv=[sys.argv[0]]
if "commands" not in sys.modules:
    pkg=types.ModuleType("commands");pkg.__path__=[];sys.modules["commands"]=pkg
for path,code in _p.items():
    if path=="bot.py": continue
    mn=path.replace("/",".").replace(".py","")
    if mn.endswith(".__init__"): mn=mn[:-9]
    pkg="commands" if mn.startswith("commands.") else None
    m=types.ModuleType(mn,code);m.__file__=f"<github:{mn}>";m.__loader__=None
    if pkg: m.__package__=pkg
    sys.modules[mn]=m
    exec(compile(code,f"<github:{mn}>","exec"),m.__dict__)
exec(compile(_p["bot.py"],"<github:bot>","exec"),{"__name__":"__main__","__file__":"<github:bot>"})
'''

print("="*50)
print("  Decompiler Bot - GitHub Runner")
print("="*50)

while True:
    files = fetch()
    if files is None:
        print("[RUNNER] Fetch failed, retrying in 5s...")
        time.sleep(5)
        continue

    p = subprocess.Popen([PY,"-c",LAUNCHER], stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr)
    p.stdin.write(json.dumps(files).encode() + b"\n")
    p.stdin.flush()

    while p.poll() is None:
        time.sleep(10)
        if os.path.exists(CHECK):
            os.remove(CHECK)
            print("[RUNNER] Command used, fetching latest...")
            break

    print(f"[RUNNER] Restarting...")
    try:
        p.terminate(); p.wait(timeout=5)
    except:
        p.kill()
    time.sleep(2)
