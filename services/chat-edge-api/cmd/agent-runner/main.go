// agent-runner — Agent Market's execution plane (docs/agent-market/).
//
// Runs in its own container behind the compose profile "agent-market",
// with its own CPU/memory limits, its own Postgres pool (application_name
// agent-runner), its own model-gateway API key and its own queue lane. It
// never serves chat and chat never waits on it: a crash, an OOM or an
// exhausted budget here stops agent runs only. Leases make every run
// resumable after a restart from the first unfinished step.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket/runner"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/db"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

var version = "dev"

func main() {
	// Image build step: zip backend/agent_market deterministically.
	if len(os.Args) == 4 && os.Args[1] == "--build-engine-zip" {
		data, err := runner.BuildZip(os.Args[2])
		if err == nil {
			err = os.WriteFile(os.Args[3], data, 0o644)
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		e, err := runner.LoadEngine(os.Args[3], "")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		for _, p := range e.Packages {
			fmt.Printf("package %s %s checksum %s\n", p.Slug, p.Version, p.Checksum)
		}
		fmt.Printf("engine checksum %s (%d bytes)\n", e.Checksum, len(data))
		return
	}
	// Deploy step (AM2): keep a pre-AM2 runner's engine zip for the versions
	// released with it, so installs pinned to them keep running their own engine.
	if len(os.Args) == 3 && os.Args[1] == "store-engine" {
		if err := storeEngine(os.Args[2]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})).With("service", "agent-runner")
	slog.SetDefault(log)
	if err := run(log); err != nil {
		log.Error("agent-runner stopped", "err", err)
		os.Exit(1)
	}
}

func storeEngine(zipPath string) error {
	ctx := context.Background()
	e, err := runner.LoadEngine(zipPath, "")
	if err != nil {
		return err
	}
	fmt.Printf("engine checksum %s build %s\n", e.Checksum, e.Build)
	pool, err := openPool(ctx, 1)
	if err != nil {
		return err
	}
	defer pool.Close()
	files, err := openFiles()
	if err != nil {
		return err
	}
	lines, err := runner.StoreLegacyEngine(ctx, am.NewStore(pool), files, e)
	for _, l := range lines {
		fmt.Println(l)
	}
	return err
}

func openPool(ctx context.Context, maxConns int32) (*pgxpool.Pool, error) {
	dsn := os.Getenv("AGENT_RUNNER_DATABASE_URL")
	if dsn == "" {
		return nil, fmt.Errorf("AGENT_RUNNER_DATABASE_URL is required")
	}
	pc, err := db.PoolConfig(dsn, db.DefaultTimeouts())
	if err != nil {
		return nil, err
	}
	// Its own pool, sized separately: exhausting it can only stall agent runs.
	pc.MaxConns = maxConns
	pc.MinConns = 0
	pc.ConnConfig.RuntimeParams["application_name"] = "agent-runner"
	pool, err := pgxpool.NewWithConfig(ctx, pc)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: %w", err)
	}
	return pool, nil
}

func openFiles() (storage.Backend, error) {
	return storage.NewBackend(storage.Config{
		Backend: am.EnvString("STORAGE_BACKEND", "local"), LocalBasePath: am.EnvString("FILE_STORAGE_ROOT", "/app/data/uploads"),
		S3Bucket: os.Getenv("S3_BUCKET"), S3Region: os.Getenv("S3_REGION"), S3Endpoint: os.Getenv("S3_ENDPOINT"),
		S3AccessKey: os.Getenv("S3_ACCESS_KEY"), S3SecretKey: os.Getenv("S3_SECRET_KEY"),
		S3ForcePathStyle: am.EnvBool("S3_FORCE_PATH_STYLE"),
	})
}

func run(log *slog.Logger) error {
	if !am.EnvBool("AGENT_MARKET_ENABLED") {
		// Flag off means absent: no pool, no migration, no loop, no log noise.
		log.Info("AGENT_MARKET_ENABLED is off; agent-runner idle")
		select {}
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	pool, err := openPool(ctx, int32(am.EnvInt("AGENT_RUNNER_POOL_MAX_CONNS", 4)))
	if err != nil {
		return err
	}
	defer pool.Close()
	applied, err := am.Migrate(ctx, pool)
	if err != nil {
		return err
	}
	if len(applied) > 0 {
		log.Info("agent_market schema migrated", "applied", applied)
	}
	store := am.NewStore(pool)

	files, err := openFiles()
	if err != nil {
		return err
	}

	engine, err := runner.LoadEngine(os.Getenv("AGENT_MARKET_ENGINE_ZIP"), os.Getenv("AGENT_MARKET_ENGINE_SRC"))
	if err != nil {
		return err
	}
	image := am.EnvString("SANDBOX_IMAGE", "ai-gateway-sandbox:latest")
	sb := sandbox.New(sandbox.Config{Image: image, SeccompPath: os.Getenv("SANDBOX_SECCOMP_PATH"),
		WorkDir: am.EnvString("SANDBOX_WORKDIR", "/agent-sandbox-runs"), HostWorkDir: os.Getenv("SANDBOX_HOST_WORKDIR"),
		Runtime: os.Getenv("SANDBOX_RUNTIME")})
	sandboxReady := true
	if err := sb.Available(ctx); err != nil {
		sandboxReady = false
		log.Error("sandbox unavailable — installs refused and runs fail until it is fixed", "err", err)
	}

	selfHosted := map[string]bool{}
	for _, m := range strings.Split(am.EnvString("AGENT_MARKET_SELF_HOSTED_MODELS", "qwen3.8-27b-fp8"), ",") {
		if m = strings.TrimSpace(m); m != "" {
			selfHosted[m] = true
		}
	}
	model := &runner.ModelClient{
		BaseURL: am.EnvString("AGENT_RUNNER_GATEWAY_URL", "http://gateway:8080/v1"), APIKey: os.Getenv("AGENT_RUNNER_GATEWAY_API_KEY"),
		Primary: am.EnvString("AGENT_MARKET_MODEL_PRIMARY", "qwen3.8-27b-fp8"), Fallback: am.EnvString("AGENT_MARKET_MODEL_FALLBACK", "deepseek-v4-flash"),
		// Reads page images of scans and photos (AM2); used only when it is
		// in AGENT_MARKET_SELF_HOSTED_MODELS. Empty turns the vision step off.
		VisionModel: am.EnvString("AGENT_MARKET_VISION_MODEL", "qwen3.8-27b-fp8"),
		SelfHosted:  selfHosted, Timeout: time.Duration(am.EnvInt("AGENT_MARKET_MODEL_TIMEOUT_SECONDS", 150)) * time.Second,
		MaxTokens: am.EnvInt("AGENT_MARKET_MODEL_MAX_TOKENS", 6000), HTTP: &http.Client{},
		YieldURL: os.Getenv("AGENT_RUNNER_YIELD_URL"), YieldMaxWait: time.Duration(am.EnvInt("AGENT_RUNNER_YIELD_MAX_SECONDS", 300)) * time.Second,
		Concurrency: am.EnvInt("AGENT_RUNNER_MODEL_CONCURRENCY", 1),
		YieldActive: am.EnvInt("AGENT_RUNNER_YIELD_ACTIVE_TURNS", 1),
	}
	if !model.Available() {
		log.Warn("no model gateway key: narratives will be written by rules")
	}
	host, _ := os.Hostname()
	// PDF (AM2): the doc-converter service on the internal network only the
	// runner shares; unset means no PDF is offered.
	converter := &runner.Converter{BaseURL: os.Getenv("AGENT_MARKET_CONVERTER_URL"), HTTP: &http.Client{},
		Timeout: time.Duration(am.EnvInt("AGENT_MARKET_CONVERT_TIMEOUT_SECONDS", 90)) * time.Second}
	r := &runner.Runner{
		Store: store, Files: files, Engine: engine, Model: model, Log: log, Converter: converter,
		Exec: &runner.Executor{SB: sb, Engine: engine, Limits: sandbox.Limits{
			Wall:     time.Duration(am.EnvInt("AGENT_SANDBOX_WALL_SECONDS", 240)) * time.Second,
			MemoryMB: int64(am.EnvInt("AGENT_SANDBOX_MEMORY_MB", 1536)), PidsLimit: 256,
			CPUs: am.EnvString("AGENT_SANDBOX_CPUS", "1.0"), MaxOutputB: 8 << 20, FileSizeB: 256 << 20, ScratchMB: 512,
		}},
		ID: "agent-runner-" + host, Version: version, SandboxImage: image, SandboxReady: sandboxReady,
		SandboxCheck: sb.Available,
		Slots:        am.EnvInt("AGENT_RUNNER_SLOTS", 1), Lease: time.Duration(am.EnvInt("AGENT_RUNNER_LEASE_SECONDS", 45)) * time.Second,
		Poll:          time.Duration(am.EnvInt("AGENT_RUNNER_POLL_MS", 1000)) * time.Millisecond,
		CreditsPerUSD: float64(am.EnvInt("AGENT_MARKET_CREDITS_PER_USD", 3000000)),
		// Retention (AM2): unattached uploads, a failed run's scratch, a removed install.
		StagedTTL:               time.Duration(am.EnvInt("AGENT_MARKET_STAGED_UPLOAD_HOURS", 720)) * time.Hour,
		ScratchTTL:              time.Duration(am.EnvInt("AGENT_MARKET_FAILED_SCRATCH_DAYS", 7)) * 24 * time.Hour,
		RemovedTTL:              time.Duration(am.EnvInt("AGENT_MARKET_REMOVED_INSTALL_DAYS", 30)) * 24 * time.Hour,
		SandboxCreditsPerSecond: float64(am.EnvInt("AGENT_MARKET_SANDBOX_CREDITS_PER_SECOND", 100)),
		StorageBackend:          am.EnvString("STORAGE_BACKEND", "local"),
	}

	// The sweep that removes sandbox containers a killed runner left behind
	// (AM1c): at start, then every AGENT_RUNNER_SWEEP_SECONDS.
	r.Reaper = runner.NewReaper(sb, r.Exec.Limits.Wall, log)
	r.Reaper.Every = time.Duration(am.EnvInt("AGENT_RUNNER_SWEEP_SECONDS", 60)) * time.Second
	r.Reaper.Grace = time.Duration(am.EnvInt("AGENT_RUNNER_SWEEP_GRACE_SECONDS", 60)) * time.Second

	// Health + metrics on its own port (am_ prefix; never the core's).
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, req *http.Request) {
		cctx, cancel := context.WithTimeout(req.Context(), 2*time.Second)
		defer cancel()
		if err := pool.Ping(cctx); err != nil {
			http.Error(w, "db", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "sandbox": r.Sandbox(), "model": model.Available(),
			"engine": engine.Checksum[:12], "version": version})
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, req *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprint(w, runner.RenderMetrics(runner.CollectMetrics(req.Context(), pool, r.Sandbox())))
	})
	srv := &http.Server{Addr: am.EnvString("AGENT_RUNNER_ADDR", ":8095"), Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Error("health server", "err", err)
		}
	}()
	if model.VisionModel != "" && !model.SelfHosted[model.VisionModel] {
		log.Warn("vision model is not in the self-hosted list: scans and photos will not be sent to it", "model", model.VisionModel)
	}
	log.Info("agent-runner started", "slots", r.Slots, "packages", len(engine.Packages), "engine", engine.Checksum[:12],
		"sandbox", sandboxReady, "model", model.Primary+"|"+model.Fallback, "vision", model.VisionModel)
	err = r.Start(ctx)
	sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(sctx)
	return err
}
