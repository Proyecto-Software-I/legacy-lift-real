from test_flow import client,headers
from test_s4_review import prepared
from app import db,pipeline,migration_verify,migration_runs,migration_review


def test_verify_default_avoids_fake_equivalence(client):
    pid,aid=prepared(client)
    response=client.post(f'/api/projects/{pid}/phases/verify',headers=headers())
    assert response.status_code==200,response.text
    assert response.json()['status']=='queued'
    result=migration_verify.run_one()
    assert result['verify_status']=='completed',result
    report=result['verification']
    assert report['classification']=='not_demonstrated',report
    assert report['outcome']=='sin_evidencia'
    # Orientative score: only build/syntax and lint evidence, never the test points.
    assert report['confidence_score'] is not None and report['confidence_score']<=30
    assert {f['id'] for f in report['confidence_factors']}=={'build','lint','tests','coverage'}
    assert next(f for f in report['confidence_factors'] if f['id']=='tests')['points']==0
    assert report['reviewed_sha256']
    assert db.last_run(pid,'verify')['status']=='completed'


def test_review_invalidates_verify_and_needs_new_execution(client):
    pid,aid=prepared(client)
    assert migration_verify.enqueue(pid)
    assert migration_verify.run_one()['verification']
    row=migration_review.summary(pid,aid)['files'][0]
    changed=migration_review.review(pid,aid,db.project_get(pid)['owner_id'],row['path'],False,'No conservar')
    assert changed['selected_count']==0
    assert migration_runs.get(pid,aid)['verification'] is None
    assert db.last_run(pid,'verify')['status']=='superseded'
    assert client.post(f'/api/projects/{pid}/phases/verify',headers=headers()).status_code==409


def test_comparison_rejects_zero_and_failed_tests():
    st={'static_checks':{'failed':0}}
    old={**st,'tests':{'status':'passed','output':'Ran 0 tests in 0.0s'}}
    new={**st,'tests':{'status':'passed','output':'Ran 0 tests in 0.0s'}}
    assert migration_verify.comparison(old,new)['classification']=='divergent'
    new['tests']={'status':'failed','output':'assert error'}
    assert migration_verify.comparison(old,new)['classification']=='divergent'


def test_verified_within_tests_still_requires_human_decision(client, monkeypatch):
    pid, aid = prepared(client)
    ok_tests = {'status': 'passed', 'exit_code': 0, 'output': 'test_a (t.T) ... ok\n\nRan 1 test in 0.001s\n\nOK'}
    monkeypatch.setattr(migration_verify.baseline, 'capture', lambda _pid, _f: {
        'snapshot_id': db.project_get(pid)['active_snapshot_id'],
        'blueprint_id': db.last_run(pid, 'baseline')['data']['blueprint_id'],
        'wave_id': db.project_get(pid)['active_wave_id'],
        'source_hash': db.last_run(pid, 'baseline')['data']['source_hash'],
        'static_checks': {'total': 2, 'passed': 2, 'failed': 0},
        'tests': ok_tests,
    })
    with db.connect() as conn:
        row = db.last_run(pid, 'baseline')
        d = dict(row['data']); d['tests'] = ok_tests
        conn.execute("UPDATE phase_runs SET data_json=? WHERE id=?", (migration_verify.json.dumps(d), row['id']))
    assert client.post(f'/api/projects/{pid}/phases/verify', headers=headers()).status_code == 200
    result = migration_verify.run_one()
    assert result['verification']['classification'] == 'verified_within_tests'
    assert result['verification']['outcome'] == 'aprobado'
    assert result['verification']['confidence_score'] >= 70
    # Product §9: the result needs an explicit human approval, even when tests pass.
    assert result['decision'] is None
    assert db.project_get(pid)['migration_decision'] is None
    response = client.post(f'/api/projects/{pid}/migration/decision', headers=headers(), json={'approved': True, 'reason': ''})
    assert response.status_code == 200, response.text
    assert db.project_get(pid)['migration_decision'] == 'approved'


def test_divergent_scores_zero():
    st={'static_checks':{'failed':0,'total':1}}
    report=migration_verify.comparison({**st,'tests':{'status':'passed','output':'Ran 1 test'}},{**st,'tests':{'status':'failed'}})
    assert report['outcome']=='fallido' and report['confidence_score']==0
