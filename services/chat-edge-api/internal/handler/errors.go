package handler

import (
	"encoding/json"
	"net/http"
)

// errorEnvelope is the wire-format error envelope used across the
// whole API. Matches the contract documented in the Phase 1 spec.
type errorEnvelope struct {
	Error struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

// WriteError emits a JSON error response with the consistent
// envelope. Used by every handler so the wire format stays stable
// regardless of which subsystem failed.
func WriteError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	env := errorEnvelope{}
	env.Error.Code = code
	env.Error.Message = message
	_ = json.NewEncoder(w).Encode(env)
}

// Common error codes — pinned as constants so misspellings show up
// at compile time, not at runtime when an alert rule fails to match.
const (
	CodeUnauthorized     = "UNAUTHORIZED"
	CodeForbidden        = "FORBIDDEN"
	CodeConflict         = "CONFLICT"
	CodeValidationError  = "VALIDATION_ERROR"
	CodeNotFound         = "NOT_FOUND"
	CodeInternalError    = "INTERNAL_ERROR"
	CodeBadRequest       = "BAD_REQUEST"
)
