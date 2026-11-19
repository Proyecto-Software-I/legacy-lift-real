"""Persistent, scope-bound migration queue. API enqueues; separate worker executes.

Never accept a path, prompt or command from the client. A new attempt always starts
from the approved, active source snapshot and cannot overwrite prior attempts.
"""
from __future__ import annotations
import json
import os
import shutil
import uuid
from pathlib import Path
from . import ai, db, planning, migration_packs, pipeline, workspace


def _limit() -> int:
    try: return max(1,min(10,int(os.getenv('LEGACYLIFT_MAX_MIGRATION_ATTEMPTS','3'))))
    except ValueError: return 3


def _row(row) -> dict:
    result=dict(row)
    result['result']=json.loads(result.pop('result_json') or '{}')
    if result.get('verification_json'):
        result['verification']=json.loads(result.pop('verification_json'))
    else:result.pop('verification_json',None);result['verification']=None
    return result


def history(pid: str) -> list[dict]:
    with db.connect() as conn:
        return [_row(r) for r in conn.execute('SELECT * FROM migration_attempts WHERE project_id=? ORDER BY rowid DESC',(pid,))]


def get(pid: str, aid: str) -> dict | None:
    with db.connect() as conn:
        row=conn.execute('SELECT * FROM migration_attempts WHERE id=? AND project_id=?',(aid,pid)).fetchone()
    return _row(row) if row else None


def latest_completed(pid: str) -> dict | None:
    # Failed / queued newer attempts MUST NOT silently fall back to an older candidate.
    rows=history(pid)
    return rows[0] if rows and rows[0]['status']=='completed' else None


def candidate_path(pid: str, aid: str) -> Path:
    if str(uuid.UUID(aid))!=aid:raise ValueError('Identificador de intento inválido')
    return workspace.root(pid)/'migration-attempts'/aid/'source'


def _scope(pid: str) -> tuple[dict,dict,dict,dict]:
    project=db.project_get(pid)
    plan=planning.current(pid)
    wave=planning.executable_wave(pid)
    if not project or not plan or plan['status']!='approved' or not wave:
        raise ValueError('Primero aprueba un Blueprint y una wave ejecutable')
    pack=migration_packs.selected_pack(pid,wave)
    if not pack or pack['status']!='available':raise ValueError('Migration Pack incompatible')
    if project['provider'] in ai.AI_PROVIDERS and not db.consent_is_active(pid,project['active_snapshot_id'],project['provider']):
        raise ValueError('No se autorizó enviar código al proveedor de IA')
    baseline_run=db.last_run(pid,'baseline')
    if not baseline_run or baseline_run['status']!='completed':raise ValueError('Captura primero el Behavioral Baseline')
    data=baseline_run['data']
    if any(data.get(k)!=v for k,v in (('snapshot_id',project['active_snapshot_id']),('blueprint_id',plan['id']),('wave_id',wave['id']))):
        raise ValueError('Baseline no corresponde al snapshot, Blueprint y wave actual')
    if data.get('source_hash')!=workspace.snapshot_hash(workspace.project_source(pid)):
        raise ValueError('La fuente cambió desde el baseline')
    return project,plan,wave,pack


def enqueue(pid: str, user_id: str, parent: str | None=None) -> dict:
    project,plan,wave,pack=_scope(pid)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active=conn.execute("SELECT id FROM migration_attempts WHERE project_id=? AND status IN ('queued','running')",(pid,)).fetchone()
        if active:raise ValueError('Ya hay una migración pendiente o en ejecución')
        # Never auto-retry. Each attempt requires a fresh explicit action.
        prior=conn.execute('SELECT id FROM migration_attempts WHERE project_id=? AND snapshot_id=? AND blueprint_id=? AND wave_id=? ORDER BY rowid',
                           (pid,project['active_snapshot_id'],plan['id'],wave['id'])).fetchall()
        if len(prior)>=_limit():raise ValueError('Límite de intentos alcanzado para esta wave')
        if parent and not any(r['id']==parent for r in prior):raise ValueError('Intento anterior no corresponde a esta wave')
        # Do not overwrite an accepted result; a future revision/wave must be authorized.
        if conn.execute("SELECT 1 FROM migration_attempts WHERE project_id=? AND snapshot_id=? AND blueprint_id=? AND wave_id=? AND decision='approved'",(pid,project['active_snapshot_id'],plan['id'],wave['id'])).fetchone():
            raise ValueError('Esta wave ya tiene un candidato aprobado')
        aid=str(uuid.uuid4());rid=str(uuid.uuid4())
        conn.execute("INSERT INTO phase_runs(id,project_id,phase,status,started_at,provider,prompt_hash,snapshot_id,model) VALUES(?,?,?,?,?,?,?,?,?)",
                     (rid,pid,'modernize','running',db.now(),project['provider'],pipeline.prompt_for('modernize',pid)[1],
                      project['active_snapshot_id'],pipeline.model_for_phase('modernize',project['provider'])))
        conn.execute("""INSERT INTO migration_attempts(id,project_id,phase_run_id,snapshot_id,source_hash,blueprint_id,blueprint_revision,wave_id,pack_id,provider,parent_attempt_id,status,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(aid,pid,rid,project['active_snapshot_id'],project['snapshot_hash'],plan['id'],plan['revision'],wave['id'],pack['id'],project['provider'],parent,'queued',db.now()))
    db.invalidate_stages(pid,('verify',))
    db.project_update(pid,migration_decision=None)
    for filename in ('verification.md','report.md','changes.patch','migration.md'):
        (workspace.artifact_dir(pid)/filename).unlink(missing_ok=True)
    db.project_event(pid,'migration',{'event':'queued','attempt_id':aid,'wave_id':wave['id']})
    return get(pid,aid)


def claim_one() -> dict | None:
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM migration_attempts WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if not row:return None
        updated=conn.execute("UPDATE migration_attempts SET status='running',started_at=? WHERE id=? AND status='queued'",(db.now(),row['id']))
        if not updated.rowcount:return None
        return _row(conn.execute('SELECT * FROM migration_attempts WHERE id=?',(row['id'],)).fetchone())


def _freeze(folder: Path) -> None:
    for item in folder.rglob('*'):
        if item.is_symlink():raise ValueError('No se permiten enlaces simbólicos en el candidato')
        if item.is_file():item.chmod(0o444)
        if item.is_dir():item.chmod(0o555)


def _complete(a: dict, summary: str, result: dict) -> dict:
    pid=a['project_id'];aid=a['id']
    folder=candidate_path(pid,aid)
    folder.parent.mkdir(parents=True,exist_ok=False)
    try:
        shutil.copytree(workspace.project_candidate(pid),folder)
        _freeze(folder)
        digest=workspace.snapshot_hash(folder)
        with db.connect() as conn:
            conn.execute("UPDATE migration_attempts SET status='completed',finished_at=?,result_json=?,candidate_sha256=? WHERE id=? AND status='running'",
                         (db.now(),json.dumps(result,ensure_ascii=False),digest,aid))
        db.run_complete(a['phase_run_id'],summary,{**result,'attempt_id':aid,'candidate_sha256':digest})
    except Exception:
        shutil.rmtree(folder.parent,ignore_errors=True)
        raise
    db.project_event(pid,'migration',{'event':'completed','attempt_id':aid,'modified_files':result.get('modified_files',[])})
    return get(pid,aid)


def run_one() -> dict | None:
    a=claim_one()
    if not a:return None
    pid=a['project_id'];aid=a['id']
    try:
        project,plan,wave,pack=_scope(pid)
        if any((a['snapshot_id']!=project['active_snapshot_id'],a['source_hash']!=project['snapshot_hash'],
                a['blueprint_id']!=plan['id'],a['blueprint_revision']!=plan['revision'],
                a['wave_id']!=wave['id'],a['pack_id']!=pack['id'],a['provider']!=project['provider'])):
            raise ValueError('Cambió el alcance aprobado desde que se encoló la ejecución')
        summary,result=pipeline.perform(pid,'modernize')
        return _complete(a,summary,result)
    except Exception as exc:
        with db.connect() as conn:
            conn.execute('UPDATE migration_attempts SET status=?,finished_at=?,error=? WHERE id=?',('failed',db.now(),str(exc)[:500],aid))
        db.run_fail(a['phase_run_id'],str(exc)[:500]);db.project_event(pid,'migration',{'event':'failed','attempt_id':aid,'error':str(exc)[:250]})
        return get(pid,aid)


def status() -> dict:
    # No invented liveness signal. A worker is alive only if it refreshes the heartbeat.
    with db.connect() as conn:
        try: row=conn.execute("SELECT last_seen FROM migration_worker_heartbeat WHERE id=1").fetchone()
        except Exception:return {'configured':False,'last_seen':None}
    return {'configured':True,'last_seen':row['last_seen'] if row else None}


def heartbeat() -> None:
    with db.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS migration_worker_heartbeat(id INTEGER PRIMARY KEY,last_seen TEXT NOT NULL)')
        conn.execute('INSERT INTO migration_worker_heartbeat(id,last_seen) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen',(db.now(),))


def recover_interrupted() -> None:
    """Called only by a worker holding the exclusive worker lock."""
    verify_in_host = os.getenv('LEGACYLIFT_VERIFY_IN_HOST', 'false').lower() == 'true'
    with db.connect() as conn:
        aborted=conn.execute("SELECT id,project_id,phase_run_id FROM migration_attempts WHERE status='running'").fetchall()
        verifications=[] if verify_in_host else conn.execute("SELECT id,project_id,verify_run_id FROM migration_attempts WHERE verify_status='running'").fetchall()
        conn.execute("UPDATE migration_attempts SET status='failed',finished_at=?,error='Worker anterior interrumpido; reintento manual necesario' WHERE status='running'",(db.now(),))
        if not verify_in_host:
            conn.execute("UPDATE migration_attempts SET verify_status='failed',verification_json=NULL WHERE verify_status='running'")
    for row in aborted:
        db.run_fail(row['phase_run_id'],'Worker anterior interrumpido; reintento manual necesario')
    for row in verifications:
        if row['verify_run_id']:
            db.run_fail(row['verify_run_id'],'Worker anterior interrumpido; vuelve a ejecutar Verify')
