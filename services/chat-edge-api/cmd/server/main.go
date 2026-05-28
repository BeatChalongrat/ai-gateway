// chat-edge-api entry point.
//
// Boot sequence:
//
//  1. Load config from env (Config.Load).
//  2. Connect to PostgreSQL (pgxpool).
//  3. Connect to Redis (go-redis v9).
//  4. Construct repositories, services, handlers, and the
//     Gateway HTTP client.
//  5. Build the Chi router with middleware + routes.
//  6. Start the HTTP server on the configured port.
//  7. Wait for SIGINT/SIGTERM, then shutdown gracefully so the
//     in-flight streams terminate cleanly before the process exits.
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

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/router"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
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
	knowledgeRepo := repository.NewKnowledgeRepo(pool)
	memoriesRepo := repository.NewMemoryRepo(pool)
	statesRepo := repository.NewConversationStateRepo(pool)

	gateway := service.NewGatewayClient(
		cfg.GatewayAPIURL, cfg.GatewayAPIKey,
		cfg.ServiceJWTSecret, cfg.ServiceJWTTTL,
	)
	titles := service.NewTitleService(gateway)
	titles.SetRepos(conversations, messages)

	authSvc := service.NewAuthService(
		pool, tenants, users, refreshTokens, invitations,
		cfg.UserJWTSecret, cfg.CSRFSecret,
		cfg.AccessTokenExpiry, cfg.RefreshTokenExpiry,
	)
	conversationSvc := service.NewConversationService(conversations, cfg)
	messageSvc := service.NewMessageService(
		conversations, messages, gateway, titles, cfg, cfg.MaxContextMessages,
	)
	feedbackSvc := service.NewFeedbackService(feedbackRepo, messages, conversations)
	provisionSvc := service.NewProvisionService(pool)

	// Blob storage — backed by either the local filesystem (default)
	// or any S3-compatible service. Both file uploads + KB documents
	// reuse the same backend instance so a single deploy switch flips
	// the whole product to S3.
	storageBackend, err := storage.NewBackend(storage.Config{
		Backend:          cfg.StorageBackend,
		LocalBasePath:    cfg.FileStorageRoot,
		S3Bucket:         cfg.S3Bucket,
		S3Region:         cfg.S3Region,
		S3Endpoint:       cfg.S3Endpoint,
		S3AccessKey:      cfg.S3AccessKey,
		S3SecretKey:      cfg.S3SecretKey,
		S3ForcePathStyle: cfg.S3ForcePathStyle,
	})
	if err != nil {
		slog.Error("storage init failed", "err", err)
		os.Exit(1)
	}
	slog.Info("storage backend ready", "backend", cfg.StorageBackend)

	fileSvc := service.NewFileService(filesRepo, conversations, storageBackend, service.Knobs{
		StorageRoot:         cfg.FileStorageRoot,
		MaxFileSizeBytes:    cfg.MaxFileSizeBytes,
		MaxConvFileTotal:    cfg.MaxConvFileTotal,
		MaxFilesPerMessage:  cfg.MaxFilesPerMessage,
		ExtractPythonBinary: cfg.ExtractPythonBinary,
		PresignedURLExpiry:  cfg.PresignedURLExpiry,
	})
	prefsSvc := service.NewPreferencesService(prefsRepo)
	shareSvc := service.NewShareService(shareRepo, conversations, messages, filesRepo)
	// S1 — Personal Memory Core. Service is wired alongside prefs +
	// share so the message service can pick it up via SetMemoryReader
	// below (system-prompt injection + AI tool execution).
	memorySvc := service.NewMemoryService(memoriesRepo)
	// S3 — Structured Conversation State. Backs the
	// <conversation_state> system-prompt block, the
	// update_conversation_state AI tool, and the GET
	// /conversations/{id}/state read endpoint. Uses the gateway
	// client for its extraction calls (low-temperature JSON).
	stateSvc := service.NewConversationStateService(statesRepo, messages, conversations, gateway)

	// Phase 4 — Excel Smart Workspace (Session 1) +
	// Word/PDF Smart Reports (Session 2). Both services wired
	// before the file service's auto-inspect hook so an upload
	// that lands the instant the server starts up still gets
	// inspected.
	excelSvc := service.NewExcelService(filesRepo, storageBackend, cfg.ExtractPythonBinary, "scripts")
	docSvc := service.NewDocService(filesRepo, conversations, storageBackend, cfg.ExtractPythonBinary, "scripts")
	// Session 5 — workspace (rollback + compare). Tenant + user
	// scoping happens inside the service via the conversations repo
	// (the conv ownership check gates Rollback/Compare/ListGenerated);
	// the handler is a thin HTTP shim per the existing handler pattern.
	workspaceSvc := service.NewWorkspaceService(filesRepo, conversations, storageBackend, cfg.ExtractPythonBinary, "scripts")
	fileSvc.SetExcelService(excelSvc)
	fileSvc.SetDocService(docSvc)
	messageSvc.SetExcelLookup(filesRepo)

	// Wire the file linkage into the message service so /messages
	// can persist file_ids → user message AND inject file content
	// into the gateway request's system prompt.
	messageSvc.SetFileLinker(fileSvc)
	messageSvc.SetPreferencesReader(prefsSvc)
	// S1 — wire the memory layer. Enables the <saved_user_memory>
	// system-prompt block + the save_memory / list_memories AI tools
	// on every conversation where memory_enabled = true.
	messageSvc.SetMemoryService(memorySvc)
	// S3 — wire the conversation-state layer. Adds the
	// <conversation_state> system-prompt block + the
	// update_conversation_state tool + the auto-extraction trigger
	// in finalizeConversation. Must follow SetMemoryService so the
	// ToolExecutor (created inside SetMemoryService) picks up the
	// state service via its internal back-wire.
	messageSvc.SetConversationStateService(stateSvc)
	// S4 — token-budgeted context compiler. Sits between
	// prepareSend (which loads history) and the gateway call,
	// trimming the history slice to fit the model's working window
	// and synchronously refreshing the state block before
	// compaction so older messages keep their fingerprint in
	// <conversation_state>.
	messageSvc.SetContextCompiler(service.NewContextCompiler(stateSvc))
	// S6 — cross-conversation search. Backs the AI's
	// search_past_conversations tool + the GET /api/v1/search
	// endpoint. Reuses the same memory + conversation + message
	// repos; the new tsvector columns from migration 016 power
	// the queries.
	searchSvc := service.NewSearchService(messages, conversations, memoriesRepo)
	messageSvc.SetSearchService(searchSvc)

	// Knowledge base (RAG) pipeline.
	//
	// EmbeddingService reads OPENAI_API_KEY at construction time —
	// when unset, the upload/index endpoints still respond but the
	// indexer marks each document "failed" with a clear "configure
	// OpenAI" error_message. We wire RAGService into messageSvc
	// unconditionally so a future env-var flip enables retrieval
	// without a service restart.
	embeddingSvc := service.NewEmbeddingService()
	knowledgeSvc := service.NewKnowledgeService(
		knowledgeRepo, embeddingSvc, fileSvc, storageBackend,
		service.KnowledgeServiceKnobs{
			// Reuse the file storage env knob's directory as a parent
			// — KB blobs land under a sibling ``knowledge/`` tree
			// (only meaningful for the local backend; the S3 backend
			// ignores StorageRoot in favour of the key prefix).
			StorageRoot:      cfg.FileStorageRoot + "/knowledge",
			MaxFileSizeBytes: 50 * 1024 * 1024,
			EmbedBatchSize:   20,
		},
	)
	ragSvc := service.NewRAGService(knowledgeRepo, embeddingSvc)
	messageSvc.SetRAGReader(ragSvc)

	conversationHandler := handler.NewConversationHandler(conversationSvc)
	conversationHandler.SetExportSources(conversations, messages, filesRepo, cfg)
	conversationHandler.SetKnowledgeService(knowledgeSvc)
	conversationHandler.SetTitleService(titles)
	conversationHandler.SetConversationStateService(stateSvc)
	conversationHandler.SetMessageService(messageSvc)

	messageHandler := handler.NewMessageHandler(messageSvc)
	messageHandler.SetFileRepo(filesRepo)

	adminHandler := handler.NewAdminHandler(pool)
	// Wire the KB mutators so the admin endpoints can reindex / delete
	// without duplicating the cascade + blob-cleanup logic the
	// tenant-facing service already implements. Closures swallow the
	// concrete return type from KnowledgeService — the admin handler
	// only cares about error.
	adminHandler.SetKBSink(handler.AdminKBSink{
		ReindexDocument: func(ctx context.Context, tenantID, kbID, docID uuid.UUID) error {
			_, err := knowledgeSvc.ReindexDocument(ctx, tenantID, kbID, docID)
			return err
		},
		DeleteKB: knowledgeSvc.DeleteKB,
	})

	handlers := router.Handlers{
		Auth:         handler.NewAuthHandler(authSvc, cfg.CSRFSecret, cfg.CookieSecure),
		Conversation: conversationHandler,
		Message:      messageHandler,
		Model:        handler.NewModelHandler(cfg),
		Feedback:     handler.NewFeedbackHandler(feedbackSvc),
		File:         handler.NewFileHandler(fileSvc),
		Preferences:  handler.NewPreferencesHandler(prefsRepo),
		Share:        handler.NewShareHandler(shareSvc),
		Admin:        adminHandler,
		Knowledge:    handler.NewKnowledgeHandler(knowledgeSvc, ragSvc),
		Voice:        handler.NewVoiceHandler(),
		Excel:        handler.NewExcelHandler(excelSvc, filesRepo),
		Document:     handler.NewDocumentHandler(filesRepo, excelSvc, docSvc),
		Workspace:    handler.NewWorkspaceHandler(filesRepo, workspaceSvc),
		Memory:       handler.NewMemoryHandler(memorySvc),
		Search:       handler.NewSearchHandler(searchSvc),
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
