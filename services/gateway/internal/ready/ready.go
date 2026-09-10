// Package ready decides what the gateway's own readiness endpoint,
// /__gw/ready, reports.
//
// FIX103 Part B. It used to ping Postgres and nothing else, so the public
// :8080 listener said "ready" while the Python backend — which owns
// provider dispatch and DLP, and to which every /v1 request is proxied —
// could not serve a single request. Readiness now requires both.
//
// What it deliberately does NOT require is the Rust DLP sidecar. FIX103
// Part A proved that when the sidecar is stopped or wedged the backend
// masks on its Python registry and the provider receives masked text, so
// a sidecar outage is a degradation, not an inability to serve. The
// backend's /ready says so in its "degraded" list, and this endpoint
// passes that list through instead of turning it into a 503.
package ready

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/gateway/internal/httpx"
)

// Pinger is the part of *pgxpool.Pool this needs.
type Pinger interface {
	Ping(ctx context.Context) error
}

// Checker serves /__gw/ready.
type Checker struct {
	DB Pinger
	// BackendURL is PYTHON_SERVICE_URL; "/ready" is appended.
	BackendURL string
	Client     *http.Client
}

// Timeout bounds the whole probe. The backend's own readiness runs under a
// 3 s budget; a probe that can hang is worse than none.
const Timeout = 5 * time.Second

type backendReady struct {
	Status   string   `json:"status"`
	Failures []string `json:"failures"`
	Degraded []string `json:"degraded"`
}

func (c Checker) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), Timeout)
	defer cancel()

	if err := c.DB.Ping(ctx); err != nil {
		httpx.WriteError(w, http.StatusServiceUnavailable, "not_ready", "postgres unreachable")
		return
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(c.BackendURL, "/")+"/ready", nil)
	if err != nil {
		httpx.WriteError(w, http.StatusServiceUnavailable, "not_ready", "backend readiness URL is invalid")
		return
	}
	resp, err := c.Client.Do(req)
	if err != nil {
		httpx.WriteError(w, http.StatusServiceUnavailable, "not_ready", "backend unreachable")
		return
	}
	defer resp.Body.Close()

	var b backendReady
	_ = json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(&b)
	if resp.StatusCode != http.StatusOK {
		msg := "backend not ready"
		if len(b.Failures) > 0 {
			msg += ": " + strings.Join(b.Failures, ", ")
		}
		httpx.WriteError(w, http.StatusServiceUnavailable, "not_ready", msg)
		return
	}

	degraded := b.Degraded
	if degraded == nil {
		degraded = []string{}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]any{"status": "ready", "degraded": degraded})
}
