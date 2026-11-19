package legacylift

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
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

func lastLines(s string, n, width int) string {
	s = strings.TrimRight(s, "\n")
	var lines []string
	for _, l := range strings.Split(s, "\n") {
		for width > 4 && lipgloss.Width(l) > width {
			lines = append(lines, l[:width])
			l = l[width:]
		}
		lines = append(lines, l)
	}
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}

func (m *model) View() tea.View {
	var b strings.Builder
	b.WriteString(titleStyle.Render("LegacyLift AI") + dimStyle.Render("  ·  Understand first. Modernize safely.") + "\n")
	b.WriteString(dimStyle.Render(fmt.Sprintf("proyecto: %s   modelo: %s", m.root, m.model)) + "\n\n")

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
	h := m.height - 9
	if h < 8 {
		h = 8
	}
	cur := m.phases[m.current]
	body := m.output[m.current]
	if e := m.errs[m.current]; e != nil {
		body += "\n" + lipgloss.NewStyle().Foreground(errCol).Render("Error: "+e.Error())
	}
	if strings.TrimSpace(body) == "" {
		body = dimStyle.Render("Esperando respuesta del modelo…")
	}
	right := boxStyle.Width(w).Render(titleStyle.Render(cur.Title) + dimStyle.Render("  → "+cur.Artifact()) + "\n" + lastLines(body, h, w-2))
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
