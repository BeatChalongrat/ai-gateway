// Internal service-to-service JWT used on every Gateway API call.
//
// The user JWT (jwt.go) is for browser sessions. This service JWT
// is for the chat-edge-api -> gateway-api hop. Two key differences:
//
//   - Different signing secret (Config.ServiceJWTSecret) so leaking
//     one set of secrets doesn't compromise both surfaces.
//   - Aggressively short TTL (60s by default) so a captured token
//     has a near-zero replay window. Each Gateway request re-mints.
//
// The Gateway side validates ``iss == chat-edge-api`` and
// ``aud == gateway-api`` — see gateway-api docs for the consumer.
package auth

import (
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// ServiceClaims is the JWT body carried on Gateway calls. We do NOT
// forward end-user identity here — the Gateway has its own per-
// request auth via the X-Gateway-User-Id header (signed under the
// same SERVICE_JWT_SECRET) when the call carries user context.
type ServiceClaims struct {
	jwt.RegisteredClaims
}

// IssueServiceJWT mints a fresh short-lived JWT for a Gateway call.
//
// Called once per outbound HTTP request to Gateway. The TTL comes
// from Config.ServiceJWTTTL (default 60s). Calling this on every
// request rather than caching avoids the trade-off of needing to
// reason about token rotation under multi-worker setups.
func IssueServiceJWT(secret string, ttl time.Duration) (string, error) {
	now := time.Now().UTC()
	claims := ServiceClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    "chat-edge-api",
			Audience:  jwt.ClaimStrings{"gateway-api"},
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(ttl)),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString([]byte(secret))
	if err != nil {
		return "", fmt.Errorf("issue service jwt: %w", err)
	}
	return signed, nil
}
