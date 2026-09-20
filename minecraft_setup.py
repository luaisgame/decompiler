import os
import subprocess
import urllib.request
import json
import shutil

BASE_DIR = os.environ.get("BOT_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
MC_DIR = os.path.join(os.path.dirname(BASE_DIR), "minecraft")


def _is_server_folder(path):
    indicators = ["server.jar", "eula.txt", "world"]
    return any(os.path.exists(os.path.join(path, f)) for f in indicators)


def _get_latest_fabric():
    try:
        meta_url = "https://meta.fabricmc.net/v2/versions/loader"
        with urllib.request.urlopen(meta_url, timeout=15) as resp:
            loaders = json.loads(resp.read())
        stable = [l for l in loaders if l.get("stable")]
        fabric_ver = stable[0]["version"] if stable else loaders[0]["version"]
        mc_meta = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
        with urllib.request.urlopen(mc_meta, timeout=15) as resp:
            versions = json.loads(resp.read())
        release = next((v for v in versions["versions"] if v["type"] == "release"), None)
        if not release:
            return None, None
        return release["id"], fabric_ver
    except Exception as e:
        print(f"[MINECRAFT] Failed to get Fabric versions: {e}")
        return None, None


def _download_file(url, dest):
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as e:
        print(f"[MINECRAFT] Failed to download {url}: {e}")
        return False


def _get_latest_viaversion(mc_ver):
    try:
        url = f'https://api.modrinth.com/v2/project/ViaVersion/version?game_versions=["{mc_ver}"]&loaders=["fabric"]'
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        versions = data.get("data", data) if isinstance(data, dict) else data
        if versions and isinstance(versions, list) and len(versions) > 0:
            files = versions[0].get("files", [])
            if files:
                return files[0].get("url")
    except Exception as e:
        print(f"[MINECRAFT] Failed to get ViaVersion: {e}")
    return None


def _install_fabric_server(server_dir):
    print(f"[MINECRAFT] Installing Fabric server in {server_dir}...")
    mc_ver, fabric_ver = _get_latest_fabric()
    if not mc_ver or not fabric_ver:
        print("[MINECRAFT] Could not determine Fabric versions.")
        return False, None
    print(f"[MINECRAFT] MC {mc_ver}, Fabric Loader {fabric_ver}")

    installer_url = f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{fabric_ver}/fabric-installer-{fabric_ver}.jar"
    installer_path = os.path.join(server_dir, "fabric-installer.jar")
    if not _download_file(installer_url, installer_path):
        print("[MINECRAFT] Could not download Fabric installer.")
        return False, None

    try:
        result = subprocess.run(
            ["java", "-jar", installer_path, "server", "-mcversion", mc_ver,
             "-loader", fabric_ver, "-downloadMinecraft", "-dir", server_dir],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            print(f"[MINECRAFT] Fabric installer error: {result.stderr}")
            return False, None
    except Exception as e:
        print(f"[MINECRAFT] Fabric installer failed: {e}")
        return False, None

    eula_path = os.path.join(server_dir, "eula.txt")
    if not os.path.exists(eula_path):
        with open(eula_path, "w") as f:
            f.write("eula=true\n")

    props_path = os.path.join(server_dir, "server.properties")
    if not os.path.exists(props_path):
        with open(props_path, "w") as f:
            f.write("online-mode=true\nserver-port=25565\n")

    print("[MINECRAFT] Fabric server installed.")
    return True, mc_ver


def _install_viaversion(server_dir, mc_ver):
    mods_dir = os.path.join(server_dir, "mods")
    os.makedirs(mods_dir, exist_ok=True)

    existing = [f for f in os.listdir(mods_dir) if "viaversion" in f.lower()]
    if existing:
        print(f"[MINECRAFT] ViaVersion already installed: {existing}")
        return True

    print("[MINECRAFT] Downloading ViaVersion...")
    url = _get_latest_viaversion(mc_ver)
    if not url:
        print("[MINECRAFT] Could not find ViaVersion download URL.")
        return False

    filename = url.split("/")[-1]
    dest = os.path.join(mods_dir, filename)
    if _download_file(url, dest):
        print(f"[MINECRAFT] ViaVersion installed: {filename}")
        return True
    return False


def setup_server(server_dir):
    if _is_server_folder(server_dir):
        print(f"[MINECRAFT] Server already set up: {os.path.basename(server_dir)}")
        return True

    print(f"[MINECRAFT] New server: {os.path.basename(server_dir)} - cleaning...")
    for item in os.listdir(server_dir):
        item_path = os.path.join(server_dir, item)
        if os.path.isfile(item_path):
            os.remove(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)

    ok, mc_ver = _install_fabric_server(server_dir)
    if not ok:
        return False
    _install_viaversion(server_dir, mc_ver)
    return True


def start_playit():
    for p in [os.path.join(MC_DIR, "playit.exe"), shutil.which("playit") or "",
              os.path.expanduser(r"~\AppData\Local\playit\playit.exe"),
              r"C:\Program Files\playit\playit.exe"]:
        if p and os.path.exists(p):
            try:
                result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq playit.exe"],
                                        capture_output=True, text=True)
                if "playit.exe" in result.stdout:
                    print("[MINECRAFT] playit.gg already running.")
                    return None
            except Exception:
                pass
            print(f"[MINECRAFT] Starting playit.gg...")
            return subprocess.Popen(
                [p], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
    print("[MINECRAFT] playit.gg not found. Skipping tunnel.")
    return None


def start_mc_server(server_dir):
    jar = None
    for f in os.listdir(server_dir):
        if f.endswith(".jar") and ("fabric" in f.lower() or "server" in f.lower()):
            if "installer" not in f.lower():
                jar = os.path.join(server_dir, f)
                break
    if not jar:
        print(f"[MINECRAFT] No server jar found in {server_dir}")
        return None

    mem = os.environ.get("MC_MEMORY", "2G")
    print(f"[MINECRAFT] Starting {os.path.basename(server_dir)} with {mem}...")
    return subprocess.Popen(
        ["java", f"-Xmx{mem}", f"-Xms{mem}", "-jar", jar, "nogui"],
        cwd=server_dir,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    )


def run_minecraft_setup():
    if not os.path.isdir(MC_DIR):
        print("[MINECRAFT] No minecraft folder found. Skipping.")
        return

    print("[MINECRAFT] Found minecraft folder. Setting up...")
    start_playit()

    entries = [d for d in os.listdir(MC_DIR) if os.path.isdir(os.path.join(MC_DIR, d))]
    if not entries:
        print("[MINECRAFT] No server folders found.")
        return

    for name in entries:
        server_dir = os.path.join(MC_DIR, name)
        setup_server(server_dir)

    print("[MINECRAFT] Setup complete.")
