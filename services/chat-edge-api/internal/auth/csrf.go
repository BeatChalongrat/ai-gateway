// CSRF double-submit-cookie token helpers.
//
// Cookie-authenticated APIs need CSRF protection because the browser
// auto-attaches cookies on cross-origin form posts. The accepted
// pattern is:
//
//  1. Server sets ``X-CSRF-Token: <random>`` cookie (SameSite=Strict).
//  2. Client copies cookie value into ``X-CSRF-Token`` request header.
//  3. Server compares header to cookie. Match → proceed.
//
// The token is an HMAC of (session-id, random nonce) so a leaked
// cookie alone can't be replayed across sessions.
package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
)

// IssueCSRFToken returns a fresh ``nonce.signature`` pair bound to
// ``sessionID`` (typically the access-token jti or user id).
func IssueCSRFToken(secret, sessionID string) (string, error) {
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	nonceHex := hex.EncodeToString(nonce)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(sessionID))
	mac.Write([]byte("|"))
	mac.Write([]byte(nonceHex))
	sig := hex.EncodeToString(mac.Sum(nil))
	return nonceHex + "." + sig, nil
}

// VerifyCSRFToken returns nil iff ``token`` is a valid HMAC for
// ``sessionID`` under ``secret``. Constant-time comparison.
func VerifyCSRFToken(secret, sessionID, token string) error {
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return errors.New("csrf: malformed token")
	}
	nonceHex, sig := parts[0], parts[1]
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(sessionID))
	mac.Write([]byte("|"))
	mac.Write([]byte(nonceHex))
	want := hex.EncodeToString(mac.Sum(nil))
	if !hmac.Equal([]byte(want), []byte(sig)) {
		return errors.New("csrf: signature mismatch")
	}
	return nil
}
