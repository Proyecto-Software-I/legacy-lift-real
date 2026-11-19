"""Explicit, auditable approval and self-contained exports from immutable reviewed code.

No automatic promotion on Verify. Rejection preserves audit history and never
modifies the source snapshot. An insufficient result needs a meaningful human reason.
"""
from __future__ import annotations
import difflib
import io
import json
import shutil
import zipfile
from pathlib import Path
from . import db, migration_review, migration_runs, workspace


def _latest(pid: str) -> dict:
    a=migration_runs.latest_completed(pid)
    if not a:raise ValueError('Primero ejecuta Modernize y revisa un candidato')
    migration_review._attempt(pid,a['id'])
    return a


def report(pid:str,aid:str) -> str:
    a=migration_runs.get(pid,aid)
    if not a:raise ValueError('Intento inexistente')
    v=a.get('verification') or {}
    s=migration_review.summary(pid,aid)
    lines=['# Informe de modernización LegacyLift','',f"- Intento: `{aid}`",f"- Snapshot: `{a['snapshot_id']}`",
      f"- Blueprint: `{a['blueprint_id']}` (rev. {a['blueprint_revision']})",f"- Wave: `{a['wave_id']}`",
      f"- Pack: `{a['pack_id']}`",f"- Decisión humana: **{ {'approved':'aprobada','rejected':'rechazada'}.get(a.get('decision') or '','pendiente') }**",'',
      '## Cambios incluidos']
    for f in s['files']:
        lines.append(f"- {'INCLUIDO' if f['selected'] else 'EXCLUIDO'} `{f['path']}`: {f['summary']} (+{f['added']}/-{f['removed']})")
        if f['comment']:lines.append('  - Comentario: '+f['comment'].replace('\n',' ')[:200])
    outcome={'aprobado':'Aprobado dentro de las pruebas','fallido':'Fallido','sin_evidencia':'Sin evidencia suficiente'}
    lines+=['','## Verificación',f"- Resultado: **{outcome.get(v.get('outcome'),'no ejecutada')}** (`{v.get('classification','no ejecutada')}`)",
            f"- Migration Confidence Score (orientativo): {v.get('confidence_score','N/D')}/100",
            f"- Motivo: {v.get('reason','No existen evidencias de Verify para este intento')}",
            f"- Límite: {v.get('limits','No se puede afirmar equivalencia sin pruebas comparables')}"]
    if a.get('result',{}).get('decision_reason'):
        lines += ['', '## Justificación humana', a['result']['decision_reason']]
    for factor in v.get('confidence_factors') or []:
        lines.append(f"  - {factor['label']}: {factor['points']}/{factor['max']} — {factor['detail']}")
    if a.get('result'):lines += ['', '## Archivos', str(a['result'].get('modified_files',[]))]
    prov=(a.get('result') or {}).get('provenance') or {}
    if prov:
        lines += ['', '## Procedencia del candidato', f"- Proveedor `{prov.get('provider')}` · modelo `{prov.get('model')}` · plantilla `{prov.get('prompt_template')}`",
                  f"- Motor realmente usado: {prov.get('engine')}"]
    return '\n'.join(lines)+'\n'


def selected_diff(pid:str,aid:str) -> str:
    a=_latest(pid)
    if a['id']!=aid:raise ValueError('Solo puede entregarse el intento actual')
    reviewed=migration_review.reviewed_path(pid,aid)
    if not reviewed.is_dir() or workspace.snapshot_hash(reviewed)!=a['reviewed_sha256']:
        raise ValueError('Los archivos revisados no coinciden con Verify')
    original=workspace.project_source(pid)
    parts=[]
    for f in migration_review.summary(pid,aid)['files']:
        if not f['selected']:continue
        name=f['path'];before=(original/name).read_text(encoding='utf-8',errors='replace').splitlines(keepends=True)
        after=(reviewed/name).read_text(encoding='utf-8',errors='replace').splitlines(keepends=True)
        body=''.join(difflib.unified_diff(before,after,fromfile='a/'+name,tofile='b/'+name))
        parts.append('diff --git a/'+name+' b/'+name+'\n'+body)
    return ''.join(parts)


def decide(pid:str,uid:str,approved:bool,reason:str='') -> dict:
    a=_latest(pid)
    v=a.get('verification') or {};classification=v.get('classification')
    if a.get('decision'):
        raise ValueError('El intento ya tiene una decisión registrada')
    if a.get('verify_status')!='completed' or not a.get('verification') or not a.get('reviewed_sha256'):
        raise ValueError('Antes de decidir ejecuta Verify sobre los archivos seleccionados')
    if approved and classification=='divergent':
        raise ValueError('No se puede aprobar un resultado divergente. Revisa y reintenta la wave.')
    if approved and classification!='verified_within_tests' and len(reason.strip())<20:
        raise ValueError('Verificación insuficiente: explica en 20 o más caracteres por qué aceptas el riesgo')
    if not isinstance(reason,str) or len(reason)>2000:raise ValueError('Motivo inválido')
    reviewed=migration_review.reviewed_path(pid,a['id'])
    if not reviewed.is_dir() or workspace.snapshot_hash(reviewed)!=a['reviewed_sha256']:
        raise ValueError('La versión revisada fue alterada después de Verify')
    # Verify source hash must remain unchanged since baseline.
    if workspace.snapshot_hash(workspace.project_source(pid))!=a['source_hash']:
        raise ValueError('Cambió la fuente original desde la verificación')
    with db.connect() as conn:
        cur=conn.execute("""UPDATE migration_attempts SET decision=?,decided_at=?,decided_by=?,result_json=?
          WHERE id=? AND decision IS NULL AND verify_status='completed' """,
          ('approved' if approved else 'rejected',db.now(),uid,
           json.dumps({**a['result'],'decision_reason':reason.strip()},ensure_ascii=False),a['id']))
        if not cur.rowcount:raise ValueError('La decisión ya fue registrada')
        conn.execute('UPDATE projects SET migration_decision=? WHERE id=?',('approved' if approved else 'rejected',pid))
    db.project_event(pid,'migration',{'event':'decision','attempt_id':a['id'],'approved':approved})
    # Cached legacy candidate is only disposable workspace. Delivery NEVER trusts it.
    if not approved:shutil.rmtree(workspace.project_candidate(pid),ignore_errors=True)
    workspace.save_artifact(pid,'report.md',report(pid,a['id'])+'\n**Motivo humano:** '+reason.strip()+'\n')
    return migration_runs.get(pid,a['id'])


def export(pid:str,fmt:str='zip',include_docs:bool=False) -> tuple[bytes,str,str]:
    """Approved delivery. include_docs adds every analysis/plan artifact under documentacion/."""
    a=_latest(pid)
    if a.get('decision')!='approved':raise ValueError('Aprueba explícitamente el candidato antes de exportar')
    reviewed=migration_review.reviewed_path(pid,a['id'])
    if not reviewed.exists() or workspace.snapshot_hash(reviewed)!=a['reviewed_sha256']:
        raise ValueError('El candidato aprobado ya no coincide con el artefacto verificado')
    patch=selected_diff(pid,a['id']).encode('utf-8')
    report_txt=report(pid,a['id']).encode('utf-8')
    if fmt=='patch':return patch,'text/plain; charset=utf-8','legacylift-changes.patch'
    if fmt=='report':return report_txt,'text/markdown; charset=utf-8','legacylift-report.md'
    if fmt!='zip':raise ValueError('Formato de exportación inválido')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in reviewed.rglob('*'):
            if f.is_file() and not f.is_symlink():
                z.write(f,'codigo-modernizado/'+f.relative_to(reviewed).as_posix())
        z.writestr('evidencias/changes.patch',patch)
        z.writestr('evidencias/report.md',report_txt)
        z.writestr('evidencias/verify.json',json.dumps(a['verification'],ensure_ascii=False,indent=2))
        z.writestr('evidencias/manifest.json',json.dumps({
            'attempt_id':a['id'],'source_snapshot_id':a['snapshot_id'],'blueprint_id':a['blueprint_id'],
            'wave_id':a['wave_id'],'source_hash':a['source_hash'],'reviewed_hash':a['reviewed_sha256'],
            'classification':a['verification']['classification'],
            'decision':'approved','decision_reason':a['result'].get('decision_reason','')},ensure_ascii=False,indent=2))
        if include_docs:
            for doc in sorted(workspace.artifact_dir(pid).glob('*')):
                if doc.is_file() and not doc.is_symlink() and doc.name in workspace.ARTIFACT_NAMES:
                    z.write(doc,'documentacion/'+doc.name)
    if include_docs:
        return output.getvalue(),'application/zip','legacylift-entrega-completa.zip'
    return output.getvalue(),'application/zip','legacylift-entrega.zip'
