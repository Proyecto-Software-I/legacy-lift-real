import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { BlueprintData, MigrationWave } from '../../../core/api/api.service';
import { ButtonDirective, IconComponent } from '../../../shared/ui';

@Component({selector:'ll-blueprint-editor',standalone: true,
  imports:[CommonModule, FormsModule, ButtonDirective, IconComponent],
  templateUrl: './blueprint-editor.component.html', styleUrl: './blueprint-editor.component.css'})
export class BlueprintEditorComponent {
  readonly vm=inject(WorkspaceFacade);
  /** Product §2.3 vocabulary; only strategies with an available Migration Pack can execute. */
  readonly strategies=['Keep','Stabilize','Encapsulate','Rehost','Replatform','Refactor','Rearchitect','Rewrite','Replace','Retire','LanguageMigration'];
  get draft():BlueprintData|null {return this.vm.blueprintDraft;}
  changeText(field:'title'|'target_architecture'|'current_architecture'|'notes',value:string):void {
    if(!this.draft)return;this.vm.updateBlueprintDraft({...this.draft,[field]:value});
  }
  changeWave(index:number,field:'goal'|'risk'|'acceptance_criteria',value:string):void {
    if(!this.draft)return;
    const waves=this.draft.waves.map((w,i)=>i===index?{...w,[field]:value}:w);
    this.vm.updateBlueprintDraft({...this.draft,waves});
  }
  changeStrategy(index:number,strategy:string):void {
    if(!this.draft)return;
    const waves=this.draft.waves.map((w,i)=>i===index?{...w,strategy}:w);
    this.vm.updateBlueprintDraft({...this.draft,waves});
  }
  changePriority(index:number,value:string):void {
    if(!this.draft)return;
    const waves=this.draft.waves.map((w,i)=>i===index?{...w,priority:Number(value)}:w);
    this.vm.updateBlueprintDraft({...this.draft,waves});
  }
}
