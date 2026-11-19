package legacylift

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// DefaultOllamaURL is where a local Ollama server listens by default.
const DefaultOllamaURL = "http://localhost:11434"

func ollamaURL() string {
	if u := strings.TrimRight(os.Getenv("OLLAMA_HOST"), "/"); u != "" {
		if !strings.HasPrefix(u, "http") {
			u = "http://" + u
		}
		return u
	}
	return DefaultOllamaURL
}

// OllamaModels lists models installed in the local Ollama server.
func OllamaModels() ([]string, error) {
	c := http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get(ollamaURL() + "/api/tags")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var body struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(body.Models))
	for _, m := range body.Models {
		out = append(out, m.Name)
	}
	return out, nil
}

// projectConfigExists reports whether the project already has a Crush config.
func projectConfigExists(root string) bool {
	for _, n := range []string{"crush.json", ".crush.json"} {
		if _, err := os.Stat(filepath.Join(root, n)); err == nil {
			return true
		}
	}
	return false
}

// EnsureLocalAI writes a project config that points to the local Ollama
// server when the project has no config yet and Ollama is running. Other
// providers (Anthropic, OpenAI, OpenRouter, …) keep working through the
// regular Crush configuration and environment variables.
// It returns the chosen model ("" when nothing was written).
func EnsureLocalAI(root, wantModel string) (string, error) {
	if projectConfigExists(root) {
		return "", nil
	}
	models, err := OllamaModels()
	if err != nil || len(models) == 0 {
		return "", nil // no local AI: rely on configured providers
	}
	model := wantModel
	if prov, name, ok := strings.Cut(model, "/"); ok {
		if prov != "ollama" {
			return "", nil // another provider was requested explicitly
		}
		model = name
	}
	if model == "" {
		model = os.Getenv("LEGACYLIFT_MODEL")
	}
	if model == "" {
		model = models[0]
	}
	found := false
	for _, m := range models {
		if m == model {
			found = true
		}
	}
	if !found {
		return "", fmt.Errorf("el modelo %q no está instalado en Ollama (ollama pull %s)", model, model)
	}
	// Ollama defaults to a 4096-token context, too small for the phases:
	// derive a local copy of the model with a larger context once.
	if big := ensureLargeContext(model); big != model {
		model = big
		models = append(models, big)
	}
	cfg := map[string]any{
		"$schema": "https://charm.land/crush.json",
		"providers": map[string]any{
			"ollama": map[string]any{
				"name": "Ollama (local)",
				// Ollama speaks the OpenAI API; listing the installed models
				// explicitly avoids depending on remote model catalogs.
				"type":     "openai-compat",
				"base_url": ollamaURL() + "/v1/",
				"models":   ollamaModelList(models),
			},
		},
		"models": map[string]any{
			"large": map[string]any{"provider": "ollama", "model": model},
			"small": map[string]any{"provider": "ollama", "model": model},
		},
		"options": map[string]any{"progress": false, "request_timeout": 900},
	}
	b, _ := json.MarshalIndent(cfg, "", "  ")
	if err := os.WriteFile(filepath.Join(root, "crush.json"), b, 0o644); err != nil {
		return "", err
	}
	return model, nil
}

func ollamaModelList(names []string) []map[string]any {
	out := make([]map[string]any, 0, len(names))
	for _, n := range names {
		out = append(out, map[string]any{"id": n, "name": n, "context_window": 32768, "default_max_tokens": 4096})
	}
	return out
}

// ensureLargeContext creates (once) "legacylift-<model>" with num_ctx 16384.
// It shares the weights with the original, so it takes no extra disk space.
// On any error it returns the original name.
func ensureLargeContext(model string) string {
	if strings.HasPrefix(model, "legacylift-") {
		return model
	}
	name := "legacylift-" + strings.NewReplacer(":", "-", "/", "-").Replace(model)
	if installed, err := OllamaModels(); err == nil {
		for _, m := range installed {
			if m == name || m == name+":latest" {
				return name
			}
		}
	}
	body, _ := json.Marshal(map[string]any{"model": name, "from": model, "stream": false,
		"parameters": map[string]any{"num_ctx": 16384}})
	c := &http.Client{Timeout: 2 * time.Minute}
	resp, err := c.Post(ollamaURL()+"/api/create", "application/json", bytes.NewReader(body))
	if err != nil {
		return model
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return model
	}
	return name
}
