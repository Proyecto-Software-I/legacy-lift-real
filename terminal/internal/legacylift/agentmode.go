package legacylift

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"strings"
	"sync"
	"time"
)

// AgentTools returns the reduced tool set for a phase in "agent" mode.
// Small models handle a few well-described tools far better than 30+.
func AgentTools(phase string) []string {
	read := []string{"ls", "glob", "grep", "view", "write"}
	switch phase {
	case "baseline", "verify":
		return append(read, "run_tests")
	case "modernize":
		return []string{"ls", "view", "edit", "write"}
	default:
		return read
	}
}

// Record is one phase execution saved for later training/evaluation.
type Record struct {
	Time   time.Time `json:"time"`
	Mode   string    `json:"mode"`
	Model  string    `json:"model"`
	Prompt string    `json:"prompt"`
	Answer string    `json:"answer"`
	Error  string    `json:"error,omitempty"`
	Millis int64     `json:"millis"`
}

var recordMu sync.Mutex

// Recorder wraps an Agent and appends every call to a JSONL file.
func Recorder(path, mode, model string, next Agent) Agent {
	return func(ctx context.Context, prompt string, w io.Writer) error {
		var buf strings.Builder
		start := time.Now()
		err := next(ctx, prompt, io.MultiWriter(w, &buf))
		rec := Record{Time: start, Mode: mode, Model: model, Prompt: prompt,
			Answer: buf.String(), Millis: time.Since(start).Milliseconds()}
		if err != nil {
			rec.Error = err.Error()
		}
		b, _ := json.Marshal(rec)
		recordMu.Lock()
		defer recordMu.Unlock()
		f, ferr := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if ferr == nil {
			_, _ = f.Write(append(b, '\n'))
			_ = f.Close()
		}
		return err
	}
}
