package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// ── The trail every admin change leaves (AM7) ────────────────────────────
//
// Every change an admin makes to Agent Market — the organisation's settings
// (the document-egress switch above all), the access list, departments and
// budgets, the catalog, the problems queue, an opening of someone's run, a
// removal of a run's files, the mail relay — is written to the admin audit
// log the auditors already read (admin-ui /audit-log, the gateway's
// admin_audit_log table): who, when, which setting, from what to what.
//
// The log lives in the gateway's database, so chat-edge writes it through
// the backend's internal endpoint (POST /internal/admin-audit), as a
// trusted service (a short-lived service JWT). A change the log cannot take
// does not happen: the row is changed in a transaction that commits only
// after the log accepted the entry (audited, below).
//
// Search works on the entry's action and resource: action
// "agent_market.org.documents_may_leave" answers "who let our documents go
// to an outside model"; the details carry the old and the new value.

// AuditEntry is one line of the admin audit log.
type AuditEntry struct {
	Actor        string         `json:"actor_key_id"` // "session:<admin-ui user id>", or "chat-user:<uuid>" (the chat app's page, before AM7 removed it)
	Tenant       string         `json:"tenant_id"`
	Action       string         `json:"action"` // always "agent_market.…"
	ResourceType string         `json:"resource_type"`
	ResourceID   string         `json:"resource_id"` // ≤ 36 characters
	Details      map[string]any `json:"details,omitempty"`
	Outcome      string         `json:"outcome"` // success | failed
}

// Auditor takes entries into the admin audit log, all or none.
type Auditor interface {
	Record(ctx context.Context, entries []AuditEntry) error
}

// SetAuditor sets where admin changes are logged (chat-edge's main does, at start).
func (a *API) SetAuditor(x Auditor) { a.auditor = x }

// ErrAuditUnavailable: the audit log did not take the entry, so nothing changed.
var ErrAuditUnavailable = errors.New("agentmarket: the admin audit log did not take the entry")

// auditTenant is the gateway's sentinel for an entry that belongs to no one tenant.
const auditTenant = "__global__"

// HTTPAuditor writes to the backend's internal audit endpoint.
type HTTPAuditor struct {
	URL            string                 // e.g. http://backend:8080/internal/admin-audit
	Bearer         func() (string, error) // a fresh service JWT per call
	InternalSecret string                 // X-Internal-Service-Secret, when set
	Client         *http.Client
}

// NewHTTPAuditor makes an auditor for the backend at base (its root URL).
func NewHTTPAuditor(base string, bearer func() (string, error), internalSecret string) *HTTPAuditor {
	return &HTTPAuditor{URL: strings.TrimRight(base, "/") + "/internal/admin-audit", Bearer: bearer,
		InternalSecret: internalSecret, Client: &http.Client{Timeout: 8 * time.Second}}
}

// Record sends the entries in one request; the backend writes them in one transaction.
func (h *HTTPAuditor) Record(ctx context.Context, entries []AuditEntry) error {
	if len(entries) == 0 {
		return nil
	}
	body, err := json.Marshal(map[string]any{"entries": entries})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.URL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if h.Bearer != nil {
		tok, err := h.Bearer()
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	if h.InternalSecret != "" {
		req.Header.Set("X-Internal-Service-Secret", h.InternalSecret)
	}
	// the backend's service-JWT path names the caller it acts for
	req.Header.Set("X-User-ID", "agent-market-audit")
	req.Header.Set("X-Tenant-ID", auditTenant)
	resp, err := h.Client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<14))
	if resp.StatusCode != http.StatusCreated {
		return fmt.Errorf("admin audit: status %d", resp.StatusCode)
	}
	return nil
}

// DiscardAuditor takes every entry and keeps none (tests of other features).
type DiscardAuditor struct{}

// Record does nothing.
func (DiscardAuditor) Record(context.Context, []AuditEntry) error { return nil }

// record hands entries to the log; a log that is missing or refuses stops the change.
func (a *API) record(ctx context.Context, entries []AuditEntry) error {
	if len(entries) == 0 {
		return nil
	}
	if a.auditor == nil {
		a.log.Error("admin change refused: no audit log is configured", "action", entries[0].Action)
		return ErrAuditUnavailable
	}
	if err := a.auditor.Record(ctx, entries); err != nil {
		a.log.Error("admin change refused: the audit log did not take it", "action", entries[0].Action, "err", err)
		return ErrAuditUnavailable
	}
	return nil
}

// recordFailed says, after the fact, that logged changes did not land (best effort).
func (a *API) recordFailed(ctx context.Context, entries []AuditEntry) {
	if a.auditor == nil || len(entries) == 0 {
		return
	}
	failed := make([]AuditEntry, len(entries))
	for i, e := range entries {
		e.Outcome = "failed"
		failed[i] = e
	}
	if err := a.auditor.Record(context.WithoutCancel(ctx), failed); err != nil {
		a.log.Error("could not log that an admin change failed", "action", entries[0].Action, "err", err)
	}
}

// audited makes a change inside one transaction and commits it only after
// the audit log took the entries the change returns.
func (a *API) audited(ctx context.Context, change func(tx pgx.Tx) ([]AuditEntry, error)) error {
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	entries, err := change(tx)
	if err != nil {
		return err
	}
	if err := a.record(ctx, entries); err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		a.recordFailed(ctx, entries)
		return err
	}
	return nil
}

// actorOf is who makes an admin change, as the log keys it, and how they are named in it.
func actorOf(r *http.Request) (string, map[string]any) {
	if act, ok := adminActorOf(r.Context()); ok {
		return "session:" + act.ID, map[string]any{"by": act.Email, "by_name": act.Name, "admin_role": act.Role, "via": "admin-ui"}
	}
	c, _ := caller(r)
	return "chat-user:" + c.UserID.String(), map[string]any{"by": normEmail(c.Email), "via": "chat"}
}

// entry is one line of the log for this request's admin.
func entry(r *http.Request, action, resourceType, resourceID string, details map[string]any) AuditEntry {
	actor, who := actorOf(r)
	d := map[string]any{"org": orgOr(accessOf(r).OrgID)}
	for k, v := range who {
		d[k] = v
	}
	for k, v := range details {
		d[k] = v
	}
	if len(resourceID) > 36 {
		resourceID = resourceID[:36]
	}
	return AuditEntry{Actor: actor, Tenant: auditTenant, Action: action, ResourceType: resourceType,
		ResourceID: resourceID, Details: d, Outcome: "success"}
}

// refusal is a request refused inside a change (the transaction rolls back).
type refusal struct {
	status int
	code   string
	vars   []string
}

func (r refusal) Error() string { return "refused: " + r.code }

// failChange answers a change that did not happen: a refusal as its sentence,
// anything else as fail() does.
func (a *API) failChange(w http.ResponseWriter, r *http.Request, err error, what string) {
	var rf refusal
	if errors.As(err, &rf) {
		a.sayTo(w, r, rf.status, rf.code, rf.vars...)
		return
	}
	a.fail(w, err, what)
}

// orgLabels name each organisation setting for a reader of the log (an entry
// is written once, in the language of the admin's request that made it).
var orgLabels = []struct {
	key   string
	label wording
}{
	{"name", "ชื่อองค์กร"},
	{"access", "ใครใช้ Agent Market ได้"},
	{"domains", "โดเมนอีเมลขององค์กร"},
	{"user_monthly_credits", "งบเครดิตต่อคนต่อเดือน"},
	{"contact_name", "ชื่อผู้ติดต่อ"},
	{"contact_email", "อีเมลผู้ติดต่อ"},
	{"documents_may_leave", "เอกสารส่งไปโมเดลนอกองค์กรได้"},
	{"retention_years", "ระยะเก็บเอกสารบัญชี (ปี)"},
	{"storage_warn_mb", "เตือนเมื่อพื้นที่เก็บไฟล์ของแต่ละคนเกิน (MB)"},
	{"assistant_line", "บอกบนเอกสารว่าผู้ช่วย AI ขององค์กรร่างคำอธิบาย"}, // AM23 §3.2
}

// wireOf is a value as the API sends it (what the log records as from / to).
func wireOf(v any) map[string]any {
	b, _ := json.Marshal(v)
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	return m
}

// orgChanges are the log's entries for one change of the organisation's
// settings: one per setting that moved, with its old and new value.
func orgChanges(r *http.Request, before, after OrgSettings) []AuditEntry {
	b, a := wireOf(before), wireOf(after)
	var out []AuditEntry
	for _, f := range orgLabels {
		from, _ := json.Marshal(b[f.key])
		to, _ := json.Marshal(a[f.key])
		if bytes.Equal(from, to) {
			continue
		}
		out = append(out, entry(r, "agent_market.org."+f.key, "agent_market_org", orgOr(after.OrgID),
			map[string]any{"setting": f.key, "label": T(LangOf(r.Context()), f.label), "from": b[f.key], "to": a[f.key]}))
	}
	return out
}

// jsonText is a value as JSON text (to compare what the log records).
func jsonText(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
