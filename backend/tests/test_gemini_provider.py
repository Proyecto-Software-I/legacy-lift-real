"""Gemini (Antigravity CLI) adapter: read-only, headless, scoped — verified with a fake `agy`."""
import json
import os
import stat
from pathlib import Path

import pytest

from app import ai


def fake_agy(tmp_path: Path, payload: dict | None, exit_code: int = 0, stderr: str = '') -> Path:
    """Write an `agy` stand-in that records argv/cwd and prints a canned JSON result."""
    log = tmp_path / 'calls.jsonl'
    script = tmp_path / 'agy'
    body = json.dumps(payload) if payload is not None else ''
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, sys\n'
        f'open({str(log)!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")\n'
        f'sys.stderr.write({stderr!r})\n'
        f'print({body!r})\n'
        f'sys.exit({exit_code})\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return log


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / 'scoped'
    folder.mkdir()
    (folder / 'a.py').write_text('x = 1\n')
    return folder


def test_gemini_runs_read_only_in_plan_mode_and_parses_response(tmp_path, workspace, monkeypatch):
    log = fake_agy(tmp_path, {'status': 'SUCCESS', 'response': '{"ok": true}\n'})
    monkeypatch.setenv('GEMINI_BIN', str(tmp_path / 'agy'))
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-4-hydrogen-medium')
    assert ai.generate('gemini', 'Analiza', workspace).strip() == '{"ok": true}'
    call = json.loads(log.read_text().splitlines()[-1])
    argv = call['argv']
    assert call['cwd'] == str(workspace)
    assert '--print=Analiza' in argv and '--model=gemini-4-hydrogen-medium' in argv
    assert '--mode=plan' in argv and '--sandbox' in argv and '--output-format=json' in argv
    settings = json.loads(next(a for a in argv if a.startswith('--settings='))[len('--settings='):])
    # Every gated tool auto-denies headless; the operator's global allow-rules do not apply.
    assert settings == {'toolPermission': 'request-review', 'permissions': {'allow': []}}
    assert not any('dangerously' in a for a in argv)


def test_gemini_long_prompt_is_passed_as_file_in_scoped_workspace(tmp_path, workspace, monkeypatch):
    log = fake_agy(tmp_path, {'status': 'SUCCESS', 'response': 'listo'})
    monkeypatch.setenv('GEMINI_BIN', str(tmp_path / 'agy'))
    prompt = 'x' * 120_000
    ai.gemini_run(prompt, workspace)
    argv = json.loads(log.read_text().splitlines()[-1])['argv']
    assert ai.GEMINI_PROMPT_FILE in next(a for a in argv if a.startswith('--print='))
    assert (workspace / ai.GEMINI_PROMPT_FILE).read_text() == prompt


def test_gemini_quota_is_reported_as_quota(tmp_path, workspace, monkeypatch):
    fake_agy(tmp_path, None, exit_code=1,
             stderr='error: Individual quota reached.\nAGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429)"}')
    monkeypatch.setenv('GEMINI_BIN', str(tmp_path / 'agy'))
    with pytest.raises(ai.AIError) as exc:
        ai.gemini_run('hola', workspace)
    assert ai.is_quota_error(exc.value)


def test_gemini_denied_actions_without_answer_fail_explicitly(tmp_path, workspace, monkeypatch):
    fake_agy(tmp_path, {'status': 'SUCCESS', 'response': '', 'denied_actions': [{'action': 'command'}]})
    monkeypatch.setenv('GEMINI_BIN', str(tmp_path / 'agy'))
    with pytest.raises(ai.AIError, match='command'):
        ai.gemini_run('hola', workspace)


def test_gemini_listed_and_recorded_as_provider(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_BIN', str(tmp_path / 'missing-agy'))
    status = {p['id']: p for p in ai.provider_status()}
    assert 'gemini' in status and status['gemini']['ready'] is False
    assert 'gemini' in ai.LOCAL_AGENTS and 'gemini' in ai.AI_PROVIDERS
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    assert ai.model_for('gemini') == 'gemini-4-hydrogen-medium'


def test_gemini_project_requires_consent_and_uses_scoped_copy(tmp_path, monkeypatch):
    """End to end through the pipeline: only selected files reach the agent's cwd."""
    from fastapi.testclient import TestClient
    import io, time, zipfile
    from app.api import app
    from app import db, pipeline
    monkeypatch.setenv('LEGACYLIFT_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('LEGACYLIFT_BOOTSTRAP_SECRET', 'bootstrap-test-secret-with-32-plus-characters')
    seen = []

    def fake_generate(provider, prompt, source, edit=False):
        seen.append((provider, sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())))
        return json.dumps({'summary': 'Pedidos', 'business_rules': [], 'questions': []})
    monkeypatch.setattr(ai, 'generate', fake_generate)
    with TestClient(app) as c:
        token = c.post('/api/auth/register', json={'email': 'g@test.test', 'display_name': 'Gemini Tester', 'password': 'long-password-123456',
                                                  'bootstrap_secret': 'bootstrap-test-secret-with-32-plus-characters'}).json()['access_token']
        h = {'Authorization': 'Bearer ' + token}
        created = c.post('/api/projects', headers=h, json={'name': 'Gemini', 'system_name': 'Python', 'provider': 'gemini'})
        assert created.status_code == 200, created.text
        pid = created.json()['id']
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('orders.py', 'def total(x):\n    return x\n')
            z.writestr('secret.py', 'TOKEN = 1\n')
        jid = c.post(f'/api/projects/{pid}/upload', headers=h, files={'file': ('t.zip', b.getvalue())}).json()['id']
        for _ in range(200):
            if db.job_get(pid, jid)['status'] not in ('queued', 'running'):
                break
            time.sleep(.02)
        assert c.post(f'/api/projects/{pid}/phases/understand', headers=h).status_code == 409  # no consent yet
        c.put(f'/api/projects/{pid}/analysis/selection', headers=h, json={'files': ['orders.py']})
        c.post(f'/api/projects/{pid}/analysis/consent', headers=h, json={'approved': True})
        pipeline.perform(pid, 'understand')
    assert seen == [('gemini', ['orders.py'])]
