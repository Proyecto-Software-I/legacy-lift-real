"""Protect the bundled Angular demo ZIP against excluded directories and bad source."""
from pathlib import Path
import zipfile

from test_flow import client, headers, await_ingest


def test_bundled_demo_zip_can_be_imported(client):
    archive = Path(__file__).resolve().parents[2] / "frontend" / "public" / "demo-source.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert "calculator.py" in names
        assert "tests/test_calculator.py" in names
        assert all(not name.startswith((".git/", ".pytest_cache/", "__pycache__/")) for name in names)

    project = client.post("/api/projects", headers=headers(), json={
        "name": "Example project", "system_name": "Python demo", "provider": "demo",
    })
    assert project.status_code == 200, project.text
    pid = project.json()["id"]
    upload = client.post(f"/api/projects/{pid}/upload", headers=headers(),
                         files={"file": ("demo-source.zip", archive.read_bytes(), "application/zip")})
    assert upload.status_code == 202, upload.text
    job = await_ingest(pid, upload.json()["id"])
    assert job["status"] == "completed", job
    ready = client.get(f"/api/projects/{pid}", headers=headers()).json()
    assert ready["snapshot_hash"] and ready["active_snapshot_id"]
