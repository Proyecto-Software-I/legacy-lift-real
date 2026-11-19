# LegacyLift AI — Terminal

Cliente de terminal de LegacyLift AI. Ejecuta las fases de modernización
(Discover → Understand → Assess → Plan → Baseline → Modernize → Verify) sobre la carpeta de un
sistema legado, **sin escribir prompts**, y guarda cada resultado en `.legacylift/`.

- **Independiente de la web:** no necesita cuenta ni servidor; trabaja con una carpeta de código.
- **El código original nunca se modifica:** los cambios van solo a `.legacylift/candidate/`.
- **IA a elección:** un modelo local con Ollama (el código no sale de tu computadora) o un proveedor
  en la nube (Anthropic, OpenAI, OpenRouter, Gemini, …) con tu propia clave.

## 1. Instalar

### Opción A — Descargar el ejecutable (recomendado)

1. Entra a **Releases** del repositorio:
   <https://github.com/Proyecto-Software-I/legacy-lift-real/releases>
2. Descarga el archivo de tu sistema:

   | Sistema | Archivo |
   |---|---|
   | Windows | `legacylift_<versión>_windows_amd64.zip` |
   | macOS (Apple Silicon) | `legacylift_<versión>_darwin_arm64.tar.gz` |
   | macOS (Intel) | `legacylift_<versión>_darwin_amd64.tar.gz` |
   | Linux | `legacylift_<versión>_linux_amd64.tar.gz` |

3. Descomprímelo y deja `legacylift` (o `legacylift.exe`) en una carpeta de tu `PATH`.

### Opción B — Script de instalación (macOS y Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/Proyecto-Software-I/legacy-lift-real/main/terminal/scripts/install-legacylift.sh | sh
```

### Opción C — Compilar desde el código

Requiere Go 1.27 o superior.

```bash
git clone https://github.com/Proyecto-Software-I/legacy-lift-real.git
cd legacy-lift-real/terminal
go build -o legacylift .
```

## 2. Elegir la IA

### IA local con Ollama (gratis, privado)

```bash
# instala Ollama desde https://ollama.com y luego:
ollama pull qwen3:4b-instruct-2507-q4_K_M     # 8 GB de RAM, rápido
# o, con 16 GB de RAM y mejor calidad:
ollama pull qwen3.5:9b
```

### Proveedor en la nube (de pago, más preciso)

```bash
export ANTHROPIC_API_KEY=...        # o OPENAI_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY…
```

La clave se lee del entorno de tu computadora; LegacyLift no la envía a ningún servidor propio.

## 3. Usar

```bash
cd /ruta/al/sistema-legado
legacylift                                         # detecta Ollama y ejecuta las 7 fases
legacylift --model anthropic/claude-sonnet-5-5     # con un proveedor en la nube
legacylift --from understand --until plan          # solo algunas fases
legacylift --plain                                 # salida en texto, útil en CI
legacylift phases                                  # lista las fases
```

En la pantalla, `q` cancela. Los informes quedan en `.legacylift/01-discover.md` … `07-verify.md`.

## 4. Publicar una versión (equipo)

Crear una etiqueta `v*` publica los ejecutables en Releases mediante GitHub Actions
(`.github/workflows/terminal-release.yml` y `terminal/.goreleaser.yml`):

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Más información

- Detalle de fases, modos y modelos probados: [`LEGACYLIFT.md`](LEGACYLIFT.md)
- Documentación del proyecto base (Crush): [`docs/README-crush.md`](docs/README-crush.md)
- Licencia: FSL-1.1-MIT (ver [`LICENSE.md`](LICENSE.md)).
