package runner

// AM21 (part 4) end to end: a self-test run delivers its files; a pages job hung off that run converts
// the delivered document with the converter and keeps its first pages as PNGs beside the file, with a
// manifest; the pictures are of the stored file itself. (AGENT_MARKET_IT=1; the converter image is
// AM_CONVERTER_IMAGE.) On 15 Oct 2026 the first pages job on live was refused by the database's check
// on a run's kind — this is the test that was missing.

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func TestThePagesOfADeliveredFileAreMadeFromTheFileItselfAndKeptBesideIt(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupInstall(3_000_000)
	def, _ := e.store.DefinitionByID(ctx, in.DefinitionID)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindSelfTest, Message: "ทดสอบกับข้อมูลตัวอย่าง", DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	r := e.runner("r1")
	r.Converter = startConverter(t)
	got := e.drive(r, run.ID, 8*time.Minute, func(x am.Run) bool { return terminal(x) || parked(x) })
	if got.Status != am.RunSucceeded {
		t.Fatalf("self-test: %s %s", got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	var doc *am.Artifact
	for i := range arts {
		if arts[i].Format == "docx" {
			doc = &arts[i]
		}
	}
	if doc == nil {
		t.Fatalf("no document among %d files", len(arts))
	}
	// the job the files card queues (POST /artifacts/{id}/pages), as the control plane creates it
	params, _ := json.Marshal(map[string]any{"artifact_id": doc.ID})
	pid := run.ID
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: run.InstallID, ParentRunID: &pid, TenantID: run.TenantID,
		UserID: run.UserID, Kind: am.KindPages, Params: params, DefinitionVersion: run.DefinitionVersion, Checksum: run.Checksum,
		EngineChecksum: run.EngineChecksum}, "pages:"+doc.ID.String())
	if err != nil {
		t.Fatalf("the database must take a pages job (migration 012): %v", err)
	}
	jr := e.drive(r, job.ID, 4*time.Minute, terminal)
	if jr.Status != am.RunSucceeded {
		t.Fatalf("pages job: %s %s", jr.Status, jr.StatusReason)
	}
	body, _, ctype, err := r.Files.Download(ctx, am.PagesManifestKey(doc.StorageKey))
	if err != nil {
		t.Fatalf("the manifest is kept beside the file: %v", err)
	}
	raw, _ := io.ReadAll(body)
	_ = body.Close()
	var m am.PagesManifest
	if err := json.Unmarshal(raw, &m); err != nil || m.Pages < 2 || m.Available < 2 || m.Available > am.PagesMax || m.SHA256 != doc.SHA256 {
		t.Fatalf("manifest (%s): %s %v", ctype, raw, err)
	}
	for n := 1; n <= 2; n++ {
		png, size, ctype, err := r.Files.Download(ctx, am.PageKey(doc.StorageKey, n))
		if err != nil {
			t.Fatalf("page %d: %v", n, err)
		}
		head := make([]byte, 8)
		_, _ = io.ReadFull(png, head)
		_ = png.Close()
		if !bytes.Equal(head, []byte("\x89PNG\r\n\x1a\n")) || (size != 0 && size < 2000) || (ctype != "" && ctype != "image/png") {
			t.Fatalf("page %d is not a picture: %q %d %s", n, head, size, ctype)
		}
	}
	// asked again for the same file, the control plane finds the job it already has (no second render)
	again, err := e.store.RunByIdempotency(ctx, run.InstallID, "pages:"+doc.ID.String())
	if err != nil || again.ID != job.ID {
		t.Fatalf("the pages job is found by its key: %v %v", again, err)
	}
}
