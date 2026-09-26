package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// parentState is what a small job reads from a finished run.
type parentState struct {
	run        am.Run
	computed   blobRef
	computeKey string
	summary    map[string]any
	narrative  narrativeOut
	narrKey    string
}

func (r *Runner) parentState(ctx context.Context, parentID uuid.UUID) (parentState, error) {
	p := parentState{}
	run, err := r.Store.RunByID(ctx, parentID)
	if err != nil {
		return p, err
	}
	p.run = run
	var out json.RawMessage
	err = r.Store.Pool.QueryRow(ctx, `SELECT output, idempotency_key FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND name = 'compute' AND status = 'succeeded' ORDER BY seq DESC LIMIT 1`, parentID).Scan(&out, &p.computeKey)
	if err != nil {
		return p, fmt.Errorf("the run has no computed table yet")
	}
	var co struct {
		Computed blobRef        `json:"computed"`
		Summary  map[string]any `json:"summary"`
	}
	if err := json.Unmarshal(out, &co); err != nil {
		return p, err
	}
	p.computed, p.summary = co.Computed, co.Summary
	err = r.Store.Pool.QueryRow(ctx, `SELECT output, idempotency_key FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND name = 'narrative' AND status = 'succeeded' ORDER BY seq DESC LIMIT 1`, parentID).Scan(&out, &p.narrKey)
	if err == nil {
		_ = json.Unmarshal(out, &p.narrative)
	}
	return p, nil
}

// renderJob re-renders a finished run from its stored computed table: the
// same numbers and citations, new artifact rows, the version bumped. It
// has no compute step — the test for "adding a format must not recompute"
// counts exactly that.
func (r *Runner) renderJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	if job.ParentRunID == nil {
		return r.fail(ctx, job, "no_parent", errors.New("render job without a run"))
	}
	ps, err := r.parentState(ctx, *job.ParentRunID)
	if err != nil {
		return r.fail(ctx, job, "render_failed", err)
	}
	if ps.run.Status != am.RunSucceeded {
		return r.fail(ctx, job, "render_failed", errors.New("the run has not finished"))
	}
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	// Presentation answers are the parent run's.
	parentRC, err := r.load(ctx, ps.run, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	rc.answers = parentRC.answers
	var params struct {
		Formats []string `json:"formats"`
		Why     string   `json:"why"`
	}
	_ = json.Unmarshal(job.Params, &params)
	formats := params.Formats
	if len(formats) == 0 {
		_ = json.Unmarshal(ps.run.Formats, &formats)
	}
	var ok []string
	for _, f := range formats {
		if rc.pkg.Manifest.FormatAllowed(f) {
			ok = append(ok, f)
		}
	}
	if len(ok) == 0 {
		return r.fail(ctx, job, "bad_format", fmt.Errorf("no supported format in %v", formats))
	}
	rc.run.Period = ps.run.Period
	rendered, renderKey, err := rc.render(ctx, ps.run.ID, ps.computed, ps.computeKey, ps.narrative, ps.narrKey, ok, "render")
	if err != nil {
		var qe *qualityErr
		if errors.As(err, &qe) {
			return r.fail(ctx, job, "quality_gate", err)
		}
		return rc.stepFailed("render", err)
	}
	arts, err := rc.publish(ctx, ps.run.ID, rendered, renderKey, ps.computed)
	if err != nil {
		return rc.stepFailed("publish", err)
	}
	// The run's current output set grows to include what was asked for.
	var current []string
	_ = json.Unmarshal(ps.run.Formats, &current)
	for _, f := range ok {
		if !contains(current, f) {
			current = append(current, f)
		}
	}
	_ = r.Store.SetFormats(ctx, r.Store.Pool, ps.run.ID, current)
	if err := rc.postOutputs(ctx, ps.run.ID, arts, ps.summary, ps.narrative, true); err != nil {
		return r.fail(ctx, job, "outputs", err)
	}
	return r.finish(rc, am.RunSucceeded, "")
}

var formatWords = []struct {
	format string
	words  []string
}{
	{"xlsx", []string{"excel", "xlsx", "เอ็กเซล", "ตารางตัวเลข", "ตาราง excel", "สเปรดชีต"}},
	{"pptx", []string{"pptx", "powerpoint", "พาวเวอร์พอยต์", "สไลด์"}},
	{"docx", []string{"docx", "word", "เวิร์ด", "รายงาน word"}},
}

// asksForPDF: a PDF is the finished .docx converted by LibreOffice (AM2). An
// agent that offers none (or a runner without the converter) answers a
// request for one honestly instead of rendering.
func asksForPDF(text string) bool {
	t := strings.ToLower(text)
	return strings.Contains(t, "pdf") || strings.Contains(t, "พีดีเอฟ")
}

// pdfOffered: the agent lists a PDF output and this runner has the converter.
func (rc *runCtx) pdfOffered() bool {
	return rc.pkg.Manifest.FormatAllowed("pdf") && rc.r.Converter.Available()
}

// detectFormatsFor is detectFormats plus "pdf" when the agent can make one.
func detectFormatsFor(text string, pdf bool) []string {
	out := detectFormats(text)
	if pdf && asksForPDF(text) {
		out = append(out, "pdf")
	}
	return out
}

func detectFormats(text string) []string {
	t := strings.ToLower(text)
	var out []string
	for _, fw := range formatWords {
		for _, w := range fw.words {
			if strings.Contains(t, w) {
				out = append(out, fw.format)
				break
			}
		}
	}
	return out
}

func isQuestion(text string) bool {
	t := strings.ToLower(text)
	for _, w := range []string{"?", "ทำไม", "เท่าไร", "เท่าไหร่", "อธิบาย", "เพราะอะไร", "อะไรบ้าง", "why", "how much", "explain"} {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

func wantsMoreFiles(text string) bool {
	t := strings.ToLower(text)
	for _, w := range []string{"ไฟล์อื่น", "แบบอื่น", "ขอเอกสารเพิ่ม", "ขอไฟล์เพิ่ม", "รูปแบบอื่น"} {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

// replyJob answers a typed message that is not an answer to a card.
func (r *Runner) replyJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	var params struct {
		ItemID uuid.UUID `json:"item_id"`
		Text   string    `json:"text"`
	}
	_ = json.Unmarshal(job.Params, &params)
	post := func(text string) error {
		target := job
		if job.ParentRunID != nil {
			if p, err := r.Store.RunByID(ctx, *job.ParentRunID); err == nil {
				target = p
			}
		}
		return r.Store.PostText(ctx, target, text, nil)
	}
	var parent *am.Run
	if job.ParentRunID != nil {
		if p, err := r.Store.RunByID(ctx, *job.ParentRunID); err == nil {
			parent = &p
		}
	}
	text := strings.TrimSpace(params.Text)
	low := strings.ToLower(text)
	switch {
	case strings.Contains(low, "ภาษาอังกฤษ") || strings.Contains(low, "english"):
		_ = post("ตอนนี้ agent ตัวนี้ทำเอกสารเป็นภาษาไทยเท่านั้นครับ ภาษาอังกฤษจะมาในรุ่นถัดไป")
	case asksForPDF(text) && !rc.pdfOffered() && len(detectFormats(text)) == 0:
		_ = post("agent ตัวนี้ยังทำไฟล์ PDF ไม่ได้ในตอนนี้ครับ " +
			"ระหว่างนี้เปิดไฟล์ที่ได้แล้วสั่ง “บันทึกเป็น PDF” ได้เลย")
	case len(detectFormatsFor(text, rc.pdfOffered())) > 0:
		formats := detectFormatsFor(text, rc.pdfOffered())
		switch {
		case parent == nil:
			_ = post("ยังไม่มีรอบไหนให้ทำไฟล์ครับ ส่งไฟล์บัญชีมาได้เลย แล้วผมจะทำให้ตามรูปแบบที่ขอ")
		case parent.Status == am.RunSucceeded:
			params, _ := json.Marshal(map[string]any{"formats": formats, "why": "message"})
			pid := parent.ID
			if _, _, err := r.Store.CreateRun(ctx, r.Store.Pool, am.Run{InstallID: parent.InstallID, ParentRunID: &pid,
				TenantID: parent.TenantID, UserID: parent.UserID, Kind: am.KindRender, Params: params,
				DefinitionVersion: parent.DefinitionVersion, Checksum: parent.Checksum, EngineChecksum: parent.EngineChecksum,
				BindingVersion: parent.BindingVersion}, ""); err != nil {
				return r.fail(ctx, job, "reply", err)
			}
			_ = post(fmt.Sprintf("ได้ครับ กำลังสร้าง %s จากตัวเลขชุดเดิมของรอบนี้ ไม่คำนวณใหม่", formatNames(formats)))
		case !am.IsTerminal(parent.Status):
			var cur []string
			_ = json.Unmarshal(parent.Formats, &cur)
			for _, f := range formats {
				if !contains(cur, f) {
					cur = append(cur, f)
				}
			}
			_ = r.Store.SetFormats(ctx, r.Store.Pool, parent.ID, cur)
			_ = post(fmt.Sprintf("รับทราบครับ จะส่ง %s ให้ด้วยเมื่อรอบนี้เสร็จ", formatNames(formats)))
		default:
			_ = post("รอบก่อนหน้าไม่สำเร็จ จึงยังทำไฟล์เพิ่มไม่ได้ครับ ส่งไฟล์ใหม่หรือพิมพ์ “ทำรอบใหม่” ได้เลย")
		}
	case wantsMoreFiles(text) && parent != nil && parent.Status == am.RunSucceeded:
		if err := r.formatCard(ctx, *parent, rc.pdfOffered()); err != nil {
			return r.fail(ctx, job, "reply", err)
		}
	case isQuestion(text) && parent != nil:
		answer := r.answerQuestion(ctx, rc, *parent, text)
		_ = post(answer)
	default:
		m := rc.pkg.Manifest
		var needs []string
		for _, in := range m.Inputs {
			needs = append(needs, in.Label)
		}
		_ = post(fmt.Sprintf("ผมทำ%sครับ ส่งไฟล์มาได้เลย (%s) หรือถามเกี่ยวกับตัวเลขของรอบล่าสุดได้ %s",
			m.Name, strings.Join(needs, " · "), m.Cannot))
	}
	if params.ItemID != uuid.Nil {
		_ = r.Store.PatchItem(ctx, r.Store.Pool, params.ItemID, map[string]any{"status": "handled"})
	}
	return r.finish(rc, am.RunSucceeded, "")
}

func formatNames(fs []string) string {
	names := map[string]string{"docx": "รายงาน .docx", "pptx": "สไลด์ .pptx", "xlsx": "ตารางตัวเลข .xlsx", "pdf": "รายงาน PDF"}
	var out []string
	for _, f := range fs {
		out = append(out, names[f])
	}
	return strings.Join(out, " และ ")
}

// formatQuestion is the "which extra files" question: the three outputs
// the agent makes, plus a PDF of the report when one can be made (AM2).
func formatQuestion(_ []string, pdf bool) am.Question {
	opts := []am.Option{
		{Label: "รายงาน .docx", Value: json.RawMessage(`"docx"`)},
		{Label: "สไลด์ .pptx", Value: json.RawMessage(`"pptx"`)},
		{Label: "ตารางตัวเลข .xlsx", Value: json.RawMessage(`"xlsx"`)},
	}
	if pdf {
		opts = append(opts, am.Option{Label: "รายงาน PDF", Value: json.RawMessage(`"pdf"`)})
	}
	return am.Question{Key: "output_format", Code: "output_format", Severity: "cosmetic", Header: "ชนิดไฟล์",
		Question: "ต้องการไฟล์แบบไหนเพิ่มครับ (เลือกได้หลายแบบ)", MultiSelect: true, Options: opts,
		Default: json.RawMessage(`"xlsx"`), DefaultLabel: "ตารางตัวเลข .xlsx"}
}

// formatCard asks which extra formats to make (multi-select) — only when
// the user's words say "more files" without naming one.
func (r *Runner) formatCard(ctx context.Context, run am.Run, pdf bool) error {
	var cur []string
	_ = json.Unmarshal(run.Formats, &cur)
	q := formatQuestion(cur, pdf)
	qs, _ := json.Marshal([]am.Question{q})
	card, err := r.Store.CreateCard(ctx, r.Store.Pool, am.Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "run", Kind: "output_format", Blocking: false, Questions: qs})
	if err != nil {
		return err
	}
	rid := run.ID
	_, _, err = r.Store.AddItem(ctx, r.Store.Pool, run.InstallID, run.TenantID, run.UserID, &rid, "card",
		am.CardItemPayload(card, "", "สร้างจากตัวเลขชุดเดิม ไม่คำนวณใหม่"), nil)
	return err
}

// answerQuestion answers from the run's computed table only, numbers guarded.
func (r *Runner) answerQuestion(ctx context.Context, rc *runCtx, parent am.Run, text string) string {
	ps, err := r.parentState(ctx, parent.ID)
	if err != nil {
		return "รอให้รอบนี้คำนวณเสร็จก่อนนะครับ แล้วถามได้เลย"
	}
	cv, _, err := rc.readComputed(ctx, ps.computed)
	if err != nil || !r.Model.Available() {
		return "ดูตัวเลขและที่มาได้ในหน้า “ขั้นตอนและที่มาของตัวเลข” ครับ (ตอนนี้ไม่มีโมเดลที่ตอบคำถามได้)"
	}
	system := rc.pkg.Instructions + "\n\nตอนนี้ให้ตอบคำถามของผู้ใช้เกี่ยวกับตัวเลขของรอบนี้ สั้น ๆ 1–3 ประโยค ตอบเป็น JSON {\"answer\": \"...\"} ใช้ตัวเลขจากรายการที่อ้างได้เท่านั้น ถ้าข้อมูลไม่พอให้บอกตรง ๆ ว่าไม่พอ"
	user := narrativePrompt(cv) + "\n\nคำถาม: " + text
	var answer string
	c, _, err := r.Model.Complete(ctx, system, user, func(s string) error {
		var v struct {
			Answer string `json:"answer"`
		}
		if err := json.Unmarshal([]byte(extractJSON(s)), &v); err != nil || strings.TrimSpace(v.Answer) == "" {
			return fmt.Errorf("ไม่ใช่ JSON ที่มี answer")
		}
		if err := GuardNumbers(v.Answer, cv.Quotable, cv.QuotableIDs); err != nil {
			return err
		}
		answer = v.Answer
		return nil
	})
	if c.CostUSD > 0 { // paid whether or not an answer was usable
		_ = r.Store.Charge(ctx, rc.install.ID, rc.run.ID, "reply", c.CostUSD*r.CreditsPerUSD, c.Model, nil)
	}
	if err != nil {
		return "ผมยังตอบคำถามนี้จากตัวเลขที่มีได้ไม่มั่นใจครับ ดูรายละเอียดได้ในหน้า “ขั้นตอนและที่มาของตัวเลข”"
	}
	return answer
}

// previewJob reads a sample export for the install wizard's column mapping.
func (r *Runner) previewJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	var params struct {
		FileID uuid.UUID `json:"file_id"`
	}
	_ = json.Unmarshal(job.Params, &params)
	f, err := r.Store.GetFile(ctx, job.TenantID, job.UserID, params.FileID)
	if err != nil {
		return r.fail(ctx, job, "preview", err)
	}
	mapping := rc.mapping()
	key := am.StepKey(job.ID, "preview", f.SHA256, mapping)
	_, err = rc.step(ctx, "preview:"+f.Name, "ingest", key, map[string]any{"file": f.Name}, false,
		func(ctx context.Context) (any, float64, string, string, error) {
			path, cleanup, err := r.Files.OpenLocalCopy(ctx, f.StorageKey)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			input := "in_" + f.ID.String()[:8] + filepath.Ext(f.Name)
			res, err := r.Exec.Run(ctx, map[string]any{"op": "parse", "package": rc.pkg.PyPackage, "preview": true,
				"file": map[string]any{"name": f.Name, "input": input}, "mapping": mapping}, []Input{{Name: input, LocalPath: path}})
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			return map[string]any{"summary": res.Result["summary"], "preview": res.Result["preview"]}, 0, "engine/ledger.parse_file", "", nil
		})
	if err != nil {
		return r.fail(ctx, job, "preview", err)
	}
	return r.finish(rc, am.RunSucceeded, "")
}
