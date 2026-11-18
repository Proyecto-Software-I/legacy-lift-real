from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import ai, db, inspection, workspace
from .analysis import context as analysis_context, understanding, assessment
from . import planning, migration_packs, baseline

STAGES = ('discover', 'understand', 'assess', 'plan', 'baseline', 'modernize', 'verify')
PREVIOUS = {'discover': None, 'understand': 'discover', 'assess': 'understand',
            'plan': 'assess', 'baseline': 'plan', 'modernize': 'baseline', 'verify': 'modernize'}
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='legacylift')
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
PROMPT_DIR = Path(__file__).resolve().parent / 'prompts'


def phase_ready(pid: str, phase: str) -> tuple[bool, str]:
    project = db.project_get(pid)
    if not project:
        return False, 'Proyecto no encontrado'
    if phase not in STAGES:
        return False, 'Fase inválida'
    if not project['snapshot_hash']:
        return False, 'Primero sube un ZIP'
    prev = PREVIOUS[phase]
    if prev and (not (last := db.last_run(pid, prev)) or last['status'] != 'completed'):
        return False, f'Primero completa {prev}'
    if phase in {'baseline', 'modernize', 'verify'} and project['plan_approved'] != 1:
        return False, 'Aprueba el plan antes de preparar o transformar'
    if phase in {'understand','assess','plan','modernize'} and project['provider'] in {'codex','claude'}:
        if not db.consent_is_active(pid,project.get('active_snapshot_id'),project['provider']):
            return False, 'Autoriza el envío de código al proveedor de IA antes de iniciar esta fase'
    if phase in {'baseline','modernize','verify'} and not planning.executable_wave(pid):
        return False, 'Aprueba el Blueprint y una wave con Migration Pack compatible antes de continuar'
    if phase == 'modernize':
        pack=migration_packs.selected_pack(pid,planning.executable_wave(pid))
        if pack is None or pack['status']!='available':
            return False, 'El Migration Pack seleccionado no tiene executor'
    if phase == 'verify':
        from . import migration_runs
        if not migration_runs.latest_completed(pid):
            return False, 'Se requiere un intento de Modernize completado'
    return True, ''


def artifact(pid: str, name: str) -> str:
    path = workspace.artifact_dir(pid) / name
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def prompt_for(phase: str, pid: str) -> tuple[str, str]:
    template_file = PROMPT_DIR / f'{phase}.md'
    template = template_file.read_text(encoding='utf-8') if template_file.exists() else ''
    inventory = artifact(pid, 'inventory.md')
    details = ''
    if phase == 'understand':
        details = inventory
    elif phase == 'assess':
        details = inventory + '\n' + artifact(pid, 'understand.md')
    elif phase == 'plan':
        details = inventory[:6000] + '\n' + artifact(pid, 'understand.md') + '\n' + artifact(pid, 'assessment.md')
    elif phase == 'modernize':
        wave=planning.executable_wave(pid)
        details='WAVE APROBADA (único alcance editable):\n'+json.dumps(wave,ensure_ascii=False) + '\n\n' + artifact(pid, 'baseline.md')
    source = workspace.project_candidate(pid) if phase == 'modernize' else workspace.project_source(pid)
    if phase in {'understand','assess','plan'}:
        batches, coverage = analysis_context.context_batches(pid,source,max_batches=2)
        content = '\n'.join(batches) + f'\n## COBERTURA DE CÓDIGO: {coverage}\n'
    else:
        content = workspace.read_code_context(source, limit=28000)
    p = template + '\n\n## Contexto anterior\n' + details[:18000] + '\n\n## Código fuente\n' + content
    return p, hashlib.sha256(template.encode('utf-8')).hexdigest()[:16]


def start(pid: str, phase: str) -> dict:
    with _guard:
        lock = _locks.setdefault(pid, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('Ya hay una operación en ejecución para este proyecto')
    try:
        if any(r['status']=='running' for r in db.runs_get(pid)):
            raise ValueError('Ya hay una fase ejecutándose en este proyecto')
        if any(j['status'] in ('queued','running') for j in db.jobs_list(pid)):
            raise ValueError('Espera a que termine la ingesta en curso')
        if phase in ('modernize','verify'):
            raise ValueError('Modernize y Verify solo se ejecutan en el worker separado')
        allowed, reason = phase_ready(pid, phase)
        if not allowed:
            raise ValueError(reason)
        project = db.project_get(pid)
        assert project
        prompt_hash = ''
        if phase in {'understand', 'assess', 'plan', 'modernize'}:
            prompt_hash = prompt_for(phase, pid)[1]
        # Invalidate newer phases before re-running an earlier one, keeping their history.
        successors = STAGES[STAGES.index(phase)+1:]
        db.invalidate_stages(pid, successors)
        artifact_by_stage = {'discover':['inventory.md','inventory.json'], 'understand':['understand.md','understand.json'],
                             'assess':['assessment.md','assessment.json'], 'plan':['blueprint.md','blueprint.json'], 'baseline':['baseline.md','baseline.json'],
                             'modernize':['migration.md','changes.patch'], 'verify':['verification.md','report.md']}
        for successor in successors:
            for artifact_name in artifact_by_stage[successor]:
                (workspace.artifact_dir(pid) / artifact_name).unlink(missing_ok=True)
        if any(stage in successors for stage in ('plan','baseline')):
            db.project_update(pid, plan_approved=None)
        if 'verify' in successors:
            db.project_update(pid, migration_decision=None)
        rid = db.run_create(pid, phase, project['provider'], prompt_hash)
        executor.submit(_perform_and_release, pid, phase, rid, lock)
        return {'id':rid, 'phase':phase, 'status':'running'}
    except Exception:
        lock.release()
        raise


def _perform_and_release(pid: str, phase: str, rid: str, lock: threading.Lock) -> None:
    try:
        summary, details = perform(pid, phase)
        db.run_complete(rid, summary, details)
    except Exception as exc:
        db.run_fail(rid, str(exc))
    finally:
        lock.release()


def _demo_text(phase: str, pid: str) -> str:
    data = json.loads(artifact(pid, 'inventory.json'))
    if phase == 'understand':
        return ('# Understand — Documentación de estructura (modo demo sin IA)\n\n'
                'Este documento se generó mediante reglas locales; no incluye inferencias de un modelo.\n\n'
                '## Módulos detectados\n' + ''.join(f'- `{x}`\n' for x in data['modules']) +
                '\n## Dependencias aproximadas\n' + '\n'.join(f"- `{e['from']}` → `{e['to']}`" for e in data['graph']['edges'][:20]) +
                '\n\n## Reglas de negocio\nNo inferidas: requieren un modelo o revisión manual.\n')
    if phase == 'assess':
        return ('# Assess — Evaluación local heurística\n\n'
                f"- Archivos: {data['files']}\n- Findings heurísticos: {len(data['findings'])}\n"
                f"- Archivos de prueba detectados: {len(data['test_files'])}\n\n"
                '## Recomendación\nAplicar refactorización limitada y verificar sintaxis y pruebas disponibles. '
                'No se atribuye evidencia de análisis semántico a este resultado.\n')
    return ('# Plan — Modernization Blueprint (modo demo)\n\n'
            '**Estrategia:** Refactor mínimo sobre una copia.\n\n'
            '**Arquitectura actual:** módulos identificados en inventario.\n\n'
            '**Arquitectura objetivo:** misma interfaz y comportamiento; código más legible.\n\n'
            '**Migration Wave 1:** archivo Python de la demo; eliminar paréntesis redundantes en `return`.\n\n'
            '**Pack:** safe-refactor-python@1.0.\n\n'
            '**Riesgos:** no se garantiza equivalencia sin pruebas comparables.\n\n'
            '**Aprobación humana:** obligatoria antes de preparar baseline o modificar archivos.\n')


def _baseline(pid: str, folder: Path) -> dict:
    return baseline.capture(pid, folder)


def _baseline_md(result: dict) -> str:
    return baseline.markdown(result)


def _validate_changed_paths(pid: str) -> list[str]:
    original, candidate = workspace.project_source(pid), workspace.project_candidate(pid)
    before = {f.relative_to(original) for f in original.rglob('*') if f.is_file() and not f.is_symlink()}
    after = {f.relative_to(candidate) for f in candidate.rglob('*') if f.is_file() and not f.is_symlink()}
    changed = [p for p in before & after if (original / p).read_bytes() != (candidate / p).read_bytes()]
    # New/deleted files, symlinks and modification of tests or config are denied in MVP.
    if before != after or any(f.is_symlink() for f in candidate.rglob('*')):
        raise ValueError('El modelo creó/eliminó archivos o enlaces no permitidos; candidato descartado')
    allowed = {'.py','.js','.ts','.java','.cs','.go'}
    for p in changed:
        if p.suffix.lower() not in allowed or any('test' in x.lower() for x in p.parts):
            raise ValueError(f'Cambio fuera del alcance permitido: {p.as_posix()}')
    wave=planning.executable_wave(pid)
    if not wave:raise ValueError('Modernize exige una wave aprobada y pack compatible')
    allowed_wave=set(wave['files'])
    if any(p.as_posix() not in allowed_wave for p in changed):
        raise ValueError('La IA modificó archivos fuera de la wave aprobada; se descarta el candidato')
    return [p.as_posix() for p in changed]


def _apply_claude_changes(pid: str, content: str) -> None:
    items = ai.parse_edit_result(content)
    src = workspace.project_candidate(pid)
    paths = []
    for item in items:
        p = Path(item['path'])
        if p.is_absolute() or '..' in p.parts or ':' in str(p) or not p.parts:
            raise ValueError('Ruta de archivo inválida recibida de Claude')
        target = src / p
        if not target.exists() or not target.is_file() or target.is_symlink():
            raise ValueError(f'Claude propuso editar archivo inexistente: {p}')
        if target.suffix not in {'.py','.ts','.js','.java','.cs','.go'} or any('test' in x.lower() for x in p.parts):
            raise ValueError(f'Claude propuso archivo no permitido: {p}')
        if len(item['content'].encode('utf-8')) > 90_000:
            raise ValueError('Archivo propuesto demasiado grande')
        paths.append((target, item['content']))
    if len({str(p) for p, _ in paths}) != len(paths):
        raise ValueError('Claude devolvió rutas duplicadas')
    for target, content in paths:
        target.write_text(content, encoding='utf-8')


def _modernize_demo(pid: str) -> str:
    candidate = workspace.project_candidate(pid)
    wave=planning.executable_wave(pid)
    if not wave:raise ValueError('No hay wave ejecutable aprobada')
    for rel in wave['files']:
        p=candidate/rel
        if p.suffix!='.py':continue
        if 'test' in p.relative_to(candidate).parts or 'test' in p.name:
            continue
        old = p.read_text(encoding='utf-8')
        new = re.sub(r'\breturn \(([^()\n]+)\)', r'return \1', old, count=1)
        if new != old:
            p.write_text(new, encoding='utf-8')
            return f'Modo demo: paréntesis redundantes simplificados en `{p.relative_to(candidate).as_posix()}`.'
    return 'No se encontró una transformación determinista del pack safe-refactor-python.'


def perform(pid: str, phase: str, progress=None) -> tuple[str, dict]:
    project = db.project_get(pid)
    assert project
    provider = project['provider']
    source = workspace.project_source(pid)
    if phase == 'discover':
        info = inspection.inspect(source, progress=progress)
        project = db.project_get(pid)
        if project and project.get('active_snapshot_id'):
            db.analysis_put(pid, project['active_snapshot_id'], 'discover', info)
        workspace.save_artifact(pid, 'inventory.json', json.dumps(info, indent=2, ensure_ascii=False))
        workspace.save_artifact(pid, 'inventory.md', inspection.inventory_md(info))
        return f"Inventario de {info['files']} archivos y {len(info['modules'])} módulos", info
    if phase in {'understand', 'assess', 'plan'}:
        # Defense in depth: worker/in-process calls must respect privacy too.
        if provider in {'codex','claude'} and not db.consent_is_active(pid,project.get('active_snapshot_id'),provider):
            raise ValueError('El proyecto requiere consentimiento explícito para enviar código a IA')
        if phase=='understand':
            template=(PROMPT_DIR/'understand.md').read_text(encoding='utf-8')
            return understanding.understand(pid,provider,template,source)
        if phase=='assess':
            template=(PROMPT_DIR/'assess.md').read_text(encoding='utf-8')
            return assessment.assess(pid,provider,template,source)
        prompt, _ = prompt_for(phase, pid)
        if provider == 'demo':
            text=_demo_text(phase,pid)
        elif provider == 'codex':
            with analysis_context.scoped_workspace(pid,source) as isolated:
                text=ai.generate(provider,prompt,isolated)
        else:
            text=ai.generate(provider,prompt,source)
        if phase == 'plan':
            # The AI narrative is advisory. The structured blueprint is constrained by the
            # source snapshot and remains a proposal until explicitly approved.
            result=planning.proposal(pid,text[:30000],provider)
            return f"Blueprint revisión {result['revision']}: {len(result['data']['waves'])} waves", {'blueprint_id':result['id'],'revision':result['revision']}
        filename = {'understand':'understand.md','assess':'assessment.md','plan':'blueprint.md'}[phase]
        workspace.save_artifact(pid, filename, text)
        return text[:250].replace('\n', ' '), {'artifact':filename, 'mode':provider}
    if phase == 'baseline':
        data = _baseline(pid,source)
        workspace.save_artifact(pid, 'baseline.md', _baseline_md(data))
        workspace.save_artifact(pid, 'baseline.json', json.dumps(data,ensure_ascii=False,indent=2))
        return f"Baseline registrado: {data['static_checks']['total']} verificaciones de sintaxis", data
    if phase == 'modernize':
        if not planning.executable_wave(pid):raise ValueError('Se requiere wave aprobada y pack compatible')
        if provider in {'codex','claude'} and not db.consent_is_active(pid,project.get('active_snapshot_id'),provider):
            raise ValueError('El proyecto requiere consentimiento para enviar código a IA')
        candidate = workspace.copy_snapshot(pid)
        try:
            if provider == 'demo':
                response = _modernize_demo(pid)
            else:
                prompt, _ = prompt_for('modernize', pid)
                if provider == 'claude':
                    response = ai.generate(provider, prompt + '\n\nRESPONDE EXCLUSIVAMENTE JSON: {"changes":[{"path":"ruta/existente.py","content":"contenido completo de reemplazo"}]}. Máximo 5 archivos existentes de código fuente. No edites tests ni configuración.', candidate, edit=True)
                    _apply_claude_changes(pid, response)
                else:
                    response = ai.generate(provider, prompt, candidate, edit=True)
            changed = _validate_changed_paths(pid)
            diff, count = workspace.diff_candidate(pid)
            if not changed or not count:
                raise ValueError('No se generó una modificación de código; no se considera completada la migración')
            workspace.save_artifact(pid, 'changes.patch', diff)
            workspace.save_artifact(pid, 'migration.md', '# Modernize — Ejecución\n\n' + response[:13000] + f'\n\nArchivos modificados: {count}\n')
            db.project_update(pid, migration_decision=None)
            return f'{count} archivo(s) modernizado(s), diff disponible', {'modified_files':changed}
        except Exception:
            workspace.copy_snapshot(pid)
            raise
    if phase == 'verify':
        candidate = workspace.project_candidate(pid)
        original = _baseline(pid,source)
        current = _baseline(pid,candidate)
        static_old, static_new = original['static_checks'], current['static_checks']
        baseline_run = db.last_run(pid, 'baseline')
        recorded = baseline_run['data'] if baseline_run else {}
        tests_old = recorded.get('tests', {'status':'not_run'})
        tests_new = current['tests']
        diff, count = workspace.diff_candidate(pid)
        if not count:
            raise ValueError('El candidato no tiene cambios')
        checks_passed = static_new['failed'] == 0 and static_old['failed'] == 0
        comparable = tests_old.get('status') == 'passed' and tests_new.get('status') == 'passed'
        tests_failed = tests_new.get('status') == 'failed'
        status = 'failed' if not checks_passed or tests_failed else ('passed' if comparable else 'insufficient_evidence')
        score = 90 if status == 'passed' else (0 if status == 'failed' else None)
        data = {'result':status, 'confidence_score':score, 'static_before':static_old,
                'static_after':static_new, 'tests_before':tests_old, 'tests_after':tests_new,
                'files_modified':count, 'note':'Score orientativo, no prueba equivalencia absoluta'}
        md = ('# Verify — Evidencia de modernización\n\n'
              f'**Resultado:** `{status}`\n\n**Confidence Score:** '
              + (f'{score}/100 (orientativo)' if score is not None else 'N/D — evidencia insuficiente') + '\n\n'
              f"- Archivos cambiados: {count}\n- Sintaxis previa: {static_old['passed']}/{static_old['total']}\n"
              f"- Sintaxis candidata: {static_new['passed']}/{static_new['total']}\n"
              f"- Tests baseline: `{tests_old.get('status')}`\n- Tests candidato: `{tests_new.get('status')}`\n\n"
              'No se declara equivalencia funcional cuando no hay pruebas comparables ejecutadas.\n')
        workspace.save_artifact(pid, 'verification.md', md)
        return 'Verificación: ' + status, data
    raise ValueError('Fase inválida')


def decide_plan(pid: str, approved: bool, uid: str = '') -> None:
    run = db.last_run(pid, 'plan')
    if not run or run['status'] != 'completed':
        raise ValueError('Primero genera el plan')
    planning.decide(pid, uid or db.project_get(pid)['owner_id'], approved)


def decide_migration(pid: str, accepted: bool) -> None:
    run = db.last_run(pid, 'verify')
    if not run or run['status'] != 'completed':
        raise ValueError('Primero ejecuta Verify')
    if accepted and run['data'].get('result') == 'failed':
        raise ValueError('No se puede aprobar una migración con verificaciones fallidas')
    db.project_update(pid, migration_decision='approved' if accepted else 'rejected')
    if not accepted:
        if workspace.project_candidate(pid).exists():
            shutil.rmtree(workspace.project_candidate(pid))
    workspace.save_artifact(pid, 'report.md', '# Informe LegacyLift\n\n' +
                            ('**Decisión: Aprobada.**\n\n' if accepted else '**Decisión: Rechazada, candidato descartado.**\n\n') +
                            artifact(pid, 'verification.md'))
