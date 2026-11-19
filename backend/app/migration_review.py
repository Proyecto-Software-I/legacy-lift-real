"""Human-readable diff summary first; full text only on explicit request.

A review selects complete changed files. We do not pretend to support hunks yet.
Verify constructs exactly the selected candidate and records its digest.
"""
from __future__ import annotations
import difflib
import shutil
import uuid
from pathlib import Path
from . import db, migration_runs, workspace


def _attempt(pid: str, aid: str) -> dict:
    item=migration_runs.get(pid,aid)
    if not item or item['status']!='completed':raise ValueError('Intento completo no encontrado')
    path=migration_runs.candidate_path(pid,aid)
    if not path.is_dir() or workspace.snapshot_hash(path)!=item['candidate_sha256']:
        raise ValueError('El candidato inmutable fue modificado o no existe')
    project=db.project_get(pid)
    if (item['snapshot_id']!=project['active_snapshot_id'] or
        item['source_hash']!=workspace.snapshot_hash(workspace.project_source(pid))):
        raise ValueError('El intento pertenece a un snapshot anterior')
    from . import planning
    plan=planning.current(pid)
    if not plan or plan['id']!=item['blueprint_id'] or plan['revision']!=item['blueprint_revision']:
        raise ValueError('El Blueprint cambió después de este intento')
    return item


def _changed(pid:str,aid:str) -> list[str]:
    original=workspace.project_source(pid)
    candidate=migration_runs.candidate_path(pid,aid)
    # The worker validated full file lists and scope before freezing the archive.
    # Use that recorded list instead of reading all 5000 source files on every UI request.
    item=migration_runs.get(pid,aid)
    names=item['result'].get('modified_files') if item else None
    # Refactor packs touch at most 5 existing files; language migration may add a few more classes.
    if not isinstance(names,list) or not names or len(names)>14:
        raise ValueError('El intento no conserva el listado validado de archivos modificados')
    changed=[]
    for name in sorted(names):
        if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('Ruta de cambio inválida')
        if not (candidate/name).is_file():
            raise ValueError('El archivo candidato desapareció')
        # A file absent from the original snapshot is a NEW file (language migration).
        if not (original/name).exists() or (original/name).read_bytes()!=(candidate/name).read_bytes():changed.append(name)
    return changed


def _record(pid: str, aid: str, name: str) -> dict:
    with db.connect() as conn:
        row=conn.execute('SELECT selected,comment,reviewed_at FROM migration_file_reviews WHERE attempt_id=? AND path=?',(aid,name)).fetchone()
    return {'selected':bool(row['selected']) if row else True,
            'comment':row['comment'] if row else '', 'reviewed_at':row['reviewed_at'] if row else None}


def summary(pid: str, aid: str) -> dict:
    _attempt(pid,aid)
    old=workspace.project_source(pid);new=migration_runs.candidate_path(pid,aid)
    rows=[];added=removed=0
    for name in _changed(pid,aid):
        is_new=not (old/name).exists()
        before=[] if is_new else (old/name).read_text(encoding='utf-8',errors='replace').splitlines()
        after=(new/name).read_text(encoding='utf-8',errors='replace').splitlines()
        ops=difflib.SequenceMatcher(a=before,b=after,autojunk=False).get_opcodes()
        plus=sum(j2-j1 for tag,i1,i2,j1,j2 in ops if tag in ('insert','replace'))
        minus=sum(i2-i1 for tag,i1,i2,j1,j2 in ops if tag in ('delete','replace'))
        added+=plus;removed+=minus
        note=('Archivo nuevo generado por la migración' if is_new else
              'Se agregaron líneas' if plus and not minus else
              'Se eliminaron líneas' if minus and not plus else
              'Se modificó la implementación existente')
        rows.append({'path':name,'added':plus,'removed':minus,'summary':note,
                     'risk':'Revisar contratos y pruebas: el diff no garantiza equivalencia',**_record(pid,aid,name)})
    return {'attempt_id':aid,'files':rows,'total_files':len(rows),'added':added,'removed':removed,
            'selected_count':sum(x['selected'] for x in rows),
            'explanation':'Resumen basado en diferencias reales del código. No implica que un comportamiento esté validado.',
            'selection_level':'file'}


def diff_file(pid: str, aid: str, name: str) -> str:
    _attempt(pid,aid)
    if name not in _changed(pid,aid):raise ValueError('Archivo no incluido en el intento')
    src=workspace.project_source(pid)/name;dst=migration_runs.candidate_path(pid,aid)/name
    before=src.read_text(encoding='utf-8',errors='replace').splitlines(keepends=True) if src.exists() else []
    return ''.join(difflib.unified_diff(before,
                  dst.read_text(encoding='utf-8',errors='replace').splitlines(keepends=True),
                  fromfile='antes/'+name,tofile='después/'+name,n=3))[:250000]


def review(pid: str, aid: str, uid: str, name: str, selected: bool, comment: str) -> dict:
    item=_attempt(pid,aid)
    if item['decision']:raise ValueError('Un candidato decidido no se puede modificar')
    if item.get('verify_status') in ('queued','running'):
        raise ValueError('Espera a que termine Verify antes de cambiar la selección')
    if name not in _changed(pid,aid):raise ValueError('Archivo no forma parte del diff')
    if not isinstance(comment,str) or len(comment)>2000:raise ValueError('Comentario demasiado largo')
    with db.connect() as conn:
        conn.execute("""INSERT INTO migration_file_reviews(attempt_id,path,selected,comment,reviewed_by,reviewed_at)
          VALUES(?,?,?,?,?,?) ON CONFLICT(attempt_id,path) DO UPDATE SET selected=excluded.selected,
          comment=excluded.comment,reviewed_by=excluded.reviewed_by,reviewed_at=excluded.reviewed_at""",
          (aid,name,int(selected),comment,uid,db.now()))
        conn.execute('UPDATE migration_attempts SET reviewed_sha256=NULL,verification_json=NULL WHERE id=?',(aid,))
    db.invalidate_stages(pid,('verify',))
    db.project_update(pid,migration_decision=None)
    db.project_event(pid,'migration-review',{'attempt_id':aid,'path':name,'selected':selected})
    return summary(pid,aid)


def reviewed_path(pid: str, aid: str) -> Path:
    return workspace.root(pid)/'migration-attempts'/aid/'reviewed'


def assemble(pid: str, aid: str) -> tuple[Path,str]:
    item=_attempt(pid,aid)
    details=summary(pid,aid)
    if not details['selected_count']:raise ValueError('Selecciona al menos un archivo antes de Verify')
    result=reviewed_path(pid,aid)
    temp=result.with_name('review-'+uuid.uuid4().hex)
    # Start from original; apply only explicitly selected whole files.
    shutil.copytree(workspace.project_source(pid),temp)
    try:
        source=migration_runs.candidate_path(pid,aid)
        for entry in details['files']:
            if entry['selected']:
                rel=entry['path'];target=temp/rel
                if target.is_symlink() or (source/rel).is_symlink():raise ValueError('Ruta simbólica prohibida')
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source/rel,target)
        digest=workspace.snapshot_hash(temp)
        if result.exists():
            # Only this disposable reviewed derivative is replaced, never the original attempt.
            for directory in sorted((x for x in result.rglob('*') if x.is_dir()),key=lambda x:len(x.parts),reverse=True):
                directory.chmod(0o755)
            result.chmod(0o755)
            shutil.rmtree(result)
        temp.rename(result)
        migration_runs._freeze(result)
        return result,digest
    finally:
        if temp.exists():shutil.rmtree(temp,ignore_errors=True)
