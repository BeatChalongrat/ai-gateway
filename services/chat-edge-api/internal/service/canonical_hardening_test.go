package service

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func completeRow(content string) model.Message {
	sha, ln, sv, gen := canonicalSHA256(content), len([]rune(content)), 1, "req_g"
	return model.Message{
		Role: "assistant", ContentMasked: "DISPLAY 80000",
		ContentCanonical:       sp(content),
		CanonicalStatus:        CanonicalStatusComplete,
		CanonicalSHA256:        sp(sha),
		CanonicalLength:        &ln,
		CanonicalSchemaVersion: &sv,
		CanonicalGenerationID:  sp(gen),
	}
}

// H1 — read-time validation rejects any tampered/under-specified row.
func TestValidateStoredCanonicalMessage(t *testing.T) {
	if c, ok, _ := ValidateStoredCanonicalMessage(completeRow("ผลลัพธ์ <r0>")); !ok || c != "ผลลัพธ์ <r0>" {
		t.Fatalf("valid row should pass")
	}
	bad := func(reason CanonicalRejectReason, mut func(*model.Message)) {
		m := completeRow("ok <r0>")
		mut(&m)
		_, ok, r := ValidateStoredCanonicalMessage(m)
		if ok || r != reason {
			t.Fatalf("expected reject %s, got ok=%v reason=%s", reason, ok, r)
		}
	}
	bad(RejectStatus, func(m *model.Message) { m.CanonicalStatus = "legacy" })
	bad(RejectNilContent, func(m *model.Message) { m.ContentCanonical = nil })
	bad(RejectSchema, func(m *model.Message) { v := 2; m.CanonicalSchemaVersion = &v })
	bad(RejectSchema, func(m *model.Message) { m.CanonicalSchemaVersion = nil })
	bad(RejectGeneration, func(m *model.Message) { m.CanonicalGenerationID = nil })
	bad(RejectGeneration, func(m *model.Message) { e := ""; m.CanonicalGenerationID = &e })
	bad(RejectSHA, func(m *model.Message) { s := "00"; m.CanonicalSHA256 = &s })                           // not 64-hex
	bad(RejectSHA, func(m *model.Message) { s := "Z" + (*m.CanonicalSHA256)[1:]; m.CanonicalSHA256 = &s }) // non-hex
	bad(RejectLength, func(m *model.Message) { n := *m.CanonicalLength + 1; m.CanonicalLength = &n })
	bad(RejectSHA, func(m *model.Message) { c := "xk <r0>"; m.ContentCanonical = &c }) // same rune length, sha mismatch
}

// H1 — a complete-status row with a wrong SHA never reaches the provider.
func TestBuildGatewayRequest_TamperedExcluded(t *testing.T) {
	m := completeRow("good <r0>")
	wrong := "0000000000000000000000000000000000000000000000000000000000000000"
	m.CanonicalSHA256 = &wrong // status=complete but tampered digest
	m.ContentMasked = "raw 80000"
	history := []model.Message{{Role: "user", ContentMasked: "q"}, m}
	req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, true)
	for _, gm := range req.Messages {
		if gm.Role == "assistant" {
			t.Fatalf("tampered complete row must be excluded")
		}
		if s, ok := gm.Content.(string); ok && containsStr(s, "80000") {
			t.Fatalf("tampered row leaked display")
		}
	}
}

// H2 — role-sequence normalization.
func TestNormalizeRoleSequence(t *testing.T) {
	in := []GatewayMessage{
		{Role: "system", Content: "sys"},
		{Role: "user", Content: "a"},
		{Role: "user", Content: "b"},      // consecutive user (assistant was excluded)
		{Role: "tool", Content: "orphan"}, // orphan tool result
		{Role: "user", Content: "c"},
	}
	out := normalizeRoleSequence(in)
	// system + one merged user (a\n\nb) + ... tool dropped (prev is user) + user c
	if out[0].Role != "system" {
		t.Fatalf("system must be preserved first")
	}
	// after merge: system, user(a\n\nb), user(c)?  tool dropped → then user c merges with prev user
	roles := make([]string, 0)
	for _, m := range out {
		roles = append(roles, m.Role)
		if m.Role == "tool" {
			t.Fatalf("orphan tool must be dropped")
		}
	}
	// all user turns collapse to a single merged user message
	users := 0
	var merged string
	for _, m := range out {
		if m.Role == "user" {
			users++
			merged, _ = m.Content.(string)
		}
	}
	if users != 1 {
		t.Fatalf("expected 1 merged user, got %d (%v)", users, roles)
	}
	for _, want := range []string{"a", "b", "c"} {
		if !containsStr(merged, want) {
			t.Fatalf("merged user missing %q: %q", want, merged)
		}
	}
}

func TestNormalizeRoleSequence_KeepsAlternation(t *testing.T) {
	in := []GatewayMessage{
		{Role: "user", Content: "u1"},
		{Role: "assistant", Content: "a1"},
		{Role: "user", Content: "u2"},
	}
	out := normalizeRoleSequence(in)
	if len(out) != 3 {
		t.Fatalf("valid alternation must be unchanged, got %d", len(out))
	}
}
