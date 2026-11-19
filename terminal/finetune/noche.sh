#!/usr/bin/env bash
# Generación nocturna: el 9B local hace las 7 fases en los proyectos de entrenamiento
# (modo agente) y solo se guardan los proyectos que aprueban la evaluación automática.
# Deja la laptop ENCHUFADA. caffeinate evita que se suspenda.
#
# Uso: finetune/noche.sh <datos> <salida> [modelo_ollama]
set -euo pipefail
DATOS="$1"; SAL="$2"; MODELO="${3:-qwen3.5:9b}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SAL"
ollama list >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &) ; sleep 2
caffeinate -dims "$AQUI/grabar.sh" "$DATOS" train "$SAL/trabajo" http://localhost:11434 "$MODELO" "$SAL/9b.jsonl" | tee "$SAL/progreso.txt"
python3 "$AQUI/evaluar.py" --datos "$DATOS" --split train --trabajo "$SAL/trabajo" --salida "$SAL/9b-eval.json"
python3 "$AQUI/a_dataset.py" --entrada "$SAL/9b.jsonl" --salida "$SAL/9b-aprobados.jsonl" --eval "$SAL/9b-eval.json"
echo "Listo: $(date)"
