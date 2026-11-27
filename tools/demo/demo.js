/* In-browser mock of the ScanVault API, used only for the interactive preview
   build (tools/build_demo.py). Sample people and images are synthetic. */
(() => {
"use strict";
const now = Date.now();
const iso = (d = 0) => new Date(now - d * 86400000).toISOString();
let seq = 1;
const id = () => "d" + (seq++).toString(36) + Math.random().toString(36).slice(2, 7);
const wait = (ms = 180) => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------- synthetic images
function rnd(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const cache = {};
function scan(mod, n = 0, size = 384) {
  const key = mod + n + size;
  if (cache[key]) return cache[key];
  const c = document.createElement("canvas"); c.width = c.height = size;
  const g = c.getContext("2d"), R = rnd(n * 97 + mod.length * 13), S = size / 384;
  g.fillStyle = "#000"; g.fillRect(0, 0, size, size);
  g.save(); g.scale(S, S);
  const blob = (x, y, rx, ry, col, rot = 0) => { g.beginPath(); g.ellipse(x, y, rx, ry, rot, 0, Math.PI * 2); g.fillStyle = col; g.fill(); };
  const soft = (x, y, r, a, b) => { const gr = g.createRadialGradient(x, y, 0, x, y, r); gr.addColorStop(0, a); gr.addColorStop(1, b); return gr; };
  if (mod === "MR") { // knee, sagittal
    blob(192, 192, 150, 175, "#3a3a3a");
    blob(192, 110, 62, 95, soft(192, 110, 95, "#9a9a9a", "#4a4a4a"));
    blob(196, 290, 58, 80, soft(196, 290, 90, "#a0a0a0", "#505050"));
    g.fillStyle = "#121212"; g.fillRect(132, 196, 124, 14 + n % 3 * 2);
    blob(280, 170, 18, 40, "#cfcfcf", .2);
    g.strokeStyle = "#e0e0e0"; g.lineWidth = 3; g.beginPath(); g.moveTo(140, 200); g.quadraticCurveTo(192, 186 - n * 2, 250, 200); g.stroke();
  } else if (mod === "CT") { // chest axial
    blob(192, 200, 170, 128, "#6b6b6b");
    blob(192, 200, 160, 118, "#5a5a5a");
    blob(128, 190, 58, 82 - n * 3, "#0b0b0b"); blob(256, 190, 58, 82 - n * 3, "#0b0b0b");
    blob(192, 214, 34, 30, "#8c8c8c"); blob(192, 290, 18, 16, "#e8e8e8");
    for (let i = 0; i < 9; i++) { g.strokeStyle = "#d8d8d8"; g.lineWidth = 3; g.beginPath(); g.arc(192, 200, 150 + i % 2 * 6, Math.PI * (.15 + i * .08), Math.PI * (.18 + i * .08)); g.stroke(); }
    for (let i = 0; i < 40; i++) blob(100 + R() * 60, 150 + R() * 90, 1.4, 1.4, "#3a3a3a");
  } else if (mod === "DX") { // wrist / hand
    g.fillStyle = "#151515"; g.fillRect(0, 0, 384, 384);
    blob(192, 300, 110, 120, "#3c3c3c");
    for (let f = 0; f < 5; f++) { const x = 110 + f * 42 - (f === 0 ? 20 : 0); for (let k = 0; k < 3; k++) { g.fillStyle = "#cfcfcf"; g.beginPath(); g.roundRect(x - 9, 60 + k * 52 + (f === 0 ? 70 : 0), 18, 44, 8); g.fill(); } }
    for (let i = 0; i < 8; i++) blob(150 + (i % 4) * 28, 250 + Math.floor(i / 4) * 30, 13, 12, "#bdbdbd");
    g.fillStyle = "#d0d0d0"; g.fillRect(150, 300, 26, 90); g.fillRect(200, 300, 30, 90);
  } else if (mod === "US") {
    g.beginPath(); g.moveTo(192, 20); g.arc(192, 20, 330, Math.PI * .3, Math.PI * .7); g.closePath(); g.fillStyle = "#1f1f1f"; g.fill(); g.clip();
    for (let i = 0; i < 2600; i++) { const v = 40 + R() * 120; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(R() * 384, R() * 384, 2, 1.5); }
    blob(200, 210, 70, 44, "#0a0a0a", .3); blob(170, 290, 50, 20, "#bdbdbd", -.2);
  } else if (mod === "MG") {
    g.beginPath(); g.moveTo(0, 20); g.quadraticCurveTo(330, 60, 300, 200); g.quadraticCurveTo(320, 340, 0, 370); g.fillStyle = soft(60, 200, 300, "#9a9a9a", "#454545"); g.fill();
    for (let i = 0; i < 60; i++) { g.strokeStyle = "rgba(230,230,230,.15)"; g.beginPath(); g.moveTo(0, 60 + i * 5); g.quadraticCurveTo(120 + R() * 80, 190, 260, 200 + (R() - .5) * 40); g.stroke(); }
    blob(190, 170, 4, 4, "#fff");
  } else { // head CT
    blob(192, 192, 150, 175, "#e9e9e9"); blob(192, 192, 138, 163, "#6e6e6e"); blob(160, 190, 12, 46, "#161616", .2); blob(224, 190, 12, 46, "#161616", -.2);
  }
  g.restore();
  const img = g.getImageData(0, 0, size, size), d = img.data;
  for (let i = 0; i < d.length; i += 4) { const nz = (R() - .5) * 18; d[i] = d[i + 1] = d[i + 2] = Math.max(0, Math.min(255, d[i] + nz)); }
  g.putImageData(img, 0, 0);
  g.fillStyle = "rgba(160,200,200,.8)"; g.font = `${11 * S}px ui-monospace,monospace`; g.fillText(`${mod} ${n + 1}`, 8 * S, 16 * S);
  return (cache[key] = c.toDataURL("image/jpeg", .82));
}
function reportPage(title, center, date, lines) {
  const key = "rep" + title + date;
  if (cache[key]) return cache[key];
  const c = document.createElement("canvas"); c.width = 850; c.height = 1100;
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, 850, 1100);
  g.fillStyle = "#0b5d63"; g.fillRect(0, 0, 850, 10);
  g.fillStyle = "#14161a"; g.font = "700 30px system-ui,sans-serif"; g.fillText(center, 70, 90);
  g.font = "16px system-ui,sans-serif"; g.fillStyle = "#5b6670"; g.fillText("Diagnostic imaging report · sample document", 70, 120);
  g.fillStyle = "#14161a"; g.font = "600 22px system-ui,sans-serif"; g.fillText(title, 70, 190);
  g.font = "17px system-ui,sans-serif"; g.fillText("Exam date: " + date, 70, 222);
  let y = 290;
  for (const [h, t] of lines) {
    g.font = "700 17px system-ui,sans-serif"; g.fillText(h, 70, y); y += 30;
    g.font = "17px system-ui,sans-serif";
    let line = "";
    for (const w of t.split(" ")) { if (g.measureText(line + w).width > 700) { g.fillText(line, 70, y); y += 26; line = ""; } line += w + " "; }
    g.fillText(line, 70, y); y += 46;
  }
  g.strokeStyle = "#e1e5e6"; g.beginPath(); g.moveTo(70, 1000); g.lineTo(780, 1000); g.stroke();
  g.fillStyle = "#5b6670"; g.font = "15px system-ui,sans-serif"; g.fillText("Electronically signed. This sample report is synthetic.", 70, 1030);
  return (cache[key] = c.toDataURL("image/png"));
}

// ---------------------------------------------------------- sample data
const db = { patients: [], studies: [], files: [], targets: [], runs: [], users: { ryan: "x" } };
const P = (name, dob, sex, mrn, notes) => { const p = { id: id(), name, date_of_birth: dob, sex, mrn, notes, created_at: iso(200) }; db.patients.push(p); return p; };
function S(p, title, mod, date, facility, nImg, report, extra = {}) {
  const ag = Math.max(0.6, Math.min(400, (now - Date.parse(date + "T12:00:00")) / 86400000 + 1.5));
  const s = { id: id(), patient_id: p.id, title, modality: mod, study_date: date, facility, description: extra.description || null, accession: extra.accession || null, referring_physician: extra.ref || null, body_part: extra.body || null, study_uid: mod === "OT" ? null : "1.2.840." + seq, created_at: iso(ag) };
  db.studies.push(s);
  if (report) db.files.push({ id: id(), study_id: s.id, patient_id: p.id, filename: report.name, content_type: "application/pdf", size_bytes: 180000 + seq * 931, kind: "report", has_thumbnail: false, uploaded_at: iso(ag - 0.01), _report: report });
  const imod = mod === "CR" ? "DX" : mod;
  for (let i = 0; i < nImg; i++) db.files.push({ id: id(), study_id: s.id, patient_id: p.id, filename: `IM${String(i + 1).padStart(6, "0")}.dcm`, content_type: "application/dicom", size_bytes: imod === "DX" || imod === "MG" ? 9800000 + i * 1000 : 530000 + i * 777, kind: "dicom", has_thumbnail: true, uploaded_at: iso(ag - 0.01), _img: [imod, i + (extra.shift || 0)],
    dicom: { modality: mod, study_date: date, series_description: extra.series || title.toUpperCase(), institution: facility, instance_number: String(i + 1), rows: imod === "DX" ? "2500" : "512", columns: imod === "DX" ? "2048" : "512", frames: "1", body_part: extra.body, patient_name: p.name.toUpperCase() } });
  return s;
}
const jane = P("Jane Doe", "1980-04-02", "F", "LIC-448812", "Allergic to iodinated contrast (hives, 2019). Ortho: Dr. Patel.");
S(jane, "MRI left knee without contrast", "MR", "2026-03-14", "Lakeside Imaging Center", 8, { name: "MRI-knee-report.pdf", lines: [["Indication", "Left knee pain after running injury, rule out meniscal tear."], ["Technique", "Multiplanar multisequence MRI of the left knee without contrast."], ["Findings", "Horizontal tear of the posterior horn of the medial meniscus extending to the inferior articular surface. Anterior and posterior cruciate ligaments intact. Small joint effusion."], ["Impression", "1. Medial meniscus posterior horn tear. 2. Small effusion."]] }, { accession: "A26031488", ref: "Dr. A. Patel", body: "KNEE", series: "SAG PD FS" });
S(jane, "CT chest with contrast", "CT", "2025-09-02", "Northside Hospital", 10, { name: "CT-chest-report.pdf", lines: [["Indication", "Persistent cough, follow-up of 6 mm right upper lobe nodule."], ["Findings", "Previously described right upper lobe nodule is stable at 6 mm. No new nodules. No lymphadenopathy. Heart size normal."], ["Impression", "Stable 6 mm nodule, likely benign. Consider 12-month follow-up."]] }, { accession: "NH-2025-77120", body: "CHEST", series: "AXIAL 1.25MM" });
S(jane, "X-ray right wrist", "DX", "2024-01-20", "Urgent Care West", 3, null, { body: "WRIST", series: "PA / LAT / OBL" });
const sam = P("Sam Doe", "2014-07-18", "M", "PED-20931", "");
S(sam, "Chest X-ray", "DX", "2026-10-05", "Children's Hospital", 2, { name: "CXR-report.pdf", lines: [["Indication", "Fever and cough for four days."], ["Findings", "Patchy opacity in the right lower lobe. No pleural effusion."], ["Impression", "Findings compatible with right lower lobe pneumonia."]] }, { body: "CHEST", series: "PA / LAT" });
S(sam, "Ultrasound abdomen", "US", "2025-05-11", "Children's Hospital", 5, null, { body: "ABDOMEN" });
const mom = P("Margaret Doe", "1952-11-30", "F", "MRN 0039915", "Pacemaker (MRI conditional). Cardiologist: Dr. Huang.");
S(mom, "Screening mammogram", "MG", "2026-08-21", "Women's Imaging Associates", 4, { name: "mammo-BIRADS-report.pdf", lines: [["Exam", "Bilateral screening mammogram with tomosynthesis."], ["Findings", "Scattered fibroglandular tissue. No suspicious mass, calcifications or architectural distortion."], ["Impression", "BI-RADS 1: Negative. Routine annual screening."]] }, { body: "BREAST", series: "R CC / L CC / R MLO / L MLO" });
S(mom, "CT head without contrast", "CT", "2025-12-03", "Northside Hospital ED", 6, { name: "CT-head-report.pdf", lines: [["Indication", "Fall at home, on anticoagulation."], ["Findings", "No acute intracranial hemorrhage, mass effect or midline shift. Age-appropriate volume loss."], ["Impression", "No acute intracranial abnormality."]] }, { body: "HEAD", series: "AXIAL 5MM" });
S(jane, "MRI left knee", "MR", "2024-06-10", "Lakeside Imaging Center", 8, { name: "MRI-knee-2024-report.pdf", lines: [["Indication", "Left knee pain, no injury."], ["Findings", "Mild degenerative signal in the posterior horn of the medial meniscus without a tear. Small joint effusion."], ["Impression", "Early medial meniscal degeneration. No tear."]] }, { body: "KNEE", series: "SAG PD FS", shift: 1 });
S(mom, "CT head", "CT", "2023-04-18", "Northside Hospital", 6, null, { body: "HEAD", series: "AXIAL 5MM", shift: 1 });
db.files.filter(f => f._img && f._img[0] === "CT" && db.studies.find(s => s.id === f.study_id).title.includes("head")).forEach(f => f._img[0] = "HEAD");
db.targets.push(
  { id: id(), type: "gdrive", name: "Google Drive", enabled: true, schedule_hours: 24, keep_last: 14, config: { auth_mode: "oauth", client_id: "1234-abc.apps.googleusercontent.com", folder_id: "1AbCdEfScanVault" }, secrets_set: { client_secret: true, refresh_token: true, service_account_json: false }, connected: true, last_run: iso(0.3), last_status: "ok" },
  { id: id(), type: "sftp", name: "Home NAS", enabled: true, schedule_hours: 6, keep_last: 30, config: { protocol: "sftp", host: "nas.local", port: 22, username: "scanvault", remote_dir: "backups/scanvault", host_key: "SHA256:q2Vx4Jw0b6Tq3yZ6m3Q1nqk2sLr8m8uZ7v0c4p9X1aA" }, secrets_set: { password: false, private_key: true, key_passphrase: false }, connected: true, last_run: iso(0.1), last_status: "ok" },
  { id: id(), type: "s3", name: "Backblaze B2", enabled: true, schedule_hours: 24, keep_last: 30, config: { provider: "b2", bucket: "doe-family-scans", prefix: "scanvault", region: "us-west-004", access_key_id: "004a1b2c3d4e5f0000000001" }, secrets_set: { secret_access_key: true }, connected: true, last_run: iso(0.25), last_status: "ok" },
  { id: id(), type: "box", name: "Box", enabled: true, schedule_hours: 168, keep_last: 8, config: { auth_mode: "oauth", client_id: "boxclient123" }, secrets_set: { client_secret: true, refresh_token: false }, connected: false, last_run: null, last_status: null });
const size0 = () => db.files.reduce((a, f) => a + f.size_bytes, 0);
const bname = d => "scanvault-backup-" + new Date(now - d * 86400000).toISOString().slice(0, 19).replace(/[-:]/g, "").replace("T", "-") + "Z.svb";
[[0.1, 1], [0.3, 0], [0.35, 1], [1.3, 0], [1.35, 1], [2.3, 0]].forEach(([d, t]) => db.runs.push({ id: id(), target_id: db.targets[t].id, target_name: db.targets[t].name, target_type: db.targets[t].type, started_at: iso(d), finished_at: iso(d), status: "ok", message: `Uploaded ${bname(d)} (${size0().toLocaleString()} bytes)`, size: Math.round(size0() * 1.002), filename: bname(d) }));
db.runs.splice(4, 0, { id: id(), target_id: db.targets[1].id, target_name: "Home NAS", target_type: "sftp", started_at: iso(1.6), finished_at: iso(1.6), status: "error", message: "SSH connection failed: timed out (NAS was asleep)", size: null, filename: bname(1.6) });

const state = { prefs: { theme: "system", dark_color: "#0d1416" }, setup: false, authed: false, unlocked: false, passphrase: null, recovery: null, saved: false, vault_id: "SV-7F3A91C2", fp: "8C1E-55D0-A7F2-0B94" };
function recoveryText() {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"; const a = new Uint8Array(58); crypto.getRandomValues(a);
  const k = [...a].map(x => chars[x % 32]).join("").replace(/(.{4})/g, "$1 ").trim();
  return `SCANVAULT EMERGENCY RECOVERY KEY\n================================\n\nVault ID:         ${state.vault_id}\nKey fingerprint:  ${state.fp}\nCreated:          ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC\n\nThis file can decrypt EVERY ScanVault backup of this vault, including\nbackups stored on Google Drive, Box or SFTP servers. Anyone who holds it\ncan read the medical records inside. Print it or store it offline.\n\n-----BEGIN SCANVAULT RECOVERY KEY-----\n${k.slice(0, 39)}\n${k.slice(40)}\n-----END SCANVAULT RECOVERY KEY-----\n\nTo restore: choose 'Restore from backup' in a fresh ScanVault, or run\n  python3 tools/restore.py --key scanvault-recovery-key.txt --backup <file>.svb --out ./restored\n`;
}

// ---------------------------------------------------------- similar studies (mirrors app/similar.py)
const MODG = { CR: "XR", DX: "XR", DR: "XR", XR: "XR", MR: "MR", MRI: "MR", PT: "PET", PET: "PET" }, MODN = { XR: "X-ray", MR: "MRI", CT: "CT", US: "ultrasound", MG: "mammogram", NM: "nuclear medicine", PET: "PET" };
const STOP = new Set("a an and the of with without w wo o contrast iv non noncon study exam imaging image images series scan routine protocol view views screening followup follow up limited complete bilateral single mr ct xr us dx cr mg nm pet pt".split(" "));
const ALIAS = { mri: "mr", xray: "xr", x: "xr", ray: "xr", radiograph: "xr", lt: "left", rt: "right", l: "left", r: "right", knees: "knee", hips: "hip", wrists: "wrist", shoulders: "shoulder", ankles: "ankle", brain: "head", skull: "head", thorax: "chest", lungs: "chest", lung: "chest", abd: "abdomen", abdominal: "abdomen", mammo: "breast", mammogram: "breast", mammography: "breast" };
const modOf = s => { const m = (s.modality || "").trim().toUpperCase(); return MODG[m] || m; };
const wordsOf = s => new Set(([s.title, s.description, s.body_part].join(" ").toLowerCase().match(/[a-z]+/g) || []).map(w => ALIAS[w] || w).filter(w => !STOP.has(w) && w.length > 1));
function likeness(a, b) {
  let pts = 0; const why = [], ma = modOf(a), mb = modOf(b), ba = (a.body_part || "").trim().toLowerCase(), bb = (b.body_part || "").trim().toLowerCase();
  if (ma && ma === mb) { pts += 2; why.push(`Same type (${MODN[ma] || ma})`); }
  if (ba && ba === bb) { pts += 2; why.push(`Same body part (${ba})`); }
  const wa = wordsOf(a), wb = wordsOf(b), side = w => w === "left" || w === "right";
  const pa = [...wa].filter(w => !side(w)), pb = [...wb].filter(w => !side(w)), all = new Set([...pa, ...pb]), common = pa.filter(w => pb.includes(w));
  if (common.length) { pts += 3 * common.length / all.size; if (!(ba && ba === bb)) why.push("Similar description"); }
  const sa = [...wa].filter(side).sort().join(), sb = [...wb].filter(side).sort().join();
  if (pts && sa && sb && sa !== sb) { pts -= 1.5; why.push("Other side"); }
  return [Math.round(Math.max(pts, 0) * 100) / 100, why];
}
function similarTo(base) {
  const day = s => s.study_date ? Date.parse(s.study_date + "T12:00:00") : null, d0 = day(base);
  return db.studies.filter(s => s.patient_id === base.patient_id && s.id !== base.id).map(s => {
    const [pts, why] = likeness(base, s), d1 = day(s), fl = db.files.filter(f => f.study_id === s.id);
    return { ...studyOut(s), similarity: pts, suggested: pts >= 3 && !why.includes("Other side"), match_reasons: why, days_apart: d0 && d1 ? Math.round(Math.abs(d1 - d0) / 86400000) : null,
      relation: !(d0 && d1) ? null : d1 < d0 ? "prior" : d1 > d0 ? "later" : "same day", thumbnail_ids: fl.filter(f => f.has_thumbnail).slice(0, 3).map(f => f.id), image_count: fl.filter(f => f.kind !== "report").length };
  }).sort((x, y) => (x.suggested === y.suggested ? 0 : x.suggested ? -1 : 1) || y.similarity - x.similarity || (x.days_apart ?? 1e6) - (y.days_apart ?? 1e6));
}

// ---------------------------------------------------------- router
const err = (status, msg, code) => { const e = new Error(msg); e.status = status; e.code = code; throw e; };
const pub = f => { const { _img, _report, _blob, ...o } = f; return o; };
const counts = pid => {
  const ss = db.studies.filter(s => s.patient_id === pid);
  return { study_count: ss.length, file_count: db.files.filter(f => f.patient_id === pid).length, last_study_date: ss.map(s => s.study_date).filter(Boolean).sort().pop() || null };
};
const studyOut = s => ({ ...s, file_count: db.files.filter(f => f.study_id === s.id).length });
const need = () => { if (!state.authed) err(401, "Not signed in", "unauthenticated"); if (!state.unlocked) err(423, "Vault is locked", "locked"); };
const findP = pid => db.patients.find(p => p.id === pid) || err(404, "Patient not found");
const findS = sid => db.studies.find(s => s.id === sid) || err(404, "Study not found");
const findT = tid => db.targets.find(t => t.id === tid) || err(404, "Backup target not found");

function runBackup(ts) {
  const name = bname(0), size = Math.round(size0() * 1.002);
  return ts.map(t => {
    let status = "ok", message = `Uploaded ${name} (${size.toLocaleString()} bytes)`;
    if (!t.connected) { status = "error"; message = `${t.name} is not connected yet: click 'Connect account'`; }
    t.last_run = new Date().toISOString(); t.last_status = status;
    db.runs.unshift({ id: id(), target_id: t.id, target_name: t.name, target_type: t.type, started_at: t.last_run, finished_at: t.last_run, status, message, size, filename: name });
    return { target_id: t.id, name: t.name, status, message };
  });
}

async function req(method, path, body) {
  await wait();
  const url = path.split("?")[0];
  let m;
  if (url === "/api/status") return { setup_complete: state.setup, unlocked: state.unlocked, authenticated: state.authed, username: state.authed ? "ryan" : null, vault_id: state.vault_id, fingerprint: state.authed ? state.fp : null, recovery_key_saved: state.authed ? state.saved : null, passphrase_restore: state.authed ? !!state.pr : null, secure: true, setup_code_required: !state.setup, version: "0.2.0 (preview)" };
  if (url === "/api/setup") { await wait(700); state.setup = state.authed = state.unlocked = true; state.passphrase = body.passphrase; state.recovery = recoveryText(); return { token: "demo", recovery_key: state.recovery, fingerprint: state.fp, vault_id: state.vault_id }; }
  if (url === "/api/auth/login") { state.authed = true; return { token: "demo", unlocked: state.unlocked }; }
  if (url === "/api/auth/logout") { state.authed = false; return { ok: true }; }
  if (url === "/api/auth/password") return { ok: true };
  if (url === "/api/auth/logout-others") return { ok: true, signed_out: 0 };
  if (url === "/api/vault/settings") { if (method === "PUT") state.pr = !!body.passphrase_restore; return { passphrase_restore: !!state.pr }; }
  if (url === "/api/vault/rekey") { if (state.passphrase && body.passphrase !== state.passphrase) err(400, "Wrong encryption key"); await wait(900); state.recovery = recoveryText(); state.saved = false; return { ok: true, fingerprint: state.fp, recovery_key: state.recovery }; }
  if (url === "/api/vault/unlock") {
    if (body.recovery_key) { if (!/BEGIN SCANVAULT RECOVERY KEY/i.test(body.recovery_key)) err(400, "That file isn't a ScanVault recovery key"); }
    else if (state.passphrase && body.passphrase !== state.passphrase) err(400, "Wrong encryption key");
    state.unlocked = true; return { unlocked: true };
  }
  if (url === "/api/prefs") { if (method === "PUT") Object.assign(state.prefs, body); return { ...state.prefs }; }
  if (url === "/api/vault/lock") { state.unlocked = false; return { unlocked: false }; }
  if (url === "/api/vault/recovery-key") { if (state.passphrase && body.passphrase !== state.passphrase) err(400, "Wrong encryption key"); return state.recovery || recoveryText(); }
  if (url === "/api/vault/recovery-key/confirm") { if (body.recovery_key && !/BEGIN SCANVAULT RECOVERY KEY/i.test(body.recovery_key)) err(400, "That file isn't a ScanVault recovery key"); state.saved = !!body.recovery_key || state.saved; return { ok: true }; }
  if (url === "/api/vault/passphrase") { if (state.passphrase && body.current !== state.passphrase) err(400, "Current encryption key is wrong"); state.passphrase = body.new; return { ok: true }; }
  need();
  if (url === "/api/stats") return { patients: db.patients.length, studies: db.studies.length, files: db.files.length, bytes: size0(), last_backup_at: db.runs.find(r => r.status === "ok")?.finished_at || null };
  if (url === "/api/patients" && method === "GET") return db.patients.map(p => ({ ...p, ...counts(p.id) })).sort((a, b) => a.name.localeCompare(b.name));
  if (url === "/api/patients" && method === "POST") { if (!body.name) err(400, "Patient name is required"); const p = P(body.name, body.date_of_birth || null, body.sex || null, body.mrn || null, body.notes || null); return { ...p, ...counts(p.id) }; }
  if ((m = url.match(/^\/api\/patients\/([^/]+)$/))) {
    const p = findP(m[1]);
    if (method === "PUT") { Object.assign(p, Object.fromEntries(Object.entries(body).map(([k, v]) => [k, v || null]))); if (!p.name) err(400, "Patient name is required"); }
    if (method === "DELETE") { db.patients = db.patients.filter(x => x !== p); db.studies = db.studies.filter(s => s.patient_id !== p.id); db.files = db.files.filter(f => f.patient_id !== p.id); return { ok: true }; }
    return { ...p, ...counts(p.id) };
  }
  if ((m = url.match(/^\/api\/patients\/([^/]+)\/studies$/))) {
    findP(m[1]);
    if (method === "POST") { const s = { id: id(), patient_id: m[1], title: body.title || "Imaging study", modality: body.modality || null, study_date: body.study_date || null, facility: body.facility || null, description: body.description || null, created_at: new Date().toISOString() }; db.studies.push(s); return studyOut(s); }
    return db.studies.filter(s => s.patient_id === m[1]).map(studyOut).sort((a, b) => (b.study_date || "").localeCompare(a.study_date || ""));
  }
  if (url === "/api/studies/recent") {
    const lim = +(new URLSearchParams(path.split("?")[1] || "").get("limit") || 8);
    const act = s => Math.max(Date.parse(s.created_at) || 0, ...db.files.filter(f => f.study_id === s.id).map(f => Date.parse(f.uploaded_at) || 0));
    return db.studies.map(s => ({ s, a: act(s) })).sort((x, y) => y.a - x.a || (y.s.study_date || "").localeCompare(x.s.study_date || "")).slice(0, lim).map(({ s, a }) => {
      const fs = db.files.filter(f => f.study_id === s.id);
      return { ...studyOut(s), patient_name: db.patients.find(p => p.id === s.patient_id)?.name || null, last_activity: new Date(a).toISOString(), thumbnail_ids: fs.filter(f => f.has_thumbnail).slice(0, 3).map(f => f.id), report_count: fs.filter(f => f.kind === "report").length };
    });
  }
  if ((m = url.match(/^\/api\/studies\/([^/]+)$/))) {
    const s = findS(m[1]);
    if (method === "PUT") Object.assign(s, Object.fromEntries(Object.entries(body).map(([k, v]) => [k, v || null])));
    if (method === "DELETE") { db.studies = db.studies.filter(x => x !== s); db.files = db.files.filter(f => f.study_id !== s.id); return { ok: true }; }
    return studyOut(s);
  }
  if ((m = url.match(/^\/api\/studies\/([^/]+)\/similar$/))) return similarTo(findS(m[1]));
  if ((m = url.match(/^\/api\/studies\/([^/]+)\/files$/))) {
    findS(m[1]);
    const order = f => ({ report: 0, dicom: 2 }[f.kind] ?? 1);
    return db.files.filter(f => f.study_id === m[1]).sort((a, b) => order(a) - order(b) || (+(a.dicom?.instance_number || 0)) - (+(b.dicom?.instance_number || 0))).map(pub);
  }
  if ((m = url.match(/^\/api\/files\/([^/]+)$/)) && method === "DELETE") { db.files = db.files.filter(f => f.id !== m[1]); return { ok: true }; }
  if (url === "/api/backups/targets" && method === "GET") return db.targets;
  if (url === "/api/backups/targets" && method === "POST") {
    const secretKeys = { gdrive: ["client_secret", "refresh_token", "service_account_json"], box: ["client_secret", "refresh_token"], sftp: ["password", "private_key", "key_passphrase"],
      dropbox: ["app_secret", "refresh_token"], onedrive: ["client_secret", "refresh_token"], s3: ["secret_access_key"], webdav: ["password"], smb: ["password"], local: [] }[body.type];
    const cfg = { ...body.config }, sec = {};
    secretKeys.forEach(k => { sec[k] = !!cfg[k]; delete cfg[k]; });
    const oauth = ["gdrive", "box", "dropbox", "onedrive"].includes(body.type) && (cfg.auth_mode || "oauth") === "oauth";
    const t = { id: id(), type: body.type, name: body.name, enabled: body.enabled, schedule_hours: body.schedule_hours, keep_last: body.keep_last, config: cfg, secrets_set: sec, connected: oauth ? !!sec.refresh_token : true, last_run: null, last_status: null };
    db.targets.push(t); return t;
  }
  if ((m = url.match(/^\/api\/backups\/targets\/([^/]+)$/))) {
    const t = findT(m[1]);
    if (method === "DELETE") { db.targets = db.targets.filter(x => x !== t); return { ok: true }; }
    const cfg = { ...body.config };
    Object.keys(t.secrets_set).forEach(k => { if (cfg[k]) t.secrets_set[k] = true; delete cfg[k]; });
    Object.assign(t, { name: body.name, enabled: body.enabled, schedule_hours: body.schedule_hours, keep_last: body.keep_last, config: { ...t.config, ...cfg } });
    if (!["gdrive", "box", "dropbox", "onedrive"].includes(t.type) || cfg.auth_mode && cfg.auth_mode !== "oauth") t.connected = true;
    return t;
  }
  if ((m = url.match(/^\/api\/backups\/targets\/([^/]+)\/test$/))) {
    await wait(600); const t = findT(m[1]);
    if (!t.connected) err(502, `${t.name} is not connected yet: click 'Connect account'`);
    if (t.type === "sftp" && !t.config.host_key) t.config.host_key = "SHA256:demoPinnedHostKey0b6Tq3yZ6m3Q1nqk2sLr8m8uZ7v";
    return { ok: true, message: { sftp: `Connected over ${(t.config.protocol || "sftp").toUpperCase()}. Host key pinned.`, s3: `Connected. Backups go to s3://${t.config.bucket || "bucket"}/${t.config.prefix || ""}`, local: "Writable. 3,712.4 GB free in " + (t.config.path || "/backups"), smb: `Connected. Backups go to \\\\${t.config.host || "nas"}\\${t.config.share || "backup"}` }[t.type] || "Connected. Backups go to folder 'ScanVault'." };
  }
  if ((m = url.match(/^\/api\/backups\/targets\/([^/]+)\/run$/))) { await wait(1100); return { results: runBackup([findT(m[1])]) }; }
  if (url === "/api/backups/run") { await wait(1400); return { results: runBackup(db.targets.filter(t => t.enabled)) }; }
  if (url === "/api/backups/runs") return db.runs;
  if ((m = url.match(/^\/api\/backups\/targets\/([^/]+)\/remote$/))) { const t = findT(m[1]); return db.runs.filter(r => r.target_id === t.id && r.status === "ok").map(r => ({ name: r.filename, handle: r.filename })); }
  if ((m = url.match(/^\/api\/oauth\/([^/]+)\/redirect-uri$/))) return { redirect_uri: `https://scanvault.example.com/api/oauth/${m[1]}/callback` };
  err(404, "Not available in the preview: " + url);
}

async function upload(path, form, onProgress) {
  for (let i = 1; i <= 8; i++) { await wait(90); onProgress && onProgress(i / 8); }
  const url = path.split("?")[0];
  if (url === "/api/setup/restore") { state.setup = state.authed = state.unlocked = true; return { patients: db.patients.length, files: db.files.length }; }
  if (url === "/api/backups/restore") { if (form.get("confirm") !== "REPLACE") err(400, "Type REPLACE to confirm restoring over current data"); await wait(800); return { patients: db.patients.length, files: db.files.length }; }
  need();
  let m, pid, sid = null;
  if ((m = url.match(/^\/api\/patients\/([^/]+)\/import$/))) pid = findP(m[1]).id;
  else if ((m = url.match(/^\/api\/studies\/([^/]+)\/files$/))) { const s = findS(m[1]); sid = s.id; pid = s.patient_id; }
  const out = [], skipped = [];
  const files = form.getAll("file");
  const today = new Date().toISOString().slice(0, 10);
  const ensure = (title, mod) => {
    let s = db.studies.find(x => x.patient_id === pid && x.title === title);
    if (!s) { s = { id: id(), patient_id: pid, title, modality: mod, study_date: today, facility: mod ? "Uploaded imaging center" : null, created_at: new Date().toISOString() }; db.studies.push(s); }
    return s.id;
  };
  for (const f of files) {
    const n = f.name.toLowerCase();
    const isImg = /^image\//.test(f.type) || /\.(png|jpe?g|gif|webp)$/.test(n);
    const isPdf = f.type === "application/pdf" || n.endsWith(".pdf");
    const isDcm = n.endsWith(".dcm") || n.endsWith(".zip") || !n.includes(".");
    const target = sid || (isDcm ? ensure("Imported DICOM study", "OT") : ensure(`Uploads ${today}`, null));
    if (n.endsWith(".zip")) {
      for (let i = 0; i < 6; i++) { const r = { id: id(), study_id: target, patient_id: pid, filename: `IM${String(i + 1).padStart(6, "0")}.dcm`, content_type: "application/dicom", size_bytes: Math.round(f.size / 6), kind: "dicom", has_thumbnail: true, uploaded_at: new Date().toISOString(), _img: ["HEAD", i], dicom: { modality: "OT", instance_number: String(i + 1), series_description: "From " + f.name } }; db.files.push(r); out.push(pub(r)); }
      skipped.push("DICOMDIR");
      continue;
    }
    const r = { id: id(), study_id: target, patient_id: pid, filename: f.name, content_type: isImg ? f.type || "image/jpeg" : isPdf ? "application/pdf" : isDcm ? "application/dicom" : f.type || "application/octet-stream", size_bytes: f.size, kind: isImg ? "image" : isPdf ? "report" : isDcm ? "dicom" : "other", has_thumbnail: isImg || isDcm, uploaded_at: new Date().toISOString() };
    if (isImg) r._blob = URL.createObjectURL(f);
    if (isDcm) { r._img = ["MR", seq % 7]; r.dicom = { modality: "OT", instance_number: "1" }; }
    db.files.push(r); out.push(pub(r));
  }
  return url.includes("/import") ? { files: out, skipped, studies: [] } : path.includes("format=full") ? { files: out, skipped } : out;
}

// Window/level on the synthetic images: gray levels are mapped to plausible
// modality values (HU for CT) so presets like Lung and Bone behave as in the app.
const HU = [[0, -1000], [20, -820], [60, -120], [110, 40], [160, 80], [200, 400], [255, 1600]];
const toHU = v => { for (let i = 1; i < HU.length; i++) if (v <= HU[i][0]) { const [a, x] = HU[i - 1], [b, y] = HU[i]; return x + (v - a) / (b - a) * (y - x); } return 1600; };
const LUT = new Float32Array(256).map((_, v) => toHU(v));
const pixCache = {};
function demoPixels(f) {
  if (pixCache[f.id]) return Promise.resolve(pixCache[f.id]);
  return new Promise(res => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
      const g = c.getContext("2d"); g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, c.width, c.height).data, n = c.width * c.height, v = new Float32Array(n);
      const ct = f._img[0] === "CT" || f._img[0] === "HEAD";
      let mn = Infinity, mx = -Infinity;
      for (let i = 0; i < n; i++) { const x = ct ? LUT[d[i * 4]] : d[i * 4] * 12; v[i] = x; if (x < mn) mn = x; if (x > mx) mx = x; }
      const dc = ct ? (f._img[0] === "HEAD" ? 40 : 40) : 1400, dw = ct ? (f._img[0] === "HEAD" ? 80 : 400) : 2600;
      res(pixCache[f.id] = { v, w: c.width, h: c.height, min: mn, max: mx, dc, dw, modality: ct ? "CT" : f.dicom?.modality || "MR" });
    };
    img.src = scan(f._img[0], f._img[1], 640);
  });
}
async function render(fid, { wc, ww } = {}) {
  const f = fileById(fid);
  if (!f._img) throw new Error("not renderable");
  const P = await demoPixels(f), c = wc ?? P.dc, w = Math.max(1, ww ?? P.dw);
  const cv = document.createElement("canvas"); cv.width = P.w; cv.height = P.h;
  const g = cv.getContext("2d"), out = g.createImageData(P.w, P.h), o = out.data, lo = c - .5 - (w - 1) / 2, k = 255 / (w - 1);
  for (let i = 0; i < P.v.length; i++) { let y = (P.v[i] - lo) * k; y = y < 0 ? 0 : y > 255 ? 255 : y; o[i * 4] = o[i * 4 + 1] = o[i * 4 + 2] = y; o[i * 4 + 3] = 255; }
  g.putImageData(out, 0, 0);
  return { url: cv.toDataURL("image/jpeg", .9), wc: c, ww: w, dc: P.dc, dw: P.dw, min: P.min, max: P.max, frames: 1, frame: 0, rgb: false, modality: P.modality };
}

const fileById = fid => db.files.find(f => f.id === fid) || {};
window.SV_API = {
  demo: true, req, upload, render,
  thumbUrl: fid => { const f = fileById(fid); return f._blob || (f._img ? scan(f._img[0], f._img[1], 192) : ""); },
  previewUrl: fid => { const f = fileById(fid); return f._blob || (f._img ? scan(f._img[0], f._img[1], 768) : ""); },
  fileUrl: fid => { const f = fileById(fid); return f._blob || (f._img ? scan(f._img[0], f._img[1], 768) : "#"); },
  reportImage: f => { const r = fileById(f.id)._report; if (!r) return null; const s = db.studies.find(x => x.id === f.study_id) || {}; return reportPage(s.title || "Report", s.facility || "Imaging center", s.study_date || "", r.lines); },
  backupDownloadUrl: () => "#",
  oauthStart: (type, tid) => { const t = findT(tid); t.connected = true; t.secrets_set.refresh_token = true; document.dispatchEvent(new CustomEvent("sv-demo-toast", { detail: `${({ box: "Box", dropbox: "Dropbox", onedrive: "Microsoft", gdrive: "Google" })[type]} sign-in would open here. Marked as connected for the preview.` })); document.querySelector('[data-go="backups"]')?.click(); },
};
window.SV_DEMO_SKIP = () => { state.setup = state.authed = state.unlocked = true; state.recovery = recoveryText(); };
})();
