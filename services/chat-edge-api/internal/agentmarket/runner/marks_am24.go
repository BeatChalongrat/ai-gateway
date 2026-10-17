package runner

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"unicode"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── Thai a reader sees as a typo (AM24 §1.2) ─────────────────────────────
//
// A model now and then writes a Thai mark twice on one letter ("ร่่าง", "กัับ"): 4 of the 7 commentaries the in-house
// model wrote on live after AM23, one Thai sentence in nine over 123 cases, whichever prompt. Whether a text marks Thai
// as Thai writes it is the engine's e2/marks.py — the rules the PDF repair already holds — asked through a platform
// op with the runner's current engine, so an install pinned to an older engine is checked the same. An answer with a
// slip is sent back with the reason, through the retry that exists. When no attempt is free of slips, the last
// answer whose every slip has one reading (a mark written twice, its marks in another order) is used with those
// slips written as Thai writes them, each one recorded on the run's narrative step (marks_mended) — never silently.
// A slip with no such reading (two tone marks, a mark on a vowel) is never mended: that answer is not used.

// markResult is e2/marks.py mend() of one text.
type markResult struct {
	Slips []struct {
		At      int     `json:"at"`
		Written string  `json:"written"`
		As      *string `json:"as"`
	} `json:"slips"`
	Mended *string `json:"mended"`
}

// markFix is one text of an answer that was used with its slips mended: where it is, as written, as used.
type markFix struct {
	Where  string `json:"where"`
	Before string `json:"before"`
	After  string `json:"after"`
}

// marksFunc asks e2/marks.py about texts: one result per text. It is not charged to the run: it guards against a fault
// of the model the platform chose, and the person already pays for the retry a slip causes.
type marksFunc func(ctx context.Context, texts []string) ([]markResult, error)

// marksVerdict is what the check says of one answer.
type marksVerdict struct {
	reason    string         // sent back with the retry; "" when every text marks Thai as Thai writes it
	mended    map[string]any // the answer with every slip mended; nil when there is no slip, or one has no reading
	fixes     []markFix
	unchecked string // why the check could not ask the engine ("" when it asked, or the answer holds no Thai)
}

// engineMarks asks the runner's current engine (a platform op, as stamp_review is).
func (rc *runCtx) engineMarks(ctx context.Context, texts []string) ([]markResult, error) {
	if rc.r.Exec == nil {
		return nil, errors.New("no engine to ask (the runner has no sandbox)")
	}
	res, err := rc.r.Exec.Run(ctx, map[string]any{"op": "thai_marks", "texts": texts}, nil)
	if err != nil {
		return nil, err
	}
	defer res.Cleanup()
	var out []markResult
	if err := res.Decode("texts", &out); err != nil {
		return nil, err
	}
	if len(out) != len(texts) {
		return nil, fmt.Errorf("thai_marks: %d results for %d texts", len(out), len(texts))
	}
	return out, nil
}

// answerText is one text of an answer and its place in it.
type answerText struct {
	where string
	text  string
}

// answerTexts are the texts of an answer that hold Thai, in a fixed order: the summary, the points, the headings.
func answerTexts(v map[string]any) []answerText {
	var out []answerText
	var walk func(where string, x any)
	walk = func(where string, x any) {
		switch t := x.(type) {
		case string:
			if strings.IndexFunc(t, func(r rune) bool { return unicode.Is(unicode.Thai, r) }) >= 0 {
				out = append(out, answerText{where, t})
			}
		case []any:
			for i, e := range t {
				walk(fmt.Sprintf("%s[%d]", where, i), e)
			}
		case []string:
			for i, e := range t {
				walk(fmt.Sprintf("%s[%d]", where, i), e)
			}
		case map[string]any:
			keys := make([]string, 0, len(t))
			for k := range t {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				walk(joinWhere(where, k), t[k])
			}
		case map[string]string: // the headings, as checkNarrative keeps them
			keys := make([]string, 0, len(t))
			for k := range t {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			for _, k := range keys {
				walk(joinWhere(where, k), t[k])
			}
		}
	}
	walk("", v)
	return out
}

// withTexts is a copy of an answer with the texts at the given places replaced.
func withTexts(v map[string]any, repl map[string]string) map[string]any {
	var copyOf func(where string, x any) any
	copyOf = func(where string, x any) any {
		switch t := x.(type) {
		case string:
			if s, ok := repl[where]; ok {
				return s
			}
			return t
		case []any:
			out := make([]any, len(t))
			for i, e := range t {
				out[i] = copyOf(fmt.Sprintf("%s[%d]", where, i), e)
			}
			return out
		case []string:
			out := make([]any, len(t))
			for i, e := range t {
				out[i] = copyOf(fmt.Sprintf("%s[%d]", where, i), e)
			}
			return out
		case map[string]any:
			out := make(map[string]any, len(t))
			for k, e := range t {
				out[k] = copyOf(joinWhere(where, k), e)
			}
			return out
		case map[string]string:
			out := make(map[string]string, len(t))
			for k, e := range t {
				out[k] = copyOf(joinWhere(where, k), e).(string)
			}
			return out
		}
		return x
	}
	return copyOf("", v).(map[string]any)
}

func joinWhere(where, k string) string {
	if where == "" {
		return k
	}
	return where + "." + k
}

// wordAt is the slip at character at with a few characters either side (Thai runs its words together: the whole run
// between two spaces can be a line), never starting on a mark; the engine counts code points, as a rune slice does.
func wordAt(text string, at int) string {
	rs := []rune(text)
	if at < 0 || at >= len(rs) {
		return ""
	}
	i, j := at, at+1
	for i > 0 && at-i < 6 && !unicode.IsSpace(rs[i-1]) {
		i--
	}
	for i < at && unicode.Is(unicode.Mn, rs[i]) {
		i++
	}
	for j < len(rs) && (j-at < 6 || unicode.Is(unicode.Mn, rs[j])) && !unicode.IsSpace(rs[j]) {
		j++
	}
	return string(rs[i:j])
}

// judgeMarks reads an answer's Thai with check: the reason to send back, and the answer mended when it can be.
func judgeMarks(ctx context.Context, l am.Lang, v map[string]any, check marksFunc) marksVerdict {
	texts := answerTexts(v)
	if len(texts) == 0 || check == nil {
		return marksVerdict{}
	}
	in := make([]string, len(texts))
	for i, t := range texts {
		in[i] = t.text
	}
	res, err := check(ctx, in)
	if err != nil {
		return marksVerdict{unchecked: err.Error()}
	}
	var words []string
	seen := map[string]bool{}
	repl := map[string]string{}
	var fixes []markFix
	every := true
	for i, r := range res {
		if len(r.Slips) == 0 {
			continue
		}
		for _, s := range r.Slips {
			if w := wordAt(texts[i].text, s.At); w != "" && !seen[w] && len(words) < 5 {
				seen[w] = true
				words = append(words, "\""+w+"\"")
			}
		}
		if r.Mended == nil {
			every = false
			continue
		}
		repl[texts[i].where] = *r.Mended
		fixes = append(fixes, markFix{Where: texts[i].where, Before: texts[i].text, After: *r.Mended})
	}
	if len(fixes) == 0 && every {
		return marksVerdict{}
	}
	out := marksVerdict{
		reason: fmt.Sprintf(inLang(l,
			"%s: เขียนสระหรือวรรณยุกต์แบบที่ภาษาไทยไม่เขียน (ซ้ำ สลับลำดับ หรือไม่มีพยัญชนะรองรับ) — เขียนแต่ละเครื่องหมายครั้งเดียวบนพยัญชนะของมัน สระก่อนวรรณยุกต์",
			"%s: a Thai vowel or tone mark written as Thai never writes it (twice, out of order, or on no consonant); write each mark once, on its consonant, the vowel before the tone mark"),
			strings.Join(words, ", "))}
	if every {
		out.mended, out.fixes = withTexts(v, repl), fixes
	}
	return out
}
