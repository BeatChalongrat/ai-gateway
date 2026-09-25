// Package agentmarket is Agent Market (AM1): installable agents that work
// in their own chat workspace, ask the user back when the data is unclear,
// and deliver a report, a deck and tables.
//
// Two planes (docs/agent-market/ARCHITECTURE.md):
//
//   - control plane — this package's HTTP handlers, mounted by chat-edge
//     under /api/v1/agent-market ONLY when AGENT_MARKET_ENABLED is true.
//     It writes rows (installs, runs, answers, uploads) and reads them. It
//     never executes an agent step: no sandbox, no model call.
//   - execution plane — agentmarket/runner, run by the separate
//     cmd/agent-runner binary in its own container behind the compose
//     profile "agent-market". It claims queued runs, executes steps in the
//     stock sandbox image, calls the model gateway with its own key, and
//     writes step rows.
//
// The contract between them is the run row + the queue (agent_market.*
// tables), never a function call. Everything lives in schema agent_market;
// removal is docs/agent-market/REMOVAL.md.
package agentmarket

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is read from AGENT_MARKET_* environment variables. It is parsed
// here, not in chat-edge's config package, so the core config struct does
// not change and the whole feature's settings sit in one place.
type Config struct {
	// Enabled — AGENT_MARKET_ENABLED. Off means absent: chat-edge registers
	// no route, opens no pool and runs no query against agent_market.
	Enabled bool
	// AllowedEmails — AGENT_MARKET_ALLOWED_EMAILS: a comma list; empty lets
	// every signed-in user in once the feature is enabled (pilot switch).
	AllowedEmails []string
	// PoolMaxConns — the control plane's own Postgres pool (never chat's).
	PoolMaxConns int
	// MaxUploadBytes — per file.
	MaxUploadBytes int64
	// RunnerStaleAfter — a runner heartbeat older than this reads as down.
	RunnerStaleAfter time.Duration
	// StreamPoll — how often the event stream re-reads the thread.
	StreamPoll time.Duration
	// DefaultBudgetCredits — per install per month.
	DefaultBudgetCredits float64
}

// ConfigFromEnv reads the AGENT_MARKET_* variables with safe defaults.
// An unparsable boolean reads as false (never a boot failure of chat-edge).
func ConfigFromEnv() Config {
	c := Config{
		Enabled:          envBool("AGENT_MARKET_ENABLED"),
		PoolMaxConns:     envInt("AGENT_MARKET_POOL_MAX_CONNS", 3),
		MaxUploadBytes:   int64(envInt("AGENT_MARKET_MAX_UPLOAD_MB", 20)) << 20,
		RunnerStaleAfter: time.Duration(envInt("AGENT_MARKET_RUNNER_STALE_SECONDS", 45)) * time.Second,
		StreamPoll:       time.Duration(envInt("AGENT_MARKET_STREAM_POLL_MS", 800)) * time.Millisecond,
		// Platform credits (USAGE_CREDITS_PER_USD = 3,000,000 per USD): 3,000,000 ≈ 1 USD per install per month.
		DefaultBudgetCredits: float64(envInt("AGENT_MARKET_DEFAULT_BUDGET_CREDITS", 3000000)),
	}
	for _, e := range strings.Split(os.Getenv("AGENT_MARKET_ALLOWED_EMAILS"), ",") {
		if e = strings.ToLower(strings.TrimSpace(e)); e != "" {
			c.AllowedEmails = append(c.AllowedEmails, e)
		}
	}
	return c
}

// Allows reports whether a signed-in user may use the feature.
func (c Config) Allows(email string) bool {
	if len(c.AllowedEmails) == 0 {
		return true
	}
	email = strings.ToLower(strings.TrimSpace(email))
	for _, e := range c.AllowedEmails {
		if e == email {
			return true
		}
	}
	return false
}

func envBool(k string) bool {
	v, err := strconv.ParseBool(strings.TrimSpace(os.Getenv(k)))
	return err == nil && v
}

func envInt(k string, def int) int {
	v, err := strconv.Atoi(strings.TrimSpace(os.Getenv(k)))
	if err != nil || v <= 0 {
		return def
	}
	return v
}

// EnvString returns the variable or def when unset/blank.
func EnvString(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}

// EnvInt is envInt exported for the runner binary.
func EnvInt(k string, def int) int { return envInt(k, def) }

// EnvBool is envBool exported for the runner binary.
func EnvBool(k string) bool { return envBool(k) }
