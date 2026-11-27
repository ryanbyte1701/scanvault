/* ScanVault web UI: vanilla JS single-page app. */
(() => {
"use strict";

// ------------------------------------------------------------------ API layer
// A demo build replaces window.SV_API before this script runs.
const API = window.SV_API || {
  async req(method, path, body, headers) {
    const opt = { method, headers: { "X-ScanVault": "1", ...(headers || {}) }, credentials: "same-origin" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) { const e = new Error((data && data.error) || r.statusText); e.status = r.status; e.code = data && data.code; throw e; }
    return data;
  },
  upload(path, form, onProgress, headers) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open("POST", path);
      x.setRequestHeader("X-ScanVault", "1");
      for (const [k, v] of Object.entries(headers || {})) x.setRequestHeader(k, v);
      x.upload.onprogress = e => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
      x.onload = () => {
        let d; try { d = JSON.parse(x.responseText); } catch { d = {}; }
        if (x.status >= 200 && x.status < 300) resolve(d);
        else { const e = new Error(d.error || x.statusText); e.status = x.status; e.code = d.code; reject(e); }
      };
      x.onerror = () => reject(new Error("Network error"));
      x.send(form);
    });
  },
  async render(id, { wc, ww, frame } = {}) {
    const q = new URLSearchParams();
    if (wc != null && ww != null) { q.set("wc", wc); q.set("ww", ww); }
    if (frame != null) q.set("frame", frame);
    const r = await fetch(`/api/files/${id}/render?${q}`, { credentials: "same-origin" });
    if (!r.ok) { let d = {}; try { d = await r.json(); } catch {} const e = new Error(d.error || r.statusText); e.code = d.code; throw e; }
    const h = k => r.headers.get(k), n = k => h(k) == null || h(k) === "" ? null : parseFloat(h(k));
    return { url: URL.createObjectURL(await r.blob()), wc: n("x-window-center"), ww: n("x-window-width"), dc: n("x-default-center"), dw: n("x-default-width"),
      min: n("x-min"), max: n("x-max"), frames: n("x-frames") || 1, frame: n("x-frame") || 0, rgb: h("x-rgb") === "1", modality: h("x-modality") || "" };
  },
  fileUrl: (id, dl) => `/api/files/${id}/content${dl ? "?download=1" : ""}`,
  thumbUrl: id => `/api/files/${id}/thumbnail`,
  backupDownloadUrl: () => "/api/backups/download",
  oauthStart: (type, id) => { location.href = `/api/oauth/${type}/start?target_id=${encodeURIComponent(id)}`; },
  demo: false,
};

// ------------------------------------------------------------------ utils
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const app = $("#app");
const fmtBytes = n => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB", "TB"]; const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024))); return (n / 1024 ** i).toFixed(i ? 1 : 0) + " " + u[i]; };
const fmtDate = d => { if (!d) return ""; const t = new Date(d.length === 10 ? d + "T12:00:00" : d); return isNaN(t) ? d : t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); };
const fmtDT = d => { if (!d) return "Never"; const t = new Date(d); return t.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); };
const ago = d => { if (!d) return "never"; const s = (Date.now() - new Date(d)) / 1000; if (s < 90) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago"; if (s < 86400 * 2) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " days ago"; };
const age = dob => { if (!dob) return ""; const b = new Date(dob + "T12:00:00"), n = new Date(); let a = n.getFullYear() - b.getFullYear(); if (n < new Date(n.getFullYear(), b.getMonth(), b.getDate())) a--; return isNaN(a) ? "" : a; };
const initials = n => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("");
let toastT;
document.addEventListener("sv-demo-toast", e => toast(e.detail));
const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 2800); };
function saveText(name, text) {
  if (API.demo) return toast(`Downloads are off in this preview. The real app saves ${name}.`);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
async function copyText(text) { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy is blocked here. Select the text and copy it."); } }

const I = {
  logo: '<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="2" width="28" height="28" rx="8" fill="#0b5d63"/><rect x="8" y="7" width="16" height="18" rx="2.5" fill="#0a0d0f" stroke="#cfe9e6" stroke-width="1.4"/><path d="M12 18c1-4 2.5-6 4-6s3 2 4 6" fill="none" stroke="#cfe9e6" stroke-width="1.6" stroke-linecap="round"/><circle cx="16" cy="11" r="1.4" fill="#cfe9e6"/><path d="M11 21.5h10" stroke="#4fb5ad" stroke-width="1.6" stroke-linecap="round"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5M12 3v12"/></svg>',
  cloud: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/><path d="m9.5 14.5 2 2 4-4"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
  pdf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
  scan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 15c1-4 2.5-6 4-6s3 2 4 6"/><circle cx="12" cy="7" r="1"/></svg>',
  camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
  drive: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M8 3h8l6 10.5-4 7H6l-4-7z"/><path d="M8 3l6 10.5H2M16 3l-6 10.5L14 20.5"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/></svg>',
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 7h.01M6 17h.01"/></svg>',
  restore: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 10.5 9-7 9 7V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  dl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/></svg>',
};
const HERO = `<svg class="hero" viewBox="0 0 520 300" aria-hidden="true">
<defs><linearGradient id="lb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e9fbf8"/><stop offset="1" stop-color="#b9dcd8"/></linearGradient>
<radialGradient id="bone" cx=".5" cy=".4" r=".6"><stop offset="0" stop-color="#f4fbfa"/><stop offset="1" stop-color="#6d8e92"/></radialGradient></defs>
<rect x="10" y="20" width="500" height="250" rx="14" fill="#151b1e" stroke="#233036"/>
<rect x="28" y="38" width="464" height="214" rx="6" fill="url(#lb)" opacity=".12"/>
${[0, 1, 2].map(i => `<g transform="translate(${44 + i * 152} 52)"><rect width="128" height="168" rx="4" fill="#05080a" stroke="#2d3f45"/>
<ellipse cx="64" cy="${70 + i * 6}" rx="${36 - i * 4}" ry="${48 - i * 6}" fill="url(#bone)" opacity="${.55 + i * .1}"/>
<ellipse cx="64" cy="${74 + i * 6}" rx="${18 - i * 2}" ry="${26 - i * 3}" fill="#05080a" opacity=".55"/>
<path d="M30 140h68" stroke="#4fb5ad" stroke-width="2" opacity=".6"/><text x="10" y="18" fill="#6f8a8e" font-size="10" font-family="ui-monospace,monospace">${["CT", "MR", "XR"][i]}</text></g>`).join("")}
<g transform="translate(404 196)"><circle r="34" fill="#0b5d63"/><rect x="-13" y="-4" width="26" height="20" rx="3" fill="#e9fbf8"/><path d="M-8 -4v-6a8 8 0 0 1 16 0v6" fill="none" stroke="#e9fbf8" stroke-width="3.4"/><circle cy="6" r="2.6" fill="#0b5d63"/></g>
</svg>`;

// ------------------------------------------------------------------ theme
const DARK_SWATCHES = [["Teal night", "#0d1416"], ["Graphite", "#161718"], ["Pure black", "#000000"], ["Navy", "#0b1426"], ["Aubergine", "#1a1022"], ["Espresso", "#1a1411"]];
const THEME = { theme: "system", dark_color: "#0d1416" };
function hexToHsl(hex) {
  const n = parseInt(hex.slice(1), 16), r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2; let h = 0, s = 0;
  if (mx !== mn) { const d = mx - mn; s = l > .5 ? d / (2 - mx - mn) : d / (mx + mn); h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60; }
  return [h, s * 100, l * 100];
}
const hsl = (h, s, l) => `hsl(${h.toFixed(0)} ${s.toFixed(1)}% ${l.toFixed(1)}%)`;
function applyTheme(p = THEME) {
  Object.assign(THEME, p);
  const root = document.documentElement;
  if (THEME.theme === "system") delete root.dataset.theme; else root.dataset.theme = THEME.theme;
  // Derive the dark palette from the chosen colour, kept dark enough for contrast.
  let [h, s, l] = hexToHsl(THEME.dark_color || "#0d1416"); l = Math.min(l, 13); s = Math.min(s, 45);
  const v = { "--d-bg": hsl(h, s, l), "--d-panel": hsl(h, s, l + 4.5), "--d-line": hsl(h, Math.min(s, 30), l + 12), "--d-soft": hsl(h, Math.min(s + 10, 50), l + 9), "--d-film": hsl(h, s, Math.max(0, l - 4)) };
  for (const [k, val] of Object.entries(v)) root.style.setProperty(k, val);
  const dark = THEME.theme === "dark" || (THEME.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.content = dark ? v["--d-panel"] : "#0b5d63";
  try { localStorage.setItem("sv-theme", JSON.stringify({ theme: THEME.theme, dark_color: THEME.dark_color })); } catch {}
}
try { applyTheme(JSON.parse(localStorage.getItem("sv-theme") || "null") || THEME); } catch { applyTheme(); }
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme());
async function saveTheme(p) { applyTheme(p); try { await API.req("PUT", "/api/prefs", p); } catch {} }
async function syncTheme() { try { applyTheme(await API.req("GET", "/api/prefs")); } catch {} }
const themeIcon = () => THEME.theme === "dark" ? I.moon : THEME.theme === "light" ? I.sun : I.auto;
const themeLabel = () => ({ system: "Theme: match device", light: "Theme: light", dark: "Theme: dark" })[THEME.theme];
function cycleTheme() { const next = { system: "light", light: "dark", dark: "system" }[THEME.theme]; saveTheme({ theme: next }); $$("[data-theme-btn]").forEach(b => { b.innerHTML = themeIcon(); b.title = b.ariaLabel = themeLabel(); }); toast(themeLabel()); }
document.body.insertAdjacentHTML("beforeend", `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><filter id="svwl" color-interpolation-filters="sRGB"><feComponentTransfer><feFuncR type="linear" slope="1" intercept="0"/><feFuncG type="linear" slope="1" intercept="0"/><feFuncB type="linear" slope="1" intercept="0"/></feComponentTransfer></filter></svg>`);

// ------------------------------------------------------------------ state + router
const S = { status: null, patients: null, route: { name: "home" } };

function parseHash() {
  if (API.demo) return S.route;
  const h = location.hash.replace(/^#\/?/, "").split("?")[0];
  const [name, id] = h.split("/");
  return { name: name || "home", id };
}
function go(name, id) {
  S.route = { name, id };
  if (!API.demo) { const h = "#/" + name + (id ? "/" + id : ""); if (location.hash !== h) { location.hash = h; return; } }
  render();
}
window.addEventListener("hashchange", () => { if (!API.demo) { S.route = parseHash(); render(); } });
document.addEventListener("click", e => {
  if (API.demo && e.target.closest("a[download], #dlb")) { e.preventDefault(); toast("Downloads are off in this preview. The real app downloads the decrypted original or an encrypted .svb backup."); return; }
  const a = e.target.closest("[data-go]");
  if (a) { e.preventDefault(); const [n, id] = a.dataset.go.split("/"); go(n, id); }
});

async function finishSso() {
  // The server redirects here after the identity provider; trade the one-time ticket for a session.
  const m = !API.demo && location.hash.match(/^#\/sso\?ticket=([\w-]+)/);
  if (!m) return;
  history.replaceState(null, "", location.pathname + "#/patients");
  try { await API.req("POST", "/api/auth/sso/exchange", { ticket: m[1] }); }
  catch (e) { S.ssoError = e.message; }
}

async function boot() {
  await finishSso();
  try { S.status = await API.req("GET", "/api/status"); }
  catch (e) { app.innerHTML = `<div class="empty"><h2>Can't reach ScanVault</h2><p>${esc(e.message)}</p><button class="btn" id="retry">Retry</button></div>`; $("#retry").onclick = () => location.reload(); return; }
  S.route = parseHash();
  render();
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  if (q.get("oauth") === "ok") toast("Account connected");
  const OAUTH_ERR = { denied: "the sign-in was cancelled or refused", expired: "the sign-in took too long, please try again", locked: "the vault is locked", exchange: "the provider did not accept the sign-in" };
  if (q.get("oauth") === "error") toast("Connecting the account failed: " + (OAUTH_ERR[q.get("msg")] || "unknown error"));
  if (q.get("sso") === "linked") toast("Single sign-on account linked");
  if (q.get("sso_error") && S.status.authenticated) { const m = takeSsoError(); if (m) toast(m); }
}

function handleErr(e) {
  if (e.code === "unauthenticated") { S.status.authenticated = false; render(); return true; }
  if (e.code === "maintenance") { toast(e.message); return true; }
  if (e.code === "locked") { S.status.unlocked = false; render(); return true; }
  toast(e.message || String(e));
  return false;
}

function render() {
  const st = S.status;
  if (!st.setup_complete) return viewSetup();
  if (!st.authenticated) return viewLogin();
  if (!st.unlocked) return viewUnlock();
  if (!S.themeSynced) { S.themeSynced = true; syncTheme(); }
  if (st.recovery_key_saved === false && !sessionStorage.getItem("sv-rk-later")) return viewRecoveryNag();
  const r = S.route;
  const view = { home: viewHome, patients: viewPatients, patient: viewPatient, study: viewStudy, backups: viewBackups, settings: viewSettings, upload: viewUploadPage }[r.name] || viewHome;
  shell(r.name);
  view(r.id).catch(handleErr);
}

function shell(active) {
  const nav = [["home", "Home", I.home], ["patients", "Patients", I.users], ["upload", "Upload", I.upload], ["backups", "Backups", I.cloud], ["settings", "Settings", I.gear]];
  const on = n => (n === active || (n === "patients" && (active === "patient" || active === "study"))) ? "on" : "";
  app.innerHTML = `${API.demo ? '<div class="demo-banner">Interactive preview with sample patients. Nothing leaves your browser and nothing is stored.</div>' : ""}
  <div class="shell">
    <nav class="side" aria-label="Main">
      <a class="brand" href="#/home" data-go="home">${I.logo}<span>ScanVault</span></a>
      <div class="nav">${nav.map(([n, l, ic]) => `<a href="#/${n}" data-go="${n}" class="${on(n)}">${ic}${l}</a>`).join("")}</div>
      <div class="foot"><span class="lockchip">${I.shield} Vault unlocked</span><span>Signed in as ${esc(S.status.username)}</span><button class="btn ghost sm themebtn" data-theme-btn title="${themeLabel()}" aria-label="${themeLabel()}">${themeIcon()}</button></div>
    </nav>
    <main id="main"><div class="empty"><span class="spin"></span></div></main>
    <nav class="tabbar" aria-label="Main">${nav.map(([n, l, ic]) => `<a href="#/${n}" data-go="${n}" class="${on(n)}">${ic}<span>${l}</span></a>`).join("")}</nav>
  </div>`;
}
const main = () => $("#main");
const mobileHead = () => `<div class="mobilehead"><div class="brand">${I.logo}<span>ScanVault</span></div><div class="row" style="gap:4px"><span class="lockchip">${I.shield} Unlocked</span><button class="btn ghost sm themebtn" data-theme-btn title="${themeLabel()}" aria-label="${themeLabel()}">${themeIcon()}</button></div></div>`;
document.addEventListener("click", e => { if (e.target.closest("[data-theme-btn]")) cycleTheme(); });
// File pickers are <label class="btn"> wrapping a hidden input: make them work from the keyboard.
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const l = e.target.closest && e.target.closest("label.btn");
  const i = l && l.querySelector("input[type=file]");
  if (i) { e.preventDefault(); i.click(); }
});

// ------------------------------------------------------------------ modal
function modal(title, body, { wide = false, footer = "" } = {}) {
  const el = document.createElement("div");
  el.className = "scrim";
  el.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <header><h2>${esc(title)}</h2><button class="btn ghost sm" data-close aria-label="Close">${I.x}</button></header>
    <div class="body">${body}</div>${footer ? `<footer>${footer}</footer>` : ""}</div>`;
  const opener = document.activeElement;
  const close = () => { el.remove(); document.removeEventListener("keydown", onKey); if (opener && opener.isConnected && opener.focus) opener.focus(); };
  const onKey = e => {
    if (e.key === "Escape") return close();
    if (e.key !== "Tab") return;
    const f = $$('button,[href],input:not([type=hidden]),select,textarea,label[tabindex],[tabindex]:not([tabindex="-1"])', el).filter(x => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    else if (!el.contains(document.activeElement)) { e.preventDefault(); f[0].focus(); }
  };
  el.addEventListener("click", e => { if (e.target === el || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(el);
  const f = $("input,select,textarea", el); if (f && matchMedia("(min-width:821px)").matches) f.focus();
  return { el, close };
}
function confirmBox(title, text, okLabel = "Delete", danger = true) {
  return new Promise(res => {
    const m = modal(title, `<p>${text}</p>`, { footer: `<button class="btn" data-close>Cancel</button><button class="btn ${danger ? "danger" : "primary"}" id="cok">${esc(okLabel)}</button>` });
    $("#cok", m.el).onclick = () => { m.close(); res(true); };
    m.el.addEventListener("click", e => { if (e.target.closest("[data-close]") || e.target === m.el) res(false); });
  });
}

// ------------------------------------------------------------------ setup wizard
function strength(p) {
  if (!p) return [0, ""];
  let s = Math.min(60, p.length * 3);
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s += 10;
  if (/\d/.test(p)) s += 10;
  if (/[^A-Za-z0-9]/.test(p)) s += 10;
  if (/\s/.test(p) && p.split(/\s+/).length >= 4) s += 15;
  if (p.length < 12) s = Math.min(s, 30);
  s = Math.min(100, s);
  return [s, s < 40 ? "Too weak" : s < 70 ? "OK" : s < 90 ? "Strong" : "Very strong"];
}
// 6 words from the EFF large list (7,776 words) = 77.5 bits. The list is
// loaded on demand; if it can't be, fall back to 20 random base-32 characters
// (100 bits). Uses rejection sampling, so every word is equally likely.
let WORDS = null;
async function loadWords() {
  if (WORDS) return WORDS;
  try {
    const r = await fetch("/static/wordlist.txt", { credentials: "same-origin" });
    if (!r.ok) throw new Error();
    const w = (await r.text()).split("\n").map(x => x.trim()).filter(x => x && !x.startsWith("#"));
    if (w.length < 7000) throw new Error();
    WORDS = w;
  } catch { WORDS = []; }
  return WORDS;
}
function randBelow(n) {
  const lim = Math.floor(0x100000000 / n) * n, a = new Uint32Array(1);
  do { crypto.getRandomValues(a); } while (a[0] >= lim);
  return a[0] % n;
}
async function genPassphrase() {
  const w = await loadWords();
  if (w.length >= 7000) return Array.from({ length: 6 }, () => w[randBelow(w.length)]).join("-");
  const abc = "abcdefghijkmnpqrstuvwxyz23456789";
  return Array.from({ length: 4 }, () => Array.from({ length: 5 }, () => abc[randBelow(abc.length)]).join("")).join("-");
}
const plainHttp = () => !S.status.secure && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) && !API.demo;
const httpWarn = () => plainHttp() ? `<div class="note warn">${I.warn}<div><b>This connection is not encrypted (http://).</b> Your password, encryption key and recovery key would cross the network in clear. Set ScanVault up over HTTPS (for example through your NAS's reverse proxy) or from the server itself.</div></div>` : "";

function authLayout(inner, title = "Your scans. Your key. Your copies.", sub = "ScanVault keeps encrypted local copies of imaging studies and reports from every center you visit, and backs them up encrypted to the places you choose.") {
  app.innerHTML = `<div class="auth"><section class="art"><div class="brand" style="color:#e6eeee">${I.logo}<span>ScanVault</span></div>
    <div><h1>${title}</h1><p>${sub}</p></div>${HERO}</section>
    <section class="formside">${inner}</section></div>`;
}

const setup = { step: 0, mode: null, username: "", password: "", passphrase: "", recovery: "" };
function viewSetup() {
  const steps = n => `<div class="steps" aria-hidden="true">${[0, 1, 2, 3].map(i => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</div>`;
  if (setup.step === 0) {
    authLayout(`${steps(0)}<div><h1>Welcome</h1><p class="muted" style="margin-top:8px">Set up this ScanVault server. It takes about two minutes.</p></div>
      ${httpWarn()}
      <div class="choice">
        <button id="new">${I.shield}<b>Create a new vault</b><span>Pick a sign-in and the encryption key that protects every file.</span></button>
        <button id="rest">${I.restore}<b>Restore from a backup</b><span>Use a .svb backup plus your recovery key file or encryption key.</span></button>
        ${window.SV_DEMO_SKIP ? `<button id="skip">${I.users}<b>Skip to the sample vault</b><span>Preview only: open three sample patients with scans, reports and backups.</span></button>` : ""}
      </div>`);
    const sk = $("#skip"); if (sk) sk.onclick = async () => { window.SV_DEMO_SKIP(); S.status = await API.req("GET", "/api/status"); go("home"); };
    $("#new").onclick = () => { setup.step = 1; viewSetup(); };
    $("#rest").onclick = viewRestoreSetup;
    return;
  }
  if (setup.step === 1) {
    authLayout(`${steps(1)}<div><h1>Create your sign-in</h1><p class="muted" style="margin-top:8px">This is how you open ScanVault on the web and on your phone.</p></div>
      <form class="form" id="f">
        ${API.demo ? "" : `<label class="f">Setup code <span class="hint">Printed in the server log when ScanVault starts (on a Synology: Container Manager, scanvault, Log). It proves you own this server.</span><input name="code" required autocomplete="off" autocapitalize="characters" spellcheck="false" value="${esc(setup.code || "")}" placeholder="XXXX-XXXX-XXXX"></label>`}
        <label class="f">Username<input name="u" autocomplete="username" required value="${esc(setup.username)}" autocapitalize="none"></label>
        <label class="f">Password <span class="hint">At least 10 characters</span><input name="p" type="password" autocomplete="new-password" required minlength="10"></label>
        <label class="f">Confirm password<input name="p2" type="password" autocomplete="new-password" required></label>
        <p class="err" id="err"></p>
        <div class="row"><button type="button" class="btn" id="back">Back</button><span class="spacer"></span><button class="btn primary">Continue</button></div>
      </form>`);
    $("#back").onclick = () => { setup.step = 0; viewSetup(); };
    $("#f").onsubmit = e => {
      e.preventDefault(); const f = e.target;
      if (f.p.value.length < 10) return $("#err").textContent = "Password must be at least 10 characters.";
      if (f.p.value !== f.p2.value) return $("#err").textContent = "Passwords don't match.";
      setup.username = f.u.value.trim(); setup.password = f.p.value; if (f.code) setup.code = f.code.value.trim(); setup.step = 2; viewSetup();
    };
    return;
  }
  if (setup.step === 2) {
    authLayout(`${steps(2)}<div><h1>Set your encryption key</h1><p class="muted" style="margin-top:8px">Every scan, report and patient record is encrypted with this key on this server, and every backup is encrypted with it before it leaves. Nobody, including ScanVault, can recover your data without it or the recovery key file you'll download next.</p></div>
      <form class="form" id="f">
        <label class="f">Encryption key (passphrase) <span class="hint">At least 12 characters. A few random words work well.</span>
          <input name="k" type="password" autocomplete="new-password" required minlength="12" id="k"></label>
        <div class="row small"><div class="meter" style="flex:1"><i id="m"></i></div><span id="ml" class="muted" style="min-width:80px;text-align:right"></span></div>
        <div class="row"><button type="button" class="btn sm" id="gen">${I.key} Generate one for me</button><button type="button" class="btn sm ghost" id="show">Show</button></div>
        <label class="f">Type it again<input name="k2" type="password" autocomplete="new-password" required></label>
        <div class="note">${I.warn}<div>Use a different value from your sign-in password. If you change your sign-in password later, the encryption key stays the same.</div></div>
        <p class="err" id="err"></p>
        <div class="row"><button type="button" class="btn" id="back">Back</button><span class="spacer"></span><button class="btn primary" id="go">Create vault</button></div>
      </form>`);
    const k = $("#k");
    const upd = () => { const [s, l] = strength(k.value); $("#m").style.width = s + "%"; $("#m").style.background = s < 40 ? "var(--red)" : s < 70 ? "var(--amber)" : "var(--green)"; $("#ml").textContent = l; };
    k.oninput = upd;
    $("#show").onclick = () => { const t = k.type === "password" ? "text" : "password"; k.type = t; $("[name=k2]").type = t; $("#show").textContent = t === "text" ? "Hide" : "Show"; };
    $("#gen").onclick = async () => { const p = await genPassphrase(); k.value = p; $("[name=k2]").value = p; k.type = "text"; $("[name=k2]").type = "text"; $("#show").textContent = "Hide"; upd(); };
    $("#back").onclick = () => { setup.step = 1; viewSetup(); };
    $("#f").onsubmit = async e => {
      e.preventDefault(); const f = e.target;
      if (f.k.value.length < 12) return $("#err").textContent = "The encryption key must be at least 12 characters.";
      if (f.k.value !== f.k2.value) return $("#err").textContent = "The two entries don't match.";
      if (f.k.value === setup.password) return $("#err").textContent = "Use something different from your sign-in password.";
      $("#go").disabled = true; $("#go").innerHTML = '<span class="spin"></span> Creating…';
      try {
        const r = await API.req("POST", "/api/setup", { username: setup.username, password: setup.password, passphrase: f.k.value }, { "X-Setup-Code": setup.code || "" });
        setup.recovery = r.recovery_key; setup.fingerprint = r.fingerprint; setup.vault_id = r.vault_id; setup.password = ""; setup.step = 3; viewSetup();
      } catch (er) {
        if (er.code === "setup_code") { setup.step = 1; viewSetup(); $("#err").textContent = er.message; return; }
        $("#err").textContent = er.message; $("#go").disabled = false; $("#go").textContent = "Create vault";
      }
    };
    return;
  }
  // step 3: recovery key
  authLayout(`${steps(3)}<div><h1>Save your recovery key</h1><p class="muted" style="margin-top:8px">This text file is your emergency restore key. With it and any backup you can get every file back, even if this server is gone or you forget the encryption key.</p></div>
    <div class="keybox" id="kb">${esc(setup.recovery)}</div>
    <div class="row"><button class="btn primary" id="dl">${I.dl} Download key file</button><button class="btn" id="cp">Copy</button><button class="btn ghost" id="pr">Print</button></div>
    <div class="note">${I.warn}<div>Keep it offline: printed, in a password manager or on a USB stick. Don't store it in the same Google Drive or Box account as your backups.</div></div>
    <form class="form" id="f">
      <label class="f">Confirm you saved it <span class="hint">Choose the file you just downloaded. We check it matches, then forget it.</span><input type="file" name="kf" accept=".txt,text/plain"></label>
      <label class="check"><input type="checkbox" name="skip"> <span>Skip the check. I understand I could lose access to my data.</span></label>
      <p class="err" id="err"></p>
      <div class="row end"><button class="btn primary" id="go">Finish setup</button></div>
    </form>`, "Your key, on paper.", "Fingerprint " + esc(setup.fingerprint || "") + ". Match this fingerprint against the one in Settings whenever you want to be sure a key file belongs to this vault.");
  $("#dl").onclick = () => saveText("scanvault-recovery-key.txt", setup.recovery);
  $("#cp").onclick = () => copyText(setup.recovery);
  $("#pr").onclick = () => { const w = window.open("", "_blank"); if (!w) return toast("Printing is blocked here; download the file instead"); w.document.write(`<pre style="font:14px monospace;white-space:pre-wrap">${esc(setup.recovery)}</pre>`); w.print(); };
  $("#f").onsubmit = async e => {
    e.preventDefault(); const f = e.target;
    try {
      if (f.kf.files[0]) await API.req("POST", "/api/vault/recovery-key/confirm", { recovery_key: await f.kf.files[0].text() });
      else if (f.skip.checked) await API.req("POST", "/api/vault/recovery-key/confirm", { skip: true });
      else return $("#err").textContent = "Choose the key file to confirm, or tick the box to skip.";
      setup.recovery = ""; S.status = await API.req("GET", "/api/status"); go("home");
    } catch (er) { $("#err").textContent = er.message; }
  };
}

function viewRestoreSetup() {
  authLayout(`<div><h1>Restore a vault</h1><p class="muted" style="margin-top:8px">Upload a ScanVault backup (.svb) from Google Drive, Box, your SFTP server or your computer.</p></div>
    <form class="form" id="f">
      ${httpWarn()}
      ${API.demo ? "" : `<label class="f">Setup code <span class="hint">Printed in the server log when ScanVault starts</span><input name="code" required autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="XXXX-XXXX-XXXX"></label>`}
      <label class="f">Backup file (.svb)<input type="file" name="b" accept=".svb" required></label>
      <div class="seg" role="tablist"><button type="button" class="on" data-m="file">Recovery key file</button><button type="button" data-m="pass">Encryption key</button></div>
      <label class="f" id="mf">Recovery key file<input type="file" name="kf" accept=".txt,text/plain"></label>
      <label class="f" id="mp" hidden>Encryption key <span class="hint">Only works if "passphrase restore" was turned on in that vault's Settings</span><input type="password" name="k" autocomplete="off"></label>
      <div class="grid2"><label class="f">New username<input name="u" required autocapitalize="none"></label><label class="f">New password<input name="p" type="password" minlength="10" required autocomplete="new-password"></label></div>
      <p class="err" id="err"></p>
      <div class="row"><button type="button" class="btn" id="back">Back</button><span class="spacer"></span><button class="btn primary" id="go">Restore</button></div>
    </form>`, "Pick up where you left off.");
  let mode = "file";
  $$(".seg button").forEach(b => b.onclick = () => { mode = b.dataset.m; $$(".seg button").forEach(x => x.classList.toggle("on", x === b)); $("#mf").hidden = mode !== "file"; $("#mp").hidden = mode !== "pass"; });
  $("#back").onclick = () => { setup.step = 0; viewSetup(); };
  $("#f").onsubmit = async e => {
    e.preventDefault(); const f = e.target; const fd = new FormData();
    fd.append("backup_file", f.b.files[0]); fd.append("username", f.u.value); fd.append("password", f.p.value);
    if (mode === "file") { if (!f.kf.files[0]) return $("#err").textContent = "Choose your recovery key file."; fd.append("recovery_key", await f.kf.files[0].text()); }
    else fd.append("passphrase", f.k.value);
    $("#go").disabled = true; $("#go").innerHTML = '<span class="spin"></span> Restoring…';
    try { const r = await API.upload("/api/setup/restore", fd, null, { "X-Setup-Code": f.code ? f.code.value.trim() : "" }); toast(`Restored ${r.patients} patients, ${r.files} files. Backup destinations are paused until you turn them back on.`); S.status = await API.req("GET", "/api/status"); go("home"); }
    catch (er) { $("#err").textContent = er.message; $("#go").disabled = false; $("#go").textContent = "Restore"; }
  };
}

function viewRecoveryNag() {
  authLayout(`<div><h1>Save your recovery key</h1><p class="muted" style="margin-top:8px">You haven't confirmed that you saved this vault's recovery key file. Without it, a forgotten encryption key means your data and backups can't be opened. Enter your encryption key to download it again.</p></div>
    <form class="form" id="f">
      <label class="f">Encryption key<input name="k" type="password" autocomplete="off" required></label>
      <p class="err" id="err"></p>
      <div class="row"><button class="btn primary" id="go">${I.dl} Download key file</button></div>
    </form>
    <form class="form" id="f2" hidden>
      <label class="f">Confirm you saved it <span class="hint">Choose the file you just downloaded</span><input type="file" name="kf" accept=".txt,text/plain"></label>
      <p class="err" id="err2"></p>
      <div class="row end"><button class="btn primary">Confirm</button></div>
    </form>
    <button class="btn ghost sm" id="later" style="justify-self:start">Remind me later</button>`, "Your key, on paper.");
  $("#f").onsubmit = async e => {
    e.preventDefault();
    try { const t = await API.req("POST", "/api/vault/recovery-key", { passphrase: e.target.k.value }); saveText("scanvault-recovery-key.txt", t); $("#f2").hidden = false; }
    catch (er) { $("#err").textContent = er.message; }
  };
  $("#f2").onsubmit = async e => {
    e.preventDefault(); const kf = e.target.kf.files[0];
    if (!kf) return $("#err2").textContent = "Choose the key file you downloaded.";
    try { await API.req("POST", "/api/vault/recovery-key/confirm", { recovery_key: await kf.text() }); S.status = await API.req("GET", "/api/status"); render(); }
    catch (er) { $("#err2").textContent = er.message; }
  };
  $("#later").onclick = () => { sessionStorage.setItem("sv-rk-later", "1"); render(); };
}

// The server hands sign-in errors over in a short-lived cookie (never via the
// URL, which anyone can craft); read it once and clear it.
function takeSsoError() {
  const m = document.cookie.split("; ").find(c => c.startsWith("sv_sso_error="));
  if (!m) return "";
  document.cookie = "sv_sso_error=; Max-Age=0; Path=/; SameSite=Lax";
  try { return decodeURIComponent(m.slice("sv_sso_error=".length)).slice(0, 300); } catch { return ""; }
}

function viewLogin() {
  const st = S.status, providers = st.sso || [], local = st.local_login !== false;
  const ssoErr = S.ssoError || takeSsoError();
  S.ssoError = null;
  const ssoBtns = providers.map(p => `<a class="btn ${local ? "" : "primary"} ssobtn" href="/api/auth/sso/${encodeURIComponent(p.id)}/start">${I.key} Continue with ${esc(p.name)}</a>`).join("");
  authLayout(`<div><h1>Sign in</h1></div>
    ${providers.length ? `<div class="stack ssobtns">${ssoBtns}</div>${local ? '<div class="ordiv"><span>or use your password</span></div>' : ""}` : ""}
    ${local ? `<form class="form" id="f">
      <label class="f">Username<input name="u" autocomplete="username" required autocapitalize="none"></label>
      <label class="f">Password<input name="p" type="password" autocomplete="current-password" required></label>
      <p class="err" id="err">${esc(ssoErr)}</p><button class="btn primary" id="go">Sign in</button></form>`
      : `<p class="err" id="err">${esc(ssoErr)}</p><p class="small muted">Password sign-in is turned off for this vault.</p>`}`, "Welcome back.");
  if (!local) return;
  $("#f").onsubmit = async e => {
    e.preventDefault(); const f = e.target; $("#go").disabled = true;
    try { await API.req("POST", "/api/auth/login", { username: f.u.value, password: f.p.value }); S.status = await API.req("GET", "/api/status"); render(); }
    catch (er) { $("#err").textContent = er.message; $("#go").disabled = false; }
  };
}

function viewUnlock() {
  authLayout(`<div><h1>Unlock the vault</h1><p class="muted" style="margin-top:8px">ScanVault restarted, so the encryption key is no longer in memory. Enter it to open your files and resume scheduled backups.</p></div>
    <form class="form" id="f">
      <label class="f">Encryption key<input name="k" type="password" autocomplete="off" required></label>
      <p class="err" id="err"></p>
      <div class="row"><button class="btn primary" id="go">${I.lock} Unlock</button><span class="spacer"></span><button type="button" class="btn ghost sm" id="kfb">Use recovery key file</button><input type="file" id="kf" accept=".txt,text/plain" hidden></div>
    </form>
    <button class="btn ghost sm" id="out" style="justify-self:start">Sign out</button>`, "Locked at rest.");
  const done = async () => { S.status = await API.req("GET", "/api/status"); render(); };
  $("#f").onsubmit = async e => {
    e.preventDefault(); $("#go").disabled = true;
    try { await API.req("POST", "/api/vault/unlock", { passphrase: e.target.k.value }); await done(); }
    catch (er) { $("#err").textContent = er.message; $("#go").disabled = false; }
  };
  $("#kfb").onclick = () => $("#kf").click();
  $("#kf").onchange = async e => { try { await API.req("POST", "/api/vault/unlock", { recovery_key: await e.target.files[0].text() }); await done(); } catch (er) { $("#err").textContent = er.message; } };
  $("#out").onclick = async () => { await API.req("POST", "/api/auth/logout"); S.status.authenticated = false; render(); };
}

// ------------------------------------------------------------------ home
const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };
const until = t => { const s = (t - Date.now()) / 1000; if (s <= 60) return "due now"; if (s < 3600) return "in " + Math.round(s / 60) + " min"; if (s < 86400 * 2) return "in " + Math.round(s / 3600) + " h"; return "in " + Math.round(s / 86400) + " days"; };

function backupHealth(tg, stats) {
  const on = tg.filter(t => t.enabled);
  const bad = on.filter(t => !t.connected || t.last_status === "error");
  const due = on.filter(t => t.connected && t.schedule_hours).map(t => (t.last_run ? Date.parse(t.last_run) : Date.now()) + t.schedule_hours * 3600e3);
  const next = due.length ? Math.min(...due) : null;
  if (!tg.length) return { tone: "warn", title: "No backups yet", text: "Add a destination so a lost disk doesn't mean lost scans.", next };
  if (bad.length) return { tone: "warn", title: `${bad.length} of ${on.length} destination${on.length === 1 ? "" : "s"} need${bad.length === 1 ? "s" : ""} attention`, text: bad.map(t => `${t.name}: ${t.connected ? "last run failed" : "not connected"}`).join(" · "), next };
  if (!stats.last_backup_at) return { tone: "warn", title: "No backup has finished yet", text: `${on.length} destination${on.length === 1 ? "" : "s"} set up. Run one now to be safe.`, next };
  return { tone: "ok", title: `Backed up ${ago(stats.last_backup_at)}`, text: `${on.length} destination${on.length === 1 ? "" : "s"}, encrypted before upload`, next };
}

async function viewHome() {
  const [list, stats, recent, tg] = await Promise.all([
    API.req("GET", "/api/patients"), API.req("GET", "/api/stats"), API.req("GET", "/api/studies/recent?limit=6"), API.req("GET", "/api/backups/targets")]);
  S.patients = list;
  const st = S.status, m = main(), bh = backupHealth(tg, stats);
  const keyOk = st.recovery_key_saved !== false;
  const steps = [
    [list.length > 0, "Add a patient", "One record per person: you, a child, a parent.", "addp"],
    [stats.files > 0, "Upload a scan or report", "DICOM, a CD/zip export, photos of films, PDFs.", "upl"],
    [tg.length > 0, "Add a backup destination", "Cloud drives, S3, a NAS or a USB disk.", "backups"]];
  const todo = steps.filter(x => !x[0]).length;
  const tone = t => `<i class="dot ${t}" aria-hidden="true"></i>`;
  const thumb = (id, big) => `<div class="t${big ? " big" : ""}"><img alt="" loading="lazy" src="${esc(API.thumbUrl(id))}"></div>`;
  const studyRow = s => `<a class="rstudy" href="#/study/${esc(s.id)}" data-go="study/${esc(s.id)}">
      <div class="film">${s.thumbnail_ids.length ? s.thumbnail_ids.map((id, i) => thumb(id, i === 0)).join("") : `<div class="t big">${s.report_count ? I.pdf : I.scan}</div>`}</div>
      <div class="rs-body"><div class="rs-top">${s.modality ? `<span class="badge">${esc(s.modality)}</span>` : ""}${s.report_count ? `<span class="badge gray">${I.pdf}Report</span>` : ""}<span class="muted small nowrap">${esc(s.study_date ? fmtDate(s.study_date) : "No date")}</span></div>
        <h3>${esc(s.title)}</h3>
        <div class="small muted rs-meta"><span class="who"><span class="avatar xs">${esc(initials(s.patient_name))}</span>${esc(s.patient_name || "Unknown")}</span>${s.facility ? `<span>${esc(s.facility)}</span>` : ""}<span>${s.file_count} file${s.file_count === 1 ? "" : "s"}</span></div></div>
      <span class="chev" aria-hidden="true">${I.chev}</span></a>`;
  const personRow = p => `<a class="prow" href="#/patient/${esc(p.id)}" data-go="patient/${esc(p.id)}"><span class="avatar">${esc(initials(p.name))}</span>
      <span class="pr-body"><b>${esc(p.name)}</b><span class="small muted">${[p.date_of_birth && `${age(p.date_of_birth)} yrs`, `${p.study_count} ${p.study_count === 1 ? "study" : "studies"}`, p.last_study_date && "last " + fmtDate(p.last_study_date)].filter(Boolean).map(esc).join(" · ")}</span></span>
      <span class="chev" aria-hidden="true">${I.chev}</span></a>`;

  m.innerHTML = `${mobileHead()}
  <div class="home">
  <header class="hhead"><div><p class="eyebrow">${esc(new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }))}</p><h1>${greeting()}${st.username ? ", " + esc(st.username) : ""}</h1>
    <p class="sub">${stats.patients} ${stats.patients === 1 ? "person" : "people"} · ${stats.studies} ${stats.studies === 1 ? "study" : "studies"} · ${fmtBytes(stats.bytes)} encrypted on this server</p></div>
    <div class="row hactions"><button class="btn" id="addp">${I.plus} Add patient</button><button class="btn primary" id="upl">${I.upload} Upload</button></div></header>

  <section class="health" aria-label="Vault status">
    <div class="card hcard ok hc-vault">${tone("ok")}<div class="hc-body"><span class="hc-l">Vault</span><b>Unlocked</b><span class="small muted">AES-256-GCM${st.fingerprint ? ` · key <span class="mono">${esc(String(st.fingerprint).slice(0, 9))}</span>` : ""}</span></div>
      <button class="btn sm ghost" id="lock">${I.lock}<span>Lock</span></button></div>
    <div class="card hcard ${bh.tone}">${tone(bh.tone)}<div class="hc-body"><span class="hc-l">Backups</span><b>${esc(bh.title)}</b><span class="small muted">${esc(bh.text)}${bh.next ? ` · next ${esc(until(bh.next))}` : ""}</span></div>
      ${tg.length ? `<button class="btn sm" id="bnow">Back up now</button>` : `<a class="btn sm" href="#/backups" data-go="backups">Set up</a>`}</div>
    <div class="card hcard ${keyOk ? "ok" : "warn"}">${tone(keyOk ? "ok" : "warn")}<div class="hc-body"><span class="hc-l">Recovery key</span><b>${keyOk ? "Saved and confirmed" : "Not confirmed yet"}</b><span class="small muted">${keyOk ? "Keep the file offline or printed." : "Without it, a forgotten key locks your backups."}</span></div>
      ${keyOk ? "" : `<a class="btn sm" href="#/settings" data-go="settings">Fix</a>`}</div>
  </section>

  ${todo ? `<section class="card pad onboard"><div class="section-h" style="margin:0 0 12px"><h2>Get set up</h2><span class="muted small">${steps.length - todo} of ${steps.length} done</span></div>
    <ol class="steps2">${steps.map(([done, t, d, act]) => `<li class="${done ? "done" : ""}"><span class="tick">${done ? I.check : ""}</span><span><b>${t}</b><span class="small muted">${d}</span></span>${done ? "" : `<button class="btn sm" data-step="${act}">Start</button>`}</li>`).join("")}</ol></section>` : ""}

  <div class="hgrid">
    <div class="hmain">
      ${list.length ? `<section class="card qup" aria-labelledby="qh">
        <div class="qup-head"><h2 id="qh">Quick upload</h2>
          <label class="qup-who"><span class="sr">Patient</span><select id="qpt">${list.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></label></div>
        <div class="drop" id="drop">${I.upload}<div><b>Drop scans or reports</b><div class="small muted">DICOM, CD/zip exports, film photos, PDFs. Encrypted as they arrive.</div></div></div>
        <div class="qup-btns"><label class="btn b-files">${I.upload} Choose files<input type="file" id="fi" multiple hidden></label>
          <label class="btn b-cam">${I.camera} Take photo<input type="file" id="cam" accept="image/*" capture="environment" hidden></label></div>
        <div class="queue" id="queue"></div></section>` : ""}

      <section aria-labelledby="rh"><div class="section-h"><h2 id="rh">Recently added</h2>${list.length ? `<a class="small" href="#/patients" data-go="patients">All patients</a>` : ""}</div>
        ${recent.length ? `<div class="card rlist">${recent.map(studyRow).join("")}</div>`
          : `<div class="card empty">${I.scan}<h2>No studies yet</h2><p>Studies you upload show up here, newest first.</p></div>`}</section>
    </div>

    <aside class="hside">
      <section class="card" aria-labelledby="ph"><div class="side-h"><h2 id="ph">People</h2><button class="btn sm ghost" id="addp2" aria-label="Add patient">${I.plus}</button></div>
        ${list.length ? `<div class="plist2">${list.slice(0, 6).map(personRow).join("")}</div>${list.length > 6 ? `<a class="more" href="#/patients" data-go="patients">See all ${list.length}</a>` : ""}`
          : `<p class="pad small muted">No patients yet.</p>`}</section>
      <section class="card storage" aria-label="Storage"><div><b>${stats.files}</b><span>files</span></div><div><b>${stats.studies}</b><span>studies</span></div><div><b>${fmtBytes(stats.bytes)}</b><span>encrypted</span></div></section>
      ${tg.length ? `<section class="card" aria-labelledby="bh"><div class="side-h"><h2 id="bh">Destinations</h2><a class="small" href="#/backups" data-go="backups">Manage</a></div>
        <ul class="dests">${tg.map(t => `<li>${tone(!t.enabled ? "gray" : !t.connected || t.last_status === "error" ? "warn" : t.last_status === "ok" ? "ok" : "gray")}<span><b>${esc(t.name)}</b><span class="small muted">${!t.enabled ? "Paused" : !t.connected ? "Not connected" : t.last_status === "error" ? "Last run failed " + esc(ago(t.last_run)) : t.last_run ? "Last run " + esc(ago(t.last_run)) : "Not run yet"}</span></span></li>`).join("")}</ul></section>` : ""}
    </aside>
  </div></div>`;

  const addP = () => patientForm();
  $("#addp").onclick = addP; $("#addp2").onclick = addP;
  $("#upl").onclick = () => list.length ? ($("#fi") ? $("#fi").click() : go("upload")) : patientForm();
  $("#lock").onclick = async () => { await API.req("POST", "/api/vault/lock"); S.status.unlocked = false; render(); };
  const bn = $("#bnow");
  if (bn) bn.onclick = async () => {
    bn.disabled = true; bn.innerHTML = `<span class="spin"></span> Backing up`;
    try { const r = await API.req("POST", "/api/backups/run"); const bad = (r.results || []).filter(x => x.status !== "ok").length; toast(bad ? `${bad} destination${bad === 1 ? "" : "s"} failed. See Backups.` : "Backup finished"); }
    catch (e) { if (handleErr(e)) return; }
    viewHome().catch(handleErr);
  };
  $$("[data-step]", m).forEach(b => b.onclick = () => { const a = b.dataset.step; a === "addp" ? addP() : a === "upl" ? $("#upl").click() : go(a); });
  if (list.length) {
    const send = fl => uploadFiles(`/api/patients/${$("#qpt").value}/import`, fl, () => viewHome().catch(handleErr));
    wireDrop($("#drop"), send);
    $("#fi").onchange = e => send([...e.target.files]);
    $("#cam").onchange = e => send([...e.target.files]);
  }
}

// ------------------------------------------------------------------ patients
async function viewPatients() {
  const [list, stats] = await Promise.all([API.req("GET", "/api/patients"), API.req("GET", "/api/stats")]);
  S.patients = list;
  const m = main();
  m.innerHTML = `${mobileHead()}
  <div class="top"><div><h1>Patients</h1><p class="sub">${list.length} ${list.length === 1 ? "person" : "people"} · last backup ${ago(stats.last_backup_at)}</p></div>
    <div class="row"><div class="searchbar">${I.search}<input type="search" id="q" placeholder="Search name, MRN or date of birth" aria-label="Search patients"></div>
    <button class="btn primary" id="add">${I.plus} Add patient</button></div></div>
  <div class="kpis">
    <div class="card kpi"><div class="v">${stats.patients}</div><div class="l">Patients</div></div>
    <div class="card kpi"><div class="v">${stats.studies}</div><div class="l">Studies</div></div>
    <div class="card kpi"><div class="v">${stats.files}</div><div class="l">Files</div></div>
    <div class="card kpi"><div class="v">${fmtBytes(stats.bytes)}</div><div class="l">Stored, encrypted</div></div>
  </div>
  ${stats.last_backup_at ? "" : `<div class="note" style="margin-bottom:18px">${I.warn}<div>No backups yet. <a href="#/backups" data-go="backups">Add a backup destination</a> so a lost disk doesn't mean lost scans.</div></div>`}
  <div class="plist" id="pl"></div>
  <button class="fab" id="fab" aria-label="Upload">${I.upload}</button>`;
  const draw = q => {
    q = (q || "").toLowerCase();
    const rows = list.filter(p => !q || [p.name, p.mrn, p.date_of_birth, p.notes].some(v => (v || "").toLowerCase().includes(q)));
    $("#pl").innerHTML = rows.length ? rows.map(p => `<a class="card pcard" href="#/patient/${esc(p.id)}" data-go="patient/${esc(p.id)}">
      <div class="avatar">${esc(initials(p.name))}</div>
      <div><h3>${esc(p.name)}</h3><div class="meta">${p.date_of_birth ? `<span>${esc(fmtDate(p.date_of_birth))} (${age(p.date_of_birth)})</span>` : ""}${p.mrn ? `<span>MRN ${esc(p.mrn)}</span>` : ""}</div>
      <div class="stats"><span><b>${p.study_count}</b> ${p.study_count === 1 ? "study" : "studies"}</span><span><b>${p.file_count}</b> ${p.file_count === 1 ? "file" : "files"}</span>${p.last_study_date ? `<span class="muted">Last ${esc(fmtDate(p.last_study_date))}</span>` : ""}</div></div></a>`).join("")
      : list.length ? `<p class="muted">No patients match "${esc(q)}".</p>`
      : `<div class="card empty" style="grid-column:1/-1">${I.users}<h2>Add your first patient</h2><p>Create one record per person: you, a parent, a child. Each keeps their own studies and reports.</p><button class="btn primary" id="add2">${I.plus} Add patient</button></div>`;
    const a2 = $("#add2"); if (a2) a2.onclick = () => patientForm();
  };
  draw();
  $("#q").oninput = e => draw(e.target.value);
  $("#add").onclick = () => patientForm();
  $("#fab").onclick = () => go("upload");
}

function patientForm(p) {
  const m = modal(p ? "Edit patient" : "Add patient", `<form class="form" id="pf">
    <label class="f">Full name<input name="name" required value="${esc(p?.name)}" autocomplete="off"></label>
    <div class="grid2"><label class="f">Date of birth<input name="date_of_birth" type="date" value="${esc(p?.date_of_birth)}"></label>
    <label class="f">Sex<select name="sex"><option value=""></option>${["F", "M", "X"].map(s => `<option ${p?.sex === s ? "selected" : ""}>${s}</option>`).join("")}</select></label></div>
    <label class="f">MRN or patient ID <span class="hint">Optional, as printed on reports</span><input name="mrn" value="${esc(p?.mrn)}" autocomplete="off"></label>
    <label class="f">Notes<textarea name="notes" placeholder="Allergies to contrast, implants, physician contacts">${esc(p?.notes)}</textarea></label>
    <p class="err" id="err"></p></form>`, { footer: `${p ? '<button class="btn danger" id="del">Delete</button><span class="spacer"></span>' : ""}<button class="btn" data-close>Cancel</button><button class="btn primary" form="pf">${p ? "Save" : "Add patient"}</button>` });
  $("#pf", m.el).onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try { const r = p ? await API.req("PUT", `/api/patients/${p.id}`, body) : await API.req("POST", "/api/patients", body); m.close(); toast(p ? "Saved" : "Patient added"); go("patient", r.id); }
    catch (er) { $("#err", m.el).textContent = er.message; }
  };
  const d = $("#del", m.el);
  if (d) d.onclick = async () => { m.close(); if (await confirmBox("Delete patient?", `This permanently deletes ${esc(p.name)} and all ${p.file_count} files from this server. Existing backups keep their copy.`)) { await API.req("DELETE", `/api/patients/${p.id}`); toast("Deleted"); go("patients"); } };
}

async function viewPatient(id) {
  const [p, studies] = await Promise.all([API.req("GET", `/api/patients/${id}`), API.req("GET", `/api/patients/${id}/studies`)]);
  const thumbs = {};
  await Promise.all(studies.slice(0, 30).map(async s => { if (s.file_count) thumbs[s.id] = (await API.req("GET", `/api/studies/${s.id}/files`)).slice(0, 4); }));
  const m = main();
  m.innerHTML = `${mobileHead()}<div class="crumbs"><a href="#/patients" data-go="patients">Patients</a><span>/</span><span>${esc(p.name)}</span></div>
  <div class="card phead"><div class="avatar">${esc(initials(p.name))}</div>
    <div><h1>${esc(p.name)}</h1><div class="facts">
      ${p.date_of_birth ? `<span>Born <b>${esc(fmtDate(p.date_of_birth))}</b> (${age(p.date_of_birth)})</span>` : ""}${p.sex ? `<span>Sex <b>${esc(p.sex)}</b></span>` : ""}${p.mrn ? `<span>MRN <b>${esc(p.mrn)}</b></span>` : ""}
      <span><b>${p.study_count}</b> ${p.study_count === 1 ? "study" : "studies"}</span><span><b>${p.file_count}</b> ${p.file_count === 1 ? "file" : "files"}</span></div>
      ${p.notes ? `<p class="small muted" style="margin-top:8px;white-space:pre-wrap">${esc(p.notes)}</p>` : ""}</div>
    <div class="row"><button class="btn" id="edit">Edit</button><button class="btn primary" id="up">${I.upload} Upload</button></div></div>
  <div class="drop" id="drop">${I.upload}<div><b>Drop scans or reports here</b><div class="small muted">DICOM files or a whole CD/zip export, JPG/PNG photos of films, PDF reports. DICOM is grouped into studies automatically.</div></div>
    <div class="row" style="justify-content:center"><label tabindex="0" role="button" class="btn sm">Choose files<input type="file" id="fi" multiple hidden accept=".dcm,.dicom,.zip,image/*,application/pdf,application/dicom,application/zip,*/*"></label>
    <label tabindex="0" role="button" class="btn sm">${I.camera} Take photo<input type="file" id="cam" accept="image/*" capture="environment" hidden></label></div></div>
  <div class="queue" id="queue"></div>
  <div class="section-h"><h2>Studies</h2><button class="btn sm" id="ns">${I.plus} New study</button></div>
  <div class="timeline" id="tl">${studies.length ? studies.map(s => `<a class="card study" href="#/study/${esc(s.id)}" data-go="study/${esc(s.id)}">
    <div class="date">${esc(s.study_date ? fmtDate(s.study_date) : "No date")}<small>${s.modality ? esc(s.modality) : ""}</small></div>
    <div><h3>${esc(s.title)}</h3><div class="small muted">${[s.facility, `${s.file_count} file${s.file_count === 1 ? "" : "s"}`].filter(Boolean).map(esc).join(" · ")}</div></div>
    <div class="film">${(thumbs[s.id] || []).map(f => `<div class="t">${f.has_thumbnail ? `<img alt="" loading="lazy" src="${esc(API.thumbUrl(f.id))}">` : f.kind === "report" ? I.pdf : I.file}</div>`).join("")}</div></a>`).join("")
    : `<div class="card empty">${I.scan}<h2>No studies yet</h2><p>Upload the files an imaging center gave you, or create a study first and add files to it.</p></div>`}</div>`;
  $("#edit").onclick = () => patientForm(p);
  $("#ns").onclick = () => studyForm(p.id);
  $("#up").onclick = () => $("#fi").click();
  wireDrop($("#drop"), files => uploadFiles(`/api/patients/${p.id}/import`, files, () => viewPatient(id)));
  $("#fi").onchange = e => uploadFiles(`/api/patients/${p.id}/import`, [...e.target.files], () => viewPatient(id));
  $("#cam").onchange = e => uploadFiles(`/api/patients/${p.id}/import`, [...e.target.files], () => viewPatient(id));
}

function wireDrop(el, cb) {
  ["dragenter", "dragover"].forEach(t => el.addEventListener(t, e => { e.preventDefault(); el.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => el.addEventListener(t, e => { e.preventDefault(); el.classList.remove("over"); }));
  el.addEventListener("drop", e => { const f = [...e.dataTransfer.files]; if (f.length) cb(f); });
}

async function uploadFiles(path, files, done) {
  const q = $("#queue");
  let ok = 0, skipped = 0;
  // Patient imports go up together so reports land in the same study as the
  // DICOM beside them. Big folder exports are split into batches (the server
  // holds every file of a request open); reports and photos go in the first.
  const BATCH = 200;
  let batches = files.map(f => [f]);
  if (path.includes("/import") && files.length > 1) {
    const loose = f => /\.(pdf|jpe?g|png|gif|heic|tiff?|txt|docx?|zip)$/i.test(f.name);
    const sorted = [...files.filter(loose), ...files.filter(f => !loose(f))];
    batches = [];
    for (let i = 0; i < sorted.length; i += BATCH) batches.push(sorted.slice(i, i + BATCH));
  }
  for (const batch of batches) {
    const row = document.createElement("div");
    row.className = "qitem";
    const label = batch.length > 1 ? `${batch.length} files (${batch.slice(0, 3).map(f => f.name).join(", ")}${batch.length > 3 ? "…" : ""})` : batch[0].name;
    row.innerHTML = `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(label)}</span><span class="muted nowrap">${fmtBytes(batch.reduce((a, f) => a + f.size, 0))}</span><div class="bar"><i></i></div>`;
    q.appendChild(row);
    const fd = new FormData(); batch.forEach(f => fd.append("file", f, f.name));
    try {
      const r = await API.upload(path, fd, p => { $("i", row).style.width = Math.round(p * 95) + "%"; });
      $("i", row).style.width = "100%";
      const n = Array.isArray(r) ? r.length : r.files.length; ok += n; skipped += (r.skipped || []).length;
      row.lastElementChild.previousElementSibling.textContent = n > 1 ? `${n} files encrypted` : "Encrypted ✓";
    } catch (e) {
      if (handleErr(e) && e.code) return;
      row.lastElementChild.previousElementSibling.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    }
  }
  toast(`${ok} file${ok === 1 ? "" : "s"} stored` + (skipped ? `, ${skipped} skipped` : ""));
  setTimeout(done, 700);
}

function studyForm(pid, s) {
  const mods = ["CT", "MR", "CR", "DX", "US", "MG", "NM", "PT", "XA", "RF", "OT"];
  const m = modal(s ? "Edit study" : "New study", `<form class="form" id="sf">
    <label class="f">Title<input name="title" required value="${esc(s?.title)}" placeholder="MRI left knee"></label>
    <div class="grid2"><label class="f">Study date<input name="study_date" type="date" value="${esc(s?.study_date)}"></label>
    <label class="f">Modality<select name="modality"><option value=""></option>${mods.map(x => `<option ${s?.modality === x ? "selected" : ""}>${x}</option>`).join("")}</select></label></div>
    <label class="f">Imaging center<input name="facility" value="${esc(s?.facility)}" placeholder="Lakeside Imaging Center"></label>
    <label class="f">Notes<textarea name="description">${esc(s?.description)}</textarea></label>
    <p class="err" id="err"></p></form>`, { footer: `${s ? '<button class="btn danger" id="del">Delete</button><span class="spacer"></span>' : ""}<button class="btn" data-close>Cancel</button><button class="btn primary" form="sf">${s ? "Save" : "Create study"}</button>` });
  $("#sf", m.el).onsubmit = async e => {
    e.preventDefault(); const body = Object.fromEntries(new FormData(e.target));
    try { const r = s ? await API.req("PUT", `/api/studies/${s.id}`, body) : await API.req("POST", `/api/patients/${pid}/studies`, body); m.close(); go("study", r.id); }
    catch (er) { $("#err", m.el).textContent = er.message; }
  };
  const d = $("#del", m.el);
  if (d) d.onclick = async () => { m.close(); if (await confirmBox("Delete study?", `Deletes "${esc(s.title)}" and its ${s.file_count} files from this server.`)) { await API.req("DELETE", `/api/studies/${s.id}`); toast("Deleted"); go("patient", pid); } };
}

async function viewStudy(id) {
  const s = await API.req("GET", `/api/studies/${id}`);
  const [p, files] = await Promise.all([API.req("GET", `/api/patients/${s.patient_id}`), API.req("GET", `/api/studies/${id}/files`)]);
  const m = main();
  const reports = files.filter(f => f.kind === "report"), imgs = files.filter(f => f.kind !== "report");
  const tile = f => `<button class="tile" data-f="${esc(f.id)}"><div class="img">${f.has_thumbnail ? `<img alt="" loading="lazy" src="${esc(API.thumbUrl(f.id))}">` : f.kind === "report" ? I.pdf : I.file}</div>
    <div class="cap"><b>${esc(f.dicom?.series_description || f.filename)}</b><span>${f.kind === "dicom" ? `DICOM${f.dicom?.instance_number ? " #" + esc(f.dicom.instance_number) : ""}` : f.kind === "report" ? "Report" : esc(f.kind)} · ${fmtBytes(f.size_bytes)}</span></div></button>`;
  m.innerHTML = `${mobileHead()}<div class="crumbs"><a href="#/patients" data-go="patients">Patients</a><span>/</span><a href="#/patient/${esc(p.id)}" data-go="patient/${esc(p.id)}">${esc(p.name)}</a><span>/</span><span>${esc(s.title)}</span></div>
  <div class="top"><div><h1>${esc(s.title)}</h1><p class="sub">${[s.study_date && fmtDate(s.study_date), s.modality, s.facility, s.referring_physician && "Ref. " + s.referring_physician, s.accession && "Acc. " + s.accession].filter(Boolean).map(esc).join(" · ")}</p>
    ${s.description ? `<p class="small" style="margin-top:8px;max-width:70ch">${esc(s.description)}</p>` : ""}</div>
    <div class="row"><button class="btn" id="edit">Edit</button>${imgs.length ? `<button class="btn" id="cmp" title="Show next to another study of this patient">Compare</button>` : ""}<label tabindex="0" role="button" class="btn primary">${I.upload} Add files<input type="file" id="fi" multiple hidden></label></div></div>
  <div class="drop" id="drop" style="padding:16px">${I.upload}<div class="small"><b>Drop more files for this study</b></div></div>
  <div class="queue" id="queue"></div>
  ${reports.length ? `<div class="section-h"><h2>Reports</h2></div><div class="lightbox">${reports.map(tile).join("")}</div>` : ""}
  <div class="section-h"><h2>Images</h2><span class="muted small">${imgs.length} file${imgs.length === 1 ? "" : "s"}</span></div>
  ${imgs.length ? `<div class="lightbox">${imgs.map(tile).join("")}</div>` : `<div class="card empty">${I.scan}<p>No images in this study yet.</p></div>`}`;
  $("#edit").onclick = () => studyForm(p.id, s);
  if ($("#cmp")) $("#cmp").onclick = () => pickCompare(id).catch(handleErr);
  const up = fl => uploadFiles(`/api/studies/${id}/files?format=full`, fl, () => viewStudy(id));
  wireDrop($("#drop"), up);
  $("#fi").onchange = e => up([...e.target.files]);
  $$(".tile", m).forEach(t => t.onclick = () => viewer(files, files.findIndex(f => f.id === t.dataset.f), () => viewStudy(id)));
}

const CT_PRESETS = [["Brain", 40, 80], ["Subdural", 75, 215], ["Stroke", 40, 40], ["Soft tissue", 50, 400], ["Mediastinum", 50, 350], ["Liver", 60, 160], ["Lung", -600, 1500], ["Bone", 400, 1800]];
const seriesKey = f => f.kind === "dicom" ? (f.dicom?.series_uid || f.dicom?.series_description || "series") : null;

function viewer(files, idx, refresh) {
  const m = modal(files[idx].filename, `<div class="viewer">
    <div class="stage" id="stage" tabindex="0" aria-label="Image. Drag to pan or adjust window, pinch or Ctrl+scroll to zoom.">
      <div class="hud" id="hud"></div>
      <div class="vtools" id="vtools"></div>
    </div><aside id="side"></aside></div>`, { wide: true });
  $(".modal .body", m.el).style.padding = "0";
  const stage = $("#stage", m.el), modalEl = $(".modal", m.el);
  // view state survives moving between slices of one series
  const V = { zoom: 1, px: 0, py: 0, tool: matchMedia("(pointer:coarse)").matches ? "pan" : "wl", inv: false, c: null, w: null, base: null, frame: null, seriesOf: null };
  let f, im, info = null, renderSeq = 0, lastUrl = null, settleT = null;

  const series = () => { const k = seriesKey(f); return k ? files.filter(x => seriesKey(x) === k) : [f]; };
  const isGray = () => info && !info.rgb;
  const units = () => info && info.server ? (info.modality === "CT" ? "HU" : "") : "";

  function filterFor() {
    // Live window/level applied in the browser relative to the image as rendered (base).
    let s = 1, i = 0;
    if (isGray() && V.base && V.c != null) {
      const { c: c0, w: w0 } = V.base, c1 = V.c, w1 = Math.max(1, V.w);
      s = w0 / w1; i = (c0 - c1) / w1 + 0.5 - w0 / (2 * w1);
    }
    if (V.inv) { s = -s; i = 1 - i; }
    ["R", "G", "B"].forEach(ch => { const fn = $("#svwl feFunc" + ch); fn.setAttribute("slope", s.toFixed(5)); fn.setAttribute("intercept", i.toFixed(5)); });
  }
  function apply() {
    if (im) { im.style.transform = `translate(${V.px}px,${V.py}px) scale(${V.zoom})`; im.style.filter = "url(#svwl)"; }
    filterFor();
    const ser = series(), si = ser.indexOf(f);
    $("#hud", m.el).innerHTML = [
      isGray() && V.c != null ? `<span>W ${fmtN(V.w)} · L ${fmtN(V.c)}${units() ? " " + units() : ""}</span>` : "",
      ser.length > 1 ? `<span>Image ${si + 1} / ${ser.length}</span>` : "",
      info && info.frames > 1 ? `<span>Frame ${V.frame + 1} / ${info.frames}</span>` : "",
      `<span>${Math.round(V.zoom * 100)}%</span>`].filter(Boolean).join("");
    const wi = $("#vw", m.el), li = $("#vl", m.el);
    if (wi && document.activeElement !== wi) wi.value = V.w != null ? fmtN(V.w) : "";
    if (li && document.activeElement !== li) li.value = V.c != null ? fmtN(V.c) : "";
    $$("[data-tool]", m.el).forEach(b => b.classList.toggle("on", b.dataset.tool === V.tool));
    const ib = $("[data-act=inv]", m.el); if (ib) ib.classList.toggle("on", V.inv);
  }
  const fmtN = n => Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10;

  async function rerender(force) {
    // Ask the server for a true window/level render from the original pixel values.
    if (!info || !info.server || !isGray()) return;
    if (!force && V.base && V.base.c === V.c && V.base.w === V.w && V.base.frame === V.frame) return;
    const seq = ++renderSeq, fid = f.id;
    try {
      const r = await API.render(fid, { wc: V.c, ww: V.w, frame: V.frame });
      if (seq !== renderSeq || fid !== f.id) return;
      await new Promise(res => { const t = new Image(); t.onload = res; t.onerror = res; t.src = r.url; });
      if (seq !== renderSeq) return;
      im.src = r.url; if (lastUrl && lastUrl !== r.url && lastUrl.startsWith("blob:")) URL.revokeObjectURL(lastUrl); lastUrl = r.url;
      V.base = { c: r.wc, w: r.ww, frame: V.frame }; apply();
    } catch (e) { /* keep the client-side approximation */ }
  }
  const settle = () => { clearTimeout(settleT); settleT = setTimeout(() => rerender(), 220); };
  function setWL(c, w, now) { V.c = c; V.w = Math.max(1, w); apply(); now ? rerender() : settle(); }

  async function load(keepView) {
    f = files[idx];
    $("header h2", m.el).textContent = f.filename;
    const sameSeries = keepView && V.seriesOf && V.seriesOf === seriesKey(f);
    if (!sameSeries) Object.assign(V, { zoom: 1, px: 0, py: 0, c: null, w: null, inv: false });
    V.base = null; V.frame = null; info = null; im = null;
    stage.querySelectorAll("img,iframe,.empty").forEach(e => e.remove());
    const rep = API.reportImage && API.reportImage(f);
    let src = null;
    if (rep) src = rep;
    else if (f.kind === "report" && (f.content_type || "").includes("pdf")) {
      stage.insertAdjacentHTML("afterbegin", `<iframe title="${esc(f.filename)}" src="${esc(API.fileUrl(f.id))}"></iframe>`);
    } else if (f.kind === "dicom") {
      try {
        const r = await API.render(f.id, { wc: sameSeries ? V.c : null, ww: sameSeries ? V.w : null });
        info = { server: true, rgb: r.rgb, frames: r.frames, modality: r.modality, min: r.min, max: r.max, dc: r.dc, dw: r.dw };
        V.frame = r.frame; src = r.url; lastUrl = r.url;
        if (!r.rgb) { V.base = { c: r.wc, w: r.ww, frame: r.frame }; if (V.c == null) { V.c = r.wc; V.w = r.ww; } }
      } catch (e) {
        if (f.has_thumbnail) { src = (API.previewUrl || API.thumbUrl)(f.id); info = { server: false, rgb: false, frames: 1, min: 0, max: 255, dc: 128, dw: 256 }; }
      }
    } else if (f.kind === "image") {
      src = API.fileUrl(f.id); info = { server: false, rgb: false, frames: 1, min: 0, max: 255, dc: 128, dw: 256 };
    }
    if (info && !info.server) { V.base = { c: 128, w: 256 }; if (!sameSeries || V.c == null) { V.c = 128; V.w = 256; } }
    if (src) { stage.insertAdjacentHTML("afterbegin", `<img id="im" alt="${esc(f.filename)}" src="${esc(src)}" draggable="false">`); im = $("#im", m.el); if (rep) im.style.background = "#fff"; }
    else if (!stage.querySelector("iframe")) stage.insertAdjacentHTML("afterbegin", `<div class="empty" style="color:#9fb3b6">${I.file}<p>No preview for this file type.${f.kind === "dicom" ? " The DICOM is stored intact; download it for a full DICOM viewer." : ""}</p></div>`);
    V.seriesOf = seriesKey(f);
    drawTools(); drawSide(); apply();
  }

  function drawTools() {
    const img = !!im && !(API.reportImage && API.reportImage(f));
    const presets = info && info.server && isGray() ? [["Default", info.dc, info.dw], ["Full range", (info.min + info.max) / 2, Math.max(1, info.max - info.min)], ...(info.modality === "CT" ? CT_PRESETS : [])] : [];
    $("#vtools", m.el).innerHTML = img ? `
      <div class="vseg" role="group" aria-label="Drag tool"><button data-tool="pan" title="Drag to pan (P)">Pan</button><button data-tool="wl" title="Drag to adjust window/level (W)">W/L</button></div>
      ${presets.length ? `<select id="pre" aria-label="Window preset"><option value="">Presets</option>${presets.map(([n, c, w], i) => `<option value="${i}">${esc(n)}${n === "Default" || n === "Full range" ? "" : ` ${c}/${w}`}</option>`).join("")}</select>` : ""}
      <button data-act="inv" title="Invert (I)">Invert</button>
      <button data-act="zout" aria-label="Zoom out">−</button><button data-act="zin" aria-label="Zoom in">+</button>
      <button data-act="reset" title="Reset view (0)">Reset</button>
      ${document.fullscreenEnabled ? `<button data-act="fs" title="Fullscreen (F)">Full screen</button>` : ""}` : "";
    $$("[data-tool]", m.el).forEach(b => b.onclick = () => { V.tool = b.dataset.tool; apply(); });
    const pre = $("#pre", m.el); if (pre) pre.onchange = () => { const p = presets[+pre.value]; if (p) setWL(p[1], p[2], true); pre.value = ""; };
    $$("[data-act]", m.el).forEach(b => b.onclick = () => act(b.dataset.act));
  }
  function act(a) {
    if (a === "inv") V.inv = !V.inv;
    if (a === "zin") zoomAt(1.4);
    if (a === "zout") zoomAt(1 / 1.4);
    if (a === "reset") { V.zoom = 1; V.px = V.py = 0; V.inv = false; if (info) { setWL(info.server ? info.dc : 128, info.server ? info.dw : 256, true); return; } }
    if (a === "fs") { (document.fullscreenElement ? document.exitFullscreen() : modalEl.requestFullscreen()).catch(() => toast("Full screen isn't allowed here")); return; }
    apply();
  }
  function zoomAt(k, cx, cy) {
    const r = stage.getBoundingClientRect();
    const x = (cx ?? r.left + r.width / 2) - (r.left + r.width / 2), y = (cy ?? r.top + r.height / 2) - (r.top + r.height / 2);
    const nz = Math.max(.25, Math.min(16, V.zoom * k)), q = nz / V.zoom;
    V.px = x - (x - V.px) * q; V.py = y - (y - V.py) * q; V.zoom = nz; apply();
  }
  function step(d) {
    if (info && info.frames > 1) { V.frame = Math.max(0, Math.min(info.frames - 1, V.frame + d)); rerender(true); apply(); return; }
    const ser = series(), si = ser.indexOf(f), n = ser[si + d];
    if (n) { idx = files.indexOf(n); load(true); }
  }

  function drawSide() {
    const d = f.dicom || {};
    const rows = [["Type", f.kind === "dicom" ? "DICOM" : f.kind === "report" ? "Report" : f.content_type], ["Size", fmtBytes(f.size_bytes)], ["Uploaded", fmtDT(f.uploaded_at)],
      ["Modality", d.modality], ["Study date", d.study_date && fmtDate(d.study_date)], ["Series", d.series_description], ["Body part", d.body_part], ["Institution", d.institution],
      ["Instance", d.instance_number], ["Matrix", d.rows && `${d.rows} × ${d.columns}`], ["Frames", d.frames !== "1" && d.frames], ["DICOM name", d.patient_name], ["SHA-256", f.sha256 && f.sha256.slice(0, 16) + "…"]].filter(r => r[1]);
    const ser = series();
    $("#side", m.el).innerHTML = `<div class="row"><button class="btn sm" id="prev" ${idx ? "" : "disabled"}>‹ Prev</button><span class="muted small">${idx + 1} / ${files.length}</span><button class="btn sm" id="next" ${idx < files.length - 1 ? "" : "disabled"}>Next ›</button></div>
      ${im && isGray() ? `<div class="grid2 wl"><label class="f">Window${units() ? ` (${units()})` : ""}<input id="vw" type="number" step="any" inputmode="decimal"></label><label class="f">Level${units() ? ` (${units()})` : ""}<input id="vl" type="number" step="any" inputmode="decimal"></label></div>` : ""}
      ${ser.length > 1 ? `<label class="f small">Image in series<input type="range" id="slc" min="1" max="${ser.length}" value="${ser.indexOf(f) + 1}"></label>` : ""}
      ${info && info.frames > 1 ? `<label class="f small">Frame<input type="range" id="frm" min="1" max="${info.frames}" value="${V.frame + 1}"></label>` : ""}
      ${im && !(API.reportImage && API.reportImage(f)) ? `<p class="small muted">${matchMedia("(pointer:coarse)").matches ? "Pinch to zoom, double-tap to zoom in. Pick <b>W/L</b> and drag: sideways for window, up/down for level." : "Drag with <b>W/L</b>: sideways = window, up/down = level. Ctrl+scroll or pinch zooms, scroll steps through slices, double-click zooms. Keys: ← → files, ↑ ↓ slices, + − 0, I, W, P, F."}</p>` : ""}
      <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <div class="row">${f.study_id && f.kind !== "report" ? `<button class="btn sm" id="cmpb" title="Show next to another study of this patient">Compare</button>` : ""}<a class="btn sm" href="${esc(API.fileUrl(f.id, 1))}" download="${esc(f.filename)}">${I.dl} Original</a><button class="btn sm danger" id="del">Delete</button></div>
      <p class="small muted">Stored encrypted (AES-256-GCM). Decrypted only while you view it.</p>`;
    $("#prev", m.el).onclick = () => { idx--; load(true); };
    $("#next", m.el).onclick = () => { idx++; load(true); };
    const wi = $("#vw", m.el), li = $("#vl", m.el);
    if (wi) { const upd = () => { const w = parseFloat(wi.value), c = parseFloat(li.value); if (!isNaN(w) && !isNaN(c)) setWL(c, w, true); }; wi.onchange = upd; li.onchange = upd; }
    const slc = $("#slc", m.el); if (slc) slc.oninput = () => { const n = ser[slc.value - 1]; if (n && n !== f) { idx = files.indexOf(n); load(true); } };
    const frm = $("#frm", m.el); if (frm) frm.oninput = () => { V.frame = frm.value - 1; apply(); clearTimeout(settleT); settleT = setTimeout(() => rerender(true), 120); };
    const cb = $("#cmpb", m.el); if (cb) cb.onclick = () => { m.close(); pickCompare(f.study_id, f).catch(handleErr); };
    $("#del", m.el).onclick = async () => { if (await confirmBox("Delete file?", `Delete ${esc(f.filename)} from this server?`)) { await API.req("DELETE", `/api/files/${f.id}`); m.close(); toast("Deleted"); refresh(); } };
  }

  // ---- pointer input: pan, W/L drag, pinch zoom, double-tap
  const pts = new Map(); let drag = null, pinch = null, lastTap = 0;
  stage.addEventListener("pointerdown", e => {
    if (!im || e.target.closest(".vtools")) return;
    stage.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: V.zoom, mx: (a[0] + b[0]) / 2, my: (a[1] + b[1]) / 2, px: V.px, py: V.py }; drag = null; return;
    }
    const now = Date.now();
    if (now - lastTap < 300) { V.zoom > 1.05 ? (V.zoom = 1, V.px = V.py = 0, apply()) : zoomAt(2.5, e.clientX, e.clientY); lastTap = 0; return; }
    lastTap = now;
    const tool = e.button === 2 || e.shiftKey ? "wl" : e.button === 1 ? "pan" : V.tool;
    drag = { x: e.clientX, y: e.clientY, px: V.px, py: V.py, c: V.c, w: V.w, tool };
  });
  stage.addEventListener("pointermove", e => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pinch && pts.size >= 2) {
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      V.zoom = Math.max(.25, Math.min(16, pinch.z * d / pinch.d)); V.px = pinch.px + (a[0] + b[0]) / 2 - pinch.mx; V.py = pinch.py + (a[1] + b[1]) / 2 - pinch.my; apply(); return;
    }
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (drag.tool === "wl" && isGray() && drag.w != null) {
      const sens = Math.max(.5, ((info.max - info.min) || 256) / 700);
      V.w = Math.max(1, drag.w + dx * sens); V.c = drag.c - dy * sens; apply(); settle();
    } else { V.px = drag.px + dx; V.py = drag.py + dy; apply(); }
  });
  const up = e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; if (!pts.size) drag = null; };
  stage.addEventListener("pointerup", up); stage.addEventListener("pointercancel", up);
  stage.addEventListener("contextmenu", e => { if (im) e.preventDefault(); });
  stage.addEventListener("wheel", e => {
    if (!im) return; e.preventDefault();
    const stack = series().length > 1 || (info && info.frames > 1);
    if (e.ctrlKey || e.metaKey || !stack) zoomAt(Math.exp(-Math.max(-60, Math.min(60, e.deltaY)) * (e.ctrlKey ? .01 : .004)), e.clientX, e.clientY);
    else { stage._acc = (stage._acc || 0) + e.deltaY; if (Math.abs(stage._acc) >= 40) { step(Math.sign(stage._acc)); stage._acc = 0; } }
  }, { passive: false });
  const key = e => {
    if (!document.body.contains(m.el)) return document.removeEventListener("keydown", key);
    if (e.target.matches("input,select,textarea")) return;
    const k = e.key;
    if (k === "ArrowRight" && idx < files.length - 1) { idx++; load(true); }
    else if (k === "ArrowLeft" && idx > 0) { idx--; load(true); }
    else if (k === "ArrowDown") { e.preventDefault(); step(1); }
    else if (k === "ArrowUp") { e.preventDefault(); step(-1); }
    else if (k === "+" || k === "=") zoomAt(1.25);
    else if (k === "-") zoomAt(1 / 1.25);
    else if (k === "0") act("reset");
    else if (k === "i" || k === "I") act("inv");
    else if (k === "w" || k === "W") { V.tool = "wl"; apply(); }
    else if (k === "p" || k === "P") { V.tool = "pan"; apply(); }
    else if (k === "f" || k === "F") act("fs");
  };
  document.addEventListener("keydown", key);
  load(false);
}

// ------------------------------------------------------------------ compare two studies side by side
const imageFiles = files => files.filter(f => f.kind === "dicom" || f.kind === "image");
const wordsOf = t => new Set(String(t || "").toLowerCase().match(/[a-z0-9]+/g) || []);
function seriesGroups(files) {
  const out = [], by = {};
  for (const f of imageFiles(files)) {
    const k = seriesKey(f) || "photos";
    if (!by[k]) out.push(by[k] = { key: k, label: k === "photos" ? "Photos and scans" : (f.dicom?.series_description || "Series"), files: [] });
    by[k].files.push(f);
  }
  out.forEach((g, i) => { if (out.filter(x => x.label === g.label).length > 1) g.label += ` (${i + 1})`; });
  return out;
}
function bestSeries(groups, label) {
  // The series whose description shares the most words with the other side's, else the first.
  const w = wordsOf(label); let best = 0, top = -1;
  groups.forEach((g, i) => { const v = wordsOf(g.label), n = [...w].filter(x => v.has(x)).length / (new Set([...w, ...v]).size || 1); if (n > top) { top = n; best = i; } });
  return best;
}
function gapText(s) {
  if (s.days_apart == null || !s.relation) return "";
  if (s.relation === "same day") return "Same day";
  const d = s.days_apart, t = d < 45 ? `${d} day${d === 1 ? "" : "s"}` : d < 548 ? `${Math.round(d / 30.4)} months` : `${(d / 365.25).toFixed(1).replace(/\.0$/, "")} years`;
  return `${t} ${s.relation === "prior" ? "earlier" : "later"}`;
}

async function pickCompare(sid, fromFile) {
  const [base, rows] = await Promise.all([API.req("GET", `/api/studies/${sid}`), API.req("GET", `/api/studies/${sid}/similar`)]);
  const usable = rows.filter(r => r.image_count > 0), sug = usable.filter(r => r.suggested), rest = usable.filter(r => !r.suggested);
  const item = r => `<button class="card cmpick" data-s="${esc(r.id)}">
    <div class="film">${(r.thumbnail_ids || []).slice(0, 2).map(id => `<div class="t"><img alt="" loading="lazy" src="${esc(API.thumbUrl(id))}"></div>`).join("") || `<div class="t">${I.scan}</div>`}</div>
    <div><b>${esc(r.title)}</b><div class="small muted">${[r.study_date ? fmtDate(r.study_date) : "No date", gapText(r), `${r.image_count} image${r.image_count === 1 ? "" : "s"}`].filter(Boolean).map(esc).join(" · ")}</div>
    ${r.match_reasons.length ? `<div class="row" style="gap:4px;margin-top:6px">${r.match_reasons.map(x => `<span class="badge ${x === "Other side" ? "warn" : ""}">${esc(x)}</span>`).join("")}</div>` : ""}</div>${I.chev}</button>`;
  const m = modal("Compare side by side", `<p class="small muted" style="margin:0 0 12px">Pick a study to show next to <b>${esc(base.title)}</b>${base.study_date ? ` (${esc(fmtDate(base.study_date))})` : ""}. Zoom, pan, window/level and slice scrolling stay linked.</p>
    ${!usable.length ? `<div class="empty" style="padding:24px 8px">${I.scan}<p>${esc(rows.length ? "This patient's other studies have no images to compare." : "This patient has no other studies yet. Upload a prior study to compare.")}</p></div>` : ""}
    ${sug.length ? `<div class="section-h" style="margin-top:0"><h2>Suggested matches</h2></div><div class="stack cmlist">${sug.map(item).join("")}</div>` : ""}
    ${rest.length ? `<div class="section-h"><h2>${sug.length ? "Other studies" : "Studies for this patient"}</h2></div><div class="stack cmlist">${rest.map(item).join("")}</div>` : ""}`);
  $$("[data-s]", m.el).forEach(b => b.onclick = () => { m.close(); compareView(base, usable.find(r => r.id === b.dataset.s), fromFile).catch(handleErr); });
}

let cmpSeq = 0;
async function compareView(sa, sb, fromFile) {
  const [fa, fb] = await Promise.all([sa, sb].map(s => API.req("GET", `/api/studies/${s.id}/files`)));
  // Earlier study on the left, like films hung prior then current.
  let pair = [[sa, fa], [sb, fb]];
  if ((sb.study_date || "") < (sa.study_date || "") || (!sb.study_date && sa.study_date)) pair.reverse();
  const coarse = matchMedia("(pointer:coarse)").matches;
  const V = { tool: coarse ? "pan" : "wl", sync: true, off: 0, active: 0 };
  const P = pair.map(([s, files], n) => ({ n, s, groups: seriesGroups(files), g: 0, i: 0, f: null, im: null, info: null, base: null,
    c: null, w: null, frame: 0, zoom: 1, px: 0, py: 0, inv: false, seq: 0, lastUrl: null, t: null, fid: `svcmp${++cmpSeq}` }));
  const fromPane = fromFile ? P.findIndex(p => p.s.id === fromFile.study_id) : -1;
  if (fromPane >= 0) { const p = P[fromPane]; p.g = Math.max(0, p.groups.findIndex(g => g.files.some(f => f.id === fromFile.id))); p.i = p.groups[p.g].files.findIndex(f => f.id === fromFile.id); V.active = fromPane; }
  const lead = P[fromPane >= 0 ? fromPane : 1], other = P[lead.n ? 0 : 1];
  other.g = bestSeries(other.groups, lead.groups[lead.g]?.label);

  const pane = p => `<section class="cpane" data-p="${p.n}">
    <div class="cphead"><div class="cpt"><b>${esc(p.s.title)}</b><span>${[p.s.study_date ? fmtDate(p.s.study_date) : "No date", p.s.modality].filter(Boolean).map(esc).join(" · ")}</span></div>
      ${p.groups.length > 1 ? `<select data-ser aria-label="Series">${p.groups.map((g, i) => `<option value="${i}" ${i === p.g ? "selected" : ""}>${esc(g.label)} (${g.files.length})</option>`).join("")}</select>` : ""}</div>
    <div class="stage" tabindex="0" aria-label="${esc(p.s.title)}. Drag to pan or adjust window, scroll for slices."><div class="hud"></div>
      <svg width="0" height="0" style="position:absolute" aria-hidden="true"><filter id="${p.fid}" color-interpolation-filters="sRGB"><feComponentTransfer><feFuncR type="linear" slope="1" intercept="0"/><feFuncG type="linear" slope="1" intercept="0"/><feFuncB type="linear" slope="1" intercept="0"/></feComponentTransfer></filter></svg></div>
    <div class="cpslice"><input type="range" data-slc min="1" max="1" value="1" aria-label="Slice"></div></section>`;
  const m = modal("Compare studies", `<div class="cmp">
    <div class="vtools cmpbar">
      <div class="vseg" role="group" aria-label="Drag tool"><button data-tool="pan" title="Drag to pan (P)">Pan</button><button data-tool="wl" title="Drag to adjust window/level (W)">W/L</button></div>
      <button data-act="sync" title="Link zoom, pan, window/level and slices (L)">Linked</button>
      <button data-act="inv" title="Invert (I)">Invert</button>
      <button data-act="zout" aria-label="Zoom out">−</button><button data-act="zin" aria-label="Zoom in">+</button>
      <button data-act="reset" title="Reset view (0)">Reset</button>
      <button data-act="swap" title="Swap sides">Swap</button>
      ${document.fullscreenEnabled ? `<button data-act="fs" title="Full screen (F)">Full screen</button>` : ""}
    </div><div class="cmpgrid">${P.map(pane).join("")}</div></div>`, { wide: true });
  const modalEl = $(".modal", m.el); modalEl.classList.add("cmpmodal");
  $(".modal .body", m.el).style.padding = "0";
  P.forEach(p => { p.el = $(`[data-p="${p.n}"]`, m.el); p.stage = $(".stage", p.el); });

  const gray = p => p.info && !p.info.rgb;
  const files = p => p.groups[p.g]?.files || [];
  const count = p => files(p).length > 1 ? files(p).length : (p.info?.frames || 1);
  const pos = p => files(p).length > 1 ? p.i : p.frame;
  const frac = p => count(p) > 1 ? pos(p) / (count(p) - 1) : 0;
  const fmtN = n => Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10;
  const def = p => p.info && p.info.server ? [p.info.dc, p.info.dw] : [128, 256];
  const others = p => V.sync ? P.filter(q => q !== p) : [];

  function filterFor(p) {
    let s = 1, i = 0;
    if (gray(p) && p.base && p.c != null) { const { c: c0, w: w0 } = p.base, w1 = Math.max(1, p.w); s = w0 / w1; i = (c0 - p.c) / w1 + 0.5 - w0 / (2 * w1); }
    if (p.inv) { s = -s; i = 1 - i; }
    ["R", "G", "B"].forEach(ch => { const fn = $(`#${p.fid} feFunc${ch}`, m.el); fn.setAttribute("slope", s.toFixed(5)); fn.setAttribute("intercept", i.toFixed(5)); });
  }
  function apply(p) {
    if (p.im) { p.im.style.transform = `translate(${p.px}px,${p.py}px) scale(${p.zoom})`; p.im.style.filter = `url(#${p.fid})`; }
    filterFor(p);
    const u = p.info && p.info.server && p.info.modality === "CT" ? " HU" : "";
    $(".hud", p.el).innerHTML = [gray(p) && p.c != null ? `<span>W ${fmtN(p.w)} · L ${fmtN(p.c)}${u}</span>` : "",
      count(p) > 1 ? `<span>${files(p).length > 1 ? "Image" : "Frame"} ${pos(p) + 1} / ${count(p)}</span>` : "", `<span>${Math.round(p.zoom * 100)}%</span>`].filter(Boolean).join("");
    const r = $("[data-slc]", p.el); r.max = count(p); if (document.activeElement !== r) r.value = pos(p) + 1; r.parentElement.hidden = count(p) < 2;
    p.el.classList.toggle("on", V.active === p.n);
  }
  function applyBar() {
    $$("[data-tool]", m.el).forEach(b => b.classList.toggle("on", b.dataset.tool === V.tool));
    $("[data-act=sync]", m.el).classList.toggle("on", V.sync); $("[data-act=sync]", m.el).setAttribute("aria-pressed", V.sync);
    $("[data-act=inv]", m.el).classList.toggle("on", P[V.active].inv);
  }
  async function rerender(p, force) {
    if (!p.info || !p.info.server || !gray(p)) return;
    if (!force && p.base && p.base.c === p.c && p.base.w === p.w && p.base.frame === p.frame) return;
    const seq = ++p.seq, fid = p.f.id;
    try {
      const r = await API.render(fid, { wc: p.c, ww: p.w, frame: p.frame });
      if (seq !== p.seq || fid !== p.f.id) return;
      await new Promise(res => { const t = new Image(); t.onload = res; t.onerror = res; t.src = r.url; });
      if (seq !== p.seq || !p.im) return;
      p.im.src = r.url; if (p.lastUrl && p.lastUrl !== r.url && p.lastUrl.startsWith("blob:")) URL.revokeObjectURL(p.lastUrl); p.lastUrl = r.url;
      p.base = { c: r.wc, w: r.ww, frame: p.frame }; apply(p);
    } catch { /* keep the in-browser approximation */ }
  }
  const settle = (p, ms = 220) => { clearTimeout(p.t); p.t = setTimeout(() => rerender(p), ms); };

  async function load(p, keepWL) {
    const f = files(p)[p.i]; p.f = f;
    const keep = keepWL && p.c != null;
    p.base = null; p.info = null; p.im = null; p.frame = 0;
    p.stage.querySelectorAll("img,.empty").forEach(e => e.remove());
    let src = null;
    if (!f) { p.stage.insertAdjacentHTML("afterbegin", `<div class="empty" style="color:#9fb3b6">${I.scan}<p>No images in this study.</p></div>`); apply(p); return; }
    if (f.kind === "dicom") {
      try {
        const r = await API.render(f.id, { wc: keep ? p.c : null, ww: keep ? p.w : null });
        if (p.f !== f) return;
        p.info = { server: true, rgb: r.rgb, frames: r.frames, modality: r.modality, min: r.min, max: r.max, dc: r.dc, dw: r.dw };
        p.frame = r.frame; src = r.url; p.lastUrl = r.url;
        if (!r.rgb) { p.base = { c: r.wc, w: r.ww, frame: r.frame }; if (!keep) { p.c = r.wc; p.w = r.ww; } }
      } catch { if (f.has_thumbnail) { src = (API.previewUrl || API.thumbUrl)(f.id); p.info = { server: false, rgb: false, frames: 1, min: 0, max: 255 }; } }
    } else { src = API.fileUrl(f.id); p.info = { server: false, rgb: false, frames: 1, min: 0, max: 255 }; }
    if (p.f !== f) return;
    if (p.info && !p.info.server) { p.base = { c: 128, w: 256 }; if (!keep) { p.c = 128; p.w = 256; } }
    if (src) { p.stage.insertAdjacentHTML("afterbegin", `<img alt="${esc(f.filename)}" src="${esc(src)}" draggable="false">`); p.im = $("img", p.stage); }
    else p.stage.insertAdjacentHTML("afterbegin", `<div class="empty" style="color:#9fb3b6">${I.file}<p>No preview for this file.</p></div>`);
    apply(p);
  }

  // ---- linked changes: the pane the person touched leads, the other follows when linked
  function setView(p, z, x, y) { for (const q of [p, ...others(p)]) { q.zoom = z; q.px = x; q.py = y; apply(q); } }
  function setWL(p, c, w, now) {
    p.c = c; p.w = Math.max(1, w); apply(p); now ? rerender(p) : settle(p);
    for (const q of others(p)) {
      if (!gray(q) || q.c == null) continue;
      if (p.info?.modality === "CT" && q.info?.modality === "CT" && p.info.server && q.info.server) { q.c = p.c; q.w = p.w; }
      else { const [dc, dw] = def(p), [qc, qw] = def(q); q.w = Math.max(1, qw * p.w / dw); q.c = qc + (p.c - dc) * qw / dw; }
      apply(q); now ? rerender(q) : settle(q);
    }
  }
  function goTo(p, n) {
    n = Math.max(0, Math.min(count(p) - 1, n));
    if (n === pos(p)) return false;
    if (files(p).length > 1) { p.i = n; load(p, true); } else { p.frame = n; apply(p); settle(p, 120); }
    return true;
  }
  function move(p, n) {
    if (!goTo(p, n)) return;
    for (const q of others(p)) { const sign = q.n > p.n ? 1 : -1, t = Math.max(0, Math.min(1, frac(p) + sign * V.off)); goTo(q, Math.round(t * (count(q) - 1))); }
  }
  const step = (p, d) => move(p, pos(p) + d);
  const link = () => { V.off = frac(P[1]) - frac(P[0]); const a = P[V.active]; setView(a, a.zoom, a.px, a.py); };
  function zoomAt(p, k, cx, cy) {
    const r = p.stage.getBoundingClientRect();
    const x = (cx ?? r.left + r.width / 2) - (r.left + r.width / 2), y = (cy ?? r.top + r.height / 2) - (r.top + r.height / 2);
    const nz = Math.max(.25, Math.min(16, p.zoom * k)), q = nz / p.zoom;
    setView(p, nz, x - (x - p.px) * q, y - (y - p.py) * q);
  }
  function act(a) {
    const p = P[V.active];
    if (a === "sync") { V.sync = !V.sync; if (V.sync) link(); toast(V.sync ? "Linked: both sides move together" : "Unlinked: each side moves on its own"); }
    if (a === "inv") { p.inv = !p.inv; apply(p); for (const q of others(p)) { q.inv = p.inv; apply(q); } }
    if (a === "zin") zoomAt(p, 1.4);
    if (a === "zout") zoomAt(p, 1 / 1.4);
    if (a === "reset") { for (const q of V.sync ? P : [p]) { q.zoom = 1; q.px = q.py = 0; q.inv = false; if (q.info && gray(q)) { const [c, w] = def(q); q.c = c; q.w = Math.max(1, w); rerender(q, true); } apply(q); } }
    if (a === "swap") { const [x, y] = [P[0], P[1]]; const grid = $(".cmpgrid", m.el); grid.insertBefore(y.el, x.el); P.reverse(); P.forEach((q, i) => { q.n = i; q.el.dataset.p = i; }); V.off = -V.off; V.active = V.active ? 0 : 1; P.forEach(apply); }
    if (a === "fs") { (document.fullscreenElement ? document.exitFullscreen() : modalEl.requestFullscreen()).catch(() => toast("Full screen isn't allowed here")); }
    applyBar();
  }
  $$("[data-tool]", m.el).forEach(b => b.onclick = () => { V.tool = b.dataset.tool; applyBar(); });
  $$("[data-act]", m.el).forEach(b => b.onclick = () => act(b.dataset.act));

  for (const p of P) {
    const sel = $("[data-ser]", p.el);
    if (sel) sel.onchange = async () => { p.g = +sel.value; p.i = 0; await load(p, false); if (V.sync) link(); };
    const r = $("[data-slc]", p.el);
    r.oninput = () => { V.active = p.n; move(p, r.value - 1); P.forEach(apply); };
    const pts = new Map(); let drag = null, pinch = null, lastTap = 0;
    p.stage.addEventListener("pointerdown", e => {
      if (V.active !== p.n) { V.active = p.n; P.forEach(apply); applyBar(); }
      p.stage.focus({ preventScroll: true });
      if (!p.im) return;
      p.stage.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: p.zoom, mx: (a[0] + b[0]) / 2, my: (a[1] + b[1]) / 2, px: p.px, py: p.py }; drag = null; return; }
      const now = Date.now();
      if (now - lastTap < 300) { p.zoom > 1.05 ? setView(p, 1, 0, 0) : zoomAt(p, 2.5, e.clientX, e.clientY); lastTap = 0; return; }
      lastTap = now;
      drag = { x: e.clientX, y: e.clientY, px: p.px, py: p.py, c: p.c, w: p.w, tool: e.button === 2 || e.shiftKey ? "wl" : e.button === 1 ? "pan" : V.tool };
    });
    p.stage.addEventListener("pointermove", e => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pinch && pts.size >= 2) { const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); setView(p, Math.max(.25, Math.min(16, pinch.z * d / pinch.d)), pinch.px + (a[0] + b[0]) / 2 - pinch.mx, pinch.py + (a[1] + b[1]) / 2 - pinch.my); return; }
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (drag.tool === "wl" && gray(p) && drag.w != null) { const sens = Math.max(.5, ((p.info.max - p.info.min) || 256) / 700); setWL(p, drag.c - dy * sens, drag.w + dx * sens); }
      else setView(p, p.zoom, drag.px + dx, drag.py + dy);
    });
    const up = e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; if (!pts.size) drag = null; };
    p.stage.addEventListener("pointerup", up); p.stage.addEventListener("pointercancel", up);
    p.stage.addEventListener("contextmenu", e => { if (p.im) e.preventDefault(); });
    p.stage.addEventListener("wheel", e => {
      if (!p.im) return; e.preventDefault(); V.active = p.n;
      if (e.ctrlKey || e.metaKey || count(p) < 2) zoomAt(p, Math.exp(-Math.max(-60, Math.min(60, e.deltaY)) * (e.ctrlKey ? .01 : .004)), e.clientX, e.clientY);
      else { p.acc = (p.acc || 0) + e.deltaY; if (Math.abs(p.acc) >= 40) { step(p, Math.sign(p.acc)); p.acc = 0; } }
    }, { passive: false });
  }
  const key = e => {
    if (!document.body.contains(m.el)) return document.removeEventListener("keydown", key);
    if (e.target.matches("input,select,textarea")) return;
    const k = e.key, p = P[V.active];
    if (k === "ArrowDown") { e.preventDefault(); step(p, 1); }
    else if (k === "ArrowUp") { e.preventDefault(); step(p, -1); }
    else if (k === "ArrowLeft" || k === "ArrowRight") { V.active = k === "ArrowLeft" ? 0 : 1; P.forEach(apply); applyBar(); }
    else if (k === "+" || k === "=") zoomAt(p, 1.25);
    else if (k === "-") zoomAt(p, 1 / 1.25);
    else if (k === "0") act("reset");
    else if (k === "i" || k === "I") act("inv");
    else if (k === "l" || k === "L") act("sync");
    else if (k === "w" || k === "W") { V.tool = "wl"; applyBar(); }
    else if (k === "p" || k === "P") { V.tool = "pan"; applyBar(); }
    else if (k === "f" || k === "F") act("fs");
  };
  document.addEventListener("keydown", key);
  applyBar();
  P[V.active].stage.focus({ preventScroll: true }); // keys drive the images, not the first slider
  await Promise.all(P.map(p => load(p, false)));
  // Start the follower at the same relative position as the slice the person came from.
  if (fromPane >= 0 && count(lead) > 1) goTo(other, Math.round(frac(lead) * (count(other) - 1)));
  V.off = frac(P[1]) - frac(P[0]);
}

// ------------------------------------------------------------------ upload page (mobile-first)
async function viewUploadPage() {
  const list = await API.req("GET", "/api/patients");
  const m = main();
  m.innerHTML = `${mobileHead()}<div class="top"><div><h1>Upload</h1><p class="sub">Files are encrypted on this server as they arrive.</p></div></div>
  ${list.length ? `<div class="card pad stack" style="max-width:720px">
    <label class="f">Patient<select id="pt">${list.map(p => `<option value="${esc(p.id)}">${esc(p.name)}${p.date_of_birth ? " · " + esc(fmtDate(p.date_of_birth)) : ""}</option>`).join("")}</select></label>
    <label class="f">Put files in<select id="st"><option value="">Automatic: group DICOM by study, others under today's date</option></select></label>
    <div class="drop" id="drop">${I.upload}<div><b>Drop files or a CD/zip export</b><div class="small muted">DICOM, JPG/PNG, PDF reports, ZIP</div></div>
      <div class="row" style="justify-content:center"><label tabindex="0" role="button" class="btn primary">Choose files<input type="file" id="fi" multiple hidden></label><label tabindex="0" role="button" class="btn">${I.camera} Take photo<input type="file" id="cam" accept="image/*" capture="environment" hidden></label></div></div>
    <div class="queue" id="queue"></div></div>`
    : `<div class="card empty">${I.users}<h2>Add a patient first</h2><p>Uploads always belong to a patient.</p><button class="btn primary" id="add">${I.plus} Add patient</button></div>`}`;
  if (!list.length) { $("#add").onclick = () => patientForm(); return; }
  const fillStudies = async () => {
    const ss = await API.req("GET", `/api/patients/${$("#pt").value}/studies`);
    $("#st").innerHTML = `<option value="">Automatic: group DICOM by study, others under today's date</option>` + ss.map(s => `<option value="${esc(s.id)}">${esc((s.study_date ? fmtDate(s.study_date) + " · " : "") + s.title)}</option>`).join("");
  };
  $("#pt").onchange = fillStudies; await fillStudies();
  const send = fl => { const sid = $("#st").value; uploadFiles(sid ? `/api/studies/${sid}/files?format=full` : `/api/patients/${$("#pt").value}/import`, fl, fillStudies); };
  wireDrop($("#drop"), send);
  $("#fi").onchange = e => send([...e.target.files]);
  $("#cam").onchange = e => send([...e.target.files]);
}

// ------------------------------------------------------------------ backups
const TT = {
  gdrive: ["Google Drive", I.drive, "Cloud"], onedrive: ["OneDrive", I.cloud, "Cloud"], dropbox: ["Dropbox", I.box, "Cloud"], box: ["Box", I.box, "Cloud"],
  s3: ["S3 / object storage", I.cloud, "Object storage"],
  sftp: ["SFTP / SCP server", I.server, "Network"], smb: ["Network share (SMB)", I.server, "Network"], webdav: ["WebDAV / Nextcloud", I.server, "Network"],
  local: ["USB / local disk", I.server, "Local"],
};
const TT_DESC = {
  gdrive: "Your Google account (OAuth) or a Shared Drive via service account.",
  onedrive: "OneDrive personal or OneDrive for Business / SharePoint, via a Microsoft app registration.",
  dropbox: "Your Dropbox, in an app folder or a folder you choose.",
  box: "Your Box account (OAuth) or a Box Custom App (client credentials).",
  s3: "Amazon S3, Backblaze B2, Wasabi, Cloudflare R2, Synology C2, MinIO or any S3-compatible bucket.",
  sftp: "Any server you can SSH into: NAS, VPS, Synology, TrueNAS. SFTP or SCP.",
  smb: "A Windows or NAS shared folder (Synology, QNAP, TrueNAS, Samba). SMB 2/3 with optional encryption.",
  webdav: "Nextcloud, ownCloud, Synology WebDAV Server, Koofr, pCloud or any WebDAV server.",
  local: "A USB drive, second disk or NFS mount, mapped into the container.",
};
const targetSummary = t => {
  const c = t.config || {};
  return { sftp: () => `${(c.protocol || "sftp").toUpperCase()} ${c.host || ""}`, s3: () => `${c.bucket || ""}${c.prefix ? "/" + c.prefix : ""}`, smb: () => `\\\\${c.host || ""}\\${c.share || ""}`,
    webdav: () => (c.url || "").replace(/^https?:\/\//, "").split("/")[0], local: () => c.path || "/backups", dropbox: () => "/" + (c.folder || "ScanVault"), onedrive: () => "/" + (c.folder || "ScanVault") }[t.type]?.() || "";
};
async function viewBackups() {
  const [tg, runs] = await Promise.all([API.req("GET", "/api/backups/targets"), API.req("GET", "/api/backups/runs")]);
  const m = main();
  const badge = s => s === "ok" ? '<span class="badge ok">OK</span>' : s === "error" ? '<span class="badge err">Failed</span>' : s === "running" ? '<span class="badge warn">Running</span>' : '<span class="badge gray">Not run yet</span>';
  m.innerHTML = `${mobileHead()}<div class="top"><div><h1>Backups</h1><p class="sub">Each backup is one archive, encrypted here with your key before upload. Providers only ever see ciphertext.</p></div>
    <div class="row"><a class="btn" href="${esc(API.backupDownloadUrl())}" id="dlb">${I.dl} Download backup</a><button class="btn primary" id="all" ${tg.length ? "" : "disabled"}>Back up now</button></div></div>
  <div class="targets">${tg.map(t => `<div class="card target">
      <div class="row"><div class="logo">${TT[t.type][1]}</div><div style="flex:1"><h3>${esc(t.name)}</h3><div class="small muted">${esc(TT[t.type][0])}${targetSummary(t) ? " · " + esc(targetSummary(t)) : ""}</div></div>${t.enabled ? badge(t.last_status) : '<span class="badge gray">Paused</span>'}</div>
      <div class="small"><div>Schedule: <b>${t.schedule_hours ? `every ${t.schedule_hours} h` : "manual only"}</b> · keep last <b>${t.keep_last || "all"}</b></div><div class="muted">Last run ${esc(fmtDT(t.last_run))}</div></div>
      ${!t.connected ? `<div class="note">${I.warn}<div>Not connected yet. <button class="btn sm" data-conn="${esc(t.id)}" data-type="${esc(t.type)}">Connect ${esc(TT[t.type][0])} account</button></div></div>` : ""}
      <div class="row"><button class="btn sm" data-run="${esc(t.id)}">Run now</button><button class="btn sm" data-test="${esc(t.id)}">Test</button><button class="btn sm ghost" data-edit="${esc(t.id)}">Edit</button></div></div>`).join("")}
    <button class="addtarget" id="add">${I.plus}<span>Add destination</span><span class="small" style="font-weight:400">Google Drive · OneDrive · Dropbox · Box · S3 · B2 · SFTP · SMB · WebDAV · USB</span></button></div>
  <div class="section-h"><h2>History</h2></div>
  <div class="card scroll">${runs.length ? `<table class="stk"><thead><tr><th>When</th><th>Destination</th><th>Status</th><th>Details</th><th style="text-align:right">Size</th></tr></thead><tbody>
    ${runs.map(r => `<tr><td class="nowrap">${esc(fmtDT(r.started_at))}</td><td>${esc(r.target_name)}</td><td>${badge(r.status)}</td><td class="small">${esc(r.message || r.filename || "")}</td><td class="num">${r.size ? fmtBytes(r.size) : ""}</td></tr>`).join("")}</tbody></table>`
    : `<p class="pad muted">No backups have run yet.</p>`}</div>
  <div class="section-h"><h2>Restore</h2></div>
  <div class="card pad stack"><p class="small">Replace this vault's contents with a backup made with the same key. Logins, settings, your current encryption key and backup destinations are kept. To restore onto a new server, use "Restore from a backup" during setup with your recovery key file.</p>
    <div class="row"><label tabindex="0" role="button" class="btn">${I.restore} Restore from file…<input type="file" id="rf" accept=".svb" hidden></label>${tg.length ? `<button class="btn" id="rr">Restore from a destination…</button>` : ""}</div></div>`;
  $("#add").onclick = () => chooseTargetType();
  $("#all").onclick = async e => { e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span> Backing up…'; try { const r = await API.req("POST", "/api/backups/run"); toast(r.results.every(x => x.status === "ok") ? "Backup complete" : "Some backups failed"); } catch (er) { handleErr(er); } viewBackups(); };
  $$("[data-run]", m).forEach(b => b.onclick = async () => { b.disabled = true; b.innerHTML = '<span class="spin"></span>'; try { const r = await API.req("POST", `/api/backups/targets/${b.dataset.run}/run`); toast(r.results[0].status === "ok" ? "Backup uploaded" : "Backup failed: " + r.results[0].message); } catch (er) { handleErr(er); } viewBackups(); });
  $$("[data-test]", m).forEach(b => b.onclick = async () => { b.disabled = true; try { const r = await API.req("POST", `/api/backups/targets/${b.dataset.test}/test`); toast(r.message); } catch (er) { toast(er.message); } b.disabled = false; });
  $$("[data-edit]", m).forEach(b => b.onclick = () => targetForm(tg.find(t => t.id === b.dataset.edit).type, tg.find(t => t.id === b.dataset.edit)));
  $$("[data-conn]", m).forEach(b => b.onclick = () => API.oauthStart(b.dataset.type, b.dataset.conn));
  $("#rf").onchange = e => restoreDialog({ file: e.target.files[0] });
  const rr = $("#rr"); if (rr) rr.onclick = () => restoreDialog({ targets: tg });
}

function chooseTargetType() {
  const groups = [...new Set(Object.values(TT).map(v => v[2]))];
  const m = modal("Add backup destination", groups.map(g => `<h3 class="small muted" style="margin:14px 0 8px">${g}</h3><div class="choice">${Object.entries(TT).filter(([, v]) => v[2] === g).map(([k, [l, ic]]) => `<button data-t="${k}">${ic}<b>${l}</b><span>${TT_DESC[k]}</span></button>`).join("")}</div>`).join(""));
  $$("[data-t]", m.el).forEach(b => b.onclick = () => { m.close(); targetForm(b.dataset.t); });
}

async function targetForm(type, t) {
  const c = t?.config || {}, sec = t?.secrets_set || {};
  const ph = k => sec[k] ? 'placeholder="•••••• saved (leave blank to keep)"' : "";
  let redirect = "";
  if (["gdrive", "box", "dropbox", "onedrive"].includes(type)) { try { redirect = (await API.req("GET", `/api/oauth/${type}/redirect-uri`)).redirect_uri; } catch { redirect = location.origin + `/api/oauth/${type}/callback`; } }
  const common = `<div class="grid3"><label class="f">Name<input name="name" value="${esc(t?.name || TT[type][0])}"></label>
    <label class="f">Every (hours) <span class="hint">0 = manual</span><input name="schedule_hours" type="number" min="0" value="${t ? t.schedule_hours : 24}"></label>
    <label class="f">Keep last<input name="keep_last" type="number" min="0" value="${t ? t.keep_last : 14}"></label></div>
    <label class="check"><input type="checkbox" name="enabled" ${!t || t.enabled ? "checked" : ""}> Enabled</label>`;
  let fields = "";
  if (type === "gdrive") fields = `<div class="seg" id="am"><button type="button" data-a="oauth" class="${c.auth_mode !== "service_account" ? "on" : ""}">Google account</button><button type="button" data-a="service_account" class="${c.auth_mode === "service_account" ? "on" : ""}">Service account</button></div>
    <div data-mode="oauth" class="form"><div class="note info">${I.info}<div>In Google Cloud Console create an OAuth client (type "Web application"), enable the Drive API, and add this redirect URI: <code>${esc(redirect)}</code>. ScanVault only asks for access to files it creates.</div></div>
      <div class="grid2"><label class="f">Client ID<input name="client_id" value="${esc(c.client_id)}"></label><label class="f">Client secret<input name="client_secret" type="password" ${ph("client_secret")}></label></div>
      <label class="f">Refresh token <span class="hint">Optional: paste one instead of clicking Connect</span><input name="refresh_token" type="password" ${ph("refresh_token")}></label></div>
    <div data-mode="service_account" class="form" hidden><label class="f">Service account JSON key <span class="hint">Share a Shared Drive folder with the service account's email</span><textarea name="service_account_json" ${ph("service_account_json")}></textarea></label></div>
    <label class="f">Folder ID <span class="hint">From the folder's URL. Blank = My Drive root</span><input name="folder_id" value="${esc(c.folder_id)}"></label>`;
  if (type === "box") fields = `<div class="seg" id="am"><button type="button" data-a="oauth" class="${c.auth_mode !== "ccg" ? "on" : ""}">Box account</button><button type="button" data-a="ccg" class="${c.auth_mode === "ccg" ? "on" : ""}">Custom App (CCG)</button></div>
    <div class="grid2"><label class="f">Client ID<input name="client_id" value="${esc(c.client_id)}"></label><label class="f">Client secret<input name="client_secret" type="password" ${ph("client_secret")}></label></div>
    <div data-mode="oauth" class="form"><div class="note info">${I.info}<div>In the Box Developer Console create a Custom App with "User Authentication (OAuth 2.0)" and add the redirect URI <code>${esc(redirect)}</code>.</div></div></div>
    <div data-mode="ccg" class="form" hidden><div class="grid2"><label class="f">Subject type<select name="subject_type"><option value="user" ${c.subject_type !== "enterprise" ? "selected" : ""}>user</option><option value="enterprise" ${c.subject_type === "enterprise" ? "selected" : ""}>enterprise</option></select></label><label class="f">User or enterprise ID<input name="subject_id" value="${esc(c.subject_id)}"></label></div></div>
    <label class="f">Folder ID <span class="hint">From the folder's URL. Blank = All Files root</span><input name="folder_id" value="${esc(c.folder_id)}"></label>`;
  if (type === "sftp") fields = `<div class="seg" id="pr"><button type="button" data-p="sftp" class="${c.protocol !== "scp" ? "on" : ""}">SFTP</button><button type="button" data-p="scp" class="${c.protocol === "scp" ? "on" : ""}">SCP</button></div>
    <div class="grid3"><label class="f" style="grid-column:span 2">Host<input name="host" value="${esc(c.host)}" placeholder="nas.local" autocapitalize="none"></label><label class="f">Port<input name="port" type="number" value="${esc(c.port || 22)}"></label></div>
    <div class="grid2"><label class="f">Username<input name="username" value="${esc(c.username)}" autocapitalize="none"></label><label class="f">Password<input name="password" type="password" ${ph("password")}></label></div>
    <label class="f">Private key <span class="hint">OpenSSH or PEM. Use instead of, or with, a password.</span><textarea name="private_key" class="mono" ${ph("private_key")}></textarea></label>
    <div class="grid2"><label class="f">Key passphrase<input name="key_passphrase" type="password" ${ph("key_passphrase")}></label><label class="f">Remote folder<input name="remote_dir" value="${esc(c.remote_dir || "scanvault-backups")}"></label></div>
    <label class="f">Pinned host key <span class="hint">Filled in automatically on the first successful test; a different key later blocks the connection.</span><input name="host_key" class="mono" value="${esc(c.host_key)}"></label>`;
  if (type === "dropbox") fields = `<div class="note info">${I.info}<div>At dropbox.com/developers create an app (Scoped access, "App folder" is enough) with the <code>files.content.write</code>, <code>files.content.read</code> and <code>files.metadata.read</code> permissions, and add the redirect URI <code>${esc(redirect)}</code>.</div></div>
    <div class="grid2"><label class="f">App key<input name="app_key" value="${esc(c.app_key)}"></label><label class="f">App secret<input name="app_secret" type="password" ${ph("app_secret")}></label></div>
    <label class="f">Folder <span class="hint">Inside the app folder or your Dropbox</span><input name="folder" value="${esc(c.folder || "ScanVault")}"></label>`;
  if (type === "onedrive") fields = `<div class="note info">${I.info}<div>In the Microsoft Entra admin center register an app (accounts: personal and work, or your org only), add a Web redirect URI <code>${esc(redirect)}</code>, create a client secret, and grant the delegated <code>Files.ReadWrite.AppFolder</code> and <code>offline_access</code> permissions. Backups go to the app's own folder (Apps/&lt;app name&gt;); ScanVault can't see the rest of your OneDrive.</div></div>
    <div class="grid2"><label class="f">Application (client) ID<input name="client_id" value="${esc(c.client_id)}"></label><label class="f">Client secret<input name="client_secret" type="password" ${ph("client_secret")}></label></div>
    <div class="grid2"><label class="f">Account type<select name="tenant">${[["common", "Personal or work/school"], ["consumers", "Personal only"], ["organizations", "Work/school only"]].map(([v, l]) => `<option value="${v}" ${(c.tenant || "common") === v ? "selected" : ""}>${l}</option>`).join("")}${c.tenant && !["common", "consumers", "organizations"].includes(c.tenant) ? `<option selected value="${esc(c.tenant)}">${esc(c.tenant)}</option>` : ""}</select></label>
    <label class="f">Folder<input name="folder" value="${esc(c.folder || "ScanVault")}"></label></div>`;
  if (type === "s3") fields = `<label class="f">Provider<select name="provider" id="s3p">${[["aws", "Amazon S3"], ["b2", "Backblaze B2"], ["wasabi", "Wasabi"], ["r2", "Cloudflare R2"], ["c2", "Synology C2 Object Storage"], ["minio", "MinIO / self-hosted"], ["other", "Other S3-compatible"]].map(([v, l]) => `<option value="${v}" ${(c.provider || "aws") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <div class="grid2"><label class="f">Bucket<input name="bucket" value="${esc(c.bucket)}" autocapitalize="none"></label><label class="f">Folder prefix <span class="hint">Optional</span><input name="prefix" value="${esc(c.prefix || "scanvault")}" autocapitalize="none"></label></div>
    <div class="grid2"><label class="f">Region <span class="hint" id="s3rh"></span><input name="region" value="${esc(c.region)}" autocapitalize="none"></label>
      <label class="f" data-s3="r2">Cloudflare account ID<input name="account_id" value="${esc(c.account_id)}"></label>
      <label class="f" data-s3="minio other">Endpoint URL<input name="endpoint_url" value="${esc(c.endpoint_url)}" placeholder="https://s3.example.com" autocapitalize="none"></label></div>
    <div class="grid2"><label class="f">Access key ID<input name="access_key_id" value="${esc(c.access_key_id)}" autocapitalize="none"></label><label class="f">Secret access key<input name="secret_access_key" type="password" ${ph("secret_access_key")}></label></div>
    <div class="grid2"><label class="f" data-s3="aws">Storage class<select name="storage_class">${["", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR"].map(v => `<option value="${v}" ${(c.storage_class || "") === v ? "selected" : ""}>${v || "Standard"}</option>`).join("")}</select></label>
      <label class="check" data-s3="minio other"><input type="checkbox" name="path_style" ${c.path_style ? "checked" : ""}> Path-style URLs</label></div>
    <div class="note info">${I.info}<div>Use a key that can only reach this bucket. Turning on bucket versioning or object lock protects backups from ransomware. With a write-only key, set Keep last to 0 and expire old backups with a lifecycle rule.</div></div>`;
  if (type === "webdav") fields = `<label class="f">WebDAV URL <span class="hint">Nextcloud: https://cloud.example.com/remote.php/dav/files/USERNAME</span><input name="url" value="${esc(c.url)}" placeholder="https://" autocapitalize="none"></label>
    <div class="grid2"><label class="f">Username<input name="username" value="${esc(c.username)}" autocapitalize="none"></label><label class="f">Password or app password<input name="password" type="password" ${ph("password")}></label></div>
    <label class="f">Folder<input name="folder" value="${esc(c.folder || "ScanVault")}"></label>
    <label class="check"><input type="checkbox" name="self_signed" ${c.self_signed || c.insecure_tls ? "checked" : ""}> The server uses a self-signed certificate <span class="hint">Its fingerprint is pinned on the first Test, like an SSH host key</span></label>
    ${c.tls_fingerprint ? `<label class="f">Pinned certificate <span class="hint">Clear it only if you replaced the server's certificate on purpose</span><input name="tls_fingerprint" class="mono" value="${esc(c.tls_fingerprint)}" autocapitalize="none" spellcheck="false"></label>` : ""}`;
  if (type === "smb") fields = `<div class="grid3"><label class="f" style="grid-column:span 2">Server<input name="host" value="${esc(c.host)}" placeholder="nas.local or 192.168.1.20" autocapitalize="none"></label><label class="f">Port<input name="port" type="number" value="${esc(c.port || 445)}"></label></div>
    <div class="grid2"><label class="f">Share<input name="share" value="${esc(c.share)}" placeholder="backup"></label><label class="f">Folder in share<input name="folder" value="${esc(c.folder || "scanvault")}"></label></div>
    <div class="grid2"><label class="f">Username <span class="hint">DOMAIN\\user if needed</span><input name="username" value="${esc(c.username)}" autocapitalize="none"></label><label class="f">Password<input name="password" type="password" ${ph("password")}></label></div>
    <label class="check"><input type="checkbox" name="require_encryption" ${c.require_encryption !== false ? "checked" : ""}> Require SMB3 encryption in transit</label>`;
  if (type === "local") fields = `<div class="note info">${I.info}<div>Map a USB drive, second disk or NFS mount into the container under <code>/backups</code> (see the docker-compose file), then point this at it.</div></div>
    <label class="f">Folder <span class="hint">Must be under /backups</span><input name="path" value="${esc(c.path || "/backups/usb1/scanvault")}" autocapitalize="none"></label>
    <label class="check"><input type="checkbox" name="require_mount" ${c.require_mount !== false ? "checked" : ""}> Fail if the disk isn't mounted (so backups never fill the NAS's own drive)</label>`;
  const m = modal((t ? "Edit " : "Add ") + TT[type][0], `<form class="form" id="tf">${common}${fields}<p class="err" id="err"></p></form>`,
    { footer: `${t ? '<button class="btn danger" id="del">Remove</button><span class="spacer"></span>' : ""}<button class="btn" data-close>Cancel</button><button class="btn primary" form="tf" id="save">Save</button>` });
  let auth = c.auth_mode || "oauth", proto = c.protocol || "sftp";
  const showMode = () => $$("[data-mode]", m.el).forEach(d => d.hidden = d.dataset.mode !== auth);
  $$("#am button", m.el).forEach(b => b.onclick = () => { auth = b.dataset.a; $$("#am button", m.el).forEach(x => x.classList.toggle("on", x === b)); showMode(); });
  $$("#pr button", m.el).forEach(b => b.onclick = () => { proto = b.dataset.p; $$("#pr button", m.el).forEach(x => x.classList.toggle("on", x === b)); });
  showMode();
  const s3p = $("#s3p", m.el);
  if (s3p) {
    const hints = { aws: "e.g. us-east-1", b2: "e.g. us-west-004", wasabi: "e.g. us-east-1", r2: "auto", c2: "e.g. us-001 or eu-002", minio: "usually us-east-1", other: "" };
    const sync = () => { $$("[data-s3]", m.el).forEach(el => el.hidden = !el.dataset.s3.split(" ").includes(s3p.value)); $("#s3rh", m.el).textContent = hints[s3p.value] || ""; };
    s3p.onchange = sync; sync();
  }
  $("#tf", m.el).onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const top = { name: fd.name, schedule_hours: +fd.schedule_hours || 0, keep_last: +fd.keep_last || 0, enabled: !!e.target.enabled.checked };
    ["name", "schedule_hours", "keep_last", "enabled"].forEach(k => delete fd[k]);
    if (type === "sftp") fd.protocol = proto; else if (type === "gdrive" || type === "box") fd.auth_mode = auth;
    $$("#tf input[type=checkbox]", m.el).forEach(cb => { if (cb.name && cb.name !== "enabled") fd[cb.name] = cb.checked; });
    if (fd.port) fd.port = +fd.port;
    try {
      const r = t ? await API.req("PUT", `/api/backups/targets/${t.id}`, { ...top, config: fd }) : await API.req("POST", "/api/backups/targets", { type, ...top, config: fd });
      m.close(); toast("Saved");
      if (!r.connected) { if (await confirmBox(`Connect ${TT[type][0]}?`, `You'll sign in to ${TT[type][0]} and allow ScanVault to store backups there.`, "Connect", false)) return API.oauthStart(type, r.id); }
      viewBackups();
    } catch (er) { $("#err", m.el).textContent = er.message; }
  };
  const d = $("#del", m.el);
  if (d) d.onclick = async () => { m.close(); if (await confirmBox("Remove destination?", "Stops backing up here. Backups already uploaded stay where they are.", "Remove")) { await API.req("DELETE", `/api/backups/targets/${t.id}`); viewBackups(); } };
}

async function restoreDialog({ file, targets }) {
  let remote = [];
  const m = modal("Restore backup", `<form class="form" id="rf">
    ${file ? `<p>Restore from <b>${esc(file.name)}</b> (${fmtBytes(file.size)}).</p>` : `<label class="f">Destination<select name="t">${targets.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</select></label><label class="f">Backup<select name="h"><option>Loading…</option></select></label>`}
    <div class="note">${I.warn}<div>This replaces every patient, study and file on this server with the backup's contents. Anything added since that backup is lost unless it's in another backup.</div></div>
    <label class="f">Type REPLACE to confirm<input name="c" autocomplete="off" autocapitalize="characters"></label><p class="err" id="err"></p></form>`,
    { footer: `<button class="btn" data-close>Cancel</button><button class="btn danger" form="rf" id="go">Restore</button>` });
  const loadRemote = async () => { try { remote = await API.req("GET", `/api/backups/targets/${$("[name=t]", m.el).value}/remote`); $("[name=h]", m.el).innerHTML = remote.length ? remote.map(r => `<option value="${esc(r.handle)}">${esc(r.name)}</option>`).join("") : "<option value=''>No backups found</option>"; } catch (er) { $("#err", m.el).textContent = er.message; } };
  if (!file) { $("[name=t]", m.el).onchange = loadRemote; loadRemote(); }
  $("#rf", m.el).onsubmit = async e => {
    e.preventDefault(); const f = e.target; const fd = new FormData(); fd.append("confirm", f.c.value);
    if (file) fd.append("backup_file", file); else { fd.append("target_id", f.t.value); fd.append("handle", f.h.value); }
    $("#go", m.el).disabled = true; $("#go", m.el).innerHTML = '<span class="spin"></span> Restoring…';
    try { const r = await API.upload("/api/backups/restore", fd); m.close(); toast(`Restored: ${r.patients} patients, ${r.files} files`); go("patients"); }
    catch (er) { $("#err", m.el).textContent = er.message; $("#go", m.el).disabled = false; $("#go", m.el).textContent = "Restore"; }
  };
}

// ------------------------------------------------------------------ settings
async function viewSettings() {
  const st = S.status = await API.req("GET", "/api/status");
  const m = main();
  m.innerHTML = `${mobileHead()}<div class="top"><div><h1>Settings</h1></div></div>
  <div class="settings">
    ${st.recovery_key_saved === false ? `<div class="note">${I.warn}<div>You haven't confirmed that you saved your recovery key file. Without it, a forgotten encryption key means your backups can't be opened.</div></div>` : ""}
    <div class="card pad stack"><h2>Appearance</h2>
      <div class="seg" id="thm" role="radiogroup" aria-label="Theme">${[["system", "Match device"], ["light", "Light"], ["dark", "Dark"]].map(([k, l]) => `<button type="button" role="radio" data-t="${k}" class="${THEME.theme === k ? "on" : ""}" aria-checked="${THEME.theme === k}">${l}</button>`).join("")}</div>
      <div><p class="small" style="font-weight:600;margin-bottom:8px">Dark theme color</p>
        <div class="swatches" role="radiogroup" aria-label="Dark theme color">${DARK_SWATCHES.map(([n, c]) => `<button type="button" class="sw ${THEME.dark_color === c ? "on" : ""}" data-c="${c}" role="radio" aria-checked="${THEME.dark_color === c}" title="${n}"><i style="background:${c}"></i><span>${n}</span></button>`).join("")}
          <label class="sw ${DARK_SWATCHES.some(([, c]) => c === THEME.dark_color) ? "" : "on"}" title="Custom"><input type="color" id="cc" value="${esc(THEME.dark_color)}"><span>Custom</span></label></div>
        <p class="small muted" style="margin-top:8px">Very bright picks are darkened so text stays readable. Saved to your account, so it follows you to other devices.</p></div></div>
    <div class="card pad stack"><h2>Encryption</h2>
      <div class="facts"><span>Vault <b class="mono">${esc(st.vault_id)}</b></span><span>Key fingerprint <b class="mono">${esc(st.fingerprint)}</b></span></div>
      <p class="small muted">AES-256-GCM for every file, preview and patient record. Your encryption key is stretched with scrypt and only unlocks the master key, which lives in memory while the vault is unlocked.</p>
      <div class="row"><button class="btn" id="rk">${I.key} Download recovery key file</button><button class="btn" id="cpw">Change encryption key</button><button class="btn" id="lock">${I.lock} Lock now</button></div>
      <label class="check"><input type="checkbox" id="pr" ${st.passphrase_restore ? "checked" : ""}> <span>Allow restoring backups with the encryption key alone <span class="hint">Off: a backup file can only be opened with the recovery key file, so someone who steals a backup can't guess your passphrase offline. On: new backups carry a passphrase-protected copy of the key.</span></span></label>
      <div class="row"><button class="btn ghost" id="rekey">Replace the master key…</button></div></div>
    <div class="card pad stack"><h2>Account</h2><p class="small">Signed in as <b>${esc(st.username)}</b>.</p>
      <div class="row"><button class="btn" id="pw">Change password</button><button class="btn" id="others">Sign out other devices</button><button class="btn ghost" id="out">Sign out</button></div></div>
    <div class="card pad stack" id="ssocard"><h2>Single sign-on</h2><div class="empty" style="padding:10px"><span class="spin"></span></div></div>
    <div class="card pad stack"><h2>Install on your phone</h2>
      <p class="small">Open this address in Safari (iPhone) or Chrome (Android) and choose <b>Add to Home Screen</b>. ScanVault opens full screen and can take photos of films and paper reports. Use HTTPS when you reach it from outside your home network.</p></div>
    <div class="card pad stack"><h2>Privacy and HIPAA</h2>
      <ul class="small" style="margin:0;padding-left:18px;display:grid;gap:6px">
        <li>For personal and family records, HIPAA generally doesn't apply to you. If a practice uses ScanVault for patients, it is responsible for HIPAA safeguards, including business associate agreements with Google or Box.</li>
        <li>Backups are encrypted before upload, so cloud providers hold only ciphertext, but file sizes and backup times are visible to them.</li>
        <li>Reach ScanVault only over HTTPS (your NAS's reverse proxy, Caddy or Traefik). Over plain http:// your password and keys cross the network in clear.</li>
        <li>Losing both the encryption key and the recovery key file makes the data unrecoverable. That is by design.</li></ul></div>
    <p class="small muted">ScanVault ${esc(st.version)}</p>
  </div>`;
  $$("#thm button").forEach(b => b.onclick = () => { saveTheme({ theme: b.dataset.t }); viewSettings(); });
  const pickDark = c => { saveTheme({ dark_color: c, theme: THEME.theme === "light" ? "dark" : THEME.theme }); };
  $$(".sw[data-c]").forEach(b => b.onclick = () => { pickDark(b.dataset.c); viewSettings(); });
  $("#cc").oninput = e => pickDark(e.target.value);
  $("#cc").onchange = () => viewSettings();
  $("#rk").onclick = () => {
    const mm = modal("Download recovery key", `<form class="form" id="f"><p class="small">Enter your encryption key to download the recovery key file again.</p><label class="f">Encryption key<input name="k" type="password" autocomplete="off" required></label><p class="err" id="err"></p></form>`, { footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" form="f">Download</button>` });
    $("#f", mm.el).onsubmit = async e => { e.preventDefault(); try { const t = await API.req("POST", "/api/vault/recovery-key", { passphrase: e.target.k.value }); saveText("scanvault-recovery-key.txt", t); mm.close(); toast("Recovery key downloaded"); if (st.recovery_key_saved === false) { sessionStorage.removeItem("sv-rk-later"); S.status.recovery_key_saved = false; render(); } } catch (er) { $("#err", mm.el).textContent = er.message; } };
  };
  $("#cpw").onclick = () => {
    const mm = modal("Change encryption key", `<form class="form" id="f"><p class="small">Your files and existing backups stay valid and your recovery key file keeps working. Only the passphrase you type changes.</p>
      <label class="f">Current encryption key<input name="a" type="password" required autocomplete="off"></label><label class="f">New encryption key<input name="b" type="password" required minlength="12" autocomplete="new-password"></label><label class="f">Repeat new key<input name="c" type="password" required autocomplete="new-password"></label><p class="err" id="err"></p></form>`, { footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" form="f">Change</button>` });
    $("#f", mm.el).onsubmit = async e => { e.preventDefault(); const f = e.target; if (f.b.value !== f.c.value) return $("#err", mm.el).textContent = "New keys don't match."; try { await API.req("POST", "/api/vault/passphrase", { current: f.a.value, new: f.b.value }); mm.close(); toast("Encryption key changed"); } catch (er) { $("#err", mm.el).textContent = er.message; } };
  };
  $("#pw").onclick = () => {
    const mm = modal("Change password", `<form class="form" id="f"><label class="f">Current password<input name="a" type="password" required autocomplete="current-password"></label><label class="f">New password<input name="b" type="password" required minlength="10" autocomplete="new-password"></label><p class="err" id="err"></p></form>`, { footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" form="f">Change</button>` });
    $("#f", mm.el).onsubmit = async e => { e.preventDefault(); try { const r = await API.req("POST", "/api/auth/password", { current: e.target.a.value, new: e.target.b.value }); mm.close(); toast("Password changed" + (r.signed_out ? `. Signed out ${r.signed_out} other session(s)` : "")); } catch (er) { $("#err", mm.el).textContent = er.message; } };
  };
  $("#others").onclick = async () => { try { const r = await API.req("POST", "/api/auth/logout-others"); toast(`Signed out ${r.signed_out} other session(s)`); } catch (er) { handleErr(er); } };
  $("#pr").onchange = async e => { try { await API.req("PUT", "/api/vault/settings", { passphrase_restore: e.target.checked }); toast(e.target.checked ? "New backups can be restored with the encryption key" : "New backups need the recovery key file to restore"); } catch (er) { e.target.checked = !e.target.checked; handleErr(er); } };
  $("#rekey").onclick = () => {
    const mm = modal("Replace the master key", `<form class="form" id="f"><p class="small">Every file, preview and record is re-encrypted with a brand-new master key, and you get a new recovery key file. Use this if a recovery key file or an old encryption key may have leaked.</p>
      <div class="note">${I.warn}<div>Your current recovery key file stops working for this vault and for new backups. Backups made before now can still be opened with the old recovery key file, so delete old backups you no longer trust. Large vaults take a while; the vault is unavailable meanwhile.</div></div>
      <label class="f">Current encryption key<input name="a" type="password" required autocomplete="off"></label>
      <label class="f">New encryption key <span class="hint">Optional. Leave empty to keep using the current one.</span><input name="b" type="password" minlength="12" autocomplete="new-password"></label>
      <p class="err" id="err"></p></form>`, { footer: `<button class="btn" data-close>Cancel</button><button class="btn danger" form="f" id="rgo">Replace key</button>` });
    $("#f", mm.el).onsubmit = async e => {
      e.preventDefault(); const f = e.target; $("#rgo", mm.el).disabled = true; $("#rgo", mm.el).innerHTML = '<span class="spin"></span> Re-encrypting…';
      try {
        const r = await API.req("POST", "/api/vault/rekey", { passphrase: f.a.value, ...(f.b.value ? { new_passphrase: f.b.value } : {}) });
        mm.close(); saveText("scanvault-recovery-key.txt", r.recovery_key);
        toast("Master key replaced. Save the new recovery key file."); sessionStorage.removeItem("sv-rk-later"); S.status = await API.req("GET", "/api/status"); render();
      } catch (er) { $("#err", mm.el).textContent = er.message; $("#rgo", mm.el).disabled = false; $("#rgo", mm.el).textContent = "Replace key"; }
    };
  };
  $("#lock").onclick = async () => { await API.req("POST", "/api/vault/lock"); S.status.unlocked = false; render(); };
  $("#out").onclick = async () => { await API.req("POST", "/api/auth/logout"); S.status.authenticated = false; render(); };
  ssoCard().catch(handleErr);
}

// ------------------------------------------------------------------ single sign-on settings
async function ssoCard() {
  const el = $("#ssocard"); if (!el) return;
  if (API.demo) { el.innerHTML = `<h2>Single sign-on</h2><p class="small muted">On your server you can sign in with Google, Microsoft, Authentik, Authelia, Keycloak, Synology SSO Server or any OpenID Connect provider. It isn't available in this preview.</p>`; return; }
  const c = await API.req("GET", "/api/sso/config");
  const linkedTo = new Set(c.identities.map(i => i.provider_id));
  el.innerHTML = `<h2>Single sign-on</h2>
    <p class="small muted">Sign in with Google, Microsoft, Authentik, Authelia, Keycloak, Synology SSO Server or any OpenID Connect provider. Single sign-on replaces your password only: after a restart you still unlock the vault with your encryption key, which never leaves ScanVault.</p>
    ${c.providers.length ? `<div class="ssolist">${c.providers.map(p => `<div class="ssorow"><div><b>${esc(p.name)}</b>${p.enabled ? "" : ' <span class="small muted">(off)</span>'}<div class="small muted mono">${esc(p.resolved_issuer)}</div>
        ${c.identities.filter(i => i.provider_id === p.id).map(i => `<div class="small">Linked: <b>${esc(i.email || "account")}</b>${i.last_login ? ` · last used ${esc(ago(i.last_login))}` : ""} <button class="btn ghost sm" data-unlink="${esc(i.id)}">Unlink</button></div>`).join("")}</div>
        <div class="row">${p.enabled && !linkedTo.has(p.id) ? `<button class="btn sm primary" data-link="${esc(p.id)}">Link my account</button>` : ""}${p.managed ? '<span class="small muted">Set by environment</span>' : `<button class="btn sm" data-edit="${esc(p.id)}">Edit</button>`}</div></div>`).join("")}</div>` : ""}
    <div class="row"><button class="btn" id="ssoadd">${I.plus} Add provider</button></div>
    ${c.providers.length ? `<label class="row small" style="gap:8px"><input type="checkbox" id="ssolocal" ${c.local_login ? "checked" : ""} ${c.local_login_forced ? "disabled" : ""}> Allow password sign-in${c.local_login_forced ? " (forced on by SCANVAULT_FORCE_LOCAL_LOGIN)" : ""}</label>
      <p class="small muted">Turn this off only after linking an account above. If your identity provider is ever unreachable, set <span class="mono">SCANVAULT_FORCE_LOCAL_LOGIN=1</span> and restart to get password sign-in back.</p>` : ""}`;
  $("#ssoadd", el).onclick = () => ssoProviderForm(c);
  $$("[data-edit]", el).forEach(b => b.onclick = () => ssoProviderForm(c, c.providers.find(p => p.id === b.dataset.edit)));
  $$("[data-link]", el).forEach(b => b.onclick = async () => {
    if (API.demo) return toast("Single sign-on isn't available in this preview.");
    const password = c.reauth_fresh ? "" : await askPassword("Link account", "Linking adds a new way into your vault, so confirm it's you.");
    if (password === null) return;
    try {
      const r = await API.req("POST", `/api/auth/sso/${encodeURIComponent(b.dataset.link)}/link`, { password });
      if (!/^https?:\/\//i.test(r.url)) throw new Error("The provider returned an unsafe sign-in address");
      location.href = r.url;
    } catch (e) { handleErr(e); }
  });
  $$("[data-unlink]", el).forEach(b => b.onclick = async () => {
    if (!await confirmBox("Unlink account", "You won't be able to sign in with this account until you link it again.", "Unlink")) return;
    try { const r = await API.req("DELETE", `/api/sso/identities/${encodeURIComponent(b.dataset.unlink)}`); if (r.local_login && !c.local_login) toast("Password sign-in was turned back on"); ssoCard(); } catch (e) { handleErr(e); }
  });
  const cb = $("#ssolocal", el);
  if (cb) cb.onchange = async () => {
    const password = cb.checked || c.reauth_fresh ? "" : await askPassword("Turn off password sign-in", "After this, only linked single sign-on accounts can sign in.");
    if (password === null) { cb.checked = !cb.checked; return; }
    try { await API.req("PUT", "/api/sso/settings", { local_login: cb.checked, password }); toast(cb.checked ? "Password sign-in on" : "Password sign-in off"); c.local_login = cb.checked; } catch (e) { cb.checked = !cb.checked; handleErr(e); }
  };
}

function askPassword(title, text) {
  return new Promise(res => {
    let done = false;
    const mm = modal(title, `<form class="form" id="rf"><p class="small">${esc(text)}</p><label class="f">Your ScanVault password<input name="pw" type="password" required autocomplete="current-password"></label></form>`,
      { footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" form="rf">Continue</button>` });
    $("#rf", mm.el).onsubmit = e => { e.preventDefault(); done = true; const v = e.target.pw.value; mm.close(); res(v); };
    mm.el.addEventListener("click", e => { if (!done && (e.target === mm.el || e.target.closest("[data-close]"))) res(null); });
  });
}

function ssoProviderForm(c, p) {
  const presets = c.presets;
  let preset = p ? p.preset : "google";
  const mm = modal(p ? "Edit sign-in provider" : "Add sign-in provider", `<form class="form" id="sf"></form>`, { footer: `${p ? '<button class="btn danger" id="sdel" type="button">Remove</button><span class="spacer"></span>' : ""}<button class="btn" data-close>Cancel</button><button class="btn primary" form="sf" id="ssave">Save</button>`, wide: true });
  const f = $("#sf", mm.el), E = n => f.elements.namedItem(n);
  const draw = () => {
    const pr = presets[preset], vals = p && p.preset === preset ? p.fields : {};
    f.innerHTML = `<label class="f">Provider<select name="preset" ${p ? "disabled" : ""}>${Object.entries(presets).map(([k, v]) => `<option value="${k}" ${k === preset ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
      <div class="note info">${I.info}<div class="small">${esc(pr.help)}</div></div>
      <label class="f">Redirect URI <span class="hint">register this with the provider</span><div class="row" style="flex-wrap:nowrap"><input readonly value="${esc(c.redirect_uri)}" class="mono" style="flex:1"><button type="button" class="btn sm" id="cpy">Copy</button></div></label>
      <label class="f">Button label<input name="name" value="${esc(p ? p.name : pr.name)}" maxlength="60"></label>
      ${preset === "generic" ? `<label class="f">Issuer URL<input name="issuer" required placeholder="https://auth.example.com" value="${esc(p ? p.issuer : "")}"></label>` : ""}
      ${pr.fields.map(x => `<label class="f">${esc(x.label)}<input name="fld_${x.key}" required placeholder="${esc(x.placeholder || "")}" value="${esc(vals[x.key] ?? x.default ?? "")}"></label>`).join("")}
      <label class="f">Client ID<input name="client_id" required autocomplete="off" value="${esc(p ? p.client_id : "")}"></label>
      <label class="f">Client secret ${p && p.has_secret ? '<span class="hint">saved; leave blank to keep</span>' : '<span class="hint">leave blank for a public client</span>'}<input name="client_secret" type="password" autocomplete="off"></label>
      <label class="f">Scopes<input name="scopes" value="${esc(p ? p.scopes : "openid email profile")}"></label>
      <label class="row small" style="gap:8px"><input type="checkbox" name="enabled" ${!p || p.enabled ? "checked" : ""}> Show on the sign-in page</label>
      ${c.reauth_fresh ? "" : `<label class="f">Your ScanVault password <span class="hint">confirms it's you</span><input name="reauth" type="password" required autocomplete="current-password"></label>`}
      <p class="err" id="serr"></p>`;
    $("select", f).onchange = e => { preset = e.target.value; draw(); };
    $("#cpy", f).onclick = () => copyText(c.redirect_uri);
  };
  draw();
  f.onsubmit = async e => {
    e.preventDefault();
    const body = { preset, name: E("name").value, client_id: E("client_id").value, scopes: E("scopes").value, enabled: E("enabled").checked, fields: {} };
    if (E("issuer")) body.issuer = E("issuer").value;
    presets[preset].fields.forEach(x => body.fields[x.key] = E("fld_" + x.key).value);
    if (E("client_secret").value) body.client_secret = E("client_secret").value;
    if (E("reauth")) body.password = E("reauth").value;
    const btn = $("#ssave", mm.el); btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Checking provider…';
    try { await API.req(p ? "PUT" : "POST", p ? `/api/sso/providers/${encodeURIComponent(p.id)}` : "/api/sso/providers", body); mm.close(); toast(p ? "Provider saved" : "Provider added. Now link your account."); ssoCard(); }
    catch (er) { $("#serr", f).textContent = er.message; btn.disabled = false; btn.textContent = "Save"; }
  };
  const del = $("#sdel", mm.el);
  if (del) del.onclick = async () => {
    if (!await confirmBox("Remove provider", `Accounts linked through ${esc(p.name)} will be unlinked.`, "Remove")) return;
    try { await API.req("DELETE", `/api/sso/providers/${encodeURIComponent(p.id)}`); mm.close(); toast("Provider removed"); ssoCard(); } catch (er) { handleErr(er); }
  };
}

boot();
if (!API.demo && "serviceWorker" in navigator && location.protocol !== "file:") navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
