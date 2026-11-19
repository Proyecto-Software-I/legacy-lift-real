"""Run the real FastAPI app as a child process against an isolated database.

The migration worker is invoked separately by the journey tests. No mocks, shared
developer data directory, external network services or AI credentials are required
for the deterministic journey.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[2]
BOOTSTRAP = "e2e-bootstrap-secret-with-more-than-32-characters"


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    port = int(os.getenv("E2E_PORT", "8000"))
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            pytest.fail(f"El puerto {port} está ocupado. Libéralo o configura E2E_PORT: {exc}")

    folder = tmp_path_factory.mktemp("legacylift-e2e")
    env = os.environ.copy()
    env.update({
        "LEGACYLIFT_DATA_DIR": str(folder / "data"),
        "LEGACYLIFT_BOOTSTRAP_SECRET": BOOTSTRAP,
        "LEGACYLIFT_ALLOW_REGISTRATION": "true",
        "LEGACYLIFT_ENV": "development",
        "LEGACYLIFT_WORKER_MODE": "docker-local" if os.getenv("E2E_DOCKER") == "1" else "disabled",
        "LEGACYLIFT_MIGRATION_WORKER_MODE": "separate",
        "LEGACYLIFT_VERIFY_IN_HOST": "false",
        "PYTHONUNBUFFERED": "1",
    })
    url = f"http://127.0.0.1:{port}"
    log_file = folder / "api.log"
    log = log_file.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if httpx.get(url + "/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail(f"FastAPI no inició. Log: {log_file.read_text(encoding='utf-8')[-3000:]}")
        if process.poll() is not None:
            pytest.fail(f"FastAPI terminó. Log: {log_file.read_text(encoding='utf-8')[-3000:]}")
        yield {"url": url, "env": env, "backend": BACKEND, "log": log_file}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


@pytest.fixture
def e2e_client(e2e_server):
    with httpx.Client(base_url=e2e_server["url"], timeout=30, follow_redirects=False) as client:
        yield client
