package handler

// FIX126 F1 — the files-per-message limit is the server's, fail closed.
// A send naming more file ids than MAX_FILES_PER_MESSAGE (default 5) is
// 422 TOO_MANY_FILES on both the streaming and the non-stream path, and
// writes nothing: no user row, no assistant row, no gateway call. Five is
// accepted.

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func fileIDsJSON(n int) string {
	ids := make([]string, n)
	for i := range ids {
		ids[i] = fmt.Sprintf("%q", uuid.NewString())
	}
	return "[" + strings.Join(ids, ",") + "]"
}

func TestFIX126_SendWithSixFilesIsRefusedAndWritesNothing(t *testing.T) {
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("OK")}})
	st := newFix125Stack(t, gw.URL+"/v1")

	for _, stream := range []bool{true, false} {
		body := fmt.Sprintf(`{"content":"FIX126 six files","stream":%t,"file_ids":%s}`, stream, fileIDsJSON(6))
		code, _, raw := postSend(t, st.probeStack, body, "")
		if code != http.StatusUnprocessableEntity || errorCode(raw) != CodeTooManyFiles {
			t.Fatalf("stream=%t: six files = %d %s, want 422 %s", stream, code, raw, CodeTooManyFiles)
		}
		if !strings.Contains(raw, "at most 5 files") {
			t.Fatalf("stream=%t: the message must name the limit: %s", stream, raw)
		}
	}
	if u, a := rowCounts(t, st.probeStack); u != 0 || a != 0 {
		t.Fatalf("a refused send wrote rows: %d user / %d assistant", u, a)
	}
	if st.svc.TurnInFlight(st.convID) {
		t.Fatal("a refused send left the conversation claimed")
	}

	// Five (the limit) is a normal send. The ids name no uploaded rows, so
	// nothing is linked, but the turn runs.
	body := fmt.Sprintf(`{"content":"FIX126 five files","stream":true,"file_ids":%s}`, fileIDsJSON(5))
	code, _, raw := postSend(t, st.probeStack, body, "")
	if code != http.StatusOK {
		t.Fatalf("five files: %d %s", code, raw)
	}
	if u, _ := rowCounts(t, st.probeStack); u != 1 {
		t.Fatalf("five files: %d user rows, want 1", u)
	}
}
