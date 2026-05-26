// HTTP route registration.
//
// Public routes (no auth) — health + auth/register + auth/login.
// Protected routes — everything else, gated by middleware.RequireAuth.
// All protected routes live under ``/api/v1`` so the path prefix
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
	Message      *handler.MessageHandler
	Model        *handler.ModelHandler
	Feedback     *handler.FeedbackHandler
	File         *handler.FileHandler
	Preferences  *handler.PreferencesHandler
	Share        *handler.ShareHandler
	Admin        *handler.AdminHandler
}

// New builds the full Chi router with all routes + middleware.
//
// ``provision`` is the user-shadow service the Portal-JWT auth
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

			// Cancel — interrupts the in-flight stream for a
			// conversation. Scoped at the conversation (one active
			// generation per conversation in Phase 1), no rate
			// limit (cancel is cheap + needs to always work to
			// stop a runaway stream).
			r.Post("/conversations/{id}/cancel", h.Message.Cancel)

			// Messages.
			r.Get("/conversations/{id}/messages", h.Message.List)

			// Send + Regenerate go through the rate-limit middleware
			// — both kick off an expensive Gateway round-trip.
			r.With(sendRateLimit).Post("/conversations/{id}/messages", h.Message.Send)
			r.With(sendRateLimit).Post(
				"/conversations/{id}/messages/{messageID}/regenerate",
				h.Message.Regenerate,
			)

			// Feedback.
			r.Post("/messages/{messageID}/feedback", h.Feedback.Create)

			// Models.
			r.Get("/models", h.Model.List)

			// File attachments. Upload + list scoped to a
			// conversation; download + delete go through the file
			// id so the URL doesn't leak conversation membership.
			r.Post("/conversations/{id}/files", h.File.Upload)
			r.Get("/conversations/{id}/files", h.File.List)
			r.Get("/files/{fileID}/download", h.File.Download)
			r.Delete("/files/{fileID}", h.File.Delete)

			// User preferences (custom instructions).
			r.Get("/user/preferences", h.Preferences.Get)
			r.Put("/user/preferences", h.Preferences.Put)

			// Conversation export.
			r.Get("/conversations/{id}/export", h.Conversation.Export)

			// Conversation sharing (authenticated half — the
			// public share-view endpoint is registered outside the
			// RequireAuth subgroup below).
			r.Post("/conversations/{id}/share", h.Share.Create)
			r.Get("/conversations/{id}/share", h.Share.GetForOwner)
			r.Delete("/conversations/{id}/share", h.Share.Revoke)

			// Branching: edit a user message + switch active branch.
			r.Post("/conversations/{id}/messages/{messageID}/edit", h.Message.Edit)
			r.Post("/conversations/{id}/messages/{messageID}/switch-branch", h.Message.SwitchBranch)
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
		})
	})

	return r
}
