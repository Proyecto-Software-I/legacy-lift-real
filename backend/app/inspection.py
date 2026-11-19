from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

EXTENSIONS = {'.py': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript', '.js': 'JavaScript',
              '.jsx': 'JavaScript', '.java': 'Java', '.cs': 'C#', '.go': 'Go',
              '.php': 'PHP', '.sql': 'SQL', '.html': 'HTML', '.css': 'CSS'}
MANIFESTS = ('pyproject.toml', 'requirements.txt', 'package.json', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'go.mod', '*.csproj')


def inspect(folder: Path, progress=None) -> dict:
    from .analysis.scanner import scan
    return scan(folder, progress)


def inventory_md(data: dict) -> str:
    return ('# Discover — Inventario técnico\n\n'
            f"Archivos: **{data['files']}**.\n\n"
            '## Tecnologías\n' + ''.join(f'- {k}: {v} archivos\n' for k,v in data['languages'].items()) +
            '\n## Módulos\n' + ''.join(f'- `{v}`\n' for v in data['modules']) +
            '\n## Manifiestos\n' + ''.join(f'- `{v}`\n' for v in data['manifests']) +
            '\n## Hallazgos heurísticos (no son vulnerabilidades confirmadas)\n' +
            (''.join(f"- **{x['severity']}** `{x['file']}` línea {x.get('line') or 'N/D'}: {x['reason']}\n" for x in data['findings']) or '- Ninguno observado.\n'))


def static_checks(folder: Path) -> list[dict]:
    checks = []
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        rel = file.relative_to(folder).as_posix()
        try:
            if file.suffix.lower() == '.py':
                ast.parse(file.read_text(encoding='utf-8-sig'), filename=rel)
            elif file.suffix.lower() == '.json':
                json.loads(file.read_text(encoding='utf-8-sig'))
            elif file.suffix.lower() in {'.cbl','.cob','.cobol'}:
                text = file.read_text(encoding='utf-8-sig')
                if not re.search(r'\bPROGRAM-ID\.', text, re.I) or not re.search(r'\bPROCEDURE\s+DIVISION\b', text, re.I):
                    raise SyntaxError('Falta PROGRAM-ID o PROCEDURE DIVISION en programa COBOL')
            else:
                continue
            checks.append({'file':rel, 'status':'passed', 'check':'syntax'})
        except (SyntaxError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            checks.append({'file': rel, 'status':'failed', 'check':'syntax', 'error':str(exc)[:240]})
    return checks


def check_summary(checks: list[dict]) -> dict:
    return {'total':len(checks), 'passed':sum(x['status']=='passed' for x in checks),
            'failed':sum(x['status']=='failed' for x in checks)}
