// Model handler — GET /api/v1/models.
//
// Phase 1 lists the config-driven model catalog with everything
// flagged ``allowed: true``. Per-tenant gating (admin can disable
// specific models for a tenant) is a Phase 1.5 / Phase 2 feature;
// the response shape already carries ``allowed`` so flipping that
// to per-tenant later is a non-breaking change.
package handler

import (
	"encoding/json"
	"net/http"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
)

type ModelHandler struct {
	cfg *config.Config
}

func NewModelHandler(cfg *config.Config) *ModelHandler {
	return &ModelHandler{cfg: cfg}
}

// modelResponse is the wire shape. Re-uses ``ModelEntry`` for the
// core fields and adds the per-tenant ``allowed`` flag.
type modelResponse struct {
	ID           string   `json:"id"`
	Name         string   `json:"name"`
	Provider     string   `json:"provider"`
	Capabilities []string `json:"capabilities"`
	Allowed      bool     `json:"allowed"`
}

func (h *ModelHandler) List(w http.ResponseWriter, r *http.Request) {
	models := h.cfg.Models()
	out := make([]modelResponse, 0, len(models))
	for _, m := range models {
		out = append(out, modelResponse{
			ID:           m.ID,
			Name:         m.Name,
			Provider:     m.Provider,
			Capabilities: m.Capabilities,
			Allowed:      true,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"models": out})
}
