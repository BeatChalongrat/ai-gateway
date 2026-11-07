package agentmarket

import (
	"regexp"
	"strings"
)

// ── no build marker on a customer's document (AM21) ──────────────────────
//
// The owner's own report carried "[AM1b]" on its cover and in every running
// header: a company name typed with a card's marker during a walkthrough,
// kept as the organisation's theme, printed on a document an auditor reads.
// MarkerIn finds such a marker — or our own product's name — in a text that
// goes on a deliverable (a company name, an e-mail draft, a PDF's author);
// StripMarkers takes the markers out of a text that must still be used.

var (
	buildMarker = regexp.MustCompile(`\[(?:AM|FIX|NIGHT|HOTFIX|TEST|E)[- ]?\d*[A-Za-z]?\]`)
	productName = regexp.MustCompile(`\bSRAN\b|(?i:\bAgent Market\b)`)
)

// MarkerIn is the first build or card marker, or product name, a text carries; "" when none.
func MarkerIn(s string) string {
	if m := buildMarker.FindString(s); m != "" {
		return m
	}
	return productName.FindString(s)
}

// StripMarkers is the text with every build or card marker taken out and the spaces tidied.
func StripMarkers(s string) string {
	s = buildMarker.ReplaceAllString(s, " ")
	return strings.Join(strings.Fields(s), " ")
}
