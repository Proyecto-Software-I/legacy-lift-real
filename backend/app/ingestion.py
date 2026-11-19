"""Immutable source snapshots and automated discovery for ZIP and authenticated Git sources."""
from __future__ import annotations
import json
import shutil
import uuid
from pathlib import Path
from . import db, github, inspection, pipeline, workspace


def _current_runs(pid: str) -> None:
    if any(r['status']=='running' for r in db.runs_get(pid)):
        raise ValueError('Hay una fase ejecutándose; espera antes de cambiar el snapshot activo')


def _activate(pid: str, sid: str, name: str, digest: str) -> None:
    workspace.activate_archive(pid,sid)
    db.snapshot_set_active(pid,sid,name,digest)


def import_staged(pid: str, staged: Path, origin_type: str, reference: str, commit: str | None=None, progress=None) -> dict:
    _current_runs(pid)
    sid=str(uuid.uuid4())
    manifest=workspace.create_manifest(staged)
    if not manifest:
        raise ValueError('La fuente está vacía')
    digest=workspace.snapshot_hash(staged)
    try:
        workspace.create_archive(pid,sid,staged,manifest)
        db.snapshot_create(pid,sid,origin_type,reference,commit,digest,manifest)
        _activate(pid,sid,reference,digest)
        # Automatic scanning; no separate manual Discover click required.
        rid=db.run_create(pid,'discover','scanner','',pipeline.model_for_phase('discover','scanner'))
        try:
            summary,info=pipeline.perform(pid,'discover',progress=progress)
            prov=pipeline.provenance(pid,'discover','scanner','',info)
            pipeline.stamp_artifact(pid,'discover',prov)
            db.run_complete(rid,summary,{**info,'provenance':prov})
        except Exception as exc:
            db.run_fail(rid,str(exc))
            raise
        return {'snapshot_id':sid,'files':len(manifest),'snapshot_hash':digest,'commit_sha':commit,'inventory':info}
    except Exception:
        # Preserve an already persisted snapshot for diagnosis; cleanup only staging.
        shutil.rmtree(staged,ignore_errors=True)
        raise


def ingest_zip(pid: str, payload: bytes, filename: str, progress=None) -> dict:
    staged=workspace.stage_zip(pid,payload)
    return import_staged(pid,staged,'zip',filename,progress=progress)


def ingest_git(pid: str, uid: str, full_name: str, progress=None) -> dict:
    checkout=workspace.root(pid)/('git-download-'+uuid.uuid4().hex)
    try:
        sha=github.clone(uid,full_name,checkout)
        staged=workspace.stage_git_folder(pid,checkout)
        return import_staged(pid,staged,'github',full_name,sha,progress=progress)
    finally:
        shutil.rmtree(checkout,ignore_errors=True)


def list_snapshots(pid: str) -> list[dict]:
    return db.snapshot_list(pid)


def activate(pid: str, sid: str) -> dict:
    _current_runs(pid)
    if any(j['status'] in ('queued','running') for j in db.jobs_list(pid)):
        raise ValueError('Hay una ingesta en curso; espera antes de activar otro snapshot')
    snapshot=db.snapshot_get(pid,sid)
    if not snapshot:
        raise ValueError('Snapshot no encontrado')
    _activate(pid,sid,snapshot['origin_reference'],snapshot['source_hash'])
    return snapshot


def restore_legacy_snapshots() -> None:
    """One-time migration for databases created by the original academic MVP."""
    for p in _all_projects():
        pid=p['id']
        if not p.get('snapshot_hash') or p.get('active_snapshot_id') or not workspace.project_source(pid).is_dir():
            continue
        sid=str(uuid.uuid4())
        source=workspace.project_source(pid)
        manifest=workspace.create_manifest(source)
        archive=workspace.snapshot_archive(pid,sid)
        archive.parent.mkdir(parents=True,exist_ok=False)
        shutil.copytree(source,archive)
        (archive.parent/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        for f in archive.rglob('*'):
            if f.is_file(): f.chmod(0o444)
        db.snapshot_create(pid,sid,'zip',p.get('source_filename') or 'importación previa',None,
                           p['snapshot_hash'],manifest)
        db.project_update(pid,active_snapshot_id=sid)


def _all_projects() -> list[dict]:
    with db.connect() as c:
        return [dict(row) for row in c.execute('SELECT * FROM projects')]
