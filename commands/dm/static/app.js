/* ============================================================
   dm.luaisgame.com — single page app
   Sections:
     1  utils
     2  state
     3  toasts / modals
     4  api helper
     5  router
     6  realtime websocket
     7  auth screen + boot
     8  shell + rail/sidebar/topbar
     9  messages + composer
    10  uploads
    11  popouts / profile
    12  modals (server / channel / role / invite / settings / admin)
    13  calls: WebRTC mesh + relay screenshare
    14  init
   ============================================================ */
(function () {
"use strict";

/* ================= 1. utils ================= */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

function h(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = String(html).trim();
  return tpl.content.firstElementChild;
}

function debounce(fn, ms) {
  let t;
  return function () {
    const args = arguments;
    clearTimeout(t);
    t = setTimeout(() => fn.apply(null, args), ms);
  };
}

const PERM = {
  ADMIN: 1, MANAGE_CHANNELS: 2, MANAGE_ROLES: 4,
  MANAGE_MESSAGES: 8, KICK: 16, BAN: 32,
};
const PERM_NAMES = [
  [PERM.ADMIN, "Administrator", "Every permission in this server."],
  [PERM.MANAGE_CHANNELS, "Manage channels", "Create, rename, reorder and delete channels."],
  [PERM.MANAGE_ROLES, "Manage roles", "Create roles and edit member roles."],
  [PERM.MANAGE_MESSAGES, "Manage messages", "Edit and delete other people's messages."],
  [PERM.KICK, "Kick members", "Remove members from the server."],
  [PERM.BAN, "Ban members", "Ban members from the server."],
];

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return (n < 10 ? n.toFixed(1) : Math.round(n)) + " " + units[i];
}

function hashHue(text) {
  let hash = 0;
  const s = String(text || "?");
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(hash) % 360;
}

function initials(name) {
  const parts = String(name || "?").trim().split(/[\s_.-]+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** Only same-origin API paths or Discord CDN URLs may reach an <img src>. */
function safeURL(url) {
  if (!url) return "";
  const u = String(url);
  if (u.startsWith("/api/dm/") || u.startsWith("/assets/") || u.startsWith("/")) return u;
  if (/^https:\/\/cdn\.discordapp\.com\//.test(u)) return u;
  return "";
}

function avatarInner(user) {
  const url = safeURL(user && user.avatar);
  if (url) return `<img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">`;
  return esc(initials(user && (user.display_name || user.username)));
}

function avatarHTML(user, cls, presence) {
  const u = user || {};
  const dot = presence !== false && u.presence
    ? `<i class="dot small ${esc(u.presence)}"></i>` : "";
  return `<span class="${cls || "mini-avatar"}" style="background:${avatarBg(u)}">${avatarInner(u)}${presence !== false && u.uid !== undefined ? dot : ""}</span>`;
}

function avatarBg(user) {
  if (user && user.avatar) return "transparent";
  return `linear-gradient(135deg, hsl(${hashHue(user && user.username)} 60% 42%), hsl(${(hashHue(user && user.username) + 40) % 360} 60% 34%))`;
}

function isSameDay(a, b) {
  const da = new Date(a), db = new Date(b);
  return da.getFullYear() === db.getFullYear() && da.getMonth() === db.getMonth()
    && da.getDate() === db.getDate();
}

function fmtClock(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function fmtStamp(ts) {
  const d = new Date(ts), now = new Date();
  const today = isSameDay(d.getTime(), now.getTime());
  const yest = isSameDay(d.getTime(), now.getTime() - 86400000);
  const time = fmtClock(ts);
  if (today) return "Today at " + time;
  if (yest) return "Yesterday at " + time;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })
    + ", " + time;
}

function fmtDayLabel(ts) {
  const d = new Date(ts), now = new Date();
  if (isSameDay(ts, now.getTime())) return "Today";
  if (isSameDay(ts, now.getTime() - 86400000)) return "Yesterday";
  return d.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" });
}

function fmtShort(ts) {
  const d = new Date(ts), now = new Date();
  if (isSameDay(ts, now.getTime())) return fmtClock(ts);
  if (isSameDay(ts, now.getTime() - 86400000)) return "Yst";
  return d.toLocaleDateString([], { month: "numeric", day: "numeric", year: "2-digit" });
}

function relTime(ts) {
  const diff = (Date.now() - (ts || 0)) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return new Date(ts).toLocaleDateString();
}

/** Message timestamps: normalize seconds -> ms (older servers send seconds). */
function normMsg(m) {
  if (!m) return m;
  if (m.created_at && m.created_at < 1e12) m.created_at *= 1000;
  if (m.edited_at && m.edited_at < 1e12) m.edited_at *= 1000;
  return m;
}

function uidKey(u) { return String(u); }

/* ================= 2. state ================= */

const state = {
  user: null,
  servers: [],              // serverBrief[]
  dms: [],                  // channel[]
  serverCache: {},          // id -> serverFull
  currentServer: null,      // serverFull | null
  currentChannel: null,     // channel | null
  messages: {},             // channelId -> message[]
  loadedChannels: {},       // channelId -> true (history fetched)
  unread: {},               // key -> count   ("s5" server, "c12" channel)
  lastRead: {},             // key -> timestamp (persisted locally)
  typing: {},               // channelId -> {uid: {name, ts}}
  members: {},              // serverId -> member[]
  presence: {},             // uid -> presence
  badges: [],               // badge catalogue
  calls: {},
  route: { name: "home" },
  ws: null, wsReady: false, reconnectDelay: 1000, latency: 0,
  shellMounted: false,
  draft: {},
  uploadQueue: [],           // attachments waiting in the composer strip
  ring: null,                // incoming call being rang for us
};

try {
  state.lastRead = JSON.parse(localStorage.getItem("dm.lastRead") || "{}");
} catch (e) { state.lastRead = {}; }

function saveLastRead() {
  try { localStorage.setItem("dm.lastRead", JSON.stringify(state.lastRead)); } catch (e) {}
}

function markRead(key) {
  if (!key) return;
  state.lastRead[key] = Date.now();
  state.unread[key] = 0;
  saveLastRead();
  renderRail();
}

function bumpUnread(key) {
  if (!key) return;
  state.unread[key] = (state.unread[key] || 0) + 1;
  renderRail();
  if (state.route.name === "sidebarOnly") renderSidebar();
}

function serverChannels(server) { return (server && server.channels) || []; }

function channelById(cid) {
  cid = Number(cid);
  for (const dm of state.dms) if (dm.id === cid) return dm;
  if (state.currentServer) {
    for (const c of serverChannels(state.currentServer)) if (c.id === cid) return c;
  }
  for (const key in state.serverCache) {
    const srv = state.serverCache[key];
    for (const c of serverChannels(srv)) if (c.id === cid) return c;
  }
  return null;
}

function findServer(sid) {
  sid = Number(sid);
  return state.servers.find((s) => s.id === sid) || null;
}

function dmTitle(dm) {
  if (dm.name) return dm.name;
  const others = (dm.members || []).filter((m) => m.uid !== (state.user && state.user.uid));
  if (others.length) return others.map((m) => m.display_name).join(", ");
  return "Group chat";
}

function hasPerm(bit) {
  const s = state.currentServer;
  if (!s) return false;
  return !!((s.my_permissions || 0) & bit);
}

function roleById(rid) {
  const s = state.currentServer;
  if (!s) return null;
  return (s.roles || []).find((r) => r.id === Number(rid)) || null;
}

function memberByUid(uid, serverId) {
  const srv = serverId ? (state.serverCache[serverId] || state.currentServer) : state.currentServer;
  if (!srv) return null;
  return (srv.members || []).find((m) => m.uid === Number(uid)) || null;
}

function colorFor(member, fallbackUser) {
  if (member && member.roles && member.roles.length) {
    let best = null;
    for (const rid of member.roles) {
      const role = (state.currentServer && state.currentServer.roles || []).find((r) => r.id === rid)
        || roleById(rid);
      if (role && (!best || role.position > best.position)) best = role;
    }
    if (best && best.color) return best.color;
  }
  if (fallbackUser && fallbackUser.is_owner) return "#f0b232";
  return "";
}

function isOnline(uid) {
  const u = typeof uid === "object" ? uid : null;
  if (u) return (u.presence || state.presence[u.uid] || "offline") !== "offline";
  return (state.presence[uid] || "offline") !== "offline";
}

/* ================= 3. toasts / modals ================= */

function toast(message, kind, ms) {
  const box = $("#toasts");
  if (!box) return;
  const node = h(`<div class="toast ${esc(kind || "info")}">${esc(message)}</div>`);
  box.appendChild(node);
  const life = ms || (kind === "error" ? 6000 : 4000);
  setTimeout(() => {
    node.style.transition = "opacity .2s, transform .2s";
    node.style.opacity = "0";
    node.style.transform = "translateX(24px)";
    setTimeout(() => node.remove(), 220);
  }, life);
}

let modalStack = [];

function openModal(html, opts) {
  opts = opts || {};
  const backdrop = h(`<div class="modal-backdrop">${html}</div>`);
  const box = $("#modals");
  box.appendChild(backdrop);
  const entry = { node: backdrop, onClose: opts.onClose };
  modalStack.push(entry);

  function close(result) {
    const idx = modalStack.indexOf(entry);
    if (idx === -1) return;
    modalStack.splice(idx, 1);
    backdrop.remove();
    if (opts.onClose) { try { opts.onClose(result); } catch (e) {} }
  }
  entry.close = close;

  backdrop.addEventListener("mousedown", (ev) => {
    if (ev.target === backdrop && opts.dismissible !== false) close(null);
  });
  backdrop.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && opts.dismissible !== false) { ev.stopPropagation(); close(null); }
    if (ev.key === "Tab") {
      const focusables = $$('a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])', backdrop)
        .filter((n) => n.offsetParent !== null);
      if (!focusables.length) return;
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    }
  });
  const autofocus = $("[autofocus]", backdrop) || $("input,textarea,button", backdrop);
  if (autofocus) setTimeout(() => autofocus.focus(), 30);
  backdrop.__close = close;
  return { node: backdrop, close };
}

function closeModal() {
  const top = modalStack[modalStack.length - 1];
  if (top) top.close(null);
}

function closeAllModals() { while (modalStack.length) modalStack[modalStack.length - 1].close(null); }

function confirmModal(title, message, confirmLabel, onYes, danger) {
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>${esc(title)}</h2><p>${esc(message)}</p></div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn ${danger ? "danger" : ""}" data-yes>${esc(confirmLabel || "Confirm")}</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-yes]", m.node).onclick = () => { m.close(true); onYes(); };
  return m;
}

/* ================= 4. api helper ================= */

async function api(method, path, body, opts) {
  opts = opts || {};
  const init = { method, headers: {} };
  if (body !== undefined && body !== null) {
    if (body instanceof FormData || body instanceof Blob) init.body = body;
    else { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    if (!opts.silent) toast("Can't reach the server.", "error");
    throw err;
  }
  if (res.status === 401 && !opts.noAuthRedirect) {
    showAuth("Your session ended. Sign in again.");
    throw new Error("not signed in");
  }
  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.indexOf("application/json") !== -1) {
    try { data = await res.json(); } catch (e) { data = null; }
  }
  if (!res.ok) {
    const msg = (data && data.error) || ("Request failed (" + res.status + ")");
    if (!opts.silent) toast(msg, "error");
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* ================= 5. router ================= */

function currentPath() {
  if (location.hash.indexOf("#/") === 0) return location.hash.slice(1);
  return location.pathname || "/";
}

function navigate(path, replace) {
  if (currentPath() === path) { handleRoute(); return; }
  if (replace) history.replaceState({}, "", path);
  else history.pushState({}, "", path);
  handleRoute();
}

function handleRoute() {
  if (!state.user) return;
  const path = currentPath();
  let m;
  if (!state.shellMounted && path.indexOf("/settings/") !== 0) mountShell();

  if (path === "/" || path === "" || path === "/index.html") {
    state.route = { name: "home" };
    state.currentServer = null;
    state.currentChannel = null;
    renderRail(); renderSidebar(); renderTopbar(); renderChatArea();
    return;
  }

  if ((m = path.match(/^\/servers\/(\d+)(?:\/(channels|voice)\/(\d+))?$/))) {
    const sid = Number(m[1]);
    const cid = m[3] ? Number(m[3]) : null;
    openServer(sid, cid, m[2] === "voice");
    return;
  }

  if ((m = path.match(/^\/channels\/(\d+)$/))) {
    openDM(Number(m[1]));
    return;
  }

  if (path.indexOf("/settings/") === 0) {
    const section = path.slice("/settings/".length) || "profile";
    if (section === "admin" && !(state.user && state.user.is_owner)) { navigate("/", true); return; }
    state.route = { name: "settings", section };
    renderRail(); renderSidebar(); renderSettings(section);
    return;
  }

  if ((m = path.match(/^\/invite\/(.+)$|\/s\/(.+)$/))) {
    const code = (m[1] || m[2] || "").trim();
    if (code) showInviteModal(code);
    if (state.route.name === "unhandled") navigate("/", true);
    else { state.route = { name: "unhandled" }; renderRail(); renderSidebar(); renderHomeBlank(); }
    return;
  }

  state.route = { name: "unhandled" };
  navigate("/", true);
}

/* ================= 6. realtime websocket ================= */

let pingTimer = null;

function wsURL() {
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/dm";
}

function connectWS() {
  if (state.ws && (state.ws.readyState === 0 || state.ws.readyState === 1)) return;
  let ws;
  try { ws = new WebSocket(wsURL()); } catch (e) { scheduleReconnect(); return; }
  state.ws = ws;
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    state.wsReady = true;
    state.reconnectDelay = 1000;
    updateConnectionBanner();
    clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (state.ws && state.ws.readyState === 1) {
        try { state.ws.send(JSON.stringify({ t: "ping", ts: Date.now() })); } catch (e) {}
      }
    }, 10000);
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") { onBinaryFrame(ev.data); return; }
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleWSEvent(msg, ev);
  };

  ws.onclose = () => {
    state.wsReady = false;
    clearInterval(pingTimer);
    updateConnectionBanner();
    scheduleReconnect();
  };

  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function scheduleReconnect() {
  updateConnectionBanner();
  setTimeout(connectWS, state.reconnectDelay);
  state.reconnectDelay = Math.min(state.reconnectDelay * 1.7, 30000);
}

function sendWS(payload) {
  if (state.ws && state.ws.readyState === 1) {
    try { state.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload)); } catch (e) {}
    return true;
  }
  return false;
}

function updateConnectionBanner() {
  let banner = $("#connBanner");
  if (state.wsReady) { if (banner) banner.remove(); return; }
  if (!banner) {
    banner = h(`<div class="banner offline" id="connBanner">
      <span>Connection lost — trying to reconnect&hellip;</span>
      <button class="btn sm secondary" id="reconnectNow">Reconnect now</button>
    </div>`);
    document.body.insertBefore(banner, document.body.firstChild);
    $("#reconnectNow", banner).onclick = () => {
      state.reconnectDelay = 1000;
      try { if (state.ws) state.ws.close(); } catch (e) {}
      connectWS();
    };
  }
}

function handleWSEvent(msg, rawEvent) {
  if (msg.t === "pong") {
    state.latency = Math.max(0, Date.now() - (msg.ts || Date.now()));
    updateLatencyHUD();
    return;
  }
  if (msg.t === "ready") return;
  if (msg.t === "error") { console.warn("[ws]", msg.error); return; }
  if (msg.t === "call.state") { onCallState(msg); return; }
  if (msg.t === "event") dispatchEvent(msg.op, msg.data || {});
}

function dispatchEvent(op, data) {
  switch (op) {
    case "message.create": onMessageCreate(data); break;
    case "message.update": onMessageUpdate(data); break;
    case "message.delete": onMessageDelete(data); break;
    case "channel.create": onChannelEvent("create", data); break;
    case "channel.update": onChannelEvent("update", data); break;
    case "channel.delete": onChannelEvent("delete", data); break;
    case "server.update": onServerUpdate(data); break;
    case "role.create":
    case "role.update":
    case "role.delete": onRoleEvent(op, data); break;
    case "member.join":
    case "member.leave":
    case "member.update": onMemberEvent(op, data); break;
    case "server.kicked":
      toast(`You were removed from ${data.server_name || "a server"}.`, "warn");
      state.servers = state.servers.filter((s) => s.id !== data.server_id);
      delete state.serverCache[data.server_id];
      if (state.currentServer && state.currentServer.id === data.server_id) navigate("/", true);
      renderRail(); renderSidebar();
      break;
    case "dm.create": onDmCreate(data); break;
    case "typing": onTyping(data); break;
    case "presence.update":
      state.presence[data.uid] = data.presence;
      updateMemberPresence(data.uid, data.presence);
      break;
    case "badge.granted":
      if (state.user && data.badges) {
        state.user.badges = data.badges;
        renderRail(); renderSidebar();
        toast("You earned a badge!", "success");
      }
      break;
    case "vanity.decided":
      toast(data.status === "approved"
        ? `Vanity approved: dm.luaisgame.com/s/${data.slug}`
        : "Your vanity request was declined.", data.status === "approved" ? "success" : "warn", 7000);
      break;
    case "webrtc.signal": onWebRTCSignal(data); break;
    case "call.update": onCallUpdate(data); break;
    case "call.ended": onCallEnded(data); break;
    case "call.ring": onCallRing(data); break;
    default: break;
  }
}

function updateLatencyHUD() {
  const node = $("#latency");
  if (!node) return;
  const ms = Math.round(state.latency);
  node.textContent = ms + " ms";
  node.classList.toggle("warn", ms >= 60 && ms < 120);
  node.classList.toggle("bad", ms >= 120);
}

/* ================= 7. auth screen + boot ================= */

function showAuth(message) {
  stopCall(true);
  state.user = null;
  state.shellMounted = false;
  const app = $("#app");
  app.innerHTML = `
    <div class="auth-wrap">
      <div class="auth-card">
        <div class="auth-logo">DM</div>
        <h1>Welcome to DM</h1>
        <p class="sub">Free chat, servers, group chats and screenshare — no paid tier, ever.</p>
        <div class="auth-tabs">
          <button class="active" data-tab="login">Log in</button>
          <button data-tab="signup">Sign up</button>
        </div>
        <form id="authForm" autocomplete="on">
          <div class="field" data-username hidden>
            <label>Username</label>
            <input class="input" name="username" placeholder="yourname" maxlength="32" autocomplete="username">
          </div>
          <div class="field">
            <label>Email</label>
            <input class="input" name="email" type="email" placeholder="you@example.com" autocomplete="email" required>
          </div>
          <div class="field">
            <label>Password</label>
            <input class="input" name="password" type="password" placeholder="At least 8 characters"
                   autocomplete="current-password" required minlength="8">
          </div>
          <div class="error-text" id="authError">${message ? esc(message) : ""}</div>
          <button class="btn block" type="submit" id="authSubmit">Log in</button>
        </form>
        <div class="auth-divider">or</div>
        <a class="btn discord" href="/api/dm/auth/discord?next=${encodeURIComponent(location.pathname + location.hash)}">
          <svg width="20" height="16" viewBox="0 0 71 55" fill="none" aria-hidden="true"><path d="M60.1 4.9A58.5 58.5 0 0 0 45.4.2a.2.2 0 0 0-.2.1 40.8 40.8 0 0 0-1.8 3.7 54 54 0 0 0-16.2 0 26.5 26.5 0 0 0-1.8-3.7.2.2 0 0 0-.2-.1A58.4 58.4 0 0 0 10.9 4.9a.2.2 0 0 0-.1.1C1.6 18.4-.5 31.7.5 44.8a.2.2 0 0 0 .1.1 58.7 58.7 0 0 0 17.7 9 .2.2 0 0 0 .2-.1 42 42 0 0 0 3.6-5.9.2.2 0 0 0-.1-.3 38.7 38.7 0 0 1-5.5-2.6.2.2 0 0 1 0-.4c.4-.3.7-.6 1.1-.9a.2.2 0 0 1 .2 0c11.5 5.3 24 5.3 35.4 0a.2.2 0 0 1 .2 0l1.1.9a.2.2 0 0 1 0 .4c-1.8 1-3.6 1.9-5.6 2.6a.2.2 0 0 0-.1.3 47.2 47.2 0 0 0 3.7 5.9.2.2 0 0 0 .2.1 58.5 58.5 0 0 0 17.7-9 .2.2 0 0 0 .1-.1c1.2-15-2-28.3-8.5-39.8a.2.2 0 0 0-.1-.1ZM23.7 36.3c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Zm23.6 0c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1-2.8 7.1-6.4 7.1Z" fill="white"/></svg>
          Continue with Discord
        </a>
        <p class="auth-note">
          One account works on <strong>dm</strong>, <strong>mc</strong> and <strong>storage</strong>.luaisgame.com.<br>
          Your login is remembered by the server, so a restart never signs you out.
        </p>
      </div>
    </div>`;

  let mode = "login";
  const form = $("#authForm");
  const errBox = $("#authError");
  const submit = $("#authSubmit");
  const userInput = $('[name="username"]', form);

  $$(".auth-tabs button", app).forEach((btn) => {
    btn.onclick = () => {
      mode = btn.dataset.tab;
      $$(".auth-tabs button", app).forEach((b) => b.classList.toggle("active", b === btn));
      userInput.closest(".field").hidden = mode !== "signup";
      userInput.required = mode === "signup";
      submit.textContent = mode === "signup" ? "Create account" : "Log in";
      errBox.textContent = "";
    };
  });

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    errBox.textContent = "";
    submit.disabled = true;
    submit.textContent = mode === "signup" ? "Creating…" : "Logging in…";
    const payload = {
      email: form.email.value.trim(),
      password: form.password.value,
    };
    if (mode === "signup") payload.username = form.username.value.trim();
    try {
      const data = await api("POST", mode === "signup" ? "/api/dm/auth/register" : "/api/dm/auth/login",
        payload, { silent: true });
      form.password.value = "";
      await afterAuth(data.user);
    } catch (err) {
      errBox.textContent = err.message || "Something went wrong.";
    } finally {
      submit.disabled = false;
      submit.textContent = mode === "signup" ? "Create account" : "Log in";
    }
  };
}

async function afterAuth(user) {
  state.user = user;
  const boot = await api("GET", "/api/dm/bootstrap");
  applyBootstrap(boot);
  mountShell();
  connectWS();
  handleRoute();
  toast(`Signed in as ${user.display_name || user.username}`, "success");
}

function applyBootstrap(data) {
  state.user = data.user;
  state.servers = data.servers || [];
  state.dms = data.dms || [];
  (data.calls || []).forEach((c) => { if (c.room) state.calls[c.room] = c; });
  for (const s of state.servers) state.unread["s" + s.id] = s.unread || 0;
  for (const d of state.dms) markChannelFresh(d);
}

function markChannelFresh(dm) {
  const key = "c" + dm.id;
  if (state.lastRead[key] === undefined) { state.lastRead[key] = Date.now(); saveLastRead(); }
}

async function boot() {
  let data;
  try {
    data = await api("GET", "/api/dm/bootstrap", null, { silent: true, noAuthRedirect: true });
  } catch (err) {
    if (err && err.status && err.status !== 401) console.warn(err);
    showAuth();
    return;
  }
  if (!data || !data.user) { showAuth(); return; }
  applyBootstrap(data);
  mountShell();
  connectWS();
  handleRoute();
}

async function logout() {
  try { await api("POST", "/api/dm/auth/logout", {}); } catch (e) {}
  try { if (state.ws) state.ws.close(); } catch (e) {}
  location.href = "/";
}

/* ================= 8. shell + rail/sidebar/topbar ================= */

function mountShell() {
  if (state.shellMounted) return;
  state.shellMounted = true;
  $("#app").innerHTML = `
    <div class="shell">
      <nav class="rail" id="rail" aria-label="Servers and DMs"></nav>
      <aside class="sidebar" id="sidebar" aria-label="Channels"></aside>
      <div class="main" id="main">
        <header class="topbar" id="topbar"></header>
        <div id="callHost"></div>
        <div class="body-row" id="bodyRow" style="display:flex;flex:1;min-height:0">
          <section class="chat" id="chatCol"></section>
          <aside class="members" id="members" aria-label="Members"></aside>
        </div>
      </div>
      <div class="drawer-back" id="drawerBack"></div>
    </div>`;
  $("#drawerBack").onclick = closeDrawers;
  initGlobalKeys();
}

function closeDrawers() {
  $("#rail").classList.remove("open");
  $("#sidebar").classList.remove("open");
  $("#drawerBack").classList.remove("show");
}

function openDrawer(which) {
  const rail = $("#rail"), side = $("#sidebar");
  rail.classList.toggle("open", which === "rail");
  side.classList.toggle("open", which === "sidebar");
  $("#drawerBack").classList.toggle("show", !!which);
}

function totalUnread() {
  let n = 0;
  for (const k in state.unread) n += state.unread[k] || 0;
  return n;
}

function renderRail() {
  const rail = $("#rail");
  if (!rail) return;
  const me = state.user || {};
  const homeActive = state.route.name === "home" || state.route.name === "settings";
  const dmCount = totalUnread();

  let html = `
    <div class="rail-item ${homeActive ? "active" : ""}" data-home title="Direct messages">
      <span class="rail-pill"></span>
      <span class="rail-avatar" style="background:${avatarBg(me)}">${avatarInner(me)}</span>
      ${dmCount ? `<span class="rail-badge">${dmCount > 99 ? "99+" : dmCount}</span>` : ""}
      <span class="rail-label">You</span>
    </div>
    <div class="rail-sep"></div>`;

  for (const s of state.servers) {
    const active = state.currentServer && state.currentServer.id === s.id;
    const unread = state.unread["s" + s.id] || 0;
    html += `
      <div class="rail-item ${active ? "active" : ""}" data-server="${s.id}" title="${esc(s.name)}">
        <span class="rail-pill"></span>
        <span class="rail-avatar">${s.icon ? `<img src="${esc(s.icon)}" alt="" onerror="this.remove()">` : esc(initials(s.name))}</span>
        ${unread ? `<span class="rail-badge">${unread > 99 ? "99+" : unread}</span>` : ""}
        <span class="rail-label">${esc(s.name)}</span>
      </div>`;
  }

  html += `<div class="rail-item rail-create" data-create-server title="Add a server">
      <span class="rail-pill"></span><span class="rail-avatar">+</span>
      <span class="rail-label">Add<br>Server</span>
    </div>`;

  rail.innerHTML = html;

  $$("[data-server]", rail).forEach((node) => {
    node.onclick = () => { navigate("/servers/" + node.dataset.server); closeDrawers(); };
  });
  $("[data-home]", rail).onclick = () => { navigate("/"); closeDrawers(); };
  $("[data-create-server]", rail).onclick = showCreateServerModal;
}

function renderSidebar() {
  const side = $("#sidebar");
  if (!side) return;

  if (state.route.name === "settings") { side.innerHTML = settingsSidebarHTML(); wireSettingsSidebar(); return; }

  if (!state.currentServer) {
    side.innerHTML = `
      <div class="side-head">
        <span class="title">Direct Messages</span>
        <button class="btn icon ghost" id="newDmBtn" aria-label="New direct message" title="New DM">✎</button>
      </div>
      <div class="side-scroll">
        <div class="nav-row" data-find>
          <span class="hash">⌕</span><span class="row-name">Find a server</span>
        </div>
        <div class="side-section">Servers</div>
        ${state.servers.length ? state.servers.map((s) => `
          <div class="nav-row" data-goto-server="${s.id}">
            <span class="hash">#</span>
            <span class="row-name">${esc(s.name)}</span>
            ${state.unread["s" + s.id] ? `<span class="row-badge">${state.unread["s" + s.id]}</span>` : ""}
          </div>`).join("")
        : `<div class="hint" style="padding:4px 9px">No servers yet.</div>`}
        <div class="side-section">Messages</div>
        ${state.dms.length ? state.dms.map(dmRowHTML).join("")
        : `<div class="hint" style="padding:4px 9px">No conversations yet. Search for someone to start one.</div>`}
      </div>
      ${userBarHTML()}`;

    $("#newDmBtn").onclick = showNewDMModal;
    $("[data-find]", side).onclick = showFindModal;
    $$("[data-goto-server]", side).forEach((n) => {
      n.onclick = () => { navigate("/servers/" + n.dataset.gotoServer); };
    });
    wireDMRows();
    wireUserBar();
    return;
  }

  const srv = state.currentServer;
  const canManage = hasPerm(PERM.MANAGE_CHANNELS);
  const text = serverChannels(srv).filter((c) => c.kind !== "voice").sort(byPos);
  const voice = serverChannels(srv).filter((c) => c.kind === "voice").sort(byPos);

  side.innerHTML = `
    <div class="side-head" id="serverHead" role="button" tabindex="0" title="Server settings">
      <span class="title">${esc(srv.name)}</span>
      <span style="color:var(--text-faint)">${srv.vanity ? "◆ " : ""}▾</span>
    </div>
    <div class="side-scroll">
      <div class="side-section">Text channels${canManage ? `<button id="addText" aria-label="Create channel">+</button>` : ""}</div>
      ${text.map((c) => channelRowHTML(c)).join("") || `<div class="hint" style="padding:4px 9px">No text channels.</div>`}
      <div class="side-section">Voice channels${canManage ? `<button id="addVoice" aria-label="Create voice channel">+</button>` : ""}</div>
      ${voice.map((c) => channelRowHTML(c, true)).join("") || `<div class="hint" style="padding:4px 9px">No voice channels.</div>`}
      ${state.user && state.user.is_owner ? `<div class="side-section">Owner</div>
        <div class="nav-row" data-admin><span class="hash">★</span><span class="row-name">Owner dashboard</span></div>` : ""}
    </div>
    ${userBarHTML()}`;

  const head = $("#serverHead", side);
  head.onclick = (ev) => showServerMenu(ev, srv);
  head.onkeydown = (ev) => { if (ev.key === "Enter") showServerMenu({ clientX: head.getBoundingClientRect().left, clientY: head.getBoundingClientRect().bottom }, srv); };
  if (canManage) {
    const at = $("#addText", side); if (at) at.onclick = (e) => { e.stopPropagation(); showCreateChannelModal("text"); };
    const av = $("#addVoice", side); if (av) av.onclick = (e) => { e.stopPropagation(); showCreateChannelModal("voice"); };
  }
  const adm = $("[data-admin]", side);
  if (adm) adm.onclick = () => navigate("/settings/admin");

  $$("[data-channel]", side).forEach((node) => {
    node.onclick = () => {
      const kind = node.dataset.kind;
      const cid = node.dataset.channel;
      const base = "/servers/" + srv.id + "/" + (kind === "voice" ? "voice" : "channels") + "/" + cid;
      navigate(base);
      closeDrawers();
    };
  });
  wireUserBar();
}

function byPos(a, b) { return (a.position || 0) - (b.position || 0) || a.id - b.id; }

function channelRowHTML(c, isVoice) {
  const active = state.currentChannel && state.currentChannel.id === c.id;
  const key = "c" + c.id;
  const unread = state.unread[key] || 0;
  const inCall = callRoomFor(c.id) && state.activeRoom === callRoomFor(c.id);
  return `<div class="nav-row ${active ? "active" : ""}" data-channel="${c.id}" data-kind="${esc(c.kind)}">
    <span class="hash">${isVoice ? "🔊" : "#"}</span>
    <span class="row-name">${esc(c.name)}</span>
    ${inCall ? `<span class="row-badge muted">Live</span>` : ""}
    ${unread ? `<span class="row-badge">${unread > 99 ? "99+" : unread}</span>` : ""}
  </div>`;
}

function dmRowHTML(dm) {
  const key = "c" + dm.id;
  const active = state.currentChannel && state.currentChannel.id === dm.id;
  const unread = state.unread[key] || 0;
  const others = (dm.members || []).filter((m) => m.uid !== (state.user && state.user.uid));
  const online = others.some((m) => isOnline(m));
  const single = dm.kind === "dm" && others.length === 1 ? others[0] : null;
  const badge = `<i class="dot small ${online ? "online" : "offline"}"></i>`;
  return `<div class="nav-row dm-row ${active ? "active" : ""}" data-dm="${dm.id}">
    <span class="mini-avatar" style="background:${single ? avatarBg(single) : "var(--accent)"}">
      ${single ? avatarInner(single) : esc(initials(dmTitle(dm)))}${badge}
    </span>
    <span class="row-name">${esc(dmTitle(dm))}</span>
    ${unread ? `<span class="row-badge">${unread > 99 ? "99+" : unread}</span>` : ""}
  </div>`;
}

function wireDMRows() {
  $$("[data-dm]").forEach((node) => {
    node.onclick = () => { navigate("/channels/" + node.dataset.dm); closeDrawers(); };
    node.oncontextmenu = (ev) => { ev.preventDefault(); showDMMenu(ev, Number(node.dataset.dm)); };
  });
}

function userBarHTML() {
  const me = state.user || {};
  return `<div class="user-bar">
    <div class="mini-avatar" id="meAvatar" style="background:${avatarBg(me)}">${avatarInner(me)}<i class="dot small ${esc(me.presence || "online")}"></i></div>
    <div class="who" id="meWho" role="button" tabindex="0">
      <div class="name">${esc(me.display_name || me.username)}</div>
      <div class="status">${esc(me.custom_status || (me.is_owner ? "Platform owner" : (me.presence || "online")))}</div>
    </div>
    <div class="ctl">
      <button id="muteBtn" title="Mute microphone" aria-label="Mute microphone">🎤</button>
      <button id="deafenBtn" title="Deafen" aria-label="Deafen">🎧</button>
      <button id="settingsBtn" title="User settings" aria-label="User settings">⚙</button>
    </div>
  </div>`;
}

function wireUserBar() {
  const who = $("#meWho");
  if (who) {
    who.onclick = (ev) => showProfilePopout(state.user, ev.currentTarget);
    who.onkeydown = (ev) => { if (ev.key === "Enter") showProfilePopout(state.user, { getBoundingClientRect: () => ({ left: 20, bottom: 300 }) }); };
  }
  const av = $("#meAvatar");
  if (av) av.onclick = (ev) => showProfilePopout(state.user, ev.currentTarget);
  const st = $("#settingsBtn");
  if (st) st.onclick = () => navigate("/settings/profile");
  const mute = $("#muteBtn");
  if (mute) mute.onclick = () => toggleMute();
  const deaf = $("#deafenBtn");
  if (deaf) deaf.onclick = () => toggleDeafen();
  syncCallButtons();
}

function renderTopbar() {
  const bar = $("#topbar");
  if (!bar) return;
  const menuBtn = `<button class="mobile-only" id="drawerBtn" aria-label="Menu">☰</button>`;

  if (state.currentServer && state.currentChannel) {
    const c = state.currentChannel;
    bar.innerHTML = `
      ${menuBtn}
      <span class="chan-name">${c.kind === "voice" ? "🔊" : "#"} ${esc(c.name)}</span>
      ${c.topic ? `<span class="chan-topic">${esc(c.topic)}</span>` : `<span class="spacer"></span>`}
      <span class="spacer"></span>
      <div class="tools">
        <span class="latency" id="latency" title="Round-trip time to the server">– ms</span>
        ${c.kind !== "voice" ? `<button id="notifBtn" title="Mark as read" aria-label="Mark as read">✓</button>` : ""}
        <button id="inviteBtn" title="Invite people" aria-label="Invite people">＋</button>
        <button id="membersBtn" title="Member list" aria-label="Toggle member list">👥</button>
        <button id="searchBtn" title="Search (Ctrl+K)" aria-label="Search">⌕</button>
      </div>`;
    const inv = $("#inviteBtn", bar);
    if (inv) inv.onclick = () => showInviteModal(state.currentServer.invite, state.currentServer);
    const mb = $("#membersBtn", bar);
    if (mb) mb.onclick = () => { $("#members").classList.toggle("hidden"); };
    const nb = $("#notifBtn", bar);
    if (nb) nb.onclick = () => { markRead("c" + state.currentChannel.id); renderSidebar(); renderMessages(); };
    updateLatencyHUD();
  } else if (state.currentChannel) {
    const c = state.currentChannel;
    const others = (c.members || []).filter((m) => m.uid !== (state.user && state.user.uid));
    bar.innerHTML = `
      ${menuBtn}
      <span class="chan-name" style="gap:8px">
        <span class="mini-avatar" style="width:24px;height:24px;font-size:11px;${others[0] ? "background:" + avatarBg(others[0]) : "background:var(--accent)"}">${others[0] ? avatarInner(others[0]) : esc(initials(dmTitle(c)))}</span>
        ${esc(dmTitle(c))}
      </span>
      <span class="spacer"></span>
      <div class="tools">
        <span class="latency" id="latency">– ms</span>
        <button id="dmCallBtn" title="Start call" aria-label="Start call">📹</button>
        <button id="dmSearchBtn" title="Search (Ctrl+K)" aria-label="Search">⌕</button>
        <button id="dmInfoBtn" title="Conversation info" aria-label="Info">ⓘ</button>
      </div>`;
    const cb = $("#dmCallBtn", bar);
    if (cb) cb.onclick = () => joinRoom(callRoomFor(c.id), true);
    updateLatencyHUD();
  } else {
    bar.innerHTML = `${menuBtn}
      <span class="chan-name">Direct Messages</span>
      <span class="spacer"></span>
      <div class="tools">
        <span class="latency" id="latency">– ms</span>
        <button id="searchBtn" title="Search (Ctrl+K)" aria-label="Search">⌕</button>
        <button id="newDmTop" title="New message" aria-label="New message">✎</button>
      </div>`;
    updateLatencyHUD();
  }

  const db = $("#drawerBtn", bar);
  if (db) db.onclick = () => openDrawer(window.innerWidth <= 720 ? "rail" : "sidebar");
  const sb = $("#searchBtn", bar) || $("#dmSearchBtn", bar);
  if (sb) sb.onclick = showFindModal;
  const nd = $("#newDmTop", bar);
  if (nd) nd.onclick = showNewDMModal;
  const info = $("#dmInfoBtn", bar);
  if (info) info.onclick = showDMInfoModal;
}

/* ================= 9. messages + composer ================= */

function renderChatArea() {
  const col = $("#chatCol");
  if (!col) return;
  $("#members").classList.add("hidden");

  if (!state.currentChannel) {
    col.innerHTML = `
      <div class="empty">
        <div class="big">💬</div>
        <h3>Welcome, ${esc((state.user && (state.user.display_name || state.user.username)) || "")}</h3>
        <p>Pick a conversation on the left, or start something new. Servers, group chats,
           10&nbsp;GB uploads and free screenshare are all included.</p>
        <div style="display:flex;gap:9px;flex-wrap:wrap;justify-content:center;margin-top:6px">
          <button class="btn" id="homeNewDm">New message</button>
          <button class="btn secondary" id="homeCreateServer">Create a server</button>
          <button class="btn ghost" id="homeJoinServer">Join with an invite</button>
        </div>
      </div>`;
    $("#homeNewDm").onclick = showNewDMModal;
    $("#homeCreateServer").onclick = showCreateServerModal;
    $("#homeJoinServer").onclick = () => showJoinServerModal();
    return;
  }

  const isVoice = state.currentChannel.kind === "voice";
  const isDM = !state.currentServer;
  const inCallHere = !!state.activeRoom &&
    state.activeRoom === callRoomFor(state.currentChannel.id);
  const placeholder = isVoice
    ? "Message this voice channel"
    : (isDM ? "Message @" + esc(dmTitle(state.currentChannel) || "conversation")
            : "Message #" + esc(state.currentChannel.name || "channel"));
  col.innerHTML = `
    ${isVoice || inCallHere ? `<div id="voicePanel"></div>` : ""}
    <div class="msg-scroll" id="msgScroll"><div class="msg-scroll-inner" id="msgList"></div></div>
    <div class="typing" id="typing"></div>
    <div class="upload-strip" id="uploadStrip"></div>
    <div class="composer" style="position:relative" id="composerWrap">
      <div class="drop-overlay" id="dropOverlay" style="display:none">Drop files to upload (up to 10 GB each)</div>
      <div class="composer-box">
        <button class="attach-btn" id="attachBtn" title="Upload a file" aria-label="Upload a file">＋</button>
        <textarea id="composerText" rows="1" placeholder="${placeholder}" maxlength="4000"></textarea>
        <button class="send-btn" id="sendBtn" title="Send" aria-label="Send">➤</button>
      </div>
      <input type="file" id="fileInput" multiple hidden>
    </div>`;

  const ta = $("#composerText");
  ta.value = state.draft[draftKey()] || "";
  ta.addEventListener("input", () => {
    state.draft[draftKey()] = ta.value;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  });
  ta.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendDraft(); }
  });
  $("#sendBtn").onclick = sendDraft;
  $("#attachBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (ev) => {
    Array.from(ev.target.files || []).forEach(enqueueUpload);
    ev.target.value = "";
  };
  ta.focus();
  wireDropZone();

  renderMessages({ reset: true });
  renderUploads();
  renderTyping();
  renderCallUI();
}

function draftKey() {
  return state.currentChannel ? "c" + state.currentChannel.id : "none";
}

function renderHomeBlank() {
  const col = $("#chatCol");
  if (!col) return;
  col.innerHTML = `<div class="empty"><div class="big">🔗</div>
    <h3>Invite or vanity link</h3><p>Checking that link&hellip;</p></div>`;
  renderMembers();
}

async function sendDraft() {
  const ta = $("#composerText");
  if (!ta || !state.currentChannel) return;
  const content = ta.value.trim();
  const queue = state.uploadQueue || [];
  const attachments = queue
    .filter((u) => u.status === "done" && u.attachment)
    .map((u) => u.attachment.id);
  if (!content && !attachments.length) return;
  if (queue.some((u) => u.status === "uploading")) {
    toast("Wait for the upload to finish, or cancel it.", "warn");
    return;
  }
  ta.value = "";
  ta.style.height = "auto";
  delete state.draft[draftKey()];
  try {
    await api("POST", `/api/dm/channels/${state.currentChannel.id}/messages`,
      { content, attachments });
    state.uploadQueue = state.uploadQueue.filter((u) => u.status !== "done");
    renderUploads();
  } catch (err) {
    ta.value = content;
  }
}

function renderMessages(opts) {
  opts = opts || {};
  const scroll = $("#msgScroll");
  const list = $("#msgList");
  if (!scroll || !list || !state.currentChannel) return;
  const cid = state.currentChannel.id;
  const msgs = state.messages[cid] || [];
  const nearBottom = opts.reset || (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight) < 120;

  if (!msgs.length) {
    const isDM = !state.currentServer;
    list.innerHTML = `<div class="empty">
        <div class="big">${isDM ? "👋" : "#"}</div>
        <h3>${isDM ? "This is the start of your conversation with " + esc(dmTitle(state.currentChannel))
                    : "Welcome to #" + esc(state.currentChannel.name)}</h3>
        <p>${isDM ? "Send a message, drop a file, or start a call — it's all free."
                  : esc(state.currentChannel.topic || "No one has said anything yet. Be the first!")}</p>
      </div>`;
    return;
  }

  let html = "";
  let prev = null;
  for (let i = 0; i < msgs.length; i++) {
    const msg = msgs[i];
    if (!prev || !isSameDay(msg.created_at, prev.created_at)) {
      html += `<div class="day-divider">${esc(fmtDayLabel(msg.created_at))}</div>`;
      prev = null;
    }
    html += messageHTML(msg, prev);
    prev = msg;
  }
  list.innerHTML = html;
  wireMessageList(list);
  if (nearBottom) scroll.scrollTop = scroll.scrollHeight;
}

function messageHTML(msg, prev) {
  const a = msg.author || {};
  const grouped = !!prev && prev.author && prev.author.uid === a.uid
    && (msg.created_at - prev.created_at) < 300000 && !msg.attachments?.length === false;
  const isGrouped = !!prev && prev.author && prev.author.uid === a.uid
    && (msg.created_at - prev.created_at) < 300000 && (prev.attachments || []).length === 0;
  const roleColor = colorFor(memberByUid(a.uid), a);
  const mentionMe = state.user && msg.content
    && new RegExp("@" + state.user.username.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i").test(msg.content);
  const mine = state.user && a.uid === state.user.uid;
  const canManage = !state.currentServer || hasPerm(PERM.MANAGE_MESSAGES) || mine;

  const badges = (a.badges || []).map((b) =>
    `<span class="badge-pill ${b.slug === "founder" || a.is_owner ? "owner" : ""}"
       style="${b.color && b.slug !== "founder" ? "border-color:" + esc(b.color) + ";color:" + esc(b.color) : ""}"
       title="${esc(b.description || b.name)}">${b.icon ? esc(b.icon) : ""} ${esc(b.name)}</span>`).join("");

  return `
  <article class="msg ${isGrouped ? "grouped" : ""} ${mentionMe ? "mention" : ""}" data-msg="${msg.id}">
    <div class="gutter">
      ${isGrouped ? `<span class="ts-mini">${esc(fmtShort(msg.created_at))}</span>`
        : avatarHTML(Object.assign({}, a, { presence: state.presence[a.uid] || a.presence }), "mini-avatar", false)}
    </div>
    <div class="body">
      ${isGrouped ? "" : `<div class="meta">
        <span class="author ${roleColor ? "role" : ""}" style="${roleColor ? "--role-color:" + esc(roleColor) : ""}" data-uid="${a.uid}">${esc(a.display_name || a.username || "Unknown")}</span>
        ${badges}
        <span class="time" title="${esc(fmtStamp(msg.created_at))}">${esc(fmtStamp(msg.created_at))}</span>
      </div>`}
      ${msg.content ? `<div class="content">${renderContent(msg.content)}</div>` : ""}
      ${(msg.attachments && msg.attachments.length) ? `<div class="attachments">${msg.attachments.map(attachmentHTML).join("")}</div>` : ""}
      ${msg.edited_at ? `<span class="edited">(edited)</span>` : ""}
    </div>
    <div class="msg-actions">
      <button data-act="copy">Copy text</button>
      ${mine ? `<button data-act="edit">Edit</button>` : ""}
      ${canManage ? `<button class="danger" data-act="delete">Delete</button>` : ""}
    </div>
  </article>`;
}

function renderContent(text) {
  let out = esc(text);
  out = out.replace(/(https?:\/\/[^\s<]+)/g, (m) => {
    if (m.indexOf('href="') !== -1) return m;
    return `<a href="${m}" target="_blank" rel="noopener noreferrer">${m}</a>`;
  });
  out = out.replace(/(^|\s)@([a-zA-Z0-9_.]{2,32})/g, (m, sp, name) =>
    `${sp}<span class="mention-chip">@${esc(name)}</span>`);
  return out;
}

function attachmentHTML(att) {
  const mime = att.mime || "";
  const url = encodeURI(att.url || "");
  if (mime.indexOf("image/") === 0) {
    return `<img class="att-img" src="${esc(url)}" alt="${esc(att.filename)}" loading="lazy" data-lightbox="${esc(url)}" onerror="this.outerHTML='<div class=\\'att-file\\'><div class=\\'fico\\'>🖼</div><div class=\\'fmeta\\'><div class=\\'fname\\'>${esc(att.filename)}</div></div></div>'">`;
  }
  if (mime.indexOf("video/") === 0) {
    return `<video class="att-video" src="${esc(url)}" controls preload="metadata" playsinline></video>`;
  }
  if (mime.indexOf("audio/") === 0) {
    return `<audio class="att-audio" src="${esc(url)}" controls preload="metadata"></audio>`;
  }
  return `<div class="att-file">
      <div class="fico">${fileGlyph(att.filename)}</div>
      <div class="fmeta">
        <div class="fname" title="${esc(att.filename)}">${esc(att.filename)}</div>
        <div class="fsize">${fmtBytes(att.size)}</div>
      </div>
      <a class="dl" href="${esc(url)}" download="${esc(att.filename)}" title="Download" aria-label="Download">⬇</a>
    </div>`;
}

function fileGlyph(name) {
  const ext = String(name || "").split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].indexOf(ext) >= 0) return "🖼";
  if (["zip", "rar", "7z", "tar", "gz"].indexOf(ext) >= 0) return "📦";
  if (["mp4", "mov", "webm", "mkv"].indexOf(ext) >= 0) return "🎬";
  if (["mp3", "wav", "ogg", "flac"].indexOf(ext) >= 0) return "🎵";
  if (["lua", "luau", "py", "js", "ts", "json", "txt", "md"].indexOf(ext) >= 0) return "📄";
  if (["exe", "msi", "apk"].indexOf(ext) >= 0) return "⚙️";
  return "📁";
}

function wireMessageList(list) {
  $$("[data-msg]", list).forEach((node) => {
    const id = Number(node.dataset.msg);
    const msg = (state.messages[state.currentChannel.id] || []).find((m) => m.id === id);
    if (!msg) return;
    $$(".msg-actions button", node).forEach((btn) => {
      btn.onclick = (ev) => {
        ev.stopPropagation();
        const act = btn.dataset.act;
        if (act === "copy") copyText(msg.content);
        else if (act === "edit") startEditMessage(msg);
        else if (act === "delete") {
          confirmModal("Delete message", "Delete this message for everyone? This can't be undone.",
            "Delete", async () => {
              try { await api("DELETE", `/api/dm/messages/${msg.id}`); } catch (e) {}
            }, true);
        }
      };
    });
  });
  $$("[data-uid]", list).forEach((node) => {
    node.onclick = (ev) => {
      const uid = Number(node.dataset.uid);
      const member = memberByUid(uid);
      showProfilePopout(member || { uid, username: "?" }, ev.currentTarget);
    };
  });
  $$("[data-lightbox]", list).forEach((img) => {
    img.onclick = () => showLightbox(img.dataset.lightbox);
  });
}

function copyText(text) {
  text = String(text == null ? "" : text);
  const done = () => toast("Copied.", "success");
  const fallback = () => {
    // Older/permission-blocked clipboards: transient textarea + execCommand.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;" +
        "pointer-events:none;z-index:-1";
      document.body.appendChild(ta);
      const sel = document.getSelection();
      const ranges = [];
      for (let i = 0; i < sel.rangeCount; i++) ranges.push(sel.getRangeAt(i));
      ta.focus({ preventScroll: true });
      ta.select();
      ta.setSelectionRange(0, text.length);
      const ok = document.execCommand("copy");
      ta.remove();
      if (ranges.length) { sel.removeAllRanges(); ranges.forEach((r) => sel.addRange(r)); }
      if (ok) { done(); return; }
    } catch (e) { /* fall through */ }
    toast("Couldn't copy — select the text and press Ctrl+C.", "error");
  };
  if (navigator.clipboard && navigator.clipboard.writeText && document.hasFocus()) {
    navigator.clipboard.writeText(text).then(done).catch(fallback);
  } else {
    fallback();
  }
}

function startEditMessage(msg) {
  const node = $(`[data-msg="${msg.id}"] .content`);
  if (!node) return;
  const original = msg.content;
  node.innerHTML = "";
  const ta = document.createElement("textarea");
  ta.className = "input";
  ta.value = original;
  ta.style.width = "100%";
  node.appendChild(ta);
  ta.focus();
  const bar = h(`<div class="hint" style="margin-top:6px">Enter to save · Esc to cancel</div>`);
  node.appendChild(bar);
  const finish = async (save) => {
    node.onkeydown = null;
    if (save && ta.value.trim() && ta.value !== original) {
      try { await api("PATCH", `/api/dm/messages/${msg.id}`, { content: ta.value }); return; }
      catch (e) {}
    }
    renderMessages();
  };
  ta.onkeydown = (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  };
}

function showLightbox(url) {
  const lb = h(`<div class="lightbox"><button class="lb-close" aria-label="Close">✕</button>
    <img src="${esc(url)}" alt=""></div>`);
  document.body.appendChild(lb);
  const close = () => lb.remove();
  lb.onclick = (ev) => { if (ev.target === lb || ev.target.classList.contains("lb-close")) close(); };
  document.addEventListener("keydown", function onEsc(ev) {
    if (ev.key === "Escape") { close(); document.removeEventListener("keydown", onEsc); }
  });
}

function scrollChatToBottom() {
  const scroll = $("#msgScroll");
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
}

function onMessageCreate(data) {
  const msg = normMsg(data.message || data);
  if (!msg || !msg.id) return;
  const cid = msg.channel;
  const list = state.messages[cid] || (state.messages[cid] = []);
  if (list.some((m) => m.id === msg.id)) return;
  list.push(msg);
  const isCurrent = state.currentChannel && state.currentChannel.id === cid;
  if (isCurrent) {
    const scroll = $("#msgScroll");
    const near = scroll && (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight) < 160;
    renderMessages();
    if (near) scrollChatToBottom();
    markRead("c" + cid);
    renderSidebar();
    bumpChannelUnreadFromServer(cid);
  } else {
    bumpUnread("c" + cid);
    bumpServerUnreadForChannel(cid);
  }
  maybeNotify(cid, msg, isCurrent);
}

/** Ping sound + desktop notification for mentions and direct messages. */
function maybeNotify(cid, msg, isCurrent) {
  if (!state.user || !msg.author || msg.author.uid === state.user.uid) return;
  const content = msg.content || "";
  let mentioned = false;
  try {
    mentioned = new RegExp("@" + state.user.username.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")
      .test(content);
  } catch (e) {}
  const isDM = state.dms.some((d) => d.id === cid);
  if (!mentioned && !isDM) return;
  const watching = isCurrent && document.hasFocus();
  if (watching) return;
  sfx.ping();
  let title = "New message";
  const dm = state.dms.find((d) => d.id === cid);
  if (dm) title = dmTitle(dm);
  else {
    const chan = channelById(cid);
    if (chan) title = "#" + chan.name;
  }
  const who = msg.author.display_name || msg.author.username || "Someone";
  const body = (mentioned ? "@" + who + " mentioned you: " : who + ": ") +
    content.replace(/\s+/g, " ").slice(0, 140);
  try {
    if (typeof Notification !== "undefined") {
      if (Notification.permission === "granted") {
        new Notification(title, { body, tag: "dm-" + cid });
      } else if (Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
      }
    }
  } catch (e) {}
}

function bumpChannelUnreadFromServer(cid) {
  const dm = state.dms.find((d) => d.id === cid);
  if (dm) markRead("c" + cid);
}

function bumpServerUnreadForChannel(cid) {
  if (state.currentServer && serverChannels(state.currentServer).some((c) => c.id === cid)) {
    bumpUnread("s" + state.currentServer.id);
  }
}

function onMessageUpdate(data) {
  const msg = normMsg(data.message || data);
  if (!msg || !msg.id) return;
  const list = state.messages[msg.channel];
  if (!list) return;
  const idx = list.findIndex((m) => m.id === msg.id);
  if (idx >= 0) list[idx] = msg;
  if (state.currentChannel && state.currentChannel.id === msg.channel) renderMessages();
}

function onMessageDelete(data) {
  const cid = data.channel;
  const list = state.messages[cid];
  if (list) {
    const idx = list.findIndex((m) => m.id === data.id);
    if (idx >= 0) list.splice(idx, 1);
  }
  if (state.currentChannel && state.currentChannel.id === cid) renderMessages();
}

function onChannelEvent(kind, data) {
  const srv = state.serverCache[data.server_id];
  const chanId = data.channel ? data.channel.id : data.channel_id;
  if (srv) {
    const arr = srv.channels || (srv.channels = []);
    const idx = arr.findIndex((c) => c.id === chanId);
    if (kind === "create") { if (idx < 0) arr.push(data.channel); }
    else if (kind === "update") { if (idx >= 0) arr[idx] = Object.assign(arr[idx], data.channel); }
    else if (kind === "delete") {
      if (idx >= 0) arr.splice(idx, 1);
      delete state.messages[chanId];
      if (state.currentChannel && state.currentChannel.id === chanId) {
        navigate("/servers/" + data.server_id, true);
        toast("That channel was deleted.", "warn");
      }
    }
  }
  if (state.currentServer && state.currentServer.id === data.server_id) renderSidebar();
  if (kind === "create" && state.user && data.channel) toast(`#${data.channel.name} created`, "success");
}

function onServerUpdate(data) {
  if (!data.server) return;
  const idx = state.servers.findIndex((s) => s.id === data.server.id);
  if (idx >= 0) state.servers[idx] = Object.assign(state.servers[idx], data.server);
  if (state.currentServer && state.currentServer.id === data.server.id) {
    Object.assign(state.currentServer, data.server);
    renderSidebar();
  }
  renderRail();
}

function onMemberEvent(op, data) {
  const srv = state.serverCache[data.server_id];
  if (!srv) return;
  const members = srv.members || (srv.members = []);
  const idx = members.findIndex((m) => m.uid === data.uid);
  if (op === "member.join" && data.member) { if (idx < 0) members.push(data.member); }
  else if (op === "member.leave" && idx >= 0) members.splice(idx, 1);
  else if (op === "member.update" && idx >= 0) members[idx].roles = data.roles || [];
  if (state.currentServer && state.currentServer.id === data.server_id) renderMembers();
}

function onRoleEvent(op, data) {
  const srv = state.serverCache[data.server_id];
  if (!srv) return;
  const roles = srv.roles || (srv.roles = []);
  const role = data.role;
  const idx = role ? roles.findIndex((r) => r.id === role.id) : -1;
  if (op === "role.create" && role) { if (idx < 0) roles.push(role); }
  else if (op === "role.update" && role) { if (idx >= 0) roles[idx] = Object.assign(roles[idx], role); }
  else if (op === "role.delete") {
    const rid = role ? role.id : data.role_id;
    const ridx = roles.findIndex((r) => r.id === rid);
    if (ridx >= 0) roles.splice(ridx, 1);
    (srv.members || []).forEach((m) => {
      if (m.roles) m.roles = m.roles.filter((x) => x !== rid);
    });
  }
  if (state.currentServer && state.currentServer.id === data.server_id) {
    renderMembers();
    renderSidebar();
  }
}

function onDmCreate(data) {
  if (!data.channel) return;
  const idx = state.dms.findIndex((d) => d.id === data.channel.id);
  if (idx < 0) state.dms.unshift(data.channel);
  else state.dms[idx] = data.channel;
  markChannelFresh(data.channel);
  if (state.route.name === "home" || state.route.name === "sidebarOnly") renderSidebar();
  renderRail();
}

function onTyping(data) {
  const cid = data.channel;
  if (!cid) return;
  const bucket = state.typing[cid] || (state.typing[cid] = {});
  bucket[data.uid] = { name: data.display_name || data.username, ts: Date.now() };
  if (state.currentChannel && state.currentChannel.id === cid) renderTyping();
}

function renderTyping() {
  const box = $("#typing");
  if (!box || !state.currentChannel) return;
  const bucket = state.typing[state.currentChannel.id] || {};
  const now = Date.now();
  const names = [];
  for (const uid in bucket) {
    if (now - bucket[uid].ts > 4000) { delete bucket[uid]; continue; }
    if (state.user && Number(uid) === state.user.uid) continue;
    names.push(bucket[uid].name);
  }
  if (!names.length) { box.innerHTML = ""; return; }
  const label = names.length === 1 ? `${names[0]} is typing`
    : names.length === 2 ? `${names[0]} and ${names[1]} are typing`
    : "Several people are typing";
  box.innerHTML = `<span class="dots3"><i></i><i></i><i></i></span> ${esc(label)}…`;
}

function renderMembers() {
  const box = $("#members");
  if (!box) return;
  if (!state.currentServer || !state.currentChannel) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const srv = state.currentServer;
  const members = (srv.members || []).slice();
  const roles = (srv.roles || []).filter((r) => r.hoist && r.name !== "@everyone")
    .sort((a, b) => b.position - a.position);

  const assigned = new Set();
  const groups = roles.map((role) => ({
    label: role.name,
    color: role.color,
    items: members.filter((m) => (m.roles || []).includes(role.id) && !assigned.has(m.uid) && assigned.add(m.uid)),
  })).filter((g) => g.items.length);

  const rest = members.filter((m) => !assigned.has(m.uid));
  if (rest.length) groups.unshift({ label: "Online — others", color: "", items: rest });

  box.innerHTML = groups.map((g) => `
    <div class="member-group" style="${g.color ? "color:" + esc(g.color) : ""}">${esc(g.label)} — ${g.items.length}</div>
    ${g.items.map(memberRowHTML).join("")}`).join("")
    || `<div class="member-group">Members — ${members.length}</div>`;

  $$(".member-row", box).forEach((row) => {
    row.onclick = (ev) => {
      const uid = Number(row.dataset.uid);
      showProfilePopout(memberByUid(uid) || { uid }, ev.currentTarget);
    };
  });
}

function memberRowHTML(m) {
  const online = isOnline(m.uid);
  const roleColor = colorFor(m, m);
  return `<div class="member-row ${m.is_owner ? "owner" : ""}" data-uid="${m.uid}"
       style="${online ? "" : "opacity:.5"}">
    ${avatarHTML(Object.assign({}, m, { presence: state.presence[m.uid] || m.presence }), "mini-avatar", true)}
    <div style="min-width:0">
      <div class="m-name" style="${roleColor ? "color:" + esc(roleColor) : ""}">${esc(m.nickname || m.display_name || m.username)}${m.is_owner ? " ★" : ""}</div>
      ${(m.badges || []).length ? `<div class="m-sub">${esc(m.badges.slice(0, 3).map((b) => b.icon + " " + b.name).join(" · "))}</div>` : ""}
    </div>
  </div>`;
}

function updateMemberPresence(uid, presence) {
  state.presence[uid] = presence;
  const row = $(`#members .member-row[data-uid="${uid}"] .dot`);
  if (row) row.className = "dot small " + presence;
  const dmRows = $$(`[data-dm]`);
  dmRows.forEach((n) => {
    const dm = state.dms.find((d) => d.id === Number(n.dataset.dm));
    if (!dm) return;
    const dot = $(".dot", n);
    if (!dot) return;
    const others = (dm.members || []).filter((m) => m.uid !== (state.user && state.user.uid));
    const on = others.some((m) => isOnline(m.uid === uid ? Object.assign({}, m, { presence }) : m));
    dot.className = "dot small " + (on ? "online" : "offline");
  });
}

/* ---- navigation ---- */

async function openServer(sid, cid, voice) {
  const brief = findServer(sid);
  if (!brief) { toast("You're not in that server.", "error"); navigate("/", true); return; }
  state.route = { name: "server", serverId: sid };
  renderRail();

  let srv = state.serverCache[sid];
  if (!srv) {
    try {
      const data = await api("GET", `/api/dm/servers/${sid}`);
      srv = data.server;
      state.serverCache[sid] = srv;
    } catch (e) { navigate("/", true); return; }
  }
  state.currentServer = srv;
  state.servers = state.servers.map((s) => (s.id === sid ? Object.assign(s, srv) : s));

  let target = cid ? serverChannels(srv).find((c) => c.id === cid) : null;
  if (!target) {
    const pool = serverChannels(srv).filter((c) => (voice ? c.kind === "voice" : c.kind !== "voice")).sort(byPos);
    target = pool[0] || null;
    if (target && cid) navigate(`/servers/${sid}/${voice ? "voice" : "channels"}/${target.id}`, true);
  }
  state.currentChannel = target;
  markRead("c" + (target ? target.id : "x"));
  renderSidebar(); renderTopbar(); renderChatArea(); renderMembers();
  if (target) loadMessages(target.id);
  renderRail();
}

async function openDM(cid) {
  let dm = state.dms.find((d) => d.id === cid);
  if (!dm) {
    try {
      const data = await api("GET", `/api/dm/channels/${cid}/messages?limit=1`);
      dm = data.channel;
      state.dms.unshift(dm);
    } catch (e) { navigate("/", true); return; }
  }
  state.route = { name: "dm", channelId: cid };
  state.currentServer = null;
  state.currentChannel = dm;
  renderRail(); renderSidebar(); renderTopbar(); renderChatArea();
  markRead("c" + cid);
  loadMessages(cid);
}

async function loadMessages(cid) {
  if (state.loadedChannels[cid]) { renderMessages({ reset: true }); return; }
  try {
    const data = await api("GET", `/api/dm/channels/${cid}/messages?limit=50`);
    state.messages[cid] = (data.messages || []).map(normMsg);
    state.loadedChannels[cid] = true;
    if (state.currentChannel && state.currentChannel.id === cid) {
      if (!state.currentChannel.server_id && data.channel) {
        state.currentChannel = data.channel;
        const idx = state.dms.findIndex((d) => d.id === cid);
        if (idx >= 0) state.dms[idx] = data.channel;
        renderSidebar(); renderTopbar();
      }
      renderMessages({ reset: true });
    }
  } catch (e) { console.warn("loadMessages", e); }
}

async function loadOlderMessages() {
  const scroll = $("#msgScroll");
  if (!scroll || !state.currentChannel) return;
  if (scroll.scrollTop > 60) return;
  const cid = state.currentChannel.id;
  const list = state.messages[cid] || [];
  if (!list.length) return;
  const before = list[0].id;
  const heightBefore = scroll.scrollHeight;
  try {
    const data = await api("GET", `/api/dm/channels/${cid}/messages?before=${before}&limit=50`, null, { silent: true });
    const older = (data.messages || []).map(normMsg).filter((m) => !list.some((x) => x.id === m.id));
    if (!older.length) return;
    state.messages[cid] = older.concat(list);
    renderMessages();
    scroll.scrollTop = scroll.scrollHeight - heightBefore;
  } catch (e) {}
}

/* ================= 10. uploads ================= */

const MAX_UPLOAD = 10 * 1024 * 1024 * 1024;

function wireDropZone() {
  const wrap = $("#composerWrap");
  const overlay = $("#dropOverlay");
  if (!wrap) return;
  let depth = 0;
  wrap.addEventListener("dragenter", (ev) => {
    ev.preventDefault(); depth++;
    if (overlay) overlay.style.display = "flex";
  });
  wrap.addEventListener("dragover", (ev) => ev.preventDefault());
  wrap.addEventListener("dragleave", (ev) => {
    ev.preventDefault(); depth--;
    if (depth <= 0 && overlay) { depth = 0; overlay.style.display = "none"; }
  });
  wrap.addEventListener("drop", (ev) => {
    ev.preventDefault(); depth = 0;
    if (overlay) overlay.style.display = "none";
    Array.from(ev.dataTransfer.files || []).forEach(enqueueUpload);
  });
}

function enqueueUpload(file) {
  if (!file) return;
  if (file.size > MAX_UPLOAD) { toast(`${file.name}: max file size is 10 GB.`, "error"); return; }
  if (!file.size) { toast(`${file.name} is empty.`, "error"); return; }
  const item = {
    id: "u" + Date.now() + Math.random().toString(36).slice(2, 7),
    file, status: "queued", received: 0, attachment: null, abort: null, started: 0,
  };
  state.uploadQueue = state.uploadQueue || [];
  state.uploadQueue.push(item);
  renderUploads();
  runUpload(item);
}

function renderUploads() {
  const strip = $("#uploadStrip");
  if (!strip) return;
  const q = state.uploadQueue || [];
  if (!q.length) { strip.innerHTML = ""; return; }
  strip.innerHTML = q.map((u) => {
    const pct = u.file.size ? Math.floor((u.received / u.file.size) * 100) : 0;
    const elapsed = u.started ? (Date.now() - u.started) / 1000 : 0;
    const bps = elapsed > 0.4 ? u.received / elapsed : 0;
    const eta = bps > 0 ? Math.ceil((u.file.size - u.received) / bps) : 0;
    const label = u.status === "done" ? "Ready"
      : u.status === "error" ? "Failed"
      : u.status === "uploading" ? `${pct}% · ${fmtBytes(bps)}/s${eta ? " · " + fmtShortEta(eta) + " left" : ""}`
      : "Waiting…";
    return `<div class="upload-card" data-u="${u.id}">
      <span style="font-size:17px">${fileGlyph(u.file.name)}</span>
      <span class="u-name">${esc(u.file.name)}</span>
      <span class="u-stat">${fmtBytes(u.received)} / ${fmtBytes(u.file.size)}</span>
      <span class="u-bar"><i style="width:${u.status === "done" ? 100 : pct}%"></i></span>
      <span class="u-stat">${esc(label)}</span>
      <button class="u-x" data-cancel="${u.id}" title="Cancel upload" aria-label="Cancel upload">✕</button>
    </div>`;
  }).join("");
  $$("[data-cancel]", strip).forEach((btn) => {
    btn.onclick = () => cancelUpload(btn.dataset.cancel);
  });
}

function fmtShortEta(sec) {
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m " + (sec % 60) + "s";
  return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
}

async function runUpload(item) {
  item.status = "uploading";
  item.started = Date.now();
  item.abort = new AbortController();
  renderUploads();
  try {
    const init = await api("POST", "/api/dm/uploads", {
      filename: item.file.name, size: item.file.size,
      mime: item.file.type || "application/octet-stream",
    }, { silent: true });
    const chunk = init.chunk_size || 16777216;
    let offset = 0;
    while (offset < item.file.size) {
      if (item.cancelled) throw new Error("cancelled");
      const slice = item.file.slice(offset, offset + chunk);
      const blob = new Blob([slice], { type: "application/octet-stream" });
      await api("PUT", `/api/dm/uploads/${init.upload_id}?offset=${offset}&received=${offset + blob.size}`,
        blob, { silent: true });
      offset += blob.size;
      item.received = offset;
      if (offset % (chunk * 2) < blob.size || offset === item.file.size) renderUploads();
      else if (offset === item.file.size) renderUploads();
      if (Math.random() < 0.12 || offset === item.file.size) renderUploads();
    }
    const done = await api("POST", `/api/dm/uploads/${init.upload_id}/complete`, {}, { silent: true });
    item.attachment = done.attachment;
    item.status = "done";
    item.uploadId = init.upload_id;
    renderUploads();
    toast(`${item.file.name} uploaded.`, "success", 2500);
  } catch (err) {
    item.status = item.cancelled ? "cancelled" : "error";
    if (item.cancelled) {
      state.uploadQueue = state.uploadQueue.filter((u) => u !== item);
    } else if (err && err.message && err.message !== "cancelled") {
      toast(`${item.file.name}: ${err.message}`, "error");
    }
    renderUploads();
  }
}

function cancelUpload(id) {
  const item = (state.uploadQueue || []).find((u) => u.id === id);
  if (!item) return;
  item.cancelled = true;
  if (item.abort) { try { item.abort.abort(); } catch (e) {} }
  if (item.uploadId) api("DELETE", `/api/dm/uploads/${item.uploadId}`, undefined, { silent: true }).catch(() => {});
  state.uploadQueue = state.uploadQueue.filter((u) => u !== item);
  renderUploads();
}

/* ================= 11. popouts / menus ================= */

function closePopouts() { $$(".popout").forEach((n) => n.remove()); }

function openMenu(x, y, items) {
  closePopouts();
  const body = items.map((it, i) => {
    if (it.divider) return `<div style="height:1px;background:var(--line);margin:5px 8px"></div>`;
    const color = it.danger ? "var(--red)" : (it.color || "");
    return `<div class="nav-row" data-i="${i}" style="margin-bottom:0;${color ? "color:" + esc(color) : ""}">
      ${it.icon ? `<span class="hash">${esc(it.icon)}</span>` : ""}
      <span class="row-name">${esc(it.label)}</span></div>`;
  }).join("");
  const node = h(`<div class="popout" style="width:230px;padding:6px">${body}</div>`);
  document.body.appendChild(node);
  node.style.left = Math.max(8, Math.min(x, window.innerWidth - node.offsetWidth - 8)) + "px";
  node.style.top = Math.max(8, Math.min(y, window.innerHeight - node.offsetHeight - 8)) + "px";
  $$(".nav-row", node).forEach((row) => {
    row.onclick = () => {
      const it = items[Number(row.dataset.i)];
      closePopouts();
      if (it && it.onClick) it.onClick();
    };
  });
  setTimeout(() => {
    const onDoc = (ev) => {
      if (node.contains(ev.target)) return;
      closePopouts();
      document.removeEventListener("mousedown", onDoc);
    };
    document.addEventListener("mousedown", onDoc);
  }, 0);
  return node;
}

function anchorBox(anchor) {
  if (!anchor) return { x: window.innerWidth / 2 - 150, y: window.innerHeight / 2 - 100 };
  if (anchor.getBoundingClientRect) {
    const r = anchor.getBoundingClientRect();
    return { x: r.left, y: r.bottom + 6 };
  }
  return { x: anchor.clientX || 40, y: (anchor.clientY || 40) + 8 };
}

async function showProfilePopout(user, anchor) {
  closePopouts();
  const uid = Number(user && user.uid);
  const pos = anchorBox(anchor);
  const node = h(`<div class="popout"><div class="p-banner"></div>
    <div class="p-body">
      <div class="p-avatar" style="background:${avatarBg(user)}">${avatarInner(user)}</div>
      <div class="p-card">
        <div class="p-name">${esc(user.display_name || user.username || "…")}</div>
        <div class="p-handle">${esc(user.username ? "@" + user.username : "")}</div>
        <div class="p-badges"></div>
        <div class="p-section">About me</div>
        <div class="p-text" id="pBio">…</div>
        <div class="p-section">Servers in common</div>
        <div class="p-text" id="pMutual">…</div>
        <div class="p-actions" id="pActions"></div>
      </div>
    </div></div>`);
  document.body.appendChild(node);
  node.style.left = Math.max(8, Math.min(pos.x, window.innerWidth - node.offsetWidth - 8)) + "px";
  node.style.top = Math.max(8, Math.min(pos.y, window.innerHeight - node.offsetHeight - 8)) + "px";

  const badgeBox = $(".p-badges", node);
  const badges = (user.badges || []).length ? (user.badges || [])
    : ((state.user && uid === state.user.uid) ? (state.user.badges || []) : []);
  badgeBox.innerHTML = badges.length ? badges.map((b) =>
    `<span class="badge-pill ${b.slug === "founder" ? "owner" : ""}"
       style="${b.color && b.slug !== "founder" ? "border-color:" + esc(b.color) + ";color:" + esc(b.color) : ""}"
       title="${esc(b.description || b.name)}">${b.icon ? esc(b.icon) : ""} ${esc(b.name)}</span>`).join("")
    : `<span class="hint">No badges yet.</span>`;

  const actions = $("#pActions", node);
  const me = state.user || {};
  if (uid && uid !== me.uid) {
    const msgBtn = h(`<button class="btn sm">Message</button>`);
    msgBtn.onclick = async () => { closePopouts(); await startDM(uid); };
    actions.appendChild(msgBtn);
  }
  if (user.is_owner || (me && me.is_owner && uid === me.uid)) {
    actions.appendChild(h(`<span class="badge-pill owner">👑 Owner</span>`));
  }

  setTimeout(async () => {
    try {
      const data = await api("GET", `/api/dm/users/${uid}`, null, { silent: true });
      const bio = $("#pBio", node);
      if (bio) bio.textContent = data.user.bio || "No bio set.";
      const mutual = $("#pMutual", node);
      if (mutual) {
        mutual.textContent = (data.mutual_servers || []).length
          ? data.mutual_servers.map((s) => s.name).join(", ")
          : "None in common.";
      }
      if (data.user.badges && data.user.badges.length) {
        badgeBox.innerHTML = data.user.badges.map((b) =>
          `<span class="badge-pill ${b.slug === "founder" ? "owner" : ""}"
             style="${b.color && b.slug !== "founder" ? "border-color:" + esc(b.color) + ";color:" + esc(b.color) : ""}"
             title="${esc(b.description || b.name)}">${b.icon ? esc(b.icon) : ""} ${esc(b.name)}</span>`).join("");
      }
      if (state.currentServer && hasPerm(PERM.MANAGE_ROLES) && uid !== me.uid) {
        const rb = h(`<button class="btn sm secondary">Roles</button>`);
        rb.onclick = () => { closePopouts(); showMemberRolesModal(uid); };
        actions.appendChild(rb);
      }
      if (state.currentServer && hasPerm(PERM.KICK) && uid !== me.uid) {
        const kb = h(`<button class="btn sm danger">Kick</button>`);
        kb.onclick = () => {
          closePopouts();
          confirmModal("Kick member", "Remove this member from the server? They can rejoin with an invite.",
            "Kick", async () => {
              try {
                await api("DELETE", `/api/dm/servers/${state.currentServer.id}/members/${uid}`);
                toast("Member kicked.", "success");
              } catch (e) {}
            }, true);
        };
        actions.appendChild(kb);
      }
    } catch (e) {
      const bio = $("#pBio", node);
      if (bio) bio.textContent = "Profile unavailable.";
    }
  }, 0);

  setTimeout(() => {
    const onDoc = (ev) => {
      if (node.contains(ev.target)) return;
      closePopouts();
      document.removeEventListener("mousedown", onDoc);
    };
    document.addEventListener("mousedown", onDoc);
  }, 0);
}

async function startDM(uid) {
  try {
    const data = await api("POST", "/api/dm/dms", { members: [Number(uid)] });
    onDmCreate(data);
    navigate("/channels/" + data.channel.id);
  } catch (e) {}
}

function showDMInfoModal() {
  const dm = state.currentChannel;
  if (!dm || dm.server_id) return;
  const others = dm.members || [];
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>${esc(dmTitle(dm))}</h2>
        <p>${dm.kind === "group" ? "Group chat" : "Direct message"} · ${others.length + 1} member${others.length ? "s" : ""}</p></div>
      <div class="modal-body">
        <div class="modal-list">
          ${others.map((u) => `<div class="nav-row" data-uid="${u.uid}">
              ${avatarHTML(u, "mini-avatar", true)}
              <span class="row-name">${esc(u.display_name || u.username)}</span>
              <span class="hint">${esc(u.presence || state.presence[u.uid] || "offline")}</span>
            </div>`).join("") || `<div class="hint">Just you so far.</div>`}
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost left" data-close>Close</button>
        <button class="btn danger" data-leave>Close conversation</button>
      </div>
    </div>`);
  $$(".nav-row[data-uid]", m.node).forEach((row) => {
    row.onclick = (ev) => {
      const u = others.find((x) => x.uid === Number(row.dataset.uid));
      m.close(null);
      showProfilePopout(u || { uid: Number(row.dataset.uid) }, ev.currentTarget);
    };
  });
  $("[data-close]", m.node).onclick = () => m.close(null);
  $("[data-leave]", m.node).onclick = () => {
    confirmModal("Close conversation", "Leave this conversation? If you are the last member it is deleted forever.",
      "Close", async () => {
        try {
          await api("DELETE", `/api/dm/dms/${dm.id}`);
          state.dms = state.dms.filter((d) => d.id !== dm.id);
          delete state.messages[dm.id];
          m.close(null);
          navigate("/", true);
          renderSidebar();
          toast("Conversation closed.", "success");
        } catch (e) {}
      }, true);
  };
}

/* ================= 12. modals ================= */

function imageFromFile(file, maxDim) {
  return new Promise((resolve, reject) => {
    if (!file || !/^image\//.test(file.type || "")) { reject(new Error("Pick an image file.")); return; }
    if (file.size > 5 * 1024 * 1024) { reject(new Error("Image must be under 5 MB.")); return; }
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const max = maxDim || 256;
      let w = img.width, hh = img.height;
      if (w > max || hh > max) {
        const scale = Math.min(max / w, max / hh);
        w = Math.round(w * scale); hh = Math.round(hh * scale);
      }
      const cv = document.createElement("canvas");
      cv.width = w; cv.height = hh;
      cv.getContext("2d").drawImage(img, 0, 0, w, hh);
      URL.revokeObjectURL(url);
      resolve(cv.toDataURL("image/png"));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Couldn't read that image.")); };
    img.src = url;
  });
}

function imagePickerHTML(id, current, label) {
  const inner = current ? `<img src="${esc(safeURL(current))}" alt="">` : `<span>＋</span>`;
  return `<div class="avatar-pick">
    <div class="preview" id="${id}Preview">${inner}</div>
    <div style="display:flex;flex-direction:column;gap:7px">
      <button type="button" class="btn sm secondary" data-pick="${id}">${esc(label || "Upload image")}</button>
      <button type="button" class="btn sm ghost" data-clear="${id}">Remove</button>
      <span class="hint">PNG or JPG, up to 5 MB.</span>
    </div>
    <input type="file" accept="image/*" id="${id}File" hidden>
  </div>`;
}

function wireImagePicker(root, id, onValue) {
  const file = $(`#${id}File`, root);
  const preview = $(`#${id}Preview`, root);
  const pick = $(`[data-pick="${id}"]`, root);
  const clear = $(`[data-clear="${id}"]`, root);
  let value = null;
  if (pick) pick.onclick = () => file.click();
  if (file) file.onchange = async () => {
    const f = file.files && file.files[0];
    file.value = "";
    if (!f) return;
    try {
      value = await imageFromFile(f, id.indexOf("server") >= 0 ? 256 : 256);
      preview.innerHTML = `<img src="${value}" alt="">`;
      if (onValue) onValue(value);
    } catch (e) { toast(e.message, "error"); }
  };
  if (clear) clear.onclick = () => {
    value = "";
    preview.innerHTML = `<span>＋</span>`;
    if (onValue) onValue("");
  };
  return { get: () => value, reset: () => { value = null; } };
}

function showCreateServerModal() {
  let icon = null;
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Create a server</h2>
        <p>Your server is where you and your friends hang out. It's free — every feature is included.</p></div>
      <div class="modal-body">
        <div class="field"><label>Server icon</label>${imagePickerHTML("serverIcon", "", "Upload icon")}</div>
        <div class="field"><label>Server name</label>
          <input class="input" name="name" maxlength="64" placeholder="Friends' server" autofocus></div>
        <div class="field"><label>Description (optional)</label>
          <textarea class="input" name="desc" maxlength="500" placeholder="What is this server about?"></textarea></div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Create server</button>
      </div>
    </div>`);
  const pick = wireImagePicker(m.node, "serverIcon", (v) => { icon = v; });
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    const name = $('[name="name"]', m.node).value.trim();
    const err = $("[data-err]", m.node);
    if (!name) { err.textContent = "Give your server a name."; return; }
    const btn = $("[data-go]", m.node);
    btn.disabled = true;
    try {
      const data = await api("POST", "/api/dm/servers", {
        name, description: $('[name="desc"]', m.node).value.trim(), icon: icon || "",
      });
      const srv = data.server;
      state.servers.push(srv);
      state.serverCache[srv.id] = srv;
      m.close(null);
      navigate("/servers/" + srv.id);
      toast(`${srv.name} created.`, "success");
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
    }
  };
}

function showJoinServerModal(prefill) {
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Join a server</h2>
        <p>Paste an invite link, an invite code, or a vanity link like <code>dm.luaisgame.com/s/gaming</code>.</p></div>
      <div class="modal-body">
        <div class="field"><label>Invite</label>
          <input class="input" name="code" placeholder="abc12345" value="${esc(prefill || "")}" autofocus></div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Join server</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  const go = async () => {
    const raw = $('[name="code"]', m.node).value.trim();
    const err = $("[data-err]", m.node);
    const code = raw.replace(/^https?:\/\/[^/]+\//, "").replace(/^invite\//, "").replace(/^s\//, "").split("/").pop();
    if (!code) { err.textContent = "Enter an invite code or link."; return; }
    try {
      const data = await api("POST", "/api/dm/join", { code });
      const srv = data.server;
      if (!state.servers.some((s) => s.id === srv.id)) state.servers.push(srv);
      state.serverCache[srv.id] = srv;
      m.close(null);
      navigate("/servers/" + srv.id);
      toast(`Joined ${srv.name}.`, "success");
    } catch (e) { err.textContent = e.message; }
  };
  $("[data-go]", m.node).onclick = go;
  $('[name="code"]', m.node).onkeydown = (ev) => { if (ev.key === "Enter") go(); };
}

function showInviteModal(code, server) {
  if (!code) { toast("No invite link available for this server.", "warn"); return; }
  const link = location.origin + "/invite/" + code;
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Invite people</h2>
        <p>Share this link — anyone with it can join. Vanity links must be requested from the platform owner.</p></div>
      <div class="modal-body">
        <div class="field"><label>Invite link</label>
          <div style="display:flex;gap:8px">
            <input class="input" readonly value="${esc(link)}" id="inviteLink">
            <button class="btn" data-copy style="white-space:nowrap">Copy</button>
          </div>
        </div>
        <div data-preview></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Close</button>
        <button class="btn secondary" data-join hidden>Join server</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-copy]", m.node).onclick = () => copyText(link);
  const preview = $("[data-preview]", m.node);
  const joinBtn = $("[data-join]", m.node);

  const renderPreview = (brief) => {
    preview.innerHTML = `
      <div class="list-card" style="margin-top:6px">
        <span class="rail-avatar" style="width:42px;height:42px;font-size:15px">
          ${brief.icon ? `<img src="${esc(safeURL(brief.icon))}" alt="" onerror="this.remove()">` : esc(initials(brief.name))}</span>
        <div style="flex:1;min-width:0">
          <div class="lc-name">${esc(brief.name)} ${brief.vanity ? `<span class="chip approved">◆ ${esc(brief.vanity)}</span>` : ""}</div>
          <div class="lc-sub">${brief.members || 0} member${(brief.members || 0) === 1 ? "" : "s"} · ${brief.channels || 0} channels</div>
        </div>
      </div>
      ${brief.description ? `<div class="hint" style="margin-top:8px">${esc(brief.description)}</div>` : ""}`;
    joinBtn.hidden = false;
  };

  if (server && server.name) renderPreview(server);
  api("GET", `/api/dm/invite/${encodeURIComponent(code)}`, null, { silent: true })
    .then((data) => { if (data && data.server) renderPreview(data.server); })
    .catch(() => {
      preview.innerHTML = `<div class="hint">That invite doesn't exist (or expired).</div>`;
    });

  joinBtn.onclick = async () => {
    try {
      const data = await api("POST", "/api/dm/join", { code });
      const srv = data.server;
      if (!state.servers.some((s) => s.id === srv.id)) state.servers.push(srv);
      state.serverCache[srv.id] = srv;
      m.close(null);
      navigate("/servers/" + srv.id);
      toast(`Joined ${srv.name}.`, "success");
    } catch (e) {}
  };
}

function showCreateChannelModal(kind) {
  if (!state.currentServer) return;
  const srv = state.currentServer;
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Create channel</h2>
        <p>In <strong>${esc(srv.name)}</strong></p></div>
      <div class="modal-body">
        <div class="field"><label>Channel type</label>
          <select class="input" name="kind">
            <option value="text" ${kind === "voice" ? "" : "selected"}># Text — messaging, files, images</option>
            <option value="voice" ${kind === "voice" ? "selected" : ""}>🔊 Voice — talk and screenshare</option>
            <option value="announce">📢 Announcement</option>
          </select>
        </div>
        <div class="field"><label>Channel name</label>
          <input class="input" name="name" maxlength="64" placeholder="general" autofocus></div>
        <div class="field"><label>Topic (optional)</label>
          <input class="input" name="topic" maxlength="200" placeholder="What's this channel for?"></div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Create channel</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    const name = $('[name="name"]', m.node).value.trim().replace(/^#+/, "");
    if (!name) { $("[data-err]", m.node).textContent = "Channel needs a name."; return; }
    try {
      const data = await api("POST", `/api/dm/servers/${srv.id}/channels`, {
        name, kind: $('[name="kind"]', m.node).value,
        topic: $('[name="topic"]', m.node).value.trim(),
      });
      const arr = serverChannels(state.currentServer);
      if (!arr.some((c) => c.id === data.channel.id)) arr.push(data.channel);
      m.close(null);
      renderSidebar();
      toast(`#${data.channel.name} created.`, "success");
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
}

function showNewDMModal() {
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>New message</h2>
        <p>Find someone to talk to, or start a group chat with several people.</p></div>
      <div class="modal-body">
        <div class="field"><label>To</label>
          <input class="input" name="q" placeholder="Search people by name" autofocus autocomplete="off"></div>
        <div class="field" data-group hidden><label>Group chat name (optional)</label>
          <input class="input" name="gname" maxlength="64" placeholder="Weekend plans"></div>
        <div class="modal-list" data-results></div>
        <div class="hint" data-selected>No one selected yet.</div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Start conversation</button>
      </div>
    </div>`);
  const picked = new Map();
  const results = $("[data-results]", m.node);
  const sel = $("[data-selected]", m.node);
  const groupField = $("[data-group]", m.node);

  const paint = () => {
    sel.textContent = picked.size
      ? "Selected: " + Array.from(picked.values()).map((u) => u.display_name || u.username).join(", ")
      : "No one selected yet.";
    groupField.hidden = picked.size < 2;
  };

  const renderResults = (users) => {
    results.innerHTML = users.map((u) => `
      <div class="nav-row" data-pick="${u.uid}">
        ${avatarHTML(u, "mini-avatar", true)}
        <span class="row-name">${esc(u.display_name || u.username)}</span>
        <span class="hint">${esc(u.username)}</span>
      </div>`).join("") || `<div class="hint">No matches.</div>`;
    $$("[data-pick]", results).forEach((row) => {
      row.onclick = () => {
        const u = users.find((x) => x.uid === Number(row.dataset.pick));
        if (!u) return;
        picked.set(u.uid, u);
        row.classList.add("active");
        paint();
      };
    });
  };

  const search = debounce(async () => {
    const q = $('[name="q"]', m.node).value.trim();
    if (!q) { results.innerHTML = ""; return; }
    try {
      const data = await api("GET", "/api/dm/directory?q=" + encodeURIComponent(q), null, { silent: true });
      renderResults((data.users || []).filter((u) => u.uid !== (state.user && state.user.uid)));
    } catch (e) {}
  }, 220);

  $('[name="q"]', m.node).addEventListener("input", search);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    if (!picked.size) { $("[data-err]", m.node).textContent = "Pick at least one person."; return; }
    try {
      const data = await api("POST", "/api/dm/dms", {
        members: Array.from(picked.keys()),
        group: picked.size > 1,
        name: $('[name="gname"]', m.node).value.trim(),
      });
      onDmCreate(data);
      m.close(null);
      navigate("/channels/" + data.channel.id);
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
  paint();
}

function showServerMenu(ev, srv) {
  const x = (ev && ev.clientX) || 40;
  const y = ((ev && ev.clientY) || 40) + 10;
  const isAdmin = hasPerm(PERM.ADMIN);
  const isOwner = srv.owner_id === (state.user && state.user.uid);
  const items = [];
  if (isAdmin) {
    items.push({ label: "Server settings", icon: "⚙", onClick: () => { state.settingsServerId = srv.id; navigate("/settings/server-overview"); } });
    items.push({ label: "Create channel", icon: "+", onClick: () => showCreateChannelModal("text") });
    items.push({ label: "Invite people", icon: "＠", onClick: () => showInviteModal(srv.invite, srv) });
    items.push({ label: "Request vanity link", icon: "◆", onClick: () => showVanityModal(srv) });
  } else {
    items.push({ label: "Invite people", icon: "＠", onClick: () => showInviteModal(srv.invite, srv) });
    items.push({ label: "Server settings (view)", icon: "⚙", onClick: () => { state.settingsServerId = srv.id; navigate("/settings/server-overview"); } });
  }
  items.push({ divider: true });
  items.push({ label: "Copy invite link", icon: "⧉", onClick: () => copyText(location.origin + "/invite/" + srv.invite) });
  items.push({ divider: true });
  if (!isOwner) {
    items.push({ label: "Leave server", icon: "⎋", danger: true, onClick: () => {
      confirmModal("Leave server", `Leave ${srv.name}? You'll need a new invite to come back.`, "Leave", async () => {
        try {
          await api("POST", `/api/dm/servers/${srv.id}/leave`);
          state.servers = state.servers.filter((s) => s.id !== srv.id);
          delete state.serverCache[srv.id];
          if (state.currentServer && state.currentServer.id === srv.id) navigate("/", true);
          renderRail(); renderSidebar();
          toast("You left the server.", "success");
        } catch (e) {}
      }, true);
    } });
  }
  if (isOwner || (state.user && state.user.is_owner)) {
    items.push({ label: "Delete server", icon: "🗑", danger: true, onClick: () => {
      confirmModal("Delete server", `Delete ${srv.name} permanently? Every channel, message and role is destroyed.`,
        "Delete forever", async () => {
          try {
            await api("DELETE", `/api/dm/servers/${srv.id}`);
            state.servers = state.servers.filter((s) => s.id !== srv.id);
            delete state.serverCache[srv.id];
            navigate("/", true);
            renderRail(); renderSidebar();
            toast("Server deleted.", "success");
          } catch (e) {}
        }, true);
    } });
  }
  openMenu(x, y, items);
}

function showDMMenu(ev, cid) {
  const x = (ev && ev.clientX) || 40;
  const y = ((ev && ev.clientY) || 40) + 8;
  const dm = state.dms.find((d) => d.id === cid);
  openMenu(x, y, [
    { label: "Open", icon: "→", onClick: () => navigate("/channels/" + cid) },
    { label: "Conversation info", icon: "ⓘ", onClick: () => { navigate("/channels/" + cid); setTimeout(showDMInfoModal, 120); } },
    { label: "Start call", icon: "📹", onClick: () => { navigate("/channels/" + cid); setTimeout(() => joinRoom("d" + cid), 160); } },
    { divider: true },
    { label: "Close conversation", icon: "✕", danger: true, onClick: async () => {
      if (!dm) return;
      try {
        await api("DELETE", `/api/dm/dms/${cid}`);
        state.dms = state.dms.filter((d) => d.id !== cid);
        delete state.messages[cid];
        if (state.currentChannel && state.currentChannel.id === cid) navigate("/", true);
        renderRail(); renderSidebar();
        toast("Conversation closed.", "success");
      } catch (e) {}
    } },
  ]);
}

function showVanityModal(srv) {
  const mine = state.servers.filter((s) => (s.my_permissions || s.permissions || 0) & PERM.ADMIN);
  const list = mine.length ? mine : (srv ? [srv] : []);
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Request a vanity link</h2>
        <p>Vanity links are hand-approved by the platform owner — nobody can reserve one on their own.
           You'll be notified the moment it's decided.</p></div>
      <div class="modal-body">
        <div class="field"><label>Server</label>
          <select class="input" name="srv">
            ${list.map((s) => `<option value="${s.id}" ${srv && srv.id === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}
          </select></div>
        <div class="field"><label>Wanted slug</label>
          <input class="input" name="slug" placeholder="my-community" maxlength="32" autofocus>
          <span class="hint">dm.luaisgame.com/s/<strong data-preview>${esc((srv && srv.vanity) || "your-slug")}</strong> · lowercase letters, numbers and dashes.</span>
        </div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Send request</button>
      </div>
    </div>`);
  const slugInput = $('[name="slug"]', m.node);
  slugInput.addEventListener("input", () => {
    $("[data-preview]", m.node).textContent = slugInput.value.toLowerCase() || "your-slug";
  });
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    const sid = Number($('[name="srv"]', m.node).value);
    const slug = slugInput.value.trim().toLowerCase();
    try {
      const data = await api("POST", `/api/dm/servers/${sid}/vanity`, { slug });
      m.close(null);
      toast(data.message || "Request sent to the owner.", "success", 7000);
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
}

/* ================= 13. settings ================= */

const SETTINGS_SECTIONS = {};

function exitSettings() {
  if (state.currentChannel && !state.currentServer) navigate("/channels/" + state.currentChannel.id);
  else if (state.currentServer) navigate("/servers/" + state.currentServer.id);
  else navigate("/");
}

function settingsSidebarHTML() {
  const me = state.user || {};
  const sid = state.settingsServerId || (state.currentServer && state.currentServer.id) || 0;
  const srv = sid ? (state.serverCache[sid] || findServer(sid)) : null;
  const cur = state.route.section;
  const row = (id, label, icon) => `<div class="nav-row ${cur === id ? "selected" : ""}" data-section="${id}">
      <span class="hash">${icon}</span><span class="row-name">${esc(label)}</span></div>`;
  return `<nav class="settings-nav">
    <div class="grp">User settings</div>
    ${row("profile", "My profile", "👤")}
    ${row("account", "Account", "🔐")}
    ${row("connections", "Connections", "🔗")}
    ${row("appearance", "Appearance", "🎨")}
    ${row("badges", "Badges", "🏅")}
    ${row("vanity", "Vanity links", "◆")}
    ${srv ? `<div class="grp">Server — ${esc(srv.name)}</div>
      ${row("server-overview", "Overview", "🏠")}
      ${row("server-channels", "Channels", "#")}
      ${row("server-roles", "Roles", "🎭")}
      ${row("server-members", "Members", "👥")}` : ""}
    ${me.is_owner ? `<div class="grp">Owner tools</div>
      ${row("admin-stats", "Dashboard", "📊")}
      ${row("admin-vanity", "Vanity queue", "◆")}
      ${row("admin-badges", "Badge studio", "🏅")}
      ${row("admin-users", "Users", "🛡")}` : ""}
    <div class="grp">&nbsp;</div>
    <div class="nav-row danger" data-logout>
      <span class="hash">⎋</span><span class="row-name">Log Out</span></div>
  </nav>`;
}

function wireSettingsSidebar() {
  $$("[data-section]").forEach((row) => {
    row.onclick = () => navigate("/settings/" + row.dataset.section);
  });
  const lo = $("[data-logout]");
  if (lo) lo.onclick = () => confirmModal("Log out", "Log out of DM on this device?", "Log out", logout, true);
}

function renderSettings(section) {
  state.shellMounted = false;
  closeAllModals();
  closePopouts();
  const app = $("#app");
  app.innerHTML = `
    <div class="settings-layout">
      ${settingsSidebarHTML()}
      <main class="settings-pane" id="settingsPane"></main>
      <div class="settings-close">
        <button class="circ" id="settingsClose" title="Close (Esc)" aria-label="Close settings">✕</button>
      </div>
    </div>`;
  wireSettingsSidebar();
  $("#settingsClose").onclick = exitSettings;
  const pane = $("#settingsPane");
  const render = SETTINGS_SECTIONS[section]
    || (state.user && state.user.is_owner ? null : null)
    || SETTINGS_SECTIONS.profile;
  if (!SETTINGS_SECTIONS[section] && section.indexOf("admin") === 0) {
    pane.innerHTML = `<h2>Not available</h2><p class="lead">Those tools are owner only.</p>`;
    return;
  }
  render(pane);
}

function sectionHead(title, lead) {
  return `<h2>${esc(title)}</h2><p class="lead">${esc(lead || "")}</p>`;
}

/* ---- profile ---- */

SETTINGS_SECTIONS.profile = function (pane) {
  const me = state.user || {};
  pane.innerHTML = `
    ${sectionHead("My profile", "How you show up across dm, mc and storage.luaisgame.com.")}
    <div class="settings-block">
      <h4>Avatar</h4>
      ${imagePickerHTML("pAvatar", safeURL(me.avatar), "Upload avatar")}
    </div>
    <div class="settings-block">
      <h4>Profile info</h4>
      <div class="field"><label>Display name</label>
        <input class="input" name="display_name" maxlength="64" value="${esc(me.display_name || me.username || "")}"></div>
      <div class="field"><label>Username</label>
        <input class="input" value="${esc(me.username || "")}" readonly>
        <span class="hint">Your username is unique and can't be changed here.</span></div>
      <div class="field"><label>Custom status</label>
        <input class="input" name="custom_status" maxlength="128" placeholder="Playing something great"
               value="${esc(me.custom_status || "")}"></div>
      <div class="field"><label>About me</label>
        <textarea class="input" name="bio" maxlength="400" placeholder="Tell people about yourself">${esc(me.bio || "")}</textarea></div>
      <div class="field"><label>Status</label>
        <select class="input" name="presence">
          ${["online", "idle", "dnd", "invisible"].map((p) =>
            `<option value="${p}" ${(me.presence || "online") === p ? "selected" : ""}>${p}</option>`).join("")}
        </select></div>
      <div class="error-text" data-err></div>
      <button class="btn" data-save>Save changes</button>
    </div>
    ${(me.badges || []).length ? `<div class="settings-block"><h4>Badges</h4>
      <div class="p-badges">${me.badges.map((b) =>
        `<span class="badge-pill ${b.slug === "founder" ? "owner" : ""}"
           style="${b.color && b.slug !== "founder" ? "border-color:" + esc(b.color) + ";color:" + esc(b.color) : ""}">${b.icon ? esc(b.icon) : ""} ${esc(b.name)}</span>`).join("")}
      </div></div>` : ""}`;

  let avatar = null;
  const pick = wireImagePicker(pane, "pAvatar", (v) => { avatar = v; });
  $("[data-save]", pane).onclick = async () => {
    const payload = {
      display_name: $('[name="display_name"]', pane).value.trim(),
      custom_status: $('[name="custom_status"]', pane).value.trim(),
      bio: $('[name="bio"]', pane).value,
      presence: $('[name="presence"]', pane).value,
    };
    if (avatar) payload.avatar = avatar;
    try {
      const data = await api("POST", "/api/dm/me", payload);
      state.user = data.user;
      avatar = null;
      renderRail(); renderSidebar();
      toast("Profile saved.", "success");
      renderSettings("profile");
    } catch (e) { $("[data-err]", pane).textContent = e.message; }
  };
};

/* ---- account ---- */

SETTINGS_SECTIONS.account = function (pane) {
  const me = state.user || {};
  const oauth = !!me.discord_id;
  pane.innerHTML = `
    ${sectionHead("Account", "Your login works on dm, mc and storage.luaisgame.com — one account everywhere.")}
    <div class="settings-block">
      <h4>Login</h4>
      <div class="row-line"><div><div class="rl-label">Email</div>
        <div class="rl-sub">${esc(me.email || "not set")}</div></div></div>
      <div class="row-line"><div><div class="rl-label">Password</div>
        <div class="rl-sub">${me.password_hash || me.has_password ? "Set — you can change it below" : "Not set (Discord sign-in only)"}</div></div>
        <button class="btn sm secondary" data-chpw>Change</button></div>
      <div class="row-line"><div><div class="rl-label">Discord</div>
        <div class="rl-sub">${oauth ? "Linked as " + esc(me.discord_name || me.discord_id) : "Not linked"}</div></div>
        ${oauth ? `<button class="btn sm ghost" data-unlink>Unlink</button>` : ""}</div>
      <div class="row-line"><div><div class="rl-label">Sessions</div>
        <div class="rl-sub">Your session lives on the server, so a restart never signs you out.</div></div></div>
    </div>
    <div class="settings-block">
      <h4>Change password</h4>
      <div class="field"><label>Current password</label><input class="input" type="password" name="current" autocomplete="current-password"></div>
      <div class="field"><label>New password</label><input class="input" type="password" name="new" minlength="8" autocomplete="new-password"></div>
      <div class="error-text" data-err></div>
      <button class="btn" data-savepw>Update password</button>
    </div>
    <div class="settings-block">
      <h4>Sign out</h4>
      <div class="row-line"><div><div class="rl-label">Log out of this device</div>
        <div class="rl-sub">You can sign back in any time with email or Discord.</div></div>
        <button class="btn sm danger" data-logout>Log out</button></div>
    </div>`;

  $("[data-chpw]", pane).onclick = () => $('[name="current"]', pane).focus();
  $("[data-savepw]", pane).onclick = async () => {
    try {
      await api("POST", "/api/dm/me/password", {
        current: $('[name="current"]', pane).value,
        new: $('[name="new"]', pane).value,
      });
      $('[name="current"]', pane).value = "";
      $('[name="new"]', pane).value = "";
      toast("Password updated.", "success");
    } catch (e) { $("[data-err]", pane).textContent = e.message; }
  };
  const un = $("[data-unlink]", pane);
  if (un) un.onclick = () => confirmModal("Unlink Discord",
    "You'll keep your account but can no longer sign in with Discord. Make sure you know your password.",
    "Unlink", async () => {
      try { await api("POST", "/api/dm/auth/discord/unlink", {}); state.user.discord_id = ""; toast("Discord unlinked.", "success"); renderSettings("account"); }
      catch (e) {}
    }, true);
  $("[data-logout]", pane).onclick = () => confirmModal("Log out", "Log out of DM on this device?", "Log out", logout, true);
};

/* ---- connections ---- */

SETTINGS_SECTIONS.connections = function (pane) {
  const me = state.user || {};
  const linked = !!me.discord_id;
  pane.innerHTML = `
    ${sectionHead("Connections", "Link the accounts you use — everything stays free.")}
    <div class="settings-block">
      <h4>Discord</h4>
      <div class="row-line">
        <div><div class="rl-label">${linked ? esc(me.discord_name || "Linked") : "Not connected"}</div>
          <div class="rl-sub">${linked
            ? "Sign in with Discord and your owner badge follows this account."
            : "Link Discord to sign in with it, and to receive your owner badge if this is your account."}</div></div>
        ${linked
          ? `<button class="btn sm ghost" data-unlink>Unlink</button>`
          : `<a class="btn sm discord" style="width:auto;text-decoration:none" href="/api/dm/auth/discord?link=1&next=/settings/connections">Connect Discord</a>`}
      </div>
    </div>
    <div class="settings-block">
      <h4>Other sites</h4>
      <div class="row-line"><div><div class="rl-label">mc.luaisgame.com</div>
        <div class="rl-sub">Same account, same session cookie.</div></div>
        <a class="btn sm ghost" href="https://mc.luaisgame.com" target="_blank" rel="noopener">Open</a></div>
      <div class="row-line"><div><div class="rl-label">storage.luaisgame.com</div>
        <div class="rl-sub">Same account, same session cookie.</div></div>
        <a class="btn sm ghost" href="https://storage.luaisgame.com" target="_blank" rel="noopener">Open</a></div>
    </div>`;
  const un = $("[data-unlink]", pane);
  if (un) un.onclick = () => confirmModal("Unlink Discord",
    "You'll keep your account but can no longer sign in with Discord. Make sure you know your password.",
    "Unlink", async () => {
      try { await api("POST", "/api/dm/auth/discord/unlink", {}); state.user.discord_id = ""; toast("Discord unlinked.", "success"); renderSettings("connections"); }
      catch (e) {}
    }, true);
};

/* ---- appearance ---- */

const ACCENTS = ["#5865f2", "#23a55a", "#f0b232", "#f23f43", "#eb459e", "#00a8fc", "#9b59b6", "#e67e22"];

function applyAccent(hex) {
  try {
    document.documentElement.style.setProperty("--accent", hex);
    document.documentElement.style.setProperty("--accent-hover", shade(hex, -0.22));
    localStorage.setItem("dm.accent", hex);
  } catch (e) {}
}

function shade(hex, amount) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || "");
  if (!m) return hex;
  const ch = (i) => Math.max(0, Math.min(255, Math.round(parseInt(m[i], 16) * (1 + amount))));
  return "#" + [ch(1), ch(2), ch(3)].map((n) => n.toString(16).padStart(2, "0")).join("");
}

try {
  const saved = localStorage.getItem("dm.accent");
  if (saved) applyAccent(saved);
} catch (e) {}

SETTINGS_SECTIONS.appearance = function (pane) {
  const current = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#5865f2";
  pane.innerHTML = `
    ${sectionHead("Appearance", "Make it yours — colour choices are stored on this device only.")}
    <div class="settings-block">
      <h4>Accent colour</h4>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${ACCENTS.map((c) => `<button class="color-dot" data-accent="${c}" title="${c}"
           style="width:34px;height:34px;background:${c};${c.toLowerCase() === current.toLowerCase() ? "box-shadow:0 0 0 3px var(--bg-0),0 0 0 5px #fff" : ""}"></button>`).join("")}
      </div>
      <div class="row-line" style="margin-top:14px">
        <div><div class="rl-label">Show member list</div>
          <div class="rl-sub">Toggle the right-hand member panel in servers.</div></div>
        <label class="switch"><input type="checkbox" data-members ${$("#members") && !$("#members").classList.contains("hidden") ? "checked" : ""}><i></i></label>
      </div>
      <div class="row-line">
        <div><div class="rl-label">Accent hex</div><div class="rl-sub">Any #rrggbb value.</div></div>
        <input class="input" name="hex" value="${esc(current)}" style="width:130px" maxlength="7">
      </div>
      <button class="btn sm" data-applyhex>Apply</button>
    </div>
    <div class="settings-block">
      <h4>Storage</h4>
      <div class="row-line"><div><div class="rl-label">Local read markers</div>
        <div class="rl-sub">Unread counts are remembered in this browser so they survive restarts.</div></div>
        <button class="btn sm ghost" data-clearlocal>Reset</button></div>
    </div>`;

  $$("[data-accent]", pane).forEach((b) => {
    b.onclick = () => { applyAccent(b.dataset.accent); renderSettings("appearance"); toast("Accent updated.", "success", 2000); };
  });
  $("[data-applyhex]", pane).onclick = () => {
    const v = $('[name="hex"]', pane).value.trim();
    if (!/^#[0-9a-fA-F]{6}$/.test(v)) { toast("Use a #rrggbb colour.", "error"); return; }
    applyAccent(v); renderSettings("appearance"); toast("Accent updated.", "success", 2000);
  };
  const mb = $("[data-members]", pane);
  if (mb) mb.onchange = () => {
    const box = $("#members");
    if (box) box.classList.toggle("hidden", !mb.checked);
  };
  $("[data-clearlocal]", pane).onclick = () => {
    state.lastRead = {}; state.unread = {};
    saveLastRead(); renderRail(); renderSidebar();
    toast("Local read markers cleared.", "success");
  };
};

/* ---- badges (mine) ---- */

SETTINGS_SECTIONS.badges = async function (pane) {
  pane.innerHTML = `${sectionHead("Badges", "Badges show up next to your name. They're all free — nobody pays for one.")}
    <div class="settings-block" data-loading>Loading…</div>`;
  try {
    const data = await api("GET", "/api/dm/badges");
    const mine = new Set((data.mine || []).map((b) => b.id));
    const owned = (data.badges || []).filter((b) => mine.has(b.id));
    const rest = (data.badges || []).filter((b) => !mine.has(b.id));
    pane.innerHTML = `
      ${sectionHead("Badges", "Badges show up next to your name. They're all free — nobody pays for one.")}
      <div class="settings-block">
        <h4>Earned — ${owned.length}</h4>
        ${owned.length ? `<div class="p-badges">${owned.map((b) => `
          <span class="badge-pill ${b.slug === "founder" ? "owner" : ""}"
            style="${b.color && b.slug !== "founder" ? "border-color:" + esc(b.color) + ";color:" + esc(b.color) : ""}"
            title="${esc(b.description || "")}">${esc(b.icon || "⭐")} ${esc(b.name)}</span>`).join("")}</div>`
          : `<div class="hint">Nothing yet — hang around, the owner hands these out.</div>`}
      </div>
      <div class="settings-block">
        <h4>All badges on the platform</h4>
        ${(rest.length ? rest : data.badges || []).map((b) => `
          <div class="list-card">
            <span class="role-icon-thumb" style="font-size:15px">${esc(b.icon || "⭐")}</span>
            <div style="flex:1;min-width:0">
              <div class="lc-name" style="color:${esc(b.color || "var(--text-strong)")}">${esc(b.name)}</div>
              <div class="lc-sub">${esc(b.description || "No description.")}</div>
            </div>
            ${mine.has(b.id) ? `<span class="chip approved">Owned</span>` : ""}
          </div>`).join("")}
      </div>`;
  } catch (e) {
    pane.innerHTML = `${sectionHead("Badges")}<div class="settings-block"><div class="hint">Couldn't load badges.</div></div>`;
  }
};

/* ---- vanity ---- */

SETTINGS_SECTIONS.vanity = async function (pane) {
  pane.innerHTML = `${sectionHead("Vanity links", "Only the platform owner creates and approves vanity links. Request one and you'll be notified.")}
    <div class="settings-block">
      <h4>Request a link</h4>
      <div class="field"><label>Server</label>
        <select class="input" name="srv">
          ${state.servers.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("") || `<option value="">No servers yet</option>`}
        </select></div>
      <div class="field"><label>Slug</label>
        <input class="input" name="slug" maxlength="32" placeholder="my-community"></div>
      <div class="error-text" data-err></div>
      <button class="btn" data-req>Send to the owner</button>
    </div>
    <div class="settings-block">
      <h4>Your requests</h4>
      <div data-list>Loading…</div>
    </div>`;

  const list = $("[data-list]", pane);
  const paint = async () => {
    try {
      const data = await api("GET", "/api/dm/vanity-requests", null, { silent: true });
      const rows = data.requests || [];
      list.innerHTML = rows.length ? rows.map((r) => `
        <div class="list-card">
          <span class="color-dot" style="background:${r.status === "approved" ? "var(--green)" : r.status === "denied" ? "var(--red)" : "var(--yellow)"}"></span>
          <div style="flex:1;min-width:0">
            <div class="lc-name">dm.luaisgame.com/s/${esc(r.slug)}</div>
            <div class="lc-sub">${esc(r.server_name || "server")} · ${esc(relTime(r.created_at * 1000))}</div>
          </div>
          <span class="chip ${esc(r.status)}">${esc(r.status)}</span>
          ${r.status === "approved" ? `<button class="btn sm ghost" data-copy="dm.luaisgame.com/s/${esc(r.slug)}">Copy</button>` : ""}
        </div>`).join("") : `<div class="hint">No requests yet.</div>`;
      $$("[data-copy]", list).forEach((b) => { b.onclick = () => copyText("https://" + b.dataset.copy); });
    } catch (e) { list.innerHTML = `<div class="hint">Couldn't load requests.</div>`; }
  };
  paint();

  $("[data-req]", pane).onclick = async () => {
    const sid = Number($('[name="srv"]', pane).value);
    const slug = $('[name="slug"]', pane).value.trim().toLowerCase();
    if (!sid) { $("[data-err]", pane).textContent = "Create a server first."; return; }
    try {
      const data = await api("POST", `/api/dm/servers/${sid}/vanity`, { slug });
      $('[name="slug"]', pane).value = "";
      $("[data-err]", pane).textContent = "";
      toast(data.message || "Sent to the owner.", "success", 7000);
      paint();
    } catch (e) { $("[data-err]", pane).textContent = e.message; }
  };
};

/* ---- server settings ---- */

function currentSettingsServer() {
  const sid = state.settingsServerId || (state.currentServer && state.currentServer.id) || 0;
  return state.serverCache[sid] || (state.currentServer && state.currentServer.id === sid ? state.currentServer : null);
}

async function ensureSettingsServer(pane) {
  let srv = currentSettingsServer();
  if (srv) return srv;
  const sid = state.settingsServerId;
  if (!sid) return null;
  try {
    const data = await api("GET", `/api/dm/servers/${sid}`);
    srv = data.server;
    state.serverCache[sid] = srv;
  } catch (e) { return null; }
  return srv;
}

function reloadServer(sid) {
  delete state.serverCache[sid];
  return api("GET", `/api/dm/servers/${sid}`).then((data) => {
    state.serverCache[sid] = data.server;
    if (state.currentServer && state.currentServer.id === sid) state.currentServer = data.server;
    renderSidebar();
    return data.server;
  });
}

SETTINGS_SECTIONS["server-overview"] = async function (pane) {
  pane.innerHTML = `${sectionHead("Server overview")}<div class="settings-block">Loading…</div>`;
  const srv = await ensureSettingsServer(pane);
  if (!srv) { pane.innerHTML = `${sectionHead("Server overview", "No server selected.")}`; return; }
  const canEdit = !!((srv.my_permissions !== undefined ? srv.my_permissions : srv.permissions) & PERM.ADMIN);
  const isOwner = srv.owner_id === (state.user && state.user.uid);
  let icon = null;
  pane.innerHTML = `
    ${sectionHead("Server overview", "Everything about " + srv.name + ".")}
    <div class="settings-block">
      <h4>Server icon</h4>
      ${imagePickerHTML("srvIcon", safeURL(srv.icon), "Upload icon")}
    </div>
    <div class="settings-block">
      <h4>Information</h4>
      <div class="field"><label>Name</label>
        <input class="input" name="name" maxlength="64" value="${esc(srv.name)}" ${canEdit ? "" : "readonly"}></div>
      <div class="field"><label>Description</label>
        <textarea class="input" name="desc" maxlength="500" ${canEdit ? "" : "readonly"}>${esc(srv.description || "")}</textarea></div>
      <div class="error-text" data-err></div>
      ${canEdit ? `<button class="btn" data-save>Save server</button>` : `<div class="hint">You need Administrator to edit this.</div>`}
    </div>
    <div class="settings-block">
      <h4>Invite &amp; links</h4>
      <div class="row-line"><div><div class="rl-label">Invite link</div>
        <div class="rl-sub">Anyone with this link can join.</div></div>
        <button class="btn sm secondary" data-invite>Copy invite</button></div>
      <div class="row-line"><div><div class="rl-label">Vanity link</div>
        <div class="rl-sub">${srv.vanity ? "dm.luaisgame.com/s/" + esc(srv.vanity) : "Not set — vanity links are approved by the platform owner."}</div></div>
        <button class="btn sm ghost" data-vanity>${srv.vanity ? "Copy" : "Request"}</button></div>
      <div class="row-line"><div><div class="rl-label">Members</div>
        <div class="rl-sub">${srv.members || 0} total · ${srv.channels || 0} channels</div></div></div>
    </div>
    <div class="settings-block">
      <h4>Danger zone</h4>
      ${!isOwner ? `<div class="row-line"><div><div class="rl-label">Leave server</div>
          <div class="rl-sub">You can come back with a new invite.</div></div>
          <button class="btn sm danger" data-leave>Leave</button></div>` : ""}
      ${isOwner ? `<div class="row-line"><div><div class="rl-label">Delete server</div>
          <div class="rl-sub">Destroys every channel, message, role and invite.</div></div>
          <button class="btn sm danger" data-del>Delete forever</button></div>` : ""}
    </div>`;

  wireImagePicker(pane, "srvIcon", (v) => { icon = v; });
  const save = $("[data-save]", pane);
  if (save) save.onclick = async () => {
    try {
      await api("PATCH", `/api/dm/servers/${srv.id}`, {
        name: $('[name="name"]', pane).value.trim(),
        description: $('[name="desc"]', pane).value,
        icon: icon || "",
      });
      await reloadServer(srv.id);
      const brief = state.servers.find((s) => s.id === srv.id);
      if (brief) Object.assign(brief, { name: $('[name="name"]', pane).value.trim() });
      renderRail();
      toast("Server updated.", "success");
    } catch (e) { $("[data-err]", pane).textContent = e.message; }
  };
  $("[data-invite]", pane).onclick = () => copyText(location.origin + "/invite/" + srv.invite);
  const van = $("[data-vanity]", pane);
  van.onclick = () => {
    if (srv.vanity) copyText(location.origin + "/s/" + srv.vanity);
    else showVanityModal(srv);
  };
  const leave = $("[data-leave]", pane);
  if (leave) leave.onclick = () => confirmModal("Leave server", `Leave ${srv.name}?`, "Leave", async () => {
    try {
      await api("POST", `/api/dm/servers/${srv.id}/leave`);
      state.servers = state.servers.filter((s) => s.id !== srv.id);
      delete state.serverCache[srv.id];
      navigate("/", true); renderRail(); renderSidebar();
      toast("You left the server.", "success");
    } catch (e) {}
  }, true);
  const del = $("[data-del]", pane);
  if (del) del.onclick = () => confirmModal("Delete server", `Delete ${srv.name} permanently?`, "Delete forever", async () => {
    try {
      await api("DELETE", `/api/dm/servers/${srv.id}`);
      state.servers = state.servers.filter((s) => s.id !== srv.id);
      delete state.serverCache[srv.id];
      navigate("/", true); renderRail(); renderSidebar();
      toast("Server deleted.", "success");
    } catch (e) {}
  }, true);
};

SETTINGS_SECTIONS["server-channels"] = async function (pane) {
  pane.innerHTML = `${sectionHead("Channels")}<div class="settings-block">Loading…</div>`;
  const srv = await ensureSettingsServer(pane);
  if (!srv) { pane.innerHTML = `${sectionHead("Channels", "No server selected.")}`; return; }
  const canManage = !!((srv.my_permissions !== undefined ? srv.my_permissions : srv.permissions) & PERM.MANAGE_CHANNELS);
  const paint = () => {
    const chans = (srv.channels || []).slice().sort(byPos);
    pane.innerHTML = `
      ${sectionHead("Channels", srv.name)}
      <div class="settings-block">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h4 style="margin:0">${chans.length} channel${chans.length === 1 ? "" : "s"}</h4>
          ${canManage ? `<button class="btn sm" data-add>Create channel</button>` : ""}
        </div>
        ${chans.map((c) => `
          <div class="list-card" data-cid="${c.id}">
            <span class="role-icon-thumb">${c.kind === "voice" ? "🔊" : c.kind === "announce" ? "📢" : "#"}</span>
            <div style="flex:1;min-width:0">
              <div class="lc-name">${esc(c.name)}</div>
              <div class="lc-sub">${esc(c.kind)}${c.topic ? " · " + esc(c.topic) : ""}</div>
            </div>
            ${canManage ? `<div class="lc-actions">
              <button class="btn sm ghost" data-edit="${c.id}">Edit</button>
              <button class="btn sm danger" data-del="${c.id}">Delete</button></div>` : ""}
          </div>`).join("") || `<div class="hint">No channels.</div>`}
      </div>`;
    const add = $("[data-add]", pane);
    if (add) add.onclick = () => showCreateChannelModal("text");
    $$("[data-edit]", pane).forEach((b) => {
      b.onclick = () => {
        const c = srv.channels.find((x) => x.id === Number(b.dataset.edit));
        if (c) showChannelEditModal(srv, c, () => reloadServer(srv.id).then(paint));
      };
    });
    $$("[data-del]", pane).forEach((b) => {
      b.onclick = () => {
        const c = srv.channels.find((x) => x.id === Number(b.dataset.del));
        if (!c) return;
        confirmModal("Delete channel", `Delete #${c.name} and all its messages?`, "Delete", async () => {
          try {
            await api("DELETE", `/api/dm/channels/${c.id}`);
            await reloadServer(srv.id);
            paint();
            toast("Channel deleted.", "success");
          } catch (e) {}
        }, true);
      };
    });
  };
  paint();
};

function showChannelEditModal(srv, channel, after) {
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Edit channel</h2></div>
      <div class="modal-body">
        <div class="field"><label>Name</label>
          <input class="input" name="name" maxlength="64" value="${esc(channel.name)}" autofocus></div>
        <div class="field"><label>Type</label>
          <select class="input" name="kind">
            ${["text", "voice", "announce"].map((k) => `<option value="${k}" ${channel.kind === k ? "selected" : ""}>${k}</option>`).join("")}
          </select></div>
        <div class="field"><label>Topic</label>
          <input class="input" name="topic" maxlength="200" value="${esc(channel.topic || "")}"></div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Save</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    try {
      await api("PATCH", `/api/dm/channels/${channel.id}`, {
        name: $('[name="name"]', m.node).value.trim(),
        kind: $('[name="kind"]', m.node).value,
        topic: $('[name="topic"]', m.node).value.trim(),
      });
      m.close(null);
      if (after) after();
      toast("Channel updated.", "success");
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
}

function permGridHTML(perms) {
  return `<div class="perm-grid">${PERM_NAMES.map(([bit, label, desc]) => `
    <label title="${esc(desc)}"><input type="checkbox" data-perm="${bit}" ${(perms & bit) ? "checked" : ""}> ${esc(label)}</label>`).join("")}</div>`;
}

function readPermGrid(root) {
  let perms = 0;
  $$("[data-perm]", root).forEach((cb) => { if (cb.checked) perms |= Number(cb.dataset.perm); });
  return perms;
}

function showRoleModal(srv, role, after) {
  const isNew = !role;
  const perms = role ? (role.permissions || 0) : 0;
  let icon = null;
  const m = openModal(`
    <div class="modal wide">
      <div class="modal-head"><h2>${isNew ? "Create role" : "Edit role"}</h2>
        <p>Roles can have a colour, an image and permissions — all included, no paid tier.</p></div>
      <div class="modal-body">
        <div class="field"><label>Role image</label>
          ${imagePickerHTML("roleIcon", role ? safeURL(role.icon) : "", "Upload role image")}</div>
        <div class="field"><label>Role name</label>
          <input class="input" name="name" maxlength="48" value="${esc(role ? role.name : "")}" placeholder="Mods" autofocus></div>
        <div class="field"><label>Colour</label>
          <div style="display:flex;gap:9px;align-items:center">
            <input type="color" name="color" value="${/^#[0-9a-fA-F]{6}$/.test(role && role.color || "") ? role.color : "#5865f2"}"
                   style="width:52px;height:36px;background:var(--bg-0);border:1px solid var(--line);border-radius:6px;padding:2px">
            <span class="hint">Shown on the member list and in messages.</span>
          </div></div>
        <div class="row-line">
          <div><div class="rl-label">Hoist</div>
            <div class="rl-sub">Show members with this role in their own group.</div></div>
          <label class="switch"><input type="checkbox" name="hoist" ${!role || role.hoist ? "checked" : ""}><i></i></label>
        </div>
        <div class="field" style="margin-top:14px"><label>Permissions</label>${permGridHTML(perms)}</div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        ${!isNew && role.name !== "@everyone" ? `<button class="btn danger left" data-del>Delete role</button>` : ""}
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>${isNew ? "Create role" : "Save role"}</button>
      </div>
    </div>`);
  wireImagePicker(m.node, "roleIcon", (v) => { icon = v; });
  $("[data-x]", m.node).onclick = () => m.close(null);
  const del = $("[data-del]", m.node);
  if (del) del.onclick = () => {
    confirmModal("Delete role", `Delete the ${role.name} role? Members keep their other roles.`,
      "Delete", async () => {
        try {
          await api("DELETE", `/api/dm/roles/${role.id}`);
          m.close(null);
          if (after) after();
          toast("Role deleted.", "success");
        } catch (e) {}
      }, true);
  };
  $("[data-go]", m.node).onclick = async () => {
    const payload = {
      name: $('[name="name"]', m.node).value.trim(),
      color: $('[name="color"]', m.node).value,
      hoist: $('[name="hoist"]', m.node).checked,
      permissions: readPermGrid(m.node),
    };
    if (icon) payload.icon = icon;
    try {
      if (isNew) await api("POST", `/api/dm/servers/${srv.id}/roles`, payload);
      else await api("PATCH", `/api/dm/roles/${role.id}`, payload);
      m.close(null);
      if (after) after();
      toast(isNew ? "Role created." : "Role saved.", "success");
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
}

SETTINGS_SECTIONS["server-roles"] = async function (pane) {
  pane.innerHTML = `${sectionHead("Roles")}<div class="settings-block">Loading…</div>`;
  const srv = await ensureSettingsServer(pane);
  if (!srv) { pane.innerHTML = `${sectionHead("Roles", "No server selected.")}`; return; }
  const canManage = !!((srv.my_permissions !== undefined ? srv.my_permissions : srv.permissions) & PERM.MANAGE_ROLES);
  const paint = () => {
    const roles = (srv.roles || []).slice().sort((a, b) => b.position - a.position);
    pane.innerHTML = `
      ${sectionHead("Roles", "Colour, image and permissions for every role in " + srv.name + ".")}
      <div class="settings-block">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h4 style="margin:0">${roles.length} role${roles.length === 1 ? "" : "s"}</h4>
          ${canManage ? `<button class="btn sm" data-add>Create role</button>` : ""}
        </div>
        ${roles.map((r) => `
          <div class="list-card" style="border-color:${esc(r.color || "transparent")}33">
            <span class="color-dot" style="background:${esc(r.color || "#99aab5")}"></span>
            <span class="role-icon-thumb">${r.icon ? `<img src="${esc(safeURL(r.icon))}" alt="" onerror="this.remove()">` : (r.name === "@everyone" ? "@" : esc(initials(r.name)))}</span>
            <div style="flex:1;min-width:0">
              <div class="lc-name" style="color:${esc(r.color || "var(--text-strong)")}">${esc(r.name)}</div>
              <div class="lc-sub">${r.permissions ? (PERM_NAMES.filter(([b]) => r.permissions & b).map(([, l]) => l).join(", ") || "some permissions") : "No special permissions"}${r.hoist ? " · hoisted" : ""}</div>
            </div>
            ${canManage ? `<div class="lc-actions"><button class="btn sm ghost" data-edit="${r.id}">Edit</button></div>` : ""}
          </div>`).join("") || `<div class="hint">No roles yet.</div>`}
      </div>`;
    const add = $("[data-add]", pane);
    if (add) add.onclick = () => showRoleModal(srv, null, () => reloadServer(srv.id).then(paint));
    $$("[data-edit]", pane).forEach((b) => {
      b.onclick = () => {
        const r = srv.roles.find((x) => x.id === Number(b.dataset.edit));
        if (r) showRoleModal(srv, r, () => reloadServer(srv.id).then(paint));
      };
    });
  };
  paint();
};

function showMemberRolesModal(uid) {
  const srv = state.currentServer;
  if (!srv) return;
  const member = (srv.members || []).find((m) => m.uid === uid);
  if (!member) return;
  const roles = (srv.roles || []).filter((r) => r.name !== "@everyone");
  const m = openModal(`
    <div class="modal">
      <div class="modal-head"><h2>Roles for ${esc(member.nickname || member.display_name || member.username)}</h2>
        <p>Highest colour and hoisted group win automatically.</p></div>
      <div class="modal-body">
        <div class="modal-list">
          ${roles.map((r) => `
            <label class="nav-row" style="cursor:pointer">
              <input type="checkbox" data-role="${r.id}" ${(member.roles || []).includes(r.id) ? "checked" : ""}>
              <span class="color-dot" style="background:${esc(r.color || "#99aab5")}"></span>
              <span class="row-name" style="color:${esc(r.color || "var(--text)")}">${esc(r.name)}</span>
            </label>`).join("") || `<div class="hint">No roles to assign yet — create one first.</div>`}
        </div>
        <div class="error-text" data-err></div>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-x>Cancel</button>
        <button class="btn" data-go>Save roles</button>
      </div>
    </div>`);
  $("[data-x]", m.node).onclick = () => m.close(null);
  $("[data-go]", m.node).onclick = async () => {
    const role_ids = $$("[data-role]", m.node).filter((c) => c.checked).map((c) => Number(c.dataset.role));
    try {
      const data = await api("PUT", `/api/dm/servers/${srv.id}/members/${uid}/roles`, { role_ids });
      member.roles = data.roles;
      renderMembers();
      m.close(null);
      toast("Roles updated.", "success");
    } catch (e) { $("[data-err]", m.node).textContent = e.message; }
  };
}

SETTINGS_SECTIONS["server-members"] = async function (pane) {
  pane.innerHTML = `${sectionHead("Members")}<div class="settings-block">Loading…</div>`;
  const srv = await ensureSettingsServer(pane);
  if (!srv) { pane.innerHTML = `${sectionHead("Members", "No server selected.")}`; return; }
  const myPerms = srv.my_permissions !== undefined ? srv.my_permissions : (srv.permissions || 0);
  const canRoles = !!(myPerms & PERM.MANAGE_ROLES);
  const canKick = !!(myPerms & PERM.KICK);
  const paint = () => {
    const members = srv.members || [];
    pane.innerHTML = `
      ${sectionHead("Members", members.length + " member" + (members.length === 1 ? "" : "s") + " in " + srv.name + ".")}
      <div class="settings-block">
        ${members.map((mem) => `
          <div class="list-card">
            ${avatarHTML(mem, "mini-avatar", true)}
            <div style="flex:1;min-width:0">
              <div class="lc-name" style="${mem.is_owner ? "color:var(--yellow)" : ""}">${esc(mem.nickname || mem.display_name || mem.username)}${mem.is_owner ? " 👑" : ""}</div>
              <div class="lc-sub">${(mem.roles || []).length
                ? esc((mem.roles || []).map((rid) => { const r = (srv.roles || []).find((x) => x.id === rid); return r ? r.name : ""; }).filter(Boolean).join(", "))
                : "No roles"}</div>
            </div>
            <div class="lc-actions">
              ${canRoles && mem.uid !== (state.user && state.user.uid) ? `<button class="btn sm ghost" data-roles="${mem.uid}">Roles</button>` : ""}
              ${canKick && !mem.is_owner && mem.uid !== (state.user && state.user.uid) ? `<button class="btn sm danger" data-kick="${mem.uid}">Kick</button>` : ""}
            </div>
          </div>`).join("")}
      </div>`;
    $$("[data-roles]", pane).forEach((b) => { b.onclick = () => showMemberRolesModal(Number(b.dataset.roles)); });
    $$("[data-kick]", pane).forEach((b) => {
      b.onclick = () => {
        const uid = Number(b.dataset.kick);
        const mem = members.find((x) => x.uid === uid);
        confirmModal("Kick member", `Remove ${mem ? (mem.display_name || mem.username) : "this member"} from ${srv.name}?`,
          "Kick", async () => {
            try {
              await api("DELETE", `/api/dm/servers/${srv.id}/members/${uid}`);
              await reloadServer(srv.id);
              paint();
              toast("Member kicked.", "success");
            } catch (e) {}
          }, true);
      };
    });
  };
  paint();
};

/* ---- owner admin ---- */

SETTINGS_SECTIONS["admin-stats"] = async function (pane) {
  pane.innerHTML = `${sectionHead("Owner dashboard", "Live numbers for the whole platform.")}
    <div class="settings-block"><div class="stat-grid" data-grid>Loading…</div></div>`;
  const grid = $("[data-grid]", pane);
  try {
    const s = await api("GET", "/api/dm/admin/stats");
    const cards = [
      [s.users, "Users"], [s.online, "Online now"], [s.servers, "Servers"], [s.messages, "Messages"],
      [s.sessions, "Live sessions"], [s.calls, "Active calls"], [s.pending_vanity, "Pending vanity"],
      [fmtBytes(s.uploads_bytes), "Storage used"],
    ];
    grid.innerHTML = cards.map(([v, l]) => `<div class="stat-card"><div class="sv">${esc(v)}</div><div class="sl">${esc(l)}</div></div>`).join("");
  } catch (e) { grid.innerHTML = `<div class="hint">Couldn't load stats.</div>`; }
};

SETTINGS_SECTIONS["admin-vanity"] = function (pane) {
  pane.innerHTML = `${sectionHead("Vanity queue", "Every vanity link must be requested and approved by you. Nobody can reserve one alone.")}
    <div class="settings-block" data-list>Loading…</div>`;
  const list = $("[data-list]", pane);
  const load = async () => {
    try {
      const data = await api("GET", "/api/dm/admin/vanity?status=pending");
      const rows = data.requests || [];
      list.innerHTML = rows.length ? rows.map((r) => `
        <div class="list-card" data-rid="${r.id}">
          <span class="color-dot" style="background:var(--yellow)"></span>
          <div style="flex:1;min-width:0">
            <div class="lc-name">/s/${esc(r.slug)}</div>
            <div class="lc-sub">${esc(r.server_name || "?")} · requested by ${esc(r.requester_name || r.requester)} · ${esc(relTime(r.created_at * 1000))}</div>
          </div>
          <input class="input" data-slug="${r.id}" placeholder="override slug" value="${esc(r.slug)}" style="width:150px">
          <div class="lc-actions">
            <button class="btn sm" data-ok="${r.id}">Approve</button>
            <button class="btn sm danger" data-no="${r.id}">Deny</button>
          </div>
        </div>`).join("") : `<div class="hint">Queue is empty 🎉</div>`;
      $$("[data-ok]", list).forEach((b) => {
        b.onclick = async () => {
          const rid = Number(b.dataset.ok);
          const slug = $(`[data-slug="${rid}"]`, list).value.trim().toLowerCase();
          try {
            await api("POST", `/api/dm/admin/vanity/${rid}`, { action: "approve", slug });
            toast(`Approved: /s/${slug}`, "success"); load();
          } catch (e) {}
        };
      });
      $$("[data-no]", list).forEach((b) => {
        b.onclick = async () => {
          const rid = Number(b.dataset.no);
          try {
            await api("POST", `/api/dm/admin/vanity/${rid}`, { action: "deny" });
            toast("Request denied.", "warn"); load();
          } catch (e) {}
        };
      });
    } catch (e) { list.innerHTML = `<div class="hint">Couldn't load the queue.</div>`; }
  };
  load();
};

SETTINGS_SECTIONS["admin-badges"] = function (pane) {
  pane.innerHTML = `
    ${sectionHead("Badge studio", "Create badges and hand them out. The Founder badge is seeded automatically.")}
    <div class="settings-block">
      <h4>Create a badge</h4>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="field"><label>Name</label><input class="input" name="bname" maxlength="48" placeholder="Speedrunner"></div>
        <div class="field"><label>Slug (optional)</label><input class="input" name="bslug" maxlength="48" placeholder="speedrunner"></div>
        <div class="field"><label>Icon (emoji)</label><input class="input" name="bicon" maxlength="8" placeholder="🏅"></div>
        <div class="field"><label>Colour</label><input type="color" name="bcolor" value="#5865f2"
          style="height:40px;background:var(--bg-0);border:1px solid var(--line);border-radius:6px;padding:3px"></div>
      </div>
      <div class="field"><label>Description</label><input class="input" name="bdesc" maxlength="200" placeholder="Shown on hover"></div>
      <div class="error-text" data-err></div>
      <button class="btn" data-create>Create badge</button>
    </div>
    <div class="settings-block">
      <h4>All badges</h4>
      <div data-list>Loading…</div>
    </div>
    <div class="settings-block">
      <h4>Grant a badge</h4>
      <div style="display:flex;gap:9px;flex-wrap:wrap">
        <select class="input" data-gbadge style="flex:1;min-width:160px"></select>
        <input class="input" data-guid placeholder="user id" style="width:120px">
        <button class="btn" data-grant>Grant</button>
      </div>
      <div class="hint" style="margin-top:7px">Find a user id in the Users section below.</div>
      <div class="error-text" data-gerr></div>
    </div>`;
  const list = $("[data-list]", pane);
  const sel = $("[data-gbadge]", pane);
  const load = async () => {
    try {
      const data = await api("GET", "/api/dm/badges");
      const badges = data.badges || [];
      list.innerHTML = badges.map((b) => `
        <div class="list-card">
          <span class="role-icon-thumb" style="font-size:15px">${esc(b.icon || "⭐")}</span>
          <div style="flex:1;min-width:0">
            <div class="lc-name" style="color:${esc(b.color || "var(--text-strong)")}">${esc(b.name)}</div>
            <div class="lc-sub">/${esc(b.slug)} · ${esc(b.description || "no description")}</div>
          </div>
          ${b.slug === "founder" ? `<span class="chip approved">protected</span>`
            : `<button class="btn sm danger" data-del="${b.id}">Delete</button>`}
        </div>`).join("") || `<div class="hint">No badges yet.</div>`;
      sel.innerHTML = badges.map((b) => `<option value="${b.id}">${esc(b.icon || "⭐")} ${esc(b.name)}</option>`).join("");
      $$("[data-del]", list).forEach((btn) => {
        btn.onclick = () => confirmModal("Delete badge", "Remove this badge from everyone who has it?", "Delete", async () => {
          try { await api("DELETE", `/api/dm/badges/${btn.dataset.del}`); load(); toast("Badge deleted.", "success"); }
          catch (e) {}
        }, true);
      });
    } catch (e) { list.innerHTML = `<div class="hint">Couldn't load badges.</div>`; }
  };
  load();
  $("[data-create]", pane).onclick = async () => {
    try {
      await api("POST", "/api/dm/badges", {
        name: $('[name="bname"]', pane).value.trim(),
        slug: $('[name="bslug"]', pane).value.trim(),
        icon: $('[name="bicon"]', pane).value.trim() || "⭐",
        color: $('[name="bcolor"]', pane).value,
        description: $('[name="bdesc"]', pane).value.trim(),
      });
      $('[name="bname"]', pane).value = "";
      $('[name="bslug"]', pane).value = "";
      $('[name="bdesc"]', pane).value = "";
      $("[data-err]", pane).textContent = "";
      toast("Badge created.", "success");
      load();
    } catch (e) { $("[data-err]", pane).textContent = e.message; }
  };
  $("[data-grant]", pane).onclick = async () => {
    const bid = Number(sel.value);
    const uid = Number($("[data-guid]", pane).value);
    if (!bid || !uid) { $("[data-gerr]", pane).textContent = "Pick a badge and a user id."; return; }
    try {
      await api("POST", `/api/dm/badges/${bid}/grant`, { uid });
      $("[data-gerr]", pane).textContent = "";
      toast("Badge granted.", "success");
    } catch (e) { $("[data-gerr]", pane).textContent = e.message; }
  };
};

SETTINGS_SECTIONS["admin-users"] = function (pane) {
  pane.innerHTML = `
    ${sectionHead("Users", "Search accounts, ban them, promote them or grant badges.")}
    <div class="settings-block">
      <div style="display:flex;gap:9px">
        <input class="input" name="q" placeholder="username, display name, email or Discord id" autofocus>
        <button class="btn" data-search>Search</button>
      </div>
      <div class="error-text" data-err></div>
      <div data-list style="margin-top:12px"><div class="hint">Type a name to search.</div></div>
    </div>`;
  const list = $("[data-list]", pane);
  const load = async () => {
    const q = $('[name="q"]', pane).value.trim();
    try {
      const data = await api("GET", "/api/dm/admin/users" + (q ? "?q=" + encodeURIComponent(q) : ""));
      const users = data.users || [];
      const badges = (await api("GET", "/api/dm/badges", null, { silent: true }).catch(() => ({ badges: [] }))).badges || [];
      list.innerHTML = `<table class="table">
        <thead><tr><th>User</th><th>Email</th><th>Flags</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody>${users.map((u) => `
          <tr data-uid="${u.uid}">
            <td><div style="display:flex;align-items:center;gap:8px">
              ${avatarHTML(u, "mini-avatar", false)}
              <div><div style="font-weight:600">${esc(u.display_name || u.username)}</div>
                <div class="hint">@${esc(u.username)} · id ${u.uid}</div></div></div></td>
            <td class="hint">${esc(u.email || "—")}</td>
            <td>
              ${u.is_owner ? `<span class="chip pending">owner</span> ` : ""}
              ${u.discord_id ? `<span class="chip approved">discord</span> ` : ""}
              ${u.banned ? `<span class="chip denied">banned</span>` : ""}
            </td>
            <td style="text-align:right;white-space:nowrap">
              <select class="input" data-gsel="${u.uid}" style="width:130px;display:inline-block;padding:5px 7px">
                <option value="">grant badge…</option>
                ${badges.map((b) => `<option value="${esc(b.slug)}">${esc(b.name)}</option>`).join("")}
              </select>
              <button class="btn sm ghost" data-give="${u.uid}">Give</button>
              <button class="btn sm ${u.banned ? "" : "danger"}" data-ban="${u.uid}">${u.banned ? "Unban" : "Ban"}</button>
              <button class="btn sm ghost" data-own="${u.uid}">${u.is_owner ? "Demote" : "Make owner"}</button>
            </td>
          </tr>`).join("")}</tbody></table>` || `<div class="hint">No users found.</div>`;

      $$("[data-ban]", list).forEach((b) => {
        b.onclick = async () => {
          const uid = Number(b.dataset.ban);
          const user = users.find((x) => x.uid === uid);
          const banning = !(user && user.banned);
          confirmModal(banning ? "Ban user" : "Unban user",
            banning ? `Ban ${user ? user.username : uid}? Their sessions are destroyed immediately.` : `Unban ${user ? user.username : uid}?`,
            banning ? "Ban" : "Unban", async () => {
              try {
                await api("POST", `/api/dm/admin/users/${uid}`, { banned: banning });
                toast(banning ? "User banned." : "User unbanned.", "success");
                load();
              } catch (e) {}
            }, banning);
        };
      });
      $$("[data-own]", list).forEach((b) => {
        b.onclick = async () => {
          const uid = Number(b.dataset.own);
          const user = users.find((x) => x.uid === uid);
          const promote = !(user && user.is_owner);
          confirmModal(promote ? "Make owner" : "Remove owner",
            `${promote ? "Give" : "Remove"} platform-owner powers for ${user ? user.username : uid}?`,
            promote ? "Make owner" : "Remove", async () => {
              try {
                await api("POST", `/api/dm/admin/users/${uid}`, { is_owner: promote });
                toast("Updated.", "success");
                load();
              } catch (e) {}
            }, !promote);
        };
      });
      $$("[data-give]", list).forEach((b) => {
        b.onclick = async () => {
          const uid = Number(b.dataset.give);
          const slug = $(`[data-gsel="${uid}"]`, list).value;
          if (!slug) { toast("Pick a badge first.", "warn"); return; }
          try {
            await api("POST", `/api/dm/admin/users/${uid}`, { grant_badge: slug });
            toast("Badge granted.", "success");
          } catch (e) {}
        };
      });
    } catch (e) { list.innerHTML = `<div class="hint">Couldn't load users.</div>`; }
  };
  $("[data-search]", pane).onclick = load;
  $('[name="q"]', pane).onkeydown = (ev) => { if (ev.key === "Enter") load(); };
};

/* ================= 14. quick switcher + global keys ================= */

function switcherIndex() {
  const items = [];
  for (const dm of state.dms) {
    items.push({ kind: "dm", label: dmTitle(dm), sub: "conversation",
      go: () => navigate("/channels/" + dm.id) });
  }
  for (const s of state.servers) {
    items.push({ kind: "server", label: s.name, sub: s.vanity ? "/s/" + s.vanity : "server",
      go: () => navigate("/servers/" + s.id) });
    for (const c of (state.serverCache[s.id] && state.serverCache[s.id].channels) || []) {
      items.push({ kind: c.kind === "voice" ? "voice" : "channel", label: c.name, sub: s.name,
        go: () => navigate(`/servers/${s.id}/${c.kind === "voice" ? "voice" : "channels"}/${c.id}`) });
    }
  }
  if (state.currentServer) {
    for (const c of serverChannels(state.currentServer)) {
      if (!items.some((i) => i.kind !== "server" && i.label === c.name && i.sub === state.currentServer.name)) {
        items.push({ kind: c.kind === "voice" ? "voice" : "channel", label: c.name, sub: state.currentServer.name,
          go: () => navigate(`/servers/${state.currentServer.id}/${c.kind === "voice" ? "voice" : "channels"}/${c.id}`) });
      }
    }
  }
  items.push({ kind: "you", label: "User settings", sub: "settings", go: () => navigate("/settings/profile") });
  if (state.user && state.user.is_owner) {
    items.push({ kind: "you", label: "Owner dashboard", sub: "settings", go: () => navigate("/settings/admin-stats") });
    items.push({ kind: "you", label: "Badge studio", sub: "settings", go: () => navigate("/settings/admin-badges") });
  }
  return items;
}

function showFindModal() {
  if (modalStack.length) return;
  const m = openModal(`
    <div class="modal">
      <div class="modal-head" style="padding-bottom:10px">
        <input class="input" id="qsInput" placeholder="Jump to a channel, server or person…" autofocus autocomplete="off">
      </div>
      <div class="modal-body qs-list" id="qsList"></div>
      <div class="modal-foot" style="padding-top:0">
        <span class="hint left">↑↓ to move · Enter to open · Esc to close</span>
      </div>
    </div>`);
  const input = $("#qsInput", m.node);
  const list = $("#qsList", m.node);
  let results = switcherIndex();
  let sel = 0;
  let remote = [];

  const paint = () => {
    const all = results.concat(remote);
    if (!all.length) {
      list.innerHTML = `<div class="hint" style="padding:10px">Nothing found. Try another word.</div>`;
      return;
    }
    if (sel >= all.length) sel = all.length - 1;
    if (sel < 0) sel = 0;
    list.innerHTML = all.map((it, i) => `
      <div class="qs-row ${i === sel ? "sel" : ""}" data-i="${i}">
        <span class="qs-kind">${esc(it.kind)}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)}</span>
        <span class="hint">${esc(it.sub || "")}</span>
      </div>`).join("");
    $$(".qs-row", list).forEach((row) => {
      row.onclick = () => { const it = all[Number(row.dataset.i)]; if (it) { m.close(null); it.go(); } };
      row.onmouseenter = () => { sel = Number(row.dataset.i); paint(); };
    });
    const active = $(".qs-row.sel", list);
    if (active) active.scrollIntoView({ block: "nearest" });
  };

  const searchRemote = debounce(async () => {
    const q = input.value.trim();
    remote = [];
    if (q.length < 2) { paint(); return; }
    try {
      const data = await api("GET", "/api/dm/directory?q=" + encodeURIComponent(q), null, { silent: true });
      remote = (data.users || []).filter((u) => u.uid !== (state.user && state.user.uid)).map((u) => ({
        kind: "person", label: u.display_name || u.username, sub: "@" + u.username,
        go: () => startDM(u.uid),
      })).concat((data.servers || []).filter((s) => !state.servers.some((x) => x.id === s.id)).map((s) => ({
        kind: "server", label: s.name, sub: "join",
        go: () => showInviteModal(s.invite, s),
      })));
      paint();
    } catch (e) { paint(); }
  }, 240);

  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    results = switcherIndex().filter((it) =>
      !q || it.label.toLowerCase().indexOf(q) >= 0 || String(it.sub || "").toLowerCase().indexOf(q) >= 0);
    sel = 0;
    paint();
    searchRemote();
  });
  input.addEventListener("keydown", (ev) => {
    const total = results.length + remote.length;
    if (ev.key === "ArrowDown") { ev.preventDefault(); sel = Math.min(sel + 1, total - 1); paint(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); sel = Math.max(sel - 1, 0); paint(); }
    else if (ev.key === "Enter") {
      ev.preventDefault();
      const it = results.concat(remote)[sel];
      if (it) { m.close(null); it.go(); }
    }
  });
  paint();
}

function initGlobalKeys() {
  if (initGlobalKeys.done) return;
  initGlobalKeys.done = true;

  document.addEventListener("keydown", (ev) => {
    const inField = /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target && ev.target.tagName) || "");
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "k" || ev.key === "p" || ev.key === "K" || ev.key === "P")) {
      ev.preventDefault();
      showFindModal();
      return;
    }
    if (ev.key === "Escape") {
      if (modalStack.length) return;
      if ($$(".popout").length) { closePopouts(); return; }
      if (state.route.name === "settings") exitSettings();
    }
    if (ev.key === "/" && !inField && !modalStack.length && state.user) {
      ev.preventDefault();
      showFindModal();
    }
  });

  window.addEventListener("popstate", () => { if (state.user) handleRoute(); });

  // infinite scroll upward in the message list
  const onScroll = debounce((ev) => {
    const t = ev.target;
    if (t && t.id === "msgScroll" && t.scrollTop <= 60) loadOlderMessages();
  }, 200);
  document.addEventListener("scroll", (ev) => onScroll(ev), true);

  // typing indicators from the composer
  let lastTyping = 0;
  document.addEventListener("input", (ev) => {
    if (!ev.target || ev.target.id !== "composerText") return;
    if (!state.currentChannel || !state.wsReady) return;
    const now = Date.now();
    if (now - lastTyping < 2500) return;
    lastTyping = now;
    sendWS({ t: "typing", channel: state.currentChannel.id });
  }, true);

  // closing drawers when the viewport grows past mobile
  window.addEventListener("resize", debounce(() => {
    if (window.innerWidth > 720) closeDrawers();
    renderCallUI();
  }, 200));
}

/* ================= 15. calls: WebRTC mesh + relay screenshare ================= */

const call = {
  room: null, state: null, isHost: false, mode: "p2p",
  mic: null, share: null, shareVideo: null,
  peers: new Map(), remote: {},
  muted: false, deaf: false,
  relayTimer: null, relayIncoming: 0,
  preferredMode: "p2p",
};

/* ---- sounds (generated with WebAudio — no asset files) ---- */

const sfx = {
  ctx: null,
  _ringTimer: null,
  ac() {
    try {
      if (!this.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        this.ctx = new AC();
      }
      if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
      return this.ctx;
    } catch (e) { return null; }
  },
  blip(freq, at, dur, opts) {
    const ctx = this.ac();
    if (!ctx) return;
    opts = opts || {};
    const t = ctx.currentTime + (at || 0);
    try {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = opts.type || "sine";
      osc.frequency.setValueAtTime(freq, t);
      const vol = opts.gain == null ? 0.14 : opts.gain;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(vol, t + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(g);
      g.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + dur + 0.05);
    } catch (e) {}
  },
  join() {   // rising chime
    this.blip(392, 0, 0.16);
    this.blip(523.25, 0.09, 0.18);
    this.blip(659.25, 0.18, 0.3, { gain: 0.12 });
  },
  leave() {  // falling two-tone
    this.blip(523.25, 0, 0.15);
    this.blip(392, 0.1, 0.24, { gain: 0.12 });
  },
  ping() {   // short bright pop for mentions / DMs
    this.blip(880, 0, 0.08, { type: "triangle", gain: 0.11 });
    this.blip(1318.5, 0.06, 0.13, { type: "triangle", gain: 0.09 });
  },
  ringStart() {
    if (this._ringTimer) return;
    const ringOnce = () => {
      this.blip(440, 0, 0.18, { gain: 0.11 });
      this.blip(554.37, 0.02, 0.18, { gain: 0.09 });
      this.blip(440, 0.38, 0.18, { gain: 0.11 });
      this.blip(554.37, 0.4, 0.22, { gain: 0.09 });
    };
    ringOnce();
    this._ringTimer = setInterval(ringOnce, 2200);
  },
  ringStop() {
    if (this._ringTimer) { clearInterval(this._ringTimer); this._ringTimer = null; }
  },
};

function callRoomFor(cid) {
  if (cid === null || cid === undefined) return null;
  return (state.currentServer ? "c" : "d") + cid;
}

function activeRoom() { return state.activeRoom; }

function usersForCall(members) {
  const out = [];
  for (const uid of members || []) {
    if (state.user && uid === state.user.uid) { out.push(state.user); continue; }
    const m = memberByUid(uid);
    if (m) { out.push(m); continue; }
    let found = null;
    for (const dm of state.dms) {
      found = (dm.members || []).find((u) => u.uid === uid);
      if (found) break;
    }
    out.push(found || { uid, username: "member" + uid, display_name: "Member " + uid });
  }
  return out;
}

function callTitle(room) {
  if (!room) return "Call";
  if (room[0] === "d") {
    const dm = state.dms.find((d) => d.id === Number(room.slice(1)));
    return dm ? dmTitle(dm) : "Direct message";
  }
  const cid = Number(room.slice(1));
  const chan = channelById(cid);
  return chan ? chan.name : "Voice";
}

/* ---- microphone / deafen ---- */

async function ensureMic() {
  if (call.mic) { applyMicState(); return call.mic; }
  try {
    call.mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    call.mic.getAudioTracks().forEach((t) => { t.enabled = !call.muted && !call.deaf; });
    for (const [, e] of call.peers) {
      call.mic.getAudioTracks().forEach((t) => {
        try { e.pc.addTrack(t, call.mic); } catch (err) {}
      });
    }
    return call.mic;
  } catch (e) {
    toast("Microphone unavailable — you can still watch and type.", "warn");
    return null;
  }
}

function applyMicState() {
  if (!call.mic) return;
  const on = !call.muted && !call.deaf;
  call.mic.getAudioTracks().forEach((t) => { t.enabled = on; });
}

function toggleMute() {
  call.muted = !call.muted;
  if (!call.mic && !call.muted) ensureMic();
  applyMicState();
  syncCallButtons();
  renderCallUI();
}

function toggleDeafen() {
  call.deaf = !call.deaf;
  if (call.deaf) call.muted = true;
  else if (call.mic) call.muted = false;
  applyMicState();
  applyDeafenToMedia();
  syncCallButtons();
  renderCallUI();
}

function applyDeafenToMedia() {
  $$("video[data-remote],audio[data-remote]").forEach((el) => { el.muted = call.deaf; });
}

function syncCallButtons() {
  const mute = $("#muteBtn");
  const deaf = $("#deafenBtn");
  if (mute) {
    mute.classList.toggle("on", !!call.mic && !call.muted);
    mute.textContent = call.muted ? "🔇" : "🎤";
    mute.title = call.muted ? "Unmute microphone" : "Mute microphone";
  }
  if (deaf) {
    deaf.classList.toggle("on", !call.deaf);
    deaf.textContent = call.deaf ? "🔕" : "🎧";
    deaf.title = call.deaf ? "Undeafen" : "Deafen";
  }
}

/* ---- joining / leaving ---- */

async function joinRoom(room, withMic) {
  if (!room || !state.wsReady) return;
  if (state.activeRoom === room) return;
  if (state.activeRoom) stopCall(true);
  stopRing();
  state.activeRoom = room;
  call.room = room;
  call.preferredMode = "p2p";
  sendWS({ t: "call.join", room });
  if (withMic !== false) await ensureMic();
  sfx.join();
  renderCallUI(); syncCallButtons(); renderSidebar();
}

function stopCall(silent) {
  const room = state.activeRoom || call.room;
  if (room && !silent) { sendWS({ t: "call.leave", room }); sfx.leave(); }
  stopShare(true);
  if (call.mic) { call.mic.getTracks().forEach((t) => t.stop()); call.mic = null; }
  for (const [, e] of call.peers) { try { e.pc.close(); } catch (err) {} }
  call.peers.clear();
  call.remote = {};
  call.state = null; call.isHost = false; call.mode = "p2p";
  call.room = null; call.relayIncoming = 0;
  state.activeRoom = null;
  stopRelay();
  renderCallUI(); syncCallButtons(); renderSidebar();
}

/* ---- peer connections (perfect negotiation) ---- */

function ensurePeer(uid) {
  if (!state.user || uid === state.user.uid) return null;
  let e = call.peers.get(uid);
  if (e) return e;
  let pc;
  try {
    pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
  } catch (err) { console.warn("[webrtc] unsupported", err); return null; }
  e = { pc, polite: state.user.uid > uid, makingOffer: false, ignoreOffer: false };
  call.peers.set(uid, e);

  pc.onicecandidate = ({ candidate }) => {
    if (candidate && activeRoom()) {
      sendWS({ t: "signal", room: activeRoom(), to: uid, kind: "ice", payload: candidate });
    }
  };
  pc.ontrack = (ev) => {
    // Merge every incoming track into ONE stream per peer. Replacing the
    // stored stream (e.g. when a screenshare video arrives in its own stream)
    // used to repoint the audio element to a video-only stream and mute the
    // sharer's voice for everyone.
    let stream = call.remote[uid];
    if (!stream) {
      stream = (ev.streams && ev.streams[0]) || new MediaStream();
      call.remote[uid] = stream;
    }
    if (ev.track && !stream.getTracks().some((t) => t.id === ev.track.id)) {
      if (ev.track.kind === "video") {
        // keep exactly one live video track (share → stop → share again)
        stream.getVideoTracks().forEach((t) => {
          if (t.id !== ev.track.id) { try { stream.removeTrack(t); } catch (e) {} }
        });
      }
      try { stream.addTrack(ev.track); } catch (e) {}
      ev.track.onended = () => { try { stream.removeTrack(ev.track); } catch (e) {} };
    }
    attachStreams();
    renderCallUI();
  };
  pc.onnegotiationneeded = async () => {
    try {
      e.makingOffer = true;
      await pc.setLocalDescription();
      sendWS({ t: "signal", room: activeRoom(), to: uid, kind: "offer", payload: pc.localDescription });
    } catch (err) { console.warn("[webrtc] offer", err); }
    finally { e.makingOffer = false; }
  };
  pc.onconnectionstatechange = () => { renderCallUI(); maybeFallBackToRelay(); };

  if (call.mic) call.mic.getAudioTracks().forEach((t) => { try { pc.addTrack(t, call.mic); } catch (err) {} });
  if (call.share && call.mode === "p2p") {
    call.share.getVideoTracks().forEach((t) => { try { pc.addTrack(t, call.share); } catch (err) {} });
  }
  return e;
}

function removePeer(uid) {
  const e = call.peers.get(uid);
  if (!e) return;
  try { e.pc.close(); } catch (err) {}
  call.peers.delete(uid);
  delete call.remote[uid];
}

function syncPeers() {
  if (!state.activeRoom || !call.state || !state.user) return;
  const members = call.state.members || [];
  members.forEach((uid) => { if (uid !== state.user.uid) ensurePeer(uid); });
  Array.from(call.peers.keys()).forEach((uid) => {
    if (members.indexOf(uid) === -1) removePeer(uid);
  });
}

function maybeFallBackToRelay() {
  if (!call.isHost || call.mode !== "p2p" || !call.share) return;
  if (!call.peers.size) return;
  let failed = 0;
  for (const [, e] of call.peers) {
    if (e.pc.connectionState === "failed") failed++;
  }
  if (failed === call.peers.size) {
    call.preferredMode = "relay";
    sendWS({ t: "call.mode", room: activeRoom(), mode: "relay" });
    toast("Peer-to-peer is blocked on this network — switched to server relay.", "warn", 6000);
  }
}

function addVideoSenders() {
  if (!call.share) return;
  const track = call.share.getVideoTracks()[0];
  if (!track) return;
  for (const [, e] of call.peers) {
    const sender = e.pc.getSenders().find((s) => s.track && s.track.kind === "video");
    try {
      if (sender) sender.replaceTrack(track);
      else e.pc.addTrack(track, call.share);
    } catch (err) { console.warn("[webrtc] add video", err); }
  }
}

function removeVideoSenders() {
  for (const [, e] of call.peers) {
    e.pc.getSenders().forEach((s) => {
      if (s.track && s.track.kind === "video") { try { s.replaceTrack(null); } catch (err) {} }
    });
  }
}

async function onWebRTCSignal(data) {
  if (!state.activeRoom || data.room !== state.activeRoom) return;
  const from = Number(data.from);
  const e = ensurePeer(from);
  if (!e) return;
  const pc = e.pc;
  try {
    if (data.kind === "offer") {
      const collision = e.makingOffer || pc.signalingState !== "stable";
      e.ignoreOffer = !e.polite && collision;
      if (e.ignoreOffer) return;
      await pc.setRemoteDescription(data.payload);
      await pc.setLocalDescription();
      sendWS({ t: "signal", room: data.room, to: from, kind: "answer", payload: pc.localDescription });
    } else if (data.kind === "answer") {
      if (pc.signalingState !== "have-local-offer") return;
      await pc.setRemoteDescription(data.payload);
    } else if (data.kind === "ice") {
      try { await pc.addIceCandidate(data.payload); }
      catch (err) { if (!e.ignoreOffer) console.warn("[webrtc] ice", err); }
    }
  } catch (err) { console.warn("[webrtc] signal", err); }
}

/* ---- screenshare ---- */

async function toggleShare() {
  if (call.share) { stopShare(); return; }
  if (!state.activeRoom) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    toast("Screen sharing isn't supported in this browser.", "error");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: 30, max: 60 } }, audio: true,
    });
    call.share = stream;
    const vt = stream.getVideoTracks()[0];
    if (vt) vt.onended = () => stopShare();
    const v = document.createElement("video");
    v.autoplay = true; v.muted = true; v.playsInline = true; v.srcObject = stream;
    call.shareVideo = v;
    v.play().catch(() => {});
    call.isHost = true;
    call.preferredMode = call.preferredMode || "p2p";
    sendWS({ t: "call.host", room: activeRoom() });
    sendWS({ t: "call.mode", room: activeRoom(), mode: call.preferredMode });
    addVideoSenders();
    renderCallUI();
    toast("You're live — anyone in this call can see your screen.", "success");
  } catch (e) {
    toast("Screen share cancelled.", "info", 2500);
  }
}

function stopShare(silent) {
  stopRelay();
  if (call.share) { call.share.getTracks().forEach((t) => t.stop()); call.share = null; }
  if (call.shareVideo) { call.shareVideo.srcObject = null; call.shareVideo = null; }
  removeVideoSenders();
  if (!silent && state.activeRoom) renderCallUI();
}

function toggleCallMode() {
  if (!call.isHost) return;
  const next = call.mode === "p2p" ? "relay" : "p2p";
  call.preferredMode = next;
  sendWS({ t: "call.mode", room: activeRoom(), mode: next });
  call.mode = next;
  applyCallMode();
  toast(next === "relay" ? "Relay mode: frames go through the server." : "P2P mode: direct connection.", "info", 3500);
}

function applyCallMode() {
  if (call.state) call.mode = call.state.mode === "relay" ? "relay" : "p2p";
  if (call.mode === "relay") {
    removeVideoSenders();
    if (call.isHost && call.share) startRelay(); else stopRelay();
  } else {
    stopRelay();
    addVideoSenders();
  }
  renderCallUI();
}

/* ---- relay encoder / decoder (binary WS fallback) ---- */

function startRelay() {
  if (call.relayTimer || !call.shareVideo || !activeRoom()) return;
  const video = call.shareVideo;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  let busy = false;
  call.relayTimer = setInterval(() => {
    if (busy || !call.relayTimer) return;
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return;
    busy = true;
    const scale = Math.min(1, 1280 / w);
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    try { ctx.drawImage(video, 0, 0, canvas.width, canvas.height); }
    catch (err) { busy = false; return; }
    canvas.toBlob((blob) => {
      busy = false;
      if (!blob || !call.relayTimer) return;
      blob.arrayBuffer().then((buf) => {
        if (!call.relayTimer || !activeRoom()) return;
        sendRelayFrame(buf, Date.now());
      }).catch(() => {});
    }, "image/jpeg", 0.62);
  }, 84);
}

function stopRelay() {
  if (call.relayTimer) { clearInterval(call.relayTimer); call.relayTimer = null; }
}

function sendRelayFrame(payload, ts) {
  const ws = state.ws;
  if (!ws || ws.readyState !== 1) return;
  const header = new TextEncoder().encode(JSON.stringify({ room: activeRoom(), type: "f", ts }));
  if (header.length > 65535) return;
  const buf = new ArrayBuffer(3 + header.length + payload.byteLength);
  const view = new DataView(buf);
  view.setUint8(0, 0x01);
  view.setUint16(1, header.length);
  new Uint8Array(buf, 3, header.length).set(header);
  new Uint8Array(buf, 3 + header.length).set(new Uint8Array(payload));
  try { ws.send(buf); } catch (e) {}
}

function onBinaryFrame(data) {
  try {
    if (!data || data.byteLength < 3) return;
    const view = new DataView(data);
    if (view.getUint8(0) !== 0x01) return;
    const hlen = view.getUint16(1);
    if (data.byteLength < 3 + hlen) return;
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 3, hlen)));
    if (!header || header.room !== state.activeRoom) return;
    call.relayIncoming = Date.now();
    drawRelayFrame(new Uint8Array(data, 3 + hlen));
  } catch (e) {}
}

function drawRelayFrame(bytes) {
  const canvas = $("#stageCanvas");
  if (!canvas) return;
  const blob = new Blob([bytes], { type: "image/jpeg" });
  if (window.createImageBitmap) {
    createImageBitmap(blob).then((bmp) => {
      const c = $("#stageCanvas");
      if (!c) return;
      if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; }
      c.getContext("2d").drawImage(bmp, 0, 0);
      if (bmp.close) bmp.close();
    }).catch(() => {});
  } else {
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const c = $("#stageCanvas");
      if (c) { c.width = img.width; c.height = img.height; c.getContext("2d").drawImage(img, 0, 0); }
      URL.revokeObjectURL(url);
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }
}

/* ---- call state from the server ---- */

function onCallState(msg) {
  if (!msg || !msg.call) return;
  stopRing();
  call.state = msg.call;
  call.room = msg.call.room;
  state.activeRoom = msg.call.room;
  call.isHost = !!(msg.you && msg.you.is_host);
  syncPeers();
  applyCallMode();
  renderCallUI(); syncCallButtons(); renderSidebar();
}

function onCallUpdate(st) {
  if (!st || !st.room || !state.user) return;
  const mine = (st.members || []).indexOf(state.user.uid) !== -1;
  if (st.room !== state.activeRoom) {
    if (!mine) return;
    state.activeRoom = st.room;
    call.room = st.room;
  }
  if (mine) stopRing();
  call.state = st;
  call.isHost = st.host === state.user.uid;
  syncPeers();
  applyCallMode();
  renderCallUI(); renderSidebar();
}

function onCallEnded(data) {
  if (!data || !data.room) return;
  delete state.calls[data.room];
  if (state.ring && state.ring.room === data.room) stopRing();
  if (data.room === state.activeRoom) {
    stopCall(true);
    sfx.leave();
    toast("The call ended.", "info", 2500);
  }
}

/* ---- incoming call ring (Discord-style) ---- */

function onCallRing(data) {
  if (!data || !data.room || !state.user) return;
  if (Number(data.from) === state.user.uid) return;
  if (state.activeRoom === data.room) return;
  stopRing();
  state.ring = {
    room: data.room,
    channel_id: data.channel_id,
    from: Number(data.from),
    name: data.caller_name || "Someone",
    username: data.caller_username || "",
    at: Date.now(),
  };
  renderRingOverlay();
  sfx.ringStart();
  setTimeout(() => {
    if (state.ring && state.ring.room === data.room) stopRing();
  }, 45000);
}

function stopRing() {
  if (!state.ring) { sfx.ringStop(); return; }
  state.ring = null;
  sfx.ringStop();
  renderRingOverlay();
}

function renderRingOverlay() {
  let el = $("#ringOverlay");
  const r = state.ring;
  if (!r) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div");
    el.id = "ringOverlay";
    document.body.appendChild(el);
  }
  el.innerHTML = `
    <div class="ring-card">
      <span class="ring-avatar">${esc((r.name || "?").slice(0, 1).toUpperCase())}</span>
      <div class="ring-meta">
        <strong>Incoming call</strong>
        <span>${esc(r.name)}${r.username ? " · @" + esc(r.username) : ""}</span>
      </div>
      <button class="ring-btn accept" id="ringAccept" title="Accept" aria-label="Accept call">📞</button>
      <button class="ring-btn decline" id="ringDecline" title="Decline" aria-label="Decline call">✕</button>
    </div>`;
  $("#ringAccept").onclick = async () => {
    const room = r.room;
    stopRing();
    if (room && room[0] === "d") {
      const cid = Number(room.slice(1));
      navigate("/channels/" + cid);
      joinRoom(room);
    } else if (room) {
      joinRoom(room);
    }
  };
  $("#ringDecline").onclick = () => { stopRing(); };
}

/* ---- call rendering ---- */

function stageSource(st) {
  const me = state.user && state.user.uid;
  if (call.share) return { kind: "stream", stream: call.share, who: "You" };
  if (st.host && st.host !== me) {
    const hostStream = call.remote[st.host];
    if (hostStream && hostStream.getVideoTracks().length) {
      const who = usersForCall([st.host])[0];
      return { kind: "stream", stream: hostStream, who: who ? (who.display_name || who.username) : "Someone" };
    }
    const anyVideo = Object.keys(call.remote).map((k) => call.remote[k])
      .find((s) => s.getVideoTracks().length);
    if (anyVideo) return { kind: "stream", stream: anyVideo, who: "Someone" };
    if (call.mode === "relay" || Date.now() - call.relayIncoming < 4000) return { kind: "relay" };
  }
  if (call.mode === "relay" && Date.now() - call.relayIncoming < 4000) return { kind: "relay" };
  return { kind: "empty" };
}

function stageHTML(st) {
  const src = stageSource(st);
  if (src.kind === "stream") {
    return `<div class="stage">
      <video id="stageVideo" autoplay playsinline muted data-src="1"></video>
      <span class="stage-tag"><span class="live">●</span> ${esc(src.who)} is sharing</span>
    </div>`;
  }
  if (src.kind === "relay") {
    return `<div class="stage" id="callStage">
      <canvas id="stageCanvas"></canvas>
      <span class="stage-tag"><span class="live">●</span> Live · relay mode</span>
    </div>`;
  }
  return `<div class="stage"><div class="stage-empty">
      <div style="font-size:30px">🖥</div>
      <strong>No one is sharing yet</strong>
      <span class="hint">Voice is live — hit “Share screen” to broadcast. P2P keeps it under 80&nbsp;ms;
        the server relay kicks in automatically if your network blocks direct connections.</span>
    </div></div>`;
}

function attachStreams() {
  const v = $("#stageVideo");
  if (v && v.dataset.src) {
    const src = stageSource(call.state || {});
    const stream = src.kind === "stream" ? src.stream : null;
    if (stream && v.srcObject !== stream) { v.srcObject = stream; v.play().catch(() => {}); }
  }
  $$("[data-remote-stream]").forEach((el) => {
    const uid = Number(el.dataset.remoteStream);
    const stream = call.remote[uid];
    if (stream && el.srcObject !== stream) {
      el.srcObject = stream;
      el.muted = call.deaf;
      el.play().catch(() => {});
    }
  });
}

function tilesHTML(st) {
  const me = state.user && state.user.uid;
  const users = usersForCall(st.members || []);
  return `<div class="tiles">${users.map((u) => `
    <div class="tile ${u.uid === me && !call.muted && !call.deaf ? "speaking" : ""}" data-tile="${u.uid}">
      ${avatarHTML(u, "mini-avatar", true)}
      <span class="t-name">${esc(u.nickname || u.display_name || u.username)}${u.uid === st.host ? " 🖥" : ""}</span>
      ${(u.uid === me && (call.muted || call.deaf)) ? `<span class="t-mute">🔇</span>` : ""}
      <audio data-remote data-remote-stream="${u.uid}" autoplay></audio>
    </div>`).join("")}</div>`;
}

function callControlsHTML(st) {
  const sharing = !!call.share;
  const isHost = call.isHost;
  return `<div class="call-strip" style="border-radius:0 0 10px 10px;border-top:1px solid var(--line);margin-top:-1px">
    <span class="c-state">🔊 ${esc(callTitle(state.activeRoom))}</span>
    <span class="c-mode">${call.mode === "relay" ? "relay" : "p2p"} · ${(st.members || []).length} in call</span>
    <span class="c-users">${usersForCall(st.members || []).slice(0, 8).map((u) => avatarHTML(u, "mini-avatar", false)).join("")}</span>
    <span class="c-ctl">
      <button class="btn sm ${call.muted ? "danger" : "secondary"}" data-call="mute">${call.muted ? "🔇 Unmute" : "🎤 Mute"}</button>
      <button class="btn sm ${call.deaf ? "danger" : "secondary"}" data-call="deafen">${call.deaf ? "🔕 Undeafen" : "🎧 Deafen"}</button>
      <button class="btn sm ${sharing ? "danger" : ""}" data-call="share">${sharing ? "⏹ Stop sharing" : "🖥 Share screen"}</button>
      ${isHost ? `<button class="btn sm ghost" data-call="mode">${call.mode === "p2p" ? "Switch to relay" : "Switch to P2P"}</button>` : ""}
      <button class="btn sm danger" data-call="leave">Disconnect</button>
    </span>
  </div>`;
}

function stripHTML() {
  const st = call.state || { members: [] };
  return `<div class="call-strip">
    <span class="c-state">🔊 In call — ${esc(callTitle(state.activeRoom))}</span>
    <span class="c-mode">${call.mode === "relay" ? "relay" : "p2p"} · ${(st.members || []).length} user${(st.members || []).length === 1 ? "" : "s"}</span>
    <span class="c-users">${usersForCall(st.members || []).slice(0, 10).map((u) => avatarHTML(u, "mini-avatar", false)).join("")}</span>
    <span class="c-ctl">
      <button class="btn sm secondary" data-call="focus">Open</button>
      <button class="btn sm danger" data-call="leave">Leave</button>
    </span>
  </div>`;
}

function renderVoicePanel(panel) {
  const chan = state.currentChannel;
  const room = chan ? callRoomFor(chan.id) : null;
  const active = room && state.activeRoom === room;
  if (!active) {
    panel.innerHTML = `<div class="stage"><div class="stage-empty">
        <div style="font-size:30px">🔊</div>
        <strong>Voice channel</strong>
        <span class="hint">Jump in to talk with everyone here. Screenshare and free roles come with it.</span>
        <button class="btn" data-call="join">Join voice</button>
      </div></div>`;
    return;
  }
  const st = call.state || { members: [state.user && state.user.uid] };
  panel.innerHTML = stageHTML(st) + tilesHTML(st) + callControlsHTML(st);
}

function renderCallUI() {
  if (!state.user) return;
  const host = $("#callHost");
  if (host) {
    const showStrip = !!state.activeRoom;
    const html = showStrip ? stripHTML() : "";
    if (host.dataset.html !== html) { host.innerHTML = html; host.dataset.html = html; }
  }
  // The call panel lives inside the chat area — mount it when this channel is
  // in a call (DMs and text channels included) and unmount it when the call ends.
  const cur = state.currentChannel;
  const wantPanel = !!cur && (cur.kind === "voice" ||
    (!!state.activeRoom && state.activeRoom === callRoomFor(cur.id)));
  const panel = $("#voicePanel");
  if (cur && (!!panel !== wantPanel)) { renderChatArea(); return; }
  if (panel) renderVoicePanel(panel);
  wireCallControls();
  attachStreams();
  applyDeafenToMedia();
}

function wireCallControls() {
  $$("[data-call]").forEach((btn) => {
    btn.onclick = () => {
      const act = btn.dataset.call;
      if (act === "join") joinRoom(callRoomFor(state.currentChannel && state.currentChannel.id));
      else if (act === "leave") stopCall();
      else if (act === "mute") toggleMute();
      else if (act === "deafen") toggleDeafen();
      else if (act === "share") toggleShare();
      else if (act === "mode") toggleCallMode();
      else if (act === "focus") {
        if (state.activeRoom && state.activeRoom[0] === "d") navigate("/channels/" + state.activeRoom.slice(1));
        else if (state.activeRoom) navigate("/servers/" + (state.currentServer ? state.currentServer.id : "") + "/voice/" + state.activeRoom.slice(1));
      }
    };
  });
}

/* ================= 16. init ================= */

boot();


})();
