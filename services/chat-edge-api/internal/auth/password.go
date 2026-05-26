// Argon2id password hashing.
//
// Argon2id is the OWASP-recommended modern KDF (libsodium default,
// PHC winner). We use the parameter floor from OWASP's 2023 cheat
// sheet: m=64MiB, t=3, p=2, salt=16B, key=32B. Tune via the package
// constants if benchmarking reveals headroom.
//
// Encoded format is the standard PHC string:
//
//	$argon2id$v=19$m=65536,t=3,p=2$<salt-b64>$<hash-b64>
//
// which embeds the parameters so a future tuning round can verify
// old hashes without breaking existing logins. The format is
// portable across Go and Python (passlib's argon2id matches).
package auth

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"

	"golang.org/x/crypto/argon2"
)

// Parameter floor — see OWASP 2023.
const (
	argon2Memory      = 64 * 1024 // KiB → 64 MiB
	argon2Time        = 3
	argon2Parallelism = 2
	argon2SaltLen     = 16
	argon2KeyLen      = 32
)

// HashPassword returns a PHC-encoded argon2id hash of ``password``.
func HashPassword(password string) (string, error) {
	salt := make([]byte, argon2SaltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("hash password: read salt: %w", err)
	}
	key := argon2.IDKey(
		[]byte(password),
		salt,
		argon2Time,
		argon2Memory,
		argon2Parallelism,
		argon2KeyLen,
	)
	encoded := fmt.Sprintf(
		"$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version,
		argon2Memory,
		argon2Time,
		argon2Parallelism,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key),
	)
	return encoded, nil
}

// VerifyPassword returns nil iff ``password`` matches ``encoded``.
// Returns a non-nil error on bad-format encoded strings or mismatch
// — caller should treat any error as "wrong password" and respond
// with 401, never leaking the specific failure mode to the client.
func VerifyPassword(password, encoded string) error {
	parts := strings.Split(encoded, "$")
	// Layout: ["", "argon2id", "v=19", "m=...,t=...,p=...", salt, key]
	if len(parts) != 6 || parts[1] != "argon2id" {
		return errors.New("password: unsupported hash format")
	}
	var version int
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil {
		return fmt.Errorf("password: parse version: %w", err)
	}
	if version != argon2.Version {
		return fmt.Errorf("password: argon2 version mismatch (got %d, want %d)", version, argon2.Version)
	}
	var memory, time uint32
	var parallelism uint8
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &memory, &time, &parallelism); err != nil {
		return fmt.Errorf("password: parse params: %w", err)
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return fmt.Errorf("password: decode salt: %w", err)
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return fmt.Errorf("password: decode hash: %w", err)
	}
	got := argon2.IDKey(
		[]byte(password),
		salt,
		time,
		memory,
		parallelism,
		uint32(len(want)),
	)
	if subtle.ConstantTimeCompare(want, got) != 1 {
		return errors.New("password: mismatch")
	}
	return nil
}
