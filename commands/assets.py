"""Asset packs: download every animation & sound from a decompiled place.

Flow
----
1. ``decompile_client.luau`` scans the live place for ``Sound`` / ``Animation``
   instances and POSTs them to ``/decompile`` as ``{"status": "assets", ...}``.
2. After the place file is uploaded, ``execute_decompile_job`` calls
   :func:`create_pack`, which turns that manifest into
   ``storage/assets/<pack_id>/`` with ``Animations/`` and ``Sounds/``
   sub-folders, then :func:`start_download`` pulls every asset from Roblox
   using the same ``.ROBLOSECURITY`` cookie that joined the game.
3. The requester (or the bot owner) gets a **View Assets Downloaded** button on
   the result embed -> ``/assets/<pack_id>`` on the website.
4. The website is gated by the Discord OAuth session: not logged in shows the
   Login Required screen, logged in but not the requester (and not the owner)
   shows a 403.
"""

import asyncio
import html
import json
import os
import random
import re
import shutil
import time
import uuid
import zipfile

import aiohttp
import discord
from aiohttp import web

from . import core

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ASSETS_DIR = os.path.join(core.storage_dir, "assets")
CACHE_DIR = os.path.join(ASSETS_DIR, "_cache")
PENDING_DIR = os.path.join(ASSETS_DIR, "_pending")
SITE_URL = core.STORAGE_SITE_URL.rstrip("/") + "/"

ASSETS_MAX_BYTES = 10 * 1024 * 1024 * 1024   # 10 GB budget for asset packs
MAX_PER_KIND = 1000000                           # safety cap sent by the client

PACK_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,90}$")
SAFE_FILE_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,160}$")

AUDIO_EXTS = {".ogg", ".mp3", ".wav", ".flac", ".m4a", ".webm"}
MODEL_EXTS = {".rbxm", ".rbxmx"}

FOLDERS = (("Animations", "animation"), ("Sounds", "sound"))

_pack_tasks = {}      # pack_id -> asyncio.Task


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _ensure_dirs():
    for d in (ASSETS_DIR, CACHE_DIR, PENDING_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def _is_pack_id(pack_id) -> bool:
    # "_" prefixed folders are reserved (_cache, _pending), never packs.
    return bool(pack_id) and not str(pack_id).startswith("_") and bool(PACK_ID_RE.match(str(pack_id)))


def pack_dir(pack_id: str):
    """Absolute pack folder, or None when the id is not a valid pack id."""
    if not _is_pack_id(pack_id):
        return None
    path = os.path.join(ASSETS_DIR, pack_id)
    if os.path.abspath(path).startswith(os.path.abspath(ASSETS_DIR) + os.sep):
        return path
    return None


def _safe_name(value: str, fallback: str) -> str:
    value = (value or "").strip()
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = fallback
    return value[:120].strip(" .") or fallback


def load_manifest(pack_id: str):
    path = pack_dir(pack_id)
    if not path:
        return None
    mf = os.path.join(path, "manifest.json")
    if not os.path.isfile(mf):
        return None
    try:
        with open(mf, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ASSETS] Failed to read manifest {pack_id}: {e}")
        return None


def save_manifest(pack_id: str, manifest: dict):
    path = pack_dir(pack_id)
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        manifest["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = os.path.join(path, "manifest.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(path, "manifest.json"))
        return True
    except Exception as e:
        print(f"[ASSETS] Failed to write manifest {pack_id}: {e}")
        return False


def list_packs():
    """Every pack on disk, newest first. Bad folders are skipped."""
    _ensure_dirs()
    out = []
    try:
        names = sorted(os.listdir(ASSETS_DIR), key=lambda n: os.path.getmtime(
            os.path.join(ASSETS_DIR, n)) if os.path.isdir(os.path.join(ASSETS_DIR, n)) else 0,
            reverse=True)
    except OSError:
        return out
    for name in names:
        if not _is_pack_id(name):
            continue
        manifest = load_manifest(name)
        if manifest:
            out.append(manifest)
    return out


def pack_state(manifest: dict) -> dict:
    entries = (manifest.get("animations") or []) + (manifest.get("sounds") or [])
    ready = sum(1 for e in entries if e.get("status") == "ready")
    failed = sum(1 for e in entries if e.get("status") == "failed")
    return {"total": len(entries), "ready": ready, "failed": failed}


def display_name(entry: dict) -> str:
    return entry.get("catalog") or entry.get("name") or f"asset_{entry.get('id', '')}"


def can_view(request, manifest: dict) -> bool:
    user = core._get_user_info(request)
    if not user:
        return False
    uid = str(user.get("id", ""))
    return uid == str(manifest.get("author_id", "")) or int(user.get("id", 0)) == core.BOT_OWNER_ID


# --------------------------------------------------------------------------
# manifest sent by the injected client
# --------------------------------------------------------------------------
def on_client_manifest(payload: dict):
    """Store the Sound/Animation scan posted by decompile_client.luau."""
    _ensure_dirs()
    place_id = str(payload.get("place_id") or "").strip()
    if not place_id:
        place_id = str(core.current_active_data or {}).get("place_id", "")
    if not place_id:
        print("[ASSETS] Client manifest ignored: no place id.")
        return
    sounds = payload.get("sounds") or []
    anims = payload.get("animations") or []
    if not isinstance(sounds, list):
        sounds = []
    if not isinstance(anims, list):
        anims = []
    data = {
        "place_id": place_id,
        "universe_id": str(payload.get("universe_id") or "").strip(),
        "received": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sounds": sounds[:MAX_PER_KIND],
        "animations": anims[:MAX_PER_KIND],
    }
    try:
        path = os.path.join(PENDING_DIR, f"{place_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"[ASSETS] Received manifest for place {place_id}: "
              f"{len(data['animations'])} animation(s), {len(data['sounds'])} sound(s).")
    except Exception as e:
        print(f"[ASSETS] Could not store client manifest: {e}")


def _pop_pending(place_id: str):
    path = os.path.join(PENDING_DIR, f"{place_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(path)
        return data
    except Exception as e:
        print(f"[ASSETS] Could not read pending manifest: {e}")
        return None


# --------------------------------------------------------------------------
# pack creation
# --------------------------------------------------------------------------
# asset ids referenced anywhere in a place file: Sound/Animation properties,
# rbxassetid:// strings in script source, Luau string constants inside the
# saved bytecode, XML <url> nodes — including ids the injected client scan
# never saw because the asset is only referenced, not instantiated.
_ASSET_REF_RES = (
    re.compile(rb"rbxassetid://(\d{1,20})", re.I),
    re.compile(rb"roblox\.com/asset/?\?id=(\d{1,20})", re.I),
    re.compile(rb"roblox\.com/asset/(\d{1,20})", re.I),
)
SCAN_MAX_IDS = 3000
SCAN_CHUNK = 4 * 1024 * 1024       # stream the file, never load it whole
SCAN_OVERLAP = 64                  # keep pattern tails across chunk borders


def scan_place_file(path: str) -> list[str]:
    """Harvest every asset id referenced in a place file (binary or XML).

    Streams the file in chunks so multi-hundred-MB places stay cheap.
    Returns digit ids, deduped, positives only; never raises."""
    found: dict = {}
    try:
        with open(path, "rb") as f:
            prev = b""
            while True:
                chunk = f.read(SCAN_CHUNK)
                if not chunk:
                    break
                data = prev + chunk
                for rx in _ASSET_REF_RES:
                    for m in rx.finditer(data):
                        aid = m.group(1).decode("ascii", "ignore")
                        if aid and int(aid) > 0 and aid not in found:
                            found[aid] = None
                            if len(found) >= SCAN_MAX_IDS:
                                return list(found)
                prev = data[-SCAN_OVERLAP:]
    except OSError as e:
        print(f"[ASSETS] scan_place_file({path}) failed: {e}")
        return []
    return list(found)


def _collect(pending: dict, kind: str, raw_key: str):
    """Group raw instance rows by asset id -> {id: entry}."""
    rows = pending.get(raw_key) or []
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "").strip()
        if not aid.isdigit() or int(aid) <= 0:
            continue
        name = str(row.get("name") or "").strip()
        path = str(row.get("path") or "").strip()
        entry = found.get(aid)
        if entry is None:
            found[aid] = {"id": aid, "kind": kind, "name": name,
                          "paths": [path] if path else [], "catalog": None,
                          "file": None, "size": 0, "status": "queued", "error": None}
        else:
            if path and path not in entry["paths"]:
                entry["paths"].append(path)
            if not entry["name"] and name:
                entry["name"] = name
    return found


def _unique_file_names(entries):
    """Give every entry a collision free file name inside its folder."""
    used = set()
    for entry in entries:
        base = _safe_name(display_name(entry), f"asset_{entry['id']}")
        candidate = f"{base} ({entry['id']})"
        n = 2
        while candidate.lower() in used:
            candidate = f"{base} ({entry['id']})_{n}"
            n += 1
        used.add(candidate.lower())
        entry["base"] = candidate


async def create_pack(place_id: str, author_id, display_name_: str, game_name: str,
                      icon_url: str = None, game_version: str = None,
                      cookie: str = None, download: bool = True,
                      scanned_ids=None):
    """Turn the client scan into a downloadable pack. Returns pack_id or None.

    ``scanned_ids`` are asset ids harvested from the place file binary (see
    :func:`scan_place_file`); they are typed and merged by the download worker
    so assets the injected client scan missed still end up in the pack."""
    _ensure_dirs()
    pending = _pop_pending(str(place_id))
    if not pending and not (scanned_ids or []):
        print(f"[ASSETS] No asset scan stored for place {place_id}; skipping pack.")
        return None
    if not pending:
        # client manifest never arrived, but the place file had references
        pending = {"place_id": str(place_id), "universe_id": "",
                   "sounds": [], "animations": []}

    animations = list(_collect(pending, "animation", "animations").values())
    sounds = list(_collect(pending, "sound", "sounds").values())

    known_ids = {e["id"] for e in animations} | {e["id"] for e in sounds}
    scanned = []
    for aid in (scanned_ids or []):
        aid = str(aid).strip()
        if aid.isdigit() and int(aid) > 0 and aid not in known_ids and aid not in scanned:
            scanned.append(aid)
            if len(scanned) >= SCAN_MAX_IDS:
                break

    if not animations and not sounds and not scanned:
        print(f"[ASSETS] Place {place_id} has no animations/sounds; skipping pack.")
        return None

    _unique_file_names(animations)
    _unique_file_names(sounds)

    pack_id = f"{place_id}_{author_id}_{uuid.uuid4().hex[:8]}"
    path = pack_dir(pack_id)
    if not path:
        return None
    for folder, _ in FOLDERS:
        os.makedirs(os.path.join(path, folder), exist_ok=True)

    manifest = {
        "pack_id": pack_id,
        "place_id": str(place_id),
        "universe_id": str(pending.get("universe_id") or ""),
        "scanned_ids": scanned,
        "game_name": game_name or f"Place {place_id}",
        "icon_url": icon_url or "",
        "game_version": game_version or "N/A",
        "author_id": str(author_id),
        "display_name": display_name_ or "Unknown",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "downloading" if download else "queued",
        "animations": animations,
        "sounds": sounds,
        "zip_state": "",
    }
    save_manifest(pack_id, manifest)
    cleanup_asset_packs()
    print(f"[ASSETS] Created pack {pack_id}: {len(animations)} animation(s), "
          f"{len(sounds)} sound(s).")
    if download:
        start_download(pack_id, cookie)
    return pack_id


def link_pack_to_game(pack_id: str, filename: str):
    """Add the pack id to the games.json entry so the homepage can link it."""
    try:
        entries = core._read_games_json()
        changed = False
        for e in entries:
            if e.get("filename") == filename and e.get("assets_pack") != pack_id:
                e["assets_pack"] = pack_id
                changed = True
        if changed:
            core._write_games_json(entries)
    except Exception as e:
        print(f"[ASSETS] Could not link pack to games.json: {e}")


# --------------------------------------------------------------------------
# downloading
# --------------------------------------------------------------------------
# Multi-cookie pool: every Roblox API call is round-robined across the cookies
# in cookies.txt (+ ROBLOX_COOKIE env + the cookie that joined the game), each
# with its own session, concurrency semaphore and kartFr-style fixed-window
# rate limiter. Failover on 401 (cookie dead) / 403 / 409 (this account can't
# see the asset -> next cookie) / 429 (pause that cookie, next cookie takes
# over), so the aggregate throughput is N cookies x limit per window.
STUDIO_UA = "RobloxStudio/WinInet"          # what Studio (and kartFr) sends
BATCH_URL = "https://assetdelivery.roblox.com/v2/assets/batch"
BATCH_SIZE = 50                              # API hard limit per batch request

API_RATE_LIMIT = int(os.getenv("ASSETS_RATE_LIMIT", "40") or 40)   # req/window/cookie
API_RATE_WINDOW = float(os.getenv("ASSETS_RATE_WINDOW", "5") or 5)  # seconds
SLOT_CONCURRENCY = 8                         # parallel requests per cookie
CDN_CONCURRENCY = 16                         # parallel CDN downloads (cookie-free)
NAME_CONCURRENCY = 32                        # parallel catalog name lookups


class _FixedWindow:
    """Fixed-window limiter: at most `limit` acquisitions per `window` seconds.

    Mirrors kartFr/Asset-Reuploader's internal/taskqueue/fixedwindow.go — when
    the window is exhausted the caller sleeps until the next window opens
    instead of erroring, so N cookies give N x limit requests per window.
    """

    def __init__(self, limit: int, window: float):
        self.limit = max(1, int(limit))
        self.window = max(0.1, float(window))
        self.start = time.monotonic()
        self.count = 0
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                if now - self.start >= self.window:
                    self.start = now
                    self.count = 0
                if self.count < self.limit:
                    self.count += 1
                    return
                wait = self.window - (now - self.start)
            await asyncio.sleep(min(max(wait, 0.02) + random.uniform(0, 0.05), self.window))


class CookieSlot:
    """One .ROBLOSECURITY cookie: its session, semaphore and rate window."""

    def __init__(self, index: int, cookie: str, label: str):
        self.index = index
        self.cookie = cookie
        self.label = label
        self.session: aiohttp.ClientSession | None = None
        self.sem = asyncio.Semaphore(SLOT_CONCURRENCY)
        self.window = _FixedWindow(API_RATE_LIMIT, API_RATE_WINDOW)
        self.invalid = False          # saw a 401: cookie is dead
        self.disabled_until = 0.0     # saw a 429: paused until then
        self.requests = 0

    def pause(self, seconds: float):
        self.disabled_until = max(self.disabled_until,
                                  time.monotonic() + min(float(seconds), 120))

    def mark_invalid(self, reason: str):
        if not self.invalid:
            self.invalid = True
            print(f"[ASSETS] Cookie '{self.label}' disabled: {reason}")


class CookiePool:
    """Round-robin pool of cookie sessions with failover across accounts."""

    def __init__(self, extra_cookie: str = None, place_id=None, universe_id=None,
                 use_cookies: bool = True):
        self.place_id = str(place_id or "").strip() or None
        self.universe_id = str(universe_id or "").strip() or None
        self.slots: list[CookieSlot] = []
        self._rr = 0
        self.anon: aiohttp.ClientSession | None = None
        self.cdn_sem = asyncio.Semaphore(CDN_CONCURRENCY)
        self._owns_sessions = True
        if use_cookies:
            seen = set()
            candidates = []
            if extra_cookie:
                candidates.append((extra_cookie, "job"))
            try:
                candidates += [(c, f"cookies.txt[{i}]")
                               for i, c in enumerate(core.load_cookies())]
            except Exception:
                pass
            if getattr(core, "ROBLOX_COOKIE", ""):
                candidates.append((core.ROBLOX_COOKIE, "env"))
            try:
                active = core.get_active_cookie()
                if active:
                    candidates.append((active, "active"))
            except Exception:
                pass
            for cookie, label in candidates:
                cookie = (cookie or "").strip()
                if not cookie or cookie in seen:
                    continue
                seen.add(cookie)
                self.slots.append(CookieSlot(len(self.slots), cookie, label))
        if not self.slots:
            # no cookies at all: open sources still fetch public assets
            self.slots.append(CookieSlot(0, "", "anonymous"))

    @classmethod
    def from_session(cls, session: aiohttp.ClientSession, place_id=None) -> "CookiePool":
        """Wrap an existing session (tests / one-off scripts) as a 1-slot pool."""
        pool = cls(use_cookies=False, place_id=place_id)
        slot = CookieSlot(0, "session" if session.headers.get("Cookie") else "", "wrapped")
        slot.session = session
        slot.window = _FixedWindow(10_000, 1.0)   # caller owns its own pacing
        pool.slots = [slot]
        pool.anon = session
        pool._owns_sessions = False
        return pool

    @property
    def has_cookie(self) -> bool:
        return any(s.cookie for s in self.slots)

    async def __aenter__(self):
        for slot in self.slots:
            if slot.session is None:
                headers = {"User-Agent": UA, "Accept": "*/*"}
                if slot.cookie:
                    headers["Cookie"] = f".ROBLOSECURITY={slot.cookie}"
                slot.session = aiohttp.ClientSession(
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45),
                    connector=aiohttp.TCPConnector(limit=32, ttl_dns_cache=300))
        if self.anon is None:
            self.anon = aiohttp.ClientSession(
                headers={"User-Agent": UA, "Accept": "*/*"},
                timeout=aiohttp.ClientTimeout(total=60),
                connector=aiohttp.TCPConnector(limit=32, ttl_dns_cache=300))
        return self

    async def __aexit__(self, *exc):
        if not self._owns_sessions:
            return
        for slot in self.slots:
            if slot.session is not None:
                try:
                    await slot.session.close()
                except Exception:
                    pass
        if self.anon is not None:
            try:
                await self.anon.close()
            except Exception:
                pass

    async def checkout(self, rate_limited: bool = True) -> CookieSlot | None:
        """Next healthy slot; acquires its semaphore (+ rate window when limited).
        Returns None only when every cookie is invalid or the wait timed out."""
        n = len(self.slots)
        deadline = time.monotonic() + 20
        while True:
            for _ in range(n):
                slot = self.slots[self._rr % n]
                self._rr += 1
                if slot.invalid or slot.disabled_until > time.monotonic():
                    continue
                await slot.sem.acquire()
                paused = slot.disabled_until - time.monotonic()
                if paused > 0:
                    await asyncio.sleep(paused)
                if rate_limited:
                    await slot.window.acquire()
                slot.requests += 1
                return slot
            if all(s.invalid for s in self.slots):
                return None
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.25)

    async def cdn_get(self, url: str, timeout: int = 60):
        """Signed CDN location fetch: anonymous, never rate limited.
        Returns (status, bytes, error)."""
        if self.anon is None:
            self.anon = aiohttp.ClientSession(headers={"User-Agent": UA, "Accept": "*/*"})
        async with self.cdn_sem:
            try:
                async with self.anon.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                                         allow_redirects=True) as r:
                    return r.status, await r.read(), None
            except asyncio.TimeoutError:
                return None, None, "timed out"
            except Exception as e:
                return None, None, str(e)[:160]

    def stats(self) -> str:
        return ", ".join(f"{s.label}={s.requests}req" for s in self.slots)


async def _send(slot: CookieSlot, url: str, headers: dict, method: str = "GET",
                json_body=None, timeout: int = 45, max_bytes: int = None):
    """One request on an already-checked-out slot.
    Returns (status, body, headers, error). Caller releases the slot."""
    try:
        kwargs = {"headers": headers,
                  "timeout": aiohttp.ClientTimeout(total=timeout),
                  "allow_redirects": True}
        if json_body is not None:
            kwargs["json"] = json_body
        async with slot.session.request(method, url, **kwargs) as r:
            body = await r.content.read(max_bytes) if max_bytes else await r.read()
            return r.status, body, dict(r.headers), None
    except asyncio.TimeoutError:
        return None, None, {}, "timed out"
    except Exception as e:
        return None, None, {}, str(e)[:160]


def _retry_after(headers: dict, default: float = 5.0) -> float:
    try:
        v = headers.get("Retry-After")
        if v:
            return min(float(v), 120.0)
    except (TypeError, ValueError):
        pass
    return default


def _cache_write(cache: str, data: bytes):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache, "wb") as f:
            f.write(data)
    except Exception:
        pass


def _sniff_ext(data: bytes, kind: str):
    if not data or len(data) < 8:
        return None
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"):
        return ".mp3"
    if data[:4] == b"fLaC":
        return ".flac"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:8] == b"<roblox!":          # binary model
        return ".rbxm"
    if data[:5] == b"<?xml" or data[:7] == b"<roblox":
        return ".rbxmx"                   # xml model
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] == b"version" or data[:7] == b"Version":
        return ".mesh"
    return None


def _kind_ok(ext: str, kind: str) -> bool:
    if kind == "sound":
        return ext in AUDIO_EXTS
    if kind == "scan":
        # place-file harvested ids: either folder is acceptable until typed
        return ext in AUDIO_EXTS or ext in MODEL_EXTS
    return ext in MODEL_EXTS


def _source_urls(asset_id: str, has_cookie: bool):
    """Ordered fetch sources. assetdelivery serves the real bytes; roproxy is a
    mirror of it. www.roblox.com/asset/ only ever returns an HTML 404 page, so
    it is left out — it used to mask real 403/409 refusals as '404'."""
    assetdelivery = f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}"
    roproxy = f"https://assetdelivery.roproxy.com/v1/asset/?id={asset_id}"
    order = [assetdelivery, roproxy] if has_cookie else [roproxy, assetdelivery]
    return list(dict.fromkeys(order))


def _cache_path(asset_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{asset_id}.bin")


_NAME_CACHE: dict = {}      # asset id -> catalog name (None = known missing)
_NAME_UNSET = object()


def _og_title(body: bytes):
    """Proper item name from a catalog page (every JSON name API is dead)."""
    m = re.search(rb'property="og:title" content="([^"]+)"', body)
    if not m:
        m = re.search(rb"<title[^>]*>(.*?)</title>", body[:120000], re.S | re.I)
    if not m:
        return None
    text = html.unescape(m.group(1).decode("utf-8", "replace")).strip()
    text = text.replace(" - Roblox", "").strip()
    return text[:120] or None


async def _resolve_names(target, ids, has_cookie=False):
    """Best effort proper names; never raises. Falls back to instance names.

    Every Roblox JSON name endpoint is dead (404), so read the catalog page's
    og:title instead — works for public items, private/unsellable ids simply
    keep their instance name. Lookups run through the cookie pool."""
    names = {}
    if not ids:
        return names
    pool = target if isinstance(target, CookiePool) else CookiePool.from_session(target)

    missing = []
    for aid in dict.fromkeys(str(i) for i in ids):
        cached = _NAME_CACHE.get(aid, _NAME_UNSET)
        if cached is _NAME_UNSET:
            missing.append(aid)
        elif cached:
            names[aid] = cached
    if not missing:
        print(f"[ASSETS] Resolved {len(names)}/{len(ids)} catalog name(s) (cached).")
        return names

    gate = asyncio.Semaphore(NAME_CONCURRENCY)

    async def one(aid):
        async with gate:
            slot = await pool.checkout()
            if slot is None:
                return
            try:
                status, body, hdrs, _err = await _send(
                    slot, f"https://www.roblox.com/catalog/{aid}",
                    {"Accept": "text/html,*/*"}, timeout=20, max_bytes=200_000)
            finally:
                slot.sem.release()
            if status == 401:
                slot.mark_invalid("HTTP 401 (expired)")
                return
            if status == 429:
                slot.pause(_retry_after(hdrs))
                return
            if status != 200 or not body:
                _NAME_CACHE[aid] = None      # 404/private: no catalog page
                return
            name = _og_title(body)
            _NAME_CACHE[aid] = name
            if name:
                names[aid] = name

    await asyncio.gather(*(one(a) for a in missing))
    print(f"[ASSETS] Resolved {len(names)}/{len(ids)} catalog name(s).")
    return names


async def _fetch_asset(target, asset_id: str, kind: str, place_id: str = None):
    """Return (bytes, ext, from_cache) or (None, reason, False).

    `target` is a CookiePool (failover across cookies) or a plain session.
    For each source the trusted-place header is tried first (an asset that is
    "not trusted for this place" 403s without it), then the plain request —
    every healthy cookie gets a turn before moving on."""
    cache = _cache_path(asset_id)
    if os.path.isfile(cache):
        try:
            with open(cache, "rb") as f:
                data = f.read()
            ext = _sniff_ext(data, kind)
            if ext and _kind_ok(ext, kind):
                return data, ext, True
        except Exception:
            pass

    pool = target if isinstance(target, CookiePool) else CookiePool.from_session(target, place_id)
    if place_id and not pool.place_id:
        pool.place_id = str(place_id)

    last_error = "no source returned usable content"
    place_hdr = {"Roblox-Place-Id": pool.place_id} if pool.place_id else {}
    for url in _source_urls(asset_id, pool.has_cookie):
        for hdr_extra in ([place_hdr, {}] if place_hdr else [{}]):
            tried = set()
            while True:
                slot = await pool.checkout()
                if slot is None:
                    return None, last_error if last_error != "no source returned usable content" \
                        else "no usable cookie (all cookies invalid)", False
                if slot.index in tried:
                    slot.sem.release()      # every healthy cookie already tried here
                    break
                tried.add(slot.index)
                try:
                    status, body, hdrs, err = await _send(
                        slot, url, {"Accept": "*/*", **hdr_extra})
                finally:
                    slot.sem.release()
                if status is None:
                    last_error = err or "request failed"
                    continue                # network error -> next cookie
                if status == 401:
                    slot.mark_invalid("HTTP 401 (expired)")
                    last_error = "HTTP 401 (cookie expired)"
                    continue
                if status == 429:
                    slot.pause(_retry_after(hdrs))
                    last_error = "HTTP 429 (rate limited)"
                    continue                # this cookie cools down, next one takes over
                if status in (403, 409):
                    # per-account / per-place refusal: another cookie may have access
                    last_error = f"HTTP {status} (not authorized for this account/place)"
                    continue
                if status == 404:
                    last_error = "HTTP 404 (deleted)"
                    break                   # account independent -> next source
                if status != 200:
                    last_error = f"HTTP {status}"
                    break
                data = body or b""
                ext = _sniff_ext(data, kind)
                if not data:
                    last_error = "empty response"
                    break
                if not ext:
                    last_error = "unrecognised content"
                    break
                if not _kind_ok(ext, kind):
                    last_error = f"unexpected content type ({ext})"
                    break
                _cache_write(cache, data)
                return data, ext, False
    return None, last_error, False


async def _batch_once(pool: CookiePool, body: list, hdrs: dict):
    """POST /v2/assets/batch, failing over cookies.

    A 200 response can still carry per-row authorization errors (access is
    per-account: one cookie's refusal doesn't mean the asset is gone), so
    rows without a location are re-sent on the next cookie until every id
    has a location or every healthy cookie has been tried. Returns the
    merged response rows, or None when nothing came back at all."""
    tried = set()
    merged: dict = {}        # requestId -> row (locations are authoritative)
    pending = list(body)
    while pending:
        slot = await pool.checkout()
        if slot is None:
            break
        if slot.index in tried:
            slot.sem.release()      # every healthy cookie already answered
            break
        tried.add(slot.index)
        try:
            status, resp, rhdrs, _err = await _send(
                slot, BATCH_URL, hdrs, method="POST", json_body=pending, timeout=60)
        finally:
            slot.sem.release()
        if status == 401:
            slot.mark_invalid("HTTP 401 (expired)")
            continue
        if status == 429:
            slot.pause(_retry_after(rhdrs))
            continue
        if status != 200 or not resp:
            continue                # other status -> next cookie
        try:
            rows = json.loads(resp.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            rid = str(row.get("requestId") or "")
            if not rid:
                continue
            if row.get("locations"):
                merged[rid] = row   # locations are authoritative
            elif rid not in merged:
                merged[rid] = row   # keep first error row for reporting
        pending = [r for r in body
                   if not (merged.get(str(r.get("requestId") or "")) or {}
                           ).get("locations")]
    return list(merged.values()) or None


async def _pull_cached(pool: CookiePool, aid: str, kind: str, url: str):
    """Download a signed CDN location (cookie-free, not rate limited).
    Serves from the asset cache when an earlier phase already fetched it."""
    cache = _cache_path(aid)
    if os.path.isfile(cache):
        try:
            with open(cache, "rb") as f:
                data = f.read()
            ext = _sniff_ext(data, kind)
            if ext and _kind_ok(ext, kind):
                return aid, (data, ext)
        except Exception:
            pass
    status, data, err = await pool.cdn_get(url)
    if status != 200 or not data:
        return aid, (None, err or f"HTTP {status} from CDN")
    ext = _sniff_ext(data, kind)
    if not ext or not _kind_ok(ext, kind):
        return aid, (None, f"unexpected content type ({ext})" if ext else "unrecognised content")
    _cache_write(cache, data)
    return aid, (data, ext)


async def _batch_fetch(pool: CookiePool, items, types_out: dict = None,
                       type_filter: set = None):
    """kartFr-style bulk download: POST /v2/assets/batch (<=50 ids per request)
    with the trusted-place header, then a second pass without it for assets
    that aren't trusted for this place. Locations are signed CDN URLs that
    download anonymously, so bulk traffic barely touches the cookie budget.

    items: [(asset_id, kind), ...] -> {id: (bytes, ext) | (None, reason)}.
    ``types_out`` receives Roblox's assetTypeId per id; when ``type_filter``
    is set, ids whose type is known and not in the filter are never even
    downloaded (used for place-file harvested ids of unknown purpose)."""
    out = {}
    if not items:
        return out
    place = pool.place_id if str(pool.place_id or "").isdigit() else None
    universe = pool.universe_id if str(pool.universe_id or "").isdigit() else None
    remaining = list(dict.fromkeys((str(a), k) for a, k in items if str(a).isdigit()))

    for pass_hdr in ([True, False] if place else [False]):
        if not remaining:
            break
        still = []
        for i in range(0, len(remaining), BATCH_SIZE):
            chunk = remaining[i:i + BATCH_SIZE]
            body = []
            for aid, _kind in chunk:
                row = {"assetId": int(aid), "requestId": aid,
                       "clientInsert": False, "scriptInsert": False}
                if place and pass_hdr:
                    row["placeId"] = int(place)
                if universe:
                    row["universeId"] = int(universe)
                body.append(row)
            hdrs = {"User-Agent": STUDIO_UA, "Content-Type": "application/json"}
            if pass_hdr:
                hdrs["Roblox-Place-Id"] = place
            data = await _batch_once(pool, body, hdrs)
            if data is None:
                still.extend(chunk)         # transport failure -> retry next pass
                continue
            locs = {}
            for row in data:
                rid = str(row.get("requestId") or "")
                ls = row.get("locations") or []
                if types_out is not None:
                    types_out[rid] = row.get("assetTypeId") or 0
                if ls and isinstance(ls[0], dict) and ls[0].get("location"):
                    locs[rid] = ls[0]["location"]
            fetch_jobs = []
            for aid, kind in chunk:
                if aid in locs:
                    if type_filter is not None:
                        t = types_out.get(aid, 0) if types_out else 0
                        if t and t not in type_filter:
                            out[aid] = (None, f"asset type {t} skipped")
                            continue        # known wrong type: don't even fetch
                    fetch_jobs.append((aid, kind, locs[aid]))
                else:
                    still.append((aid, kind))
            if fetch_jobs:
                got = await asyncio.gather(
                    *[_pull_cached(pool, aid, kind, url) for aid, kind, url in fetch_jobs])
                out.update(dict(got))
        remaining = still

    for aid, kind in remaining:
        out.setdefault(aid, (None, "not available via batch"))
    ok = sum(1 for v in out.values() if v[0])
    print(f"[ASSETS] Batch: {len(out)} id(s) -> {ok} via /v2/assets/batch.")
    return out


async def _integrate_scanned(pool: CookiePool, manifest: dict) -> int:
    """Type the ids harvested from the place file with one batch call and turn
    the audio/animation ones into real pack entries — assets the injected
    client scan never saw (only referenced in scripts/properties).
    Returns how many entries were added; never raises."""
    scanned = [str(i) for i in (manifest.get("scanned_ids") or [])]
    scanned = [i for i in dict.fromkeys(scanned) if i.isdigit() and int(i) > 0]
    known = {str(e.get("id"))
             for e in (manifest.get("animations") or []) + (manifest.get("sounds") or [])}
    scanned = [i for i in scanned if i not in known]
    if not scanned:
        return 0

    types = {}
    try:
        # 3 = audio, 24/61 = animation (legacy vs current type ids)
        res = await _batch_fetch(pool, [(i, "scan") for i in scanned],
                                 types_out=types, type_filter={3, 24, 61})
    except Exception as e:
        print(f"[ASSETS] Scanned-id batch failed: {e}")
        return 0

    added = 0
    for aid in scanned:
        r = res.get(aid)
        if not r or not r[0]:
            continue                        # inaccessible, deleted or wrong type
        data, ext = r
        t = types.get(aid, 0)
        if ext in AUDIO_EXTS:
            kind, key = "sound", "sounds"
        elif t in (24, 61) and ext in MODEL_EXTS:
            kind, key = "animation", "animations"
        else:
            continue
        manifest.setdefault(key, []).append({
            "id": aid, "kind": kind, "name": f"Asset{aid}",
            "paths": ["(referenced in place file)"], "catalog": None,
            "file": None, "size": 0, "status": "queued", "error": None,
        })
        added += 1
    if added:
        for key in ("animations", "sounds"):
            _unique_file_names(manifest.get(key) or [])
        print(f"[ASSETS] Place-file scan added {added} asset(s) the client scan missed.")
    return added


async def _download_worker(pack_id: str, cookie: str = None):
    manifest = load_manifest(pack_id)
    if not manifest:
        return
    path = pack_dir(pack_id)
    place_id = str(manifest.get("place_id") or "")
    universe_id = str(manifest.get("universe_id") or "")

    sem = asyncio.Semaphore(4)
    flush_lock = asyncio.Lock()
    dirty = [0]

    async def flush(force=False):
        async with flush_lock:
            dirty[0] += 1
            if force or dirty[0] >= 5:
                dirty[0] = 0
                await asyncio.to_thread(save_manifest, pack_id, manifest)

    try:
        async with CookiePool(cookie, place_id=place_id,
                              universe_id=universe_id) as pool:
            # place-file harvested ids first: they may add entries to the
            # manifest, so pending has to be computed after integration
            # (this also makes scanned-only packs downloadable).
            try:
                await _integrate_scanned(pool, manifest)
                await flush(force=True)
            except Exception as e:
                print(f"[ASSETS] Scanned-id integration error: {e}")

            entries = [(e, "Animations") for e in manifest.get("animations", [])]
            entries += [(e, "Sounds") for e in manifest.get("sounds", [])]
            pending = [(e, folder) for e, folder in entries
                       if e.get("status") in ("queued", "downloading")]
            if not pending:
                manifest["status"] = "done"
                save_manifest(pack_id, manifest)
                return

            todo = [e for e, _ in pending]
            for e in todo:
                e["status"] = "downloading"
            manifest["status"] = "downloading"
            save_manifest(pack_id, manifest)

            ids = [e["id"] for e in todo]
            try:
                names = await _resolve_names(pool, ids, pool.has_cookie)
            except Exception as e:
                print(f"[ASSETS] Name resolution error: {e}")
                names = {}
            for e in todo:
                nm = names.get(e["id"])
                if nm:
                    e["catalog"] = nm
            # File names were derived before catalog names were known, so every
            # pending entry would have kept its raw instance name. Rebuild them
            # now that the catalog name (the "proper name") is available.
            for key in ("animations", "sounds"):
                _unique_file_names(manifest.get(key) or [])
            await flush(force=True)

            # bulk pass: 50 ids per request, CDN locations are cookie-free
            try:
                batched = await _batch_fetch(pool, [(e["id"], e["kind"]) for e in todo])
            except Exception as e:
                print(f"[ASSETS] Batch fetch error: {e}")
                batched = {}

            async def work(entry, folder):
                async with sem:
                    try:
                        data, ext, from_cache = None, None, False
                        res = batched.get(entry["id"])
                        if res and res[0]:
                            data, ext = res
                        elif res and res[1] and str(res[1]).startswith(
                                ("unexpected content type", "unrecognised")):
                            # the CDN already served the real bytes and they do
                            # not fit this folder — retrying through the pool
                            # would only burn requests on identical content
                            data, ext = None, res[1]
                        else:
                            # per-asset failover: trusted-place header first,
                            # then every cookie, then the open mirrors
                            data, ext, from_cache = await _fetch_asset(
                                pool, entry["id"], entry["kind"], place_id=place_id)
                        if not data:
                            entry["status"] = "failed"
                            entry["error"] = str(ext)
                        else:
                            base = entry.get("base") or _safe_name(
                                display_name(entry), f"asset_{entry['id']}")
                            filename = f"{base}{ext}"
                            dest_dir = os.path.join(path, folder)
                            os.makedirs(dest_dir, exist_ok=True)
                            dest = os.path.join(dest_dir, filename)
                            await asyncio.to_thread(_write_bytes, dest, data)
                            entry["file"] = filename
                            entry["size"] = len(data)
                            entry["status"] = "ready"
                            entry["error"] = None
                            if from_cache:
                                entry["cached"] = True
                    except Exception as e:
                        entry["status"] = "failed"
                        entry["error"] = str(e)[:160]
                    await flush()

            await asyncio.gather(*(work(e, folder) for e, folder in pending))
            print(f"[ASSETS] Pool usage for {pack_id}: {pool.stats()}")
    except Exception as e:
        print(f"[ASSETS] Download worker failed for {pack_id}: {e}")
        manifest["status"] = "failed"
        save_manifest(pack_id, manifest)
        return

    state = pack_state(manifest)
    manifest["status"] = "done" if state["failed"] == 0 else "partial"
    manifest["zip_state"] = ""
    save_manifest(pack_id, manifest)
    print(f"[ASSETS] Pack {pack_id} finished: {state['ready']}/{state['total']} ready, "
          f"{state['failed']} failed.")


def _write_bytes(dest: str, data: bytes):
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def start_download(pack_id: str, cookie: str = None):
    task = _pack_tasks.get(pack_id)
    if task and not task.done():
        return task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    t = loop.create_task(_download_worker(pack_id, cookie))
    _pack_tasks[pack_id] = t
    return t


def register_persistent_views(bot):
    """Re-arm the Discord buttons for packs that already exist (after restart)."""
    count = 0
    for manifest in list_packs():
        try:
            bot.add_view(AssetsView(manifest["pack_id"]))
            count += 1
        except Exception as e:
            print(f"[ASSETS] Could not register view for {manifest.get('pack_id')}: {e}")
    if count:
        print(f"[ASSETS] Registered {count} asset button view(s).")
    resume_incomplete_downloads()
    return count


def _cookie_for_download() -> str:
    try:
        return core.get_active_cookie() or core.ROBLOX_COOKIE or ""
    except Exception:
        return ""


def resume_incomplete_downloads():
    """Pick up packs a restart interrupted mid-download."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0
    resumed = 0
    for manifest in list_packs():
        if manifest.get("status") not in ("downloading", "queued"):
            continue
        pack_id = manifest.get("pack_id")
        task = _pack_tasks.get(pack_id)
        if task and not task.done():
            continue
        print(f"[ASSETS] Resuming interrupted download: {pack_id}")
        _pack_tasks[pack_id] = loop.create_task(_download_worker(pack_id, _cookie_for_download()))
        resumed += 1
    return resumed


def cleanup_asset_packs():
    """Drop the oldest packs once the asset budget is exceeded."""
    try:
        total = 0
        packs = []
        for name in os.listdir(ASSETS_DIR):
            p = os.path.join(ASSETS_DIR, name)
            if not os.path.isdir(p) or not _is_pack_id(name):
                continue
            size = _dir_size(p)
            mtime = os.path.getmtime(p)
            packs.append((mtime, size, p))
            total += size
        total += _dir_size(CACHE_DIR)
        packs.sort()
        while total > ASSETS_MAX_BYTES and packs:
            _, size, victim = packs.pop(0)
            task = _pack_tasks.get(os.path.basename(victim))
            if task and not task.done():
                continue
            shutil.rmtree(victim, ignore_errors=True)
            total -= size
            print(f"[ASSETS] Evicted pack {os.path.basename(victim)} ({size} bytes)")
    except Exception as e:
        print(f"[ASSETS] Cleanup error: {e}")


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


# --------------------------------------------------------------------------
# discord
# --------------------------------------------------------------------------
def assets_url(pack_id: str) -> str:
    return f"{SITE_URL}assets/{pack_id}"


def zip_url(pack_id: str) -> str:
    return f"{SITE_URL}assets/{pack_id}/zip"


async def _respond_to_click(pack_id: str, interaction: discord.Interaction):
    """Shared callback for the 'View Assets Downloaded' button."""
    manifest = load_manifest(pack_id)
    if not manifest:
        await interaction.response.send_message(
            "These assets are no longer available (the pack was cleaned up).",
            ephemeral=True,
        )
        return
    uid = str(interaction.user.id)
    if uid != str(manifest.get("author_id")) and interaction.user.id != core.BOT_OWNER_ID:
        await interaction.response.send_message(
            "Only the person who requested this decompile can view its assets.",
            ephemeral=True,
        )
        return

    state = pack_state(manifest)
    if state["total"] == 0:
        await interaction.response.send_message("No assets were found in this place.", ephemeral=True)
        return

    page = assets_url(pack_id)
    status_value = f"Downloading… {state['ready']}/{state['total']}"
    if state["ready"] >= state["total"]:
        status_value = f"Ready — {state['ready']}/{state['total']}"
        if state["failed"]:
            status_value += f", {state['failed']} unavailable"

    embed = discord.Embed(
        title=f"Assets — {manifest.get('game_name', 'this place')}",
        color=0x00DC78 if state["failed"] == 0 else 0xF1C40F,
        url=page,
    )
    embed.add_field(name="Animations", value=str(len(manifest.get("animations") or [])), inline=True)
    embed.add_field(name="Sounds", value=str(len(manifest.get("sounds") or [])), inline=True)
    embed.add_field(name="Status", value=status_value, inline=True)
    embed.set_footer(text="Login with Discord to open • requester and bot owner only")

    view = discord.ui.View(timeout=180)
    view.add_item(discord.ui.Button(label="Open Asset Folders", url=page,
                                    style=discord.ButtonStyle.link, emoji="🗂️"))
    if state["ready"] > 0:
        view.add_item(discord.ui.Button(label="Download .zip", url=zip_url(pack_id),
                                        style=discord.ButtonStyle.link, emoji="⬇️"))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def _assets_button(pack_id: str) -> discord.ui.Button:
    btn = discord.ui.Button(
        label="View Assets Downloaded",
        emoji="📦",
        style=discord.ButtonStyle.primary,
        custom_id=f"assets_pack:{pack_id}",
    )

    async def _cb(interaction: discord.Interaction):
        await _respond_to_click(pack_id, interaction)

    btn.callback = _cb
    return btn


class AssetsView(discord.ui.View):
    """Persistent "View Assets Downloaded" button, requester/owner only."""

    def __init__(self, pack_id: str):
        super().__init__(timeout=None)
        self.pack_id = pack_id
        self.add_item(_assets_button(pack_id))


def add_assets_button(view: discord.ui.View, pack_id: str):
    """Attach the assets button to the final decompile embed's view."""
    view.add_item(_assets_button(pack_id))
    manifest = load_manifest(pack_id)
    return int((manifest or {}).get("author_id", 0) or 0)


# --------------------------------------------------------------------------
# web: shared shell
# --------------------------------------------------------------------------
PAGE_CSS = """
:root{--bg:#050507;--bg-elev:rgba(18,21,28,.72);--bg-elev-2:rgba(24,28,36,.6);
--line:rgba(255,255,255,.07);--txt:#e6edf3;--txt-dim:#8b949e;--txt-faint:#5a626d;
--accent:#00dc78;--accent-soft:rgba(0,220,120,.12);--accent-line:rgba(0,220,120,.28);
--blue:#58a6ff;--amber:#f1c40f;--radius:14px;--radius-sm:9px}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--txt);font-family:'Inter','SF Pro Display',system-ui,-apple-system,sans-serif;
min-height:100vh;padding-bottom:70px;-webkit-font-smoothing:antialiased;letter-spacing:-.01em}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.5;
background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);
background-size:52px 52px;mask-image:radial-gradient(ellipse 90% 60% at 50% 0%,#000 40%,transparent 100%);
-webkit-mask-image:radial-gradient(ellipse 90% 60% at 50% 0%,#000 40%,transparent 100%)}
.bg-glow{position:fixed;width:720px;height:720px;border-radius:50%;z-index:0;pointer-events:none;
background:radial-gradient(circle,rgba(0,220,120,.055),transparent 68%);top:-260px;left:50%;transform:translateX(-50%)}
.header{position:sticky;top:0;z-index:100;display:flex;align-items:center;justify-content:space-between;gap:20px;
padding:14px 32px;background:rgba(8,9,13,.72);border-bottom:1px solid var(--line);
backdrop-filter:blur(18px) saturate(160%);-webkit-backdrop-filter:blur(18px) saturate(160%)}
.header-left{display:flex;align-items:center;gap:22px;min-width:0}
.header-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.logo{font-size:21px;font-weight:700;letter-spacing:-.6px;display:flex;align-items:center;gap:10px;white-space:nowrap;
text-decoration:none;color:var(--txt)}
.roblox-icon{width:28px;height:28px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.4)}
.lua{color:var(--blue)}.is{color:var(--txt-faint);font-weight:500}.game{color:var(--accent)}
.btn{padding:9px 17px;border-radius:var(--radius-sm);border:1px solid transparent;font-size:13px;font-weight:600;
cursor:pointer;transition:transform .16s,filter .16s,background .18s,border-color .18s;text-decoration:none;
display:inline-flex;align-items:center;gap:7px;line-height:1;white-space:nowrap}
.btn:hover{transform:translateY(-1px)}
.btn-primary{background:var(--accent);color:#04140b;box-shadow:0 2px 14px rgba(0,220,120,.22)}
.btn-secondary{background:rgba(255,255,255,.04);color:var(--txt);border-color:var(--line)}
.btn-secondary:hover{background:rgba(255,255,255,.075);border-color:rgba(255,255,255,.14)}
.btn-discord{background:rgba(88,101,242,.16);color:#c3cbff;border-color:rgba(88,101,242,.32)}
.btn-discord:hover{background:rgba(88,101,242,.26);border-color:rgba(88,101,242,.5)}
.container{max-width:1160px;margin:0 auto;padding:30px 24px;position:relative;z-index:1}
.footer{text-align:center;padding:22px;color:var(--txt-faint);font-size:12.5px;border-top:1px solid rgba(255,255,255,.045);
position:fixed;bottom:0;left:0;right:0;background:rgba(5,5,7,.82);backdrop-filter:blur(16px);z-index:50}
.footer a{color:var(--blue);text-decoration:none}
.crumb{color:var(--txt-dim);font-size:12.5px;margin-bottom:14px}
.crumb a{color:var(--txt-dim);text-decoration:none}
.crumb a:hover{color:var(--accent)}
.crumb span{color:var(--txt-faint);margin:0 7px}
.card{background:var(--bg-elev);border:1px solid var(--line);border-radius:var(--radius);backdrop-filter:blur(10px);
margin-bottom:16px;overflow:hidden}
.pack-head{display:flex;gap:18px;padding:20px 22px;align-items:center}
.pack-icon{width:76px;height:76px;border-radius:12px;object-fit:cover;background:#0d1117;flex-shrink:0;border:1px solid var(--line)}
.pack-title{font-size:20px;font-weight:650;color:#f0f6fc;margin-bottom:8px;word-break:break-word}
.meta{font-size:12.5px;color:var(--txt-dim);line-height:1.9}
.meta .label{color:var(--accent);font-weight:500}
.meta .value{color:var(--txt);margin-right:16px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:11.5px;
font-weight:600;letter-spacing:.2px;border:1px solid var(--accent-line);background:var(--accent-soft);color:var(--accent)}
.chip.busy{border-color:rgba(241,196,15,.35);background:rgba(241,196,15,.1);color:var(--amber)}
.chip.err{border-color:rgba(218,54,51,.35);background:rgba(218,54,51,.12);color:#ff8b88}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:0 22px 18px}
.progress{flex:1;min-width:200px;height:8px;border-radius:999px;background:rgba(255,255,255,.06);overflow:hidden}
.progress>i{display:block;height:100%;background:linear-gradient(90deg,#00dc78,#58a6ff);transition:width .4s}
.progress-text{font-size:12px;color:var(--txt-dim);font-variant-numeric:tabular-nums}
.folder{background:var(--bg-elev);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;
backdrop-filter:blur(10px);margin-bottom:16px}
.folder-head{display:flex;align-items:center;gap:12px;padding:14px 20px;background:rgba(255,255,255,.025);
border-bottom:1px solid var(--line);cursor:pointer;user-select:none}
.folder-head:hover{background:rgba(255,255,255,.045)}
.folder-ico{font-size:17px}
.folder-name{font-size:14.5px;font-weight:650;color:#f0f6fc}
.folder-count{font-size:12px;color:var(--txt-faint);margin-left:auto}
.chev{color:var(--txt-faint);transition:transform .2s;font-size:12px}
.folder.collapsed .chev{transform:rotate(-90deg)}
.folder.collapsed .files{display:none}
.file-row{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid rgba(255,255,255,.045)}
.file-row:last-child{border-bottom:none}
.file-row:hover{background:rgba(255,255,255,.02)}
.file-main{min-width:0;flex:1}
.file-name{font-size:13.5px;font-weight:600;color:#e6edf3;word-break:break-word}
.file-name a{color:inherit;text-decoration:none}
.file-name a:hover{color:var(--accent)}
.file-sub{font-size:11.5px;color:var(--txt-faint);margin-top:3px;word-break:break-word}
.file-sub .id{color:var(--blue);font-family:ui-monospace,monospace}
.file-sub .path{color:var(--txt-dim)}
.badge{font-size:10.5px;font-weight:700;letter-spacing:.4px;padding:3px 8px;border-radius:6px;text-transform:uppercase;
border:1px solid var(--line);color:var(--txt-dim);background:rgba(255,255,255,.03);flex-shrink:0}
.badge.anim{color:#c3cbff;border-color:rgba(88,101,242,.3);background:rgba(88,101,242,.1)}
.badge.snd{color:#7ee2a8;border-color:rgba(0,220,120,.28);background:rgba(0,220,120,.1)}
.badge.pending{color:var(--amber);border-color:rgba(241,196,15,.3);background:rgba(241,196,15,.08)}
.badge.failed{color:#ff8b88;border-color:rgba(218,54,51,.3);background:rgba(218,54,51,.1)}
.size{font-size:11.5px;color:var(--txt-faint);font-variant-numeric:tabular-nums;flex-shrink:0;min-width:62px;text-align:right}
.file-act{flex-shrink:0}
.dl{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#238636,#2ea043);color:#fff;
padding:7px 14px;border-radius:var(--radius-sm);text-decoration:none;font-size:12.5px;font-weight:600;
transition:transform .16s,box-shadow .2s;box-shadow:0 2px 10px rgba(35,134,54,.25)}
.dl:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(35,134,54,.4)}
.dl.off{background:rgba(255,255,255,.04);color:var(--txt-faint);box-shadow:none;border:1px solid var(--line);cursor:not-allowed}
audio{width:210px;height:32px;flex-shrink:0;opacity:.9}
.empty{text-align:center;padding:56px 20px;color:var(--txt-dim)}
.empty h3{color:#f0f6fc;font-size:17px;margin-bottom:8px}
.empty p{font-size:13.5px}
.count{color:var(--txt-dim);font-size:13px;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.count:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 9px var(--accent)}
.pack-card{display:flex;gap:16px;padding:16px 20px;align-items:center;background:var(--bg-elev);border:1px solid var(--line);
border-radius:var(--radius);margin-bottom:12px;transition:transform .2s,border-color .2s,box-shadow .2s;text-decoration:none;color:inherit}
.pack-card:hover{border-color:var(--accent-line);transform:translateY(-2px);box-shadow:0 12px 34px rgba(0,0,0,.45)}
.pack-card img{width:64px;height:64px;border-radius:10px;object-fit:cover;background:#0d1117;flex-shrink:0}
.pack-card .t{font-size:15.5px;font-weight:650;color:#f0f6fc;margin-bottom:6px}
.pack-card .s{font-size:12px;color:var(--txt-dim);line-height:1.7}
.pack-card .go{margin-left:auto;color:var(--accent);font-size:20px;flex-shrink:0}
/* login required */
.login-wrap{min-height:calc(100vh - 60px);display:flex;align-items:center;justify-content:center;padding:40px 20px;position:relative;z-index:1}
.login-card{width:100%;max-width:430px;text-align:center;background:var(--bg-elev);border:1px solid var(--line);
border-radius:20px;padding:38px 32px 32px;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
box-shadow:0 30px 70px rgba(0,0,0,.55)}
.login-art{width:172px;height:172px;object-fit:contain;margin:-96px auto 14px;display:block;
filter:drop-shadow(0 18px 34px rgba(0,0,0,.6))}
.login-card h1{font-size:27px;font-weight:700;color:#f0f6fc;letter-spacing:-.6px;margin-bottom:10px}
.login-card p{font-size:13.5px;color:var(--txt-dim);line-height:1.65;margin-bottom:22px}
.login-card .btn{width:100%;justify-content:center;padding:12px 18px;font-size:14px}
.login-card .sub{margin-top:14px;font-size:12.5px}
.login-card .sub a{color:var(--blue);text-decoration:none}
.login-card .sub a:hover{text-decoration:underline}
.lock-ring{width:88px;height:88px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 18px;
background:radial-gradient(circle,rgba(0,220,120,.16),rgba(0,220,120,.03));border:1px solid var(--accent-line)}
.forbidden{border-color:rgba(218,54,51,.35)!important}
.forbidden h1{color:#ff8b88}
@media(max-width:768px){
.header{padding:12px 16px;flex-wrap:wrap;gap:12px}
.header-left,.header-right{width:100%;justify-content:center;flex-wrap:wrap;gap:10px}
.container{padding:20px 14px}
.pack-head{flex-direction:column;align-items:flex-start;gap:12px}
.file-row{flex-wrap:wrap;gap:10px}
audio{width:100%}
.size{text-align:left}
.login-art{width:132px;height:132px;margin-top:-84px}
}
"""

DISCORD_SVG = ("<svg width='18' height='14' viewBox='0 0 71 55' fill='none' xmlns='http://www.w3.org/2000/svg'>"
               "<path d='M60.1 4.9A58.5 58.5 0 0 0 45.4.2a.2.2 0 0 0-.2.1 40.8 40.8 0 0 0-1.8 3.7 54 54 0 0 0-16.2 0 26.5 "
               "26.5 0 0 0-1.8-3.7.2.2 0 0 0-.2-.1A58.4 58.4 0 0 0 10.9 4.9a.2.2 0 0 0-.1.1C1.6 18.4-.5 31.7.5 44.8a.2.2 "
               "0 0 0 .1.1 58.7 58.7 0 0 0 17.7 9 .2.2 0 0 0 .2-.1 42 42 0 0 0 3.6-5.9.2.2 0 0 0-.1-.3 38.7 38.7 0 0 "
               "1-5.5-2.6.2.2 0 0 1 0-.4c.4-.3.7-.6 1.1-.9a.2.2 0 0 1 .2 0c11.5 5.3 24 5.3 35.4 0a.2.2 0 0 1 .2 0l1.1.9a.2.2 "
               "0 0 1 0 .4c-1.8 1-3.6 1.9-5.6 2.6a.2.2 0 0 0-.1.3 47.2 47.2 0 0 0 3.7 5.9.2.2 0 0 0 .2.1 58.5 58.5 0 0 0 "
               "17.7-9 .2.2 0 0 0 .1-.1c1.2-15-2-28.3-8.5-39.8a.2.2 0 0 0-.1-.1ZM23.7 36.3c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 "
               "6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Zm23.6 0c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 "
               "7.1-6.4 7.1Z' fill='white'/></svg>")

# Placeholder art shown above "Login Required". A real image dropped at
# site/login_art.png takes precedence without any code change.
_FALLBACK_ART = (
    "<svg class='login-art' viewBox='0 0 200 200' fill='none' xmlns='http://www.w3.org/2000/svg' role='img' "
    "aria-label='Login required'>"
    "<defs><linearGradient id='g1' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='#12201a'/><stop offset='1' stop-color='#0a0f14'/></linearGradient>"
    "<linearGradient id='g2' x1='0' y1='0' x2='0' y2='1'>"
    "<stop offset='0' stop-color='#00dc78'/><stop offset='1' stop-color='#0a9e5a'/></linearGradient></defs>"
    "<rect x='6' y='6' width='188' height='188' rx='44' fill='url(#g1)' stroke='rgba(255,255,255,.09)' stroke-width='2'/>"
    "<rect x='24' y='24' width='152' height='152' rx='34' fill='none' stroke='rgba(0,220,120,.18)' stroke-width='2' "
    "stroke-dasharray='7 9'/>"
    "<path d='M74 92V76a26 26 0 0 1 52 0v16' stroke='url(#g2)' stroke-width='9' stroke-linecap='round'/>"
    "<rect x='62' y='90' width='76' height='58' rx='15' fill='url(#g2)'/>"
    "<rect x='62' y='90' width='76' height='58' rx='15' fill='rgba(255,255,255,.06)'/>"
    "<circle cx='100' cy='114' r='9' fill='#04140b'/>"
    "<rect x='96.5' y='114' width='7' height='17' rx='3.5' fill='#04140b'/>"
    "<circle cx='100' cy='114' r='3.4' fill='url(#g2)'/>"
    "</svg>"
)


def _login_art_html() -> str:
    art = os.path.join(core.BASE_DIR, "site", "login_art.png")
    if os.path.isfile(art):
        return "<img class='login-art' src='/assets/login-art' alt='Login required'>"
    return _FALLBACK_ART


def _shell(title: str, body: str, user_info: dict = None) -> str:
    user_btn = (
        f"<a class='btn btn-discord' href='#'>{DISCORD_SVG} {html.escape(user_info.get('username', ''))}</a>"
        if user_info else
        f"<a class='btn btn-discord' href='/api/auth/login'>{DISCORD_SVG} Login with Discord</a>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<link rel="icon" href="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png" type="image/png">
<meta name="theme-color" content="#0d1117">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-glow"></div>
<div class="header">
    <div class="header-left">
        <a class="logo" href="/"><img src="https://upload.wikimedia.org/wikipedia/commons/b/b2/Roblox_Icon_2022.png" class="roblox-icon" alt="Roblox"><span class="lua">Lua</span> <span class="is">is</span> <span class="game">game</span></a>
    </div>
    <div class="header-right">
        <a class="btn btn-secondary" href="/">All Games</a>
        <a class="btn btn-primary" href="/assets">Assets</a>
        {user_btn}
    </div>
</div>
{body}
<div class="footer">Created by: <strong>iispeaklua</strong> (Crimson) &bull; <a href="https://discord.gg/robloxdecompiler">Discord</a></div>
</body>
</html>"""


def _login_required_body(next_url: str) -> str:
    nxt = html.escape(next_url or "/assets")
    return f"""<div class="login-wrap">
  <div class="login-card">
    {_login_art_html()}
    <h1>Login Required</h1>
    <p>Asset folders are private. Sign in with the same Discord account you use
       for the bot to see the animations &amp; sounds downloaded for your decompiles.</p>
    <a class="btn btn-discord" href="/api/auth/login?next={nxt}">{DISCORD_SVG} Login with Discord</a>
    <div class="sub">Not your account? <a href="/">Back to home</a></div>
  </div>
</div>"""


def _forbidden_body() -> str:
    return f"""<div class="login-wrap">
  <div class="login-card forbidden">
    <div class="lock-ring">&#128274;</div>
    <h1>Not your assets</h1>
    <p>Only the person who requested this decompile (or the bot owner) can open
       these asset folders. Log in with that Discord account to continue.</p>
    <a class="btn btn-discord" href="/api/auth/login">{DISCORD_SVG} Login with Discord</a>
    <div class="sub"><a href="/">Back to home</a></div>
  </div>
</div>"""


def _missing_body() -> str:
    return f"""<div class="login-wrap">
  <div class="login-card forbidden">
    <div class="lock-ring">&#128683;</div>
    <h1>Assets not found</h1>
    <p>These asset folders do not exist any more — they may have been cleaned up
       to free storage space. Run the decompile again to rebuild them.</p>
    <div class="sub"><a href="/assets">Back to asset packs</a></div>
  </div>
</div>"""


def _page(title, body, user_info=None):
    return web.Response(text=_shell(title, body, user_info), content_type="text/html")


def _login_page(next_url):
    return web.Response(text=_shell("Login Required", _login_required_body(next_url)), content_type="text/html")


def _json_login():
    return web.json_response({"error": "login_required", "login": "/api/auth/login"}, status=401)


def _json_forbidden():
    return web.json_response({"error": "forbidden"}, status=403)


def _fmt_size(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


# --------------------------------------------------------------------------
# web: routes
# --------------------------------------------------------------------------
async def handle_assets_index(request):
    if core._request_is_mc_host(request):
        return web.Response(text="Assets live on storage.luaisgame.com", status=404)
    user = core._get_user_info(request)
    if not user:
        return _login_page("/assets")

    is_owner = int(user.get("id", 0)) == core.BOT_OWNER_ID
    packs = [m for m in list_packs()
             if is_owner or str(m.get("author_id")) == str(user.get("id"))]

    cards = ""
    for m in packs:
        state = pack_state(m)
        icon = html.escape(m.get("icon_url") or "", quote=True)
        counts = []
        if m.get("animations"):
            counts.append(f"{len(m['animations'])} animation(s)")
        if m.get("sounds"):
            counts.append(f"{len(m['sounds'])} sound(s)")
        status = ("Ready" if state["failed"] == 0 and state["ready"] == state["total"]
                  else f"{state['ready']}/{state['total']} ready")
        cards += f"""<a class="pack-card" href="/assets/{html.escape(m['pack_id'])}">
        <img src="{icon}" alt="" onerror="this.style.display='none'">
        <div>
            <div class="t">{html.escape(str(m.get('game_name', 'Unknown')))}</div>
            <div class="s">{html.escape(' &bull; '.join(counts) or 'No assets')}<br>
            Requested by {html.escape(str(m.get('display_name', 'Unknown')))} &bull; {html.escape(str(m.get('created', '')))}</div>
        </div>
        <div class="chip {'busy' if status != 'Ready' else ''}" style="margin-left:auto">{html.escape(status)}</div>
        <div class="go">&#8250;</div>
    </a>"""

    if not cards:
        cards = """<div class="card"><div class="empty"><h3>No asset packs yet</h3>
        <p>Run a decompile from Discord — every place's animations and sounds are
        saved into a folder you can browse and download here.</p></div></div>"""

    body = f"""<div class="container">
      <div class="crumb"><a href="/">Home</a><span>/</span>Asset Packs</div>
      <div class="count">{len(packs)} asset pack(s) &mdash; animations &amp; sounds from your decompiles</div>
      {cards}
    </div>"""
    return _page("Asset Packs", body, user)


async def handle_asset_pack(request):
    pack_id = request.match_info.get("pack_id", "")
    user = core._get_user_info(request)
    if not user:
        return _login_page(f"/assets/{pack_id}")
    manifest = load_manifest(pack_id)
    if not manifest:
        return web.Response(text=_shell("Assets", _missing_body(), user),
                            content_type="text/html", status=404)
    if not can_view(request, manifest):
        return web.Response(text=_shell("Not your assets", _forbidden_body(), user),
                            content_type="text/html", status=403)
    return _page(f"Assets — {manifest.get('game_name', '')}", _pack_body(manifest), user)


def _pack_body(manifest: dict) -> str:
    pack_id = manifest["pack_id"]
    state = pack_state(manifest)
    status = manifest.get("status", "queued")
    chip_cls = "" if status == "done" else ("err" if status == "failed" else "busy")
    chip_txt = {
        "done": "All files ready",
        "downloading": "Downloading from Roblox…",
        "queued": "Queued…",
        "partial": "Finished (some files unavailable)",
        "failed": "Download failed",
    }.get(status, status.title())
    if state["failed"] and state["ready"] == 0:
        # every single asset came back empty -> the .ROBLOSECURITY cookie was
        # rejected, so tell the user instead of showing an empty folder.
        chip_cls, chip_txt = "err", "Download failed"
    pct = int(100 * state["ready"] / state["total"]) if state["total"] else 0
    icon = html.escape(manifest.get("icon_url") or "", quote=True)
    if state["ready"] > 0:
        zip_btn = (f'<a class="dl" href="/assets/{html.escape(pack_id)}/zip">'
                   f'&#11015; Download All ({state["ready"]} file(s))</a>')
    else:
        zip_btn = '<span class="dl off">Downloading&hellip;</span>'

    head = f"""<div class="card">
      <div class="pack-head">
        <img class="pack-icon" src="{icon}" alt="" onerror="this.style.display='none'">
        <div style="min-width:0">
          <div class="pack-title">{html.escape(str(manifest.get('game_name', 'Unknown')))}</div>
          <div class="meta">
            <span class="label">Place ID:</span><span class="value">{html.escape(str(manifest.get('place_id', '')))}</span>
            <span class="label">Version:</span><span class="value">{html.escape(str(manifest.get('game_version', 'N/A')))}</span>
            <span class="label">Requested by:</span><span class="value">{html.escape(str(manifest.get('display_name', 'Unknown')))}</span>
            <span class="label">Downloaded:</span><span class="value">{html.escape(str(manifest.get('created', '')))}</span>
          </div>
          <div style="margin-top:10px"><span class="chip {chip_cls}" id="statusChip">{html.escape(chip_txt)}</span></div>
        </div>
      </div>
      <div class="toolbar">
        {zip_btn}
        <div class="progress"><i id="bar" style="width:{pct}%"></i></div>
        <div class="progress-text" id="ptxt">{state['ready']} / {state['total']} ready</div>
      </div>
      {f'<div style="padding:0 22px 18px;color:#ff8b88;font-size:12.5px">Every asset was rejected by Roblox — the cookie used for this decompile has likely expired or been banned. Re-run the decompile with a fresh cookie.</div>' if (state['failed'] and state['ready'] == 0) else ''}
    </div>"""

    folders = ""
    for folder, kind in FOLDERS:
        entries = manifest.get("animations" if kind == "animation" else "sounds") or []
        rows = ""
        for e in entries:
            rows += _file_row(pack_id, folder, kind, e)
        if not entries:
            rows = ("<div class='file-row'><div class='file-main'><div class='file-sub'>"
                    "No " + folder.lower() + " found in this place.</div></div></div>")
        folders += f"""<div class="folder" data-folder="{kind}">
          <div class="folder-head" onclick="this.parentElement.classList.toggle('collapsed')">
            <span class="folder-ico">{'&#127916;' if kind == 'animation' else '&#128266;'}</span>
            <span class="folder-name">{folder}</span>
            <span class="folder-count">{len(entries)} item(s)</span>
            <span class="chev">&#9662;</span>
          </div>
          <div class="files">{rows}</div>
        </div>"""

    body = f"""<div class="container">
      <div class="crumb"><a href="/">Home</a><span>/</span><a href="/assets">Asset Packs</a><span>/</span>{html.escape(str(manifest.get('game_name', '')))}</div>
      {head}
      {folders}
    </div>
    <script>
    var PACK_ID = {json.dumps(pack_id)};
    var LAST = {json.dumps(state['ready'])};
    (function poll() {{
      fetch('/api/assets/' + encodeURIComponent(PACK_ID) + '/status').then(function(r) {{
        return r.json();
      }}).then(function(s) {{
        if (!s || !s.total) return;
        var pct = Math.round(100 * s.ready / s.total);
        var bar = document.getElementById('bar');
        var ptxt = document.getElementById('ptxt');
        var chip = document.getElementById('statusChip');
        if (bar) bar.style.width = pct + '%';
        if (ptxt) ptxt.textContent = s.ready + ' / ' + s.total + ' ready' + (s.failed ? ' (' + s.failed + ' unavailable)' : '');
        if (chip && s.status) {{
          chip.textContent = s.status === 'done' ? 'All files ready'
            : s.status === 'partial' ? 'Finished (some files unavailable)'
            : s.status === 'failed' ? 'Download failed' : 'Downloading from Roblox…';
          chip.className = 'chip ' + (s.status === 'done' ? '' : (s.status === 'failed' ? 'err' : 'busy'));
        }}
        if (s.ready !== LAST) {{ LAST = s.ready; location.reload(); return; }}
        if (s.status === 'downloading' || s.status === 'queued') setTimeout(poll, 2500);
      }}).catch(function() {{ setTimeout(poll, 5000); }});
    }})();
    </script>"""
    return body


def _file_row(pack_id: str, folder: str, kind: str, entry: dict) -> str:
    name = html.escape(display_name(entry))
    aid = html.escape(str(entry.get("id", "")))
    st = entry.get("status", "queued")
    paths = entry.get("paths") or []
    path_txt = html.escape(paths[0]) if paths else ""
    extra = ""
    if len(paths) > 1:
        extra = f" <span class='path'>&bull; used by {len(paths)} instances</span>"
    if entry.get("catalog") and entry.get("name"):
        extra = (f" <span class='path'>&bull; instance &ldquo;{html.escape(entry['name'])}&rdquo;</span>"
                 + extra)

    if st == "ready" and entry.get("file"):
        href = (f"/assets/{html.escape(pack_id)}/file?folder={html.escape(folder, quote=True)}"
                f"&amp;file={html.escape(entry['file'], quote=True)}")
        badge = f"<span class='badge {'anim' if kind == 'animation' else 'snd'}'>{'RIG' if kind == 'animation' else 'AUDIO'}</span>"
        act = f"<a class='dl' href='{href}'>&#11015; Save</a>"
        size = f"<div class='size'>{_fmt_size(entry.get('size'))}</div>"
        audio = ""
        if kind == "sound":
            audio = (f"<audio controls preload='none' src='/assets/{html.escape(pack_id)}/file"
                     f"?folder={html.escape(folder, quote=True)}&amp;file={html.escape(entry['file'], quote=True)}'></audio>")
    elif st == "failed":
        badge = "<span class='badge failed'>Unavailable</span>"
        act = "<span class='dl off' title=\"Roblox no longer serves this asset\">Unavailable</span>"
        size = "<div class='size'>&mdash;</div>"
        audio = ""
        path_txt = (html.escape(str(entry.get("error") or "not available")) + (f" {path_txt}" if path_txt else ""))
    else:
        badge = "<span class='badge pending'>Downloading</span>"
        act = "<span class='dl off'>…</span>"
        size = "<div class='size'>&mdash;</div>"
        audio = ""

    return f"""<div class="file-row">
      {badge}
      <div class="file-main">
        <div class="file-name">{name}</div>
        <div class="file-sub"><span class="id">#{aid}</span> <span class="path">{path_txt}</span>{extra}</div>
      </div>
      {audio}
      {size}
      <div class="file-act">{act}</div>
    </div>"""


async def handle_assets_list(request):
    user = core._get_user_info(request)
    if not user:
        return _json_login()
    is_owner = int(user.get("id", 0)) == core.BOT_OWNER_ID
    out = []
    for m in list_packs():
        if not is_owner and str(m.get("author_id")) != str(user.get("id")):
            continue
        out.append({"pack_id": m.get("pack_id"), "game_name": m.get("game_name"),
                    "place_id": m.get("place_id"), "created": m.get("created"),
                    "status": m.get("status"), **pack_state(m)})
    return web.json_response(out)


async def handle_assets_status(request):
    pack_id = request.match_info.get("pack_id", "")
    manifest = load_manifest(pack_id)
    if not manifest:
        return web.json_response({"error": "not_found"}, status=404)
    if not can_view(request, manifest):
        user = core._get_user_info(request)
        return _json_login() if not user else _json_forbidden()
    state = pack_state(manifest)
    return web.json_response({"pack_id": pack_id, "status": manifest.get("status"), **state})


def _auth_pack(request, pack_id: str):
    """Return (manifest, None) on success or (None, response) on failure."""
    manifest = load_manifest(pack_id)
    if not manifest:
        return None, web.json_response({"error": "not_found"}, status=404)
    if not can_view(request, manifest):
        user = core._get_user_info(request)
        return None, (_json_login() if not user else _json_forbidden())
    return manifest, None


def _disposition(filename: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", filename)
    from urllib.parse import quote
    return f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(filename)}"


async def handle_asset_file(request):
    pack_id = request.match_info.get("pack_id", "")
    manifest, err = _auth_pack(request, pack_id)
    if err:
        return err
    folder = request.query.get("folder", "")
    name = request.query.get("file", "")
    if folder not in {f for f, _ in FOLDERS} or not SAFE_FILE_RE.match(name) or "/" in name or "\\" in name:
        return web.Response(text="Bad file name", status=400)
    base = pack_dir(pack_id)
    path = os.path.join(base, folder, name)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(real):
        return web.Response(text="Not found", status=404)
    return web.FileResponse(real, headers={"Content-Disposition": _disposition(name),
                                           "Cache-Control": "private, max-age=3600"})


def _zip_state_key(manifest: dict) -> str:
    size = 0
    ready = 0
    for key in ("animations", "sounds"):
        for e in manifest.get(key) or []:
            if e.get("status") == "ready":
                ready += 1
                size += int(e.get("size") or 0)
    return f"{ready}:{size}"


def _build_zip(pack_id: str, manifest: dict) -> str:
    base = pack_dir(pack_id)
    zip_path = os.path.join(base, f"{pack_id}.zip")
    root = _safe_name(str(manifest.get("game_name") or pack_id), pack_id)
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for folder, kind in FOLDERS:
            key = "animations" if kind == "animation" else "sounds"
            for e in manifest.get(key) or []:
                if e.get("status") == "ready" and e.get("file"):
                    src = os.path.join(base, folder, e["file"])
                    if os.path.isfile(src):
                        zf.write(src, arcname=f"{root}/{folder}/{e['file']}")
    os.replace(tmp, zip_path)
    return zip_path


async def handle_asset_zip(request):
    pack_id = request.match_info.get("pack_id", "")
    manifest, err = _auth_pack(request, pack_id)
    if err:
        return err
    base = pack_dir(pack_id)
    zip_path = os.path.join(base, f"{pack_id}.zip")
    key = _zip_state_key(manifest)
    if key == "0:0":
        return web.json_response({"error": "no_files_ready"}, status=409)

    def ensure():
        if os.path.isfile(zip_path) and manifest.get("zip_state") == key:
            return zip_path
        return _build_zip(pack_id, manifest)

    try:
        path = await asyncio.to_thread(ensure)
    except Exception as e:
        print(f"[ASSETS] zip build failed: {e}")
        return web.Response(text="Could not build the zip right now.", status=500)

    manifest["zip_state"] = key
    await asyncio.to_thread(save_manifest, pack_id, manifest)

    name = _safe_name(str(manifest.get("game_name") or pack_id), pack_id) + "_assets.zip"
    return web.FileResponse(path, headers={"Content-Disposition": _disposition(name)})


async def handle_login_art(request):
    art = os.path.join(core.BASE_DIR, "site", "login_art.png")
    if os.path.isfile(art):
        return web.FileResponse(art, headers={"Cache-Control": "public, max-age=86400"})
    return web.Response(text=_FALLBACK_ART, content_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=3600"})


def register_routes(app: web.Application):
    """Must be called BEFORE the catch-all /{filename} route."""
    app.router.add_get("/assets", handle_assets_index)
    app.router.add_get("/assets/login-art", handle_login_art)
    app.router.add_get("/assets/{pack_id}/zip", handle_asset_zip)
    app.router.add_get("/assets/{pack_id}/file", handle_asset_file)
    app.router.add_get("/assets/{pack_id}", handle_asset_pack)
    app.router.add_get("/api/assets/list", handle_assets_list)
    app.router.add_get("/api/assets/{pack_id}/status", handle_assets_status)
