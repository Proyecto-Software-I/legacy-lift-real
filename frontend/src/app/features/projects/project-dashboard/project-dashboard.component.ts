import { Component, ElementRef, effect, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective, NoticeComponent } from '../../../shared/ui';
import { SourcePanelComponent } from '../source-panel/source-panel.component';
import { PhaseActionsComponent } from '../../pipeline/phase-actions/phase-actions.component';
import { AssessmentOverviewComponent } from '../../analysis/assessment-overview/assessment-overview.component';
import { AnalysisControlsComponent } from '../../analysis/analysis-controls/analysis-controls.component';
import { ReviewQueueComponent } from '../../analysis/review-queue/review-queue.component';
import { DiscoverOverviewComponent } from '../../analysis/discover-overview/discover-overview.component';
import { BaselinePanelComponent } from '../../planning/baseline-panel/baseline-panel.component';
import { MigrationPacksComponent } from '../../planning/migration-packs/migration-packs.component';
import { BlueprintEditorComponent } from '../../planning/blueprint-editor/blueprint-editor.component';
import { ArtifactsPanelComponent } from '../../artifacts/artifacts-panel/artifacts-panel.component';
import { MigrationReviewComponent } from '../../migration/migration-review/migration-review.component';
import { VerifyResultComponent } from '../../migration/verify-result/verify-result.component';

interface RouteStation { id: string; label: string; icon: string; group: string; title: string; summary: string; }

/** The project workspace is one guided route. The rail on the left is the only progress
 * indicator; the "next step" card is the only primary call to action per step. */
@Component({
  selector: 'll-project-dashboard',
  standalone: true,
  imports: [CommonModule, IconComponent, ButtonDirective, NoticeComponent, SourcePanelComponent, PhaseActionsComponent, ArtifactsPanelComponent, DiscoverOverviewComponent, AnalysisControlsComponent, ReviewQueueComponent, AssessmentOverviewComponent, BlueprintEditorComponent, MigrationPacksComponent, BaselinePanelComponent, MigrationReviewComponent, VerifyResultComponent],
  templateUrl: './project-dashboard.component.html',
  styleUrl: './project-dashboard.component.css'
})
export class ProjectDashboardComponent {
  readonly vm = inject(WorkspaceFacade);
  private readonly host = inject(ElementRef<HTMLElement>);

  readonly workspaceTabs: readonly RouteStation[] = [
    {id:'source',label:'Código fuente',icon:'upload_file',group:'Entender',title:'Código fuente',
      summary:'Importa el sistema que quieres modernizar. El repositorio original nunca se modifica.'},
    {id:'discover',label:'Discover',icon:'manage_search',group:'Entender',title:'Discover · inventario',
      summary:'Lenguajes, archivos y módulos detectados automáticamente en el snapshot.'},
    {id:'understand',label:'Understand',icon:'account_tree',group:'Entender',title:'Understand · arquitectura',
      summary:'Interpreta cómo está construido el sistema y deja preguntas abiertas para revisar.'},
    {id:'assess',label:'Assess',icon:'monitor_heart',group:'Entender',title:'Assess · riesgos',
      summary:'Señales de riesgo observadas y una estrategia recomendada por módulo.'},
    {id:'plan',label:'Plan',icon:'schema',group:'Planificar',title:'Plan · Blueprint',
      summary:'Propuesta de cambio dividida en waves. Nada se ejecuta sin tu aprobación.'},
    {id:'baseline',label:'Baseline',icon:'fact_check',group:'Planificar',title:'Baseline · comportamiento actual',
      summary:'Registra cómo se comporta hoy el código para poder compararlo después.'},
    {id:'modernize',label:'Modernize',icon:'auto_fix_high',group:'Modernizar',title:'Modernize · candidato',
      summary:'Genera los cambios sobre una copia y revisa archivo por archivo qué cambió.'},
    {id:'verify',label:'Verify',icon:'verified',group:'Modernizar',title:'Verify · decisión',
      summary:'Compara original y candidato, y decide si aceptas el resultado.'},
    {id:'evidence',label:'Entregables',icon:'folder_open',group:'Entregar',title:'Entregables',
      summary:'Documentación y evidencia generadas en cada paso, listas para descargar.'}
  ] as const;

  readonly groups = ['Entender', 'Planificar', 'Modernizar', 'Entregar'];

  constructor() {
    // On narrow screens the route scrolls horizontally: keep the current station in view.
    effect(() => {
      const section = this.vm.workspaceSection;
      setTimeout(() => {
        if (typeof window === 'undefined' || !window.matchMedia('(max-width: 1080px)').matches) return;
        const tab = this.host.nativeElement.querySelector(`#tab-${section}`) as HTMLElement | null;
        const rail = tab?.closest('.route-list') as HTMLElement | null;
        if (tab && rail) rail.scrollTo({left: tab.offsetLeft - rail.clientWidth / 2 + tab.clientWidth / 2, behavior: 'smooth'});
      });
    });
  }

  stationsIn(group: string): RouteStation[] { return this.workspaceTabs.filter(t => t.group === group); }
  get current(): RouteStation { return this.workspaceTabs.find(t => t.id === this.vm.workspaceSection) ?? this.workspaceTabs[0]; }
  get stepNumber(): number { return this.workspaceTabs.findIndex(t => t.id === this.vm.workspaceSection) + 1; }
  get completedStations(): number { return this.workspaceTabs.filter(t => t.id !== 'evidence' && this.tabStatus(t.id) === 'completed').length; }
  get totalStations(): number { return this.workspaceTabs.length - 1; }

  isStageTab(id: string): boolean {
    return id !== 'source' && id !== 'evidence';
  }

  tabStatus(id: string): string {
    if (id === 'source') {
      if (this.vm.ingestRunning) return 'running';
      return this.vm.hasSource ? 'completed' : 'ready';
    }
    if (id === 'evidence') return this.vm.isStepGateComplete('verify') ? 'completed' : 'locked';
    return this.vm.stageStatus(id);
  }

  /** A step that is "ready" but waits on a person rather than on a run. */
  needsDecision(id: string): boolean {
    const p = this.vm.selected;
    if (!p) return false;
    if (id === 'plan') return this.vm.latestRun('plan')?.status === 'completed' && (p.plan_approved !== 1 || !p.active_wave_id);
    if (id === 'verify') return this.vm.decisionReady && this.vm.latestMigration?.verification?.classification !== 'divergent';
    return false;
  }

  tabLabelStatus(id: string): string {
    const st = this.tabStatus(id);
    if (st === 'ready' && this.needsDecision(id)) return id === 'verify' ? 'Tu decisión' : 'Tu aprobación';
    if (id === 'source' && st === 'running') return 'Importando';
    if (id === 'evidence') return st === 'locked' ? 'Al terminar Verify' : 'Disponible';
    return ({completed:'Completado', running:'En curso', failed:'Requiere atención', ready:'Por hacer', locked:'Bloqueado'} as Record<string,string>)[st] || st;
  }

  /** Arrow keys move between reachable stations, as expected from a tablist. */
  onRouteKey(event: KeyboardEvent): void {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const tabs = Array.from(this.host.nativeElement.querySelectorAll('.station:not(:disabled)')) as HTMLButtonElement[];
    const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : (index + (event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
  }

  isDeterministic(model?: string): boolean { return !model || /determinist|comprobaciones/i.test(model); }

  providerName(id?: string): string {
    return this.vm.providerLabel(id);
  }
}
