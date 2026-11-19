package legacylift

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

// ErrPickerCancelled is returned by [RunPicker] when the user leaves the
// picker without starting a run.
var ErrPickerCancelled = errors.New("selección cancelada")

// Picker palette. It mirrors the run TUI accents but uses its own names so
// both screens can evolve independently.
var (
	pickerAccent = lipgloss.Color("#22D3EE")
	pickerOK     = lipgloss.Color("#4ADE80")
	pickerErr    = lipgloss.Color("#F87171")
	pickerDim    = lipgloss.Color("#6B7280")

	pickerTitleStyle    = lipgloss.NewStyle().Bold(true).Foreground(pickerAccent)
	pickerDimStyle      = lipgloss.NewStyle().Foreground(pickerDim)
	pickerSelStyle      = lipgloss.NewStyle().Bold(true).Foreground(pickerAccent)
	pickerHintStyle     = lipgloss.NewStyle().Foreground(pickerOK)
	pickerErrStyle      = lipgloss.NewStyle().Foreground(pickerErr)
	pickerDisabledStyle = lipgloss.NewStyle().Foreground(pickerDim).Italic(true)
	pickerBoxStyle      = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(pickerDim).Padding(0, 1)
)

// ---------------------------------------------------------------------------
// Project candidates
// ---------------------------------------------------------------------------

// Project kinds shown as hints in the picker.
const (
	ProjectCurrent = "directorio actual"
	ProjectRecent  = "reciente"
	ProjectSubdir  = "subcarpeta"
)

const (
	maxRecentProjects = 8
	maxSubdirProjects = 40
	maxSourceCount    = 200  // stop counting source files after this many
	maxWalkEntries    = 2000 // stop walking a candidate after this many entries
	maxWalkDepth      = 4
)

// ProjectOption is one selectable project directory.
type ProjectOption struct {
	Path       string // absolute path
	Name       string // base name
	Kind       string // ProjectCurrent, ProjectRecent or ProjectSubdir
	Sources    int    // number of source files found (capped at maxSourceCount)
	Manifest   bool   // has a build manifest (pom.xml, go.mod, package.json, …)
	HasResults bool   // contains a previous .legacylift/ folder
}

// Hint describes the option in plain words, e.g.
// "subcarpeta · 12 archivos de código · con resultados previos".
func (p ProjectOption) Hint() string {
	parts := []string{p.Kind}
	switch {
	case p.Sources >= maxSourceCount:
		parts = append(parts, fmt.Sprintf("%d+ archivos de código", maxSourceCount))
	case p.Sources == 1:
		parts = append(parts, "1 archivo de código")
	case p.Sources > 1:
		parts = append(parts, fmt.Sprintf("%d archivos de código", p.Sources))
	case p.Manifest:
		parts = append(parts, "proyecto de build")
	default:
		parts = append(parts, "sin código detectado")
	}
	if p.HasResults {
		parts = append(parts, "con resultados previos")
	}
	return strings.Join(parts, " · ")
}

// sourceExts are file extensions counted as source code, legacy languages
// included.
var sourceExts = map[string]bool{
	".cbl": true, ".cob": true, ".cobol": true, ".cpy": true, ".jcl": true, ".pli": true,
	".pl1": true, ".rpg": true, ".rpgle": true, ".asm": true, ".bms": true, ".nat": true,
	".4gl": true, ".prg": true, ".abap": true, ".ada": true, ".adb": true, ".ads": true,
	".java": true, ".kt": true, ".scala": true, ".groovy": true, ".go": true, ".py": true,
	".js": true, ".jsx": true, ".ts": true, ".tsx": true, ".mjs": true, ".vue": true,
	".svelte": true, ".c": true, ".h": true, ".cc": true, ".cpp": true, ".hpp": true,
	".cs": true, ".vb": true, ".bas": true, ".frm": true, ".cls": true, ".rb": true,
	".php": true, ".pl": true, ".pm": true, ".rs": true, ".swift": true, ".m": true,
	".f": true, ".for": true, ".f77": true, ".f90": true, ".pas": true, ".dpr": true,
	".sql": true, ".sh": true, ".bat": true, ".ps1": true, ".lua": true, ".dart": true,
	".ex": true, ".exs": true, ".erl": true, ".hs": true, ".clj": true, ".r": true,
	".lisp": true, ".tcl": true,
}

// manifestFiles mark a directory as a code project even when its sources are
// deeper than the walk limit.
var manifestFiles = map[string]bool{
	"pom.xml": true, "build.gradle": true, "build.gradle.kts": true, "package.json": true,
	"go.mod": true, "Cargo.toml": true, "Makefile": true, "CMakeLists.txt": true,
	"requirements.txt": true, "pyproject.toml": true, "setup.py": true, "composer.json": true,
	"Gemfile": true, "build.xml": true,
}

// skipDir reports whether a directory is never part of a project scan.
func skipDir(name string) bool {
	return strings.HasPrefix(name, ".") || name == "node_modules" || name == "vendor" || name == "__pycache__"
}

// scanProject counts source files and detects manifests below dir, with
// limits on depth and visited entries so huge trees stay fast.
func scanProject(dir string) (sources int, manifest bool) {
	visited := 0
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if d != nil && d.IsDir() && p != dir {
				return fs.SkipDir
			}
			return nil
		}
		visited++
		if visited > maxWalkEntries || sources >= maxSourceCount {
			return fs.SkipAll
		}
		if d.IsDir() {
			if p == dir {
				return nil
			}
			rel, _ := filepath.Rel(dir, p)
			if skipDir(d.Name()) || strings.Count(rel, string(filepath.Separator)) >= maxWalkDepth {
				return fs.SkipDir
			}
			return nil
		}
		if manifestFiles[d.Name()] || strings.HasSuffix(d.Name(), ".csproj") {
			manifest = true
		}
		if sourceExts[strings.ToLower(filepath.Ext(d.Name()))] {
			sources++
		}
		return nil
	})
	return sources, manifest
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func newProjectOption(path, kind string) ProjectOption {
	src, man := scanProject(path)
	return ProjectOption{
		Path:       path,
		Name:       filepath.Base(path),
		Kind:       kind,
		Sources:    src,
		Manifest:   man,
		HasResults: isDir(filepath.Join(path, Dir)),
	}
}

// ProjectCandidates lists the directories offered in the project step: cwd
// first, then recent projects that still exist, then immediate subdirectories
// of cwd that look like code projects. Paths are absolute and unique.
func ProjectCandidates(cwd string, recent []string) []ProjectOption {
	if abs, err := filepath.Abs(cwd); err == nil {
		cwd = abs
	}
	seen := map[string]bool{cwd: true}
	out := []ProjectOption{newProjectOption(cwd, ProjectCurrent)}

	n := 0
	for _, r := range recent {
		if n >= maxRecentProjects {
			break
		}
		r = filepath.Clean(r)
		if !filepath.IsAbs(r) || seen[r] || !isDir(r) {
			continue
		}
		seen[r] = true
		out = append(out, newProjectOption(r, ProjectRecent))
		n++
	}

	entries, _ := os.ReadDir(cwd)
	n = 0
	for _, e := range entries {
		if n >= maxSubdirProjects {
			break
		}
		if !e.IsDir() || skipDir(e.Name()) {
			continue
		}
		p := filepath.Join(cwd, e.Name())
		if seen[p] {
			continue
		}
		opt := newProjectOption(p, ProjectSubdir)
		if opt.Sources == 0 && !opt.Manifest {
			continue
		}
		seen[p] = true
		out = append(out, opt)
		n++
	}
	return out
}

// ValidateDir resolves a typed path (relative to base, "~" allowed) and
// checks that it is an existing directory.
func ValidateDir(base, input string) (string, error) {
	p := strings.TrimSpace(input)
	if p == "" {
		return "", errors.New("escribe una ruta")
	}
	if p == "~" || strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			p = filepath.Join(home, strings.TrimPrefix(p, "~"))
		}
	}
	if !filepath.IsAbs(p) {
		p = filepath.Join(base, p)
	}
	p = filepath.Clean(p)
	st, err := os.Stat(p)
	if err != nil {
		return "", fmt.Errorf("no existe: %s", p)
	}
	if !st.IsDir() {
		return "", fmt.Errorf("no es un directorio: %s", p)
	}
	return p, nil
}

// ---------------------------------------------------------------------------
// Model options
// ---------------------------------------------------------------------------

// OllamaLabel is the provider name shown for the local Ollama server.
const OllamaLabel = "Ollama (local)"

// ProviderModels is a configured provider (API key, OAuth login or config
// file) with the model IDs that --model can select on it.
type ProviderModels struct {
	ID     string
	Name   string
	Models []string
}

// ModelInputs is everything the model step needs to know. It is gathered by
// [DiscoverModels] (or a test) and turned into rows by [BuildModelOptions].
type ModelInputs struct {
	OllamaURL        string
	Ollama           []string // installed models; nil when unreachable
	OllamaErr        error    // non-nil when the server did not answer
	ProjectHasConfig bool     // the project already has crush.json/.crush.json
	Providers        []ProviderModels
	ProvidersErr     error  // error loading the provider configuration
	Large            string // configured large model as "provider/model"
	EnvModel         string // LEGACYLIFT_MODEL
}

// ModelOption is one row of the model step. Value is the exact string passed
// as --model ("" means automatic selection).
type ModelOption struct {
	Value    string
	Label    string
	Group    string
	Hint     string
	Disabled bool
	Auto     bool
}

// UsesLocal applies the phases.go rule: an explicit ollama/ or local/ model
// runs locally.
func UsesLocal(model string) bool {
	return strings.HasPrefix(model, "ollama/") || strings.HasPrefix(model, "local/")
}

// ChooseMode is the mode selection used by the phases command: an explicit
// --mode wins, then --lite / --full, then lite for local models and full
// otherwise.
func ChooseMode(modeFlag string, lite, full, usesLocal bool) string {
	switch {
	case modeFlag != "":
		return modeFlag
	case lite:
		return "lite"
	case full:
		return "full"
	case usesLocal:
		return "lite"
	default:
		return "full"
	}
}

func (in ModelInputs) ollamaUp() bool { return in.OllamaErr == nil && len(in.Ollama) > 0 }

func (in ModelInputs) provider(id string) (ProviderModels, bool) {
	for _, p := range in.Providers {
		if p.ID == id {
			return p, true
		}
	}
	return ProviderModels{}, false
}

// AutoChoice predicts what "Automático" will run with the existing logic:
// without a project config and with Ollama running, EnsureLocalAI points the
// project at LEGACYLIFT_MODEL or the first installed model; otherwise the
// configured large model is used.
func AutoChoice(in ModelInputs) (desc string, local bool) {
	if !in.ProjectHasConfig && in.ollamaUp() {
		m := in.EnvModel
		if m == "" {
			m = in.Ollama[0]
		}
		return fmt.Sprintf("usará %s · %s (copia con contexto ampliado)", OllamaLabel, m), true
	}
	if in.Large != "" {
		prov, _, _ := strings.Cut(in.Large, "/")
		return "usará " + in.Large + " (modelo grande configurado)", prov == "ollama" || prov == "local"
	}
	if len(in.Providers) > 0 {
		return "usará el modelo por defecto del proveedor configurado", false
	}
	return "no hay IA configurada: la ejecución fallará", false
}

// BuildModelOptions turns the discovered inputs into picker rows. Only real,
// usable options are selectable; everything else is shown disabled with the
// reason. Labels always name the real provider and model ID.
func BuildModelOptions(in ModelInputs) []ModelOption {
	autoDesc, _ := AutoChoice(in)
	out := []ModelOption{{Label: "Automático (como hoy)", Hint: autoDesc, Auto: true}}

	// Local Ollama, straight from the server.
	url := in.OllamaURL
	if url == "" {
		url = DefaultOllamaURL
	}
	switch {
	case in.OllamaErr != nil:
		out = append(out, ModelOption{Group: OllamaLabel, Label: "Ollama no responde en " + url, Hint: "inicia ollama serve", Disabled: true})
	case len(in.Ollama) == 0:
		out = append(out, ModelOption{Group: OllamaLabel, Label: "Ollama no tiene modelos instalados", Hint: "ollama pull <modelo>", Disabled: true})
	default:
		// With a project config, EnsureLocalAI does not touch it, so only
		// the models listed in its ollama provider resolve.
		cfgOllama, hasCfgOllama := in.provider("ollama")
		for _, m := range in.Ollama {
			opt := ModelOption{Group: OllamaLabel, Label: OllamaLabel + " · " + m, Value: "ollama/" + m}
			if strings.HasPrefix(m, "legacylift-") {
				opt.Hint = "copia con contexto ampliado"
			}
			if in.ProjectHasConfig && (!hasCfgOllama || !slices.Contains(cfgOllama.Models, m)) {
				opt.Disabled = true
				opt.Hint = "no figura en el crush.json del proyecto"
			}
			out = append(out, opt)
		}
	}

	// Configured providers (API keys, OAuth logins, config files).
	providers := slices.Clone(in.Providers)
	slices.SortFunc(providers, func(a, b ProviderModels) int { return strings.Compare(a.ID, b.ID) })
	for _, p := range providers {
		if p.ID == "ollama" {
			continue // listed live above
		}
		name := p.Name
		if name == "" {
			name = p.ID
		}
		models := slices.Clone(p.Models)
		slices.Sort(models)
		for _, m := range models {
			out = append(out, ModelOption{Group: name, Label: name + " · " + m, Value: p.ID + "/" + m})
		}
	}
	if in.ProvidersErr != nil {
		out = append(out, ModelOption{Group: "Proveedores", Label: "No se pudo leer la configuración de proveedores", Hint: in.ProvidersErr.Error(), Disabled: true})
	} else if len(providers) == 0 || (len(providers) == 1 && providers[0].ID == "ollama") {
		out = append(out, ModelOption{Group: "Proveedores", Label: "Ningún proveedor en la nube configurado", Hint: "usa legacylift login o ANTHROPIC_API_KEY, OPENAI_API_KEY…", Disabled: true})
	}
	return out
}

// DiscoverModels gathers the model inputs for dir: it asks the local Ollama
// server and calls configured (may be nil) for the providers known to the
// Crush configuration of that directory.
func DiscoverModels(dir string, configured func(dir string) ([]ProviderModels, string, error)) ModelInputs {
	in := ModelInputs{
		OllamaURL:        ollamaURL(),
		ProjectHasConfig: projectConfigExists(dir),
		EnvModel:         os.Getenv("LEGACYLIFT_MODEL"),
	}
	in.Ollama, in.OllamaErr = OllamaModels()
	if configured != nil {
		in.Providers, in.Large, in.ProvidersErr = configured(dir)
	}
	return in
}

// ---------------------------------------------------------------------------
// Bubble Tea model
// ---------------------------------------------------------------------------

// PickerResult is what the user confirmed.
type PickerResult struct {
	Dir   string // absolute project directory
	Model string // exact --model value; "" for automatic
	From  string // first phase ID; "" for the first one
	Until string // last phase ID; "" for the last one
}

// PickerOptions configures the picker.
type PickerOptions struct {
	Cwd      string
	Recent   []string
	Phases   []Phase
	Discover func(dir string) ModelInputs
	// Mode returns the mode the run will use; nil applies [ChooseMode]
	// without flags.
	Mode func(usesLocal bool) string
}

type pickerStep int

const (
	stepProject pickerStep = iota
	stepPath
	stepModel
	stepConfirm
)

type modelsLoadedMsg struct {
	dir    string
	inputs ModelInputs
}

// Confirmation rows.
const (
	confirmStart = iota
	confirmFrom
	confirmUntil
	confirmBack
)

// Picker is the pre-launch Bubble Tea model.
type Picker struct {
	opts PickerOptions
	step pickerStep

	projects []ProjectOption
	pCursor  int // len(projects) is "Escribir otra ruta…"
	path     textinput.Model
	pathErr  string

	dir       string
	loading   bool
	inputs    ModelInputs
	models    []ModelOption
	filter    textinput.Model
	filtering bool
	mCursor   int // index into visibleModels()
	chosen    ModelOption

	cCursor     int
	from, until int

	width, height int
	result        PickerResult
	cancelled     bool
	confirmed     bool
}

// NewPicker builds the picker, scanning the project candidates.
func NewPicker(opts PickerOptions) *Picker {
	if opts.Mode == nil {
		opts.Mode = func(local bool) string { return ChooseMode("", false, false, local) }
	}
	path := textinput.New()
	path.Placeholder = "/ruta/al/proyecto"
	path.Prompt = "› "
	filter := textinput.New()
	filter.Placeholder = "escribe para filtrar"
	filter.Prompt = "/ "
	path.SetWidth(94)
	filter.SetWidth(50)
	return &Picker{
		opts:     opts,
		projects: ProjectCandidates(opts.Cwd, opts.Recent),
		path:     path,
		filter:   filter,
		until:    max(len(opts.Phases)-1, 0),
		width:    100,
		height:   30,
	}
}

// Result returns the confirmed choice, or ErrPickerCancelled.
func (m *Picker) Result() (PickerResult, error) {
	if !m.confirmed {
		return PickerResult{}, ErrPickerCancelled
	}
	return m.result, nil
}

func (m *Picker) Init() tea.Cmd { return nil }

func (m *Picker) quit() (tea.Model, tea.Cmd) {
	m.cancelled = true
	return m, tea.Quit
}

func (m *Picker) chooseDir(dir string) tea.Cmd {
	m.dir = dir
	m.step = stepModel
	m.loading = true
	m.models = nil
	m.mCursor = 0
	m.filter.SetValue("")
	m.filtering = false
	discover := m.opts.Discover
	return func() tea.Msg {
		var in ModelInputs
		if discover != nil {
			in = discover(dir)
		}
		return modelsLoadedMsg{dir: dir, inputs: in}
	}
}

func (m *Picker) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.path.SetWidth(max(m.width-6, 20))
		m.filter.SetWidth(max(m.width/2, 20))
		return m, nil
	case modelsLoadedMsg:
		if msg.dir != m.dir {
			return m, nil // stale result from a previous project
		}
		m.loading = false
		m.inputs = msg.inputs
		m.models = BuildModelOptions(msg.inputs)
		m.mCursor = m.firstEnabled(m.visibleModels())
		return m, nil
	case tea.KeyPressMsg:
		if msg.String() == "ctrl+c" {
			return m.quit()
		}
		switch m.step {
		case stepProject:
			return m.updateProject(msg)
		case stepPath:
			return m.updatePath(msg)
		case stepModel:
			return m.updateModel(msg)
		case stepConfirm:
			return m.updateConfirm(msg)
		}
	}
	if m.step == stepPath {
		var cmd tea.Cmd
		m.path, cmd = m.path.Update(msg)
		return m, cmd
	}
	if m.step == stepModel && m.filtering {
		var cmd tea.Cmd
		m.filter, cmd = m.filter.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m *Picker) updateProject(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	total := len(m.projects) + 1
	switch msg.String() {
	case "q", "esc":
		return m.quit()
	case "up", "k":
		m.pCursor = (m.pCursor - 1 + total) % total
	case "down", "j":
		m.pCursor = (m.pCursor + 1) % total
	case "home", "g":
		m.pCursor = 0
	case "end", "G":
		m.pCursor = total - 1
	case "enter":
		if m.pCursor == len(m.projects) {
			m.step = stepPath
			m.pathErr = ""
			return m, m.path.Focus()
		}
		return m, m.chooseDir(m.projects[m.pCursor].Path)
	}
	return m, nil
}

func (m *Picker) updatePath(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.path.Blur()
		m.step = stepProject
		return m, nil
	case "enter":
		dir, err := ValidateDir(m.opts.Cwd, m.path.Value())
		if err != nil {
			m.pathErr = err.Error()
			return m, nil
		}
		m.path.Blur()
		return m, m.chooseDir(dir)
	}
	m.pathErr = ""
	var cmd tea.Cmd
	m.path, cmd = m.path.Update(msg)
	return m, cmd
}

// visibleModels returns the model rows that match the filter.
func (m *Picker) visibleModels() []ModelOption {
	q := strings.ToLower(strings.TrimSpace(m.filter.Value()))
	if q == "" {
		return m.models
	}
	var out []ModelOption
	for _, o := range m.models {
		if strings.Contains(strings.ToLower(o.Label+" "+o.Value+" "+o.Group), q) {
			out = append(out, o)
		}
	}
	return out
}

func (m *Picker) firstEnabled(list []ModelOption) int {
	for i, o := range list {
		if !o.Disabled {
			return i
		}
	}
	return 0
}

// moveModel moves the cursor by delta, skipping disabled rows.
func (m *Picker) moveModel(delta int) {
	list := m.visibleModels()
	for i := m.mCursor + delta; i >= 0 && i < len(list); i += delta {
		if !list[i].Disabled {
			m.mCursor = i
			return
		}
	}
}

func (m *Picker) chooseModel() {
	list := m.visibleModels()
	if m.mCursor < 0 || m.mCursor >= len(list) || list[m.mCursor].Disabled {
		return
	}
	m.chosen = list[m.mCursor]
	m.filter.Blur()
	m.filtering = false
	m.step = stepConfirm
	m.cCursor = confirmStart
}

func (m *Picker) updateModel(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	key := msg.String()
	if m.loading {
		switch key {
		case "q":
			return m.quit()
		case "esc":
			m.loading = false
			m.dir = ""
			m.step = stepProject
		}
		return m, nil
	}
	switch key {
	case "up":
		m.moveModel(-1)
		return m, nil
	case "down":
		m.moveModel(1)
		return m, nil
	case "pgup":
		for range 10 {
			m.moveModel(-1)
		}
		return m, nil
	case "pgdown":
		for range 10 {
			m.moveModel(1)
		}
		return m, nil
	case "enter":
		m.chooseModel()
		return m, nil
	}
	if m.filtering {
		if key == "esc" {
			m.filtering = false
			m.filter.Blur()
			m.filter.SetValue("")
			m.mCursor = m.firstEnabled(m.visibleModels())
			return m, nil
		}
		var cmd tea.Cmd
		m.filter, cmd = m.filter.Update(msg)
		m.mCursor = m.firstEnabled(m.visibleModels())
		return m, cmd
	}
	switch key {
	case "q":
		return m.quit()
	case "esc":
		m.step = stepProject
	case "k":
		m.moveModel(-1)
	case "j":
		m.moveModel(1)
	case "/":
		m.filtering = true
		return m, m.filter.Focus()
	}
	return m, nil
}

func (m *Picker) confirmRows() []int {
	if len(m.opts.Phases) == 0 {
		return []int{confirmStart, confirmBack}
	}
	return []int{confirmStart, confirmFrom, confirmUntil, confirmBack}
}

func (m *Picker) finish() (tea.Model, tea.Cmd) {
	m.result = PickerResult{Dir: m.dir, Model: m.chosen.Value}
	if n := len(m.opts.Phases); n > 0 {
		if m.from > 0 {
			m.result.From = m.opts.Phases[m.from].ID
		}
		if m.until < n-1 {
			m.result.Until = m.opts.Phases[m.until].ID
		}
	}
	m.confirmed = true
	return m, tea.Quit
}

func (m *Picker) updateConfirm(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	rows := m.confirmRows()
	idx := max(slices.Index(rows, m.cCursor), 0)
	last := len(m.opts.Phases) - 1
	switch msg.String() {
	case "q":
		return m.quit()
	case "esc":
		m.step = stepModel
	case "up", "k":
		m.cCursor = rows[(idx-1+len(rows))%len(rows)]
	case "down", "j", "tab":
		m.cCursor = rows[(idx+1)%len(rows)]
	case "left", "h":
		switch m.cCursor {
		case confirmFrom:
			m.from = max(m.from-1, 0)
		case confirmUntil:
			m.until = max(m.until-1, m.from)
		}
	case "right", "l":
		switch m.cCursor {
		case confirmFrom:
			m.from = min(m.from+1, m.until)
		case confirmUntil:
			m.until = min(m.until+1, last)
		}
	case "enter":
		switch m.cCursor {
		case confirmStart:
			return m.finish()
		case confirmBack:
			m.step = stepModel
		}
	}
	return m, nil
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

func shortenHome(p string) string {
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		if p == home {
			return "~"
		}
		if strings.HasPrefix(p, home+string(filepath.Separator)) {
			return "~" + p[len(home):]
		}
	}
	return p
}

// pickerLine is a rendered row; item is the selectable index it belongs to
// (-1 for headers).
type pickerLine struct {
	text string
	item int
}

// window returns the lines that fit in height, keeping the cursor visible.
func window(lines []pickerLine, cursor, height int) []string {
	height = max(height, 3)
	cur := 0
	for i, l := range lines {
		if l.item == cursor {
			cur = i
			break
		}
	}
	start := 0
	if len(lines) > height {
		start = min(max(cur-height/2, 0), len(lines)-height)
	}
	end := min(start+height, len(lines))
	out := make([]string, 0, end-start)
	for _, l := range lines[start:end] {
		out = append(out, l.text)
	}
	if start > 0 {
		out[0] = pickerDimStyle.Render("  ↑ más")
	}
	if end < len(lines) {
		out[len(out)-1] = pickerDimStyle.Render("  ↓ más")
	}
	return out
}

func (m *Picker) header() string {
	logo := Logo(m.width)
	if m.height > 0 && m.height < 24 {
		logo = LogoCompact()
	}
	steps := []string{"Proyecto", "Modelo", "Confirmación"}
	cur := map[pickerStep]int{stepProject: 0, stepPath: 0, stepModel: 1, stepConfirm: 2}[m.step]
	var crumbs []string
	for i, s := range steps {
		label := fmt.Sprintf("%d %s", i+1, s)
		switch {
		case i == cur:
			crumbs = append(crumbs, pickerSelStyle.Render(label))
		case i < cur:
			crumbs = append(crumbs, pickerHintStyle.Render("✔ "+s))
		default:
			crumbs = append(crumbs, pickerDimStyle.Render(label))
		}
	}
	return logo + "\n" + pickerDimStyle.Render(Tagline) + "\n\n" + strings.Join(crumbs, pickerDimStyle.Render("  ›  "))
}

func (m *Picker) truncate(s string) string {
	return ansi.Truncate(s, max(m.width-1, 20), "…")
}

func (m *Picker) projectBody(avail int) string {
	total := len(m.projects) + 1
	title := pickerTitleStyle.Render("Elige el proyecto a modernizar") +
		pickerDimStyle.Render(fmt.Sprintf("  %d/%d", m.pCursor+1, total))
	nameW, hintW := 0, 0
	for _, p := range m.projects {
		nameW = max(nameW, lipgloss.Width(p.Name))
		hintW = max(hintW, lipgloss.Width(p.Hint()))
	}
	nameW, hintW = min(nameW, 24), min(hintW, 60)
	pad := func(s string, w int) string {
		if lipgloss.Width(s) > w {
			s = ansi.Truncate(s, w, "…")
		}
		return s + strings.Repeat(" ", w-lipgloss.Width(s))
	}
	lines := make([]pickerLine, 0, total)
	for i, p := range m.projects {
		marker, name := "  ", pad(p.Name, nameW)
		if i == m.pCursor {
			marker, name = pickerSelStyle.Render("› "), pickerSelStyle.Render(name)
		}
		hintStyle := pickerDimStyle
		if p.HasResults {
			hintStyle = pickerHintStyle
		}
		lines = append(lines, pickerLine{
			text: m.truncate(marker + name + "  " + hintStyle.Render(pad(p.Hint(), hintW)) + "  " + pickerDimStyle.Render(shortenHome(p.Path))),
			item: i,
		})
	}
	other := "Escribir otra ruta…"
	if m.pCursor == len(m.projects) {
		other = pickerSelStyle.Render("› " + other)
	} else {
		other = "  " + other
	}
	lines = append(lines, pickerLine{text: other, item: len(m.projects)})
	return title + "\n\n" + strings.Join(window(lines, m.pCursor, avail-2), "\n")
}

func (m *Picker) pathBody() string {
	var b strings.Builder
	b.WriteString(pickerTitleStyle.Render("Escribe la ruta del proyecto") + "\n\n")
	b.WriteString(m.path.View() + "\n")
	if m.pathErr != "" {
		b.WriteString("\n" + pickerErrStyle.Render(m.pathErr))
	} else {
		b.WriteString("\n" + pickerDimStyle.Render("Relativa a "+shortenHome(m.opts.Cwd)+" o absoluta (~ permitido)."))
	}
	return b.String()
}

func (m *Picker) modelBody(avail int) string {
	if m.loading {
		return pickerTitleStyle.Render("Elige el modelo") + "\n\n" +
			pickerDimStyle.Render("Buscando modelos disponibles para "+shortenHome(m.dir)+"…")
	}
	list := m.visibleModels()
	selectable := 0
	for _, o := range list {
		if !o.Disabled {
			selectable++
		}
	}
	title := pickerTitleStyle.Render("Elige el modelo") +
		pickerDimStyle.Render(fmt.Sprintf("  %d/%d · proyecto %s", min(m.mCursor+1, len(list)), len(list), shortenHome(m.dir)))
	var top string
	if m.filtering || m.filter.Value() != "" {
		top = m.filter.View() + pickerDimStyle.Render(fmt.Sprintf("  %d opciones", selectable)) + "\n"
	}
	var lines []pickerLine
	group := "\x00"
	for i, o := range list {
		if o.Group != group {
			group = o.Group
			if group != "" {
				lines = append(lines, pickerLine{text: pickerDimStyle.Render(group), item: -1})
			}
		}
		marker, label := "  ", o.Label
		switch {
		case o.Disabled:
			label = pickerDisabledStyle.Render(label)
		case i == m.mCursor:
			marker, label = pickerSelStyle.Render("› "), pickerSelStyle.Render(label)
		}
		if o.Group != "" {
			marker = "  " + marker
		}
		text := marker + label
		if o.Hint != "" {
			text += "  " + pickerDimStyle.Render(o.Hint)
		}
		lines = append(lines, pickerLine{text: m.truncate(text), item: i})
	}
	if len(list) == 0 {
		lines = append(lines, pickerLine{text: pickerDimStyle.Render("  Ningún modelo coincide con el filtro."), item: -1})
	}
	used := 2
	if top != "" {
		used++
	}
	return title + "\n\n" + top + strings.Join(window(lines, m.mCursor, avail-used), "\n")
}

func (m *Picker) usesLocal() bool {
	if m.chosen.Auto {
		_, local := AutoChoice(m.inputs)
		return local
	}
	return UsesLocal(m.chosen.Value)
}

func (m *Picker) phaseRange() string {
	ph := m.opts.Phases
	if len(ph) == 0 {
		return "Todas las fases"
	}
	if m.from == 0 && m.until == len(ph)-1 {
		return fmt.Sprintf("Todas las fases (%d)", len(ph))
	}
	return fmt.Sprintf("%s %s → %s %s (%d)", ph[m.from].Num, ph[m.from].Title, ph[m.until].Num, ph[m.until].Title, m.until-m.from+1)
}

func (m *Picker) confirmBody() string {
	mode := m.opts.Mode(m.usesLocal())
	modeDesc := map[string]string{
		"lite":  "lite · sin herramientas, prompts cortos",
		"agent": "agent · herramientas reducidas por fase",
		"full":  "full · agente con todas las herramientas",
	}[mode]
	if modeDesc == "" {
		modeDesc = mode
	}
	var model string
	if m.chosen.Auto {
		model = m.chosen.Label + pickerDimStyle.Render("  "+m.chosen.Hint)
	} else {
		model = m.chosen.Label + pickerDimStyle.Render("  --model "+m.chosen.Value)
	}
	key := func(s string) string { return pickerDimStyle.Render(fmt.Sprintf("%-10s", s)) }
	summary := strings.Join([]string{
		key("Proyecto") + shortenHome(m.dir),
		key("Modelo") + model,
		key("Modo") + modeDesc,
		key("Fases") + m.phaseRange(),
	}, "\n")

	var rows []string
	for _, r := range m.confirmRows() {
		var label string
		switch r {
		case confirmStart:
			label = "Iniciar"
		case confirmFrom:
			label = "Primera fase  ‹ " + m.opts.Phases[m.from].Title + " ›"
		case confirmUntil:
			label = "Última fase   ‹ " + m.opts.Phases[m.until].Title + " ›"
		case confirmBack:
			label = "Volver"
		}
		if r == m.cCursor {
			rows = append(rows, pickerSelStyle.Render("› "+label))
		} else {
			rows = append(rows, "  "+label)
		}
	}
	return pickerTitleStyle.Render("Confirma y empieza") + "\n\n" +
		pickerBoxStyle.Render(summary) + "\n\n" + strings.Join(rows, "\n")
}

func (m *Picker) help() string {
	switch m.step {
	case stepProject:
		return "↑/↓ j/k mover · enter elegir · q salir"
	case stepPath:
		return "enter confirmar · esc volver · ctrl+c salir"
	case stepModel:
		if m.filtering {
			return "↑/↓ mover · enter elegir · esc borrar filtro · ctrl+c salir"
		}
		return "↑/↓ j/k mover · / filtrar · enter elegir · esc volver · q salir"
	default:
		return "↑/↓ mover · ←/→ cambiar fase · enter aceptar · esc volver · q salir"
	}
}

// Render returns the picker screen as a string.
func (m *Picker) Render() string {
	head := m.header()
	foot := pickerDimStyle.Render(m.help())
	avail := m.height - lipgloss.Height(head) - 4
	var body string
	switch m.step {
	case stepProject:
		body = m.projectBody(avail)
	case stepPath:
		body = m.pathBody()
	case stepModel:
		body = m.modelBody(avail)
	case stepConfirm:
		body = m.confirmBody()
	}
	return head + "\n\n" + body + "\n\n" + foot
}

func (m *Picker) View() tea.View {
	v := tea.NewView(m.Render())
	v.AltScreen = true
	return v
}

// RunPicker shows the pre-launch picker and returns the user's choice, or
// ErrPickerCancelled when the user quits.
func RunPicker(ctx context.Context, opts PickerOptions) (PickerResult, error) {
	m := NewPicker(opts)
	if _, err := tea.NewProgram(m, tea.WithContext(ctx)).Run(); err != nil {
		if !errors.Is(err, tea.ErrProgramPanic) &&
			(errors.Is(err, tea.ErrProgramKilled) || errors.Is(err, tea.ErrInterrupted) || errors.Is(err, context.Canceled)) {
			return PickerResult{}, ErrPickerCancelled
		}
		return PickerResult{}, err
	}
	return m.Result()
}
