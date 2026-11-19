#!/usr/bin/env python3
"""Genera proyectos "legacy" sintéticos con verdad conocida (Fase B del fine-tuning).

Cada proyecto es un sistema pequeño o mediano en Python, JavaScript, Java o Go con:
  - reglas de negocio parametrizadas (descuentos, recargos, topes, tramos, calificaciones),
  - defectos sembrados a propósito (TODO, comparación con True, paréntesis redundantes,
    código muerto, duplicación, código comentado, excepciones silenciadas, números mágicos),
  - pruebas automáticas estrictas (incluyen decimales para detectar cambios de redondeo).

La verdad conocida (qué reglas hay, en qué archivo y línea, qué defectos) se guarda
FUERA del proyecto, en <salida>/meta/<id>.json, para que el modelo no la vea.
Con una semilla fija el resultado es siempre el mismo.

Uso: python3 generar_proyectos.py --salida datos --train 60 --valid 10 --test 10 --semilla 7
"""
import argparse
import json
import random
import shutil
from pathlib import Path

LANGS = ["python", "javascript", "java", "go"]

# Dominios: nombres de funciones y frases de negocio por tipo de regla.
DOMINIOS = {
    "facturacion": {
        "pct_flag": ("descuento_cliente", "monto", "es_frecuente", "los clientes frecuentes reciben {p}% de descuento"),
        "min_fee": ("total_con_recargo", "monto", None, "las facturas menores a {t} pagan un recargo fijo de {f}"),
        "bulk": ("precio_por_volumen", "precio", "cantidad", "comprar {n} o más unidades da {d}% de descuento"),
        "cap": ("limitar_credito", "credito", None, "el crédito nunca supera {m}"),
        "tier": ("impuesto", "monto", None, "montos sobre {l} pagan {r2}% de impuesto; el resto {r1}%"),
        "grade": ("categoria_cliente", "compras", None, "{a} compras o más es ORO, {b} o más PLATA, si no BRONCE", ("ORO", "PLATA", "BRONCE")),
    },
    "inventario": {
        "pct_flag": ("ajuste_merma", "valor", "es_perecible", "los productos perecibles descuentan {p}% por merma"),
        "min_fee": ("costo_envio", "peso", None, "pedidos de menos de {t} kg pagan {f} extra de manipulación"),
        "bulk": ("costo_compra", "costo", "unidades", "pedir {n} o más unidades al proveedor da {d}% de rebaja"),
        "cap": ("stock_maximo", "stock", None, "el stock de un producto no puede pasar de {m}"),
        "tier": ("valor_almacenaje", "volumen", None, "volúmenes sobre {l} se cobran al {r2}%; el resto al {r1}%"),
        "grade": ("nivel_reposicion", "existencias", None, "{a} o más existencias es ALTO, {b} o más MEDIO, si no CRITICO", ("ALTO", "MEDIO", "CRITICO")),
    },
    "nomina": {
        "pct_flag": ("bono_antiguedad", "sueldo", "tiene_antiguedad", "con antigüedad se paga un bono del {p}% del sueldo"),
        "min_fee": ("sueldo_con_minimo", "sueldo", None, "sueldos menores a {t} reciben un complemento de {f}"),
        "bulk": ("pago_horas_extra", "tarifa", "horas", "desde {n} horas extra la tarifa sube un {d}% (se descuenta del recargo base)"),
        "cap": ("tope_anticipo", "anticipo", None, "ningún anticipo supera {m}"),
        "tier": ("retencion", "sueldo", None, "sueldos sobre {l} retienen {r2}%; el resto {r1}%"),
        "grade": ("evaluacion_desempeno", "puntaje", None, "{a} puntos o más es DESTACADO, {b} o más ADECUADO, si no INSUFICIENTE", ("DESTACADO", "ADECUADO", "INSUFICIENTE")),
    },
    "academico": {
        "pct_flag": ("beca_descuento", "matricula", "tiene_beca", "los becados tienen {p}% de descuento en la matrícula"),
        "min_fee": ("multa_atraso", "pago", None, "pagos menores a {t} llevan una multa de {f}"),
        "bulk": ("costo_creditos", "valor_credito", "creditos", "inscribir {n} o más créditos da {d}% de descuento"),
        "cap": ("nota_maxima", "nota", None, "ninguna nota puede superar {m}"),
        "tier": ("arancel_extra", "monto", None, "montos sobre {l} pagan {r2}% de arancel; el resto {r1}%"),
        "grade": ("estado_alumno", "promedio", None, "promedio {a} o más es APROBADO, {b} o más RECUPERACION, si no REPROBADO", ("APROBADO", "RECUPERACION", "REPROBADO")),
    },
    "reservas": {
        "pct_flag": ("descuento_socio", "tarifa", "es_socio", "los socios pagan {p}% menos"),
        "min_fee": ("tarifa_minima", "tarifa", None, "reservas menores a {t} cobran {f} de gestión"),
        "bulk": ("tarifa_noches", "precio_noche", "noches", "quedarse {n} noches o más da {d}% de descuento"),
        "cap": ("limite_huespedes", "huespedes", None, "una reserva admite como máximo {m} huéspedes"),
        "tier": ("tasa_turistica", "monto", None, "montos sobre {l} pagan {r2}% de tasa; el resto {r1}%"),
        "grade": ("tipo_temporada", "ocupacion", None, "{a}% de ocupación o más es ALTA, {b}% o más MEDIA, si no BAJA", ("ALTA", "MEDIA", "BAJA")),
    },
    "prestamos": {
        "pct_flag": ("rebaja_tasa", "interes", "buen_historial", "con buen historial el interés baja {p}%"),
        "min_fee": ("comision_apertura", "monto", None, "préstamos menores a {t} pagan comisión de {f}"),
        "bulk": ("costo_cuotas", "cuota", "meses", "desde {n} meses las cuotas tienen {d}% de descuento"),
        "cap": ("monto_maximo", "monto", None, "no se presta más de {m}"),
        "tier": ("seguro_desgravamen", "monto", None, "montos sobre {l} pagan {r2}% de seguro; el resto {r1}%"),
        "grade": ("riesgo_cliente", "score", None, "score {a} o más es BAJO riesgo, {b} o más MEDIO, si no ALTO", ("BAJO", "MEDIO", "ALTO")),
    },
    # Dominios reservados para la prueba final (nunca aparecen en entrenamiento).
    "biblioteca": {
        "pct_flag": ("descuento_multa", "multa", "es_estudiante", "los estudiantes pagan {p}% menos de multa"),
        "min_fee": ("multa_minima", "multa", None, "multas menores a {t} suman {f} administrativos"),
        "bulk": ("costo_fotocopias", "precio_hoja", "hojas", "desde {n} hojas hay {d}% de descuento"),
        "cap": ("max_prestamos", "libros", None, "un lector no puede tener más de {m} libros"),
        "tier": ("recargo_demora", "dias", None, "atrasos sobre {l} días se cobran al {r2}%; el resto al {r1}%"),
        "grade": ("categoria_lector", "prestamos", None, "{a} préstamos o más es FRECUENTE, {b} o más REGULAR, si no OCASIONAL", ("FRECUENTE", "REGULAR", "OCASIONAL")),
    },
    "envios": {
        "pct_flag": ("descuento_prime", "costo", "es_prime", "los clientes prime pagan {p}% menos de envío"),
        "min_fee": ("cargo_pequeno", "costo", None, "envíos menores a {t} pagan {f} extra"),
        "bulk": ("costo_paquetes", "precio_paquete", "paquetes", "enviar {n} paquetes o más da {d}% de descuento"),
        "cap": ("peso_maximo", "peso", None, "un paquete no puede pesar más de {m}"),
        "tier": ("seguro_envio", "valor", None, "valores sobre {l} pagan {r2}% de seguro; el resto {r1}%"),
        "grade": ("prioridad_envio", "urgencia", None, "urgencia {a} o más es EXPRESS, {b} o más NORMAL, si no ECONOMICO", ("EXPRESS", "NORMAL", "ECONOMICO")),
    },
}
DOMINIOS_PRUEBA = {"biblioteca", "envios"}
KINDS = ["pct_flag", "min_fee", "bulk", "cap", "tier", "grade"]


def r2(x):
    return round(x + 1e-9, 2)


def safe_amount(rng, factor):
    """Monto con decimales cuyo resultado no cae en un empate de redondeo."""
    while True:
        a = round(rng.uniform(10, 900), 2)
        v = a * factor * 100
        if abs((v % 1) - 0.5) > 0.05 and abs(v % 1) > 0.05:
            return a


# ---------------------------------------------------------------- reglas
def make_rule(rng, kind, dom):
    spec = DOMINIOS[dom][kind]
    name, a1, a2, frase = spec[0], spec[1], spec[2], spec[3]
    p = {}
    if kind == "pct_flag":
        p["p"] = rng.choice([5, 8, 10, 12, 15, 20])
    elif kind == "min_fee":
        p["t"] = rng.choice([50, 100, 150, 200]); p["f"] = rng.choice([5, 7.5, 10, 12.5])
    elif kind == "bulk":
        p["n"] = rng.choice([5, 10, 12, 20]); p["d"] = rng.choice([5, 10, 15])
    elif kind == "cap":
        p["m"] = rng.choice([1000, 2500, 5000, 10000])
    elif kind == "tier":
        p["l"] = rng.choice([500, 1000, 2000]); p["r1"] = rng.choice([3, 5]); p["r2"] = rng.choice([8, 10, 12])
    elif kind == "grade":
        p["a"] = rng.choice([80, 85, 90]); p["b"] = rng.choice([50, 60, 70])
    labels = spec[4] if kind == "grade" else None
    return {"kind": kind, "name": name, "a1": a1, "a2": a2, "params": p, "labels": labels,
            "statement": frase.format(**p)}


def rule_cases(rng, rule):
    """Casos de prueba (args, esperado). Incluye decimales para detectar cambios de redondeo."""
    k, p = rule["kind"], rule["params"]
    if k == "pct_flag":
        a = safe_amount(rng, p["p"] / 100)
        return [((a, True), r2(a * p["p"] / 100)), ((a, False), 0.0), ((200.0, True), r2(200 * p["p"] / 100))]
    if k == "min_fee":
        lo = round(p["t"] * 0.37, 2); hi = float(p["t"] * 3)
        return [((lo,), r2(lo + p["f"])), ((hi,), r2(hi))]
    if k == "bulk":
        u = safe_amount(rng, p["n"] * (1 - p["d"] / 100))
        return [((u, p["n"]), r2(u * p["n"] * (1 - p["d"] / 100))), ((u, p["n"] - 1), r2(u * (p["n"] - 1)))]
    if k == "cap":
        return [((p["m"] * 2,), p["m"]), ((p["m"] // 3,), p["m"] // 3)]
    if k == "tier":
        hi = safe_amount(rng, p["r2"] / 100) + p["l"]; lo = round(p["l"] * 0.41, 2)
        return [((hi,), r2(hi * p["r2"] / 100)), ((lo,), r2(lo * p["r1"] / 100))]
    if k == "grade":
        a, b = p["a"], p["b"]; L = rule["labels"]
        return [((a,), L[0]), ((b,), L[1]), ((b - 1,), L[2])]
    raise ValueError(k)


# ---------------------------------------------------------------- renderizado por lenguaje
class Buf:
    """Acumula líneas y registra etiquetas (línea 1-based) para la verdad conocida."""

    def __init__(self):
        self.lines, self.tags = [], []

    def add(self, text, tag=None):
        self.lines.append(text)
        if tag:
            self.tags.append((tag, len(self.lines)))

    @property
    def n(self):
        return len(self.lines)


def lit(x):
    return repr(x) if isinstance(x, float) else str(x)


def render_rule(lang, b, rule, smells):
    k, p, n, a1, a2 = rule["kind"], rule["params"], rule["name"], rule["a1"], rule["a2"]
    todo = "todo" in smells; boolc = "bool_compare" in smells and k == "pct_flag"; par = "redundant_parens" in smells
    start = b.n + 1
    if lang == "python":
        cm, ret = "#", lambda e: f"return ({e})" if par else f"return {e}"
        rnd = lambda e: f"round({e}, 2)"
        args = f"{a1}, {a2}" if a2 else a1
        b.add(f"def {n}({args}):")
        I = "    "
        if k == "pct_flag":
            cond = f"{a2} == True" if boolc else a2
            b.add(f"{I}if {cond}:" + (f"  {cm} TODO: aclarar esta regla de negocio" if todo else ""), "todo" if todo else None)
            if boolc: b.tags.append(("bool_compare", b.n))
            b.add(f"{I*2}{ret(rnd(f'{a1} * {p['p'] / 100}'))}", "magic_number")
            b.add(f"{I}{ret('0.0')}")
        elif k == "min_fee":
            b.add(f"{I}if {a1} < {p['t']}:" + (f"  {cm} TODO: revisar recargo" if todo else ""), "todo" if todo else None)
            b.add(f"{I*2}{ret(rnd(f'{a1} + {lit(float(p['f']))}'))}", "magic_number")
            b.add(f"{I}{ret(rnd(a1))}")
        elif k == "bulk":
            b.add(f"{I}total = {a1} * {a2}")
            b.add(f"{I}if {a2} >= {p['n']}:" + (f"  {cm} TODO: confirmar volumen" if todo else ""), "todo" if todo else None)
            b.add(f"{I*2}{ret(rnd(f'total * {1 - p['d'] / 100}'))}", "magic_number")
            b.add(f"{I}{ret(rnd('total'))}")
        elif k == "cap":
            b.add(f"{I}if {a1} > {p['m']}:" + (f"  {cm} TODO: tope definido por gerencia" if todo else ""), "todo" if todo else None)
            b.add(f"{I*2}{ret(str(p['m']))}", "magic_number")
            b.add(f"{I}{ret(a1)}")
        elif k == "tier":
            b.add(f"{I}if {a1} > {p['l']}:" + (f"  {cm} TODO: tramo vigente?" if todo else ""), "todo" if todo else None)
            b.add(f"{I*2}{ret(rnd(f'{a1} * {p['r2'] / 100}'))}", "magic_number")
            b.add(f"{I}{ret(rnd(f'{a1} * {p['r1'] / 100}'))}")
        elif k == "grade":
            L = rule["labels"]
            b.add(f"{I}if {a1} >= {p['a']}:" + (f"  {cm} TODO: umbrales del reglamento" if todo else ""), "todo" if todo else None)
            b.add(f"{I*2}{ret(repr(L[0]))}", "magic_number")
            b.add(f"{I}if {a1} >= {p['b']}:")
            b.add(f"{I*2}{ret(repr(L[1]))}")
            b.add(f"{I}{ret(repr(L[2]))}")
        b.add(""); b.add("")
    else:
        # JavaScript, Java y Go comparten estructura con llaves.
        cm = "//"
        ret = lambda e: f"return ({e});" if par else f"return {e};"
        if lang == "go":
            ret = lambda e: f"return ({e})" if par else f"return {e}"
        if lang == "javascript":
            rnd = lambda e: f"Math.round(({e}) * 100) / 100"
        elif lang == "java":
            rnd = lambda e: f"Math.round(({e}) * 100.0) / 100.0"
        else:
            rnd = lambda e: f"math.Round(({e})*100) / 100"
        is_str = k == "grade"; is_int = k == "cap"
        if lang == "javascript":
            sig = f"function {camel(n)}({a1}" + (f", {a2}" if a2 else "") + ") {"
        elif lang == "java":
            t1 = "int" if is_int or k == "grade" else "double"
            t2 = "boolean" if k == "pct_flag" else "int"
            rt = "String" if is_str else ("int" if is_int else "double")
            sig = f"    public static {rt} {camel(n)}({t1} {camel(a1)}" + (f", {t2} {camel(a2)}" if a2 else "") + ") {"
        else:
            t1 = "int" if is_int or k == "grade" else "float64"
            t2 = "bool" if k == "pct_flag" else "int"
            rt = "string" if is_str else ("int" if is_int else "float64")
            sig = f"func {pascal(n)}({camel(a1)} {t1}" + (f", {camel(a2)} {t2}" if a2 else "") + f") {rt} {{"
        x1, x2 = (camel(a1), camel(a2) if a2 else None) if lang != "javascript" else (a1, a2)
        if lang == "go" and a2 and k == "bulk":
            mul = f"{x1} * float64({x2})"
        else:
            mul = f"{x1} * {x2}"
        I = "    " if lang != "java" else "        "
        I2 = I + "    "
        b.add(sig)
        open_if = lambda c: f"if ({c}) {{" if lang != "go" else f"if {c} {{"
        tcm = lambda txt: f"  {cm} TODO: {txt}" if todo else ""
        if k == "pct_flag":
            cond = (f"{x2} == true" if boolc else x2)
            b.add(I + open_if(cond) + tcm("aclarar esta regla de negocio"), "todo" if todo else None)
            if boolc: b.tags.append(("bool_compare", b.n))
            b.add(I2 + ret(rnd(f"{x1} * {p['p'] / 100}")), "magic_number")
            b.add(I + "}"); b.add(I + ret("0.0" if lang != "javascript" else "0"))
        elif k == "min_fee":
            b.add(I + open_if(f"{x1} < {p['t']}") + tcm("revisar recargo"), "todo" if todo else None)
            b.add(I2 + ret(rnd(f"{x1} + {lit(float(p['f']))}")), "magic_number")
            b.add(I + "}"); b.add(I + ret(rnd(x1)))
        elif k == "bulk":
            decl = {"javascript": "const total = ", "java": "double total = ", "go": "total := "}[lang]
            end = "" if lang == "go" else ";"
            b.add(I + decl + mul + end)
            b.add(I + open_if(f"{x2} >= {p['n']}") + tcm("confirmar volumen"), "todo" if todo else None)
            b.add(I2 + ret(rnd(f"total * {1 - p['d'] / 100}")), "magic_number")
            b.add(I + "}"); b.add(I + ret(rnd("total")))
        elif k == "cap":
            b.add(I + open_if(f"{x1} > {p['m']}") + tcm("tope definido por gerencia"), "todo" if todo else None)
            b.add(I2 + ret(str(p["m"])), "magic_number")
            b.add(I + "}"); b.add(I + ret(x1))
        elif k == "tier":
            b.add(I + open_if(f"{x1} > {p['l']}") + tcm("tramo vigente?"), "todo" if todo else None)
            b.add(I2 + ret(rnd(f"{x1} * {p['r2'] / 100}")), "magic_number")
            b.add(I + "}"); b.add(I + ret(rnd(f"{x1} * {p['r1'] / 100}")))
        elif k == "grade":
            L = rule["labels"]
            b.add(I + open_if(f"{x1} >= {p['a']}") + tcm("umbrales del reglamento"), "todo" if todo else None)
            b.add(I2 + ret(f'"{L[0]}"'), "magic_number")
            b.add(I + "}")
            b.add(I + open_if(f"{x1} >= {p['b']}"))
            b.add(I2 + ret(f'"{L[1]}"'))
            b.add(I + "}"); b.add(I + ret(f'"{L[2]}"'))
        b.add(("    }" if lang == "java" else "}")); b.add("")
    end = b.n
    while end > start and not b.lines[end - 1].strip():
        end -= 1
    return start, end


def camel(s):
    parts = s.split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:])


def pascal(s):
    return "".join(w.capitalize() for w in s.split("_"))


FILLER_PY = [
    ("formatear_moneda", ["def formatear_moneda(valor):", "    texto = '%.2f' % valor", "    return '$ ' + texto"]),
    ("validar_positivo", ["def validar_positivo(valor):", "    if valor < 0:", "        raise ValueError('valor negativo')", "    return valor"]),
    ("redondear", ["def redondear(valor):", "    return round(valor, 2)"]),
    ("normalizar_texto", ["def normalizar_texto(texto):", "    return texto.strip().upper()"]),
    ("promedio", ["def promedio(valores):", "    if len(valores) == 0:", "        return 0", "    return sum(valores) / len(valores)"]),
]


def render_filler(lang, b, idx, smells_file, tags_prefix):
    """Funciones auxiliares realistas, código muerto, duplicado, comentado y excepciones silenciadas."""
    base = ["formatear_moneda", "validar_positivo", "normalizar_texto", "promedio"][idx % 4]
    name = base + (f"_{idx // 4 + 1}" if idx >= 4 else "")
    if lang == "go":
        name += "_" + tags_prefix  # Go comparte un solo paquete entre archivos
    if lang == "python":
        body = list(dict(FILLER_PY)[base])
        body[0] = body[0].replace(base, name, 1)
        for l in body: b.add(l)
        b.add(""); b.add("")
    elif lang == "javascript":
        b.add(f"function {camel(name)}(v) {{"); b.add("    return v;"); b.add("}"); b.add("")
    elif lang == "java":
        b.add(f"    public static Object {camel(name)}(Object v) {{"); b.add("        return v;"); b.add("    }"); b.add("")
    else:
        b.add(f"func {pascal(name)}(v interface{{}}) interface{{}} {{"); b.add("    return v"); b.add("}"); b.add("")


def render_file_smells(lang, b, smells_file, mod):
    cm = "#" if lang == "python" else "//"
    if "dead_code" in smells_file:
        nm = f"calculo_antiguo_{mod}"
        if lang == "python":
            b.add(f"def {nm}(x):", "dead_code"); b.add("    # ya no se usa desde la migración de 2014"); b.add("    return x * 1.18"); b.add(""); b.add("")
        elif lang == "javascript":
            b.add(f"function {camel(nm)}(x) {{", "dead_code"); b.add("    // ya no se usa desde la migración de 2014"); b.add("    return x * 1.18;"); b.add("}"); b.add("")
        elif lang == "java":
            b.add(f"    private static double {camel(nm)}(double x) {{", "dead_code"); b.add("        // ya no se usa desde la migración de 2014"); b.add("        return x * 1.18;"); b.add("    }"); b.add("")
        else:
            b.add(f"func {camel(nm)}(x float64) float64 {{", "dead_code"); b.add("    // ya no se usa desde la migración de 2014"); b.add("    return x * 1.18"); b.add("}"); b.add("")
    if "duplicate" in smells_file:
        for suf in ("", "_v2"):
            nm = f"sumar_items{suf}" + (f"_{mod}" if lang == "go" else "")
            if lang == "python":
                b.add(f"def {nm}(items):", "duplicate"); b.add("    total = 0"); b.add("    for i in items:"); b.add("        total = total + i"); b.add("    return total"); b.add(""); b.add("")
            elif lang == "javascript":
                b.add(f"function {camel(nm)}(items) {{", "duplicate"); b.add("    let total = 0;"); b.add("    for (const i of items) { total = total + i; }"); b.add("    return total;"); b.add("}"); b.add("")
            elif lang == "java":
                b.add(f"    public static double {camel(nm)}(double[] items) {{", "duplicate"); b.add("        double total = 0;"); b.add("        for (double i : items) { total = total + i; }"); b.add("        return total;"); b.add("    }"); b.add("")
            else:
                b.add(f"func {pascal(nm)}(items []float64) float64 {{", "duplicate"); b.add("    total := 0.0"); b.add("    for _, i := range items {"); b.add("        total = total + i"); b.add("    }"); b.add("    return total"); b.add("}"); b.add("")
    if "commented_code" in smells_file:
        b.add(f"{cm if lang != 'java' else '    //'} versión anterior, no borrar por si acaso:", "commented_code")
        b.add(f"{cm if lang != 'java' else '    //'} total = total * 1.13")
        b.add(f"{cm if lang != 'java' else '    //'} if total > 99999: total = 99999"); b.add("")
    if "swallowed_exception" in smells_file:
        if lang == "python":
            b.add("def leer_config(ruta):"); b.add("    try:"); b.add("        return open(ruta).read()"); b.add("    except:", "swallowed_exception"); b.add("        pass"); b.add(""); b.add("")
        elif lang == "javascript":
            b.add("function leerConfig(ruta) {"); b.add("    try {"); b.add("        return require('fs').readFileSync(ruta, 'utf8');"); b.add("    } catch (e) {", "swallowed_exception"); b.add("    }"); b.add("}"); b.add("")
        elif lang == "java":
            b.add("    public static String leerConfig(String ruta) {"); b.add("        try {"); b.add("            return new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(ruta)));"); b.add("        } catch (Exception e) {", "swallowed_exception"); b.add("        }"); b.add("        return null;"); b.add("    }"); b.add("")
        else:
            b.add(f"func LeerConfig{pascal(mod)}(ruta string) string {{"); b.add("    data, err := os.ReadFile(ruta)"); b.add("    if err != nil {", "swallowed_exception"); b.add("    }"); b.add("    return string(data)"); b.add("}"); b.add("")


# ---------------------------------------------------------------- pruebas
def fmt_expected(lang, v):
    if isinstance(v, str):
        return f'"{v}"'
    return lit(v) if isinstance(v, float) else str(v)


def render_tests(lang, mod, rules, rng):
    out = []
    if lang == "python":
        out += ["import unittest", f"from {mod} import " + ", ".join(r["name"] for r in rules), "", "",
                f"class Prueba{pascal(mod)}(unittest.TestCase):"]
        for r in rules:
            out.append(f"    def test_{r['name']}(self):")
            for args, exp in rule_cases(rng, r):
                a = ", ".join(repr(x) for x in args)
                if isinstance(exp, float):
                    out.append(f"        self.assertEqual({r['name']}({a}), {exp!r})")
                else:
                    out.append(f"        self.assertEqual({r['name']}({a}), {exp!r})")
            out.append("")
        out += ["", "if __name__ == '__main__':", "    unittest.main()"]
    elif lang == "javascript":
        out += ["const test = require('node:test');", "const assert = require('node:assert');",
                f"const m = require('../src/{mod}');", ""]
        for r in rules:
            out.append(f"test('{r['name']}', () => {{")
            for args, exp in rule_cases(rng, r):
                a = ", ".join(("true" if x is True else "false" if x is False else repr(x)) for x in args)
                if isinstance(exp, float):
                    out.append(f"    assert.strictEqual(m.{camel(r['name'])}({a}), {exp!r});")
                else:
                    out.append(f"    assert.strictEqual(m.{camel(r['name'])}({a}), {fmt_expected(lang, exp)});")
            out.append("});")
            out.append("")
    elif lang == "java":
        cls = pascal(mod)
        out += [f"public class {cls}Test {{", "    static void ok(boolean c, String m) { if (!c) throw new AssertionError(m); }",
                "    public static void main(String[] args) {"]
        for r in rules:
            for args, exp in rule_cases(rng, r):
                a = ", ".join(("true" if x is True else "false" if x is False else (str(int(x)) if r["kind"] in ("cap", "grade") or (r["kind"] == "bulk" and i == 1) else repr(float(x)))) for i, x in enumerate(args))
                call = f"{cls}.{camel(r['name'])}({a})"
                if isinstance(exp, float):
                    out.append(f"        ok({call} == {exp!r}, \"{r['name']}\");")
                elif isinstance(exp, str):
                    out.append(f"        ok({call}.equals(\"{exp}\"), \"{r['name']}\");")
                else:
                    out.append(f"        ok({call} == {exp}, \"{r['name']}\");")
        out += ['        System.out.println("OK ' + cls + '");', "    }", "}"]
    else:
        out += ["package legacy", "", "import (", '    "math"', '    "testing"', ")", "", "var _ = math.Abs", ""]
        for r in rules:
            out.append(f"func Test{pascal(r['name'])}(t *testing.T) {{")
            for args, exp in rule_cases(rng, r):
                a = ", ".join(("true" if x is True else "false" if x is False else (str(int(x)) if r["kind"] in ("cap", "grade") or (r["kind"] == "bulk" and i == 1) else repr(float(x)))) for i, x in enumerate(args))
                call = f"{pascal(r['name'])}({a})"
                if isinstance(exp, float):
                    out.append(f"    if {call} != {exp!r} {{ t.Fatal(\"{r['name']}\") }}")
                else:
                    out.append(f"    if {call} != {fmt_expected(lang, exp)} {{ t.Fatal(\"{r['name']}\") }}")
            out.append("}")
            out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- proyecto
SIZES = {"chico": (1, 2, 0), "mediano": (2, 4, 2), "grande": (4, 8, 6)}
FILE_SMELLS = ["dead_code", "duplicate", "commented_code", "swallowed_exception"]
RULE_SMELLS = ["todo", "bool_compare", "redundant_parens"]


def generate(pid, dom, lang, size, rng, out_dir, meta_dir):
    nmods, nrules, nfill = SIZES[size]
    root = out_dir / pid
    if root.exists():
        shutil.rmtree(root)
    kinds = rng.sample(KINDS, min(nrules, len(KINDS)))
    while len(kinds) < nrules:
        kinds.append(rng.choice(KINDS))
    rules = [make_rule(rng, k, dom) for k in kinds]
    # nombres únicos si se repite el tipo
    seen = {}
    for r in rules:
        c = seen.get(r["name"], 0); seen[r["name"]] = c + 1
        if c:
            r["name"] = f"{r['name']}_{c + 1}"
    mods = [f"{dom}_{i + 1}" if nmods > 1 else dom for i in range(nmods)]
    per = [rules[i::nmods] for i in range(nmods)]
    truth = {"id": pid, "dominio": dom, "lenguaje": lang, "tamano": size, "reglas": [], "defectos": [], "archivos": []}
    files = {}
    for mi, (mod, mrules) in enumerate(zip(mods, per)):
        b = Buf()
        fsm = [s for s in FILE_SMELLS if rng.random() < 0.45]
        if lang == "python":
            b.add(f'"""Módulo {mod}. Sistema heredado, sin documentación actualizada."""'); b.add(""); b.add("")
            path = f"{mod}.py"
        elif lang == "javascript":
            b.add(f"// Módulo {mod}. Sistema heredado, sin documentación actualizada."); b.add("'use strict';"); b.add("")
            path = f"src/{mod}.js"
        elif lang == "java":
            b.add(f"// Módulo {mod}. Sistema heredado, sin documentación actualizada."); b.add(f"public class {pascal(mod)} {{"); b.add("")
            path = f"src/{pascal(mod)}.java"
        else:
            imports = ['"math"'] + (['"os"'] if "swallowed_exception" in fsm else [])
            b.add(f"// Módulo {mod}. Sistema heredado, sin documentación actualizada."); b.add("package legacy"); b.add("")
            b.add("import (")
            for im in imports: b.add("    " + im)
            b.add(")"); b.add(""); b.add("var _ = math.Round"); b.add("")
            path = f"{mod}.go"
        for r in mrules:
            sm = [s for s in RULE_SMELLS if rng.random() < 0.5]
            if r["kind"] == "pct_flag" and rng.random() < 0.7 and "bool_compare" not in sm:
                sm.append("bool_compare")
            tags_before = len(b.tags)
            s, e = render_rule(lang, b, r, sm)
            fn = r["name"] if lang == "python" else (pascal(r["name"]) if lang == "go" else camel(r["name"]))
            truth["reglas"].append({"funcion": fn, "archivo": path, "linea_inicio": s, "linea_fin": e,
                                    "tipo": r["kind"], "parametros": r["params"], "enunciado": r["statement"]})
            for tag, ln in b.tags[tags_before:]:
                if tag != "magic_number" or rng.random() < 0.0:
                    truth["defectos"].append({"tipo": tag, "archivo": path, "linea": ln})
                else:
                    truth.setdefault("numeros_magicos", []).append({"archivo": path, "linea": ln})
        for j in range(nfill if mi == 0 else max(0, nfill // 2)):
            render_filler(lang, b, j, fsm, mod)
        tb = len(b.tags)
        render_file_smells(lang, b, fsm, mod)
        for tag, ln in b.tags[tb:]:
            truth["defectos"].append({"tipo": tag, "archivo": path, "linea": ln})
        if lang == "java":
            b.add("}")
        if lang == "javascript":
            b.add("module.exports = { " + ", ".join(camel(r["name"]) for r in mrules) + " };")
        files[path] = "\n".join(b.lines).rstrip() + "\n"
        tpath = {"python": f"tests/test_{mod}.py", "javascript": f"test/{mod}.test.js",
                 "java": f"test/{pascal(mod)}Test.java", "go": f"{mod}_test.go"}[lang]
        files[tpath] = render_tests(lang, mod, mrules, rng)
    if lang == "javascript":
        files["package.json"] = json.dumps({"name": pid, "version": "0.0.1", "private": True,
                                            "scripts": {"test": "node --test"}}, indent=2) + "\n"
    elif lang == "java":
        tests = " ".join(f"{pascal(m)}Test" for m in mods)
        files["run_tests.sh"] = ("set -e\nrm -rf out && mkdir -p out\njavac -d out src/*.java test/*.java\n"
                                 f"for t in {tests}; do java -cp out $t; done\n")
    elif lang == "go":
        files["go.mod"] = "module legacy\n\ngo 1.22\n"
    elif lang == "python":
        files["requirements.txt"] = "# sin dependencias externas\n"
    files["README.md"] = (f"# Sistema de {dom}\n\nSistema heredado. La documentación original se perdió.\n"
                          "Algunas reglas dependen de acuerdos verbales con el área de negocio.\n")
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    truth["archivos"] = sorted(files)
    truth["lineas_codigo"] = sum(c.count("\n") for p_, c in files.items() if not p_.endswith(".md"))
    (meta_dir / f"{pid}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="datos")
    ap.add_argument("--train", type=int, default=60)
    ap.add_argument("--valid", type=int, default=10)
    ap.add_argument("--test", type=int, default=10)
    ap.add_argument("--semilla", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.semilla)
    base = Path(a.salida)
    train_doms = [d for d in DOMINIOS if d not in DOMINIOS_PRUEBA]
    resumen = {}
    for split, count in (("train", a.train), ("valid", a.valid), ("test", a.test)):
        out = base / "proyectos" / split; meta = base / "meta" / split
        out.mkdir(parents=True, exist_ok=True); meta.mkdir(parents=True, exist_ok=True)
        resumen[split] = []
        for i in range(count):
            lang = LANGS[i % len(LANGS)]
            size = ["chico", "mediano", "grande"][(i // len(LANGS)) % 3]
            if split == "test":
                dom = sorted(DOMINIOS_PRUEBA)[i % 2] if i % 2 == 0 else rng.choice(sorted(DOMINIOS_PRUEBA))
            else:
                dom = rng.choice(train_doms)
            pid = f"{split}-{i:03d}-{lang}-{dom}"
            t = generate(pid, dom, lang, size, rng, out, meta)
            resumen[split].append({"id": pid, "lenguaje": lang, "tamano": size, "reglas": len(t["reglas"]),
                                   "defectos": len(t["defectos"]), "lineas": t["lineas_codigo"]})
    (base / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    for s, items in resumen.items():
        print(s, len(items), "proyectos;", sum(x["lineas"] for x in items), "líneas;",
              sum(x["reglas"] for x in items), "reglas;", sum(x["defectos"] for x in items), "defectos")


if __name__ == "__main__":
    main()
