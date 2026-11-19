import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective } from '../../../shared/ui';

@Component({selector:'ll-review-queue',standalone: true,
 imports:[CommonModule,FormsModule,IconComponent,ButtonDirective],
 templateUrl: './review-queue.component.html',styleUrl: './review-queue.component.css'})
export class ReviewQueueComponent {
  readonly vm=inject(WorkspaceFacade);
  get pendingCount():number {return this.vm.reviews.filter(r=>r.status==='pending').length;}
  phaseLabel(phase:string):string {
    return ({discover:'Discover · análisis estático',understand:'Understand · regla de IA',assess:'Assess · señal de riesgo'} as Record<string,string>)[phase] || phase;
  }
}
