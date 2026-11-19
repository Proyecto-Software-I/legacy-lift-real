package legacylift

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestExtractClaims(t *testing.T) {
	txt := "`total_final(10, 3, True)` devuelve 27.\n- `precio=50, cantidad=2, cliente_frecuente=True` → `total_final = 95`\n"
	got := ExtractClaims(txt)
	if len(got) != 2 || got[0].Call != "total_final(10, 3, True)" || got[1].Call != "total_final(precio=50, cantidad=2, cliente_frecuente=True)" || got[1].Expected != "95" {
		t.Fatalf("claims: %+v", got)
	}
}

func TestCheckClaimsFlagsInvented(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("sin python3")
	}
	dir := t.TempDir()
	code := "def total_final(precio, cantidad, cliente_frecuente):\n    b = precio * cantidad\n    return round(b - (b * 0.1 if cliente_frecuente else 0), 2)\n"
	if err := os.WriteFile(filepath.Join(dir, "calc.py"), []byte(code), 0o644); err != nil {
		t.Fatal(err)
	}
	txt := "total_final(10, 3, True) → 27\nprecio=50, cantidad=2, cliente_frecuente=True → total_final = 95\n__import__('os').system('x') → 1\n"
	res := CheckClaims(context.Background(), dir, txt)
	md, bad := ClaimsReport(res, "el código")
	if len(res) != 2 || bad != 1 || !strings.Contains(md, "❌ inventado") {
		t.Fatalf("bad=%d res=%+v\n%s", bad, res, md)
	}
}
