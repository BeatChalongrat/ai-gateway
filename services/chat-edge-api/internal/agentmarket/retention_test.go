package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestRetentionKeepsWhatItShouldAndNothingLonger(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	put := func(key string) {
		if err := e.api.files.Upload(ctx, key, strings.NewReader("x"), "", 1); err != nil {
			t.Fatal(err)
		}
	}
	exists := func(key string) bool {
		ok, _ := e.api.files.Exists(ctx, key)
		return ok
	}
	exec := func(q string, args ...any) {
		if _, err := e.store.Pool.Exec(ctx, q, args...); err != nil {
			t.Fatal(err)
		}
	}
	file := func(status string, age time.Duration) (uuid.UUID, string) {
		id := uuid.New()
		key := UploadKey(in.TenantID, in.ID, id, "a.pdf")
		put(key)
		if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: id, TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
			Name: "a.pdf", Size: 1, SHA256: "x", StorageKey: key, Status: status}); err != nil {
			t.Fatal(err)
		}
		exec(`UPDATE agent_market.agent_files SET created_at = now() - make_interval(secs => $2) WHERE id = $1`, id, age.Seconds())
		return id, key
	}
	// AM4: a file never used in a round is kept 30 days (it was 48 hours)
	oldStaged, oldStagedKey := file("staged", 31*24*time.Hour)
	_, freshKey := file("staged", 49*time.Hour)
	_, templateKey := file("active", 90*24*time.Hour) // a saved template: an install file with no run

	run := func(status string, ended time.Duration) (Run, string, string) {
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
			Message: "[AM4] retention", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
		if err != nil {
			t.Fatal(err)
		}
		blob := BlobKey(in.TenantID, r.ID, strings.Repeat("cd", 16), "computed.json")
		put(blob)
		st, _, _ := e.store.BeginStep(ctx, r.ID, "compute", "compute", "k-"+r.ID.String(), json.RawMessage(`{}`))
		out, _ := json.Marshal(map[string]any{"computed": map[string]any{"key": blob}, "upload": oldStagedKey})
		if err := e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "", "", 1); err != nil {
			t.Fatal(err)
		}
		// the round's own upload
		fid := uuid.New()
		up := UploadKey(in.TenantID, in.ID, fid, "gl.xlsx")
		put(up)
		rid := r.ID
		if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: fid, TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
			RunID: &rid, Name: "gl.xlsx", Size: 1, SHA256: "y", StorageKey: up, Status: "active"}); err != nil {
			t.Fatal(err)
		}
		exec(`UPDATE agent_market.agent_runs SET status = $2, finished_at = now() - make_interval(secs => $3) WHERE id = $1`,
			r.ID, status, ended.Seconds())
		return r, blob, up
	}
	oldFailed, oldFailedBlob, oldFailedUpload := run("failed", 8*24*time.Hour)
	_, recentFailedBlob, recentFailedUpload := run("failed", 24*time.Hour)
	succeeded, succeededBlob, succeededUpload := run("succeeded", 60*24*time.Hour)
	ancient, ancientBlob, ancientUpload := run("succeeded", 6*365*24*time.Hour)

	pass := func() RetentionCounts {
		c, err := e.store.Retention(ctx, e.api.files, 30*24*time.Hour, 7*24*time.Hour, 30*24*time.Hour)
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	c := pass()
	if c.StagedFiles != 1 || exists(oldStagedKey) || !exists(freshKey) || !exists(templateKey) {
		t.Fatalf("unused files: %+v old=%v fresh=%v template=%v", c, exists(oldStagedKey), exists(freshKey), exists(templateKey))
	}
	if _, err := e.store.GetFile(ctx, in.TenantID, in.UserID, oldStaged); err == nil {
		t.Fatal("the unused upload's row is gone with its object")
	}
	// a failed round's data goes after 7 days — its scratch and its uploads
	if c.ScratchRuns != 1 || exists(oldFailedBlob) || exists(oldFailedUpload) || !exists(recentFailedBlob) || !exists(recentFailedUpload) {
		t.Fatalf("failed rounds: %+v", c)
	}
	if r, err := e.store.RunByID(ctx, oldFailed.ID); err != nil || r.Status != "failed" {
		t.Fatal("the failed run's record stays; only its data goes")
	}
	// a finished round is the organisation's record: kept for its retention (default 5 years)
	if !exists(succeededBlob) || !exists(succeededUpload) {
		t.Fatal("a finished round's evidence is kept")
	}
	if c.ExpiredRuns != 1 || exists(ancientBlob) || exists(ancientUpload) {
		t.Fatalf("a round finished 6 years ago is past the default 5 years: %+v", c)
	}
	if _, err := e.store.RunByID(ctx, ancient.ID); err == nil {
		t.Fatal("an expired round's rows go with its files")
	}
	// a second pass finds nothing more to do
	if c2 := pass(); c2 != (RetentionCounts{}) {
		t.Fatalf("second pass: %+v", c2)
	}
	// a shorter retention set by the organisation applies at once (60 days > 1 month)
	exec(`INSERT INTO agent_market.org_settings (org_id) VALUES ($1) ON CONFLICT DO NOTHING`, orgOr(in.OrgID))
	exec(`UPDATE agent_market.org_settings SET retention_years = 1 WHERE org_id = $1`, orgOr(in.OrgID))
	if c3 := pass(); c3.ExpiredRuns != 0 || !exists(succeededBlob) {
		t.Fatalf("60 days is inside 1 year: %+v", c3)
	}
	// a removed install: its conversation and settings go after 30 days; its
	// finished round stays (the organisation's record) until its retention ends
	if err := e.store.RemoveInstall(ctx, in.TenantID, in.UserID, in.ID); err != nil {
		t.Fatal(err)
	}
	if c4 := pass(); c4.RemovedInstalls != 0 || !exists(templateKey) {
		t.Fatal("a just-removed install is kept for 30 days")
	}
	exec(`UPDATE agent_market.agent_installs SET removed_at = now() - interval '31 days' WHERE id = $1`, in.ID)
	c5 := pass()
	if c5.RemovedInstalls != 0 || exists(templateKey) || exists(freshKey) || !exists(succeededBlob) || !exists(succeededUpload) {
		t.Fatalf("removed install, record kept: %+v template=%v fresh=%v", c5, exists(templateKey), exists(freshKey))
	}
	var defaults int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_install_defaults WHERE install_id = $1`, in.ID).Scan(&defaults)
	if defaults != 0 {
		t.Fatalf("the removed install's settings are gone: %d", defaults)
	}
	// once its last record is past retention the install goes whole
	exec(`UPDATE agent_market.agent_runs SET finished_at = now() - interval '2 years' WHERE id = $1`, succeeded.ID)
	c6 := pass()
	if c6.RemovedInstalls != 1 || exists(succeededBlob) {
		t.Fatalf("removed install erased once nothing is kept: %+v", c6)
	}
	var left int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1`, in.ID).Scan(&left)
	if left != 0 {
		t.Fatalf("runs left: %d", left)
	}
}
