"""Sprint 3: versioned, evidence-based blueprint; human approval is a separate operation.

A draft is only a proposal, never an executable authorization. The active SourceSnapshot
is part of every key. Editing creates a new immutable revision and clears approvals.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from . import db, workspace

SUPPORTED = {'Python', 'JavaScript', 'TypeScript', 'Java', 'C#', 'Go'}
STRATEGIES = {'Keep', 'Stabilize', 'Encapsulate', 'Refactor', 'Rewrite', 'LanguageMigration'}


def current(pid: str) -> dict | None:
    project = db.project_get(pid)
    if not project or not project.get('active_snapshot_id'):
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM modernization_blueprints WHERE project_id=? AND snapshot_id=? ORDER BY revision DESC LIMIT 1",
          (pid, project['active_snapshot_id'])).fetchone()
    return _read(row) if row else None


def history(pid: str) -> list[dict]:
    project = db.project_get(pid)
    if not project or not project.get('active_snapshot_id'):
        return []
    with db.connect() as conn:
        rows=conn.execute("SELECT * FROM modernization_blueprints WHERE project_id=? AND snapshot_id=? ORDER BY revision DESC",(pid,project['active_snapshot_id'])).fetchall()
    return [_read(row) for row in rows]


def _read(row: Any) -> dict:
    obj=dict(row)
    obj['data']=json.loads(obj.pop('data_json'))
    with db.connect() as conn:
        obj['wave_approvals']={r['wave_id']:r['status'] for r in conn.execute(
            'SELECT wave_id,status FROM blueprint_wave_approvals WHERE blueprint_id=?',(obj['id'],))}
    return obj


def _source_files(pid: str) -> tuple[set[str], dict[str,str]]:
    # Do not trust user-supplied paths: derive everything from the active snapshot.
    source=workspace.project_source(pid)
    files={x.relative_to(source).as_posix() for x in source.rglob('*') if x.is_file() and not x.is_symlink()}
    info=db.analysis_get(pid,db.project_get(pid)['active_snapshot_id'],'discover') or {}
    languages={x['path']:x.get('language','Otro') for x in info.get('file_index',[])}
    return files,languages


def _text_value(value: object, joiner: str = '; ') -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        joined = joiner.join(item.strip() for item in value if item.strip())
        return joined
    return None


def validate(pid: str, data: dict) -> dict:
    if not isinstance(data,dict): raise ValueError('Blueprint debe ser un objeto JSON')
    name=_text_value(data.get('title'), ' ')
    target=_text_value(data.get('target_architecture'), '\n')
    waves=data.get('waves')
    if not name or not 3<=len(name)<=140:raise ValueError('Título inválido')
    if not target or not 10<=len(target)<=2500:raise ValueError('Arquitectura objetivo inválida')
    if not isinstance(waves,list) or not 1<=len(waves)<=30:raise ValueError('Se requieren entre 1 y 30 waves')
    files,langs=_source_files(pid)
    seen=set();out=[]
    for wave in waves:
        if not isinstance(wave,dict): raise ValueError('Wave inválida')
        wid=wave.get('id'); scope=wave.get('files'); deps=wave.get('depends_on',[])
        if not isinstance(wid,str) or not wid or len(wid)>60 or wid in seen:raise ValueError('IDs de waves inválidos o duplicados')
        seen.add(wid)
        if not isinstance(scope,list) or not 1<=len(scope)<=5 or any(not isinstance(f,str) or f not in files for f in scope) or len(set(scope))!=len(scope):
            raise ValueError(f'Archivos inválidos para wave {wid}; máximo cinco existentes')
        if not isinstance(deps,list) or any(not isinstance(dep,str) or dep not in seen or dep==wid for dep in deps):
            raise ValueError(f'Dependencias inválidas o cíclicas en {wid}; waves deben estar en orden topológico')
        strategy=wave.get('strategy')
        if strategy not in STRATEGIES:raise ValueError(f'Estrategia no soportada: {strategy}')
        normalized={}
        for key, limit in (('goal', 1200), ('acceptance_criteria', 2000), ('risk', 1200)):
            value=_text_value(wave.get(key))
            if not value or not 4<=len(value)<=limit:raise ValueError(f'{key} inválido en {wid}')
            normalized[key]=value
        priority=wave.get('priority',2)
        if isinstance(priority,str) and priority.strip() in {'1','2','3'}:
            priority=int(priority.strip())
        if isinstance(priority,bool) or not isinstance(priority,int) or priority not in (1,2,3):raise ValueError('Prioridad debe ser 1, 2 o 3')
        out.append({'id':wid,'goal':normalized['goal'],'strategy':strategy,'files':scope,
                    'depends_on':deps,'priority':priority,'risk':normalized['risk'],
                    'acceptance_criteria':normalized['acceptance_criteria']})
    notes=_text_value(data.get('notes',''), '\n')
    return {'schema_version':1,'title':name,'target_architecture':target,
            'source_snapshot_id':db.project_get(pid)['active_snapshot_id'],
            'waves':out,'notes':(notes if notes is not None else str(data.get('notes','')))[:2000],
            'limitations':'Las waves son propuestas. Un pack y aprobación humana determinan ejecutabilidad.'}


def save_revision(pid: str, payload: dict) -> dict:
    project=db.project_get(pid)
    if not project or not project.get('active_snapshot_id'):raise ValueError('Importa un snapshot antes de planificar')
    data=validate(pid,payload)
    with db.connect() as conn:
        last=conn.execute('SELECT MAX(revision) AS r FROM modernization_blueprints WHERE project_id=? AND snapshot_id=?',
                          (pid,project['active_snapshot_id'])).fetchone()
        revision=(last['r'] or 0)+1; bid=str(uuid.uuid4())
        conn.execute("UPDATE modernization_blueprints SET status='superseded' WHERE project_id=? AND snapshot_id=? AND status='draft'",
                     (pid,project['active_snapshot_id']))
        conn.execute('INSERT INTO modernization_blueprints(id,project_id,snapshot_id,revision,status,data_json,created_at) VALUES (?,?,?,?,?,?,?)',
                     (bid,pid,project['active_snapshot_id'],revision,'draft',json.dumps(data,ensure_ascii=False),db.now()))
        conn.execute('UPDATE projects SET plan_approved=NULL,migration_decision=NULL,active_wave_id=NULL,active_pack_id=NULL WHERE id=?',(pid,))
    from . import pipeline
    db.invalidate_stages(pid,('baseline','modernize','verify'))
    for name in ('baseline.md','baseline.json','changes.patch','migration.md','verification.md','report.md'):
        (workspace.artifact_dir(pid)/name).unlink(missing_ok=True)
    _artifacts(pid,data,revision)
    db.project_event(pid,'blueprint',{'revision':revision,'status':'draft','snapshot_id':project['active_snapshot_id']})
    return current(pid)


def _artifacts(pid: str, data: dict, revision: int) -> None:
    workspace.save_artifact(pid,'blueprint.json',json.dumps({'revision':revision,**data},ensure_ascii=False,indent=2))
    lines=['# '+data['title'],'','Blueprint revisión '+str(revision),'','## Arquitectura objetivo',data['target_architecture'],'','## Waves']
    for w in data['waves']:
        lines += ['','### '+w['id']+' — '+w['goal'],f"- Estrategia: {w['strategy']}",
                  '- Archivos: '+', '.join('`'+f+'`' for f in w['files']),
                  '- Dependencias: '+(', '.join(w['depends_on']) or 'ninguna'),
                  '- Prioridad: '+str(w['priority']),'- Riesgo: '+w['risk'],
                  '- Aceptación: '+w['acceptance_criteria']]
    lines += ['','**Importante:** no se ha autorizado ni ejecutado ninguna modificación.']
    workspace.save_artifact(pid,'blueprint.md','\n'.join(lines)+'\n')


def proposal(pid: str, notes: str = '', provider: str = 'demo') -> dict:
    """Prefer validated structured AI recommendations; otherwise fall back to safe static draft.

    No model output, regardless of provider, can authorize execution or name files
    outside the immutable source snapshot.
    """
    project=db.project_get(pid)
    inv=db.analysis_get(pid,project['active_snapshot_id'],'discover') or {}
    assessed=db.analysis_get(pid,project['active_snapshot_id'],'assess') or {}
    paths,languages=_source_files(pid)
    # Exclude tests/config: the only executable MVP pack changes existing source code.
    allowed={'.py','.js','.ts','.java','.cs','.go','.php','.cbl','.cob','.cobol'}
    eligible=[path for path in sorted(paths) if Path(path).suffix.lower() in allowed
              and not any('test' in part.lower() for part in Path(path).parts)]
    if not eligible:raise ValueError('No hay código fuente modernizable en el snapshot')
    from collections import defaultdict
    groups=defaultdict(list)
    for path in eligible: groups[path.split('/')[0] if '/' in path else '(raíz)'].append(path)
    strategy_by_module={r['module']:r['strategy'] for r in assessed.get('recommendations',[])
                        if isinstance(r,dict) and r.get('strategy') in STRATEGIES}
    waves=[]
    for module,files in list(groups.items())[:30]:
        for part in range(0,len(files),5):
            chunk=files[part:part+5]
            ix=len(waves)+1;wid=f'wave-{ix:02d}'
            waves.append({'id':wid,'goal':f'Mejorar {module} sin alterar contratos públicos',
                          'strategy':strategy_by_module.get(module,'Refactor'),'files':chunk,
                          'depends_on':[waves[-1]['id']] if waves else [],'priority':1 if ix==1 else 2,
                          'risk':'Cambios podrían alterar comportamiento; requiere baseline y revisión.',
                          'acceptance_criteria':'Comparar diff y resultados con el baseline antes de aprobar.'})
            if len(waves)>=30:break
        if len(waves)>=30:break
    if provider in {'claude','codex'}:
        from .analysis.understanding import _json_object
        # The parser requires valid structured data, not a prose summary that we guess about.
        # AI can propose multiple waves and migration strategies, but validate() checks paths.
        parsed=_json_object(notes)
        failure_reason='respuesta no es un JSON válido'
        if parsed is not None:
            try:
                ai_notes=_text_value(parsed.get('notes',''), '\n') or ''
                parsed['notes']=('Propuesta generada por IA, pendiente de aprobación humana.' + (f'\n{ai_notes}' if ai_notes else ''))[:2000]
                return save_revision(pid,parsed)
            except (ValueError,TypeError) as exc:
                failure_reason=str(exc)[:180]
        notes=f'La IA no entregó un Blueprint JSON validable ({failure_reason}); se generó un plan estático para edición manual.'
    else:
        notes='Propuesta estática, sin inferencias de IA. Requiere revisión humana.'
    return save_revision(pid,{'title':'Modernization Blueprint — '+project['name'],
      'target_architecture':'Mantener contratos externos y modularizar gradualmente. Las estrategias y las dependencias son propuestas iniciales revisables, no decisiones de IA verificadas.',
      'waves':waves,'notes':notes})


def decide(pid: str, uid: str, approved: bool) -> dict:
    item=current(pid)
    if not item or item['status']!='draft':raise ValueError('No existe un blueprint pendiente de aprobación')
    with db.connect() as conn:
        conn.execute("UPDATE modernization_blueprints SET status=?,decided_at=?,decided_by=? WHERE id=? AND status='draft'",
                     ('approved' if approved else 'rejected',db.now(),uid,item['id']))
        conn.execute('UPDATE projects SET plan_approved=? WHERE id=?',(1 if approved else 0,pid))
    db.project_event(pid,'blueprint',{'revision':item['revision'],'status':'approved' if approved else 'rejected'})
    return current(pid)


def active_wave(pid: str) -> dict | None:
    item=current(pid)
    if not item or item['status']!='approved':return None
    approvals=item['wave_approvals']
    for wave in item['data']['waves']:
        if approvals.get(wave['id'])=='approved':return wave
    return None


def wave_decision(pid: str, wid: str, uid: str, approved: bool) -> dict:
    from . import migration_packs
    plan=current(pid)
    if not plan or plan['status']!='approved' or db.project_get(pid)['plan_approved']!=1:
        raise ValueError('Primero aprueba el blueprint')
    wave=next((w for w in plan['data']['waves'] if w['id']==wid),None)
    if wave is None:raise ValueError('Wave inexistente en el blueprint activo')
    if approved:
        for prerequisite in wave['depends_on']:
            if plan['wave_approvals'].get(prerequisite)!='approved':
                raise ValueError(f'Primero aprueba la wave dependiente {prerequisite}')
        matches=[x for x in migration_packs.compatible(pid,wave) if x['compatible']]
        if not matches:raise ValueError('La wave no tiene un Migration Pack ejecutable compatible')
        pack=matches[0]['id']
    previous_active=db.project_get(pid).get('active_wave_id')
    with db.connect() as conn:
        conn.execute("INSERT INTO blueprint_wave_approvals(blueprint_id,wave_id,status,user_id,decided_at) VALUES(?,?,?,?,?) ON CONFLICT(blueprint_id,wave_id) DO UPDATE SET status=excluded.status,user_id=excluded.user_id,decided_at=excluded.decided_at",
                     (plan['id'],wid,'approved' if approved else 'rejected',uid,db.now()))
        if approved:
            conn.execute('UPDATE projects SET active_wave_id=?, active_pack_id=? WHERE id=?',(wid,pack,pid))
        else:
            conn.execute('UPDATE projects SET active_wave_id=NULL, active_pack_id=NULL WHERE id=? AND active_wave_id=?',(pid,wid))
    new_active=db.project_get(pid).get('active_wave_id')
    if previous_active!=new_active:
        db.invalidate_stages(pid,('baseline','modernize','verify'))
        for name in ('baseline.md','baseline.json','migration.md','changes.patch','verification.md','report.md'):
            (workspace.artifact_dir(pid)/name).unlink(missing_ok=True)
        db.project_update(pid,migration_decision=None)
    db.project_event(pid,'wave',{'id':wid,'status':'approved' if approved else 'rejected','revision':plan['revision']})
    return current(pid)


def select_wave(pid: str, wid: str) -> dict:
    from . import migration_packs
    item=current(pid)
    if not item or item['status']!='approved' or item['wave_approvals'].get(wid)!='approved':
        raise ValueError('Solo puedes seleccionar una wave aprobada del blueprint actual')
    wave=next((w for w in item['data']['waves'] if w['id']==wid),None)
    if not wave:raise ValueError('Wave no encontrada')
    matches=[m for m in migration_packs.compatible(pid,wave) if m['compatible']]
    if not matches:raise ValueError('La wave ya no tiene un pack compatible')
    db.project_update(pid,active_wave_id=wid,active_pack_id=matches[0]['id'],migration_decision=None)
    db.invalidate_stages(pid,('baseline','modernize','verify'))
    for filename in ('baseline.md','baseline.json','migration.md','changes.patch','verification.md','report.md'):
        (workspace.artifact_dir(pid)/filename).unlink(missing_ok=True)
    db.project_event(pid,'wave',{'id':wid,'status':'selected'})
    return current(pid)


def executable_wave(pid: str) -> dict | None:
    from . import migration_packs
    item=current(pid);proj=db.project_get(pid)
    if not proj or not item or item['status']!='approved' or proj['plan_approved']!=1:return None
    wid=proj.get('active_wave_id')
    if not wid or item['wave_approvals'].get(wid)!='approved':return None
    wave=next((w for w in item['data']['waves'] if w['id']==wid),None)
    return wave if wave and migration_packs.selected_pack(pid,wave) else None
