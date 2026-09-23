// HTTP middleware that resolves the Portal access JWT into the
// request context.
//
// Phase 1.5 migration: chat-edge no longer mints its own user JWTs.
// The Portal (FastAPI “services/portal/auth_service.py“) is the
// sole identity authority. Every protected route reads a Bearer
// token signed with “settings.portal_jwt_secret“; the middleware
// verifies the signature, expiry, and “type=="portal"“ marker,
// then upserts a shadow row in chat-edge's identity tables so chat
// FKs hold. Subsequent requests in the same process hit a sync.Map
// cache and skip the upsert.
//
// FIX129 B2 — a token the Portal has revoked (sign-out) is refused
// with 401 even though its signature and expiry are still good; see
// auth.Revocations.
package middleware

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
)

// Caller is the resolved-user view downstream handlers see.
//
//   - UserID         — same UUID as the Portal's “sub“ claim.
//   - TenantID       — Portal tenant (or auto-derived personal tenant).
//   - Role           — kept for backward compatibility with the older
//     chat-edge code path; populated from
//     “tenant_role“ so handlers gating "owner-only"
//     actions can keep working.
//   - Email          — convenience for log lines + display defaults.
type Caller struct {
	UserID     uuid.UUID
	TenantID   string
	TenantUUID uuid.UUID
	Role       string
	Email      string
}

type ctxKey int

const callerKey ctxKey = 1

// CallerFrom pulls the resolved caller from a request context.
// Returns (zero, false) on a route that wasn't behind RequireAuth.
func CallerFrom(ctx context.Context) (Caller, bool) {
	c, ok := ctx.Value(callerKey).(Caller)
	return c, ok
}

// MustCaller is the convenience form: returns the Caller assuming
// the route is behind RequireAuth. On a route the auth middleware
// never ran on it returns the zero value — callers MUST only use
// this from handlers wired inside the protected subgroup. Returns
// the caller directly so handler code reads as
// “caller := middleware.MustCaller(r.Context())“ without the
// (caller, _) := destructure dance every site repeats.
func MustCaller(ctx context.Context) Caller {
	c, _ := ctx.Value(callerKey).(Caller)
	return c
}

// UserIDFrom is the most common single-field accessor — sugar for
// MustCaller(ctx).UserID. Returns uuid.Nil on a route the auth
// middleware never ran on; downstream queries that filter by
// user_id naturally return zero rows in that case (defence in
// depth — the route SHOULD have been auth-gated).
func UserIDFrom(ctx context.Context) uuid.UUID {
	return MustCaller(ctx).UserID
}

// UserProvisioner is the slice of service.ProvisionService the
// middleware needs: the shadow-row upsert. An interface so the
// middleware is testable without a database.
type UserProvisioner interface {
	EnsureUserExists(ctx context.Context, userID uuid.UUID, email, tenantRole, portalTenantID string) (uuid.UUID, error)
}

// RequireAuth produces middleware that:
//
//  1. Parses Portal JWT from “Authorization: Bearer <jwt>“ (or
//     the “portal_access_token“ cookie for browser navigations).
//  2. Refuses a token the Portal has revoked (FIX129 B2) — nil
//     “revocations“ means no check, as before.
//  3. Ensures a matching shadow row exists in identity.users /
//     identity.tenants (idempotent upsert).
//  4. Stuffs a Caller into the request context.
//
// “portalSecret“ MUST match FastAPI “portal_jwt_secret“.
// “provision“ is the ProvisionService instance from main.go.
func RequireAuth(
	portalSecret string,
	provision UserProvisioner,
	revocations auth.Revocations,
) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			raw, err := extractToken(r)
			if err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			parsed, err := auth.ParsePortalToken(portalSecret, raw)
			if err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}

			// FIX129 B2 — a token the Portal has revoked is refused even
			// though its signature and expiry are good. A store error
			// serves the request (availability first — the Portal makes
			// the same choice for its refresh blacklist) and is logged;
			// the token's own expiry bounds that window.
			if revocations != nil {
				revoked, rerr := revocations.IsRevoked(
					r.Context(), parsed.JTI, parsed.PortalUserID.String(), parsed.IssuedAt,
				)
				switch {
				case rerr != nil:
					slog.Warn("auth_revocation_check_failed",
						slog.String("error", rerr.Error()),
						slog.String("user_id", parsed.PortalUserID.String()),
						slog.String("path", r.URL.Path),
					)
				case revoked:
					http.Error(w, "unauthorized", http.StatusUnauthorized)
					return
				}
			}

			tenantUUID, err := provision.EnsureUserExists(
				r.Context(),
				parsed.PortalUserID,
				parsed.Email,
				parsed.TenantRole,
				parsed.TenantID,
			)
			if err != nil {
				// OPEN_DECISIONS #61: this 500 has been seen twice in
				// the first ~60-90s after a restart and has never been
				// diagnosable, because the cause died here. Every
				// return path in EnsureUserExists already wraps its
				// error with the stage that produced it (begin /
				// tenant upsert / user email reconcile / user upsert /
				// commit), so logging it is the whole difference
				// between "it happened again" and knowing which
				// statement failed. The response body is unchanged —
				// the caller still learns nothing it could act on.
				slog.Error("user_provision_failed",
					slog.String("error", err.Error()),
					slog.String("user_id", parsed.PortalUserID.String()),
					slog.String("tenant_id", parsed.TenantID),
					slog.String("path", r.URL.Path),
				)
				http.Error(w, "user provision failed", http.StatusInternalServerError)
				return
			}

			caller := Caller{
				UserID:     parsed.PortalUserID,
				TenantID:   parsed.TenantID,
				TenantUUID: tenantUUID,
				Role:       parsed.TenantRole,
				Email:      parsed.Email,
			}
			r = r.WithContext(context.WithValue(r.Context(), callerKey, caller))
			next.ServeHTTP(w, r)
		})
	}
}

// ExtractPortalToken exposes the raw portal JWT for handlers that
// proxy to the gateway's portal API on the caller's behalf (e.g. the
// usage endpoint forwards it as a Bearer header). Same cookie-first
// contract as RequireAuth.
func ExtractPortalToken(r *http.Request) (string, error) {
	return extractToken(r)
}

func extractToken(r *http.Request) (string, error) {
	// Cookie-first, matching the FastAPI ``get_portal_user`` contract.
	// Post-HttpOnly-migration the browser holds the access token in the
	// ``portal_access_token`` HttpOnly cookie (Path=/, SameSite=Lax),
	// shared across localhost ports; JS can't read it, so EventSource /
	// fetch rely on the cookie travelling automatically. SSE in
	// particular can't set an Authorization header, which is exactly why
	// cookie auth is the primary path here.
	if c, err := r.Cookie("portal_access_token"); err == nil && c.Value != "" {
		return c.Value, nil
	}
	// Fallback: ``Authorization: Bearer <jwt>`` for non-browser API
	// clients (mobile / server-to-server) that can't use cookies.
	if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer "), nil
	}
	return "", errors.New("no token")
}
