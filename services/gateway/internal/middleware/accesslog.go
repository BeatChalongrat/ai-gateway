package middleware

// FIX119 — the gateway front logs one line per request with the request
// id it propagates.
//
// It already forwarded X-Request-Id upstream (proxy.go) so the Python
// logs could be correlated with "ours" — but it wrote no line of its own,
// so a grep for a turn's id found chat-edge and the backend and skipped
// this layer. One structured line here closes the gap: method, path,
// status, duration, and the id.

import (
	"log/slog"
	"net/http"
	"time"

	chimw "github.com/go-chi/chi/v5/middleware"
)

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

// Flush keeps SSE streaming working through the recorder.
func (w *statusWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// AccessLog logs one line per request, carrying the chi request id (which
// is the caller's X-Request-Id when it sent one).
func AccessLog(logger *slog.Logger) func(http.Handler) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(sw, r)
			logger.Info("http_request",
				"request_id", chimw.GetReqID(r.Context()),
				"method", r.Method,
				"path", r.URL.Path,
				"status", sw.status,
				"latency_ms", time.Since(start).Milliseconds(),
			)
		})
	}
}
