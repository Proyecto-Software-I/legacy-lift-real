from __future__ import annotations

import os
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import ai, auth as security, db, github, ingestion, jobs, pipeline, workspace, planning, migration_packs, baseline, migration_runs, migration_review, migration_verify, migration_delivery, tenancy, reporting
from .settings import cors_origins

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    tenancy.init()
    ingestion.restore_legacy_snapshots()
    yield


app = FastAPI(title='LegacyLift API', version='0.1.0', docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_credentials=False,
                   allow_methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE'], allow_headers=['Authorization', 'Content-Type'])

@app.middleware('http')
async def audit_successful_writes(request: Request, call_next):
    response = await call_next(request)
    if request.method in ('POST','PUT','PATCH','DELETE') and 200 <= response.status_code < 300:
        actor=getattr(request.state,'user_id',None)
        route=request.scope.get('route')
        path=getattr(route,'path','')
        if actor and path:
            try:
                tenancy.record_action(actor,request.method,path,
                    pid=request.path_params.get('pid',''),oid=request.path_params.get('oid',''))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('No se pudo registrar auditoría')
    return response


def current_user(request: Request, authorization: str = Header(default='')) -> dict:
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Se requiere una sesión')
    user = security.resolve(authorization[7:].strip())
    request.state.user_id = user['id']
    user['_method'] = request.method
    user['_path'] = request.url.path
    return user



class Login(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1)


class Register(Login):
    invitation_token: str = Field(default="", max_length=128)
    bootstrap_secret: str = ""
    display_name: str = Field(min_length=2, max_length=100)



class NewProject(BaseModel):
    organization_id: str | None = None
    name: str = Field(min_length=3, max_length=80)
    system_name: str = Field(min_length=2, max_length=80)
    provider: str = Field(pattern='^(codex|gemini|claude|demo)$')


def public_job(job: dict) -> dict:
    return {k:v for k,v in job.items() if k not in {'input_json','input','result_json'}}


class GitImport(BaseModel):
    full_name: str = Field(min_length=3,max_length=202)


class UpdateProject(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    system_name: str = Field(min_length=2, max_length=80)


class Decision(BaseModel):
    approved: bool
    reason: str = Field(default='',max_length=2000)

class BlueprintEdit(BaseModel):
    data: dict

class FileReview(BaseModel):
    path: str = Field(min_length=1,max_length=512)
    selected: bool = True
    comment: str = Field(default="",max_length=2000)



@app.get('/api/health')
def health() -> dict:
    return {'ok':True, 'service':'legacylift'}


@app.get('/api/auth/config')
def auth_config() -> dict:
    return {'registration_open': security.user_count() == 0 or os.getenv('LEGACYLIFT_ALLOW_REGISTRATION', 'false').lower() == 'true', 'bootstrap_required':security.user_count()==0}


@app.post('/api/auth/register')
def register(data: Register) -> dict:
    try:
        user = security.register(data.email, data.display_name, data.password, data.bootstrap_secret, data.invitation_token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'user': user, **security.login(data.email, data.password)}


@app.post('/api/auth/login')
def login(data: Login, request: Request) -> dict:
    return security.login(data.email, data.password, request.client.host if request.client else '')


@app.post('/api/auth/logout')
def logout(authorization: str = Header(default=''), user: dict = Depends(current_user)) -> dict:
    security.revoke(authorization[7:].strip())
    return {'ok': True}


@app.get('/api/auth/me')
def me(user: dict = Depends(current_user)) -> dict:
    return {'username': user['display_name'], 'email': user['email'], 'role': user['role'], 'id':user['id']}


@app.get('/api/providers')
def providers(_user: dict = Depends(current_user)) -> list[dict]:
    return ai.provider_status()


@app.get('/api/projects')
def projects(user: dict = Depends(current_user)) -> list[dict]:
    return tenancy.project_list(user['id'])


@app.post('/api/projects')
def create_project(data: NewProject, user: dict = Depends(current_user)) -> dict:
    oid = data.organization_id or tenancy.personal(user['id'])
    return tenancy.create_project(user['id'], oid, data.name, data.system_name, data.provider)


def ensure_project(pid: str, user: dict) -> dict:
    required = 'viewer' if user.get('_method', 'GET') in ('GET', 'HEAD') else 'developer'
    if any(t in user.get('_path','') for t in ('/decision','/consent')) and required != 'viewer':
        required = 'admin'
    return tenancy.project_access(pid, user['id'], required)


@app.patch('/api/projects/{pid}')
def rename_project(pid: str, data: UpdateProject, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid, user)
    db.project_update(pid, name=data.name.strip(), system_name=data.system_name.strip())
    return ensure_project(pid, user)


@app.get('/api/github/status')
def github_status(user: dict = Depends(current_user)) -> dict:
    return {'configured': github.ready(), 'connection':github.connection(user['id'])}


@app.post('/api/github/connect')
def github_connect(user: dict = Depends(current_user)) -> dict:
    return {'url':github.start(user['id'])}


@app.get('/api/github/callback')
def github_callback(code: str = '', state: str = '') -> RedirectResponse:
    return RedirectResponse(github.complete(code,state), status_code=303)


@app.post('/api/github/disconnect')
def github_disconnect(user: dict = Depends(current_user)) -> dict:
    github.disconnect(user['id'])
    return {'ok':True}


@app.get('/api/github/repos')
def github_repos(user: dict = Depends(current_user)) -> list[dict]:
    return github.repo_list(user['id'])


@app.get('/api/projects/{pid}')
def get_project(pid: str, user: dict = Depends(current_user)) -> dict:
    p = ensure_project(pid, user)
    runs = db.runs_get(pid)
    return {**p, 'runs':runs, 'artifacts':workspace.artifacts(pid),
            'stages':list(pipeline.STAGES), 'code_consent':db.consent_is_active(pid,p.get('active_snapshot_id'),p['provider']), 'phase_ready':{stage:pipeline.phase_ready(pid, stage)[0] for stage in pipeline.STAGES}}


@app.get('/api/projects/{pid}/snapshots')
def snapshots(pid: str, user: dict = Depends(current_user)) -> list[dict]:
    ensure_project(pid,user)
    return ingestion.list_snapshots(pid)


@app.post('/api/projects/{pid}/snapshots/{sid}/activate')
def activate_snapshot(pid: str, sid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:
        return ingestion.activate(pid,sid)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@app.post('/api/projects/{pid}/git',status_code=202)
def import_git(pid: str, data: GitImport, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if not github.connection(user['id']):
        raise HTTPException(409,'Conecta GitHub antes de importar')
    try:
        return public_job(jobs.submit(pid,'github',{'user_id':user['id'],'full_name':data.full_name}))
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@app.post('/api/projects/{pid}/upload',status_code=202)
async def upload(pid: str, file: UploadFile = File(...), user: dict = Depends(current_user)) -> dict:
    ensure_project(pid, user)
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, 'Sube un archivo .zip')
    raw = await file.read(workspace.MAX_ZIP_BYTES + 1)
    if len(raw)>workspace.MAX_ZIP_BYTES:
        raise HTTPException(413,'ZIP supera límite configurado')
    # Save the payload for retries; never keep it in the HTTP request or in the event stream.
    upload_dir=workspace.root(pid)/'uploads'
    upload_dir.mkdir(parents=True,exist_ok=True)
    path=upload_dir/(str(uuid.uuid4())+'.zip')
    path.write_bytes(raw)
    path.chmod(0o600)
    try:
        return public_job(jobs.submit(pid,'zip',{'filename':Path(file.filename).name[:150],'path':str(path)}))
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(409,str(exc)) from exc


class AnalysisSelection(BaseModel):
    files: list[str] = Field(max_length=20000)

class CodeConsent(BaseModel):
    approved: bool

class ReviewDecision(BaseModel):
    status: str = Field(pattern='^(pending|confirmed|discarded)$')
    comment: str = Field(default='',max_length=1000)


def active_analysis(pid: str,user: dict) -> tuple[dict,str]:
    project=ensure_project(pid,user)
    sid=project.get('active_snapshot_id')
    if not sid: raise HTTPException(409,'Importa primero una fuente')
    return project,sid


@app.get('/api/projects/{pid}/analysis/files')
def analysis_files(pid: str,user: dict = Depends(current_user)) -> dict:
    from .analysis import context
    project,sid=active_analysis(pid,user)
    rows=context.indexed_files(pid,sid)
    selection=db.selection_get(pid,sid)
    return {'snapshot_id':sid,'files':rows,'selected_files':context.selected_files(pid,sid),
            'explicit_selection':selection is not None}


@app.put('/api/projects/{pid}/analysis/selection')
def select_analysis_files(pid: str,data: AnalysisSelection,user: dict = Depends(current_user)) -> dict:
    from .analysis import context
    project,sid=active_analysis(pid,user)
    if any(r['status']=='running' for r in db.runs_get(pid)) or any(j['status'] in ('running','queued') for j in db.jobs_list(pid)):
        raise HTTPException(409,'No modifiques la selección durante una ejecución')
    valid={x['path'] for x in context.indexed_files(pid,sid) if x.get('selected')}
    values=set(data.files)
    if not values or not values<=valid or len(values)!=len(data.files):
        raise HTTPException(400,'Selecciona archivos de código válidos, sin duplicados')
    if sorted(values) != context.selected_files(pid,sid):
        db.selection_set(pid,sid,sorted(values))
        db.invalidate_stages(pid,('understand','assess','plan','baseline','modernize','verify'))
        for name in ('understand.md','understand.json','assessment.md','assessment.json','blueprint.md','baseline.md','migration.md','changes.patch','verification.md','report.md'):
            (workspace.artifact_dir(pid)/name).unlink(missing_ok=True)
        with db.connect() as c:
            c.execute("DELETE FROM analysis_documents WHERE project_id=? AND snapshot_id=? AND phase IN ('understand','assess')",(pid,sid))
            c.execute("DELETE FROM analysis_reviews WHERE project_id=? AND snapshot_id=? AND phase IN ('understand','assess')",(pid,sid))
        db.project_update(pid,plan_approved=None,migration_decision=None)
    return {'snapshot_id':sid,'selected_files':sorted(values)}


@app.get('/api/projects/{pid}/analysis/consent')
def analysis_consent(pid: str,user: dict = Depends(current_user)) -> dict:
    project,sid=active_analysis(pid,user)
    return {'snapshot_id':sid,'provider':project['provider'],
            'approved':db.consent_is_active(pid,sid,project['provider'])}


@app.post('/api/projects/{pid}/analysis/consent')
def set_analysis_consent(pid: str,data: CodeConsent,user: dict = Depends(current_user)) -> dict:
    project,sid=active_analysis(pid,user)
    if project['provider']=='demo':return {'approved':True,'provider':'demo','snapshot_id':sid}
    db.consent_set(pid,sid,project['provider'],user['id'],data.approved)
    return {'approved':data.approved,'provider':project['provider'],'snapshot_id':sid}


@app.get('/api/projects/{pid}/analysis/reviews')
def list_analysis_reviews(pid: str,phase: str | None = None,user: dict = Depends(current_user)) -> list[dict]:
    project,sid=active_analysis(pid,user)
    if phase not in (None,'discover','understand','assess'):raise HTTPException(400,'Fase inválida')
    # Lazy backfill for snapshots imported before Sprint 2.
    data=db.analysis_get(pid,sid,'discover') or {}
    db.review_seed_discover(pid,sid,data.get('findings',[]))
    return db.review_list(pid,sid,phase)


@app.patch('/api/projects/{pid}/analysis/reviews/{rid}')
def change_review(pid: str,rid: str,data: ReviewDecision,user: dict = Depends(current_user)) -> dict:
    project,sid=active_analysis(pid,user)
    if not db.review_update(pid,sid,rid,user['id'],data.status,data.comment.strip()):
        raise HTTPException(404,'Hallazgo no encontrado para el snapshot activo')
    return {'ok':True,'id':rid,'status':data.status}


@app.get('/api/projects/{pid}/analysis/discover')
def discover_data(pid: str, user: dict = Depends(current_user)) -> dict:
    p = ensure_project(pid,user)
    sid=p.get('active_snapshot_id')
    return {'snapshot_id':sid,'data':db.analysis_get(pid,sid,'discover') if sid else None,
            'progress':db.last_analysis_progress(pid)}


@app.get('/api/projects/{pid}/analysis/{phase}')
def phase_analysis(pid: str,phase: str,user: dict = Depends(current_user)) -> dict:
    project,sid=active_analysis(pid,user)
    if phase not in ('understand','assess'):raise HTTPException(404,'Fase no soportada')
    return {'snapshot_id':sid,'data':db.analysis_get(pid,sid,phase)}


@app.get('/api/projects/{pid}/jobs')
def list_jobs(pid: str, user: dict = Depends(current_user)) -> list[dict]:
    ensure_project(pid,user)
    return [public_job(j) for j in db.jobs_list(pid)]


@app.post('/api/projects/{pid}/jobs/{jid}/retry',status_code=202)
def retry_job(pid: str, jid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:
        return public_job(jobs.retry(pid,jid))
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@app.post('/api/projects/{pid}/jobs/{jid}/cancel')
def cancel_job(pid: str, jid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if not db.job_get(pid,jid):
        raise HTTPException(404,'Trabajo no encontrado')
    if not db.job_cancel_queued(pid,jid):
        raise HTTPException(409,'Solo se puede cancelar un trabajo en cola')
    return {'ok':True}


@app.get('/api/projects/{pid}/events')
async def stream_events(pid: str, after: int = 0, user: dict = Depends(current_user)) -> StreamingResponse:
    ensure_project(pid,user)
    async def iterator():
        last=max(0,after)
        # Secure browser fetch() uses Authorization header; no bearer token in URL.
        yield ': connected\n\n'
        for _ in range(300):
            rows=db.events_after(pid,last)
            for entry in rows:
                last=entry['id']
                yield f"id: {last}\nevent: update\ndata: {json.dumps(entry,ensure_ascii=False)}\n\n"
            if not rows:
                yield ': ping\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(iterator(),media_type='text/event-stream',headers={
      'Cache-Control':'no-cache,no-transform','X-Accel-Buffering':'no'})


@app.post('/api/projects/{pid}/phases/{phase}')
def start_phase(pid: str, phase: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid, user)
    tenancy.check_run_quota(pid,user['id'])
    if any(j['status'] in ('queued', 'running') for j in db.jobs_list(pid)):
        raise HTTPException(409, 'La importación y el escaneo deben terminar antes de iniciar otras fases')
    try:
        if phase == 'modernize':
            return migration_runs.enqueue(pid,user['id'])
        if phase == 'verify':
            res = migration_verify.enqueue(pid)
            if os.getenv('LEGACYLIFT_WORKER_MODE','disabled').lower() == 'docker-local' and os.getenv('LEGACYLIFT_VERIFY_IN_HOST','false').lower() == 'true':
                pipeline.executor.submit(migration_verify.run_one)
            return res
        return pipeline.start(pid, phase)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/projects/{pid}/migration/attempts')
def migration_attempt_list(pid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    return {'attempts':migration_runs.history(pid),'worker':migration_runs.status()}


@app.post('/api/projects/{pid}/migration/attempts/{aid}/retry',status_code=202)
def migration_attempt_retry(pid: str, aid: str,user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:return migration_runs.enqueue(pid,user['id'],parent=aid)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.get('/api/projects/{pid}/migration/attempts/{aid}/review')
def migration_review_summary(pid:str,aid:str,user:dict=Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:return migration_review.summary(pid,aid)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.get('/api/projects/{pid}/migration/attempts/{aid}/diff')
def migration_diff_file(pid:str,aid:str,path:str,user:dict=Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:return {'path':path,'diff':migration_review.diff_file(pid,aid,path)}
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.patch('/api/projects/{pid}/migration/attempts/{aid}/review')
def migration_review_update(pid:str,aid:str,data:FileReview,user:dict=Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:return migration_review.review(pid,aid,user['id'],data.path,data.selected,data.comment)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.get('/api/projects/{pid}/blueprints/current')
def blueprint_current(pid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    return {'blueprint':planning.current(pid)}

@app.get('/api/projects/{pid}/blueprints/history')
def blueprint_history(pid: str, user: dict = Depends(current_user)) -> list[dict]:
    ensure_project(pid,user)
    return planning.history(pid)

@app.patch('/api/projects/{pid}/blueprints/current')
def blueprint_edit(pid: str, body: BlueprintEdit, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if any(j['status'] in ('queued','running') for j in db.jobs_list(pid)) or any(r['status']=='running' for r in db.runs_get(pid)):
        raise HTTPException(409,'Espera a que termine la ejecución antes de editar')
    if not planning.current(pid):raise HTTPException(409,'Ejecuta Plan primero')
    try:
        return planning.save_revision(pid,body.data)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@app.get('/api/projects/{pid}/migration-packs')
def migration_pack_catalog(pid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    blueprint=planning.current(pid)
    matches={w['id']:migration_packs.compatible(pid,w) for w in blueprint['data']['waves']} if blueprint else {}
    return {'packs':migration_packs.catalog(),'compatibility':matches}

@app.post('/api/projects/{pid}/blueprints/current/waves/{wid}/decision')
def blueprint_wave_decision(pid: str,wid: str,body: Decision,user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if any(r['status']=='running' for r in db.runs_get(pid)):
        raise HTTPException(409,'Espera a que termine la ejecución')
    try:return planning.wave_decision(pid,wid,user['id'],body.approved)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@app.post('/api/projects/{pid}/blueprints/current/waves/{wid}/select')
def blueprint_wave_select(pid: str,wid: str,user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if any(r['status']=='running' for r in db.runs_get(pid)):
        raise HTTPException(409,'Espera a que termine la ejecución')
    try:return planning.select_wave(pid,wid)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@app.post('/api/projects/{pid}/plan/decision')
def plan_decision(pid: str, body: Decision, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid, user)
    if any(j['status'] in ('queued', 'running') for j in db.jobs_list(pid)):
        raise HTTPException(409, 'Espera a que termine la ingesta antes de aprobar el plan')
    try:
        pipeline.decide_plan(pid, body.approved, user['id'])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'approved':body.approved}


@app.get('/api/projects/{pid}/baseline/preflight')
def baseline_preflight(pid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:
        project,plan=baseline.scope(pid)
    except ValueError:
        return {'ready':False,'execution_authorized':False,'tests':[], 'proposals':[]}
    return {'ready':True,'execution_authorized':baseline.authorized(pid),
            'tests':baseline.discover_tests(workspace.project_source(pid)),
            'proposals':baseline.proposals(pid),
            'worker_mode':os.getenv('LEGACYLIFT_WORKER_MODE','disabled')}

@app.post('/api/projects/{pid}/baseline/consent')
def baseline_consent(pid: str, body: Decision, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if any(r['status']=='running' for r in db.runs_get(pid)):
        raise HTTPException(409,'No modifiques la autorización durante una ejecución')
    try:return {'execution_authorized':baseline.set_consent(pid,user['id'],body.approved)}
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@app.post('/api/projects/{pid}/baseline/suggestions')
def baseline_suggest(pid: str, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    if any(r['status']=='running' for r in db.runs_get(pid)):
        raise HTTPException(409,'Espera a que termine la ejecución')
    try:return {'proposals':baseline.suggest(pid)}
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@app.post('/api/projects/{pid}/baseline/cases/{cid}/decision')
def baseline_case_decision(pid: str,cid: str,body: Decision,user: dict = Depends(current_user)) -> dict:
    ensure_project(pid,user)
    try:return baseline.review(pid,user['id'],cid,body.approved)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc


@app.post('/api/projects/{pid}/migration/decision')
def migration_decision(pid: str, body: Decision, user: dict = Depends(current_user)) -> dict:
    ensure_project(pid, user)
    if any(j['status'] in ('queued', 'running') for j in db.jobs_list(pid)):
        raise HTTPException(409, 'Espera a que termine la ingesta antes de aprobar la migración')
    try:
        migration_delivery.decide(pid,user['id'],body.approved,body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'approved':body.approved}


@app.get('/api/projects/{pid}/migration/delivery')
def migration_delivery_download(pid: str, format: str = 'zip', user: dict = Depends(current_user)) -> Response:
    ensure_project(pid,user)
    try:payload,media,name=migration_delivery.export(pid,format)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return Response(payload,media_type=media,headers={'Content-Disposition':f'attachment; filename="{name}"'})


@app.get('/api/projects/{pid}/artifacts/{filename}')
def get_artifact(pid: str, filename: str, user: dict = Depends(current_user)) -> Response:
    ensure_project(pid, user)
    if filename not in workspace.ARTIFACT_NAMES:
        raise HTTPException(404, 'Artefacto inexistente')
    path = workspace.artifact_dir(pid) / filename
    if not path.is_file():
        raise HTTPException(404, 'Artefacto no generado')
    return Response(path.read_text(encoding='utf-8'), media_type='text/plain; charset=utf-8')


@app.get('/api/projects/{pid}/export')
def export(pid: str, user: dict = Depends(current_user)) -> Response:
    project = ensure_project(pid, user)
    # Approved migration: full delivery (modernized code + migration evidence + analysis docs).
    if project['migration_decision'] == 'approved' and migration_runs.history(pid):
        payload,_,name = migration_delivery.export(pid,'zip',include_docs=True)
    else:
        payload,name = workspace.export_zip(pid,False),'legacylift-evidencias.zip'
    return Response(payload, media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=3,max_length=80)

class InvitationCreate(BaseModel):
    email: str
    role: str = Field(pattern='^(admin|developer|viewer)$')

class InvitationAccept(BaseModel):
    token: str = Field(min_length=20,max_length=128)

class MemberRoleUpdate(BaseModel):
    role: str = Field(pattern='^(admin|developer|viewer)$')

@app.get('/api/organizations')
def organization_list(user:dict=Depends(current_user)) -> list[dict]:
    return tenancy.list_organizations(user['id'])

@app.post('/api/organizations')
def organization_create(body:OrganizationCreate,user:dict=Depends(current_user)) -> dict:
    return tenancy.create_organization(user['id'],body.name)

@app.get('/api/organizations/plans')
def organization_plans(user:dict=Depends(current_user)) -> list[dict]:
    return list(tenancy.PLANS.values())

@app.get('/api/organizations/{oid}')
def organization_detail(oid:str,user:dict=Depends(current_user)) -> dict:
    return tenancy.organization(oid,user['id'])

@app.get('/api/organizations/{oid}/members')
def organization_members(oid:str,user:dict=Depends(current_user)) -> list[dict]:
    return tenancy.members(oid,user['id'])

@app.post('/api/organizations/{oid}/invitations')
def organization_invite(oid:str,body:InvitationCreate,user:dict=Depends(current_user)) -> dict:
    return tenancy.invite(oid,user['id'],body.email,body.role)

@app.post('/api/organizations/invitations/accept')
def organization_accept_invitation(body:InvitationAccept,user:dict=Depends(current_user)) -> dict:
    return tenancy.accept_invitation(user['id'],body.token)

@app.patch('/api/organizations/{oid}/members/{uid}')
def organization_member_role(oid:str,uid:str,body:MemberRoleUpdate,user:dict=Depends(current_user)) -> dict:
    return tenancy.update_member(oid,user['id'],uid,body.role)

@app.delete('/api/organizations/{oid}/members/{uid}')
def organization_member_remove(oid:str,uid:str,user:dict=Depends(current_user)) -> dict:
    return tenancy.update_member(oid,user['id'],uid,None)

@app.get('/api/organizations/{oid}/reports')
def organization_report(oid:str,user:dict=Depends(current_user)) -> dict:
    return reporting.summary(oid,user['id'])

@app.get('/api/organizations/{oid}/reports.csv')
def organization_report_csv(oid:str,user:dict=Depends(current_user)) -> Response:
    return Response(reporting.export_csv(oid,user['id']),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':'attachment; filename="legacylift-proyectos.csv"'})

@app.get('/api/organizations/{oid}/usage')
def organization_usage(oid:str,user:dict=Depends(current_user)) -> dict:
    return reporting.usage(oid,user['id'])

@app.get('/api/organizations/{oid}/audit')
def organization_audit(oid:str,limit:int=100,user:dict=Depends(current_user)) -> list[dict]:
    return tenancy.audit_log(oid,user['id'],limit)
