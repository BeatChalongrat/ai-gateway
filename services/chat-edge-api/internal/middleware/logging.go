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
		rid := r.Header.Get("X-Request-Id")
		if rid == "" {
			rid = uuid.NewString()
		}
		w.Header().Set("X-Request-Id", rid)

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
