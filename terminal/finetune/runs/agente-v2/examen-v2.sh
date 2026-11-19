#!/usr/bin/env bash
set -u
T=~/Projects/legacy-lift-real/terminal; PY=~/Projects/software1-final/ml/.venv/bin/python
M=~/.lmstudio/models/lmstudio-community/Qwen3-4B-Instruct-2507-MLX-4bit
while pgrep -f "config-agente-v2" >/dev/null; do sleep 60; done
OUT=$T/finetune/runs/agente-v2/examen; mkdir -p $OUT
nohup $PY -m mlx_lm server --model $M --adapter-path $T/finetune/runs/agente-v2/adapters --host 127.0.0.1 --port 8080 > $OUT/server.log 2>&1 &
sleep 25
PROXY_PORT=11630 LEGACYLIFT_BIN=/tmp/legacylift $T/finetune/grabar.sh $T/finetune/datos test $OUT/trabajo http://localhost:8080 $M $OUT/v2.jsonl > $OUT/progreso.txt 2>&1
python3 $T/finetune/evaluar.py --datos $T/finetune/datos --split test --trabajo $OUT/trabajo --salida $OUT/eval-v2.json > $OUT/resumen.txt
pkill -f "mlx_lm server"; echo FIN
