#!/usr/bin/env bash
set -u
T=~/Projects/legacy-lift-real/terminal; PY=~/Projects/software1-final/ml/.venv/bin/python
M=~/.lmstudio/models/lmstudio-community/Qwen3-4B-Instruct-2507-MLX-4bit
O=$T/finetune/runs/recomendada; cd $T
nohup $PY -m mlx_lm server --model $M --host 127.0.0.1 --port 8080 > $O/server-4b.log 2>&1 &
sleep 25
PROXY_PORT=11640 LEGACYLIFT_BIN=/tmp/legacylift finetune/grabar.sh finetune/datos test $O/trabajo-4b http://localhost:8080 $M $O/4b.jsonl > $O/progreso-4b.txt 2>&1
pkill -f "mlx_lm server"
python3 finetune/evaluar.py --datos finetune/datos --split test --trabajo $O/trabajo-4b --salida $O/eval-4b.json > $O/resumen-4b.txt
for f in 000 001 009; do
  PROXY_PORT=11641 LEGACYLIFT_RUN_MODE=lite LEGACYLIFT_BIN=/tmp/legacylift finetune/grabar.sh finetune/datos test $O/trabajo-9b http://localhost:11434 qwen3.5:9b $O/9b.jsonl "test-$f" >> $O/progreso-9b.txt 2>&1
done
python3 finetune/evaluar.py --datos finetune/datos --split test --trabajo $O/trabajo-9b --salida $O/eval-9b.json > $O/resumen-9b.txt
echo FIN
