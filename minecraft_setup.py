import os
import subprocess
import urllib.request
import json
import shutil
import glob

BASE_DIR = os.environ.get("BOT_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
MC_DIR = os.path.join(os.path.dirname(BASE_DIR), "minecraft")


def _mc_ver_to_java(mc_ver):
    try:
        manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
        with urllib.request.urlopen(manifest_url, timeout=15) as resp:
            manifest = json.loads(resp.read())
        version_entry = next((v for v in manifest["versions"] if v["id"] == mc_ver), None)
        if version_entry:
            with urllib.request.urlopen(version_entry["url"], timeout=15) as resp:
                version_meta = json.loads(resp.read())
            java_info = version_meta.get("javaVersion", {})
            major_version = java_info.get("majorVersion")
            if major_version:
                print(f"[MINECRAFT] Mojang says MC {mc_ver} needs Java {major_version}")
                return major_version
    except Exception as e:
        print(f"[MINECRAFT] Failed to query Mojang for Java version: {e}")
    try:
        major = int(mc_ver.split(".")[1])
    except Exception:
        return 21
    if major <= 16:
        return 8
    elif major <= 17:
        return 17
    elif major <= 20:
        return 17
    else:
        return 21


def _find_java(java_ver):
    candidates = []
    if os.name == "nt":
        for base in [
            os.environ.get("JAVA_HOME", ""),
            r"C:\Program Files\Microsoft",
            r"C:\Program Files\Eclipse Adoptium",
            r"C:\Program Files\Java",
            r"C:\Program Files (x86)\Java",
        ]:
            if base and os.path.isdir(base):
                for d in os.listdir(base):
                    if f"jdk-{java_ver}" in d.lower() or f"jdk{java_ver}" in d.lower():
                        p = os.path.join(base, d, "bin", "java.exe")
                        if os.path.exists(p):
                            candidates.append(p)
        for p in glob.glob(r"C:\Program Files\Microsoft\jdk-*\bin\java.exe"):
            if f"-{java_ver}." in p.lower() or f"-{java_ver}-" in p.lower():
                candidates.append(p)
        for p in glob.glob(r"C:\Program Files\Eclipse Adoptium\jdk-*\bin\java.exe"):
            if f"-{java_ver}." in p.lower() or f"-{java_ver}-" in p.lower():
                candidates.append(p)
    else:
        for pattern in [f"/usr/lib/jvm/java-{java_ver}-*/bin/java", f"/usr/lib/jvm/java-{java_ver}-openjdk*/bin/java"]:
            candidates.extend(glob.glob(pattern))
    if candidates:
        print(f"[MINECRAFT] Found Java {java_ver}: {candidates[0]}")
        return candidates[0]
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=5)
        version_str = result.stderr + result.stdout
        if f"version \"{java_ver}" in version_str or f"version '{java_ver}" in version_str:
            print(f"[MINECRAFT] Default java is Java {java_ver}")
            return "java"
    except Exception:
        pass
    print(f"[MINECRAFT] Java {java_ver} not found, falling back to default java")
    return "java"


def _is_server_folder(path):
    indicators = ["server.jar", "eula.txt", "world"]
    return any(os.path.exists(os.path.join(path, f)) for f in indicators)


def _get_latest_fabric():
    try:
        mc_meta = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
        with urllib.request.urlopen(mc_meta, timeout=15) as resp:
            versions = json.loads(resp.read())
        release = next((v for v in versions["versions"] if v["type"] == "release"), None)
        if not release:
            return None, None
        meta_url = f"https://meta.fabricmc.net/v2/versions/loader/{release['id']}"
        with urllib.request.urlopen(meta_url, timeout=15) as resp:
            loaders = json.loads(resp.read())
        stable = [l for l in loaders if l.get("game", {}).get("stable")]
        if not stable:
            stable = [l for l in loaders if l.get("loader", {}).get("stable")]
        if stable:
            loader = stable[0]["loader"]
            return release["id"], loader["version"]
        if loaders:
            loader = loaders[0]["loader"]
            return release["id"], loader["version"]
        return release["id"], None
    except Exception as e:
        print(f"[MINECRAFT] Failed to get Fabric versions: {e}")
        return None, None


def _get_latest_forge():
    try:
        promo_url = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
        with urllib.request.urlopen(promo_url, timeout=15) as resp:
            data = json.loads(resp.read())
        promos = data.get("promos", {})
        recommended = promos.get("latest")
        if not recommended:
            for v in reversed(list(promos.keys())):
                if "recommended" in promos.get(v, ""):
                    recommended = v
                    break
            else:
                recommended = next(iter(reversed(list(promos.keys()))), None)
        if not recommended:
            return None, None
        mc_ver = recommended
        forge_ver = promos.get(recommended, "")
        if not forge_ver:
            parts = recommended.split("-", 1)
            if len(parts) == 2:
                mc_ver, forge_ver = parts[0], parts[1]
        else:
            if "-" in recommended:
                parts = recommended.split("-", 1)
                mc_ver, forge_ver = parts[0], parts[1]
        return mc_ver, forge_ver
    except Exception as e:
        print(f"[MINECRAFT] Failed to get Forge versions: {e}")
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
        url = f'https://api.modrinth.com/v2/project/ViaVersion/version?game_versions=%5B%22{mc_ver}%22%5D&loaders=%5B%22fabric%22%5D'
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        versions = data.get("data", data) if isinstance(data, dict) else data
        if versions and isinstance(versions, list) and len(versions) > 0:
            files = versions[0].get("files", [])
            if files:
                return files[0].get("url")
    except Exception as e:
        print(f"[MINECRAFT] ViaVersion not available for {mc_ver}: {e}")
    return None


def _install_fabric_server(server_dir):
    print(f"[MINECRAFT] Installing Fabric server in {server_dir}...")
    mc_ver, loader_ver = _get_latest_fabric()
    if not mc_ver:
        print("[MINECRAFT] Could not determine latest MC version.")
        return False, None
    installer_ver = "0.11.2"
    print(f"[MINECRAFT] MC {mc_ver}, Loader {loader_ver}, Installer {installer_ver}")

    java_ver = _mc_ver_to_java(mc_ver)
    java = _find_java(java_ver)

    installer_url = f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{installer_ver}/fabric-installer-{installer_ver}.jar"
    installer_path = os.path.join(server_dir, "fabric-installer.jar")
    if not _download_file(installer_url, installer_path):
        print("[MINECRAFT] Could not download Fabric installer.")
        return False, None

    try:
        cmd = [java, "-jar", installer_path, "server", "-mcversion", mc_ver, "-dir", server_dir]
        if loader_ver:
            cmd.extend(["-loader", loader_ver])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300
        )
        print(f"[MINECRAFT] Installer output: {result.stdout}")
        if result.stderr:
            print(f"[MINECRAFT] Installer stderr: {result.stderr}")
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


def _install_forge_server(server_dir):
    print(f"[MINECRAFT] Installing Forge server in {server_dir}...")
    mc_ver, forge_ver = _get_latest_forge()
    if not mc_ver or not forge_ver:
        print("[MINECRAFT] Could not determine latest Forge version.")
        return False, None
    print(f"[MINECRAFT] MC {mc_ver}, Forge {forge_ver}")

    java_ver = _mc_ver_to_java(mc_ver)
    java = _find_java(java_ver)

    installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_ver}-{forge_ver}/forge-{mc_ver}-{forge_ver}-installer.jar"
    installer_path = os.path.join(server_dir, f"forge-{mc_ver}-{forge_ver}-installer.jar")
    if not _download_file(installer_url, installer_path):
        print("[MINECRAFT] Could not download Forge installer.")
        return False, None

    try:
        result = subprocess.run(
            [java, "-jar", installer_path, "--installServer"],
            cwd=server_dir,
            capture_output=True, text=True, timeout=600
        )
        print(f"[MINECRAFT] Installer output: {result.stdout}")
        if result.stderr:
            print(f"[MINECRAFT] Installer stderr: {result.stderr}")
        if result.returncode != 0:
            print(f"[MINECRAFT] Forge installer error: {result.stderr}")
            return False, None
    except Exception as e:
        print(f"[MINECRAFT] Forge installer failed: {e}")
        return False, None

    run_bat = os.path.join(server_dir, "run.bat")
    run_sh = os.path.join(server_dir, "run.sh")
    if os.name == "nt" and os.path.exists(run_bat):
        print(f"[MINECRAFT] Forge run.bat found.")
    elif os.path.exists(run_sh):
        print(f"[MINECRAFT] Forge run.sh found.")
    else:
        print("[MINECRAFT] No run script found after Forge install. Looking for args.txt...")
        for root, dirs, files in os.walk(server_dir):
            for f in files:
                if f == "win_args.txt" or f == "unix_args.txt":
                    print(f"[MINECRAFT] Found args file: {os.path.join(root, f)}")

    eula_path = os.path.join(server_dir, "eula.txt")
    if not os.path.exists(eula_path):
        with open(eula_path, "w") as f:
            f.write("eula=true\n")

    props_path = os.path.join(server_dir, "server.properties")
    if not os.path.exists(props_path):
        with open(props_path, "w") as f:
            f.write("online-mode=true\nserver-port=25565\n")

    print("[MINECRAFT] Forge server installed.")
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


def setup_server(server_dir, loader_type="fabric"):
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

    if loader_type == "forge":
        ok, mc_ver = _install_forge_server(server_dir)
    else:
        ok, mc_ver = _install_fabric_server(server_dir)
    if not ok:
        return False
    if loader_type == "fabric":
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


def _is_forge_server(server_dir):
    if os.path.exists(os.path.join(server_dir, "run.bat")) or os.path.exists(os.path.join(server_dir, "run.sh")):
        return True
    libs = os.path.join(server_dir, "libraries", "net", "minecraftforge")
    if os.path.isdir(libs):
        return True
    return False


def _detect_mc_ver(server_dir):
    libs_dir = os.path.join(server_dir, "libraries", "net", "minecraftforge", "forge")
    if os.path.isdir(libs_dir):
        for d in os.listdir(libs_dir):
            parts = d.split("-")
            if len(parts) >= 2:
                return parts[0]
    libs_dir2 = os.path.join(server_dir, "libraries", "net", "fabricmc")
    if os.path.isdir(libs_dir2):
        for d in os.listdir(libs_dir2):
            if d.startswith("fabric-loader-"):
                pass
    versions_json = os.path.join(server_dir, "versions")
    if os.path.isdir(versions_json):
        for f in os.listdir(versions_json):
            if f.endswith(".json"):
                return f.replace(".json", "")
    return None


def _get_java_for_server(server_dir):
    if _is_forge_server(server_dir):
        mc_ver = _detect_mc_ver(server_dir)
        if mc_ver:
            java_ver = _mc_ver_to_java(mc_ver)
            return _find_java(java_ver), mc_ver
    return "java", _detect_mc_ver(server_dir)


FORGE_JVM_ARGS = [
    "--add-opens", "java.base/java.lang=ALL-UNNAMED",
    "--add-opens", "java.base/java.lang.invoke=ALL-UNNAMED",
    "--add-opens", "java.base/java.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.nio=ALL-UNNAMED",
    "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    "--add-opens", "java.base/java.io=ALL-UNNAMED",
    "--add-opens", "java.base/sun.security.ssl=ALL-UNNAMED",
    "--add-opens", "java.base/sun.security.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.net=ALL-UNNAMED",
]


def _find_user_jvm_args(server_dir):
    for name in ["user_jvm_args.txt", ".javaargs"]:
        p = os.path.join(server_dir, name)
        if os.path.exists(p):
            return p
    return None


def start_mc_server(server_dir):
    forge = _is_forge_server(server_dir)
    mem = os.environ.get("MC_MEMORY", "2G")
    java, mc_ver = _get_java_for_server(server_dir)

    if forge:
        if os.name == "nt" and os.path.exists(os.path.join(server_dir, "run.bat")):
            print(f"[MINECRAFT] Starting Forge server via run.bat with {mem}...")
            return subprocess.Popen(
                ["cmd", "/c", "run.bat"],
                cwd=server_dir,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env={**os.environ, "JAVA_FLAGS": f"-Xmx{mem} -Xms{mem} {' '.join(FORGE_JVM_ARGS)}"}
            )
        elif os.path.exists(os.path.join(server_dir, "run.sh")):
            print(f"[MINECRAFT] Starting Forge server via run.sh with {mem}...")
            return subprocess.Popen(
                ["bash", "run.sh"],
                cwd=server_dir,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env={**os.environ, "JAVA_FLAGS": f"-Xmx{mem} -Xms{mem} {' '.join(FORGE_JVM_ARGS)}"}
            )
        jar = None
        for f in os.listdir(server_dir):
            if f.endswith(".jar") and "universal" in f.lower():
                jar = os.path.join(server_dir, f)
                break
        if not jar:
            for f in os.listdir(server_dir):
                if f.endswith(".jar") and "installer" not in f.lower():
                    jar = os.path.join(server_dir, f)
                    break
        if jar:
            args_file = os.path.join(server_dir, "libraries", "net", "minecraftforge", "forge")
            win_args = None
            for root, dirs, files in os.walk(args_file):
                for f in files:
                    if f == "win_args.txt":
                        win_args = os.path.join(root, f)
                        break
                if win_args:
                    break
            if win_args:
                user_args = _find_user_jvm_args(server_dir)
                extra = []
                if user_args:
                    with open(user_args, "r") as uf:
                        extra = [l.strip() for l in uf.readlines() if l.strip() and not l.strip().startswith("#")]
                else:
                    extra = FORGE_JVM_ARGS
                print(f"[MINECRAFT] Starting Forge via args.txt with {mem}...")
                return subprocess.Popen(
                    [java, f"-Xmx{mem}", f"-Xms{mem}"] + extra + [f"@{win_args}", "nogui"],
                    cwd=server_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
            print(f"[MINECRAFT] Starting Forge jar {os.path.basename(jar)} with {mem}...")
            return subprocess.Popen(
                [java, f"-Xmx{mem}", f"-Xms{mem}"] + FORGE_JVM_ARGS + ["-jar", jar, "nogui"],
                cwd=server_dir,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
        print(f"[MINECRAFT] No Forge jar or run script found in {server_dir}")
        return None

    jar = None
    for f in os.listdir(server_dir):
        if f.endswith(".jar") and ("fabric" in f.lower() or "server" in f.lower()):
            if "installer" not in f.lower():
                jar = os.path.join(server_dir, f)
                break
    if not jar:
        for f in os.listdir(server_dir):
            if f.endswith(".jar") and "installer" not in f.lower():
                jar = os.path.join(server_dir, f)
                break
    if not jar:
        print(f"[MINECRAFT] No server jar found in {server_dir}")
        return None

    print(f"[MINECRAFT] Starting {os.path.basename(server_dir)} with {mem}...")
    return subprocess.Popen(
        [java, f"-Xmx{mem}", f"-Xms{mem}", "-jar", jar, "nogui"],
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
