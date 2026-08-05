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
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
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
	// Fail closed on a production deploy still carrying dev-default
	// secrets / non-Secure cookies (parity with the Python backend).
	if err := cfg.Validate(); err != nil {
		slog.Error("config validation failed", "err", err)
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
	folders := repository.NewFolderRepo(pool)
	projects := repository.NewProjectRepo(pool)
	messages := repository.NewMessageRepo(pool)
	// H4 — only read canonical columns when the feature is enabled (and
	// migration 024 is therefore applied), so a pre-migration binary with
	// the flag off never references the new columns.
	messages.SetCanonicalHistory(cfg.DLPCanonicalHistoryEnabled)
	// H4 fail-closed: if canonical history is enabled but migration 024 is
	// not applied, refuse to start (never silently fall back to display).
	if cfg.DLPCanonicalHistoryEnabled {
		chkCtx, chkCancel := context.WithTimeout(context.Background(), 5*time.Second)
		var col *string
		_ = pool.QueryRow(chkCtx,
			"SELECT column_name FROM information_schema.columns WHERE table_schema='chat' AND table_name='messages_masked' AND column_name='canonical_status'",
		).Scan(&col)
		chkCancel()
		if col == nil {
			slog.Error("canonical history enabled but migration 024 not applied; refusing to start — apply migrations first")
			os.Exit(1)
		}
		slog.Info("dlp canonical history enabled; migration 024 verified")
	}
	// Phase 4A.2.4 — display lifecycle. Reads/writes the display_state column
	// only when enabled (H4 deploy-order). Display lifecycle requires canonical
	// history (it needs the canonical artifact to persist a provider-safe
	// representation) — refuse to start on a misconfiguration.
	if cfg.DLPDisplayLifecycleEnabled && !cfg.DLPCanonicalHistoryEnabled {
		slog.Error("display lifecycle enabled without canonical history; refusing to start — enable DLP_CANONICAL_HISTORY_ENABLED")
		os.Exit(1)
	}
	messages.SetDisplayLifecycle(cfg.DLPDisplayLifecycleEnabled)
	if cfg.DLPDisplayLifecycleEnabled {
		chkCtx, chkCancel := context.WithTimeout(context.Background(), 5*time.Second)
		var col *string
		_ = pool.QueryRow(chkCtx,
			"SELECT column_name FROM information_schema.columns WHERE table_schema='chat' AND table_name='messages_masked' AND column_name='display_state'",
		).Scan(&col)
		chkCancel()
		if col == nil {
			slog.Error("display lifecycle enabled but migration 025 not applied; refusing to start — apply migrations first")
			os.Exit(1)
		}
		slog.Info("dlp display lifecycle enabled; migration 025 verified")
	}
	feedbackRepo := repository.NewFeedbackRepo(pool)
	filesRepo := repository.NewFileRepo(pool)
	prefsRepo := repository.NewPreferencesRepo(pool)
	shareRepo := repository.NewShareRepo(pool)
	knowledgeRepo := repository.NewKnowledgeRepo(pool)
	memoriesRepo := repository.NewMemoryRepo(pool)
	statesRepo := repository.NewConversationStateRepo(pool)
	sourcesRepo := repository.NewResponseContextSourceRepo(pool)
	tenantSettingsRepo := repository.NewTenantMemorySettingsRepo(pool)
	exportsRepo := repository.NewDataExportRepo(pool)

	gateway := service.NewGatewayClient(
		cfg.GatewayAPIURL, cfg.GatewayAPIKey,
		cfg.ServiceJWTSecret, cfg.ServiceJWTTTL,
		cfg.InternalServiceSecret,
	)
	titles := service.NewTitleService(gateway)
	titles.SetRepos(conversations, messages)

	authSvc := service.NewAuthService(
		pool, tenants, users, refreshTokens, invitations,
		cfg.UserJWTSecret, cfg.CSRFSecret,
		cfg.AccessTokenExpiry, cfg.RefreshTokenExpiry,
	)
	conversationSvc := service.NewConversationService(conversations, cfg)
	folderSvc := service.NewFolderService(folders, conversations)
	projectSvc := service.NewProjectService(projects, conversations)
	messageSvc := service.NewMessageService(
		conversations, messages, gateway, titles, cfg, cfg.MaxContextMessages,
	)
	messageSvc.SetImageGenerationTimeout(time.Duration(cfg.ChatEdgeImageTimeoutSeconds) * time.Second)
	// Phase 5 — stream resume uses an IN-PROCESS broker: reattach works
	// only against the instance that owns the generation. Correct for the
	// current single-instance deployment (compose pins one container); a
	// multi-replica rollout needs a shared broker (e.g. Redis pub/sub).
	slog.Info("stream-resume broker: in-process (single-instance only; cross-instance reattach falls back to persisted row)")
	// Phase 4A.2.2 — DLP canonical history rollout (default off/shadow).
	messageSvc.SetCanonicalHistory(cfg.DLPCanonicalHistoryEnabled, cfg.DLPCanonicalHistoryShadow)
	// Phase 4A.2.4 — display lifecycle rollout (default off/shadow). The
	// restore-on-read bridge to Vault V2 is not wired here yet (no cross-
	// service Vault V2 client), so the restorer stays nil → owner reload uses
	// the tokenized/redacted fallback until a bridge is added.
	messageSvc.SetDisplayLifecycle(
		cfg.DLPDisplayLifecycleEnabled, cfg.DLPDisplayLifecycleShadow, cfg.DLPDisplayLifecycleStrictLegacy,
	)
	// Phase 4A.2.5 (B2) — wire the Go→Vault V2 restore-on-read bridge ONLY when
	// display lifecycle AND Vault V2 are both enabled. Otherwise the restorer
	// stays nil and owner reload uses the tokenized/redacted fallback.
	if cfg.DLPDisplayLifecycleEnabled && cfg.DLPVaultV2Enabled {
		messageSvc.SetDisplayRestorer(service.NewVaultV2DisplayRestorer(
			cfg.GatewayAPIURL, cfg.GatewayAPIKey, cfg.InternalServiceSecret, cfg.DLPVaultV2Enabled,
		))
		slog.Info("dlp vault v2 restore bridge wired (display lifecycle + vault v2 enabled)")
	}
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
	// S9 — wire the tenant settings repo onto the memory service
	// so CreateMemory enforces the per-tenant max-memories cap.
	memorySvc.SetTenantSettings(tenantSettingsRepo)
	// S9 — Export + Purge + Retention. Storage root reuses the
	// existing FileStorageRoot config so exports land in the
	// same bind-mount as uploads (./data by default).
	exportSvc := service.NewExportService(memoriesRepo, exportsRepo, tenantSettingsRepo, cfg.FileStorageRoot)
	purgeSvc := service.NewPurgeService(memoriesRepo, statesRepo, sourcesRepo, exportsRepo, tenantSettingsRepo, cfg.FileStorageRoot)
	retentionEnforcer := service.NewRetentionEnforcer(memoriesRepo, tenantSettingsRepo)
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

	// Phase 13 Part A — migrate the plan-executor + doc generators into
	// the hardened sandbox when enabled. Model-authored plans then run
	// with no network, non-root, seccomp, read-only rootfs and a stripped
	// env — never again in this secret-bearing process. Fail LOUD if
	// enabled-but-unavailable so a misconfig can't silently fall back to
	// the in-process path in prod.
	var sbx *sandbox.Runner
	if cfg.SandboxEnabled {
		sbx = sandbox.New(sandbox.Config{
			Image:       cfg.SandboxImage,
			SeccompPath: cfg.SandboxSeccompPath,
			WorkDir:     cfg.SandboxWorkDir,
			HostWorkDir: cfg.SandboxHostWorkDir,
		})
		if err := sbx.Available(context.Background()); err != nil {
			slog.Error("sandbox enabled but unavailable — refusing to run plan-exec in-process", "err", err)
			os.Exit(1)
		}
		excelSvc.SetSandbox(sbx)
		docSvc.SetSandbox(sbx)
		fileSvc.SetSandbox(sbx)
		workspaceSvc.SetSandbox(sbx)
		slog.Info("plan-exec sandbox active", "image", cfg.SandboxImage)
	} else {
		slog.Warn("SANDBOX_ENABLED=0: document, extraction, compare, and code execution fail closed")
	}

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
	// Phase 4A.2.4.1 — search/index + the LLM search tool must never surface a
	// legacy raw content_masked; gate the non-raw representation on the flag.
	searchSvc.SetDisplayLifecycle(cfg.DLPDisplayLifecycleEnabled)
	messageSvc.SetSearchService(searchSvc)
	// Phase B — image generation tool. Wires the gateway image endpoint
	// + the file service onto the ToolExecutor so generate_image can
	// produce an image and attach it to the assistant message. Same
	// ordering rule as SetSearchService (executor created in
	// SetMemoryService). gateway + fileSvc satisfy the executor's
	// imageGenerator / generatedImageSaver interfaces.
	messageSvc.SetImageGeneration(gateway, fileSvc, cfg.ImageGenModel)
	// Sprint 2A — wire the generate_video tool: the gateway client (async
	// submit/poll/fetch against the backend job API) + the file service
	// (persists the fetched MP4 as a ConversationFile). gateway + fileSvc
	// satisfy the executor's videoGenerator / generatedVideoSaver interfaces.
	messageSvc.SetVideoGeneration(gateway, fileSvc, cfg.VideoGenModel)
	// Web search — wire the gateway client (web_search calls + the
	// per-tenant search-config gate) and the message service (persists
	// citations on the assistant message). The tool is offered only when
	// the gateway reports the tenant enabled search + has a provider.
	messageSvc.SetWebSearch(gateway, messageSvc)
	messageSvc.SetSearchConfigChecker(gateway)
	// Phase 3 — web_fetch. SSRF-guarded direct page reader; no provider
	// dependency, so no per-tenant gate. Shares the citation saver with
	// web_search so fetched pages render as the same source chips.
	messageSvc.SetWebFetch(service.NewWebFetcher(), messageSvc)
	// Phase 13 Part B — run_python code interpreter. Wired ONLY when the
	// hardened sandbox is available: run_python must never execute
	// model-authored code in this secret-bearing process. The interpreter
	// runs ModeCode in the same locked-down container as plan-exec; the
	// file service resolves (ownership-checked) file inputs + persists
	// produced artifacts as ConversationFiles on the assistant message.
	var codeInterp *service.CodeInterpreter
	if sbx != nil {
		codeInterp = service.NewCodeInterpreter(sbx)
		messageSvc.SetCodeInterpreter(codeInterp, fileSvc, fileSvc)
		slog.Info("run_python code interpreter active")
	} else {
		slog.Warn("SANDBOX_ENABLED=0 — run_python code interpreter DISABLED")
	}
	// S7 — wire the source-attribution repo so per-turn sources
	// (memory + state + compaction + search) get persisted to
	// chat.response_context_sources after each assistant turn
	// lands. The message-list handler hydrates them inline via
	// the same repo.
	messageSvc.SetResponseSourceRepo(sourcesRepo)
	// S8 — wire the background preference-pattern learner. Fires
	// from finalizeConversation once per qualifying conversation
	// (20+ messages, memory enabled, no prior extraction). Pending
	// suggestions surface in Settings → Memory; auto-promotion
	// kicks in after the same pattern is observed in 3+
	// conversations.
	prefExtractor := service.NewPreferenceExtractor(memorySvc, memoriesRepo, messages, conversations, gateway)
	messageSvc.SetPreferenceExtractor(prefExtractor)

	// Knowledge base (RAG) pipeline.
	//
	// EmbeddingService reads OPENAI_API_KEY at construction time —
	// when unset, the upload/index endpoints still respond but the
	// indexer marks each document "failed" with a clear "configure
	// OpenAI" error_message. We wire RAGService into messageSvc
	// unconditionally so a future env-var flip enables retrieval
	// without a service restart.
	embeddingSvc := service.NewEmbeddingService(gateway)
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
	// Phase 4 — project context (shared instructions + KB) inherited by
	// member conversations at prompt-compile time.
	messageSvc.SetProjectReader(projects)

	conversationHandler := handler.NewConversationHandler(conversationSvc)
	conversationHandler.SetExportSources(conversations, messages, filesRepo, cfg)
	conversationHandler.SetKnowledgeService(knowledgeSvc)

	folderHandler := handler.NewFolderHandler(folderSvc)
	projectHandler := handler.NewProjectHandler(projectSvc)
	conversationHandler.SetTitleService(titles)
	conversationHandler.SetConversationStateService(stateSvc)
	conversationHandler.SetMessageService(messageSvc)

	messageHandler := handler.NewMessageHandler(messageSvc)
	messageHandler.SetFileRepo(filesRepo)
	messageHandler.SetSourceRepo(sourcesRepo)

	adminHandler := handler.NewAdminHandler(pool)
	// Phase 4A.2.4.1 — admin default viewer redacts raw-sensitive legacy rows.
	adminHandler.SetDisplayLifecycle(cfg.DLPDisplayLifecycleEnabled)
	// S9 — wire the aggregate stats lookup as a closure so the
	// admin handler doesn't need a direct MemoryRepo import.
	// Returns the typed struct as `any` for the handler's JSON
	// encoder; the structural fields go straight to the wire.
	adminHandler.SetMemoryStatsFn(func(ctx context.Context, tenantID uuid.UUID) (any, error) {
		stats, err := memoriesRepo.GetAggregateStats(ctx, tenantID)
		if err != nil {
			return nil, err
		}
		return stats, nil
	})
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

	// Phase D — the model handler also backs the image-route capability
	// check; wire it into the message service so SendStream can tell whether
	// the selected model can call tools (and override a non-tool model for
	// an image-generation turn).
	// Phase 4A.2.4.1 — the PUBLIC shared-link view is a non-restoring consumer.
	shareHandler := handler.NewShareHandler(shareSvc)
	shareHandler.SetDisplayLifecycle(cfg.DLPDisplayLifecycleEnabled)

	modelHandler := handler.NewModelHandler(cfg, gateway)
	messageSvc.SetToolCapabilityChecker(modelHandler.SupportsImageTools)
	// Skip the tools block for models the catalog KNOWS can't call
	// tools, so switching to one mid-conversation (e.g. after an image
	// was generated) doesn't send a ``tools`` param the provider rejects.
	messageSvc.SetNonToolModelChecker(modelHandler.KnownWithoutTools)
	// Approach B — vision-capable models get generated images replayed
	// into history as image_url content so they can SEE what was made;
	// everything else keeps the text-annotation fallback.
	messageSvc.SetVisionCapabilityChecker(modelHandler.SupportsVision)

	handlers := router.Handlers{
		Auth:         handler.NewAuthHandler(authSvc, cfg.CSRFSecret, cfg.CookieSecure),
		Usage:        handler.NewUsageHandler(cfg.GatewayAPIURL),
		Conversation: conversationHandler,
		Folder:       folderHandler,
		Project:      projectHandler,
		Message:      messageHandler,
		Model:        modelHandler,
		Feedback:     handler.NewFeedbackHandler(feedbackSvc),
		File:         handler.NewFileHandler(fileSvc),
		Preferences:  handler.NewPreferencesHandler(prefsRepo),
		Share:        shareHandler,
		Admin:        adminHandler,
		Knowledge:    handler.NewKnowledgeHandler(knowledgeSvc, ragSvc),
		Voice:        handler.NewVoiceHandler(gateway),
		Excel:        handler.NewExcelHandler(excelSvc, filesRepo),
		Document:     handler.NewDocumentHandler(filesRepo, excelSvc, docSvc),
		Workspace:    handler.NewWorkspaceHandler(filesRepo, workspaceSvc),
		Memory:       handler.NewMemoryHandler(memorySvc),
		Search:       handler.NewSearchHandler(searchSvc),
		Account:      handler.NewAccountHandler(exportSvc, purgeSvc),
		// Sprint 2A.5 — direct-dispatch media proxy. Reuses the same
		// GatewayClient (and its internal-trust headers) as the video tool.
		MediaProxy: handler.NewMediaProxyHandler(gateway),
		// Phase 3 — desktop agent-conversation backup mirror (migration 026).
		// filesRepo: FIX 10 — imported user messages can link uploaded
		// attachments via file_ids.
		Import: handler.NewImportHandler(
			service.NewImportService(pool, conversations, messages, filesRepo, conversationSvc),
		),
	}

	// Phase 13 Part B — the /code/run endpoint is ALWAYS registered so the
	// desktop client/server contract stays stable across sandbox on/off
	// deployments. codeInterp is nil when SANDBOX_ENABLED=0; the handler
	// then answers 503 ("feature off"), never 404. No model code runs
	// in-process — a nil interpreter simply refuses the request.
	handlers.Code = handler.NewCodeHandler(codeInterp, fileSvc)

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

	// S9 — start the retention enforcer goroutine. Lifetime is
	// tied to bgCtx, which we cancel during shutdown so the
	// background goroutine drains alongside the HTTP server.
	bgCtx, bgCancel := context.WithCancel(context.Background())
	go retentionEnforcer.StartBackground(bgCtx)

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

	bgCancel()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Error("http server shutdown error", "err", err)
	}
	slog.Info("chat-edge-api stopped")
}
