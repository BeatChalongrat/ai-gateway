package service

// FIX131 A2 — a plan execution that fails says why, in a closed set the page
// words in Thai, and the generator's own error reaches the server log.
//
// Before: `if !genResult.Success { … "script reported failure" }` threw the
// script's error away, and the handler wrote err.Error() — raw English, and
// for a sandbox failure docker's own stderr — to the user's card
// ("doc: generator failed: script reported failure").
//
// Now ExecutePlan returns a *PlanExecError: Reason is the closed-set code the
// client maps to a sentence with a next step; Detail is the script's own
// error (type + message, quoted values cut out) for the log only — it never
// goes on the wire. Error() carries only the sentinel and the reason, so a
// handler that prints it leaks nothing either.

import (
	"encoding/json"
	"errors"
	"log/slog"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// Plan failure reasons — the closed set on the wire (error.reason on the
// execute response, plan_outcomes[].reason on GET /messages). The page maps
// each to a Thai/English sentence with what to do next (plan.fail.*).
const (
	// The input file could not be read, so the plan's content cannot have
	// come from it (FIX126 F3's unreadable file, now with the reason).
	PlanFailSourceEncrypted  = "source_encrypted"
	PlanFailSourceScanned    = "source_scanned"
	PlanFailSourceDamaged    = "source_damaged"
	PlanFailSourceUnreadable = "source_unreadable"
	// The plan's source file is gone (deleted, or never in this chat).
	PlanFailSourceNotFound = "source_not_found"
	// The plan itself is unusable (format, JSON).
	PlanFailInvalidPlan = "invalid_plan"
	// The generator ran and failed / ran out of time.
	PlanFailGenerator = "generator_failed"
	PlanFailTimeout   = "generator_timeout"
	// The generator produced a file the validator would not pass.
	PlanFailValidation = "validation_failed"
	// The sandbox that runs generators is not available right now.
	PlanFailSandbox = "sandbox_unavailable"
	// Storage / database trouble after a good generation.
	PlanFailInternal = "internal"
)

// PlanExecError is the typed failure of a plan execution.
type PlanExecError struct {
	Reason string
	// Source is the plan's input file when the reason is about it (the
	// unreadable/missing cases) — the handler names it on the card.
	Source *model.ConversationFile
	// Role is how the source was used (edit / input / none).
	Role PlanSourceRole
	// Detail is the generator's own error for the server log: exception
	// type + message with quoted values removed, bounded. Never on the wire.
	Detail string
	// Err is the package sentinel (ErrDocGeneratorFailed, …) so existing
	// errors.Is branches keep working.
	Err error
}

func (e *PlanExecError) Error() string {
	if e.Err == nil {
		return "plan: " + e.Reason
	}
	return e.Err.Error() + " (" + e.Reason + ")"
}

func (e *PlanExecError) Unwrap() error { return e.Err }

// AsPlanExecError extracts the typed failure, if err is one.
func AsPlanExecError(err error) (*PlanExecError, bool) {
	var pe *PlanExecError
	if errors.As(err, &pe) {
		return pe, true
	}
	return nil, false
}

// PlanFailureStatus is the HTTP status for a reason: the input is the
// user's to fix (422), a missing source is 404, the sandbox being down is
// 503, a generator failure is the upstream tool's (502).
func PlanFailureStatus(reason string) int {
	switch reason {
	case PlanFailSourceNotFound:
		return 404
	case PlanFailSourceEncrypted, PlanFailSourceScanned, PlanFailSourceDamaged,
		PlanFailSourceUnreadable, PlanFailInvalidPlan, PlanFailValidation:
		return 422
	case PlanFailSandbox:
		return 503
	case PlanFailGenerator, PlanFailTimeout:
		return 502
	default:
		return 500
	}
}

// unreadableSourceReason maps a source file that could not be read to the
// plan failure the card shows. ok=false for a readable (or still-reading)
// file.
func unreadableSourceReason(f *model.ConversationFile) (string, bool) {
	if f == nil {
		return "", false
	}
	why, bad := UnreadableReason(*f)
	if !bad {
		return "", false
	}
	switch why {
	case UnreadableEncrypted:
		return PlanFailSourceEncrypted, true
	case UnreadableScanned:
		return PlanFailSourceScanned, true
	case UnreadableDamaged:
		return PlanFailSourceDamaged, true
	default:
		return PlanFailSourceUnreadable, true
	}
}

// scriptFailure is the JSON a generator prints when it fails:
// {"error": "...", "error_type": "BadZipFile", "success": false}.
// error_type is FIX131; an older sandbox image prints only "error".
type scriptFailure struct {
	Success   *bool  `json:"success"`
	Error     string `json:"error"`
	ErrorType string `json:"error_type"`
}

// parseScriptFailure reads a generator's stdout. ok=false when stdout is not
// a failure envelope (empty, not JSON, or a success).
func parseScriptFailure(stdout []byte) (scriptFailure, bool) {
	var sf scriptFailure
	s := strings.TrimSpace(string(stdout))
	if s == "" || json.Unmarshal([]byte(s), &sf) != nil {
		return scriptFailure{}, false
	}
	if sf.Success != nil && *sf.Success {
		return scriptFailure{}, false
	}
	if sf.Error == "" && sf.ErrorType == "" {
		return scriptFailure{}, false
	}
	return sf, true
}

// sourceOpenErrorTypes are the exceptions python-docx / python-pptx /
// openpyxl raise when the file they were handed to EDIT is not a readable
// Office file — the source's fault, not the generator's.
var sourceOpenErrorTypes = map[string]bool{
	"BadZipFile":           true,
	"PackageNotFoundError": true,
	"InvalidFileException": true,
}

// generatorFailure turns a failed generator run into the typed error.
// stdout is what the script printed (may be nil), runErr the sandbox/run
// error (may be nil when the script ran and reported failure), timedOut
// whether the sandbox killed it at the wall clock.
func generatorFailure(sentinel error, role PlanSourceRole, src *model.ConversationFile,
	stdout []byte, runErr error, timedOut bool,
) *PlanExecError {
	pe := &PlanExecError{Reason: PlanFailGenerator, Role: role, Err: sentinel}
	switch {
	case runErr != nil && (errors.Is(runErr, sandbox.ErrUnavailable) ||
		errors.Is(runErr, ErrDocSandboxUnavailable) || errors.Is(runErr, ErrExcelSandboxUnavailable)):
		pe.Reason = PlanFailSandbox
		pe.Detail = redactScriptError(runErr.Error())
		return pe
	case timedOut:
		pe.Reason = PlanFailTimeout
		pe.Detail = "wall-clock timeout"
		return pe
	}
	if sf, ok := parseScriptFailure(stdout); ok {
		pe.Detail = strings.TrimSpace(sf.ErrorType + ": " + redactScriptError(sf.Error))
		if role == PlanSourceEdit && sourceOpenErrorTypes[sf.ErrorType] {
			pe.Reason = PlanFailSourceDamaged
			pe.Source = src
		}
		return pe
	}
	if runErr != nil {
		pe.Detail = redactScriptError(runErr.Error())
	} else {
		pe.Detail = "script reported failure without an error"
	}
	return pe
}

var (
	quotedSingle = regexp.MustCompile(`'[^']*'`)
	quotedDouble = regexp.MustCompile(`"[^"]*"`)
	spaceRun     = regexp.MustCompile(`\s+`)
)

// maxScriptErrorLog bounds a script error in the log line.
const maxScriptErrorLog = 240

// redactScriptError keeps an exception message loggable without user
// content: Python quotes the values it complains about ('สรุป', "report"),
// so quoted runs are cut, anything non-ASCII left (Thai text a message
// embeds unquoted) is dropped, and the result is bounded.
func redactScriptError(s string) string {
	s = quotedSingle.ReplaceAllString(s, "'…'")
	s = quotedDouble.ReplaceAllString(s, `"…"`)
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '…':
			b.WriteRune(r)
		case r == utf8.RuneError, r > 0x7e:
			// dropped: non-ASCII is where content hides
		case r < 0x20:
			b.WriteByte(' ')
		default:
			b.WriteRune(r)
		}
	}
	out := strings.TrimSpace(spaceRun.ReplaceAllString(b.String(), " "))
	if r := []rune(out); len(r) > maxScriptErrorLog {
		out = string(r[:maxScriptErrorLog]) + "…"
	}
	return out
}

// LogPlanFailure is the one structured log line of a failed execution:
// ids, the reason, how the source was used, and the script's own error —
// no plan text, no file names.
func LogPlanFailure(kind, format string, convID uuid.UUID, messageID *uuid.UUID, err error) {
	attrs := []any{
		"kind", kind,
		"format", format,
		"conversation_id", convID.String(),
	}
	if messageID != nil {
		attrs = append(attrs, "message_id", messageID.String())
	}
	if pe, ok := AsPlanExecError(err); ok {
		attrs = append(attrs, "reason", pe.Reason, "source_role", string(pe.Role))
		if pe.Source != nil {
			attrs = append(attrs, "source_file_id", pe.Source.ID.String(),
				"source_kind", OfficeKind(pe.Source.ContentType, pe.Source.OriginalFilename))
		}
		if pe.Detail != "" {
			attrs = append(attrs, "script_error", pe.Detail)
		}
	} else if err != nil {
		attrs = append(attrs, "reason", PlanFailInternal, "err", redactScriptError(err.Error()))
	}
	slog.Warn("plan_execute_failed", attrs...)
}
