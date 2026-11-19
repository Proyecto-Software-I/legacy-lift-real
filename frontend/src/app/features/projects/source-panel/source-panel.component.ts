import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective, StatusBadgeComponent } from '../../../shared/ui';

@Component({
  selector: 'll-source-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, IconComponent, ButtonDirective, StatusBadgeComponent],
  templateUrl: './source-panel.component.html',
  styleUrl: './source-panel.component.css'
})
export class SourcePanelComponent {
  readonly vm = inject(WorkspaceFacade);
  dragging = false;

  onDragOver(event: DragEvent): void { event.preventDefault(); this.dragging = true; }

  /** Dropping a ZIP uses the same upload path as the file picker. */
  onDrop(event: DragEvent): void {
    event.preventDefault(); this.dragging = false;
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (!/\.zip$/i.test(file.name)) { this.vm.showError('Solo se admiten archivos .zip.'); return; }
    this.vm.upload({target: {files: [file], value: ''}} as unknown as Event);
  }
}
