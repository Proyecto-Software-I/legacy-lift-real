# Cumplimiento de `product.md` — verificación funcional

**Fecha:** 19 de noviembre de 2026 · **Alcance:** MVP académico (§4). Lo marcado "fuera del alcance de examen" en §4 no se exige; Organizaciones (Sprint 5) existe como extra y no bloquea el flujo.

**Cómo se verificó:** suite backend (`pytest tests --ignore=tests/e2e`, 63 tests), E2E académico demo en modo estático y con Docker (`E2E_DOCKER=1`, pruebas reales), E2E de navegador y un recorrido completo por la interfaz con Playwright. El E2E con Codex real no pudo completarse: la cuenta de ChatGPT tenía el límite de uso agotado (en `gpt-6.1-sol` y en `gpt-6.1-terra`) hasta las 19:31. El recorrido completo con **IA real se completó con Gemini** (`E2E_AI_PROVIDER=gemini E2E_DOCKER=1`, modelo `gemini-4-hydrogen-medium` mediante Antigravity CLI con cuenta de Google): Understand, Assess y Plan generados por el modelo, Modernize en el worker, Verify `verified_within_tests` y aprobación humana.

## Matriz

| Requisito (product.md) | Estado | Evidencia |
|---|---|---|
| §2.1 Discover: snapshot, inventario, lenguajes, módulos, **frameworks, dependencias y puntos de entrada**; progreso y errores visibles | Cumple (corregido) | `scanner.py` detecta frameworks, dependencias externas (manifiestos e imports), puntos de entrada (`__main__`, programas COBOL que nadie llama) y complejidad. Se muestran en `inventory.md` y en la pantalla Discover. |
| §2.2 Understand: arquitectura, **grafo de conocimiento mínimo**, reglas con referencias, hipótesis marcadas, documentación (visión, mapa de módulos, reglas) | Cumple (corregido) | `understand.json.knowledge_graph` (módulos, archivos, puntos de entrada y reglas, con aristas CONTAINS / DEPENDS_ON / EVIDENCED_BY). `understand.md` incluye visión técnica, mapa de módulos con relaciones, puntos de entrada y reglas marcadas como hipótesis. |
| §2.3 Assess: findings con severidad, ubicación y evidencia; **complejidad, deuda, dependencias, mantenibilidad, riesgo y pruebas**; estrategias Keep…Retire | Cumple (corregido) | Nuevos hallazgos COMPLEX_FUNCTION, LONG_FUNCTION y COMPLEX_FILE; métricas de dependencias externas, complejidad y mantenibilidad; vocabulario completo de 10 estrategias en el prompt (`assess.md v3`) y en la validación. |
| §2.4 Plan: Blueprint con **arquitectura actual y objetivo**, hallazgos vinculados, archivos y criterios; waves; aprobación; pack; baseline | Cumple (corregido) | Blueprint `schema_version 2`: `current_architecture`, `related_findings` por wave (enlace determinista), `origin` (IA / estático / manual, fijado por el servidor). |
| §2.4 Baseline: registrar explícitamente las carencias de verificación | Cumple (corregido) | `baseline.gaps()` y sección "Carencias de verificación" en `baseline.md`; lint incluido. |
| §2.5 Modernize: copia aislada, diff por archivo, trazabilidad plan → wave → pack → cambios | Cumple (ampliado) | Intentos inmutables (`migration_attempts`) y diff por archivo, con procedencia (proveedor, modelo, plantilla y motor real). Segundo pack ejecutable `cobol-to-java@1.0`: traducción a Java 17 con IA, compilación obligatoria y archivos nuevos aislados en `java/`. |
| §2.6 *Dual run* equivalente cuando el caso lo permite | Cumple (nuevo) | Para `cobol-to-java`, Baseline ejecuta el COBOL original con GnuCOBOL y Verify ejecuta el Java en el mismo contenedor sin red; compara la salida. La salida copiada literalmente no cuenta como evidencia. |
| §2.6 Verify: **build, lint y tests**; comparación con el baseline; **Confidence Score orientativo** que distingue aprobado, fallido y sin evidencia | Cumple (corregido) | Antes el score era siempre `null` y no había lint. Ahora: lint AST, `outcome` (aprobado / fallido / sin_evidencia) y score de 0 a 100 desglosado en build (20), lint (10), pruebas comparables (50) y cobertura de cambios por tests (20). Si el resultado es divergente, el score es 0. |
| §9 **Dos decisiones humanas diferenciadas** (plan y resultado) | Cumple (corregido) | Se eliminó la auto-aprobación de Verify. Con pruebas superadas, aprobar es un clic sin motivo obligatorio; sin evidencia suficiente se exige un motivo de 20 o más caracteres; si el resultado es divergente, no se puede aprobar. |
| §2.6 Rechazar → rollback / reintentar | Cumple (mejorado en la UI) | Al rechazar se descarta el candidato y el original queda intacto; la UI ofrece "Solicitar otra iteración". |
| §9 Registrar **modelo, plantilla y snapshot** de cada artefacto | Cumple (corregido) | Nueva columna `phase_runs.model`, `provenance` en los datos de cada ejecución, sección "Procedencia" en cada `.md` e indicador en cada paso de la UI. |
| §5 "El proveedor demo… no simula una IA"; no presentar como validado lo que no lo está | Cumple (corregido) | Cuando la IA agotaba la cuota, Understand, Assess y Baseline **fabricaban** reglas, recomendaciones y casos atribuidos al modelo. Ahora no se inventa nada: se registra el motor real usado (por ejemplo, "análisis estático (cuota agotada)") y se muestra como aviso. |
| §4 Reintentos por fase, progreso visible, prompts automáticos, descarga de artefactos | Cumple | Sin cambios. |
| §4 / §9 HTTPS + IPv6 desde otra red | No verificado en esta sesión | Existe configuración (`deploy/`, `firebase.json`), pero no se probó la conectividad. |

## Proveedores de IA
Codex CLI (cuenta de ChatGPT), **Gemini mediante Antigravity CLI (cuenta de Google / Google AI Pro)**, Claude API y Demo sin IA. Gemini se ejecuta sin poder editar ni ejecutar comandos (`--mode=plan`, `--sandbox`, permisos `request-review` en modo headless) y sobre una copia temporal de solo lectura de los archivos seleccionados.

## Pendiente
- E2E con Codex real: `E2E_AI_PROVIDER=codex CODEX_MODEL=gpt-6.1-sol pytest tests/e2e/test_academic_journey.py -k real_ai` cuando se renueve la cuota.
- `frontend/public/app-config.js` versionado apunta a la API remota; el E2E de navegador del CI depende de ese valor.
