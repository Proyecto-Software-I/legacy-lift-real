import io,time,zipfile
from fastapi.testclient import TestClient
from app import db,pipeline
from app.api import app


def test_assessment_scores_are_not_invented_and_reviews_are_stored(tmp_path,monkeypatch):
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET','bootstrap-test-secret-with-32-plus-characters')
    with TestClient(app) as c:
        reg=c.post('/api/auth/register',json={'email':'assessment@test.test','password':'strong-password-123456','display_name':'Assessment','bootstrap_secret':'bootstrap-test-secret-with-32-plus-characters'})
        h={'Authorization':'Bearer '+reg.json()['access_token']}
        pid=c.post('/api/projects',headers=h,json={'name':'COBOL demo','system_name':'Payments','provider':'demo'}).json()['id']
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('account.cbl','       PROGRAM-ID. ACCOUNT.\n       *> TODO refine.\n')
        jid=c.post(f'/api/projects/{pid}/upload',headers=h,files={'file':('legacy.zip',b.getvalue())}).json()['id']
        for _ in range(200):
            if db.job_get(pid,jid)['status'] not in ('running','queued'):break
            time.sleep(.015)
        assert db.job_get(pid,jid)['status']=='completed'
        run_id=db.run_create(pid,'understand','demo')
        assert db.runs_get(pid)[-1]['snapshot_id']==db.project_get(pid)['active_snapshot_id']
        pipeline.perform(pid,'understand')
        db.run_complete(run_id,'Understood')
        result,details=pipeline.perform(pid,'assess')
        assert details['confidence_score'] is None
        assert details['metrics'][0]['value']==1
        assert details['signals']['heuristic_findings']>=1
        assert details['recommendations'][0]['strategy']=='Stabilize'
        assert 'sin score arbitrario' in result
        data=c.get(f'/api/projects/{pid}/analysis/assess',headers=h).json()['data']
        assert data['status']=='completed'
        assert any(r['phase']=='assess' for r in c.get(f'/api/projects/{pid}/analysis/reviews',headers=h).json())


def test_assessment_normalizes_ai_module_from_file_and_keeps_questions(tmp_path,monkeypatch):
    import json
    from app import ai
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET','bootstrap-test-secret-with-32-plus-characters')
    monkeypatch.setenv('CLAUDE_API_KEY','test-key')
    with TestClient(app) as c:
        reg=c.post('/api/auth/register',json={'email':'assess-ai@test.test','password':'strong-password-123456','display_name':'Assess AI','bootstrap_secret':'bootstrap-test-secret-with-32-plus-characters'})
        h={'Authorization':'Bearer '+reg.json()['access_token']}
        pid=c.post('/api/projects',headers=h,json={'name':'Py AI','system_name':'Calc','provider':'claude'}).json()['id']
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('calculator.py','def total(x):\n    return (x + 1)\n')
        jid=c.post(f'/api/projects/{pid}/upload',headers=h,files={'file':('calc.zip',b.getvalue())}).json()['id']
        for _ in range(200):
            if db.job_get(pid,jid)['status'] not in ('running','queued'):break
            time.sleep(.015)
        c.post(f'/api/projects/{pid}/analysis/consent',headers=h,json={'approved':True})
        for phase in ('discover','understand'):
            rid=db.run_create(pid,phase,'claude')
            monkeypatch.setattr(ai,'generate',lambda *a,**kw: json.dumps({'summary':'Suma 1','business_rules':[{'statement':'total suma 1','evidence':[{'file':'calculator.py','line':1}]}],'questions':[]}))
            res,det=pipeline.perform(pid,phase)
            db.run_complete(rid,res,det)
        monkeypatch.setattr(ai,'generate',lambda *a,**kw: json.dumps({
            'recommendations':[
                {'module':'calculator.py','strategy':'Refactor','reason':'Simplificar retorno','evidence':[{'file':'calculator.py','line':2}]},
                {'module':'inventado','strategy':'Rewrite','reason':'Sin archivo real','evidence':[{'file':'no_existe.py','line':1}]}
            ],
            'questions':['¿Qué tipos acepta total(x)?']
        }))
        _,details=pipeline.perform(pid,'assess')
        assert len(details['ai_suggestions'])==1
        assert details['ai_suggestions'][0]['module']=='(raíz)'
        assert details['questions']==['¿Qué tipos acepta total(x)?']
