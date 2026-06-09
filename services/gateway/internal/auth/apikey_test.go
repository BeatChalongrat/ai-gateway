package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

// HashKey must equal lowercase hex of SHA-256 — byte-for-byte what
// Python's app.core.security.hash_api_key produces, so the gateway and
// Python resolve the same row for the same raw key.
func TestHashKeyMatchesSHA256Hex(t *testing.T) {
	cases := []string{"gw_abc123", "gw_" + "a", "", "ทดสอบ"}
	for _, raw := range cases {
		sum := sha256.Sum256([]byte(raw))
		want := hex.EncodeToString(sum[:])
		got := HashKey(raw)
		if got != want {
			t.Fatalf("HashKey(%q) = %q, want %q", raw, got, want)
		}
		if len(got) != 64 {
			t.Fatalf("HashKey(%q) length = %d, want 64", raw, len(got))
		}
	}
}

func TestHashKeyDeterministic(t *testing.T) {
	if HashKey("gw_xyz") != HashKey("gw_xyz") {
		t.Fatal("HashKey is not deterministic")
	}
	if HashKey("gw_a") == HashKey("gw_b") {
		t.Fatal("distinct keys collided")
	}
}
