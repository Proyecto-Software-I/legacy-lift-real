"""Browser smoke against the real Angular application and the isolated FastAPI.

Opt-in locally with E2E_BROWSER=1 and start Angular on port 4200. The GitHub
workflow starts Angular automatically. Captures a screenshot for academic evidence.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

from conftest import BOOTSTRAP


@pytest.mark.skipif(os.getenv("E2E_BROWSER") != "1",
                    reason="Navegador opt-in: configura E2E_BROWSER=1 y levanta Angular")
def test_browser_login_create_import_and_sprint5(e2e_client, e2e_server):
    from playwright.sync_api import sync_playwright, expect

    suffix = uuid.uuid4().hex[:8]
    email = f"browser-{suffix}@example.test"
    password = "e2e-browser-strong-password-123456"
    response = e2e_client.post("/api/auth/register", json={
        "email": email, "password": password,
        "display_name": "Browser E2E", "bootstrap_secret": BOOTSTRAP,
    })
    assert response.status_code == 200, response.text
    project_name = "E2E Navegador " + suffix
    artifact_dir = Path(os.getenv("E2E_ARTIFACTS", "test-results/e2e"))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="es-ES")
        page.set_default_timeout(30000)
        try:
            page.goto(os.getenv("E2E_FRONTEND_URL", "http://127.0.0.1:4200"),
                      wait_until="domcontentloaded", timeout=45000)
            expect(page.locator("#email")).to_be_visible()
            page.locator("#email").fill(email)
            page.locator("#password").fill(password)
            page.locator("form.auth-form button[type=submit]").click()
            expect(page.locator(".sidebar-new")).to_be_visible(timeout=30000)

            page.locator(".sidebar-new").click()
            expect(page.locator("#projectName")).to_be_visible()
            page.locator("#projectName").fill(project_name)
            page.locator("#systemName").fill("Python legado")
            page.get_by_role("button", name="Crear proyecto", exact=True).click()
            expect(page.locator("h1").filter(has_text=project_name)).to_be_visible(timeout=30000)

            page.get_by_role("button", name="Usar proyecto de ejemplo").click()
            expect(page.get_by_role("button", name="Importar otro ZIP")).to_be_visible(timeout=60000)
            page.get_by_role("tab", name=re.compile("Discover", re.I)).click()
            expect(page.get_by_role("tabpanel", name=re.compile("Discover", re.I))).to_be_visible()

            page.get_by_role("button", name="Abrir organizaciones y reportes").click()
            expect(page.get_by_text("Organizaciones y reportes", exact=True)).to_be_visible()
            expect(page.get_by_text("Plan:", exact=False)).to_be_visible()
            page.screenshot(path=str(artifact_dir / "workspace-organizations.png"), full_page=True)
        except Exception:
            page.screenshot(path=str(artifact_dir / "browser-failure.png"), full_page=True)
            raise
        finally:
            browser.close()
