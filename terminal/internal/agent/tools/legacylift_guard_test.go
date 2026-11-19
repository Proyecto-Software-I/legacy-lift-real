package tools

import (
	"path/filepath"
	"testing"
)

func TestLegacyliftWriteGuard(t *testing.T) {
	wd := "/proj"
	j := func(p string) string { return filepath.Join(wd, p) }
	t.Setenv("LEGACYLIFT_WRITE_GUARD", "candidate")
	for p, ok := range map[string]bool{
		".legacylift/candidate/src/a.js": true, ".legacylift/06-modernize.md": true,
		"src/a.js": false, "calculator.py": false, ".legacylift/x/y.md": false,
	} {
		if got := legacyliftWriteGuard(wd, j(p)) == ""; got != ok {
			t.Errorf("candidate %s: allowed=%v want %v", p, got, ok)
		}
	}
	if legacyliftWriteGuard(wd, "/etc/passwd") == "" {
		t.Error("outside project must be blocked")
	}
	t.Setenv("LEGACYLIFT_WRITE_GUARD", "artifacts")
	if legacyliftWriteGuard(wd, j(".legacylift/01-discover.md")) != "" || legacyliftWriteGuard(wd, j("src/a.js")) == "" {
		t.Error("artifacts mode")
	}
	t.Setenv("LEGACYLIFT_WRITE_GUARD", "")
	if legacyliftWriteGuard(wd, j("src/a.js")) != "" {
		t.Error("guard must be off outside LegacyLift")
	}
}
