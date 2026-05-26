// chat-edge-api entry point.
//
// Boot sequence:
//
//	1. Load config from env (Config.Load).
//	2. Connect to PostgreSQL (pgxpool).
//	3. Connect to Redis (go-redis v9).
//	4. Construct repositories, services, handlers, and the
//	   Gateway HTTP client.
//	5. Build the Chi router with middleware + routes.
//	6. Start the HTTP server on the configured port.
//	7. Wait for SIGINT/SIGTERM, then shutdown gracefully so the
//	   in-flight streams terminate cleanly before the process exits.
//
// Privacy contract reminders (also documented per-package):
//   - The logging middleware never reads request/response bodies.
//   - Every Gateway call carries a fresh service JWT (60s TTL).
//   - This service stores only masked content; raw prompts are
//     transient and never hit the DB.
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

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/router"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func main() {
	// Structured logging: JSON in prod, text in dev. We key off the
	// presence of a CHAT_EDGE_DEV env var so the deployment config
	// just sets that one knob.
	var logger *slog.Logger
	if os.Getenv("CHAT_EDGE_DEV") != "" {
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

	// PostgreSQL — bounded pool sized for the expected concurrent
	// SSE streams plus headroom for short-lived auth/CRUD calls.
	pgCtx, pgCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer pgCancel()
	pool, err := pgxpool.New(pgCtx, cfg.DatabaseURL)
	if err != nil {
		slog.Error("postgres connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err := pool.Ping(pgCtx); err != nil {
		slog.Error("postgres ping failed", "err", err)
		os.Exit(1)
	}
	slog.Info("postgres connected")

	// Redis — go-redis v9 parses the URL form (incl. DB number).
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
		slog.Error("redis ping failed", "err", err)
		os.Exit(1)
	}
	slog.Info("redis connected")

	// Wire repos → services → handlers.
	tenants := repository.NewTenantRepo(pool)
	users := repository.NewUserRepo(pool)
	refreshTokens := repository.NewRefreshTokenRepo(pool)
	invitations := repository.NewInvitationTokenRepo(pool)
	conversations := repository.NewConversationRepo(pool)
	messages := repository.NewMessageRepo(pool)
	feedbackRepo := repository.NewFeedbackRepo(pool)
	filesRepo := repository.NewFileRepo(pool)
	prefsRepo := repository.NewPreferencesRepo(pool)
	shareRepo := repository.NewShareRepo(pool)

	gateway := service.NewGatewayClient(
		cfg.GatewayAPIURL, cfg.GatewayAPIKey,
		cfg.ServiceJWTSecret, cfg.ServiceJWTTTL,
	)
	titles := service.NewTitleService(gateway)

	authSvc := service.NewAuthService(
		pool, tenants, users, refreshTokens, invitations,
		cfg.UserJWTSecret, cfg.CSRFSecret,
		cfg.AccessTokenExpiry, cfg.RefreshTokenExpiry,
	)
	conversationSvc := service.NewConversationService(conversations, cfg)
	messageSvc := service.NewMessageService(
		conversations, messages, gateway, titles, cfg, cfg.MaxContextMessages,
	)
	feedbackSvc := service.NewFeedbackService(feedbackRepo, messages)
	provisionSvc := service.NewProvisionService(pool)
	fileSvc := service.NewFileService(filesRepo, conversations, service.Knobs{
		StorageRoot:         cfg.FileStorageRoot,
		MaxFileSizeBytes:    cfg.MaxFileSizeBytes,
		MaxConvFileTotal:    cfg.MaxConvFileTotal,
		MaxFilesPerMessage:  cfg.MaxFilesPerMessage,
		ExtractPythonBinary: cfg.ExtractPythonBinary,
	})
	prefsSvc := service.NewPreferencesService(prefsRepo)
	shareSvc := service.NewShareService(shareRepo, conversations, messages, filesRepo)
	// Wire the file linkage into the message service so /messages
	// can persist file_ids → user message AND inject file content
	// into the gateway request's system prompt.
	messageSvc.SetFileLinker(fileSvc)
	messageSvc.SetPreferencesReader(prefsSvc)

	conversationHandler := handler.NewConversationHandler(conversationSvc)
	conversationHandler.SetExportSources(conversations, messages, filesRepo, cfg)

	messageHandler := handler.NewMessageHandler(messageSvc)
	messageHandler.SetFileRepo(filesRepo)

	handlers := router.Handlers{
		Auth:         handler.NewAuthHandler(authSvc, cfg.CSRFSecret, cfg.CookieSecure),
		Conversation: conversationHandler,
		Message:      messageHandler,
		Model:        handler.NewModelHandler(cfg),
		Feedback:     handler.NewFeedbackHandler(feedbackSvc),
		File:         handler.NewFileHandler(fileSvc),
		Preferences:  handler.NewPreferencesHandler(prefsRepo),
		Share:        handler.NewShareHandler(shareSvc),
		Admin:        handler.NewAdminHandler(pool),
	}

	// CORS origins — default to the web-chat Next.js dev origin so a
	// fresh checkout works without ceremony. Production sets the
	// CORS_ALLOW_ORIGINS env var.
	corsOrigins := os.Getenv("CORS_ALLOW_ORIGINS")
	if corsOrigins == "" {
		corsOrigins = "http://localhost:3002"
	}

	r := router.New(cfg, handlers, corsOrigins, rdb, provisionSvc)

	srv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
	}

	// Run + graceful shutdown.
	go func() {
		slog.Info("chat-edge-api listening", "port", cfg.Port)
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
	slog.Info("chat-edge-api stopped")
}
