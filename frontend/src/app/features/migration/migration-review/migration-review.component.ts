import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { ButtonDirective, IconComponent, StatusBadgeComponent } from '../../../shared/ui';

@Component({selector:'ll-migration-review',standalone: true,imports:[CommonModule,ButtonDirective,IconComponent,StatusBadgeComponent],
  templateUrl: './migration-review.component.html',styleUrl: './migration-review.component.css'})
export class MigrationReviewComponent {
  readonly vm=inject(WorkspaceFacade);
  get diffLines():string[] {return (this.vm.migrationDiffText||'').split('\n');}
  lineClass(line:string):string {
    if(line.startsWith('+++')||line.startsWith('---')||line.startsWith('@@'))return 'ln-meta';
    if(line.startsWith('+'))return 'ln-add';
    if(line.startsWith('-'))return 'ln-del';
    return '';
  }
}
