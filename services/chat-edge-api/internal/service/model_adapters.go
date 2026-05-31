package service

import "strings"

// model_adapters.go — Layer 8 of the SRAN Prompt Compiler.
//
// API ≠ Platform. SRAN talks to every provider over its raw API, so the
// model that answers carries NO vendor persona — only the base weights'
// behaviour. Each base family has its own quirks (GPT mixes ครับ/ค่ะ,
// Claude under-calls tools, Gemini over-lists, Llama needs explicit
// formatting rules). This layer injects a short, model-specific
// <model_guidance> snippet that nudges the chosen model toward the
// behaviour the other layers assume — without changing those layers.
//
// The snippet is selected by PROVIDER (gpt-4o and gpt-4o-mini share the
// OpenAI adapter). When the provider isn't known to the caller it is
// derived from the model id prefix via deriveProvider.

const openaiAdapter = `<model_guidance>
- Structure responses with clear sections when content is long.
- For Thai: be extra careful with ครับ/ค่ะ consistency — GPT models tend
  to mix these. Pick one and never switch within a conversation.
- When using tools, you may call multiple tools in one turn if needed.
</model_guidance>`

const anthropicAdapter = `<model_guidance>
- Use XML tags to structure complex outputs when helpful.
- You are naturally cautious about tool calling — override that instinct
  when the user's request clearly warrants a tool call (especially
  save_memory). Do not wait to be asked.
- Think step-by-step for complex tasks.
</model_guidance>`

const googleAdapter = `<model_guidance>
- Keep responses focused and concise — avoid overly long answers.
- For Thai: ensure natural sentence structure, avoid direct translation
  patterns.
- When the user asks for a list, limit it to the most relevant items.
</model_guidance>`

const deepseekAdapter = `<model_guidance>
- Follow the language and formatting rules strictly.
- For Thai: use natural Thai phrasing, avoid Chinese-influenced patterns.
- For coding tasks: provide complete, runnable examples.
</model_guidance>`

const groqAdapter = `<model_guidance>
- Follow all formatting and language rules strictly — Llama models need
  explicit guidance.
- Keep responses well-structured with clear formatting.
- For Thai: be very careful with tone and politeness levels.
</model_guidance>`

const mistralAdapter = `<model_guidance>
- Provide clear, structured responses.
- For Thai: ensure grammatically correct Thai output.
- Follow tool usage instructions carefully.
</model_guidance>`

const cohereAdapter = `<model_guidance>
- Structure responses clearly with good formatting.
- For Thai: follow the language rules strictly.
- When citing information, be specific about sources.
</model_guidance>`

const qwenAdapter = `<model_guidance>
- Follow language rules strictly, especially for Thai responses.
- Keep formatting clean and consistent.
- For translation tasks: prioritize natural target language.
</model_guidance>`

const xaiAdapter = `<model_guidance>
- Follow the tone rules — keep it professional, avoid a casual/edgy style.
- For Thai: maintain a polite, warm tone.
- Structure responses clearly.
</model_guidance>`

const zaiAdapter = `<model_guidance>
- Follow all language and formatting rules strictly.
- For Thai: use natural phrasing, avoid literal translations.
- Keep responses concise and focused.
</model_guidance>`

const kimiAdapter = `<model_guidance>
- Follow Thai language rules carefully.
- Keep responses focused and well-structured.
- Avoid overly verbose explanations.
</model_guidance>`

const minimaxAdapter = `<model_guidance>
- Follow all language rules strictly.
- Structure responses clearly.
- For Thai: ensure natural, polite language.
</model_guidance>`

const perplexityAdapter = `<model_guidance>
- Follow formatting and language rules strictly.
- Keep responses well-organized.
- For Thai: maintain a consistent tone.
</model_guidance>`

const fallbackAdapter = `<model_guidance>
- Follow all language, tone, and formatting rules strictly.
- When in doubt, keep responses concise and well-structured.
- For Thai: use polite, natural language with consistent ครับ/ค่ะ.
</model_guidance>`

// modelAdapters maps a normalised provider id to its guidance snippet.
// OpenRouter is intentionally absent — it is a router, not a model
// family, so GetModelAdapter resolves the concrete family from the
// model id prefix before this map is consulted.
var modelAdapters = map[string]string{
	"openai":     openaiAdapter,
	"anthropic":  anthropicAdapter,
	"google":     googleAdapter,
	"deepseek":   deepseekAdapter,
	"groq":       groqAdapter,
	"mistral":    mistralAdapter,
	"cohere":     cohereAdapter,
	"qwen":       qwenAdapter,
	"xai":        xaiAdapter,
	"zai":        zaiAdapter,
	"kimi":       kimiAdapter,
	"minimax":    minimaxAdapter,
	"perplexity": perplexityAdapter,
}

// GetModelAdapter returns the <model_guidance> snippet for the given
// model/provider. When provider is empty (the common case in chat-edge,
// which doesn't carry the gateway's provider field) it is derived from
// the model id. Unknown families fall back to fallbackAdapter so the
// layer is never empty for a real model.
func GetModelAdapter(model, provider string) string {
	p := strings.ToLower(strings.TrimSpace(provider))
	if p == "" {
		p = deriveProvider(model)
	}
	// OpenRouter routes to a concrete family; resolve from the id prefix
	// (e.g. "anthropic/claude-3.5-sonnet" -> anthropic).
	if p == "openrouter" {
		p = parseOpenRouterProvider(model)
	}
	if adapter, ok := modelAdapters[p]; ok {
		return adapter
	}
	return fallbackAdapter
}

// parseOpenRouterProvider maps an OpenRouter "vendor/model" id to one of
// our adapter keys. The vendor segment before the slash is authoritative
// when we recognise it; meta-llama maps onto the groq (Llama) adapter
// since the guidance is family-, not host-, specific.
func parseOpenRouterProvider(model string) string {
	m := strings.ToLower(model)
	switch {
	case strings.HasPrefix(m, "anthropic/"):
		return "anthropic"
	case strings.HasPrefix(m, "openai/"):
		return "openai"
	case strings.HasPrefix(m, "google/"):
		return "google"
	case strings.HasPrefix(m, "meta-llama/"), strings.HasPrefix(m, "meta/"):
		return "groq"
	case strings.HasPrefix(m, "deepseek/"):
		return "deepseek"
	case strings.HasPrefix(m, "mistralai/"), strings.HasPrefix(m, "mistral/"):
		return "mistral"
	case strings.HasPrefix(m, "cohere/"):
		return "cohere"
	case strings.HasPrefix(m, "qwen/"):
		return "qwen"
	case strings.HasPrefix(m, "x-ai/"), strings.HasPrefix(m, "xai/"):
		return "xai"
	case strings.HasPrefix(m, "perplexity/"):
		return "perplexity"
	case strings.HasPrefix(m, "moonshotai/"), strings.HasPrefix(m, "moonshot/"):
		return "kimi"
	case strings.HasPrefix(m, "minimax/"):
		return "minimax"
	case strings.HasPrefix(m, "01-ai/"), strings.HasPrefix(m, "zhipuai/"), strings.HasPrefix(m, "z-ai/"):
		return "zai"
	default:
		return "" // -> fallbackAdapter
	}
}

// deriveProvider infers the provider family from a raw model id when the
// caller doesn't already know it. chat-edge resolves models to concrete
// ids (model_resolver.go) but does NOT thread the gateway's provider
// field down to prompt assembly, so we recover it here from well-known
// id shapes. Returns "" for unrecognised ids (-> fallbackAdapter).
func deriveProvider(model string) string {
	m := strings.ToLower(strings.TrimSpace(model))
	if m == "" {
		return ""
	}
	// OpenRouter ids are "vendor/model"; signal the caller to delegate.
	if strings.Contains(m, "/") {
		return "openrouter"
	}
	switch {
	case strings.HasPrefix(m, "gpt-"), strings.HasPrefix(m, "o1"),
		strings.HasPrefix(m, "o3"), strings.HasPrefix(m, "o4"),
		strings.HasPrefix(m, "chatgpt"), strings.HasPrefix(m, "text-"):
		return "openai"
	case strings.HasPrefix(m, "claude"):
		return "anthropic"
	case strings.HasPrefix(m, "gemini"), strings.HasPrefix(m, "palm"):
		return "google"
	case strings.HasPrefix(m, "deepseek"):
		return "deepseek"
	case strings.HasPrefix(m, "llama"), strings.Contains(m, "-llama"),
		strings.HasPrefix(m, "mixtral"), strings.HasPrefix(m, "gemma"):
		// Open-weight families served by Groq in this deployment.
		return "groq"
	case strings.HasPrefix(m, "mistral"), strings.HasPrefix(m, "ministral"),
		strings.HasPrefix(m, "codestral"):
		return "mistral"
	case strings.HasPrefix(m, "command"), strings.HasPrefix(m, "cohere"):
		return "cohere"
	case strings.HasPrefix(m, "qwen"):
		return "qwen"
	case strings.HasPrefix(m, "grok"):
		return "xai"
	case strings.HasPrefix(m, "yi-"), strings.HasPrefix(m, "glm-"):
		return "zai"
	case strings.HasPrefix(m, "kimi"), strings.HasPrefix(m, "moonshot"):
		return "kimi"
	case strings.HasPrefix(m, "abab"), strings.HasPrefix(m, "minimax"):
		return "minimax"
	case strings.HasPrefix(m, "sonar"), strings.HasPrefix(m, "perplexity"):
		return "perplexity"
	default:
		return ""
	}
}
