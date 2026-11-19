import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, ButtonDirective } from '../../../shared/ui';

type Tone = 'action' | 'running' | 'done' | 'blocked';
type ActionKind = 'run' | 'next' | 'approvePlan' | 'approveWave' | 'fixStrategy' | 'consent' | 'decide'
  | 'toModernize' | 'retryMigration' | 'download';
interface StepAction { kind: ActionKind; label: string; icon: string; disabled?: boolean; arg?: string; arg2?: string; }
interface StepGuide { tone: Tone; kicker: string; title: string; detail: string; icon: string;
  primary?: StepAction; secondary?: StepAction; rerun?: boolean; progress?: number | null; }

/** "Your next step": the single place where each step states what is happening and offers
 * the one primary action that moves the project forward. */
@Component({
  selector: 'll-phase-actions',
  standalone: true,
  imports: [CommonModule, IconComponent, ButtonDirective],
  templateUrl: './phase-actions.component.html',
  styleUrl: './phase-actions.component.css'
})
export class PhaseActionsComponent {
  readonly vm = inject(WorkspaceFacade);

  private readonly kickers: Record<Tone, string> = {
    action: 'Tu siguiente paso', running: 'En curso', done: 'Paso completado', blocked: 'Necesita atención'
  };

  get guide(): StepGuide | null {
    const vm = this.vm, p = vm.selected;
    if (!p) return null;
    const section = vm.workspaceSection;
    const g = (tone: Tone, title: string, detail: string, extra: Partial<StepGuide> = {}): StepGuide =>
      ({tone, kicker: this.kickers[tone], title, detail,
        icon: ({action:'arrow_forward', running:'progress_activity', done:'check', blocked:'priority_high'} as Record<Tone,string>)[tone], ...extra});
    const next: StepAction = {kind:'next', label:`Continuar a ${vm.nextStepLabel}`, icon:'arrow_forward', disabled: !vm.canAdvanceNext};

    if (section === 'source') {
      if (vm.ingestRunning) return g('running', 'Importando y escaneando el código',
        `${vm.scanProgress?.scanned || 0} de ${vm.scanProgress?.total || '…'} archivos leídos. Puedes esperar aquí; el inventario se genera solo.`,
        {progress: vm.scanProgress?.percent ?? 0});
      if (!vm.hasSource) return g('action', 'Importa el código del sistema',
        'Sube un ZIP, conecta GitHub o prueba con uno de los ejemplos incluidos.');
      return g('done', 'Código importado', `Snapshot ${p.snapshot_hash?.slice(0, 12) || ''} listo. El inventario se generó automáticamente.`, {primary: next});
    }

    if (section === 'evidence') {
      return g('done', 'Modernización completada',
        `El proyecto modernizado está listo junto a ${p.artifacts?.length || 0} documentos de evidencia. El paquete completo incluye código, parche, informe y documentación.`,
        {primary: {kind:'download', label:'Descargar paquete completo', icon:'download'}});
    }

    const step = vm.activeStep;
    const status = vm.stageStatus(section);
    const run = vm.latestRun(section);
    const migration = vm.latestMigration;

    if (status === 'running') {
      if (section === 'modernize' && migration?.status === 'queued') return g('running', 'En cola: esperando al worker de migración',
        `Un proceso separado debe tomar el trabajo. Última señal del worker: ${vm.migrationWorkerSeen ? vm.readableDate(vm.migrationWorkerSeen) : 'sin registro'}.`);
      if (section === 'verify' && migration?.verify_status === 'queued') return g('running', 'En cola: esperando al worker de verificación',
        'La comparación empezará cuando el worker tome el trabajo.');
      const pct = vm.assessmentProgress?.phase === section ? vm.assessmentProgress?.progress : null;
      return g('running', `Ejecutando ${step.label}…`, 'Puedes revisar otros pasos mientras tanto; esta página se actualiza sola.', {progress: pct});
    }

    if (section === 'verify' && migration?.verification?.classification === 'divergent') {
      return g('blocked', 'El candidato diverge del original',
        'Las pruebas no coinciden antes y después del cambio, así que no se puede aprobar. Pide otra iteración desde Modernize.',
        {primary: {kind:'toModernize', label:'Volver a Modernize', icon:'arrow_back'}});
    }
    if ((section === 'verify' || section === 'modernize') && migration?.decision === 'rejected') {
      return g('blocked', 'Rechazaste este intento',
        'El candidato se descartó y el código original sigue intacto (rollback). Puedes pedir otra iteración de la misma wave o volver a Plan para cambiarla.',
        {primary: {kind:'retryMigration', label:'Solicitar otra iteración', icon:'restart_alt'}});
    }
    if (section === 'modernize' && migration?.status === 'failed') {
      return g('blocked', 'Modernize no se completó', migration.error || 'El worker devolvió un error.',
        {primary: {kind:'retryMigration', label:'Solicitar otra iteración', icon:'restart_alt'}});
    }
    if (status === 'failed') {
      return g('blocked', `${step.label} falló`, run?.error || 'La ejecución terminó con un error.',
        {primary: {kind:'run', label:`Ejecutar ${step.label} de nuevo`, icon:'refresh', disabled: !vm.stageRunnable || vm.busy}});
    }

    if (section === 'plan' && run?.status === 'completed' && vm.blueprint) {
      const waves = vm.blueprint.data.waves;
      const executable = waves.filter(w => vm.packMatch(w).compatible);
      const fixable = waves.find(w => vm.strategyFix(w));
      if (!executable.length && fixable) {
        const fix = vm.strategyFix(fixable)!;
        return g('action', `La wave ${fixable.id} no se puede ejecutar todavía`,
          `Su estrategia «${fixable.strategy}» no tiene un Migration Pack disponible. Cámbiala a «${fix}» para continuar; se guardará una nueva revisión del plan.`,
          {primary: {kind:'fixStrategy', label:`Cambiar a ${fix}`, icon:'build', arg: fixable.id, arg2: fix, disabled: vm.blueprintSaving}});
      }
      if (!executable.length) return g('blocked', 'Ninguna wave es ejecutable',
        'Ningún Migration Pack disponible admite estas waves. Edita el Blueprint (lenguaje, estrategia o archivos) y guarda una nueva revisión.');
      if (p.plan_approved === 0) return g('blocked', 'Rechazaste este plan',
        'Edita el Blueprint y guarda una nueva revisión, o vuelve a ejecutar Plan para generar otra propuesta.', {rerun: true});
      if (p.plan_approved !== 1) return g('action', 'Aprueba el Blueprint',
        'Revisa las waves propuestas abajo. Al aprobar, podrás elegir qué wave ejecutar.',
        {primary: {kind:'approvePlan', label:'Aprobar Blueprint', icon:'check'}, rerun: true});
      if (!p.active_wave_id) {
        const pending = executable.find(w => vm.blueprint!.wave_approvals[w.id] !== 'rejected') || executable[0];
        return g('action', 'Aprueba la wave que quieres ejecutar',
          `${pending.id}: ${pending.goal}. Solo se modifica una wave por vez.`,
          {primary: {kind:'approveWave', label:`Aprobar ${pending.id}`, icon:'check', arg: pending.id}});
      }
    }

    if (section === 'understand' && p.provider !== 'demo' && !p.code_consent && status !== 'completed') {
      return g('action', `Autoriza el envío de código a ${vm.providerLabel(p.provider)}`,
        'Understand envía fragmentos del snapshot a un servicio externo. Nada se envía sin tu autorización.',
        {primary: {kind:'consent', label:'Autorizar envío', icon:'shield', disabled: vm.analysisBusy}});
    }

    if (section === 'verify' && vm.decisionReady) {
      const v = migration!.verification!;
      const score = v.confidence_score != null ? ` Confidence Score orientativo: ${v.confidence_score}/100.` : '';
      return g('action', 'Decide si aceptas el resultado', v.classification === 'verified_within_tests'
        ? `Las pruebas equivalentes pasaron antes y después.${score} Confirma la aprobación para habilitar los entregables.`
        : `No hay evidencia suficiente para demostrar equivalencia.${score} Puedes aprobar justificando el riesgo, o rechazar el intento.`,
        {primary: {kind:'decide', label:'Ir a la decisión', icon:'arrow_downward'}});
    }

    if (vm.isStepGateComplete(section)) {
      const verified = section === 'verify' && migration?.verification?.classification === 'verified_within_tests';
      const title = section === 'verify' ? 'Versión aprobada' : `${step.label} completado`;
      const detail = section === 'verify'
        ? (verified ? 'Aprobaste un candidato que superó las pruebas equivalentes. Los entregables están listos.' : 'Aprobaste el candidato asumiendo el riesgo documentado. Los entregables están listos.')
        : (run?.summary || 'Resultado listo para revisar.');
      return g('done', title, detail, {primary: next, rerun: section !== 'modernize' && section !== 'verify'});
    }

    const hints: Record<string, string> = {
      discover: 'Genera el inventario de lenguajes, archivos y módulos.',
      understand: 'Interpreta la arquitectura de los archivos seleccionados abajo.',
      assess: 'Calcula señales de riesgo y propone una estrategia por módulo.',
      plan: 'Propone un Blueprint con waves de cambio a partir del análisis.',
      baseline: 'Por defecto solo se comprueba la sintaxis. Si quieres ejecutar las pruebas del repositorio, autorízalo abajo antes de empezar.',
      modernize: 'Genera un candidato sobre una copia del código para la wave aprobada.',
      verify: 'Compara el código original con los archivos que decidiste conservar.'
    };
    return g('action', `Ejecuta ${step.label}`, vm.stageRunnable || vm.isAnyRunning ? hints[section] || step.description
      : 'Este paso aún no está disponible para el snapshot activo.',
      {primary: {kind:'run', label:`Ejecutar ${step.label}`, icon:'play_arrow', disabled: !vm.stageRunnable || vm.busy || vm.ingestRunning}});
  }

  act(action: StepAction): void {
    const vm = this.vm;
    switch (action.kind) {
      case 'run': vm.run(); break;
      case 'next': vm.advanceToNextStep(); break;
      case 'approvePlan': vm.planDecision(true); break;
      case 'approveWave': if (action.arg) vm.waveDecision(action.arg, true); break;
      case 'fixStrategy': if (action.arg && action.arg2) vm.adoptStrategy(action.arg, action.arg2); break;
      case 'consent': vm.updateCodeConsent(true); break;
      case 'decide': document.getElementById('verify-decision')?.scrollIntoView({behavior:'smooth', block:'center'});
        setTimeout(() => (document.querySelector('#verify-decision textarea, #verify-decision button') as HTMLElement | null)?.focus({preventScroll:true}), 350); break;
      case 'toModernize': vm.chooseWorkspaceSection('modernize'); break;
      case 'retryMigration': vm.retryMigration(); break;
      case 'download': vm.download(); break;
    }
  }
}
