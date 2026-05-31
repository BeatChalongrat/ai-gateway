package service

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// prompt_compiler.go — the SRAN Prompt Compiler.
//
// API ≠ Platform. SRAN consumes every provider over its raw API, so the
// model that answers carries no vendor persona — the entire SRAN Chat
// persona has to be assembled here, on every request. This file replaces
// the old flat `buildSystemPrefixWithSources` concatenation with an
// explicit 13-layer pipeline, each layer a single-responsibility method:
//
//	 1. Global Identity        (always)
//	 2. Date / Time            (always)
//	 3. Language / Tone Rules  (always)
//	 4. Action Rules           (always)
//	 5. Capability Statement   (always)
//	 6. Safety / Refusal Style (always)
//	 7. Tool Policy            (always; content varies by active tools)
//	 8. Model/Provider Adapter (varies by model)
//	 9. Tenant Settings        (when a TenantPromptConfig is supplied)
//	10. User Custom Instructions (when set)
//	11. Memory Policy + Saved Memory (when memory enabled) — emits sources
//	12. Conversation State     (when present)            — emits sources
//	13. RAG / File Context     (when present)
//
// Each layer is a pure-ish function: given the CompileContext it returns
// a string (or ""), with no side effect beyond the source attribution
// that layers 11/12 already performed inside their respective services.
// Empty layers are filtered out before the join, so order is fixed but
// presence is conditional.

// bangkokZone is a fixed UTC+7 location used for the Date/Time layer. A
// fixed zone (not time.LoadLocation) keeps the binary independent of the
// OS tzdata package — Thailand has no DST so the offset is constant.
var bangkokZone = time.FixedZone("Asia/Bangkok", 7*60*60)

// TenantPromptConfig carries tenant-level persona overrides (Layer 9).
// Every field is optional; a zero-value (or nil) config makes Layer 9
// emit nothing and all the defaults apply. The struct exists now so the
// layer is wired and testable; the DB table + admin UI that populate it
// land in a later phase.
type TenantPromptConfig struct {
	CustomIdentity  string // replaces the default identity sentence
	BrandName       string // brand shown instead of "SRAN Chat"
	ToneOverride    string // "formal" / "casual" / "professional"
	AdditionalRules string // free-form tenant rules appended verbatim
	DisabledCapabs  string // human note of capabilities to suppress
}

// IsZero reports whether the config carries no overrides — used by the
// tenant layer to skip itself entirely.
func (c *TenantPromptConfig) IsZero() bool {
	if c == nil {
		return true
	}
	return strings.TrimSpace(c.CustomIdentity) == "" &&
		strings.TrimSpace(c.BrandName) == "" &&
		strings.TrimSpace(c.ToneOverride) == "" &&
		strings.TrimSpace(c.AdditionalRules) == "" &&
		strings.TrimSpace(c.DisabledCapabs) == ""
}

// CompileContext is the full input to a single Build. Identity/IDs +
// the resolved model drive the static and per-model layers; the
// pre-built RAG/file/excel/doc/pptx strings carry the Layer-13 context
// that the MessageService renders (it owns the file/KB repositories the
// compiler deliberately does not).
type CompileContext struct {
	Model    string // resolved (concrete) model id
	Provider string // optional; derived from Model when empty

	Conv     *model.Conversation
	TenantID uuid.UUID
	UserID   uuid.UUID

	CurrentTime  time.Time // present moment for the Date/Time layer
	TenantConfig *TenantPromptConfig
	ActiveTools  []string // tool names active for this request

	// Layer 13 — pre-rendered by the caller (needs file/KB repos).
	RAGContext   string
	FileContext  string
	ExcelContext string
	DocContext   string
	PptxContext  string
}

// PromptCompiler assembles the system prompt. It holds only the
// persona-shaping services it calls directly (layers 10–12); the
// per-file/RAG context (layer 13) is pre-built and passed in via the
// CompileContext. Construct it with the same services the MessageService
// already wires — see newPromptCompiler.
type PromptCompiler struct {
	prefs  PreferencesReader
	memory MemoryReader
	state  *ConversationStateService
}

// newPromptCompiler builds a compiler from the (possibly nil) services
// the MessageService holds. Nil services make the corresponding layers
// no-ops, matching the legacy behaviour where an unwired service simply
// contributed no block.
func newPromptCompiler(
	prefs PreferencesReader,
	memory MemoryReader,
	state *ConversationStateService,
) *PromptCompiler {
	return &PromptCompiler{prefs: prefs, memory: memory, state: state}
}

// Build assembles the 13 layers in order and returns the joined system
// prompt plus the ContextSource list contributed by the memory + state
// layers (Layer 11/12) so the S7 attribution table stays populated.
func (pc *PromptCompiler) Build(ctx context.Context, in CompileContext) (string, []ContextSource) {
	parts := make([]string, 0, 16)
	sources := make([]ContextSource, 0, 8)

	add := func(s string) {
		if strings.TrimSpace(s) != "" {
			parts = append(parts, s)
		}
	}

	// Layers 1–9: static identity/behaviour + per-model + tenant.
	add(pc.globalIdentity(in))     // 1
	add(pc.dateTime(in))           // 2
	add(pc.languageRules(in))      // 3
	add(pc.actionRules(in))        // 4
	add(pc.capabilityStatement(in))// 5
	add(pc.safetyRules(in))        // 6
	add(pc.toolPolicy(in))         // 7
	add(pc.modelAdapter(in))       // 8
	add(pc.tenantSettings(in))     // 9

	// Layer 10: user custom instructions.
	add(pc.userCustomInstructions(ctx, in))

	// Layer 11: memory policy + saved memory (emits sources).
	memBlock, memSrcs := pc.memoryBlock(ctx, in)
	add(memBlock)
	sources = append(sources, memSrcs...)

	// Layer 12: conversation state (emits sources).
	stateBlock, stateSrcs := pc.conversationState(ctx, in)
	add(stateBlock)
	sources = append(sources, stateSrcs...)

	// Layer 13: RAG / file / artifact context.
	add(pc.ragAndFileContext(in))

	return strings.Join(parts, "\n\n"), sources
}

// ---------------------------------------------------------------------------
// Layer 1 — Global Identity (always)
// ---------------------------------------------------------------------------

const identityBlock = `<identity>
You are SRAN Chat — an AI assistant built by SRAN (sran.ai).
You are helpful, direct, and action-oriented. You provide thoughtful,
accurate responses while being concise.
You do not pretend to be the underlying model (GPT, Claude, Gemini, etc.) — you are SRAN Chat, powered by AI.
If asked who you are, say "SRAN Chat" — not the model name.
If asked what model powers you, you can mention it honestly.
</identity>`

func (pc *PromptCompiler) globalIdentity(in CompileContext) string {
	// Tenant branding override (Layer 9 supplies the values; identity is
	// rendered here so the brand name lands in the very first block).
	tc := in.TenantConfig
	if tc != nil {
		if id := strings.TrimSpace(tc.CustomIdentity); id != "" {
			return "<identity>\n" + id + "\n</identity>"
		}
		if brand := strings.TrimSpace(tc.BrandName); brand != "" {
			return strings.Replace(identityBlock, "SRAN Chat", brand, 2)
		}
	}
	return identityBlock
}

// ---------------------------------------------------------------------------
// Layer 2 — Date / Time (always)
// ---------------------------------------------------------------------------

// dateTime renders the "what is today" block. Both an ISO date
// (machine-clear) and a human-readable form are included so the model
// parses it unambiguously; the time anchors "this morning"/"tonight"
// style questions. Without this the model defaults to its training
// cutoff and answers e.g. "2024" in 2026.
func (pc *PromptCompiler) dateTime(in CompileContext) string {
	now := in.CurrentTime
	if now.IsZero() {
		now = time.Now()
	}
	now = now.In(bangkokZone)
	return fmt.Sprintf(
		"Current date and time: %s (%s) %s, Asia/Bangkok (UTC+7). "+
			"Treat this as the present moment when answering anything "+
			"time-relative (today, this year, recent, latest, etc.).",
		now.Format("2006-01-02"),
		now.Format("Monday"),
		now.Format("15:04"),
	)
}

// ---------------------------------------------------------------------------
// Layer 3 — Language / Tone Rules (always)
// ---------------------------------------------------------------------------

const languageRulesBlock = `<language_rules>
- Always respond in the same language the user is using. If they write
  Thai, respond in Thai. If English, respond in English. If they mix,
  follow the dominant language.
- For Thai responses: choose either ครับ (masculine) or ค่ะ/คะ (feminine)
  and use it consistently throughout the ENTIRE conversation. Never mix
  ครับ and ค่ะ in the same message or conversation. Default to ครับ unless
  the user indicates otherwise.
- Keep a warm, professional tone. Not too formal, not too casual.
- Avoid unnecessary filler phrases. Be direct but polite.
- Do not repeat the user's question back to them before answering.
</language_rules>`

func (pc *PromptCompiler) languageRules(in CompileContext) string {
	block := languageRulesBlock
	// Tenant tone override appends a single steering line so the default
	// "warm, professional" guidance still applies as the floor.
	if tc := in.TenantConfig; tc != nil {
		if tone := strings.TrimSpace(tc.ToneOverride); tone != "" {
			block += "\n<tone_override>\nPreferred tone for this deployment: " +
				tone + ". Apply it consistently.\n</tone_override>"
		}
	}
	return block
}

// ---------------------------------------------------------------------------
// Layer 4 — Action Rules (always)
// ---------------------------------------------------------------------------

const actionRulesBlock = `<action_rules>
- When the user asks you to decide, choose, or pick something — make your
  best choice and proceed immediately. Do NOT ask "which one do you
  prefer?" or seek confirmation.
  Phrases like "เลือกมาเลย", "เลือกให้", "pick for me", "you decide",
  "อะไรก็ได้" mean: choose and act NOW.
- Do not ask more than one clarifying question per response. If you need
  info, ask the single most important question.
- If the user gives a vague request, make reasonable assumptions, act on
  them, and briefly mention what you assumed.
- Prefer doing over asking. Prefer answering over disclaiming.
- If you can partially fulfill a request, do the partial work and explain
  what's missing — don't refuse entirely.
</action_rules>`

func (pc *PromptCompiler) actionRules(in CompileContext) string {
	return actionRulesBlock
}

// ---------------------------------------------------------------------------
// Layer 5 — Capability Statement (always)
// ---------------------------------------------------------------------------

const capabilityBlock = `<capabilities>
What you CAN do:
- Conversations, analysis, writing, coding help, translation,
  summarization, brainstorming, math, explanations
- Remember user preferences and facts across conversations (via memory tools)
- Search the user's past conversations
- Generate and edit files: Excel (.xlsx), Word (.docx), PDF,
  PowerPoint (.pptx) — when asked or when a file is uploaded
- Create charts and data visualizations INSIDE Excel and PowerPoint files
  (bar, line, pie, area, scatter)
- Process and analyze uploaded files (images, documents, spreadsheets, PDFs)

What you CANNOT do — be honest about these, never pretend you can:
- Display charts, graphs, or interactive visualizations inline in the chat
  message — only inside downloadable files
- Execute or run code
- Search the internet or access external URLs/websites
- Generate, create, or edit images
- Access real-time data (stock prices, live scores, current weather)
- Send emails, make API calls, or interact with external services
- Access the user's device, camera, or microphone
</capabilities>`

func (pc *PromptCompiler) capabilityStatement(in CompileContext) string {
	block := capabilityBlock
	if tc := in.TenantConfig; tc != nil {
		if dis := strings.TrimSpace(tc.DisabledCapabs); dis != "" {
			block += "\n<disabled_capabilities>\nThe following capabilities are " +
				"disabled for this deployment — do not offer or use them: " +
				dis + ".\n</disabled_capabilities>"
		}
	}
	return block
}

// ---------------------------------------------------------------------------
// Layer 6 — Safety / Refusal Style (always)
// ---------------------------------------------------------------------------

const safetyBlock = `<safety>
- If you cannot or should not do something, explain briefly and suggest
  what you CAN do instead. Never just say "I can't do that" without
  offering an alternative.
- Do not generate harmful, illegal, or unethical content.
- Do not make up facts or data. If you're not sure, say so.
- Do not pretend to access URLs, run code, or use capabilities you don't
  have. If the user asks, redirect to what you CAN do.
- Protect user privacy — do not repeat sensitive information (passwords,
  API keys, personal IDs) the user shares. If they share such info,
  advise them to keep it private.
- For medical, legal, or financial topics: provide helpful information but
  note you're not a professional in those fields.
</safety>`

func (pc *PromptCompiler) safetyRules(in CompileContext) string {
	return safetyBlock
}

// ---------------------------------------------------------------------------
// Layer 7 — Tool Policy (always; content varies by active tools)
// ---------------------------------------------------------------------------

// toolDescriptions maps a tool name to its policy paragraph. Only tools
// active for THIS request (gated upstream by MemoryEnabled etc.) get a
// paragraph, so a memory-off chat doesn't advertise save_memory. This
// layer also subsumes the old standalone MemoryPolicyBlock — the
// save_memory paragraph carries the proactive-save directive.
var toolDescriptions = map[string]string{
	"save_memory": "- save_memory: Save important facts about the user (name, role, " +
		"location, team, preferences, standing instructions, ongoing projects). " +
		"Save proactively the moment you notice these — do NOT wait for the user " +
		"to say \"remember\" or \"จำไว้\", and do not ask permission. Save silently; " +
		"do not announce the save. Skip transient info (weather, mood, one-off " +
		"tasks) and never save secrets (passwords, API keys, IDs).",
	"list_memories": "- list_memories: Check what you already know about the user. " +
		"Use it before asking something you might already have on file.",
	"search_past_conversations": "- search_past_conversations: Search the user's chat " +
		"history. Use it when they reference earlier conversations (\"เมื่อวานเราคุยอะไร\", " +
		"\"remember when we discussed...\").",
	"update_conversation_state": "- update_conversation_state: Track the goal and " +
		"progress of the current conversation as it evolves.",
}

// toolPolicyOrder fixes the paragraph order regardless of how ActiveTools
// is sorted by the caller.
var toolPolicyOrder = []string{
	"save_memory", "list_memories", "search_past_conversations", "update_conversation_state",
}

func (pc *PromptCompiler) toolPolicy(in CompileContext) string {
	active := make(map[string]bool, len(in.ActiveTools))
	for _, t := range in.ActiveTools {
		active[t] = true
	}
	var body []string
	for _, name := range toolPolicyOrder {
		if active[name] {
			if desc, ok := toolDescriptions[name]; ok {
				body = append(body, desc)
			}
		}
	}
	if len(body) == 0 {
		// No tools active for this request — emit nothing rather than an
		// empty <tool_policy> shell.
		return ""
	}
	var b strings.Builder
	b.WriteString("<tool_policy>\n")
	b.WriteString("You have access to the following tools. Use them proactively when " +
		"appropriate — do not wait for the user to ask:\n\n")
	b.WriteString(strings.Join(body, "\n"))
	b.WriteString("\n\nDo NOT mention tool names to the user. Just use them naturally.\n")
	b.WriteString("If a tool call fails, continue the conversation gracefully.\n")
	b.WriteString("</tool_policy>")
	return b.String()
}

// ---------------------------------------------------------------------------
// Layer 8 — Model / Provider Adapter (varies by model)
// ---------------------------------------------------------------------------

func (pc *PromptCompiler) modelAdapter(in CompileContext) string {
	return GetModelAdapter(in.Model, in.Provider)
}

// ---------------------------------------------------------------------------
// Layer 9 — Tenant Settings (when a TenantPromptConfig is supplied)
// ---------------------------------------------------------------------------

// tenantSettings emits only the tenant's free-form AdditionalRules here;
// identity/tone/disabled-capability overrides are folded into layers 1,
// 3 and 5 respectively so they shape the relevant block in place. When
// the config is zero this layer (and those folds) contribute nothing.
func (pc *PromptCompiler) tenantSettings(in CompileContext) string {
	tc := in.TenantConfig
	if tc.IsZero() {
		return ""
	}
	rules := strings.TrimSpace(tc.AdditionalRules)
	if rules == "" {
		return ""
	}
	return "<tenant_rules>\n" + rules + "\n</tenant_rules>"
}

// ---------------------------------------------------------------------------
// Layer 10 — User Custom Instructions (when set)
// ---------------------------------------------------------------------------

func (pc *PromptCompiler) userCustomInstructions(ctx context.Context, in CompileContext) string {
	if pc.prefs == nil {
		return ""
	}
	return pc.prefs.BuildInstructionsBlock(ctx, in.TenantID, in.UserID)
}

// ---------------------------------------------------------------------------
// Layer 11 — Memory Policy + Saved Memory (emits sources)
// ---------------------------------------------------------------------------

// memoryBlock injects the saved-memory facts when memory is enabled. The
// proactive-save POLICY now lives in the tool policy (Layer 7), so this
// layer only surfaces what's already on file. Returns the per-memory
// ContextSource list for S7 attribution.
func (pc *PromptCompiler) memoryBlock(ctx context.Context, in CompileContext) (string, []ContextSource) {
	if pc.memory == nil || in.Conv == nil || !in.Conv.MemoryEnabled {
		return "", nil
	}
	return pc.memory.BuildMemoryContextWithSources(ctx, in.UserID, in.TenantID)
}

// ---------------------------------------------------------------------------
// Layer 12 — Conversation State (emits sources)
// ---------------------------------------------------------------------------

func (pc *PromptCompiler) conversationState(ctx context.Context, in CompileContext) (string, []ContextSource) {
	if pc.state == nil || in.Conv == nil {
		return "", nil
	}
	return pc.state.BuildStateContextWithSources(ctx, in.Conv.ID, in.UserID, in.TenantID)
}

// ---------------------------------------------------------------------------
// Layer 13 — RAG / File Context (when present)
// ---------------------------------------------------------------------------

// ragAndFileContext joins the pre-rendered context blocks in the same
// order the legacy assembler used: RAG, file, then the per-format
// Excel/Doc/Pptx capability prompts. Each is "" when absent.
func (pc *PromptCompiler) ragAndFileContext(in CompileContext) string {
	parts := make([]string, 0, 5)
	for _, p := range []string{
		in.RAGContext, in.FileContext, in.ExcelContext, in.DocContext, in.PptxContext,
	} {
		if strings.TrimSpace(p) != "" {
			parts = append(parts, p)
		}
	}
	return strings.Join(parts, "\n\n")
}
