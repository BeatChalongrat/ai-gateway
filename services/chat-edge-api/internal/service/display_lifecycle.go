package service

// DLP Display Lifecycle (Phase 4A.2.4).
//
// Goal: stop persisting restored RAW display in ``chat.messages_masked.
// content_masked``. When the feature is enabled, NEW assistant writes store a
// provider-safe representation (the tokenized canonical text) or a redacted
// placeholder, classified by the ``display_state`` column (migration 025). The
// browser still shows the restored display LIVE during the stream (the handler
// forwards gateway deltas verbatim); only the PERSISTED snapshot changes.
//
// Reload + every other consumer go through ResolveRepresentation, which encodes
// the representation matrix:
//
//	Provider history      → content_canonical only (never display, never restore)
//	Browser live stream    → restored display (handled in the SSE path, not here)
//	Browser reload (owner) → restore-on-read (Vault V2) or tokenized/redacted
//	Admin default          → tokenized/redacted (never raw, never restore)
//	Export / share         → tokenized/redacted
//	Search index           → tokenized/redacted
//	Analytics              → metadata only (callers must not pass content here)
//
// Restore-on-read raw resolution is delegated to a DisplayRestorer that MUST
// enforce owner scope + category never-restore rules through an authorized
// Vault V2 lookup. The production default restorer is nil (no cross-service
// Vault V2 bridge yet) → reload falls back to the tokenized/redacted form.
// With Vault V2 disabled (repo default) restore-on-read is therefore inert.

import (
	"context"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// display_state values (mirror migration 025).
const (
	// DisplayStateLegacy — content_masked holds restored RAW display at rest.
	// Pre-feature rows AND flag-off writes. RAW-SENSITIVE.
	DisplayStateLegacy = "legacy_display"
	// DisplayStateCanonicalSafe — content_masked holds the tokenized canonical
	// (provider-safe) text. No raw PII at rest.
	DisplayStateCanonicalSafe = "canonical_safe"
	// DisplayStateRedacted — content_masked holds a safe placeholder (a
	// non-complete row had no canonical to store).
	DisplayStateRedacted = "redacted"
)

// RedactedDisplayPlaceholder is the UI-safe stand-in persisted (and shown on
// reload) when no provider-safe content is available. Carries no raw content.
const RedactedDisplayPlaceholder = "[ข้อความนี้ถูกปกปิดเพื่อความปลอดภัย / redacted]"

// neverRestoreCategories are the secret/identifier classes whose canonical
// tokens must NEVER be restored to a persisted/reloaded display, even for the
// owner. Mirrors the Python DLP never-restore policy (secrets never egress raw;
// identifiers stay tokenized at rest). Matched case-insensitively as a
// substring of the token's category so e.g. “<<NATIONAL_ID_1>>“ is caught.
var neverRestoreCategories = []string{
	"SECRET", "APIKEY", "API_KEY", "CREDENTIAL", "PASSWORD", "PRIVATE_KEY",
	"PRIVATEKEY", "TOKEN", "ACCESS_KEY", "ACCESS_TOKEN", "SSH",
	"NATIONAL_ID", "NATIONALID", "CREDIT_CARD", "CREDITCARD", "CARD",
	"SSN", "BANK_ACCOUNT", "BANKACCOUNT", "IBAN", "PASSPORT", "TAX_ID", "TAXID",
}

// neverRestoreToken reports whether a token belongs to a never-restore class.
// The generic “<rN>“ shape carries no category, so its provenance is unknown
// → treated as never-restore (conservative). Outbound-generated tokens are
// likewise not restorable unless an implementation explicitly marks them so.
func neverRestoreToken(token string) bool {
	u := strings.ToUpper(token)
	if strings.HasPrefix(u, "<R") { // <rN> generic, unknown category
		return true
	}
	for _, cat := range neverRestoreCategories {
		if strings.Contains(u, cat) {
			return true
		}
	}
	return false
}

// selectRestorableTokens extracts the distinct “<<CATEGORY_N>>“ pseudonym
// tokens from canonical text that are eligible for restore-on-read (i.e. NOT a
// never-restore class). It never returns “<rN>“ tokens. The result is the
// ONLY set handed to the restorer, so a secret/identifier token can never be
// resolved back to raw even if a vault entry exists.
func selectRestorableTokens(canonical string) []string {
	out := []string{}
	seen := map[string]bool{}
	s := canonical
	for {
		i := strings.Index(s, "<<")
		if i < 0 {
			break
		}
		j := strings.Index(s[i:], ">>")
		if j < 0 {
			break
		}
		tok := s[i : i+j+2]
		s = s[i+j+2:]
		if seen[tok] || neverRestoreToken(tok) {
			continue
		}
		seen[tok] = true
		out = append(out, tok)
	}
	return out
}

// DisplayConsumer identifies the read surface requesting a representation.
type DisplayConsumer int

const (
	// ConsumerProviderHistory — the provider-replay path. NEVER restores; reads
	// content_canonical only (defensive — the real path uses
	// ValidateStoredCanonicalMessage).
	ConsumerProviderHistory DisplayConsumer = iota
	// ConsumerBrowserReloadOwner — the owner re-opening the conversation.
	ConsumerBrowserReloadOwner
	// ConsumerAdminDefault — admin list/detail without privileged raw permission.
	ConsumerAdminDefault
	// ConsumerExportShare — data export + shared-conversation rendering.
	ConsumerExportShare
	// ConsumerSearchIndex — the full-text search index / results.
	ConsumerSearchIndex
	// ConsumerAnalytics — metadata-only aggregates (must not receive content).
	ConsumerAnalytics
)

// DisplayActor is the authenticated reader, for owner-scoped restore.
type DisplayActor struct {
	UserID   uuid.UUID
	TenantID uuid.UUID
}

// DisplayRestorer resolves restorable canonical tokens back to raw display for
// an AUTHORIZED owner through a scoped Vault V2 lookup. Implementations MUST
// verify the actor owns the message's scope and MUST honor category
// never-restore rules (callers pre-filter via selectRestorableTokens, but the
// implementation is the final authority). A nil restorer (production default
// until a Vault V2 bridge exists) means restore-on-read is unavailable.
type DisplayRestorer interface {
	// RestoreDisplay returns (restoredText, true) only when the actor is
	// authorized AND a scoped vault entry exists for the requested tokens.
	// Returns (_, false) on: vault disabled, TTL expired, wrong actor/tenant,
	// or no entry — the caller then falls back to the tokenized representation.
	RestoreDisplay(ctx context.Context, actor DisplayActor, m model.Message, restorable []string) (string, bool)
}

// DisplayResolution is the chosen representation for a consumer.
type DisplayResolution struct {
	Content  string
	Restored bool // raw restore-on-read was applied (owner reload only)
	Redacted bool // a tokenized/placeholder fallback was substituted for raw
}

// SetDisplayLifecycle wires the Phase 4A.2.4 rollout. “enabled“ switches new
// writes to provider-safe persistence and activates the read-path policy;
// “shadow“ reserves observe-only semantics; “strictLegacy“ redacts
// pre-feature legacy rows on owner reload instead of showing their stored raw.
// Display lifecycle is only meaningful with canonical history enabled.
func (s *MessageService) SetDisplayLifecycle(enabled, shadow, strictLegacy bool) {
	s.displayLifecycleEnabled = enabled
	s.displayLifecycleShadow = shadow
	s.legacyDisplayStrict = strictLegacy
}

// SetDisplayRestorer injects the Vault-V2-backed restore-on-read implementation.
// Nil (default) → restore-on-read unavailable → tokenized/redacted fallback.
func (s *MessageService) SetDisplayRestorer(r DisplayRestorer) {
	s.displayRestorer = r
}

// DisplayLifecycleEnabled reports whether new writes use provider-safe
// persistence (consumed by the read handler to decide whether to apply policy).
func (s *MessageService) DisplayLifecycleEnabled() bool {
	return s.displayLifecycleEnabled
}

// persistedDisplay decides what to store in “content_masked“ and the
// “display_state“, honoring the display-lifecycle flag. Flag OFF → UNCHANGED
// legacy behavior (store the restored display verbatim, state=legacy_display).
// Flag ON → store the tokenized canonical when a complete canonical exists
// (state=canonical_safe), else a redacted placeholder (state=redacted). NEVER
// stores restored raw display when the flag is on.
func (s *MessageService) persistedDisplay(restoredDisplay string, canonical *string) (string, string) {
	if !s.displayLifecycleEnabled {
		return restoredDisplay, DisplayStateLegacy
	}
	if canonical != nil {
		return *canonical, DisplayStateCanonicalSafe
	}
	return RedactedDisplayPlaceholder, DisplayStateRedacted
}

// ResolveRepresentation returns the representation a consumer is allowed to see
// for a stored message. When display lifecycle is OFF it is a pure passthrough
// of “content_masked“ for every consumer (zero behavior change). When ON it
// enforces the representation matrix above.
func (s *MessageService) ResolveRepresentation(
	ctx context.Context, consumer DisplayConsumer, m model.Message, actor DisplayActor,
) DisplayResolution {
	if !s.displayLifecycleEnabled {
		return DisplayResolution{Content: m.ContentMasked}
	}
	state := m.DisplayState
	if state == "" {
		state = DisplayStateLegacy
	}
	switch consumer {
	case ConsumerBrowserReloadOwner:
		return s.resolveOwnerReload(ctx, m, actor, state)
	case ConsumerProviderHistory:
		// Defensive: provider history reads content_canonical only and never
		// restores. (The real replay path uses ValidateStoredCanonicalMessage.)
		if m.CanonicalStatus == CanonicalStatusComplete && m.ContentCanonical != nil {
			return DisplayResolution{Content: *m.ContentCanonical}
		}
		return DisplayResolution{Content: "", Redacted: true}
	case ConsumerAnalytics:
		// Analytics is metadata-only; never expose content.
		return DisplayResolution{Content: "", Redacted: true}
	default:
		// Admin default / export-share / search index: NEVER restore, NEVER
		// emit legacy raw. canonical_safe → tokenized; everything else → redacted.
		return s.resolveNonRestoring(m, state)
	}
}

func (s *MessageService) resolveOwnerReload(
	ctx context.Context, m model.Message, actor DisplayActor, state string,
) DisplayResolution {
	switch state {
	case DisplayStateCanonicalSafe:
		if s.displayRestorer != nil && m.ContentCanonical != nil {
			if restorable := selectRestorableTokens(*m.ContentCanonical); len(restorable) > 0 {
				if restored, ok := s.displayRestorer.RestoreDisplay(ctx, actor, m, restorable); ok {
					return DisplayResolution{Content: restored, Restored: true}
				}
			}
		}
		// Vault disabled / TTL expired / wrong actor / no restorable tokens →
		// tokenized fallback (never raw, never an error).
		if m.ContentCanonical != nil {
			return DisplayResolution{Content: *m.ContentCanonical, Redacted: true}
		}
		return DisplayResolution{Content: m.ContentMasked, Redacted: true}
	case DisplayStateRedacted:
		return DisplayResolution{Content: m.ContentMasked, Redacted: true}
	default: // legacy_display
		if s.legacyDisplayStrict {
			return DisplayResolution{Content: RedactedDisplayPlaceholder, Redacted: true}
		}
		// Non-strict: existing rows remain readable to their owner.
		return DisplayResolution{Content: m.ContentMasked}
	}
}

func (s *MessageService) resolveNonRestoring(m model.Message, state string) DisplayResolution {
	if state == DisplayStateCanonicalSafe {
		// content_masked already holds the tokenized canonical under the flag;
		// prefer the canonical column if present, else the stored display.
		if m.ContentCanonical != nil {
			return DisplayResolution{Content: *m.ContentCanonical, Redacted: true}
		}
		return DisplayResolution{Content: m.ContentMasked, Redacted: true}
	}
	// redacted OR legacy_display → treat as raw-sensitive; never emit raw to
	// admin/export/search by default.
	return DisplayResolution{Content: RedactedDisplayPlaceholder, Redacted: true}
}

// SearchSafeContent is the field-based (no model.Message) representation for
// search/index and the LLM search tool — both NON-RESTORING consumers. When
// the feature is OFF it passes through “contentMasked“ (zero behavior change).
// When ON it NEVER emits raw display: canonical_safe / redacted rows return
// their stored (already-safe) content_masked, and raw-sensitive legacy (or
// pre-migration empty) rows are redacted to the placeholder. There is NO owner
// restore and NO Vault lookup on the search path.
func SearchSafeContent(enabled bool, displayState, contentMasked string) string {
	if !enabled {
		return contentMasked
	}
	switch displayState {
	case DisplayStateCanonicalSafe, DisplayStateRedacted:
		return contentMasked
	default: // legacy_display or empty (pre-migration) → raw-sensitive
		return RedactedDisplayPlaceholder
	}
}

// NonRestoringRepresentation is the pure (no service state) representation for
// non-restoring consumers (admin default / export / share / search). Used by
// handlers that hold a model.Message but not the MessageService. When the
// feature is OFF it is a passthrough of content_masked (zero behavior change);
// when ON it NEVER emits raw display: canonical_safe / redacted rows return
// their stored (already-safe) content_masked, and raw-sensitive legacy rows are
// redacted to the placeholder.
func NonRestoringRepresentation(enabled bool, m model.Message) string {
	if !enabled {
		return m.ContentMasked
	}
	switch m.DisplayState {
	case DisplayStateCanonicalSafe, DisplayStateRedacted:
		return m.ContentMasked
	default: // legacy_display or empty (pre-migration) → raw-sensitive
		return RedactedDisplayPlaceholder
	}
}
