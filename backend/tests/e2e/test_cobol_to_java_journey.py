"""End-to-end: Migration Pack cobol-to-java@1.0 with a real AI model and a real dual run.

Opt-in: E2E_AI_PROVIDER=gemini|codex|claude and E2E_DOCKER=1 (needs the operator-built
runner image `docker build -t legacylift/legacy-runner:1 docker/legacy-runner`).
HTTP only: no backend function is called in-process and nothing is mocked.
"""
from __future__ import annotations

import io
import os
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from test_academic_journey import call, model_ready, run_phase, wait_for_job, worker_once

DEMO = Path(__file__).resolve().parents[3] / 'demo-cobol'


def cobol_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        for path in sorted(DEMO.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(DEMO).as_posix())
    return buf.getvalue()


@pytest.mark.skipif(not (os.getenv('E2E_AI_PROVIDER') and os.getenv('E2E_DOCKER') == '1'),
                    reason='Opt-in: E2E_AI_PROVIDER=gemini|codex|claude y E2E_DOCKER=1')
def test_cobol_to_java_with_real_ai_and_dual_run(e2e_client, e2e_server):
    client, system = e2e_client, e2e_server
    provider = os.environ['E2E_AI_PROVIDER'].lower().strip()
    unique = uuid.uuid4().hex[:10]
    token = call(client, 'POST', '/api/auth/register', json={
        'email': f'cobol-{unique}@example.test', 'display_name': 'E2E COBOL',
        'password': 'e2e-example-strong-password-12345',
        'bootstrap_secret': system['env']['LEGACYLIFT_BOOTSTRAP_SECRET']}).json()['access_token']
    client.headers['Authorization'] = f'Bearer {token}'
    model_ready(client, provider)

    pid = call(client, 'POST', '/api/projects', json={'name': 'Nómina COBOL ' + unique,
               'system_name': 'COBOL bancario', 'provider': provider}).json()['id']
    upload = call(client, 'POST', f'/api/projects/{pid}/upload',
                  files={'file': ('demo-cobol.zip', cobol_zip(), 'application/zip')}).json()
    wait_for_job(client, pid, upload['id'])
    call(client, 'POST', f'/api/projects/{pid}/analysis/consent', json={'approved': True})
    for phase in ('discover', 'understand', 'assess', 'plan'):
        run_phase(client, pid, phase, timeout=420)

    # The operator chooses the language-migration strategy for the COBOL programs.
    packs = call(client, 'GET', f'/api/projects/{pid}/migration-packs').json()
    pack = next(p for p in packs['packs'] if p['id'] == 'cobol-to-java@1.0')
    assert pack['status'] == 'available'
    blueprint = call(client, 'GET', f'/api/projects/{pid}/blueprints/current').json()['blueprint']['data']
    blueprint['waves'] = [{'id': 'wave-01', 'goal': 'Migrar PAYROLL e INTEREST a Java 17 conservando la salida.',
                           'strategy': 'LanguageMigration', 'files': ['src/PAYROLL.cbl', 'src/INTEREST.cbl'],
                           'depends_on': [], 'priority': 1, 'risk': 'Formato de DISPLAY y truncamiento decimal.',
                           'acceptance_criteria': 'El Java produce exactamente la misma salida que el COBOL original.'}]
    saved = call(client, 'PATCH', f'/api/projects/{pid}/blueprints/current', json={'data': blueprint}).json()
    call(client, 'POST', f'/api/projects/{pid}/plan/decision', json={'approved': True})
    call(client, 'POST', f"/api/projects/{pid}/blueprints/current/waves/{saved['data']['waves'][0]['id']}/decision",
         json={'approved': True})
    project = call(client, 'GET', f'/api/projects/{pid}').json()
    assert project['active_pack_id'] == 'cobol-to-java@1.0', project.get('active_pack_id')

    call(client, 'POST', f'/api/projects/{pid}/baseline/consent', json={'approved': True})
    baseline = run_phase(client, pid, 'baseline', timeout=240)
    reference = baseline['data']['dual_run']['cobol']
    assert reference['status'] == 'passed', baseline['data']['dual_run']
    assert 'NETO:' in reference['runs']['PAYROLL']['stdout']

    queued = call(client, 'POST', f'/api/projects/{pid}/phases/modernize').json()
    worker_once(system, 'modernize')
    attempt = next(a for a in call(client, 'GET', f'/api/projects/{pid}/migration/attempts').json()['attempts']
                   if a['id'] == queued['id'])
    assert attempt['status'] == 'completed', f"Modernize falló: {attempt.get('error')}"
    review = call(client, 'GET', f"/api/projects/{pid}/migration/attempts/{attempt['id']}/review").json()
    paths = [f['path'] for f in review['files']]
    assert 'java/src/main/java/legacylift/migrated/Payroll.java' in paths, paths
    assert all(p.startswith('java/') for p in paths)

    call(client, 'POST', f'/api/projects/{pid}/phases/verify')
    worker_once(system, 'verify')
    attempt = next(a for a in call(client, 'GET', f'/api/projects/{pid}/migration/attempts').json()['attempts']
                   if a['id'] == queued['id'])
    verification = attempt['verification']
    print('\nDual run:', [(r['program'], r['equal'], r['cobol'], r['java']) for r in verification['dual_run']['comparison']])
    assert verification['evidence_kind'] == 'dual_run'
    assert verification['classification'] == 'verified_within_tests', verification['reason']
    assert attempt['decision'] is None  # never auto-approved

    call(client, 'POST', f'/api/projects/{pid}/migration/decision', json={'approved': True, 'reason': ''})
    names = zipfile.ZipFile(io.BytesIO(call(client, 'GET', f'/api/projects/{pid}/export').content)).namelist()
    assert 'codigo-modernizado/java/src/main/java/legacylift/migrated/Payroll.java' in names
    assert 'codigo-modernizado/src/PAYROLL.cbl' in names  # original kept for traceability
    assert 'documentacion/verification.md' in names
