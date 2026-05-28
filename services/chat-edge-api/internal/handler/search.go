// SearchHandler — HTTP surface for the S6 cross-conversation
// search.
//
// One route today: GET /api/v1/search?q=...&scope=...&limit=...
// Backs both the AI tool's underlying retrieval (same SearchService
// instance, different formatter) and the frontend's future "search
// my chats" surface. Auth-gated; every query is scoped to the
// caller's (userID, tenantID) inside SearchService.
package handler

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type SearchHandler struct {
	search *service.SearchService
}

func NewSearchHandler(s *service.SearchService) *SearchHandler {
	return &SearchHandler{search: s}
}

// Search runs SearchPastContext and serialises the result. Query
// param shape mirrors the AI tool's arguments so the same mental
// model works for human callers:
//   q     — keyword string (required; sanitised inside the service)
//   scope — all / messages / conversations / memories (default all)
//   limit — 1-20 (default 10)
//
// 200 + empty results on a no-hit query (rather than 404) so the
// frontend can render "no matches" without branching on the error
// path.
func (h *SearchHandler) Search(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	query := r.URL.Query().Get("q")
	if query == "" {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "q parameter required")
		return
	}
	scope := r.URL.Query().Get("scope")
	if scope == "" {
		scope = service.ScopeAll
	}
	limit := 10
	if v := r.URL.Query().Get("limit"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			limit = parsed
		}
	}
	if limit > 20 {
		limit = 20
	}

	results, err := h.search.SearchPastContext(
		r.Context(), caller.UserID, caller.TenantUUID, query, scope, limit,
	)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "search failed")
		return
	}
	if results == nil {
		// SearchPastContext returns nil on empty/sanitised-empty
		// queries; normalise to an empty slice so the wire shape
		// stays stable.
		results = []service.PastContextResult{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"query":   query,
		"scope":   scope,
		"results": results,
		"total":   len(results),
	})
}
