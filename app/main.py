"""ScanVault server."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
import zipfile
import zlib
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlencode

import requests
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import backup, crypto, media, similar, sso, targets
from .forms import Form as UploadForm, FormError, UploadPart, parse_form
from .spool import Spool, SpoolFull
from .store import fsync_dir, fsync_tree, write_durable, Store, seal, unseal

VERSION = "0.2.0"
log = logging.getLogger("scanvault")
logging.basicConfig(level=os.environ.get("SCANVAULT_LOG_LEVEL", "INFO"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


SESSION_DAYS = 30
SESSION_IDLE_DAYS = _env_int("SCANVAULT_SESSION_IDLE_DAYS", 14)
AUTO_LOCK_MINUTES = _env_int("SCANVAULT_AUTO_LOCK_MINUTES", 0)
MAX_UPLOAD = _env_int("SCANVAULT_MAX_UPLOAD_MB", 2048) * 1024 * 1024
MAX_ZIP_TOTAL = _env_int("SCANVAULT_MAX_ZIP_MB", 8192) * 1024 * 1024
MAX_JSON = 1024 * 1024
PUBLIC_URL = os.environ.get("SCANVAULT_PUBLIC_URL", "").rstrip("/")
COOKIE_SECURE = os.environ.get("SCANVAULT_COOKIE_SECURE", "auto")
MIN_PASSPHRASE = 12
REKEY_WAIT = 300.0  # seconds a key change waits for running uploads and restores
API_DOCS = os.environ.get("SCANVAULT_API_DOCS", "").lower() in ("1", "true", "yes")

app = FastAPI(
    title="ScanVault",
    version=VERSION,
    docs_url="/api/docs" if API_DOCS else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if API_DOCS else None,
)
store = Store()
vault = crypto.Vault()
backup_lock = threading.Lock()
_state = {"maintenance": None, "last_activity": time.time(), "setup_code": None, "rekeying": False}
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class KeyGate:
    """Signed-in work that writes with the master key (uploads, edits, deletes,
    restores, saving backup tokens, building an archive). A master key change
    closes the gate, waits for the work already inside to finish, then runs.
    Only requests that have passed sign-in get in, so a stranger holding a
    connection open can't delay a key change; reads and sign-in never wait."""

    def __init__(self):
        self.n = 0
        self.closed = False
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            if self.closed:
                raise ApiError(503, "Changing the master key. Try again in a moment.", "maintenance", {"Retry-After": "30"})
            self.n += 1

    def exit(self) -> None:
        with self._lock:
            self.n -= 1

    @contextmanager
    def held(self):
        self.enter()
        try:
            yield
        finally:
            self.exit()


key_gate = KeyGate()
_setup_lock = asyncio.Lock()


class ApiError(Exception):
    def __init__(self, status: int, msg: str, code: str | None = None, headers: dict | None = None):
        self.status, self.msg, self.code, self.headers = status, msg, code, headers


@app.exception_handler(ApiError)
async def _api_error(_, exc: ApiError):
    body = {"error": exc.msg}
    if exc.code:
        body["code"] = exc.code
    return JSONResponse(body, status_code=exc.status, headers=exc.headers)


@app.exception_handler(crypto.CryptoError)
async def _crypto_error(_, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(targets.TargetError)
async def _target_error(_, exc):
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.exception_handler(FormError)
async def _form_error(_, exc: FormError):
    return JSONResponse({"error": exc.msg}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_, exc: RequestValidationError):
    errs = exc.errors()
    where = ".".join(str(x) for x in errs[0].get("loc", [])[1:]) if errs else ""
    msg = (errs[0].get("msg") if errs else "Invalid request") + (f" ({where})" if where else "")
    return JSONResponse({"error": msg, "code": "invalid"}, status_code=400)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_, exc: StarletteHTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
    "connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    try:
        resp = await call_next(request)
    finally:
        if request.scope.pop("sv_gated", False):
            key_gate.exit()
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Content-Security-Policy", APP_CSP)
    if _secure(request):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# =================================================================== helpers

def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def setup_complete() -> bool:
    return store.keystore() is not None and store.get_config("setup_complete", False)


def mk() -> bytes:
    if _state["maintenance"]:
        raise ApiError(503, _state["maintenance"], "maintenance")
    if not vault.unlocked:
        raise ApiError(423, "Vault is locked", "locked")
    _state["last_activity"] = time.time()
    return vault.master_key  # type: ignore[return-value]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


class Throttle:
    """Failed-attempt limiter for passwords and keys.

    Each attempt is counted as a failure *before* the (slow) check runs and
    forgiven on success, so parallel guesses are limited too. After
    ``free`` failures inside ``window`` seconds every further attempt must
    wait ``base * 2**k`` seconds (capped at ``max_wait``) after the last one."""

    def __init__(self, free: int, window: float = 900, base: float = 15, max_wait: float = 900):
        self.free, self.window, self.base, self.max_wait = free, window, base, max_wait
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        lst = [t for t in self._fails.get(key, []) if now - t < max(self.window, self.max_wait)]
        if lst:
            self._fails[key] = lst
        else:
            self._fails.pop(key, None)
        return lst

    def attempt(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for k in keys:
                lst = self._recent(k, now)
                inwin = [t for t in lst if now - t < self.window]
                if len(inwin) >= self.free:
                    wait = min(self.max_wait, self.base * 2 ** (len(inwin) - self.free)) - (now - lst[-1])
                    if wait > 0:
                        raise ApiError(429, f"Too many attempts. Try again in {math.ceil(wait)} seconds.", "rate_limited", {"Retry-After": str(math.ceil(wait))})
            for k in keys:
                self._fails.setdefault(k, []).append(now)
            if len(self._fails) > 10000:
                for k in list(self._fails)[:5000]:
                    self._fails.pop(k, None)

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                lst = self._fails.get(k)
                if lst:
                    lst.pop()

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)


login_throttle = Throttle(free=5)
account_throttle = Throttle(free=10)
device_throttle = Throttle(free=10)
key_throttle = Throttle(free=5)
setup_throttle = Throttle(free=5)


def _token_from(request: Request) -> tuple[str | None, str | None]:
    """Returns (token, how) where how is 'bearer' or 'cookie'."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), "bearer"
    c = request.cookies.get("sv_session")
    return (c, "cookie") if c else (None, None)


def _th(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def current_user(request: Request) -> dict | None:
    tok, how = _token_from(request)
    if not tok:
        return None
    th = _th(tok)
    r = store.conn().execute(
        "SELECT u.id, u.username, u.role, s.expires_at, s.last_seen, s.created_at, s.created_at AS session_created, s.identity_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
        (th,),
    ).fetchone()
    now = time.time()
    if not r or r["expires_at"] < now:
        return None
    seen = r["last_seen"] or r["created_at"]
    if SESSION_IDLE_DAYS > 0 and now - seen > SESSION_IDLE_DAYS * 86400:
        store.conn().execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    if now - seen > 60:
        store.conn().execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, th))
    d = dict(r)
    d["via"], d["token_hash"] = how, th
    return d


def _csrf(request: Request, how: str | None) -> None:
    # Cookie-authenticated state-changing requests must carry the custom
    # header (a cross-site form cannot set it). Bearer requests are immune.
    if request.method not in ("GET", "HEAD") and how != "bearer":
        if request.headers.get("x-scanvault") != "1":
            raise ApiError(403, "Missing X-ScanVault header", "csrf")


def require_user(request: Request) -> dict:
    if not setup_complete():
        raise ApiError(409, "ScanVault has not been set up yet", "setup_required")
    u = current_user(request)
    if not u:
        raise ApiError(401, "Not signed in", "unauthenticated")
    _csrf(request, u["via"])
    return u


def data_access(request: Request) -> bytes:
    require_user(request)
    m = mk()
    if request.method in _MUTATING:
        gate_request(request)
    return m


def gate_request(request: Request) -> None:
    """Hold the key gate until this (signed-in) request has been answered."""
    if not request.scope.get("sv_gated"):
        key_gate.enter()
        request.scope["sv_gated"] = True


def new_session(user_id: str, identity_id: str | None = None) -> tuple[str, float]:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    exp = now + SESSION_DAYS * 86400
    c = store.conn()
    c.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
    c.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen,identity_id) VALUES(?,?,?,?,?,?)",
        (_th(tok), user_id, now, exp, now, identity_id),
    )
    return tok, exp


def _secure(request: Request) -> bool:
    if COOKIE_SECURE == "auto":
        return request.url.scheme == "https"
    return COOKIE_SECURE.lower() in ("1", "true", "yes")


def _is_browser(request: Request) -> bool:
    return request.headers.get("x-scanvault") == "1"


def session_response(request: Request, body: dict, tok: str, exp: float) -> JSONResponse:
    # The web UI authenticates with the HttpOnly cookie only; the token is
    # put in the body just for API/mobile clients (which do not send the
    # browser's X-ScanVault header), so script on the page can never read it.
    if not _is_browser(request):
        body = {"token": tok, **body}
    resp = JSONResponse(body)
    resp.set_cookie("sv_session", tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=_secure(request), path="/")
    return resp


async def body_json(request: Request, limit: int = MAX_JSON) -> dict:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise ApiError(415, "Expected Content-Type: application/json")
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > limit:
        raise ApiError(413, "Request body too large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ApiError(413, "Request body too large")
    try:
        j = json.loads(bytes(buf))
    except Exception:
        raise ApiError(400, "Expected a JSON body")
    if not isinstance(j, dict):
        raise ApiError(400, "Expected a JSON object")
    return j


def _str(v, maxlen=2000) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        raise ApiError(400, "Expected text")
    s = str(v).strip()
    return s[:maxlen] if s else None


def jstr(j: dict, key: str, default: str = "") -> str:
    """A string field from a JSON body; anything else is a 400, not a 500."""
    v = j.get(key)
    if v is None:
        return default
    if not isinstance(v, str):
        raise ApiError(400, f"{key} must be text")
    return v


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(name: str | None, default: str = "file") -> str:
    """Last path component (either slash style), no control characters."""
    n = _CTRL.sub("", str(name or "")).replace("\\", "/").split("/")[-1].strip()
    if n in ("", ".", ".."):
        return default
    return n[:255]


def _valid_date(v: str | None, what: str) -> str | None:
    if not v:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ApiError(400, f"{what} must be a date like 2024-03-14")


def recovery_text(master_key: bytes) -> str:
    ks = store.keystore()
    return crypto.recovery_file_text(master_key, ks["vault_id"], ks.get("created", ""))


def _header_safe(v) -> str:
    return re.sub(r"[^\x20-\x7e]", "", str(v or ""))[:200]


# =================================================================== status/setup

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status(request: Request):
    ks = store.keystore()
    done = setup_complete()
    u = current_user(request) if done else None
    return {
        "setup_complete": done,
        "setup_code_required": not done,
        "unlocked": vault.unlocked,
        "authenticated": bool(u),
        "username": u["username"] if u else None,
        "vault_id": ks["vault_id"] if (ks and u) else None,
        "fingerprint": ks["fingerprint"] if (ks and u) else None,
        "recovery_key_saved": bool(store.get_config("recovery_key_saved", False)) if u else None,
        "passphrase_restore": bool(store.get_config("backup_include_wrapped_key", False)) if u else None,
        "secure": _secure(request),
        "maintenance": _state["maintenance"],
        "local_login": local_login_enabled(),
        "sso": [_sso_public(p) for p in _sso_providers()] if setup_complete() else [],
        "version": VERSION if u else None,
    }


def _validate_new_account(username: str, password: str) -> None:
    if not username or len(username) > 64:
        raise ApiError(400, "Choose a username")
    if len(password) < 10:
        raise ApiError(400, "Password must be at least 10 characters")
    if len(password) > 1024:
        raise ApiError(400, "Password is too long")


def _validate_new_passphrase(p: str) -> None:
    if len(p) < MIN_PASSPHRASE:
        raise ApiError(400, f"Encryption key must be at least {MIN_PASSPHRASE} characters")
    if len(p) > 1024:
        raise ApiError(400, "Encryption key is too long")
    if p != p.strip():
        raise ApiError(400, "The encryption key can't start or end with a space")
    if passphrase_bits(p) < MIN_PASSPHRASE_BITS:
        raise ApiError(400, "That encryption key is too easy to guess. Use four or more random words (the generator makes six), or a longer mix of letters, digits and symbols.", "weak_passphrase")


MIN_PASSPHRASE_BITS = 44
_COMMON = ("password", "passwort", "qwerty", "azerty", "letmein", "welcome", "scanvault", "123456", "abcdef", "iloveyou", "admin", "monkey", "dragon")


def passphrase_bits(p: str) -> float:
    """A deliberately conservative guess at how hard a passphrase is to guess.
    Backups can be attacked offline, so this matters more than for a password."""
    low = p.lower()
    # Words: about 11 bits each for words a person picked (a random EFF list word is 12.9).
    words = [w for w in re.split(r"[^a-z]+", low) if len(w) >= 3]
    word_bits = 11.0 * len(set(words)) if len(words) >= 3 else 0.0
    # Characters: pool size from the kinds used, length after removing
    # well-known fragments and squeezing repeats.
    for c in _COMMON:
        low = low.replace(c, "")
    squeezed = re.sub(r"(.)\1+", r"\1", low)
    eff = min(len(squeezed), 2 * len(set(squeezed)))
    pool = 0
    pool += 26 if re.search(r"[a-z]", p) else 0
    pool += 26 if re.search(r"[A-Z]", p) else 0
    pool += 10 if re.search(r"[0-9]", p) else 0
    pool += 33 if re.search(r"[^A-Za-z0-9]", p) else 0
    char_bits = eff * math.log2(pool) if pool else 0.0
    if re.fullmatch(r"[0-9]+", p):
        char_bits = min(char_bits, len(set(p)) * 3.3)
    # People build "random" strings from a word, a year and a symbol
    # (Summer2024!!). Count each run of letters as one guessable word, a year
    # as a handful of choices, and repeats as nothing.
    char_bits = min(char_bits, _pattern_bits(p))
    return max(word_bits, char_bits)


def _pattern_bits(p: str) -> float:
    bits = 0.0
    for run in re.findall(r"[A-Z][a-z]+|[a-z]+|[A-Z]+|[0-9]+|[^A-Za-z0-9]+", p):
        if run[0].isalpha():
            if len(run) >= 4:
                bits += 11.0 + (1.0 if run[0].isupper() else 0.0)
            else:
                bits += len(run) * math.log2(52)
        elif run[0].isdigit():
            bits += 7.0 if re.fullmatch(r"(19|20)\d\d", run) else len(set(run)) * 3.3 + max(0, len(run) - len(set(run)))
        else:
            bits += 5.0 * len(set(run)) + 1.0 * (len(run) - len(set(run)))
    return bits


def _new_setup_code() -> str:
    pre = os.environ.get("SCANVAULT_SETUP_CODE", "").strip()
    if pre:
        return pre
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


def _announce_setup_code() -> None:
    if setup_complete():
        _state["setup_code"] = None
        return
    if not _state["setup_code"]:
        _state["setup_code"] = _new_setup_code()
    bar = "=" * 64
    log.warning("\n%s\n ScanVault is not set up yet.\n First-run setup code: %s\n (enter it on the setup screen; it changes on every restart)\n%s", bar, _state["setup_code"], bar)


def _check_setup_code(request: Request, given: str | None) -> None:
    if setup_complete():
        raise ApiError(409, "Already set up")
    setup_throttle.attempt("ip:" + client_ip(request))
    if not _state["setup_code"]:
        _announce_setup_code()
    want = _state["setup_code"] or ""
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())  # noqa: E731
    if not want or not given or not hmac.compare_digest(norm(given), norm(want)):
        raise ApiError(403, "Wrong setup code. It is printed in the server log (docker logs scanvault).", "setup_code")
    setup_throttle.success("ip:" + client_ip(request))


@app.post("/api/setup")
async def setup(request: Request):
    if setup_complete():
        raise ApiError(409, "Already set up")
    j = await body_json(request, 64 * 1024)
    _check_setup_code(request, request.headers.get("x-setup-code") or jstr(j, "setup_code"))
    username = jstr(j, "username").strip()
    password = jstr(j, "password")
    passphrase = jstr(j, "passphrase")
    _validate_new_account(username, password)
    _validate_new_passphrase(passphrase)
    if passphrase == password:
        raise ApiError(400, "Use a different value for the encryption key and the sign-in password")
    async with _setup_lock:
        if setup_complete():
            raise ApiError(409, "Already set up")
        master = crypto.new_master_key()
        vault_id = "SV-" + secrets.token_hex(4).upper()
        ks = await asyncio.to_thread(crypto.wrap_master_key, master, passphrase, vault_id)
        ks["created"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        pw_hash = await asyncio.to_thread(crypto.hash_password, password)
        store.write_keystore(ks)
        c = store.conn()
        uid = crypto.random_id()
        c.execute("DELETE FROM sessions")
        c.execute("DELETE FROM user_devices")
        c.execute("DELETE FROM user_identities")
        c.execute("DELETE FROM users")
        c.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)", (uid, username, pw_hash, "admin", time.time()))
        store.set_config("recovery_key_saved", False)
        store.set_config("backup_include_wrapped_key", False)
        store.set_config("setup_complete", True)
        _state["setup_code"] = None
        vault.master_key = master
        tok, exp = new_session(uid)
    return session_response(
        request,
        {"expires_at": iso(exp), "vault_id": vault_id, "fingerprint": ks["fingerprint"], "recovery_key": recovery_text(master)},
        tok,
        exp,
    )


@app.post("/api/setup/restore")
async def setup_restore(request: Request):
    """Fresh install: restore a .svb backup, then create/replace the admin login.
    Multipart fields: backup_file, username, password, and recovery_file /
    recovery_key or passphrase. The setup code goes in the X-Setup-Code header
    (checked before the upload is read)."""
    if setup_complete():
        raise ApiError(409, "Already set up. Restoring over an existing vault is done from Backups > Restore.")
    _check_setup_code(request, request.headers.get("x-setup-code"))
    form = await parse_form(request, store.tmp_dir, MAX_UPLOAD + 1024 * 1024)
    try:
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        passphrase = form.get("passphrase")
        _validate_new_account(username, password)
        bf = form.file("backup_file")
        if not bf:
            raise ApiError(400, "Choose the backup file (.svb)")
        rec_text = form.get("recovery_key")
        rf = form.file("recovery_file")
        if not rec_text and rf:
            with rf.spool.open() as fh:
                rec_text = fh.read(64 * 1024).decode("utf-8", "replace")
        async with _setup_lock:
            if setup_complete():
                raise ApiError(409, "Already set up")
            archive = store.tmp_dir / f"restore-{crypto.random_id()}.svb"
            try:
                with bf.spool.open() as src, open(archive, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                master = await asyncio.to_thread(backup.master_key_for, archive, passphrase=passphrase, recovery_text=rec_text)
                header = await asyncio.to_thread(backup.restore_into_store, store, archive, master)
            finally:
                archive.unlink(missing_ok=True)
            pw_hash = await asyncio.to_thread(crypto.hash_password, password)
            c = store.conn()
            c.execute("DELETE FROM sessions")
            c.execute("DELETE FROM user_devices")
            c.execute("DELETE FROM user_identities")  # SSO links belonged to the replaced login
            c.execute("DELETE FROM users")
            _sso_save({**_sso_cfg(), "local_login": True})
            uid = crypto.random_id()
            c.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)", (uid, username, pw_hash, "admin", time.time()))
            store.set_config("setup_complete", True)
            _state["setup_code"] = None
            vault.master_key = master
            await asyncio.to_thread(migrate_file_macs, master)
            tok, exp = new_session(uid)
    finally:
        form.close()
    counts = _counts()
    return session_response(request, {"expires_at": iso(exp), "restored": header["created"], "targets_disabled": True, **counts}, tok, exp)


# =================================================================== auth

@app.post("/api/auth/login")
async def login(request: Request):
    if not setup_complete():
        raise ApiError(409, "ScanVault has not been set up yet", "setup_required")
    if not local_login_enabled():
        raise ApiError(403, "Password sign-in is turned off. Use single sign-on.", "local_login_disabled")
    j = await body_json(request, 64 * 1024)
    username = jstr(j, "username").strip()
    password = jstr(j, "password")
    keys = ("ip:" + client_ip(request),)
    login_throttle.attempt(*keys)
    r = store.conn().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    # The per-account limit stops guessing spread over many addresses, but must
    # not let a stranger lock the owner out: a browser or app that has signed
    # in before carries its own random device token, and is limited per device
    # instead (so a stolen token still only buys a few guesses).
    dev_id = _device_lookup(r, request.cookies.get("sv_device") or jstr(j, "device")) if r else None
    akey = ("dev:" + dev_id) if dev_id else ("user:" + username.lower())
    (device_throttle if dev_id else account_throttle).attempt(akey)
    ok = await asyncio.to_thread(crypto.verify_password, password, r["password_hash"] if r else crypto.DUMMY_PASSWORD_HASH)
    if not r or not ok:
        raise ApiError(401, "Wrong username or password")
    login_throttle.success(*keys)
    (device_throttle if dev_id else account_throttle).reset(akey)
    tok, exp = new_session(r["id"])
    dev = None if dev_id else _new_device(r["id"])
    body = {"expires_at": iso(exp), "unlocked": vault.unlocked}
    if dev and not _is_browser(request):
        body["device"] = dev
    resp = session_response(request, body, tok, exp)
    if dev:
        resp.set_cookie("sv_device", dev, max_age=365 * 86400, httponly=True, samesite="strict", secure=_secure(request), path="/api/auth/login")
    return resp


MAX_DEVICES = 50


def _new_device(user_id: str) -> str:
    """A random token proving this browser or app signed in to this account
    before. Stored hashed; a password change retires all of them."""
    did, secret = crypto.random_id(), secrets.token_urlsafe(24)
    c = store.conn()
    now = time.time()
    c.execute("INSERT INTO user_devices(id,user_id,token_hash,created_at,last_used) VALUES(?,?,?,?,?)", (did, user_id, _th(secret), now, now))
    c.execute(
        "DELETE FROM user_devices WHERE user_id=? AND id NOT IN (SELECT id FROM user_devices WHERE user_id=? ORDER BY last_used DESC LIMIT ?)",
        (user_id, user_id, MAX_DEVICES),
    )
    return f"d2.{did}.{secret}"


def _device_lookup(user, token: str | None) -> str | None:
    parts = str(token or "").split(".")
    if len(parts) != 3 or parts[0] != "d2":
        return None
    row = store.conn().execute("SELECT id, token_hash FROM user_devices WHERE id=? AND user_id=?", (parts[1], user["id"])).fetchone()
    if not row or not hmac.compare_digest(row["token_hash"], _th(parts[2])):
        return None
    store.conn().execute("UPDATE user_devices SET last_used=? WHERE id=?", (time.time(), row["id"]))
    return row["id"]


@app.post("/api/auth/logout")
def logout(request: Request):
    tok, how = _token_from(request)
    if tok:
        _csrf(request, how)
        store.conn().execute("DELETE FROM sessions WHERE token_hash=?", (_th(tok),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sv_session", path="/", httponly=True, samesite="strict", secure=_secure(request))
    return resp


@app.post("/api/auth/logout-others")
def logout_others(request: Request):
    """Sign out every other device and browser."""
    u = require_user(request)
    n = store.conn().execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], u["token_hash"])).rowcount
    return {"ok": True, "signed_out": n}


@app.post("/api/auth/password")
async def change_password(request: Request):
    u = require_user(request)
    j = await body_json(request, 64 * 1024)
    key_throttle.attempt("pw:" + u["id"])
    r = store.conn().execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if not await asyncio.to_thread(crypto.verify_password, jstr(j, "current"), r["password_hash"]):
        raise ApiError(400, "Current password is wrong")
    key_throttle.success("pw:" + u["id"])
    new = jstr(j, "new")
    _validate_new_account(u["username"], new)
    ks = store.keystore()
    try:
        await asyncio.to_thread(crypto.unwrap_master_key, ks, new)
        raise ApiError(400, "Use a different value for the sign-in password and the encryption key")
    except crypto.CryptoError:
        pass
    h = await asyncio.to_thread(crypto.hash_password, new)
    c = store.conn()
    c.execute("UPDATE users SET password_hash=? WHERE id=?", (h, u["id"]))
    n = c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], u["token_hash"])).rowcount
    c.execute("DELETE FROM user_devices WHERE user_id=?", (u["id"],))
    return {"ok": True, "signed_out": n}


# =================================================================== vault

def _clear_caches() -> None:
    with _pix_lock:
        _pix_cache.clear()


def migrate_file_macs(master: bytes) -> int:
    """One-time upgrade: move each file's plain SHA-256 into its sealed record
    and keep only a keyed MAC in the clear column."""
    c = store.conn()
    rows = c.execute("SELECT id, sha256, blob FROM files WHERE sha256 NOT LIKE 'h1:%'").fetchall()
    for r in rows:
        d = unseal(master, r["blob"], f"file:{r['id']}")
        d.setdefault("sha256", r["sha256"])
        c.execute(
            "UPDATE files SET sha256=?, blob=? WHERE id=?",
            (crypto.file_mac(master, d["sha256"]), seal(master, d, f"file:{r['id']}"), r["id"]),
        )
    if rows:
        log.info("Protected the content digests of %d file(s)", len(rows))
    return len(rows)


def _unlock_keys(request: Request) -> tuple[str, ...]:
    return ("ip:" + client_ip(request), "vault")


async def _verify_passphrase(request: Request, passphrase: str, wrong_msg: str = "Wrong encryption key") -> bytes:
    keys = _unlock_keys(request)
    key_throttle.attempt(*keys)
    ks = store.keystore()
    try:
        master = await asyncio.to_thread(crypto.unwrap_master_key, ks, passphrase)
    except crypto.CryptoError:
        await asyncio.sleep(0.3)
        raise ApiError(400, wrong_msg)
    key_throttle.success(*keys)
    if crypto.keystore_outdated(ks):
        nks = await asyncio.to_thread(crypto.wrap_master_key, master, passphrase, ks["vault_id"])
        nks["created"] = ks.get("created")
        store.write_keystore(nks)
        log.info("Keystore upgraded to stronger key derivation settings")
    return master


@app.post("/api/vault/unlock")
async def unlock(request: Request):
    require_user(request)
    if _state["maintenance"]:
        raise ApiError(503, _state["maintenance"], "maintenance")
    j = await body_json(request, 64 * 1024)
    ks = store.keystore()
    if j.get("recovery_key"):
        keys = _unlock_keys(request)
        key_throttle.attempt(*keys)
        master = crypto.parse_recovery_file(jstr(j, "recovery_key"))
        if crypto.fingerprint(master) != ks["fingerprint"]:
            raise ApiError(400, "That recovery key belongs to a different vault")
        key_throttle.success(*keys)
    else:
        master = await _verify_passphrase(request, jstr(j, "passphrase"))
    vault.master_key = master
    _state["last_activity"] = time.time()
    await asyncio.to_thread(migrate_file_macs, master)
    return {"unlocked": True}


@app.post("/api/vault/lock")
def lock(request: Request):
    require_user(request)
    vault.lock()
    _clear_caches()
    return {"unlocked": False}


@app.post("/api/vault/recovery-key")
async def get_recovery_key(request: Request):
    """Re-download the recovery key file. Requires the encryption passphrase."""
    require_user(request)
    gate_request(request)
    j = await body_json(request, 64 * 1024)
    master = await _verify_passphrase(request, jstr(j, "passphrase"))
    return PlainTextResponse(recovery_text(master), headers={"Content-Disposition": 'attachment; filename="scanvault-recovery-key.txt"'})


@app.post("/api/vault/recovery-key/confirm")
async def confirm_recovery_key(request: Request):
    """The user proves they saved the file by pasting/uploading it back."""
    require_user(request)
    j = await body_json(request, 64 * 1024)
    if j.get("recovery_key"):
        master = crypto.parse_recovery_file(jstr(j, "recovery_key"))
        if crypto.fingerprint(master) != store.keystore()["fingerprint"]:
            raise ApiError(400, "That key file belongs to a different vault")
        store.set_config("recovery_key_saved", True)
    elif not j.get("skip"):
        raise ApiError(400, "Upload the recovery key file to confirm")
    return {"ok": True, "recovery_key_saved": bool(store.get_config("recovery_key_saved", False))}


@app.post("/api/vault/passphrase")
async def change_passphrase(request: Request):
    """Re-wrap the master key under a new passphrase. Data and backups stay
    valid and the recovery key file does not change. (To make old backups and
    the old recovery key useless, use /api/vault/rekey.)"""
    u = require_user(request)
    gate_request(request)  # not while the master key is being replaced
    j = await body_json(request, 64 * 1024)
    master = await _verify_passphrase(request, jstr(j, "current"), "Current encryption key is wrong")
    new = jstr(j, "new")
    _validate_new_passphrase(new)
    r = store.conn().execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if await asyncio.to_thread(crypto.verify_password, new, r["password_hash"]):
        raise ApiError(400, "Use a different value for the encryption key and the sign-in password")
    ks = store.keystore()
    nks = await asyncio.to_thread(crypto.wrap_master_key, master, new, ks["vault_id"])
    nks["created"] = ks.get("created")
    store.write_keystore(nks)
    return {"ok": True}


@app.get("/api/vault/settings")
def vault_settings(request: Request):
    require_user(request)
    return {"passphrase_restore": bool(store.get_config("backup_include_wrapped_key", False))}


@app.put("/api/vault/settings")
async def put_vault_settings(request: Request):
    require_user(request)
    j = await body_json(request, 64 * 1024)
    if "passphrase_restore" in j:
        store.set_config("backup_include_wrapped_key", bool(j["passphrase_restore"]))
    return vault_settings(request)


@app.post("/api/vault/rekey")
async def rekey(request: Request):
    """Rotate the master key: every file, preview and record is re-encrypted
    under a brand-new key and a new recovery key file is issued. Afterwards the
    old recovery key file and old backups no longer open anything made from
    now on (old backups can still be opened with the old recovery key)."""
    u = require_user(request)
    j = await body_json(request, 64 * 1024)
    master = await _verify_passphrase(request, jstr(j, "passphrase"))
    new_pass = jstr(j, "new_passphrase") or jstr(j, "passphrase")
    _validate_new_passphrase(new_pass)
    if new_pass != jstr(j, "passphrase"):
        r = store.conn().execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
        if await asyncio.to_thread(crypto.verify_password, new_pass, r["password_hash"]):
            raise ApiError(400, "Use a different value for the encryption key and the sign-in password")
    if _state["maintenance"] or not backup_lock.acquire(blocking=False):
        raise ApiError(409, "A backup or another key change is running. Try again when it has finished.")
    failed_after_commit = False
    try:
        with key_gate._lock:
            key_gate.closed = True
        _state["rekeying"] = True
        # Let signed-in uploads, restores and deletes that are already running
        # finish first. Reads and sign-in keep working meanwhile.
        deadline = time.monotonic() + REKEY_WAIT
        while key_gate.n > 0:
            if time.monotonic() > deadline:
                raise ApiError(409, "Uploads or other changes are still running. Try again when they have finished.", "busy")
            await asyncio.sleep(0.05)
        _state["maintenance"] = "Changing the master key. This can take a while for large vaults."
        try:
            new_master = await asyncio.to_thread(_rekey, master, new_pass)
        except RekeyCommitted:
            failed_after_commit = True
            raise ApiError(500, "The master key change could not be finished. Restart ScanVault to complete it, then unlock with the new encryption key.", "rekey_incomplete")
    finally:
        if failed_after_commit:
            # The database already uses the new key but the files could not be
            # swapped in. Stay locked until a restart finishes the change.
            vault.lock()
            _clear_caches()
            _state["maintenance"] = "A master key change could not be finished. Restart ScanVault to complete it."
        else:
            _state["maintenance"] = None
            with key_gate._lock:
                key_gate.closed = False
        _state["rekeying"] = False
        backup_lock.release()
    vault.master_key = new_master
    _clear_caches()
    return {"ok": True, "fingerprint": store.keystore()["fingerprint"], "recovery_key": recovery_text(new_master)}


class RekeyCommitted(Exception):
    """A key change was committed to the database but not finished on disk."""


def _rekey_paths(jid: str) -> tuple[Path, Path, Path]:
    return store.dir / f"rekey-{jid}", store.dir / "keystore.rekey.json", store.dir / "rekey.json"


def _rekey(old: bytes, passphrase: str) -> bytes:
    new = crypto.new_master_key()
    jid = crypto.random_id()
    work, new_ks_path, journal = _rekey_paths(jid)
    c = store.conn()
    with store.backup_guard():
        try:
            for r in c.execute("SELECT id FROM files").fetchall():
                fid = r["id"]
                for src, sub, aad in ((store.blob_path(fid), "files", f"file:{fid}"), (store.preview_path(fid), "previews", f"preview:{fid}")):
                    if not src.exists():
                        continue
                    dst = work / sub / fid[:2] / f"{fid}.enc"
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with open(src, "rb") as i, open(dst, "wb") as o:
                        crypto.encrypt_stream(new, crypto.StreamDecryptor(old, i, aad.encode()), o, aad.encode())
            for sub in ("files", "previews"):
                (work / sub).mkdir(parents=True, exist_ok=True)
            ks = store.keystore()
            nks = crypto.wrap_master_key(new, passphrase, ks["vault_id"])
            nks["created"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            # Everything the commit below points at must be on disk first.
            fsync_tree(work)
            write_durable(new_ks_path, json.dumps(nks, indent=2).encode())
            write_durable(journal, json.dumps({"id": jid}).encode())
            fsync_dir(store.dir)
            c.execute("BEGIN IMMEDIATE")
            try:
                for t, ctx in (("patients", "patient"), ("studies", "study")):
                    for r in c.execute(f"SELECT id, blob FROM {t}").fetchall():
                        d = unseal(old, r["blob"], f"{ctx}:{r['id']}")
                        c.execute(f"UPDATE {t} SET blob=? WHERE id=?", (seal(new, d, f"{ctx}:{r['id']}"), r["id"]))
                for r in c.execute("SELECT id, sha256, blob FROM files").fetchall():
                    d = unseal(old, r["blob"], f"file:{r['id']}")
                    if not str(r["sha256"]).startswith("h1:"):
                        d.setdefault("sha256", r["sha256"])
                    mac = crypto.file_mac(new, d["sha256"]) if d.get("sha256") else r["sha256"]
                    c.execute("UPDATE files SET blob=?, sha256=? WHERE id=?", (seal(new, d, f"file:{r['id']}"), mac, r["id"]))
                for r in c.execute("SELECT id, config FROM backup_targets").fetchall():
                    d = unseal(old, r["config"], f"target:{r['id']}")
                    c.execute("UPDATE backup_targets SET config=? WHERE id=?", (seal(new, d, f"target:{r['id']}"), r["id"]))
                c.execute(
                    "INSERT INTO config(key,value) VALUES('rekey_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(jid),),
                )
                c.execute("INSERT INTO config(key,value) VALUES('recovery_key_saved','false') ON CONFLICT(key) DO UPDATE SET value='false'")
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            new_ks_path.unlink(missing_ok=True)
            journal.unlink(missing_ok=True)
            raise
        try:
            _finish_rekey(jid)
        except Exception as exc:
            log.error("Master key change committed but could not be finished: %s", exc)
            raise RekeyCommitted(str(exc)) from exc
    log.info("Master key rotated; new fingerprint %s", crypto.fingerprint(new))
    return new


def _finish_rekey(jid: str) -> None:
    """Roll a committed key change forward (idempotent; also run at startup)."""
    work, new_ks_path, journal = _rekey_paths(jid)
    for sub, target in (("files", store.files_dir), ("previews", store.preview_dir)):
        src = work / sub
        old = target.with_name(target.name + ".prerekey")
        if src.exists():
            if target.exists() and not old.exists():
                target.rename(old)
            elif target.exists():
                shutil.rmtree(target)
            src.rename(target)
            fsync_dir(store.dir)
        shutil.rmtree(old, ignore_errors=True)
    if new_ks_path.exists():
        new_ks_path.replace(store.keystore_path)
        fsync_dir(store.dir)
    shutil.rmtree(work, ignore_errors=True)
    journal.unlink(missing_ok=True)


def _recover_rekey() -> None:
    journal = store.dir / "rekey.json"
    if not journal.exists():
        return
    try:
        jid = json.loads(journal.read_text())["id"]
    except Exception:
        journal.unlink(missing_ok=True)
        return
    if store.get_config("rekey_id") == jid:
        log.warning("Finishing an interrupted master key change")
        _finish_rekey(jid)
    else:
        log.warning("Discarding an interrupted master key change (nothing was changed)")
        work, new_ks_path, _ = _rekey_paths(jid)
        shutil.rmtree(work, ignore_errors=True)
        new_ks_path.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)


# =================================================================== patients

def _patient_out(row, master: bytes, counts: dict | None = None) -> dict:
    d = unseal(master, row["blob"], f"patient:{row['id']}")
    out = {
        "id": row["id"],
        "name": d.get("name"),
        "date_of_birth": d.get("date_of_birth"),
        "mrn": d.get("mrn"),
        "sex": d.get("sex"),
        "notes": d.get("notes"),
        "created_at": iso(row["created_at"]),
        "updated_at": iso(row["updated_at"]),
    }
    if counts is not None:
        out.update(counts.get(row["id"], {"study_count": 0, "file_count": 0, "last_study_date": None}))
    return out


def _patient_fields(j: dict, existing: dict | None = None) -> dict:
    d = dict(existing or {})
    for k in ("name", "date_of_birth", "mrn", "sex", "notes"):
        if k in j:
            d[k] = _str(j[k], 5000 if k == "notes" else 200)
    if "date_of_birth" in j:
        d["date_of_birth"] = _valid_date(d.get("date_of_birth"), "Date of birth")
    if not d.get("name"):
        raise ApiError(400, "Patient name is required")
    return d


def _patient_counts(master: bytes) -> dict:
    c = store.conn()
    out: dict = {}
    for r in c.execute("SELECT patient_id, COUNT(*) n FROM studies GROUP BY patient_id"):
        out.setdefault(r["patient_id"], {"study_count": 0, "file_count": 0, "last_study_date": None})["study_count"] = r["n"]
    for r in c.execute("SELECT patient_id, COUNT(*) n FROM files GROUP BY patient_id"):
        out.setdefault(r["patient_id"], {"study_count": 0, "file_count": 0, "last_study_date": None})["file_count"] = r["n"]
    for r in c.execute("SELECT id, patient_id, blob FROM studies"):
        sd = unseal(master, r["blob"], f"study:{r['id']}").get("study_date")
        e = out.setdefault(r["patient_id"], {"study_count": 0, "file_count": 0, "last_study_date": None})
        if sd and (not e["last_study_date"] or sd > e["last_study_date"]):
            e["last_study_date"] = sd
    return out


@app.get("/api/patients")
def list_patients(request: Request):
    master = data_access(request)
    counts = _patient_counts(master)
    rows = store.conn().execute("SELECT * FROM patients").fetchall()
    out = [_patient_out(r, master, counts) for r in rows]
    out.sort(key=lambda p: (p["name"] or "").lower())
    return out


@app.post("/api/patients")
async def create_patient(request: Request):
    master = data_access(request)
    d = _patient_fields(await body_json(request))
    pid, now = crypto.random_id(), time.time()
    store.conn().execute(
        "INSERT INTO patients(id,created_at,updated_at,blob) VALUES(?,?,?,?)", (pid, now, now, seal(master, d, f"patient:{pid}"))
    )
    return _get_patient(pid, master)


def _get_patient(pid: str, master: bytes) -> dict:
    r = store.conn().execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ApiError(404, "Patient not found")
    return _patient_out(r, master, _patient_counts(master))


@app.get("/api/patients/{pid}")
def get_patient(pid: str, request: Request):
    return _get_patient(pid, data_access(request))


@app.put("/api/patients/{pid}")
async def update_patient(pid: str, request: Request):
    master = data_access(request)
    r = store.conn().execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ApiError(404, "Patient not found")
    d = _patient_fields(await body_json(request), unseal(master, r["blob"], f"patient:{pid}"))
    store.conn().execute("UPDATE patients SET blob=?, updated_at=? WHERE id=?", (seal(master, d, f"patient:{pid}"), time.time(), pid))
    return _get_patient(pid, master)


@app.delete("/api/patients/{pid}")
def delete_patient(pid: str, request: Request):
    data_access(request)
    if not store.conn().execute("SELECT 1 FROM patients WHERE id=?", (pid,)).fetchone():
        raise ApiError(404, "Patient not found")
    ids = [f["id"] for f in store.conn().execute("SELECT id FROM files WHERE patient_id=?", (pid,)).fetchall()]
    store.conn().execute("DELETE FROM patients WHERE id=?", (pid,))
    for fid in ids:
        _remove_blobs(fid)
    return {"ok": True}


# =================================================================== studies

STUDY_KEYS = ("title", "modality", "study_date", "facility", "description", "study_uid", "accession", "body_part", "referring_physician")


def _study_out(row, master: bytes, file_count: int | None = None) -> dict:
    d = unseal(master, row["blob"], f"study:{row['id']}")
    out = {"id": row["id"], "patient_id": row["patient_id"], "created_at": iso(row["created_at"])}
    for k in STUDY_KEYS:
        out[k] = d.get(k)
    if file_count is None:
        file_count = store.conn().execute("SELECT COUNT(*) FROM files WHERE study_id=?", (row["id"],)).fetchone()[0]
    out["file_count"] = file_count
    return out


def _study_fields(j: dict, existing: dict | None = None) -> dict:
    d = dict(existing or {})
    for k in STUDY_KEYS:
        if k in j:
            d[k] = _str(j[k], 2000 if k == "description" else 200)
    if "study_date" in j:
        d["study_date"] = _valid_date(d.get("study_date"), "Study date")
    if not d.get("title"):
        parts = [d.get("modality"), d.get("description") or d.get("body_part")]
        d["title"] = " ".join(p for p in parts if p) or "Imaging study"
    return d


def _insert_study(pid: str, d: dict, master: bytes) -> str:
    sid = crypto.random_id()
    store.conn().execute(
        "INSERT INTO studies(id,patient_id,created_at,blob) VALUES(?,?,?,?)", (sid, pid, time.time(), seal(master, d, f"study:{sid}"))
    )
    return sid


@app.get("/api/patients/{pid}/studies")
def list_studies(pid: str, request: Request):
    master = data_access(request)
    _get_patient(pid, master)
    counts = {r["study_id"]: r["n"] for r in store.conn().execute("SELECT study_id, COUNT(*) n FROM files WHERE patient_id=? GROUP BY study_id", (pid,))}
    rows = store.conn().execute("SELECT * FROM studies WHERE patient_id=?", (pid,)).fetchall()
    out = [_study_out(r, master, counts.get(r["id"], 0)) for r in rows]
    out.sort(key=lambda s: (s["study_date"] or "", s["created_at"] or ""), reverse=True)
    return out


@app.post("/api/patients/{pid}/studies")
async def create_study(pid: str, request: Request):
    master = data_access(request)
    _get_patient(pid, master)
    sid = _insert_study(pid, _study_fields(await body_json(request)), master)
    return _get_study(sid, master)


@app.get("/api/studies/recent")
def recent_studies(request: Request, limit: int = 8):
    """Studies across all patients, most recently added or updated first, for the home page."""
    master = data_access(request)
    limit = max(1, min(int(limit), 50))
    c = store.conn()
    activity = {r["study_id"]: (r["n"], r["last"]) for r in c.execute("SELECT study_id, COUNT(*) n, MAX(created_at) last FROM files GROUP BY study_id")}
    rows = c.execute("SELECT * FROM studies").fetchall()
    rows.sort(key=lambda r: max(r["created_at"], (activity.get(r["id"]) or (0, 0))[1] or 0), reverse=True)
    names: dict = {}
    out = []
    for r in rows[:limit]:
        s = _study_out(r, master, (activity.get(r["id"]) or (0, 0))[0])
        pid = r["patient_id"]
        if pid not in names:
            p = c.execute("SELECT blob FROM patients WHERE id=?", (pid,)).fetchone()
            names[pid] = unseal(master, p["blob"], f"patient:{pid}").get("name") if p else None
        s["patient_name"] = names[pid]
        s["last_activity"] = iso(max(r["created_at"], (activity.get(r["id"]) or (0, 0))[1] or 0))
        files = c.execute("SELECT id, kind, has_preview FROM files WHERE study_id=? ORDER BY created_at", (r["id"],)).fetchall()
        s["thumbnail_ids"] = [f["id"] for f in files if f["has_preview"]][:3]
        s["report_count"] = sum(1 for f in files if f["kind"] == "report")
        out.append(s)
    return out


def _study_row(sid: str):
    r = store.conn().execute("SELECT * FROM studies WHERE id=?", (sid,)).fetchone()
    if not r:
        raise ApiError(404, "Study not found")
    return r


def _get_study(sid: str, master: bytes) -> dict:
    return _study_out(_study_row(sid), master)


@app.get("/api/studies/{sid}")
def get_study(sid: str, request: Request):
    return _get_study(sid, data_access(request))


@app.get("/api/studies/{sid}/similar")
def similar_studies(sid: str, request: Request):
    """The patient's other studies, most comparable first (same type, body part and wording, closest in time)."""
    master = data_access(request)
    base = _get_study(sid, master)
    c = store.conn()
    rows = c.execute("SELECT * FROM studies WHERE patient_id=? AND id<>?", (base["patient_id"], sid)).fetchall()
    files: dict = {}
    for f in c.execute("SELECT id, study_id, kind, has_preview FROM files WHERE patient_id=? ORDER BY created_at", (base["patient_id"],)):
        files.setdefault(f["study_id"], []).append(f)
    others = []
    for r in rows:
        fl = files.get(r["id"], [])
        s = _study_out(r, master, len(fl))
        s["thumbnail_ids"] = [f["id"] for f in fl if f["has_preview"]][:3]
        s["image_count"] = sum(1 for f in fl if f["kind"] != "report")
        others.append(s)
    return similar.rank(base, others)


@app.put("/api/studies/{sid}")
async def update_study(sid: str, request: Request):
    master = data_access(request)
    r = _study_row(sid)
    d = _study_fields(await body_json(request), unseal(master, r["blob"], f"study:{sid}"))
    store.conn().execute("UPDATE studies SET blob=? WHERE id=?", (seal(master, d, f"study:{sid}"), sid))
    return _get_study(sid, master)


@app.delete("/api/studies/{sid}")
def delete_study(sid: str, request: Request):
    data_access(request)
    _study_row(sid)
    ids = [f["id"] for f in store.conn().execute("SELECT id FROM files WHERE study_id=?", (sid,)).fetchall()]
    store.conn().execute("DELETE FROM studies WHERE id=?", (sid,))
    for fid in ids:
        _remove_blobs(fid)
    return {"ok": True}


# =================================================================== files

KIND_MAP = {"pdf": "report", "image": "image", "dicom": "dicom"}
SKIP_NAMES = ("DICOMDIR", "AUTORUN.INF", "LOCKFILE", "VERSION")
SKIP_EXT = (".exe", ".dll", ".ini", ".inf", ".htm", ".html", ".js", ".css", ".xml", ".jar", ".class", ".bat", ".sh", ".cmd", ".app", ".dylib", ".so", ".ico", ".chm", ".msi", ".cab")
# Folders that hold a CD's bundled viewer program, not patient images.
VIEWER_DIRS = re.compile(r"(^|/)([^/]*viewer[^/]*|program files|programs?|bin|lib|help|autorun|efilm|radiant|weasis|osirix|jre|java|runtime)/", re.I)


def _file_out(row, master: bytes) -> dict:
    d = unseal(master, row["blob"], f"file:{row['id']}")
    col = row["sha256"] or ""
    out = {
        "id": row["id"],
        "study_id": row["study_id"],
        "patient_id": row["patient_id"],
        "filename": d.get("filename") or "file",
        "content_type": d.get("content_type"),
        "size_bytes": row["size"],
        "kind": row["kind"],
        "sha256": d.get("sha256") or (None if col.startswith("h1:") else col),
        "has_thumbnail": bool(row["has_preview"]),
        "uploaded_at": iso(row["created_at"]),
    }
    if d.get("dicom"):
        out["dicom"] = d["dicom"]
    return out


def _ingest(sp: Spool, filename: str, pid: str, sid: str | None, master: bytes, kind_hint: str | None = None) -> dict:
    """Encrypt an (already encrypted-in-scratch) upload into the vault. If sid
    is None the study is chosen automatically (DICOM StudyInstanceUID, else
    'Uploads <date>')."""
    filename = clean_filename(filename)
    with sp.open() as fp:
        kind_raw, mime = media.sniff(fp, filename)
        if kind_raw == "empty":
            raise ApiError(400, f"{filename} is empty")
        kind = KIND_MAP.get(kind_raw, "other")
        if kind_hint in ("image", "report", "dicom", "other") and kind == "other":
            kind = kind_hint
        meta = media.dicom_meta(fp) if kind == "dicom" else {}
        if sid is None:
            sid = _auto_study(pid, meta, master)
        h = hashlib.sha256()
        fp.seek(0)
        while b := fp.read(1 << 20):
            h.update(b)
        digest = h.hexdigest()
        mac = crypto.file_mac(master, digest)
        dup = store.conn().execute("SELECT id FROM files WHERE study_id=? AND sha256=?", (sid, mac)).fetchone()
        if dup:
            return {"duplicate": True, **_file_out(store.conn().execute("SELECT * FROM files WHERE id=?", (dup["id"],)).fetchone(), master)}
        fid = crypto.random_id() + crypto.random_id()
        bp = store.blob_path(fid)
        bp.parent.mkdir(parents=True, exist_ok=True)
        fp.seek(0)
        with open(bp, "wb") as dst:
            size = crypto.encrypt_stream(master, fp, dst, f"file:{fid}".encode())
        preview = media.render_preview(fp, kind_raw)
    if preview:
        pp = store.preview_path(fid)
        pp.parent.mkdir(parents=True, exist_ok=True)
        with open(pp, "wb") as dst:
            crypto.encrypt_stream(master, io.BytesIO(preview), dst, f"preview:{fid}".encode())
    blob = {"filename": filename, "content_type": mime, "sha256": digest}
    if meta:
        blob["dicom"] = {k: v for k, v in meta.items() if v}
    store.conn().execute(
        "INSERT INTO files(id,study_id,patient_id,kind,size,sha256,has_preview,created_at,blob) VALUES(?,?,?,?,?,?,?,?,?)",
        (fid, sid, pid, kind, size, mac, 1 if preview else 0, time.time(), seal(master, blob, f"file:{fid}")),
    )
    return _file_out(store.conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone(), master)


def _iso_or_none(v: str | None) -> str | None:
    return v if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None


def _auto_study(pid: str, meta: dict, master: bytes) -> str:
    rows = store.conn().execute("SELECT * FROM studies WHERE patient_id=?", (pid,)).fetchall()
    if meta.get("study_uid"):
        for r in rows:
            if unseal(master, r["blob"], f"study:{r['id']}").get("study_uid") == meta["study_uid"]:
                return r["id"]
        return _insert_study(
            pid,
            _study_fields(
                {
                    "modality": meta.get("modality"),
                    "study_date": _iso_or_none(meta.get("study_date")),
                    "facility": meta.get("institution"),
                    "description": meta.get("study_description"),
                    "study_uid": meta.get("study_uid"),
                    "accession": meta.get("accession"),
                    "body_part": meta.get("body_part"),
                    "referring_physician": meta.get("referring_physician"),
                    "title": " ".join(x for x in (meta.get("modality"), meta.get("study_description") or meta.get("body_part")) if x) or None,
                }
            ),
            master,
        )
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"Uploads {today}"
    for r in rows:
        d = unseal(master, r["blob"], f"study:{r['id']}")
        if d.get("title") == title and not d.get("study_uid"):
            return r["id"]
    # Loose uploads get no study date, so they don't pose as the patient's
    # most recent imaging.
    return _insert_study(pid, {"title": title, "uploaded": today}, master)


def _record(res: dict, f: dict) -> None:
    res["files"].append(f)
    if f["study_id"] not in res["_sids"]:
        res["_sids"].append(f["study_id"])


def _expand(sp: Spool, filename: str, skipped: list, budget: dict, want=lambda key: True, key: tuple = (), depth: int = 0):
    """Flatten an upload into a stream of (key, spool, name, owned) items. Zips
    (one level of nesting) are expanded with per-member and total size limits.
    Members are extracted one at a time and the caller closes each owned spool
    before asking for the next, so a CD export with thousands of images needs
    only a couple of open files. ``want(key)`` is asked before a member is
    extracted, so a second pass can skip what the first already handled."""
    with sp.open() as fp:
        kind_raw, _ = media.sniff(fp, filename)
    if kind_raw != "zip":
        if want(key):
            yield key, sp, filename, False
        return
    try:
        z = zipfile.ZipFile(sp.open())
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ApiError(400, f"{clean_filename(filename)} is not a valid ZIP file (damaged or incomplete download?)")
    with z:
        members = [m for m in z.infolist() if not m.is_dir()]
        if len(members) > 20000:
            raise ApiError(400, "ZIP has too many entries")
        for idx, m in enumerate(members):
            mkey = key + (idx,)
            path = m.filename.replace("\\", "/")
            base = clean_filename(path, "")
            if (
                not base
                or base.startswith(".")
                or "__MACOSX" in path
                or base.upper() in SKIP_NAMES
                or base.lower().endswith(SKIP_EXT)
                or VIEWER_DIRS.search("/" + path)
            ):
                skipped.append(m.filename)
                continue
            if m.flag_bits & 0x1:
                skipped.append(m.filename + " (password-protected)")
                continue
            if m.file_size > MAX_UPLOAD:
                raise ApiError(413, f"{base} inside the ZIP is larger than the upload limit")
            if not want(mkey):  # also true for a nested zip holding something wanted
                continue
            member = Spool(store.tmp_dir, limit=min(MAX_UPLOAD, max(0, MAX_ZIP_TOTAL - budget["used"])) + 1)
            owned = True
            try:
                try:
                    with z.open(m) as src:
                        while chunk := src.read(1 << 20):
                            member.write(chunk)
                            if member.size > MAX_UPLOAD:
                                raise SpoolFull(MAX_UPLOAD)
                    member.finish()
                except SpoolFull:
                    raise ApiError(413, "The ZIP expands to more data than allowed (zip bomb protection)")
                except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error) as exc:
                    skipped.append(f"{m.filename} (unreadable: {exc.__class__.__name__})")
                    continue
                budget["used"] += member.size
                if budget["used"] > MAX_ZIP_TOTAL:
                    raise ApiError(413, "The ZIP expands to more data than allowed (zip bomb protection)")
                with member.open() as fp:
                    k, _ = media.sniff(fp, base)
                if k == "zip" and depth == 0:
                    yield from _expand(member, base, skipped, budget, want, mkey, depth + 1)
                    continue
                if k in ("other", "zip", "empty"):
                    skipped.append(m.filename)
                    continue
                owned = False  # handed to the caller, who closes it
                yield mkey, member, base, True
            finally:
                if owned:
                    member.close()


def _ingest_all(parts: list[UploadPart], pid: str, sid: str | None, master: bytes, kind: str | None) -> dict:
    """DICOM (also from CD/zip exports) first; if it all belongs to one study,
    the reports and photos uploaded alongside join that study.

    Two passes over the upload: the first ingests imaging as each member is
    extracted and notes where everything else is, the second extracts and
    ingests only those other files."""
    res: dict = {"files": [], "skipped": [], "_sids": []}
    loose: set = set()
    loose_prefixes: set = set()
    budget = {"used": 0}
    for i, p in enumerate(parts):
        for key, sp, name, owned in _expand(p.spool, p.filename or "file", res["skipped"], budget, key=(i,)):
            try:
                with sp.open() as fp:
                    k = media.sniff(fp, name)[0]
                if k == "dicom":
                    _record(res, _ingest(sp, name, pid, sid, master, kind))
                else:
                    loose.add(key)
                    for n in range(1, len(key)):
                        loose_prefixes.add(key[:n])
            finally:
                if owned:
                    sp.close()
    if not loose:
        return res
    target = sid or (res["_sids"][0] if len(res["_sids"]) == 1 else None)
    want = lambda key: key in loose or key in loose_prefixes  # noqa: E731
    again = {"used": 0}
    for i, p in enumerate(parts):
        if (i,) not in loose and (i,) not in loose_prefixes:
            continue
        for key, sp, name, owned in _expand(p.spool, p.filename or "file", [], again, want, key=(i,)):
            try:
                _record(res, _ingest(sp, name, pid, target, master, kind))
            except ApiError as exc:
                if exc.status != 400:
                    raise
                res["skipped"].append(f"{name} ({exc.msg})")
            finally:
                if owned:
                    sp.close()
    return res


async def _upload_form(request: Request) -> UploadForm:
    return await parse_form(request, store.tmp_dir, MAX_UPLOAD)


@app.post("/api/studies/{sid}/files")
async def upload_files(sid: str, request: Request):
    master = data_access(request)
    st = _study_row(sid)
    form = await _upload_form(request)
    try:
        parts = form.files_named("file")
        if not parts:
            raise ApiError(400, "Choose at least one file")
        if len(parts) == 1 and parts[0].size == 0:
            raise ApiError(400, f"{clean_filename(parts[0].filename)} is empty")
        res = await asyncio.to_thread(_ingest_all, parts, st["patient_id"], sid, master, form.get("kind"))
    finally:
        form.close()
    res.pop("_sids")
    return res["files"] if request.query_params.get("format") != "full" else res


@app.post("/api/patients/{pid}/import")
async def import_files(pid: str, request: Request):
    master = data_access(request)
    _get_patient(pid, master)
    form = await _upload_form(request)
    try:
        parts = form.files_named("file")
        if not parts:
            raise ApiError(400, "Choose at least one file")
        if len(parts) == 1 and parts[0].size == 0:
            raise ApiError(400, f"{clean_filename(parts[0].filename)} is empty")
        res = await asyncio.to_thread(_ingest_all, parts, pid, None, master, None)
    finally:
        form.close()
    res["studies"] = [_get_study(s, master) for s in res.pop("_sids")]
    return res


@app.get("/api/studies/{sid}/files")
def list_files(sid: str, request: Request):
    master = data_access(request)
    _study_row(sid)
    rows = store.conn().execute("SELECT * FROM files WHERE study_id=? ORDER BY created_at", (sid,)).fetchall()
    out = [_file_out(r, master) for r in rows]

    def num(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    def key(f):
        d = f.get("dicom") or {}
        return (
            {"report": 0, "dicom": 2}.get(f["kind"], 1),
            num(d.get("series_number")),
            d.get("series_uid") or "",
            num(d.get("instance_number")),
            f["filename"] or "",
        )

    out.sort(key=key)
    return out


def _file_row(fid: str):
    r = store.conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if not r:
        raise ApiError(404, "File not found")
    return r


@app.get("/api/files/{fid}")
def file_meta(fid: str, request: Request):
    master = data_access(request)
    return _file_out(_file_row(fid), master)


def _stream(path: Path, master: bytes, aad: bytes):
    def gen():
        with open(path, "rb") as f:
            yield from crypto.decrypt_stream_iter(master, f, aad)

    return gen()


@app.get("/api/files/{fid}/content")
def file_content(fid: str, request: Request, download: int = 0):
    master = data_access(request)
    row = _file_row(fid)
    f = _file_out(row, master)
    ctype = f["content_type"] or "application/octet-stream"
    inline_ok = ctype.startswith("image/") and ctype != "image/svg+xml" or ctype == "application/pdf"
    if not inline_ok:
        download = 1
        if ctype.startswith("text/") or "xml" in ctype or "html" in ctype or "javascript" in ctype:
            ctype = "application/octet-stream"
    disp = "attachment" if download else "inline"
    safe = clean_filename(f["filename"]).replace('"', "")
    ascii_name = _header_safe(safe.encode("ascii", "replace").decode()) or "file"
    headers = {
        "Content-Disposition": f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe)}",
        "Content-Length": str(row["size"]),
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; object-src 'self'",
        "Cache-Control": "private, no-store",
    }
    return StreamingResponse(_stream(store.blob_path(fid), master, f"file:{fid}".encode()), media_type=ctype, headers=headers)


@app.get("/api/files/{fid}/thumbnail")
def file_thumb(fid: str, request: Request):
    master = data_access(request)
    row = _file_row(fid)
    if not row["has_preview"]:
        raise ApiError(404, "No thumbnail for this file")
    return StreamingResponse(_stream(store.preview_path(fid), master, f"preview:{fid}".encode()), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


_pix_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_pix_lock = threading.Lock()
PIX_CACHE_BYTES = _env_int("SCANVAULT_RENDER_CACHE_MB", 256) * 1024 * 1024


def _decoded(fid: str, master: bytes, frame: int | None, nframes_hint: int):
    """Decrypt + decode one DICOM frame, keeping a few recent ones in memory.
    The decrypted file only ever exists in an encrypted scratch spool."""
    if frame is not None:
        frame = max(0, min(max(nframes_hint, 1) - 1, frame))
        if nframes_hint <= 1:
            frame = None
    key = (fid, frame)
    with _pix_lock:
        if key in _pix_cache:
            _pix_cache.move_to_end(key)
            return _pix_cache[key]
    with Spool(store.tmp_dir) as sp:
        with open(store.blob_path(fid), "rb") as src:
            for chunk in crypto.decrypt_stream_iter(master, src, f"file:{fid}".encode()):
                sp.write(chunk)
        with sp.open() as fp:
            val = media.dicom_pixels(fp, frame)
    if not vault.unlocked:
        return val
    with _pix_lock:
        _pix_cache[key] = val
        while sum(v[0].nbytes for v in _pix_cache.values()) > PIX_CACHE_BYTES and len(_pix_cache) > 1:
            _pix_cache.popitem(last=False)
    return val


@app.get("/api/files/{fid}/render")
async def file_render(fid: str, request: Request, wc: str | None = None, ww: str | None = None, frame: int | None = None, invert: int = 0, size: int = 2048):
    """Render a DICOM image with a window center/width in modality units (HU for CT).
    Response headers report the window used, the file's default and its value range."""
    master = data_access(request)
    row = _file_row(fid)
    if row["kind"] != "dicom":
        raise ApiError(400, "Only DICOM files can be rendered with window/level")

    def num(v, what):
        if v is None or v == "":
            return None
        try:
            x = float(v)
        except ValueError:
            raise ApiError(400, f"{what} must be a number")
        if not math.isfinite(x) or abs(x) > 1e7:
            raise ApiError(400, f"{what} must be a finite number")
        return x

    wcv, wwv = num(wc, "wc"), num(ww, "ww")
    try:
        nframes = int((_file_out(row, master).get("dicom") or {}).get("frames") or 1)
    except ValueError:
        nframes = 1
    try:
        arr, info = await asyncio.to_thread(_decoded, fid, master, frame, nframes)
        jpg, c_used, w_used = await asyncio.to_thread(media.render_window, arr, info, wcv, wwv, bool(invert), max(256, min(4096, size)))
    except Exception as exc:
        log.info("Render failed for %s: %s", fid, exc)
        raise ApiError(422, "This DICOM's pixel data can't be rendered here (unsupported compression or too large). Download the original for a full DICOM viewer.")
    h = {"Cache-Control": "private, no-store", "X-Frames": str(info.get("frames", 1)), "X-Frame": str(info.get("frame", 0)), "X-Rgb": "1" if info.get("rgb") else "0"}
    if not info.get("rgb"):
        h.update({
            "X-Window-Center": str(round(c_used, 4)),
            "X-Window-Width": str(round(w_used, 4)),
            "X-Default-Center": str(info["default_center"]),
            "X-Default-Width": str(info["default_width"]),
            "X-Min": str(info["min"]),
            "X-Max": str(info["max"]),
            "X-Modality": _header_safe(info.get("modality", "")),
        })
    h["Access-Control-Expose-Headers"] = ", ".join(h)
    return Response(jpg, media_type="image/jpeg", headers=h)


# =================================================================== per-user preferences

PREF_KEYS = {"theme": ("system", "light", "dark")}


@app.get("/api/prefs")
def get_prefs(request: Request):
    u = require_user(request)
    return {"theme": "system", "dark_color": "#0d1416", **store.get_config(f"prefs:{u['id']}", {})}


@app.put("/api/prefs")
async def put_prefs(request: Request):
    u = require_user(request)
    j = await body_json(request)
    cur = store.get_config(f"prefs:{u['id']}", {})
    if "theme" in j:
        if j["theme"] not in PREF_KEYS["theme"]:
            raise ApiError(400, "theme must be system, light or dark")
        cur["theme"] = j["theme"]
    if "dark_color" in j:
        import re

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(j["dark_color"])):
            raise ApiError(400, "dark_color must look like #0d1416")
        cur["dark_color"] = str(j["dark_color"]).lower()
    store.set_config(f"prefs:{u['id']}", cur)
    return {"theme": "system", "dark_color": "#0d1416", **cur}


@app.put("/api/files/{fid}")
async def update_file(fid: str, request: Request):
    master = data_access(request)
    row = _file_row(fid)
    j = await body_json(request)
    if "study_id" in j:
        st = _study_row(str(j["study_id"]))
        if st["patient_id"] != row["patient_id"]:
            raise ApiError(400, "Can only move a file to a study of the same patient")
        store.conn().execute("UPDATE files SET study_id=? WHERE id=?", (st["id"], fid))
    if j.get("filename"):
        d = unseal(master, row["blob"], f"file:{fid}")
        d["filename"] = clean_filename(jstr(j, "filename"))
        store.conn().execute("UPDATE files SET blob=? WHERE id=?", (seal(master, d, f"file:{fid}"), fid))
    return _file_out(_file_row(fid), master)


def _remove_blobs(fid: str) -> None:
    store.remove_blobs(fid)


@app.delete("/api/files/{fid}")
def delete_file(fid: str, request: Request):
    data_access(request)
    _file_row(fid)
    store.conn().execute("DELETE FROM files WHERE id=?", (fid,))
    _remove_blobs(fid)
    return {"ok": True}


def _counts() -> dict:
    c = store.conn()
    return {
        "patients": c.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "studies": c.execute("SELECT COUNT(*) FROM studies").fetchone()[0],
        "files": c.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        "bytes": c.execute("SELECT COALESCE(SUM(size),0) FROM files").fetchone()[0],
    }


@app.get("/api/stats")
def stats(request: Request):
    data_access(request)
    last = store.conn().execute("SELECT MAX(finished_at) FROM backup_runs WHERE status='ok'").fetchone()[0]
    return {**_counts(), "last_backup_at": iso(last)}


# =================================================================== backups

def _target_row(tid: str):
    r = store.conn().execute("SELECT * FROM backup_targets WHERE id=?", (tid,)).fetchone()
    if not r:
        raise ApiError(404, "Backup target not found")
    return r


def _target_config(row, master: bytes) -> dict:
    return unseal(master, row["config"], f"target:{row['id']}")


def _save_target_config(tid: str, master: bytes, base: dict | None = None):
    """Persist config changes a target makes itself (rotated tokens, a pinned
    host key). Only the keys the target changed are written, on top of the
    *current* stored config, so edits the user saves while a backup is running
    are not overwritten with the stale copy the run started with."""
    seen = dict(base or {})

    def save(cfg: dict) -> None:
        with store.lock:
            row = store.conn().execute("SELECT config FROM backup_targets WHERE id=?", (tid,)).fetchone()
            if not row:
                return
            cur = unseal(master, row["config"], f"target:{tid}")
            for k, v in cfg.items():
                if seen.get(k) != v:
                    cur[k] = v
            seen.clear()
            seen.update(cfg)
            store.conn().execute("UPDATE backup_targets SET config=? WHERE id=?", (seal(master, cur, f"target:{tid}"), tid))

    return save


def _make_target(row, master: bytes) -> targets.Target:
    cfg = _target_config(row, master)
    ks = store.keystore() or {}
    return targets.make(row["type"], cfg, _save_target_config(row["id"], master, cfg), vault_id=ks.get("vault_id", ""))


def _target_out(row, master: bytes) -> dict:
    cfg = _target_config(row, master)
    cls = targets.TYPES[row["type"]]
    public = {k: v for k, v in cfg.items() if k not in cls.secret_fields and not k.startswith("_")}
    secrets_set = {k: bool(cfg.get(k)) for k in cls.secret_fields}
    connected = not targets.needs_connect(row["type"], cfg)
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "schedule_hours": row["schedule_hours"],
        "keep_last": row["keep_last"],
        "config": public,
        "secrets_set": secrets_set,
        "connected": connected,
        "last_run": iso(row["last_run"]),
        "last_status": row["last_status"],
    }


NO_STRIP = ("private_key", "service_account_json", "password", "secret_access_key", "client_secret", "app_secret", "key_passphrase", "passphrase")


def _merge_config(type_: str, old: dict, new: dict) -> dict:
    cls = targets.TYPES[type_]
    cfg = dict(old)
    if not isinstance(new, dict):
        raise ApiError(400, "config must be an object")
    for k, v in new.items():
        if k.startswith("_"):
            continue
        if k in cls.secret_fields:
            if v == "":
                continue  # blank = keep the existing secret
            if v is None:
                cfg.pop(k, None)  # null = remove it
                continue
        cfg[k] = v.strip() if isinstance(v, str) and k not in NO_STRIP else v
    def same(k):
        a, b = old.get(k), cfg.get(k)
        if k == "port":
            a, b = str(a or ""), str(b or "")
            a, b = a if a not in ("", "22") else "", b if b not in ("", "22") else ""
        return (a or "") == (b or "")

    if any(old.get(k) for k in cls.location_fields) and not all(same(k) for k in cls.location_fields):
        for k in cls.secret_fields:
            if new.get(k) in (None, ""):
                cfg.pop(k, None)  # new server: secrets must be entered again
        if type_ == "webdav" and new.get("tls_fingerprint") in (None, old.get("tls_fingerprint")):
            cfg.pop("tls_fingerprint", None)
    if type_ == "sftp":
        def port(x):
            try:
                return int(x or 22)
            except (TypeError, ValueError):
                return x

        if old.get("host") and (cfg.get("host") != old.get("host") or port(cfg.get("port")) != port(old.get("port"))):
            if new.get("host_key") in (None, old.get("host_key")):
                cfg["host_key"] = ""  # host changed: re-pin on the next Test
    try:
        targets.TYPES[type_].validate(cfg)
    except targets.TargetError as exc:
        raise ApiError(400, str(exc))
    return cfg


TARGET_NAMES = {"gdrive": "Google Drive", "box": "Box", "dropbox": "Dropbox", "onedrive": "OneDrive", "s3": "S3 storage", "webdav": "WebDAV", "sftp": "SFTP server", "smb": "Network share", "local": "USB / local disk"}


def _int(v, default: int, lo: int = 0, hi: int = 100000) -> int:
    try:
        return max(lo, min(hi, int(v if v is not None else default)))
    except (TypeError, ValueError):
        raise ApiError(400, "Expected a whole number")


@app.get("/api/backups/targets")
def list_targets(request: Request):
    master = data_access(request)
    return [_target_out(r, master) for r in store.conn().execute("SELECT * FROM backup_targets ORDER BY created_at")]


@app.post("/api/backups/targets")
async def create_target(request: Request):
    master = data_access(request)
    j = await body_json(request)
    t = j.get("type")
    if t not in targets.TYPES:
        raise ApiError(400, "type must be one of " + ", ".join(targets.TYPES))
    tid = crypto.random_id()
    cfg = _merge_config(t, {}, j.get("config") or {})
    store.conn().execute(
        "INSERT INTO backup_targets(id,type,name,enabled,schedule_hours,keep_last,config,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            tid,
            t,
            _str(j.get("name"), 100) or TARGET_NAMES[t],
            1 if j.get("enabled", True) else 0,
            _int(j.get("schedule_hours"), 24),
            _int(j.get("keep_last"), 14),
            seal(master, cfg, f"target:{tid}"),
            time.time(),
        ),
    )
    return _target_out(_target_row(tid), master)


@app.put("/api/backups/targets/{tid}")
async def update_target(tid: str, request: Request):
    master = data_access(request)
    row = _target_row(tid)
    j = await body_json(request)
    with store.lock:
        row = _target_row(tid)
        cfg = _merge_config(row["type"], _target_config(row, master), j.get("config") or {})
        store.conn().execute(
            "UPDATE backup_targets SET name=?, enabled=?, schedule_hours=?, keep_last=?, config=? WHERE id=?",
            (
                _str(j.get("name"), 100) or row["name"],
                (1 if j.get("enabled") else 0) if "enabled" in j else row["enabled"],
                _int(j.get("schedule_hours", row["schedule_hours"]), 24),
                _int(j.get("keep_last", row["keep_last"]), 14),
                seal(master, cfg, f"target:{tid}"),
                tid,
            ),
        )
    return _target_out(_target_row(tid), master)


@app.delete("/api/backups/targets/{tid}")
def delete_target(tid: str, request: Request):
    data_access(request)
    _target_row(tid)
    store.conn().execute("DELETE FROM backup_targets WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/backups/targets/{tid}/test")
async def test_target(tid: str, request: Request):
    master = data_access(request)
    tgt = _make_target(_target_row(tid), master)
    msg = await asyncio.to_thread(tgt.test)
    return {"ok": True, "message": msg, "target": _target_out(_target_row(tid), master)}


def _errmsg(exc: Exception) -> str:
    m = str(exc).strip()
    return (m or exc.__class__.__name__)[:500]


def _tmp_name(name: str) -> Path:
    return store.tmp_dir / f"{secrets.token_hex(6)}-{name}"


def run_backup(tid: str | None, master: bytes, trigger: str = "manual") -> dict:
    """Build an encrypted archive and push it to one target (or all enabled)."""
    with backup_lock:
        rows = [_target_row(tid)] if tid else store.conn().execute("SELECT * FROM backup_targets WHERE enabled=1").fetchall()
        if not rows:
            raise ApiError(400, "No backup targets configured")
        vid = store.keystore()["vault_id"]
        name = backup.backup_name(vid)
        archive = _tmp_name(name)
        results = []
        try:
            backup.create_archive(store, master, archive, VERSION, bool(store.get_config("backup_include_wrapped_key", False)))
            size = archive.stat().st_size
            for row in rows:
                row = _target_row(row["id"])  # fresh copy: may have been edited meanwhile
                rid, started = crypto.random_id(), time.time()
                store.conn().execute(
                    "INSERT INTO backup_runs(id,target_id,started_at,status,filename,size) VALUES(?,?,?,?,?,?)",
                    (rid, row["id"], started, "running", name, size),
                )
                try:
                    tgt = _make_target(row, master)
                    tgt.upload(archive, name)
                    pruned = tgt.prune(row["keep_last"])
                    status, msg = "ok", f"Uploaded {name} ({size:,} bytes)" + (f", removed {pruned} old backup(s)" if pruned else "")
                except Exception as exc:
                    log.exception("Backup to %s failed", row["name"])
                    status, msg = "error", _errmsg(exc)
                now = time.time()
                store.conn().execute("UPDATE backup_runs SET finished_at=?, status=?, message=? WHERE id=?", (now, status, msg, rid))
                store.conn().execute("UPDATE backup_targets SET last_run=?, last_status=? WHERE id=?", (now, status, row["id"]))
                results.append({"target_id": row["id"], "name": row["name"], "status": status, "message": msg})
        finally:
            archive.unlink(missing_ok=True)
        return {"filename": name, "results": results, "trigger": trigger}


@app.post("/api/backups/targets/{tid}/run")
async def run_target(tid: str, request: Request):
    master = data_access(request)
    _target_row(tid)
    return await asyncio.to_thread(run_backup, tid, master)


@app.post("/api/backups/run")
async def run_all(request: Request):
    master = data_access(request)
    return await asyncio.to_thread(run_backup, None, master)


@app.get("/api/backups/runs")
def list_runs(request: Request, limit: int = 50):
    data_access(request)
    rows = store.conn().execute(
        "SELECT r.*, t.name AS target_name, t.type AS target_type FROM backup_runs r LEFT JOIN backup_targets t ON t.id=r.target_id ORDER BY started_at DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "target_id": r["target_id"],
            "target_name": r["target_name"] or ("Local download" if not r["target_id"] else "(deleted)"),
            "target_type": r["target_type"],
            "started_at": iso(r["started_at"]),
            "finished_at": iso(r["finished_at"]),
            "status": r["status"],
            "message": r["message"],
            "size": r["size"],
            "filename": r["filename"],
        }
        for r in rows
    ]


@app.get("/api/backups/download")
async def download_backup(request: Request):
    """Build an encrypted .svb archive and download it directly."""
    master = data_access(request)
    name = backup.backup_name(store.keystore()["vault_id"])
    path = _tmp_name(name)
    started = time.time()
    try:
        with key_gate.held():  # a key change waits for this archive to be written
            await asyncio.to_thread(backup.create_archive, store, master, path, VERSION, bool(store.get_config("backup_include_wrapped_key", False)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    size = path.stat().st_size

    def done():
        path.unlink(missing_ok=True)
        store.conn().execute(
            "INSERT INTO backup_runs(id,target_id,started_at,finished_at,status,message,size,filename) VALUES(?,?,?,?,?,?,?,?)",
            (crypto.random_id(), None, started, time.time(), "ok", "Downloaded to device", size, name),
        )

    return FileResponse(path, filename=name, media_type="application/octet-stream", background=BackgroundTask(done))


@app.get("/api/backups/targets/{tid}/remote")
async def list_remote(tid: str, request: Request):
    master = data_access(request)
    gate_request(request)  # listing may save refreshed tokens
    tgt = _make_target(_target_row(tid), master)
    items = await asyncio.to_thread(tgt.list_backups)
    return [{"name": n, "handle": h} for n, h in reversed(items)]


@app.post("/api/backups/restore")
async def restore_existing(request: Request):
    """Restore over the current vault from an uploaded .svb or a remote backup.
    Uses the current master key; the backup must belong to this vault.
    Patients, studies and files are replaced; logins, sessions, the current
    encryption key, settings, destinations and history are kept."""
    master = data_access(request)
    form = await parse_form(request, store.tmp_dir, MAX_UPLOAD + 1024 * 1024)
    archive = store.tmp_dir / f"restore-{crypto.random_id()}.svb"
    try:
        if form.get("confirm") != "REPLACE":
            raise ApiError(400, "Type REPLACE to confirm restoring over current data")
        bf = form.file("backup_file")
        target_id, handle = form.get("target_id"), form.get("handle")
        if bf is not None:
            with bf.spool.open() as src, open(archive, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
        elif target_id and handle:
            tgt = _make_target(_target_row(target_id), master)
            await asyncio.to_thread(tgt.download, handle, archive)
        else:
            raise ApiError(400, "Upload a backup file or choose a remote backup")
        hdr = backup.read_header(archive)
        if hdr["fingerprint"] != crypto.fingerprint(master):
            raise ApiError(400, "This backup was made with a different encryption key. Use a fresh install and the recovery key instead.")
        header = await asyncio.to_thread(backup.restore_into_store, store, archive, master, True)
        await asyncio.to_thread(migrate_file_macs, master)
        _clear_caches()
    finally:
        form.close()
        archive.unlink(missing_ok=True)
    return {"ok": True, "restored": header["created"], **_counts()}


# =================================================================== OAuth (Drive/Box/Dropbox/OneDrive)

_oauth_states: dict[str, tuple[str, float]] = {}
OAUTH_STATE_TTL = 900


def _redirect_uri(request: Request, provider: str) -> str:
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/api/oauth/{provider}/callback"


@app.get("/api/oauth/{provider}/start")
def oauth_start(provider: str, target_id: str, request: Request):
    master = data_access(request)
    row = _target_row(target_id)
    if row["type"] != provider or provider not in targets.OAUTH_TYPES:
        raise ApiError(400, "Provider mismatch")
    cfg = _target_config(row, master)
    if not targets.oauth_client_id(provider, cfg):
        raise ApiError(400, "Enter the app's client ID and secret first, then Save")
    now = time.time()
    for k in [k for k, (_, t) in _oauth_states.items() if now - t > OAUTH_STATE_TTL]:
        _oauth_states.pop(k, None)
    if len(_oauth_states) > 100:
        _oauth_states.clear()
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = (target_id, now)
    return RedirectResponse(targets.oauth_auth_url(provider, cfg, _redirect_uri(request, provider), state))


@app.get("/api/oauth/{provider}/redirect-uri")
def oauth_redirect_uri(provider: str, request: Request):
    require_user(request)
    return {"redirect_uri": _redirect_uri(request, provider)}


@app.get("/api/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request, state: str = "", code: str = "", error: str = ""):
    entry = _oauth_states.pop(state, None)
    if error:
        log.info("OAuth sign-in for %s returned an error: %r", _header_safe(provider), _header_safe(error)[:80])
        return RedirectResponse("/#/backups?oauth=error&msg=denied")
    if not entry or time.time() - entry[1] > OAUTH_STATE_TTL:
        return RedirectResponse("/#/backups?oauth=error&msg=expired")
    if not vault.unlocked or _state["maintenance"]:
        return RedirectResponse("/#/backups?oauth=error&msg=locked")
    try:
        gate_request(request)  # the tokens are saved sealed with the master key
    except ApiError:
        return RedirectResponse("/#/backups?oauth=error&msg=locked")
    master = vault.master_key
    tid = entry[0]
    try:
        row = _target_row(tid)
    except ApiError:
        return RedirectResponse("/#/backups?oauth=error&msg=expired")
    if row["type"] != provider:
        return RedirectResponse("/#/backups?oauth=error&msg=expired")
    tgt = _make_target(row, master)
    try:
        tgt.exchange_code(code, _redirect_uri(request, provider))  # type: ignore[attr-defined]
    except (targets.TargetError, requests.RequestException, ValueError) as exc:
        log.warning("OAuth exchange failed: %s", exc.__class__.__name__ if not isinstance(exc, targets.TargetError) else exc)
        return RedirectResponse("/#/backups?oauth=error&msg=exchange")
    return RedirectResponse("/#/backups?oauth=ok")


# =================================================================== single sign-on (OIDC)
# SSO only proves who is signing in. It never touches the vault encryption key:
# a locked vault still has to be unlocked with the encryption key or recovery
# key file afterwards, exactly like after a password sign-in.

oidc = sso.OIDC()
_sso_requests = sso.PendingStore(ttl=600)   # state -> pending authorization request
_sso_tickets = sso.PendingStore(ttl=120)    # one-time ticket -> signed-in user
SSO_COOKIE = "sv_sso"
SSO_COOKIE_PATH = "/api/auth/sso"
FORCE_LOCAL_LOGIN = os.environ.get("SCANVAULT_FORCE_LOCAL_LOGIN", "").lower() in ("1", "true", "yes")


def _sso_cfg() -> dict:
    return {"providers": [], "local_login": True, **(store.get_config("sso", {}) or {})}


def _sso_save(cfg: dict) -> None:
    store.set_config("sso", {"providers": cfg.get("providers", []), "local_login": bool(cfg.get("local_login", True))})


def _sso_providers(include_disabled: bool = False) -> list[dict]:
    ps = []
    env = sso.env_provider()
    if env:
        ps.append(env)
    ps += _sso_cfg()["providers"]
    return [p for p in ps if include_disabled or p.get("enabled", True)]


def _sso_provider(pid: str, include_disabled: bool = False) -> dict:
    for p in _sso_providers(include_disabled):
        if p["id"] == pid:
            return p
    raise ApiError(404, "Unknown sign-in provider")


def _sso_public(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "preset": p.get("preset", "generic")}


def _sso_admin_out(p: dict) -> dict:
    return {
        "id": p["id"], "name": p["name"], "preset": p.get("preset", "generic"), "issuer": p.get("issuer", ""),
        "fields": p.get("fields") or {}, "resolved_issuer": sso.resolve_issuer(p), "client_id": p.get("client_id", ""),
        "has_secret": bool(p.get("client_secret")), "scopes": p.get("scopes") or sso.DEFAULT_SCOPES,
        "enabled": p.get("enabled", True), "managed": p.get("managed"),
    }


def _linked_identity_count() -> int:
    ids = {p["id"] for p in _sso_providers()}
    rows = store.conn().execute("SELECT provider_id FROM user_identities").fetchall()
    return sum(1 for r in rows if r["provider_id"] in ids)


def local_login_enabled() -> bool:
    """Password sign-in can be turned off only while some SSO link can still sign in."""
    if FORCE_LOCAL_LOGIN or _sso_cfg()["local_login"]:
        return True
    return _linked_identity_count() == 0


def _require_admin(request: Request) -> dict:
    u = require_user(request)
    if u["role"] != "admin":
        raise ApiError(403, "Only an administrator can change sign-in settings")
    return u


def _sso_redirect_uri(request: Request) -> str:
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/api/auth/sso/callback"


REAUTH_SECONDS = 600


def _recent_sso(u: dict) -> bool:
    return bool(u.get("identity_id")) and time.time() - (u.get("session_created") or 0) < REAUTH_SECONDS


async def _reauth(u: dict, j: dict) -> None:
    """Changes that add a way to sign in need the password, or an SSO sign-in in
    the last 10 minutes, so a stolen session can't plant a backdoor. Wrong
    passwords count against the same limits as the sign-in form."""
    if _recent_sso(u):
        return
    pw = j.get("password")
    if not isinstance(pw, str) or not pw:
        raise ApiError(403, "Enter your ScanVault password to confirm this change", "reauth_required")
    key = "reauth:" + u["id"]
    login_throttle.attempt(key)
    r = store.conn().execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if not r or not await asyncio.to_thread(crypto.verify_password, pw, r["password_hash"]):
        raise ApiError(403, "Enter your ScanVault password to confirm this change", "reauth_required")
    login_throttle.success(key)


def _revoke_identity_sessions(where: str, args: tuple) -> None:
    store.conn().execute(f"DELETE FROM sessions WHERE identity_id IN (SELECT id FROM user_identities WHERE {where})", args)


_start_hits: dict[str, list[float]] = {}
START_LIMIT = (30, 600)  # sign-in starts per client per 10 minutes


def _start_rate_ok(request: Request) -> bool:
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _start_hits.get(ip, []) if now - t < START_LIMIT[1]]
    if len(_start_hits) > 10000:
        _start_hits.clear()
    ok = len(hits) < START_LIMIT[0]
    if ok:
        hits.append(now)
    _start_hits[ip] = hits
    return ok


def _sso_flow(request: Request, p: dict, mode: str, user_id: str | None = None, app: dict | None = None) -> tuple[str, str | None]:
    """Start an authorization request. Returns (provider URL, browser binding cookie)."""
    verifier, challenge = sso.pkce_pair()
    nonce = secrets.token_urlsafe(24)
    binding = None if app else secrets.token_urlsafe(24)
    redirect_uri = _sso_redirect_uri(request)
    state = _sso_requests.put({
        "mode": mode, "pid": p["id"], "user_id": user_id, "verifier": verifier, "nonce": nonce,
        "binding": sso.sha(binding) if binding else None, "app": app, "redirect_uri": redirect_uri,
    })
    try:
        # Apps: make the provider show which account is used instead of silently reusing a session.
        prompt = "select_account" if app and p.get("preset") in ("google", "microsoft") else None
        url = oidc.authorization_url(p, redirect_uri, state, nonce, challenge, prompt=prompt)
    except sso.SSOError as exc:
        _sso_requests.pop(state)
        raise ApiError(502, str(exc), "sso_provider") from exc
    return url, binding


def _set_binding(request: Request, resp: Response, binding: str | None) -> Response:
    if binding:
        # Lax, not Strict: the browser must send it on the top-level redirect back from the provider.
        resp.set_cookie(SSO_COOKIE, binding, max_age=600, httponly=True, samesite="lax", secure=_secure(request), path=SSO_COOKIE_PATH)
    return resp


def _binding_ok(request: Request, expected_hash: str | None) -> bool:
    got = request.cookies.get(SSO_COOKIE)
    return bool(expected_hash and got and secrets.compare_digest(sso.sha(got), expected_hash))


def _app_redirect(app: dict, **params) -> RedirectResponse:
    uri = app["redirect"]
    q = {k: v for k, v in params.items() if v is not None}
    if app.get("state"):
        q["state"] = app["state"]
    return RedirectResponse(uri + ("&" if "?" in uri else "?") + urlencode(q))


def _web_redirect(path: str, **params) -> RedirectResponse:
    # An error message travels in a short-lived cookie that only this server
    # can set, not in the URL, so a crafted link can't put words on the page.
    err = params.pop("sso_error", None)
    if err:
        params["sso_error"] = "1"
    resp = RedirectResponse(f"/#/{path}?{urlencode(params)}" if params else f"/#/{path}")
    if err:
        resp.set_cookie("sv_sso_error", quote(str(err)[:300]), max_age=120, samesite="lax", path="/")
    return resp


@app.get("/api/auth/sso/{pid}/start")
def sso_start(pid: str, request: Request, app_redirect: str = "", app_state: str = "", code_challenge: str = ""):
    """Begin SSO sign-in. Browsers navigate here directly. Mobile apps open it in a
    browser tab with ``app_redirect`` (e.g. ``scanvault://sso``) and a PKCE
    ``code_challenge``; the ticket comes back to ``app_redirect``."""
    app_ctx = None
    if app_redirect:
        if not sso.app_redirect_allowed(app_redirect):
            raise ApiError(400, "app_redirect is not allowed (see SCANVAULT_SSO_APP_REDIRECTS)")
        if not (43 <= len(code_challenge) <= 128) or not all(c.isalnum() or c in "-_" for c in code_challenge):
            raise ApiError(400, "A PKCE code_challenge (S256, base64url) is required")
        app_ctx = {"redirect": app_redirect, "state": app_state[:500] or None, "challenge": code_challenge}
    if not setup_complete():
        return _app_redirect(app_ctx, error="setup_required") if app_ctx else _web_redirect("")
    if not _start_rate_ok(request):
        raise ApiError(429, "Too many sign-in attempts. Wait a few minutes.")
    try:
        p = _sso_provider(pid)
        url, binding = _sso_flow(request, p, "login", app=app_ctx)
    except ApiError as exc:
        log.warning("SSO start failed: %s", exc.msg)
        return _app_redirect(app_ctx, error=exc.msg) if app_ctx else _web_redirect("", sso_error=exc.msg)
    return _set_binding(request, RedirectResponse(url), binding)


@app.post("/api/auth/sso/{pid}/link")
async def sso_link_start(pid: str, request: Request):
    """Signed-in user links an identity-provider account. Returns the URL to open.
    Body: ``{password}`` (not needed within 10 minutes of an SSO sign-in)."""
    u = require_user(request)
    try:
        j = await body_json(request, 64 * 1024)
    except ApiError as exc:
        if exc.status == 413:
            raise
        j = {}  # an empty body is fine right after an SSO sign-in
    await _reauth(u, j if isinstance(j, dict) else {})
    p = _sso_provider(pid)
    url, binding = _sso_flow(request, p, "link", user_id=u["id"])
    return _set_binding(request, JSONResponse({"url": url}), binding)


@app.get("/api/auth/sso/callback")
def sso_callback(request: Request, state: str = "", code: str = "", error: str = "", error_description: str = ""):
    resp_iss = request.query_params.get("iss", "")
    entry = _sso_requests.pop(state)
    if not entry:
        return _web_redirect("", sso_error="This sign-in link expired. Try again.")
    app_ctx = entry["app"]
    back = "settings" if entry["mode"] == "link" else ""

    def fail(msg: str):
        log.info("SSO %s failed: %r", entry["mode"], msg)
        resp = _app_redirect(app_ctx, error=msg) if app_ctx else _web_redirect(back, sso_error=msg)
        resp.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)
        return resp

    if not app_ctx and not _binding_ok(request, entry["binding"]):
        return fail("This sign-in was started in a different browser. Try again.")
    if error:
        return fail((error_description or error)[:300])
    try:
        p = _sso_provider(entry["pid"])
        # RFC 9207: if the provider names itself in the response, it must be the one we asked.
        doc = oidc.discover(sso.resolve_issuer(p))
        if resp_iss and doc["issuer"] != sso.MS_TEMPLATE and resp_iss.rstrip("/") != doc["issuer"].rstrip("/"):
            return fail("The sign-in response came from a different provider.")
        tokens = oidc.exchange_code(p, code, entry["redirect_uri"], entry["verifier"])
        claims = oidc.validate_id_token(p, tokens["id_token"], entry["nonce"])
    except (sso.SSOError, ApiError) as exc:
        return fail(getattr(exc, "msg", None) or str(exc))
    iss, sub = claims["iss"], str(claims["sub"])
    if resp_iss and doc["issuer"] == sso.MS_TEMPLATE and resp_iss.rstrip("/") != iss.rstrip("/"):
        return fail("The sign-in response came from a different provider.")
    email = claims.get("email") if claims.get("email_verified", True) not in (False, "false") else None
    email = email or claims.get("preferred_username")
    c = store.conn()
    row = c.execute("SELECT * FROM user_identities WHERE issuer=? AND subject=?", (iss, sub)).fetchone()

    if entry["mode"] == "link":
        if row and row["user_id"] != entry["user_id"]:
            return fail(f"That {p['name']} account is already linked to another ScanVault login.")
        if row:
            if row["provider_id"] != p["id"]:
                c.execute("DELETE FROM sessions WHERE identity_id=?", (row["id"],))
            c.execute("UPDATE user_identities SET provider_id=?, email=? WHERE id=?", (p["id"], email, row["id"]))
        else:
            c.execute(
                "INSERT INTO user_identities(id,user_id,provider_id,issuer,subject,email,created_at) VALUES(?,?,?,?,?,?,?)",
                (crypto.random_id(), entry["user_id"], p["id"], iss, sub, email, time.time()),
            )
        resp = _web_redirect("settings", sso="linked")
        resp.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)
        return resp

    if row and row["provider_id"] != p["id"]:
        row = None  # an identity only signs in through the provider it was linked with
    if not row or not c.execute("SELECT 1 FROM users WHERE id=?", (row["user_id"],)).fetchone():
        return fail(f"This {p['name']} account isn't linked to ScanVault yet. Sign in with your password, then link it under Settings > Single sign-on.")
    c.execute("UPDATE user_identities SET last_login=?, email=? WHERE id=?", (time.time(), email, row["id"]))
    grant = {
        "user_id": row["user_id"],
        "identity_id": row["id"],
        "binding": entry["binding"],
        "challenge": app_ctx["challenge"] if app_ctx else None,
    }
    if app_ctx:
        # Anything on the phone can open /start with its own scanvault:// redirect,
        # so the person confirms on a page served by ScanVault before an app gets a ticket.
        cid = _sso_confirms.put({"grant": grant, "app": app_ctx, "email": email, "provider": p["name"]})
        return _app_confirm_page(cid, email, p["name"])
    # The session itself is issued by POST /api/auth/sso/exchange, a same-origin
    # request, so the SameSite=Strict session cookie is set reliably.
    return _web_redirect("sso", ticket=_sso_tickets.put(grant))


_sso_confirms = sso.PendingStore(ttl=300)


def _app_confirm_page(cid: str, email: str | None, provider: str) -> HTMLResponse:
    from html import escape

    who = escape(email or "your account")
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in to the ScanVault app?</title><style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;background:#f4f7f7;color:#13201f}}
main{{max-width:420px;margin:24px;padding:28px;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.08)}}
h1{{font-size:22px;margin:0 0 12px}}p{{margin:0 0 14px}}.row{{display:flex;gap:10px;margin-top:20px}}
button{{flex:1;font:inherit;font-weight:600;padding:12px;border-radius:10px;border:1px solid #c9d6d5;background:#fff;cursor:pointer}}
button.go{{background:#0b5d63;color:#fff;border-color:#0b5d63}}.warn{{font-size:14px;color:#6b4b00;background:#fff6dc;padding:10px 12px;border-radius:10px}}
@media (prefers-color-scheme:dark){{body{{background:#0d1416;color:#e6eeee}}main{{background:#162024}}button{{background:#162024;color:#e6eeee;border-color:#2b3a3e}}.warn{{background:#3a2f12;color:#ffe3a3}}}}
</style></head><body><main><h1>Sign in to the ScanVault app?</h1>
<p>You signed in with {escape(provider)} as <b>{who}</b>. Continuing gives the app that opened this page access to your ScanVault for 30 days.</p>
<p class="warn">Only continue if you just tapped <b>Continue with {escape(provider)}</b> in the ScanVault app yourself.</p>
<form method="post" action="/api/auth/sso/app-confirm"><input type="hidden" name="cid" value="{escape(cid)}">
<div class="row"><button name="action" value="cancel">Cancel</button><button class="go" name="action" value="allow">Continue</button></div></form>
</main></body></html>"""
    return HTMLResponse(body, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'", "Cache-Control": "no-store"})


@app.post("/api/auth/sso/app-confirm")
async def sso_app_confirm(request: Request):
    # A tiny urlencoded form; read it by hand with a cap so nobody can stream a
    # large multipart body to disk on this unauthenticated endpoint.
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return HTMLResponse("<!doctype html><p>Unsupported request.</p>", status_code=415)
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > 4096:
            return HTMLResponse("<!doctype html><p>Request too large.</p>", status_code=413)
    fields = {k: v[0] for k, v in parse_qs(buf.decode("latin-1"), keep_blank_values=True).items()}
    cid, action = fields.get("cid", ""), fields.get("action", "")
    c = _sso_confirms.pop(cid)
    if not c:
        return HTMLResponse("<!doctype html><p>This sign-in expired. Go back to the ScanVault app and try again.</p>", status_code=400)
    if action != "allow":
        return RedirectResponse(_app_redirect(c["app"], error="cancelled").headers["location"], status_code=303)
    ticket = _sso_tickets.put(c["grant"])
    return RedirectResponse(_app_redirect(c["app"], ticket=ticket).headers["location"], status_code=303)


@app.post("/api/auth/sso/exchange")
async def sso_exchange(request: Request):
    """Trade a one-time SSO ticket for a session. Browsers are matched by the
    binding cookie; apps must send the PKCE ``code_verifier`` they started with."""
    j = await body_json(request)
    t = _sso_tickets.pop(str(j.get("ticket") or ""))
    if not t:
        raise ApiError(400, "This sign-in link expired. Try again.", "sso_expired")
    if t["challenge"]:
        if not secrets.compare_digest(sso.pkce_challenge(str(j.get("code_verifier") or "")), t["challenge"]):
            raise ApiError(400, "code_verifier does not match", "sso_pkce")
    elif not _binding_ok(request, t["binding"]):
        raise ApiError(400, "This sign-in was started in a different browser. Try again.", "sso_binding")
    tok, exp = new_session(t["user_id"], t.get("identity_id"))
    resp = session_response(request, {"expires_at": iso(exp), "unlocked": vault.unlocked}, tok, exp)
    resp.delete_cookie(SSO_COOKIE, path=SSO_COOKIE_PATH)
    return resp


# ---- administration

@app.get("/api/sso/config")
def sso_config(request: Request):
    u = require_user(request)
    cfg = _sso_cfg()
    names = {p["id"]: p["name"] for p in _sso_providers(include_disabled=True)}
    idents = [
        {"id": r["id"], "provider_id": r["provider_id"], "provider_name": names.get(r["provider_id"], "Removed provider"),
         "email": r["email"], "linked_at": iso(r["created_at"]), "last_login": iso(r["last_login"])}
        for r in store.conn().execute("SELECT * FROM user_identities WHERE user_id=? ORDER BY created_at", (u["id"],))
    ]
    return {
        "providers": [_sso_admin_out(p) for p in _sso_providers(include_disabled=True)],
        "local_login": cfg["local_login"],
        "local_login_effective": local_login_enabled(),
        "local_login_forced": FORCE_LOCAL_LOGIN,
        "reauth_fresh": _recent_sso(u),
        "redirect_uri": _sso_redirect_uri(request),
        "presets": sso.PRESETS,
        "identities": idents,
    }


def _sso_provider_from(j: dict, old: dict | None = None) -> dict:
    p = dict(old or {})
    preset = j.get("preset", p.get("preset", "generic"))
    if preset not in sso.PRESETS:
        raise ApiError(400, "Unknown provider type")
    p["preset"] = preset
    p["name"] = _str(j.get("name"), 60) or p.get("name") or sso.PRESETS[preset]["name"]
    if "issuer" in j:
        p["issuer"] = (_str(j["issuer"], 500) or "").rstrip("/") if preset == "generic" else ""
    if "fields" in j:
        keys = {f["key"] for f in sso.PRESETS[preset]["fields"]}
        p["fields"] = {k: _str(v, 300) or "" for k, v in (j.get("fields") or {}).items() if k in keys}
    if "client_id" in j:
        p["client_id"] = _str(j["client_id"], 300) or ""
    if j.get("client_secret"):
        p["client_secret"] = str(j["client_secret"]).strip()[:2000]
    if j.get("clear_secret"):
        p.pop("client_secret", None)
    if "scopes" in j:
        scopes = (_str(j["scopes"], 300) or sso.DEFAULT_SCOPES).split()
        p["scopes"] = " ".join(["openid"] + [s for s in scopes if s != "openid"])
    if "enabled" in j:
        p["enabled"] = bool(j["enabled"])
    if not p.get("client_id"):
        raise ApiError(400, "Enter the client ID from your identity provider")
    issuer = sso.resolve_issuer(p)
    if not issuer:
        raise ApiError(400, "Enter the issuer URL" if preset == "generic" else "Fill in every field of the provider")
    try:
        sso.validate_issuer_url(issuer)
        oidc.discover(issuer, force=True)
    except sso.SSOError as exc:
        raise ApiError(400, f"Could not read the provider's OpenID configuration: {exc}", "sso_discovery") from exc
    return p


@app.post("/api/sso/providers")
async def sso_create_provider(request: Request):
    u = _require_admin(request)
    j = await body_json(request)
    await _reauth(u, j)
    p = _sso_provider_from(j)
    p["id"] = crypto.random_id()
    p.setdefault("enabled", True)
    cfg = _sso_cfg()
    cfg["providers"].append(p)
    _sso_save(cfg)
    return _sso_admin_out(p)


@app.put("/api/sso/providers/{pid}")
async def sso_update_provider(pid: str, request: Request):
    u = _require_admin(request)
    j = await body_json(request)
    await _reauth(u, j)
    cfg = _sso_cfg()
    for i, old in enumerate(cfg["providers"]):
        if old["id"] == pid:
            cfg["providers"][i] = p = _sso_provider_from(j, old)
            _sso_save(cfg)
            c = store.conn()
            if sso.resolve_issuer(p) != sso.resolve_issuer(old) or p["client_id"] != old.get("client_id"):
                # A different provider behind the same entry: its old links no longer mean anything.
                _revoke_identity_sessions("provider_id=?", (pid,))
                c.execute("DELETE FROM user_identities WHERE provider_id=?", (pid,))
            elif not p.get("enabled", True):
                _revoke_identity_sessions("provider_id=?", (pid,))
            return _sso_admin_out(p)
    if pid == "env":
        raise ApiError(409, "This provider is configured with SCANVAULT_OIDC_* environment variables")
    raise ApiError(404, "Unknown sign-in provider")


@app.delete("/api/sso/providers/{pid}")
def sso_delete_provider(pid: str, request: Request):
    _require_admin(request)
    if pid == "env":
        raise ApiError(409, "This provider is configured with SCANVAULT_OIDC_* environment variables")
    cfg = _sso_cfg()
    cfg["providers"] = [p for p in cfg["providers"] if p["id"] != pid]
    _sso_save(cfg)
    _revoke_identity_sessions("provider_id=?", (pid,))
    store.conn().execute("DELETE FROM user_identities WHERE provider_id=?", (pid,))
    return {"ok": True, "local_login": local_login_enabled()}


@app.post("/api/sso/providers/{pid}/test")
def sso_test_provider(pid: str, request: Request):
    _require_admin(request)
    p = _sso_provider(pid, include_disabled=True)
    try:
        doc = oidc.discover(sso.resolve_issuer(p), force=True)
    except sso.SSOError as exc:
        raise ApiError(502, str(exc), "sso_discovery") from exc
    return {"ok": True, "issuer": doc["issuer"], "authorization_endpoint": doc["authorization_endpoint"]}


@app.put("/api/sso/settings")
async def sso_settings(request: Request):
    u = _require_admin(request)
    j = await body_json(request)
    cfg = _sso_cfg()
    if "local_login" in j:
        want = bool(j["local_login"])
        if not want and cfg["local_login"]:
            await _reauth(u, j)
        if not want:
            ids = {p["id"] for p in _sso_providers()}
            mine = [r for r in store.conn().execute("SELECT provider_id FROM user_identities WHERE user_id=?", (u["id"],)) if r["provider_id"] in ids]
            if not mine:
                raise ApiError(400, "Link an SSO account to your login first, so you can still sign in.")
        cfg["local_login"] = want
    _sso_save(cfg)
    return {"ok": True, "local_login": local_login_enabled()}


@app.delete("/api/sso/identities/{iid}")
def sso_unlink(iid: str, request: Request):
    u = require_user(request)
    _revoke_identity_sessions("id=? AND user_id=?", (iid, u["id"]))
    store.conn().execute("DELETE FROM user_identities WHERE id=? AND user_id=?", (iid, u["id"]))
    if _linked_identity_count() == 0 and not _sso_cfg()["local_login"]:
        cfg = _sso_cfg()
        cfg["local_login"] = True  # never leave the vault without a way to sign in
        _sso_save(cfg)
    return {"ok": True, "local_login": local_login_enabled()}


# =================================================================== scheduler

RETRY_AFTER_FAILURE = 3600


def _due(r, now: float) -> bool:
    if not r["last_run"]:
        return True
    interval = r["schedule_hours"] * 3600
    if r["last_status"] == "error":
        interval = min(interval, RETRY_AFTER_FAILURE)
    return now - r["last_run"] >= interval


async def scheduler():
    while True:
        await asyncio.sleep(60)
        try:
            now = time.time()
            store.conn().execute("DELETE FROM sessions WHERE expires_at<?", (now,))
            if SESSION_IDLE_DAYS > 0:
                store.conn().execute("DELETE FROM sessions WHERE COALESCE(last_seen, created_at)<?", (now - SESSION_IDLE_DAYS * 86400,))
            if AUTO_LOCK_MINUTES > 0 and vault.unlocked and not backup_lock.locked() and now - _state["last_activity"] > AUTO_LOCK_MINUTES * 60:
                log.info("Locking the vault after %d idle minutes", AUTO_LOCK_MINUTES)
                vault.lock()
                _clear_caches()
            if not vault.unlocked or not setup_complete() or _state["maintenance"]:
                continue
            due = [r["id"] for r in store.conn().execute("SELECT * FROM backup_targets WHERE enabled=1 AND schedule_hours>0") if _due(r, now)]
            for tid in due:
                if vault.unlocked:
                    await asyncio.to_thread(run_backup, tid, vault.master_key, "schedule")
        except Exception:
            log.exception("Scheduled backup failed")


def _auto_unlock() -> None:
    pf = os.environ.get("SCANVAULT_PASSPHRASE_FILE")
    if not pf or not setup_complete():
        return
    p = Path(pf)
    if not p.is_file():
        log.error("SCANVAULT_PASSPHRASE_FILE %s does not exist or is not a file; the vault stays locked", pf)
        return
    try:
        text = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.error("Cannot read SCANVAULT_PASSPHRASE_FILE %s (%s); the vault stays locked", pf, exc.__class__.__name__)
        return
    passphrase = text.rstrip("\r\n")
    try:
        try:
            master = crypto.unwrap_master_key(store.keystore(), passphrase)
        except crypto.CryptoError:
            master = crypto.unwrap_master_key(store.keystore(), passphrase.strip())
        vault.master_key = master
        migrate_file_macs(master)
        log.info("Vault auto-unlocked from SCANVAULT_PASSPHRASE_FILE")
    except crypto.CryptoError:
        log.error("SCANVAULT_PASSPHRASE_FILE does not unlock the vault")


async def _startup():
    _recover_rekey()
    for p in store.tmp_dir.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    for sub in (store.files_dir, store.preview_dir):
        for suffix in (".old", ".prerekey"):
            old = sub.with_name(sub.name + suffix)
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
    store.conn().execute("UPDATE backup_runs SET status='error', message='Interrupted (server restarted)', finished_at=? WHERE status='running'", (time.time(),))
    store.conn().execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
    _announce_setup_code()
    try:
        _auto_unlock()
    except Exception:
        log.exception("Auto-unlock failed; the vault stays locked")
    return asyncio.create_task(scheduler())


@asynccontextmanager
async def lifespan(_app):
    task = await _startup()
    yield
    task.cancel()


app.router.lifespan_context = lifespan


# =================================================================== static UI

STATIC = Path(__file__).parent / "static"


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
