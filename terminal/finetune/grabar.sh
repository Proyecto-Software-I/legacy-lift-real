#!/usr/bin/env bash
# Corre la terminal LegacyLift (modo agente) sobre copias de los proyectos de un split,
# pasando por el proxy que graba cada conversación.
#
# Uso: finetune/grabar.sh <datos> <split> <trabajo> <url_modelo> <id_modelo> <salida.jsonl> [filtro]
#   url_modelo: servidor compatible con OpenAI (profesor_guion.py, ollama, mlx_lm.server)
set -euo pipefail
DATOS="$1"; SPLIT="$2"; TRABAJO="$3"; UPSTREAM="$4"; MODELO="$5"; SALIDA="$6"; FILTRO="${7:-}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
BIN="${LEGACYLIFT_BIN:-legacylift}"
PUERTO="${PROXY_PORT:-11600}"
mkdir -p "$TRABAJO"
python3 "$AQUI/../scripts/legacylift_proxy.py" --listen "$PUERTO" --upstream "$UPSTREAM" --out "$SALIDA" >/dev/null 2>&1 &
PROXY=$!
trap 'kill $PROXY 2>/dev/null' EXIT
sleep 1
for src in "$DATOS/proyectos/$SPLIT"/*${FILTRO}*; do
  id="$(basename "$src")"
  dst="$TRABAJO/$id"
  rm -rf "$dst"; cp -R "$src" "$dst"
  cat > "$dst/crush.json" <<EOF
{"providers":{"local":{"name":"Local","type":"openai-compat","base_url":"http://localhost:$PUERTO/v1/",
  "models":[{"id":"$MODELO","name":"$MODELO","context_window":32768,"default_max_tokens":4096}]}},
 "models":{"large":{"provider":"local","model":"$MODELO"},"small":{"provider":"local","model":"$MODELO"}},
 "options":{"progress":false,"request_timeout":900}}
EOF
  inicio=$(date +%s)
  if (cd "$dst" && "$BIN" --plain --mode "${LEGACYLIFT_RUN_MODE:-agent}" > "$dst.log" 2>&1); then estado=ok; else estado=error; fi
  echo "$id $estado $(( $(date +%s) - inicio ))s"
done
