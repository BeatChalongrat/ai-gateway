// Package metrics exposes Prometheus instrumentation for the gateway.
//
// Label cardinality is kept deliberately low: paths are bucketed into a
// handful of groups (never raw paths or tenant IDs) so the series count
// stays bounded no matter how many distinct routes or tenants exist.
package metrics

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	requestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "gateway_requests_total",
		Help: "Total HTTP requests handled by the gateway.",
	}, []string{"method", "path_group", "status"})

	requestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "gateway_request_duration_seconds",
		Help:    "Request latency through the gateway (includes upstream time).",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120},
	}, []string{"method", "path_group"})

	inFlight = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "gateway_in_flight_requests",
		Help: "Requests currently being served (proxied streams included).",
	})
)

// pathGroup buckets a path to bound label cardinality.
func pathGroup(p string) string {
	switch {
	case strings.HasPrefix(p, "/v1/"):
		return "/v1"
	case strings.HasPrefix(p, "/portal/"):
		return "/portal"
	case strings.HasPrefix(p, "/admin/"):
		return "/admin"
	case strings.HasPrefix(p, "/auth/"):
		return "/auth"
	case strings.HasPrefix(p, "/__gw/"):
		return "/__gw"
	default:
		return "other"
	}
}

// Middleware records count, latency, and in-flight gauge per request.
// It wraps the ResponseWriter with chi's wrapper, which preserves
// http.Flusher so SSE streaming through the proxy still flushes.
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		grp := pathGroup(r.URL.Path)

		inFlight.Inc()
		defer inFlight.Dec()

		ww := chimw.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)

		status := ww.Status()
		if status == 0 {
			status = http.StatusOK
		}
		requestsTotal.WithLabelValues(r.Method, grp, strconv.Itoa(status)).Inc()
		requestDuration.WithLabelValues(r.Method, grp).Observe(time.Since(start).Seconds())
	})
}
