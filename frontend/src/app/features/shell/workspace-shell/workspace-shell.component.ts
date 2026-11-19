import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { ProjectSidebarComponent } from '../project-sidebar/project-sidebar.component';
import { WorkspaceTopbarComponent } from '../workspace-topbar/workspace-topbar.component';
import { ProjectDashboardComponent } from '../../projects/project-dashboard/project-dashboard.component';
import { NoticeComponent } from '../../../shared/ui';

@Component({
  selector: 'll-workspace-shell',
  standalone: true,
  imports: [CommonModule, ProjectSidebarComponent, WorkspaceTopbarComponent, ProjectDashboardComponent, NoticeComponent],
  templateUrl: './workspace-shell.component.html',
  styleUrl: './workspace-shell.component.css'
})
export class WorkspaceShellComponent {
  readonly vm = inject(WorkspaceFacade);
}
