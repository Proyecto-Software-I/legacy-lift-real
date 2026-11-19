import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { IconComponent } from '../icon/icon.component';

/** Shared accessible status semantics for jobs, phases, connections and decisions.
 * Raw backend enums never reach the user: unknown values fall back to a neutral label. */
@Component({
  selector: 'll-status-badge',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './status-badge.component.html',
  styleUrl: './status-badge.component.css'
})
export class StatusBadgeComponent {
  @Input() status = 'pending';
  @Input() label = '';
  get tone(): string {
    if (['completed', 'approved', 'active', 'success', 'connected', 'passed', 'verified_within_tests', 'available'].includes(this.status)) return 'success';
    if (['failed', 'rejected', 'error', 'divergent', 'timeout'].includes(this.status)) return 'danger';
    if (['running', 'queued'].includes(this.status)) return 'info';
    if (['ready', 'action', 'draft'].includes(this.status)) return 'signal';
    return 'neutral';
  }
  get icon(): string {
    if (this.status === 'locked') return 'lock';
    return ({ success:'check_circle', danger:'error', info:'progress_activity', signal:'radio_button_checked', neutral:'schedule' } as Record<string,string>)[this.tone];
  }
  get text(): string {
    return ({completed:'Completado', approved:'Aprobado', active:'Activo', success:'Correcto', connected:'Conectado',
      failed:'Error', rejected:'Rechazado', error:'Error', running:'En curso', queued:'En cola', ready:'Por hacer',
      locked:'Bloqueado', canceled:'Cancelado', pending:'Pendiente', draft:'Borrador', superseded:'Reemplazado',
      passed:'Superadas', not_run:'No ejecutadas', timeout:'Tiempo agotado', partial:'Parcial',
      verified_within_tests:'Verificado', not_demonstrated:'No demostrado', divergent:'Divergente',
      available:'Disponible', planned:'Planificado'} as Record<string,string>)[this.status] || 'Sin dato';
  }
}
