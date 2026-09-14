package handler

import (
	"bufio"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// FIX117 — POST /compact streams only for a caller that says so.

func TestFIX117_OnlyAnExplicitEventStreamAcceptSelectsTheStream(t *testing.T) {
	cases := map[string]bool{
		"":                       false, // the pre-FIX117 default
		"application/json":       false, // what the installed desktop (e48) sends
		"*/*":                    false,
		"text/event-stream":      true,
		"text/event-stream, */*": true,
		"application/json, text/event-stream;q=0.9": true,
	}
	for accept, want := range cases {
		r := httptest.NewRequest(http.MethodPost, "/api/v1/conversations/x/compact", nil)
		if accept != "" {
			r.Header.Set("Accept", accept)
		}
		if got := wantsEventStream(r); got != want {
			t.Fatalf("Accept %q: wantsEventStream=%v want %v", accept, got, want)
		}
	}
}

func fix117Frames(t *testing.T, body string) []struct{ event, data string } {
	t.Helper()
	var out []struct{ event, data string }
	sc := bufio.NewScanner(strings.NewReader(body))
	cur := struct{ event, data string }{}
	for sc.Scan() {
		line := sc.Text()
		switch {
		case strings.HasPrefix(line, "event: "):
			cur.event = strings.TrimPrefix(line, "event: ")
		case strings.HasPrefix(line, "data: "):
			cur.data = strings.TrimPrefix(line, "data: ")
		case line == "":
			if cur.event != "" {
				out = append(out, cur)
			}
			cur = struct{ event, data string }{}
		}
	}
	return out
}

func TestFIX117_CompactStreamCarriesRawCountsThenTheSameResultBody(t *testing.T) {
	rec := httptest.NewRecorder()
	res := &service.ManualCompactResult{MessagesTotal: 36, CoverageReached: true}
	writeCompactStream(rec, rec, func(onProgress func(service.ExtractionProgress)) (*service.ManualCompactResult, error) {
		onProgress(service.ExtractionProgress{Folded: 0, Total: 36, TotalKnown: true})
		onProgress(service.ExtractionProgress{Folded: 20, Total: 36, TotalKnown: true})
		onProgress(service.ExtractionProgress{Folded: 36, Total: 36, TotalKnown: true, Done: true, Reached: true})
		return res, nil
	})
	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content-type %q", ct)
	}
	frames := fix117Frames(t, rec.Body.String())
	if len(frames) != 4 {
		t.Fatalf("expected 3 progress frames and the result, got %d: %+v", len(frames), frames)
	}
	for i, want := range []string{`{"done":false,"folded":0,"reached":false,"total":36}`, `{"done":false,"folded":20,"reached":false,"total":36}`, `{"done":true,"folded":36,"reached":true,"total":36}`} {
		if frames[i].event != "compaction_progress" || frames[i].data != want {
			t.Fatalf("frame %d = %s %s, want compaction_progress %s", i, frames[i].event, frames[i].data, want)
		}
	}
	if frames[3].event != "compact_result" {
		t.Fatalf("last frame %s", frames[3].event)
	}
	// The result frame is byte-for-byte the JSON body the non-stream path writes.
	want, _ := json.Marshal(res)
	if frames[3].data != string(want) {
		t.Fatalf("result frame %s != JSON body %s", frames[3].data, want)
	}
}

func TestFIX117_UnknownTotalIsNullNotANumber(t *testing.T) {
	rec := httptest.NewRecorder()
	writeCompactStream(rec, rec, func(onProgress func(service.ExtractionProgress)) (*service.ManualCompactResult, error) {
		onProgress(service.ExtractionProgress{Folded: 5})
		return &service.ManualCompactResult{}, nil
	})
	frames := fix117Frames(t, rec.Body.String())
	if frames[0].data != `{"done":false,"folded":5,"reached":false,"total":null}` {
		t.Fatalf("unknown total must be null: %s", frames[0].data)
	}
}

func TestFIX117_CompactStreamReportsTheErrorAsTheLastFrameWithTheJSONPathsCodeAndMessage(t *testing.T) {
	rec := httptest.NewRecorder()
	writeCompactStream(rec, rec, func(onProgress func(service.ExtractionProgress)) (*service.ManualCompactResult, error) {
		onProgress(service.ExtractionProgress{Folded: 0, Total: 3, TotalKnown: true})
		return nil, service.ErrCompactNoSummary
	})
	frames := fix117Frames(t, rec.Body.String())
	last := frames[len(frames)-1]
	if last.event != "error" {
		t.Fatalf("last frame %s", last.event)
	}
	_, code, msg := compactErrorStatus(service.ErrCompactNoSummary)
	var got map[string]string
	_ = json.Unmarshal([]byte(last.data), &got)
	if got["code"] != code || got["message"] != msg {
		t.Fatalf("error frame %s must carry the JSON path's code/message (%s / %s)", last.data, code, msg)
	}
	if _, code2, _ := compactErrorStatus(errors.New("x")); code2 != CodeInternalError {
		t.Fatalf("unknown errors stay internal: %s", code2)
	}
}
