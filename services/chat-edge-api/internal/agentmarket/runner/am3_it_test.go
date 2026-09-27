package runner

// AM3 integration tests on the real sandbox (AGENT_MARKET_IT=1): the
// reviewer's name inside the signed files, the catalog's samples made at
// start, and a stranger's own tax ID not tripping the invoice self-test.

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ooxmlText is the text of every XML part of a .docx/.pptx/.xlsx.
func ooxmlText(t *testing.T, data []byte) string {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for _, f := range zr.File {
		if !strings.HasSuffix(f.Name, ".xml") {
			continue
		}
		rc, _ := f.Open()
		x, _ := io.ReadAll(rc)
		rc.Close()
		b.Write(x)
	}
	return b.String()
}

func TestReviewStampsTheFilesWithTheReviewersName(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx")
	r := e.runner("r1")
	if got := e.drive(r, run.ID, 4*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("the run: %s %s", got.Status, got.StatusReason)
	}
	before, _ := e.store.RunArtifacts(ctx, run.ID)
	reviewID := uuid.New()
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.run_reviews (id, run_id, org_id, reviewer_user_id, reviewer_email,
		reviewer_name, note) VALUES ($1, $2, 'default', $3, 'cfo@example.test', 'นางสาวตรวจทาน ละเอียดดี', 'ตรวจกับงบทดลองแล้ว')`,
		reviewID, run.ID, uuid.New()); err != nil {
		t.Fatal(err)
	}
	params, _ := json.Marshal(map[string]any{"review_id": reviewID, "reviewer": "นางสาวตรวจทาน ละเอียดดี", "reviewer_email": "cfo@example.test",
		"at_label": "28 ก.ย. 2569 เวลา 10:05 น.", "note": "ตรวจกับงบทดลองแล้ว", "preparer": "ผู้จัดทำ ทดสอบ",
		"agent": "รายงานการเงิน รุ่น " + run.DefinitionVersion, "run_ref": run.ID.String()[:8]})
	pid := run.ID
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: run.InstallID, ParentRunID: &pid, TenantID: run.TenantID,
		UserID: run.UserID, Kind: am.KindReview, Params: params, DefinitionVersion: run.DefinitionVersion, Checksum: run.Checksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	if got := e.drive(r, job.ID, 3*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("the stamp job: %s %s", got.Status, got.StatusReason)
	}
	after, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(after) != len(before)*2 {
		t.Fatalf("one stamped version per file, the unsigned kept: %d → %d", len(before), len(after))
	}
	stamped := 0
	for _, a := range after {
		if !strings.Contains(a.Name, "_reviewed.") {
			continue
		}
		stamped++
		body, _, _, err := e.files.Download(ctx, a.StorageKey)
		if err != nil {
			t.Fatal(err)
		}
		data, _ := io.ReadAll(body)
		body.Close()
		text := ooxmlText(t, data)
		for _, want := range []string{"นางสาวตรวจทาน ละเอียดดี", "28 ก.ย. 2569", "ตรวจกับงบทดลองแล้ว"} {
			if !strings.Contains(text, want) {
				t.Fatalf("%s lacks %q on its face", a.Name, want)
			}
		}
		var v map[string]any
		_ = json.Unmarshal(a.Validation, &v)
		if v["review"] == nil || a.Version != 2 {
			t.Fatalf("%s: version %d, validation %v", a.Name, a.Version, v)
		}
	}
	if stamped != len(before) {
		t.Fatalf("stamped %d of %d", stamped, len(before))
	}
	var raw json.RawMessage
	_ = e.store.Pool.QueryRow(ctx, `SELECT artifact_versions FROM agent_market.run_reviews WHERE id = $1`, reviewID).Scan(&raw)
	var versions map[string]int
	if err := json.Unmarshal(raw, &versions); err != nil || versions["docx"] != 2 || versions["pptx"] != 2 {
		t.Fatalf("the review records the versions it signed: %s", raw)
	}
	// the numbers were not computed again
	if n := countSteps(e.steps(run.ID), "compute"); n != 1 {
		t.Fatalf("signing recomputed: %d compute steps", n)
	}
}

func TestCatalogSamplesAreMadeOnceAtStart(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	r := e.runner("r1")
	if err := r.Register(ctx); err != nil {
		t.Fatal(err)
	}
	missing, _ := e.store.MissingSamples(ctx)
	if len(missing) == 0 {
		t.Fatal("registered packages have no sample yet")
	}
	// drive the runner until every sample has ended
	deadline := time.Now().Add(8 * time.Minute)
	c, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() { _ = r.Start(c); close(done) }()
	defer func() { cancel(); <-done }()
	var rows []struct {
		slug, status, reason string
	}
	for time.Now().Before(deadline) {
		rows = nil
		q, err := e.store.Pool.Query(ctx, `SELECT d.slug, r.status, r.status_reason FROM agent_market.catalog_samples c
			JOIN agent_market.agent_definitions d ON d.id = c.definition_id JOIN agent_market.agent_runs r ON r.id = c.run_id`)
		if err == nil {
			for q.Next() {
				var x struct{ slug, status, reason string }
				_ = q.Scan(&x.slug, &x.status, &x.reason)
				rows = append(rows, x)
			}
			q.Close()
		}
		ended := len(rows) == len(missing)
		for _, x := range rows {
			ended = ended && am.IsTerminal(x.status)
		}
		if ended {
			break
		}
		time.Sleep(time.Second)
	}
	if len(rows) != len(missing) {
		t.Fatalf("one sample per package: %v", rows)
	}
	for _, x := range rows {
		if x.status != am.RunSucceeded {
			t.Fatalf("sample of %s: %s %s", x.slug, x.status, x.reason)
		}
	}
	if again, _ := e.store.MissingSamples(ctx); len(again) != 0 {
		t.Fatalf("a restart makes none again: %v", again)
	}
	var owners int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_installs WHERE status = 'catalog'
		AND user_id = '00000000-0000-0000-0000-000000000000'`).Scan(&owners)
	if owners != len(rows) {
		t.Fatalf("each sample runs under an install nobody owns: %d", owners)
	}
}

func TestInvoiceSelfTestIgnoresTheStrangersOwnTaxID(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.setupInvoiceInstall()
	// the person's real company, not the sample documents' buyer
	for k, v := range map[string]string{"company": `"บริษัท ของฉันเอง จำกัด"`, "company_tax_id": `"0105556012341"`} {
		if err := e.store.SetDefault(ctx, e.store.Pool, in.ID, am.Default{Key: k, Value: json.RawMessage(v), Source: "install"}); err != nil {
			t.Fatal(err)
		}
	}
	def, _ := e.store.DefinitionByID(ctx, in.DefinitionID)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindSelfTest, Message: "ทดสอบกับข้อมูลตัวอย่าง", DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum}, "")
	if err != nil {
		t.Fatal(err)
	}
	got := e.drive(e.runner("r1"), run.ID, 4*time.Minute, func(x am.Run) bool { return terminal(x) || parked(x) })
	cards, _ := e.store.RunCards(ctx, run.ID)
	if got.Status != am.RunSucceeded || len(cards) != 0 {
		for _, c := range cards {
			t.Logf("card: %s", c.Questions)
		}
		t.Fatalf("the self-test on the sample data asks nothing whatever the person's company: %s %s, %d card(s)",
			got.Status, got.StatusReason, len(cards))
	}
}
