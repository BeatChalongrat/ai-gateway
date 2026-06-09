// Package provider holds the gateway's provider-facing primitives. This
// file is the first piece of Phase 2 prep: AES-256-GCM decryption of
// provider API keys, byte-compatible with the Python backend's
// app.core.security.encrypt_provider_key / decrypt_provider_key.
//
// On-disk format (must match Python exactly):
//
//	base64( 0x01 || 12-byte nonce || ciphertext || 16-byte GCM tag )
//
// The leading 0x01 version byte makes the scheme self-describing (and the
// Base64->AES migration idempotent). Go's cipher.NewGCM appends the 16-byte
// tag to the ciphertext, which is exactly what Python's AESGCM produces, so
// gcm.Open(nonce, blob[13:]) round-trips a Python-encrypted value.
package provider

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
)

// versionAESGCM is the 1-byte scheme tag prefixed to every blob.
const versionAESGCM = 0x01

// minBlobLen = 1 version + 12 nonce + 16 GCM tag (empty plaintext). This
// matches Python's _MIN_AES_BLOB_LEN = 29. (The original spec used 30,
// which would reject a valid empty-plaintext blob.)
const minBlobLen = 1 + 12 + 16

var (
	// ErrNotAESGCM means the value isn't a versioned AES-GCM blob — e.g. a
	// legacy plain-Base64 value that hasn't been migrated yet.
	ErrNotAESGCM = errors.New("not an AES-GCM encrypted value (missing version byte)")
	// ErrBadMasterKey means the master key wasn't 32 bytes of hex.
	ErrBadMasterKey = errors.New("master key must be 32 bytes (64 hex chars)")
	// ErrDecrypt means authentication failed (wrong key or tampered data).
	ErrDecrypt = errors.New("provider key decryption failed (wrong master key or corrupt data)")
)

func gcmFor(masterKeyHex string) (cipher.AEAD, error) {
	key, err := hex.DecodeString(masterKeyHex)
	if err != nil || len(key) != 32 {
		return nil, ErrBadMasterKey
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block) // 12-byte nonce, 16-byte tag — matches Python
}

// DecryptProviderKey reverses Python's encrypt_provider_key. masterKeyHex
// must be 64 hex chars (32 bytes / 256 bits).
func DecryptProviderKey(encryptedB64, masterKeyHex string) (string, error) {
	blob, err := base64.StdEncoding.DecodeString(encryptedB64)
	if err != nil {
		return "", ErrNotAESGCM
	}
	if len(blob) < minBlobLen || blob[0] != versionAESGCM {
		return "", ErrNotAESGCM
	}
	gcm, err := gcmFor(masterKeyHex)
	if err != nil {
		return "", err
	}
	nonce := blob[1:13]
	ciphertext := blob[13:] // ciphertext || 16-byte tag
	plaintext, err := gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return "", ErrDecrypt
	}
	return string(plaintext), nil
}

// EncryptProviderKey produces a blob in the same format, readable by
// Python's decrypt_provider_key. Useful when Go later writes keys; included
// now so the round-trip is testable. Uses a fresh random 12-byte nonce.
func EncryptProviderKey(plaintext, masterKeyHex string) (string, error) {
	gcm, err := gcmFor(masterKeyHex)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	ct := gcm.Seal(nil, nonce, []byte(plaintext), nil)
	blob := make([]byte, 0, 1+len(nonce)+len(ct))
	blob = append(blob, versionAESGCM)
	blob = append(blob, nonce...)
	blob = append(blob, ct...)
	return base64.StdEncoding.EncodeToString(blob), nil
}
