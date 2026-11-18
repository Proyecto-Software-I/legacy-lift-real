"""End-to-end acceptance: HTTP -> import -> analysis -> plan -> worker -> verify -> ZIP.

No backend functions are called in-process and no AI provider is mocked. The demo
runs on every CI build. Set E2E_AI_PROVIDER=codex or claude explicitly to run
a second, real-model journey using the host's Codex login or Anthropic API key.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

import pytest


def call(client, method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    assert 200 <= response.status_code < 300, (
        f"{method} {url}: HTTP {response.status_code}: {response.text[:1200]}"
    )
    return response


def source_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("calculator.py", "def total(x):\n    return (x + 1)\n")
        archive.writestr(
            "tests/test_calculator.py",
            "import unittest\n"
            "from calculator import total\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_total(self):\n"
            "        self.assertEqual(total(4), 5)\n"
            "        self.assertEqual(total(-1), 0)\n",
        )
        archive.writestr("README.md", "# Calculator legado\n\nModernizar sin alterar total(x).\n")
    return buf.getvalue()


def wait_for_job(client, project_id, job_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = call(client, "GET", f"/api/projects/{project_id}/jobs").json()
        job = next((x for x in jobs if x["id"] == job_id), None)
        if job and job["status"] not in ("queued", "running"):
            assert job["status"] == "completed", f"Ingesta fallida: {job}"
            return job
        time.sleep(0.25)
    pytest.fail(f"Timeout de ingesta: job {job_id}")


def run_phase(client, project_id, phase, timeout=45):
    started = call(client, "POST", f"/api/projects/{project_id}/phases/{phase}").json()
    run_id = started["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        project = call(client, "GET", f"/api/projects/{project_id}").json()
        run = next((r for r in project["runs"] if r["id"] == run_id), None)
        if run and run["status"] not in ("queued", "running"):
            assert run["status"] == "completed", f"{phase} falló: {run}"
            return run
        time.sleep(0.4)
    pytest.fail(f"Timeout de {phase}: {run_id}")


def worker_once(system, phase):
    finished = subprocess.run(
        [sys.executable, "-m", "app.migration_worker", "--once"],
        cwd=system["backend"], env=system["env"],
        capture_output=True, text=True, timeout=420, check=False,
    )
    assert finished.returncode == 0, (
        f"Worker falló procesando {phase}:\n{finished.stdout[-1500:]}\n{finished.stderr[-1500:]}"
    )


def model_ready(client, provider):
    assert provider in ("codex", "claude"), "E2E_AI_PROVIDER debe ser codex o claude"
    available = {p["id"]: p for p in call(client, "GET", "/api/providers").json()}
    assert available.get(provider, {}).get("ready"), (
        f"{provider} no está configurado en el backend. "
        "Codex exige CLI + codex login; Claude exige CLAUDE_API_KEY (no basta su suscripción)."
    )


def full_journey(client, system, provider):
    unique = uuid.uuid4().hex[:10]
    email = f"e2e-{unique}@example.test"
    password = "e2e-example-strong-password-12345"
    auth = call(client, "POST", "/api/auth/register", json={
        "email": email, "display_name": "E2E Academic",
        "password": password,
        "bootstrap_secret": system["env"]["LEGACYLIFT_BOOTSTRAP_SECRET"],
    }).json()
    token = auth["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    if provider != "demo":
        model_ready(client, provider)

    project = call(client, "POST", "/api/projects", json={
        "name": "E2E Academic " + unique,
        "system_name": "Calculator Python",
        "provider": provider,
    }).json()
    pid = project["id"]
    payload = source_zip()
    upload = call(client, "POST", f"/api/projects/{pid}/upload", files={
        "file": ("calculator.zip", payload, "application/zip")
    }).json()
    wait_for_job(client, pid, upload["id"])

    project = call(client, "GET", f"/api/projects/{pid}").json()
    assert project["active_snapshot_id"] and project["snapshot_hash"]
    snapshots = call(client, "GET", f"/api/projects/{pid}/snapshots").json()
    assert any(s["id"] == project["active_snapshot_id"] for s in snapshots)

    if provider != "demo":
        consent = call(client, "POST", f"/api/projects/{pid}/analysis/consent",
                       json={"approved": True}).json()
        assert consent["approved"]

    timeout = 420 if provider != "demo" else 45
    for phase in ("discover", "understand", "assess", "plan"):
        run_phase(client, pid, phase, timeout=timeout)

    for path in ("inventory.md", "understand.md", "assessment.md", "blueprint.md"):
        result = call(client, "GET", f"/api/projects/{pid}/artifacts/{path}")
        assert result.content.strip(), f"Artefacto vacío: {path}"

    draft = call(client, "GET", f"/api/projects/{pid}/blueprints/current").json()["blueprint"]
    blueprint = draft["data"]
    if provider != "demo":
        understand_doc = call(client, "GET", f"/api/projects/{pid}/artifacts/understand.json").json()
        assert understand_doc.get("invalid_ai_batches") == 0, f"Lotes inválidos en Understand: {understand_doc}"
        assert understand_doc.get("business_rules"), f"Understand sin reglas de negocio de IA: {understand_doc}"

        assess_doc = call(client, "GET", f"/api/projects/{pid}/artifacts/assessment.json").json()
        assert assess_doc.get("ai_suggestions"), f"Assess sin recomendaciones de IA: {assess_doc}"

        assert "no entregó un Blueprint JSON validable" not in (blueprint.get("notes") or ""), (
            f"Plan cayó en fallback estático: {blueprint.get('notes')}"
        )
    # Model-produced plans are advisory. The academic test explicitly selects
    # one supported Refactor wave so the same known scenario is executable.
    wave = blueprint["waves"][0]
    wave["strategy"] = "Refactor"
    wave["goal"] = "Refactorizar calculator.py (legibilidad, simplificación segura o tipado/docstring) sin alterar total(x)."
    wave["files"] = ["calculator.py"]
    wave["depends_on"] = []
    wave["acceptance_criteria"] = "Se simplifica o mejora calculator.py conservando el contrato y pruebas de total(x)."
    saved = call(client, "PATCH", f"/api/projects/{pid}/blueprints/current",
                 json={"data": blueprint}).json()
    wave_id = saved["data"]["waves"][0]["id"]

    call(client, "POST", f"/api/projects/{pid}/plan/decision", json={"approved": True})
    call(client, "POST", f"/api/projects/{pid}/blueprints/current/waves/{wave_id}/decision",
         json={"approved": True})

    if os.getenv("E2E_DOCKER") == "1":
        call(client, "POST", f"/api/projects/{pid}/baseline/consent",
             json={"approved": True})

    run_phase(client, pid, "baseline", timeout=90 if os.getenv("E2E_DOCKER") == "1" else 45)

    queued = call(client, "POST", f"/api/projects/{pid}/phases/modernize").json()
    aid = queued["id"]
    worker_once(system, "modernize")
    attempts = call(client, "GET", f"/api/projects/{pid}/migration/attempts").json()["attempts"]
    attempt = next(x for x in attempts if x["id"] == aid)
    assert attempt["status"] == "completed", f"Modernize falló: {attempt.get('error')}"

    review = call(client, "GET", f"/api/projects/{pid}/migration/attempts/{aid}/review").json()
    assert review["files"] and review["selected_count"] > 0
    assert all(f["path"] == "calculator.py" for f in review["files"])
    diff = call(client, "GET", f"/api/projects/{pid}/migration/attempts/{aid}/diff",
                params={"path": "calculator.py"}).json()["diff"]
    assert "-    return (x + 1)" in diff or diff.strip().startswith("---"), diff[:500]

    call(client, "POST", f"/api/projects/{pid}/phases/verify")
    worker_once(system, "verify")
    attempts = call(client, "GET", f"/api/projects/{pid}/migration/attempts").json()["attempts"]
    attempt = next(x for x in attempts if x["id"] == aid)
    assert attempt["verify_status"] == "completed", f"Verify falló: {attempt}"
    classification = attempt["verification"]["classification"]
    expected = "verified_within_tests" if os.getenv("E2E_DOCKER") == "1" else "not_demonstrated"
    assert classification == expected, (
        f"Verificación {classification} (se esperaba {expected}): {attempt['verification']['reason']}"
    )

    call(client, "POST", f"/api/projects/{pid}/migration/decision", json={
        "approved": True,
        "reason": "Caso académico: aceptamos la ausencia de ejecución de tests." if expected == "not_demonstrated" else "",
    })
    delivery = call(client, "GET", f"/api/projects/{pid}/migration/delivery")
    with zipfile.ZipFile(io.BytesIO(delivery.content)) as archive:
        names = archive.namelist()
        assert "codigo-modernizado/calculator.py" in names
        assert "evidencias/report.md" in names
        assert "evidencias/verify.json" in names
        assert "evidencias/changes.patch" in names
        new_source = archive.read("codigo-modernizado/calculator.py").decode()
        assert new_source != "def total(x):\n    return (x + 1)\n"
        assert "def total(" in new_source

    original = call(client, "GET", f"/api/projects/{pid}/artifacts/inventory.md")
    assert original.content
    patch = call(client, "GET", f"/api/projects/{pid}/migration/delivery",
                 params={"format": "patch"})
    assert patch.content
    final = call(client, "GET", f"/api/projects/{pid}").json()
    assert final["migration_decision"] == "approved"
    return {"project_id": pid, "classification": classification, "provider": provider}


def test_demo_full_journey(e2e_client, e2e_server):
    result = full_journey(e2e_client, e2e_server, "demo")
    assert result["provider"] == "demo"


@pytest.mark.skipif(not os.getenv("E2E_AI_PROVIDER"),
                    reason="IA real opt-in: configura E2E_AI_PROVIDER=codex o claude")
def test_real_ai_full_journey(e2e_client, e2e_server):
    provider = os.environ["E2E_AI_PROVIDER"].lower().strip()
    if provider == "codex":
        assert shutil.which(os.getenv("CODEX_BIN", "codex")), "Instala Codex CLI y ejecuta codex login"
    elif provider == "claude":
        assert os.getenv("CLAUDE_API_KEY"), "Configura CLAUDE_API_KEY"
    else:
        pytest.fail("E2E_AI_PROVIDER debe ser codex o claude")
    result = full_journey(e2e_client, e2e_server, provider)
    assert result["provider"] == provider
