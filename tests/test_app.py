import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import ROOT, SETUP, make_dicom

from app import main  # noqa: E402

H = {"X-ScanVault": "1"}
PASS = "orbit-cedar-lantern-quill"


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def setup(client):
    r = client.post("/api/setup", json={"username": "ryan", "password": "pw-1234567890", "passphrase": PASS}, headers=SETUP)
    assert r.status_code == 200, r.text
    return r.json()


def png_bytes():
    b = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(b, "PNG")
    return b.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_full_flow(client, setup, tmp_path, data_dir):
    assert "BEGIN SCANVAULT RECOVERY KEY" in setup["recovery_key"]
    tok = setup["token"]
    B = {"Authorization": f"Bearer {tok}"}

    # CSRF: cookie-only POST without header is rejected
    client.cookies.set("sv_session", tok)
    assert client.post("/api/patients", json={"name": "x"}).status_code == 403
    client.cookies.clear()

    st = client.get("/api/status", headers=B).json()
    assert st["setup_complete"] and st["unlocked"] and st["authenticated"]

    p = client.post("/api/patients", json={"name": "Jane Doe", "date_of_birth": "1980-04-02", "mrn": "MRN123"}, headers=B).json()
    p2 = client.post("/api/patients", json={"name": "Sam Doe"}, headers=B).json()
    assert client.post("/api/patients", json={}, headers=B).status_code == 400

    # PHI never stored in plaintext
    db = (data_dir / "scanvault.db").read_bytes()
    assert b"Jane Doe" not in db and b"MRN123" not in db

    # import: zip of 3 DICOMs (CD export) + a PDF + a PNG
    zpath = tmp_path / "cd.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for i in range(1, 4):
            d = make_dicom(tmp_path / f"IM{i}", instance=i)
            z.write(d, f"DICOM/IM{i}")
        z.writestr("DICOMDIR", b"junk")
        z.writestr("autorun.inf", b"junk")
    files = [
        ("file", ("cd.zip", zpath.read_bytes(), "application/zip")),
        ("file", ("report.pdf", PDF, "application/pdf")),
        ("file", ("film.png", png_bytes(), "image/png")),
    ]
    r = client.post(f"/api/patients/{p['id']}/import", files=files, headers=B)
    assert r.status_code == 200, r.text
    res = r.json()
    assert len(res["files"]) == 5
    assert "DICOMDIR" in res["skipped"]
    kinds = sorted(f["kind"] for f in res["files"])
    assert kinds == ["dicom", "dicom", "dicom", "image", "report"]
    studies = client.get(f"/api/patients/{p['id']}/studies", headers=B).json()
    dstudy = [s for s in studies if s["study_uid"] == "1.2.3.4.5"][0]
    assert dstudy["file_count"] == 5 and dstudy["modality"] == "MR" and dstudy["facility"] == "Lakeside Imaging"
    assert dstudy["study_date"] == "2026-03-14"

    # duplicate upload is de-duplicated
    r = client.post(f"/api/studies/{dstudy['id']}/files", files=[("file", ("IM1", (tmp_path / "IM1").read_bytes()))], headers=B)
    assert r.json()[0].get("duplicate")

    # contract: list files, content round trip, thumbnail
    fl = client.get(f"/api/studies/{dstudy['id']}/files", headers=B).json()
    assert [f["kind"] for f in fl] == ["report", "image", "dicom", "dicom", "dicom"]
    assert [f["dicom"]["instance_number"] for f in fl[2:]] == ["1", "2", "3"]
    f0 = fl[2]
    assert set(["id", "study_id", "filename", "content_type", "size_bytes", "kind", "uploaded_at"]) <= set(f0)
    c = client.get(f"/api/files/{f0['id']}/content", headers=B)
    assert c.content == (tmp_path / "IM1").read_bytes()
    assert c.headers["content-disposition"].startswith("attachment")  # dicom is never rendered inline
    t = client.get(f"/api/files/{f0['id']}/thumbnail", headers=B)
    assert t.status_code == 200 and t.content[:3] == b"\xff\xd8\xff"
    # blob on disk is ciphertext
    blob = (data_dir / "files" / f0["id"][:2] / f"{f0['id']}.enc").read_bytes()
    assert b"DICM" not in blob and b"DOE^JANE" not in blob

    pdf = [f for f in res["files"] if f["kind"] == "report"][0]
    c = client.get(f"/api/files/{pdf['id']}/content", headers=B)
    assert c.content == PDF and c.headers["content-type"] == "application/pdf"

    # Android contract: create study and upload into it
    s = client.post(f"/api/patients/{p2['id']}/studies", json={"title": "Chest X-ray", "modality": "DX", "study_date": "2025-11-02", "facility": "City Hospital"}, headers=B).json()
    r = client.post(f"/api/studies/{s['id']}/files", files=[("file", ("cxr.png", png_bytes(), "image/png"))], headers=B)
    assert r.status_code == 200 and r.json()[0]["kind"] == "image"
    pl = client.get("/api/patients", headers=B).json()
    assert [x["name"] for x in pl] == ["Jane Doe", "Sam Doe"]
    assert pl[0]["study_count"] == 1 and pl[0]["file_count"] == 5

    # lock / unlock
    assert client.post("/api/vault/lock", headers=B).status_code == 200
    r = client.get("/api/patients", headers=B)
    assert r.status_code == 423 and r.json()["code"] == "locked"
    assert client.post("/api/vault/unlock", json={"passphrase": "nope"}, headers=B).status_code == 400
    assert client.post("/api/vault/unlock", json={"recovery_key": setup["recovery_key"]}, headers=B).json()["unlocked"]

    # recovery key re-download requires passphrase
    assert client.post("/api/vault/recovery-key", json={"passphrase": "bad"}, headers=B).status_code == 400
    rk = client.post("/api/vault/recovery-key", json={"passphrase": PASS}, headers=B).text
    assert rk.split("-----BEGIN")[1] == setup["recovery_key"].split("-----BEGIN")[1]

    # change passphrase keeps data
    assert client.post("/api/vault/passphrase", json={"current": PASS, "new": "new-passphrase-here"}, headers=B).status_code == 200
    client.post("/api/vault/lock", headers=B)
    assert client.post("/api/vault/unlock", json={"passphrase": "new-passphrase-here"}, headers=B).status_code == 200
    client.post("/api/vault/passphrase", json={"current": "new-passphrase-here", "new": PASS}, headers=B)

    # local backup download
    r = client.get("/api/backups/download", headers=B)
    assert r.status_code == 200 and r.content[:4] == b"SVB1"
    assert b"Jane" not in r.content
    svb = tmp_path / "b.svb"
    svb.write_bytes(r.content)
    keyf = tmp_path / "key.txt"
    keyf.write_text(setup["recovery_key"])

    # emergency restore tool (no app running)
    out = tmp_path / "restored"
    cp = subprocess.run([sys.executable, str(ROOT / "tools" / "restore.py"), "--backup", str(svb), "--key", str(keyf), "--out", str(out)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    restored = list(out.rglob("*"))
    assert any(x.name == "report.pdf" and x.read_bytes() == PDF for x in restored)
    assert sum(1 for x in restored if x.name.startswith("IM")) == 3
    assert (out / "patients.json").exists()

    # wrong key refused
    other = tmp_path / "other.txt"
    from app import crypto
    other.write_text(crypto.recovery_file_text(crypto.new_master_key(), "X", ""))
    cp = subprocess.run([sys.executable, str(ROOT / "tools" / "restore.py"), "--backup", str(svb), "--key", str(other), "--out", str(tmp_path / "x")], capture_output=True, text=True)
    assert cp.returncode != 0 and "does not match" in cp.stderr

    # restore over existing: delete a patient, restore, it's back
    client.delete(f"/api/patients/{p2['id']}", headers=B)
    assert len(client.get("/api/patients", headers=B).json()) == 1
    r = client.post("/api/backups/restore", files={"backup_file": ("b.svb", svb.read_bytes())}, data={"confirm": "REPLACE"}, headers=B)
    assert r.status_code == 200, r.text
    assert len(client.get("/api/patients", headers=B).json()) == 2

    # backup target CRUD hides secrets
    t = client.post("/api/backups/targets", json={"type": "sftp", "name": "NAS", "config": {"host": "127.0.0.1", "port": 1, "username": "u", "password": "s3cret"}}, headers=B).json()
    assert "password" not in t["config"] and t["secrets_set"]["password"]
    t = client.put(f"/api/backups/targets/{t['id']}", json={"config": {"password": ""}}, headers=B).json()
    assert t["secrets_set"]["password"]
    r = client.post(f"/api/backups/targets/{t['id']}/run", headers=B).json()
    assert r["results"][0]["status"] == "error"
    runs = client.get("/api/backups/runs", headers=B).json()
    assert runs[0]["status"] == "error"


def test_fresh_install_restore(tmp_path, client, setup):
    """Simulate losing the server: new data dir, restore with recovery file."""
    B = {"Authorization": f"Bearer {setup['token']}"}
    svb = client.get("/api/backups/download", headers=B).content
    from app.store import Store
    old_store = main.store
    main.store = Store(tmp_path / "fresh")
    main.vault.lock()
    try:
        assert not client.get("/api/status").json()["setup_complete"]
        r = client.post(
            "/api/setup/restore",
            files={"backup_file": ("b.svb", svb)},
            data={"username": "newadmin", "password": "pw-0987654321", "recovery_key": setup["recovery_key"]},
            headers=SETUP,
        )
        assert r.status_code == 200, r.text
        assert r.json()["patients"] == 2
        tok = r.json()["token"]
        pl = client.get("/api/patients", headers={"Authorization": f"Bearer {tok}"}).json()
        assert {p["name"] for p in pl} == {"Jane Doe", "Sam Doe"}
        # and the old passphrase still unlocks after restart
        main.vault.lock()
        assert client.post("/api/vault/unlock", json={"passphrase": PASS}, headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    finally:
        main.store = old_store


def test_render_window_and_prefs(client, setup, tmp_path):
    B = {"Authorization": f"Bearer {setup['token']}"}
    p = client.post("/api/patients", json={"name": "Render Test"}, headers=B).json()
    d = make_dicom(tmp_path / "R1", study_uid="9.9.9", modality="CT")
    f = client.post(f"/api/patients/{p['id']}/import", files=[("file", ("R1", d.read_bytes()))], headers=B).json()["files"][0]
    r = client.get(f"/api/files/{f['id']}/render", headers=B)
    assert r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff"
    assert float(r.headers["x-default-width"]) > 0 and r.headers["x-modality"] == "CT"
    import io as _io
    from PIL import Image as _Im
    # narrow window centred far below the disc's value -> almost all white; far above -> black
    lo = _Im.open(_io.BytesIO(client.get(f"/api/files/{f['id']}/render?wc=-5000&ww=10", headers=B).content))
    hi = _Im.open(_io.BytesIO(client.get(f"/api/files/{f['id']}/render?wc=99999&ww=10", headers=B).content))
    assert sum(lo.getdata()) / (lo.width * lo.height) > 250 and sum(hi.getdata()) / (hi.width * hi.height) < 5
    inv = _Im.open(_io.BytesIO(client.get(f"/api/files/{f['id']}/render?wc=-5000&ww=10&invert=1", headers=B).content))
    assert sum(inv.getdata()) / (inv.width * inv.height) < 5
    # non-DICOM refused
    png = [x for x in client.get("/api/patients", headers=B).json()]
    assert client.get("/api/prefs", headers=B).json()["theme"] == "system"
    assert client.put("/api/prefs", json={"theme": "dark", "dark_color": "#1A1033"}, headers=B).json() == {"theme": "dark", "dark_color": "#1a1033"}
    assert client.put("/api/prefs", json={"dark_color": "red; x"}, headers=B).status_code == 400
    assert client.get("/api/prefs", headers=B).json()["dark_color"] == "#1a1033"


def test_all_target_types_via_api(client, setup):
    B = {"Authorization": f"Bearer {setup['token']}"}
    cfgs = {
        "dropbox": {"app_key": "k", "app_secret": "s"},
        "onedrive": {"client_id": "c", "client_secret": "s", "tenant": "consumers"},
        "s3": {"provider": "b2", "bucket": "b", "region": "us-west-004", "access_key_id": "a", "secret_access_key": "SECRET"},
        "webdav": {"url": "https://cloud.example.com/remote.php/dav/files/u", "username": "u", "password": "SECRET"},
        "smb": {"host": "nas", "share": "backup", "username": "u", "password": "SECRET"},
        "local": {"path": "usb1"},
    }
    for t, cfg in cfgs.items():
        r = client.post("/api/backups/targets", json={"type": t, "config": cfg}, headers=B)
        assert r.status_code == 200, (t, r.text)
        out = r.json()
        assert "SECRET" not in r.text and "app_secret" not in out["config"]
        assert out["connected"] is (t not in ("dropbox", "onedrive")), t
        if t in ("dropbox", "onedrive"):
            loc = client.get(f"/api/oauth/{t}/start?target_id={out['id']}", headers=B, follow_redirects=False).headers["location"]
            assert loc.startswith({"dropbox": "https://www.dropbox.com/oauth2/authorize", "onedrive": "https://login.microsoftonline.com/consumers/"}[t])
    assert client.post("/api/backups/targets", json={"type": "ftp", "config": {}}, headers=B).status_code == 400


def test_recent_studies_for_home(client, setup):
    B = {"Authorization": f"Bearer {setup['token']}"}
    p = client.post("/api/patients", json={"name": "Recent Person"}, headers=B).json()
    old = client.post(f"/api/patients/{p['id']}/studies", json={"title": "Older study", "study_date": "2020-01-01"}, headers=B).json()
    new = client.post(f"/api/patients/{p['id']}/studies", json={"title": "Newer study", "modality": "CT"}, headers=B).json()
    r = client.get("/api/studies/recent?limit=50", headers=B)
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = [s["id"] for s in rows]
    assert ids.index(new["id"]) < ids.index(old["id"])
    top = rows[ids.index(new["id"])]
    assert top["patient_name"] == "Recent Person" and top["title"] == "Newer study"
    assert top["thumbnail_ids"] == [] and top["report_count"] == 0 and top["last_activity"]

    # adding a file to the older study moves it ahead
    client.post(f"/api/studies/{old['id']}/files", files=[("file", ("film.png", png_bytes(), "image/png"))], headers=B)
    rows = client.get("/api/studies/recent?limit=2", headers=B).json()
    assert len(rows) == 2 and rows[0]["id"] == old["id"] and len(rows[0]["thumbnail_ids"]) == 1
    # still routes to a single study by id
    assert client.get(f"/api/studies/{old['id']}", headers=B).json()["title"] == "Older study"
    assert client.get("/api/studies/recent").status_code in (401, 403)


def test_similar_endpoint(client, setup):
    B = {"Authorization": f"Bearer {setup['token']}"}
    p = client.post("/api/patients", json={"name": "Compare Person"}, headers=B).json()
    other = client.post("/api/patients", json={"name": "Someone Else"}, headers=B).json()
    mk = lambda pid, **j: client.post(f"/api/patients/{pid}/studies", json=j, headers=B).json()
    cur = mk(p["id"], title="MRI left knee", modality="MR", body_part="KNEE", study_date="2026-03-14")
    prior = mk(p["id"], title="MRI left knee", modality="MR", body_part="KNEE", study_date="2024-03-01")
    chest = mk(p["id"], title="Chest X-ray", modality="DX", study_date="2026-03-01")
    mk(other["id"], title="MRI left knee", modality="MR", body_part="KNEE")
    client.post(f"/api/studies/{prior['id']}/files", files=[("file", ("film.png", png_bytes(), "image/png"))], headers=B)
    r = client.get(f"/api/studies/{cur['id']}/similar", headers=B)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [s["id"] for s in rows] == [prior["id"], chest["id"]]  # never itself, never another patient
    top = rows[0]
    assert top["suggested"] and top["relation"] == "prior" and top["image_count"] == 1 and len(top["thumbnail_ids"]) == 1
    assert "Same type (MRI)" in top["match_reasons"]
    assert not rows[1]["suggested"]
    assert client.get("/api/studies/nope/similar", headers=B).status_code == 404
    assert client.get(f"/api/studies/{cur['id']}/similar").status_code in (401, 403)
