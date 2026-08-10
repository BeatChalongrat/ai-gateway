package repository

import "testing"

func TestNeedsTrigramSearch(t *testing.T) {
	cases := []struct {
		q    string
		want bool
	}{
		{"quarterly report", false},
		{"", false},
		{"hello-world_123", false},
		{"เสนอราคา", true},
		{"ใบเสนอราคาลูกค้า", true},
		{"invoice ใบเสนอราคา", true}, // mixed → trigram (ILIKE still matches the English half)
		{"見積もり", true},              // Japanese
		{"报价单", true},                // Chinese
		{"견적서", true},                // Korean
		{"café résumé", false},       // Latin diacritics stay on tsvector
	}
	for _, c := range cases {
		if got := NeedsTrigramSearch(c.q); got != c.want {
			t.Errorf("NeedsTrigramSearch(%q) = %v, want %v", c.q, got, c.want)
		}
	}
}

func TestEscapeLikeAndPattern(t *testing.T) {
	if got := EscapeLike(`100%_done\`); got != `100\%\_done\\` {
		t.Errorf("EscapeLike wildcards: got %q", got)
	}
	if got := TrigramPattern("เสนอราคา"); got != "%เสนอราคา%" {
		t.Errorf("TrigramPattern: got %q", got)
	}
	if got := TrigramPattern("50%"); got != `%50\%%` {
		t.Errorf("TrigramPattern must escape user wildcards: got %q", got)
	}
}
