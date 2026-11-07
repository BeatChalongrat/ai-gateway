// Package httpx holds tiny HTTP helpers shared across the gateway.
package httpx

import (
	"encoding/json"
	"net/http"
)

// WriteError emits the gateway's standard JSON error envelope. The
// shape ({"error": {"code", "message"}}) matches the Python backend's
// ErrorResponse so clients parse gateway-originated and Python-
// originated errors identically.
func WriteError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": map[string]string{
			"code":    code,
			"message": message,
		},
	})
}
