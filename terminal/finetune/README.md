# Fine-tuning de la IA local de LegacyLift

Objetivo: que un modelo local chico (Qwen3-4B) ejecute las 7 fases en **modo agente reducido**
(`legacylift --mode agent`) con calidad cercana a un modelo grande.

## Fase B — Proyectos de práctica con verdad conocida

```bash
python3 finetune/generar_proyectos.py --salida datos --train 60 --valid 10 --test 10 --semilla 7
```

- 80 proyectos legacy en Python, JavaScript, Java y Go (chicos, medianos y grandes).
- Reglas de negocio parametrizadas y defectos sembrados (TODO, `== True`, paréntesis redundantes,
  código muerto, duplicación, código comentado, excepciones silenciadas).
- Pruebas exactas (detectan, por ejemplo, que se quite un redondeo).
- La verdad conocida queda en `datos/meta/<split>/<id>.json`, **fuera** del proyecto.
- Los dominios `biblioteca` y `envios` solo aparecen en `test` (generalización).

## Evaluación automática (sin otra IA)

```bash
python3 finetune/evaluar.py --datos datos --split valid --trabajo <carpeta-con-copias> --salida eval.json
```

Mide: reglas encontradas, defectos encontrados, citas `archivo:línea` válidas, español,
original intacto, Modernize con cambios reales y pruebas que pasan, y honestidad de Verify.

## Fase C — Ejemplos de entrenamiento (gratis, sin IA externa)

1. **Profesor con guion** (`profesor_guion.py`): servidor compatible con OpenAI que, con la verdad
   conocida, decide qué herramienta usar y qué escribir. La terminal real ejecuta las herramientas.
   ```bash
   python3 finetune/profesor_guion.py --datos finetune/datos --puerto 8090 &
   finetune/grabar.sh finetune/datos train /tmp/trabajo http://localhost:8090 profesor finetune/datos/grabaciones/profesor-train.jsonl
   python3 finetune/a_dataset.py --entrada finetune/datos/grabaciones/profesor-train.jsonl --salida finetune/datos/dataset/train.jsonl
   ```
   Resultado: 60 proyectos con nota 100 % en reglas, defectos, citas y pruebas; 2 617 ejemplos de
   entrenamiento + 416 de validación (máx. 7 126 tokens).
2. **Generación nocturna con el 9B** (`noche.sh`, muestreo por rechazo): el 9B hace las 7 fases y
   solo se guardan los proyectos que aprueban `evaluar.py`. Aporta variedad de redacción.
   ```bash
   finetune/noche.sh finetune/datos finetune/datos/noche
   ```

## Resultados (10 proyectos de prueba)

| Métrica | 4B base | v1 | v2 |
|---|---|---|---|
| Fases completas | 6,8 | 6,6 | 6,4 |
| Reglas | 0,95 | 0,94 | 0,85 |
| Defectos | 0,84 | 0,84 | 0,94 |
| Citas | 0,97 | 0,97 | 0,87 |
| Original intacto | 1,0 | 0,8 | 1,0 |
| Modernize cambió | 0,8 | 0,6 | 0,7 |
| Pruebas pasan | 0,7 | 1,0 | 0,7 |

Conclusión: ni v1 ni v2 superan al modelo base. La mejora real vino del bloqueo de escritura y del diseño por fases. Una corrida repetida del 4B base dio 0,79 en reglas, así que las diferencias de ±0,1 con 10 proyectos están dentro del ruido.
