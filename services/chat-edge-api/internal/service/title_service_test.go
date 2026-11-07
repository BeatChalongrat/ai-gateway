package service

import (
	"strings"
	"testing"
)

// FIX11 §5: title generation now asks gpt-4o-mini for a strict
// json_schema {"title": string} so a small model has nowhere to
// preamble in the wrong language. parseTitleCompletion must pull the
// title out of the JSON object, preserve non-ASCII (Thai/CJK)
// verbatim, and degrade gracefully when a provider ignores the schema
// and returns free text (or malformed JSON) instead. The language
// correctness itself is the model's job; these tests only prove the
// JSON path doesn't mangle what the model returned.

func TestParseTitleCompletion_JSONObject(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want string
	}{
		{
			name: "english",
			raw:  `{"title":"Debugging API rate limits"}`,
			want: "Debugging API rate limits",
		},
		{
			name: "thai",
			raw:  `{"title":"วิธีแก้บั๊กการเชื่อมต่อ"}`,
			want: "วิธีแก้บั๊กการเชื่อมต่อ",
		},
		{
			name: "mixed",
			raw:  `{"title":"ตั้งค่า OpenAI API key"}`,
			want: "ตั้งค่า OpenAI API key",
		},
		{
			name: "pretty printed with surrounding whitespace",
			raw:  "  {\n  \"title\": \"Weather in Bangkok\"\n}  ",
			want: "Weather in Bangkok",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := parseTitleCompletion(tc.raw); got != tc.want {
				t.Fatalf("parseTitleCompletion(%q) = %q, want %q", tc.raw, got, tc.want)
			}
		})
	}
}

func TestParseTitleCompletion_FreeTextIsKept(t *testing.T) {
	// Provider ignored the schema and returned genuine free text: use it
	// as-is so a title still lands (cleanTitle runs afterwards).
	const raw = "Debugging API rate limits"
	if got := parseTitleCompletion(raw); got != raw {
		t.Fatalf("parseTitleCompletion(%q) = %q, want the raw text", raw, got)
	}

	// Non-ASCII free text must survive the fallback too.
	const thai = "วิธีแก้บั๊ก"
	if got := parseTitleCompletion(thai); got != thai {
		t.Fatalf("parseTitleCompletion(%q) = %q, want the raw Thai text", thai, got)
	}
}

// FIX21 §3: the live bug. A BROKEN or TRUNCATED structured-output envelope must
// NOT be returned raw — that leaked `{"title":` verbatim into the sidebar. It
// signals "no usable title" (empty) so Generate falls back to the user message.
func TestParseTitleCompletion_BrokenEnvelopeDoesNotLeakJSON(t *testing.T) {
	for _, raw := range []string{
		`{"title":`,       // the exact fragment seen live in the sidebar
		`{"title": "half`, // truncated mid-value
		`{"title"`,        // even shorter
		`{`,               // bare brace
		`{"title": "x"`,   // missing closing brace
	} {
		got := parseTitleCompletion(raw)
		if got != "" {
			t.Fatalf("parseTitleCompletion(%q) = %q, want empty (must not leak JSON)", raw, got)
		}
		if cleaned := cleanTitle(got); cleaned != "" {
			t.Fatalf("cleanTitle after broken envelope %q = %q, want empty", raw, cleaned)
		}
	}
}

func TestParseTitleCompletion_EmptyInputs(t *testing.T) {
	if got := parseTitleCompletion(""); got != "" {
		t.Fatalf("empty raw = %q, want empty", got)
	}
	// Valid JSON but empty title: return empty (the caller maps this to
	// the "empty completion" error) rather than leaking the braces.
	if got := parseTitleCompletion(`{"title":""}`); got != "" {
		t.Fatalf("empty title field = %q, want empty", got)
	}
}

// End-to-end of the string pipeline: parse then clean, the exact order
// Generate uses. Proves quotes/trailing punctuation still get stripped
// on the fallback path and that a clean JSON title is left intact.
func TestParseThenCleanTitle(t *testing.T) {
	if got := cleanTitle(parseTitleCompletion(`{"title":"Weather in Bangkok"}`)); got != "Weather in Bangkok" {
		t.Fatalf("json path = %q", got)
	}
	// Free-text fallback with quotes + trailing period: cleanTitle fixes it.
	if got := cleanTitle(parseTitleCompletion(`"Weather in Bangkok."`)); got != "Weather in Bangkok" {
		t.Fatalf("fallback path = %q", got)
	}
}

// FIX21 §3: resolveTitle is the exact path that produced the `{"title":` leak
// — parse the completion, and on anything unusable fall back to the user's
// first message, NEVER the JSON. This drives the buggy path, not the working
// one (§4): a broken envelope + a real user message must yield the user text.
func TestResolveTitle(t *testing.T) {
	const userMsg = "จำเลขนี้ไว้นะ 74-กระต่าย-2951 แล้วตอบแค่คำว่า ok"
	cases := []struct {
		name string
		raw  string
		user string
		want string
	}{
		{"valid envelope wins", `{"title":"Weather in Bangkok"}`, userMsg, "Weather in Bangkok"},
		{"broken envelope falls back to a short Thai user msg", `{"title":`, "จำเลขและตอบกลับ", "จำเลขและตอบกลับ"},
		{"truncated envelope falls back", `{"title": "half`, "Hello world", "Hello world"},
		{"empty completion falls back", "", "Hello world", "Hello world"},
		{"free text kept as-is", "A Concise Title", userMsg, "A Concise Title"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := resolveTitle(tc.raw, tc.user)
			if got != tc.want {
				t.Fatalf("resolveTitle(%q, %q) = %q, want %q", tc.raw, tc.user, got, tc.want)
			}
			// The final title must never contain the JSON envelope.
			if strings.HasPrefix(got, "{") || strings.Contains(got, `"title"`) {
				t.Fatalf("resolveTitle leaked JSON: %q", got)
			}
		})
	}
}

func TestFallbackTitleFromMessage(t *testing.T) {
	if got := fallbackTitleFromMessage(""); got != "" {
		t.Fatalf("empty user msg = %q, want empty", got)
	}
	// First line only.
	if got := fallbackTitleFromMessage("first line\nsecond line"); got != "first line" {
		t.Fatalf("multiline = %q, want first line", got)
	}
	// Long messages are trimmed to a sidebar-sized prefix (rune-safe).
	long := strings.Repeat("ก", 100)
	got := fallbackTitleFromMessage(long)
	if n := len([]rune(got)); n != 48 {
		t.Fatalf("long fallback rune count = %d, want 48", n)
	}
}
