package handler

// FIX47.6 Part D — the desktop host addresses messages by its own ids
// ("asst_<32 hex>" / "u_<32 hex>", minted in the Electron coordinator).
// The feedback route resolves exactly that shape through client_id and
// nothing else: a UUID takes the normal path, anything else stays a 400.

import "testing"

func TestHostClientIDShape(t *testing.T) {
	accept := []string{
		"asst_49f75c119315485e8ea7a9240191ac11",
		"u_7c6bde34832842e088eb94f31d6652ec",
	}
	for _, id := range accept {
		if !hostClientID.MatchString(id) {
			t.Errorf("expected accept: %s", id)
		}
	}
	reject := []string{
		"0f6e84e4-fed1-493b-9d76-eeb85a683066", // server UUID — handled by uuid.Parse, not this
		"asst_49F75C119315485E8EA7A9240191AC11", // uppercase hex is not what the host mints
		"asst_49f75c11",                        // too short
		"tool_49f75c119315485e8ea7a9240191ac11", // unknown prefix
		"asst_49f75c119315485e8ea7a9240191ac11x", // trailing junk
		"",
	}
	for _, id := range reject {
		if hostClientID.MatchString(id) {
			t.Errorf("expected reject: %s", id)
		}
	}
}
