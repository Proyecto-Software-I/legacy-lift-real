import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { ButtonDirective, IconComponent, StatusBadgeComponent } from '../../../shared/ui';
@Component({selector:'ll-verify-result',standalone: true,imports:[CommonModule,ButtonDirective,IconComponent,StatusBadgeComponent],
  templateUrl: './verify-result.component.html',styleUrl: './verify-result.component.css'})
export class VerifyResultComponent {
  readonly vm=inject(WorkspaceFacade);
  outcomeLabel(v:{outcome?:string;classification:string}):string {
    const outcome=v.outcome || (v.classification==='verified_within_tests'?'aprobado':v.classification==='divergent'?'fallido':'sin_evidencia');
    return ({aprobado:'Aprobado dentro de las pruebas',fallido:'Fallido',sin_evidencia:'Sin evidencia suficiente'} as Record<string,string>)[outcome];
  }
  verdict(classification:string):string {
    return ({verified_within_tests:'Las pruebas equivalentes pasaron antes y después',
      partial:'La evidencia es parcial',
      divergent:'El candidato se comporta distinto al original',
      not_demonstrated:'No se pudo demostrar la equivalencia'} as Record<string,string>)[classification] || 'Resultado sin clasificar';
  }
}
