package agentmarket

import "strings"

// ── Reading what a person typed, by its words (AM11) ─────────────────────
//
// Several places read a typed message by the words in it — "รอบใหม่", "ทำไม",
// "pdf", "ขอบคุณ" — to tell what it asks for. Those lists were Thai with a few
// English words beside them; a person typing in English is now read the same
// way, so each list has its English words too.
//
// The rule of the match is one rule for both: Thai is written without
// spaces, so a Thai word is found anywhere in the text. An English word is
// not found that way — "word" is inside "password" and "in other words",
// "excel" inside "excellent", "how" inside "show", "ok" inside "book". A
// word or phrase in Latin letters is therefore found only as whole words:
// with no Latin letter or digit straight before its first letter or after
// its last. A Thai letter may touch it ("ขอpdfหน่อย" asks for a PDF), and a
// word that begins or ends with something else ("?", ".docx", a Thai
// syllable) is held to nothing on that side.

func latinEdge(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
}

// wordAt is where word first stands in text from a position on, by the rule
// above; -1 = nowhere. Both are in lower case.
func wordAt(text, word string, from int) int {
	if word == "" {
		return -1
	}
	for from <= len(text) {
		i := strings.Index(text[from:], word)
		if i < 0 {
			return -1
		}
		i += from
		end := i + len(word)
		before := !latinEdge(word[0]) || i == 0 || !latinEdge(text[i-1])
		after := !latinEdge(word[len(word)-1]) || end == len(text) || !latinEdge(text[end])
		if before && after {
			return i
		}
		from = i + 1
	}
	return -1
}

// HasWord: the text has one of the words (written in lower case), in any case.
func HasWord(text string, words ...string) bool {
	t := strings.ToLower(text)
	for _, w := range words {
		if wordAt(t, w, 0) >= 0 {
			return true
		}
	}
	return false
}

// WithoutWords is the text, in lower case, with every one of the words taken
// out of it — what is left of "ok ขอบคุณครับ!" once the thanks are gone.
func WithoutWords(text string, words ...string) string {
	t := strings.ToLower(text)
	for _, w := range words {
		for from := 0; ; {
			i := wordAt(t, w, from)
			if i < 0 {
				break
			}
			t = t[:i] + t[i+len(w):]
			from = i
		}
	}
	return t
}
