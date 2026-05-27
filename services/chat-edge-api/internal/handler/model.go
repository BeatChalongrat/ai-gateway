// Model handler — GET /api/v1/models.
//
// Phase 1 lists the config-driven model catalog with everything
// flagged “allowed: true“. Phase 2 session 2 enriches each
// entry with display metadata (description, context window,
// category, pricing) so the front-end can render a rich model
// picker without baking the metadata into the React bundle.
//
// Metadata sources:
//   - Capabilities & basic fields come from the configured catalog
//     (env AVAILABLE_MODELS or DefaultModels).
//   - Display metadata (description, context window, category,
//     pricing) comes from the static modelMetadata map below for
//     known IDs; unknown IDs fall back to "other"-category defaults
//     so the picker always has something to render.
package handler

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
)

type ModelHandler struct {
	cfg *config.Config
}

func NewModelHandler(cfg *config.Config) *ModelHandler {
	return &ModelHandler{cfg: cfg}
}

// modelResponse is the wire shape returned by GET /api/v1/models.
// Mirrors the front-end Model type in apps/web-chat/src/stores/modelStore.ts.
type modelResponse struct {
	ID              string   `json:"id"`
	Name            string   `json:"name"`
	Provider        string   `json:"provider"`
	Description     string   `json:"description"`
	ContextWindow   int      `json:"context_window"`
	Capabilities    []string `json:"capabilities"`
	Icon            string   `json:"icon"`
	Category        string   `json:"category"`
	MaxOutputTokens int      `json:"max_output_tokens,omitempty"`
	InputPricePerM  float64  `json:"input_price_per_1m,omitempty"`
	OutputPricePerM float64  `json:"output_price_per_1m,omitempty"`
	Allowed         bool     `json:"allowed"`
}

// modelMeta is the static display-metadata shape. The map below is
// looked up by canonical model id; the lookup falls back to provider
// prefix when the exact id isn't known so e.g. "gpt-4o-2024-11" still
// resolves to the openai/flagship bucket.
type modelMeta struct {
	Description     string
	ContextWindow   int
	Category        string // "flagship" | "fast" | "reasoning" | "specialized"
	MaxOutputTokens int
	InputPricePerM  float64
	OutputPricePerM float64
	Capabilities    []string
}

// modelMetadata is the static lookup of display metadata for known
// model ids. Capabilities listed here REPLACE the config-supplied
// capabilities — the config catalog only carries the bare "chat"
// flag in DefaultModels, so the rich list lives here.
var modelMetadata = map[string]modelMeta{
	// ── OpenAI ─────────────────────────────────────────────
	"gpt-4o": {
		Description:     "OpenAI's most capable multimodal flagship. Great for complex tasks, vision, and tool use.",
		ContextWindow:   128000,
		Category:        "flagship",
		MaxOutputTokens: 16384,
		InputPricePerM:  2.50,
		OutputPricePerM: 10.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"gpt-4o-mini": {
		Description:     "Cheaper, faster GPT-4o variant. Good default for everyday chat.",
		ContextWindow:   128000,
		Category:        "fast",
		MaxOutputTokens: 16384,
		InputPricePerM:  0.15,
		OutputPricePerM: 0.60,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"gpt-4-turbo": {
		Description:     "Prior-generation flagship. Strong reasoning, slower than 4o.",
		ContextWindow:   128000,
		Category:        "flagship",
		MaxOutputTokens: 4096,
		InputPricePerM:  10.00,
		OutputPricePerM: 30.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"o1": {
		Description:     "Reasoning-tuned model that thinks before responding. Best for hard math/code.",
		ContextWindow:   200000,
		Category:        "reasoning",
		MaxOutputTokens: 100000,
		InputPricePerM:  15.00,
		OutputPricePerM: 60.00,
		Capabilities:    []string{"streaming", "reasoning"},
	},
	"o3-mini": {
		Description:     "Affordable reasoning model. Great cost-to-quality ratio for analytical work.",
		ContextWindow:   200000,
		Category:        "reasoning",
		MaxOutputTokens: 100000,
		InputPricePerM:  1.10,
		OutputPricePerM: 4.40,
		Capabilities:    []string{"streaming", "reasoning"},
	},

	// ── Anthropic ──────────────────────────────────────────
	"claude-sonnet-4-20250514": {
		Description:     "Anthropic's balanced flagship. Strong code generation, careful reasoning.",
		ContextWindow:   200000,
		Category:        "flagship",
		MaxOutputTokens: 8192,
		InputPricePerM:  3.00,
		OutputPricePerM: 15.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"claude-3-5-sonnet-20241022": {
		Description:     "Claude 3.5 Sonnet — strong reasoning, vision, and tool use.",
		ContextWindow:   200000,
		Category:        "flagship",
		MaxOutputTokens: 8192,
		InputPricePerM:  3.00,
		OutputPricePerM: 15.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"claude-3-5-haiku-20241022": {
		Description:     "Anthropic's fastest model. Snappy replies for short turns.",
		ContextWindow:   200000,
		Category:        "fast",
		MaxOutputTokens: 8192,
		InputPricePerM:  0.80,
		OutputPricePerM: 4.00,
		Capabilities:    []string{"streaming", "function_calling"},
	},
	"claude-3-opus-20240229": {
		Description:     "Long-context deep-thinker. Best for sprawling documents and intricate reasoning.",
		ContextWindow:   200000,
		Category:        "reasoning",
		MaxOutputTokens: 4096,
		InputPricePerM:  15.00,
		OutputPricePerM: 75.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},

	// ── Google ─────────────────────────────────────────────
	"gemini-1.5-pro": {
		Description:     "Google's flagship multimodal. Huge context, native vision and audio.",
		ContextWindow:   2000000,
		Category:        "flagship",
		MaxOutputTokens: 8192,
		InputPricePerM:  1.25,
		OutputPricePerM: 5.00,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
	"gemini-1.5-flash": {
		Description:     "Fast Gemini variant. Tuned for low-latency calls and big context.",
		ContextWindow:   1000000,
		Category:        "fast",
		MaxOutputTokens: 8192,
		InputPricePerM:  0.075,
		OutputPricePerM: 0.30,
		Capabilities:    []string{"streaming", "vision", "function_calling"},
	},
}

// providerDefaults is the fallback category and rough context window
// per provider when the exact id isn't in modelMetadata. Keeps the
// dropdown usable when the catalog adds a new model the front-end
// hasn't seen.
var providerDefaults = map[string]modelMeta{
	"openai":    {Description: "OpenAI chat model.", ContextWindow: 128000, Category: "other"},
	"anthropic": {Description: "Anthropic Claude model.", ContextWindow: 200000, Category: "other"},
	"google":    {Description: "Google Gemini model.", ContextWindow: 128000, Category: "other"},
	"meta":      {Description: "Meta Llama model.", ContextWindow: 128000, Category: "other"},
	"mistral":   {Description: "Mistral model.", ContextWindow: 128000, Category: "other"},
}

func (h *ModelHandler) List(w http.ResponseWriter, r *http.Request) {
	models := h.cfg.Models()
	out := make([]modelResponse, 0, len(models))
	for _, m := range models {
		meta := lookupMeta(m)
		caps := meta.Capabilities
		if len(caps) == 0 {
			caps = m.Capabilities
		}
		if len(caps) == 0 {
			caps = []string{"streaming"}
		}
		out = append(out, modelResponse{
			ID:              m.ID,
			Name:            m.Name,
			Provider:        m.Provider,
			Description:     meta.Description,
			ContextWindow:   meta.ContextWindow,
			Capabilities:    caps,
			Icon:            iconFor(m.Provider),
			Category:        meta.Category,
			MaxOutputTokens: meta.MaxOutputTokens,
			InputPricePerM:  meta.InputPricePerM,
			OutputPricePerM: meta.OutputPricePerM,
			Allowed:         true,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"models": out})
}

// lookupMeta returns rich display metadata for a configured model.
// Exact id wins; provider default backs off; generic "other" is the
// last resort. Always returns a non-zero “Category“ so the front-
// end's category bucketing never sees an empty string.
func lookupMeta(m config.ModelEntry) modelMeta {
	if meta, ok := modelMetadata[m.ID]; ok {
		return meta
	}
	if meta, ok := providerDefaults[strings.ToLower(m.Provider)]; ok {
		if meta.Category == "" {
			meta.Category = "other"
		}
		return meta
	}
	return modelMeta{
		Description:   m.Name + " is available in this workspace.",
		ContextWindow: 0,
		Category:      "other",
	}
}

// iconFor maps the wire “provider“ string to a stable icon slug the
// front-end's AssistantAvatar component recognises. Falls through to
// "generic" for unknown providers.
func iconFor(provider string) string {
	switch strings.ToLower(provider) {
	case "openai":
		return "openai"
	case "anthropic":
		return "anthropic"
	case "google":
		return "google"
	case "meta":
		return "meta"
	case "mistral":
		return "mistral"
	default:
		return "generic"
	}
}
