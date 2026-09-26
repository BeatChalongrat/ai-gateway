package runner

// AM2 Phase E end to end: a report PDF is the rendered .docx converted by
// the doc-converter image and gated (AGENT_MARKET_IT=1; the image is
// AM_CONVERTER_IMAGE, default ai-gateway-doc-converter:dev).

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// startConverter runs the converter image hardened like its compose service,
// on a random localhost port, removed when the test ends.
func startConverter(t *testing.T) *Converter {
	t.Helper()
	image := envOr("AM_CONVERTER_IMAGE", "ai-gateway-doc-converter:dev")
	out, err := exec.Command("docker", "run", "-d", "--rm", "--read-only", "--tmpfs", "/tmp:rw,size=256m,mode=1777",
		"--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "768m", "--pids-limit", "128",
		"-p", "127.0.0.1::8096", image).Output()
	if err != nil {
		t.Fatalf("start %s: %v", image, err)
	}
	id := strings.TrimSpace(string(out))
	t.Cleanup(func() { _ = exec.Command("docker", "rm", "-f", id).Run() })
	port, err := exec.Command("docker", "port", id, "8096/tcp").Output()
	if err != nil {
		t.Fatal(err)
	}
	addr := strings.TrimSpace(strings.Split(string(port), "\n")[0])
	base := "http://" + strings.Replace(addr, "0.0.0.0", "127.0.0.1", 1)
	deadline := time.Now().Add(90 * time.Second)
	for time.Now().Before(deadline) {
		if resp, err := http.Get(base + "/health"); err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return &Converter{BaseURL: base, HTTP: http.DefaultClient, Timeout: 120 * time.Second}
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	t.Fatal("converter never became healthy")
	return nil
}

func (e *itEnv) runWithFormats(formats string, files ...string) am.Run {
	e.t.Helper()
	run := e.newRun("ปิดงบเดือนกันยายน", files...)
	if err := e.store.SetFormats(context.Background(), e.store.Pool, run.ID, nil); err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(context.Background(), `UPDATE agent_market.agent_runs SET formats = $2 WHERE id = $1`,
		run.ID, json.RawMessage(formats)); err != nil {
		e.t.Fatal(err)
	}
	return run
}

func TestReportPDFIsTheConvertedDocxAndPassesItsGate(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	conv := startConverter(t)
	ctx := context.Background()

	// docx + pdf: both delivered, the PDF with its two gate checks passed
	run := e.runWithFormats(`["docx","pdf"]`, "sample-data/GL_2569-09.xlsx")
	r := e.runner("r1")
	r.Converter = conv
	got := e.drive(r, run.ID, 6*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("docx + pdf: %s %s", got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	var pdf *am.Artifact
	for i := range arts {
		if arts[i].Format == "pdf" {
			pdf = &arts[i]
		}
	}
	if len(arts) != 2 || pdf == nil {
		t.Fatalf("a .docx and its PDF: %+v", arts)
	}
	var q struct {
		Passed bool        `json:"passed"`
		Checks []gateCheck `json:"checks"`
	}
	_ = json.Unmarshal(pdf.Validation, &q)
	names := map[string]bool{}
	for _, c := range q.Checks {
		names[c.Name] = c.OK
	}
	for _, c := range q.Checks {
		t.Logf("gate %s ok=%v: %s", c.Name, c.OK, c.Detail)
	}
	if !q.Passed || !names["pdf.pages"] || !names["pdf.thai"] {
		t.Fatalf("the PDF's gate: %+v", q)
	}
	body, _, _, err := e.files.Download(ctx, pdf.StorageKey)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(body)
	body.Close()
	if !bytes.HasPrefix(b, []byte("%PDF-")) {
		t.Fatal("the artifact is a PDF")
	}

	// pdf alone: the .docx it came from is rendered but not delivered
	run2 := e.runWithFormats(`["pdf"]`, "sample-data/GL_2569-09.xlsx")
	got2 := e.drive(r, run2.ID, 6*time.Minute, terminal)
	arts2, _ := e.store.RunArtifacts(ctx, run2.ID)
	if got2.Status != am.RunSucceeded || len(arts2) != 1 || arts2[0].Format != "pdf" {
		t.Fatalf("pdf only: %s %+v", got2.Status, arts2)
	}

	// no converter: the PDF is not delivered and the run says why
	run3 := e.runWithFormats(`["docx","pdf"]`, "sample-data/GL_2569-09.xlsx")
	r3 := e.runner("r3")
	r3.Converter = &Converter{}
	got3 := e.drive(r3, run3.ID, 6*time.Minute, terminal)
	if got3.Status != am.RunFailed || got3.StatusReason != "quality_gate" {
		t.Fatalf("no converter must not deliver a PDF: %s %s", got3.Status, got3.StatusReason)
	}
}
