package service

// FIX118 — the terminal cause is ONE closed set across three trees, and
// the codes chat-edge mints for a transport failure are catalogue codes.
//
// docs/contracts/terminal_cause.json is the source of truth; this test
// pins the Go constants to it (apps/desktop/test/terminal-cause.test.ts
// and apps/web-chat/src/lib/terminalCause.test.ts pin the other two
// trees to the same file). backend/app/core/error_catalog.py is the
// catalogue; every code this package mints must exist there with the
// same retryable verdict and the same sentence, except chat-edge's own
// resume code, which the web dictionary knows directly.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"regexp"
	"syscall"
	"testing"
)

const terminalContractPath = "../../../../docs/contracts/terminal_cause.json"
const errorCatalogPath = "../../../../backend/app/core/error_catalog.py"

type terminalContract struct {
	Causes         []string `json:"causes"`
	TransportCodes map[string]struct {
		Code      string `json:"code"`
		Retryable bool   `json:"retryable"`
	} `json:"transport_codes"`
	StatusByCause map[string]string `json:"message_status_by_cause"`
}

func loadTerminalContract(t *testing.T) terminalContract {
	t.Helper()
	raw, err := os.ReadFile(terminalContractPath)
	if err != nil {
		t.Fatalf("contract file: %v", err)
	}
	var c terminalContract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("contract JSON: %v", err)
	}
	return c
}

var catalogLine = regexp.MustCompile(`ErrorInfo\("([a-z_]+)",\s*"([^"]*)",\s*(True|False)\)`)

func loadCatalog(t *testing.T) map[string]struct {
	message   string
	retryable bool
} {
	t.Helper()
	raw, err := os.ReadFile(errorCatalogPath)
	if err != nil {
		t.Skipf("catalogue not in this checkout: %v", err)
	}
	out := map[string]struct {
		message   string
		retryable bool
	}{}
	for _, m := range catalogLine.FindAllStringSubmatch(string(raw), -1) {
		out[m[1]] = struct {
			message   string
			retryable bool
		}{m[2], m[3] == "True"}
	}
	if len(out) < 30 {
		t.Fatalf("catalogue parse found only %d codes", len(out))
	}
	return out
}

func TestTerminalCause_PinnedToContract(t *testing.T) {
	c := loadTerminalContract(t)
	if fmt.Sprint(c.Causes) != fmt.Sprint(TerminalCauses) {
		t.Fatalf("Go causes %v != contract %v", TerminalCauses, c.Causes)
	}
	for _, cause := range c.Causes {
		if !IsTerminalCause(cause) {
			t.Fatalf("IsTerminalCause(%q) = false", cause)
		}
	}
	if IsTerminalCause("failed") || IsTerminalCause("") {
		t.Fatal("a value outside the set must not be a terminal cause")
	}
	want := map[string]string{
		"eof_without_terminal": CodeStreamInterrupted,
		"disconnected":         CodeStreamInterrupted,
		"timeout":              CodeProviderTimeout,
		"interrupted":          CodeGenerationInterrupted,
		"unclassified":         CodeUnclassifiedError,
	}
	for k, code := range want {
		got, ok := c.TransportCodes[k]
		if !ok || got.Code != code {
			t.Fatalf("contract transport_codes[%s] = %+v, Go mints %s", k, got, code)
		}
	}
	// The status a cause implies is the status the finish callbacks write.
	for cause, status := range c.StatusByCause {
		if cause == TerminalCauseCompleted && status != "completed" ||
			cause == TerminalCauseStoppedByUser && status != "cancelled" ||
			cause != TerminalCauseCompleted && cause != TerminalCauseStoppedByUser && status != "error" {
			t.Fatalf("contract status for %s = %s", cause, status)
		}
	}
}

func TestTerminalCause_MintedCodesAreCatalogueCodes(t *testing.T) {
	cat := loadCatalog(t)
	c := loadTerminalContract(t)
	for k, tc := range c.TransportCodes {
		if tc.Code == CodeGenerationInterrupted {
			continue // chat-edge's own resume code, not a gateway code
		}
		entry, ok := cat[tc.Code]
		if !ok {
			t.Fatalf("%s: code %q is not in the catalogue", k, tc.Code)
		}
		if entry.retryable != tc.Retryable {
			t.Fatalf("%s: catalogue says retryable=%v, contract says %v", tc.Code, entry.retryable, tc.Retryable)
		}
		if msg := StreamFailureMessage(tc.Code); msg != entry.message {
			t.Fatalf("%s: Go sentence %q != catalogue %q", tc.Code, msg, entry.message)
		}
	}
}

type fakeTimeoutErr struct{}

func (fakeTimeoutErr) Error() string   { return "i/o timeout" }
func (fakeTimeoutErr) Timeout() bool   { return true }
func (fakeTimeoutErr) Temporary() bool { return true }

// The classifier, case by case — the shapes the card names: a transport
// error with no code, a timeout, a disconnect, and a bare EOF. (An error
// WITH a code — a 429 or a 5xx — arrives as a provider error frame and is
// forwarded by the drain untouched; stream_terminal_test.go covers that.)
func TestClassifyStreamError(t *testing.T) {
	cases := []struct {
		name     string
		err      error
		code     string
		cause    string
		retry    bool
		unmapped bool
	}{
		{"eof without terminal", ErrStreamEOFWithoutTerminal, CodeStreamInterrupted, TerminalCauseEOFWithoutTerminal, true, false},
		{"wrapped eof sentinel", fmt.Errorf("read: %w", ErrStreamEOFWithoutTerminal), CodeStreamInterrupted, TerminalCauseEOFWithoutTerminal, true, false},
		{"deadline", context.DeadlineExceeded, CodeProviderTimeout, TerminalCauseTimeout, true, false},
		{"net timeout", fakeTimeoutErr{}, CodeProviderTimeout, TerminalCauseTimeout, true, false},
		{"unexpected eof", io.ErrUnexpectedEOF, CodeStreamInterrupted, TerminalCauseDisconnected, true, false},
		{"connection reset", fmt.Errorf("read tcp: %w", syscall.ECONNRESET), CodeStreamInterrupted, TerminalCauseDisconnected, true, false},
		{"net.OpError", &net.OpError{Op: "read", Err: errors.New("closed")}, CodeStreamInterrupted, TerminalCauseDisconnected, true, false},
		{"http2 stream error text", errors.New("stream error: stream ID 5; INTERNAL_ERROR; received from peer"), CodeStreamInterrupted, TerminalCauseDisconnected, true, false},
		{"context canceled (not by a person)", context.Canceled, CodeStreamInterrupted, TerminalCauseDisconnected, true, false},
		{"nobody classified it", errors.New("json: cannot unmarshal"), CodeUnclassifiedError, TerminalCauseError, false, true},
		{"nil", nil, CodeUnclassifiedError, TerminalCauseError, false, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := ClassifyStreamError(tc.err)
			if f.Code != tc.code || f.Cause != tc.cause || f.Retryable != tc.retry || f.Unmapped != tc.unmapped {
				t.Fatalf("got %+v, want code=%s cause=%s retry=%v unmapped=%v", f, tc.code, tc.cause, tc.retry, tc.unmapped)
			}
			if f.Message == "" || f.Message != StreamFailureMessage(f.Code) {
				t.Fatalf("message must be the catalogue sentence for %s, got %q", f.Code, f.Message)
			}
			if tc.err != nil && f.Message == tc.err.Error() {
				t.Fatal("the Go error text must never be the message a person reads")
			}
		})
	}
}

func TestCauseForStatus(t *testing.T) {
	for status, want := range map[string]string{
		"completed": TerminalCauseCompleted, "cancelled": TerminalCauseStoppedByUser,
		"error": TerminalCauseError, "waiting_user": "", "streaming": "", "pending": "",
	} {
		if got := CauseForStatus(status); got != want {
			t.Fatalf("CauseForStatus(%s) = %q, want %q", status, got, want)
		}
	}
}
