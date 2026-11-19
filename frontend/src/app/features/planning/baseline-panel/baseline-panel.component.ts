import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { ButtonDirective, IconComponent, StatusBadgeComponent } from '../../../shared/ui';

@Component({selector:'ll-baseline-panel', standalone: true,
  imports:[CommonModule,ButtonDirective,IconComponent,StatusBadgeComponent],
  templateUrl: './baseline-panel.component.html', styleUrl: './baseline-panel.component.css'})
export class BaselinePanelComponent {
  readonly vm=inject(WorkspaceFacade);
  workerLabel(mode:string):string {
    return ({disabled:'Solo sintaxis','docker-local':'Docker local',docker:'Docker',separate:'Worker separado',vm:'Máquina aislada'} as Record<string,string>)[mode] || mode;
  }
}
