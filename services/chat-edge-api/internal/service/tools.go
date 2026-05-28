// AI tool registry + execution dispatcher.
//
// S1 ships two memory-layer tools:
//   - save_memory   — AI writes a fact/preference on user instruction
//   - list_memories — AI lists what it remembers about the user
//
// S3 adds one more, scoped to the current conversation:
//   - update_conversation_state — AI records goal / decisions /
//     completed tasks / rejected approaches the instant they land,
//     without waiting for the background extraction trigger.
//
// The OpenAI tool-calling spec is forwarded verbatim by the backend
// gateway (see backend/app/schemas/public.py — tools + tool_choice
// flow through to the provider adapter). The chat-edge MessageService
// consumes the model's tool_calls response, executes via this
// dispatcher, and threads the result back as role="tool" messages
// before re-calling the gateway for the final assistant response.
//
// Tool execution is gated by Conversation.MemoryEnabled at the
// MessageService layer; this file is the format-agnostic registry,
// so future tools (calendar, search, etc.) layer on the same hook.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Tool registry
// ---------------------------------------------------------------------------

// updateConversationStateToolDef is the S3 tool definition.
// Exported as a free function so MemoryToolDefs (which the
// MessageService calls) can splice it into the same gated tool
// list.
func updateConversationStateToolDef() GatewayTool {
	return GatewayTool{
		Type: "function",
		Function: GatewayToolFunction{
			Name: "update_conversation_state",
			Description: "Update the structured state of this conversation. " +
				"Call this when:\n" +
				"- The user states or changes the goal of the conversation\n" +
				"- A significant decision is made or confirmed\n" +
				"- A task is completed or a new task is identified\n" +
				"- An approach is rejected\n" +
				"- An important file/artifact is created or modified\n" +
				"Do NOT call for casual chat, greetings, or minor clarifications.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"current_goal": map[string]any{
						"type":        "string",
						"description": "One sentence: what is the user trying to accomplish in this conversation? Only update if the goal changed.",
					},
					"add_decision": map[string]any{
						"type":        "string",
						"description": "A decision that was just confirmed.",
					},
					"add_task": map[string]any{
						"type":        "string",
						"description": "A new task identified.",
					},
					"complete_task": map[string]any{
						"type":        "string",
						"description": "A task that was just completed. Match the wording from open_tasks.",
					},
					"add_artifact": map[string]any{
						"type":        "string",
						"description": "A file/artifact just created or modified.",
					},
					"reject_approach": map[string]any{
						"type":        "string",
						"description": "An approach that was just rejected.",
					},
					"add_question": map[string]any{
						"type":        "string",
						"description": "An unresolved question identified.",
					},
					"resolve_question": map[string]any{
						"type":        "string",
						"description": "A question that was just answered. Match the wording from unresolved_questions.",
					},
				},
			},
		},
	}
}

// MemoryToolDefs returns the OpenAI tool definitions the memory
// layer registers with the gateway. Stable values — safe to call on
// every request without allocating fresh maps (jsoner reuses them).
//
// Returned slice is read-only by contract; callers append it to
// their own request's Tools (don't mutate in place).
func MemoryToolDefs() []GatewayTool {
	return []GatewayTool{
		{
			Type: "function",
			Function: GatewayToolFunction{
				Name: "save_memory",
				Description: "Save a fact, preference, or instruction to memory so it persists across all conversations. " +
					"Use when the user says 'remember', 'จำไว้ว่า', 'note that', 'from now on', 'ต่อไปนี้', " +
					"or gives a standing preference/instruction. " +
					"Do NOT save passwords, API keys, tokens, ID numbers, credit card numbers, bank accounts, or salary amounts.",
				Parameters: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"content": map[string]any{
							"type":        "string",
							"description": "The fact/preference/instruction to remember. Be concise. Example: 'Preferred name is Beat'",
						},
						"memory_type": map[string]any{
							"type": "string",
							"enum": []string{
								model.MemoryTypeExplicitFact,
								model.MemoryTypeResponsePreference,
								model.MemoryTypeOngoingInterest,
								model.MemoryTypeConfirmedInstruction,
								model.MemoryTypeArtifactReference,
							},
							"description": "explicit_fact=personal info, response_preference=how to respond, ongoing_interest=current work/hobby, confirmed_instruction=standing rule, artifact_reference=about a file",
						},
						"category": map[string]any{
							"type":        "string",
							"description": "Optional grouping: identity, language, coding, work, style, etc.",
						},
					},
					"required": []string{"content", "memory_type"},
				},
			},
		},
		{
			Type: "function",
			Function: GatewayToolFunction{
				Name: "list_memories",
				Description: "List what you remember about this user. " +
					"Use when they ask 'what do you remember?', 'จำอะไรเกี่ยวกับฉันบ้าง?', or similar.",
				Parameters: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"scope_type": map[string]any{
							"type":        "string",
							"enum":        []string{"global_user", "all"},
							"description": "Default global_user (cross-conversation facts). Use 'all' to also include conversation-scoped + artifact-scoped memories.",
						},
					},
				},
			},
		},
	}
}

// ---------------------------------------------------------------------------
// Tool execution
// ---------------------------------------------------------------------------

// ToolExecContext threads the per-turn identity + conversation
// pointers into each tool execution. Tools that mutate user-scoped
// data (save_memory) need UserID + TenantID; tools that record
// provenance (save_memory's source_conversation_id) need ConvID.
type ToolExecContext struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
}

// ToolExecutor dispatches a single tool call to its handler.
//
// Keeps the MessageService independent of the concrete tool set —
// the message flow asks the executor "run this tool_call, give me
// a string result"; the executor decides how to do that.
type ToolExecutor struct {
	memories *MemoryService
	// S3 — optional. When wired, the executor handles
	// update_conversation_state; when nil, that tool isn't
	// exposed (see ToolDefs() below).
	states *ConversationStateService
}

func NewToolExecutor(memories *MemoryService) *ToolExecutor {
	return &ToolExecutor{memories: memories}
}

// SetConversationStateService wires the S3 state layer. Optional
// so unit tests that don't exercise the state path stay decoupled.
func (e *ToolExecutor) SetConversationStateService(s *ConversationStateService) {
	e.states = s
}

// ToolDefs returns the full set of tool definitions the executor
// can dispatch, gated by what's wired. The MessageService calls
// this from attachTools — it replaces the old MemoryToolDefs()
// direct splice so S3's tool joins the list when its service is
// available.
func (e *ToolExecutor) ToolDefs() []GatewayTool {
	defs := MemoryToolDefs()
	if e.states != nil {
		defs = append(defs, updateConversationStateToolDef())
	}
	return defs
}

// Execute runs the named tool with the supplied JSON arguments and
// returns the string result that ships back to the gateway as a
// role="tool" message. Returns a user-facing error message on
// failure — never a raw error type — so the model gets a
// usable explanation instead of a stack trace.
func (e *ToolExecutor) Execute(
	ctx context.Context, call GatewayToolCall, ec ToolExecContext,
) string {
	switch call.Function.Name {
	case "save_memory":
		return e.execSaveMemory(ctx, call.Function.Arguments, ec)
	case "list_memories":
		return e.execListMemories(ctx, call.Function.Arguments, ec)
	case "update_conversation_state":
		return e.execUpdateConversationState(ctx, call.Function.Arguments, ec)
	default:
		return fmt.Sprintf("Unknown tool %q.", call.Function.Name)
	}
}

// ---------------------------------------------------------------------------
// update_conversation_state
// ---------------------------------------------------------------------------

type updateConversationStateArgs struct {
	CurrentGoal     string `json:"current_goal"`
	AddDecision     string `json:"add_decision"`
	AddTask         string `json:"add_task"`
	CompleteTask    string `json:"complete_task"`
	AddArtifact     string `json:"add_artifact"`
	RejectApproach  string `json:"reject_approach"`
	AddQuestion     string `json:"add_question"`
	ResolveQuestion string `json:"resolve_question"`
}

func (e *ToolExecutor) execUpdateConversationState(
	ctx context.Context, rawArgs string, ec ToolExecContext,
) string {
	if e.states == nil {
		return "The conversation state tool isn't available right now."
	}
	var args updateConversationStateArgs
	if rawArgs != "" {
		if err := json.Unmarshal([]byte(rawArgs), &args); err != nil {
			return "I couldn't parse the update_conversation_state arguments."
		}
	}
	changes, err := e.states.ApplyToolUpdate(ctx, ec.ConversationID, ec.UserID, ec.TenantID, ToolStateUpdate{
		CurrentGoal:     args.CurrentGoal,
		AddDecision:     args.AddDecision,
		AddTask:         args.AddTask,
		CompleteTask:    args.CompleteTask,
		AddArtifact:     args.AddArtifact,
		RejectApproach:  args.RejectApproach,
		AddQuestion:     args.AddQuestion,
		ResolveQuestion: args.ResolveQuestion,
	})
	if err != nil {
		return "I couldn't update the conversation state right now."
	}
	if len(changes) == 0 {
		return "No changes to record."
	}
	return "Updated conversation state:\n" + strings.Join(changes, "\n")
}

// ---------------------------------------------------------------------------
// save_memory
// ---------------------------------------------------------------------------

type saveMemoryArgs struct {
	Content    string `json:"content"`
	MemoryType string `json:"memory_type"`
	Category   string `json:"category"`
}

func (e *ToolExecutor) execSaveMemory(
	ctx context.Context, rawArgs string, ec ToolExecContext,
) string {
	var args saveMemoryArgs
	if err := json.Unmarshal([]byte(rawArgs), &args); err != nil {
		return "I couldn't parse the save_memory arguments. Please try rephrasing what you'd like me to remember."
	}
	if strings.TrimSpace(args.Content) == "" {
		return "I need actual content to save. Tell me what you'd like me to remember."
	}
	// Default to explicit_fact when the model forgets to set the
	// type — better to save the fact than to refuse over a missing
	// metadata field.
	if args.MemoryType == "" {
		args.MemoryType = model.MemoryTypeExplicitFact
	}

	in := CreateMemoryInput{
		TenantID:             ec.TenantID,
		UserID:               ec.UserID,
		ScopeType:            model.MemoryScopeGlobalUser,
		MemoryType:           args.MemoryType,
		Category:             args.Category,
		Content:              args.Content,
		SourceType:           model.MemorySourceUserExplicit,
		SourceConversationID: nilOrUUID(ec.ConversationID),
		SourceMessageID:      nilOrUUID(ec.MessageID),
		ActorType:            "assistant",
	}

	out, err := e.memories.CreateMemory(ctx, in)
	if err != nil {
		switch {
		case errors.Is(err, ErrMemoryProhibitedContent):
			return "I can't save that — it appears to contain sensitive data (API keys, IDs, financial info, etc.)."
		case errors.Is(err, ErrMemoryDuplicate):
			return "I already have that saved."
		case errors.Is(err, ErrMemoryEmpty):
			return "I need actual content to save."
		case errors.Is(err, ErrMemoryInvalidType),
			errors.Is(err, ErrMemoryInvalidScope),
			errors.Is(err, ErrMemoryInvalidSource):
			return "I couldn't save that — the memory metadata wasn't valid."
		default:
			return "I couldn't save that right now. Please try again in a moment."
		}
	}
	return fmt.Sprintf("Saved: %q", out.Content)
}

// ---------------------------------------------------------------------------
// list_memories
// ---------------------------------------------------------------------------

type listMemoriesArgs struct {
	ScopeType string `json:"scope_type"`
}

func (e *ToolExecutor) execListMemories(
	ctx context.Context, rawArgs string, ec ToolExecContext,
) string {
	var args listMemoriesArgs
	// Empty args is a valid call — default scope is global_user.
	if strings.TrimSpace(rawArgs) != "" {
		_ = json.Unmarshal([]byte(rawArgs), &args)
	}

	filters := repository.MemoryListFilters{
		Status: model.MemoryStatusActive,
		Limit:  100,
	}
	if args.ScopeType == "" || args.ScopeType == "global_user" {
		filters.ScopeType = model.MemoryScopeGlobalUser
	}
	// "all" → no scope_type filter so every active memory surfaces.

	memories, _, err := e.memories.ListByUser(ctx, ec.UserID, ec.TenantID, filters)
	if err != nil {
		return "I couldn't load your memories right now. Please try again in a moment."
	}
	if len(memories) == 0 {
		return "I don't have any saved memories about you yet. Tell me to 'remember' something and I'll save it."
	}

	var sb strings.Builder
	sb.WriteString("Here's what I remember about you:\n\n")
	for _, m := range memories {
		sb.WriteString("- ")
		sb.WriteString(m.Content)
		if m.Category != "" {
			sb.WriteString(" (")
			sb.WriteString(m.Category)
			sb.WriteString(")")
		}
		sb.WriteString("\n")
	}
	sb.WriteString(fmt.Sprintf("\nYou have %d saved %s. ",
		len(memories), pluralize(len(memories), "memory", "memories")))
	sb.WriteString("You can manage them in Settings → Memory.")
	return sb.String()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func nilOrUUID(u uuid.UUID) *uuid.UUID {
	if u == uuid.Nil {
		return nil
	}
	return &u
}

func pluralize(n int, singular, plural string) string {
	if n == 1 {
		return singular
	}
	return plural
}
