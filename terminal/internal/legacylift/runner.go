package legacylift

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Agent runs a single prompt non-interactively and streams the answer to w.
// It is provided by the cmd package so this package does not depend on the
// app internals (and stays testable).
type Agent func(ctx context.Context, prompt string, w io.Writer) error

// Status of a phase.
type Status int

const (
	Pending Status = iota
	Running
	Done
	Failed
	Skipped
)

// Event is sent by the runner while it works.
type Event struct {
	Index  int
	Status Status
	Chunk  string // streamed model output
	Err    error
	Took   time.Duration
}

// Runner executes phases in order on Root.
type Runner struct {
	Root   string
	Phases []Phase
	Agent  Agent
	// Lite runs phases without agent tools: Go gathers code, tests and
	// diffs and the model only writes the report (for small local models).
	Lite bool
	// BeforePhase, when set, runs before each phase (e.g. to pick tools).
	BeforePhase func(Phase)
}

// skipCopy are top-level entries never copied into the candidate.
var skipCopy = map[string]bool{Dir: true, ".git": true, ".crush": true, "crush.json": true, ".crush.json": true, "node_modules": true}

// prepareCandidate copies the original sources into .legacylift/candidate
// so Modernize never touches the original.
func (r *Runner) prepareCandidate() error {
	dst := filepath.Join(r.Root, Dir, "candidate")
	if err := os.RemoveAll(dst); err != nil {
		return err
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	return filepath.WalkDir(r.Root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(r.Root, p)
		if rel == "." {
			return nil
		}
		top := strings.Split(filepath.ToSlash(rel), "/")[0]
		if skipCopy[top] {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if !d.Type().IsRegular() {
			return nil
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, b, 0o644)
	})
}

type chunkWriter struct {
	idx  int
	send func(Event)
	buf  strings.Builder
}

func (c *chunkWriter) Write(p []byte) (int, error) {
	c.buf.Write(p)
	c.send(Event{Index: c.idx, Status: Running, Chunk: string(p)})
	return len(p), nil
}

// Run executes every phase sequentially. It stops at the first failure.
func (r *Runner) Run(ctx context.Context, send func(Event)) error {
	if err := os.MkdirAll(filepath.Join(r.Root, Dir), 0o755); err != nil {
		return err
	}
	prev := "(no hay artefacto previo)"
	for i, ph := range r.Phases {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if i > 0 {
			prev = r.Phases[i-1].Artifact()
		} else if ph.Num != "01" {
			prev = previousArtifact(ph)
		}
		send(Event{Index: i, Status: Running})
		if r.BeforePhase != nil {
			r.BeforePhase(ph)
		}
		// The original code is never writable; Modernize may only touch the copy.
		guard := "artifacts"
		if ph.ID == "modernize" {
			guard = "candidate"
		}
		_ = os.Setenv("LEGACYLIFT_WRITE_GUARD", guard)
		start := time.Now()

		if ph.ID == "modernize" {
			if err := r.prepareCandidate(); err != nil {
				send(Event{Index: i, Status: Failed, Err: fmt.Errorf("no se pudo copiar la candidata: %w", err)})
				return err
			}
		}

		w := &chunkWriter{idx: i, send: send}
		art := filepath.Join(r.Root, ph.Artifact())
		var err error
		// Small local models sometimes finish without writing anything:
		// retry once with an explicit reminder before failing the phase.
		for attempt := 1; attempt <= 2; attempt++ {
			prompt := ph.Prompt(prev)
			if r.Lite {
				prompt = r.LitePrompt(ctx, ph)
			}
			if attempt > 1 {
				prompt += "\n\nIMPORTANTE: el intento anterior no dejó un resultado válido. Responde ahora con el informe completo en Markdown, en español."
				if ph.ID == "modernize" && !r.Lite {
					prompt += " Antes del informe DEBES modificar de verdad los archivos dentro de `.legacylift/candidate/` con la herramienta edit o write."
				}
				send(Event{Index: i, Status: Running, Chunk: "\n— reintentando la fase —\n"})
			}
			w.buf.Reset()
			err = r.Agent(ctx, prompt, w)
			if err != nil && attempt == 1 && ctx.Err() == nil {
				// e.g. a malformed tool call from a small model: try once more.
				send(Event{Index: i, Status: Running, Chunk: "\n— error del modelo: " + err.Error() + " —\n"})
				continue
			}
			if err == nil && !r.Lite && ph.ID == "modernize" && !candidateChanged(ctx, r.Root) {
				// The model claimed changes without editing anything: never
				// accept a Modernize report that is not backed by a real diff.
				_ = os.Remove(art)
				if attempt == 1 {
					send(Event{Index: i, Status: Running, Chunk: "\n— no hubo cambios reales en .legacylift/candidate: reintentando —\n"})
					continue
				}
				err = fmt.Errorf("Modernize no modificó ningún archivo en %s/candidate (el informe no está respaldado por cambios reales)", Dir)
				break
			}
			if err != nil || validMarkdown(readFile(art)) || validMarkdown(w.buf.String()) {
				break
			}
			_ = os.Remove(art) // discard an invalid artifact before retrying
		}
		took := time.Since(start)
		if err != nil {
			send(Event{Index: i, Status: Failed, Err: err, Took: took})
			return err
		}
		// If the model answered but did not write the artifact itself,
		// keep its answer as the artifact so nothing is lost.
		if r.Lite && ph.ID == "modernize" {
			changed := ApplyFiles(r.Root, w.buf.String())
			note := "\n\n## Archivos aplicados en la candidata\n"
			if len(changed) == 0 {
				note += "Ninguno: el modelo no devolvió archivos válidos.\n"
			}
			for _, c := range changed {
				note += "- `" + c + "`\n"
			}
			w.buf.WriteString(note)
		}
		if r.Lite || !validMarkdown(readFile(art)) {
			if out := cleanOutput(w.buf.String()); validMarkdown(out) {
				if !strings.HasPrefix(out, "#") {
					out = "# " + ph.Title + "\n\n" + out
				}
				_ = os.WriteFile(art, []byte(out+"\n"), 0o644)
			} else {
				err := fmt.Errorf("la fase no produjo un resultado válido en %s (el modelo puede ser demasiado pequeño: prueba otro con --model)", ph.Artifact())
				send(Event{Index: i, Status: Failed, Err: err, Took: took})
				return err
			}
		}
		if ph.ID == "modernize" {
			// Never hand a broken candidate to Verify: if the tests passed on the
			// original and fail on the candidate, set it aside and restore a clean copy.
			cand := filepath.Join(r.Root, Dir, "candidate")
			if ranO, okO := TestsResult(ctx, r.Root); ranO && okO {
				if _, okC := TestsResult(ctx, cand); !okC {
					rej := filepath.Join(r.Root, Dir, "candidate-rechazada")
					_ = os.RemoveAll(rej)
					_ = os.Rename(cand, rej)
					_ = r.prepareCandidate()
					note := "\n\n## Cambios rechazados por el sistema\n\nLas pruebas pasaban en el original y fallaron con estos cambios, así que LegacyLift los apartó en `" + Dir + "/candidate-rechazada/` y restauró la candidata al código original. Nada de esto llegó a Verify.\n"
					_ = os.WriteFile(art, []byte(strings.TrimRight(readFile(art), "\n")+note), 0o644)
					send(Event{Index: i, Status: Running, Chunk: "\n— las pruebas fallaron con los cambios: rechazados y candidata restaurada —\n"})
				}
			}
		}
		if ph.ID == "verify" {
			reports := readFile(filepath.Join(r.Root, Dir, "05-baseline.md")) + "\n" + readFile(art)
			v := "\n\n## Veredicto del sistema (no lo escribe la IA)\n\n**Veredicto: ⚪ sin cambios que verificar.** La candidata es idéntica al original: Modernize no dejó modificaciones aceptadas"
			if _, err := os.Stat(filepath.Join(r.Root, Dir, "candidate-rechazada")); err == nil {
				v += " (sus cambios rompían las pruebas y se apartaron en `" + Dir + "/candidate-rechazada/`)"
			}
			v += ". Cualquier afirmación de mejora en este informe no aplica.\n"
			if candidateChanged(ctx, r.Root) {
				v = VerdictReport(ctx, r.Root, reports)
			}
			_ = os.WriteFile(art, []byte(strings.TrimRight(readFile(art), "\n")+v), 0o644)
		}
		if ph.ID == "baseline" || ph.ID == "verify" {
			dir, where := r.Root, "el código original"
			if ph.ID == "verify" {
				dir, where = filepath.Join(r.Root, Dir, "candidate"), "la candidata"
			}
			if md, bad := ClaimsReport(CheckClaims(ctx, dir, readFile(art)), where); md != "" {
				_ = os.WriteFile(art, []byte(strings.TrimRight(readFile(art), "\n")+md), 0o644)
				if bad > 0 {
					send(Event{Index: i, Status: Running, Chunk: fmt.Sprintf("\n— %d ejemplo(s) del informe no coinciden con el código real: marcados en %s —\n", bad, ph.Artifact())})
				}
			}
		}
		send(Event{Index: i, Status: Done, Took: took})
	}
	return nil
}

func previousArtifact(ph Phase) string {
	all, err := Phases()
	if err != nil {
		return ""
	}
	for i, p := range all {
		if p.ID == ph.ID && i > 0 {
			return all[i-1].Artifact()
		}
	}
	return ""
}

func hasContent(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Size() > 0
}

var thinkRe = regexp.MustCompile(`(?s)<think>.*?</think>`)

// cleanOutput removes reasoning blocks some local models emit.
func cleanOutput(s string) string {
	return strings.TrimSpace(thinkRe.ReplaceAllString(s, ""))
}

func readFile(p string) string {
	b, _ := os.ReadFile(p)
	return string(b)
}

// validMarkdown is a cheap sanity check: real phase reports are prose with
// at least one heading or list, not empty text or a raw JSON blob.
func validMarkdown(s string) bool {
	s = cleanOutput(s)
	if len(s) < 200 || strings.HasPrefix(s, "{") || strings.HasPrefix(s, "[") {
		return false
	}
	return strings.Contains(s, "#") || strings.Contains(s, "\n- ") || strings.Contains(s, "\n* ") || strings.Contains(s, "|")
}

// candidateChanged reports whether .legacylift/candidate differs from the original.
func candidateChanged(ctx context.Context, root string) bool {
	return !strings.HasPrefix(Diff(ctx, root), "Sin diferencias")
}
