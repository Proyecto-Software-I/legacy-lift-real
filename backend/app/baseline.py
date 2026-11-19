"""Sprint 3 baseline: discovery, audit, reviewable AI proposals and opt-in execution."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from . import ai, db, execution, inspection, planning, workspace
from .analysis import context as analysis_context


def scope(pid: str) -> tuple[dict,dict]:
    project=db.project_get(pid)
    plan=planning.current(pid)
    if not project or not plan or plan['status']!='approved' or not planning.executable_wave(pid):
        raise ValueError('Se necesita snapshot activo, blueprint y wave aprobados')
    return project,plan


def authorized(pid: str) -> bool:
    try: project,plan=scope(pid)
    except ValueError:return False
    with db.connect() as conn:
        row=conn.execute("""SELECT approved_at,revoked_at FROM baseline_execution_consents
             WHERE project_id=? AND snapshot_id=? AND blueprint_id=? AND wave_id=?""",
             (pid,project['active_snapshot_id'],plan['id'],project['active_wave_id'])).fetchone()
    return bool(row and row['approved_at'] and not row['revoked_at'])


def set_consent(pid:str,uid:str,approved:bool)->bool:
    project,plan=scope(pid)
    with db.connect() as conn:
        conn.execute("""INSERT INTO baseline_execution_consents(project_id,snapshot_id,blueprint_id,wave_id,user_id,approved_at,revoked_at)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,snapshot_id,blueprint_id,wave_id)
          DO UPDATE SET user_id=excluded.user_id,approved_at=excluded.approved_at,revoked_at=excluded.revoked_at""",
          (pid,project['active_snapshot_id'],plan['id'],project['active_wave_id'],uid,db.now() if approved else None, None if approved else db.now()))
    db.project_event(pid,'baseline',{'event':'consent','approved':approved})
    # Revocation invalidates all dependent evidence; previous results are not deleted from run history.
    db.invalidate_stages(pid,('baseline','modernize','verify'))
    for name in ('baseline.md','baseline.json','migration.md','changes.patch','verification.md','report.md'):
        (workspace.artifact_dir(pid)/name).unlink(missing_ok=True)
    return authorized(pid)


def discover_tests(folder:Path)->list[dict]:
    found=[]
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink():continue
        path=file.relative_to(folder).as_posix();name=file.name.lower()
        framework=None
        if file.suffix=='.py' and (name.startswith('test_') or path.startswith('tests/')):framework='python-unittest/pytest (discovery only)'
        elif name in ('phpunit.xml','phpunit.xml.dist'):framework='PHPUnit (discovery only)'
        elif name in ('pom.xml','build.gradle','build.gradle.kts'):framework='Java build manifest (not executed)'
        elif file.suffix.lower() in ('.cbl','.cob') and 'test' in name:framework='COBOL test candidate (not executed)'
        if framework:found.append({'path':path,'framework':framework})
        if len(found)>=300:break
    return found


def proposals(pid:str)->list[dict]:
    project,plan=scope(pid)
    with db.connect() as conn:
        rows=conn.execute("""SELECT * FROM baseline_test_proposals
          WHERE project_id=? AND snapshot_id=? AND blueprint_id=? AND wave_id=? ORDER BY created_at,id""",
          (pid,project['active_snapshot_id'],plan['id'],project['active_wave_id'])).fetchall()
    return [{**dict(row),'data':json.loads(row['data_json'])} for row in rows]


def suggest(pid:str)->list[dict]:
    project,plan=scope(pid)
    wave=planning.executable_wave(pid)
    if project['provider']=='demo':return proposals(pid)  # No fake AI-generated suggestions.
    if not db.consent_is_active(pid,project['active_snapshot_id'],project['provider']):
        raise ValueError('Autoriza explícitamente el envío de código al proveedor de IA')
    files={x.relative_to(workspace.project_source(pid)).as_posix() for x in workspace.project_source(pid).rglob('*') if x.is_file()}
    prompt=('Propón hasta 8 casos de prueba de comportamiento para esta wave; NO generes código ejecutable. '
      'Devuelve SOLO JSON {"cases":[{"title":"...","input":"...","expected":"...","evidence_file":"..."}]}. '
      'Cada evidence_file debe ser uno de estos archivos reales: '+json.dumps(wave['files'])+'\n'
      'Contexto: '+str(db.analysis_get(pid,project['active_snapshot_id'],'understand') or {})[:2600])
    try:
        if project['provider'] in ai.LOCAL_AGENTS:
            with analysis_context.scoped_workspace(pid,workspace.project_source(pid)) as isolated:
                response=ai.generate(project['provider'],prompt,isolated)
        else:response=ai.generate('claude',prompt,workspace.project_source(pid))
        match=re.search(r'\{[\s\S]*\}',response)
        try:data=json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:data={}
        entries=data.get('cases') if isinstance(data,dict) else None
    except ai.AIError as exc:
        # No synthetic "AI" proposals when the model is unavailable: report the failure.
        if ai.is_quota_error(exc):
            raise ValueError('La cuota del proveedor de IA está agotada; no se generaron propuestas. Reintenta más tarde.') from exc
        raise ValueError(str(exc)) from exc
    if not isinstance(entries,list):return proposals(pid)
    for case in entries[:8]:
        if not isinstance(case,dict):continue
        title=case.get('title');inp=case.get('input');expected=case.get('expected');evidence=case.get('evidence_file')
        if not (all(isinstance(v,str) for v in (title,inp,expected,evidence))
                and evidence in wave['files'] and 3<=len(title)<=150 and 1<=len(expected)<=500):continue
        entry={'title':title[:150],'input':inp[:500],'expected':expected[:500],'evidence_file':evidence,
               'source':'ai_proposed','note':'No ejecutable: requiere revisión y posterior implementación del test.'}
        digest=hashlib.sha256((plan['id']+project['active_wave_id']+json.dumps(entry,sort_keys=True)).encode()).hexdigest()[:30]
        with db.connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO baseline_test_proposals
              (id,project_id,snapshot_id,blueprint_id,wave_id,status,data_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
              (digest,pid,project['active_snapshot_id'],plan['id'],project['active_wave_id'],'pending',json.dumps(entry,ensure_ascii=False),db.now()))
    db.project_event(pid,'baseline',{'event':'suggestions','count':len(proposals(pid))})
    return proposals(pid)


def review(pid:str,uid:str,case_id:str,approved:bool)->dict:
    project,plan=scope(pid)
    with db.connect() as conn:
        result=conn.execute("""UPDATE baseline_test_proposals SET status=?,reviewed_at=?,reviewed_by=?
          WHERE id=? AND project_id=? AND snapshot_id=? AND blueprint_id=? AND wave_id=?""",
          ('approved' if approved else 'rejected',db.now(),uid,case_id,pid,project['active_snapshot_id'],plan['id'],project['active_wave_id']))
        if not result.rowcount:raise ValueError('Caso de prueba no encontrado')
    db.project_event(pid,'baseline',{'event':'case-review','id':case_id,'approved':approved})
    return next(x for x in proposals(pid) if x['id']==case_id)


def capture(pid:str,folder:Path)->dict:
    project,plan=scope(pid)
    checks=inspection.static_checks(folder)
    output=execution.run_authorized(folder,authorized(pid))
    test_inventory=discover_tests(folder)
    approved=[c['id'] for c in proposals(pid) if c['status']=='approved']
    return {'schema_version':1,'snapshot_id':project['active_snapshot_id'],'blueprint_id':plan['id'],
            'blueprint_revision':plan['revision'],'wave_id':project['active_wave_id'],
            'source_hash':workspace.snapshot_hash(folder), 'static_checks':inspection.check_summary(checks),
            'lint':inspection.lint_checks(folder),
            'details':checks[:250],'test_inventory':test_inventory,'test_cases_approved':approved,
            'tests':output,'execution_authorized':authorized(pid),
            'gaps':gaps(test_inventory,output,authorized(pid),approved),
            'methodology':'Sintaxis y manifiestos no prueban equivalencia; los casos propuestos por IA son solo especificaciones revisables.'}


def gaps(test_inventory:list[dict],tests:dict,execution_authorized:bool,approved_cases:list[str])->list[str]:
    """Explicit list of what this baseline cannot prove (product §2.4: register the gap)."""
    out=[]
    if not test_inventory:
        out.append('No se detectó ninguna suite de pruebas: no habrá comparación de comportamiento, solo de sintaxis.')
    if tests.get('status')!='passed':
        if not execution_authorized:
            out.append('La ejecución de pruebas no fue autorizada: el baseline solo registra sintaxis y estructura.')
        else:
            out.append('Las pruebas no se ejecutaron con éxito ('+str(tests.get('status'))+'): '+str(tests.get('reason') or 'sin detalle'))
    if approved_cases:
        out.append(f'{len(approved_cases)} casos propuestos por IA están aprobados pero aún no son tests ejecutables.')
    return out


def markdown(data:dict)->str:
    s=data['static_checks'];t=data['tests']
    lines=['# Behavioral Baseline','',f"Snapshot: `{data['snapshot_id']}`",f"Blueprint: `{data['blueprint_id']}` rev. {data['blueprint_revision']}",
           f"Wave aprobada: `{data['wave_id']}`",f"Hash de código: `{data['source_hash']}`",'',
           '## Comprobaciones estáticas',f"- {s['passed']} / {s['total']} superadas; {s['failed']} fallidas",'',
           '## Pruebas detectadas']
    lines += [f"- `{x['path']}` — {x['framework']}" for x in data['test_inventory']] or ['- No se detectaron suites conocidas.']
    lines += ['', '## Pruebas ejecutadas',f"- Resultado: **{t['status']}**",'- '+t.get('reason','Ejecución limitada a Docker de desarrollo con autorización explícita.'),
              f"- Casos propuestos por IA y aprobados: {len(data['test_cases_approved'])} (no ejecutados automáticamente)"]
    lint=data.get('lint') or {}
    if lint:
        lines += ['', '## Lint',f"- {lint.get('warnings',0)} avisos en {lint.get('files_linted',0)} archivos Python; {lint.get('files_not_supported',0)} archivos de otros lenguajes sin lint disponible"]
    lines += ['', '## Carencias de verificación']
    lines += [f'- {g}' for g in data.get('gaps') or []] or ['- Ninguna detectada: hay pruebas ejecutadas para comparar.']
    lines += ['', 'Este documento no certifica equivalencia entre versiones.']
    return '\n'.join(lines)+'\n'
