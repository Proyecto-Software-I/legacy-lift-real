package legacylift

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// Small models sometimes invent concrete examples ("f(50, 2, True) → 95").
// CheckClaims extracts those examples from a report and runs them against the
// real code, so invented values are flagged instead of trusted.

// Claim is one "call → expected value" example found in a report.
type Claim struct {
	Call     string `json:"call"`
	Expected string `json:"expected"`
	Got      string `json:"got"`
	Ok       bool   `json:"ok"`
	Err      string `json:"err,omitempty"`
}

var (
	num = `(-?\d+(?:[.,]\d+)?)`
	// total_final(10, 3, True) → 27   |   `f(1)` devuelve `2`
	reCall = regexp.MustCompile("`?([A-Za-z_]\\w*\\([^()\\n]*\\))`?\\s*(?:→|->|=>|==|=|devuelve|retorna|da|returns?)\\s*`?" + num)
	// precio=10, cantidad=3, cliente_frecuente=True → total_final = 27
	reKw = regexp.MustCompile("((?:[A-Za-z_]\\w*\\s*=\\s*[^,→\\n]+,\\s*)*[A-Za-z_]\\w*\\s*=\\s*[^,→\\n]+?)\\s*(?:→|->|=>)\\s*`?([A-Za-z_]\\w*)`?\\s*=\\s*`?" + num)
)

// ExtractClaims returns the distinct call examples written in a report.
func ExtractClaims(text string) []Claim {
	seen := map[string]bool{}
	var out []Claim
	add := func(call, exp string) {
		call = strings.TrimSpace(call)
		exp = strings.ReplaceAll(exp, ",", ".")
		if seen[call+exp] {
			return
		}
		seen[call+exp] = true
		out = append(out, Claim{Call: call, Expected: exp})
	}
	for _, m := range reCall.FindAllStringSubmatch(text, -1) {
		add(m[1], m[2])
	}
	for _, m := range reKw.FindAllStringSubmatch(text, -1) {
		add(m[2]+"("+strings.Trim(m[1], "` ")+")", m[3])
	}
	return out
}

// pyChecker evaluates each call with literal arguments only (nothing from the
// report is executed as code) against the project's own functions.
const pyChecker = `
import ast, importlib, json, os, sys
sys.path.insert(0, ".")
claims = json.load(sys.stdin)
funcs = {}
for dp, dn, fn in os.walk("."):
    dn[:] = [d for d in dn if not d.startswith(".") and d not in ("tests", "test", "venv", "node_modules", "__pycache__")]
    for f in fn:
        if f.endswith(".py") and not f.startswith("test"):
            mod = os.path.relpath(os.path.join(dp, f), ".")[:-3].replace(os.sep, ".")
            try:
                m = importlib.import_module(mod)
            except Exception:
                continue
            for k, v in vars(m).items():
                if callable(v) and getattr(v, "__module__", "") == m.__name__:
                    funcs.setdefault(k, v)
for c in claims:
    try:
        node = ast.parse(c["call"], mode="eval").body
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            raise ValueError("no es una llamada simple")
        if node.func.id not in funcs:
            raise ValueError("función no encontrada")
        args = [ast.literal_eval(a) for a in node.args]
        kw = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
        got = funcs[node.func.id](*args, **kw)
        c["got"] = repr(got)
        c["ok"] = abs(float(got) - float(c["expected"])) < 1e-6
    except Exception as e:
        c["err"] = str(e)[:120]
print(json.dumps(claims))
`

// CheckClaims runs the examples found in text against the code in dir.
// Only Python projects are supported; others return nil.
func CheckClaims(ctx context.Context, dir, text string) []Claim {
	claims := ExtractClaims(text)
	if len(claims) == 0 || len(sourceFilesExt(dir, ".py")) == 0 {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	in, _ := json.Marshal(claims)
	cmd := exec.CommandContext(ctx, "python3", "-c", pyChecker)
	cmd.Dir = dir
	cmd.Stdin = strings.NewReader(string(in))
	out, err := cmd.Output()
	if err != nil {
		return nil
	}
	var res []Claim
	if json.Unmarshal(out, &res) != nil {
		return nil
	}
	var checked []Claim
	for _, c := range res {
		if c.Err == "" {
			checked = append(checked, c)
		}
	}
	return checked
}

func sourceFilesExt(root, ext string) []string {
	var out []string
	for _, f := range sourceFiles(root) {
		if strings.HasSuffix(f, ext) {
			out = append(out, f)
		}
	}
	return out
}

// ClaimsReport renders a Markdown section; bad counts the invented values.
func ClaimsReport(claims []Claim, where string) (md string, bad int) {
	if len(claims) == 0 {
		return "", 0
	}
	var b strings.Builder
	b.WriteString("\n\n## Comprobación automática de ejemplos\n\n")
	fmt.Fprintf(&b, "LegacyLift ejecutó los ejemplos citados en este informe contra %s:\n\n", where)
	b.WriteString("| Llamada | El informe dice | Resultado real | |\n|---|---|---|---|\n")
	for _, c := range claims {
		mark := "✅"
		if !c.Ok {
			mark = "❌ inventado"
			bad++
		}
		fmt.Fprintf(&b, "| `%s` | %s | %s | %s |\n", c.Call, c.Expected, c.Got, mark)
	}
	if bad > 0 {
		fmt.Fprintf(&b, "\n**Atención:** %d de %d ejemplos no coinciden con el código real. No confíes en las afirmaciones de equivalencia de este informe sin revisarlas.\n", bad, len(claims))
	}
	return b.String(), bad
}
