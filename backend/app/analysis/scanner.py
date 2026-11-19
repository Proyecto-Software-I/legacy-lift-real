"""Static-first file analysis with explicit evidence and bounded reads.

The registry is deliberately simple: extensions, regex declarations and imports. Future
Tree-sitter/COBOL parsers can replace a strategy without changing the response contract.
"""
from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Language:
    name: str
    extensions: tuple[str, ...]
    declarations: tuple[tuple[str, str], ...] = ()
    dependencies: tuple[str, ...] = ()


LANGUAGES = (
    Language('Python', ('.py',), ((r'^\s*(?:async\s+)?def\s+(\w+)', 'function'), (r'^\s*class\s+(\w+)', 'class'))),
    Language('JavaScript', ('.js', '.jsx', '.mjs', '.cjs'), ((r'\b(?:class|function)\s+(\w+)', 'declaration'),),
             (r'(?:import\s+.*?from\s*|require\s*\()\s*[\'\"]([^\'\"]+)',)),
    Language('TypeScript', ('.ts', '.tsx', '.mts', '.cts'), ((r'\b(?:class|interface|type|function)\s+(\w+)', 'declaration'),),
             (r'(?:import\s+.*?from\s*|require\s*\()\s*[\'\"]([^\'\"]+)',)),
    Language('PHP', ('.php', '.phtml'), ((r'\b(?:class|interface|trait|function)\s+(\w+)', 'declaration'),),
             (r'\b(?:require|include)(?:_once)?\s*\(?\s*[\'\"]([^\'\"]+)', r'\buse\s+([\\\w]+)')),
    Language('Java', ('.java',), ((r'\b(?:class|interface|enum|record)\s+(\w+)', 'declaration'),), (r'^\s*import\s+([\w.*]+)\s*;',)),
    Language('COBOL', ('.cbl', '.cob', '.cobol', '.cpy', '.pco'),
             ((r'\bPROGRAM-ID\.\s*([\w-]+)', 'program'), (r'^\s*(?:\d{6}\s*)?([\w-]+)\s+SECTION\s*\.', 'section')),
             (r'\bCOPY\s+[\'\"]?([\w.-]+)', r'\bCALL\s+[\'\"]([\w-]+)')),
    Language('C#', ('.cs',)), Language('Go', ('.go',)), Language('SQL', ('.sql',)),
    Language('HTML', ('.html', '.htm')), Language('CSS', ('.css', '.scss')),
)
REGISTRY = {suffix: language for language in LANGUAGES for suffix in language.extensions}
MANIFESTS = {'package.json', 'composer.json', 'pom.xml', 'build.gradle', 'build.gradle.kts',
             'requirements.txt', 'pyproject.toml', 'go.mod', 'Gemfile', 'Cargo.toml'}
ENTRYPOINTS = {'main.py', 'main.ts', 'main.js', 'app.py', 'index.php', 'index.js', 'index.ts',
               'application.java', 'program.cs', 'package.json', 'pom.xml'}


FRAMEWORKS = {
    'flask':'Flask', 'django':'Django', 'fastapi':'FastAPI', 'sqlalchemy':'SQLAlchemy', 'pytest':'pytest',
    'unittest':'unittest', 'express':'Express', 'react':'React', '@angular/core':'Angular', 'vue':'Vue',
    'next':'Next.js', 'jest':'Jest', 'org.springframework':'Spring', 'javax.servlet':'Java Servlet',
    'junit':'JUnit', 'illuminate':'Laravel', 'symfony':'Symfony', 'phpunit':'PHPUnit',
}
STDLIB = set(getattr(__import__('sys'), 'stdlib_module_names', ()))
_DECISIONS = {
    'COBOL': re.compile(r'\b(?:IF|EVALUATE|WHEN|UNTIL|VARYING)\b', re.I),
    'default': re.compile(r'\b(?:if|for|while|case|catch|elif|foreach)\b|&&|\|\|'),
}


def _python_complexity(text: str, filename: str) -> list[dict]:
    """Approximate cyclomatic complexity per function: 1 + decision points (AST, no execution)."""
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        score = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += len(child.values) - 1
            elif isinstance(child, ast.comprehension):
                score += 1 + len(child.ifs)
        length = (getattr(node, 'end_lineno', node.lineno) or node.lineno) - node.lineno + 1
        out.append({'name': node.name, 'line': node.lineno, 'complexity': score, 'length': length})
    return out


def _framework_hits(target: str) -> str | None:
    lowered = target.lower().lstrip('.')
    for key, name in FRAMEWORKS.items():
        if lowered == key or lowered.startswith(key + '.') or lowered.startswith(key + '/'):
            return name
    return None


def _manifest_dependencies(file: Path, rel: str) -> list[str]:
    try:
        text = file.read_text(encoding='utf-8-sig', errors='replace')[:200_000]
    except OSError:
        return []
    names: list[str] = []
    if file.name == 'requirements.txt':
        for line in text.splitlines():
            line = line.split('#')[0].strip()
            if line and not line.startswith('-'):
                names.append(re.split(r'[<>=!~\[; ]', line, maxsplit=1)[0])
    elif file.name in ('package.json', 'composer.json'):
        import json as _json
        try:
            doc = _json.loads(text)
            for key in ('dependencies', 'devDependencies', 'require', 'require-dev'):
                if isinstance(doc.get(key), dict):
                    names.extend(doc[key].keys())
        except ValueError:
            pass
    elif file.name == 'go.mod':
        names.extend(m.group(1) for m in re.finditer(r'^\s*(?:require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+v', text, re.M))
    elif file.name == 'pom.xml':
        names.extend(m.group(1) for m in re.finditer(r'<artifactId>([\w.\-]+)</artifactId>', text))
    return [n for n in names if n][:200]


def _node_id(module: str) -> str:
    return 'module:' + module


def _module(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if len(parts) > 1 else '(raíz)'


def _file_symbols(text: str, language: Language, filename: str) -> tuple[list[dict], list[dict]]:
    declarations, dependencies = [], []
    if language.name == 'Python':
        try:
            tree = ast.parse(text, filename=filename)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    declarations.append({'name':node.name, 'kind':'class' if isinstance(node, ast.ClassDef) else 'function', 'line':node.lineno})
                elif isinstance(node, ast.Import):
                    dependencies.extend({'target':alias.name, 'line':node.lineno} for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    dependencies.append({'target':'.'*node.level+(node.module or ''), 'line':node.lineno})
        except SyntaxError:
            pass  # The syntax validator reports syntax failures separately.
    else:
        for line_no, line in enumerate(text.splitlines(), 1):
            # COBOL fixed format: columns 1-6 sequence, 7 indicator; skip comment lines.
            if language.name == 'COBOL' and len(line) > 6 and line[6] in {'*', '/'}:
                continue
            for regex, kind in language.declarations:
                for match in re.finditer(regex, line, re.I):
                    declarations.append({'name':match.group(1), 'kind':kind, 'line':line_no})
            for regex in language.dependencies:
                for match in re.finditer(regex, line, re.I):
                    dependencies.append({'target':match.group(1).rstrip('.'), 'line':line_no})
    return declarations[:80], dependencies[:120]


def scan(folder: Path, progress: Callable[[dict], None] | None = None) -> dict:
    files = sorted((f for f in folder.rglob('*') if f.is_file() and not f.is_symlink()),
                   key=lambda f: f.relative_to(folder).as_posix())
    total = len(files)
    code_bytes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    modules: Counter[str] = Counter()
    file_index: list[dict] = []
    findings: list[dict] = []
    symbols: list[dict] = []
    raw_deps: list[dict] = []
    manifests: list[str] = []
    test_files: list[str] = []
    entrypoints: list[str] = []
    manifest_deps: list[dict] = []
    total_lines = 0
    for idx, file in enumerate(files, 1):
        rel = file.relative_to(folder).as_posix()
        size = file.stat().st_size
        module = _module(rel)
        modules[module] += 1
        language = REGISTRY.get(file.suffix.lower())
        lang = language.name if language else 'Otro'
        counts[lang] += 1
        if language:
            code_bytes[lang] += size
        if file.name in MANIFESTS or file.suffix.lower() == '.csproj':
            manifests.append(rel)
            manifest_deps.extend({'name': n, 'manifest': rel} for n in _manifest_dependencies(file, rel))
        if file.name.lower() in ENTRYPOINTS or file.name.lower().endswith('application.java'):
            entrypoints.append(rel)
        if 'test' in file.name.lower() or 'spec' in file.name.lower():
            test_files.append(rel)
        item = {'path':rel, 'language':lang, 'module':module, 'bytes':size,
                'lines':0, 'selected':bool(language)}
        if language and size <= 5_000_000:
            content = file.read_text(encoding='utf-8-sig', errors='replace')
            lines = content.splitlines()
            item['lines'] = len(lines)
            total_lines += len(lines)
            decl, deps = _file_symbols(content[:1_000_000], language, rel)
            if lang == 'Python':
                functions = _python_complexity(content[:1_000_000], rel)
                item['complexity'] = max((f['complexity'] for f in functions), default=1)
                for fn in functions:
                    if fn['complexity'] > 10:
                        rule = 'COMPLEX_FUNCTION'
                        findings.append({'id':hashlib.sha256(f"{rel}:{fn['line']}:{rule}".encode()).hexdigest()[:20],
                            'file':rel, 'line':fn['line'], 'rule':rule, 'kind':'heuristic',
                            'severity':'high' if fn['complexity'] > 20 else 'medium',
                            'reason':f"Función `{fn['name']}` con complejidad ciclomática aproximada {fn['complexity']} (umbral 10)",
                            'evidence':{'file':rel,'line':fn['line'],'excerpt':f"def {fn['name']}(...)  # {fn['length']} líneas"}})
                    elif fn['length'] > 80:
                        rule = 'LONG_FUNCTION'
                        findings.append({'id':hashlib.sha256(f"{rel}:{fn['line']}:{rule}".encode()).hexdigest()[:20],
                            'file':rel, 'line':fn['line'], 'rule':rule, 'kind':'heuristic', 'severity':'low',
                            'reason':f"Función `{fn['name']}` de {fn['length']} líneas; dificulta la mantenibilidad",
                            'evidence':{'file':rel,'line':fn['line'],'excerpt':f"def {fn['name']}(...)"}})
            else:
                pattern = _DECISIONS['COBOL' if lang == 'COBOL' else 'default']
                item['complexity'] = 1 + len(pattern.findall(content[:1_000_000]))
                if item['complexity'] > 60:
                    rule = 'COMPLEX_FILE'
                    findings.append({'id':hashlib.sha256(f'{rel}:0:{rule}'.encode()).hexdigest()[:20],
                        'file':rel, 'line':None, 'rule':rule, 'kind':'heuristic', 'severity':'medium',
                        'reason':f"Archivo con {item['complexity']} puntos de decisión aproximados; conviene dividirlo",
                        'evidence':{'file':rel,'line':None,'excerpt':f"{item['complexity']} decisiones (IF/WHEN/loops)"}})
            symbols.extend({**d, 'file':rel, 'language':lang} for d in decl[:40])
            raw_deps.extend({'file':rel, **d} for d in deps[:75])
            for line_no, line in enumerate(lines[:15000], 1):
                if re.search(r'\b(?:TODO|FIXME)\b', line, re.I):
                    rule='TODO_MARKER'
                    findings.append({'id':hashlib.sha256(f'{rel}:{line_no}:{rule}'.encode()).hexdigest()[:20],
                                     'file':rel, 'line':line_no, 'rule':rule, 'kind':'heuristic',
                                     'severity':'low', 'reason':'Marcador TODO/FIXME; requiere revisión',
                                     'evidence':{'file':rel,'line':line_no,'excerpt':line.strip()[:180]}})
                    if len(findings)>1500: break
        if size > 100_000 and lang != 'Otro':
            rule='LARGE_FILE'
            findings.append({'id':hashlib.sha256(f'{rel}:0:{rule}'.encode()).hexdigest()[:20],
                             'file':rel, 'line':None, 'rule':rule, 'kind':'heuristic', 'severity':'medium',
                             'reason':'Archivo de código grande; revisar responsabilidades',
                             'evidence':{'file':rel,'line':None,'excerpt':f'{size} bytes'}})
        file_index.append(item)
        if progress and (idx == 1 or idx == total or idx % max(1, min(25, total // 20)) == 0):
            progress({'scanned':idx, 'total':total,'percent':round(idx/max(total,1)*100),
                      'language_bytes':dict(code_bytes), 'languages':dict(counts)})
    # Only connect modules when a dependency target unambiguously names a local module.
    module_keys=set(modules)
    edges=set()
    deps_evidence=[]
    for dep in raw_deps:
        name=dep['target']
        target=name.removeprefix('./').removeprefix('../').split('/')[0].split('.')[0]
        origin=_module(dep['file'])
        local=target if target in module_keys else None
        if local and local != origin:
            edges.add((origin,local))
        deps_evidence.append({**dep,'module':origin, 'target_module':local,'resolved':bool(local)})
    frameworks: Counter[str] = Counter()
    external: Counter[str] = Counter()
    for dep in deps_evidence:
        name = dep['target']
        if hit := _framework_hits(name):
            frameworks[hit] += 1
        if dep['resolved'] or name.startswith('.') or name.startswith('/'):
            continue
        # npm scoped packages keep their scope (@scope/name); others use the top-level name.
        top = '/'.join(name.split('/')[:2]) if name.startswith('@') else name.split('/')[0].split('.')[0]
        if not top or top in STDLIB or top in module_keys:
            continue
        external[top] += 1
    for dep in manifest_deps:
        if hit := _framework_hits(dep['name']):
            frameworks[hit] += 1
        external[dep['name']] += 0
    # Entry points beyond conventional file names: Python __main__ guards and COBOL
    # programs that no other program CALLs.
    called = {d['target'].upper() for d in deps_evidence if d['file'].lower().endswith(('.cbl','.cob','.cobol'))}
    for sym in symbols:
        if sym.get('kind') == 'program' and sym['name'].upper() not in called and sym['file'] not in entrypoints:
            entrypoints.append(sym['file'])
    for item in file_index:
        if item['language'] == 'Python' and item['path'] not in entrypoints and item['path'] not in test_files:
            try:
                head = (folder / item['path']).read_text(encoding='utf-8-sig', errors='replace')[:400_000]
            except OSError:
                continue
            if re.search(r'^if\s+__name__\s*==\s*[\'"]__main__[\'"]', head, re.M):
                entrypoints.append(item['path'])
    local_stems = {Path(f['path']).stem for f in file_index}
    external_dependencies = [{'name': n, 'references': c, 'declared_in_manifest': any(d['name'] == n for d in manifest_deps)}
                             for n, c in external.most_common(80) if n not in local_stems]
    total_code_bytes=sum(code_bytes.values())
    lang_breakdown=[{'name':lang,'bytes':size,'files':counts[lang],
                     'percent':round(size*100/total_code_bytes, 2) if total_code_bytes else 0}
                    for lang,size in code_bytes.most_common()]
    graph={'nodes':[{'id':_node_id(m),'label':m,'files':cnt,'kind':'module'} for m,cnt in modules.most_common(120)],
           'edges':[{'from':_node_id(a),'to':_node_id(b),'type':'DEPENDS_ON','inferred':False}
                    for a,b in sorted(edges)][:350],
           'note':'Dependencias locales inferidas por referencias estáticas; no refleja todas las llamadas dinámicas'}
    return {'schema_version':2,'files':total,'languages':dict(counts.most_common()),
            'language_bytes':dict(code_bytes),'language_breakdown':lang_breakdown,
            'total_code_bytes':total_code_bytes,'total_lines':total_lines,
            'modules':list(modules),'manifests':manifests,'findings':findings[:1500],
            'graph':graph,'test_files':test_files,'entrypoints':entrypoints,
            'frameworks':[{'name':n,'references':c} for n,c in frameworks.most_common()],
            'external_dependencies':external_dependencies,
            'complexity':{'max':max((f.get('complexity',1) for f in file_index),default=0),
                          'files_over_threshold':sum(1 for f in file_index if f.get('complexity',1)>10),
                          'method':'Python: 1 + decisiones por función (AST). Otros: puntos de decisión por archivo (regex).'},
            'symbols':symbols[:4000], 'dependencies':deps_evidence[:5000],
            'file_index':file_index,
            'methodology':'Porcentaje por bytes de extensiones reconocidas; no es GitHub Linguist ni ejecución de código.'}
