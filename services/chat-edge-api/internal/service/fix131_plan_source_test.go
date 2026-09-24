package service

// FIX131 A1/A2/A3 — the pure rules behind "PDF → Word is broken":
//
//	A1 which source is edited and which is only input (PlanSourceRoleFor);
//	A2 what a failed run says (generatorFailure, the unreadable reasons) and
//	   that the script's own error reaches the log redacted, never the wire;
//	A3 the outcome store (in process and Redis).
//
// The same flows end to end, through the real sandbox, are in
// fix131_plan_source_live_test.go.

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

func fix131File(name, ct string) *model.ConversationFile {
	return &model.ConversationFile{ID: uuid.New(), OriginalFilename: name, ContentType: ct, ExtractionStatus: "completed"}
}

func TestFIX131_SourceRoleMatrix(t *testing.T) {
	docx := fix131File("a.docx", docxContentType)
	pptx := fix131File("a.pptx", pptxContentType)
	xlsx := fix131File("a.xlsx", xlsxContentType)
	pdf := fix131File("a.pdf", pdfContentType)
	png := fix131File("a.png", "image/png")
	txt := fix131File("a.txt", "text/plain")
	csv := fix131File("a.csv", "text/csv")
	// A browser that sent no type: the extension decides.
	octetDocx := fix131File("b.docx", "application/octet-stream")
	octetPdf := fix131File("b.pdf", "")

	cases := []struct {
		format string
		src    *model.ConversationFile
		want   PlanSourceRole
	}{
		{"docx", nil, PlanSourceNone},
		{"docx", docx, PlanSourceEdit},
		{"docx", octetDocx, PlanSourceEdit},
		{"docx", pdf, PlanSourceInput}, // NIGHT3
		{"docx", octetPdf, PlanSourceInput},
		{"docx", png, PlanSourceInput},
		{"docx", txt, PlanSourceInput},
		{"docx", xlsx, PlanSourceInput},
		{"docx", pptx, PlanSourceInput},
		{"pptx", pptx, PlanSourceEdit},
		{"pptx", pdf, PlanSourceInput},
		{"pptx", docx, PlanSourceInput},
		{"pdf", docx, PlanSourceInput}, // DOCX → PDF: a new PDF, as before
		{"pdf", pdf, PlanSourceInput},  // the PDF generator never edits
		{"xlsx", xlsx, PlanSourceEdit},
		{"excel", xlsx, PlanSourceEdit},
		{"xlsx", csv, PlanSourceInput},
		{"xlsx", pdf, PlanSourceInput},
		{"xlsx", docx, PlanSourceInput},
		{"DOCX ", docx, PlanSourceEdit},
	}
	for _, c := range cases {
		name := "none"
		if c.src != nil {
			name = c.src.OriginalFilename + "(" + c.src.ContentType + ")"
		}
		if got := PlanSourceRoleFor(c.format, c.src); got != c.want {
			t.Errorf("%s from %s: role = %s, want %s", c.format, name, got, c.want)
		}
	}
}

func TestFIX131_GeneratorFailureKeepsTheScriptErrorForTheLogOnly(t *testing.T) {
	src := fix131File("fixture.pdf", pdfContentType)
	// What docx_generator.py printed on NIGHT3 (edit path on a PDF), with
	// the plan's Thai in a quoted value.
	stdout := []byte(`{"error": "File is not a zip file: 'สรุปรายงาน.docx'", "error_type": "BadZipFile", "success": false}`)

	pe := generatorFailure(ErrDocGeneratorFailed, PlanSourceInput, src, stdout, nil, false)
	if pe.Reason != PlanFailGenerator || !errors.Is(pe, ErrDocGeneratorFailed) {
		t.Fatalf("input role: %+v", pe)
	}
	if !strings.Contains(pe.Detail, "BadZipFile") || !strings.Contains(pe.Detail, "File is not a zip file") {
		t.Fatalf("the script's own error must reach the log: %q", pe.Detail)
	}
	if strings.Contains(pe.Detail, "สรุป") {
		t.Fatalf("user content in the log line: %q", pe.Detail)
	}
	if strings.Contains(pe.Error(), "zip") || strings.Contains(pe.Error(), "script") {
		t.Fatalf("Error() must not carry the detail: %q", pe.Error())
	}

	// The same failure on an EDIT of a same-kind file is the file's fault.
	docx := fix131File("draft.docx", docxContentType)
	pe = generatorFailure(ErrDocGeneratorFailed, PlanSourceEdit, docx, stdout, nil, false)
	if pe.Reason != PlanFailSourceDamaged || pe.Source != docx {
		t.Fatalf("edit of an unopenable .docx: %+v", pe)
	}

	// Sandbox down / timeout / nothing printed.
	if pe := generatorFailure(ErrDocGeneratorFailed, PlanSourceNone, nil, nil,
		fmt.Errorf("%w: no result sidecar (docker stderr: Cannot connect)", sandbox.ErrUnavailable), false); pe.Reason != PlanFailSandbox {
		t.Fatalf("sandbox down: %+v", pe)
	}
	if pe := generatorFailure(ErrDocGeneratorFailed, PlanSourceNone, nil, nil, ErrDocSandboxUnavailable, false); pe.Reason != PlanFailSandbox {
		t.Fatalf("no sandbox wired: %+v", pe)
	}
	if pe := generatorFailure(ErrExcelExecutorFailed, PlanSourceNone, nil, nil, errGeneratorTimedOut, true); pe.Reason != PlanFailTimeout {
		t.Fatalf("timeout: %+v", pe)
	}
	if pe := generatorFailure(ErrDocGeneratorFailed, PlanSourceNone, nil, []byte("not json"), nil, false); pe.Reason != PlanFailGenerator || pe.Detail == "" {
		t.Fatalf("garbage stdout: %+v", pe)
	}
}

func TestFIX131_RedactScriptError(t *testing.T) {
	got := redactScriptError("KeyError: 'ยอดขาย'\nValueError: invalid literal for int() with base 10: \"สิบ\" ข้อความไทย")
	if strings.ContainsAny(got, "ยอดขายสิบข้อความไทย\n") {
		t.Fatalf("content survived: %q", got)
	}
	if !strings.Contains(got, "KeyError") || !strings.Contains(got, "invalid literal for int()") {
		t.Fatalf("the error's shape was lost: %q", got)
	}
	if long := redactScriptError(strings.Repeat("x", 1000)); len([]rune(long)) > maxScriptErrorLog+1 {
		t.Fatalf("not bounded: %d", len(long))
	}
}

func TestFIX131_ScriptFailureEnvelope(t *testing.T) {
	if _, ok := parseScriptFailure([]byte(`{"success": true, "output_path": "x"}`)); ok {
		t.Fatal("a success is not a failure")
	}
	if sf, ok := parseScriptFailure([]byte(`{"error": "boom", "success": false}`)); !ok || sf.Error != "boom" {
		t.Fatalf("an older image's envelope (no error_type): %+v %v", sf, ok)
	}
	if _, ok := parseScriptFailure(nil); ok {
		t.Fatal("empty stdout")
	}
}

func TestFIX131_UnreadableReasons(t *testing.T) {
	f := func(status, ct string, text *string) model.ConversationFile {
		return model.ConversationFile{OriginalFilename: "x", ContentType: ct, ExtractionStatus: status, ExtractedText: text}
	}
	s := func(v string) *string { return &v }
	row := &model.ConversationFile{OriginalFilename: "locked.pdf", FileSizeBytes: 2048}
	for _, reason := range []string{UnreadableEncrypted, UnreadableScanned, UnreadableDamaged} {
		ph := unreadablePlaceholder(row, pdfContentType, reason)
		got, bad := UnreadableReason(f("failed", pdfContentType, &ph))
		if !bad || got != reason {
			t.Fatalf("placeholder round trip %s: %s %v (%q)", reason, got, bad, ph)
		}
		if note, _ := fileUnreadable(f("failed", pdfContentType, &ph)); note == "" || strings.Contains(note, "or password") {
			t.Fatalf("%s: the model's note must name the one reason: %q", reason, note)
		}
	}
	// A pre-FIX131 row: unknown, worded exactly as before.
	old := f("failed", pdfContentType, s("[File: x. The file could not be read (damaged or password-protected).]"))
	if got, bad := UnreadableReason(old); !bad || got != UnreadableUnknown {
		t.Fatalf("old row: %s %v", got, bad)
	}
	if note, _ := fileUnreadable(old); note != "the file is damaged or password-protected" {
		t.Fatalf("old note changed: %q", note)
	}
	// Readable / reading / an image "unsupported" is not unreadable.
	for _, r := range []model.ConversationFile{f("completed", pdfContentType, s("text")), f("processing", pdfContentType, nil), f("unsupported", "image/png", nil)} {
		if _, bad := UnreadableReason(r); bad {
			t.Fatalf("%+v reported unreadable", r)
		}
	}
	// A PDF stored "unsupported" before FIX126 still is.
	if _, bad := UnreadableReason(f("unsupported", pdfContentType, nil)); !bad {
		t.Fatal("legacy unsupported PDF")
	}
	// The plan failure each maps to.
	for why, want := range map[string]string{UnreadableEncrypted: PlanFailSourceEncrypted, UnreadableScanned: PlanFailSourceScanned,
		UnreadableDamaged: PlanFailSourceDamaged} {
		ph := unreadablePlaceholder(row, pdfContentType, why)
		r := f("failed", pdfContentType, &ph)
		if got, _ := unreadableSourceReason(&r); got != want {
			t.Fatalf("%s → %s, want %s", why, got, want)
		}
	}
	if got, _ := unreadableSourceReason(&old); got != PlanFailSourceUnreadable {
		t.Fatalf("unknown → %s", got)
	}
}

func TestFIX131_ClassifyExtractorFailure(t *testing.T) {
	cases := []struct {
		exit   int
		stderr string
		want   string
	}{
		{4, "extraction failed: \nreason=encrypted\n", UnreadableEncrypted},
		{4, "extraction failed: No /Root object!\nreason=damaged\n", UnreadableDamaged},
		{5, "pdf has no text layer and OCR produced no text (pages=1)\nreason=scanned\n", UnreadableScanned},
		{5, "pdf has no text layer and OCR produced no text (pages=1)\n", UnreadableScanned}, // older image
		{4, "extraction failed: \n", UnreadableUnknown},                                      // older image
		{1, "Traceback …\n", UnreadableUnknown},
	}
	for _, c := range cases {
		if got := classifyExtractFailure(c.exit, []byte(c.stderr)); got != c.want {
			t.Errorf("exit %d %q → %s, want %s", c.exit, c.stderr, got, c.want)
		}
	}
}

func TestFIX131_DerivedNames(t *testing.T) {
	if got := deriveFromInputFilename("fixture.pdf", "docx"); got != "fixture.docx" {
		t.Fatalf("from input: %s", got)
	}
	if got := deriveFromInputFilename("sales.csv", "xlsx"); got != "sales.xlsx" {
		t.Fatalf("csv: %s", got)
	}
	if got := deriveFromInputFilename("", "pptx"); got != "report.pptx" {
		t.Fatalf("empty: %s", got)
	}
	if got := deriveGeneratedFilename("draft.docx", "docx"); got != "draft_edited.docx" {
		t.Fatalf("edit name changed: %s", got)
	}
}

func fix131ExerciseStore(t *testing.T, st PlanOutcomeStore, conv uuid.UUID) {
	t.Helper()
	ctx := context.Background()
	msgA, msgB := uuid.New(), uuid.New()
	src := uuid.New()
	pe := &PlanExecError{Reason: PlanFailSourceEncrypted, Err: ErrDocSourceUnreadable}
	st.RecordFailure(ctx, conv, PlanOutcomeFromError(msgA, "docx", "summary.docx", src, pe))
	time.Sleep(2 * time.Millisecond)
	st.RecordFailure(ctx, conv, PlanOutcomeFromError(msgB, "xlsx", "", uuid.Nil, errors.New("disk full")))
	// A second failure of the same card replaces the first.
	st.RecordFailure(ctx, conv, PlanOutcomeFromError(msgA, "docx", "summary.docx", src,
		&PlanExecError{Reason: PlanFailGenerator, Err: ErrDocGeneratorFailed}))

	got := st.List(ctx, conv)
	if len(got) != 2 {
		t.Fatalf("outcomes = %+v", got)
	}
	byMsg := map[uuid.UUID]PlanOutcome{}
	for _, o := range got {
		byMsg[o.MessageID] = o
	}
	a, b := byMsg[msgA], byMsg[msgB]
	if a.Fence != "doc-plan" || a.Format != "docx" || a.OutputFilename != "summary.docx" || a.Reason != PlanFailGenerator ||
		a.Status != PlanOutcomeFailed || a.SourceFileID == nil || *a.SourceFileID != src {
		t.Fatalf("doc outcome = %+v", a)
	}
	if b.Fence != "excel-plan" || b.Format != "xlsx" || b.Reason != PlanFailInternal || b.SourceFileID != nil {
		t.Fatalf("excel outcome = %+v", b)
	}
	if other := st.List(ctx, uuid.New()); len(other) != 0 {
		t.Fatalf("another conversation sees outcomes: %+v", other)
	}
	st.ClearFailure(ctx, conv, msgA, "doc-plan", "summary.docx")
	if got := st.List(ctx, conv); len(got) != 1 || got[0].MessageID != msgB {
		t.Fatalf("after clear: %+v", got)
	}
}

func TestFIX131_PlanOutcomeStore_InProcess(t *testing.T) {
	fix131ExerciseStore(t, NewMemoryPlanOutcomeStore(), uuid.New())
	if _, ok := NewPlanOutcomeStore(nil).(*memoryPlanOutcomes); !ok {
		t.Fatal("no Redis → the in-process store")
	}
}

func TestFIX131_PlanOutcomeStore_Redis(t *testing.T) {
	url := os.Getenv("FIX41_BENCH_REDIS_URL")
	if url == "" {
		t.Skip("FIX41_BENCH_REDIS_URL not set")
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Fatalf("redis url: %v", err)
	}
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { _ = rdb.Close() })
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		t.Skipf("redis unreachable: %v", err)
	}
	// Every key this test writes is its own (random conversation ids) and
	// is deleted when it ends.
	exercised, conv, msg := uuid.New(), uuid.New(), uuid.New()
	t.Cleanup(func() { _ = rdb.Del(context.Background(), planOutcomeKey(exercised), planOutcomeKey(conv)).Err() })
	st := NewPlanOutcomeStore(rdb)
	fix131ExerciseStore(t, st, exercised)

	// The hash expires (30 days), and a fresh process reads what another
	// wrote (no in-process fallback involved).
	st.RecordFailure(context.Background(), conv, PlanOutcomeFromError(msg, "pptx", "deck.pptx", uuid.Nil,
		&PlanExecError{Reason: PlanFailTimeout}))
	ttl := rdb.TTL(context.Background(), planOutcomeKey(conv)).Val()
	if ttl <= 29*24*time.Hour || ttl > planOutcomeTTL {
		t.Fatalf("ttl = %s", ttl)
	}
	fresh := NewPlanOutcomeStore(rdb)
	if got := fresh.List(context.Background(), conv); len(got) != 1 || got[0].Reason != PlanFailTimeout || got[0].Fence != "pptx-plan" {
		t.Fatalf("another replica reads: %+v", got)
	}
}
