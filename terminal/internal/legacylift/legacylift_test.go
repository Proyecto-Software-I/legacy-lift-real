package legacylift

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
)

func TestPhasesOrderAndSelect(t *testing.T) {
	all, err := Phases()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"discover", "understand", "assess", "plan", "baseline", "modernize", "verify"}
	if len(all) != len(want) {
		t.Fatalf("got %d phases", len(all))
	}
	for i, p := range all {
		if p.ID != want[i] {
			t.Fatalf("phase %d = %s, want %s", i, p.ID, want[i])
		}
		if strings.Contains(p.Prompt("x"), "{{") {
			t.Fatalf("unrendered placeholder in %s", p.ID)
		}
	}
	sel, err := Select(all, "assess", "plan")
	if err != nil || len(sel) != 2 || sel[0].ID != "assess" {
		t.Fatalf("select: %v %v", sel, err)
	}
	if _, err := Select(all, "nope", ""); err == nil {
		t.Fatal("expected error for unknown phase")
	}
}

// fakeAgent writes the artifact like a real model would, except for the
// phase named in "silent", where it only answers on stdout.
func fakeAgent(root, silent string) Agent {
	return func(_ context.Context, prompt string, w io.Writer) error {
		all, _ := Phases()
		for _, p := range all {
			if strings.Contains(prompt, p.Artifact()) && strings.Contains(prompt, "/ "+strings.ToUpper(p.ID)) {
				fmt.Fprintf(w, "## trabajando en %s\n\n%s", p.ID, strings.Repeat("- punto observado en archivo:1\n", 12))
				if p.ID == "modernize" {
					// Modernize must only see the candidate copy.
					if _, err := os.Stat(filepath.Join(root, Dir, "candidate", "app.py")); err != nil {
						return errors.New("candidate copy missing")
					}
					_ = os.WriteFile(filepath.Join(root, Dir, "candidate", "app.py"), []byte("print('nuevo')\n"), 0o644)
				}
				if p.ID != silent {
					return os.WriteFile(filepath.Join(root, p.Artifact()), []byte("# ok\n\n"+strings.Repeat("- hallazgo con evidencia\n", 12)), 0o644)
				}
				return nil
			}
		}
		return errors.New("unknown prompt")
	}
}

func TestRunnerAllPhases(t *testing.T) {
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "app.py"), []byte("print('viejo')\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "crush.json"), []byte("{}"), 0o644)
	all, _ := Phases()
	r := &Runner{Root: root, Phases: all, Agent: fakeAgent(root, "assess")}
	var done int
	err := r.Run(context.Background(), func(e Event) {
		if e.Status == Done && e.Chunk == "" {
			done++
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	if done != len(all) {
		t.Fatalf("done = %d", done)
	}
	// The original is never modified.
	b, _ := os.ReadFile(filepath.Join(root, "app.py"))
	if string(b) != "print('viejo')\n" {
		t.Fatalf("original modified: %q", b)
	}
	// Config files are not copied into the candidate.
	if _, err := os.Stat(filepath.Join(root, Dir, "candidate", "crush.json")); err == nil {
		t.Fatal("crush.json copied into candidate")
	}
	// A phase that only answered on stdout still gets its artifact.
	b, _ = os.ReadFile(filepath.Join(root, ".legacylift", "03-assess.md"))
	if !strings.Contains(string(b), "trabajando en assess") {
		t.Fatalf("fallback artifact = %q", b)
	}
}

func TestRunnerStopsOnFailure(t *testing.T) {
	root := t.TempDir()
	all, _ := Phases()
	calls := 0
	r := &Runner{Root: root, Phases: all, Agent: func(context.Context, string, io.Writer) error {
		calls++
		return errors.New("modelo caído")
	}}
	if err := r.Run(context.Background(), func(Event) {}); err == nil {
		t.Fatal("expected error")
	}
	if calls != 2 { // one retry after a model error, then stop
		t.Fatalf("calls = %d", calls)
	}
}

func TestTUIView(t *testing.T) {
	all, _ := Phases()
	n := len(all)
	m := &model{phases: all, status: make([]Status, n), took: make([]time.Duration, n),
		errs: make([]error, n), output: make([]string, n), model: "ollama/qwen", root: "/demo",
		events: make(chan Event, 1), cancel: func() {}, width: 120, height: 30}
	m.Update(eventMsg{Index: 0, Status: Done, Took: 3 * time.Second})
	m.Update(eventMsg{Index: 1, Status: Running})
	m.Update(eventMsg{Index: 1, Status: Running, Chunk: "Analizando reglas de negocio"})
	out := m.View().Content
	for _, s := range []string{"LegacyLift AI", "Discover", "Understand", "Analizando reglas", "ollama/qwen", "q cancelar"} {
		if !strings.Contains(out, s) {
			t.Fatalf("view missing %q:\n%s", s, out)
		}
	}
	m.Update(finishedMsg{})
	if !strings.Contains(m.View().Content, "Todas las fases completadas") {
		t.Fatal("missing final message")
	}
	if _, cmd := m.Update(tea.KeyPressMsg{Code: 'q', Text: "q"}); cmd == nil {
		t.Fatal("q should quit")
	}
}

func TestRunnerRetriesEmptyPhase(t *testing.T) {
	root := t.TempDir()
	all, _ := Phases()
	calls := 0
	r := &Runner{Root: root, Phases: all[:1], Agent: func(_ context.Context, prompt string, w io.Writer) error {
		calls++
		if calls == 2 {
			if !strings.Contains(prompt, "intento anterior") {
				t.Error("retry prompt missing reminder")
			}
			fmt.Fprint(w, "# Inventario\n\n"+strings.Repeat("- archivo.py: módulo principal\n", 12))
		}
		return nil
	}}
	if err := r.Run(context.Background(), func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("calls = %d", calls)
	}
}

func TestValidMarkdown(t *testing.T) {
	if validMarkdown("<think>\n</think>\n{\"id\":\"c1\"}") {
		t.Fatal("raw JSON must be rejected")
	}
	if !validMarkdown("<think>x</think>\n# Informe\n\n" + strings.Repeat("- regla con evidencia\n", 12)) {
		t.Fatal("markdown report must be accepted")
	}
}

func TestLiteHelpers(t *testing.T) {
	root := t.TempDir()
	_ = os.MkdirAll(filepath.Join(root, "tests"), 0o755)
	_ = os.WriteFile(filepath.Join(root, "calc.py"), []byte("def f():\n    return 1\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "tests", "test_calc.py"), []byte("import unittest\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "crush.json"), []byte("{}"), 0o644)

	code := CollectCode(root, 10000)
	if !strings.Contains(code, "### ARCHIVO: calc.py") || !strings.Contains(code, "2|     return 1") {
		t.Fatalf("code:\n%s", code)
	}
	if strings.Contains(code, "crush.json") {
		t.Fatal("config file leaked into code context")
	}
	if !strings.Contains(code, "COBERTURA: 2 de 2") {
		t.Fatalf("coverage missing:\n%s", code)
	}
	if cmd := TestCommand(root); len(cmd) == 0 || cmd[0] != "python3" {
		t.Fatalf("test command = %v", cmd)
	}

	cand := filepath.Join(root, Dir, "candidate")
	_ = os.MkdirAll(cand, 0o755)
	_ = os.WriteFile(filepath.Join(cand, "calc.py"), []byte("old\n"), 0o644)
	answer := "### ARCHIVO: calc.py\n```python\n1| def f():\n2|     return 2\n```\n" +
		"### ARCHIVO: ../escape.py\n```\nx\n```\n### ARCHIVO: new.py\n```\nx\n```\n"
	changed := ApplyFiles(root, answer)
	if len(changed) != 1 || changed[0] != "calc.py" {
		t.Fatalf("changed = %v", changed)
	}
	b, _ := os.ReadFile(filepath.Join(cand, "calc.py"))
	if string(b) != "def f():\n    return 2\n" {
		t.Fatalf("applied = %q", b)
	}
	if _, err := os.Stat(filepath.Join(root, Dir, "escape.py")); err == nil {
		t.Fatal("path escape was written")
	}
}

func TestRunnerLiteSendsCodeAndAppliesModernize(t *testing.T) {
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "app.py"), []byte("print('viejo')\n"), 0o644)
	all, _ := Phases()
	var prompts []string
	report := "\n\n" + strings.Repeat("- punto con evidencia app.py:1\n", 12)
	r := &Runner{Root: root, Phases: all, Lite: true, Agent: func(_ context.Context, prompt string, w io.Writer) error {
		prompts = append(prompts, prompt)
		if strings.Contains(prompt, "/ MODERNIZE") {
			fmt.Fprint(w, "### ARCHIVO: app.py\n```\nprint('nuevo')\n```\n## Resumen"+report)
			return nil
		}
		fmt.Fprint(w, "# Informe"+report)
		return nil
	}}
	if err := r.Run(context.Background(), func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(prompts[0], "1| print('viejo')") {
		t.Fatalf("discover prompt lacks code:\n%s", prompts[0])
	}
	if !strings.Contains(prompts[1], "Resultado previo: DISCOVER") {
		t.Fatal("understand prompt lacks previous artifact")
	}
	b, _ := os.ReadFile(filepath.Join(root, Dir, "candidate", "app.py"))
	if string(b) != "print('nuevo')\n" {
		t.Fatalf("candidate = %q", b)
	}
	orig, _ := os.ReadFile(filepath.Join(root, "app.py"))
	if string(orig) != "print('viejo')\n" {
		t.Fatal("original modified")
	}
	if !strings.Contains(prompts[6], "candidata/app.py") || !strings.Contains(prompts[6], "+print('nuevo')") {
		t.Fatalf("verify prompt lacks diff:\n%s", prompts[6])
	}
	mod, _ := os.ReadFile(filepath.Join(root, ".legacylift", "06-modernize.md"))
	if !strings.Contains(string(mod), "Archivos aplicados") {
		t.Fatalf("modernize artifact = %s", mod)
	}
}

func TestModernizeWithoutRealChangesFails(t *testing.T) {
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "app.py"), []byte("print(1)\n"), 0o644)
	all, _ := Phases()
	mod, _ := Select(all, "modernize", "modernize")
	calls := 0
	r := &Runner{Root: root, Phases: mod, Agent: func(_ context.Context, _ string, w io.Writer) error {
		calls++ // claims success but never edits the candidate
		fmt.Fprint(w, "# Modernize\n\n"+strings.Repeat("- cambié app.py\n", 20))
		return nil
	}}
	if err := r.Run(context.Background(), func(Event) {}); err == nil || !strings.Contains(err.Error(), "no modificó") {
		t.Fatalf("err = %v", err)
	}
	if calls != 2 {
		t.Fatalf("calls = %d", calls)
	}
}
