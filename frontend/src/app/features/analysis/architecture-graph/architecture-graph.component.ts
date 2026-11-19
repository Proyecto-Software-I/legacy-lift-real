import { Component, Input, OnChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AnalysisGraph } from '../../../core/api/api.service';

export interface ModuleCard {
  id: string;
  label: string;
  files: number;
  kind: string;
  layer: 'entry' | 'core' | 'leaf';
  roleLabel: string;
  imports: { id: string; label: string; type: string; inferred: boolean }[];
  importedBy: { id: string; label: string; type: string; inferred: boolean }[];
}

@Component({
  selector: 'll-architecture-graph',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './architecture-graph.component.html',
  styleUrl: './architecture-graph.component.css'
})
export class ArchitectureGraphComponent implements OnChanges {
  @Input({required: true}) graph!: AnalysisGraph;
  nodes: ModuleCard[] = [];
  active?: ModuleCard;

  ngOnChanges(): void {
    const raw = this.graph?.nodes?.slice(0, 28) || [];
    const edges = this.graph?.edges || [];
    const labelMap = new Map(raw.map(n => [n.id, n.label]));

    this.nodes = raw.map(n => {
      const imports = edges
        .filter(e => e.from === n.id && e.to !== n.id)
        .map(e => ({ id: e.to, label: labelMap.get(e.to) || e.to, type: e.type || 'import', inferred: !!e.inferred }));
      const importedBy = edges
        .filter(e => e.to === n.id && e.from !== n.id)
        .map(e => ({ id: e.from, label: labelMap.get(e.from) || e.from, type: e.type || 'import', inferred: !!e.inferred }));

      const layer: 'entry' | 'core' | 'leaf' =
        imports.length > 0 && importedBy.length === 0 ? 'entry'
        : imports.length > 0 && importedBy.length > 0 ? 'core'
        : 'leaf';
      const roleLabel =
        layer === 'entry' ? 'Consumidor / Tests'
        : layer === 'core' ? 'Núcleo acoplado'
        : importedBy.length > 0 ? 'Módulo base (proveído)'
        : 'Módulo independiente';

      return {
        id: n.id,
        label: n.label,
        files: n.files,
        kind: n.kind || 'module',
        layer,
        roleLabel,
        imports,
        importedBy
      };
    });
    this.active = this.nodes[0];
  }

  select(node: ModuleCard): void {
    this.active = node;
  }

  isRelated(node: ModuleCard): boolean {
    if (!this.active || this.active.id === node.id) return false;
    return this.active.imports.some(i => i.id === node.id) || this.active.importedBy.some(i => i.id === node.id);
  }

  get importsText(): string {
    return this.active?.imports.length ? this.active.imports.map(i => i.label).join(', ') : 'ninguno';
  }

  get importedByText(): string {
    return this.active?.importedBy.length ? this.active.importedBy.map(i => i.label).join(', ') : 'ninguno';
  }
}
