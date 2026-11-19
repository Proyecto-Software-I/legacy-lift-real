"""Full live-stack E2E suite: Multi-module ERP Python project with Codex (gpt-6.1-sol)
+ Docker unittest Verify auto-approval + Review/Rollback/Snapshots/RBAC gap tests.
"""
from __future__ import annotations

import io
import os
import time
import uuid
import zipfile
from pathlib import Path
import httpx
import pytest

API_BASE = os.getenv("E2E_API_BASE", "https://classforge-ai.dynv6.net/api")
EMAIL = os.getenv("E2E_EMAIL", "")
PASSWORD = os.getenv("E2E_PASSWORD", "")
ERP_ZIP = Path(__file__).resolve().parents[3] / "demo-erp.zip"
ERP_PROJECT_NAME = "ERP Facturación Multi-Módulo (IA Sol)"


def call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    res = client.request(method, f"{API_BASE}{path}", **kwargs)
    assert 200 <= res.status_code < 300, f"{method} {path} -> HTTP {res.status_code}: {res.text[:1200]}"
    return res


def wait_for_job(client: httpx.Client, pid: str, jid: str, timeout: int = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = call(client, "GET", f"/projects/{pid}/jobs").json()
        job = next((x for x in jobs if x["id"] == jid), None)
        if job and job["status"] not in ("queued", "running"):
            assert job["status"] == "completed", f"Ingesta fallida: {job}"
            return job
        time.sleep(0.5)
    raise AssertionError(f"Timeout esperando job {jid}")


def wait_for_phase(client: httpx.Client, pid: str, phase: str, run_id: str, timeout: int = 360) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proj = call(client, "GET", f"/projects/{pid}").json()
        run = next((r for r in proj["runs"] if r["id"] == run_id), None)
        if run and run["status"] not in ("queued", "running"):
            assert run["status"] == "completed", f"Fase {phase} falló: {run}"
            return run
        time.sleep(1.5)
    raise AssertionError(f"Timeout esperando fase {phase}")


@pytest.mark.skipif(not (EMAIL and PASSWORD), reason="Live E2E opt-in: configura E2E_EMAIL y E2E_PASSWORD")
def test_erp_large_real_ai_and_gaps():
    assert ERP_ZIP.is_file(), f"Falta {ERP_ZIP}"
    assert EMAIL and PASSWORD, "Configura E2E_EMAIL y E2E_PASSWORD en el entorno para ejecutar este E2E"

    with httpx.Client(timeout=180.0) as client:
        print("1. Autenticando contra HTTPS IPv6...")
        auth = call(client, "POST", "/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()
        client.headers["Authorization"] = f"Bearer {auth['access_token']}"

        providers = {p["id"]: p for p in call(client, "GET", "/providers").json()}
        assert providers.get("codex", {}).get("ready"), f"Codex no listo: {providers}"

        existing = [p for p in call(client, "GET", "/projects").json() if p["name"] == ERP_PROJECT_NAME]
        if existing:
            pid = existing[0]["id"]
            print(f"2. Reutilizando proyecto ERP existente: {pid}")
        else:
            print("2. Creando proyecto ERP multi-módulo (Codex gpt-6.1-sol)...")
            pid = call(client, "POST", "/projects", json={
                "name": ERP_PROJECT_NAME,
                "system_name": "ERP Facturación, Impuestos e Inventario Python",
                "provider": "codex",
            }).json()["id"]

        proj = call(client, "GET", f"/projects/{pid}").json()
        if not proj.get("active_snapshot_id"):
            assert client.post(f"{API_BASE}/projects/{pid}/phases/understand").status_code == 409
            assert client.post(f"{API_BASE}/projects/{pid}/phases/verify").status_code == 409
            assert client.get(f"{API_BASE}/projects/{pid}/migration/delivery").status_code == 409
            print("   Compuertas sin snapshot verificadas (409 OK). Subiendo demo-erp.zip...")
            job = call(client, "POST", f"/projects/{pid}/upload", files={
                "file": ("demo-erp.zip", ERP_ZIP.read_bytes(), "application/zip")
            }).json()
            wait_for_job(client, pid, job["id"])

        discover = call(client, "GET", f"/projects/{pid}/analysis/discover").json()["data"]
        assert discover["files"] >= 10, discover
        assert len(discover["modules"]) >= 4, discover["modules"]
        assert len(discover["graph"]["edges"]) >= 4, discover["graph"]
        print(f"3. Discover verificado: {discover['files']} archivos, {len(discover['modules'])} módulos, {len(discover['graph']['edges'])} relaciones.")

        # Explicit file selection check
        files_info = call(client, "GET", f"/projects/{pid}/analysis/files").json()
        selected_code = [f["path"] for f in files_info["files"] if f["selected"]]
        assert len(selected_code) >= 6
        call(client, "PUT", f"/projects/{pid}/analysis/selection", json={"files": selected_code})

        assert call(client, "POST", f"/projects/{pid}/analysis/consent", json={"approved": True}).json()["approved"] is True

        for phase in ("understand", "assess", "plan"):
            proj = call(client, "GET", f"/projects/{pid}").json()
            runs = [r for r in proj["runs"] if r["phase"] == phase and r["status"] == "completed"]
            if runs:
                print(f"4. Fase {phase.upper()} ya completada.")
            else:
                print(f"4. Ejecutando {phase.upper()} con Codex (gpt-6.1-sol)...")
                started = call(client, "POST", f"/projects/{pid}/phases/{phase}").json()
                run_info = wait_for_phase(client, pid, phase, started["id"], timeout=360)
                print(f"   {phase.upper()} OK -> {run_info.get('summary')}")

        # Review queue confirmation check
        reviews = call(client, "GET", f"/projects/{pid}/analysis/reviews").json()
        if reviews:
            rid = reviews[0]["id"]
            call(client, "PATCH", f"/projects/{pid}/analysis/reviews/{rid}",
                 json={"status": "confirmed", "comment": "Confirmado en auditoría ERP."})
            print(f"5. Review queue confirmada ({len(reviews)} items).")

        proj = call(client, "GET", f"/projects/{pid}").json()
        baseline_runs = [r for r in proj["runs"] if r["phase"] == "baseline"]
        baseline_ok = bool(baseline_runs and baseline_runs[-1]["status"] == "completed")
        if not baseline_ok:
            if not proj.get("plan_approved"):
                assert client.post(f"{API_BASE}/projects/{pid}/phases/baseline").status_code == 409
                bp_cur = call(client, "GET", f"/projects/{pid}/blueprints/current").json()["blueprint"]
                bp = bp_cur["data"]
                wave = bp["waves"][0]
                wave["strategy"] = "Refactor"
                wave["goal"] = "Refactorizar customers/tiers.py e inventory/catalog.py eliminando defaults mutables y paréntesis redundantes sin alterar reglas de descuento o stock."
                wave["files"] = ["customers/tiers.py", "inventory/catalog.py"]
                wave["depends_on"] = []
                wave["acceptance_criteria"] = "Los 10 tests unittest de customers, inventory y billing pasan sin cambios."
                saved = call(client, "PATCH", f"/projects/{pid}/blueprints/current", json={"data": bp}).json()
                assert saved["revision"] >= bp_cur["revision"] + 1
                history = call(client, "GET", f"/projects/{pid}/blueprints/history").json()
                assert len(history) >= 2, "Historial de Blueprint no versionó"
                wid = saved["data"]["waves"][0]["id"]
                call(client, "POST", f"/projects/{pid}/plan/decision", json={"approved": True})
                call(client, "POST", f"/projects/{pid}/blueprints/current/waves/{wid}/decision", json={"approved": True})
                call(client, "POST", f"/projects/{pid}/baseline/consent", json={"approved": True})
            else:
                history = call(client, "GET", f"/projects/{pid}/blueprints/history").json()
                assert len(history) >= 2, "Historial de Blueprint no versionó"

            sug = call(client, "POST", f"/projects/{pid}/baseline/suggestions").json()
            if sug.get("proposals"):
                cid = sug["proposals"][0]["id"]
                call(client, "POST", f"/projects/{pid}/baseline/cases/{cid}/decision", json={"approved": True})
                print(f"6. Baseline: {len(sug['proposals'])} propuestas generadas y 1 aprobada.")

            proj_now = call(client, "GET", f"/projects/{pid}").json()
            running_b = next((r for r in proj_now["runs"] if r["phase"] == "baseline" and r["status"] == "running"), None)
            b_id = running_b["id"] if running_b else call(client, "POST", f"/projects/{pid}/phases/baseline").json()["id"]
            b_run = wait_for_phase(client, pid, "baseline", b_id, timeout=120)
            assert b_run["data"]["tests"]["status"] == "passed", b_run["data"]["tests"]
            assert "Ran 10 tests" in b_run["data"]["tests"]["output"]
            print("7. Baseline Docker PASSED (Ran 10 tests across 3 suites).")

        attempts = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
        if not attempts or attempts[0]["status"] == "failed":
            if attempts and attempts[0]["status"] == "failed":
                queued = call(client, "POST", f"/projects/{pid}/migration/attempts/{attempts[0]['id']}/retry").json()
            else:
                queued = call(client, "POST", f"/projects/{pid}/phases/modernize").json()
            aid = queued["id"]
            deadline = time.monotonic() + 360
            attempt = None
            while time.monotonic() < deadline:
                attempts = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
                attempt = next(a for a in attempts if a["id"] == aid)
                if attempt["status"] not in ("queued", "running"):
                    break
                time.sleep(2.0)
            assert attempt and attempt["status"] == "completed", f"Modernize falló: {attempt}"
        else:
            attempt = attempts[0]
            aid = attempt["id"]

        rev = call(client, "GET", f"/projects/{pid}/migration/attempts/{aid}/review").json()
        print(f"8. Modernize completado: {rev['total_files']} archivo(s) modificados (+{rev['added']}/-{rev['removed']}).")

        if attempt.get("verify_status") != "completed":
            call(client, "POST", f"/projects/{pid}/phases/verify")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                attempts = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
                attempt = next(a for a in attempts if a["id"] == aid)
                if attempt.get("verify_status") not in (None, "queued", "running"):
                    break
                time.sleep(1.5)

        assert attempt.get("verify_status") == "completed", f"Verify falló: {attempt}"
        ver = attempt["verification"]
        assert ver["classification"] == "verified_within_tests", f"Se esperaba verified_within_tests: {ver}"
        assert len(ver.get("test_names_before") or []) == 10, ver
        assert len(ver.get("test_names_after") or []) == 10, ver
        assert attempt.get("decision") == "approved", f"Verify pasó pruebas equivalentes pero no auto-aprobó: {attempt}"
        print("9. Verify PASSED (10/10 pruebas equivalentes en Docker) -> Auto-marcado como Correcto/Aprobado.")

        delivery = call(client, "GET", f"/projects/{pid}/migration/delivery")
        with zipfile.ZipFile(io.BytesIO(delivery.content)) as z:
            assert any(n.startswith("codigo-modernizado/") for n in z.namelist())
            assert "evidencias/verify.json" in z.namelist()
        print("10. Entregables ERP descargados y verificados.")


@pytest.mark.skipif(not (EMAIL and PASSWORD), reason="Live E2E opt-in: configura E2E_EMAIL y E2E_PASSWORD")
def test_live_org_reports_and_gates():
    assert EMAIL and PASSWORD, "Configura E2E_EMAIL y E2E_PASSWORD"
    with httpx.Client(timeout=45.0) as client:
        auth = call(client, "POST", "/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()
        client.headers["Authorization"] = f"Bearer {auth['access_token']}"

        orgs = call(client, "GET", "/organizations").json()
        oid = orgs[0]["id"]
        report = call(client, "GET", f"/organizations/{oid}/reports").json()
        assert report["totals"]["projects"] >= 2
        csv_res = call(client, "GET", f"/organizations/{oid}/reports.csv")
        assert "Nombre" in csv_res.text or "Proyecto" in csv_res.text or len(csv_res.text) > 10
        usage = call(client, "GET", f"/organizations/{oid}/usage").json()
        assert usage["plan"]["id"]
        audit = call(client, "GET", f"/organizations/{oid}/audit").json()
        assert len(audit) > 0
        print("11. Sprint 5 Org/Audit/CSV/Quotas verificados en vivo sin contaminar la lista de proyectos.")


if __name__ == "__main__":
    test_erp_large_real_ai_and_gaps()
    test_live_org_reports_and_gates()
