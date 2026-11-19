package legacylift

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"
	"github.com/stretchr/testify/require"
)

func writeFile(t *testing.T, path string) {
	t.Helper()
	require.NoError(t, os.MkdirAll(filepath.Dir(path), 0o755))
	require.NoError(t, os.WriteFile(path, []byte("x\n"), 0o644))
}

func projectTree(t *testing.T) string {
	t.Helper()
	cwd := t.TempDir()
	writeFile(t, filepath.Join(cwd, "cobol-app", "PAYROLL.cbl"))
	writeFile(t, filepath.Join(cwd, "cobol-app", "copy", "EMP.cpy"))
	writeFile(t, filepath.Join(cwd, "java-app", "src", "main", "App.java"))
	require.NoError(t, os.MkdirAll(filepath.Join(cwd, "java-app", Dir), 0o755))
	writeFile(t, filepath.Join(cwd, "maven-only", "pom.xml"))
	writeFile(t, filepath.Join(cwd, "docs", "README.md"))
	writeFile(t, filepath.Join(cwd, "node_modules", "lib", "index.js"))
	writeFile(t, filepath.Join(cwd, "vendor", "x.go"))
	writeFile(t, filepath.Join(cwd, ".hidden", "main.go"))
	return cwd
}

func TestProjectCandidates(t *testing.T) {
	t.Parallel()
	cwd := projectTree(t)
	other := t.TempDir()
	writeFile(t, filepath.Join(other, "main.go"))

	got := ProjectCandidates(cwd, []string{other, cwd, filepath.Join(cwd, "missing"), "relative/path", other})

	var paths, kinds []string
	for _, p := range got {
		paths = append(paths, p.Path)
		kinds = append(kinds, p.Kind)
	}
	require.Equal(t, []string{
		cwd,
		other,
		filepath.Join(cwd, "cobol-app"),
		filepath.Join(cwd, "java-app"),
		filepath.Join(cwd, "maven-only"),
	}, paths)
	require.Equal(t, []string{ProjectCurrent, ProjectRecent, ProjectSubdir, ProjectSubdir, ProjectSubdir}, kinds)

	require.Equal(t, 3, got[0].Sources, "cwd counts its tree but skips node_modules, vendor and hidden dirs")
	require.Equal(t, "subcarpeta · 2 archivos de código", got[2].Hint())
	require.True(t, got[3].HasResults)
	require.Equal(t, "subcarpeta · 1 archivo de código · con resultados previos", got[3].Hint())
	require.Equal(t, "subcarpeta · proyecto de build", got[4].Hint())
}

func TestValidateDir(t *testing.T) {
	t.Parallel()
	base := projectTree(t)

	dir, err := ValidateDir(base, "cobol-app")
	require.NoError(t, err)
	require.Equal(t, filepath.Join(base, "cobol-app"), dir)

	dir, err = ValidateDir(base, "  "+filepath.Join(base, "java-app")+" ")
	require.NoError(t, err)
	require.Equal(t, filepath.Join(base, "java-app"), dir)

	_, err = ValidateDir(base, "nope")
	require.ErrorContains(t, err, "no existe")
	_, err = ValidateDir(base, filepath.Join("cobol-app", "PAYROLL.cbl"))
	require.ErrorContains(t, err, "no es un directorio")
	_, err = ValidateDir(base, "")
	require.Error(t, err)
}

func values(opts []ModelOption) (enabled, disabled []string) {
	for _, o := range opts {
		v := o.Value
		if o.Auto {
			v = "auto"
		}
		if o.Disabled {
			disabled = append(disabled, o.Label)
		} else {
			enabled = append(enabled, v)
		}
	}
	return enabled, disabled
}

func TestBuildModelOptions(t *testing.T) {
	t.Parallel()

	t.Run("ollama up and providers configured", func(t *testing.T) {
		t.Parallel()
		in := ModelInputs{
			OllamaURL: "http://localhost:11434",
			Ollama:    []string{"qwen3.5:9b", "legacylift-qwen3.5-9b"},
			Providers: []ProviderModels{
				{ID: "openai", Name: "OpenAI", Models: []string{"gpt-5.5", "gpt-5.5-mini"}},
				{ID: "anthropic", Name: "Anthropic", Models: []string{"claude-sonnet-5-5"}},
				{ID: "ollama", Name: "Ollama (local)", Models: []string{"stale"}},
			},
		}
		opts := BuildModelOptions(in)
		enabled, disabled := values(opts)
		require.Equal(t, []string{
			"auto",
			"ollama/qwen3.5:9b",
			"ollama/legacylift-qwen3.5-9b",
			"anthropic/claude-sonnet-5-5",
			"openai/gpt-5.5",
			"openai/gpt-5.5-mini",
		}, enabled)
		require.Empty(t, disabled)
		require.Equal(t, "Ollama (local) · qwen3.5:9b", opts[1].Label)
		require.Equal(t, "Anthropic · claude-sonnet-5-5", opts[3].Label)
		require.Equal(t, "Anthropic", opts[3].Group)
		for _, o := range opts[1:] {
			prov, model, _ := strings.Cut(o.Value, "/")
			require.Contains(t, o.Label, model, "label must show the real model id")
			if prov == "ollama" {
				require.Contains(t, o.Label, "Ollama")
			}
		}
		require.Contains(t, opts[0].Hint, "Ollama (local) · qwen3.5:9b")
	})

	t.Run("ollama down", func(t *testing.T) {
		t.Parallel()
		in := ModelInputs{
			OllamaURL: "http://127.0.0.1:9",
			OllamaErr: errors.New("connection refused"),
			Providers: []ProviderModels{{ID: "anthropic", Name: "Anthropic", Models: []string{"claude-sonnet-5-5"}}},
			Large:     "anthropic/claude-sonnet-5-5",
		}
		enabled, disabled := values(BuildModelOptions(in))
		require.Equal(t, []string{"auto", "anthropic/claude-sonnet-5-5"}, enabled)
		require.Equal(t, []string{"Ollama no responde en http://127.0.0.1:9"}, disabled)
		desc, local := AutoChoice(in)
		require.Contains(t, desc, "anthropic/claude-sonnet-5-5")
		require.False(t, local)
	})

	t.Run("project config limits ollama models", func(t *testing.T) {
		t.Parallel()
		in := ModelInputs{
			Ollama:           []string{"qwen3.5:9b", "llama3:8b"},
			ProjectHasConfig: true,
			Providers:        []ProviderModels{{ID: "ollama", Name: "Ollama (local)", Models: []string{"qwen3.5:9b"}}},
			Large:            "ollama/qwen3.5:9b",
		}
		enabled, disabled := values(BuildModelOptions(in))
		require.Equal(t, []string{"auto", "ollama/qwen3.5:9b"}, enabled)
		require.Equal(t, []string{"Ollama (local) · llama3:8b", "Ningún proveedor en la nube configurado"}, disabled)
		_, local := AutoChoice(in)
		require.True(t, local)
	})

	t.Run("nothing available", func(t *testing.T) {
		t.Parallel()
		in := ModelInputs{OllamaErr: errors.New("down")}
		enabled, disabled := values(BuildModelOptions(in))
		require.Equal(t, []string{"auto"}, enabled)
		require.Len(t, disabled, 2)
	})
}

func TestChooseMode(t *testing.T) {
	t.Parallel()
	require.Equal(t, "lite", ChooseMode("", false, false, UsesLocal("ollama/qwen3.5:9b")))
	require.Equal(t, "lite", ChooseMode("", false, false, UsesLocal("local/x")))
	require.Equal(t, "full", ChooseMode("", false, false, UsesLocal("anthropic/claude-sonnet-5-5")))
	require.Equal(t, "full", ChooseMode("", false, true, true))
	require.Equal(t, "lite", ChooseMode("", true, false, false))
	require.Equal(t, "agent", ChooseMode("agent", true, false, false))
}

func key(s string) tea.KeyPressMsg {
	switch s {
	case "enter":
		return tea.KeyPressMsg{Code: tea.KeyEnter}
	case "esc":
		return tea.KeyPressMsg{Code: tea.KeyEscape}
	case "down":
		return tea.KeyPressMsg{Code: tea.KeyDown}
	case "up":
		return tea.KeyPressMsg{Code: tea.KeyUp}
	case "right":
		return tea.KeyPressMsg{Code: tea.KeyRight}
	case "end":
		return tea.KeyPressMsg{Code: tea.KeyEnd}
	case "ctrl+c":
		return tea.KeyPressMsg{Code: 'c', Mod: tea.ModCtrl}
	}
	r := []rune(s)[0]
	return tea.KeyPressMsg{Code: r, Text: s}
}

// send feeds a message and runs any resulting command, feeding a model
// discovery result back. Slow commands (cursor blink ticks) are ignored.
func send(t *testing.T, m *Picker, msg tea.Msg) tea.Msg {
	t.Helper()
	_, cmd := m.Update(msg)
	if cmd == nil {
		return nil
	}
	ch := make(chan tea.Msg, 1)
	go func() { ch <- cmd() }()
	var out tea.Msg
	select {
	case out = <-ch:
	case <-time.After(100 * time.Millisecond):
		return nil
	}
	if loaded, ok := out.(modelsLoadedMsg); ok {
		_, _ = m.Update(loaded)
	}
	return out
}

func typeText(t *testing.T, m *Picker, s string) {
	t.Helper()
	for _, r := range s {
		send(t, m, key(string(r)))
	}
}

func testInputs() ModelInputs {
	return ModelInputs{
		OllamaURL: "http://localhost:11434",
		Ollama:    []string{"qwen3.5:9b", "legacylift-qwen3.5-9b"},
		Providers: []ProviderModels{
			{ID: "anthropic", Name: "Anthropic", Models: []string{"claude-haiku-5-5", "claude-sonnet-5-5"}},
			{ID: "openai", Name: "OpenAI", Models: []string{"gpt-5.5", "gpt-5.5-mini"}},
		},
	}
}

func testPicker(t *testing.T) (*Picker, string) {
	t.Helper()
	cwd := projectTree(t)
	phases, err := Phases()
	require.NoError(t, err)
	m := NewPicker(PickerOptions{
		Cwd:      cwd,
		Phases:   phases,
		Discover: func(string) ModelInputs { return testInputs() },
	})
	_, _ = m.Update(tea.WindowSizeMsg{Width: 100, Height: 30})
	return m, cwd
}

func TestPickerHappyPath(t *testing.T) {
	t.Parallel()
	m, cwd := testPicker(t)

	// Project: second row is cobol-app.
	send(t, m, key("j"))
	send(t, m, key("enter"))
	require.Equal(t, stepModel, m.step)
	require.False(t, m.loading)

	// Model: skip "Automático", take the first Ollama model.
	send(t, m, key("down"))
	send(t, m, key("enter"))
	require.Equal(t, stepConfirm, m.step)
	require.Equal(t, "ollama/qwen3.5:9b", m.chosen.Value)
	require.Contains(t, ansi.Strip(m.Render()), "lite")

	// Narrow the phases: start at the second one.
	send(t, m, key("down"))
	send(t, m, key("right"))
	send(t, m, key("up"))
	out := send(t, m, key("enter"))
	require.IsType(t, tea.QuitMsg{}, out)

	res, err := m.Result()
	require.NoError(t, err)
	require.Equal(t, PickerResult{
		Dir:   filepath.Join(cwd, "cobol-app"),
		Model: "ollama/qwen3.5:9b",
		From:  m.opts.Phases[1].ID,
	}, res)
}

func TestPickerAutomaticAndFilter(t *testing.T) {
	t.Parallel()
	m, cwd := testPicker(t)
	send(t, m, key("enter"))

	// Filter to OpenAI and pick the mini model.
	send(t, m, key("/"))
	require.True(t, m.filtering)
	typeText(t, m, "mini")
	require.Len(t, m.visibleModels(), 1)
	send(t, m, key("enter"))
	require.Equal(t, "openai/gpt-5.5-mini", m.chosen.Value)
	require.Contains(t, ansi.Strip(m.Render()), "full")

	// Back to the model list and choose Automático instead.
	send(t, m, key("esc"))
	require.Equal(t, stepModel, m.step)
	send(t, m, key("/"))
	send(t, m, key("esc"))
	require.Empty(t, m.filter.Value())
	m.mCursor = 0
	send(t, m, key("enter"))
	send(t, m, key("enter"))

	res, err := m.Result()
	require.NoError(t, err)
	require.Equal(t, PickerResult{Dir: cwd}, res)
}

func TestPickerCustomPath(t *testing.T) {
	t.Parallel()
	m, cwd := testPicker(t)
	send(t, m, key("end"))
	send(t, m, key("enter"))
	require.Equal(t, stepPath, m.step)

	typeText(t, m, "nope")
	send(t, m, key("enter"))
	require.Equal(t, stepPath, m.step)
	require.Contains(t, m.pathErr, "no existe")

	m.path.SetValue("java-app")
	send(t, m, key("enter"))
	require.Equal(t, stepModel, m.step)
	require.Equal(t, filepath.Join(cwd, "java-app"), m.dir)
}

func TestPickerCancel(t *testing.T) {
	t.Parallel()
	for _, keys := range [][]string{{"q"}, {"ctrl+c"}, {"esc"}, {"enter", "ctrl+c"}, {"enter", "esc", "q"}} {
		m, _ := testPicker(t)
		var out tea.Msg
		for _, k := range keys {
			out = send(t, m, key(k))
		}
		require.IsType(t, tea.QuitMsg{}, out, "keys %v", keys)
		_, err := m.Result()
		require.ErrorIs(t, err, ErrPickerCancelled, "keys %v", keys)
	}
}

// TestPickerRender renders every step at 100x30; run with -v to see them.
func TestPickerRender(t *testing.T) {
	t.Parallel()
	m, _ := testPicker(t)
	screens := map[string]string{}
	screens["1-proyecto"] = ansi.Strip(m.Render())
	send(t, m, key("end"))
	send(t, m, key("enter"))
	screens["1b-ruta"] = ansi.Strip(m.Render())
	send(t, m, key("esc"))
	send(t, m, key("up"))
	m.pCursor = 0
	send(t, m, key("enter"))
	screens["2-modelo"] = ansi.Strip(m.Render())
	send(t, m, key("down"))
	send(t, m, key("enter"))
	screens["3-confirmacion"] = ansi.Strip(m.Render())

	for _, name := range []string{"1-proyecto", "1b-ruta", "2-modelo", "3-confirmacion"} {
		s := screens[name]
		lines := strings.Split(s, "\n")
		require.LessOrEqual(t, len(lines), 30, name)
		for _, l := range lines {
			require.LessOrEqual(t, ansi.StringWidth(l), 100, name)
		}
		t.Logf("=== %s ===\n%s", name, s)
	}
	require.Contains(t, screens["1-proyecto"], "Escribir otra ruta…")
	require.Contains(t, screens["2-modelo"], "Ollama (local) · qwen3.5:9b")
	require.Contains(t, screens["3-confirmacion"], "--model ollama/qwen3.5:9b")
}
