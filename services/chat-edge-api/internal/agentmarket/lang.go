package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ── Two languages (AM11) ─────────────────────────────────────────────────
//
// Agent Market speaks Thai and English. The rule for which one:
//
//   - a run has a language — the one its person's screen was in when the
//     run began (agent_runs.params.lang) — and everything made for the run
//     is in it: the questions its package asks, the sentences the runner
//     writes, the documents. A run is never half one and half the other, and
//     a finished run stays as it was written;
//   - whatever the control plane says in answer to a request — an error, an
//     estimate, a notice it writes — is in the language the request names
//     (the web sends its screen's language; nothing named = Thai, which is
//     what every client before this card expects).
//
// How a sentence gets its language without every function that builds one
// learning about languages: a sentence written in the source is written in
// Thai, and its English is in the catalogue (english.go), found by the Thai
// sentence itself. Text() renders both; which one is kept is decided where
// the text leaves memory — the thread's writers and the HTTP writer marshal
// "in" a language (MarshalIn), taken from the context (WithLang / LangOf).
// A text marshalled anywhere else is Thai: a place that was missed shows
// Thai, as it did before, and never half a sentence.
//
// Tax rules, rates and what a tax invoice must carry are code, per
// jurisdiction, and are the same in both languages (docs/agent-market/GLOSSARY.md §4).

// Lang is a language Agent Market speaks.
type Lang string

const (
	Thai    Lang = "th"
	English Lang = "en"
)

// ParseLang reads a language as a client names it ("en", "en-GB", "TH"); anything else is Thai.
func ParseLang(s string) Lang {
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "en" || strings.HasPrefix(s, "en-") || strings.HasPrefix(s, "en_") {
		return English
	}
	return Thai
}

type langKey struct{}

// WithLang says which language whatever is written under ctx is in.
func WithLang(ctx context.Context, l Lang) context.Context {
	if l != English {
		l = Thai
	}
	return context.WithValue(ctx, langKey{}, l)
}

// LangOf is the language of ctx (Thai when none was said).
func LangOf(ctx context.Context) Lang {
	if ctx != nil {
		if l, ok := ctx.Value(langKey{}).(Lang); ok {
			return l
		}
	}
	return Thai
}

// mark is the language as a row names it: "en", and nothing at all for Thai —
// a Thai row is written exactly as it was before there were two.
func (l Lang) mark() string {
	if l == English {
		return string(English)
	}
	return ""
}

// InRunLang is ctx saying the language of run. Whatever is written for a run
// — a notice in its thread, a card, an assumption, a notification about it —
// is in the run's own language, whichever screen asked for it: a run started
// in Thai is not answered in English because its person changed the screen's
// language since. A Run that was not read from its row (no params at all)
// says nothing, and ctx stays as it is.
func InRunLang(ctx context.Context, run Run) context.Context {
	if len(run.Params) == 0 {
		return ctx
	}
	return WithLang(ctx, run.Lang())
}

// RequestLang is the language a request names: ?lang= (a link, an event
// stream — neither can set a header), else Accept-Language when it is exactly
// one language tag — what this product's clients send. A browser's own list
// ("en-US,en;q=0.9,th;q=0.8") names no screen and is not read as one.
func RequestLang(r *http.Request) Lang {
	if q := r.URL.Query().Get("lang"); q != "" {
		return ParseLang(q)
	}
	h := strings.TrimSpace(r.Header.Get("Accept-Language"))
	if h == "" || strings.ContainsAny(h, ",;") {
		return Thai
	}
	return ParseLang(h)
}

// langWriter is a response that knows the language of its request, so that
// say() and writeJSON() need no request handed to them.
type langWriter struct {
	http.ResponseWriter
	l Lang
}

func (w *langWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

func (w *langWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// langOfWriter is the language of the request a response answers.
func langOfWriter(w http.ResponseWriter) Lang {
	for i := 0; i < 8 && w != nil; i++ {
		if lw, ok := w.(*langWriter); ok {
			return lw.l
		}
		u, ok := w.(interface{ Unwrap() http.ResponseWriter })
		if !ok {
			break
		}
		w = u.Unwrap()
	}
	return Thai
}

// inRequestLang is the middleware in front of every Agent Market route: the
// request's language on its context and on its response.
func inRequestLang(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		l := RequestLang(r)
		next.ServeHTTP(&langWriter{ResponseWriter: w, l: l}, r.WithContext(WithLang(r.Context(), l)))
	})
}

// ── marshalling in a language ────────────────────────────────────────────

// encoding/json hands a marshaller no context, so the language of one call is
// kept here for its length — under the goroutine that is marshalling, because
// encoding/json calls a value's MarshalJSON on the goroutine that called
// Marshal. The server writes for many people at once: a plain json.Marshal
// running at the same moment for somebody else finds nothing under its own
// goroutine and writes Thai, as every writer that was never taught a language
// does. (One shared variable behind a lock, the first shape of this, let that
// plain write see the other call's English: a Thai person's item was then
// stored in English, which the reader of a Thai item withholds.)
var marshalling struct {
	calls atomic.Int32 // MarshalIn calls in flight that set a language; while there are none, nobody looks anything up
	by    sync.Map     // goroutine id → Lang
}

// MarshalIn is json.Marshal with every PersonText inside v written in l.
// A call made inside another (a value whose MarshalJSON writes in a language
// of its own) has its own language and gives the outer one back.
func MarshalIn(l Lang, v any) ([]byte, error) {
	id := goroutineID()
	outer, nested := marshalling.by.Load(id)
	if l == Thai && !nested {
		return json.Marshal(v)
	}
	marshalling.by.Store(id, l)
	marshalling.calls.Add(1)
	defer func() {
		marshalling.calls.Add(-1)
		if nested {
			marshalling.by.Store(id, outer)
		} else {
			marshalling.by.Delete(id)
		}
	}()
	return json.Marshal(v)
}

// marshalLang is the language of the MarshalIn this goroutine is inside, and
// Thai outside one.
func marshalLang() Lang {
	if marshalling.calls.Load() == 0 {
		return Thai
	}
	if l, ok := marshalling.by.Load(goroutineID()); ok {
		return l.(Lang)
	}
	return Thai
}

// goroutineID is the running goroutine's number, read from the first line of
// its own stack ("goroutine 18 [running]:"). The runtime offers it no other way.
func goroutineID() uint64 {
	var buf [64]byte
	b := bytes.TrimPrefix(buf[:runtime.Stack(buf[:], false)], []byte("goroutine "))
	if i := bytes.IndexByte(b, ' '); i > 0 {
		b = b[:i]
	}
	id, _ := strconv.ParseUint(string(b), 10, 64)
	return id
}

// MarshalFor is MarshalIn in the language of ctx.
func MarshalFor(ctx context.Context, v any) ([]byte, error) { return MarshalIn(LangOf(ctx), v) }

// ── a plain string in a language ─────────────────────────────────────────

// T is a sentence written in the source (in Thai), in l. For text that is not
// a PersonText: a label kept in a row, a subject line, a word in a list.
func T(l Lang, thai wording) string {
	if l == English {
		if e, ok := english[string(thai)]; ok {
			return e
		}
	}
	return string(thai)
}

// ── dates, as each language says them (GLOSSARY.md §4) ───────────────────
//
// A date has no translation: each language has its own form of it, written
// here beside the Thai one (rounds.go, review.go, reminders.go). The Thai
// forms' tables and formats are therefore not in the catalogue, and nothing
// prints an English date through a Thai formatter — a Thai format takes a
// Buddhist-era year.

var (
	englishMonths      = []string{"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"}
	englishMonthsShort = []string{"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
	englishWeekdays    = []string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)

// the days of the week are words an English sentence of ours says ("Every Monday at 09:00"), so the
// reader of an English item knows them, as it knows the months (reads.go)
func init() { AddEnglishWords(englishWeekdays...) }

// WeekdayIn is a day of the week (1 = Monday … 7 = Sunday) as a reader of l says it.
func WeekdayIn(l Lang, n int) string {
	if n < 1 || n > 7 {
		n = 7
	}
	if l == English {
		return englishWeekdays[n-1]
	}
	return thaiWeekdays[n-1]
}

// ReminderPeriodLabelIn is the period a reminder is for, as a reader of l says it: a month
// ("ก.ย. 2569", "Sep 2026") or seven days ("28 ก.ย. – 4 ต.ค. 2569", "28 Sep – 4 Oct 2026").
func ReminderPeriodLabelIn(l Lang, period string) string {
	if l != English {
		return ReminderPeriodLabel(period)
	}
	if t, err := time.ParseInLocation("2006-01", period, thaiTime); err == nil && len(period) == 7 {
		return fmt.Sprintf("%s %d", englishMonthsShort[t.Month()-1], t.Year())
	}
	start, err := time.ParseInLocation("2006-01-02", period, thaiTime)
	if err != nil {
		return ""
	}
	end := start.AddDate(0, 0, 6)
	ms, me := englishMonthsShort[start.Month()-1], englishMonthsShort[end.Month()-1]
	switch {
	case start.Year() != end.Year():
		return fmt.Sprintf("%d %s %d – %d %s %d", start.Day(), ms, start.Year(), end.Day(), me, end.Year())
	case start.Month() != end.Month():
		return fmt.Sprintf("%d %s – %d %s %d", start.Day(), ms, end.Day(), me, end.Year())
	}
	return fmt.Sprintf("%d–%d %s %d", start.Day(), end.Day(), me, end.Year())
}

func bangkok(t time.Time) time.Time {
	loc, err := time.LoadLocation("Asia/Bangkok")
	if err != nil {
		loc = time.FixedZone("ICT", 7*3600)
	}
	return t.In(loc)
}

// PeriodLabelIn is "2026-09" as a reader of l says it: "กันยายน 2569", "September 2026".
func PeriodLabelIn(l Lang, period string) string {
	if l != English {
		return PeriodLabel(period)
	}
	var y, m int
	if _, err := fmt.Sscanf(period, "%d-%d", &y, &m); err != nil || m < 1 || m > 12 {
		return period
	}
	return fmt.Sprintf("%s %d", englishMonths[m-1], y)
}

// DateIn is a day: "28 ก.ย. 2569", "28 Sep 2026" (Thai time in both).
func DateIn(l Lang, t time.Time) string {
	if l != English {
		return ThaiDate(t)
	}
	t = bangkok(t)
	return fmt.Sprintf("%d %s %d", t.Day(), englishMonthsShort[int(t.Month())-1], t.Year())
}

// DateTimeIn is a moment: "28 ก.ย. 2569 เวลา 10:05 น.", "28 Sep 2026, 10:05" (Thai time in both).
func DateTimeIn(l Lang, t time.Time) string {
	if l != English {
		return ThaiDateTime(t)
	}
	t = bangkok(t)
	return fmt.Sprintf("%d %s %d, %02d:%02d", t.Day(), englishMonthsShort[int(t.Month())-1], t.Year(), t.Hour(), t.Minute())
}

// ── a run's language ─────────────────────────────────────────────────────

// Lang is the language the run is in: the one named when it was created
// (params.lang; none = Thai, which is every run from before AM11).
func (r Run) Lang() Lang {
	if len(r.Params) == 0 {
		return Thai
	}
	var p struct {
		Lang string `json:"lang"`
	}
	if json.Unmarshal(r.Params, &p) != nil || p.Lang == "" {
		return Thai
	}
	return ParseLang(p.Lang)
}

// paramsIn writes l into a run's params. Thai is written as nothing at all,
// so a Thai run's row is byte for byte what it was before AM11.
func paramsIn(params json.RawMessage, l Lang) json.RawMessage {
	m := map[string]json.RawMessage{}
	if len(params) > 0 && json.Unmarshal(params, &m) != nil {
		return params // not an object: left as it is
	}
	if l == English {
		m["lang"] = json.RawMessage(`"en"`)
	} else {
		delete(m, "lang")
	}
	if len(m) == 0 {
		return params
	}
	b, err := json.Marshal(m)
	if err != nil {
		return params
	}
	return b
}
