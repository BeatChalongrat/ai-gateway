package provider

import (
	"encoding/base64"
	"strings"
	"testing"
)

// goldenVectors were produced by the REAL Python backend:
//
//	cd backend && PYTHONIOENCODING=utf-8 python -c "
//	from app.core.security import encrypt_provider_key as e
//	print(e('sk-test-12345678', '00'*32))"
//
// They are frozen here so the Go decrypt is pinned to Python's exact on-disk
// format (version byte, nonce position, tag handling, std base64) without a
// runtime Python dependency. Regenerate if the format ever changes.
var goldenVectors = []struct {
	name      string
	plaintext string
	masterKey string
	blob      string
}{
	{
		name:      "openai-style",
		plaintext: "sk-test-12345678",
		masterKey: strings.Repeat("00", 32),
		blob:      "Ae7eLzGIdGVY4tfQAzNFElflGP1Wso9DuOKvzNv9rk+4rmXc4UvNAQo9476M",
	},
	{
		name:      "anthropic-style-long",
		plaintext: "sk-ant-api03-" + strings.Repeat("A", 40),
		masterKey: strings.Repeat("ab", 32),
		blob:      "AVC3cxlfzZpTV6OyjiaGUCnr4scho8PgkHg/QI0goPOmWVACB2zvNCx1oKUzltJTuuC6R1Ql6vriy3R4XkwTyBMJDthtPUuBXmQLArtJDgRvUw==",
	},
	{
		name:      "short",
		plaintext: "gw_short",
		masterKey: strings.Repeat("cd", 32),
		blob:      "ATS82Xc9QoC8oeGWCvO8xoIqkABYbCZAd5uwr8sytRrbiXfcIg==",
	},
}

// The load-bearing parity test: Go must decrypt what Python encrypted.
func TestDecryptPythonGoldenVectors(t *testing.T) {
	for _, v := range goldenVectors {
		t.Run(v.name, func(t *testing.T) {
			got, err := DecryptProviderKey(v.blob, v.masterKey)
			if err != nil {
				t.Fatalf("decrypt failed: %v", err)
			}
			if got != v.plaintext {
				t.Fatalf("decrypt = %q, want %q", got, v.plaintext)
			}
		})
	}
}

func TestEncryptDecryptRoundtrip(t *testing.T) {
	master := strings.Repeat("9f", 32)
	for _, pt := range []string{
		"sk-ant-api03-realkey",
		"คีย์ลับ-๑๒๓-secret", // unicode round-trips byte-for-byte
		"",                  // empty plaintext -> 29-byte blob (>= minBlobLen)
	} {
		enc, err := EncryptProviderKey(pt, master)
		if err != nil {
			t.Fatalf("encrypt(%q): %v", pt, err)
		}
		dec, err := DecryptProviderKey(enc, master)
		if err != nil {
			t.Fatalf("decrypt(%q): %v", pt, err)
		}
		if dec != pt {
			t.Fatalf("roundtrip = %q, want %q", dec, pt)
		}
	}
}

func TestEncryptNonceIsRandom(t *testing.T) {
	master := strings.Repeat("00", 32)
	a, _ := EncryptProviderKey("same", master)
	b, _ := EncryptProviderKey("same", master)
	if a == b {
		t.Fatal("two encryptions of the same plaintext produced identical blobs (nonce not random)")
	}
}

func TestWrongMasterKeyFails(t *testing.T) {
	v := goldenVectors[0]
	if _, err := DecryptProviderKey(v.blob, strings.Repeat("ff", 32)); err == nil {
		t.Fatal("expected decrypt to fail with the wrong master key")
	}
}

func TestRejectsLegacyBase64(t *testing.T) {
	// A pre-migration plain-Base64 value has no version byte.
	legacy := base64.StdEncoding.EncodeToString([]byte("sk-ant-api03-some-real-looking-key-value"))
	if _, err := DecryptProviderKey(legacy, strings.Repeat("00", 32)); err != ErrNotAESGCM {
		t.Fatalf("expected ErrNotAESGCM, got %v", err)
	}
}

func TestBadMasterKeyLength(t *testing.T) {
	if _, err := DecryptProviderKey(goldenVectors[0].blob, "abcd"); err != ErrBadMasterKey {
		t.Fatalf("expected ErrBadMasterKey, got %v", err)
	}
	if _, err := EncryptProviderKey("x", "notlongenough"); err != ErrBadMasterKey {
		t.Fatalf("expected ErrBadMasterKey, got %v", err)
	}
}

func TestTamperedBlobFails(t *testing.T) {
	v := goldenVectors[0]
	raw, _ := base64.StdEncoding.DecodeString(v.blob)
	raw[len(raw)-1] ^= 0x01 // flip a tag bit
	tampered := base64.StdEncoding.EncodeToString(raw)
	if _, err := DecryptProviderKey(tampered, v.masterKey); err != ErrDecrypt {
		t.Fatalf("expected ErrDecrypt on tampered blob, got %v", err)
	}
}
