# LegacyLift AI — Terminal (TUI en Go)

Fork de [Crush](https://github.com/charmbracelet/crush) adaptado a LegacyLift AI.
**No es un chat:** no se escriben prompts. Al abrirlo ejecuta automáticamente las fases documentadas
sobre la carpeta actual y guarda cada resultado como un archivo Markdown en `.legacylift/`.

| # | Fase | Artefacto |
|---|---|---|
| 01 | Discover | `.legacylift/01-discover.md` |
| 02 | Understand | `.legacylift/02-understand.md` |
| 03 | Assess | `.legacylift/03-assess.md` |
| 04 | Plan | `.legacylift/04-plan.md` |
| 05 | Baseline | `.legacylift/05-baseline.md` |
| 06 | Modernize | `.legacylift/06-modernize.md` (cambios solo en `.legacylift/candidate/`) |
| 07 | Verify | `.legacylift/07-verify.md` |

- **El código original nunca se modifica**: Modernize trabaja sobre una copia en `.legacylift/candidate/`.
- **Independiente del backend**: no necesita el servidor FastAPI ni la base de datos; solo una carpeta con código.
- Las instrucciones de cada fase están en `internal/legacylift/phases/*.md` (se pueden editar).

## Compilar

Requiere Go 1.27+.

```bash
cd terminal
go build -o legacylift .
```

## Usar con IA local (Ollama) — recomendado

```bash
ollama serve                 # en otra terminal
ollama pull qwen3.5:9b       # o cualquier modelo con soporte de herramientas
cd /ruta/al/sistema-legado
/ruta/a/legacylift           # detecta Ollama y crea crush.json automáticamente
```

Elegir modelo: `legacylift --model ollama/qwen3.5:9b` o `LEGACYLIFT_MODEL=qwen3.5:9b legacylift`.

## Modo liviano (automático con Ollama)

Los modelos locales chicos (4B–9B) no manejan bien el prompt largo de Crush ni sus 30+ herramientas.
Con Ollama se activa el **modo liviano**: el programa lee el código (con números de línea), corre las
pruebas y calcula el diff, y la IA solo analiza y escribe cada informe. En Modernize la IA devuelve el
archivo completo y el programa lo guarda solo en `.legacylift/candidate/`.
Forzar: `--lite` (también con nube) o `--full` (modo agente completo).

Prueba real con `qwen3.5:9b` (Apple M5, 16 GB) sobre `demo-source`: las 7 fases completas en ~30 min;
Understand encontró las 3 reglas de negocio, Modernize refactorizó `calculator.py` y Verify confirmó
que las 3 pruebas pasan sobre la candidata.

## Qué modelo local usar (probado)

Probado sobre 10 proyectos sintéticos de prueba (Python, JS, Java, Go) con evaluación automática (`finetune/evaluar.py`):

| | Rápido: `qwen3:4b`, `--mode agent` | Calidad: `qwen3.5:9b`, `--mode lite` |
|---|---|---|
| RAM | 8 GB | 16 GB |
| Tiempo por proyecto | 5–11 min | 30–60 min |
| Fases completas (de 7) | 6,4 | 7,0 |
| Reglas de negocio | 0,79 | 1,0 |
| Defectos detectados | 0,90 | 0,82 |
| Citas `archivo:línea` válidas | 0,96 | 0,66 |
| Original intacto | 1,0 | 1,0 |
| Modernize cambió la copia | 0,6 | 0,67 |
| Pruebas de la copia pasan | 0,7 | 1,0 |

- Para trabajar a diario o en laptops modestas: `legacylift --model qwen3:4b-instruct-2507-q4_K_M --mode agent` (instalar antes con `ollama pull qwen3:4b-instruct-2507-q4_K_M`; evita `qwen3:4b`, que «piensa» antes de responder y es mucho más lento).
- Para la demo o un resultado final: `legacylift --model qwen3.5:9b --mode lite`.
- En ambos casos el bloqueo de escritura impide modificar el código original: Modernize solo edita `.legacylift/candidate/`.
- Detector de datos inventados: tras Baseline y Verify, LegacyLift ejecuta los ejemplos que cita el informe (p. ej. `total_final(50, 2, True) → 95`) contra el código real y agrega una tabla «Comprobación automática de ejemplos» que marca ❌ los valores que no coinciden. Por ahora solo en proyectos Python; los argumentos se leen como literales, nunca se ejecuta código del informe.
- Veredicto del sistema: al final de Verify, LegacyLift (no la IA) corre las pruebas en el original y en la candidata, ejecuta los ejemplos de los informes en ambas versiones y escribe una tabla «Antes vs. después» con un veredicto: ✅ respaldada por pruebas, ⚠️ no demostrada o ❌ no equivalente.
- Si en Modernize las pruebas pasaban en el original y fallan con los cambios, el sistema los aparta en `.legacylift/candidate-rechazada/` y restaura la candidata.
- El fine-tuning de un 4B (`finetune/`) se dejó como experimento: no superó al modelo base (ver `finetune/README.md`).

## Usar con proveedores en la nube

Funcionan todos los proveedores de Crush (Anthropic, OpenAI, OpenRouter, Gemini, Groq, …):

```bash
export ANTHROPIC_API_KEY=...
legacylift --model anthropic/claude-sonnet-5-5
```

También se pueden configurar en `crush.json` del proyecto.

## Opciones

```bash
legacylift --from understand --until plan   # solo algunas fases
legacylift --plain                          # salida en texto (sin TUI), útil en CI
legacylift --cwd /ruta/al/proyecto          # otra carpeta
legacylift phases                           # lista las fases
```

En la pantalla: `q` cancela. No hay campo para escribir.

## Qué se cambió respecto de Crush

- `internal/legacylift/`: fases, ejecutor, pantalla (TUI) y configuración automática de Ollama.
- `internal/cmd/phases.go`: el comando principal ejecuta las fases en lugar del chat.
- `internal/cmd/root.go`: se quitó el chat interactivo y el comando `run` (prompts libres).

## Licencia

Crush se distribuye bajo **FSL-1.1-MIT** (ver `LICENSE.md`), que se conserva en este fork.
Permite uso, modificación y fines académicos; no permite ofrecerlo como producto que compita con Crush.
Cada versión pasa a MIT dos años después de publicada.
