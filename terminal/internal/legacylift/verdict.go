package legacylift

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// The final verdict never comes from the model: the system runs the tests on
// both sides and compares real outputs, then writes the result itself.

// TestsResult runs the project's tests in dir. ran is false when no tests exist.
func TestsResult(ctx context.Context, dir string) (ran, ok bool) {
	cmd := TestCommand(dir)
	if cmd == nil {
		return false, false
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	c := exec.CommandContext(ctx, cmd[0], cmd[1:]...)
	c.Dir = dir
	c.Env = append(os.Environ(), "PYTHONPATH="+dir)
	return true, c.Run() == nil
}

// Behavior is one call executed on the original and on the candidate.
type Behavior struct {
	Call, Before, After string
}

func evalCalls(ctx context.Context, dir string, claims []Claim) map[string]string {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	in, _ := json.Marshal(claims)
	cmd := exec.CommandContext(ctx, "python3", "-c", pyChecker)
	cmd.Dir = dir
	cmd.Stdin = strings.NewReader(string(in))
	out, err := cmd.Output()
	res := map[string]string{}
	var got []Claim
	if err != nil || json.Unmarshal(out, &got) != nil {
		return res
	}
	for _, c := range got {
		if c.Err == "" {
			res[c.Call] = c.Got
		}
	}
	return res
}

// CompareBehavior runs every example call found in text on both versions.
func CompareBehavior(ctx context.Context, root, text string) []Behavior {
	cand := filepath.Join(root, Dir, "candidate")
	claims := ExtractClaims(text)
	if len(claims) == 0 || len(sourceFilesExt(root, ".py")) == 0 {
		return nil
	}
	before, after := evalCalls(ctx, root, claims), evalCalls(ctx, cand, claims)
	var out []Behavior
	for _, c := range claims {
		b, okb := before[c.Call]
		a, oka := after[c.Call]
		if okb && oka {
			out = append(out, Behavior{c.Call, b, a})
		}
	}
	return out
}

// VerdictReport builds the system-owned "Antes vs. después" section.
func VerdictReport(ctx context.Context, root, reports string) string {
	cand := filepath.Join(root, Dir, "candidate")
	ranO, okO := TestsResult(ctx, root)
	_, okC := TestsResult(ctx, cand)
	beh := CompareBehavior(ctx, root, reports)
	diffs := 0
	var b strings.Builder
	b.WriteString("\n\n## Veredicto del sistema (no lo escribe la IA)\n\n")
	mark := func(ok bool) string {
		if ok {
			return "✅ pasan"
		}
		return "❌ fallan"
	}
	if ranO {
		fmt.Fprintf(&b, "| Pruebas automáticas | Antes (original) | Después (candidata) |\n|---|---|---|\n| `%s` | %s | %s |\n\n",
			strings.Join(TestCommand(root), " "), mark(okO), mark(okC))
	} else {
		b.WriteString("No se detectaron pruebas automáticas: no hay forma de demostrar la equivalencia.\n\n")
	}
	if len(beh) > 0 {
		b.WriteString("### Comportamiento antes vs. después\n\n| Llamada | Antes | Después | |\n|---|---|---|---|\n")
		for _, x := range beh {
			m := "✅ igual"
			if x.Before != x.After {
				m = "❌ distinto"
				diffs++
			}
			fmt.Fprintf(&b, "| `%s` | %s | %s | %s |\n", x.Call, x.Before, x.After, m)
		}
		b.WriteString("\n")
	}
	switch {
	case !ranO:
		b.WriteString("**Veredicto: ⚠️ equivalencia NO demostrada** (sin pruebas). Revisa el diff manualmente.\n")
	case okO && !okC:
		b.WriteString("**Veredicto: ❌ NO equivalente**: las pruebas pasan en el original y fallan en la candidata.\n")
	case diffs > 0:
		fmt.Fprintf(&b, "**Veredicto: ❌ NO equivalente**: %d llamada(s) dan un resultado distinto antes y después.\n", diffs)
	case okO && okC:
		b.WriteString("**Veredicto: ✅ equivalencia respaldada por las pruebas** (y por los ejemplos comparados). No es una garantía total: solo cubre lo que las pruebas y los ejemplos ejercitan.\n")
	default:
		b.WriteString("**Veredicto: ⚠️ equivalencia NO demostrada**: las pruebas ya fallaban en el original.\n")
	}
	return b.String()
}
