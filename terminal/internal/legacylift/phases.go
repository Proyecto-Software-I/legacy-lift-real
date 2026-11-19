// Package legacylift turns Crush into a phase runner for LegacyLift AI:
// instead of a chat where the user writes prompts, it executes the
// documented modernization phases one after another on the current
// directory and stores every result as a Markdown artifact.
package legacylift

import (
	"embed"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
)

//go:embed phases/*.md
var phaseFS embed.FS

// Dir is the folder (relative to the project) where artifacts live.
const Dir = ".legacylift"

// Phase is one documented LegacyLift stage.
type Phase struct {
	ID       string // e.g. "discover"
	Num      string // e.g. "01"
	Title    string // e.g. "Discover"
	template string
}

// Artifact returns the artifact path (relative to the project root).
func (p Phase) Artifact() string {
	return filepath.ToSlash(filepath.Join(Dir, p.Num+"-"+p.ID+".md"))
}

// Prompt renders the phase template. prev is the previous artifact.
func (p Phase) Prompt(prev string) string {
	r := strings.NewReplacer("{{ARTIFACT}}", p.Artifact(), "{{PREV}}", prev)
	return "IDIOMA OBLIGATORIO: escribe TODO en español (no en portugués ni en inglés).\n" +
		"Ignora siempre `.legacylift/`, `.crush/`, `crush.json` y `.git/`: no forman parte del sistema legado.\n\n" +
		r.Replace(p.template) +
		"\n\nCuando termines, confirma en una línea que escribiste `" + p.Artifact() + "`."
}

// Phases returns the phases in execution order.
func Phases() ([]Phase, error) {
	entries, err := fs.ReadDir(phaseFS, "phases")
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	out := make([]Phase, 0, len(names))
	for _, n := range names {
		b, err := phaseFS.ReadFile("phases/" + n)
		if err != nil {
			return nil, err
		}
		base := strings.TrimSuffix(n, ".md")
		num, id, ok := strings.Cut(base, "-")
		if !ok {
			return nil, fmt.Errorf("invalid phase file name %q", n)
		}
		out = append(out, Phase{
			ID:       id,
			Num:      num,
			Title:    strings.ToUpper(id[:1]) + id[1:],
			template: string(b),
		})
	}
	return out, nil
}

// Select returns the phases from "from" up to "until" (inclusive).
// Empty values mean the first / last phase.
func Select(all []Phase, from, until string) ([]Phase, error) {
	start, end := 0, len(all)-1
	for i, p := range all {
		if from != "" && p.ID == from {
			start = i
		}
		if until != "" && p.ID == until {
			end = i
		}
	}
	if from != "" && all[start].ID != from {
		return nil, fmt.Errorf("unknown phase %q", from)
	}
	if until != "" && all[end].ID != until {
		return nil, fmt.Errorf("unknown phase %q", until)
	}
	if start > end {
		return nil, fmt.Errorf("phase %q comes after %q", from, until)
	}
	return all[start : end+1], nil
}
