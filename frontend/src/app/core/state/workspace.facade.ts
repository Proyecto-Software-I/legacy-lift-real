import { Injectable, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Subscription, interval } from 'rxjs';
import { marked } from 'marked';
import { ApiService, Project, Provider, Run, Snapshot, IngestJob, DiscoverData, ScanProgress, SourceFile, ReviewItem, AssessmentData, Blueprint, BlueprintData, PackCatalog, MigrationWave, BaselinePreflight, MigrationAttempt, MigrationReviewSummary } from '../api/api.service';
import { StageView } from '../models/pipeline-stage';
import { ThemeService } from '../theme/theme.service';
import { AuthSessionService } from '../auth/auth-session.service';

const STAGES = [
  {id:'discover', index:'01', label:'Discover', description:'Inventario técnico', sprint:'S2', file:'inventory.md'},
  {id:'understand', index:'02', label:'Understand', description:'Arquitectura y documentación', sprint:'S2', file:'understand.md'},
  {id:'assess', index:'03', label:'Assess', description:'Riesgos y recomendaciones', sprint:'S2', file:'assessment.md'},
  {id:'plan', index:'04', label:'Plan', description:'Blueprint y wave', sprint:'S3', file:'blueprint.md'},
  {id:'baseline', index:'05', label:'Baseline', description:'Evidencia antes del cambio', sprint:'S3', file:'baseline.md'},
  {id:'modernize', index:'06', label:'Modernize', description:'Transformación en copia', sprint:'S4', file:'changes.patch'},
  {id:'verify', index:'07', label:'Verify', description:'Validación y decisión', sprint:'S4', file:'verification.md'},
] as const;

@Injectable({providedIn: 'root'})
export class WorkspaceFacade {
  private api = inject(ApiService);
  private readonly session = inject(AuthSessionService);
  private sessionWatcher?: Subscription;
  private activeSessionToken: string | null = null;
  private selectedRequestId = 0;
  /** Angular 22 defaults to OnPush. Every template-facing mutable field is signal-backed,
   * preserving the facade property interface while notifying all interested components.
   * Never mutate arrays, records or snapshot objects in place: replace their references.
   */
  private readonly _sessionCheckFailed = signal<boolean>(false);
  get sessionCheckFailed(): boolean { return this._sessionCheckFailed(); }
  set sessionCheckFailed(value: boolean) { this._sessionCheckFailed.set(value); }
  readonly themeService = inject(ThemeService);
  get theme(): string { return this.themeService.value; }
  toggleTheme(): void { this.themeService.toggle(); }
  private poller?: Subscription;
  readonly steps = STAGES;
  private readonly _authenticated = signal<boolean>(false);
  get authenticated(): boolean { return this._authenticated(); }
  set authenticated(value: boolean) { this._authenticated.set(value); }
  private readonly _invitationToken = signal<string>('');
  get invitationToken():string {return this._invitationToken();}
  set invitationToken(value:string){this._invitationToken.set(value);}
  private readonly _password = signal<string>('');
  get password(): string { return this._password(); }
  set password(value: string) { this._password.set(value); }
  private readonly _passwordVisible = signal<boolean>(false);
  get passwordVisible(): boolean { return this._passwordVisible(); }
  set passwordVisible(value: boolean) { this._passwordVisible.set(value); }
  private readonly _mobileNavOpen = signal<boolean>(false);
  get mobileNavOpen(): boolean { return this._mobileNavOpen(); }
  set mobileNavOpen(value: boolean) { this._mobileNavOpen.set(value); }
  private readonly _projectQuery = signal<string>('');
  get projectQuery(): string { return this._projectQuery(); }
  set projectQuery(value: string) { this._projectQuery.set(value); }
  private readonly _email = signal<string>('');
  get email(): string { return this._email(); }
  set email(value: string) { this._email.set(value); }
  private readonly _displayName = signal<string>('');
  get displayName(): string { return this._displayName(); }
  set displayName(value: string) { this._displayName.set(value); }
  private readonly _registerMode = signal<boolean>(false);
  get registerMode(): boolean { return this._registerMode(); }
  set registerMode(value: boolean) { this._registerMode.set(value); }
  private readonly _registrationOpen = signal<boolean>(false);
  get registrationOpen(): boolean { return this._registrationOpen(); }
  set registrationOpen(value: boolean) { this._registrationOpen.set(value); }
  private readonly _bootstrapRequired = signal<boolean>(false);
  get bootstrapRequired(): boolean { return this._bootstrapRequired(); }
  set bootstrapRequired(value: boolean) { this._bootstrapRequired.set(value); }
  private readonly _bootstrapSecret = signal<string>('');
  get bootstrapSecret(): string { return this._bootstrapSecret(); }
  set bootstrapSecret(value: string) { this._bootstrapSecret.set(value); }
  private readonly _currentUser = signal<string>('Operador');
  get currentUser(): string { return this._currentUser(); }
  set currentUser(value: string) { this._currentUser.set(value); }
  private readonly _busy = signal<boolean>(false);
  get busy(): boolean { return this._busy(); }
  set busy(value: boolean) { this._busy.set(value); }
  private readonly _error = signal<string>('');
  get error(): string { return this._error(); }
  set error(value: string) { this._error.set(value); }
  private readonly _notice = signal<string>('');
  get notice(): string { return this._notice(); }
  set notice(value: string) { this._notice.set(value); }
  private readonly _organizations = signal<import('../api/api.service').Organization[]>([]);
  get organizations() { return this._organizations(); }
  set organizations(value: import('../api/api.service').Organization[]) { this._organizations.set(value); }
  private readonly _createOrganizationId = signal<string>('');
  get createOrganizationId(): string { return this._createOrganizationId(); }
  set createOrganizationId(value: string) { this._createOrganizationId.set(value); }
  private readonly _projects = signal<Project[]>([]);
  get projects(): Project[] { return this._projects(); }
  set projects(value: Project[]) { this._projects.set(value); }
  private readonly _providers = signal<Provider[]>([]);
  get providers(): Provider[] { return this._providers(); }
  set providers(value: Provider[]) { this._providers.set(value); }
  private readonly _selected = signal<Project | undefined>(undefined);
  get selected(): Project | undefined { return this._selected(); }
  set selected(value: Project | undefined) { this._selected.set(value); }
  private readonly _packs = signal<PackCatalog|null>(null);
  get packs():PackCatalog|null{return this._packs();}
  set packs(value:PackCatalog|null){this._packs.set(value);}
  packMatch(wave:MigrationWave):{compatible:boolean;reason:string;name:string} {
    const matches=this.packs?.compatibility?.[wave.id]||[];
    const best=matches.find(m=>m.compatible);
    const pack=this.packs?.packs.find(p=>p.id===best?.id);
    return best?{compatible:true,reason:best.reason,name:pack?.name||best.id}:
      {compatible:false,reason:matches.find(m=>m.reason)?.reason||'Sin pack compatible',name:'No ejecutable'};
  }
  private readonly _baselinePreflight = signal<BaselinePreflight|null>(null);
  get baselinePreflight():BaselinePreflight|null{return this._baselinePreflight();}
  set baselinePreflight(value:BaselinePreflight|null){this._baselinePreflight.set(value);}
  private readonly _baselineBusy = signal<boolean>(false);
  get baselineBusy():boolean {return this._baselineBusy();}
  set baselineBusy(value:boolean){this._baselineBusy.set(value);}
  private readonly _blueprint = signal<Blueprint|null>(null);
  get blueprint():Blueprint|null {return this._blueprint();}
  set blueprint(value:Blueprint|null){this._blueprint.set(value);}
  private readonly _blueprintDraft = signal<BlueprintData|null>(null);
  get blueprintDraft():BlueprintData|null{return this._blueprintDraft();}
  set blueprintDraft(value:BlueprintData|null){this._blueprintDraft.set(value);}
  private readonly _blueprintSaving = signal<boolean>(false);
  get blueprintSaving():boolean{return this._blueprintSaving();}
  set blueprintSaving(value:boolean){this._blueprintSaving.set(value);}
  private readonly _migrationAttempts=signal<MigrationAttempt[]>([]);
  get migrationAttempts():MigrationAttempt[]{return this._migrationAttempts();}
  set migrationAttempts(v:MigrationAttempt[]){this._migrationAttempts.set(v);}
  private readonly _migrationReview=signal<MigrationReviewSummary|null>(null);
  get migrationReview():MigrationReviewSummary|null{return this._migrationReview();}
  set migrationReview(v:MigrationReviewSummary|null){this._migrationReview.set(v);}
  private readonly _migrationDiffFile=signal<string>('');
  get migrationDiffFile():string{return this._migrationDiffFile();}
  set migrationDiffFile(v:string){this._migrationDiffFile.set(v);}
  private readonly _migrationDiffText=signal<string>('');
  get migrationDiffText():string{return this._migrationDiffText();}
  set migrationDiffText(v:string){this._migrationDiffText.set(v);}
  private readonly _migrationRiskReason=signal<string>('');
  get migrationRiskReason():string{return this._migrationRiskReason();}
  set migrationRiskReason(v:string){this._migrationRiskReason.set(v);}
  private readonly _migrationWorkerSeen=signal<string|null>(null);
  get migrationWorkerSeen():string|null{return this._migrationWorkerSeen();}
  set migrationWorkerSeen(v:string|null){this._migrationWorkerSeen.set(v);}
  get latestMigration():MigrationAttempt|undefined{return this.migrationAttempts[0];}
  private readonly _stageId = signal<string>('discover');
  get stageId(): string { return this._stageId(); }
  set stageId(value: string) { this._stageId.set(value); }
  private readonly _artifactName = signal<string>('');
  get artifactName(): string { return this._artifactName(); }
  set artifactName(value: string) { this._artifactName.set(value); }
  private readonly _artifactContent = signal<string>('');
  get artifactContent(): string { return this._artifactContent(); }
  set artifactContent(value: string) { this._artifactContent.set(value); }
  private readonly _rendered = signal<string>('');
  get rendered(): string { return this._rendered(); }
  set rendered(value: string) { this._rendered.set(value); }
  private readonly _showCreate = signal<boolean>(false);
  get showCreate(): boolean { return this._showCreate(); }
  set showCreate(value: boolean) { this._showCreate.set(value); }
  private readonly _showHelp = signal<boolean>(false);
  get showHelp(): boolean { return this._showHelp(); }
  set showHelp(value: boolean) { this._showHelp.set(value); }
  private readonly _createName = signal<string>('');
  get createName(): string { return this._createName(); }
  set createName(value: string) { this._createName.set(value); }
  private readonly _createSystem = signal<string>('');
  get createSystem(): string { return this._createSystem(); }
  set createSystem(value: string) { this._createSystem.set(value); }
  private readonly _createProvider = signal<Provider['id']>('demo');
  get createProvider(): Provider['id'] { return this._createProvider(); }
  set createProvider(value: Provider['id']) { this._createProvider.set(value); }
  private readonly _workspaceSection = signal<string>('source');
  get workspaceSection():string {return this._workspaceSection();}
  set workspaceSection(value:string){this._workspaceSection.set(value);}
  private readonly _currentTab = signal<'overview' | 'artifacts'>('overview');
  get currentTab(): 'overview' | 'artifacts' { return this._currentTab(); }
  set currentTab(value: 'overview' | 'artifacts') { this._currentTab.set(value); }
  private readonly _uploading = signal<boolean>(false);
  get uploading(): boolean { return this._uploading(); }
  set uploading(value: boolean) { this._uploading.set(value); }
  private readonly _githubConfigured = signal<boolean>(false);
  get githubConfigured(): boolean { return this._githubConfigured(); }
  set githubConfigured(value: boolean) { this._githubConfigured.set(value); }
  private readonly _githubLogin = signal<string>('');
  get githubLogin(): string { return this._githubLogin(); }
  set githubLogin(value: string) { this._githubLogin.set(value); }
  private readonly _githubRepos = signal<{full_name:string;private:boolean;default_branch:string}[]>([]);
  get githubRepos(): {full_name:string;private:boolean;default_branch:string}[] { return this._githubRepos(); }
  set githubRepos(value: {full_name:string;private:boolean;default_branch:string}[]) { this._githubRepos.set(value); }
  private readonly _selectedRepo = signal<string>('');
  get selectedRepo(): string { return this._selectedRepo(); }
  set selectedRepo(value: string) { this._selectedRepo.set(value); }
  private readonly _snapshots = signal<Snapshot[]>([]);
  get snapshots(): Snapshot[] { return this._snapshots(); }
  set snapshots(value: Snapshot[]) { this._snapshots.set(value); }
  private readonly _ingestJobs = signal<IngestJob[]>([]);
  get ingestJobs(): IngestJob[] { return this._ingestJobs(); }
  set ingestJobs(value: IngestJob[]) { this._ingestJobs.set(value); }
  private readonly _discoverData = signal<DiscoverData | null>(null);
  get discoverData(): DiscoverData | null { return this._discoverData(); }
  set discoverData(value: DiscoverData | null) { this._discoverData.set(value); }
  private readonly _scanProgress = signal<ScanProgress | null>(null);
  get scanProgress(): ScanProgress | null { return this._scanProgress(); }
  set scanProgress(value: ScanProgress | null) { this._scanProgress.set(value); }
  private readonly _sourceFiles = signal<SourceFile[]>([]);
  get sourceFiles(): SourceFile[] { return this._sourceFiles(); }
  set sourceFiles(value: SourceFile[]) { this._sourceFiles.set(value); }
  private readonly _selectedFilePaths = signal<Set<string>>(new Set<string>());
  get selectedFilePaths(): Set<string> { return this._selectedFilePaths(); }
  set selectedFilePaths(value: Set<string>) { this._selectedFilePaths.set(value); }
  private readonly _fileQuery = signal<string>('');
  get fileQuery(): string { return this._fileQuery(); }
  set fileQuery(value: string) { this._fileQuery.set(value); }
  private readonly _selectionExplicit = signal<boolean>(false);
  get selectionExplicit(): boolean { return this._selectionExplicit(); }
  set selectionExplicit(value: boolean) { this._selectionExplicit.set(value); }
  private readonly _reviews = signal<ReviewItem[]>([]);
  get reviews(): ReviewItem[] { return this._reviews(); }
  set reviews(value: ReviewItem[]) { this._reviews.set(value); }
  private readonly _reviewQuery = signal<string>('');
  get reviewQuery(): string { return this._reviewQuery(); }
  set reviewQuery(value: string) { this._reviewQuery.set(value); }
  private readonly _reviewFilter = signal<'all' | 'pending' | 'confirmed' | 'discarded'>('all');
  get reviewFilter(): 'all' | 'pending' | 'confirmed' | 'discarded' { return this._reviewFilter(); }
  set reviewFilter(value: 'all' | 'pending' | 'confirmed' | 'discarded') { this._reviewFilter.set(value); }
  private readonly _reviewDraft = signal<Record<string,string>>({});
  get reviewDraft(): Record<string,string> { return this._reviewDraft(); }
  set reviewDraft(value: Record<string,string>) { this._reviewDraft.set(value); }
  private readonly _analysisBusy = signal<boolean>(false);
  get analysisBusy(): boolean { return this._analysisBusy(); }
  set analysisBusy(value: boolean) { this._analysisBusy.set(value); }
  private readonly _understandData = signal<Record<string,any> | null>(null);
  get understandData(): Record<string,any> | null { return this._understandData(); }
  set understandData(value: Record<string,any> | null) { this._understandData.set(value); }
  private readonly _assessmentData = signal<AssessmentData | null>(null);
  get assessmentData(): AssessmentData | null { return this._assessmentData(); }
  set assessmentData(value: AssessmentData | null) { this._assessmentData.set(value); }
  private readonly _assessmentProgress = signal<{phase:string;stage:string;progress:number;findings?:number} | null>(null);
  get assessmentProgress(): {phase:string;stage:string;progress:number;findings?:number} | null { return this._assessmentProgress(); }
  set assessmentProgress(value: {phase:string;stage:string;progress:number;findings?:number} | null) { this._assessmentProgress.set(value); }
  private eventsAbort?: AbortController;
  private lastEventId = 0;
  private readonly _editName = signal<string>('');
  get editName(): string { return this._editName(); }
  set editName(value: string) { this._editName.set(value); }
  private readonly _editSystem = signal<string>('');
  get editSystem(): string { return this._editSystem(); }
  set editSystem(value: string) { this._editSystem.set(value); }
  private readonly _showEdit = signal<boolean>(false);
  get showEdit(): boolean { return this._showEdit(); }
  set showEdit(value: boolean) { this._showEdit.set(value); }

  start(): void {
    this.api.authConfig().subscribe({
      next: v => {this.registrationOpen=v.registration_open;this.bootstrapRequired=v.bootstrap_required;},
      error: () => { /* Registration config is noncritical to an existing login. */ }
    });
    this.sessionWatcher = this.session.changes.subscribe(stored => {
      const nextToken = stored?.access_token ?? null;
      const changed = nextToken !== this.activeSessionToken;
      this.activeSessionToken = nextToken;
      if (changed) this.resetWorkspace();
      this.authenticated = !!nextToken;
      this.currentUser = stored?.user?.username || 'Operador';
      if (changed && nextToken) this.bootstrap();
    });
    this.poller = interval(2500).subscribe(() => {
      if (!this.selected || !this.authenticated) return;
      this.refreshSelected(true);
      this.loadJobs(this.selected.id);
    });
  }
  stop(): void {
    this.poller?.unsubscribe(); this.sessionWatcher?.unsubscribe(); this.eventsAbort?.abort();
  }

  private resetWorkspace(): void {
    this.eventsAbort?.abort(); this.eventsAbort=undefined;
    ++this.selectedRequestId; // Invalidate outstanding project loads from the old account.
    this.mobileNavOpen=false;this.projects=[];this.organizations=[];this.createOrganizationId='';this.providers=[];this.selected=undefined;
    this.snapshots=[];this.ingestJobs=[];this.githubConfigured=false;this.githubLogin='';this.githubRepos=[];this.selectedRepo='';
    this.discoverData=null;this.scanProgress=null;this.sourceFiles=[];this.selectedFilePaths=new Set();
    this.reviews=[];this.reviewDraft={};this.understandData=null;this.assessmentData=null;this.assessmentProgress=null;this.blueprint=null;this.blueprintDraft=null;this.packs=null;this.baselinePreflight=null;this.migrationAttempts=[];this.migrationReview=null;this.migrationDiffFile='';this.migrationDiffText='';this.migrationRiskReason='';this.migrationWorkerSeen=null;this.workspaceSection='source';
    this.artifactContent='';this.artifactName='';this.rendered='';this.showCreate=false;this.showEdit=false;this.showHelp=false;
    this.createName='';this.createSystem='';this.projectQuery='';this.stageId='discover';
    this.error='';this.notice='';this.busy=false;this.uploading=false;this.analysisBusy=false;
    this.sessionCheckFailed=false;
  }

  get userInitial(): string { return (this.currentUser.trim().charAt(0) || 'U').toUpperCase(); }
  get filteredProjects(): Project[] {
    const query = this.projectQuery.trim().toLocaleLowerCase();
    return query ? this.projects.filter(p => (p.name + ' ' + p.system_name).toLocaleLowerCase().includes(query)) : this.projects;
  }
  scrollToWorkspace(): void {
    this.mobileNavOpen = false;
    document.getElementById('workspace-top')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    });
  }

  private static readonly STEP_ORDER = ['source','discover','understand','assess','plan','baseline','modernize','verify','evidence'] as const;

  isStepGateComplete(id: string): boolean {
    if (!this.selected) return false;
    if (id === 'source') return this.hasSource && !this.ingestRunning;
    if (id === 'discover') return this.isStepGateComplete('source') && this.latestRun('discover')?.status === 'completed';
    if (id === 'understand') return this.isStepGateComplete('discover') && this.latestRun('understand')?.status === 'completed';
    if (id === 'assess') return this.isStepGateComplete('understand') && this.latestRun('assess')?.status === 'completed';
    if (id === 'plan') return this.isStepGateComplete('assess') && this.latestRun('plan')?.status === 'completed' && this.selected.plan_approved === 1 && !!this.selected.active_wave_id;
    if (id === 'baseline') return this.isStepGateComplete('plan') && this.latestRun('baseline')?.status === 'completed';
    if (id === 'modernize') return this.isStepGateComplete('baseline') && this.latestRun('modernize')?.status === 'completed' && this.latestMigration?.status === 'completed';
    if (id === 'verify') {
      const v = this.latestMigration?.verification;
      return this.isStepGateComplete('modernize') && this.latestRun('verify')?.status === 'completed' && this.latestMigration?.verify_status === 'completed' && (v?.classification === 'verified_within_tests' || this.selected.migration_decision === 'approved');
    }
    if (id === 'evidence') return this.isStepGateComplete('verify');
    return false;
  }

  canNavigateTo(section: string): boolean {
    if (section === 'source') return true;
    const idx = WorkspaceFacade.STEP_ORDER.indexOf(section as any);
    if (idx <= 0) return true;
    return this.isStepGateComplete(WorkspaceFacade.STEP_ORDER[idx - 1]);
  }

  get nextStepId(): string | null {
    const idx = WorkspaceFacade.STEP_ORDER.indexOf(this.workspaceSection as any);
    if (idx < 0 || idx >= WorkspaceFacade.STEP_ORDER.length - 1) return null;
    return WorkspaceFacade.STEP_ORDER[idx + 1];
  }

  get nextStepLabel(): string {
    const next = this.nextStepId;
    if (!next) return '';
    if (next === 'evidence') return 'Evidencias';
    return STAGES.find(s => s.id === next)?.label || next;
  }

  get canAdvanceNext(): boolean {
    const next = this.nextStepId;
    return !!next && this.canNavigateTo(next) && !this.isAnyRunning && !this.ingestRunning;
  }

  advanceToNextStep(): void {
    const next = this.nextStepId;
    if (next && this.canNavigateTo(next)) {
      this.chooseWorkspaceSection(next);
    }
  }

  get stages(): StageView[] {
    return STAGES.map(s => ({...s, status: this.stageStatus(s.id) as any}));
  }
  get activeStep() { return STAGES.find(s => s.id === this.stageId) ?? STAGES[0]; }
  get activeRun(): Run | undefined { return this.latestRun(this.stageId); }
  get completedCount(): number { return STAGES.filter(s => this.stageStatus(s.id) === 'completed').length; }
  get ingestRunning(): boolean { return this.ingestJobs.some(j=>j.status==='queued'||j.status==='running'); }
  get hasSource(): boolean { return !!this.selected?.snapshot_hash; }
  get planReady(): boolean { return this.latestRun('plan')?.status === 'completed' && this.selected?.plan_approved == null; }
  get decisionReady(): boolean { return this.latestMigration?.verify_status==='completed' && !!this.latestMigration?.verification && !this.latestMigration?.decision; }
  get isAnyRunning(): boolean { return !!this.selected?.runs?.some(r => r.status === 'running'); }
  get stageRunnable(): boolean { return !!this.selected?.phase_ready?.[this.stageId] && !this.isAnyRunning; }
  get chosenProvider(): Provider | undefined { return this.providers.find(p => p.id === this.selected?.provider); }

  latestRun(stage: string): Run | undefined {
    return [...(this.selected?.runs || [])].reverse().find(r => r.phase === stage);
  }
  stageStatus(stage: string): string {
    const latest = this.latestRun(stage);
    if (latest?.status === 'running') return 'running';
    if (stage === 'modernize' && (this.latestMigration?.status === 'queued' || this.latestMigration?.status === 'running')) return 'running';
    if (stage === 'verify' && (this.latestMigration?.verify_status === 'queued' || this.latestMigration?.verify_status === 'running')) return 'running';
    if (latest?.status === 'failed') return 'failed';
    if (stage === 'modernize' && this.latestMigration?.status === 'failed') return 'failed';
    if (stage === 'verify' && (this.latestMigration?.verify_status === 'failed' || this.latestMigration?.verification?.classification === 'divergent')) return 'failed';
    if (!this.canNavigateTo(stage)) return 'locked';
    if (stage === 'verify') {
      return this.isStepGateComplete('verify') ? 'completed' : 'ready';
    }
    if (!this.isStepGateComplete(stage)) return 'ready';
    return 'completed';
  }
  displayStatus(status: string, stage?: string): string {
    if (status === 'completed' && stage === 'verify' && this.latestMigration?.verification?.classification === 'verified_within_tests') {
      return 'Correcto';
    }
    return ({completed:'Completado',running:'Ejecutando',failed:'Error',ready:'Actual',locked:'Pendiente',pending:'Pendiente'} as Record<string,string>)[status] || status;
  }
  get currentPhaseTitle(): string {
    if (this.workspaceSection === 'source') return 'Paso 0 · Código Fuente';
    if (this.workspaceSection === 'evidence') return 'Entregables y Evidencias';
    const step = this.activeStep;
    return `Paso ${step.index} · ${step.label}`;
  }
  get currentPhaseFeedback(): string {
    if (!this.selected) return '';
    if (this.ingestRunning) return `Importando y escaneando fuente (${this.scanProgress?.percent || 0}%)…`;
    if (!this.hasSource) return 'Sube un archivo ZIP o usa un ejemplo para iniciar.';
    if (this.isAnyRunning) {
      const running = [...(this.selected.runs || [])].reverse().find(r => r.status === 'running');
      return `Ejecutando ${(running?.phase || this.stageId).toUpperCase()}…`;
    }
    const sec = this.workspaceSection;
    if (sec === 'source') return 'Fuente lista. Avanza al paso 1 (Discover) para revisar el inventario.';
    if (sec === 'evidence') return `${this.selected.artifacts?.length || 0} artefactos listos para consulta y descarga.`;
    if (this.stageId === 'plan' && this.latestRun('plan')?.status === 'completed') {
      if (this.selected.plan_approved !== 1) return 'Paso actual: Aprueba el Blueprint para continuar.';
      if (!this.selected.active_wave_id) return 'Paso actual: Aprueba una Wave compatible para desbloquear Baseline.';
    }
    if (this.stageId === 'verify' && this.isStepGateComplete('verify')) {
      return this.latestMigration?.verification?.classification === 'verified_within_tests'
        ? 'Verificación correcta: todas las pruebas equivalentes pasaron antes y después.'
        : 'Verificación aprobada. Puedes descargar los entregables.';
    }
    if (this.isStepGateComplete(this.stageId)) {
      return `Resultado listo. Revisa la información y avanza a ${this.nextStepLabel}.`;
    }
    return `Paso actual: Pulsa «Ejecutar ${this.activeStep.label}» para procesar esta fase.`;
  }
  readableDate(value?: string): string {
    return value ? new Date(value).toLocaleString('es-BO', {dateStyle:'short',timeStyle:'short'}) : '—';
  }
  showError(err: unknown): void {
    const http = err as HttpErrorResponse;
    this.error = typeof http?.error?.detail === 'string' ? http.error.detail : http?.message || String(err);
    this.busy = false; this.uploading = false;
  }
  toast(msg: string): void { this.notice = msg; this.error = ''; }
  dismiss(): void { this.notice = ''; this.error = ''; }

  login(): void {
    if (this.busy || !this.password || !this.email) return;
    this.busy = true; this.error = '';
    const result = this.registerMode
      ? this.api.register(this.email, this.password, this.displayName, this.bootstrapSecret, this.invitationToken)
      : this.api.login(this.email, this.password);
    result.subscribe({
      next: response => {
        this.password='';this.bootstrapSecret='';this.invitationToken='';this.busy=false;
        try {
          const user = response.user ? {username: response.user.display_name, email: response.user.email, id: response.user.id} : null;
          this.session.save(response.access_token, response.expires_at, user);
          this.registerMode=false;
        } catch(e) { this.showError(e); }
      },
      error: e => this.showError(e)
    });
  }
  logout(): void {
    const token = this.session.token;
    if (token) this.api.logout().subscribe({error: () => { /* Local logout must still succeed offline. */ }});
    this.session.clear(token ?? undefined);
  }
  bootstrap(): void {
    const token = this.session.token;
    if (!token) return;
    this.sessionCheckFailed = false;
    // Do not run project requests until the server confirms this session.
    this.api.me().subscribe({
      next: user => {
        if (this.session.token !== token) return; // Stale response from a previous account.
        this.session.rememberUser(user, token);
        this.api.providers().subscribe({
          next: rows => {if(this.session.token===token) this.providers=rows;},
          error:e => {if(this.session.token===token) this.showError(e);}
        });
        this.loadOrganizations();
        this.loadProjects();
        this.refreshGithub();
      },
      error: e => {
        if (this.session.token !== token) return;
        if (e instanceof HttpErrorResponse && e.status===401) {
          this.session.clear(token);
        } else {
          // A temporary 502, timeout or network error must never revoke a valid login.
          this.sessionCheckFailed=true;
          this.error='No se pudo verificar tu sesión con el servidor. Tu acceso se conserva. Reintenta cuando vuelva la conexión.';
        }
      }
    });
  }
  loadOrganizations(): void {
    const token=this.session.token;
    if (!token) return;
    this.api.organizations().subscribe({next:rows=>{
      if (this.session.token!==token) return;
      this.organizations=rows;
      if (!rows.some(o=>o.id===this.createOrganizationId)) this.createOrganizationId=rows[0]?.id || '';
    },error:e=>{if(this.session.token===token)this.showError(e);}});
  }
  loadProjects(): void {
    const token = this.session.token;
    this.api.projects().subscribe({next: rows => {
      if (!token || this.session.token!==token) return;
      this.projects=rows;
      if (!this.selected && rows.length) this.select(rows[0].id);
    }, error:e=>{if(this.session.token===token)this.showError(e);}});
  }
  refreshGithub(): void {
    const token = this.session.token;
    if (!token) return;
    this.api.githubStatus().subscribe({next: state => {
      if (this.session.token!==token) return;
      this.githubConfigured=state.configured;
      this.githubLogin=state.connection?.github_login || '';
      if (this.githubLogin) this.api.githubRepos().subscribe({next: rows=>{
        if (this.session.token===token) this.githubRepos=rows;
      }});
    }});
  }
  connectGithub(): void {
    this.api.githubConnect().subscribe({next:r=>window.location.assign(r.url),error:e=>this.showError(e)});
  }
  disconnectGithub(): void {
    this.api.githubDisconnect().subscribe({next:()=>{this.githubLogin='';this.githubRepos=[];this.toast('GitHub desconectado.');},error:e=>this.showError(e)});
  }
  editProject(): void {
    if (!this.selected) return;
    this.editName=this.selected.name;this.editSystem=this.selected.system_name;this.showEdit=true;
  }
  saveProject(): void {
    if (!this.selected) return;
    const pid=this.selected.id;
    this.api.rename(pid,this.editName,this.editSystem).subscribe({next:()=>{
      this.showEdit=false;this.refreshSelected();this.loadProjects();this.toast('Proyecto actualizado.');
    },error:e=>this.showError(e)});
  }
  create(): void {
    if (this.busy || this.createName.trim().length<3 || this.createSystem.trim().length<2) return;
    const token = this.session.token;
    if (!token) return;
    this.busy=true;
    this.api.create(this.createName.trim(), this.createSystem.trim(), this.createProvider, this.createOrganizationId).subscribe({
      next: p => {
        if (this.session.token!==token) return;
        this.busy=false; this.showCreate=false; this.createName=''; this.createSystem='';
        this.projects=[p,...this.projects]; this.select(p.id); this.toast('Proyecto creado. Sube el ZIP para comenzar.');
      },
      error:e=>{if(this.session.token===token)this.showError(e);}
    });
  }
  select(pid: string): void {
    const token = this.session.token;
    if (!token) return;
    const requestId = ++this.selectedRequestId;
    this.eventsAbort?.abort();this.lastEventId=0;this.ingestJobs=[];
    this.discoverData=null;this.scanProgress=null;this.sourceFiles=[];this.reviews=[];this.understandData=null;this.assessmentData=null;this.assessmentProgress=null;this.selectedFilePaths=new Set();
    this.selected = undefined; this.artifactContent=''; this.artifactName=''; this.stageId='discover';this.workspaceSection='source';
    this.api.project(pid).subscribe({next:p=>{
      if(this.session.token!==token || requestId!==this.selectedRequestId) return;
      this.selected=p; this.pickFirstArtifact(); this.loadSnapshots(pid);this.loadJobs(pid);this.loadDiscover(pid);this.loadAnalysisControls(pid);this.loadReviews(pid);this.loadUnderstand(pid);this.loadAssessment(pid);this.loadBlueprint(pid);this.loadBaselinePreflight(pid);this.loadMigration(pid,true);this.connectEvents(pid);
      const currentStep = WorkspaceFacade.STEP_ORDER.find(s => !this.isStepGateComplete(s)) || 'verify';
      this.workspaceSection = currentStep;
      if (STAGES.some(x => x.id === currentStep)) this.stageId = currentStep;
    },error:e=>{if(this.session.token===token)this.showError(e);}});
  }
  refreshSelected(silent=false): void {
    if (!this.selected) return;
    const pid = this.selected.id;
    this.api.project(pid).subscribe({next: updated => {
      if (!this.selected || this.selected.id !== pid) return;
      const was = this.selected.runs?.length ?? 0;
      const hadRunning = this.selected.runs?.some(x=>x.status==='running') ?? false;
      const sourceChanged=updated.active_snapshot_id!==this.selected.active_snapshot_id;
      this.selected=updated;
      if (!silent || sourceChanged) { this.loadSnapshots(pid);this.loadJobs(pid); }
      this.loadMigration(pid);
      if (sourceChanged) {this.blueprint=null;this.blueprintDraft=null;this.baselinePreflight=null;this.pickFirstArtifact();this.loadDiscover(pid);this.loadAnalysisControls(pid);this.loadReviews(pid);this.loadUnderstand(pid);this.loadAssessment(pid);}
      if (hadRunning && !this.isAnyRunning) {
        if (this.activeRun?.status === 'completed') {
          this.toast('Fase completada; consulta el artefacto generado.');this.loadReviews(pid);this.loadUnderstand(pid);this.loadAssessment(pid);this.loadBlueprint(pid);
          this.openArtifact(this.activeStep.file);
        } else if (this.activeRun?.status === 'failed') {
          this.error=this.activeRun.error || 'La operación falló';
        }
      }
      if (!silent || was===0) this.loadProjects();
    },error:e=>{if(!silent)this.showError(e)}});
  }
  pickFirstArtifact(): void {
    const arr = this.selected?.artifacts || [];
    if (arr.includes('inventory.md')) this.openArtifact('inventory.md');
    else if (arr.length) this.openArtifact(arr[0]);
  }
  chooseStage(id: string): void {
    if (!this.canNavigateTo(id)) {
      this.toast('Completa primero el paso actual antes de avanzar.');
      return;
    }
    this.stageId=id;this.workspaceSection=id;
    const step=STAGES.find(x=>x.id===id);
    if (step && this.selected?.artifacts?.includes(step.file)) this.openArtifact(step.file);
  }
  loadJobs(pid: string): void {
    this.api.jobs(pid).subscribe({next:items=>{if(this.selected?.id===pid){
      const previouslyRunning=this.ingestRunning;
      this.ingestJobs=items;
      if (previouslyRunning && !this.ingestRunning) {
        this.refreshSelected();this.loadDiscover(pid);this.loadAnalysisControls(pid);this.loadReviews(pid);this.loadAssessment(pid);
        this.toast(items[0]?.status==='completed'?'Código importado y escaneado.':'La ingesta terminó; revisa su estado.');
      }
    }}});
  }
  retryJob(jid: string): void {
    if (!this.selected) return;
    this.api.retry(this.selected.id,jid).subscribe({next:()=>{
      this.toast('Ingesta reintentada.');this.loadJobs(this.selected!.id);
    },error:e=>this.showError(e)});
  }
  cancelJob(jid: string): void {
    if (!this.selected) return;
    this.api.cancel(this.selected.id,jid).subscribe({next:()=>{
      this.toast('Trabajo cancelado.');this.loadJobs(this.selected!.id);
    },error:e=>this.showError(e)});
  }
  private async connectEvents(pid: string): Promise<void> {
    this.eventsAbort?.abort();
    const controller=new AbortController();this.eventsAbort=controller;
    try {
      const token=this.session.token;
      if (!token) return;
      const response=await fetch(this.api.eventsUrl(pid, this.lastEventId), {
        headers:{Authorization:`Bearer ${token}`},signal:controller.signal,cache:'no-store'
      });
      if (!response.ok || !response.body) return; // Polling remains as fallback.
      const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
      while(!controller.signal.aborted) {
        const {value,done}=await reader.read();if(done) break;
        buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');
        const frames=buffer.split('\n\n');buffer=frames.pop() || '';
        for(const frame of frames) {
          const id=frame.match(/^id: (\d+)/m);
          if(id) this.lastEventId=Math.max(this.lastEventId,Number(id[1]));
          if(frame.includes('event: update') && this.selected?.id===pid) {
            const match=frame.match(/^data: (.+)$/m);
            if (match) {
              try {
                const evt=JSON.parse(match[1]);
                if(evt.kind==='analysis-progress') this.scanProgress=evt.payload as ScanProgress;
                if(evt.kind==='analysis' && evt.payload?.phase==='discover') this.loadDiscover(pid);
                if(evt.kind==='analysis' && evt.payload?.phase==='assess') this.loadAssessment(pid);
                if(evt.kind==='wave'||evt.kind==='baseline')this.loadBaselinePreflight(pid);
                if(evt.kind==='blueprint') this.loadBlueprint(pid);
                if(evt.kind==='migration'||evt.kind==='verify'||evt.kind==='migration-review')this.loadMigration(pid);
                if(evt.kind==='analysis-stage') this.assessmentProgress=evt.payload;
              } catch { /* Ignore malformed event; polling is the fallback. */ }
            }
            this.refreshSelected(true);this.loadJobs(pid);
          }
        }
      }
    } catch(e) { if(!controller.signal.aborted) { /* polling fallback */ } }
  }
  get isAssessRunning():boolean {return this.latestRun('assess')?.status==='running';}
  loadUnderstand(pid:string):void {
    this.api.analysisPhase(pid,'understand').subscribe({next:r=>{
      if(this.selected?.id===pid && this.selected.active_snapshot_id===r.snapshot_id)
        this.understandData=r.data;
    },error:()=>{/* No snapshot or understand yet. */}});
  }
  loadAssessment(pid:string):void {
    this.api.analysisPhase(pid,'assess').subscribe({next:r=>{
      if(this.selected?.id===pid && this.selected.active_snapshot_id===r.snapshot_id)
        this.assessmentData=r.data as AssessmentData|null;
    },error:()=>{/* No snapshot or assessment yet. */}});
  }
  get filteredSourceFiles():SourceFile[] {
    const term=this.fileQuery.toLocaleLowerCase().trim();
    return this.sourceFiles.filter(f=>f.selected && (!term || (f.path+' '+f.language).toLocaleLowerCase().includes(term))).slice(0,100);
  }
  get filteredReviews():ReviewItem[] {
    const term=this.reviewQuery.toLocaleLowerCase().trim();
    return this.reviews.filter(r=>(this.reviewFilter==='all'||r.status===this.reviewFilter) &&
      (!term || (r.title+' '+r.phase+' '+JSON.stringify(r.evidence)).toLocaleLowerCase().includes(term))).slice(0,70);
  }
  get selectedFileCount():number {return this.selectedFilePaths.size;}
  loadAnalysisControls(pid:string):void {
    this.api.analysisFiles(pid).subscribe({next:r=>{
      if(this.selected?.id!==pid||this.selected.active_snapshot_id!==r.snapshot_id)return;
      this.sourceFiles=r.files;this.selectedFilePaths=new Set(r.selected_files);this.selectionExplicit=r.explicit_selection;
    }});
    this.api.analysisConsent(pid).subscribe({next:r=>{
      if(this.selected?.id===pid&&this.selected.active_snapshot_id===r.snapshot_id) this.selected={...this.selected, code_consent:r.approved};
    }});
  }
  toggleSourceFile(path:string,on:boolean):void {
    const files=new Set(this.selectedFilePaths);
    if(on)files.add(path);else files.delete(path);
    this.selectedFilePaths=files;
  }
  selectAllCodeFiles():void {this.selectedFilePaths=new Set(this.sourceFiles.filter(f=>f.selected).map(f=>f.path));}
  saveSourceSelection():void {
    if(!this.selected||!this.selectedFileCount)return;
    const pid=this.selected.id;this.analysisBusy=true;
    this.api.setAnalysisSelection(pid,[...this.selectedFilePaths]).subscribe({next:()=>{
      this.analysisBusy=false;this.selectionExplicit=true;this.assessmentData=null;this.toast('Selección guardada; los análisis dependientes se invalidaron si cambió.');this.refreshSelected();
    },error:e=>{this.analysisBusy=false;this.showError(e);}});
  }
  updateCodeConsent(approved:boolean):void {
    if(!this.selected)return;
    const pid=this.selected.id;this.analysisBusy=true;
    this.api.setAnalysisConsent(pid,approved).subscribe({next:r=>{
      this.analysisBusy=false;if(this.selected?.id===pid)this.selected={...this.selected, code_consent:r.approved};
      this.toast(approved?'Autorización de envío de código registrada.':'Autorización revocada.');this.refreshSelected();
    },error:e=>{this.analysisBusy=false;this.showError(e);}});
  }
  loadReviews(pid:string):void {
    this.api.reviewItems(pid).subscribe({next:rows=>{
      if(this.selected?.id===pid){this.reviews=rows;
        const draft={...this.reviewDraft};
        for(const item of rows) if(!(item.id in draft))draft[item.id]=item.comment||'';
        this.reviewDraft=draft;
      }
    }});
  }
  setReviewDraft(id:string, comment:string):void { this.reviewDraft={...this.reviewDraft,[id]:comment}; }
  updateReview(row:ReviewItem,status:'pending'|'confirmed'|'discarded'):void {
    if(!this.selected)return;
    const pid=this.selected.id;
    this.api.reviewItem(pid,row.id,status,this.reviewDraft[row.id]||'').subscribe({next:()=>{
      this.loadReviews(pid);this.toast('Revisión guardada en el snapshot activo.');
    },error:e=>this.showError(e)});
  }
  loadDiscover(pid: string): void {
    this.api.discover(pid).subscribe({next: r=>{
      if(this.selected?.id===pid && r.snapshot_id===this.selected.active_snapshot_id) {
        this.discoverData=r.data;
        if(this.ingestRunning && r.progress) this.scanProgress=r.progress;
      }
    },error:()=>{/* Keep ZIP and existing phases available on older servers. */}});
  }
  loadSnapshots(pid: string): void {
    this.api.snapshots(pid).subscribe({next:items=>{if(this.selected?.id===pid)this.snapshots=items;}});
  }
  activateSnapshot(sid: string): void {
    if (!this.selected || sid===this.selected.active_snapshot_id) return;
    const pid=this.selected.id;
    this.busy=true;this.discoverData=null;this.scanProgress=null;this.sourceFiles=[];this.reviews=[];this.assessmentData=null;this.assessmentProgress=null;this.selectedFilePaths=new Set();
    this.api.activateSnapshot(pid,sid).subscribe({next:()=>{
      this.busy=false;this.artifactContent='';this.refreshSelected();this.toast('Snapshot activado. Análisis anteriores invalidado.');
    },error:e=>this.showError(e)});
  }
  importRepository(): void {
    if (!this.selected || !this.selectedRepo || this.uploading) return;
    const pid=this.selected.id;this.uploading=true;this.scanProgress=null;
    this.api.importGit(pid,this.selectedRepo).subscribe({next:job=>{
      this.uploading=false;this.toast(`Importación ${job.id.slice(0,8)} en cola. Sigue el progreso.`);this.loadJobs(pid);
    },error:e=>this.showError(e)});
  }
  upload(event: Event): void {
    const input=event.target as HTMLInputElement;
    const file=input.files?.[0];
    if (!file || !this.selected) return;
    this.uploading=true;this.scanProgress=null;
    this.api.upload(this.selected.id,file).subscribe({
      next: job=>{this.uploading=false;this.toast(`Ingesta ${job.id.slice(0,8)} iniciada.`);this.loadJobs(this.selected!.id);},
      error:e=>this.showError(e)
    });
    input.value='';
  }
  loadDemo(): void {
    if (!this.selected || this.uploading) return;
    const pid = this.selected.id; this.uploading = true;this.scanProgress=null;
    this.api.demoZip().subscribe({
      next: blob => {
        const file = new File([blob], 'legacylift-demo.zip', {type: 'application/zip'});
        this.api.upload(pid, file).subscribe({
          next: job => { this.uploading = false; this.toast(`Ejemplo: ingesta ${job.id.slice(0,8)} en ejecución.`);this.loadJobs(pid); },
          error: e => this.showError(e)
        });
      }, error: e => this.showError(e)
    });
  }
  loadCobolDemo(): void {
    if (!this.selected || this.uploading) return;
    const pid = this.selected.id; this.uploading = true; this.scanProgress = null;
    this.api.demoCobolZip().subscribe({
      next: blob => {
        const file = new File([blob], 'demo-cobol.zip', {type: 'application/zip'});
        this.api.upload(pid, file).subscribe({
          next: job => { this.uploading = false; this.toast(`Ejemplo COBOL: ingesta ${job.id.slice(0,8)} en ejecución.`); this.loadJobs(pid); },
          error: e => this.showError(e)
        });
      }, error: e => this.showError(e)
    });
  }
  run(): void {
    if (!this.selected || !this.stageRunnable) return;
    this.busy=true;
    this.api.run(this.selected.id,this.stageId).subscribe({
      next:()=>{this.busy=false; this.toast('Ejecución iniciada. La interfaz actualizará el estado.');this.refreshSelected();},
      error:e=>this.showError(e)
    });
  }
  loadBlueprint(pid:string):void {
    this.api.blueprint(pid).subscribe({next:r=>{
      if(this.selected?.id!==pid)return;
      this.blueprint=r.blueprint;this.loadPacks(pid);
      this.blueprintDraft=r.blueprint?structuredClone(r.blueprint.data):null;
    },error:()=>{/* Older version or plan not generated. */}});
  }
  chooseWorkspaceSection(section:string):void {
    if (!this.canNavigateTo(section)) {
      this.toast('Completa primero el paso actual antes de avanzar.');
      return;
    }
    this.workspaceSection=section;
    if(STAGES.some(stage=>stage.id===section))this.chooseStage(section);
  }
  loadBaselinePreflight(pid:string):void {
    this.api.baselinePreflight(pid).subscribe({next:r=>{
      if(this.selected?.id===pid)this.baselinePreflight=r;
    },error:()=>{/* Not authorized or baseline not ready. */}});
  }
  authorizeExecution(approved:boolean):void {
    if(!this.selected||this.baselineBusy)return;
    const pid=this.selected.id;this.baselineBusy=true;
    this.api.baselineConsent(pid,approved).subscribe({next:()=>{
      this.baselineBusy=false;this.loadBaselinePreflight(pid);this.refreshSelected();
      this.toast(approved?'Autorización para pruebas en worker registrada.':'Ejecución de pruebas revocada; resultados dependientes invalidados.');
    },error:e=>{this.baselineBusy=false;this.showError(e);}});
  }
  generateTestSuggestions():void {
    if(!this.selected||this.baselineBusy)return;
    const pid=this.selected.id;this.baselineBusy=true;
    this.api.baselineSuggestions(pid).subscribe({next:r=>{
      this.baselineBusy=false;this.loadBaselinePreflight(pid);
      this.toast(r.proposals.length?'Propuestas generadas; revísalas antes de aprobar.':'No se generaron propuestas. El modo Demo no inventa casos de IA.');
    },error:e=>{this.baselineBusy=false;this.showError(e);}});
  }
  decideTestProposal(id:string,approved:boolean):void {
    if(!this.selected)return;
    const pid=this.selected.id;
    this.api.baselineReview(pid,id,approved).subscribe({next:()=>{
      this.loadBaselinePreflight(pid);this.toast(approved?'Caso propuesto aprobado (aún no se ejecuta automáticamente).':'Propuesta descartada.');
    },error:e=>this.showError(e)});
  }
  loadPacks(pid:string):void {
    this.api.migrationPacks(pid).subscribe({next:r=>{if(this.selected?.id===pid)this.packs=r;},error:()=>{/* existing endpoints stay available */}});
  }
  waveDecision(wid:string,approved:boolean):void {
    if(!this.selected)return;const pid=this.selected.id;
    this.api.decideWave(pid,wid,approved).subscribe({next:r=>{
      this.blueprint=r;this.blueprintDraft=structuredClone(r.data);this.refreshSelected();this.loadBaselinePreflight(pid);
      this.toast(approved?'Wave aprobada y seleccionada. Puedes ejecutar Baseline.':'Wave rechazada.');
    },error:e=>this.showError(e)});
  }
  selectWave(wid:string):void {
    if(!this.selected)return;const pid=this.selected.id;
    this.api.selectWave(pid,wid).subscribe({next:r=>{
      this.blueprint=r;this.refreshSelected();this.loadBaselinePreflight(pid);this.toast('Wave seleccionada; baseline anterior invalidado.');
    },error:e=>this.showError(e)});
  }
  updateBlueprintDraft(data:BlueprintData):void {
    this.blueprintDraft={...data,waves:data.waves.map(w=>({...w,files:[...w.files],depends_on:[...w.depends_on]}))};
  }
  saveBlueprint():void {
    if(!this.selected||!this.blueprintDraft||this.blueprintSaving)return;
    const pid=this.selected.id;this.blueprintSaving=true;
    this.api.editBlueprint(pid,this.blueprintDraft).subscribe({next:r=>{
      this.blueprintSaving=false;this.blueprint=r;this.blueprintDraft=structuredClone(r.data);this.loadPacks(pid);
      this.toast('Blueprint guardado como revisión '+r.revision+'. Las aprobaciones anteriores ya no aplican.');
      this.refreshSelected();this.openArtifact('blueprint.md');
    },error:e=>{this.blueprintSaving=false;this.showError(e);}});
  }
  planDecision(approved:boolean): void {
    if (!this.selected) return;
    this.api.plan(this.selected.id,approved).subscribe({
      next:()=>{this.toast(approved?'Blueprint aprobado. Revisa y aprueba una wave antes de preparar el baseline.':'Plan rechazado.');this.refreshSelected();this.loadBlueprint(this.selected!.id);},
      error:e=>this.showError(e)
    });
  }
  loadMigration(pid:string, focusActiveStep=false):void {
    this.api.migrationAttempts(pid).subscribe({next:r=>{
      if(this.selected?.id!==pid)return;
      const previous=this.latestMigration?.id;
      this.migrationAttempts=r.attempts;
      this.migrationWorkerSeen=r.worker?.last_seen ?? null;
      if (focusActiveStep) {
        const currentStep = WorkspaceFacade.STEP_ORDER.find(s => !this.isStepGateComplete(s)) || 'verify';
        this.workspaceSection = currentStep;
        if (STAGES.some(x => x.id === currentStep)) this.stageId = currentStep;
      }
      const active=this.latestMigration;
      if(!active || active.status!=='completed') {this.migrationReview=null;this.migrationDiffFile='';this.migrationDiffText='';return;}
      if(previous!==active.id){this.migrationDiffFile='';this.migrationDiffText='';}
      if(previous!==active.id || !this.migrationReview) {
        this.api.migrationReview(pid,active.id).subscribe({next:s=>{
          if(this.selected?.id===pid && this.latestMigration?.id===active.id)this.migrationReview=s;
        },error:()=>{if(this.latestMigration?.id===active.id)this.migrationReview=null;}});
      }
    },error:()=>{/* The classic pipeline still loads if the migration extension is unavailable. */}});
  }
  showMigrationDiff(path:string):void {
    if(!this.selected||!this.latestMigration)return;
    if(this.migrationDiffFile===path){this.migrationDiffFile='';this.migrationDiffText='';return;}
    const aid=this.latestMigration.id;this.migrationDiffFile=path;this.migrationDiffText='Cargando...';
    this.api.migrationDiff(this.selected.id,aid,path).subscribe({next:r=>{
      if(this.latestMigration?.id===aid&&this.migrationDiffFile===path)this.migrationDiffText=r.diff;
    },error:e=>this.showError(e)});
  }
  setMigrationFile(path:string,selected:boolean,comment:string):void {
    if(!this.selected||!this.latestMigration)return;
    const pid=this.selected.id,aid=this.latestMigration.id;
    this.api.migrationSetReview(pid,aid,path,selected,comment).subscribe({next:r=>{
      if(this.latestMigration?.id===aid){this.migrationReview=r;this.loadMigration(pid);this.toast('Revisión guardada. Ejecuta Verify nuevamente si cambiaste archivos.');}
    },error:e=>this.showError(e)});
  }
  retryMigration():void {
    if(!this.selected||!this.latestMigration)return;
    const pid=this.selected.id;
    this.api.migrationRetry(pid,this.latestMigration.id).subscribe({next:()=>{
      this.toast('Nuevo intento encolado; requiere el worker separado.');this.refreshSelected();this.loadMigration(pid);
    },error:e=>this.showError(e)});
  }
  migrationDecision(approved:boolean):void {
    if(!this.selected||!this.latestMigration)return;
    this.api.decision(this.selected.id,approved,this.migrationRiskReason).subscribe({next:()=>{
      this.toast(approved?'Candidato aprobado. Puedes descargar ZIP, parche e informe.':'Intento rechazado. Se conserva el historial.');
      this.migrationRiskReason='';this.refreshSelected();this.loadMigration(this.selected!.id);
    },error:e=>this.showError(e)});
  }
  downloadMigration(format:'zip'|'patch'|'report'):void {
    if(!this.selected)return;
    this.api.migrationDelivery(this.selected.id,format).subscribe({next:blob=>{
      const url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download=format==='zip'?'legacylift-entrega.zip':format==='patch'?'legacylift-changes.patch':'legacylift-report.md';
      a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    },error:e=>this.showError(e)});
  }
  openArtifact(filename:string): void {
    if (!this.selected) return;
    this.artifactName=filename;
    this.api.artifact(this.selected.id,filename).subscribe({next: text=>{
      this.artifactContent=text;
      // Angular sanitizes the [innerHTML] binding; never bypass its sanitizer.
      this.rendered = filename.endsWith('.md') ? String(marked.parse(text)) : '';
    }, error:e=>this.showError(e)});
  }
  copyArtifact(): void { if (this.artifactContent) navigator.clipboard.writeText(this.artifactContent)
      .then(()=>this.toast('Contenido copiado')).catch(()=>this.toast('No se pudo copiar; selecciona el texto manualmente.')); }
  download(): void {
    if (!this.selected) return;
    this.api.export(this.selected.id).subscribe({next: blob=>{
      const url=URL.createObjectURL(blob);
      const el=document.createElement('a');el.href=url;el.download='legacylift-evidencias.zip';el.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }, error:e=>this.showError(e)});
  }
}
