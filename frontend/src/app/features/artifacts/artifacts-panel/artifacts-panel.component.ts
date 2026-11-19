import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective } from '../../../shared/ui';

const GROUPS: {label: string; match: RegExp}[] = [
  {label: 'Entender', match: /^(inventory|understand|assessment|discover)/},
  {label: 'Planificar', match: /^(blueprint|baseline|plan)/},
  {label: 'Modernizar', match: /^(changes|verification|verify|migration|modernize)/},
  {label: 'Entregar', match: /^(report|delivery|summary)/},
];

@Component({
  selector: 'll-artifacts-panel',
  standalone: true,
  imports: [CommonModule, IconComponent, ButtonDirective],
  templateUrl: './artifacts-panel.component.html',
  styleUrl: './artifacts-panel.component.css'
})
export class ArtifactsPanelComponent {
  readonly vm = inject(WorkspaceFacade);

  /** Files grouped by the route segment that produced them; anything unknown goes last. */
  get groups(): {label: string; files: string[]}[] {
    const files = this.vm.selected?.artifacts || [];
    const used = new Set<string>();
    const out = GROUPS.map(g => {
      const list = files.filter(f => !used.has(f) && g.match.test(f.split('/').pop() || f));
      list.forEach(f => used.add(f));
      return {label: g.label, files: list};
    });
    out.push({label: 'Otros', files: files.filter(f => !used.has(f))});
    return out.filter(g => g.files.length);
  }
}
