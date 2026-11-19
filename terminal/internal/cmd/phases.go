package cmd

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"slices"
	"strings"

	"github.com/charmbracelet/crush/internal/config"
	"github.com/charmbracelet/crush/internal/event"
	"github.com/charmbracelet/crush/internal/legacylift"
	"github.com/charmbracelet/crush/internal/projects"
	"github.com/charmbracelet/crush/internal/workspace"
	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"
)

func init() {
	rootCmd.Flags().String("from", "", "First phase to run (discover, understand, assess, plan, baseline, modernize, verify)")
	rootCmd.Flags().String("until", "", "Last phase to run")
	rootCmd.Flags().StringP("model", "m", "", "Model to use: 'model' or 'provider/model'. Local Ollama is used automatically when no provider is configured")
	rootCmd.Flags().Bool("plain", false, "Print progress lines instead of the TUI")
	rootCmd.Flags().Bool("lite", false, "Lite mode for small models: no agent tools, short prompts (default with Ollama)")
	rootCmd.Flags().Bool("full", false, "Full agent mode with tools even for local models")
	rootCmd.Flags().String("mode", "", "lite (no tools), agent (reduced tools per phase) or full (all Crush tools)")
	rootCmd.Flags().String("record", "", "Append every phase prompt/answer to this JSONL file (training data)")
	rootCmd.Flags().Bool("no-picker", false, "Skip the interactive project/model picker shown when no flags are given")
	rootCmd.AddCommand(phasesListCmd)
}

var phasesListCmd = &cobra.Command{
	Use:   "phases",
	Short: "List the LegacyLift phases and their artifacts",
	RunE: func(cmd *cobra.Command, _ []string) error {
		all, err := legacylift.Phases()
		if err != nil {
			return err
		}
		for _, p := range all {
			fmt.Fprintf(cmd.OutOrStdout(), "%s  %-10s → %s\n", p.Num, p.ID, p.Artifact())
		}
		return nil
	},
}

// runPhases replaces the chat TUI: it executes the documented LegacyLift
// phases on the current directory without asking the user for prompts.
func runPhases(cmd *cobra.Command) error {
	from, _ := cmd.Flags().GetString("from")
	until, _ := cmd.Flags().GetString("until")
	modelFlag, _ := cmd.Flags().GetString("model")
	plain, _ := cmd.Flags().GetBool("plain")
	liteFlag, _ := cmd.Flags().GetBool("lite")
	fullFlag, _ := cmd.Flags().GetBool("full")
	modeFlag, _ := cmd.Flags().GetString("mode")
	rec, _ := cmd.Flags().GetString("record")

	// Without flags in a terminal, let the user pick project and model
	// first. The choice is applied as if it had been passed as flags, so
	// everything below (root, EnsureLocalAI, workspace) uses it.
	if shouldShowPicker(cmd) {
		res, err := runPicker(cmd, func(local bool) string {
			return legacylift.ChooseMode(modeFlag, liteFlag, fullFlag, local)
		})
		if errors.Is(err, legacylift.ErrPickerCancelled) {
			return nil
		}
		if err != nil {
			return err
		}
		if err := cmd.Flags().Set("cwd", res.Dir); err != nil {
			return err
		}
		if res.Model != "" {
			modelFlag = res.Model
		}
		from, until = res.From, res.Until
	}

	root, err := ResolveCwd(cmd)
	if err != nil {
		return err
	}
	all, err := legacylift.Phases()
	if err != nil {
		return err
	}
	phases, err := legacylift.Select(all, from, until)
	if err != nil {
		return err
	}

	// Local AI first: if nothing is configured and Ollama is running,
	// point the project at it.
	localModel, err := legacylift.EnsureLocalAI(root, modelFlag)
	if err != nil {
		return err
	}
	if localModel != "" && (modelFlag == "" || !strings.Contains(modelFlag, "/")) {
		modelFlag = "ollama/" + localModel
	} else if modelFlag != "" && !strings.Contains(modelFlag, "/") {
		// A bare name like "qwen3:4b" that Ollama has installed means the local provider.
		if installed, _ := legacylift.OllamaModels(); slices.Contains(installed, modelFlag) {
			modelFlag = "ollama/" + modelFlag
		}
	}

	// Every phase is unattended: never stop to ask for permissions.
	if c, _ := cmd.Flags().GetCount("yolo"); c == 0 {
		_ = cmd.Flags().Set("yolo", "1")
	}

	ws, cleanup, err := setupLocalWorkspace(cmd)
	if err != nil {
		return err
	}
	defer cleanup()
	event.SetNonInteractive(true)
	event.AppInitialized()

	if !ws.Config().IsConfigured() {
		return fmt.Errorf("no hay IA configurada: inicia Ollama (ollama serve) o configura un proveedor (ANTHROPIC_API_KEY, OPENAI_API_KEY, … o crush.json)")
	}
	app := ws.(*workspace.AppWorkspace).App()

	// Modes: "full" (Crush agent, all tools — cloud models), "agent"
	// (reduced per-phase tools + short prompt — small local models,
	// fine-tuning target) and "lite" (no tools: Go gathers everything).
	cfg := ws.Config()
	usesLocal := legacylift.UsesLocal(modelFlag)
	if modelFlag == "" && cfg.Models != nil {
		p := cfg.Models["large"].Provider
		usesLocal = p == "ollama" || p == "local"
	}
	mode := legacylift.ChooseMode(modeFlag, liteFlag, fullFlag, usesLocal)
	if mode != "lite" && mode != "agent" && mode != "full" {
		return fmt.Errorf("modo inválido %q (lite, agent o full)", mode)
	}
	if mode != "full" {
		_ = os.Setenv("LEGACYLIFT_MODE", mode)
	}
	setTools := func(names []string) {
		if a, ok := cfg.Agents[config.AgentCoder]; ok {
			a.AllowedTools = names
			cfg.Agents[config.AgentCoder] = a
		}
	}
	if mode == "lite" {
		setTools([]string{})
	}

	agent := func(ctx context.Context, prompt string, w io.Writer) error {
		return app.RunNonInteractive(ctx, w, prompt, modelFlag, "", "", true, "", false)
	}
	runner := &legacylift.Runner{Root: root, Phases: phases, Agent: agent, Lite: mode == "lite"}
	if mode == "agent" {
		runner.BeforePhase = func(ph legacylift.Phase) { setTools(legacylift.AgentTools(ph.ID)) }
	}
	if rec != "" {
		runner.Agent = legacylift.Recorder(rec, mode, modelFlag, agent)
	}

	modelName := modelFlag
	if modelName == "" {
		if m := ws.Config().Models; m != nil {
			if large, ok := m["large"]; ok {
				modelName = large.Provider + "/" + large.Model
			}
		}
	}

	if plain || !term.IsTerminal(os.Stdout.Fd()) {
		fmt.Fprintln(cmd.OutOrStdout(), "modo: "+mode)
		return legacylift.RunPlain(cmd.Context(), runner, func(s string) { fmt.Fprintln(cmd.OutOrStdout(), s) })
	}
	restoreStderr, _ := detachStderr()
	defer restoreStderr()
	modelName += " · modo " + mode
	return legacylift.RunTUI(cmd.Context(), runner, modelName)
}

// shouldShowPicker reports whether the pre-launch picker runs: only in an
// interactive terminal and when no flag already decides project, model,
// phases or output.
func shouldShowPicker(cmd *cobra.Command) bool {
	if noPicker, _ := cmd.Flags().GetBool("no-picker"); noPicker {
		return false
	}
	for _, f := range []string{"cwd", "model", "plain", "from", "until"} {
		if cmd.Flags().Changed(f) {
			return false
		}
	}
	return term.IsTerminal(os.Stdout.Fd()) && term.IsTerminal(os.Stdin.Fd())
}

// runPicker gathers recent projects and phases and shows the picker.
func runPicker(cmd *cobra.Command, mode func(usesLocal bool) string) (legacylift.PickerResult, error) {
	cwd, err := os.Getwd()
	if err != nil {
		return legacylift.PickerResult{}, fmt.Errorf("failed to get current working directory: %v", err)
	}
	var recent []string
	if list, err := projects.List(); err == nil {
		for _, p := range list {
			recent = append(recent, p.Path)
		}
	}
	phases, _ := legacylift.Phases()
	dataDir, _ := cmd.Flags().GetString("data-dir")
	ctx := cmd.Context()
	return legacylift.RunPicker(ctx, legacylift.PickerOptions{
		Cwd:    cwd,
		Recent: recent,
		Phases: phases,
		Mode:   mode,
		Discover: func(dir string) legacylift.ModelInputs {
			return legacylift.DiscoverModels(dir, func(dir string) ([]legacylift.ProviderModels, string, error) {
				return configuredModels(ctx, dir, dataDir)
			})
		},
	})
}

// configuredModels lists the providers configured for dir (API keys, OAuth
// logins, config files), like `legacylift models` does for configured
// providers. Only models that --model resolves and the credential can use
// are returned: the usable models (subscription catalog for OAuth logins)
// that also appear in the provider's model list, which is what the
// non-interactive model override searches.
func configuredModels(ctx context.Context, dir, dataDir string) ([]legacylift.ProviderModels, string, error) {
	store, err := config.Init(ctx, dir, dataDir, false)
	if err != nil {
		return nil, "", err
	}
	cfg := store.Config()
	var out []legacylift.ProviderModels
	for id, p := range cfg.Providers.Seq2() {
		if p.Disable {
			continue
		}
		resolvable := make(map[string]bool, len(p.Models))
		for _, m := range p.Models {
			resolvable[m.ID] = true
		}
		pm := legacylift.ProviderModels{ID: id, Name: cmp.Or(p.Name, id)}
		for _, m := range p.UsableModels() {
			if resolvable[m.ID] && !slices.Contains(pm.Models, m.ID) {
				pm.Models = append(pm.Models, m.ID)
			}
		}
		if len(pm.Models) > 0 {
			out = append(out, pm)
		}
	}
	var large string
	if sm, ok := cfg.Models[config.SelectedModelTypeLarge]; ok && sm.Provider != "" {
		large = sm.Provider + "/" + sm.Model
	}
	return out, large, nil
}
