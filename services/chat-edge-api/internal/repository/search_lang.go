package repository

import "strings"

// NeedsTrigramSearch reports whether a search query should route to
// the pg_trgm substring branch (migration 028) instead of the
// tsvector branch (migration 016).
//
// The tsvector path tokenizes with the 'simple' dictionary, which
// splits on whitespace. Thai — like Lao, Khmer, Myanmar, and CJK — is
// written without spaces, so a query in those scripts is almost
// always a SUBSTRING of a longer unbroken run; tsvector can only
// match when the entire run is identical. Any query containing a
// codepoint from an unsegmented script therefore uses
// ILIKE + similarity() over the trigram indexes.
//
// Mixed queries ("invoice ใบเสนอราคา") route to the trigram branch
// too: ILIKE still finds the literal phrase, whereas tsvector would
// silently drop the Thai half.
func NeedsTrigramSearch(q string) bool {
	for _, r := range q {
		switch {
		case r >= 0x0E00 && r <= 0x0E7F: // Thai
			return true
		case r >= 0x0E80 && r <= 0x0EFF: // Lao
			return true
		case r >= 0x1780 && r <= 0x17FF: // Khmer
			return true
		case r >= 0x1000 && r <= 0x109F: // Myanmar
			return true
		case r >= 0x3040 && r <= 0x30FF: // Japanese kana
			return true
		case r >= 0x4E00 && r <= 0x9FFF: // CJK unified ideographs
			return true
		case r >= 0xAC00 && r <= 0xD7AF: // Hangul syllables
			return true
		}
	}
	return false
}

// EscapeLike escapes LIKE/ILIKE wildcards so the trigram branch
// matches the user's literal text (Postgres default backslash
// escape). SanitizeSearchQuery upstream already strips % and \ but
// this stays defensive — repos are also called by the AI tool path.
func EscapeLike(q string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(q)
}

// TrigramPattern is the ILIKE argument for a substring search.
func TrigramPattern(q string) string {
	return "%" + EscapeLike(q) + "%"
}
