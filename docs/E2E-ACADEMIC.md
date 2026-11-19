# Pruebas E2E académicas (HTTP, navegador e IA real)

Este conjunto prueba un recorrido de LegacyLift contra **FastAPI real**, una base SQLite temporal, **un worker de migración separado** y, opcionalmente, Angular/Playwright o un proveedor de IA real. No hace llamadas directas a funciones internas para simular las etapas.

## Qué se comprueba

1. Registro y sesión; creación de proyecto.
2. Ingesta ZIP real, job asíncrono, snapshot e inventario.
3. Discover, Understand, Assess y Plan por HTTP, con artefactos.
4. Revisión y aprobación explícita del Blueprint y su primera wave Refactor.
5. Baseline y consentimiento opcional para pruebas Python en Docker.
6. Modernize en un proceso worker separado; código realmente cambiado, diff y selección.
7. Verify en el mismo worker; clasificación conforme a las pruebas ejecutadas.
8. Aprobación, entrega ZIP, patch, reporte y conservación del origen.
9. En Playwright: inicio de sesión real, creación de proyecto desde la UI, importación de ejemplo, pestaña Discover y consola Sprint 5.

La suite determinista **no envía código ni prompts a proveedores externos** y es obligatoria en CI. La suite de IA real solo se ejecuta si el operador la habilita expresamente.

## Preparación (Linux/Zorin)

Desde la raíz del repositorio:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q tests/e2e/test_academic_journey.py -k demo
```

**No levantes FastAPI en el puerto 8000** mientras ejecutas la suite: pytest crea su propio servidor y una base aislada en un directorio temporal. Cambia `E2E_PORT` para usar otro puerto si solo pruebas la API.

Para ejecutar todo el backend: `python -m pytest -q tests --ignore=tests/e2e`.

## Navegador Playwright

En la virtualenv del backend:

```bash
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

En otra terminal, desde `frontend/`, ejecuta `npm ci && npm start`. Con Angular en `http://127.0.0.1:4200`, vuelve a la terminal de `backend/`:

```bash
E2E_BROWSER=1 python -m pytest -q tests/e2e/test_browser_smoke.py
```

Angular tiene proxy `/api` configurado para el puerto **8000**. La prueba comprueba login, creación, ejemplo de código y consola de organizaciones. Guarda capturas en `backend/test-results/e2e` por defecto o en `E2E_ARTIFACTS`.

## IA real con tu cuenta o API key

La prueba de IA no usa mocks. Se ejecutan llamadas reales durante Understand, Assess, Plan y Modernize, por lo que **puede consumir tokens/cuota y puede fallar por límites, conectividad, prompts o salidas del modelo**. Ejecuta la prueba solo con autorización y un repositorio de ejemplo sin secretos.

### Codex CLI (sesión de ChatGPT del host)

Instala Codex y autentícate en el equipo **en el que se ejecutan las pruebas**:

```bash
npm install -g @openai/codex
codex login
cd backend
E2E_AI_PROVIDER=codex python -m pytest -q tests/e2e/test_academic_journey.py -k real_ai -s
```

El worker separado utiliza las credenciales de Codex CLI del host; jamás se copian al repositorio. El script comprueba que el binario exista, pero la autenticación efectiva la valida Codex durante la ejecución.

### Claude (Anthropic Messages API)

```bash
cd backend
export CLAUDE_API_KEY="TU_API_KEY"
# Si tu cuenta necesita otro modelo, configura CLAUDE_MODEL con un identificador válido.
E2E_AI_PROVIDER=claude python -m pytest -q tests/e2e/test_academic_journey.py -k real_ai -s
```

**La suscripción Claude/Claude Code no equivale a una API key** para el adaptador actual. Por eso la suite no intenta usar ni extraer credenciales de Claude Code.

Las ejecuciones reales fallan visiblemente si el proveedor no está disponible, si no produce cambios, o si las verificaciones divergen. Nunca se declaran exitosas sin evidencia.

## Verificación dinámica con unittest y Docker (opcional)

En Linux con Docker funcionando:

```bash
docker pull python:3.12-alpine
cd backend
E2E_DOCKER=1 python -m pytest -q tests/e2e/test_academic_journey.py -k demo
```

La prueba crea un sistema Python pequeño con una suite `unittest`, ejecuta el baseline y Verify con esa suite dentro de Docker. En este modo exige clasificación `verified_within_tests`. Sin Docker, la clasificación esperada es `not_demonstrated` y la aprobación incluye la justificación del riesgo, sin afirmar equivalencia.

Se puede combinar `E2E_AI_PROVIDER=codex` (o `claude`) con `E2E_DOCKER=1` para probar que un cambio generado por IA **conserva las pruebas originales**. Si falla, la prueba E2E falla deliberadamente.

## CI y límites

El workflow [Academic E2E](../.github/workflows/academic-e2e.yml) se ejecuta al hacer push a `test/e2e-academic-ai` y en PRs a `main`. Ejecuta el recorrido demo, toda la suite backend y la prueba del navegador; sube capturas de Playwright como artefacto. No usa secretos de IA automáticamente.

Esto comprueba un caso académico controlado, no la capacidad de modernizar cualquier stack ni la equivalencia universal de programas.

**Nota:** `docs/SPRINT5-GUIA.md` y algunas instrucciones anteriores pueden referirse a formas antiguas de lanzar los tests; esta guía es la referencia para la nueva suite E2E.
