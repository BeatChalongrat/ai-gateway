package service

// DLP canonical artifact consumer (Phase 4A.2.2).
//
// The Python gateway emits, for managed internal WebChat requests, a
// CANONICAL representation of each assistant generation — the masked /
// tokenized, pre-restore text that is safe to replay to the provider as
// history. This file validates that artifact and tracks the per-stream
// state machine that derives a persisted ``canonical_status``.
//
// Security contract: provider history uses ``content_canonical`` ONLY when
// the status is ``complete`` and the artifact validates. Every other state
// (legacy / unsupported / missing / unavailable / invalid / cancelled /
// error) is EXCLUDED from provider history — never a restored-display
// fallback. The digest is an integrity check, NOT authorization (ownership
// is still the tenant/user/conversation/message binding at persist time).

import (
	"crypto/sha256"
	"encoding/hex"
	"unicode/utf8"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

const canonicalSchemaVersion = 1

// canonicalMaxBytes bounds an accepted canonical payload (defense against a
// malformed/oversized frame). Comfortably above any real assistant turn.
const canonicalMaxBytes = 1 << 20 // 1 MiB

// canonical_status values (mirror migration 024).
const (
	CanonicalStatusLegacy      = "legacy"
	CanonicalStatusComplete    = "complete"
	CanonicalStatusUnsupported = "unsupported"
	CanonicalStatusMissing     = "missing"
	CanonicalStatusUnavailable = "unavailable"
	CanonicalStatusInvalid     = "invalid"
	CanonicalStatusCancelled   = "cancelled"
	CanonicalStatusError       = "error"
)

// CanonicalArtifact mirrors the gateway's ``dlp.canonical_final`` /
// non-stream ``x_sran_dlp`` envelope. Canonical content only — never
// display/raw values.
type CanonicalArtifact struct {
	SchemaVersion    int    `json:"schema_version"`
	GenerationID     string `json:"generation_id"`
	RequestID        string `json:"request_id"`
	CanonicalContent string `json:"canonical_content"`
	CanonicalSHA256  string `json:"canonical_sha256"`
	CanonicalLength  int    `json:"canonical_length"`
	Status           string `json:"status"`
}

// CanonicalCapabilityFrame is the ``dlp.canonical_capability`` payload.
type CanonicalCapabilityFrame struct {
	SchemaVersion int    `json:"schema_version"`
	GenerationID  string `json:"generation_id"`
	Supported     bool   `json:"supported"`
}

// CanonicalUnavailableFrame is the ``dlp.canonical_unavailable`` payload.
type CanonicalUnavailableFrame struct {
	SchemaVersion int    `json:"schema_version"`
	GenerationID  string `json:"generation_id"`
	Status        string `json:"status"`
	ErrorCode     string `json:"error_code"`
}

func canonicalSHA256(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

func isLowerHex64(s string) bool {
	if len(s) != 64 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

// ValidateCanonicalArtifact returns true iff ``a`` is a well-formed
// COMPLETE artifact safe to persist + replay. Length is the Unicode
// rune count; the SHA is over the exact UTF-8 bytes (must match the
// gateway's producer contract).
func ValidateCanonicalArtifact(a *CanonicalArtifact) bool {
	if a == nil {
		return false
	}
	if a.SchemaVersion != canonicalSchemaVersion {
		return false
	}
	if a.Status != CanonicalStatusComplete {
		return false
	}
	if a.GenerationID == "" || a.RequestID == "" {
		return false
	}
	// Current contract: generation id == request id.
	if a.GenerationID != a.RequestID {
		return false
	}
	if !isLowerHex64(a.CanonicalSHA256) {
		return false
	}
	if !utf8.ValidString(a.CanonicalContent) {
		return false
	}
	if len(a.CanonicalContent) > canonicalMaxBytes {
		return false
	}
	// canonical_length = rune count (code points), NOT byte length.
	if a.CanonicalLength != utf8.RuneCountInString(a.CanonicalContent) {
		return false
	}
	if canonicalSHA256(a.CanonicalContent) != a.CanonicalSHA256 {
		return false
	}
	return true
}

// CanonicalStreamState accumulates the canonical events observed on ONE
// stream. Request-local — never shared across streams/requests.
type CanonicalStreamState struct {
	CapabilitySeen bool
	GenerationID   string
	Artifact       *CanonicalArtifact
	Unavailable    bool
	UnavailableErr string
	conflict       bool // duplicate final with differing content
}

// OnCapability records the capability announcement.
func (s *CanonicalStreamState) OnCapability(f *CanonicalCapabilityFrame) {
	if f == nil {
		return
	}
	s.CapabilitySeen = true
	if s.GenerationID == "" {
		s.GenerationID = f.GenerationID
	}
}

// OnFinal records a terminal canonical artifact. A second final with the
// SAME content is idempotent; a conflicting one marks the state invalid.
func (s *CanonicalStreamState) OnFinal(a *CanonicalArtifact) {
	if a == nil {
		return
	}
	if s.Artifact != nil {
		if s.Artifact.CanonicalSHA256 != a.CanonicalSHA256 ||
			s.Artifact.CanonicalContent != a.CanonicalContent {
			s.conflict = true
		}
		return // idempotent: keep the first
	}
	s.Artifact = a
}

// OnUnavailable records a terminal unavailable verdict.
func (s *CanonicalStreamState) OnUnavailable(f *CanonicalUnavailableFrame) {
	if f == nil {
		return
	}
	s.Unavailable = true
	s.UnavailableErr = f.ErrorCode
}

// DeriveStatus computes the persisted canonical verdict from the observed
// stream state. ``streamCompleted`` is true when the stream reached its
// normal terminal ([DONE]) without cancellation/error.
//
// Returns the status, the validated artifact (only for ``complete``), and
// a stable error code (only for ``unavailable``). For every non-complete
// status the artifact is nil → the caller MUST exclude the row from
// provider history (never fall back to display).
func (s *CanonicalStreamState) DeriveStatus(streamCompleted bool) (string, *CanonicalArtifact, string) {
	if !s.CapabilitySeen {
		return CanonicalStatusUnsupported, nil, ""
	}
	if s.conflict {
		return CanonicalStatusInvalid, nil, ""
	}
	if s.Unavailable {
		return CanonicalStatusUnavailable, nil, s.UnavailableErr
	}
	if s.Artifact != nil {
		if !ValidateCanonicalArtifact(s.Artifact) {
			return CanonicalStatusInvalid, nil, ""
		}
		return CanonicalStatusComplete, s.Artifact, ""
	}
	if !streamCompleted {
		return CanonicalStatusCancelled, nil, ""
	}
	return CanonicalStatusMissing, nil, ""
}

// CanonicalRejectReason is a stable, log-safe enum for why a stored row's
// canonical was rejected at read time (never raw content).
type CanonicalRejectReason string

const (
	RejectNone       CanonicalRejectReason = ""
	RejectStatus     CanonicalRejectReason = "status_not_complete"
	RejectNilContent CanonicalRejectReason = "nil_content"
	RejectSchema     CanonicalRejectReason = "schema"
	RejectGeneration CanonicalRejectReason = "generation"
	RejectSHA        CanonicalRejectReason = "sha"
	RejectLength     CanonicalRejectReason = "length"
	RejectUTF8       CanonicalRejectReason = "utf8"
	RejectSize       CanonicalRejectReason = "size"
)

// ValidateStoredCanonicalMessage RE-VALIDATES a persisted assistant row at
// READ time (H1) — it never trusts ``canonical_status`` alone. Returns the
// canonical content, ok, and a stable reject reason. A row that fails ANY
// check is excluded from provider history by the caller (no display
// fallback). The reason is log-safe; the caller must not log content.
func ValidateStoredCanonicalMessage(m model.Message) (string, bool, CanonicalRejectReason) {
	if m.CanonicalStatus != CanonicalStatusComplete {
		return "", false, RejectStatus
	}
	if m.ContentCanonical == nil {
		return "", false, RejectNilContent
	}
	c := *m.ContentCanonical
	if m.CanonicalSchemaVersion == nil || *m.CanonicalSchemaVersion != canonicalSchemaVersion {
		return "", false, RejectSchema
	}
	if m.CanonicalGenerationID == nil || *m.CanonicalGenerationID == "" {
		return "", false, RejectGeneration
	}
	if m.CanonicalSHA256 == nil || !isLowerHex64(*m.CanonicalSHA256) {
		return "", false, RejectSHA
	}
	if !utf8.ValidString(c) {
		return "", false, RejectUTF8
	}
	if len(c) > canonicalMaxBytes {
		return "", false, RejectSize
	}
	if m.CanonicalLength == nil || *m.CanonicalLength != utf8.RuneCountInString(c) {
		return "", false, RejectLength
	}
	if canonicalSHA256(c) != *m.CanonicalSHA256 {
		return "", false, RejectSHA
	}
	return c, true, RejectNone
}

// validateNonStreamArtifact derives the persisted status for a non-stream
// ``x_sran_dlp`` envelope. ``present`` is whether the envelope existed on
// the response (consumer expected it because the gateway supports it).
func validateNonStreamArtifact(a *CanonicalArtifact, present bool) (string, *CanonicalArtifact, string) {
	if !present || a == nil {
		// Managed consumer expected an envelope but got none → missing.
		return CanonicalStatusMissing, nil, ""
	}
	if a.Status == CanonicalStatusUnavailable {
		return CanonicalStatusUnavailable, nil, ""
	}
	if ValidateCanonicalArtifact(a) {
		return CanonicalStatusComplete, a, ""
	}
	return CanonicalStatusInvalid, nil, ""
}
