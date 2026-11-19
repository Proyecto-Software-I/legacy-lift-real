package tools

import (
	"context"
	"path/filepath"
	"strings"

	"charm.land/fantasy"
	"github.com/charmbracelet/crush/internal/legacylift"
)

// RunTestsToolName runs only the project's detected test command. It is the
// LegacyLift replacement for bash: a small model can verify behaviour
// without being able to run arbitrary commands.
const RunTestsToolName = "run_tests"

type RunTestsParams struct {
	Target string `json:"target,omitempty" description:"\"original\" (default) or \"candidate\" (.legacylift/candidate)"`
}

func NewRunTestsTool(workingDir string) fantasy.AgentTool {
	return fantasy.NewAgentTool(
		RunTestsToolName,
		"Run the project's automated tests (detected automatically: python unittest, npm test, go test, mvn test) and return the output. target: \"original\" or \"candidate\".",
		func(ctx context.Context, params RunTestsParams, _ fantasy.ToolCall) (fantasy.ToolResponse, error) {
			dir := workingDir
			if strings.EqualFold(strings.TrimSpace(params.Target), "candidate") {
				dir = filepath.Join(workingDir, legacylift.Dir, "candidate")
			}
			return fantasy.NewTextResponse(legacylift.RunTests(ctx, dir)), nil
		},
	)
}
