"""Provider adapters. Credentials never leave this server for the Angular client.

Codex uses the official locally-authenticated CLI (ChatGPT account).
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


def provider_status() -> list[dict]:
    return [
        {'id': 'codex', 'name': 'ChatGPT / Codex CLI', 'ready': shutil.which(os.getenv('CODEX_BIN', 'codex')) is not None,
         'help': 'Requiere codex login en la computadora anfitriona. El binario puede existir sin estar autenticado.'},
        {'id': 'claude', 'name': 'Claude / Anthropic API', 'ready': bool(os.getenv('CLAUDE_API_KEY')),
         'help': 'Requiere CLAUDE_API_KEY; la suscripción de Claude no sustituye la API key.'},
        {'id': 'demo', 'name': 'Demo determinista sin IA', 'ready': True,
         'help': 'Para probar la interfaz sin consumir tokens. Resultados explícitamente locales.'},
    ]


def codex_run(prompt: str, workspace: Path, edit: bool = False) -> str:
    cli = os.getenv('CODEX_BIN', 'codex')
    if shutil.which(cli) is None:
        raise AIError('Codex CLI no encontrado. Instala @openai/codex y ejecuta codex login en este PC.')
    model = os.getenv('CODEX_MODEL', 'gpt-6.1-sol').strip() or 'gpt-6.1-sol'
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


def claude_run(prompt: str) -> str:
    token = os.getenv('CLAUDE_API_KEY', '')
    if not token:
        raise AIError('CLAUDE_API_KEY no configurada en el backend')
    try:
        response = httpx.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key':token, 'anthropic-version':'2023-06-01', 'content-type':'application/json'},
            json={'model':os.getenv('CLAUDE_MODEL', 'claude-sonnet-5'), 'max_tokens':6000,
                  'system':'Eres un ingeniero de software. Evita inventar evidencia, cambios no solicitados y afirmar validaciones no realizadas.',
                  'messages':[{'role':'user','content':prompt}]}, timeout=180)
        response.raise_for_status()
        return '\n'.join(x['text'] for x in response.json().get('content', []) if x.get('type') == 'text')
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AIError(f'Error en la API de Claude: {str(exc)[:250]}') from exc


def generate(provider: str, prompt: str, workspace: Path, edit: bool = False) -> str:
    if provider == 'codex':
        return codex_run(prompt, workspace, edit=edit)
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
