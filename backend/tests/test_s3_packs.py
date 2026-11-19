"""Migration execution must be tied to approved, compatible, snapshot-scoped waves."""
from app import db, planning, migration_packs, pipeline, workspace
from test_flow import client, headers, sample_zip, await_ingest
import io
import zipfile


def make_ready(client, source=None):
    p=client.post('/api/projects',headers=headers(),json={'name':'Proyecto Packs S3','system_name':'Legacy','provider':'demo'}).json()
    pid=p['id']
    up=client.post(f'/api/projects/{pid}/upload',headers=headers(),files={'file':('code.zip',source or sample_zip(),'application/zip')})
    assert await_ingest(pid,up.json()['id'])['status']=='completed'
    for phase in ('discover','understand','assess','plan'):
        summary,details=pipeline.perform(pid,phase)
        rid=db.run_create(pid,phase,'demo');db.run_complete(rid,summary,details)
    return pid


def test_wave_requires_plan_and_compatible_pack(client):
    pid=make_ready(client)
    url=f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision'
    assert client.post(url,headers=headers(),json={'approved':True}).status_code==409
    assert client.post(f'/api/projects/{pid}/plan/decision',headers=headers(),json={'approved':True}).status_code==200
    draft=planning.current(pid)
    matches=migration_packs.compatible(pid,draft['data']['waves'][0])
    if not any(m['compatible'] for m in matches):
        response=client.post(url,headers=headers(),json={'approved':True})
        if draft['data']['waves'][0]['strategy']=='Keep':
            # Keep is an acknowledgement: approved, but never the executable wave.
            assert response.status_code==200 and planning.executable_wave(pid) is None
            assert not pipeline.phase_ready(pid,'baseline')[0]
        else:
            assert response.status_code==409
        edited=draft['data'].copy();edited['waves'][0]['strategy']='Refactor'
        assert client.patch(f'/api/projects/{pid}/blueprints/current',headers=headers(),json={'data':edited}).status_code==200
        assert client.post(f'/api/projects/{pid}/plan/decision',headers=headers(),json={'approved':True}).status_code==200
    assert client.post(url,headers=headers(),json={'approved':True}).status_code==200
    assert planning.executable_wave(pid)['id']=='wave-01'
    assert pipeline.phase_ready(pid,'baseline')[0]
    assert client.post(f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision',headers=headers(),json={'approved':False}).status_code==200
    assert not pipeline.phase_ready(pid,'baseline')[0]


def test_cobol_planned_pack_is_not_executable(client):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:
        archive.writestr('program.cbl','       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PAGOS.\n       PROCEDURE DIVISION.\n           DISPLAY "HOLA".\n           STOP RUN.')
    pid=make_ready(client,buf.getvalue())
    plan=planning.current(pid)
    data=plan['data'].copy();data['waves'][0]['strategy']='LanguageMigration'
    assert client.patch(f'/api/projects/{pid}/blueprints/current',headers=headers(),json={'data':data}).status_code==200
    assert client.post(f'/api/projects/{pid}/plan/decision',headers=headers(),json={'approved':True}).status_code==200
    matches=client.get(f'/api/projects/{pid}/migration-packs',headers=headers()).json()['compatibility']['wave-01']
    assert all(not m['compatible'] for m in matches)
    assert client.post(f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision',headers=headers(),json={'approved':True}).status_code==409
    assert not pipeline.phase_ready(pid,'modernize')[0]


def test_ai_changes_outside_approved_wave_are_blocked(client):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:
        archive.writestr('a.py','def a():\n    return (1)\n')
        archive.writestr('b.py','def b():\n    return (2)\n')
    pid=make_ready(client,buf.getvalue())
    data=planning.current(pid)['data']; data['waves'][0]['files']=['a.py'];data['waves'][0]['strategy']='Refactor'
    assert client.patch(f'/api/projects/{pid}/blueprints/current',headers=headers(),json={'data':data}).status_code==200
    assert client.post(f'/api/projects/{pid}/plan/decision',headers=headers(),json={'approved':True}).status_code==200
    assert client.post(f'/api/projects/{pid}/blueprints/current/waves/wave-01/decision',headers=headers(),json={'approved':True}).status_code==200
    workspace.copy_snapshot(pid)
    (workspace.project_candidate(pid)/'b.py').write_text('def b():\n    return 999\n')
    import pytest
    with pytest.raises(ValueError,match='fuera de la wave'):
        pipeline._validate_changed_paths(pid)
