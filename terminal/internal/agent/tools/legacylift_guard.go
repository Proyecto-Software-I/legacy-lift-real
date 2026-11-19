package tools

import (
	"os"
	"path/filepath"
	"strings"
)

// legacyliftWriteGuard enforces LegacyLift's promise that the original code is
// never modified. The runner sets LEGACYLIFT_WRITE_GUARD per phase:
//   - "artifacts": only files under .legacylift/ may be written (analysis phases);
//   - "candidate": also files under .legacylift/candidate/ (Modernize).
//
// It returns a message for the model when the path is not allowed.
func legacyliftWriteGuard(workingDir, absPath string) string {
	mode := os.Getenv("LEGACYLIFT_WRITE_GUARD")
	if mode == "" {
		return ""
	}
	rel, err := filepath.Rel(workingDir, absPath)
	if err != nil || strings.HasPrefix(rel, "..") {
		return "No puedes escribir fuera del proyecto."
	}
	rel = filepath.ToSlash(rel)
	if mode == "candidate" {
		if strings.HasPrefix(rel, ".legacylift/candidate/") {
			return ""
		}
		if strings.HasPrefix(rel, ".legacylift/") && strings.HasSuffix(rel, ".md") && !strings.Contains(strings.TrimPrefix(rel, ".legacylift/"), "/") {
			return ""
		}
		return "Prohibido modificar el código original (" + rel + "). En Modernize solo puedes editar la copia: usa la ruta .legacylift/candidate/" + rel
	}
	if strings.HasPrefix(rel, ".legacylift/") {
		return ""
	}
	return "Prohibido modificar el código original (" + rel + "). En esta fase solo puedes escribir el informe dentro de .legacylift/."
}
