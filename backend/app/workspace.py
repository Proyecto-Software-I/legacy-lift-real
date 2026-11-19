from __future__ import annotations

import hashlib
import json
import os
import uuid
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .settings import data_dir

# Configurable caps, with hard safety ceilings for a local single-machine deployment.
def _limit(name: str, default: int, ceiling: int) -> int:
    try: return max(1, min(int(os.getenv(name, str(default))), ceiling))
    except ValueError: return default

MAX_ZIP_BYTES = _limit('LEGACYLIFT_MAX_ZIP_MB', 64, 128) * 1024 * 1024
MAX_UNPACKED_BYTES = _limit('LEGACYLIFT_MAX_SOURCE_MB', 128, 256) * 1024 * 1024
MAX_FILES = _limit('LEGACYLIFT_MAX_FILES', 5000, 20000)
MAX_FILE_BYTES = _limit('LEGACYLIFT_MAX_FILE_MB', 5, 16) * 1024 * 1024
BLOCKED_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules', 'dist', 'build', '__pycache__', '.idea'}
BLOCKED_FILES = {'.env', '.env.local', 'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'}


def root(pid: str) -> Path:
    # UUIDs come from db, but validate before joining.
    import uuid
    if str(uuid.UUID(pid)) != pid:
        raise ValueError('ID inválido')
    value = data_dir() / 'projects' / pid
    value.mkdir(parents=True, exist_ok=True)
    return value


def project_source(pid: str) -> Path:
    return root(pid) / 'snapshot'


def project_candidate(pid: str) -> Path:
    return root(pid) / 'candidate'


def artifact_dir(pid: str) -> Path:
    path = root(pid) / 'artifacts'
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_artifact(pid: str, filename: str, contents: str) -> None:
    if filename not in ARTIFACT_NAMES:
        raise ValueError('Artefacto inválido')
    (artifact_dir(pid) / filename).write_text(contents, encoding='utf-8')


ARTIFACT_NAMES = {
    'inventory.md', 'inventory.json', 'understand.md', 'understand.json', 'assessment.md', 'assessment.json',
    'blueprint.md', 'blueprint.json', 'baseline.md', 'baseline.json', 'migration.md', 'changes.patch',
    'verification.md', 'report.md'
}


def artifacts(pid: str) -> list[str]:
    return sorted(f.name for f in artifact_dir(pid).iterdir() if f.is_file() and f.name in ARTIFACT_NAMES)


def snapshot_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        h.update(file.relative_to(folder).as_posix().encode('utf-8'))
        h.update(b'\0')
        h.update(file.read_bytes())
    return h.hexdigest()


def safe_relative(name: str) -> Path:
    normalized = name.replace('\\', '/')
    p = PurePosixPath(normalized)
    if not normalized or p.is_absolute() or '..' in p.parts or ':' in normalized or '\0' in normalized:
        raise ValueError('ZIP con ruta insegura')
    if any(part in BLOCKED_DIRS for part in p.parts):
        raise ValueError('ZIP incluye directorio excluido')
    if p.name in BLOCKED_FILES or p.name.endswith(('.pem', '.key', '.p12')):
        raise ValueError('ZIP incluye archivos de secretos')
    return Path(*p.parts)


def _stage_path(pid: str) -> Path:
    path=root(pid) / ('incoming-' + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path


def stage_zip(pid: str, payload: bytes) -> Path:
    """Unpack into a temporary folder; never replace an existing active snapshot."""
    import io
    if len(payload) > MAX_ZIP_BYTES:
        raise ValueError('ZIP supera el límite configurado')
    tmp=_stage_path(pid)
    total=0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            members=[i for i in z.infolist() if not i.is_dir()]
            if not members or len(members)>MAX_FILES:
                raise ValueError(f'ZIP vacío o con más de {MAX_FILES} archivos')
            seen=set()
            for info in members:
                rel=safe_relative(info.filename)
                if rel.as_posix().lower() in seen:
                    raise ValueError('Ruta duplicada en ZIP')
                seen.add(rel.as_posix().lower())
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('No se aceptan enlaces simbólicos')
                if info.file_size>MAX_FILE_BYTES:
                    raise ValueError(f'Archivo demasiado grande: {rel}')
                total+=info.file_size
                if total>MAX_UNPACKED_BYTES:
                    raise ValueError('Contenido descomprimido supera el límite configurado')
                target=tmp/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    raise ValueError('Ruta duplicada en ZIP')
                with z.open(info) as src, target.open('wb') as dest:
                    shutil.copyfileobj(src,dest)
        children=list(tmp.iterdir())
        if len(children)==1 and children[0].is_dir():
            nested=children[0]
            unpacked=tmp.with_name(tmp.name+'-flat')
            nested.rename(unpacked)
            shutil.rmtree(tmp)
            tmp=unpacked
        return tmp
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True)
        raise


def stage_git_folder(pid: str, git_folder: Path) -> Path:
    """Copy only ordinary files from a cloned repo. Never follow symlinks or copy Git internals."""
    tmp=_stage_path(pid)
    count=total=0
    try:
        for source in sorted(git_folder.rglob('*')):
            rel=source.relative_to(git_folder)
            if any(part in BLOCKED_DIRS for part in rel.parts):
                continue
            if source.is_symlink():
                raise ValueError('El repositorio contiene enlaces simbólicos (no admitidos)')
            if not source.is_file():
                continue
            safe_relative(rel.as_posix())
            size=source.stat().st_size
            if size>MAX_FILE_BYTES:
                raise ValueError(f'Archivo demasiado grande: {rel}')
            count+=1;total+=size
            if count>MAX_FILES or total>MAX_UNPACKED_BYTES:
                raise ValueError('Repositorio supera límite configurado')
            target=tmp/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        if not count:
            raise ValueError('Repositorio sin archivos admitidos')
        return tmp
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True)
        raise


def snapshot_archive(pid: str, sid: str) -> Path:
    if str(uuid.UUID(sid)) != sid:
        raise ValueError('Snapshot inválido')
    return root(pid)/'snapshots'/sid/'source'


def create_manifest(folder: Path) -> list[dict]:
    result=[]
    for f in sorted(folder.rglob('*')):
        if not f.is_file() or f.is_symlink():
            continue
        rel=f.relative_to(folder).as_posix()
        result.append({'path':rel,'size':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
    return result


def create_archive(pid: str, sid: str, staged: Path, manifest: list[dict]) -> Path:
    target=snapshot_archive(pid,sid)
    if target.exists():
        raise ValueError('Snapshot duplicado')
    target.parent.mkdir(parents=True,exist_ok=False)
    staged.rename(target)
    (target.parent/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # Archive is write-protected at filesystem level as well as by the application flow.
    for f in target.rglob('*'):
        if f.is_file(): f.chmod(0o444)
    return target


def activate_archive(pid: str, sid: str) -> None:
    """Make a disposable active copy; historical archive is never mutated by pipeline."""
    archive=snapshot_archive(pid,sid)
    if not archive.is_dir():
        raise ValueError('No existe el snapshot archivado')
    active=project_source(pid)
    incoming=root(pid)/('activate-'+uuid.uuid4().hex)
    shutil.copytree(archive,incoming)
    # Source is a separate working copy. Give it writable permissions for normal tooling.
    for f in incoming.rglob('*'):
        if f.is_file():f.chmod(0o644)
        elif f.is_dir():f.chmod(0o755)
    old=root(pid)/('previous-'+uuid.uuid4().hex)
    if active.exists(): active.rename(old)
    try:
        incoming.rename(active)
    except Exception:
        if old.exists(): old.rename(active)
        raise
    finally:
        shutil.rmtree(incoming,ignore_errors=True)
        shutil.rmtree(old,ignore_errors=True)
    shutil.rmtree(project_candidate(pid),ignore_errors=True)
    # Current artifacts belong to the previous active snapshot; future source snapshots
    # should never be confused with old understanding/approval artifacts.
    for file in artifact_dir(pid).iterdir():
        if file.is_file() and file.name in ARTIFACT_NAMES:
            file.unlink()


def extract_zip(pid: str, payload: bytes) -> tuple[int,str]:
    """Legacy helper, kept for callers that only need a first snapshot."""
    if project_source(pid).exists():
        raise ValueError('El proyecto ya tiene un snapshot')
    staged=stage_zip(pid,payload)
    staged.rename(project_source(pid))
    return len(create_manifest(project_source(pid))),snapshot_hash(project_source(pid))


def copy_snapshot(pid: str) -> Path:
    candidate = project_candidate(pid)
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(project_source(pid), candidate)
    return candidate


def read_code_context(folder: Path, limit: int = 55000) -> str:
    blocks = []
    used = 0
    allowed = {'.py', '.ts', '.js', '.java', '.cs', '.go', '.php', '.cbl', '.cob', '.cobol', '.cpy', '.pco', '.md', '.json', '.xml', '.yml', '.yaml', '.toml', '.sql'}
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink() or file.suffix.lower() not in allowed:
            continue
        rel = file.relative_to(folder).as_posix()
        text = file.read_text(encoding='utf-8', errors='replace')
        excerpt = text[:7000]
        block = f'\n### {rel}\n```\n{excerpt}\n```\n'
        if used + len(block) > limit:
            break
        blocks.append(block)
        used += len(block)
    return ''.join(blocks) or 'No hay archivos de texto en el snapshot.'


def diff_candidate(pid: str) -> tuple[str, int]:
    import difflib
    original, candidate = project_source(pid), project_candidate(pid)
    before = {f.relative_to(original) for f in original.rglob('*') if f.is_file() and not f.is_symlink()}
    after = {f.relative_to(candidate) for f in candidate.rglob('*') if f.is_file() and not f.is_symlink()}
    changed = 0
    result = []
    for rel in sorted(before | after):
        a = (original / rel).read_text(encoding='utf-8', errors='replace').splitlines(keepends=True) if rel in before else []
        b = (candidate / rel).read_text(encoding='utf-8', errors='replace').splitlines(keepends=True) if rel in after else []
        if a == b:
            continue
        changed += 1
        chunk = ''.join(difflib.unified_diff(a, b, fromfile=f'a/{rel.as_posix()}', tofile=f'b/{rel.as_posix()}'))
        result.append(chunk[:150_000])
    return '\n'.join(result)[:350_000], changed


def export_zip(pid: str, approved: bool) -> bytes:
    import io
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        if approved:
            candidate = project_candidate(pid)
            for p in candidate.rglob('*'):
                if p.is_file() and not p.is_symlink():
                    z.write(p, f'codigo-modernizado/{p.relative_to(candidate).as_posix()}')
        for p in artifact_dir(pid).glob('*'):
            if p.is_file() and p.name in ARTIFACT_NAMES:
                z.write(p, 'evidencias/'+p.name)
    return output.getvalue()
