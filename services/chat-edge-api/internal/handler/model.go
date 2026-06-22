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
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// modelCatalogTTL bounds how often chat-edge re-fetches the Gateway's
// model list. Short enough that a newly-discovered model (added via a
// provider key) shows up within a few minutes; long enough that the
// chat page load doesn't hammer the Gateway.
const modelCatalogTTL = 5 * time.Minute

type ModelHandler struct {
	cfg *config.Config
	// Optional. When set, the list endpoint serves the Gateway's live
	// model catalog (so operator-discovered models appear without a
	// redeploy). When nil, or on any fetch error, the handler falls
	// back to the static config catalog.
	gateway *service.GatewayClient

	mu       sync.RWMutex
	cached   []modelResponse
	cachedAt time.Time
}

func NewModelHandler(cfg *config.Config, gateway *service.GatewayClient) *ModelHandler {
	return &ModelHandler{cfg: cfg, gateway: gateway}
}

// reasoningCapability mirrors the Gateway's ReasoningCapability
// block (backend/app/schemas/public.py — T1). Surfaced on each
// known reasoning-capable model so the frontend can render the
// ReasoningToggle and the mode picker without an extra call.
//
// ``Modes`` lists every value the Gateway's ReasoningConfig.mode
// Literal accepts so a new mode added Gateway-side requires only
// a single-line change here.
type reasoningCapability struct {
	Supported        bool     `json:"supported"`
	Modes            []string `json:"modes"`
	DefaultMode      string   `json:"default_mode"`
	SummarySupported bool     `json:"summary_supported"`
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
	// T4 — Thinking Mode capability. ``nil`` when the model
	// does NOT support reasoning so the wire stays unchanged for
	// non-reasoning models. Populated for the known set in
	// ``reasoningCapableModels`` below.
	Reasoning *reasoningCapability `json:"reasoning,omitempty"`
}

// reasoningCapableModels is the known set of model ids whose
// Gateway-side row is flagged ``supports_reasoning=True`` by
// migration 045. Kept as a static map rather than fetching live
// from the Gateway so the model list stays a single in-process
// lookup (the existing /api/v1/models contract). Operators who
// onboard a new reasoning model in migration 045 should add the
// id here so the frontend toggle becomes visible.
var reasoningCapableModels = map[string]struct{}{
	// OpenAI o-series — reasoning_effort.
	"o1":                   {},
	"o3":                   {},
	"o3-mini":              {},
	"o3-2025-04-16":        {},
	"o3-mini-2025-01-31":   {},
	"o4-mini":              {},
	"o3-pro":               {},
	// Anthropic Claude 4 family — adaptive thinking.
	"claude-sonnet-4-20250514":   {},
	"claude-opus-4-20250514":     {},
	"claude-sonnet-4.5-20250514": {},
	// DeepSeek / Groq R1 distill — openai_compatible_reasoning.
	"deepseek-reasoner":              {},
	"deepseek-r1-distill-llama-70b": {},
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
		// vision removed 2026-06-12: OpenAI now rejects image_url on
		// gpt-4-turbo ("image_url is only supported by certain
		// models", verified live) — its image input was retired.
		Capabilities:    []string{"streaming", "function_calling"},
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
	out := h.catalog(r.Context())
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"models": out})
}

// catalog returns the model list to serve. It prefers the Gateway's
// live catalog (cached for modelCatalogTTL) so operator-discovered
// models surface without a redeploy, and falls back to the static
// config catalog when the Gateway is unreachable or returns nothing.
func (h *ModelHandler) catalog(ctx context.Context) []modelResponse {
	if h.gateway == nil {
		return h.configCatalog()
	}

	h.mu.RLock()
	if h.cached != nil && time.Since(h.cachedAt) < modelCatalogTTL {
		cached := h.cached
		h.mu.RUnlock()
		return cached
	}
	h.mu.RUnlock()

	gwModels, err := h.gateway.ListModels(ctx)
	if err != nil || len(gwModels) == 0 {
		if err != nil {
			slog.Warn("model catalog: gateway fetch failed, using config fallback", "err", err)
		}
		// Serve a stale cache if we have one; otherwise the config list.
		h.mu.RLock()
		stale := h.cached
		h.mu.RUnlock()
		if stale != nil {
			return stale
		}
		return h.configCatalog()
	}

	out := h.buildFromGateway(gwModels)
	h.mu.Lock()
	h.cached = out
	h.cachedAt = time.Now()
	h.mu.Unlock()
	return out
}

// buildFromGateway enriches the Gateway's minimal model entries (id +
// provider + optional reasoning block) with chat-edge's display
// metadata. Curated models keep their friendly names + rich metadata;
// discovered models fall back to a humanized name + provider-default
// metadata so the picker always renders something sensible. Reasoning
// capability is taken from the Gateway (DB-authoritative), so a newly
// onboarded reasoning model no longer needs a code change here.
func (h *ModelHandler) buildFromGateway(gw []service.GatewayModelInfo) []modelResponse {
	// Friendly names from the operator's curated catalog take priority
	// over a humanized id for models that appear in both.
	names := map[string]string{}
	for _, m := range h.cfg.Models() {
		names[m.ID] = m.Name
	}

	out := make([]modelResponse, 0, len(gw))
	for _, m := range gw {
		// The Gateway exposes an "auto" pseudo-model; the frontend
		// renders its own Auto row, so skip it here.
		if m.ID == "auto" {
			continue
		}
		name := names[m.ID]
		if name == "" {
			name = humanizeModelID(m.ID)
		}
		var rb *reasoningCapability
		if m.Reasoning != nil && m.Reasoning.Supported {
			modes := m.Reasoning.Modes
			if len(modes) == 0 {
				modes = []string{"off", "auto", "low", "medium", "high"}
			}
			defaultMode := m.Reasoning.DefaultMode
			if defaultMode == "" {
				defaultMode = "auto"
			}
			rb = &reasoningCapability{
				Supported:        true,
				Modes:            modes,
				DefaultMode:      defaultMode,
				SummarySupported: m.Reasoning.SummarySupported,
			}
		}
		// Phase C — hide DEDICATED image models from the chat picker:
		// they're the generate_image tool target, not chat-selectable.
		// Match by id too, not just the capability flag — the
		// discovered-openai candidate pool surfaces gpt-image-1.5 /
		// gpt-image-2 / chatgpt-image-latest WITHOUT image_generation set.
		if isImageOnlyModel(m.ID, m.Capabilities) {
			continue
		}
		resp := h.buildModelResponse(m.ID, name, m.Provider, rb)
		// Surface the image-via-tools flag so the frontend can gate the
		// image (🎨) composer button + show the image badge.
		if m.Capabilities != nil && m.Capabilities.ImageGenerationViaTools {
			resp.Capabilities = append(resp.Capabilities, "image_generation_via_tools")
		}
		// Surface the video-gen flag so the frontend can gate the 🎬
		// composer button (mirrors image_generation_via_tools).
		if m.Capabilities != nil && m.Capabilities.VideoGeneration {
			resp.Capabilities = append(resp.Capabilities, "video_generation")
		}
		out = append(out, resp)
	}
	return out
}

// isImageOnlyModel reports whether a model is a DEDICATED image-generation
// model (gpt-image-*, chatgpt-image-*, dall-e-*) — the generate_image tool
// target, not a chat model. Matches by id because the discovered-openai
// candidate pool can surface these without the gateway's image_generation
// capability flag. Hidden from the chat picker.
func isImageOnlyModel(id string, caps *service.GatewayModelCapabilities) bool {
	if caps != nil && caps.ImageGeneration {
		return true
	}
	return strings.HasPrefix(id, "gpt-image") ||
		strings.HasPrefix(id, "chatgpt-image") ||
		strings.HasPrefix(id, "dall-e")
}

// SupportsImageTools reports whether the model can call the generate_image
// tool — i.e. its catalog entry advertises function_calling or
// image_generation_via_tools. Phase D wires this into MessageService to
// decide whether to override a non-tool model for an image-generation turn.
// Auto / unknown ids return false so the router falls back to a known
// tool-capable model.
func (h *ModelHandler) SupportsImageTools(ctx context.Context, id string) bool {
	if id == "" {
		return false
	}
	for _, m := range h.catalog(ctx) {
		if m.ID != id {
			continue
		}
		for _, c := range m.Capabilities {
			if c == "image_generation_via_tools" || c == "function_calling" {
				return true
			}
		}
		return false
	}
	return false
}

// SupportsVision reports whether the model's catalog entry advertises
// the vision capability. Used by MessageService to decide whether
// generated images can be replayed into history as image_url content
// (Approach B) — unknown ids and the Auto sentinel return false so the
// flow safely falls back to the text annotation (Approach A) rather
// than sending image blocks a model might reject.
func (h *ModelHandler) SupportsVision(ctx context.Context, id string) bool {
	if id == "" {
		return false
	}
	for _, m := range h.catalog(ctx) {
		if m.ID != id {
			continue
		}
		for _, c := range m.Capabilities {
			if c == "vision" {
				return true
			}
		}
		return false
	}
	return false
}

// KnownWithoutTools reports whether ``id`` has a CURATED metadata entry
// that deliberately omits function_calling (e.g. o1, o3-mini).
// MessageService uses this to skip attaching the tools block so a
// mid-conversation switch to such a model doesn't send a ``tools``
// request param the provider may reject.
//
// Deliberately consults ONLY the static modelMetadata map, NOT the live
// catalog: gateway-discovered models carry no capability flags, so the
// dynamic catalog under-reports tool support (gpt-5.x etc. show no
// function_calling despite supporting it). Gating on the catalog would
// silently strip tools from those models. Unknown ids and the Auto
// sentinel return false (capability unknown → keep tools).
func (h *ModelHandler) KnownWithoutTools(_ context.Context, id string) bool {
	meta, ok := modelMetadata[id]
	if !ok {
		return false
	}
	for _, c := range meta.Capabilities {
		if c == "function_calling" {
			return false
		}
	}
	return true
}

// configCatalog is the static fallback path (Gateway unreachable). It
// preserves the pre-dynamic behaviour: the operator's curated catalog
// enriched with local metadata + the static reasoning-capable set.
func (h *ModelHandler) configCatalog() []modelResponse {
	models := h.cfg.Models()
	out := make([]modelResponse, 0, len(models))
	for _, m := range models {
		// T4: Gemini 2.5+ ids are versioned — match by prefix; other
		// reasoning-capable models match by exact id.
		_, reasoning := reasoningCapableModels[m.ID]
		if !reasoning && (strings.HasPrefix(m.ID, "gemini-2.5") || strings.HasPrefix(m.ID, "gemini-3")) {
			reasoning = true
		}
		var rb *reasoningCapability
		if reasoning {
			rb = &reasoningCapability{
				Supported:        true,
				Modes:            []string{"off", "auto", "low", "medium", "high"},
				DefaultMode:      "auto",
				SummarySupported: true,
			}
		}
		resp := h.buildModelResponse(m.ID, m.Name, m.Provider, rb)
		// Honour config-supplied capabilities when local metadata had none.
		if len(resp.Capabilities) == 1 && resp.Capabilities[0] == "streaming" && len(m.Capabilities) > 0 {
			resp.Capabilities = m.Capabilities
		}
		out = append(out, resp)
	}
	return out
}

// buildModelResponse assembles one wire entry from an id/name/provider
// plus an already-resolved reasoning block. Shared by the dynamic
// (Gateway) and static (config) paths so display metadata lookup +
// capability defaults stay identical.
func (h *ModelHandler) buildModelResponse(id, name, provider string, rb *reasoningCapability) modelResponse {
	meta := lookupMeta(config.ModelEntry{ID: id, Name: name, Provider: provider})
	caps := meta.Capabilities
	if len(caps) == 0 {
		caps = []string{"streaming"}
	}
	return modelResponse{
		ID:              id,
		Name:            name,
		Provider:        provider,
		Description:     meta.Description,
		ContextWindow:   meta.ContextWindow,
		Capabilities:    caps,
		Icon:            iconFor(provider),
		Category:        meta.Category,
		MaxOutputTokens: meta.MaxOutputTokens,
		InputPricePerM:  meta.InputPricePerM,
		OutputPricePerM: meta.OutputPricePerM,
		Allowed:         true,
		Reasoning:       rb,
	}
}

// humanizeModelID turns a raw model id into a presentable name for
// discovered models that aren't in the curated catalog. Drops any
// "provider/" prefix (OpenRouter ids), splits on - / _, and upper-cases
// a few well-known acronym tokens. Imperfect but readable: e.g.
// "gpt-4.1-mini" → "GPT 4.1 Mini", "anthropic/claude-3-opus" →
// "Claude 3 Opus".
func humanizeModelID(id string) string {
	s := id
	if i := strings.LastIndex(s, "/"); i >= 0 {
		s = s[i+1:]
	}
	s = strings.ReplaceAll(s, "_", " ")
	s = strings.ReplaceAll(s, "-", " ")
	parts := strings.Fields(s)
	for i, p := range parts {
		switch strings.ToLower(p) {
		case "gpt":
			parts[i] = "GPT"
		case "ai":
			parts[i] = "AI"
		case "llm":
			parts[i] = "LLM"
		default:
			if len(p) > 0 {
				parts[i] = strings.ToUpper(p[:1]) + p[1:]
			}
		}
	}
	if len(parts) == 0 {
		return id
	}
	return strings.Join(parts, " ")
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
