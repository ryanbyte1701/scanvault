"""Backup destinations: cloud drives, object storage, SSH, WebDAV, SMB, local disks.

Every archive handed to these classes is already encrypted with the vault
master key (see backup.py). Providers only ever see ciphertext and an
opaque file name.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import posixpath
import shutil
import time
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger("scanvault.targets")
PREFIX = "scanvault-backup-"
SUFFIX = ".svb"
TIMEOUT = 60


MAX_LISTING = 16 * 1024 * 1024  # bytes of folder listing accepted from a server


class TargetError(Exception):
    pass


class HostKeyPinned(TargetError):
    """Raised on the first contact with an SSH server, after its host key was
    pinned and before any credentials were sent."""

    def __init__(self, fingerprint: str):
        super().__init__(f"Pinned the server's host key {fingerprint} on first contact. Check it, then run the backup again.")
        self.fingerprint = fingerprint


class Target:
    type = ""
    # Secrets are write-only in the UI: never returned by the API.
    secret_fields: tuple[str, ...] = ()
    # Fields that say *where* the backups go. When one changes, stored secrets
    # are dropped unless they are entered again, so an edit can't quietly send
    # the old password to a new server.
    location_fields: tuple[str, ...] = ()

    def __init__(self, config: dict, save_config: Callable[[dict], None] | None = None, vault_id: str = ""):
        self.config = config
        self._save = save_config or (lambda c: None)
        self.vault_id = vault_id

    @classmethod
    def validate(cls, config: dict) -> None:
        """Reject a configuration before it is saved (raise TargetError)."""

    def test(self) -> str:
        raise NotImplementedError

    def upload(self, path: Path, name: str) -> None:
        raise NotImplementedError

    def list_backups(self) -> list[tuple[str, str]]:
        """[(name, handle)] sorted oldest first."""
        raise NotImplementedError

    def delete(self, handle: str) -> None:
        raise NotImplementedError

    def download(self, handle: str, dest: Path) -> None:
        raise NotImplementedError

    def own_prefix(self) -> str:
        return f"{PREFIX}{self.vault_id}-" if self.vault_id else PREFIX

    def prune(self, keep_last: int) -> int:
        """Delete this vault's oldest backups beyond keep_last. Backups of other
        vaults sharing the folder (and pre-0.2 names without a vault id) are
        never touched."""
        if keep_last <= 0:
            return 0
        pre = self.own_prefix()
        items = [i for i in self.list_backups() if i[0].startswith(pre) and i[0].endswith(SUFFIX)]
        items.sort(key=lambda i: i[0])
        doomed = items[:-keep_last] if len(items) > keep_last else []
        for _, handle in doomed:
            self.delete(handle)
        return len(doomed)


def _raise(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:300]
        raise TargetError(f"{what} failed ({resp.status_code}): {detail}")


def _refresh_token_of(r: requests.Response, who: str) -> str:
    try:
        tok = r.json().get("refresh_token")
    except Exception:
        tok = None
    if not tok:
        raise TargetError(f"{who} did not return a refresh token")
    return tok


# ============================================================ Google Drive
class GoogleDrive(Target):
    """auth_mode 'oauth' (client id/secret + refresh token, works with personal
    Drive) or 'service_account' (JSON key, use with a Shared Drive)."""

    type = "gdrive"
    secret_fields = ("client_secret", "refresh_token", "service_account_json")
    API = "https://www.googleapis.com/drive/v3"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
    TOKEN = "https://oauth2.googleapis.com/token"
    SCOPE = "https://www.googleapis.com/auth/drive.file"

    def _token(self) -> str:
        c = self.config
        if c.get("auth_mode") == "service_account":
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            try:
                info = json.loads(c.get("service_account_json") or "{}")
            except json.JSONDecodeError as exc:
                raise TargetError("Service account JSON is not valid JSON") from exc
            creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive"])
            creds.refresh(Request())
            return creds.token
        if not c.get("refresh_token"):
            raise TargetError("Google Drive is not connected yet: click 'Connect Google account'")
        r = requests.post(
            self.TOKEN,
            data={
                "client_id": c.get("client_id", ""),
                "client_secret": c.get("client_secret", ""),
                "refresh_token": c["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=TIMEOUT,
        )
        _raise(r, "Google token refresh")
        return r.json()["access_token"]

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def _q_params(self) -> dict:
        return {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}

    def test(self) -> str:
        h = self._h()
        folder = self.config.get("folder_id") or "root"
        r = requests.get(f"{self.API}/files/{folder}", headers=h, params={"fields": "id,name", "supportsAllDrives": "true"}, timeout=TIMEOUT)
        _raise(r, "Google Drive folder lookup")
        return f"Connected. Backups go to folder '{r.json().get('name')}'."

    def upload(self, path: Path, name: str) -> None:
        h = self._h()
        meta = {"name": name, "mimeType": "application/octet-stream"}
        if self.config.get("folder_id"):
            meta["parents"] = [self.config["folder_id"]]
        size = path.stat().st_size
        r = requests.post(
            self.UPLOAD,
            params={"uploadType": "resumable", "supportsAllDrives": "true"},
            headers={**h, "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size)},
            data=json.dumps(meta),
            timeout=TIMEOUT,
        )
        _raise(r, "Google Drive upload start")
        url = r.headers.get("Location")
        if not url:
            raise TargetError("Google Drive did not return an upload session")
        chunk = 32 * 256 * 1024  # 8 MiB, multiple of 256 KiB
        failures = 0
        with open(path, "rb") as f:
            offset = 0
            while True:
                f.seek(offset)
                data = f.read(chunk)
                end = offset + len(data) - 1
                rng = f"bytes {offset}-{end}/{size}" if data else f"bytes */{size}"
                try:
                    r = requests.put(url, headers={"Content-Range": rng}, data=data, timeout=300)
                except requests.RequestException as exc:
                    r, err = None, exc
                if r is not None and r.status_code in (200, 201):
                    return
                if r is None or r.status_code >= 500 or r.status_code == 429:
                    failures += 1
                    if failures > 5:
                        if r is None:
                            raise TargetError(f"Google Drive upload failed: {err}")
                        _raise(r, "Google Drive upload")
                    time.sleep(min(30, 2**failures))
                    # ask Drive how much it has, then resume from there
                    try:
                        r = requests.put(url, headers={"Content-Range": f"bytes */{size}"}, timeout=60)
                    except requests.RequestException:
                        continue
                    if r.status_code in (200, 201):
                        return
                if r.status_code != 308:
                    _raise(r, "Google Drive upload")
                got = r.headers.get("Range", "")
                offset = int(got.rsplit("-", 1)[1]) + 1 if "-" in got else 0
                if size == 0 and not data:
                    raise TargetError("Google Drive upload did not complete")

    def list_backups(self):
        h = self._h()
        q = f"name contains '{PREFIX}' and trashed = false"
        if self.config.get("folder_id"):
            q += f" and '{self.config['folder_id']}' in parents"
        out, token = [], None
        while True:
            params = {**self._q_params(), "q": q, "fields": "nextPageToken,files(id,name)", "pageSize": 1000}
            if token:
                params["pageToken"] = token
            r = requests.get(f"{self.API}/files", headers=h, params=params, timeout=TIMEOUT)
            _raise(r, "Google Drive list")
            j = r.json()
            out += [(f["name"], f["id"]) for f in j.get("files", [])]
            token = j.get("nextPageToken")
            if not token:
                return sorted(out)

    def delete(self, handle: str) -> None:
        r = requests.delete(f"{self.API}/files/{handle}", headers=self._h(), params={"supportsAllDrives": "true"}, timeout=TIMEOUT)
        if r.status_code not in (204, 404):
            _raise(r, "Google Drive delete")

    def download(self, handle: str, dest: Path) -> None:
        with requests.get(f"{self.API}/files/{handle}", headers=self._h(), params={"alt": "media", "supportsAllDrives": "true"}, stream=True, timeout=300) as r:
            _raise(r, "Google Drive download")
            with open(dest, "wb") as f:
                for b in r.iter_content(1 << 20):
                    f.write(b)

    # OAuth helpers
    @staticmethod
    def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
        from urllib.parse import urlencode

        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": GoogleDrive.SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        r = requests.post(
            self.TOKEN,
            data={
                "client_id": self.config.get("client_id", ""),
                "client_secret": self.config.get("client_secret", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT,
        )
        _raise(r, "Google code exchange")
        tok = r.json().get("refresh_token")
        if not tok:
            raise TargetError("Google did not return a refresh token; remove ScanVault's access in your Google account and try again")
        self.config["refresh_token"] = tok
        self._save(self.config)


# ============================================================ Box
class Box(Target):
    """auth_mode 'oauth' (3-legged, refresh token rotates on every use) or
    'ccg' (Client Credentials Grant, for a Box Custom App)."""

    type = "box"
    secret_fields = ("client_secret", "refresh_token")
    API = "https://api.box.com/2.0"
    UPLOAD = "https://upload.box.com/api/2.0"
    TOKEN = "https://api.box.com/oauth2/token"
    CHUNKED_MIN = 50 * 1024 * 1024

    def _token(self) -> str:
        c = self.config
        if c.get("auth_mode") == "ccg":
            data = {
                "grant_type": "client_credentials",
                "client_id": c.get("client_id", ""),
                "client_secret": c.get("client_secret", ""),
                "box_subject_type": c.get("subject_type") or "user",
                "box_subject_id": c.get("subject_id", ""),
            }
            r = requests.post(self.TOKEN, data=data, timeout=TIMEOUT)
            _raise(r, "Box token")
            return r.json()["access_token"]
        if not c.get("refresh_token"):
            raise TargetError("Box is not connected yet: click 'Connect Box account'")
        r = requests.post(
            self.TOKEN,
            data={
                "grant_type": "refresh_token",
                "refresh_token": c["refresh_token"],
                "client_id": c.get("client_id", ""),
                "client_secret": c.get("client_secret", ""),
            },
            timeout=TIMEOUT,
        )
        _raise(r, "Box token refresh")
        j = r.json()
        # Box refresh tokens are single-use: persist the new one immediately.
        if j.get("refresh_token"):
            c["refresh_token"] = j["refresh_token"]
            self._save(c)
        return j["access_token"]

    def _folder(self) -> str:
        return self.config.get("folder_id") or "0"

    def test(self) -> str:
        h = {"Authorization": f"Bearer {self._token()}"}
        r = requests.get(f"{self.API}/folders/{self._folder()}", headers=h, params={"fields": "name"}, timeout=TIMEOUT)
        _raise(r, "Box folder lookup")
        return f"Connected. Backups go to folder '{r.json().get('name')}'."

    def upload(self, path: Path, name: str) -> None:
        h = {"Authorization": f"Bearer {self._token()}"}
        size = path.stat().st_size
        if size < self.CHUNKED_MIN:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{self.UPLOAD}/files/content",
                    headers=h,
                    files={
                        "attributes": (None, json.dumps({"name": name, "parent": {"id": self._folder()}})),
                        "file": (name, f, "application/octet-stream"),
                    },
                    timeout=600,
                )
            _raise(r, "Box upload")
            return
        r = requests.post(
            f"{self.UPLOAD}/files/upload_sessions",
            headers=h,
            json={"folder_id": self._folder(), "file_size": size, "file_name": name},
            timeout=TIMEOUT,
        )
        _raise(r, "Box upload session")
        sess = r.json()
        try:
            self._upload_parts(sess, path, size, h)
        except Exception:
            try:
                requests.delete(sess["session_endpoints"]["abort"], headers=h, timeout=TIMEOUT)
            except Exception:
                pass
            raise

    def _upload_parts(self, sess: dict, path: Path, size: int, h: dict) -> None:
        part_size = sess["part_size"]
        parts, whole = [], hashlib.sha1()
        with open(path, "rb") as f:
            offset = 0
            while offset < size:
                data = f.read(part_size)
                whole.update(data)
                digest = base64.b64encode(hashlib.sha1(data).digest()).decode()
                rr = requests.put(
                    sess["session_endpoints"]["upload_part"],
                    headers={
                        **h,
                        "Digest": f"sha={digest}",
                        "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}",
                        "Content-Type": "application/octet-stream",
                    },
                    data=data,
                    timeout=600,
                )
                if rr.status_code >= 500:
                    time.sleep(2)
                    rr = requests.put(rr.request.url, headers=dict(rr.request.headers), data=data, timeout=600)
                _raise(rr, "Box upload part")
                parts.append(rr.json()["part"])
                offset += len(data)
        for _ in range(30):
            rr = requests.post(
                sess["session_endpoints"]["commit"],
                headers={**h, "Digest": "sha=" + base64.b64encode(whole.digest()).decode()},
                json={"parts": parts},
                timeout=600,
            )
            if rr.status_code != 202:
                break
            # Box is still assembling the parts: wait and commit again
            time.sleep(min(60, int(rr.headers.get("Retry-After", "5") or 5)))
        else:
            raise TargetError("Box did not finish processing the upload")
        _raise(rr, "Box upload commit")

    def list_backups(self):
        h = {"Authorization": f"Bearer {self._token()}"}
        out, offset = [], 0
        while True:
            r = requests.get(
                f"{self.API}/folders/{self._folder()}/items",
                headers=h,
                params={"fields": "id,name,type", "limit": 1000, "offset": offset},
                timeout=TIMEOUT,
            )
            _raise(r, "Box list")
            j = r.json()
            entries = j.get("entries", [])
            out += [(e["name"], e["id"]) for e in entries if e.get("type") == "file" and e["name"].startswith(PREFIX)]
            offset += len(entries)
            if not entries or offset >= j.get("total_count", 0):
                return sorted(out)

    def delete(self, handle: str) -> None:
        h = {"Authorization": f"Bearer {self._token()}"}
        r = requests.delete(f"{self.API}/files/{handle}", headers=h, timeout=TIMEOUT)
        if r.status_code not in (204, 404):
            _raise(r, "Box delete")

    def download(self, handle: str, dest: Path) -> None:
        h = {"Authorization": f"Bearer {self._token()}"}
        with requests.get(f"{self.API}/files/{handle}/content", headers=h, stream=True, timeout=300) as r:
            _raise(r, "Box download")
            with open(dest, "wb") as f:
                for b in r.iter_content(1 << 20):
                    f.write(b)

    @staticmethod
    def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
        from urllib.parse import urlencode

        return "https://account.box.com/api/oauth2/authorize?" + urlencode(
            {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state}
        )

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        r = requests.post(
            self.TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.config.get("client_id", ""),
                "client_secret": self.config.get("client_secret", ""),
                "redirect_uri": redirect_uri,
            },
            timeout=TIMEOUT,
        )
        _raise(r, "Box code exchange")
        self.config["refresh_token"] = _refresh_token_of(r, "Box")
        self._save(self.config)


# ============================================================ SFTP / SCP
class SSHTarget(Target):
    """protocol 'sftp' or 'scp'. Password or private key auth. The server host
    key is pinned on first successful test (trust on first use) and enforced
    afterwards."""

    type = "sftp"
    secret_fields = ("password", "private_key", "key_passphrase")
    location_fields = ("host", "port")

    def _connect(self):
        import paramiko

        c = self.config
        pinned = (c.get("host_key") or "").strip()
        if not pinned:
            # First contact: learn the host key without signing in, so the
            # password or key is only ever offered to the pinned server.
            fp = self._fetch_host_key()
            c["host_key"] = fp
            self._save(c)
            raise HostKeyPinned(fp)
        client = paramiko.SSHClient()

        class Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(inner, client_, hostname, key):  # noqa: N805
                fp = _ssh_fp(key)
                if pinned and fp != pinned:
                    raise TargetError(f"Host key mismatch! Expected {pinned}, server sent {fp}. Possible MITM; refusing to connect.")
                c["_seen_host_key"] = fp

        client.set_missing_host_key_policy(Policy())
        pkey = None
        if c.get("private_key"):
            pkey = _load_pkey(c["private_key"], c.get("key_passphrase") or None)
        try:
            client.connect(
                c["host"],
                port=int(c.get("port") or 22),
                username=c.get("username"),
                password=c.get("password") or None,
                pkey=pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=20,
                banner_timeout=20,
                auth_timeout=20,
            )
        except TargetError:
            raise
        except Exception as exc:
            raise TargetError(f"SSH connection failed: {exc}") from exc
        if not pinned and c.get("_seen_host_key"):
            c["host_key"] = c.pop("_seen_host_key")
            self._save(c)
        c.pop("_seen_host_key", None)
        return client

    def _fetch_host_key(self) -> str:
        import socket

        import paramiko

        c = self.config
        try:
            sock = socket.create_connection((c["host"], int(c.get("port") or 22)), timeout=20)
        except (OSError, ValueError, KeyError) as exc:
            raise TargetError(f"SSH connection failed: {exc}") from exc
        t = paramiko.Transport(sock)
        try:
            t.banner_timeout = 20
            t.start_client(timeout=20)
            return _ssh_fp(t.get_remote_server_key())
        except Exception as exc:
            raise TargetError(f"SSH connection failed: {exc}") from exc
        finally:
            t.close()

    def _dir(self) -> str:
        return self.config.get("remote_dir") or "."

    def _proto(self) -> str:
        return self.config.get("protocol") or "sftp"

    def test(self) -> str:
        try:
            client = self._connect()
        except HostKeyPinned as exc:
            return (f"Reached the server and pinned its host key {exc.fingerprint}. Nothing was signed in yet. "
                    "Check that this matches your server (ssh-keygen -lf on its host key), then press Test again to sign in.")
        try:
            if self._proto() == "sftp":
                sftp = client.open_sftp()
                _sftp_mkdirs(sftp, self._dir())
                sftp.listdir(self._dir())
            else:
                _exec(client, f"mkdir -p {_q(self._dir())} && test -w {_q(self._dir())}")
        finally:
            client.close()
        return f"Connected over {self._proto().upper()}. Host key pinned: {self.config.get('host_key')}"

    def upload(self, path: Path, name: str) -> None:
        client = self._connect()
        try:
            remote = posixpath.join(self._dir(), name)
            tmp = remote + ".part"
            if self._proto() == "sftp":
                sftp = client.open_sftp()
                _sftp_mkdirs(sftp, self._dir())
                try:
                    sftp.put(str(path), tmp, confirm=True)
                    sftp.posix_rename(tmp, remote)
                except Exception:
                    try:
                        sftp.remove(tmp)
                    except Exception:
                        pass
                    raise
            else:
                from scp import SCPClient

                _exec(client, f"mkdir -p {_q(self._dir())}")
                try:
                    with SCPClient(client.get_transport(), socket_timeout=120) as scp:
                        scp.put(str(path), tmp)
                    # only a complete upload gets the real backup name
                    _exec(client, f"test $(wc -c < {_q(tmp)}) -eq {path.stat().st_size} && mv -f {_q(tmp)} {_q(remote)}")
                except Exception:
                    try:
                        _exec(client, f"rm -f {_q(tmp)}")
                    except Exception:
                        pass
                    raise
        finally:
            client.close()

    def list_backups(self):
        client = self._connect()
        try:
            if self._proto() == "sftp":
                names = client.open_sftp().listdir(self._dir())
            else:
                names = _exec(client, f"ls -1 {_q(self._dir())}").splitlines()
        finally:
            client.close()
        return sorted((n, n) for n in names if n.startswith(PREFIX) and n.endswith(SUFFIX))

    def delete(self, handle: str) -> None:
        _plain_name(handle)
        client = self._connect()
        try:
            remote = posixpath.join(self._dir(), handle)
            if self._proto() == "sftp":
                client.open_sftp().remove(remote)
            else:
                _exec(client, f"rm -f {_q(remote)}")
        finally:
            client.close()

    def download(self, handle: str, dest: Path) -> None:
        _plain_name(handle)
        client = self._connect()
        try:
            remote = posixpath.join(self._dir(), handle)
            if self._proto() == "sftp":
                client.open_sftp().get(remote, str(dest))
            else:
                from scp import SCPClient

                with SCPClient(client.get_transport()) as scp:
                    scp.get(remote, str(dest))
        finally:
            client.close()


def _plain_name(handle: str) -> str:
    """Remote backup handles that are file names must be just a backup name."""
    if not handle or "/" in handle or "\\" in handle or handle.startswith(".") or not handle.startswith(PREFIX) or not handle.endswith(SUFFIX):
        raise TargetError("Not a ScanVault backup name")
    return handle


def _q(s: str) -> str:
    import shlex

    return shlex.quote(s)


def _exec(client, cmd: str) -> str:
    _, out, err = client.exec_command(cmd, timeout=60)
    code = out.channel.recv_exit_status()
    if code != 0:
        raise TargetError(f"Remote command failed ({code}): {err.read().decode(errors='replace')[:300]}")
    return out.read().decode(errors="replace")


def _sftp_mkdirs(sftp, path: str) -> None:
    if path in ("", ".", "/"):
        return
    cur = "/" if path.startswith("/") else ""
    for part in [p for p in path.split("/") if p]:
        cur = posixpath.join(cur, part) if cur else part
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def _ssh_fp(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _load_pkey(text: str, passphrase: str | None):
    import paramiko

    errors = []
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase)
        except Exception as exc:
            errors.append(f"{cls.__name__}: {exc}")
    raise TargetError("Could not load private key (" + "; ".join(errors) + ")")


# ============================================================ S3 and S3-compatible
S3_PRESETS = {
    # provider -> endpoint template ({region} filled in); "" = AWS default
    "aws": "",
    "b2": "https://s3.{region}.backblazeb2.com",
    "wasabi": "https://s3.{region}.wasabisys.com",
    "r2": "https://{account_id}.r2.cloudflarestorage.com",
    "c2": "https://{region}.s3.synologyc2.net",
    "minio": "",
    "other": "",
}


class S3(Target):
    """Amazon S3 and anything speaking its API: Backblaze B2, Wasabi,
    Cloudflare R2, Synology C2 Object Storage, MinIO, Ceph, Garage..."""

    type = "s3"
    secret_fields = ("secret_access_key",)
    location_fields = ("provider", "endpoint_url", "account_id", "region")

    @classmethod
    def validate(cls, config: dict) -> None:
        from urllib.parse import urlparse

        region = (config.get("region") or "").strip()
        if region and not re.fullmatch(r"[a-z0-9-]{1,40}", region):
            raise TargetError("Region may only contain lowercase letters, digits and dashes (e.g. eu-central-1)")
        ep = (config.get("endpoint_url") or "").strip()
        if ep:
            u = urlparse(ep)
            if u.scheme != "https" or not u.hostname:
                raise TargetError("The endpoint URL must start with https:// (keys and backups would otherwise travel in clear)")
            if u.username or u.password:
                raise TargetError("Put the keys in their own fields, not in the endpoint URL")

    def _client(self):
        import boto3
        from botocore.config import Config

        c = self.config
        prov = c.get("provider") or "aws"
        endpoint = (c.get("endpoint_url") or "").strip()
        region = (c.get("region") or "").strip() or ("auto" if prov == "r2" else "us-east-1")
        if not endpoint and S3_PRESETS.get(prov):
            if not re.fullmatch(r"[a-z0-9-]{1,40}", region) or not re.fullmatch(r"[A-Za-z0-9-]{0,64}", c.get("account_id") or ""):
                raise TargetError("Region and account ID may only contain letters, digits and dashes")
            endpoint = S3_PRESETS[prov].format(region=region, account_id=c.get("account_id", ""))
        if endpoint:
            self.validate({"endpoint_url": endpoint})  # also for destinations saved before https was required
        if not c.get("bucket"):
            raise TargetError("Bucket name is required")
        if not c.get("access_key_id") or not c.get("secret_access_key"):
            # never fall back to whatever credentials the host or container has
            raise TargetError("Access key ID and secret access key are required")
        return boto3.client(
            "s3",
            aws_access_key_id=c["access_key_id"],
            aws_secret_access_key=c["secret_access_key"],
            region_name=region,
            endpoint_url=endpoint or None,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if c.get("path_style") or prov == "minio" else "auto"},
                retries={"max_attempts": 5, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _key(self, name: str) -> str:
        pre = (self.config.get("prefix") or "").strip("/")
        return f"{pre}/{name}" if pre else name

    def _wrap(self, what, fn):
        try:
            return fn()
        except TargetError:
            raise
        except Exception as exc:
            raise TargetError(f"S3 {what} failed: {exc}") from exc

    def test(self) -> str:
        cl = self._client()
        b = self.config["bucket"]
        self._wrap("bucket check", lambda: cl.head_bucket(Bucket=b))
        k = self._key(".scanvault-write-test")
        self._wrap("write test", lambda: cl.put_object(Bucket=b, Key=k, Body=b"ok"))
        try:
            cl.delete_object(Bucket=b, Key=k)
        except Exception:
            return (f"Connected. Backups go to s3://{b}/{self._key('')}. These keys can't delete objects, so ScanVault "
                    "can't remove old backups: set 'Keep last' to 0 and use a bucket lifecycle rule instead.")
        return f"Connected. Backups go to s3://{b}/{self._key('')}"

    def upload(self, path: Path, name: str) -> None:
        from boto3.s3.transfer import TransferConfig

        cl = self._client()
        extra = {"ContentType": "application/octet-stream"}
        if self.config.get("storage_class"):
            extra["StorageClass"] = self.config["storage_class"]
        cfg = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=64 * 1024 * 1024, max_concurrency=4)
        self._wrap("upload", lambda: cl.upload_file(str(path), self.config["bucket"], self._key(name), ExtraArgs=extra, Config=cfg))

    def list_backups(self):
        cl = self._client()
        out = []
        pag = cl.get_paginator("list_objects_v2")

        def run():
            for page in pag.paginate(Bucket=self.config["bucket"], Prefix=self._key(PREFIX)):
                for o in page.get("Contents", []):
                    out.append((o["Key"].rsplit("/", 1)[-1], o["Key"]))

        self._wrap("list", run)
        return sorted(out)

    def delete(self, handle: str) -> None:
        self._own_key(handle)
        cl = self._client()
        self._wrap("delete", lambda: cl.delete_object(Bucket=self.config["bucket"], Key=handle))

    def _own_key(self, handle: str) -> None:
        if not handle.startswith(self._key(PREFIX)) or not handle.endswith(SUFFIX) or ".." in handle:
            raise TargetError("Not a ScanVault backup in this bucket folder")

    def download(self, handle: str, dest: Path) -> None:
        self._own_key(handle)
        cl = self._client()
        self._wrap("download", lambda: cl.download_file(self.config["bucket"], handle, str(dest)))


# ============================================================ Dropbox
class Dropbox(Target):
    """Dropbox app (scoped access, App folder or Full Dropbox) with OAuth
    offline refresh token."""

    type = "dropbox"
    secret_fields = ("app_secret", "refresh_token")
    API = "https://api.dropboxapi.com/2"
    CONTENT = "https://content.dropboxapi.com/2"
    TOKEN = "https://api.dropboxapi.com/oauth2/token"
    CHUNK = 64 * 1024 * 1024

    def _token(self) -> str:
        c = self.config
        if not c.get("refresh_token"):
            raise TargetError("Dropbox is not connected yet: click 'Connect Dropbox account'")
        r = requests.post(self.TOKEN, data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"]}, auth=(c.get("app_key", ""), c.get("app_secret", "")), timeout=TIMEOUT)
        _raise(r, "Dropbox token refresh")
        return r.json()["access_token"]

    def _folder(self) -> str:
        f = "/" + (self.config.get("folder") or "ScanVault").strip("/")
        return "" if f == "/" else f

    def _rpc(self, tok, ep, body, ok404=False):
        r = requests.post(f"{self.API}/{ep}", headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=TIMEOUT)
        if ok404 and r.status_code == 409:
            return None
        _raise(r, f"Dropbox {ep}")
        return r.json()

    def test(self) -> str:
        tok = self._token()
        acct = self._rpc(tok, "users/get_current_account", None)
        if self._folder():
            r = requests.post(f"{self.API}/files/create_folder_v2", headers={"Authorization": f"Bearer {tok}"}, json={"path": self._folder()}, timeout=TIMEOUT)
            if r.status_code not in (200, 409):  # 409 = already exists
                _raise(r, "Dropbox create folder")
        return f"Connected as {acct.get('email') or acct.get('name', {}).get('display_name', 'Dropbox user')}. Backups go to {self._folder() or '/'}"

    def upload(self, path: Path, name: str) -> None:
        tok = self._token()
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/octet-stream"}
        dest = f"{self._folder()}/{name}"
        size = path.stat().st_size
        commit = {"path": dest, "mode": "add", "autorename": False, "mute": True}
        with open(path, "rb") as f:
            if size <= 150 * 1024 * 1024:
                r = requests.post(f"{self.CONTENT}/files/upload", headers={**h, "Dropbox-API-Arg": json.dumps(commit)}, data=f, timeout=600)
                _raise(r, "Dropbox upload")
                return
            r = requests.post(f"{self.CONTENT}/files/upload_session/start", headers={**h, "Dropbox-API-Arg": json.dumps({"close": False})}, data=f.read(self.CHUNK), timeout=600)
            _raise(r, "Dropbox upload start")
            sid, offset = r.json()["session_id"], f.tell()
            while True:
                data = f.read(self.CHUNK)
                cursor = {"session_id": sid, "offset": offset}
                if f.tell() >= size:
                    r = requests.post(f"{self.CONTENT}/files/upload_session/finish", headers={**h, "Dropbox-API-Arg": json.dumps({"cursor": cursor, "commit": commit})}, data=data, timeout=600)
                    _raise(r, "Dropbox upload finish")
                    return
                r = requests.post(f"{self.CONTENT}/files/upload_session/append_v2", headers={**h, "Dropbox-API-Arg": json.dumps({"cursor": cursor, "close": False})}, data=data, timeout=600)
                _raise(r, "Dropbox upload append")
                offset += len(data)

    def list_backups(self):
        tok = self._token()
        j = self._rpc(tok, "files/list_folder", {"path": self._folder(), "limit": 2000}, ok404=True)
        out = []
        while j:
            out += [(e["name"], e["path_lower"]) for e in j.get("entries", []) if e.get(".tag") == "file" and e["name"].startswith(PREFIX)]
            j = self._rpc(tok, "files/list_folder/continue", {"cursor": j["cursor"]}) if j.get("has_more") else None
        return sorted(out)

    def delete(self, handle: str) -> None:
        self._rpc(self._token(), "files/delete_v2", {"path": handle}, ok404=True)

    def download(self, handle: str, dest: Path) -> None:
        tok = self._token()
        with requests.post(f"{self.CONTENT}/files/download", headers={"Authorization": f"Bearer {tok}", "Dropbox-API-Arg": json.dumps({"path": handle})}, stream=True, timeout=600) as r:
            _raise(r, "Dropbox download")
            with open(dest, "wb") as f:
                for b in r.iter_content(1 << 20):
                    f.write(b)

    @staticmethod
    def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
        from urllib.parse import urlencode

        return "https://www.dropbox.com/oauth2/authorize?" + urlencode(
            {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "token_access_type": "offline", "state": state}
        )

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        r = requests.post(self.TOKEN, data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, auth=(self.config.get("app_key", ""), self.config.get("app_secret", "")), timeout=TIMEOUT)
        _raise(r, "Dropbox code exchange")
        self.config["refresh_token"] = _refresh_token_of(r, "Dropbox")
        self._save(self.config)


# ============================================================ OneDrive
class OneDrive(Target):
    """OneDrive personal or OneDrive for Business / SharePoint through Microsoft
    Graph, with an Entra ID (Azure AD) app registration."""

    type = "onedrive"
    secret_fields = ("client_secret", "refresh_token")
    GRAPH = "https://graph.microsoft.com/v1.0"
    # App folder only (/Apps/<app name>): the token cannot read the rest of the drive.
    SCOPE = "offline_access Files.ReadWrite.AppFolder"
    ROOT = "/me/drive/special/approot"
    CHUNK = 320 * 1024 * 32  # 10 MiB, must be a multiple of 320 KiB

    def _tenant(self) -> str:
        return (self.config.get("tenant") or "common").strip()

    def _token(self) -> str:
        c = self.config
        if not c.get("refresh_token"):
            raise TargetError("OneDrive is not connected yet: click 'Connect OneDrive account'")
        r = requests.post(
            f"https://login.microsoftonline.com/{self._tenant()}/oauth2/v2.0/token",
            data={"client_id": c.get("client_id", ""), "client_secret": c.get("client_secret", ""), "grant_type": "refresh_token", "refresh_token": c["refresh_token"], "scope": self.SCOPE},
            timeout=TIMEOUT,
        )
        _raise(r, "OneDrive token refresh")
        j = r.json()
        if j.get("refresh_token") and j["refresh_token"] != c["refresh_token"]:
            c["refresh_token"] = j["refresh_token"]
            self._save(c)
        return j["access_token"]

    def _folder(self) -> str:
        return (self.config.get("folder") or "ScanVault").strip("/")

    def _item(self, name: str = "") -> str:
        p = "/".join(x for x in (self._folder(), name) if x)
        from urllib.parse import quote

        return f"{self.GRAPH}{self.ROOT}:/{quote(p)}:" if p else f"{self.GRAPH}{self.ROOT}"

    def test(self) -> str:
        h = {"Authorization": f"Bearer {self._token()}"}
        d = requests.get(f"{self.GRAPH}{self.ROOT}", headers=h, timeout=TIMEOUT)
        _raise(d, "OneDrive app folder lookup")
        r = requests.get(self._item(), headers=h, timeout=TIMEOUT)
        if r.status_code == 404:
            parent, _, leaf = self._folder().rpartition("/")
            base = f"{self.GRAPH}{self.ROOT}" + (f":/{parent}:" if parent else "")
            r = requests.post(f"{base}/children", headers=h, json={"name": leaf, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}, timeout=TIMEOUT)
        _raise(r, "OneDrive folder")
        app_dir = d.json().get("name") or "the app folder"
        return f"Connected. Backups go to Apps/{app_dir}/{self._folder()}"

    def upload(self, path: Path, name: str) -> None:
        h = {"Authorization": f"Bearer {self._token()}"}
        r = requests.post(f"{self._item(name)}/createUploadSession", headers=h, json={"item": {"@microsoft.graph.conflictBehavior": "fail"}}, timeout=TIMEOUT)
        _raise(r, "OneDrive upload session")
        url = r.json()["uploadUrl"]
        size = path.stat().st_size
        with open(path, "rb") as f:
            offset = 0
            while offset < size:
                data = f.read(self.CHUNK)
                rr = requests.put(url, headers={"Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}", "Content-Length": str(len(data))}, data=data, timeout=600)
                if rr.status_code not in (200, 201, 202):
                    _raise(rr, "OneDrive upload")
                offset += len(data)
        if size == 0:
            raise TargetError("Refusing to upload an empty backup")

    def list_backups(self):
        h = {"Authorization": f"Bearer {self._token()}"}
        url, out = f"{self._item()}/children?$select=id,name,file&$top=999", []
        while url:
            r = requests.get(url, headers=h, timeout=TIMEOUT)
            if r.status_code == 404:
                return []
            _raise(r, "OneDrive list")
            j = r.json()
            out += [(i["name"], i["id"]) for i in j.get("value", []) if "file" in i and i["name"].startswith(PREFIX)]
            url = j.get("@odata.nextLink")
        return sorted(out)

    def delete(self, handle: str) -> None:
        r = requests.delete(f"{self.GRAPH}/me/drive/items/{handle}", headers={"Authorization": f"Bearer {self._token()}"}, timeout=TIMEOUT)
        if r.status_code not in (204, 404):
            _raise(r, "OneDrive delete")

    def download(self, handle: str, dest: Path) -> None:
        with requests.get(f"{self.GRAPH}/me/drive/items/{handle}/content", headers={"Authorization": f"Bearer {self._token()}"}, stream=True, timeout=600) as r:
            _raise(r, "OneDrive download")
            with open(dest, "wb") as f:
                for b in r.iter_content(1 << 20):
                    f.write(b)

    def auth_url_for(self, redirect_uri: str, state: str) -> str:
        from urllib.parse import urlencode

        return f"https://login.microsoftonline.com/{self._tenant()}/oauth2/v2.0/authorize?" + urlencode(
            {"client_id": self.config.get("client_id", ""), "redirect_uri": redirect_uri, "response_type": "code", "scope": self.SCOPE, "state": state, "prompt": "select_account"}
        )

    def exchange_code(self, code: str, redirect_uri: str) -> None:
        r = requests.post(
            f"https://login.microsoftonline.com/{self._tenant()}/oauth2/v2.0/token",
            data={"client_id": self.config.get("client_id", ""), "client_secret": self.config.get("client_secret", ""), "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "scope": self.SCOPE},
            timeout=TIMEOUT,
        )
        _raise(r, "OneDrive code exchange")
        if not r.json().get("refresh_token"):
            raise TargetError("OneDrive did not return a refresh token (is offline_access allowed?)")
        self.config["refresh_token"] = r.json()["refresh_token"]
        self._save(self.config)


# ============================================================ WebDAV / Nextcloud
class _PinnedAdapter(requests.adapters.HTTPAdapter):
    """Accept exactly one (self-signed) certificate, identified by its SHA-256
    fingerprint. urllib3 checks it during the TLS handshake, before any
    request (or password) is sent."""

    def __init__(self, fingerprint: str, **kw):
        self._fp = fingerprint.replace(":", "").lower()
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["assert_fingerprint"] = self._fp
        kw["cert_reqs"] = "CERT_NONE"
        return super().init_poolmanager(*a, **kw)


def _cert_fingerprint(url: str) -> str:
    import ssl
    from urllib.parse import urlparse

    u = urlparse(url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=15)
    der = ssl.PEM_cert_to_DER_cert(pem)
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i : i + 2] for i in range(0, len(h), 2))


class WebDAV(Target):
    """Any WebDAV server over HTTPS: Nextcloud, ownCloud, Synology WebDAV
    Server, QNAP, Apache/nginx, Koofr, pCloud, Seafile, 4shared...

    A self-signed certificate is pinned by fingerprint on the first Test (like
    an SSH host key) instead of turning certificate checks off."""

    type = "webdav"
    secret_fields = ("password",)
    location_fields = ("url",)

    @classmethod
    def validate(cls, config: dict) -> None:
        from urllib.parse import urlparse

        url = (config.get("url") or "").strip()
        if not url:
            return
        u = urlparse(url)
        if u.scheme != "https":
            raise TargetError("WebDAV URL must start with https:// (the password would otherwise be sent in clear)")
        if u.username or u.password or "@" in u.netloc:
            raise TargetError("Put the user name and password in their own fields, not in the URL")
        if not u.hostname:
            raise TargetError("WebDAV URL is missing the server name")

    def _base(self) -> str:
        self.validate(self.config)
        url = (self.config.get("url") or "").rstrip("/")
        if not url:
            raise TargetError("Enter the WebDAV URL")
        folder = (self.config.get("folder") or "").strip("/")
        return f"{url}/{folder}" if folder else url

    def _auth(self):
        return (self.config.get("username", ""), self.config.get("password", ""))

    def _session(self) -> requests.Session:
        s = requests.Session()
        fp = (self.config.get("tls_fingerprint") or "").strip()
        if self.config.get("self_signed") or self.config.get("insecure_tls"):
            if not fp:
                fp = _cert_fingerprint(self._base())
                self.config["tls_fingerprint"] = fp
                self.config.pop("insecure_tls", None)
                self.config["self_signed"] = True
                self._save(self.config)
            s.mount("https://", _PinnedAdapter(fp))
            s.verify = False  # the CA chain is replaced by the exact fingerprint check
        return s

    def _req(self, method, url, **kw):
        try:
            with self._session() as s:
                # verify passed per request: requests replaces a session-level False with REQUESTS_CA_BUNDLE
                return s.request(method, url, auth=self._auth(), allow_redirects=False, verify=s.verify, timeout=kw.pop("timeout", TIMEOUT), **kw)
        except requests.exceptions.SSLError as exc:
            if "fingerprint" in str(exc).lower():
                raise TargetError("The WebDAV server's certificate changed since it was pinned. Possible MITM; refusing to connect. "
                                  "If you replaced the certificate on purpose, clear the pinned fingerprint and Test again.") from exc
            raise TargetError("The WebDAV server's certificate is not trusted. If it is self-signed, tick 'self-signed certificate' to pin it.") from exc
        except requests.RequestException as exc:
            raise TargetError(f"WebDAV {method} failed: {exc.__class__.__name__}") from exc

    @staticmethod
    def _check(r: requests.Response, what: str, ok: tuple[int, ...]) -> None:
        if r.status_code not in ok:
            # Only the status is reported: the body of an arbitrary URL is not echoed back.
            raise TargetError(f"{what} failed (HTTP {r.status_code})")

    def _mkdirs(self) -> None:
        url = (self.config.get("url") or "").rstrip("/")
        for part in [p for p in (self.config.get("folder") or "").strip("/").split("/") if p]:
            url += "/" + requests.utils.quote(part)
            r = self._req("MKCOL", url + "/")
            self._check(r, "WebDAV create folder", (201, 405))  # 405 = exists

    def test(self) -> str:
        self._mkdirs()
        r = self._req("PROPFIND", self._base() + "/", headers={"Depth": "0"})
        self._check(r, "WebDAV folder check", (207,))
        probe = self._base() + "/.scanvault-write-test"
        self._check(self._req("PUT", probe, data=b"ok"), "WebDAV write test", (200, 201, 204))
        self._req("DELETE", probe)
        pin = self.config.get("tls_fingerprint")
        return f"Connected. Backups go to {self._base()}/" + (f" Certificate pinned: {pin}" if pin and self.config.get("self_signed") else "")

    def upload(self, path: Path, name: str) -> None:
        self._mkdirs()
        tmp = f"{self._base()}/{name}.part"
        with open(path, "rb") as f:
            r = self._req("PUT", tmp, data=f, headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)}, timeout=3600)
        self._check(r, "WebDAV upload", (200, 201, 204))
        r = self._req("MOVE", tmp, headers={"Destination": f"{self._base()}/{name}", "Overwrite": "F"})
        if r.status_code not in (201, 204):
            self._req("DELETE", tmp)
            self._check(r, "WebDAV rename", (201, 204))

    def list_backups(self):
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote, urlparse

        r = self._req("PROPFIND", self._base() + "/", headers={"Depth": "1", "Content-Type": "application/xml"}, stream=True,
                      data=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>')
        if r.status_code == 404:
            return []
        self._check(r, "WebDAV list", (207,))
        body = bytearray()
        for b in r.iter_content(1 << 16):
            body += b
            if len(body) > MAX_LISTING:
                raise TargetError("WebDAV folder listing is too large (keep the backup folder for backups only)")
        out = []
        try:
            root = ET.fromstring(bytes(body))
        except ET.ParseError as exc:
            raise TargetError("WebDAV list returned something that is not WebDAV XML") from exc
        for resp in root.iter("{DAV:}response"):
            href = resp.findtext("{DAV:}href") or ""
            name = unquote(urlparse(href).path.rstrip("/").rsplit("/", 1)[-1])
            if name.startswith(PREFIX) and name.endswith(SUFFIX):
                out.append((name, name))
        return sorted(out)

    def delete(self, handle: str) -> None:
        _plain_name(handle)
        r = self._req("DELETE", f"{self._base()}/{handle}")
        self._check(r, "WebDAV delete", (200, 204, 404))

    def download(self, handle: str, dest: Path) -> None:
        _plain_name(handle)
        r = self._req("GET", f"{self._base()}/{handle}", stream=True, timeout=3600)
        self._check(r, "WebDAV download", (200,))
        with open(dest, "wb") as f:
            for b in r.iter_content(1 << 20):
                f.write(b)


# ============================================================ SMB / Windows share
class SMB(Target):
    """SMB2/3 share: Windows, Synology/QNAP/TrueNAS shares, Samba. SMB3
    encryption is required unless it is explicitly turned off."""

    type = "smb"
    secret_fields = ("password",)
    location_fields = ("host",)

    def _root(self) -> str:
        c = self.config
        host, share = (c.get("host") or "").strip(), (c.get("share") or "").strip("\\/ ")
        if not host or not share:
            raise TargetError("Server and share name are required")
        folder = (c.get("folder") or "").strip("\\/").replace("/", "\\")
        if ".." in folder.split("\\"):
            raise TargetError("The folder can't contain '..'")
        return f"\\\\{host}\\{share}" + (f"\\{folder}" if folder else "")

    def _session(self):
        import smbclient

        c = self.config
        try:
            smbclient.register_session(
                c["host"].strip(), username=c.get("username") or None, password=c.get("password") or None,
                port=int(c.get("port") or 445), encrypt=c.get("require_encryption") is not False, connection_timeout=20,
            )
        except Exception as exc:
            raise TargetError(f"SMB connection failed: {exc}") from exc
        return smbclient

    def _wrap(self, what, fn):
        try:
            return fn()
        except TargetError:
            raise
        except Exception as exc:
            raise TargetError(f"SMB {what} failed: {exc}") from exc

    def test(self) -> str:
        sc = self._session()
        root = self._root()
        self._wrap("create folder", lambda: sc.makedirs(root, exist_ok=True))
        probe = root + "\\.scanvault-write-test"

        def w():
            with sc.open_file(probe, mode="wb") as f:
                f.write(b"ok")
            sc.remove(probe)

        self._wrap("write test", w)
        enc = "encrypted (SMB3)" if self.config.get("require_encryption") is not False else "NOT encrypted in transit"
        return f"Connected, {enc}. Backups go to {root}"

    def upload(self, path: Path, name: str) -> None:
        sc = self._session()
        root = self._root()

        def up():
            sc.makedirs(root, exist_ok=True)
            tmp, dst = f"{root}\\{name}.part", f"{root}\\{name}"
            try:
                with open(path, "rb") as src, sc.open_file(tmp, mode="wb") as out:
                    shutil.copyfileobj(src, out, 4 * 1024 * 1024)
                sc.rename(tmp, dst)
            except Exception:
                try:
                    sc.remove(tmp)
                except Exception:
                    pass
                raise

        self._wrap("upload", up)

    def list_backups(self):
        sc = self._session()
        try:
            names = sc.listdir(self._root())
        except FileNotFoundError:
            return []
        except Exception as exc:
            raise TargetError(f"SMB list failed: {exc}") from exc
        return sorted((n, n) for n in names if n.startswith(PREFIX) and n.endswith(SUFFIX))

    def delete(self, handle: str) -> None:
        _plain_name(handle)
        sc = self._session()
        self._wrap("delete", lambda: sc.remove(f"{self._root()}\\{handle}"))

    def download(self, handle: str, dest: Path) -> None:
        _plain_name(handle)
        sc = self._session()

        def dl():
            with sc.open_file(f"{self._root()}\\{handle}", mode="rb") as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 4 * 1024 * 1024)

        self._wrap("download", dl)


# ============================================================ Local folder (USB / NFS / mounted disk)
class LocalFolder(Target):
    """A folder inside the container under /backups: mount a USB disk, a second
    internal drive or an NFS/CIFS mount from the host there (see
    docker-compose). Paths outside that root are refused."""

    type = "local"
    secret_fields = ()

    @staticmethod
    def root() -> Path:
        return Path(os.environ.get("SCANVAULT_LOCAL_BACKUP_ROOT", "/backups")).resolve()

    @classmethod
    def validate(cls, config: dict) -> None:
        cls._resolve(config.get("path"))

    @classmethod
    def _resolve(cls, raw) -> Path:
        root = cls.root()
        p = Path(str(raw or root)).expanduser()
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if p != root and root not in p.parents:
            raise TargetError(f"Backups must go to a folder under {root} (mount the disk there)")
        data = Path(os.environ.get("SCANVAULT_DATA", "/data")).resolve()
        if p == data or data in p.parents or p in data.parents:
            raise TargetError("Pick a folder outside the vault's own data folder")
        return p

    def _dir(self) -> Path:
        return self._resolve(self.config.get("path"))

    def test(self) -> str:
        d = self._dir()
        # Default on: without a mounted disk the "backup" would land on the
        # container's own filesystem and silently fill the NAS system drive.
        if self.config.get("require_mount") is not False and _mount_root(d) == Path("/"):
            raise TargetError(f"{d} is not on a mounted disk. Is the USB drive plugged in and mounted into the container?")
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".scanvault-write-test"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise TargetError(f"Can't write to {d}: {exc.strerror or exc}") from exc
        free = shutil.disk_usage(d).free
        return f"Writable. {free / 1e9:,.1f} GB free in {d}"

    def upload(self, path: Path, name: str) -> None:
        self.test()
        d = self._dir()
        tmp = d / f"{name}.part"
        try:
            shutil.copyfile(path, tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            tmp.replace(d / name)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise TargetError(f"Copy to {d} failed: {exc.strerror or exc}") from exc

    def list_backups(self):
        d = self._dir()
        if not d.exists():
            return []
        return sorted((p.name, p.name) for p in d.iterdir() if p.is_file() and p.name.startswith(PREFIX) and p.name.endswith(SUFFIX))

    def delete(self, handle: str) -> None:
        _plain_name(handle)
        (self._dir() / handle).unlink(missing_ok=True)

    def download(self, handle: str, dest: Path) -> None:
        _plain_name(handle)
        shutil.copyfile(self._dir() / handle, dest)


def _mount_root(p: Path) -> Path:
    p = p if p.exists() else p.parent
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


TYPES: dict[str, type[Target]] = {
    "gdrive": GoogleDrive,
    "box": Box,
    "dropbox": Dropbox,
    "onedrive": OneDrive,
    "s3": S3,
    "webdav": WebDAV,
    "sftp": SSHTarget,
    "smb": SMB,
    "local": LocalFolder,
}

OAUTH_TYPES = {"gdrive", "box", "dropbox", "onedrive"}


def oauth_client_id(type_: str, cfg: dict) -> str:
    return cfg.get("app_key" if type_ == "dropbox" else "client_id", "")


def oauth_auth_url(type_: str, cfg: dict, redirect_uri: str, state: str) -> str:
    if type_ == "onedrive":
        return OneDrive(cfg).auth_url_for(redirect_uri, state)
    return TYPES[type_].auth_url(oauth_client_id(type_, cfg), redirect_uri, state)  # type: ignore[attr-defined]


def needs_connect(type_: str, cfg: dict) -> bool:
    if type_ not in OAUTH_TYPES:
        return False
    mode = cfg.get("auth_mode", "oauth")
    return mode == "oauth" and not cfg.get("refresh_token")


def make(type_: str, config: dict, save: Callable[[dict], None] | None = None, vault_id: str = "") -> Target:
    if type_ not in TYPES:
        raise TargetError(f"Unknown target type {type_}")
    return TYPES[type_](config, save, vault_id)
