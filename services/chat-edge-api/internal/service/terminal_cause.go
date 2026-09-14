package service

// FIX118 — how a turn ended, said once and stored.
//
// Before this file a run's ending was three different things in three
// places: the terminal SSE frame (message_complete / error / cancelled),
// the row's status (which overloads "error"), and nothing at all for the
// upstream stream simply closing — the pump turned a bare EOF into a
// synthetic Done, so a cut stream persisted as `completed` and any raw
// transport error reached the client as "stream error: <Go error text>"
// under the code INTERNAL_ERROR.
//
// This file owns two things:
//
//   - the CLOSED set of terminal causes (docs/contracts/terminal_cause.json
//     is the source of truth; terminal_cause_test.go pins this file to it
//     and the desktop and web trees pin themselves to the same file), and
//   - the one classifier that turns a transport-level error into a code
//     from the shared catalogue (backend/app/core/error_catalog.py). A
//     failure it cannot name lands on `unclassified_error` and says so in
//     the log, so the missing code is visible rather than invented.

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"os"
	"strings"
	"syscall"
)

// Terminal causes — the closed set. Stored in chat.messages_masked.terminal_cause
// (migration 043) and carried on the terminal frame as `cause`.
const (
	TerminalCauseCompleted          = "completed"
	TerminalCauseStoppedByUser      = "stopped_by_user"
	TerminalCauseError              = "error"
	TerminalCauseTimeout            = "timeout"
	TerminalCauseDisconnected       = "disconnected"
	TerminalCauseEOFWithoutTerminal = "eof_without_terminal"
	TerminalCauseInterrupted        = "interrupted"
)

// TerminalCauses lists the set in contract order.
var TerminalCauses = []string{
	TerminalCauseCompleted,
	TerminalCauseStoppedByUser,
	TerminalCauseError,
	TerminalCauseTimeout,
	TerminalCauseDisconnected,
	TerminalCauseEOFWithoutTerminal,
	TerminalCauseInterrupted,
}

// IsTerminalCause reports whether s is one of the closed set.
func IsTerminalCause(s string) bool {
	for _, c := range TerminalCauses {
		if c == s {
			return true
		}
	}
	return false
}

// Codes chat-edge mints itself for a failure the gateway did not name.
// Every one of these must exist in the shared catalogue with the same
// retryable verdict (pinned by terminal_cause_test.go), except
// CodeGenerationInterrupted, which is chat-edge's own resume code and is
// known to the web dictionary directly.
const (
	CodeStreamInterrupted     = "stream_interrupted"
	CodeProviderTimeout       = "provider_timeout"
	CodeUnclassifiedError     = "unclassified_error"
	CodeGenerationInterrupted = "GENERATION_INTERRUPTED"
)

// ErrStreamEOFWithoutTerminal is what the pump reports when the upstream
// stream closed without [DONE], without an error frame and without a
// finish reason. Before FIX118 that shape was a synthetic Done.
var ErrStreamEOFWithoutTerminal = errors.New("upstream stream ended without a terminal")

// TerminalInfo is what the handler learned about how the run ended, handed
// to the service's finish callback so the row can say the same thing the
// stream said.
type TerminalInfo struct {
	// Cause is one of TerminalCauses, or "" when the handler has nothing
	// to add (the finish callback then derives it from the status).
	Cause string
	// ErrorCode is the catalogue code of the terminal error frame, "" when
	// the turn did not fail.
	ErrorCode string
}

// StreamFailure is a classified transport-level failure.
type StreamFailure struct {
	Code      string
	Cause     string
	Message   string
	Retryable bool
	// Unmapped is true when nothing above could name the error and the
	// code is the catalogue's "we could not identify what" entry. The
	// caller logs the Go error type and text so the gap is visible.
	Unmapped bool
}

// streamFailureMessages carries the catalogue sentence for each code this
// file mints. The clients localise by code; the sentence is what a client
// that does not know the code shows, so it must be the catalogue's own.
var streamFailureMessages = map[string]string{
	CodeStreamInterrupted: "The response was interrupted. Please try again.",
	CodeProviderTimeout:   "The AI provider took too long to respond. Please try again.",
	CodeUnclassifiedError: "Something went wrong while the reply was being received, and we could not identify what. Write a new message to continue.",
}

// StreamFailureMessage is the catalogue sentence for a code this file
// mints ("" for any other code).
func StreamFailureMessage(code string) string { return streamFailureMessages[code] }

// ClassifyStreamError maps an error from the upstream read (or from the
// pump) to a catalogue code and a terminal cause.
//
// A user cancel is NOT classified here: the handler asks the stream
// registration first (finishStream's UserCancelled branch) and never
// reaches this for a run a person stopped. What arrives is therefore a
// stream that ended by itself.
func ClassifyStreamError(err error) StreamFailure {
	switch {
	case err == nil:
		return StreamFailure{Code: CodeUnclassifiedError, Cause: TerminalCauseError, Message: streamFailureMessages[CodeUnclassifiedError], Unmapped: true}
	case errors.Is(err, ErrStreamEOFWithoutTerminal):
		return StreamFailure{Code: CodeStreamInterrupted, Cause: TerminalCauseEOFWithoutTerminal, Message: streamFailureMessages[CodeStreamInterrupted], Retryable: true}
	case isTimeout(err):
		return StreamFailure{Code: CodeProviderTimeout, Cause: TerminalCauseTimeout, Message: streamFailureMessages[CodeProviderTimeout], Retryable: true}
	case isDisconnect(err):
		return StreamFailure{Code: CodeStreamInterrupted, Cause: TerminalCauseDisconnected, Message: streamFailureMessages[CodeStreamInterrupted], Retryable: true}
	default:
		return StreamFailure{Code: CodeUnclassifiedError, Cause: TerminalCauseError, Message: streamFailureMessages[CodeUnclassifiedError], Unmapped: true}
	}
}

func isTimeout(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return true
	}
	return false
}

// isDisconnect names the shapes a broken provider connection takes in Go:
// a context cancelled by something other than a person (the handler has
// already ruled the person out), an unexpected EOF, a reset or closed
// socket, a TLS or HTTP/2 transport failure, and a scanner line over its
// buffer (the read is abandoned mid-stream either way).
func isDisconnect(err error) bool {
	if errors.Is(err, context.Canceled) || errors.Is(err, io.ErrUnexpectedEOF) ||
		errors.Is(err, io.ErrClosedPipe) || errors.Is(err, net.ErrClosed) ||
		errors.Is(err, bufio.ErrTooLong) ||
		errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.ECONNABORTED) ||
		errors.Is(err, syscall.EPIPE) {
		return true
	}
	var oe *net.OpError
	if errors.As(err, &oe) {
		return true
	}
	msg := strings.ToLower(err.Error())
	for _, needle := range []string{
		"connection reset", "broken pipe", "unexpected eof", "http2: ", "http2 stream",
		"tls: ", "connection closed", "server closed idle connection", "transport connection broken",
		"stream error", "response body closed",
	} {
		if strings.Contains(msg, needle) {
			return true
		}
	}
	return false
}

// CauseForStatus derives the terminal cause a finish callback records when
// the handler did not name one: the status is the only witness.
func CauseForStatus(status string) string {
	switch status {
	case "completed":
		return TerminalCauseCompleted
	case "cancelled":
		return TerminalCauseStoppedByUser
	case "error":
		return TerminalCauseError
	default:
		return "" // waiting_user (parked) has not ended; pending/streaming are not terminal
	}
}
