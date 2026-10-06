package agentmarket

import (
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"
)

// ── The English of every sentence written in the source (AM11) ───────────
//
// A sentence for a person is written in the source in Thai. Its English is
// here, found by the Thai sentence itself — the whole literal, with its %s —
// so the place that writes a sentence does not change when a language is
// added, and a sentence cannot exist in one language only: the test that
// reads the source (TestEverySentenceHasItsEnglish) fails on a Thai literal
// with no entry, and on an entry no literal uses.
//
// The entries are kept beside what they translate (english_*.go here, and
// runner/english*.go), each file registering its own in init().
//
// Wording follows docs/agent-market/GLOSSARY.md. An English sentence takes
// the same facts in the same order as its Thai one; where English needs
// another order it says so with indexed verbs (%[2]s … %[1]s).

var english = map[string]string{}

// AddEnglish registers the English of sentences written in the source.
func AddEnglish(sentences map[string]string) {
	vocab.mu.Lock()
	defer vocab.mu.Unlock()
	for th, en := range sentences {
		english[th] = en
	}
	vocab.words = nil
}

// EnglishOf is the English of a sentence written in the source.
func EnglishOf(thai string) (string, bool) {
	e, ok := english[thai]
	return e, ok
}

// ── the words the product's English sentences use ────────────────────────
//
// reads.go shows a person only text that reads as written for them. In Thai
// that is decided by the script. In English it is decided by vocabulary: a
// word is readable when one of the product's own English sentences uses it —
// the catalogue above, and the packages' catalogues (AddEnglishWords). Like
// productWords, the list is read from the sentences, not thought up.

var vocab struct {
	mu    sync.Mutex
	words map[string]bool
	extra map[string]bool
}

// AddEnglishWords adds the words of English sentences kept elsewhere (a
// package's catalogue) to what reads as written for a person.
func AddEnglishWords(sentences ...string) {
	vocab.mu.Lock()
	defer vocab.mu.Unlock()
	if vocab.extra == nil {
		vocab.extra = map[string]bool{}
	}
	for _, s := range sentences {
		for _, w := range latinWords(s) {
			vocab.extra[w] = true
		}
	}
	vocab.words = nil
}

// isEnglishWord: one of the product's own English sentences uses this word.
func isEnglishWord(tok string) bool {
	vocab.mu.Lock()
	defer vocab.mu.Unlock()
	if vocab.words == nil {
		vocab.words = make(map[string]bool, 4096)
		for _, e := range english {
			for _, w := range latinWords(e) {
				vocab.words[w] = true
			}
		}
		// the messages are sentences of the product too (messages_en.go, reminders.go): one of them is
		// what a thread says when a request made for it is refused
		for _, m := range []map[string]string{userMessagesEn, whatEnglish, reminderMailMessagesEn} {
			for _, e := range m {
				for _, w := range latinWords(e) {
					vocab.words[w] = true
				}
			}
		}
		for w := range vocab.extra {
			vocab.words[w] = true
		}
	}
	return vocab.words[strings.ToLower(tok)]
}

// latinWords cuts a sentence into its Latin words exactly as the reader does
// (reads.go split: latinToken), in lower case; a word needs a letter.
func latinWords(s string) []string {
	var out []string
	for len(s) > 0 {
		c, size := utf8.DecodeRuneInString(s)
		if c < 0x80 && (unicode.IsLetter(c) || unicode.IsDigit(c)) {
			tok := latinToken.FindString(s)
			if strings.ContainsFunc(tok, unicode.IsLetter) {
				out = append(out, strings.ToLower(tok))
			}
			size = len(tok)
		}
		s = s[size:]
	}
	return out
}
