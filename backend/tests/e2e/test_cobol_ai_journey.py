"""End-to-end test for COBOL demo project with real AI (Codex CLI using gpt-6.1-sol)
from Firebase Hosting (HTTPS) to WSL2 FastAPI + migration-worker container over IPv6.
Reuses an existing COBOL project if already analyzed so we don't waste AI tokens.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
import httpx
import pytest

API_BASE = os.getenv("E2E_API_BASE", "https://classforge-ai.dynv6.net/api")
EMAIL = os.getenv("E2E_EMAIL", "")
PASSWORD = os.getenv("E2E_PASSWORD", "")
COBOL_ZIP = Path(__file__).resolve().parents[3] / "demo-cobol.zip"
PROJECT_NAME = "Demo Bancaria COBOL (IA Sol)"


def call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    res = client.request(method, f"{API_BASE}{path}", **kwargs)
    assert 200 <= res.status_code < 300, f"{method} {path} -> HTTP {res.status_code}: {res.text[:1200]}"
    return res


def wait_for_job(client: httpx.Client, pid: str, jid: str, timeout: int = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = call(client, "GET", f"/projects/{pid}/jobs").json()
        job = next((x for x in jobs if x["id"] == jid), None)
        if job and job["status"] not in ("queued", "running"):
            assert job["status"] == "completed", f"Ingesta COBOL fallida: {job}"
            return job
        time.sleep(0.5)
    raise AssertionError(f"Timeout esperando job de ingesta {jid}")


def wait_for_phase(client: httpx.Client, pid: str, phase: str, run_id: str, timeout: int = 360) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proj = call(client, "GET", f"/projects/{pid}").json()
        run = next((r for r in proj["runs"] if r["id"] == run_id), None)
        if run and run["status"] not in ("queued", "running"):
            assert run["status"] == "completed", f"Fase {phase} falló: {run}"
            return run
        time.sleep(1.5)
    raise AssertionError(f"Timeout esperando fase {phase} ({run_id})")


def latest_phase_status(proj: dict, phase: str) -> str | None:
    runs = [r for r in (proj.get("runs") or []) if r["phase"] == phase]
    return runs[-1]["status"] if runs else None


@pytest.mark.skipif(not (EMAIL and PASSWORD), reason="Live E2E opt-in: configura E2E_EMAIL y E2E_PASSWORD")
def test_cobol_real_ai_journey():
    assert COBOL_ZIP.is_file(), f"No existe {COBOL_ZIP}"
    assert EMAIL and PASSWORD, "Configura E2E_EMAIL y E2E_PASSWORD en el entorno para ejecutar este E2E"

    with httpx.Client(timeout=60.0) as client:
        print("1. Iniciando sesión contra el backend HTTPS por IPv6...")
        auth = call(client, "POST", "/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()
        client.headers["Authorization"] = f"Bearer {auth['access_token']}"

        providers = {p["id"]: p for p in call(client, "GET", "/providers").json()}
        assert providers.get("codex", {}).get("ready"), f"Codex no está listo: {providers}"
        print("   Codex CLI disponible en el backend (modelo configurado: gpt-6.1-sol).")

        existing = [p for p in call(client, "GET", "/projects").json() if p["name"] == PROJECT_NAME and p["provider"] == "codex"]
        if existing:
            pid = existing[0]["id"]
            print(f"2. Reutilizando proyecto COBOL existente: {pid}")
        else:
            print("2. Creando proyecto COBOL con proveedor 'codex'...")
            project = call(client, "POST", "/projects", json={
                "name": PROJECT_NAME,
                "system_name": "Core Bancario Nómina COBOL-85",
                "provider": "codex",
            }).json()
            pid = project["id"]
            print(f"   Proyecto creado: {pid}")

        proj = call(client, "GET", f"/projects/{pid}").json()
        if not proj.get("active_snapshot_id"):
            print("3. Subiendo demo-cobol.zip (PAYROLL.cbl, INTEREST.cbl, ACCOUNT.cpy)...")
            upload = call(client, "POST", f"/projects/{pid}/upload", files={
                "file": ("demo-cobol.zip", COBOL_ZIP.read_bytes(), "application/zip")
            }).json()
            wait_for_job(client, pid, upload["id"])
            proj = call(client, "GET", f"/projects/{pid}").json()

        discover = call(client, "GET", f"/projects/{pid}/analysis/discover").json()["data"]
        assert "COBOL" in discover["languages"], f"No se detectó COBOL en Discover: {discover['languages']}"
        print(f"3. Discover verificado: lenguajes={discover['languages']}, módulos={discover['modules']}")

        consent = call(client, "POST", f"/projects/{pid}/analysis/consent", json={"approved": True}).json()
        assert consent["approved"] is True

        for phase in ("understand", "assess", "plan"):
            proj = call(client, "GET", f"/projects/{pid}").json()
            if latest_phase_status(proj, phase) == "completed":
                print(f"5. Fase {phase.upper()} ya completada previamente con Codex (gpt-6.1-sol).")
            else:
                print(f"5. Ejecutando fase {phase.upper()} con Codex (gpt-6.1-sol)...")
                t0 = time.monotonic()
                started = call(client, "POST", f"/projects/{pid}/phases/{phase}").json()
                run_info = wait_for_phase(client, pid, phase, started["id"], timeout=360)
                print(f"   {phase.upper()} completado en {time.monotonic() - t0:.1f}s -> {run_info.get('summary')}")

        understand_doc = call(client, "GET", f"/projects/{pid}/artifacts/understand.json").json()
        assert understand_doc.get("business_rules"), f"Understand sin reglas de negocio de IA: {understand_doc}"
        print(f"   Reglas de negocio inferidas por Codex en COBOL: {len(understand_doc['business_rules'])}")

        assess_doc = call(client, "GET", f"/projects/{pid}/artifacts/assessment.json").json()
        assert assess_doc.get("ai_suggestions"), f"Assess sin recomendaciones de IA: {assess_doc}"
        print(f"   Recomendaciones de Assess por Codex: {len(assess_doc['ai_suggestions'])}")

        proj = call(client, "GET", f"/projects/{pid}").json()
        if latest_phase_status(proj, "baseline") != "completed":
            print("6. Configurando wave-01 para Refactor seguro de src/PAYROLL.cbl y aprobando Blueprint + Wave...")
            bp = call(client, "GET", f"/projects/{pid}/blueprints/current").json()["blueprint"]["data"]
            wave = bp["waves"][0]
            wave["strategy"] = "Refactor"
            wave["goal"] = "Simplificar asignaciones redundantes (ADD ... TO 0 GIVING -> MOVE) y paréntesis redundantes en src/PAYROLL.cbl conservando la lógica de cálculo de nómina."
            wave["files"] = ["src/PAYROLL.cbl"]
            wave["depends_on"] = []
            wave["acceptance_criteria"] = "Se mantiene PROGRAM-ID. PAYROLL, PROCEDURE DIVISION y el cálculo de WS-GROSS-PAY, WS-TAX-AMOUNT y ACCT-NET-PAY."
            saved = call(client, "PATCH", f"/projects/{pid}/blueprints/current", json={"data": bp}).json()
            wid = saved["data"]["waves"][0]["id"]
            call(client, "POST", f"/projects/{pid}/plan/decision", json={"approved": True})
            call(client, "POST", f"/projects/{pid}/blueprints/current/waves/{wid}/decision", json={"approved": True})
            print("7. Ejecutando Baseline estático sobre el código COBOL...")
            b_start = call(client, "POST", f"/projects/{pid}/phases/baseline").json()
            b_run = wait_for_phase(client, pid, "baseline", b_start["id"], timeout=60)
            print(f"   Baseline completado -> {b_run.get('summary')}")
        else:
            print("6-7. Blueprint, Wave-01 y Baseline COBOL ya aprobados y completados.")

        print("8. Encolando Modernize para el contenedor separado migration-worker (Codex gpt-6.1-sol)...")
        t0 = time.monotonic()
        attempts_before = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
        if attempts_before and attempts_before[0]["status"] == "failed":
            queued = call(client, "POST", f"/projects/{pid}/migration/attempts/{attempts_before[0]['id']}/retry").json()
        elif attempts_before and attempts_before[0]["status"] == "completed":
            queued = attempts_before[0]
        else:
            queued = call(client, "POST", f"/projects/{pid}/phases/modernize").json()
        aid = queued["id"]

        deadline = time.monotonic() + 360
        attempt = None
        while time.monotonic() < deadline:
            attempts = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
            attempt = next((a for a in attempts if a["id"] == aid), None)
            if attempt and attempt["status"] not in ("queued", "running"):
                break
            time.sleep(2.0)
        assert attempt and attempt["status"] == "completed", f"Modernize COBOL falló: {attempt}"
        print(f"   Modernize procesado por contenedor migration-worker en {time.monotonic() - t0:.1f}s.")

        diff = call(client, "GET", f"/projects/{pid}/migration/attempts/{aid}/diff", params={"path": "src/PAYROLL.cbl"}).json()["diff"]
        assert "PAYROLL" in diff or "@@" in diff, f"Diff vacío: {diff}"
        print("   Diff generado por Codex (gpt-6.1-sol) en src/PAYROLL.cbl:\n" + "\n".join("     " + l for l in diff.splitlines()[:22]))

        print("9. Ejecutando Verify sobre el candidato COBOL modernizado...")
        if attempt.get("verify_status") != "completed":
            call(client, "POST", f"/projects/{pid}/phases/verify")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                attempts = call(client, "GET", f"/projects/{pid}/migration/attempts").json()["attempts"]
                attempt = next((a for a in attempts if a["id"] == aid), None)
                if attempt and attempt.get("verify_status") not in (None, "queued", "running"):
                    break
                time.sleep(1.5)
        assert attempt and attempt.get("verify_status") == "completed", f"Verify COBOL falló: {attempt}"
        ver = attempt["verification"]
        print(f"   Verify completado -> classification={ver['classification']}, static_after={ver['static_after']}")
        assert ver["static_after"]["failed"] == 0

        print("10. Aprobando migración COBOL con justificación humana y descargando ZIP de entrega...")
        if not attempt.get("decision"):
            call(client, "POST", f"/projects/{pid}/migration/decision", json={
                "approved": True,
                "reason": "Refactorización COBOL validada por sintaxis estructural e inspección humana del diff de nómina."
            })
        delivery = call(client, "GET", f"/projects/{pid}/migration/delivery")
        with zipfile.ZipFile(io.BytesIO(delivery.content)) as z:
            names = z.namelist()
            assert "codigo-modernizado/src/PAYROLL.cbl" in names
            assert "evidencias/changes.patch" in names
            assert "evidencias/report.md" in names
        print("   Entregable ZIP COBOL verificado con éxito (codigo-modernizado/src/PAYROLL.cbl + evidencias).")
        print("PRUEBA E2E COBOL CON IA REAL (GPT-6.1-SOL) COMPLETADA EXITOSAMENTE.")


if __name__ == "__main__":
    test_cobol_real_ai_journey()
