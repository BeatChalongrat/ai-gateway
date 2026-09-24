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
	"strings"
	"regexp"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/db"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
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
	// FIX129 A1 — every session carries statement / lock / idle-in-
	// transaction timeouts (internal/db), so an abandoned transaction
	// can no longer hold a row lock for the rest of the day.
	pgTimeouts := db.Timeouts{
		StatementMS:         cfg.PGStatementTimeoutMS,
		LockMS:              cfg.PGLockTimeoutMS,
		IdleInTransactionMS: cfg.PGIdleInTransactionTimeoutMS,
	}
	pgCtx, pgCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer pgCancel()
	pool, err := db.NewPool(pgCtx, cfg.DatabaseURL, pgTimeouts)
	if err != nil {
		slog.Error("postgres connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err := pool.Ping(pgCtx); err != nil {
		slog.Error("postgres ping failed", "err", err)
		os.Exit(1)
	}
	slog.Info("postgres connected",
		"statement_timeout_ms", pgTimeouts.StatementMS,
		"lock_timeout_ms", pgTimeouts.LockMS,
		"idle_in_transaction_timeout_ms", pgTimeouts.IdleInTransactionMS,
	)

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
	projects := repository.NewProjectRepo(pool)
	canvasArtifacts := repository.NewCanvasArtifactRepo(pool)
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
	// FIX127 B1 — every conversation read names is_temporary (migration
	// 048). A binary started against a database without it would fail every
	// conversation query; refuse to start instead, the way 024/025 do.
	{
		chkCtx, chkCancel := context.WithTimeout(context.Background(), 5*time.Second)
		var col *string
		_ = pool.QueryRow(chkCtx,
			"SELECT column_name FROM information_schema.columns WHERE table_schema='chat' AND table_name='conversations' AND column_name='is_temporary'",
		).Scan(&col)
		chkCancel()
		if col == nil {
			slog.Error("migration 048 (conversations.is_temporary) not applied; refusing to start — apply migrations first")
			os.Exit(1)
		}
	}
	feedbackRepo := repository.NewFeedbackRepo(pool)
	filesRepo := repository.NewFileRepo(pool)
	prefsRepo := repository.NewPreferencesRepo(pool)
	shareRepo := repository.NewShareRepo(pool)
	knowledgeRepo := repository.NewKnowledgeRepo(pool)
	memoriesRepo := repository.NewMemoryRepo(pool)
	skillsRepo := repository.NewSkillRepo(pool)
	cloudTasksRepo := repository.NewCloudTaskRepo(pool)
	statesRepo := repository.NewConversationStateRepo(pool)
	sourcesRepo := repository.NewResponseContextSourceRepo(pool)
	tenantSettingsRepo := repository.NewTenantMemorySettingsRepo(pool)
	exportsRepo := repository.NewDataExportRepo(pool)
	// Retention & deletion program (docs/DATA_RETENTION_PLAN.md,
	// migration 040): the durable deletion-audit sink, the per-tenant
	// transcript-retention settings, and the one-transaction erase
	// cascade. Declared early — several services below take the audit
	// sink.
	deletionAuditRepo := repository.NewDeletionAuditRepo(pool)
	retentionSettingsRepo := repository.NewTenantRetentionSettingsRepo(pool)
	eraseRepo := repository.NewConversationEraseRepo(pool)

	gateway := service.NewGatewayClient(
		cfg.GatewayAPIURL, cfg.GatewayAPIKey,
		cfg.ServiceJWTSecret, cfg.ServiceJWTTTL,
		cfg.InternalServiceSecret,
	)
	if cfg.GatewayClientTimeout > 0 {
		gateway.SetTotalTimeout(cfg.GatewayClientTimeout)
	}
	titles := service.NewTitleService(gateway)
	titles.SetRepos(conversations, messages)

	authSvc := service.NewAuthService(
		pool, tenants, users, refreshTokens, invitations,
		cfg.UserJWTSecret, cfg.CSRFSecret,
		cfg.AccessTokenExpiry, cfg.RefreshTokenExpiry,
	)
	conversationSvc := service.NewConversationService(conversations, cfg)
	projectSvc := service.NewProjectService(projects, conversations)
	// FIX58 — deleting a project erases the memories scoped to it.
	// Without this they survive as rows no chat can read and no screen
	// can list, holding the user's text with no way to remove it.
	projectSvc.SetMemoryRepo(memoriesRepo)
	// FIX22 §1a — the write-time prompt guard: every stored text that later
	// lands in a system prompt (memories, project instructions, custom
	// instructions, skill instructions) is checked against the gateway's
	// content guardrail BEFORE it is persisted. One rule set (the backend's);
	// chat-edge keeps no copy. Fail-open when the gateway is unreachable.
	promptGuard := service.NewGatewayPromptGuard(gateway)
	projectSvc.SetPromptGuard(promptGuard)
	messageSvc := service.NewMessageService(
		conversations, messages, gateway, titles, cfg, cfg.MaxContextMessages,
	)
	service.SetMaxToolIterations(cfg.MaxToolIterations)
	messageSvc.SetImageGenerationTimeout(time.Duration(cfg.ChatEdgeImageTimeoutSeconds) * time.Second)
	// FIX41 — the run ledger. In-process memory is the READ path for a
	// live process (measured 8 us per turn); Redis is a mirror written by
	// a background pipeline (17 us per turn) so a restart — or another
	// replica — can still replay a run it never held. The synchronous
	// alternatives were measured and rejected: one Redis round trip per
	// frame costs 325 ms per turn and one Postgres INSERT per frame
	// 425 ms, against a 15 ms budget
	// (scripts/fix41-probe/results/E1-ledger-backends.json).
	//
	// Redis unreachable is NOT fatal: the broker falls back to
	// in-memory-only, which is exactly the pre-chunk-2 behaviour.
	ledgerTTL := time.Duration(cfg.StreamLedgerTTLSeconds) * time.Second
	messageSvc.ConfigureRunLedger(ledgerTTL, cfg.StreamLedgerMaxEvents, rdb)
	// FIX74 — parked web turns (ask_user_question). Migration 042.
	messageSvc.SetPendingQuestionStore(repository.NewPendingQuestionRepo(pool))
	// FIX131 E1 — every streaming row holds a run lease (redis-webchat)
	// while this process runs it, so the interrupted-turn sweep — here or on
	// another instance — never settles a live turn. Renewed by runLeases.Run
	// (started below, stopped only after the shutdown drain).
	runLeases := service.NewRunLeases(service.NewRedisLeaseStore(rdb, ""), "", cfg.RunLeaseTTL, 0)
	messageSvc.SetRunLeases(runLeases)
	slog.Info("run leases configured",
		"owner", runLeases.Owner(),
		"ttl", runLeases.TTL().String(),
		"renew_every", runLeases.RenewEvery().String(),
	)
	slog.Info("stream-resume ledger configured",
		"ttl_seconds", cfg.StreamLedgerTTLSeconds,
		"max_events_per_run", cfg.StreamLedgerMaxEvents,
		"durable_mirror", rdb != nil,
	)
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
	// FIX119 Part C2 — which callers are test accounts.
	if p := strings.TrimSpace(cfg.TestAccountEmailPattern); p != "" {
		if re, err := regexp.Compile(p); err != nil {
			slog.Warn("TEST_ACCOUNT_EMAIL_PATTERN is not a valid regexp — no account will be labelled as test", "pattern", p, "err", err.Error())
		} else {
			provisionSvc.SetTestAccountPattern(re)
		}
	}

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
	fileSvc.SetDeletionAudit(deletionAuditRepo)
	// The transcript sweep needs the storage backend (file OBJECTS are
	// deleted before the row cascade), so it is built here rather than
	// with the other retention pieces above.
	transcriptRetention := service.NewTranscriptRetentionService(
		retentionSettingsRepo, eraseRepo, filesRepo, deletionAuditRepo, storageBackend,
	)
	// FIX132 C — the purge of what users deleted runs for every tenant;
	// DELETED_DATA_PURGE_MODE (on | dry_run | off) is its ceiling.
	transcriptRetention.SetDeletedPurgeMode(cfg.DeletedDataPurgeMode)
	slog.Info("deleted data purge", "mode", transcriptRetention.DeletedPurgeMode(),
		"default_days", model.DefaultDeletedPurgeDays)
	prefsSvc := service.NewPreferencesService(prefsRepo)
	shareSvc := service.NewShareService(shareRepo, conversations, messages, filesRepo)
	// S1 — Personal Memory Core. Service is wired alongside prefs +
	// share so the message service can pick it up via SetMemoryReader
	// below (system-prompt injection + AI tool execution).
	memorySvc := service.NewMemoryService(memoriesRepo)
	// S9 — wire the tenant settings repo onto the memory service
	// so CreateMemory enforces the per-tenant max-memories cap.
	memorySvc.SetTenantSettings(tenantSettingsRepo)
	memorySvc.SetPromptGuard(promptGuard) // FIX22 §1a
	// S9 — Export + Purge + Retention. Storage root reuses the
	// existing FileStorageRoot config so exports land in the
	// same bind-mount as uploads (./data by default).
	exportSvc := service.NewExportService(memoriesRepo, exportsRepo, tenantSettingsRepo, cfg.FileStorageRoot)
	purgeSvc := service.NewPurgeService(memoriesRepo, statesRepo, sourcesRepo, exportsRepo, tenantSettingsRepo, cfg.FileStorageRoot)
	retentionEnforcer := service.NewRetentionEnforcer(memoriesRepo, tenantSettingsRepo)
	purgeSvc.SetDeletionAudit(deletionAuditRepo)
	conversationSvc.SetDeletionAudit(deletionAuditRepo)
	// S3 — Structured Conversation State. Backs the
	// <conversation_state> system-prompt block, the
	// update_conversation_state AI tool, and the GET
	// /conversations/{id}/state read endpoint. Uses the gateway
	// client for its extraction calls (low-temperature JSON).
	stateSvc := service.NewConversationStateService(statesRepo, messages, conversations, gateway)
	// FIX102 Part D — an operator-chosen summariser; FIX126 O1 — else the
	// background model (BACKGROUND_TASK_MODEL). Either way the chat model is
	// the fallback.
	summariserModel, backgroundModel := service.BackgroundModels(cfg.ContextSummaryModel, cfg.BackgroundTaskModel)
	stateSvc.SetExtractionModel(summariserModel)
	slog.Info("context summariser", "model", summariserModel, "fallback", "the conversation's chat model")
	compactionUsageRepo := repository.NewCompactionUsageRepo(pool)

	// Phase 4 — Excel Smart Workspace (Session 1) +
	// Word/PDF Smart Reports (Session 2). Both services wired
	// before the file service's auto-inspect hook so an upload
	// that lands the instant the server starts up still gets
	// inspected.
	excelSvc := service.NewExcelService(filesRepo, conversations, storageBackend, cfg.ExtractPythonBinary, "scripts")
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
			Runtime:     cfg.SandboxRuntime,
		})
		if err := sbx.Available(context.Background()); err != nil {
			slog.Error("sandbox enabled but unavailable — refusing to run plan-exec in-process", "err", err)
			os.Exit(1)
		}
		excelSvc.SetSandbox(sbx)
		docSvc.SetSandbox(sbx)
		fileSvc.SetSandbox(sbx)
		workspaceSvc.SetSandbox(sbx)
		// FIX14 §2 — state the isolation boundary in effect at boot, measured
		// from a probe run (not from config), so an operator learns it here
		// rather than from an incident.
		requestedRuntime := cfg.SandboxRuntime
		if requestedRuntime == "" {
			requestedRuntime = "runc"
		}
		if boundary, berr := sbx.VerifyBoundary(context.Background()); berr == nil {
			slog.Info("plan-exec sandbox active", "image", cfg.SandboxImage,
				"requested_runtime", requestedRuntime, "boundary_in_effect", boundary)
		} else {
			slog.Info("plan-exec sandbox active", "image", cfg.SandboxImage,
				"requested_runtime", requestedRuntime, "boundary_probe_err", berr.Error())
		}
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
	// FIX130 A2 — memory-off and temporary chats read no memory; the
	// FIX56.1 switch that let them is gone. An old value is ignored, loudly.
	if v := strings.TrimSpace(cfg.TemporaryChatReadsMemory); v != "" {
		slog.Warn("TEMPORARY_CHAT_READS_MEMORY is retired and ignored: memory-off and temporary chats never read memory",
			"value", v)
	}
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
	// FIX33 A-W2 — compaction fires at CONTEXT_COMPACT_TRIGGER_RATIO of
	// the messages budget (default 0.85) and trims down to
	// CONTEXT_COMPACT_TARGET_RATIO (0.60), instead of only on overflow.
	contextCompiler := service.NewContextCompiler(stateSvc)
	contextCompiler.SetRatios(cfg.ContextCompactTriggerRatio, cfg.ContextCompactTargetRatio)
	messageSvc.SetContextCompiler(contextCompiler)
	// FIX33 A-W8 — the token cost of every compaction pass is kept apart
	// from the cost of answering (chat.compaction_usage, migration 038).
	stateSvc.SetCompactionUsageRecorder(service.NewCompactionUsageSink(compactionUsageRepo))
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
	// S8 — wire the background memory learner (facts + preferences,
	// FIX130). Fires from finalizeConversation after two user turns and
	// then every two more, over the new messages only, in chats whose
	// memory is active. Pending suggestions surface on the Memory page;
	// repeated preferences are consolidated after 3+ conversations.
	prefExtractor := service.NewPreferenceExtractor(memorySvc, memoriesRepo, messages, conversations, gateway)
	prefExtractor.SetExtractionModel(backgroundModel) // FIX126 O1
	// FIX130 A1e — the per-conversation watermark and in-flight claim live in
	// Redis, so replicas agree and a restart does not re-read old messages.
	prefExtractor.SetRedis(rdb)
	slog.Info("preference extractor", "model", backgroundModel, "fallback", "the conversation's chat model")
	messageSvc.SetPreferenceExtractor(prefExtractor)

	// FIX12 Item 3 — user skills. The reader preloads enabled skills into
	// the prompt compiler (trigger match + use_skill catalog); the tool
	// wiring exposes use_skill so the model can pull an unmatched skill on
	// demand. SetSkillTool forwards to the ToolExecutor, so it must follow
	// SetMemoryService (which creates the executor). A skill is instruction
	// text only — nothing here executes it.
	messageSvc.SetSkillReader(skillsRepo)
	messageSvc.SetSkillTool(skillsRepo)

	// Knowledge base (RAG) pipeline.
	//
	// EmbeddingService reads OPENAI_API_KEY at construction time —
	// when unset, the upload/index endpoints still respond but the
	// indexer marks each document "failed" with a clear "configure
	// OpenAI" error_message. We wire RAGService into messageSvc
	// unconditionally so a future env-var flip enables retrieval
	// without a service restart.
	embeddingSvc := service.NewEmbeddingService(gateway)
	// FIX130 C2 — every embedding outcome feeds the knowledge availability
	// verdict the clients read before offering a knowledge base.
	kbAvailability := service.NewKnowledgeAvailability(embeddingSvc.Probe)
	embeddingSvc.SetObserver(kbAvailability.Observe)
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

	knowledgeHandler := handler.NewKnowledgeHandler(knowledgeSvc, ragSvc)
	knowledgeHandler.SetAvailability(kbAvailability)

	conversationHandler := handler.NewConversationHandler(conversationSvc)
	// FIX33 A-W8 — /usage/me reports the compaction bill separately.
	usageHandler := handler.NewUsageHandler(cfg.GatewayAPIURL)
	usageHandler.SetCompactionUsageRepo(compactionUsageRepo)
	conversationHandler.SetExportSources(conversations, messages, filesRepo, cfg)
	conversationHandler.SetKnowledgeService(knowledgeSvc)

	projectHandler := handler.NewProjectHandler(projectSvc)
	// Phase 6/C3 — canvas artifacts. messageSvc supplies the canonical
	// conversation-ownership check (AssertConversationAccess).
	canvasArtifactSvc := service.NewCanvasArtifactService(canvasArtifacts, messageSvc)
	canvasArtifactHandler := handler.NewCanvasArtifactHandler(canvasArtifactSvc)
	// Phase 8 Task 3 — model-driven canvas updates. The lookup feeds the
	// "## Canvas Artifacts" system block (repo: ownership is already
	// settled by the message flow); the tool goes through the SERVICE so
	// every write re-asserts conversation ownership.
	messageSvc.SetCanvasLookup(canvasArtifacts)
	messageSvc.SetCanvasArtifactTool(canvasArtifactSvc)
	conversationHandler.SetTitleService(titles)
	conversationHandler.SetConversationStateService(stateSvc)
	conversationHandler.SetMessageService(messageSvc)
	// FIX125 M2 — DELETE stops the conversation's running turn.
	conversationHandler.SetStreamCanceller(messageSvc)

	messageHandler := handler.NewMessageHandler(messageSvc)
	// FIX22 §1a — custom instructions + skill instructions are prompt sources too.
	preferencesHandler := handler.NewPreferencesHandler(prefsRepo)
	preferencesHandler.SetPromptGuard(promptGuard)
	skillsHandler := handler.NewSkillHandler(skillsRepo)
	skillsHandler.SetPromptGuard(promptGuard)
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
	// FIX64 — when the Gateway refuses tools the catalog said the model
	// had, drop the cached catalog and retry the turn without tools.
	messageSvc.SetCatalogInvalidator(modelHandler.InvalidateCatalog)
	// FIX101 — the context budget stops being a hard-coded map and starts
	// being data: the catalogue's max_context_tokens, which an operator
	// edits in the admin console. Wired into BOTH the compiler (every
	// send) and the service (the /context-info fallback) so the meter the
	// user reads and the budget the send uses can never disagree.
	messageSvc.SetContextWindowLookup(modelHandler.AdvertisedContextWindow)
	contextCompiler.SetContextWindowLookup(modelHandler.AdvertisedContextWindow)
	// FIX65 — a model the catalog says cannot read images gets no history
	// image blocks; a model_no_vision_support refusal is retried once
	// without them, so one failed image turn never poisons the chat.
	messageSvc.SetNonVisionModelChecker(modelHandler.KnownWithoutVision)
	// Approach B — vision-capable models get generated images replayed
	// into history as image_url content so they can SEE what was made;
	// everything else keeps the text-annotation fallback.
	messageSvc.SetVisionCapabilityChecker(modelHandler.SupportsVision)
	// Models-catalog step 3 — the trivial-turn reasoning downgrade gate
	// reads reasoning capability from the live gateway catalog
	// (DB-authoritative) instead of the static set, so onboarding a
	// reasoning model is a data change, not a chat-edge release.
	messageSvc.SetReasoningCapabilityChecker(modelHandler.ReasoningCapable)

	// FIX126 F4 — a file made from a plan card is linked to the message
	// that wrote the plan, so a reload shows it on that message.
	planOwners := service.NewPlanOwnerResolver(messages)
	excelHandler := handler.NewExcelHandler(excelSvc, filesRepo)
	excelHandler.SetPlanOwnerResolver(planOwners)
	documentHandler := handler.NewDocumentHandler(filesRepo, excelSvc, docSvc)
	documentHandler.SetPlanOwnerResolver(planOwners)
	// FIX131 A3 — a failed plan execution is kept (Redis, 30 days) for the
	// plan's message; GET /messages returns it so a reload shows the failure.
	planOutcomes := service.NewPlanOutcomeStore(rdb)
	excelHandler.SetPlanOutcomeStore(planOutcomes)
	documentHandler.SetPlanOutcomeStore(planOutcomes)
	messageHandler.SetPlanOutcomeStore(planOutcomes)

	// Phase 3 — desktop agent-conversation backup mirror (migration 026).
	// FIX130 A1 — an import that adds messages kicks the memory extractor,
	// the desktop's only route to it (its turns bypass the send path).
	importSvc := service.NewImportService(pool, conversations, messages, filesRepo, conversationSvc)
	importSvc.SetAfterImport(messageSvc.AfterImport)

	// FIX132 C — account deletion, driven by the backend: hide now, purge
	// after 30 days (handler/internal_account.go). The revocation store is
	// wired below, once it exists.
	accountDeletion := service.NewAccountDeletionService(
		pool, eraseRepo, filesRepo, deletionAuditRepo, storageBackend, cfg.FileStorageRoot,
	)
	accountDeletion.SetStreamCanceller(messageSvc)

	// FIX132 C — "export all my data" (a ZIP built in the background) and
	// the data policy the Settings page states.
	accountExport := service.NewAccountExportService(service.AccountExportSources{
		Conversations: conversations, Messages: messages, Files: filesRepo,
		Memories: memoriesRepo, Projects: projects, Preferences: prefsRepo,
		Skills: skillsRepo, Exports: exportsRepo, Settings: tenantSettingsRepo,
	}, storageBackend, cfg.FileStorageRoot, cfg.ExportMaxFileBytes, cfg.ExportMaxConversations)
	if cfg.DLPDisplayLifecycleEnabled {
		// The export reads messages the way the owner's reload does.
		accountExport.SetDisplayResolver(func(ctx context.Context, m model.Message, userID, tenantID uuid.UUID) string {
			return messageSvc.ResolveRepresentation(ctx, service.ConsumerBrowserReloadOwner, m,
				service.DisplayActor{UserID: userID, TenantID: tenantID}).Content
		})
	}
	accountHandler := handler.NewAccountHandler(exportSvc, purgeSvc)
	accountHandler.SetFullExport(accountExport)
	accountHandler.SetDataPolicy(handler.DataPolicySource{
		Retention:    retentionSettingsRepo,
		PurgeEnvMode: cfg.DeletedDataPurgeMode,
		BackupDays:   cfg.BackupRetentionDays,
	})

	handlers := router.Handlers{
		Auth:           handler.NewAuthHandler(authSvc, cfg.CSRFSecret, cfg.CookieSecure),
		Usage:          usageHandler,
		Conversation:   conversationHandler,
		Project:        projectHandler,
		CanvasArtifact: canvasArtifactHandler,
		Message:        messageHandler,
		Model:          modelHandler,
		Feedback:       handler.NewFeedbackHandler(feedbackSvc),
		File:           handler.NewFileHandler(fileSvc),
		GeneratedFile:  handler.NewGeneratedFileHandler(fileSvc),
		Preferences:    preferencesHandler,
		Share:          shareHandler,
		Admin:          adminHandler,
		Knowledge:      knowledgeHandler,
		Voice:          handler.NewVoiceHandler(gateway),
		Excel:          excelHandler,
		Document:       documentHandler,
		Workspace:      handler.NewWorkspaceHandler(filesRepo, workspaceSvc),
		Memory:         handler.NewMemoryHandler(memorySvc),
		Search:         handler.NewSearchHandler(searchSvc),
		Skills:         skillsHandler,
		// FIX13 — cloud scheduled tasks (migration 035): authenticated CRUD +
		// audit. FIX16 — the unattended firing loop IS now wired (above, gated
		// on CLOUD_SCHEDULER_ENABLED): StartScheduler → CloudTaskRunner.Tick,
		// authorize-before-run, DB lease (migration 036), cloud tool subset.
		CloudTasks: handler.NewCloudTaskHandler(cloudTasksRepo),
		Account:    accountHandler,
		// Retention & deletion program admin surface: settings (floor-
		// validated, audited), deletion-audit reads, dry-run preview.
		RetentionAdmin: handler.NewRetentionAdminHandler(
			retentionSettingsRepo, deletionAuditRepo, transcriptRetention,
		),
		// Sprint 2A.5 — direct-dispatch media proxy. Reuses the same
		// GatewayClient (and its internal-trust headers) as the video tool.
		MediaProxy: handler.NewMediaProxyHandler(gateway),
		// Phase 3 — desktop agent-conversation backup mirror (migration 026).
		// filesRepo: FIX 10 — imported user messages can link uploaded
		// attachments via file_ids.
		Import: handler.NewImportHandler(importSvc),
		// FIX132 C — registered only when INTERNAL_SERVICE_SECRET is set.
		InternalAccount: handler.NewInternalAccountHandler(cfg.InternalServiceSecret, accountDeletion),
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

	// FIX129 B2 — sign-out ends the session here too: the Portal posts the
	// logged-out token to /internal/auth/revoke (gated by
	// INTERNAL_SERVICE_SECRET) and the auth middleware refuses it from
	// then on. Redis holds the marks; a Redis error on the read side
	// serves the request and logs auth_revocation_check_failed.
	revocations := auth.NewRedisRevocations(rdb)
	accountDeletion.SetRevoker(revocations)
	if cfg.InternalServiceSecret == "" {
		slog.Warn("INTERNAL_SERVICE_SECRET is empty: POST /internal/auth/revoke is not registered, so a Portal sign-out cannot end a chat-edge session before the token expires")
	}

	r := router.New(cfg, handlers, corsOrigins, rdb, provisionSvc, revocations)

	srv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
	}

	// S9 — start the retention enforcer goroutine. Lifetime is
	// tied to bgCtx, which we cancel during shutdown so the
	// background goroutine drains alongside the HTTP server.
	// FIX131 E2 — the background loops are tracked, so the shutdown waits
	// for them to stop instead of exiting under a half-done pass.
	bgCtx, bgCancel := context.WithCancel(context.Background())
	var bgWG sync.WaitGroup
	startBackground := func(run func(context.Context)) {
		bgWG.Add(1)
		go func() {
			defer bgWG.Done()
			run(bgCtx)
		}()
	}
	startBackground(retentionEnforcer.StartBackground)

	// Transcript retention sweep — 00:00 UTC daily. FIX132: chats users
	// deleted are erased 30 days later for every tenant (the tenant flags
	// and DELETED_DATA_PURGE_MODE can hold it at dry_run or off); the
	// inactive-transcript rule stays per-tenant opt-in, dry-run first.
	// No boot-time pass: the deletion hour is fixed.
	startBackground(transcriptRetention.StartBackground)

	// FIX132 C — export files are removed once their 24-hour download
	// window closes (hourly pass; at boot too).
	startBackground(exportSvc.StartSweep)

	// FIX131 E1 — the lease renewal runs until AFTER the shutdown drain
	// (its own context): the turns being drained must keep their leases.
	leaseCtx, leaseCancel := context.WithCancel(context.Background())
	go runLeases.Run(leaseCtx)
	// FIX131 E1 — the interrupted-turn sweep: at boot, a confirming pass
	// shortly after, then every INTERRUPTED_TURN_SWEEP_INTERVAL. Settles
	// streaming rows no process holds a lease for (the previous process's
	// turns after a crash, the rows whose conversation was deleted while
	// they ran). One summary line per pass, counts only.
	interruptedSweep := service.NewInterruptedTurnSweep(messages, runLeases, cfg.InterruptedTurnSweepInterval, cfg.InterruptedTurnMinAge)
	slog.Info("interrupted turn sweep",
		"interval", interruptedSweep.Interval.String(),
		"min_age", interruptedSweep.MinAge.String(),
		"confirm", interruptedSweep.Confirm.String(),
	)
	startBackground(interruptedSweep.StartBackground)

	// FIX127 A2 — uploads never attached to a sent message expire after
	// UNATTACHED_FILE_TTL (48 h). dry_run by default: the first pass on a
	// live system is the per-account report the owner reads before
	// setting UNATTACHED_FILE_SWEEP_MODE=delete.
	unattachedSweep := service.NewUnattachedFileSweep(
		filesRepo, storageBackend, deletionAuditRepo,
		cfg.UnattachedFileTTL, cfg.UnattachedFileSweepMode, cfg.UnattachedFileSweepInterval,
	)
	slog.Info("unattached file sweep", "mode", unattachedSweep.Mode,
		"ttl", unattachedSweep.TTL.String(), "interval", unattachedSweep.Interval.String())
	startBackground(unattachedSweep.StartBackground)

	// FIX16 — cloud scheduled tasks: the always-on firing goroutine. OFF
	// unless CLOUD_SCHEDULER_ENABLED=true. The runner authorizes EVERY fire
	// against the backend's revocation+balance gate BEFORE any turn (FIX13's
	// invariant), fires with the read-only cloud tool subset only, holds a DB
	// lease so no task double-fires, and audits every run. messageSvc is the
	// TurnRunner (the same headless SendNonStream FIX13 proved).
	if cfg.CloudSchedulerEnabled {
		if cfg.CloudTaskAuthorizeURL == "" {
			// Fail closed: never run unattended tasks without the revocation
			// gate. Refuse to START the scheduler (the rest of the server is
			// unaffected) rather than fire tasks that can't be authorized.
			logger.Error("cloud scheduler NOT started: CLOUD_SCHEDULER_ENABLED=true but CLOUD_TASK_AUTHORIZE_URL is empty (no revocation gate)")
		} else {
			bearer := func() (string, error) {
				return auth.IssueServiceJWT(cfg.ServiceJWTSecret, cfg.ServiceJWTTTL)
			}
			cloudAuthorizer := service.NewHTTPRunAuthorizer(cfg.CloudTaskAuthorizeURL, cfg.InternalServiceSecret, bearer)
			cloudRunner := service.NewCloudTaskRunner(cloudTasksRepo, cloudAuthorizer, messageSvc)
			service.StartScheduler(bgCtx, cloudRunner, cfg.CloudSchedulerInterval, logger)
			logger.Info("cloud scheduled tasks ENABLED", "interval", cfg.CloudSchedulerInterval.String(), "authorize_url_configured", true)
		}
	} else {
		logger.Info("cloud scheduled tasks disabled (CLOUD_SCHEDULER_ENABLED not set)")
	}

	// Run + graceful shutdown.
	go func() {
		slog.Info("chat-edge-api listening", "port", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("http server exited", "err", err)
			os.Exit(1)
		}
	}()

	// FIX131 E2 — graceful drain on SIGTERM (cmd/server/shutdown.go,
	// service/drain.go). The listener stays open through the drain; only
	// NEW turns are refused (503 SERVER_DRAINING + Retry-After).
	stop := make(chan os.Signal, 2)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	shutdownPlan{
		svc:          messageSvc,
		srv:          srv,
		bgCancel:     bgCancel,
		bgWG:         &bgWG,
		leaseCancel:  leaseCancel,
		drainTimeout: cfg.DrainTimeout,
		drainGrace:   cfg.DrainGrace,
	}.run(stop)
	slog.Info("chat-edge-api stopped")
}
