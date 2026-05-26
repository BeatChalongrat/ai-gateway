// Portal JWT parser — verifies tokens minted by the FastAPI Portal
// (services/portal/auth_service.py).
//
// Wire shape (HS256, signed with “settings.portal_jwt_secret“):
//
//	{
//	  "sub": "<portal_user_id uuid>",
//	  "email": "user@example.com",
//	  "type": "portal",
//	  "token_use": "access",
//	  "tenant_id": "<uuid or null>",
//	  "tenant_role": "owner" | "member",
//	  "token_version": 2,
//	  "iat": ..., "exp": ..., "jti": "..."
//	}
//
// Chat Edge does NOT validate “token_version“ — that's FastAPI's
// responsibility on the Portal side. We trust the signature + expiry
// + the “type=="portal"“ + “token_use=="access"“ markers. A stale
// token that survived a Portal-side bump will fail when the user
// next hits the Portal; in the meantime, Chat Edge happily serves
// the cached identity. That's the documented contract.
package auth

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// PortalClaims is the decoded JWT body produced by FastAPI's Portal.
//
// Optional “TenantID“ reflects the production Portal schema: a
// freshly removed member can transiently carry “tenant_id=null“
// between drop and re-invite. Handlers that need a tenant must
// reject this case themselves — the middleware only fails closed on
// missing “sub“ / wrong type.
type PortalClaims struct {
	Sub          string `json:"sub"`
	Email        string `json:"email"`
	Type         string `json:"type"`
	TokenUse     string `json:"token_use"`
	TenantID     string `json:"tenant_id"`
	TenantRole   string `json:"tenant_role"`
	TokenVersion int    `json:"token_version"`
	jwt.RegisteredClaims
}

// Parsed is the subset of PortalClaims downstream code consumes.
type Parsed struct {
	PortalUserID uuid.UUID
	Email        string
	TenantID     string // raw Portal tenant id; may be a UUID or slug
	TenantRole   string // "owner" | "member"
}

// ParsePortalToken verifies a Bearer token from the Portal and
// returns its decoded identity. Returns an error on any signature /
// alg / expiry / shape failure.
func ParsePortalToken(secret, raw string) (*Parsed, error) {
	if raw == "" {
		return nil, errors.New("portal: empty token")
	}
	claims := &PortalClaims{}
	tok, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		if t.Method.Alg() != jwt.SigningMethodHS256.Alg() {
			return nil, fmt.Errorf("portal: unexpected alg %v", t.Method.Alg())
		}
		return []byte(secret), nil
	})
	if err != nil {
		return nil, fmt.Errorf("portal: %w", err)
	}
	if !tok.Valid {
		return nil, errors.New("portal: invalid token")
	}
	if claims.Type != "portal" {
		return nil, fmt.Errorf("portal: wrong token type %q", claims.Type)
	}
	if claims.TokenUse != "" && claims.TokenUse != "access" {
		// Refresh tokens are allowed only at the Portal's /refresh
		// endpoint, never as a Bearer on chat-edge.
		return nil, fmt.Errorf("portal: token_use=%q not allowed", claims.TokenUse)
	}
	if claims.ExpiresAt != nil && claims.ExpiresAt.Before(time.Now().UTC()) {
		return nil, errors.New("portal: token expired")
	}

	userID, err := uuid.Parse(claims.Sub)
	if err != nil {
		return nil, fmt.Errorf("portal: bad sub uuid: %w", err)
	}
	role := claims.TenantRole
	if role == "" {
		role = "member"
	}
	return &Parsed{
		PortalUserID: userID,
		Email:        claims.Email,
		TenantID:     claims.TenantID,
		TenantRole:   role,
	}, nil
}
