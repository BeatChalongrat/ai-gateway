// User-facing access-token JWT (the cookie the browser carries).
//
// Claims (chat-edge own JWT — NOT a Gateway token):
//
//	sub        — user UUID
//	tenant_id  — tenant UUID (set so authz middleware can reject
//	             cross-tenant access without a DB hit per request)
//	role       — user / admin / super_admin
//	iat / exp  — standard timestamps
//
// The refresh token is opaque and stored hashed in
// identity.refresh_tokens. Only the access token is a JWT; the
// refresh path swaps it on POST /auth/refresh. This split lets the
// service revoke refresh families on logout-everywhere without
// invalidating every issued access token.
package auth

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// UserClaims is the JWT body carried by an access token.
type UserClaims struct {
	UserID   uuid.UUID `json:"sub"`
	TenantID uuid.UUID `json:"tenant_id"`
	Role     string    `json:"role"`
	jwt.RegisteredClaims
}

// IssueAccessToken signs a user JWT with HS256.
//
// ``secret`` is read from Config.UserJWTSecret. Callers never read
// the secret directly — this keeps the signing key behind one
// audit-visible function.
func IssueAccessToken(
	secret string,
	userID, tenantID uuid.UUID,
	role string,
	ttl time.Duration,
) (string, error) {
	now := time.Now().UTC()
	claims := UserClaims{
		UserID:   userID,
		TenantID: tenantID,
		Role:     role,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    "chat-edge-api",
			Audience:  jwt.ClaimStrings{"chat-edge-api"},
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(ttl)),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString([]byte(secret))
	if err != nil {
		return "", fmt.Errorf("issue access token: %w", err)
	}
	return signed, nil
}

// ParseAccessToken verifies + decodes a user JWT. Returns an error
// for any failure path (bad signature, expired, wrong alg). The
// HTTP layer maps any error to 401.
func ParseAccessToken(secret, raw string) (*UserClaims, error) {
	claims := &UserClaims{}
	tok, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		if t.Method.Alg() != jwt.SigningMethodHS256.Alg() {
			return nil, fmt.Errorf("unexpected signing method: %v", t.Method.Alg())
		}
		return []byte(secret), nil
	})
	if err != nil {
		return nil, err
	}
	if !tok.Valid {
		return nil, errors.New("invalid token")
	}
	return claims, nil
}
