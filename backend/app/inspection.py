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
    def bullet(items, empty):
        return ''.join(items) or f'- {empty}\n'
    complexity = data.get('complexity') or {}
    return ('# Discover — Inventario técnico\n\n'
            f"Archivos: **{data['files']}** · líneas de código leídas: **{data.get('total_lines', 0)}**.\n\n"
            '## Lenguajes\n' + bullet([f'- {k}: {v} archivos\n' for k,v in data['languages'].items()], 'Sin lenguajes reconocidos') +
            '\n## Frameworks y librerías de prueba detectados\n' +
            bullet([f"- {x['name']} ({x['references']} referencias)\n" for x in data.get('frameworks', [])], 'Ninguno reconocido por imports o manifiestos') +
            '\n## Dependencias externas\n' +
            bullet([f"- `{x['name']}`" + (' (declarada en manifiesto)' if x.get('declared_in_manifest') else '') + f" — {x['references']} referencias\n" for x in data.get('external_dependencies', [])[:40]],
                   'Solo biblioteca estándar o dependencias locales') +
            '\n## Puntos de entrada\n' + bullet([f'- `{v}`\n' for v in data.get('entrypoints', [])], 'No identificados') +
            '\n## Módulos\n' + bullet([f'- `{v}`\n' for v in data['modules']], 'Ninguno') +
            '\n## Manifiestos\n' + bullet([f'- `{v}`\n' for v in data['manifests']], 'Ninguno') +
            '\n## Complejidad\n' + f"- Máxima observada: {complexity.get('max', 'N/D')}; archivos sobre el umbral 10: {complexity.get('files_over_threshold', 0)}\n"
            f"- Método: {complexity.get('method', 'N/D')}\n" +
            '\n## Hallazgos heurísticos (no son vulnerabilidades confirmadas)\n' +
            bullet([f"- **{x['severity']}** `{x['file']}` línea {x.get('line') or 'N/D'}: {x['reason']}\n" for x in data['findings']], 'Ninguno observado.'))


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


def _python_lint(tree: ast.AST) -> list[dict]:
    """Small deterministic lint (no third-party tools, no execution)."""
    issues: list[dict] = []
    imported: dict[str, int] = {}
    used: set[str] = set()
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split('.')[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module != '__future__':
            for alias in node.names:
                if alias.name != '*':
                    imported[alias.asname or alias.name] = node.lineno
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                exported.update(e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({'rule': 'BARE_EXCEPT', 'line': node.lineno, 'message': '`except:` captura cualquier error, incluso KeyboardInterrupt'})
        elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops) \
                and any(isinstance(c, ast.Constant) and c.value is None for c in node.comparators):
            issues.append({'rule': 'NONE_COMPARISON', 'line': node.lineno, 'message': 'Comparación con None usando == o !=; usa `is`'})
    for name, line in imported.items():
        if name not in used and name not in exported:
            issues.append({'rule': 'UNUSED_IMPORT', 'line': line, 'message': f'Import `{name}` sin usar'})
    for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        seen: dict[str, int] = {}
        for stmt in getattr(scope, 'body', []):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if stmt.name in seen and not stmt.decorator_list:
                    issues.append({'rule': 'REDEFINED', 'line': stmt.lineno, 'message': f'`{stmt.name}` redefine la definición de la línea {seen[stmt.name]}'})
                seen[stmt.name] = stmt.lineno
    return issues


def lint_checks(folder: Path) -> dict:
    """Non-blocking lint warnings for Python sources. Other languages report 'not supported'."""
    warnings: list[dict] = []
    files = 0
    unsupported = 0
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        suffix = file.suffix.lower()
        if suffix != '.py':
            if suffix in EXTENSIONS or suffix in {'.cbl', '.cob', '.cobol'}:
                unsupported += 1
            continue
        rel = file.relative_to(folder).as_posix()
        try:
            tree = ast.parse(file.read_text(encoding='utf-8-sig'), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue  # Syntax failures are reported by static_checks.
        files += 1
        warnings.extend({'file': rel, **issue} for issue in _python_lint(tree))
    return {'files_linted': files, 'files_not_supported': unsupported, 'warnings': len(warnings),
            'details': warnings[:200],
            'method': 'Lint AST propio: imports sin usar, except genérico, redefiniciones y comparación con None. Solo Python.'}
