"""Provider adapters. Credentials never leave this server for the Angular client.

Codex uses the official locally-authenticated CLI (ChatGPT account).
Gemini uses Google's Antigravity CLI (`agy`), authenticated with a Google account
(e.g. Google AI Pro) on the host; no API key is stored by LegacyLift.
Claude uses the official Anthropic Messages API and an independent API key.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx


class AIError(RuntimeError):
    pass


AI_PROVIDERS = ('codex', 'gemini', 'claude')
# Local agent CLIs read their working directory, so they must only ever receive a
# scoped, read-only copy of the files the operator selected.
LOCAL_AGENTS = ('codex', 'gemini')
GEMINI_PROMPT_FILE = 'LEGACYLIFT_INSTRUCCIONES.md'


def provider_status() -> list[dict]:
    return [
        {'id': 'codex', 'name': 'ChatGPT / Codex CLI', 'ready': shutil.which(os.getenv('CODEX_BIN', 'codex')) is not None,
         'help': 'Requiere codex login en la computadora anfitriona. El binario puede existir sin estar autenticado.'},
        {'id': 'gemini', 'name': 'Gemini / Google AI Pro (Antigravity CLI)',
         'ready': shutil.which(os.getenv('GEMINI_BIN', 'agy')) is not None,
         'help': 'Requiere Antigravity CLI (agy) con sesión iniciada con tu cuenta de Google en este PC.'},
        {'id': 'claude', 'name': 'Claude / Anthropic API', 'ready': bool(os.getenv('CLAUDE_API_KEY')),
         'help': 'Requiere CLAUDE_API_KEY; la suscripción de Claude no sustituye la API key.'},
        {'id': 'demo', 'name': 'Demo determinista sin IA', 'ready': True,
         'help': 'Para probar la interfaz sin consumir tokens. Resultados explícitamente locales.'},
    ]


DETERMINISTIC_ENGINE = 'reglas deterministas locales (sin IA)'


def model_for(provider: str) -> str:
    """Model identifier recorded with every run and artifact (product §9 traceability)."""
    if provider == 'codex':
        return os.getenv('CODEX_MODEL', 'gpt-6.1-sol').strip() or 'gpt-6.1-sol'
    if provider == 'gemini':
        return os.getenv('GEMINI_MODEL', 'gemini-4-hydrogen-medium').strip() or 'gemini-4-hydrogen-medium'
    if provider == 'claude':
        return os.getenv('CLAUDE_MODEL', 'claude-sonnet-5').strip() or 'claude-sonnet-5'
    return DETERMINISTIC_ENGINE


def is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ('usage limit', 'quota', 'rate limit', 'resource_exhausted', 'resource exhausted'))


def codex_run(prompt: str, workspace: Path, edit: bool = False) -> str:
    cli = os.getenv('CODEX_BIN', 'codex')
    if shutil.which(cli) is None:
        raise AIError('Codex CLI no encontrado. Instala @openai/codex y ejecuta codex login en este PC.')
    model = model_for('codex')
    env = os.environ.copy()
    # When ~/.codex is mounted read-only (inside the hardened migration-worker container),
    # copy auth.json & config.toml to writable /tmp/codex-home and disable nested bwrap
    # (Docker cap_drop: ALL blocks nested user namespaces inside the container sandbox).
    ro_home = Path.home() / '.codex'
    if (ro_home / 'auth.json').is_file() and not os.access(ro_home, os.W_OK):
        rw_home = Path('/tmp/codex-home')
        rw_home.mkdir(parents=True, exist_ok=True)
        for fname in ('auth.json', 'config.toml'):
            if (ro_home / fname).is_file():
                shutil.copy2(ro_home / fname, rw_home / fname)
        env['CODEX_HOME'] = str(rw_home)
    cmd = [cli, 'exec', '-m', model, '-c', 'model_reasoning_effort="low"',
           '--cd', str(workspace), '--sandbox', 'read-only',
           '--skip-git-repo-check', '-']
    try:
        result = subprocess.run(cmd, input=prompt, text=True, encoding='utf-8', errors='replace',
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=330, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AIError('Codex excedió el límite de 330 segundos') from exc
    if result.returncode:
        raise AIError(f'Codex terminó con error: {result.stderr[-1000:] or result.stdout[-1000:]}')
    return (result.stdout or result.stderr)[-45000:]


def gemini_run(prompt: str, workspace: Path) -> str:
    """Run Antigravity CLI headless and read-only.

    Safety: `--mode=plan` never edits files, `--sandbox` restricts the terminal and the
    per-session `request-review` permission makes every gated tool (commands, web, ...)
    auto-deny because nobody can approve it in headless mode. The operator's global
    allow-rules are cleared for the session. The cwd is a scoped temporary copy.
    """
    cli = os.getenv('GEMINI_BIN', 'agy')
    if shutil.which(cli) is None:
        raise AIError('Antigravity CLI (agy) no encontrado. Instálalo e inicia sesión con tu cuenta de Google en este PC.')
    model = model_for('gemini')
    instruction = prompt
    # A single argv entry is limited to 128 KiB on Linux: hand long prompts over as a file
    # inside the (temporary, scoped) workspace.
    if len(prompt.encode('utf-8')) > 100_000:
        (workspace / GEMINI_PROMPT_FILE).write_text(prompt, encoding='utf-8')
        instruction = (f'Lee completo el archivo {GEMINI_PROMPT_FILE} de este directorio y cumple exactamente sus '
                       'instrucciones. No edites archivos ni ejecutes comandos. Responde solo con lo que pide.')
    settings = json.dumps({'toolPermission': 'request-review', 'permissions': {'allow': []}})
    limit = 300
    cmd = [cli, f'--print={instruction}', f'--model={model}', '--mode=plan', '--sandbox',
           f'--settings={settings}', '--output-format=json', f'--print-timeout={limit}s']
    try:
        result = subprocess.run(cmd, cwd=str(workspace), text=True, encoding='utf-8', errors='replace',
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=limit + 30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AIError(f'Gemini excedió el límite de {limit} segundos') from exc
    payload = None
    for line in reversed((result.stdout or '').strip().splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            payload = candidate.get('result') if isinstance(candidate.get('result'), dict) else candidate
            break
    if result.returncode or not payload:
        detail = (result.stderr or result.stdout or '').strip()[-1000:]
        raise AIError(f'Gemini terminó con error: {detail or "sin salida"}')
    if payload.get('status') != 'SUCCESS':
        raise AIError(f"Gemini terminó con error: {payload.get('error') or payload.get('status')}")
    response = str(payload.get('response') or '')
    if not response.strip():
        denied = ', '.join(sorted({str(a.get('action')) for a in payload.get('denied_actions') or []})) or 'ninguna'
        raise AIError(f'Gemini no produjo respuesta (acciones denegadas por seguridad: {denied}).')
    return response[-45000:]


def claude_run(prompt: str) -> str:
    token = os.getenv('CLAUDE_API_KEY', '')
    if not token:
        raise AIError('CLAUDE_API_KEY no configurada en el backend')
    try:
        response = httpx.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key':token, 'anthropic-version':'2023-06-01', 'content-type':'application/json'},
            json={'model':model_for('claude'), 'max_tokens':6000,
                  'system':'Eres un ingeniero de software. Evita inventar evidencia, cambios no solicitados y afirmar validaciones no realizadas.',
                  'messages':[{'role':'user','content':prompt}]}, timeout=180)
        response.raise_for_status()
        return '\n'.join(x['text'] for x in response.json().get('content', []) if x.get('type') == 'text')
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AIError(f'Error en la API de Claude: {str(exc)[:250]}') from exc


def generate(provider: str, prompt: str, workspace: Path, edit: bool = False) -> str:
    if provider == 'codex':
        return codex_run(prompt, workspace, edit=edit)
    if provider == 'gemini':
        return gemini_run(prompt, workspace)
    if provider == 'claude':
        return claude_run(prompt)
    if provider == 'demo':
        raise AIError('El adaptador demo se ejecuta mediante las plantillas deterministas del pipeline')
    raise AIError('Proveedor no soportado')


def parse_edit_result(text: str) -> list[dict]:
    # Extract structured JSON replacement content for explicitly chosen files.
    from .analysis.understanding import _json_object
    try:
        doc = _json_object(text)
        if not isinstance(doc, dict):
            raise ValueError('No se encontró JSON válido')
        changes = doc['changes']
        if not isinstance(changes, list) or len(changes) > 5:
            raise ValueError('Máximo cinco cambios')
        for item in changes:
            if not isinstance(item.get('path'), str) or not isinstance(item.get('content'), str):
                raise ValueError('Formato de cambio inválido')
        return changes
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AIError('El modelo no devolvió un JSON de cambios válido. No se alteró el candidato.') from exc
