"""Worker-only verification against the snapshot-bound Behavioral Baseline.

Only passing identical, non-empty test suites are reported as verified WITHIN tests.
The confidence score is orientative and fully explained by its factors; it is never
a proof. No result is auto-approved: the operator always takes the final decision.
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
        conn.execute("INSERT INTO phase_runs(id,project_id,phase,status,started_at,provider,snapshot_id,model) VALUES(?,?,?,?,?,?,?,?)",
                     (rid,pid,'verify','running',db.now(),item['provider'],item['snapshot_id'],'comprobaciones deterministas (sintaxis, lint, pruebas)'))
        conn.execute("UPDATE migration_attempts SET verify_status='queued',verify_run_id=?,verification_json=NULL,reviewed_sha256=NULL WHERE id=?",
                     (rid,item['id']))
    db.project_event(pid,'verify',{'event':'queued','attempt_id':item['id']})
    return {'id':rid,'attempt_id':item['id'],'phase':'verify','status':'queued'}


def _names(result: dict) -> list[str]:
    output=str(result.get('output') or '')
    return sorted(set(re.findall(r'^([\w-]+) \([^\n]+\) \.\.\. (?:ok|FAIL|ERROR)$',output,re.MULTILINE)))


OUTCOME={'verified_within_tests':'aprobado','divergent':'fallido','partial':'sin_evidencia','not_demonstrated':'sin_evidencia'}


def test_coverage(folder, changed: list[str], test_files: list[str]) -> dict:
    """Which changed files are referenced by a detected test file (static import/name match)."""
    from pathlib import Path
    texts=[]
    for rel in test_files[:200]:
        f=Path(folder)/rel
        if f.is_file() and not f.is_symlink():
            texts.append(f.read_text(encoding='utf-8',errors='replace')[:200_000])
    covered=[]
    for rel in changed:
        p=Path(rel);stem=p.stem;dotted='.'.join(p.with_suffix('').parts)
        pattern=re.compile(r'\b(?:import|from)\s+(?:[\w.]*\.)?(?:'+re.escape(stem)+'|'+re.escape(dotted)+r')\b|\bCALL\s+[\'"]'+re.escape(stem)+r'[\'"]',re.I)
        if any(pattern.search(t) for t in texts):covered.append(rel)
    return {'changed_files':len(changed),'covered_by_tests':len(covered),'covered':covered,
            'ratio':round(len(covered)/len(changed),2) if changed else 0.0,
            'method':'Un archivo cuenta como cubierto si algún test lo importa o lo llama por nombre (análisis estático).'}


def confidence(classification:str, pre:dict, post:dict, old:dict, new:dict, lint_before:dict, lint_after:dict, coverage:dict) -> tuple[int,list[dict]]:
    """Orientative 0-100 score. Each factor states its points and evidence; no hidden weights."""
    factors=[]
    syntax_ok=int(pre.get('failed') or 0)==0 and int(post.get('failed') or 0)==0 and int(post.get('total') or 0)>0
    factors.append({'id':'build','label':'Build/sintaxis del candidato','points':20 if syntax_ok else 0,'max':20,
                    'detail':f"{int(post.get('total') or 0)-int(post.get('failed') or 0)} de {int(post.get('total') or 0)} archivos correctos"})
    if lint_before and lint_after and lint_after.get('files_linted'):
        added=int(lint_after.get('warnings') or 0)-int(lint_before.get('warnings') or 0)
        factors.append({'id':'lint','label':'Lint sin avisos nuevos','points':10 if added<=0 else 0,'max':10,
                        'detail':f"{lint_before.get('warnings',0)} avisos antes, {lint_after.get('warnings',0)} después"})
    else:
        factors.append({'id':'lint','label':'Lint sin avisos nuevos','points':0,'max':10,'detail':'Lint no disponible para estos lenguajes'})
    if classification=='verified_within_tests':
        tests_points,detail=50,'Misma suite aprobada antes y después'
    elif classification=='partial':
        tests_points,detail=10,'Pruebas ejecutadas solo en uno de los dos lados'
    else:
        tests_points,detail=0,'Sin pruebas comparables ejecutadas' if classification!='divergent' else 'Las pruebas divergen o fallan'
    factors.append({'id':'tests','label':'Pruebas comparables','points':tests_points,'max':50,'detail':detail})
    cov_points=round(20*float(coverage.get('ratio') or 0)) if tests_points else 0
    factors.append({'id':'coverage','label':'Cambios cubiertos por pruebas','points':cov_points,'max':20,
                    'detail':f"{coverage.get('covered_by_tests',0)} de {coverage.get('changed_files',0)} archivos modificados referenciados por tests"
                             + ('' if tests_points else ' (no cuenta sin pruebas ejecutadas)')})
    score=0 if classification=='divergent' else sum(f['points'] for f in factors)
    return score,factors


def comparison(before:dict,after:dict,coverage:dict|None=None) -> dict:
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
    coverage=coverage or {'changed_files':0,'covered_by_tests':0,'covered':[],'ratio':0.0}
    lint_before=before.get('lint') or {};lint_after=after.get('lint') or {}
    score,factors=confidence(classification,pre,post,old,new,lint_before,lint_after,coverage)
    return {'classification':classification,'outcome':OUTCOME[classification],'reason':reason,'before':old,'after':new,
            'static_before':pre,'static_after':post,'lint_before':{k:lint_before.get(k) for k in ('files_linted','warnings')},
            'lint_after':{k:lint_after.get(k) for k in ('files_linted','warnings')},
            'test_names_before':names_before,'test_names_after':names_after,
            'confidence_score':score,'confidence_factors':factors,'coverage':coverage,
            'confidence_note':'Score orientativo: resume la evidencia ejecutada, no demuestra equivalencia.',
            'limits':'No existe garantía de equivalencia funcional general, incluso con pruebas aprobadas.'}


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
        changed=[f['path'] for f in migration_review.summary(pid,aid)['files'] if f['selected']]
        tests=[t['path'] for t in (recorded['data'].get('test_inventory') or [])]
        result=comparison(recorded['data'],current,test_coverage(folder,changed,tests))
        result.update({'attempt_id':aid,'snapshot_id':item['snapshot_id'],'blueprint_id':item['blueprint_id'],
                       'wave_id':item['wave_id'],'reviewed_sha256':digest,'candidate_sha256':item['candidate_sha256'],
                       'files_selected':migration_review.summary(pid,aid)['selected_count']})
        with db.connect() as conn:
            conn.execute("UPDATE migration_attempts SET verification_json=?,verify_status='completed',reviewed_sha256=? WHERE id=?",
                         (json.dumps(result,ensure_ascii=False),digest,aid))
        labels={'aprobado':'Aprobado dentro de las pruebas','fallido':'Fallido','sin_evidencia':'Sin evidencia suficiente'}
        message='# Verify — Resultado de pruebas y comportamiento\n\n'
        message+=f"**Resultado:** {labels[result['outcome']]} (`{result['classification']}`)\n\n{result['reason']}\n\n"
        message+=f"**Migration Confidence Score (orientativo):** {result['confidence_score']}/100\n\n"
        message+='| Factor | Puntos | Evidencia |\n|---|---|---|\n'
        message+=''.join(f"| {f['label']} | {f['points']}/{f['max']} | {f['detail']} |\n" for f in result['confidence_factors'])
        message+=f"\n**Cobertura de cambios:** {result['coverage']['covered_by_tests']} de {result['coverage']['changed_files']} archivos modificados referenciados por tests. {result['coverage'].get('method','')}\n"
        message+=f"\n**Archivos seleccionados:** {result['files_selected']}\n\n**Límites:** {result['limits']} {result['confidence_note']}\n"
        message+='\n**Decisión humana:** ningún resultado se aprueba automáticamente; la decisión y su motivo quedan en `report.md`.\n'
        from . import pipeline
        prov=pipeline.provenance(pid,'verify',item['provider'],'',{})
        result['provenance']=prov
        workspace.save_artifact(pid,'verification.md',message+pipeline.provenance_md(prov))
        db.run_complete(rid,'Verificación: '+result['classification'],result)
        db.project_event(pid,'verify',{'event':'completed','attempt_id':aid,'classification':result['classification']})
    except Exception as exc:
        with db.connect() as conn:conn.execute("UPDATE migration_attempts SET verify_status='failed',reviewed_sha256=NULL WHERE id=?",(aid,))
        db.run_fail(rid,str(exc)[:500]);db.project_event(pid,'verify',{'event':'failed','attempt_id':aid,'error':str(exc)[:250]})
    return migration_runs.get(pid,aid)
