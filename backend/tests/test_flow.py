import io
import time
import os
import tempfile
import zipfile
from pathlib import Path

import pytest


from fastapi.testclient import TestClient
from app import db, workspace, pipeline
from app.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET','bootstrap-test-secret-with-32-plus-characters')
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('VALIDATION_MODE', 'static')
    monkeypatch.setenv('LEGACYLIFT_WORKER_MODE', 'disabled')
    with TestClient(app) as t:
        t.post('/api/auth/register',json={'email':'tester@example.test','password':'testing-strong-password-123','display_name':'Tester','bootstrap_secret':'bootstrap-test-secret-with-32-plus-characters'})
        yield t


def headers():
    from app import auth
    return {'Authorization': 'Bearer ' + auth.login('tester@example.test','testing-strong-password-123')['access_token']}


def await_ingest(pid: str, jid: str) -> dict:
    for _ in range(600):
        job=db.job_get(pid,jid)
        if job['status'] not in ('queued','running'):
            return job
        time.sleep(.05)
    raise AssertionError('El trabajo de ingesta no terminó')


def sample_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('repo/calculator.py', 'def total(x):\n    return (x + 1)\n')
        z.writestr('repo/tests/test_example.py', 'def test_x():\n    assert True\n')
        z.writestr('repo/README.md', '# Example')
    return b.getvalue()


def test_auth_and_project(client):
    assert client.get('/api/projects').status_code == 401
    assert client.post('/api/auth/login', json={'email':'tester@example.test','password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json={'email':'tester@example.test','password': 'testing-strong-password-123'}).status_code == 200
    r = client.post('/api/projects', headers=headers(),json={'name':'Facturación', 'system_name':'Python', 'provider':'demo'})
    assert r.status_code == 200, r.text
    pid = r.json()['id']
    assert not client.get(f'/api/projects/{pid}', headers=headers()).json()['snapshot_hash']
    up = client.post(f'/api/projects/{pid}/upload', headers=headers(), files={'file':('codigo.zip', sample_zip(), 'application/zip')})
    assert up.status_code == 202, up.text
    result=await_ingest(pid,up.json()['id'])
    assert result['status']=='completed',result
    assert result['result']['files']==3
    assert client.post(f'/api/projects/{pid}/phases/modernize', headers=headers()).status_code == 409


def test_pipeline_end_to_end(client):
    p = client.post('/api/projects', headers=headers(),json={'name':'Contabilidad', 'system_name':'Legacy Python', 'provider':'demo'}).json()
    pid = p['id']
    up=client.post(f'/api/projects/{pid}/upload', headers=headers(), files={'file':('codigo.zip', sample_zip(), 'application/zip')})
    assert await_ingest(pid,up.json()['id'])['status']=='completed'
    for stage in ('discover','understand','assess','plan'):
        summary, details = pipeline.perform(pid,stage)
        rid=db.run_create(pid,stage,'demo')
        db.run_complete(rid,summary,details)
    assert client.post(f'/api/projects/{pid}/phases/baseline',headers=headers()).status_code == 409
    blueprint=client.get(f'/api/projects/{pid}/blueprints/current',headers=headers()).json()['blueprint']['data']
    # A human chooses an executable Refactor strategy before approval.
    blueprint['waves'][0]['strategy']='Refactor'
    assert client.patch(f'/api/projects/{pid}/blueprints/current',headers=headers(),json={'data':blueprint}).status_code==200
    assert client.post(f'/api/projects/{pid}/plan/decision',headers=headers(),json={'approved':True}).status_code == 200
    wave_approval=client.post(f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision',headers=headers(),json={'approved':True})
    assert wave_approval.status_code==200,wave_approval.text
    summary, details = pipeline.perform(pid,'baseline')
    rid=db.run_create(pid,'baseline','demo');db.run_complete(rid,summary,details)
    from app import migration_runs, migration_verify
    enqueue=client.post(f'/api/projects/{pid}/phases/modernize',headers=headers())
    assert enqueue.status_code == 200,enqueue.text
    assert migration_runs.run_one()['status']=='completed'
    verify_request=client.post(f'/api/projects/{pid}/phases/verify',headers=headers())
    assert verify_request.status_code == 200,verify_request.text
    assert migration_verify.run_one()['verify_status']=='completed'
    assert 'return x + 1' in (workspace.project_candidate(pid)/'calculator.py').read_text()
    assert 'return (x + 1)' in (workspace.project_source(pid)/'calculator.py').read_text()
    assert client.get(f'/api/projects/{pid}/artifacts/changes.patch', headers=headers()).status_code == 200
    verify = db.last_run(pid,'verify')
    assert verify['data']['classification'] == 'not_demonstrated'
    assert client.post(f'/api/projects/{pid}/migration/decision', headers=headers(),json={'approved': True,'reason':'Acepto el riesgo del ejemplo sin tests de comportamiento comparables.'}).status_code == 200
    response=client.get(f'/api/projects/{pid}/export',headers=headers())
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert 'codigo-modernizado/calculator.py' in z.namelist()
        assert 'evidencias/report.md' in z.namelist()


def test_zip_traversal_denied(client):
    p=client.post('/api/projects',headers=headers(),json={'name':'Test', 'system_name':'Test', 'provider':'demo'}).json()
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('../escape.py','malicious')
    resp=client.post(f"/api/projects/{p['id']}/upload",headers=headers(),files={'file':('bad.zip',b.getvalue(),'application/zip')})
    assert resp.status_code==202
    assert await_ingest(p['id'],resp.json()['id'])['status']=='failed'


def test_background_job_and_invalidation(client):
    import time
    p = client.post('/api/projects', headers=headers(),json={'name':'Proyecto demo', 'system_name':'Ejemplo', 'provider':'demo'}).json()
    pid=p['id']
    up=client.post(f'/api/projects/{pid}/upload', headers=headers(), files={'file':('codigo.zip', sample_zip(), 'application/zip')})
    assert await_ingest(pid,up.json()['id'])['status']=='completed'
    for stage in ('discover','understand','assess','plan'):
        r=client.post(f'/api/projects/{pid}/phases/{stage}',headers=headers())
        assert r.status_code==200, r.text
        for attempt in range(80):
            run=db.last_run(pid,stage)
            if run and run['status']!='running':
                break
            time.sleep(.01)
        assert run['status']=='completed', run
    assert client.get(f'/api/projects/{pid}',headers=headers()).json()['artifacts']
    # Re-running discovery invalidates dependent analysis, plan and approvals.
    response=client.post(f'/api/projects/{pid}/phases/discover',headers=headers())
    assert response.status_code==200, response.text
    assert db.last_run(pid,'understand')['status']=='superseded'
    assert db.last_run(pid,'plan')['status']=='superseded'
    assert db.project_get(pid)['plan_approved'] is None
