package legacylift

import (
	"strings"

	"charm.land/lipgloss/v2"
)

// Brand palette shared by the picker and the phase TUI. The official wordmark is
// monochrome; the cyan accent is the terminal's existing UI accent.
var (
	brandInk   = lipgloss.Color("#F3F3F1")
	brandMuted = lipgloss.Color("#9A9CA3")
	brandMark  = lipgloss.NewStyle().Foreground(brandInk)
	brandWord  = lipgloss.NewStyle().Foreground(brandInk).Bold(true)
	brandTag   = lipgloss.NewStyle().Foreground(brandMuted)
)

// logoMark is the LegacyLift symbol rendered from branding/custom/logo-dark.webp.
var logoMark = []string{
	"█████████████",
	"████▀▀▀▀▀▀▀▀▀",
	"███▀         ",
	"         ████",
	"▄▄▄▄▄▄▄▄▄████",
	"█████████████",
}

// logoWord is the lowercase "Legacy / Lift." wordmark in a 3-row half-block
// font (6 pixel rows), sharing a common baseline like the official logo.
var logoWord = []string{
	"█     ▄▄   ▄▄▄  ▄▄   ▄▄▄ ▄  ▄",
	"█    █▄▄█ ▀▄▄█  ▄▄█ █    ▀▄▄█",
	"█▄▄▄ ▀▄▄▄ ▄▄▄▀ ▀▄▄█ ▀▄▄▄ ▄▄▄▀",
	"█     ▀   ▄▀▀  ▄",
	"█    ▀█  ▀█▀▀ ▀█▀",
	"█▄▄▄ ▄█▄  █    ▀▄▄ ▄",
}

// Tagline is shown under the logo.
const Tagline = "Understand first. Modernize safely."

// BannerPlain is the ANSI-free one-line brand used by plain (non-TTY) output.
const BannerPlain = "LegacyLift AI — " + Tagline

// Logo renders the full brand block (mark + wordmark), or a one-line compact
// version when the available width is too small.
func Logo(width int) string {
	if width > 0 && width < 48 {
		return LogoCompact()
	}
	var b strings.Builder
	for i := range logoMark {
		b.WriteString(brandMark.Render(logoMark[i]))
		b.WriteString("   ")
		b.WriteString(brandWord.Render(logoWord[i]))
		if i < len(logoMark)-1 {
			b.WriteByte('\n')
		}
	}
	return b.String()
}

// LogoCompact is a single-line brand for narrow terminals and plain output.
func LogoCompact() string {
	return brandMark.Render("▟█▛") + " " + brandWord.Render("LegacyLift.") + " " + brandTag.Render("AI · terminal")
}
