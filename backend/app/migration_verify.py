"""Worker-only verification against the snapshot-bound Behavioral Baseline.

Only passing identical, non-empty test suites are reported as verified WITHIN tests.
No fabricated confidence score. A failure or divergence is never auto-approved.
"""
from __future__ import annotations
import json
import re
import hashlib
from . import baseline,db,migration_review,migration_runs,workspace


def enqueue(pid: str) -> dict:
    item=migration_runs.latest_completed(pid)
    if not item:raise ValueError('No hay un intento completo de Modernize para verificar')
    migration_review._attempt(pid,item['id'])
    if not migration_review.summary(pid,item['id'])['selected_count']:
        raise ValueError('Selecciona al menos un archivo del diff')
    baseline_run=db.last_run(pid,'baseline')
    if not baseline_run or baseline_run['status']!='completed':raise ValueError('Falta el baseline previo')
    if item.get('verify_status') in ('queued','running'):
        raise ValueError('Verify ya está en curso')
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        fresh=conn.execute('SELECT verify_status FROM migration_attempts WHERE id=?',(item['id'],)).fetchone()
        if fresh['verify_status'] in ('queued','running'):raise ValueError('Verify ya está en curso')
        import uuid
        rid=str(uuid.uuid4())
        conn.execute("INSERT INTO phase_runs(id,project_id,phase,status,started_at,provider) VALUES(?,?,?,?,?,?)",
                     (rid,pid,'verify','running',db.now(),item['provider']))
        conn.execute("UPDATE migration_attempts SET verify_status='queued',verify_run_id=?,verification_json=NULL,reviewed_sha256=NULL WHERE id=?",
                     (rid,item['id']))
    db.project_event(pid,'verify',{'event':'queued','attempt_id':item['id']})
    return {'id':rid,'attempt_id':item['id'],'phase':'verify','status':'queued'}


def _names(result: dict) -> list[str]:
    output=str(result.get('output') or '')
    return sorted(set(re.findall(r'^([\w-]+) \([^\n]+\) \.\.\. (?:ok|FAIL|ERROR)$',output,re.MULTILINE)))


def comparison(before:dict,after:dict) -> dict:
    old=before.get('tests',{}) or {};new=after.get('tests',{}) or {}
    pre=before.get('static_checks',{}) or {};post=after.get('static_checks',{}) or {}
    names_before=_names(old);names_after=_names(new)
    reason='No se ejecutaron suites comparables en ambos lados.'
    classification='not_demonstrated'
    if int(pre.get('failed') or 0)>0 or int(post.get('failed') or 0)>0 or new.get('status')=='failed':
        classification='divergent';reason='Falló una comprobación del candidato o ya existían fallos de sintaxis.'
    elif old.get('status')=='passed' and new.get('status')=='passed':
        # Zero tests cannot be called passed; refuse to infer equivalence from empty logs.
        old_count=re.search(r'Ran (\d+) tests?',str(old.get('output') or ''))
        new_count=re.search(r'Ran (\d+) tests?',str(new.get('output') or ''))
        if not old_count or not new_count or int(old_count.group(1))<1 or int(old_count.group(1))!=int(new_count.group(1)):
            classification='divergent';reason='Cambió el número de pruebas ejecutadas o falta evidencia del número.'
        elif names_before and names_after and names_before != names_after:
            classification='divergent';reason='Cambió el conjunto de casos de prueba ejecutados.'
        else:
            classification='verified_within_tests';reason='Misma suite de pruebas aprobada antes y después. Solo demuestra los casos ejercitados.'
    elif new.get('status')=='passed' or old.get('status')=='passed':
        classification='partial';reason='Solo existe ejecución de pruebas en uno de los dos lados.'
    return {'classification':classification,'reason':reason,'before':old,'after':new,
            'static_before':pre,'static_after':post,'test_names_before':names_before,'test_names_after':names_after,
            'confidence_score':None,'limits':'No existe garantía de equivalencia funcional general, incluso con pruebas aprobadas.'}


def run_one() -> dict | None:
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM migration_attempts WHERE verify_status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:return None
        conn.execute("UPDATE migration_attempts SET verify_status='running' WHERE id=? AND verify_status='queued'",(row['id'],))
        aid=row['id'];pid=row['project_id'];rid=row['verify_run_id']
    try:
        item=migration_review._attempt(pid,aid)
        if migration_runs.latest_completed(pid)['id']!=aid:
            raise ValueError('El intento dejó de ser el candidato actual')
        recorded=db.last_run(pid,'baseline')
        if not recorded or recorded['status']!='completed' or recorded['data'].get('source_hash')!=item['source_hash']:
            raise ValueError('Baseline no válido para este intento')
        folder,digest=migration_review.assemble(pid,aid)
        current=baseline.capture(pid,folder)
        result=comparison(recorded['data'],current)
        result.update({'attempt_id':aid,'snapshot_id':item['snapshot_id'],'blueprint_id':item['blueprint_id'],
                       'wave_id':item['wave_id'],'reviewed_sha256':digest,'candidate_sha256':item['candidate_sha256'],
                       'files_selected':migration_review.summary(pid,aid)['selected_count']})
        with db.connect() as conn:
            conn.execute("UPDATE migration_attempts SET verification_json=?,verify_status='completed',reviewed_sha256=? WHERE id=?",
                         (json.dumps(result,ensure_ascii=False),digest,aid))
        message='# Verify — Resultado de pruebas y comportamiento\n\n'
        message+=f"**Clasificación:** {result['classification']}\n\n{result['reason']}\n\n"
        message+=f"**Archivos seleccionados:** {result['files_selected']}\n\n{result['limits']}\n"
        workspace.save_artifact(pid,'verification.md',message)
        if result['classification']=='verified_within_tests':
            from . import migration_delivery
            owner_id=(db.project_get(pid) or {}).get('owner_id') or 'system'
            with db.connect() as conn:
                conn.execute("""UPDATE migration_attempts SET decision='approved',decided_at=?,decided_by=?,result_json=?
                  WHERE id=? AND decision IS NULL""",
                  (db.now(),owner_id,json.dumps({**item.get('result',{}),'decision_reason':'Auto-aprobado: pruebas equivalentes superadas en Verify.'},ensure_ascii=False),aid))
                conn.execute("UPDATE projects SET migration_decision='approved' WHERE id=?",(pid,))
            workspace.save_artifact(pid,'report.md',migration_delivery.report(pid,aid)+'\n**Verificación automática:** Pruebas equivalentes superadas.\n')
        db.run_complete(rid,'Verificación: '+result['classification'],result)
        db.project_event(pid,'verify',{'event':'completed','attempt_id':aid,'classification':result['classification']})
    except Exception as exc:
        with db.connect() as conn:conn.execute("UPDATE migration_attempts SET verify_status='failed',reviewed_sha256=NULL WHERE id=?",(aid,))
        db.run_fail(rid,str(exc)[:500]);db.project_event(pid,'verify',{'event':'failed','attempt_id':aid,'error':str(exc)[:250]})
    return migration_runs.get(pid,aid)
