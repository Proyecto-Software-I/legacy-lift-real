package legacylift

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func writeProj(t *testing.T, dir, code string) {
	t.Helper()
	_ = os.MkdirAll(filepath.Join(dir, "tests"), 0o755)
	_ = os.WriteFile(filepath.Join(dir, "calc.py"), []byte(code), 0o644)
	test := "import unittest\nfrom calc import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(2), 4)\n"
	_ = os.WriteFile(filepath.Join(dir, "tests", "test_calc.py"), []byte(test), 0o644)
}

func TestVerdictDetectsBehaviorChange(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("sin python3")
	}
	root := t.TempDir()
	writeProj(t, root, "def f(x):\n    return round(x * 2.0, 1)\n")
	// candidate: same for integers (tests pass) but different for decimals
	writeProj(t, filepath.Join(root, Dir, "candidate"), "def f(x):\n    return x * 2.0\n")
	v := VerdictReport(context.Background(), root, "f(2) → 4\nf(1.234) → 2.5\n")
	if !strings.Contains(v, "✅ pasan") || !strings.Contains(v, "❌ distinto") || !strings.Contains(v, "NO equivalente") {
		t.Fatal(v)
	}
}

func TestVerdictEquivalent(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("sin python3")
	}
	root := t.TempDir()
	writeProj(t, root, "def f(x):\n    return (x * 2)\n")
	writeProj(t, filepath.Join(root, Dir, "candidate"), "def f(x):\n    return x * 2\n")
	v := VerdictReport(context.Background(), root, "f(2) → 4\n")
	if !strings.Contains(v, "equivalencia respaldada") {
		t.Fatal(v)
	}
}
