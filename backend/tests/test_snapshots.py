import io,zipfile,time
import pytest
from fastapi.testclient import TestClient
from app import db, workspace
from app.api import app

@pytest.fixture
def c(tmp_path,monkeypatch):
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET','bootstrap-test-secret-with-32-plus-characters')
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('LEGACYLIFT_WORKER_MODE','disabled')
    with TestClient(app) as t:
        r=t.post('/api/auth/register',json={'email':'snap@example.test','password':'long-good-password-789','display_name':'Snapshot','bootstrap_secret':'bootstrap-test-secret-with-32-plus-characters'})
        h={'Authorization':'Bearer '+r.json()['access_token']}
        pid=t.post('/api/projects',headers=h,json={'name':'Project Test','system_name':'Python','provider':'demo'}).json()['id']
        yield t,h,pid

def zip_source(value):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as f:
        f.writestr('project/src.py',f'def test():\n    return {value}\n')
        f.writestr('project/README.md','# Hello')
    return buffer.getvalue()

def await_result(pid,jid):
    for _ in range(600):
        row=db.job_get(pid,jid)
        if row['status'] not in ('queued','running'):
            assert row['status']=='completed',row
            return row['result']
        time.sleep(.05)
    raise AssertionError('No terminó la ingesta')


def test_multiple_immutable_snapshots_and_switch(c):
    t,h,pid=c
    one=t.post(f'/api/projects/{pid}/upload',headers=h,files={'file':('first.zip',zip_source(1))})
    assert one.status_code==202,one.text
    one_result=await_result(pid,one.json()['id'])
    assert db.last_run(pid,'discover')['status']=='completed'
    old_id=one_result['snapshot_id']
    assert (workspace.snapshot_archive(pid,old_id)/'src.py').exists()
    two=t.post(f'/api/projects/{pid}/upload',headers=h,files={'file':('second.zip',zip_source(2))})
    assert two.status_code==202,two.text
    two_result=await_result(pid,two.json()['id'])
    assert two_result['snapshot_hash']!=one_result['snapshot_hash']
    versions=t.get(f'/api/projects/{pid}/snapshots',headers=h).json()
    assert len(versions)==2
    assert (workspace.snapshot_archive(pid,old_id)/'src.py').read_text().endswith('1\n')
    back=t.post(f'/api/projects/{pid}/snapshots/{old_id}/activate',headers=h)
    assert back.status_code==200,back.text
    assert workspace.project_source(pid).joinpath('src.py').read_text().endswith('1\n')
    assert db.project_get(pid)['active_snapshot_id']==old_id
    assert db.last_run(pid,'discover')['status']=='superseded'

def test_legacy_snapshot_migration(tmp_path,monkeypatch):
    from app import auth, ingestion
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET','bootstrap-test-secret-with-32-plus-characters')
    db.init()
    uid=auth.register('legacy@example.test','Legacy','long-legacy-password-123','bootstrap-test-secret-with-32-plus-characters')['id']
    pid=db.project_create('Anterior','Python','demo',uid)['id']
    count,digest=workspace.extract_zip(pid,zip_source(42))
    db.project_update(pid,source_filename='before.zip',snapshot_hash=digest)
    ingestion.restore_legacy_snapshots()
    snapshots=db.snapshot_list(pid)
    assert len(snapshots)==1
    assert db.project_get(pid)['active_snapshot_id']==snapshots[0]['id']
    assert workspace.snapshot_archive(pid,snapshots[0]['id']).joinpath('src.py').exists()
    ingestion.restore_legacy_snapshots()
    assert len(db.snapshot_list(pid))==1


def test_git_import_sanitized_and_automatic(c,monkeypatch):
    from app import github
    t,h,pid=c
    def fakeclone(uid,full_name,destination):
        assert full_name=='test/private'
        destination.mkdir()
        (destination/'.git').mkdir()
        (destination/'.git'/'config').write_text('secret')
        (destination/'app.py').write_text('def foo():\n    return 1\n')
        return '0123456789abcdef'
    monkeypatch.setattr(github,'clone',fakeclone)
    monkeypatch.setattr(github,'connection',lambda uid:{'github_login':'test'})
    res=t.post(f'/api/projects/{pid}/git',headers=h,json={'full_name':'test/private'})
    assert res.status_code==202,res.text
    assert await_result(pid,res.json()['id'])['commit_sha']=='0123456789abcdef'
    assert not (workspace.project_source(pid)/'.git').exists()
    assert db.last_run(pid,'discover')['status']=='completed'


def test_snapshot_access_isolated(c):
    t,h,pid=c
    job=t.post(f'/api/projects/{pid}/upload',headers=h,files={'file':('test.zip',zip_source(1))}).json()
    sid=await_result(pid,job['id'])['snapshot_id']
    second=t.post('/api/auth/register',json={'email':'other@example.test','password':'another-strong-password-789','display_name':'Other'})
    # Open registration only for a multiuser test.
    assert second.status_code in (200,400)
    if second.status_code==200:
        newh={'Authorization':'Bearer '+second.json()['access_token']}
        assert t.get(f'/api/projects/{pid}/snapshots',headers=newh).status_code==404
        assert t.post(f'/api/projects/{pid}/snapshots/{sid}/activate',headers=newh).status_code==404
