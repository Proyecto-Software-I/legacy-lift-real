from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .settings import data_dir


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / 'legacylift.sqlite3', timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            system_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'demo',
            created_at TEXT NOT NULL,
            source_filename TEXT,
            snapshot_hash TEXT,
            plan_approved INTEGER,
            migration_decision TEXT
        );
        CREATE TABLE IF NOT EXISTS phase_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            error TEXT,
            provider TEXT,
            prompt_hash TEXT,
            summary TEXT,
            data_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS phase_runs_project ON phase_runs(project_id,started_at);
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
          created_at TEXT NOT NULL, disabled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id, expires_at);
        CREATE TABLE IF NOT EXISTS auth_attempts (
          identity TEXT PRIMARY KEY, attempts INTEGER NOT NULL, blocked_until TEXT
        );
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(projects)')}
        if 'owner_id' not in columns:
            db.execute('ALTER TABLE projects ADD COLUMN owner_id TEXT REFERENCES users(id)')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS github_oauth_states (
          state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          code_verifier TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS github_connections (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          github_user_id TEXT NOT NULL, github_login TEXT NOT NULL,
          encrypted_token TEXT NOT NULL, connected_at TEXT NOT NULL
        );
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS source_snapshots (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          origin_type TEXT NOT NULL, origin_reference TEXT NOT NULL,
          commit_sha TEXT, source_hash TEXT NOT NULL, file_count INTEGER NOT NULL,
          total_bytes INTEGER NOT NULL, manifest_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS source_snapshots_project ON source_snapshots(project_id,created_at);
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(projects)')}
        if 'active_snapshot_id' not in columns:
            db.execute('ALTER TABLE projects ADD COLUMN active_snapshot_id TEXT REFERENCES source_snapshots(id)')
        phase_columns = {row['name'] for row in db.execute('PRAGMA table_info(phase_runs)')}
        if 'snapshot_id' not in phase_columns:
            db.execute('ALTER TABLE phase_runs ADD COLUMN snapshot_id TEXT')
        if 'model' not in phase_columns:
            db.execute('ALTER TABLE phase_runs ADD COLUMN model TEXT')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS ingest_jobs (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, input_json TEXT NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
          error TEXT, result_json TEXT, created_at TEXT NOT NULL,
          started_at TEXT, finished_at TEXT, retry_of TEXT
        );
        CREATE INDEX IF NOT EXISTS ingest_jobs_project ON ingest_jobs(project_id,created_at);
        CREATE TABLE IF NOT EXISTS project_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS project_events_project ON project_events(project_id,id);
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS analysis_documents (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id,snapshot_id,phase)
        );
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS analysis_selection (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
            selected_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id,snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS analysis_consent (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            approved_at TEXT,
            revoked_at TEXT,
            user_id TEXT NOT NULL REFERENCES users(id),
            PRIMARY KEY(project_id,snapshot_id,provider)
        );
        CREATE TABLE IF NOT EXISTS analysis_reviews (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
            phase TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            reviewed_by TEXT REFERENCES users(id),
            reviewed_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS analysis_reviews_project ON analysis_reviews(project_id,snapshot_id,phase);
        ''')
        db.executescript("""
        CREATE TABLE IF NOT EXISTS modernization_blueprints (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
          revision INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('draft','approved','rejected','superseded')),
          data_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          decided_at TEXT,
          decided_by TEXT REFERENCES users(id),
          UNIQUE(project_id, snapshot_id, revision)
        );
        CREATE INDEX IF NOT EXISTS modernization_blueprints_project
          ON modernization_blueprints(project_id,snapshot_id,revision);
        CREATE TABLE IF NOT EXISTS blueprint_wave_approvals (
          blueprint_id TEXT NOT NULL REFERENCES modernization_blueprints(id) ON DELETE CASCADE,
          wave_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('approved','rejected')),
          user_id TEXT NOT NULL REFERENCES users(id),
          decided_at TEXT NOT NULL,
          PRIMARY KEY(blueprint_id,wave_id)
        );
        """)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS baseline_execution_consents (
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
          blueprint_id TEXT NOT NULL REFERENCES modernization_blueprints(id) ON DELETE CASCADE,
          wave_id TEXT NOT NULL,
          user_id TEXT NOT NULL REFERENCES users(id),
          approved_at TEXT,
          revoked_at TEXT,
          PRIMARY KEY(project_id,snapshot_id,blueprint_id,wave_id)
        );
        CREATE TABLE IF NOT EXISTS baseline_test_proposals (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE CASCADE,
          blueprint_id TEXT NOT NULL REFERENCES modernization_blueprints(id) ON DELETE CASCADE,
          wave_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
          data_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          reviewed_at TEXT,
          reviewed_by TEXT REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS baseline_test_proposals_scope
          ON baseline_test_proposals(project_id,snapshot_id,blueprint_id,wave_id);
        """)
        # S4: immutable migration attempts, managed by a separately started worker.
        db.executescript("""
        CREATE TABLE IF NOT EXISTS migration_attempts (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          phase_run_id TEXT NOT NULL REFERENCES phase_runs(id) ON DELETE CASCADE,
          snapshot_id TEXT NOT NULL, source_hash TEXT NOT NULL,
          blueprint_id TEXT NOT NULL, blueprint_revision INTEGER NOT NULL,
          wave_id TEXT NOT NULL, pack_id TEXT NOT NULL, provider TEXT NOT NULL,
          parent_attempt_id TEXT, status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','superseded')),
          created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
          error TEXT, result_json TEXT NOT NULL DEFAULT '{}',
          candidate_sha256 TEXT, reviewed_sha256 TEXT, verification_json TEXT,
          decision TEXT, decided_at TEXT, decided_by TEXT
        );
        CREATE INDEX IF NOT EXISTS migration_attempts_project ON migration_attempts(project_id,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS migration_one_active ON migration_attempts(project_id)
          WHERE status IN ('queued','running');
        """)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS migration_file_reviews (
          attempt_id TEXT NOT NULL REFERENCES migration_attempts(id) ON DELETE CASCADE,
          path TEXT NOT NULL, selected INTEGER NOT NULL DEFAULT 1,
          comment TEXT NOT NULL DEFAULT '', reviewed_by TEXT, reviewed_at TEXT,
          PRIMARY KEY(attempt_id,path)
        );
        """)
        migration_columns={r['name'] for r in db.execute('PRAGMA table_info(migration_attempts)')}
        if 'verify_status' not in migration_columns:
            db.execute('ALTER TABLE migration_attempts ADD COLUMN verify_status TEXT')
        if 'verify_run_id' not in migration_columns:
            db.execute('ALTER TABLE migration_attempts ADD COLUMN verify_run_id TEXT')
        # Migration attempts belong to the separate worker; an API restart must NOT
        # interrupt its running jobs. Orphan recovery happens under the worker lock.
        columns={row['name'] for row in db.execute('PRAGMA table_info(projects)')}
        if 'active_wave_id' not in columns:
            db.execute('ALTER TABLE projects ADD COLUMN active_wave_id TEXT')
        if 'active_pack_id' not in columns:
            db.execute('ALTER TABLE projects ADD COLUMN active_pack_id TEXT')
        db.execute("UPDATE ingest_jobs SET status='failed' ,stage='interrupted',finished_at=?,error='Interrumpido por reinicio; puedes reintentar' WHERE status IN ('queued','running')", (now(),))
        db.execute("""UPDATE phase_runs SET status='failed', finished_at=?, error='Interrumpido por reinicio' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM migration_attempts WHERE phase_run_id=phase_runs.id AND status IN ('queued','running')) AND NOT EXISTS (SELECT 1 FROM migration_attempts WHERE verify_run_id=phase_runs.id AND verify_status IN ('queued','running'))""", (now(),))


def project_list(owner_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM projects WHERE owner_id=? ORDER BY created_at DESC', (owner_id,))]


def project_get(pid: str, owner_id: str | None = None) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute('SELECT * FROM projects WHERE id=? AND (? IS NULL OR owner_id=?)', (pid, owner_id, owner_id)).fetchone()
        return dict(row) if row else None


def project_create(name: str, system_name: str, provider: str, owner_id: str) -> dict[str, Any]:
    pid = str(uuid.uuid4())
    with connect() as db:
        db.execute('INSERT INTO projects(id,name,system_name,provider,created_at,owner_id) VALUES(?,?,?,?,?,?)',
                   (pid, name, system_name, provider, now(), owner_id))
    return project_get(pid, owner_id)  # type: ignore[return-value]


def project_update(pid: str, **values: Any) -> None:
    allowed = {'name', 'system_name', 'source_filename', 'snapshot_hash', 'active_snapshot_id', 'plan_approved', 'migration_decision', 'active_wave_id', 'active_pack_id'}
    if not values or any(key not in allowed for key in values):
        raise ValueError('Campos no autorizados')
    keys = list(values)
    sql = 'UPDATE projects SET ' + ','.join(k+'=?' for k in keys) + ' WHERE id=?'
    with connect() as db:
        db.execute(sql, (*[values[k] for k in keys], pid))


def run_create(pid: str, phase: str, provider: str, prompt_hash: str = '', model: str = '') -> str:
    rid = str(uuid.uuid4())
    with connect() as db:
        active=db.execute('SELECT active_snapshot_id FROM projects WHERE id=?',(pid,)).fetchone()
        sid=active['active_snapshot_id'] if active else None
        db.execute('INSERT INTO phase_runs(id,project_id,phase,status,started_at,provider,prompt_hash,snapshot_id,model) VALUES(?,?,?,?,?,?,?,?,?)',
                   (rid, pid, phase, 'running', now(), provider, prompt_hash,sid,model))
    project_event(pid, 'phase', {'phase':phase,'status':'running','id':rid})
    return rid


def run_complete(rid: str, summary: str, data: dict[str, Any] | None = None) -> None:
    pid = run_project_id(rid)
    with connect() as db:
        db.execute('UPDATE phase_runs SET status=?,finished_at=?,summary=?,data_json=? WHERE id=?',
                   ('completed', now(), summary[:1200], json.dumps(data or {}, ensure_ascii=False), rid))
    if pid: project_event(pid,'phase',{'id':rid,'status':'completed'})


def run_fail(rid: str, error: str) -> None:
    pid=run_project_id(rid)
    with connect() as db:
        db.execute('UPDATE phase_runs SET status=?,finished_at=?,error=? WHERE id=?',
                   ('failed', now(), error[:1600], rid))
    if pid: project_event(pid,'phase',{'id':rid,'status':'failed'})


def run_project_id(rid: str) -> str | None:
    with connect() as c:
        row=c.execute('SELECT project_id FROM phase_runs WHERE id=?',(rid,)).fetchone()
        return row['project_id'] if row else None


def runs_get(pid: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute('SELECT * FROM phase_runs WHERE project_id=? ORDER BY started_at ASC,rowid ASC', (pid,))
        return [{**dict(r), 'data': json.loads(r['data_json'])} for r in rows]


def last_run(pid: str, phase: str) -> dict[str, Any] | None:
    for r in reversed(runs_get(pid)):
        if r['phase'] == phase:
            return r
    return None


def invalidate_stages(pid: str, stages: tuple[str, ...]) -> None:
    if not stages:
        return
    with connect() as db:
        db.executemany("UPDATE phase_runs SET status='superseded' WHERE project_id=? AND phase=? AND status IN ('completed','failed')",
                       [(pid, phase) for phase in stages])


def snapshot_list(pid: str) -> list[dict[str, Any]]:
    with connect() as c:
        rows=c.execute('SELECT id,project_id,origin_type,origin_reference,commit_sha,source_hash,file_count,total_bytes,created_at FROM source_snapshots WHERE project_id=? ORDER BY created_at DESC,rowid DESC', (pid,))
        return [dict(r) for r in rows]


def snapshot_get(pid: str, sid: str) -> dict | None:
    with connect() as c:
        row=c.execute('SELECT * FROM source_snapshots WHERE project_id=? AND id=?', (pid,sid)).fetchone()
        return {**dict(row),'manifest':json.loads(row['manifest_json'])} if row else None


def snapshot_create(pid: str, sid: str, origin_type: str, reference: str, commit_sha: str | None, digest: str, manifest: list[dict]) -> None:
    with connect() as c:
        c.execute("""INSERT INTO source_snapshots(id,project_id,origin_type,origin_reference,commit_sha,source_hash,file_count,total_bytes,manifest_json,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (sid,pid,origin_type,reference,commit_sha,digest,len(manifest),sum(x['size'] for x in manifest),json.dumps(manifest,ensure_ascii=False),now()))


def snapshot_set_active(pid: str, sid: str, filename: str, digest: str) -> None:
    with connect() as c:
        c.execute("""UPDATE projects SET active_snapshot_id=?,source_filename=?,snapshot_hash=?,plan_approved=NULL,migration_decision=NULL
                     WHERE id=? AND EXISTS (SELECT 1 FROM source_snapshots WHERE id=? AND project_id=?)""",
                  (sid,filename,digest,pid,sid,pid))
        c.execute("UPDATE phase_runs SET status='superseded' WHERE project_id=? AND status IN ('completed','failed')",(pid,))


def project_event(pid: str, kind: str, payload: dict) -> None:
    with connect() as c:
        c.execute('INSERT INTO project_events(project_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                  (pid,kind,json.dumps(payload,ensure_ascii=False),now()))


def events_after(pid: str, event_id: int) -> list[dict]:
    with connect() as c:
        rows=c.execute('SELECT * FROM project_events WHERE project_id=? AND id>? ORDER BY id ASC LIMIT 50',(pid,event_id))
        return [{'id':r['id'],'kind':r['kind'],'payload':json.loads(r['payload_json']),'created_at':r['created_at']} for r in rows]


def job_create(pid: str, kind: str, params: dict, retry_of: str | None = None) -> dict:
    jid=str(uuid.uuid4())
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT count(*) AS n FROM ingest_jobs WHERE project_id=? AND status IN ('queued','running')",(pid,)).fetchone()['n']:
            raise ValueError('Ya hay una ingesta activa en este proyecto')
        if c.execute("SELECT count(*) AS n FROM phase_runs WHERE project_id=? AND status='running'",(pid,)).fetchone()['n']:
            raise ValueError('Termina primero la fase en ejecución')
        c.execute('INSERT INTO ingest_jobs(id,project_id,kind,input_json,status,stage,progress,created_at,retry_of) VALUES(?,?,?,?,?,?,?,?,?)',
                  (jid,pid,kind,json.dumps(params),'queued','queued',0,now(),retry_of))
    project_event(pid,'job',{'id':jid,'status':'queued','stage':'queued','progress':0})
    return job_get(pid,jid)


def job_get(pid: str, jid: str) -> dict | None:
    with connect() as c:
        row=c.execute('SELECT * FROM ingest_jobs WHERE project_id=? AND id=?',(pid,jid)).fetchone()
        return {**dict(row),'input':json.loads(row['input_json']), 'result':json.loads(row['result_json']) if row['result_json'] else None} if row else None


def jobs_list(pid: str) -> list[dict]:
    with connect() as c:
        rows=c.execute('SELECT * FROM ingest_jobs WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50',(pid,))
        return [{**dict(r),'input':json.loads(r['input_json']),'result':json.loads(r['result_json']) if r['result_json'] else None} for r in rows]


def job_update(pid: str,jid: str,status: str,stage: str,progress: int,error: str | None=None,result: dict | None=None) -> None:
    progress=max(0,min(100,progress))
    with connect() as c:
        c.execute("""UPDATE ingest_jobs SET status=?,stage=?,progress=?,error=?,result_json=?,
                    started_at=COALESCE(started_at,CASE WHEN ?='running' THEN ? ELSE NULL END),
                    finished_at=CASE WHEN ? IN ('completed','failed','canceled') THEN ? ELSE finished_at END
                    WHERE id=? AND project_id=?""",
                  (status,stage,progress,error[:1600] if error else None,
                   json.dumps(result,ensure_ascii=False) if result else None,status,now(),status,now(),jid,pid))
    project_event(pid,'job',{'id':jid,'status':status,'stage':stage,'progress':progress,'error':error[:400] if error else None})


def job_claim(pid: str,jid: str) -> bool:
    with connect() as c:
        cursor=c.execute("UPDATE ingest_jobs SET status='running',stage='acquiring',progress=10,started_at=? WHERE project_id=? AND id=? AND status='queued'",(now(),pid,jid))
        claimed=cursor.rowcount>0
    if claimed:
        project_event(pid,'job',{'id':jid,'status':'running','stage':'acquiring','progress':10})
    return claimed


def job_cancel_queued(pid: str,jid: str) -> bool:
    with connect() as c:
        cursor=c.execute("UPDATE ingest_jobs SET status='canceled',stage='canceled',finished_at=? WHERE project_id=? AND id=? AND status='queued'",(now(),pid,jid))
        updated=cursor.rowcount>0
    if updated:
        project_event(pid,'job',{'id':jid,'status':'canceled','stage':'canceled','progress':0})
    return updated


def analysis_put(pid: str, sid: str, phase: str, data: dict, status: str = 'completed') -> None:
    with connect() as c:
        c.execute("""INSERT INTO analysis_documents(project_id,snapshot_id,phase,data_json,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(project_id,snapshot_id,phase)
                   DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at""",
                  (pid,sid,phase,json.dumps(data,ensure_ascii=False),now()))
    project_event(pid,'analysis',{'phase':phase,'status':status,'snapshot_id':sid})


def analysis_get(pid: str, sid: str, phase: str) -> dict | None:
    with connect() as c:
        row=c.execute('SELECT data_json FROM analysis_documents WHERE project_id=? AND snapshot_id=? AND phase=?',(pid,sid,phase)).fetchone()
        return json.loads(row['data_json']) if row else None


def last_analysis_progress(pid: str) -> dict | None:
    with connect() as c:
        row=c.execute("SELECT payload_json FROM project_events WHERE project_id=? AND kind='analysis-progress' ORDER BY id DESC LIMIT 1",(pid,)).fetchone()
        return json.loads(row['payload_json']) if row else None


def selection_get(pid: str,sid: str) -> list[str] | None:
    with connect() as c:
        row=c.execute('SELECT selected_json FROM analysis_selection WHERE project_id=? AND snapshot_id=?',(pid,sid)).fetchone()
        return json.loads(row['selected_json']) if row else None


def selection_set(pid: str,sid: str,files: list[str]) -> None:
    with connect() as c:
        c.execute("""INSERT INTO analysis_selection(project_id,snapshot_id,selected_json,updated_at) VALUES(?,?,?,?)
          ON CONFLICT(project_id,snapshot_id) DO UPDATE SET selected_json=excluded.selected_json,updated_at=excluded.updated_at""",
          (pid,sid,json.dumps(files,ensure_ascii=False),now()))
    project_event(pid,'analysis',{'phase':'selection','status':'updated','snapshot_id':sid})


def consent_is_active(pid: str, sid: str | None,provider: str) -> bool:
    if provider == 'demo': return True
    if not sid: return False
    with connect() as c:
        row=c.execute('SELECT approved_at,revoked_at FROM analysis_consent WHERE project_id=? AND snapshot_id=? AND provider=?',(pid,sid,provider)).fetchone()
        return bool(row and row['approved_at'] and not row['revoked_at'])


def consent_set(pid: str, sid: str,provider: str, uid: str, approved: bool) -> None:
    with connect() as c:
        c.execute("""INSERT INTO analysis_consent(project_id,snapshot_id,provider,approved_at,revoked_at,user_id)
          VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,snapshot_id,provider) DO UPDATE SET
          approved_at=excluded.approved_at,revoked_at=excluded.revoked_at,user_id=excluded.user_id""",
          (pid,sid,provider,now() if approved else None,None if approved else now(),uid))
    project_event(pid,'consent',{'provider':provider,'approved':approved,'snapshot_id':sid})


def review_insert(pid: str,sid: str,phase: str,kind: str,rid: str,title: str,evidence: dict) -> None:
    # Same static finding in two snapshots must remain independently reviewable.
    import hashlib
    rid=hashlib.sha256(f'{sid}:{rid}'.encode()).hexdigest()[:24]
    with connect() as c:
        c.execute("""INSERT OR IGNORE INTO analysis_reviews(id,project_id,snapshot_id,phase,kind,status,title,evidence_json,created_at)
          VALUES(?,?,?,?,?,'pending',?,?,?)""",
          (rid,pid,sid,phase,kind,title[:400],json.dumps(evidence,ensure_ascii=False),now()))


def review_list(pid: str,sid: str,phase: str | None = None) -> list[dict]:
    with connect() as c:
        rows=c.execute("""SELECT * FROM analysis_reviews WHERE project_id=? AND snapshot_id=? AND (? IS NULL OR phase=?)
          ORDER BY created_at,id LIMIT 2000""",(pid,sid,phase,phase))
        return [{**dict(row),'evidence':json.loads(row['evidence_json'])} for row in rows]


def review_update(pid: str,sid: str,rid: str,uid: str,status: str,comment: str) -> bool:
    with connect() as c:
        result=c.execute("""UPDATE analysis_reviews SET status=?,comment=?,reviewed_by=?,reviewed_at=?
          WHERE project_id=? AND snapshot_id=? AND id=?""",(status,comment,uid,now(),pid,sid,rid))
        changed=result.rowcount>0
    if changed: project_event(pid,'review',{'id':rid,'status':status,'snapshot_id':sid})
    return changed


def review_seed_discover(pid: str, sid: str, findings: list[dict]) -> None:
    """Idempotent bulk seed; never overwrite decisions made by a person."""
    import hashlib
    with connect() as c:
        c.executemany("""INSERT OR IGNORE INTO analysis_reviews(
          id,project_id,snapshot_id,phase,kind,status,title,evidence_json,created_at)
          VALUES(?,?,?,'discover','static_finding','pending',?,?,?)""",[
          (hashlib.sha256(f"{sid}:{x['id']}".encode()).hexdigest()[:24],pid,sid,
           x['reason'][:400],json.dumps(x.get('evidence',{}),ensure_ascii=False),now())
          for x in findings[:2000]
        ])
