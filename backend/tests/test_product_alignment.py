"""Acceptance checks derived from docs/product.md (scope MVP académico)."""
import io
import json
import time
import zipfile
from pathlib import Path

from app import ai, db, inspection, pipeline, planning
from app.analysis.scanner import scan
from test_flow import client, headers, sample_zip, await_ingest
from test_s3_blueprints import project_ready


# §2.1 Discover — frameworks, dependencies, entry points; Assess signals — complexity.
def test_discover_reports_frameworks_dependencies_entrypoints_and_complexity(tmp_path):
    (tmp_path / 'web').mkdir()
    (tmp_path / 'requirements.txt').write_text('flask==2.0\nrequests>=2\n')
    (tmp_path / 'web' / 'views.py').write_text('X = 1\n')
    branches = '\n'.join(f'    if x == {i}:\n        return {i}' for i in range(12))
    (tmp_path / 'run.py').write_text('import flask\nimport requests\nimport os\nfrom web import views\n\n'
                                     f'def route(x):\n{branches}\n    return -1\n\n'
                                     'if __name__ == "__main__":\n    route(1)\n')
    info = scan(tmp_path)
    assert {'name': 'Flask', 'references': 2} in info['frameworks']
    names = {d['name']: d for d in info['external_dependencies']}
    assert names['flask']['declared_in_manifest'] and 'os' not in names and 'web' not in names
    assert 'run.py' in info['entrypoints']
    assert any(f['rule'] == 'COMPLEX_FUNCTION' and f['line'] == 6 for f in info['findings'])
    md = inspection.inventory_md(info)
    assert '## Puntos de entrada' in md and 'Flask' in md and '## Complejidad' in md


def test_lint_flags_new_issues_without_executing_code(tmp_path):
    (tmp_path / 'a.py').write_text('import os\n\ndef f(x):\n    try:\n        return x == None\n    except:\n        return 0\n')
    lint = inspection.lint_checks(tmp_path)
    assert {d['rule'] for d in lint['details']} == {'UNUSED_IMPORT', 'NONE_COMPARISON', 'BARE_EXCEPT'}


# §2.2 Understand — minimal knowledge graph and module map.
def test_understand_builds_knowledge_graph_and_module_map(client):
    pid = project_ready(client)
    data = db.analysis_get(pid, db.project_get(pid)['active_snapshot_id'], 'understand')
    kg = data['knowledge_graph']
    assert any(n['type'] == 'module' for n in kg['nodes']) and any(e['type'] == 'CONTAINS' for e in kg['edges'])
    md = pipeline.artifact(pid, 'understand.md')
    assert '## Mapa de módulos' in md and '### Puntos de entrada' in md and '## Grafo de conocimiento' in md


# §2.2 / §5 — the demo provider and quota errors never produce invented AI content.
def test_quota_exhaustion_does_not_fabricate_rules_or_recommendations(client, monkeypatch):
    p = client.post('/api/projects', headers=headers(), json={'name': 'Cuota', 'system_name': 'Python', 'provider': 'claude'}).json()
    pid = p['id']
    up = client.post(f'/api/projects/{pid}/upload', headers=headers(), files={'file': ('c.zip', sample_zip(), 'application/zip')})
    assert await_ingest(pid, up.json()['id'])['status'] == 'completed'
    client.post(f'/api/projects/{pid}/analysis/consent', headers=headers(), json={'approved': True})

    def exhausted(*_args, **_kwargs):
        raise ai.AIError('You have hit your usage limit')
    monkeypatch.setattr(ai, 'generate', exhausted)
    for phase in ('discover', 'understand', 'assess'):
        result, details = pipeline.perform(pid, phase)
        rid = db.run_create(pid, phase, 'claude'); db.run_complete(rid, result, details)
    sid = db.project_get(pid)['active_snapshot_id']
    understood = db.analysis_get(pid, sid, 'understand')
    assessed = db.analysis_get(pid, sid, 'assess')
    assert understood['business_rules'] == [] and 'cuota' in understood['engine']
    assert assessed['ai_suggestions'] == [] and 'cuota' in assessed['engine']
    summary, details = pipeline.perform(pid, 'plan')
    assert 'cuota' in details['engine']
    plan = planning.current(pid)
    assert plan['data']['origin'] == 'static' and 'cuota' in plan['data']['notes'].lower()


# §2.4 Plan — blueprint links current/target architecture, files, findings; human edits are 'manual'.
def test_blueprint_links_findings_and_current_architecture(client):
    pid = project_ready(client)
    plan = planning.current(pid)
    assert plan['data']['current_architecture'] and plan['data']['target_architecture']
    assert plan['data']['origin'] == 'static'
    wave = plan['data']['waves'][0]
    # The demo assessment recommends a strategy for the wave's module; the link must be explicit.
    assert any(f['source'].startswith('assess') and f['rule'].startswith('STRATEGY:') for f in wave['related_findings'])
    edited = dict(plan['data']); edited['origin'] = 'ai'  # A client cannot claim AI authorship.
    response = client.patch(f'/api/projects/{pid}/blueprints/current', headers=headers(), json={'data': edited})
    assert response.status_code == 200, response.text
    assert response.json()['data']['origin'] == 'manual'
    assert '## Arquitectura actual' in pipeline.artifact(pid, 'blueprint.md')


# §9 — every artifact records provider, model, template and snapshot.
def test_runs_and_artifacts_record_provenance(client):
    p = client.post('/api/projects', headers=headers(), json={'name': 'Trazable', 'system_name': 'Python', 'provider': 'demo'}).json()
    pid = p['id']
    up = client.post(f'/api/projects/{pid}/upload', headers=headers(), files={'file': ('c.zip', sample_zip(), 'application/zip')})
    assert await_ingest(pid, up.json()['id'])['status'] == 'completed'
    for phase in ('discover', 'understand'):
        if (db.last_run(pid, phase) or {}).get('status') == 'completed':
            continue
        response = client.post(f'/api/projects/{pid}/phases/{phase}', headers=headers())
        assert response.status_code == 200, response.text
        for _ in range(300):
            run = db.last_run(pid, phase)
            if run['status'] != 'running':
                break
            time.sleep(.02)
    run = db.last_run(pid, 'understand')
    assert run['status'] == 'completed', run
    assert run['model'] == ai.DETERMINISTIC_ENGINE
    prov = run['data']['provenance']
    assert prov['snapshot_id'] == db.project_get(pid)['active_snapshot_id']
    # The demo provider sends no prompt anywhere, so no template is claimed.
    assert prov['prompt_template'] == 'sin prompt (determinista)'
    real = pipeline.provenance(pid, 'understand', 'codex', 'abc123')
    assert real['prompt_template'].startswith('understand.md v') and real['model'] == ai.model_for('codex')
    md = pipeline.artifact(pid, 'understand.md')
    assert '## Procedencia' in md and 'demo' in md
    discover = db.last_run(pid, 'discover')  # created by the automatic scan after import
    assert discover['model'] == ai.DETERMINISTIC_ENGINE and discover['data']['provenance']['phase'] == 'discover'


def test_codex_model_is_configurable_and_recorded(monkeypatch):
    monkeypatch.setenv('CODEX_MODEL', 'gpt-6.1-terra')
    assert ai.model_for('codex') == 'gpt-6.1-terra'
    assert pipeline.model_for_phase('discover', 'codex') == ai.DETERMINISTIC_ENGINE
    assert pipeline.model_for_phase('plan', 'codex') == 'gpt-6.1-terra'


# §2.4 Baseline — missing verifications are recorded explicitly.
def test_baseline_records_verification_gaps():
    from app import baseline
    gaps = baseline.gaps([], {'status': 'not_run'}, False, [])
    assert any('No se detectó ninguna suite' in g for g in gaps)
    assert any('no fue autorizada' in g for g in gaps)


# §2.6 — the approved delivery contains the modernized project, its evidence and the documentation.
def test_approved_export_is_complete_delivery(client):
    import io, zipfile
    from test_s4_review import prepared
    from app import migration_verify
    pid, aid = prepared(client)
    assert client.post(f'/api/projects/{pid}/phases/verify', headers=headers()).status_code == 200
    migration_verify.run_one()
    decision = client.post(f'/api/projects/{pid}/migration/decision', headers=headers(),
                           json={'approved': True, 'reason': 'Aceptamos el riesgo: no hay pruebas comparables ejecutadas.'})
    assert decision.status_code == 200, decision.text
    response = client.get(f'/api/projects/{pid}/export', headers=headers())
    assert response.status_code == 200
    assert 'legacylift-entrega-completa.zip' in response.headers['content-disposition']
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith('codigo-modernizado/') and n.endswith('calculator.py') for n in names)
    assert {'evidencias/changes.patch', 'evidencias/report.md', 'evidencias/verify.json'} <= set(names)
    assert {'documentacion/inventory.md', 'documentacion/blueprint.md', 'documentacion/verification.md'} <= set(names)
