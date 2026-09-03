package service

// FIX49 Part B (8a) — the memory extractor must never eat masked text.
// A permanent memory row holding a DLP placeholder is the user's real
// value destroyed forever (the 2026-09-03 audit found 6 such rows across
// 3 users, re-injected into every conversation). These pin the exact
// shapes the gateway's transformer can emit, and the false-positive
// boundary: ordinary prose — Thai or English — must never be refused.

import "testing"

func TestContainsMaskedContentCatchesAllTransformerShapes(t *testing.T) {
	masked := []string{
		"คนที่ต้องขออนุญาตก่อนคือ <<PERSON_A>>",           // pseudonym
		"contact <<EMAIL_1>> for details",             // pseudonym, digit index
		"User HEALTH_1 is allergic to seafood.",       // bracket-stripped residue
		"User HEALTH_2 is an architect in Chiang Mai", // residue, second index
		"the key is <r0>",                             // secret-class token
		"and <r1a-b> too",                             // secret-class, suffixed
		"call [PHONE_0] now",                          // legacy indexed
		"send to [[EMAIL_2]]",                         // legacy double-bracket
		"approver PERSON_B said yes",                  // bare residue, letter index
	}
	for _, s := range masked {
		if !containsMaskedContent(s) {
			t.Errorf("expected masked: %q", s)
		}
	}
}

func TestContainsMaskedContentNeverFiresOnOrdinaryProse(t *testing.T) {
	clean := []string{
		"ผู้ใช้ชอบคำตอบสั้น ไม่เกิน 5 ข้อ",
		"The user prefers TypeScript over JavaScript.",
		"User is allergic to seafood.",             // the same fact, unmasked
		"Meeting at ROOM_A is fine",               // unknown category word — not a detector name
		"HEALTH matters to the user",              // category word without index
		"the CARD game night is on Friday",        // category word, no _N
		"อีเมลของฉันคือ (ลบไปแล้ว)",
		"vitamin B_12 helps",                      // underscore-digit on a non-category
	}
	for _, s := range clean {
		if containsMaskedContent(s) {
			t.Errorf("false positive on clean prose: %q", s)
		}
	}
}
