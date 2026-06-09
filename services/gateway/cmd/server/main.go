// Gateway entry point — Phase 1 of the Python → Go strangler-fig
// migration.
//
// The gateway is a transparent reverse proxy that takes over the public
// :8080 listener. It does three things of its own — coarse API-key auth
// on /v1/*, Prometheus metrics, and request-ID propagation — and
// forwards everything else to the Python backend unchanged. Streaming
// is byte-for-byte passthrough (Go never parses SSE), so Python keeps
// owning provider dispatch, DLP, routing, and request logging exactly
// as before. See README.md for the contract and the deliberate
// non-goals.
//
// Boot sequence:
//  1. Load + parse config from env.
//  2. Connect to Postgres (small pool, auth lookups only) and Redis.
//  3. Build the reverse proxy + auth + metrics middleware chain.
//  4. Serve; drain in-flight streams on SIGINT/SIGTERM (30s).
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/gateway/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/gateway/internal/config"
	"github.com/BeatChalongrat/ai-gateway/gateway/internal/httpx"
	gwmw "github.com/BeatChalongrat/ai-gateway/gateway/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/gateway/internal/metrics"
	"github.com/BeatChalongrat/ai-gateway/gateway/internal/proxy"
)

const v1Prefix = "/v1/"

func main() {
	var logger *slog.Logger
	if os.Getenv("GATEWAY_DEV") != "" {
		logger = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	} else {
		logger = slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	}
	slog.SetDefault(logger)

	cfg, err := config.Load()
	if err != nil {
		slog.Error("config load failed", "err", err)
		os.Exit(1)
	}

	authTTL, err := time.ParseDuration(cfg.AuthCacheTTL)
	if err != nil {
		slog.Error("invalid GATEWAY_AUTH_CACHE_TTL", "value", cfg.AuthCacheTTL, "err", err)
		os.Exit(1)
	}

	// Postgres — small pool; the gateway only runs the api_keys lookup.
	pgCtx, pgCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer pgCancel()
	pgCfg, err := pgxpool.ParseConfig(cfg.DatabaseURL)
	if err != nil {
		slog.Error("invalid DATABASE_URL", "err", err)
		os.Exit(1)
	}
	pgCfg.MaxConns = cfg.PgMaxConns
	pool, err := pgxpool.NewWithConfig(pgCtx, pgCfg)
	if err != nil {
		slog.Error("postgres connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err := pool.Ping(pgCtx); err != nil {
		slog.Error("postgres ping failed", "err", err)
		os.Exit(1)
	}
	slog.Info("postgres connected", "max_conns", cfg.PgMaxConns)

	// Redis — caches resolved keys.
	redisOpts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		slog.Error("redis parse url failed", "err", err)
		os.Exit(1)
	}
	rdb := redis.NewClient(redisOpts)
	defer rdb.Close()
	redisCtx, redisCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer redisCancel()
	if err := rdb.Ping(redisCtx).Err(); err != nil {
		// Redis is a cache, not a hard dependency — auth falls back to
		// Postgres (and ultimately fails open). Warn, don't die.
		slog.Warn("redis ping failed — auth cache disabled until it recovers", "err", err)
	} else {
		slog.Info("redis connected")
	}

	authn := auth.New(pool, rdb, authTTL)

	rp, err := proxy.New(cfg.PythonServiceURL, logger)
	if err != nil {
		slog.Error("proxy init failed", "err", err)
		os.Exit(1)
	}

	r := chi.NewRouter()
	r.Use(chimw.RequestID)
	r.Use(chimw.RealIP)
	r.Use(chimw.Recoverer)
	r.Use(metrics.Middleware)

	// Gateway-owned endpoints, namespaced so they never shadow a Python
	// route. (/health, /ready, /metrics WITHOUT the prefix still proxy
	// to Python, preserving any existing probes against those paths.)
	r.Get("/__gw/health", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	r.Get("/__gw/ready", func(w http.ResponseWriter, req *http.Request) {
		ctx, cancel := context.WithTimeout(req.Context(), 2*time.Second)
		defer cancel()
		if err := pool.Ping(ctx); err != nil {
			httpx.WriteError(w, http.StatusServiceUnavailable, "not_ready", "postgres unreachable")
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ready"}`))
	})
	r.Handle("/__gw/metrics", promhttp.Handler())

	// Everything else: coarse auth on /v1/*, body guard on /v1/*, then
	// transparent proxy to Python for ALL paths.
	r.Group(func(r chi.Router) {
		r.Use(gwmw.BodyLimit(cfg.MaxBodyBytes, v1Prefix))
		r.Use(auth.Middleware(authn, v1Prefix, logger))
		r.Handle("/*", rp)
	})

	srv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		slog.Info("gateway listening", "port", cfg.Port, "upstream", cfg.PythonServiceURL)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("http server exited", "err", err)
			os.Exit(1)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	slog.Info("shutdown signal received")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Error("http server shutdown error", "err", err)
	}
	slog.Info("gateway stopped")
}
