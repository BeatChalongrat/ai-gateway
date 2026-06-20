package service

// Phase 4A.2.7 — tenant-scoped feature gating (Go mirror of the Python
// gateway's feature_scope). A canary-sensitive feature is effective only when
// its flag is on AND the tenant is in scope:
//
//	off       → no tenant (default; safe)
//	allowlist → tenant appears as the tenant part of a "tenant/project" entry
//	global    → any non-empty tenant
//
// The Python gateway is the AUTHORITATIVE per-tenant gate (it withholds the
// canonical artifact for non-allowlisted tenant/project, so chat-edge receives
// nothing to persist and falls back to legacy). This helper is defense-in-depth
// + parity; chat-edge matches on the tenant identifier it holds (project lives
// gateway-side). Fail-closed on unknown mode / blank tenant / malformed entries.

import "strings"

const (
	scopeOff       = "off"
	scopeAllowlist = "allowlist"
	scopeGlobal    = "global"
)

// FeatureScope holds the parsed scope config (set once at wiring time).
type FeatureScope struct {
	mode    string
	tenants map[string]struct{} // tenant parts of the allowlist
}

// NewFeatureScope parses “mode“ + “allowlist“ ("tenant/project,...").
// Unknown mode → off. Malformed entries (no single '/', blank halves) skipped.
func NewFeatureScope(mode, allowlist string) FeatureScope {
	m := strings.ToLower(strings.TrimSpace(mode))
	if m != scopeAllowlist && m != scopeGlobal {
		m = scopeOff
	}
	tenants := map[string]struct{}{}
	for _, chunk := range strings.Split(allowlist, ",") {
		c := strings.TrimSpace(chunk)
		if c == "" || strings.Count(c, "/") != 1 {
			continue
		}
		parts := strings.SplitN(c, "/", 2)
		t, p := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if t != "" && p != "" {
			tenants[t] = struct{}{}
		}
	}
	return FeatureScope{mode: m, tenants: tenants}
}

// TenantInScope reports whether the tenant passes the scope gate. “tenantID“
// is the human tenant identifier (e.g. "acme-corp-001").
func (fs FeatureScope) TenantInScope(tenantID string) bool {
	t := strings.TrimSpace(tenantID)
	if t == "" {
		return false
	}
	switch fs.mode {
	case scopeGlobal:
		return true
	case scopeAllowlist:
		_, ok := fs.tenants[t]
		return ok
	default: // off / unknown
		return false
	}
}

// Effective combines a global flag with the tenant scope.
func (fs FeatureScope) Effective(globalFlag bool, tenantID string) bool {
	return globalFlag && fs.TenantInScope(tenantID)
}
