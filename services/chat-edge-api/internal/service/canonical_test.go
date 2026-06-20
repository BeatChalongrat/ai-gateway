package service

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func mkArtifact(content string) *CanonicalArtifact {
	return &CanonicalArtifact{
		SchemaVersion:    1,
		GenerationID:     "req_abc",
		RequestID:        "req_abc",
		CanonicalContent: content,
		CanonicalSHA256:  canonicalSHA256(content),
		CanonicalLength:  len([]rune(content)),
		Status:           CanonicalStatusComplete,
	}
}

func TestValidateCanonicalArtifact(t *testing.T) {
	for _, c := range []string{"ผลลัพธ์ <r0>", "emoji 😀 <r1>", "line\nbreak", "", "<r0> <<NAME_1>>"} {
		if !ValidateCanonicalArtifact(mkArtifact(c)) {
			t.Fatalf("expected valid artifact for %q", c)
		}
	}
	// rune count vs byte length: Thai "ก" is 1 rune, 3 bytes.
	a := mkArtifact("ก")
	if a.CanonicalLength != 1 {
		t.Fatalf("expected rune length 1, got %d", a.CanonicalLength)
	}

	bad := func(mut func(*CanonicalArtifact)) {
		a := mkArtifact("ok <r0>")
		mut(a)
		if ValidateCanonicalArtifact(a) {
			t.Fatalf("expected invalid artifact")
		}
	}
	bad(func(a *CanonicalArtifact) { a.SchemaVersion = 99 })
	bad(func(a *CanonicalArtifact) { a.Status = "cancelled" })
	bad(func(a *CanonicalArtifact) { a.GenerationID = "" })
	bad(func(a *CanonicalArtifact) { a.GenerationID = "other" }) // gen != req
	bad(func(a *CanonicalArtifact) { a.CanonicalSHA256 = "0" })
	bad(func(a *CanonicalArtifact) { a.CanonicalSHA256 = "ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789" }) // uppercase
	bad(func(a *CanonicalArtifact) { a.CanonicalSHA256 = "z" + a.CanonicalSHA256[1:] })                                       // non-hex
	bad(func(a *CanonicalArtifact) { a.CanonicalLength++ })                                                                   // wrong length
	bad(func(a *CanonicalArtifact) { a.CanonicalContent = "tampered <r0>" })                                                  // sha mismatch
	// byte-length-right but rune-length-wrong: claim 3 (byte count of ก)
	bad(func(a *CanonicalArtifact) { a.CanonicalContent = "ก"; a.CanonicalSHA256 = canonicalSHA256("ก"); a.CanonicalLength = 3 })
}

func TestCanonicalStreamStateDeriveStatus(t *testing.T) {
	// no capability → unsupported
	s := &CanonicalStreamState{}
	if st, _, _ := s.DeriveStatus(true); st != CanonicalStatusUnsupported {
		t.Fatalf("want unsupported, got %s", st)
	}
	// capability + final → complete
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{GenerationID: "g", Supported: true})
	s.OnFinal(mkArtifact("hi <r0>"))
	if st, art, _ := s.DeriveStatus(true); st != CanonicalStatusComplete || art == nil {
		t.Fatalf("want complete+artifact, got %s", st)
	}
	// capability + unavailable → unavailable (errcode preserved)
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{Supported: true})
	s.OnUnavailable(&CanonicalUnavailableFrame{Status: "unavailable", ErrorCode: "x"})
	if st, art, ec := s.DeriveStatus(true); st != CanonicalStatusUnavailable || art != nil || ec != "x" {
		t.Fatalf("want unavailable, got %s ec=%s", st, ec)
	}
	// capability + conflicting finals → invalid
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{Supported: true})
	s.OnFinal(mkArtifact("a <r0>"))
	s.OnFinal(mkArtifact("b <r1>"))
	if st, _, _ := s.DeriveStatus(true); st != CanonicalStatusInvalid {
		t.Fatalf("want invalid, got %s", st)
	}
	// capability + duplicate identical final → idempotent complete
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{Supported: true})
	s.OnFinal(mkArtifact("same <r0>"))
	s.OnFinal(mkArtifact("same <r0>"))
	if st, _, _ := s.DeriveStatus(true); st != CanonicalStatusComplete {
		t.Fatalf("want complete (idempotent), got %s", st)
	}
	// capability + no terminal + completed → missing
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{Supported: true})
	if st, _, _ := s.DeriveStatus(true); st != CanonicalStatusMissing {
		t.Fatalf("want missing, got %s", st)
	}
	// capability + no terminal + not completed → cancelled
	s = &CanonicalStreamState{}
	s.OnCapability(&CanonicalCapabilityFrame{Supported: true})
	if st, _, _ := s.DeriveStatus(false); st != CanonicalStatusCancelled {
		t.Fatalf("want cancelled, got %s", st)
	}
}

func TestParseStreamChunkCanonicalEvents(t *testing.T) {
	// final event with SSE-like content round-trips intact, not forwarded
	// as a delta.
	raw := `{"schema_version":1,"generation_id":"req_abc","request_id":"req_abc","canonical_content":"x [DONE]\ndata: y","canonical_sha256":"` +
		canonicalSHA256("x [DONE]\ndata: y") + `","canonical_length":` + itoa(len([]rune("x [DONE]\ndata: y"))) + `,"status":"complete"}`
	ev, err := parseStreamChunk(raw, "dlp.canonical_final")
	if err != nil || ev.CanonicalFinal == nil {
		t.Fatalf("expected canonical_final parsed, err=%v", err)
	}
	if ev.Delta != "" {
		t.Fatalf("canonical event must not be a delta")
	}
	if !ValidateCanonicalArtifact(ev.CanonicalFinal) {
		t.Fatalf("expected the parsed artifact to validate")
	}
	// capability + unavailable
	ev, _ = parseStreamChunk(`{"schema_version":1,"generation_id":"g","supported":true}`, "dlp.canonical_capability")
	if ev.CanonicalCapability == nil || !ev.CanonicalCapability.Supported {
		t.Fatalf("expected capability frame")
	}
	ev, _ = parseStreamChunk(`{"schema_version":1,"generation_id":"g","status":"unavailable","error_code":"c"}`, "dlp.canonical_unavailable")
	if ev.CanonicalUnavailable == nil || ev.CanonicalUnavailable.ErrorCode != "c" {
		t.Fatalf("expected unavailable frame")
	}
	// a normal data chunk (no event name) is unaffected.
	ev, err = parseStreamChunk(`{"choices":[{"delta":{"content":"hello"}}]}`, "")
	if err != nil || ev.Delta != "hello" {
		t.Fatalf("expected normal delta, got %q err=%v", ev.Delta, err)
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

func sp(s string) *string { return &s }

// CORE TEST — canonical-only provider history closes the raw-history leak.
func TestBuildGatewayRequestCanonicalOnly(t *testing.T) {
	cc := "ผลลัพธ์คือ <r3>"
	csha, cln, csv, cgen := canonicalSHA256(cc), len([]rune(cc)), 1, "req_g3"
	history := []model.Message{
		{Role: "user", ContentMasked: "เงินเดือนเท่าไหร่"},
		{
			Role:             "assistant",
			ContentMasked:    "ผลลัพธ์คือ 80000", // restored DISPLAY (raw)
			ContentCanonical: sp(cc),              // tokenized CANONICAL
			CanonicalStatus:  CanonicalStatusComplete,
			// H1 — full integrity metadata so read-time validation passes.
			CanonicalSHA256:        sp(csha),
			CanonicalLength:        &cln,
			CanonicalSchemaVersion: &csv,
			CanonicalGenerationID:  sp(cgen),
		},
		{Role: "user", ContentMasked: "ขอบคุณ"},
	}

	// Enabled → provider sees CANONICAL, never the restored display.
	req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, true)
	var asstContent string
	for _, m := range req.Messages {
		if m.Role == "assistant" {
			asstContent, _ = m.Content.(string)
		}
		if s, ok := m.Content.(string); ok && containsStr(s, "80000") {
			t.Fatalf("provider history leaked restored display: %q", s)
		}
	}
	if asstContent != "ผลลัพธ์คือ <r3>" {
		t.Fatalf("expected canonical assistant content, got %q", asstContent)
	}

	// Disabled → legacy behaviour (uses ContentMasked / display).
	reqOff := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, false)
	foundDisplay := false
	for _, m := range reqOff.Messages {
		if m.Role == "assistant" {
			if s, _ := m.Content.(string); s == "ผลลัพธ์คือ 80000" {
				foundDisplay = true
			}
		}
	}
	if !foundDisplay {
		t.Fatalf("disabled path must use display content (legacy)")
	}
}

func TestBuildGatewayRequestExcludesNonComplete(t *testing.T) {
	for _, status := range []string{
		CanonicalStatusLegacy, CanonicalStatusMissing, CanonicalStatusUnavailable,
		CanonicalStatusInvalid, CanonicalStatusCancelled, CanonicalStatusError,
	} {
		history := []model.Message{
			{Role: "user", ContentMasked: "q"},
			{Role: "assistant", ContentMasked: "secret 80000", CanonicalStatus: status},
			{Role: "user", ContentMasked: "q2"},
		}
		req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, true)
		for _, m := range req.Messages {
			if m.Role == "assistant" {
				t.Fatalf("status %s assistant row must be EXCLUDED, got %v", status, m.Content)
			}
			if s, ok := m.Content.(string); ok && containsStr(s, "80000") {
				t.Fatalf("status %s leaked display", status)
			}
		}
		// H2 — after excluding the assistant, the two consecutive user
		// turns are MERGED into one (provider-valid sequence), still
		// carrying both user texts and no assistant/display content.
		users := 0
		var userText string
		for _, m := range req.Messages {
			if m.Role == "user" {
				users++
				userText, _ = m.Content.(string)
			}
		}
		if users != 1 {
			t.Fatalf("status %s: expected 1 merged user row, got %d", status, users)
		}
		if !containsStr(userText, "q") || !containsStr(userText, "q2") {
			t.Fatalf("status %s: merged user must keep both turns: %q", status, userText)
		}
	}
}

func containsStr(haystack, needle string) bool {
	return len(needle) > 0 && len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0
}

func indexOf(h, n string) int {
	for i := 0; i+len(n) <= len(h); i++ {
		if h[i:i+len(n)] == n {
			return i
		}
	}
	return -1
}
