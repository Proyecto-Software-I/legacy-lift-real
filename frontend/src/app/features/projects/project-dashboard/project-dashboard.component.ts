import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective, MetricCardComponent, NoticeComponent } from '../../../shared/ui';
import { SourcePanelComponent } from '../source-panel/source-panel.component';
import { PipelinePanelComponent } from '../../pipeline/pipeline-panel/pipeline-panel.component';
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

@Component({
  selector: 'll-project-dashboard',
  standalone: true,
  imports: [CommonModule, IconComponent, ButtonDirective, MetricCardComponent, NoticeComponent, SourcePanelComponent, PipelinePanelComponent, PhaseActionsComponent, ArtifactsPanelComponent, DiscoverOverviewComponent, AnalysisControlsComponent, ReviewQueueComponent, AssessmentOverviewComponent, BlueprintEditorComponent, MigrationPacksComponent, BaselinePanelComponent, MigrationReviewComponent, VerifyResultComponent],
  templateUrl: './project-dashboard.component.html',
  styleUrl: './project-dashboard.component.css'
})
export class ProjectDashboardComponent {
  readonly vm = inject(WorkspaceFacade);
  readonly workspaceTabs = [
    {id:'source',label:'Fuente',icon:'upload_file',group:'INGESTA'},
    {id:'discover',label:'Discover',icon:'manage_search',group:'S2 · ANÁLISIS'},
    {id:'understand',label:'Understand',icon:'account_tree',group:'S2 · ANÁLISIS'},
    {id:'assess',label:'Assess',icon:'monitor_heart',group:'S2 · ANÁLISIS'},
    {id:'plan',label:'Plan',icon:'schema',group:'S3 · PLAN'},
    {id:'baseline',label:'Baseline',icon:'fact_check',group:'S3 · PLAN'},
    {id:'modernize',label:'Modernize',icon:'auto_fix_high',group:'S4 · EJECUCIÓN'},
    {id:'verify',label:'Verify',icon:'verified',group:'S4 · EJECUCIÓN'},
    {id:'evidence',label:'Evidencias',icon:'folder_open',group:'ENTREGABLES'}
  ] as const;

  isStageTab(id: string): boolean {
    return id !== 'source' && id !== 'evidence';
  }

  tabStatus(id: string): string {
    if (id === 'source') {
      if (this.vm.ingestRunning) return 'running';
      return this.vm.hasSource ? 'completed' : 'ready';
    }
    if (id === 'evidence') return this.vm.isStepGateComplete('verify') ? (this.vm.workspaceSection === 'evidence' ? 'ready' : 'completed') : 'locked';
    return this.vm.stageStatus(id);
  }

  tabLabelStatus(id: string): string {
    const st = this.tabStatus(id);
    if (id === 'source') return st === 'completed' ? 'Completado' : st === 'running' ? 'Importando' : 'Actual';
    if (id === 'evidence') return st === 'locked' ? 'Pendiente' : st === 'ready' ? 'Actual' : 'Disponible';
    return this.vm.displayStatus(st, id);
  }
}
