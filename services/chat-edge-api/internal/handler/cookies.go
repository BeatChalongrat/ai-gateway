package handler

import (
	"net/http"
	"time"
)

// Cookie names + paths used across the auth surface.
//
// Paths:
//   - refresh_token  is scoped to /api/v1/auth so only the auth
//     endpoints receive it. Reduces the blast radius of an XSS that
//     can read response headers (HttpOnly still defeats JS reads,
//     but tightening Path defends against accidental leak via
//     misconfigured middleware logging the cookie header).
//   - csrf_token     is scoped to / so XHR from any route can copy
//     it into the X-CSRF-Token header. Not HttpOnly by design — JS
//     MUST read it for double-submit validation to work.
const (
	CookieRefreshToken = "refresh_token"
	CookieCSRFToken    = "csrf_token"

	PathRefreshToken = "/api/v1/auth"
	PathCSRFToken    = "/"
)

// SetRefreshCookie writes the HttpOnly refresh-token cookie. The
// secure flag is gated on Config.CookieSecure so local HTTP dev
// works without a TLS terminator in front.
func SetRefreshCookie(w http.ResponseWriter, raw string, ttl time.Duration, secure bool) {
	http.SetCookie(w, &http.Cookie{
		Name:     CookieRefreshToken,
		Value:    raw,
		Path:     PathRefreshToken,
		HttpOnly: true,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		Expires:  time.Now().UTC().Add(ttl),
		MaxAge:   int(ttl.Seconds()),
	})
}

// SetCSRFCookie writes the CSRF cookie. NOT HttpOnly — the browser
// JS reads it and forwards the value in the X-CSRF-Token header.
func SetCSRFCookie(w http.ResponseWriter, raw string, ttl time.Duration, secure bool) {
	http.SetCookie(w, &http.Cookie{
		Name:     CookieCSRFToken,
		Value:    raw,
		Path:     PathCSRFToken,
		HttpOnly: false,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		Expires:  time.Now().UTC().Add(ttl),
		MaxAge:   int(ttl.Seconds()),
	})
}

// ClearAuthCookies expires both cookies on logout. We set MaxAge=-1
// + an empty value so old browsers that don't honour Max-Age=0 still
// drop the cookie.
func ClearAuthCookies(w http.ResponseWriter, secure bool) {
	http.SetCookie(w, &http.Cookie{
		Name:     CookieRefreshToken,
		Value:    "",
		Path:     PathRefreshToken,
		HttpOnly: true,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		Expires:  time.Unix(0, 0),
		MaxAge:   -1,
	})
	http.SetCookie(w, &http.Cookie{
		Name:     CookieCSRFToken,
		Value:    "",
		Path:     PathCSRFToken,
		HttpOnly: false,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		Expires:  time.Unix(0, 0),
		MaxAge:   -1,
	})
}
