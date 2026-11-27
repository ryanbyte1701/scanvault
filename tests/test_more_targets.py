"""S3 (moto), Dropbox and OneDrive (mocked HTTP), WebDAV (real WsgiDAV server),
SMB (smbclient faked over a temp dir) and local folder targets."""
import json
import os
import threading
from pathlib import Path

import pytest
import responses

from app import targets

NAMES = [f"scanvault-backup-2026010{i}-000000Z.svb" for i in range(4)]


def _roundtrip(t, tmp_path, keep=2):
    src = tmp_path / "a.svb"
    src.write_bytes(os.urandom(300_000))
    for n in NAMES:
        t.upload(src, n)
    assert [n for n, _ in t.list_backups()] == NAMES
    assert t.prune(keep) == len(NAMES) - keep
    left = t.list_backups()
    assert [n for n, _ in left] == NAMES[-keep:]
    dst = tmp_path / "dl.svb"
    t.download(left[-1][1], dst)
    assert dst.read_bytes() == src.read_bytes()


# ------------------------------------------------------------------ S3
def test_s3_moto(tmp_path, monkeypatch):
    from moto import mock_aws
    import boto3

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vault")
        t = targets.make("s3", {"provider": "aws", "bucket": "vault", "prefix": "nas/scanvault", "access_key_id": "AK", "secret_access_key": "SK", "region": "us-east-1"})
        assert "s3://vault/nas/scanvault/" in t.test()
        _roundtrip(t, tmp_path)
        keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="vault")["Contents"]]
        assert all(k.startswith("nas/scanvault/scanvault-backup-") for k in keys)
        bad = targets.make("s3", {"provider": "aws", "bucket": "nope", "access_key_id": "AK", "secret_access_key": "SK"})
        with pytest.raises(targets.TargetError):
            bad.test()


def test_s3_provider_endpoints():
    for prov, region, extra, expect in [
        ("b2", "us-west-004", {}, "https://s3.us-west-004.backblazeb2.com"),
        ("wasabi", "eu-central-1", {}, "https://s3.eu-central-1.wasabisys.com"),
        ("r2", "", {"account_id": "abc123"}, "https://abc123.r2.cloudflarestorage.com"),
        ("c2", "us-001", {}, "https://us-001.s3.synologyc2.net"),
        ("minio", "", {"endpoint_url": "https://nas.local:9000"}, "https://nas.local:9000"),
    ]:
        cl = targets.make("s3", {"provider": prov, "region": region, "bucket": "b", "access_key_id": "a", "secret_access_key": "s", **extra})._client()
        assert cl.meta.endpoint_url == expect, prov
    # an http endpoint saved before https was required is refused when used
    with pytest.raises(targets.TargetError, match="https"):
        targets.make("s3", {"provider": "minio", "endpoint_url": "http://nas.local:9000", "bucket": "b", "access_key_id": "a", "secret_access_key": "s"})._client()
    # a region can't smuggle in another host
    with pytest.raises(targets.TargetError):
        targets.make("s3", {"provider": "b2", "region": "x.evil.example/", "bucket": "b", "access_key_id": "a", "secret_access_key": "s"})._client()


# ------------------------------------------------------------------ Dropbox
@responses.activate
def test_dropbox_chunked_upload_list_prune(tmp_path):
    responses.post("https://api.dropboxapi.com/oauth2/token", json={"access_token": "AT"})
    responses.post("https://content.dropboxapi.com/2/files/upload_session/start", json={"session_id": "S1"})
    responses.post("https://content.dropboxapi.com/2/files/upload_session/append_v2", json=None)
    responses.post("https://content.dropboxapi.com/2/files/upload_session/finish", json={"name": "x"})
    entries = [{".tag": "file", "name": n, "path_lower": "/scanvault/" + n} for n in NAMES] + [{".tag": "folder", "name": "other", "path_lower": "/scanvault/other"}]
    responses.post("https://api.dropboxapi.com/2/files/list_folder", json={"entries": entries[:2], "cursor": "C", "has_more": True})
    responses.post("https://api.dropboxapi.com/2/files/list_folder/continue", json={"entries": entries[2:], "cursor": "C", "has_more": False})
    responses.post("https://api.dropboxapi.com/2/files/delete_v2", json={})
    t = targets.make("dropbox", {"app_key": "k", "app_secret": "s", "refresh_token": "R", "folder": "ScanVault"})
    p = tmp_path / "big.svb"
    with open(p, "wb") as f:
        f.truncate(200 * 1024 * 1024)
    t.upload(p, NAMES[0])
    fin = [c for c in responses.calls if c.request.url.endswith("/finish")][0]
    arg = json.loads(fin.request.headers["Dropbox-API-Arg"])
    assert arg["commit"]["path"] == "/ScanVault/" + NAMES[0] and arg["cursor"]["offset"] == 192 * 1024 * 1024
    assert t.prune(2) == 2
    dels = [json.loads(c.request.body)["path"] for c in responses.calls if c.request.url.endswith("/delete_v2")]
    assert dels == ["/scanvault/" + NAMES[0], "/scanvault/" + NAMES[1]]


def test_dropbox_needs_connect():
    assert targets.needs_connect("dropbox", {"app_key": "k"})
    assert "token_access_type=offline" in targets.oauth_auth_url("dropbox", {"app_key": "k"}, "https://x/cb", "st")


# ------------------------------------------------------------------ OneDrive
@responses.activate
def test_onedrive_upload_session_and_token_rotation(tmp_path):
    responses.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", json={"access_token": "AT", "refresh_token": "R2"})
    responses.post("https://graph.microsoft.com/v1.0/me/drive/special/approot:/Backups/ScanVault/" + NAMES[0] + ":/createUploadSession", json={"uploadUrl": "https://up.example/s"})
    responses.put("https://up.example/s", status=202, json={})
    responses.put("https://up.example/s", status=201, json={"id": "F"})
    responses.get("https://graph.microsoft.com/v1.0/me/drive/special/approot:/Backups/ScanVault:/children", json={"value": [{"id": f"id{i}", "name": n, "file": {}} for i, n in enumerate(NAMES)]})
    for i in range(3):
        responses.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/id{i}", status=204)
    saved = {}
    t = targets.make("onedrive", {"client_id": "c", "client_secret": "s", "refresh_token": "R1", "tenant": "consumers", "folder": "Backups/ScanVault"}, saved.update)
    p = tmp_path / "b.svb"
    p.write_bytes(b"\1" * (12 * 1024 * 1024))
    t.upload(p, NAMES[0])
    assert saved["refresh_token"] == "R2"
    puts = [c for c in responses.calls if c.request.method == "PUT"]
    assert puts[0].request.headers["Content-Range"] == f"bytes 0-{10 * 1024 * 1024 - 1}/{12 * 1024 * 1024}"
    assert t.prune(1) == 3
    assert "login.microsoftonline.com/consumers/" in targets.oauth_auth_url("onedrive", {"client_id": "c", "tenant": "consumers"}, "https://x/cb", "s")


# ------------------------------------------------------------------ WebDAV (real server over TLS)
def _self_signed(tmp_path, cn="127.0.0.1"):
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(cn))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    c, k = tmp_path / f"cert-{os.urandom(3).hex()}.pem", tmp_path / f"key-{os.urandom(3).hex()}.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return c, k


def _dav(root, cert, key, port=0):
    from cheroot import wsgi
    from cheroot.ssl.builtin import BuiltinSSLAdapter
    from wsgidav.wsgidav_app import WsgiDAVApp

    app = WsgiDAVApp({
        "provider_mapping": {"/remote.php/dav/files/ryan": str(root)},
        "simple_dc": {"user_mapping": {"*": {"ryan": {"password": "pw"}}}},
        "http_authenticator": {"accept_basic": True, "accept_digest": False, "default_to_digest": False},
        "verbose": 0, "logging": {"enable": False},
    })
    srv = wsgi.Server(("127.0.0.1", port), app)
    srv.ssl_adapter = BuiltinSSLAdapter(str(cert), str(key))
    srv.prepare()
    threading.Thread(target=srv.serve, daemon=True).start()
    return srv


@pytest.fixture()
def dav_server(tmp_path):
    root = tmp_path / "dav"
    root.mkdir()
    cert, key = _self_signed(tmp_path)
    srv = _dav(root, cert, key)
    info = {"root": root, "srv": srv, "port": srv.bind_addr[1]}
    yield f"https://127.0.0.1:{srv.bind_addr[1]}/remote.php/dav/files/ryan", info
    info["srv"].stop()


def test_webdav_nextcloud_style(tmp_path, dav_server):
    url, info = dav_server
    root = info["root"]
    saved = {}
    cfg = {"url": url, "username": "ryan", "password": "pw", "folder": "Backups/ScanVault"}
    # an untrusted self-signed certificate is refused unless the user opts in to pinning it
    with pytest.raises(targets.TargetError, match="not trusted"):
        targets.make("webdav", dict(cfg)).test()
    t = targets.make("webdav", {**cfg, "self_signed": True}, saved.update)
    msg = t.test()
    assert "Connected" in msg and "pinned" in msg and saved["tls_fingerprint"].count(":") == 31
    _roundtrip(t, tmp_path)
    assert sorted(p.name for p in (root / "Backups" / "ScanVault").iterdir()) == NAMES[-2:]
    bad = targets.make("webdav", {**cfg, "self_signed": True, "tls_fingerprint": saved["tls_fingerprint"], "password": "nope"})
    with pytest.raises(targets.TargetError, match=r"HTTP 401"):
        bad.test()
    # the server's certificate changes (MITM): refused before the password is sent
    info["srv"].stop()
    cert2, key2 = _self_signed(tmp_path)
    info["srv"] = _dav(root, cert2, key2, info["port"])
    pinned = targets.make("webdav", {**cfg, "self_signed": True, "tls_fingerprint": saved["tls_fingerprint"]})
    with pytest.raises(targets.TargetError, match="changed"):
        pinned.test()


def test_webdav_rejects_cleartext_and_userinfo():
    with pytest.raises(targets.TargetError, match="https"):
        targets.WebDAV.validate({"url": "http://nas.local/dav"})
    with pytest.raises(targets.TargetError, match="own fields"):
        targets.WebDAV.validate({"url": "https://ryan:pw@nas.local/dav"})
    targets.WebDAV.validate({"url": "https://nas.local/dav"})


# ------------------------------------------------------------------ SMB (smbclient faked)
class FakeSMB:
    def __init__(self, root):
        self.root, self.sessions = Path(root), []

    def _p(self, unc):
        parts = unc.lstrip("\\").split("\\")
        return self.root.joinpath(*parts)

    def register_session(self, host, **kw):
        if kw.get("password") != "pw":
            raise PermissionError("STATUS_LOGON_FAILURE")
        self.sessions.append((host, kw))

    def makedirs(self, p, exist_ok=False):
        self._p(p).mkdir(parents=True, exist_ok=exist_ok)

    def open_file(self, p, mode="rb"):
        return open(self._p(p), mode)

    def remove(self, p):
        self._p(p).unlink()

    def rename(self, a, b):
        self._p(a).rename(self._p(b))

    def listdir(self, p):
        q = self._p(p)
        if not q.exists():
            raise FileNotFoundError(p)
        return os.listdir(q)


def test_smb(tmp_path, monkeypatch):
    import sys
    import types

    fake = FakeSMB(tmp_path / "smb")
    mod = types.ModuleType("smbclient")
    for name in ("register_session", "makedirs", "open_file", "remove", "rename", "listdir"):
        setattr(mod, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "smbclient", mod)
    t = targets.make("smb", {"host": "nas.local", "share": "backup", "folder": "scanvault/nightly", "username": "u", "password": "pw", "require_encryption": True})
    assert "\\\\nas.local\\backup\\scanvault\\nightly" in t.test()
    _roundtrip(t, tmp_path)
    assert fake.sessions[0][1]["encrypt"] is True
    assert not list((tmp_path / "smb").rglob("*.part"))
    with pytest.raises(targets.TargetError, match="LOGON"):
        targets.make("smb", {"host": "nas.local", "share": "backup", "password": "bad"}).test()


# ------------------------------------------------------------------ local folder
def test_local_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANVAULT_LOCAL_BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setattr(targets, "_mount_root", lambda p: tmp_path)  # pretend a disk is mounted
    t = targets.make("local", {"path": "usb1/scanvault"})
    assert "free" in t.test()
    _roundtrip(t, tmp_path)
    assert (tmp_path / "backups" / "usb1" / "scanvault" / NAMES[-1]).exists()
    # confined to the backup root
    for bad in ("/etc", str(tmp_path), "../../x", str(tmp_path / "backups" / ".." / "elsewhere")):
        with pytest.raises(targets.TargetError, match="under"):
            targets.LocalFolder.validate({"path": bad})
    with pytest.raises(targets.TargetError, match="Not a ScanVault backup"):
        t.download("../../etc/passwd", tmp_path / "x")
    # require_mount is on unless explicitly turned off
    monkeypatch.setattr(targets, "_mount_root", lambda p: Path("/"))  # e.g. USB disk not mounted
    with pytest.raises(targets.TargetError, match="mounted"):
        targets.make("local", {"path": "nomount"}).test()
    assert "free" in targets.make("local", {"path": "nomount", "require_mount": False}).test()
