# LegacyLift AI — MVP académico

> **Understand first. Modernize safely.**

Aplicación web funcional para analizar y modernizar un repositorio pequeño. Usa **Angular + FastAPI**,
SQLite y adaptadores de IA intercambiables: **Codex CLI** (cuenta ChatGPT autorizada en el host),
**Gemini** vía Antigravity CLI (cuenta de Google, p. ej. Google AI Pro, autorizada en el host),
**Claude** (API oficial de Anthropic) y **demo local sin IA**. La interfaz negra/tech implementa
primitivas visuales de tipo shadcn/ui (Card, Button, Badge, Dialog, Progress, Tabs) en Angular;
**no utiliza el paquete shadcn/ui React**, que no es compatible directamente con Angular.

## Qué sprints cubre

| Sprint | Implementación en este MVP |
|---|---|
| **1 — Ingest** | Usuarios y sesiones revocables (Sprint 1), proyectos, fuente ZIP, snapshot hash, progreso de tareas |
| **2 — Understand** | Discover determinista, Understand/Assess mediante IA o demo honesta, artefactos Markdown |
| **3 — Plan & Prepare** | Blueprint, una wave, autorización explícita, baseline y comprobaciones |
| **4 — Modernize Safely** | Refactor limitado sobre copia, diff, verificación, aprobación/rollback y exportación |
| **5 — Productize** | Organizaciones, roles, invitaciones, auditoría, reportes y cuotas locales. Facturación y workers distribuidos siguen pendientes. |

**Importante:** hay una diferencia entre tener el *flujo implementado* y tener una garantía de modernización segura.
La versión académica no asegura equivalencia sin tests comparables, ni admite todas las tecnologías.
Hay dos Migration Packs ejecutables:

- `safe-refactor@1.0`: cambios limitados dentro del lenguaje existente (Python, JS/TS, Java, C#, Go, COBOL).
- `cobol-to-java@1.0`: traduce programas COBOL a **Java 17** con IA (Codex, Gemini o Claude; no en modo demo).
  El COBOL original se conserva; el Java debe compilar (`javac -proc:none`) y Verify hace un **dual run**:
  ejecuta el COBOL original (GnuCOBOL) y el Java en un contenedor sin red y compara su salida. Si la salida
  esperada aparece escrita literalmente en el Java, no cuenta como evidencia. Requiere `LEGACYLIFT_WORKER_MODE=docker-local`,
  autorizar la ejecución en Baseline y construir una vez la imagen del runner:
  `docker build -t legacylift/legacy-runner:1 docker/legacy-runner`.
  Para usarlo, en Plan elige «Migrar COBOL a Java» para la wave con los programas `.cbl`.

## Requisitos

- Python 3.11+ y Node.js compatible con Angular 22 (p.ej. Node 22 LTS actualizado).
- npm, conexión para instalar dependencias.
- Opcional ChatGPT: `npm install -g @openai/codex` y luego **`codex login`** en la computadora anfitriona.
- Opcional Gemini (Google AI Pro): instala **Antigravity CLI** (`agy`), ejecútalo una vez e inicia sesión con tu cuenta de Google.
  `agy models` lista los modelos de tu plan; elige uno con `GEMINI_MODEL` (por defecto `gemini-4-hydrogen-medium`).
- Opcional Claude: una **Anthropic API key** en `CLAUDE_API_KEY` (no equivale a iniciar sesión con la suscripción Claude).
- Opcional Docker: para ejecutar `unittest` Python con red desactivada y filesystem montado solo lectura.

## Inicio rápido — Windows PowerShell

```powershell
# En la raíz del proyecto
Copy-Item .env.example .env
# Genera LEGACYLIFT_BOOTSTRAP_SECRET de 24+ caracteres y pégalo en .env antes de registrarte.
# El primer registro requiere ese código de instalación; REGISTRATION=false bloquea altas adicionales.

cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

En **otra terminal**:

```powershell
cd frontend
npm install
npm start
```

Abre **http://localhost:4200**. Registra el primer usuario con email, contraseña de 12+ caracteres y el código de instalación de `.env`.
Si usas Codex, instala/autentica el CLI **antes** de iniciar el backend y selecciona
`ChatGPT / Codex CLI` al crear el proyecto. El backend no necesita API key de OpenAI.
Con Gemini es igual: inicia sesión en `agy` antes del backend y elige `Gemini / Google AI Pro`.
LegacyLift lo ejecuta en modo `plan` (sin ediciones), con `--sandbox` y permisos `request-review`
por sesión (en modo headless cualquier comando o herramienta que requiera permiso se deniega),
sobre una copia temporal de solo lectura de los archivos seleccionados. Usa el worker en el host
(`python -m app.migration_worker`); la imagen Docker del worker solo incluye Codex.

## Inicio rápido — Linux/macOS

```bash
cp .env.example .env
# Configura LEGACYLIFT_BOOTSTRAP_SECRET (24+ caracteres aleatorios) en .env para registrar el primer usuario.
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.api:app --host 127.0.0.1 --port 8000
# otra terminal: cd frontend && npm install && npm start
```

> El archivo `.env` se encuentra en la raíz (junto a `frontend/` y `backend/`).
> `LEGACYLIFT_DATA_DIR` admite rutas absolutas o relativas a la raíz.

## Demostración sin tokens (útil para examen)

El proyecto incluye `demo-source/`: un pequeño sistema Python con tests.

1. Comprime el **contenido de `demo-source/`** en `demo-source.zip`.
2. Crea un proyecto con motor **Demo determinista sin IA**.
3. Sube el ZIP.
4. Ejecuta `Discover → Understand → Assess → Plan` desde los botones.
5. Revisa `blueprint.md`, **aprueba el plan** y ejecuta Baseline.
6. Ejecuta Modernize: el pack demo elimina paréntesis redundantes en `return`.
7. Revisa `changes.patch`; ejecuta Verify, decide y exporta evidencias.

En modo `VALIDATION_MODE=static` se validan sintaxis Python/JSON, pero **no se ejecutan tests**.
El resultado de Verify será `insufficient_evidence` a menos que configures un ejecutor de pruebas
compatible y se obtengan baselines comparables. La demo no falsifica un confidence score.

## Validación real de pruebas en Docker

En `.env` configura:

```dotenv
VALIDATION_MODE=docker
```

Instala Docker Desktop o Docker Engine. Al ejecutar Baseline y Verify, FastAPI lanza
la suite Python `unittest` del repositorio dentro de un contenedor sin red, con memoria/CPU
limitadas, montando la fuente solo lectura. No se recomienda ejecutar repositorios desconocidos
fuera de un sandbox endurecido. Es un aislamiento educativo, no un sistema de ejecución multi-tenant.

## Despliegue desde tu computadora por IPv6

En producción local compila Angular:

```bash
cd frontend && npm install && npm run build
```

Usa un reverse proxy (Caddy o Nginx) que sirva el directorio `frontend/dist/legacylift/browser/`
y proxifique `/api/*` hacia FastAPI en `127.0.0.1:8000`. Abre **únicamente el puerto HTTPS**
en el firewall IPv6. Mantén Codex, SQLite, FastAPI y los datos privados en loopback.
Configura un nombre DNS y un certificado TLS válido; evita exponer directamente
el puerto 4200 de Angular o el 8000 de FastAPI.

Ejemplo Caddyfile (ajusta dominio y la ruta absoluta del proyecto):

```caddyfile
legacylift.ejemplo.com {
    encode zstd gzip
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /RUTA_ABSOLUTA/legacylift-mvp/frontend/dist/legacylift/browser
        try_files {path} /index.html
        file_server
    }
}
```

Para IPv6 doméstico comprueba IPv6 global, firewall, ruta entrante y que tu operador no
bloquee tráfico. La dirección local `fe80::/10` no es accesible remotamente.

## Estructura y escalabilidad

```text
frontend/         Angular standalone + UI shadcn-inspired
backend/app/
    api.py        HTTP, autenticación y contratos públicos
    db.py         persistencia aislada (SQLite -> PostgreSQL después)
    workspace.py  snapshots/candidatos y artefactos (filesystem -> S3 después)
    inspection.py scanner determinista
    ai.py         proveedores Codex/Gemini/Claude/Demo (adaptadores reemplazables)
    pipeline.py   orquestador de fases (ThreadPoolExecutor -> Cola/Workers después)
    prompts/      instrucciones versionadas por fase
backend/tests/    tests de API, autorización y pipeline
demo-source/      repositorio de ejemplo
docs/             visión y backlog de usuario
```

Cuando evoluciones a Sprint 5, cambia el adaptador de persistencia, introduce autenticación por
usuario y organización, control de tenants en todas las operaciones, colas de trabajo y sandbox
por ejecución. No actives `multi-tenancy` solo desde la UI.

## Seguridad y limitaciones conocidas

- Solo un operador, contraseña larga en `.env`, sesión temporal en `sessionStorage` de Angular.
- El ZIP se valida (tamaño, traversal, symlinks y nombres de secretos obvios), pero **no existe un
  antivirus ni un análisis exhaustivo del contenido**. Usa repositorios propios/de confianza.
- No subas `.env`, secretos, credenciales, claves ni datos personales al motor de IA.
- Los prompts de análisis incluyen el código del proyecto; los proveedores externos lo reciben.
- El CLI Codex usa la autenticación personal del PC (`codex login`). Respeta las condiciones de uso
  correspondientes; no revendas ni compartas la cuota de tu cuenta.
- El modelo podría intentar comandos o cambios inesperados. Codex se invoca con sandbox limitado;
  el backend **rechaza cambios fuera de rutas/categorías permitidas**.
- `static` no ejecuta código; `docker` ejecuta tests Python solo dentro de un contenedor restringido.
- Para una demo importante, primero verifica Codex, Claude y Docker en tu entorno real.

## Test backend

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Documentación de producto: [`docs/product.md`](docs/product.md).

## Sprint 1 · GitHub OAuth App

Para importar repositorios privados o públicos mediante consentimiento, registra una
**GitHub OAuth App** (Settings → Developer settings → OAuth Apps), con callback
`http://localhost:8000/api/github/callback` para desarrollo local (o `https://TU-DOMINIO/api/github/callback`
si vas a exponer la app). Configura las variables `GITHUB_OAUTH_*` en el `.env` de la raíz,
además de `LEGACYLIFT_ENCRYPTION_KEY` (Fernet). **Nunca coloques esos secretos en Angular.**
El permiso OAuth `repo` concede acceso amplio a los repositorios accesibles al usuario;
GitHub muestra ese alcance antes de autorizar. Puedes desconectar desde la interfaz.
El flujo usa `state` de un solo uso y PKCE (S256); los tokens quedan cifrados en SQLite.
Cuando agregues fuentes Git en HU-03, se clonarán exclusivamente desde `github.com` usando ese token.

### Protección de la primera cuenta

El primer registro exige `LEGACYLIFT_BOOTSTRAP_SECRET`, configurado en `.env` con un valor
aleatorio de al menos 24 caracteres. Esto impide que un visitante se apropie de una instancia
recién publicada por IPv6. El primer usuario recibe el rol owner y hereda los proyectos
antiguos sin propietario; los demás usuarios solo ven sus proyectos. Para habilitar registro
posterior establece temporalmente `LEGACYLIFT_ALLOW_REGISTRATION=true` (no existe invitación
administrativa todavía). Usa HTTPS para acceso externo y guarda `.env` fuera del control de versiones.

## Pruebas E2E académicas

El repositorio incluye una suite con FastAPI real, HTTP, worker separado y exportación completa, además de un smoke test de Angular con Playwright. GitHub Actions ejecuta la demo determinista y los tests de regresión. Opcionalmente puedes ejecutar una modernización real con **Codex CLI (codex login)**, **Gemini (Antigravity CLI con cuenta de Google)** o **Claude API (CLAUDE_API_KEY)** (`E2E_AI_PROVIDER=codex|gemini|claude`) y comprobar los tests de Python con Docker. Las pruebas de IA consumen cuota y **no se habilitan automáticamente en CI**.

Consulta [la guía de pruebas E2E](docs/E2E-ACADEMIC.md).
