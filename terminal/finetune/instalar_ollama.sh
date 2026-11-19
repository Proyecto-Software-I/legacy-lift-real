#!/usr/bin/env bash
# Fusiona el adaptador LoRA con el modelo base y lo instala en Ollama como legacylift-qwen4b.
# Uso: finetune/instalar_ollama.sh <carpeta_corrida> [nombre]
set -euo pipefail
RUN="$1"; NOMBRE="${2:-legacylift-qwen4b}"
PY="${PY:-$HOME/Projects/software1-final/ml/.venv/bin/python}"
BASE="${BASE:-$HOME/.lmstudio/models/lmstudio-community/Qwen3-4B-Instruct-2507-MLX-4bit}"
FUSED="$RUN/fused"
rm -rf "$FUSED"
"$PY" -m mlx_lm fuse --model "$BASE" --adapter-path "$RUN/adapters" --save-path "$FUSED" --dequantize
cat > "$RUN/Modelfile" <<EOF
FROM ./fused
PARAMETER temperature 0
PARAMETER num_ctx 16384
EOF
(cd "$RUN" && ollama create "$NOMBRE" -f Modelfile --quantize q4_K_M)
ollama show "$NOMBRE" | head -12
