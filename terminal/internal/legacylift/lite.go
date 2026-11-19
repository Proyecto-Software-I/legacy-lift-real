package legacylift

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Lite mode is meant for small local models. Go does the mechanical work
// (reading the code, running the tests, computing the diff) and the model
// only reads text and writes the Markdown report. No agent tools are used,
// so a 4B–9B model does not have to drive tool calls.

const (
	codeBudget     = 24000 // characters of source code sent per phase
	artifactBudget = 6000  // characters kept from each previous artifact
)

var codeExt = map[string]bool{
	".py": true, ".js": true, ".ts": true, ".jsx": true, ".tsx": true, ".java": true,
	".go": true, ".rb": true, ".php": true, ".cs": true, ".c": true, ".h": true,
	".cpp": true, ".kt": true, ".swift": true, ".rs": true, ".scala": true, ".vb": true,
	".cbl": true, ".cob": true, ".sql": true, ".html": true, ".css": true, ".scss": true,
	".json": true, ".yaml": true, ".yml": true, ".xml": true, ".toml": true, ".ini": true,
	".cfg": true, ".gradle": true, ".md": true, ".txt": true, ".sh": true, ".bat": true,
}

var skipDirs = map[string]bool{
	Dir: true, ".git": true, ".crush": true, "node_modules": true, "vendor": true,
	"dist": true, "build": true, "target": true, "__pycache__": true, ".venv": true, "venv": true,
}

func skippedFile(name string) bool {
	switch name {
	case "crush.json", ".crush.json", "package-lock.json", "yarn.lock", "go.sum":
		return true
	}
	return false
}

// sourceFiles lists the text source files of a project, sorted.
func sourceFiles(root string) []string {
	var files []string
	_ = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if p != root && (skipDirs[d.Name()] || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if skippedFile(d.Name()) || !codeExt[strings.ToLower(filepath.Ext(d.Name()))] {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		files = append(files, filepath.ToSlash(rel))
		return nil
	})
	sort.Strings(files)
	return files
}

// CollectCode returns the project's source with numbered lines (so the model
// can cite `archivo:línea`) and a coverage note when it had to cut.
func CollectCode(root string, budget int) string {
	files := sourceFiles(root)
	var b strings.Builder
	included := 0
	for _, f := range files {
		data, err := os.ReadFile(filepath.Join(root, f))
		if err != nil {
			continue
		}
		var block strings.Builder
		fmt.Fprintf(&block, "\n### ARCHIVO: %s\n```\n", f)
		for i, line := range strings.Split(strings.TrimRight(string(data), "\n"), "\n") {
			fmt.Fprintf(&block, "%d| %s\n", i+1, line)
		}
		block.WriteString("```\n")
		if b.Len()+block.Len() > budget && included > 0 {
			continue
		}
		b.WriteString(block.String())
		included++
	}
	fmt.Fprintf(&b, "\nCOBERTURA: %d de %d archivos incluidos.", included, len(files))
	if included < len(files) {
		b.WriteString(" El análisis es parcial: no afirmes nada sobre los archivos no incluidos.")
	}
	return b.String()
}

func clip(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) <= n {
		return s
	}
	return s[:n] + "\n…(recortado)"
}

func (r *Runner) artifactText(id string) string {
	for _, p := range mustPhases() {
		if p.ID == id {
			return clip(cleanOutput(readFile(filepath.Join(r.Root, p.Artifact()))), artifactBudget)
		}
	}
	return ""
}

func mustPhases() []Phase {
	p, _ := Phases()
	return p
}

// TestCommand guesses how to run the project's tests.
func TestCommand(dir string) []string {
	exists := func(n string) bool { _, err := os.Stat(filepath.Join(dir, n)); return err == nil }
	switch {
	case exists("run_tests.sh"):
		// Explicit hook for stacks without a standard runner (e.g. plain Java).
		return []string{"sh", "run_tests.sh"}
	case exists("go.mod"):
		return []string{"go", "test", "./..."}
	case exists("package.json"):
		if b, _ := os.ReadFile(filepath.Join(dir, "package.json")); strings.Contains(string(b), `"test"`) {
			return []string{"npm", "test", "--silent"}
		}
	case exists("pom.xml"):
		return []string{"mvn", "-q", "test"}
	}
	for _, f := range sourceFiles(dir) {
		base := filepath.Base(f)
		if strings.HasSuffix(f, ".py") && (strings.HasPrefix(base, "test_") || strings.HasSuffix(base, "_test.py")) {
			return []string{"python3", "-m", "unittest", "discover", "-s", filepath.Dir(f), "-p", "test*.py", "-v"}
		}
	}
	return nil
}

// RunTests executes the detected test command with a timeout and no stdin.
func RunTests(ctx context.Context, dir string) string {
	cmd := TestCommand(dir)
	if cmd == nil {
		return "No se detectaron pruebas automáticas en el proyecto."
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	c := exec.CommandContext(ctx, cmd[0], cmd[1:]...)
	c.Dir = dir
	// Let tests import the project's modules without being a package.
	c.Env = append(os.Environ(), "PYTHONPATH="+dir)
	out, err := c.CombinedOutput()
	status := "ÉXITO (código 0)"
	if err != nil {
		status = "FALLO: " + err.Error()
	}
	return fmt.Sprintf("Comando: `%s`\nResultado: %s\n```\n%s\n```", strings.Join(cmd, " "), status, clip(string(out), 4000))
}

// Diff returns a unified diff between the original and the candidate.
func Diff(ctx context.Context, root string) string {
	c := exec.CommandContext(ctx, "diff", "-ru", "-x", Dir, "-x", ".git", "-x", "crush.json",
		"-x", ".crush", "-x", "__pycache__", ".", filepath.Join(Dir, "candidate"))
	c.Dir = root
	out, _ := c.CombinedOutput()
	s := strings.ReplaceAll(string(out), filepath.Join(Dir, "candidate")+"/", "candidata/")
	if strings.TrimSpace(s) == "" {
		return "Sin diferencias entre el original y la candidata."
	}
	return clip(s, 8000)
}

var fileBlockRe = regexp.MustCompile("(?s)###\\s*ARCHIVO:\\s*([^\\n`]+?)\\s*\\n```[a-zA-Z0-9]*\\n(.*?)\\n```")

// ApplyFiles writes the "### ARCHIVO: path" blocks of the model answer into
// the candidate. Only files that already exist there and stay inside it are
// written, so the model cannot create or escape paths.
func ApplyFiles(root, answer string) []string {
	cand := filepath.Join(root, Dir, "candidate")
	var changed []string
	for _, m := range fileBlockRe.FindAllStringSubmatch(answer, -1) {
		rel := filepath.Clean(strings.TrimSpace(m[1]))
		if filepath.IsAbs(rel) || strings.HasPrefix(rel, "..") {
			continue
		}
		target := filepath.Join(cand, rel)
		if _, err := os.Stat(target); err != nil {
			continue
		}
		// Strip the "N| " line numbers if the model copied them back.
		var lines []string
		numbered := regexp.MustCompile(`^\s*\d+\|\s?`)
		for _, l := range strings.Split(m[2], "\n") {
			lines = append(lines, numbered.ReplaceAllString(l, ""))
		}
		if os.WriteFile(target, []byte(strings.Join(lines, "\n")+"\n"), 0o644) == nil {
			changed = append(changed, filepath.ToSlash(rel))
		}
	}
	return changed
}

var liteTask = map[string]string{
	"discover":   `Haz el INVENTARIO del sistema legado: lenguajes y frameworks (con el archivo donde lo ves), estructura y módulos, dependencias declaradas, puntos de entrada, configuración, pruebas existentes y hallazgos iniciales (código muerto, duplicación, TODOs, riesgos) con evidencia ` + "`archivo:línea`" + `.`,
	"understand": `Documenta CÓMO FUNCIONA el sistema: resumen de la arquitectura, REGLAS DE NEGOCIO (cada una con su evidencia ` + "`archivo:línea`" + `), flujos principales y preguntas abiertas. No propongas cambios.`,
	"assess":     `EVALÚA el sistema por módulo: deuda técnica, riesgo, mantenibilidad y testabilidad (puntaje 1–5 solo con evidencia; si no, "sin evidencia suficiente"), estrategia recomendada (mantener, refactorizar, reemplazar, migrar) y riesgos de cambiarlo.`,
	"plan":       `Escribe el MODERNIZATION BLUEPRINT: arquitectura actual y objetivo, alcance, riesgos, priorización y UNA sola Migration Wave pequeña y segura (refactor dentro del mismo lenguaje): lista exacta de archivos existentes a cambiar, el cambio en cada uno y cómo se verificará.`,
	"baseline":   `Escribe el BASELINE de comportamiento antes de modernizar, usando el resultado REAL de las pruebas que se muestra abajo. Si no hay pruebas, indica "sin baseline de pruebas: no se podrá afirmar equivalencia" y propone casos de entrada/salida para los archivos de la wave.`,
	"modernize": `Aplica SOLO la Migration Wave del plan. Para cada archivo que cambies, devuelve su contenido COMPLETO nuevo con este formato exacto:

### ARCHIVO: ruta/del/archivo.ext
` + "```" + `
contenido completo del archivo, sin números de línea
` + "```" + `

Solo puedes cambiar archivos que ya existen. No agregues dependencias. Al final escribe "## Resumen" con qué cambiaste y por qué.`,
	"verify": `Escribe el INFORME DE VERIFICACIÓN usando el diff y el resultado REAL de las pruebas sobre la candidata que se muestran abajo: cambios por archivo, comparación con el baseline, divergencias y un confidence score orientativo (0–100) explicando qué se verificó y qué NO. Nunca afirmes equivalencia sin pruebas.`,
}

// LitePrompt builds the self-contained prompt for a phase in lite mode.
func (r *Runner) LitePrompt(ctx context.Context, ph Phase) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# LegacyLift / %s\n\nTAREA: %s\n\nResponde en español, en Markdown, empezando con el título \"# %s\".\n",
		strings.ToUpper(ph.ID), liteTask[ph.ID], ph.Title)

	prev := map[string][]string{
		"understand": {"discover"},
		"assess":     {"discover", "understand"},
		"plan":       {"understand", "assess"},
		"baseline":   {"plan"},
		"modernize":  {"plan", "baseline"},
		"verify":     {"plan", "baseline", "modernize"},
	}[ph.ID]
	for _, id := range prev {
		if t := r.artifactText(id); t != "" {
			fmt.Fprintf(&b, "\n## Resultado previo: %s\n%s\n", strings.ToUpper(id), t)
		}
	}

	switch ph.ID {
	case "baseline":
		fmt.Fprintf(&b, "\n## Pruebas del sistema original\n%s\n", RunTests(ctx, r.Root))
	case "verify":
		cand := filepath.Join(r.Root, Dir, "candidate")
		fmt.Fprintf(&b, "\n## Diff original → candidata\n```diff\n%s\n```\n", Diff(ctx, r.Root))
		fmt.Fprintf(&b, "\n## Pruebas sobre la candidata\n%s\n", RunTests(ctx, cand))
		return b.String()
	}
	code := r.Root
	if ph.ID == "modernize" {
		code = filepath.Join(r.Root, Dir, "candidate")
	}
	fmt.Fprintf(&b, "\n## CÓDIGO DEL SISTEMA (con números de línea)\n%s\n", CollectCode(code, codeBudget))
	return b.String()
}
