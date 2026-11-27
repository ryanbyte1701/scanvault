"""Encrypted backup archives (.svb).

Layout::

    b"SVB1" | header_len(4, BE) | header JSON | SVE1 encrypted stream of a tar

The JSON header is not secret: vault id, created time and key fingerprint.
Since format 2 it is authenticated (its SHA-256 is part of the stream's
associated data), so it cannot be edited without the restore failing.

By default the header does NOT contain the passphrase-wrapped master key, so
a stolen backup file cannot be attacked by guessing the passphrase offline:
it can only be opened with the recovery key file. The owner can opt in to
"passphrase restore" (vault setting ``backup_include_wrapped_key``).

The tar holds the SQLite database (patient metadata sealed per row), the
already-encrypted file blobs and previews, and the keystore. It is streamed
straight into the encryption, so no plaintext archive is written to disk.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import crypto

MAGIC = b"SVB1"
AAD_V1 = b"scanvault-backup-v1"
FORMAT = 2
CONTENT_TABLES = ("patients", "studies", "files")


def backup_name(vault_id: str, ts: float | None = None) -> str:
    """scanvault-backup-<vault id>-<UTC time>Z-<random>.svb

    The vault id lets several vaults share one destination folder without
    pruning each other's backups; the random suffix keeps names unique when
    two backups start in the same second. Names still sort by time."""
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return f"scanvault-backup-{vault_id}-{dt.strftime('%Y%m%d-%H%M%S')}Z-{secrets.token_hex(2)}.svb"


def _aad(fmt: int, header_bytes: bytes) -> bytes:
    if fmt <= 1:
        return AAD_V1
    return b"scanvault-backup-v2:" + hashlib.sha256(header_bytes).digest()


def _db_snapshot(store) -> tuple[bytes, list[str]]:
    src = sqlite3.connect(store.db_path)
    mem = sqlite3.connect(":memory:")
    try:
        src.backup(mem)
        mem.execute("DELETE FROM sessions")  # never ship session tokens off the box
        mem.execute("DELETE FROM user_devices")
        mem.commit()
        ids = [r[0] for r in mem.execute("SELECT id FROM files")]
        data = bytearray(mem.serialize())
        data[18:20] = b"\x01\x01"  # rollback-journal file format (not WAL), so it opens anywhere
        return bytes(data), ids
    finally:
        src.close()
        mem.close()


def create_archive(store, master_key: bytes, out_path: Path, app_version: str, include_wrapped_key: bool = False) -> dict:
    """Write an encrypted backup to out_path. Blob deletions requested while
    the backup runs are deferred (store.backup_guard), so the archive is a
    consistent snapshot of the database and the files it references."""
    with store.backup_guard():
        ks = store.keystore()
        db_bytes, file_ids = _db_snapshot(store)
        created = datetime.now(timezone.utc).isoformat()
        manifest = {"format": FORMAT, "app_version": app_version, "vault_id": ks["vault_id"], "created": created, "files": len(file_ids)}
        header = {
            "format": FORMAT,
            "vault_id": ks["vault_id"],
            "fingerprint": ks["fingerprint"],
            "created": created,
            "app_version": app_version,
        }
        if include_wrapped_key:
            header["keystore"] = ks
        hb = json.dumps(header).encode()
        tmp_out = out_path.with_name(out_path.name + f".{secrets.token_hex(4)}.partial")
        try:
            with open(tmp_out, "wb") as out:
                out.write(MAGIC + struct.pack(">I", len(hb)) + hb)
                enc = crypto.StreamEncryptor(master_key, out, _aad(FORMAT, hb))
                with tarfile.open(fileobj=enc, mode="w|") as tar:
                    _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
                    _add_bytes(tar, "scanvault.db", db_bytes)
                    _add_bytes(tar, "keystore.json", json.dumps(ks, indent=2).encode())
                    for fid in file_ids:
                        bp = store.blob_path(fid)
                        if not bp.exists():
                            raise crypto.CryptoError(f"File {fid} is missing from the vault; backup aborted")
                        tar.add(bp, f"files/{fid[:2]}/{fid}.enc")
                        pp = store.preview_path(fid)
                        if pp.exists():
                            tar.add(pp, f"previews/{fid[:2]}/{fid}.enc")
                enc.close()
            tmp_out.replace(out_path)
        finally:
            tmp_out.unlink(missing_ok=True)
    return header


def read_header(path: Path) -> dict:
    with open(path, "rb") as f:
        return _read_header(f)[0]


def _read_header(f) -> tuple[dict, bytes]:
    if f.read(4) != MAGIC:
        raise crypto.CryptoError("Not a ScanVault backup (.svb) file")
    raw = f.read(4)
    if len(raw) != 4:
        raise crypto.CryptoError("Corrupt backup header")
    (n,) = struct.unpack(">I", raw)
    if n > 1_000_000:
        raise crypto.CryptoError("Corrupt backup header")
    hb = f.read(n)
    try:
        h = json.loads(hb)
    except Exception as exc:
        raise crypto.CryptoError("Corrupt backup header") from exc
    if not isinstance(h, dict) or "fingerprint" not in h:
        raise crypto.CryptoError("Corrupt backup header")
    return h, hb


def master_key_for(path: Path, passphrase: str | None = None, recovery_text: str | None = None) -> bytes:
    header = read_header(path)
    if recovery_text:
        mk = crypto.parse_recovery_file(recovery_text)
    elif passphrase:
        if not header.get("keystore"):
            raise crypto.CryptoError(
                "This backup can only be opened with the recovery key file (passphrase restore is turned off for its vault)"
            )
        mk = crypto.unwrap_master_key(header["keystore"], passphrase)
    else:
        raise crypto.CryptoError("Provide the passphrase or the recovery key file")
    if crypto.fingerprint(mk) != header["fingerprint"]:
        raise crypto.CryptoError(
            f"This key (fingerprint {crypto.fingerprint(mk)}) does not match the backup (fingerprint {header['fingerprint']})"
        )
    return mk


def extract_archive(path: Path, master_key: bytes, dest: Path) -> dict:
    """Decrypt and untar into dest (a fresh directory), streaming. Returns the header."""
    dest.mkdir(parents=True, exist_ok=True)
    with open(path, "rb") as f:
        header, hb = _read_header(f)
        dec = crypto.StreamDecryptor(master_key, f, _aad(int(header.get("format", 1)), hb))
        with tarfile.open(fileobj=dec, mode="r|") as tar:
            tar.extractall(dest, filter="data")
        dec.read(-1)  # drain: verifies the final chunk and that nothing follows it
    return header


def restore_into_store(store, archive: Path, master_key: bytes, keep_local: bool = False) -> dict:
    """Replace the store's data with the archive's contents.

    keep_local=False (fresh install): the whole database and keystore come
    from the backup. Sessions are cleared and every backup destination is
    disabled so a restored copy does not start writing to (and pruning) the
    old server's destinations.

    keep_local=True (restore over a running vault): only patients, studies
    and files come back. Logins, sessions, the current keystore (passphrase),
    settings, destinations and backup history stay as they are now."""
    with tempfile.TemporaryDirectory(dir=store.tmp_dir) as td:
        td = Path(td)
        header = extract_archive(archive, master_key, td)
        if not (td / "scanvault.db").exists() or not (td / "keystore.json").exists():
            raise crypto.CryptoError("Backup is missing its database or keystore")
        rdb = sqlite3.connect(td / "scanvault.db")
        try:
            missing = [r[0] for r in rdb.execute("SELECT id FROM files") if not (td / "files" / r[0][:2] / f"{r[0]}.enc").exists()]
        finally:
            rdb.close()
        if missing:
            raise crypto.CryptoError(f"Backup is incomplete: {len(missing)} file(s) are missing from it")
        with store.lock:
            if keep_local:
                c = store.conn()
                c.execute("ATTACH DATABASE ? AS r", (str(td / "scanvault.db"),))
                try:
                    c.execute("BEGIN IMMEDIATE")
                    for t in reversed(CONTENT_TABLES):
                        c.execute(f"DELETE FROM main.{t}")
                    for t in CONTENT_TABLES:
                        cols = [r[1] for r in c.execute(f"PRAGMA main.table_info({t})")]
                        rcols = {r[1] for r in c.execute(f"PRAGMA r.table_info({t})")}
                        use = [x for x in cols if x in rcols]
                        c.execute(f"INSERT INTO main.{t}({','.join(use)}) SELECT {','.join(use)} FROM r.{t}")
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise
                finally:
                    c.execute("DETACH DATABASE r")
            else:
                store.reopen()
                for name in ("scanvault.db-wal", "scanvault.db-shm"):
                    p = store.dir / name
                    if p.exists():
                        p.unlink()
                shutil.move(str(td / "scanvault.db"), store.db_path)
                shutil.move(str(td / "keystore.json"), store.keystore_path)
                os.chmod(store.keystore_path, 0o600)
                store.reopen()
                store.migrate()
                c = store.conn()
                c.execute("DELETE FROM sessions")
                c.execute("DELETE FROM user_devices")
                c.execute("UPDATE backup_targets SET enabled=0")
                c.execute("UPDATE backup_runs SET status='error', message='Interrupted' WHERE status='running'")
            for sub, target in (("files", store.files_dir), ("previews", store.preview_dir)):
                old = target.with_name(target.name + ".old")
                if old.exists():
                    shutil.rmtree(old)
                if target.exists():
                    target.rename(old)
                if (td / sub).exists():
                    shutil.move(str(td / sub), target)
                else:
                    target.mkdir(parents=True)
                shutil.rmtree(old, ignore_errors=True)
    return header


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))
