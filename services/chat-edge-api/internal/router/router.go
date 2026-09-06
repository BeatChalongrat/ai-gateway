// HTTP route registration.
//
// Public routes (no auth) — health + auth/register + auth/login.
// Protected routes — everything else, gated by middleware.RequireAuth.
// All protected routes live under “/api/v1“ so the path prefix
// also acts as a deployment versioning anchor.
//
// Order of middleware is load-bearing:
//
//	logging      (request id minted here, body never read)
//	cors         (preflight short-circuits before handler)
//	auth         (only applied inside the protected subgroup)
//	rate limit   (applied ONLY to the high-cost send endpoints,
//	              chained AFTER auth so the user-id is resolvable)
package router

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/redis/go-redis/v9"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// Handlers bundles every HTTP handler the router wires up.
//
// Passed in from cmd/server/main.go so router has zero knowledge of
// concrete services / repos / db / redis.
type Handlers struct {
	Auth         *handler.AuthHandler
	Conversation *handler.ConversationHandler
	// Project owns the projects surface (migration 029): project CRUD +
	// KB binding + the conversation→project move route. Since Phase 9
	// this is the ONLY organiser — folders were merged into projects by
	// migration 031 and their routes are gone.
	Project *handler.ProjectHandler
	// CanvasArtifact owns the Phase 6/C3 canvas-artifact surface
	// (migration 030): versioned inline artifacts per conversation.
	CanvasArtifact *handler.CanvasArtifactHandler
	Message        *handler.MessageHandler
	Model          *handler.ModelHandler
	Feedback       *handler.FeedbackHandler
	File           *handler.FileHandler
	Preferences    *handler.PreferencesHandler
	Share          *handler.ShareHandler
	Admin          *handler.AdminHandler
	// Knowledge is optional — left nil when RAG isn't compiled in
	// (it always is in the current build but the handler keeps a
	// guard so omitting it doesn't crash the router setup).
	Knowledge *handler.KnowledgeHandler
	// Voice proxies to OpenAI Whisper / TTS. Always wired (the
	// handler itself fails 503 when the API key isn't configured).
	Voice *handler.VoiceHandler
	// Phase 4 — Excel Smart Workspace. Optional in tests; routes
	// only register when wired so legacy stacks stay green.
	Excel *handler.ExcelHandler
	// Phase 4 Session 2 — Word/PDF Smart Reports. Owns the
	// type-dispatching /files/{id}/inspect route and the docx/pdf
	// execute route.
	Document *handler.DocumentHandler
	// Phase 4 Session 5 — Artifact Workspace. Owns the
	// generated-files list, rollback, and file-compare routes.
	Workspace *handler.WorkspaceHandler
	// S1 — Personal Memory Core. CRUD + pin + confirm + audit
	// for chat.user_memories.
	Memory *handler.MemoryHandler
	// S6 — Past-conversation search. Backs both the AI tool and
	// the frontend's "search my chats" surface (future).
	Search *handler.SearchHandler
	// FIX12 Item 3 — user SKILLS. CRUD for chat.user_skills; the
	// stored instruction text feeds the prompt compiler + use_skill
	// tool. Optional (nil → routes not registered).
	Skills *handler.SkillHandler
	// FIX13 — CLOUD SCHEDULED TASKS. Authenticated CRUD + audit for
	// chat.cloud_scheduled_tasks; the unattended firing (authorize-
	// before-run) lives in the runner, not here. Optional (nil → routes
	// not registered).
	CloudTasks *handler.CloudTaskHandler
	// S9 — GDPR-style data export + right-to-erasure for memory.
	Account *handler.AccountHandler
	// Retention & deletion program — admin settings, deletion audit,
	// dry-run preview (docs/DATA_RETENTION_PLAN.md). Optional (nil →
	// routes not registered).
	RetentionAdmin *handler.RetentionAdminHandler
	// Usage — credit badge + per-model breakdown for web-chat.
	// Thin authenticated proxy onto the gateway's portal API.
	Usage *handler.UsageHandler
	// MediaProxy — Sprint 2A.5 direct-dispatch media gen. Thin trusted
	// proxy forwarding /videos|audio/generations + /jobs/{id} to the
	// gateway's public media routes. Optional (nil when not wired).
	MediaProxy *handler.MediaProxyHandler
	// Import — desktop agent-conversation backup mirror (migration 026).
	// Optional; no sendRateLimit (nothing here reaches an LLM).
	Import *handler.ImportHandler
	// Code — Phase 13 Part B run_python endpoint for the desktop agent's
	// thin-client tool. Optional: wired ONLY when the hardened sandbox is
	// available (SANDBOX_ENABLED=1). Rate-limited (a sandbox run is
	// expensive). nil → route not registered (404).
	Code *handler.CodeHandler
}

// New builds the full Chi router with all routes + middleware.
//
// “provision“ is the user-shadow service the Portal-JWT auth
// middleware uses to lazily insert identity rows the first time a
// Portal user hits chat-edge. Threaded through here rather than via
// Handlers because it's middleware-only.
func New(
	cfg *config.Config,
	h Handlers,
	corsOrigins string,
	rdb *redis.Client,
	provision *service.ProvisionService,
) http.Handler {
	r := chi.NewRouter()

	// Order matters: log -> cors -> (route auth applied per subgroup).
	r.Use(middleware.Logging)
	r.Use(middleware.CORS(corsOrigins))

	// Health + readiness — public, used by docker / k8s probes.
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":  "ok",
			"service": "chat-edge-api",
			// FIX42 D1 — how many runs lost their durable ledger copy to
			// writer overflow (STREAM_CONTRACT.md §8.1). Zero is the
			// expectation; anything else is worth a look before it is
			// worth an alert threshold (OPEN_DECISIONS.md #40).
			"run_ledger_overflow_drops": service.RunLedgerOverflowDrops(),
		})
	})

	// Rate-limit middleware shared across the high-cost endpoints.
	// Constructed once so the closure captures rdb + the configured
	// limit / window. Routes that don't need rate-limiting simply
	// don't pull this into their chain.
	sendRateLimit := middleware.RateLimit(rdb, cfg.RateLimitMessages, cfg.RateLimitWindow)

	r.Route("/api/v1", func(r chi.Router) {
		// Public auth endpoints — no RequireAuth. Logout sits here
		// (alongside refresh) because both authenticate via the
		// refresh cookie + CSRF header pair: making them depend on
		// a valid access JWT would lock the user out of logging
		// out once the access token expires.
		r.Post("/auth/register", h.Auth.Register)
		r.Post("/auth/login", h.Auth.Login)
		r.Post("/auth/refresh", h.Auth.Refresh)
		r.Post("/auth/logout", h.Auth.Logout)

		// Protected subgroup. Auth is now Portal-JWT only — the chat-
		// edge issues no tokens of its own (see handler/auth.go which
		// returns 410 Gone on /auth/* for any leftover clients).
		r.Group(func(r chi.Router) {
			r.Use(middleware.RequireAuth(cfg.PortalJWTSecret, provision))

			// Conversations.
			r.Get("/conversations", h.Conversation.List)
			r.Post("/conversations", h.Conversation.Create)
			r.Get("/conversations/{id}", h.Conversation.Get)
			r.Patch("/conversations/{id}", h.Conversation.Update)
			r.Delete("/conversations/{id}", h.Conversation.Delete)

			// Folders (migration 022) are RETIRED as of Phase 9 — every
			// folder became a project (migration 031) and the routes are
			// no longer registered. The table, the folder_id column, and
			// the repo/service/handler code all remain for one release so
			// the migration stays reversible; nothing serves them.
			// See migrations/031_merge_folders_into_projects.sql.

			// Projects (migration 029) — the single organiser: grouping,
			// plus optional shared instructions + KB. A project with
			// empty instructions and no KB is exactly what a folder was.
			if h.Project != nil {
				r.Get("/projects", h.Project.List)
				r.Post("/projects", h.Project.Create)
				r.Get("/projects/{id}", h.Project.Get)
				r.Put("/projects/{id}", h.Project.Update)
				r.Put("/projects/{id}/knowledge-base", h.Project.SetKnowledgeBase)
				r.Delete("/projects/{id}", h.Project.Delete)
				// Conversation → project membership.
				r.Put("/conversations/{id}/project", h.Project.MoveConversation)
			}

			// Phase 6/C3 — Canvas artifacts: versioned inline artifacts
			// (html/react/mermaid/svg/markdown) per conversation. Every
			// save appends a version; revert appends a copy of an older
			// one (history is never mutated).
			if h.CanvasArtifact != nil {
				r.Get("/conversations/{id}/artifacts", h.CanvasArtifact.List)
				r.Post("/conversations/{id}/artifacts", h.CanvasArtifact.Save)
				r.Get("/conversations/{id}/artifacts/{key}/versions", h.CanvasArtifact.Versions)
				r.Post("/conversations/{id}/artifacts/{key}/revert", h.CanvasArtifact.Revert)
			}

			// Desktop import — idempotent backup mirror of agent-mode
			// chats. NOT rate-limited (no LLM cost; a first sync bursts
			// legitimately). chi matches the literal "import" segment
			// before the {id} routes.
			if h.Import != nil {
				r.Post("/conversations/import", h.Import.ImportConversation)
				r.Post("/conversations/{id}/messages/import", h.Import.ImportMessages)
			}

			// Cancel — interrupts the in-flight stream for a
			// conversation. Scoped at the conversation (one active
			// generation per conversation in Phase 1), no rate
			// limit (cancel is cheap + needs to always work to
			// stop a runaway stream).
			r.Post("/conversations/{id}/cancel", h.Message.Cancel)

			// Messages.
			r.Get("/conversations/{id}/messages", h.Message.List)
			// Phase 5 — reattach to an in-flight generation (SSE).
			r.Get("/conversations/{id}/messages/{messageID}/stream", h.Message.ResumeStream)

			// Send + Regenerate go through the rate-limit middleware
			// — both kick off an expensive Gateway round-trip.
			r.With(sendRateLimit).Post("/conversations/{id}/messages", h.Message.Send)
			// FIX74 — answer a parked question; resumes the turn as a stream.
			r.With(sendRateLimit).Post("/conversations/{id}/questions/{promptID}", h.Message.AnswerQuestion)
			r.With(sendRateLimit).Post(
				"/conversations/{id}/messages/{messageID}/regenerate",
				h.Message.Regenerate,
			)

			// Feedback.
			r.Post("/messages/{messageID}/feedback", h.Feedback.Create)

			// Models.
			r.Get("/models", h.Model.List)

			// Sprint 2A.5 — direct-dispatch media generation. Thin trusted
			// proxy to the gateway's PUBLIC media routes (caller identity
			// forwarded via the internal-trust headers, same as the tool
			// path). The gateway enforces DLP + balance + the feature gate.
			// Submits are rate-limited (high-cost, like message Send); job
			// polling is not.
			if h.MediaProxy != nil {
				r.With(sendRateLimit).Post("/videos/generations", h.MediaProxy.SubmitVideo)
				r.With(sendRateLimit).Post("/audio/generations", h.MediaProxy.SubmitAudio)
				r.Get("/jobs/{id}", h.MediaProxy.GetJob)
				r.Post("/jobs/{id}/cancel", h.MediaProxy.CancelJob)
				// Finished artifact bytes — streamed, cookie-authed. The
				// web-chat media forms play video/audio from this via a
				// blob object URL (Phase 11 A3).
				r.Get("/jobs/{id}/output", h.MediaProxy.JobOutput)
			}

			// Usage — credit balance + per-model breakdown (proxied
			// from the gateway portal API with the caller's token).
			if h.Usage != nil {
				r.Get("/usage/me", h.Usage.Me)
			}

			// File attachments. Upload + list scoped to a
			// conversation; download + delete go through the file
			// id so the URL doesn't leak conversation membership.
			r.Post("/conversations/{id}/files", h.File.Upload)
			r.Get("/conversations/{id}/files", h.File.List)
			r.Get("/files/{fileID}/download", h.File.Download)
			// Extracted text of one attachment. The desktop agent's
			// read_document tool reads it for scanned PDFs, which only
			// the server-side OCR pipeline can handle.
			r.Get("/files/{fileID}/text", h.File.Text)
			// Structured preview data (Phase 16) — the same bytes as
			// /text, shaped: sheets/rows for a spreadsheet, ordered
			// blocks for Word, one entry per slide. 415 when the type
			// has no structured view, so a client can ask first and
			// fall back to /text.
			r.Get("/files/{fileID}/preview-data", h.File.PreviewData)
			r.Delete("/files/{fileID}", h.File.Delete)

			// User preferences (custom instructions).
			r.Get("/user/preferences", h.Preferences.Get)
			r.Put("/user/preferences", h.Preferences.Put)

			// Conversation export.
			r.Get("/conversations/{id}/export", h.Conversation.Export)

			// AI-generated title — frontend fires this once after the
			// first assistant turn completes so the sidebar gets a
			// human-readable name instead of a truncated user prompt.
			r.Post("/conversations/{id}/generate-title", h.Conversation.GenerateTitle)

			// S3 — Structured Conversation State (read-only). The
			// AI mutates state via the update_conversation_state
			// tool; this surface lets the frontend display
			// goals / decisions / tasks if it wants to in future.
			r.Get("/conversations/{id}/state", h.Conversation.GetState)

			// S4 — Context compiler dry-run. Returns the token
			// budget breakdown + how many messages would be sent
			// on the next turn without actually sending one.
			// Useful for debugging long conversations + powering
			// a future "your chat is getting long" UI nudge.
			r.Get("/conversations/{id}/context-info", h.Conversation.GetContextInfo)

			// FIX33 A-W7 — manual compaction (Claude's /compact +
			// instructions): summarise everything up to now into
			// <conversation_state> and drop it from the next turn.
			r.Post("/conversations/{id}/compact", h.Conversation.Compact)

			// Conversation sharing (authenticated half — the
			// public share-view endpoint is registered outside the
			// RequireAuth subgroup below).
			r.Post("/conversations/{id}/share", h.Share.Create)
			r.Get("/conversations/{id}/share", h.Share.GetForOwner)
			r.Delete("/conversations/{id}/share", h.Share.Revoke)

			// Branching: edit a user message + switch active branch.
			r.Post("/conversations/{id}/messages/{messageID}/edit", h.Message.Edit)
			r.Post("/conversations/{id}/messages/{messageID}/switch-branch", h.Message.SwitchBranch)

			// Voice (Whisper transcription + OpenAI TTS). Each
			// handler returns 503 when OPENAI_API_KEY is unset so
			// the front-end can degrade to browser-native APIs.
			if h.Voice != nil {
				r.Post("/voice/transcribe", h.Voice.Transcribe)
				r.Post("/voice/synthesize", h.Voice.Synthesize)
			}

			// Phase 4 — Smart Workspace routes.
			//   POST /files/{fileID}/inspect         — type-dispatching
			//                                          inspector
			//                                          (Document handler;
			//                                          xlsx/docx/pdf)
			//   POST /conversations/{convID}/excel/execute       — Session 1
			//   POST /conversations/{convID}/document/execute    — Session 2
			//                                                      (docx/pdf)
			//   GET  /conversations/{convID}/files/{fileID}/versions
			//
			// The dispatcher inspect route requires both handlers so
			// it can branch to whichever backing service knows the
			// file type. When Document is wired we use it; otherwise
			// fall back to the Excel-only handler so legacy stacks
			// that don't register Document keep working.
			if h.Document != nil {
				r.Post("/files/{fileID}/inspect", h.Document.Inspect)
				r.Post("/conversations/{convID}/document/execute", h.Document.Execute)
			} else if h.Excel != nil {
				r.Post("/files/{fileID}/inspect", h.Excel.Inspect)
			}
			if h.Excel != nil {
				r.Post("/conversations/{convID}/excel/execute", h.Excel.Execute)
				r.Get("/conversations/{convID}/files/{fileID}/versions", h.Excel.Versions)
			}

			// Phase 4 Session 5 — Artifact Workspace.
			//   GET  /conversations/{convID}/generated-files
			//   POST /conversations/{convID}/files/{fileID}/rollback
			//   GET  /conversations/{convID}/file-compare?v1=X&v2=Y
			// file-compare lives at the conversation root (not under
			// /files/) so chi's segment matcher doesn't fight the
			// existing /files/{fileID}/{action} patterns.
			if h.Workspace != nil {
				r.Get("/conversations/{convID}/generated-files", h.Workspace.ListGenerated)
				r.Post("/conversations/{convID}/files/{fileID}/rollback", h.Workspace.Rollback)
				r.Get("/conversations/{convID}/file-compare", h.Workspace.Compare)
			}

			// S6 — Past conversation search. Auth-gated; tenant +
			// user scoping happens inside SearchService.
			if h.Search != nil {
				r.Get("/search", h.Search.Search)
			}

			// FIX12 Item 3 — user skills. Auth-gated; every op is
			// scoped to the caller's (tenant, user) inside the repo.
			if h.Skills != nil {
				r.Get("/skills", h.Skills.List)
				r.Post("/skills", h.Skills.Create)
				r.Patch("/skills/{id}", h.Skills.SetEnabled)
				r.Delete("/skills/{id}", h.Skills.Delete)
			}

			// FIX13 — cloud scheduled tasks. Auth-gated; every op is
			// scoped to the caller's (tenant, user) inside the repo. The
			// owning identity is taken from the caller, never the body.
			if h.CloudTasks != nil {
				r.Get("/cloud-tasks", h.CloudTasks.List)
				r.Post("/cloud-tasks", h.CloudTasks.Create)
				r.Patch("/cloud-tasks/{id}", h.CloudTasks.SetEnabled)
				r.Delete("/cloud-tasks/{id}", h.CloudTasks.Delete)
				r.Get("/cloud-tasks/{id}/runs", h.CloudTasks.ListRuns)
			}

			// Phase 13 Part B — run_python code interpreter for the
			// desktop agent. Rate-limited like Send (a sandbox run is
			// expensive). ALWAYS registered so the desktop client/server
			// contract is stable; the handler answers 503 (not 404) when
			// the sandbox is disabled (SANDBOX_ENABLED=0).
			if h.Code != nil {
				r.With(sendRateLimit).Post("/code/run", h.Code.Run)
			}

			// S9 — Account data management. Export generates a JSON
			// snapshot of the user's memories + audit metadata; the
			// download endpoint streams it back; purge is the
			// right-to-erasure cascade (requires the literal
			// confirmation string in the body).
			if h.Account != nil {
				r.Post("/account/export-memories", h.Account.ExportMemories)
				r.Get("/account/exports/{id}/download", h.Account.DownloadExport)
				r.Delete("/account/purge-memories", h.Account.PurgeMemories)
			}

			// S1 — Personal Memory Core. Bulk-clear lives at
			// /memories/all so chi's segment matcher doesn't fight
			// the /memories/{id} patterns; pin + confirm + events
			// nest under /memories/{id}/...
			if h.Memory != nil {
				r.Get("/memories", h.Memory.List)
				r.Post("/memories", h.Memory.Create)
				r.Delete("/memories/all", h.Memory.ClearAll)
				// S8 — pending-count must register BEFORE the
				// /memories/{id} routes so chi's segment matcher
				// resolves "pending-count" as a literal rather
				// than trying to parse it as a uuid.
				r.Get("/memories/pending-count", h.Memory.PendingCount)
				r.Put("/memories/{id}", h.Memory.Update)
				r.Delete("/memories/{id}", h.Memory.Delete)
				r.Post("/memories/{id}/pin", h.Memory.Pin)
				r.Post("/memories/{id}/confirm", h.Memory.Confirm)
				r.Get("/memories/{id}/events", h.Memory.Events)
			}

			// Knowledge bases (RAG). All tenant-scoped via the
			// service layer.
			if h.Knowledge != nil {
				r.Get("/knowledge-bases", h.Knowledge.List)
				r.Post("/knowledge-bases", h.Knowledge.Create)
				r.Get("/knowledge-bases/{id}", h.Knowledge.Get)
				r.Patch("/knowledge-bases/{id}", h.Knowledge.Update)
				r.Delete("/knowledge-bases/{id}", h.Knowledge.Delete)
				r.Get("/knowledge-bases/{id}/documents", h.Knowledge.ListDocuments)
				r.Post("/knowledge-bases/{id}/documents", h.Knowledge.UploadDocument)
				r.Delete("/knowledge-bases/{id}/documents/{docId}", h.Knowledge.DeleteDocument)
				r.Post("/knowledge-bases/{id}/documents/{docId}/reindex", h.Knowledge.ReindexDocument)
				r.Post("/knowledge-bases/{id}/search", h.Knowledge.Search)
			}
		})

		// Public — no auth. Anyone with the share token can read.
		r.Get("/shared/{token}", h.Share.PublicGet)

		// ── Admin observability ─────────────────────────────────
		// Behind a shared bearer secret; intended for the admin-ui
		// proxied via its server-side route handler so the secret
		// never reaches the browser.
		r.Group(func(r chi.Router) {
			r.Use(middleware.RequireAdmin(cfg.AdminAPIKey))
			r.Get("/admin/analytics/usage", h.Admin.Usage)
			r.Get("/admin/conversations", h.Admin.Conversations)
			r.Get("/admin/conversations/{id}", h.Admin.ConversationDetail)
			r.Get("/admin/users", h.Admin.Users)
			r.Patch("/admin/users/{id}", h.Admin.UpdateUser)

			// Knowledge-base admin (cross-tenant). Read endpoints
			// query the pool directly; mutations route through the
			// KnowledgeService via the AdminKBSink injected from main.go.
			// S9 — aggregate memory stats per tenant. Counts only,
			// never content. Requires tenant_id query param.
			r.Get("/admin/memory-stats", h.Admin.MemoryStats)

			r.Get("/admin/knowledge-bases", h.Admin.ListKnowledgeBases)
			r.Get("/admin/knowledge-bases/{id}", h.Admin.KnowledgeBaseDetail)
			r.Delete("/admin/knowledge-bases/{id}", h.Admin.DeleteKnowledgeBase)
			r.Post("/admin/knowledge-bases/{id}/reindex", h.Admin.ReindexKnowledgeBase)

			// Retention & deletion program: per-tenant settings (floor-
			// validated, audited), the deletion-audit read path, and the
			// deletes-nothing dry-run preview.
			if h.RetentionAdmin != nil {
				r.Get("/admin/retention-settings", h.RetentionAdmin.GetSettings)
				r.Put("/admin/retention-settings", h.RetentionAdmin.PutSettings)
				r.Get("/admin/deletion-audit", h.RetentionAdmin.ListAudit)
				r.Get("/admin/retention-preview", h.RetentionAdmin.Preview)
			}
		})
	})

	return r
}
