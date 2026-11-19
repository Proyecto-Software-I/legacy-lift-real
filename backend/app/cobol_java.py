"""Migration Pack cobol-to-java@1.0: AI translation of COBOL programs to Java 17.

Safety and evidence model:
- The model only proposes file contents (JSON). LegacyLift validates every path: new
  `.java` files under JAVA_ROOT, package `legacylift.migrated`, original COBOL untouched.
- The candidate must compile with `javac -proc:none` (no annotation processors, no code
  execution) or the attempt fails with the compiler output.
- Behaviour is compared by a *dual run*: the original COBOL (GnuCOBOL) and the Java
  translation are executed in an operator-controlled Docker image without network and
  with a read-only source mount; identical output is the evidence Verify reports.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

PACK_ID = 'cobol-to-java@1.0'
JAVA_PACKAGE = 'legacylift.migrated'
JAVA_ROOT = 'java/src/main/java/legacylift/migrated'
COBOL_SUFFIXES = {'.cbl', '.cob', '.cobol'}
COPYBOOK_SUFFIXES = {'.cpy'}
RUNNER_IMAGE_PATTERN = r'legacylift/legacy-runner:[\w.-]+(?:@sha256:[0-9a-f]{64})?'
SAFE_PATH = re.compile(r'^[A-Za-z0-9_./-]+$')
PROMPT = Path(__file__).resolve().parent / 'prompts' / 'cobol_to_java.md'


def class_name(program_id: str) -> str:
    """PAYROLL -> Payroll, CALC-INTEREST -> CalcInterest (deterministic, Java-safe)."""
    words = [w for w in re.split(r'[^A-Za-z0-9]+', program_id) if w]
    name = ''.join(w[:1].upper() + w[1:].lower() for w in words) or 'Program'
    return name if name[0].isalpha() else 'P' + name


def _strip_comments(text: str) -> str:
    # Fixed format: column 7 '*' or '/' marks a comment line.
    return '\n'.join(line for line in text.splitlines() if not (len(line) > 6 and line[6] in '*/'))


def programs(folder: Path, files: list[str]) -> list[dict]:
    """COBOL programs in the wave with their PROGRAM-ID, Java class and role (main/subprogram)."""
    found = []
    for rel in files:
        path = folder / rel
        if Path(rel).suffix.lower() not in COBOL_SUFFIXES or not path.is_file():
            continue
        text = _strip_comments(path.read_text(encoding='utf-8', errors='replace'))
        match = re.search(r'\bPROGRAM-ID\.\s*["\']?([\w-]+)', text, re.I)
        if not match:
            continue
        calls = {c.upper() for c in re.findall(r'\bCALL\s+["\']([\w-]+)["\']', text, re.I)}
        uses = bool(re.search(r'PROCEDURE\s+DIVISION\s+USING', text, re.I))
        found.append({'file': rel, 'program_id': match.group(1).upper(), 'class': class_name(match.group(1)),
                      'calls': sorted(calls), 'uses': uses})
    called = {c for p in found for c in p['calls']}
    for p in found:
        p['main'] = p['program_id'] not in called and not p['uses']
    return found


def copybooks(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*')
                  if p.is_file() and not p.is_symlink() and p.suffix.lower() in COPYBOOK_SUFFIXES)


def build_prompt(folder: Path, wave: dict) -> tuple[str, list[dict]]:
    progs = programs(folder, wave['files'])
    if not progs:
        raise ValueError('La wave no contiene programas COBOL con PROGRAM-ID; no hay nada que traducir.')
    template = PROMPT.read_text(encoding='utf-8')
    parts = [template, '', '## Wave aprobada', json.dumps({k: wave[k] for k in ('id', 'goal', 'files', 'acceptance_criteria')}, ensure_ascii=False),
             '', '## Clases Java obligatorias']
    for p in progs:
        role = 'programa principal (main)' if p['main'] else 'subprograma (llamado con CALL)'
        parts.append(f"- `{p['program_id']}` ({p['file']}, {role}) -> `{JAVA_ROOT}/{p['class']}.java`, clase `{p['class']}`")
    for rel in copybooks(folder)[:20]:
        parts += ['', f'## Copybook {rel}', '```cobol', (folder / rel).read_text(encoding='utf-8', errors='replace')[:12000], '```']
    for p in progs:
        parts += ['', f"## Programa {p['file']}", '```cobol', (folder / p['file']).read_text(encoding='utf-8', errors='replace')[:30000], '```']
    return '\n'.join(parts), progs


def apply(candidate: Path, response: str, progs: list[dict]) -> list[str]:
    """Validate the model's JSON and write only new Java files. Returns written paths."""
    from .analysis.understanding import _json_object
    doc = _json_object(response)
    files = doc.get('files') if isinstance(doc, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError('El modelo no devolvió el JSON {"files":[...]} con las clases Java.')
    if len(files) > 2 * len(progs) + 4:
        raise ValueError('El modelo propuso demasiados archivos para esta wave.')
    expected = {f"{JAVA_ROOT}/{p['class']}.java" for p in progs}
    written: dict[str, str] = {}
    for item in files:
        path, content = (item or {}).get('path'), (item or {}).get('content')
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError('Formato de archivo inválido en la respuesta del modelo.')
        rel = Path(path)
        if rel.is_absolute() or '..' in rel.parts or not SAFE_PATH.match(path) or rel.parent.as_posix() != JAVA_ROOT or rel.suffix != '.java':
            raise ValueError(f'Ruta no permitida: {path}. Solo se aceptan archivos nuevos en {JAVA_ROOT}/.')
        if (candidate / rel).exists():
            raise ValueError(f'El modelo intentó sobrescribir un archivo existente: {path}')
        if len(content.encode('utf-8')) > 200_000:
            raise ValueError(f'Archivo demasiado grande: {path}')
        if not re.search(rf'^\s*package\s+{re.escape(JAVA_PACKAGE)}\s*;', content, re.M):
            raise ValueError(f'{path} debe declarar `package {JAVA_PACKAGE};`')
        if re.search(r'\b(?:java\.net|java\.nio\.file|ProcessBuilder|Runtime\.getRuntime|java\.lang\.reflect)\b', content):
            raise ValueError(f'{path} usa APIs de red, archivos, procesos o reflexión, prohibidas en este pack.')
        written[path] = content
    missing = expected - set(written)
    if missing:
        raise ValueError('Faltan clases obligatorias: ' + ', '.join(sorted(missing)))
    for path, content in written.items():
        target = candidate / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    return sorted(written)


def java_files(folder: Path) -> list[Path]:
    root = folder / JAVA_ROOT
    return sorted(p for p in root.rglob('*.java') if p.is_file() and not p.is_symlink()) if root.is_dir() else []


def compile_java(folder: Path) -> dict:
    """Host compile: javac never runs project code with -proc:none and an empty classpath."""
    sources = java_files(folder)
    if not sources:
        return {'status': 'not_run', 'reason': 'No hay código Java en el candidato.'}
    javac = shutil.which(os.getenv('JAVAC_BIN', 'javac'))
    if not javac:
        return {'status': 'not_run', 'reason': 'javac no está instalado en el servidor.'}
    with tempfile.TemporaryDirectory(prefix='legacylift-javac-') as out:
        cmd = [javac, '-proc:none', '-implicit:none', '-encoding', 'UTF-8', '--release', '17', '-cp', '', '-d', out, *map(str, sources)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
        except subprocess.TimeoutExpired:
            return {'status': 'failed', 'output': 'javac excedió 90 segundos'}
    rel = [s.relative_to(folder).as_posix() for s in sources]
    return {'status': 'passed' if proc.returncode == 0 else 'failed', 'files': rel,
            'output': (proc.stdout + proc.stderr)[-6000:], 'compiler': 'javac --release 17 (host, -proc:none)'}


def _normalize(text: str) -> str:
    return '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').splitlines()).strip('\n')


def dual_run(folder: Path, progs: list[dict], run_cobol: bool = True, run_java: bool = True) -> dict:
    """Execute main programs in COBOL and/or Java inside the controlled runner image."""
    if os.getenv('LEGACYLIFT_ENV', 'development').lower() != 'development':
        return {'status': 'not_run', 'reason': 'Docker local prohibido fuera del entorno development'}
    if not shutil.which('docker'):
        return {'status': 'not_run', 'reason': 'Docker no está instalado'}
    image = os.getenv('LEGACYLIFT_LEGACY_RUNNER_IMAGE', 'legacylift/legacy-runner:1')
    if not re.fullmatch(RUNNER_IMAGE_PATTERN, image):
        return {'status': 'not_run', 'reason': 'Imagen de runner no autorizada por política local'}
    mains = [p for p in progs if p['main']]
    if not mains:
        return {'status': 'not_run', 'reason': 'la wave solo contiene subprogramas ('
                + ', '.join(p['program_id'] for p in progs) + '); incluye el programa principal que los llama para poder hacer dual run.'}
    for p in progs:
        if not SAFE_PATH.match(p['file']) or not re.fullmatch(r'[A-Z0-9-]+', p['program_id']) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', p['class']):
            return {'status': 'not_run', 'reason': 'Nombres de archivo o programa fuera de la política del runner'}
    include = ' '.join('-I ' + shlex.quote('/src/' + d) for d in sorted({Path(c).parent.as_posix() for c in copybooks(folder)}))
    subprograms = ' '.join(shlex.quote('/src/' + p['file']) for p in progs if not p['main'])
    has_java = bool(java_files(folder)) and run_java
    script = ['set -u', 'mkdir -p /tmp/ll/j']
    if run_cobol:
        for p in mains:
            pid = p['program_id']
            script += [f'echo "===LL:COBOL:{pid}:BEGIN==="',
                       f'if cobc -x -O0 -o /tmp/ll/{pid} {shlex.quote("/src/" + p["file"])} {subprograms} {include} 2>/tmp/ll/cobc-{pid}.err; then '
                       f'timeout 10 /tmp/ll/{pid}; echo "===LL:COBOL:{pid}:EXIT=$?==="; '
                       f'else cat /tmp/ll/cobc-{pid}.err; echo "===LL:COBOL:{pid}:EXIT=COMPILE==="; fi']
    if has_java:
        script += [f'if javac -proc:none -encoding UTF-8 -d /tmp/ll/j $(find /src/{JAVA_ROOT} -name "*.java") 2>/tmp/ll/javac.err; then JOK=1; else JOK=0; fi']
        for p in mains:
            pid, cls = p['program_id'], p['class']
            script += [f'echo "===LL:JAVA:{pid}:BEGIN==="',
                       f'if [ "$JOK" = 1 ]; then timeout 10 java -Xmx128m -cp /tmp/ll/j {JAVA_PACKAGE}.{cls}; echo "===LL:JAVA:{pid}:EXIT=$?==="; '
                       f'else cat /tmp/ll/javac.err; echo "===LL:JAVA:{pid}:EXIT=COMPILE==="; fi']
    command = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
               '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--pids-limit', '128', '--memory', '768m', '--cpus', '1', '--ulimit', 'nofile=256:256',
               '--tmpfs', '/tmp:rw,nosuid,nodev,exec,size=128m', '--env', 'HOME=/tmp',
               '--mount', f'type=bind,src={folder.resolve()},dst=/src,readonly',
               '--workdir', '/tmp', image, 'sh', '-c', '\n'.join(script)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'not_run', 'reason': 'Runner no disponible o excedió el tiempo: ' + str(exc)[:160]}
    if proc.returncode == 125:
        return {'status': 'not_run', 'reason': f'Docker no pudo iniciar el runner; construye la imagen: docker build -t {image} docker/legacy-runner'}
    outputs: dict[str, dict] = {'cobol': {}, 'java': {}}
    for lang, pid, body, code in re.findall(r'===LL:(COBOL|JAVA):([A-Z0-9-]+):BEGIN===\n(.*?)===LL:\1:\2:EXIT=(\w+)===', proc.stdout, re.S):
        outputs[lang.lower()][pid] = {'stdout': body[-8000:], 'exit': code}
    def side(name: str, requested: bool) -> dict:
        if not requested:
            return {'status': 'not_run', 'reason': 'No solicitado'}
        runs = outputs[name]
        if len(runs) != len(mains):
            return {'status': 'failed', 'reason': 'El runner no produjo salida para todos los programas', 'runs': runs,
                    'stderr': proc.stderr[-2000:]}
        ok = all(r['exit'] == '0' for r in runs.values())
        return {'status': 'passed' if ok else 'failed', 'runs': runs,
                'reason': '' if ok else 'Fallo de compilación o ejecución (ver salida)'}
    return {'status': 'completed', 'image': image, 'network': 'none', 'read_only': True, 'worker': 'docker-local-dev',
            'programs': [p['program_id'] for p in mains],
            'cobol': side('cobol', run_cobol), 'java': side('java', has_java)}


def compare(reference: dict, candidate: dict) -> tuple[str, str, list[dict]]:
    """Classification from the baseline COBOL run vs the candidate Java run."""
    ref = (reference or {}).get('cobol') or {}
    new = (candidate or {}).get('java') or {}
    if ref.get('status') != 'passed':
        cause = (reference or {}).get('reason') or ref.get('reason') or 'la ejecución del COBOL original falló'
        return 'not_demonstrated', f'No hay ejecución de referencia del COBOL original: {cause}', []
    if new.get('status') != 'passed':
        return 'divergent', 'El Java no compiló o no terminó correctamente: ' + str(new.get('reason') or 'sin ejecución'), []
    details = []
    for pid, run in ref['runs'].items():
        expected, actual = _normalize(run['stdout']), _normalize((new['runs'].get(pid) or {}).get('stdout', ''))
        details.append({'program': pid, 'equal': expected == actual, 'cobol': expected[-2000:], 'java': actual[-2000:]})
    if details and all(d['equal'] for d in details) and any(d['cobol'] for d in details):
        return ('verified_within_tests',
                f'Dual run: la salida del Java coincide exactamente con la del COBOL original en {len(details)} programa(s). Solo demuestra el escenario ejecutado.',
                details)
    if all(d['equal'] for d in details):
        return 'not_demonstrated', 'Ambos programas no producen salida observable; no hay evidencia de comportamiento.', details
    return 'divergent', 'Dual run: la salida del Java difiere de la del COBOL original.', details
