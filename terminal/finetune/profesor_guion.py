#!/usr/bin/env python3
"""Profesor con guion: un servidor compatible con OpenAI que actúa como una IA ideal.

Para cada proyecto generado conoce la verdad (reglas, defectos, líneas) y decide,
paso a paso, qué herramienta llamar y qué informe escribir. La terminal real
ejecuta esas herramientas (ls, view, grep, edit, write, run_tests), así que las
trayectorias grabadas por legacylift_proxy.py tienen exactamente el formato que
verá el modelo local, con resultados de herramientas reales.

No usa ninguna IA externa: todo sale de la verdad conocida (sin restricciones de licencia).

Uso: profesor_guion.py --datos datos --puerto 8090
"""
import argparse
import hashlib
import json
import random
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PHASE_RE = re.compile(r"#\s*LegacyLift\s*/\s*([A-Z]+)")
WD_RE = re.compile(r"Directorio de trabajo:\s*(\S+)")
NUM = {"DISCOVER": "01-discover", "UNDERSTAND": "02-understand", "ASSESS": "03-assess", "PLAN": "04-plan",
       "BASELINE": "05-baseline", "MODERNIZE": "06-modernize", "VERIFY": "07-verify"}
LANG_NAME = {"python": "Python 3", "javascript": "JavaScript (Node.js)", "java": "Java", "go": "Go"}
TEST_FW = {"python": "unittest", "javascript": "node:test", "java": "pruebas propias con `run_tests.sh`", "go": "testing de Go"}
DEFECT_TXT = {
    "todo": "comentario TODO pendiente",
    "bool_compare": "comparación con true explícita (`== True`/`== true`), innecesaria",
    "dead_code": "código muerto: función que no se usa en ningún lugar",
    "duplicate": "función duplicada (misma lógica repetida)",
    "commented_code": "código comentado de una versión anterior",
    "swallowed_exception": "excepción silenciada: el error se ignora sin registrarlo",
}


class Truth:
    def __init__(self, datos):
        self.by_id = {}
        for f in Path(datos, "meta").glob("*/*.json"):
            t = json.loads(f.read_text(encoding="utf-8"))
            self.by_id[t["id"]] = t

    def get(self, workdir):
        return self.by_id.get(Path(workdir).name)


def rng_for(*parts):
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16))


def src_files(t):
    return [f for f in t["archivos"] if not f.endswith((".md", ".json", ".sh", ".mod", ".txt"))]


def code_files(t):
    return [f for f in src_files(t) if not re.search(r"(^|/)test|_test\.go$|Test\.java$", f)]


def test_files(t):
    return [f for f in src_files(t) if f not in code_files(t)]


# ---------------------------------------------------------------- informes
def pick(r, *opts):
    return r.choice(opts)


def report_discover(t, r):
    L = t["lenguaje"]
    out = [f"# Discover — inventario del sistema de {t['dominio']}", ""]
    out += [pick(r, "## Resumen", "## Visión general"), "",
            f"Sistema heredado de {t['dominio']} escrito en **{LANG_NAME[L]}**, con {len(code_files(t))} archivo(s) de código "
            f"y {len(test_files(t))} de pruebas. No tiene dependencias externas declaradas.", ""]
    out += ["## Lenguajes y frameworks", "", "| Tecnología | Evidencia |", "|---|---|",
            f"| {LANG_NAME[L]} | `{code_files(t)[0]}:1` |", f"| Pruebas: {TEST_FW[L]} | `{test_files(t)[0]}:1` |", ""]
    out += ["## Estructura", ""] + [f"- `{f}`" for f in t["archivos"]] + [""]
    out += ["## Funciones de negocio encontradas", ""]
    for x in t["reglas"]:
        out.append(f"- `{x['funcion']}` en `{x['archivo']}:{x['linea_inicio']}-{x['linea_fin']}`")
    out += ["", "## Pruebas existentes", "", f"Hay pruebas automáticas en " + ", ".join(f"`{f}`" for f in test_files(t)) + ".", ""]
    out += ["## Hallazgos iniciales", ""]
    for d in t["defectos"]:
        out.append(f"- {DEFECT_TXT[d['tipo']]} en `{d['archivo']}:{d['linea']}`")
    if not t["defectos"]:
        out.append("- No se encontraron defectos evidentes.")
    return "\n".join(out) + "\n"


def report_understand(t, r):
    out = [f"# Understand — cómo funciona el sistema de {t['dominio']}", "", "## Arquitectura actual", ""]
    out.append(pick(r, "Es un sistema de funciones puras: cada función recibe valores y devuelve un resultado, sin base de datos ni servicios externos.",
                    "La lógica está en funciones independientes, sin estado compartido ni acceso a datos externos."))
    out += ["", "## Reglas de negocio", ""]
    for x in t["reglas"]:
        out.append(f"- **`{x['funcion']}`**: {x['enunciado']}. Evidencia: `{x['archivo']}:{x['linea_inicio']}-{x['linea_fin']}`.")
    out += ["", "## Flujos principales", ""]
    out.append("Cada regla se usa de forma aislada; las pruebas en " + ", ".join(f"`{f}`" for f in test_files(t)) + " muestran los casos esperados.")
    todos = [d for d in t["defectos"] if d["tipo"] == "todo"]
    out += ["", "## Preguntas abiertas", ""]
    if todos:
        for d in todos:
            out.append(f"- El comentario TODO en `{d['archivo']}:{d['linea']}` indica que la regla no está confirmada con negocio.")
    else:
        out.append("- No hay dudas explícitas en el código; confirmar los valores con el área de negocio.")
    return "\n".join(out) + "\n"


def report_assess(t, r):
    out = ["# Assess — evaluación del sistema", "", "| Archivo | Deuda técnica | Riesgo | Mantenibilidad | Testabilidad | Estrategia |", "|---|---|---|---|---|---|"]
    for f in code_files(t):
        ds = [d for d in t["defectos"] if d["archivo"] == f]
        deuda = min(5, 1 + len(ds))
        out.append(f"| `{f}` | {deuda} | {2 if deuda < 4 else 3} | {max(1, 5 - len(ds) // 2)} | 4 | {'refactorizar' if ds else 'mantener'} |")
    out += ["", "Escala 1–5 (5 = peor deuda / mejor mantenibilidad). Puntajes basados en los hallazgos citados abajo.", "", "## Evidencia", ""]
    for d in t["defectos"]:
        out.append(f"- {DEFECT_TXT[d['tipo']]} en `{d['archivo']}:{d['linea']}`")
    out += ["", "## Riesgos de modernizar", "",
            "- Las reglas usan valores fijos (números mágicos); cambiarlos sin confirmación altera montos.",
            "- El redondeo a 2 decimales es parte del comportamiento: no debe eliminarse."]
    return "\n".join(out) + "\n"


def wave(t):
    """Cambios seguros (no alteran el comportamiento) para la Migration Wave."""
    ch = []
    for d in t["defectos"]:
        if d["tipo"] in ("bool_compare", "dead_code", "todo"):
            ch.append(d)
    if not ch and t["reglas"]:
        # Sin defectos seguros de corregir: documentar la primera regla.
        x = t["reglas"][0]
        ch.append({"tipo": "document_rule", "archivo": x["archivo"], "linea": x["linea_inicio"]})
    return ch


def report_plan(t, r):
    ch = wave(t)
    out = ["# Plan — Modernization Blueprint", "", "## Arquitectura actual y objetivo", "",
           "Actual: funciones con valores fijos, comparaciones redundantes y restos de código antiguo.",
           "Objetivo: el mismo comportamiento, con código más claro y sin código muerto.", "",
           "## Alcance", "", "Refactor dentro del mismo lenguaje. Fuera de alcance: cambiar reglas de negocio o el redondeo.", "",
           "## Migration Wave 1", ""]
    files = sorted({d["archivo"] for d in ch})
    for f in files:
        out.append(f"- `{f}`:")
        for d in ch:
            if d["archivo"] != f:
                continue
            what = {"bool_compare": "quitar la comparación `== True`/`== true`",
                    "dead_code": "eliminar la función que no se usa",
                    "todo": "reemplazar el TODO por un comentario que documente la regla",
                    "document_rule": "documentar la regla de negocio con un comentario"}[d["tipo"]]
            out.append(f"  - línea {d['linea']}: {what}")
    if not files:
        out.append("- Sin cambios necesarios: el código ya es claro.")
    out += ["", "## Verificación", "", "Las pruebas existentes deben seguir pasando sin cambios en `.legacylift/candidate/`."]
    return "\n".join(out) + "\n"


def report_baseline(t, result):
    ok = "ÉXITO" in result
    return ("# Baseline — comportamiento antes de modernizar\n\n"
            f"Se ejecutaron las pruebas del sistema original con `run_tests`.\n\n"
            f"Resultado: **{'todas las pruebas pasan' if ok else 'hay pruebas que fallan'}**.\n\n"
            "```\n" + result.strip()[:1500] + "\n```\n\n"
            "Este resultado es la referencia para comparar la versión candidata.\n")


def report_modernize(t, applied):
    out = ["# Modernize — cambios aplicados en `.legacylift/candidate/`", "", "## Archivos transformados", ""]
    for f, desc in applied:
        out.append(f"- `{f}`: {desc}")
    if not applied:
        out.append("- Ninguno: la wave no tenía cambios.")
    out += ["", "## Notas", "", "- El código original no se modificó.", "- No se agregaron dependencias ni se cambió el redondeo."]
    return "\n".join(out) + "\n"


def report_verify(t, result, applied):
    ok = "ÉXITO" in result
    score = 90 if ok and applied else (60 if ok else 15)
    out = ["# Verify — comparación de la candidata con el baseline", "", "## Cambios", ""]
    out += [f"- `{f}`: {d}" for f, d in applied] or ["- Sin cambios."]
    out += ["", "## Pruebas sobre la candidata", "", "```", result.strip()[:1500], "```", "",
            f"Resultado: **{'las pruebas pasaron' if ok else 'hay pruebas que FALLAN: hay divergencias'}**.", "",
            "## Confidence score", "", f"**{score}/100**. Se verificó con las pruebas existentes; "
            "no se verificaron casos fuera de las pruebas ni rendimiento."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- ediciones de Modernize
def edits_for(t, workdir):
    """Lista de (archivo_candidato, old, new, descripción) que preservan el comportamiento."""
    out = []
    root = Path(workdir)
    by_file = {}
    for d in wave(t):
        by_file.setdefault(d["archivo"], []).append(d)
    for f, ds in by_file.items():
        lines = (root / f).read_text(encoding="utf-8").split("\n")
        handled = set()
        for d in sorted(ds, key=lambda x: x["linea"]):
            i = d["linea"] - 1
            if (i, d["tipo"]) in handled:
                continue
            if d["tipo"] in ("bool_compare", "todo"):
                old = lines[i]
                new = re.sub(r"\s*==\s*(True|true)", "", old)
                regla = next((x for x in t["reglas"] if x["linea_inicio"] <= d["linea"] <= x["linea_fin"]), None)
                if "TODO" in new and regla:
                    cm = "#" if t["lenguaje"] == "python" else "//"
                    new = re.sub(r"(#|//) TODO:.*$", f"{cm} Regla: {regla['enunciado']}", new)
                handled.update({(i, "bool_compare"), (i, "todo")})
                if new != old:
                    desc = []
                    if old.count("==") > new.count("=="): desc.append("se quitó la comparación redundante con true")
                    if "TODO" in old and "TODO" not in new: desc.append("el TODO se reemplazó por la regla documentada")
                    out.append((f".legacylift/candidate/{f}", old, new, " y ".join(desc) + f" (línea {d['linea']})"))
            elif d["tipo"] == "document_rule":
                regla = next(x for x in t["reglas"] if x["linea_inicio"] == d["linea"] and x["archivo"] == f)
                cm = "#" if t["lenguaje"] == "python" else "//"
                indent = "    " if t["lenguaje"] == "java" else ""
                old = lines[i]
                out.append((f".legacylift/candidate/{f}", old, f"{indent}{cm} Regla: {regla['enunciado']}\n{old}",
                            f"se documentó la regla de `{regla['funcion']}` (línea {d['linea']})"))
            elif d["tipo"] == "dead_code":
                j = i + 1
                if t["lenguaje"] == "python":
                    while j < len(lines) and lines[j].strip():
                        j += 1
                else:
                    close = "    }" if t["lenguaje"] == "java" else "}"
                    while j < len(lines) and lines[j] != close:
                        j += 1
                    j += 1
                old = "\n".join(lines[i:j]) + "\n"
                out.append((f".legacylift/candidate/{f}", old, "", f"se eliminó la función sin uso (línea {d['linea']})"))
    return out


# ---------------------------------------------------------------- guion por fase
def script(phase, t, workdir, r):
    """Lista de pasos: ("tool", nombre, args) o ("write", nombre_fn)."""
    cf = code_files(t)
    steps = []
    if phase == "DISCOVER":
        steps.append(("tool", "ls", {"path": "."}) if r.random() < 0.6 else ("tool", "glob", {"pattern": "**/*", "path": "."}))
        for f in src_files(t):
            steps.append(("tool", "view", {"file_path": f}))
        if any(d["tipo"] == "todo" for d in t["defectos"]):
            steps.append(("tool", "grep", {"pattern": "TODO", "path": "."}))
        steps.append(("write", "01-discover", report_discover))
    elif phase == "UNDERSTAND":
        steps.append(("tool", "view", {"file_path": ".legacylift/01-discover.md"}))
        for f in cf + test_files(t)[:1]:
            steps.append(("tool", "view", {"file_path": f}))
        steps.append(("write", "02-understand", report_understand))
    elif phase == "ASSESS":
        steps.append(("tool", "view", {"file_path": ".legacylift/02-understand.md"}))
        steps.append(("tool", "view", {"file_path": ".legacylift/01-discover.md"}))
        for f in cf:
            steps.append(("tool", "view", {"file_path": f}))
        steps.append(("write", "03-assess", report_assess))
    elif phase == "PLAN":
        steps.append(("tool", "view", {"file_path": ".legacylift/03-assess.md"}))
        for f in sorted({d["archivo"] for d in wave(t)}):
            steps.append(("tool", "view", {"file_path": f}))
        steps.append(("write", "04-plan", report_plan))
    elif phase == "BASELINE":
        steps.append(("tool", "view", {"file_path": ".legacylift/04-plan.md"}))
        steps.append(("tool", "run_tests", {"target": "original"}))
        steps.append(("write", "05-baseline", "baseline"))
    elif phase == "MODERNIZE":
        steps.append(("tool", "view", {"file_path": ".legacylift/04-plan.md"}))
        eds = edits_for(t, workdir)
        for f in sorted({e[0] for e in eds}):
            steps.append(("tool", "view", {"file_path": f}))
        for k, (f, old, new, _) in enumerate(eds):
            if k == 0 and r.random() < 0.4:
                # Ejemplo de recuperación: intenta editar el original, el bloqueo lo rechaza y
                # corrige la ruta. a_dataset.py descarta el paso erróneo como objetivo de
                # entrenamiento, pero conserva el rechazo en el historial de los pasos siguientes.
                steps.append(("wrong", "edit", {"file_path": f.replace(".legacylift/candidate/", "", 1),
                                                "old_string": old, "new_string": new}))
            steps.append(("tool", "edit", {"file_path": f, "old_string": old, "new_string": new}))
        steps.append(("write", "06-modernize", "modernize"))
    elif phase == "VERIFY":
        steps.append(("tool", "view", {"file_path": ".legacylift/06-modernize.md"}))
        steps.append(("tool", "run_tests", {"target": "candidate"}))
        steps.append(("write", "07-verify", "verify"))
    return steps


def last_tool_result(messages, name):
    ids = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            ids[tc["id"]] = tc["function"]["name"]
    for m in reversed(messages):
        if m.get("role") == "tool" and ids.get(m.get("tool_call_id")) == name:
            c = m.get("content")
            return c if isinstance(c, str) else json.dumps(c)
    return ""


def next_action(body, truth):
    msgs = body.get("messages", [])
    if not body.get("tools"):
        return {"text": "Análisis LegacyLift"}
    sysmsg = " ".join(m.get("content") or "" for m in msgs if m.get("role") == "system" and isinstance(m.get("content"), str))
    user = " ".join(m.get("content") or "" for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), str))
    wd, ph = WD_RE.search(sysmsg), PHASE_RE.search(user)
    t = truth.get(wd.group(1)) if wd else None
    if not t or not ph:
        return {"text": "No tengo guion para este proyecto."}
    phase = ph.group(1)
    r = rng_for(t["id"], phase)
    steps = script(phase, t, wd.group(1), r)
    done = sum(1 for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
    if done >= len(steps):
        return {"text": f"Listo: escribí `.legacylift/{NUM[phase]}.md`."}
    kind, name, arg = steps[done]
    if kind in ("tool", "wrong"):
        return {"tool": name, "args": arg, "wrong": kind == "wrong"}
    applied = [(e[0].replace(".legacylift/candidate/", ""), e[3]) for e in edits_for(t, wd.group(1))]
    if arg == "baseline":
        content = report_baseline(t, last_tool_result(msgs, "run_tests"))
    elif arg == "modernize":
        content = report_modernize(t, applied)
    elif arg == "verify":
        content = report_verify(t, last_tool_result(msgs, "run_tests"), applied)
    else:
        content = arg(t, r)
    return {"tool": "write", "args": {"file_path": f".legacylift/{name}.md", "content": content}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._send_json({"object": "list", "data": [{"id": "profesor", "object": "model", "owned_by": "legacylift"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        act = next_action(body, self.server.truth)
        cid, now, model = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time()), body.get("model", "profesor")
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if "tool" in act:
            prefix = "call_wrong_" if act.get("wrong") else "call_"
            call = {"index": 0, "id": f"{prefix}{uuid.uuid4().hex[:16]}", "type": "function",
                    "function": {"name": act["tool"], "arguments": json.dumps(act["args"], ensure_ascii=False)}}
            delta, finish = {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
            message = {"role": "assistant", "content": None, "tool_calls": [{k: v for k, v in call.items() if k != "index"}]}
        else:
            delta, finish = {"role": "assistant", "content": act["text"]}, "stop"
            message = {"role": "assistant", "content": act["text"]}
        if not body.get("stream"):
            return self._send_json({"id": cid, "object": "chat.completion", "created": now, "model": model,
                                    "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for ch in ({"index": 0, "delta": delta, "finish_reason": None}, {"index": 0, "delta": {}, "finish_reason": finish}):
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": model, "choices": [ch]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': now, 'model': model, 'choices': [], 'usage': usage})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True)
    ap.add_argument("--puerto", type=int, default=8090)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.puerto), Handler)
    srv.truth = Truth(a.datos)
    print(f"profesor con guion en :{a.puerto} ({len(srv.truth.by_id)} proyectos)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
