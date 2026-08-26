package service

import "testing"

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

func TestParseTitleCompletion_MalformedJSONFallsBackToRaw(t *testing.T) {
	// Provider ignored the schema and returned free text: use it as-is
	// so a title still lands (cleanTitle runs afterwards).
	const raw = "Debugging API rate limits"
	if got := parseTitleCompletion(raw); got != raw {
		t.Fatalf("parseTitleCompletion(%q) = %q, want the raw text", raw, got)
	}

	// Non-ASCII free text must survive the fallback too.
	const thai = "วิธีแก้บั๊ก"
	if got := parseTitleCompletion(thai); got != thai {
		t.Fatalf("parseTitleCompletion(%q) = %q, want the raw Thai text", thai, got)
	}

	// A completion that starts JSON-ish but is broken is not valid JSON;
	// it falls back to the raw string rather than panicking.
	const broken = `{"title": "half`
	if got := parseTitleCompletion(broken); got != broken {
		t.Fatalf("parseTitleCompletion(%q) = %q, want raw fallback", broken, got)
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
