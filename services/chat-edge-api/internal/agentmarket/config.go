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
	// AllowedEmails — AGENT_MARKET_ALLOWED_EMAILS: the AM1 pilot list. Since
	// AM3 it only seeds the organisation's access list the day the
	// organisation row is created (empty = access "everyone", as before);
	// from then on the admins keep the list in the database (org.go).
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
	// AM3. AdminEmails — AGENT_MARKET_ADMIN_EMAILS: always admins of the
	// organisation (break-glass; they can never lock themselves out). When
	// unset, the people in AGENT_MARKET_ALLOWED_EMAILS become the first admins
	// the day the organisation is created (org.go) — once, not every start.
	AdminEmails []string
	// OrgDomains — AGENT_MARKET_ORG_DOMAINS: the organisation's email
	// domains (who counts as "in the organisation" for sharing a run). Empty:
	// the domains of the allowed and admin emails.
	OrgDomains []string
	// UserBudgetCredits — the organisation's first per-person monthly budget
	// (admins change it later; 3,000,000 credits ≈ 1 USD).
	UserBudgetCredits float64
	// CreditsPerUSD / THBPerUSD — how the catalog shows a run's cost in baht.
	CreditsPerUSD float64
	THBPerUSD     float64
	// Mail (AM6) sends an e-mail the person approved (email.go). There is no
	// mail connector in the product: nil, and a draft says it cannot be sent.
	Mail MailConnector
	// AuditURL (AM7) — AGENT_MARKET_AUDIT_URL: the backend whose internal
	// endpoint writes the admin audit log (audit.go). Every admin change is
	// logged there first; with the backend unreachable, no admin change is made.
	AuditURL string
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
		UserBudgetCredits:    float64(envInt("AGENT_MARKET_USER_BUDGET_CREDITS", 3000000)),
		CreditsPerUSD:        float64(envInt("USAGE_CREDITS_PER_USD", 3000000)),
		THBPerUSD:            float64(envInt("AGENT_MARKET_THB_PER_USD", 35)),
	}
	c.AuditURL = EnvString("AGENT_MARKET_AUDIT_URL", "http://backend:8080")
	c.AllowedEmails = emailList(os.Getenv("AGENT_MARKET_ALLOWED_EMAILS"))
	c.AdminEmails = emailList(os.Getenv("AGENT_MARKET_ADMIN_EMAILS"))
	for _, d := range strings.Split(os.Getenv("AGENT_MARKET_ORG_DOMAINS"), ",") {
		if d = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(d), "@"))); d != "" {
			c.OrgDomains = append(c.OrgDomains, d)
		}
	}
	return c
}

func emailList(v string) []string {
	var out []string
	for _, e := range strings.Split(v, ",") {
		if e = strings.ToLower(strings.TrimSpace(e)); e != "" {
			out = append(out, e)
		}
	}
	return out
}

// IsEnvAdmin reports a break-glass admin (AGENT_MARKET_ADMIN_EMAILS).
func (c Config) IsEnvAdmin(email string) bool {
	email = strings.ToLower(strings.TrimSpace(email))
	for _, e := range c.AdminEmails {
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
