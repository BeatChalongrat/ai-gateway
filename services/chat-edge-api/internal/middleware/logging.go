// Request-level structured logging.
//
// CRITICAL PRIVACY CONTRACT: this middleware MUST NOT capture
// request or response bodies — they contain raw user prompts (pre-
// mask) and restored AI replies (post-restore), neither of which
// may land in logs. Only metadata is logged.
//
// Fields recorded per request:
//
//	method, path, status, latency_ms
//	request_id (X-Request-Id; minted if absent)
//	user_id, tenant_id, conversation_id, message_id (when present
//	in URL path or auth context — never read from body)
//
// Anything message-content related comes from the masked column in
// chat.messages_masked, never from this code path.
package middleware

import (
	"regexp"
	"context"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

// statusRecorder wraps ResponseWriter to capture the status code
// without buffering the body.
//
// Forwards “Flush()“ so SSE handlers downstream still see a
// usable “http.Flusher“ after the middleware wraps the writer.
// Without this, “w.(http.Flusher)“ in the SSE handler fails the
// type assertion and the streaming path errors with
// "streaming not supported by transport".
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// Flush forwards to the underlying writer when it implements
// http.Flusher (chi's default writer does). No-op otherwise so
// non-streaming responses are unaffected.
func (r *statusRecorder) Flush() {
	if f, ok := r.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Logging emits one structured log line per request via slog.
// The slog handler is configured at startup (JSON in prod, text in
// dev).
func Logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		// FIX119 — one id per turn. A client-supplied X-Request-Id is
		// honoured when it has a safe shape (the desktop sends its run id;
		// the page sends nothing), otherwise one is minted here — ONCE.
		// The handlers read it back from the context (RequestIDFrom) and
		// use it as the run id on every frame and as the X-Request-Id sent
		// upstream, so the http_request line below, the stream envelope,
		// the gateway's and the backend's logs and the row all carry the
		// same value. Before this the handler minted a second, different
		// uuid whenever the client sent none.
		rid := SanitizeRequestID(r.Header.Get("X-Request-Id"))
		supplied := rid != ""
		if rid == "" {
			rid = uuid.NewString()
		}
		w.Header().Set("X-Request-Id", rid)
		r = r.WithContext(WithRequestID(r.Context(), rid))
		if supplied {
			// FIX121 — an id the CLIENT chose names one request; the send
			// path treats it as that request's idempotency key. A minted
			// id is unique by construction and is never a key.
			r = r.WithContext(context.WithValue(r.Context(), requestIDSuppliedKey{}, true))
		}

		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)

		// Pull stable identifiers from the URL path. We never look
		// at the body — handlers do that separately on the chat-
		// stream path and own their own privacy contract.
		attrs := []any{
			slog.String("request_id", rid),
			slog.String("method", r.Method),
			slog.String("path", r.URL.Path),
			slog.Int("status", rec.status),
			slog.Int64("latency_ms", time.Since(start).Milliseconds()),
		}
		if caller, ok := CallerFrom(r.Context()); ok {
			attrs = append(attrs,
				slog.String("user_id", caller.UserID.String()),
				slog.String("tenant_id", caller.TenantID),
				slog.String("tenant_uuid", caller.TenantUUID.String()),
			)
		}
		if id := extractPathID(r.URL.Path, "conversations"); id != "" {
			attrs = append(attrs, slog.String("conversation_id", id))
		}
		if id := extractPathID(r.URL.Path, "messages"); id != "" {
			attrs = append(attrs, slog.String("message_id", id))
		}
		slog.Info("http_request", attrs...)
	})
}

// extractPathID pulls the UUID segment that follows “/<resource>/“
// in a REST path. Returns "" when the resource isn't present.
//
// We don't use a router-bound URL-param read because the logging
// middleware sits ABOVE the router; chi's URLParam helper isn't
// populated yet at that layer.
func extractPathID(path, resource string) string {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	for i := 0; i < len(parts)-1; i++ {
		if parts[i] == resource {
			return parts[i+1]
		}
	}
	return ""
}

type requestIDKey struct{}

// requestIDShape bounds a client-supplied id: it is logged on every line,
// echoed on a header, stored on the row and shown to a person, so it may
// not carry anything but a plain token. Anything else is replaced by a
// minted uuid, never rewritten.
var requestIDShape = regexp.MustCompile(`^[A-Za-z0-9._-]{8,64}$`)

// SanitizeRequestID returns the id when it has the allowed shape, "" otherwise.
func SanitizeRequestID(raw string) string {
	raw = strings.TrimSpace(raw)
	if requestIDShape.MatchString(raw) {
		return raw
	}
	return ""
}

// WithRequestID stores the request id on the context.
func WithRequestID(ctx context.Context, rid string) context.Context {
	return context.WithValue(ctx, requestIDKey{}, rid)
}

type requestIDSuppliedKey struct{}

// SuppliedRequestIDFrom returns the request id only when the CLIENT sent it
// (a well-formed X-Request-Id), "" when the middleware minted one. FIX121:
// this is the send path's idempotency key.
func SuppliedRequestIDFrom(ctx context.Context) string {
	if ok, _ := ctx.Value(requestIDSuppliedKey{}).(bool); !ok {
		return ""
	}
	return RequestIDFrom(ctx)
}

// RequestIDFrom returns the request id the Logging middleware minted or
// honoured for this request ("" outside the middleware, e.g. in a test
// that calls a handler directly).
func RequestIDFrom(ctx context.Context) string {
	v, _ := ctx.Value(requestIDKey{}).(string)
	return v
}
