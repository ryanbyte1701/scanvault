"""Storage: SQLite metadata (sensitive fields sealed with the master key) and
encrypted file blobs on disk."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import crypto

DATA_DIR = Path(os.environ.get("SCANVAULT_DATA", "/data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  last_seen REAL);
CREATE TABLE IF NOT EXISTS user_devices (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL, created_at REAL NOT NULL, last_used REAL);
CREATE TABLE IF NOT EXISTS patients (
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL, blob BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS studies (
  id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  created_at REAL NOT NULL, blob BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  patient_id TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  has_preview INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, blob BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS files_study ON files(study_id);
CREATE INDEX IF NOT EXISTS files_patient ON files(patient_id);
CREATE INDEX IF NOT EXISTS studies_patient ON studies(patient_id);
CREATE TABLE IF NOT EXISTS backup_targets (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  schedule_hours INTEGER NOT NULL DEFAULT 24, keep_last INTEGER NOT NULL DEFAULT 14,
  config BLOB NOT NULL, last_run REAL, last_status TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS backup_runs (
  id TEXT PRIMARY KEY, target_id TEXT, started_at REAL NOT NULL, finished_at REAL,
  status TEXT NOT NULL, message TEXT, size INTEGER, filename TEXT);
CREATE TABLE IF NOT EXISTS user_identities (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, email TEXT,
  created_at REAL NOT NULL, last_login REAL, UNIQUE(issuer, subject));
"""


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = Path(data_dir)
        self.files_dir = self.dir / "files"
        self.preview_dir = self.dir / "previews"
        self.tmp_dir = self.dir / "tmp"
        for d in (self.dir, self.files_dir, self.preview_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.db_path = self.dir / "scanvault.db"
        self.keystore_path = self.dir / "keystore.json"
        self._local = threading.local()
        self.generation = 0  # bumped on restore so every thread reopens the DB
        self.lock = threading.RLock()
        self._guard = threading.Lock()
        self._backups_running = 0
        self._deferred: list[Path] = []
        with self.conn() as c:
            c.executescript(SCHEMA)
        self.migrate()

    def migrate(self) -> None:
        c = self.conn()
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        if "last_seen" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN last_seen REAL")

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is not None and getattr(self._local, "gen", -1) != self.generation:
            c.close()
            c = None
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")  # a commit survives power loss (key changes rely on it)
            # A restored backup may predate newer tables; create any that are missing.
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
            if "identity_id" not in cols:  # sessions from an SSO sign-in remember the identity
                c.execute("ALTER TABLE sessions ADD COLUMN identity_id TEXT")
            self._local.c = c
            self._local.gen = self.generation
        return c

    def reopen(self) -> None:
        """Invalidate connections in all threads (called around a restore)."""
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None
        self.generation += 1

    # ---- config
    def get_config(self, key: str, default=None):
        r = self.conn().execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_config(self, key: str, value) -> None:
        self.conn().execute(
            "INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ---- keystore
    def keystore(self) -> dict | None:
        if not self.keystore_path.exists():
            return None
        return json.loads(self.keystore_path.read_text())

    def write_keystore(self, ks: dict) -> None:
        tmp = self.keystore_path.with_suffix(".tmp")
        write_durable(tmp, json.dumps(ks, indent=2).encode())
        tmp.replace(self.keystore_path)
        fsync_dir(self.keystore_path.parent)

    # ---- blobs
    def blob_path(self, file_id: str) -> Path:
        return self.files_dir / file_id[:2] / f"{file_id}.enc"

    def preview_path(self, file_id: str) -> Path:
        return self.preview_dir / file_id[:2] / f"{file_id}.enc"

    def now(self) -> float:
        return time.time()

    # ---- consistent backups
    @contextmanager
    def backup_guard(self):
        """While a backup is being written, blob deletions are queued and run
        when the last backup finishes, so the archive never references a
        file that has disappeared."""
        with self._guard:
            self._backups_running += 1
        try:
            yield
        finally:
            with self._guard:
                self._backups_running -= 1
                doomed = self._deferred if self._backups_running == 0 else []
                if doomed:
                    self._deferred = []
            for p in doomed:
                p.unlink(missing_ok=True)

    def remove_blobs(self, file_id: str) -> None:
        paths = [self.blob_path(file_id), self.preview_path(file_id)]
        with self._guard:
            if self._backups_running:
                self._deferred.extend(paths)
                return
        for p in paths:
            p.unlink(missing_ok=True)


def fsync_dir(path: Path) -> None:
    """Make renames and new files in a directory survive power loss."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def fsync_tree(root: Path) -> None:
    """fsync every file and directory below root (used before a key change commits)."""
    for d, _dirs, files in os.walk(root):
        for f in files:
            fd = os.open(os.path.join(d, f), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fsync_dir(Path(d))


def write_durable(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, mode)


def seal(mk: bytes, obj, ctx: str) -> bytes:
    return crypto.seal_json(mk, obj, ctx)


def unseal(mk: bytes, blob: bytes, ctx: str):
    return crypto.open_json(mk, blob, ctx)
