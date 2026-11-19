package legacylift

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

var (
	accent  = lipgloss.Color("#22D3EE")
	okColor = lipgloss.Color("#4ADE80")
	errCol  = lipgloss.Color("#F87171")
	dim     = lipgloss.Color("#6B7280")

	titleStyle = lipgloss.NewStyle().Bold(true).Foreground(accent)
	dimStyle   = lipgloss.NewStyle().Foreground(dim)
	boxStyle   = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(dim).Padding(0, 1)
)

type eventMsg Event
type finishedMsg struct{ err error }
type tickMsg time.Time

type model struct {
	phases  []Phase
	status  []Status
	took    []time.Duration
	errs    []error
	current int
	output  []string // streamed text per phase
	model   string
	root    string
	events  chan Event
	done    bool
	err     error
	frame   int
	width   int
	height  int
	cancel  context.CancelFunc
}

var spinner = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

func waitEvent(ch chan Event) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-ch
		if !ok {
			return nil
		}
		return eventMsg(ev)
	}
}

func tick() tea.Cmd {
	return tea.Tick(120*time.Millisecond, func(t time.Time) tea.Msg { return tickMsg(t) })
}

func (m *model) Init() tea.Cmd { return tea.Batch(waitEvent(m.events), tick()) }

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case tea.KeyPressMsg:
		switch msg.String() {
		case "q", "ctrl+c", "esc":
			m.cancel()
			return m, tea.Quit
		}
	case tickMsg:
		m.frame++
		return m, tick()
	case eventMsg:
		ev := Event(msg)
		m.current = ev.Index
		if ev.Chunk != "" {
			m.output[ev.Index] += ev.Chunk
		}
		if ev.Chunk == "" {
			m.status[ev.Index] = ev.Status
			m.took[ev.Index] = ev.Took
			m.errs[ev.Index] = ev.Err
		}
		return m, waitEvent(m.events)
	case finishedMsg:
		m.done = true
		m.err = msg.err
	}
	return m, nil
}

func (m *model) icon(i int) string {
	switch m.status[i] {
	case Running:
		return lipgloss.NewStyle().Foreground(accent).Render(spinner[m.frame%len(spinner)])
	case Done:
		return lipgloss.NewStyle().Foreground(okColor).Render("✔")
	case Failed:
		return lipgloss.NewStyle().Foreground(errCol).Render("✘")
	default:
		return dimStyle.Render("○")
	}
}

// lastLines hard-wraps s to width cells and returns its last n lines.
func lastLines(s string, n, width int) string {
	s = strings.TrimRight(s, "\n")
	if width > 4 {
		s = ansi.Hardwrap(s, width, true)
	}
	lines := strings.Split(s, "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}

// Minimum terminal size for the full block-art logo; smaller terminals (such
// as 80x24) get the one-line compact brand so the phase panels still fit.
const (
	fullLogoMinWidth  = 56
	fullLogoMinHeight = 30
)

// header renders the brand block: the logo, the tagline and the project/model
// line. The model string is shown verbatim.
func (m *model) header() string {
	fits := func(s string) bool { return m.width <= 0 || lipgloss.Width(s) <= m.width }
	var lines []string
	if m.width >= fullLogoMinWidth && m.height >= fullLogoMinHeight {
		lines = append(lines, Logo(m.width), "")
		if tag := titleStyle.Render("LegacyLift AI") + dimStyle.Render("  ·  "+Tagline); fits(tag) {
			lines = append(lines, tag)
		} else {
			lines = append(lines, dimStyle.Render(Tagline))
		}
	} else if l := LogoCompact() + "  " + dimStyle.Render(Tagline); fits(l) {
		lines = append(lines, l)
	} else {
		lines = append(lines, LogoCompact(), dimStyle.Render(Tagline))
	}
	proj, mdl := "proyecto: "+m.root, "modelo: "+m.model
	if meta := proj + "   " + mdl; fits(meta) {
		lines = append(lines, dimStyle.Render(meta))
	} else {
		wrap := dimStyle
		if m.width > 0 {
			wrap = wrap.Width(m.width)
		}
		lines = append(lines, wrap.Render(proj), wrap.Render(mdl))
	}
	return strings.Join(lines, "\n") + "\n\n"
}

func (m *model) View() tea.View {
	var b strings.Builder
	head := m.header()
	b.WriteString(head)

	var list strings.Builder
	for i, p := range m.phases {
		line := fmt.Sprintf("%s %s %-10s", m.icon(i), p.Num, p.Title)
		if m.took[i] > 0 {
			line += dimStyle.Render(fmt.Sprintf(" %4.0fs", m.took[i].Seconds()))
		}
		list.WriteString(line + "\n")
	}
	left := boxStyle.Render(strings.TrimRight(list.String(), "\n"))

	w := m.width - lipgloss.Width(left) - 6
	if w < 30 {
		w = 30
	}
	// Lines left for streamed output: the terminal height minus the header,
	// the output box chrome (two borders and its title), the footer and one
	// spare line so the alt screen never scrolls.
	h := m.height - (lipgloss.Height(head) - 1) - 3 - 1 - 1
	if minH := lipgloss.Height(left) - 3; h < minH {
		h = minH
	}
	cur := m.phases[m.current]
	body := m.output[m.current]
	if e := m.errs[m.current]; e != nil {
		body += "\n" + lipgloss.NewStyle().Foreground(errCol).Render("Error: "+e.Error())
	}
	if strings.TrimSpace(body) == "" {
		body = dimStyle.Render("Esperando respuesta del modelo…")
	}
	// The box width includes its border and padding, leaving w-4 cells of text.
	inner := w - 4
	title := ansi.Truncate(titleStyle.Render(cur.Title)+dimStyle.Render("  → "+cur.Artifact()), inner, "…")
	right := boxStyle.Width(w).Render(title + "\n" + lastLines(body, h, inner))
	b.WriteString(lipgloss.JoinHorizontal(lipgloss.Top, left, " ", right) + "\n")

	switch {
	case m.done && m.err == nil:
		b.WriteString(lipgloss.NewStyle().Foreground(okColor).Render("Todas las fases completadas. Artefactos en "+Dir+"/") + dimStyle.Render("   q salir"))
	case m.done:
		b.WriteString(lipgloss.NewStyle().Foreground(errCol).Render("Ejecución detenida: "+m.err.Error()) + dimStyle.Render("   q salir"))
	default:
		b.WriteString(dimStyle.Render("Ejecutando fases automáticamente · q cancelar"))
	}
	v := tea.NewView(b.String())
	v.AltScreen = true
	return v
}

// RunTUI runs the phases with the interactive (read-only) progress screen.
func RunTUI(ctx context.Context, r *Runner, modelName string) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	n := len(r.Phases)
	m := &model{
		phases: r.Phases, status: make([]Status, n), took: make([]time.Duration, n),
		errs: make([]error, n), output: make([]string, n), model: modelName,
		root: r.Root, events: make(chan Event, 256), cancel: cancel,
	}
	p := tea.NewProgram(m)
	go func() {
		err := r.Run(ctx, func(e Event) { m.events <- e })
		p.Send(finishedMsg{err: err})
	}()
	_, err := p.Run()
	if err != nil {
		return err
	}
	return m.err
}

// RunPlain runs the phases printing progress lines (no TUI), for CI or
// non-terminal use.
func RunPlain(ctx context.Context, r *Runner, w func(string)) error {
	w(BannerPlain)
	return r.Run(ctx, func(e Event) {
		if e.Chunk != "" {
			return
		}
		p := r.Phases[e.Index]
		switch e.Status {
		case Running:
			w(fmt.Sprintf("▶ %s %s …", p.Num, p.Title))
		case Done:
			w(fmt.Sprintf("✔ %s %s (%.0fs) → %s", p.Num, p.Title, e.Took.Seconds(), p.Artifact()))
		case Failed:
			w(fmt.Sprintf("✘ %s %s: %v", p.Num, p.Title, e.Err))
		}
	})
}
