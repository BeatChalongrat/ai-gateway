// Package proxy is the transparent reverse proxy at the heart of the
// Phase-1 gateway. It forwards EVERY request — streaming and
// non-streaming alike — to the Python backend without parsing or
// modifying the body. It does not know or care whether a response is
// SSE; FlushInterval = -1 flushes each write immediately, so provider
// token deltas reach the client in real time exactly as they did when
// Python owned the socket.
package proxy

import (
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	chimw "github.com/go-chi/chi/v5/middleware"

	"github.com/BeatChalongrat/ai-gateway/gateway/internal/httpx"
)

// New builds a reverse proxy targeting the Python backend base URL.
func New(target string, log *slog.Logger) (*httputil.ReverseProxy, error) {
	u, err := url.Parse(target)
	if err != nil {
		return nil, err
	}

	rp := httputil.NewSingleHostReverseProxy(u)

	// -1 = flush after every write. Critical for SSE; negligible for
	// the single-write JSON responses on the non-streaming path.
	rp.FlushInterval = -1

	// No overall response timeout — streams can run for minutes. The
	// dial timeout still fails fast when Python is unreachable. Python's
	// own 20s request-timeout + 60s gunicorn worker timeout remain the
	// authoritative ceilings.
	rp.Transport = &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		MaxIdleConns:          100,
		MaxIdleConnsPerHost:   100,
		IdleConnTimeout:       90 * time.Second,
		ResponseHeaderTimeout: 0,
		ExpectContinueTimeout: 1 * time.Second,
	}

	orig := rp.Director
	rp.Director = func(req *http.Request) {
		orig(req) // sets scheme/host/path against the target
		// Propagate the request ID so Python's logs correlate with ours.
		if rid := chimw.GetReqID(req.Context()); rid != "" {
			req.Header.Set("X-Request-Id", rid)
		}
	}

	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		// Fires on dial failure / upstream reset BEFORE any byte reached
		// the client. Mid-stream failures (headers already sent) can't be
		// turned into a clean error here — ReverseProxy just closes the
		// body, mirroring Python's own mid-stream behaviour.
		log.Error("proxy upstream error", "err", err, "path", r.URL.Path)
		httpx.WriteError(w, http.StatusBadGateway,
			"upstream_unavailable", "Upstream service unavailable.")
	}

	return rp, nil
}
