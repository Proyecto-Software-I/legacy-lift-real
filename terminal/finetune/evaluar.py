#!/usr/bin/env python3
"""Evalúa automáticamente los informes de LegacyLift contra la verdad conocida.

Para cada proyecto generado por generar_proyectos.py compara lo que escribió el
modelo en .legacylift/ con meta/<split>/<id>.json, sin depender de otra IA:

  reglas      % de reglas de negocio de la verdad que Understand menciona
              (nombre de la función + su parámetro clave, p. ej. 10 % o 1000)
  defectos    % de defectos sembrados que Discover/Assess detectan (tipo + archivo)
  citas       % de citas `archivo:línea` que existen de verdad (precisión)
  espanol     el informe está en español (no portugués ni inglés)
  modernize   la candidata cambió, el original quedó intacto y las pruebas pasan
  honestidad  Verify no dice "pasaron" si las pruebas de la candidata fallan

Uso: python3 evaluar.py --datos datos --split valid [--trabajo carpeta_con_copias] --salida eval.json
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PHASES = ["01-discover", "02-understand", "03-assess", "04-plan", "05-baseline", "06-modernize", "07-verify"]
DEFECT_WORDS = {
    "todo": ["todo"],
    "bool_compare": ["== true", "comparación con true", "comparacion con true", "comparación booleana", "booleano", "true explícit"],
    "dead_code": ["código muerto", "codigo muerto", "no se usa", "sin uso", "no utilizada", "no utilizado", "nunca se llama", "obsolet"],
    "duplicate": ["duplica"],
    "commented_code": ["código comentado", "codigo comentado", "comentado", "versión anterior"],
    "swallowed_exception": ["excepción", "excepcion", "except", "catch", "silenci", "ignora el error", "error ignorado"],
}
CITE = re.compile(r"([\w./-]+\.(?:py|js|java|go|md|txt|json|sh)):(\d+)(?:-(\d+))?")


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return ""


def norm(s):
    return s.lower().replace("_", " ")


def spanish(text):
    t = f" {text.lower()} "
    es = sum(t.count(w) for w in (" el ", " la ", " los ", " las ", " que ", " una ", " del ", " para ", " con ", " se "))
    pt = sum(t.count(w) for w in (" não ", " uma ", " são ", " também ", " função", " arquivo", " código está"))
    en = sum(t.count(w) for w in (" the ", " and ", " is ", " with ", " this ", " that "))
    return es > 3 * (pt + en) and es >= 5


def key_values(rule):
    p = rule["parametros"]
    key = {"pct_flag": "p", "min_fee": "t", "bulk": "n", "cap": "m", "tier": "l", "grade": "a"}[rule["tipo"]]
    vals = [p[key]]
    out = set()
    for v in vals:
        out.add(str(v))
        if isinstance(v, (int, float)) and rule["tipo"] in ("pct_flag",):
            out.add(f"{v / 100:g}")
    return out


def rule_found(text, rule):
    t = norm(text)
    fn = norm(rule["funcion"])
    # camelCase/PascalCase -> palabras
    words = norm(re.sub(r"(?<!^)([A-Z])", r" \1", rule["funcion"]))
    if fn not in t and words not in t:
        return False
    return any(v in text for v in key_values(rule))


def defect_found(text, d):
    t = text.lower()
    base = Path(d["archivo"]).name.lower()
    if base not in t and d["archivo"].lower() not in t:
        return False
    return any(w in t for w in DEFECT_WORDS[d["tipo"]])


def citations(text, root):
    total = valid = 0
    for f, a, b in CITE.findall(text):
        f = f.lstrip("./")
        if f.startswith(".legacylift"):
            continue
        total += 1
        path = Path(root) / f
        if not path.is_file():
            cands = list(Path(root).rglob(Path(f).name))
            cands = [c for c in cands if ".legacylift" not in c.parts]
            path = cands[0] if cands else None
        if path:
            n = read(path).count("\n") + 1
            if 1 <= int(a) <= n and (not b or int(b) <= n):
                valid += 1
    return total, valid


def test_cmd(root):
    r = Path(root)
    if (r / "run_tests.sh").exists():
        return "sh run_tests.sh"
    if (r / "go.mod").exists():
        return "go test ./..."
    if (r / "package.json").exists():
        return "npm test --silent"
    return 'PYTHONPATH=. python3 -m unittest discover -s tests -p "test*.py"'


def tests_pass(root):
    try:
        return subprocess.run(test_cmd(root), shell=True, cwd=root, capture_output=True, timeout=300).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def tree_hash(root, files):
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(f.encode()); h.update(read(Path(root) / f).encode())
    return h.hexdigest()


def evaluate(truth, root, original_root):
    art = Path(root) / ".legacylift"
    reports = {ph: read(art / f"{ph}.md") for ph in PHASES}
    res = {"id": truth["id"], "lenguaje": truth["lenguaje"], "tamano": truth["tamano"]}
    res["fases_completas"] = sum(1 for v in reports.values() if len(v.strip()) > 200)
    und = reports["02-understand"] + reports["01-discover"]
    res["reglas"] = sum(rule_found(und, r) for r in truth["reglas"]) / max(1, len(truth["reglas"]))
    disc = reports["01-discover"] + reports["03-assess"]
    res["defectos"] = sum(defect_found(disc, d) for d in truth["defectos"]) / max(1, len(truth["defectos"]))
    tot = val = 0
    for v in reports.values():
        t, ok = citations(v, root)
        tot += t; val += ok
    res["citas_total"], res["citas_validas"] = tot, val
    res["citas"] = val / tot if tot else 0.0
    present = [v for v in reports.values() if v.strip()]
    res["espanol"] = sum(spanish(v) for v in present) / max(1, len(present))
    cand = art / "candidate"
    res["original_intacto"] = tree_hash(root, truth["archivos"]) == tree_hash(original_root, truth["archivos"])
    if cand.is_dir():
        changed = tree_hash(cand, truth["archivos"]) != tree_hash(original_root, truth["archivos"])
        passed = tests_pass(cand)
        res["modernize_cambio"], res["modernize_pruebas_ok"] = changed, passed
        ver = reports["07-verify"].lower()
        claims_ok = any(w in ver for w in ("todas las pruebas pasaron", "éxito", "exito", "pasaron", "pasan"))
        res["verify_honesto"] = passed or not claims_ok
    else:
        res["modernize_cambio"] = res["modernize_pruebas_ok"] = False
        res["verify_honesto"] = True
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True, help="carpeta de generar_proyectos.py")
    ap.add_argument("--split", default="valid")
    ap.add_argument("--trabajo", help="carpeta con las copias donde corrió la terminal (por defecto, los proyectos)")
    ap.add_argument("--salida", default="eval.json")
    a = ap.parse_args()
    datos = Path(a.datos)
    work = Path(a.trabajo) if a.trabajo else datos / "proyectos" / a.split
    results = []
    for meta in sorted((datos / "meta" / a.split).glob("*.json")):
        truth = json.loads(meta.read_text(encoding="utf-8"))
        root = work / truth["id"]
        if not (root / ".legacylift").is_dir():
            continue
        results.append(evaluate(truth, root, datos / "proyectos" / a.split / truth["id"]))
    keys = ["fases_completas", "reglas", "defectos", "citas", "espanol", "original_intacto",
            "modernize_cambio", "modernize_pruebas_ok", "verify_honesto"]
    agg = {k: round(sum(float(r[k]) for r in results) / max(1, len(results)), 3) for k in keys}
    agg["proyectos"] = len(results)
    Path(a.salida).write_text(json.dumps({"resumen": agg, "proyectos": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
