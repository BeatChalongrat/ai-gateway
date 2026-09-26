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
	oldStaged, oldStagedKey := file("staged", 49*time.Hour)
	_, freshKey := file("staged", time.Hour)
	_, templateKey := file("active", 90*24*time.Hour) // a saved template: an install file with no run

	run := func(status string, ended time.Duration) (Run, string) {
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
			Message: "[AM2] retention", DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
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
		exec(`UPDATE agent_market.agent_runs SET status = $2, finished_at = now() - make_interval(secs => $3) WHERE id = $1`,
			r.ID, status, ended.Seconds())
		return r, blob
	}
	oldFailed, oldFailedBlob := run("failed", 8*24*time.Hour)
	_, recentFailedBlob := run("failed", 24*time.Hour)
	_, succeededBlob := run("succeeded", 60*24*time.Hour)

	c, err := e.store.Retention(ctx, e.api.files, 48*time.Hour, 7*24*time.Hour, 30*24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if c.StagedFiles != 1 || exists(oldStagedKey) || !exists(freshKey) || !exists(templateKey) {
		t.Fatalf("staged: %+v old=%v fresh=%v template=%v", c, exists(oldStagedKey), exists(freshKey), exists(templateKey))
	}
	if _, err := e.store.GetFile(ctx, in.TenantID, in.UserID, oldStaged); err == nil {
		t.Fatal("the unattached upload's row is gone with its object")
	}
	if c.ScratchRuns != 1 || exists(oldFailedBlob) || !exists(recentFailedBlob) || !exists(succeededBlob) {
		t.Fatalf("scratch: %+v", c)
	}
	if r, err := e.store.RunByID(ctx, oldFailed.ID); err != nil || r.Status != "failed" {
		t.Fatal("the failed run's record stays; only its scratch goes")
	}
	// a second pass finds nothing more to do
	if c2, _ := e.store.Retention(ctx, e.api.files, 48*time.Hour, 7*24*time.Hour, 30*24*time.Hour); c2 != (RetentionCounts{}) {
		t.Fatalf("second pass: %+v", c2)
	}
	// a removed install is erased after 30 days, not before
	if err := e.store.RemoveInstall(ctx, in.TenantID, in.UserID, in.ID); err != nil {
		t.Fatal(err)
	}
	if c3, _ := e.store.Retention(ctx, e.api.files, 48*time.Hour, 7*24*time.Hour, 30*24*time.Hour); c3.RemovedInstalls != 0 {
		t.Fatal("a just-removed install is kept for 30 days")
	}
	exec(`UPDATE agent_market.agent_installs SET removed_at = now() - interval '31 days' WHERE id = $1`, in.ID)
	c4, err := e.store.Retention(ctx, e.api.files, 48*time.Hour, 7*24*time.Hour, 30*24*time.Hour)
	if err != nil || c4.RemovedInstalls != 1 || exists(templateKey) || exists(succeededBlob) || exists(freshKey) {
		t.Fatalf("removed install: %+v %v", c4, err)
	}
	var left int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1`, in.ID).Scan(&left)
	if left != 0 {
		t.Fatalf("runs left: %d", left)
	}
}
