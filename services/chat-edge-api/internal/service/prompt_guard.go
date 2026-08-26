package service

// FIX22 §1a — write-time prompt guard.
//
// Text that chat-edge STORES and later folds into an assembled system prompt
// (a memory, project instructions, custom instructions, a skill's
// instructions) is checked against the gateway's content guardrail BEFORE
// it is persisted. A stored string that matches a guardrail rule would 403
// every future turn that carries it — silently, permanently, with an error
// that names no cause (docs/FIX21_REPORT.md §5) — and `save_memory` is a tool
// the model can call on its own, so a poisoned page can talk it into planting
// exactly such a string. Refusing at write time cuts that chain at storage.
//
// The verdict comes from the gateway (POST /v1/guardrails/check), which runs
// the chat path's own GuardrailService — there is ONE rule set, and it lives
// on the backend. chat-edge keeps no copy of the rules. Diagnostic and
// refusal only: the gateway still enforces on every turn regardless.
//
// Fail-open: if the gateway cannot be reached the write proceeds and the
// failure is logged. The alternative — refusing every memory write while the
// gateway is down — would turn an outage into data loss for a check whose
// enforcement point still exists downstream.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"

	"github.com/google/uuid"
)

// PromptGuard answers "would this text be blocked by the gateway's content
// guardrail?" for text that is about to be stored into a prompt source.
type PromptGuard interface {
	CheckPromptText(ctx context.Context, tenantID, userID uuid.UUID, text string) (*GuardrailVerdict, error)
}

// GuardrailVerdict mirrors the gateway's GuardrailCheckResponse.
type GuardrailVerdict struct {
	Passed   bool   `json:"passed"`
	RuleName string `json:"rule_name"`
	Reason   string `json:"reason"`
	// The first matched span — shown to the user so they can reword it.
	// Never written anywhere that gets re-sent to a model.
	Matched string `json:"matched"`
}

// ErrPromptGuardrailBlocked is the sentinel every *PromptGuardrailError
// matches via errors.Is, so handlers map it in one case.
var ErrPromptGuardrailBlocked = errors.New("prompt guard: text would be blocked by the content guardrail")

// PromptGuardrailError carries the rule and the span for the user-facing
// refusal message. Error() is written for the person who typed the text.
type PromptGuardrailError struct {
	Rule    string
	Matched string
	// What was being saved — "memory", "project instructions", … — so the
	// message says where the phrase lives.
	What string
}

func (e *PromptGuardrailError) Error() string {
	what := e.What
	if what == "" {
		what = "this text"
	}
	if e.Matched != "" {
		return fmt.Sprintf(
			"Not saved: %s contains the phrase %q, which the gateway's content guardrail (rule %s) blocks — "+
				"if it were saved, every future conversation would be refused. Reword that phrase and try again.",
			what, e.Matched, e.Rule)
	}
	return fmt.Sprintf(
		"Not saved: %s would be blocked by the gateway's content guardrail (rule %s) — "+
			"if it were saved, every future conversation would be refused. Reword it and try again.",
		what, e.Rule)
}

func (e *PromptGuardrailError) Is(target error) bool { return target == ErrPromptGuardrailBlocked }

// GuardPromptText is the exported form for handlers outside this package.
func GuardPromptText(ctx context.Context, g PromptGuard, tenantID, userID uuid.UUID, text, what string) error {
	return guardPromptText(ctx, g, tenantID, userID, text, what)
}

// guardPromptText runs the guard (nil-safe) and returns a *PromptGuardrailError
// when the gateway says the text would be blocked. Gateway errors are logged
// and swallowed (fail-open, see the file comment).
func guardPromptText(ctx context.Context, g PromptGuard, tenantID, userID uuid.UUID, text, what string) error {
	if g == nil || strings.TrimSpace(text) == "" {
		return nil
	}
	v, err := g.CheckPromptText(ctx, tenantID, userID, text)
	if err != nil {
		slog.Warn("prompt guard unavailable — write proceeds unchecked (fail-open)",
			"what", what, "err", err.Error())
		return nil
	}
	if v == nil || v.Passed {
		return nil
	}
	return &PromptGuardrailError{Rule: v.RuleName, Matched: v.Matched, What: what}
}

// ---------------------------------------------------------------------------
// Gateway-backed implementation
// ---------------------------------------------------------------------------

type gatewayPromptGuard struct{ gw *GatewayClient }

// NewGatewayPromptGuard returns a PromptGuard that asks the gateway.
func NewGatewayPromptGuard(gw *GatewayClient) PromptGuard { return &gatewayPromptGuard{gw: gw} }

func (g *gatewayPromptGuard) CheckPromptText(ctx context.Context, tenantID, userID uuid.UUID, text string) (*GuardrailVerdict, error) {
	return g.gw.GuardrailCheck(ctx, GatewayCallContext{TenantID: tenantID, UserID: userID}, text)
}

// GuardrailCheck calls POST /v1/guardrails/check with the same trusted-caller
// identity every other gateway call carries. A non-200 is an error (the
// caller fails open); the gateway's verdict is returned verbatim.
func (g *GatewayClient) GuardrailCheck(
	ctx context.Context, callCtx GatewayCallContext, text string,
) (*GuardrailVerdict, error) {
	buf, err := json.Marshal(map[string]string{"text": text})
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal guardrail check: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.BaseURL+"/guardrails/check", bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build guardrail-check request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("gateway guardrail-check: status %d", resp.StatusCode)
	}
	out := &GuardrailVerdict{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode guardrail-check: %w", err)
	}
	return out, nil
}
