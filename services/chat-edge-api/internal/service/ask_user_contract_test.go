package service

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// FIX74 — web and desktop must not disagree on the question contract.
// This test reads the DESKTOP source (apps/desktop/src/main/ask-user.ts)
// and fails if any limit, the tool description, or a result sentence in
// this Go copy differs from it. Drift on either side goes red here.
func readDesktopAskUser(t *testing.T) string {
	t.Helper()
	p := filepath.Join("..", "..", "..", "..", "apps", "desktop", "src", "main", "ask-user.ts")
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Skipf("desktop source not present at %s: %v", p, err)
	}
	return string(raw)
}

func tsConst(t *testing.T, src, name string) int {
	t.Helper()
	m := regexp.MustCompile(`\b` + name + `:\s*(\d+)`).FindStringSubmatch(src)
	if m == nil {
		t.Fatalf("desktop ask-user.ts has no %s", name)
	}
	n, _ := strconv.Atoi(m[1])
	return n
}

func TestAskUserContract_MatchesDesktop(t *testing.T) {
	src := readDesktopAskUser(t)
	for name, got := range map[string]int{
		"MAX_QUESTIONS":   AskMaxQuestions,
		"MIN_OPTIONS":     AskMinOptions,
		"MAX_OPTIONS":     AskMaxOptions,
		"MAX_HEADER":      AskMaxHeader,
		"MAX_QUESTION":    AskMaxQuestion,
		"MAX_LABEL":       AskMaxLabel,
		"MAX_DESCRIPTION": AskMaxDescription,
		"MAX_FREE_TEXT":   AskMaxFreeText,
	} {
		if want := tsConst(t, src, name); want != got {
			t.Errorf("%s: desktop %d, web %d", name, want, got)
		}
	}
	if !strings.Contains(src, `export const ASK_USER_TOOL = "`+AskUserTool+`"`) {
		t.Errorf("tool name differs from the desktop's")
	}
	// The description is one TS string split over four concatenated
	// literals; join the literals and compare.
	descRe := regexp.MustCompile(`description:\s*\n((?:\s*"[^"\n]*"\s*[+,]?\s*\n)+)`)
	m := descRe.FindStringSubmatch(src)
	if m == nil {
		t.Fatal("desktop tool description not found")
	}
	var parts []string
	for _, lit := range regexp.MustCompile(`"([^"\n]*)"`).FindAllStringSubmatch(m[1], -1) {
		parts = append(parts, lit[1])
	}
	if got := strings.Join(parts, ""); got != askUserDescription {
		t.Errorf("tool description differs.\ndesktop: %q\nweb:     %q", got, askUserDescription)
	}
	for _, want := range []string{askSkippedResult, `The user responded: `, `(no answer)`, `Not executed: the turn was cancelled.`} {
		if !strings.Contains(src, want) {
			t.Errorf("desktop source lacks the result text %q", want)
		}
	}
	if !strings.Contains(src, `(other|others|something else|none of (the|these) above|อื่น\s*ๆ|อื่นๆ|อื่น|ไม่ใช่ข้อไหนเลย)`) {
		t.Errorf("the 'Other' label rule differs from the desktop's")
	}
}

func TestAskUserContract_ValidationRules(t *testing.T) {
	ok := `{"questions":[{"question":"Which format?","header":"Format","options":[{"label":"Summary","description":"Brief"},{"label":"Detailed"}]}]}`
	if qs, err := validateAskArgs(ok); err != nil || len(qs) != 1 || qs[0].Options[1].Label != "Detailed" || qs[0].MultiSelect {
		t.Fatalf("a valid call must validate: %v %+v", err, qs)
	}
	cases := map[string]string{
		`{"questions":[{"question":"Q","header":"H","options":[{"label":"A"},{"label":"Other"}]}]}`:       "do not offer an 'Other' choice",
		`{"questions":[{"question":"Q","header":"H","options":[{"label":"A"},{"label":"อื่นๆ"}]}]}`:        "do not offer an 'Other' choice",
		`{"questions":[{"question":"Q","header":"H","options":[{"label":"A"}]}]}`:                          "2–4 choices",
		`{"questions":[{"question":"Q","header":"A header that is too long","options":[{"label":"A"},{"label":"B"}]}]}`: "header is longer than 12",
		`{"questions":[{"question":"Q","header":"H","options":[{"label":"A"},{"label":"a"}]}]}`:           "repeats an earlier option",
		`{"questions":"not an array"}`: "'questions' must be an array",
		`{"questions":[]}`:             "at least one question",
		`{"questions":[{"question":"` + strings.Repeat("x", AskMaxQuestion+1) + `","header":"H","options":[{"label":"A"},{"label":"B"}]}]}`: "longer than 500",
	}
	five := `{"questions":[` + strings.Repeat(`{"question":"Q","header":"H","options":[{"label":"A"},{"label":"B"}]},`, 4) + `{"question":"Q5","header":"H5","options":[{"label":"A"},{"label":"B"}]}]}`
	cases[five] = "at most 4 questions"
	for in, want := range cases {
		if _, err := validateAskArgs(in); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s: want error containing %q, got %v", in, want, err)
		}
	}
}

func TestAskUserContract_AnswerText(t *testing.T) {
	qs, err := validateAskArgs(`{"questions":[{"question":"Which format?","header":"Format","options":[{"label":"Summary"},{"label":"Detailed"}]},{"question":"Audience?","header":"Audience","options":[{"label":"Execs"},{"label":"Devs"}]}]}`)
	if err != nil {
		t.Fatal(err)
	}
	got := formatAnswerForModel(qs, QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Summary"}}})
	if got != "\"Which format?\": Summary\n\"Audience?\": (no answer)" {
		t.Errorf("answered: %q", got)
	}
	resp := "  just do both  "
	if got := formatAnswerForModel(qs, QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Summary"}}, Response: &resp}); got != `The user responded: "just do both"` {
		t.Errorf("response wins: %q", got)
	}
	if got := formatAnswerForModel(qs, QuestionAnswer{Outcome: "skipped"}); got != askSkippedResult {
		t.Errorf("skipped: %q", got)
	}
	multi := formatAnswerForModel(qs[:1], QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Summary", "a glossary too"}}})
	if multi != `"Which format?": Summary, a glossary too` || strings.Contains(strings.ToLower(multi), "other") {
		t.Errorf("multi/other: %q", multi)
	}
	for _, bad := range []QuestionAnswer{{Outcome: "maybe"}, {Outcome: "answered", Answers: map[string][]string{"q": {strings.Repeat("x", AskMaxFreeText+1)}}}} {
		if err := validateQuestionAnswer(&bad); err == nil {
			t.Errorf("expected rejection for %+v", bad)
		}
	}
}
