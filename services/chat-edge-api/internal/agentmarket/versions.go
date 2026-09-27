package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── Versions (AM2 Phase G) ───────────────────────────────────────────────
//
// An install is pinned to the definition it installed: its runs execute
// that version's package with the engine it was released with (the runner
// keeps every engine zip by checksum), whatever newer version the catalog
// shows. A changed package must carry a new version (the runner refuses to
// overwrite a released one).
//
//	GET  /installs/{id}/upgrade    the newest version and what changes:
//	                               manifest keys, AGENT.md lines,
//	                               capabilities, default settings
//	POST /installs/{id}/upgrade    run the new version's self-test on this
//	                               install (its sample data and expectations);
//	                               the runner switches the install only when
//	                               it passes, keeping the old version to roll
//	                               back to
//	POST /installs/{id}/rollback   point the install back at the version the
//	                               last upgrade replaced; historical runs keep
//	                               their own snapshot

// DefinitionBySlugVersion returns one released version.
func (s *Store) DefinitionBySlugVersion(ctx context.Context, slug, version string) (Definition, error) {
	d, err := scanDef(s.Pool.QueryRow(ctx, `SELECT `+defCols+` FROM agent_market.agent_definitions
		WHERE slug = $1 AND version = $2`, slug, version))
	if errors.Is(err, pgx.ErrNoRows) {
		return d, ErrNotFound
	}
	return d, err
}

// InsertDefinition registers a new version (never overwrites one).
func (s *Store) InsertDefinition(ctx context.Context, d Definition) (Definition, error) {
	return scanDef(s.Pool.QueryRow(ctx, `
		INSERT INTO agent_market.agent_definitions (slug, version, name, manifest, instructions, checksum, engine_checksum)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING `+defCols, d.Slug, d.Version, d.Name, d.Manifest, d.Instructions, d.Checksum, d.EngineChecksum))
}

// SwitchInstall points an install at another version; the one it leaves
// becomes the rollback target.
func (s *Store) SwitchInstall(ctx context.Context, install uuid.UUID, to Definition, bindings json.RawMessage) (Install, error) {
	return scanInstall(s.Pool.QueryRow(ctx, `UPDATE agent_market.agent_installs SET
			previous_definition_id = definition_id, definition_id = $2, definition_version = $3, checksum = $4,
			capability_bindings = COALESCE($5, capability_bindings), binding_version = binding_version + 1, updated_at = now()
		WHERE id = $1 AND status = 'active' AND definition_id <> $2
		RETURNING `+installCols, install, to.ID, to.Version, to.Checksum, nullIfEmpty(bindings)))
}

// Change is one difference between two versions.
type Change struct {
	What   string `json:"what"` // manifest | instructions | capability | setting
	Key    string `json:"key"`
	Before string `json:"before,omitempty"`
	After  string `json:"after,omitempty"`
}

// VersionDiff lists what an upgrade changes, in words a person can check.
func VersionDiff(from, to Definition) []Change {
	var out []Change
	var a, b map[string]json.RawMessage
	_ = json.Unmarshal(from.Manifest, &a)
	_ = json.Unmarshal(to.Manifest, &b)
	keys := map[string]bool{}
	for k := range a {
		keys[k] = true
	}
	for k := range b {
		keys[k] = true
	}
	var sorted []string
	for k := range keys {
		sorted = append(sorted, k)
	}
	sort.Strings(sorted)
	for _, k := range sorted {
		if k == "version" || k == "capabilities" || k == "install" {
			continue
		}
		if !JSONEqual(a[k], b[k]) {
			out = append(out, Change{What: "manifest", Key: k, Before: compactJSON(a[k]), After: compactJSON(b[k])})
		}
	}
	ma, _ := ParseManifest(from.Manifest)
	mb, _ := ParseManifest(to.Manifest)
	for _, c := range listChanges(ma.Capabilities.Required, mb.Capabilities.Required) {
		out = append(out, Change{What: "capability", Key: "required", Before: c[0], After: c[1]})
	}
	for _, c := range listChanges(ma.Capabilities.Optional, mb.Capabilities.Optional) {
		out = append(out, Change{What: "capability", Key: "optional", Before: c[0], After: c[1]})
	}
	dk := map[string]bool{}
	for k := range ma.Install.Defaults {
		dk[k] = true
	}
	for k := range mb.Install.Defaults {
		dk[k] = true
	}
	var dks []string
	for k := range dk {
		dks = append(dks, k)
	}
	sort.Strings(dks)
	for _, k := range dks {
		if !JSONEqual(ma.Install.Defaults[k], mb.Install.Defaults[k]) {
			out = append(out, Change{What: "setting", Key: k, Before: compactJSON(ma.Install.Defaults[k]),
				After: compactJSON(mb.Install.Defaults[k])})
		}
	}
	if !stringsEqual(ma.Install.Questions, mb.Install.Questions) {
		out = append(out, Change{What: "setting", Key: "install.questions", Before: strings.Join(ma.Install.Questions, ", "),
			After: strings.Join(mb.Install.Questions, ", ")})
	}
	for _, l := range lineDiff(from.Instructions, to.Instructions, 200) {
		out = append(out, l)
	}
	return out
}

func stringsEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// listChanges: removed items as (x, ""), added as ("", x).
func listChanges(a, b []string) [][2]string {
	in := func(xs []string, x string) bool {
		for _, v := range xs {
			if v == x {
				return true
			}
		}
		return false
	}
	var out [][2]string
	for _, x := range a {
		if !in(b, x) {
			out = append(out, [2]string{x, ""})
		}
	}
	for _, x := range b {
		if !in(a, x) {
			out = append(out, [2]string{"", x})
		}
	}
	return out
}

func compactJSON(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var buf bytes.Buffer
	if json.Compact(&buf, raw) != nil {
		return string(raw)
	}
	s := buf.String()
	if len([]rune(s)) > 400 {
		s = string([]rune(s)[:400]) + "…"
	}
	return s
}

// lineDiff is AGENT.md's removed and added lines (longest common subsequence).
func lineDiff(a, b string, limit int) []Change {
	x, y := strings.Split(a, "\n"), strings.Split(b, "\n")
	n, m := len(x), len(y)
	if n*m > 4_000_000 {
		return []Change{{What: "instructions", Key: "AGENT.md", Before: "(ยาวเกินกว่าจะเทียบทีละบรรทัด)", After: "(changed)"}}
	}
	lcs := make([][]int, n+1)
	for i := range lcs {
		lcs[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			if x[i] == y[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else if lcs[i+1][j] >= lcs[i][j+1] {
				lcs[i][j] = lcs[i+1][j]
			} else {
				lcs[i][j] = lcs[i][j+1]
			}
		}
	}
	var out []Change
	i, j := 0, 0
	for (i < n || j < m) && len(out) < limit {
		switch {
		case i < n && j < m && x[i] == y[j]:
			i++
			j++
		case j < m && (i == n || lcs[i][j+1] >= lcs[i+1][j]):
			out = append(out, Change{What: "instructions", Key: "AGENT.md", After: y[j]})
			j++
		default:
			out = append(out, Change{What: "instructions", Key: "AGENT.md", Before: x[i]})
			i++
		}
	}
	return out
}

func (a *API) upgradeInfo(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	cur, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		a.fail(w, err, "upgrade")
		return
	}
	out := map[string]any{"current": defSummary(cur), "available": nil}
	if in.PreviousDefinitionID != nil {
		if prev, err := a.store.DefinitionByID(ctx, *in.PreviousDefinitionID); err == nil {
			out["previous"] = defSummary(prev)
		}
	}
	newest, err := a.store.DefinitionBySlug(ctx, in.Slug)
	if err == nil && newest.ID != cur.ID && versionLess(cur.Version, newest.Version) {
		out["available"] = defSummary(newest)
		out["changes"] = VersionDiff(cur, newest)
	}
	writeJSON(w, http.StatusOK, out)
}

func defSummary(d Definition) map[string]any {
	return map[string]any{"id": d.ID, "version": d.Version, "name": d.Name, "checksum": d.Checksum, "released_at": d.UpdatedAt}
}

// versionLess compares dotted numeric versions ("1.9.0" < "1.10.0").
func versionLess(a, b string) bool {
	pa, pb := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < len(pa) || i < len(pb); i++ {
		var x, y int
		if i < len(pa) {
			x = atoiSafe(pa[i])
		}
		if i < len(pb) {
			y = atoiSafe(pb[i])
		}
		if x != y {
			return x < y
		}
	}
	return false
}

func atoiSafe(s string) int {
	n := 0
	for _, r := range s {
		if r < '0' || r > '9' {
			break
		}
		n = n*10 + int(r-'0')
	}
	return n
}

func (a *API) startUpgrade(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	var body struct {
		DefinitionID uuid.UUID `json:"definition_id"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil || body.DefinitionID == uuid.Nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	to, err := a.store.DefinitionByID(ctx, body.DefinitionID)
	if err != nil || to.Slug != in.Slug || to.ID == in.DefinitionID || to.Status != "active" {
		say(w, http.StatusBadRequest, "BAD_VERSION")
		return
	}
	m, err := ParseManifest(to.Manifest)
	if err != nil {
		a.fail(w, err, "upgrade")
		return
	}
	st, err := a.store.LatestRunner(ctx, a.cfg.RunnerStaleAfter)
	if err != nil || !st.Healthy {
		a.sayTo(w, r, http.StatusServiceUnavailable, "RUNNER_DOWN")
		return
	}
	var caps map[string]Binding
	_ = json.Unmarshal(st.Capabilities, &caps)
	res, err := Resolve(m, caps, in.BindingVersion+1)
	if err != nil {
		a.sayTo(w, r, http.StatusConflict, "MISSING_CAPABILITY", "detail", err.Error())
		return
	}
	bindings, _ := json.Marshal(res)
	params, _ := json.Marshal(map[string]any{"upgrade_to": to.ID, "bindings": json.RawMessage(bindings)})
	run, _, err := a.store.CreateRun(ctx, a.store.Pool, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID,
		Kind: KindSelfTest, Message: "ทดสอบรุ่น " + to.Version + " ก่อนอัปเกรด", Params: params, DefinitionVersion: to.Version,
		Checksum: to.Checksum, EngineChecksum: to.EngineChecksum, BindingVersion: res.Version}, "")
	if err != nil {
		a.fail(w, err, "upgrade")
		return
	}
	rid := run.ID
	_, _, _ = a.store.AddItem(ctx, a.store.Pool, in.ID, in.TenantID, in.UserID, &rid, "progress",
		Progress{RunID: run.ID.String(), Kind: KindSelfTest, Status: RunQueued}, nil)
	writeJSON(w, http.StatusAccepted, map[string]any{"run": run})
}

func (a *API) rollback(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	if in.PreviousDefinitionID == nil {
		say(w, http.StatusConflict, "NO_PREVIOUS")
		return
	}
	ctx := r.Context()
	prev, err := a.store.DefinitionByID(ctx, *in.PreviousDefinitionID)
	if err != nil {
		a.fail(w, err, "rollback")
		return
	}
	out, err := a.store.SwitchInstall(ctx, in.ID, prev, nil)
	if err != nil {
		a.fail(w, err, "rollback")
		return
	}
	_, _, _ = a.store.AddItem(ctx, a.store.Pool, in.ID, in.TenantID, in.UserID, nil, "agent_text",
		map[string]any{"text": "ย้อนกลับไปใช้รุ่น " + prev.Version + " แล้วครับ รอบที่ทำไปแล้วยังเป็นของรุ่นที่ใช้ตอนนั้น"}, nil)
	writeJSON(w, http.StatusOK, map[string]any{"install": out})
}
