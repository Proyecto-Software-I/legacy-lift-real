import { CommonModule } from '@angular/common';
import { Component,inject } from '@angular/core';
import { MigrationWave } from '../../../core/api/api.service';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { ButtonDirective, IconComponent, StatusBadgeComponent } from '../../../shared/ui';

@Component({selector:'ll-migration-packs',standalone: true,
  imports:[CommonModule,ButtonDirective,IconComponent,StatusBadgeComponent],
  templateUrl: './migration-packs.component.html', styleUrl: './migration-packs.component.css'})
export class MigrationPacksComponent {
  readonly vm=inject(WorkspaceFacade);
  originLabel(origin?:string):string {
    return ({ai:'propuesta por IA',static:'propuesta estática sin IA',manual:'editado a mano'} as Record<string,string>)[origin||''] || 'origen no registrado';
  }
  get hasExecutable():boolean {return !!this.vm.blueprint?.data.waves.some(w=>this.vm.packMatch(w).compatible);}
  missingDeps(wave:MigrationWave):string[] {
    const approvals=this.vm.blueprint?.wave_approvals||{};
    return (wave.depends_on||[]).filter(dep=>approvals[dep]!=='approved');
  }
}
