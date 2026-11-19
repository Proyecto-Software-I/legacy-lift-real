#!/usr/bin/env python3
"""Convierte trayectorias grabadas (legacylift_proxy.py) al formato de mlx-lm.

Cada pedido grabado al modelo es un ejemplo: el historial completo (system, user,
llamadas a herramientas y sus resultados reales) más la respuesta correcta como
último mensaje del asistente. Con mask_prompt solo se entrena esa última respuesta,
así cada paso (llamar una herramienta o escribir el informe) es un ejemplo.

Formato de salida (JSONL, uno por línea): {"messages": [...], "tools": [...]}

Uso: python3 a_dataset.py --entrada tr.jsonl --salida dataset/train.jsonl [--max-chars 28000]
"""
import argparse
import json
import re
from pathlib import Path


def norm_call(tc):
    fn = tc.get("function") or {"name": tc.get("name"), "arguments": tc.get("arguments")}
    args = fn.get("arguments") or "{}"
    try:
        args = json.loads(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        return None
    return {"type": "function", "function": {"name": fn["name"], "arguments": args}}


def norm_msg(m):
    out = {"role": m["role"]}
    c = m.get("content")
    if isinstance(c, list):  # partes de texto
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    out["content"] = c or ""
    if m.get("tool_calls"):
        calls = [norm_call(tc) for tc in m["tool_calls"]]
        if any(x is None for x in calls):
            return None
        out["tool_calls"] = calls
    if m["role"] == "tool":
        out["tool_call_id"] = m.get("tool_call_id")
    return out


def convert(rec):
    if not rec.get("tools"):
        return None  # títulos de sesión u otras llamadas sin herramientas
    msgs = [norm_msg(m) for m in rec["messages"]]
    if any(m is None for m in msgs):
        return None
    target = {"role": "assistant", "content": rec.get("answer") or ""}
    if rec.get("tool_calls"):
        calls = [norm_call({"function": {"name": c["name"], "arguments": c["arguments"]}}) for c in rec["tool_calls"]]
        if any(x is None for x in calls):
            return None
        target["tool_calls"] = calls
    if not target["content"] and not target.get("tool_calls"):
        return None
    tools = [{"type": "function", "function": t["function"]} for t in rec["tools"]]
    return {"messages": msgs + [target], "tools": tools}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", nargs="+", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--max-chars", type=int, default=28000, help="descarta ejemplos más largos (≈8k tokens)")
    ap.add_argument("--eval", help="eval.json de evaluar.py: solo se usan proyectos que aprueban (muestreo por rechazo)")
    ap.add_argument("--min", type=float, default=0.8, help="nota mínima en reglas, defectos y citas para aprobar")
    ap.add_argument("--repetir", default="", help="sobremuestreo por fase, p. ej. MODERNIZE=3")
    a = ap.parse_args()
    n_in = n_out = long_ = rejected = wrong = 0
    reps = {k.upper(): int(v) for k, v in (x.split("=") for x in a.repetir.split(",") if x)}
    ok_ids = None
    if a.eval:
        res = json.load(open(a.eval, encoding="utf-8"))["proyectos"]
        ok_ids = {p["id"] for p in res if p["reglas"] >= a.min and p["defectos"] >= a.min and p["citas"] >= a.min
                  and p["espanol"] >= 0.99 and p["original_intacto"] and p["modernize_pruebas_ok"] and p["verify_honesto"]}
        print(f"proyectos aprobados: {len(ok_ids)} de {len(res)}")
    out = Path(a.salida)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for path in a.entrada:
            for line in open(path, encoding="utf-8"):
                n_in += 1
                rec = json.loads(line)
                if ok_ids is not None:
                    sysm = " ".join(m.get("content") or "" for m in rec.get("messages", []) if m.get("role") == "system" and isinstance(m.get("content"), str))
                    wd = re.search(r"Directorio de trabajo:\s*(\S+)", sysm)
                    if not wd or Path(wd.group(1)).name not in ok_ids:
                        rejected += 1
                        continue
                if any(str(c.get("id", "")).startswith("call_wrong_") for c in rec.get("tool_calls") or []):
                    wrong += 1  # paso erróneo a propósito: no se aprende como respuesta
                    continue
                ex = convert(rec)
                if not ex:
                    continue
                user = " ".join(m.get("content") or "" for m in rec.get("messages", []) if m.get("role") == "user" and isinstance(m.get("content"), str))
                ph = re.search(r"#\s*LegacyLift\s*/\s*([A-Z]+)", user)
                times = reps.get(ph.group(1), 1) if ph else 1
                if len(json.dumps(ex, ensure_ascii=False)) > a.max_chars:
                    long_ += 1
                    continue
                for _ in range(times):
                    f.write(json.dumps(ex, ensure_ascii=False) + "\n")
                    n_out += 1
    print(f"{n_in} pedidos -> {n_out} ejemplos ({long_} descartados por largos, {rejected} de proyectos reprobados, {wrong} pasos erróneos omitidos) en {out}")


if __name__ == "__main__":
    main()
