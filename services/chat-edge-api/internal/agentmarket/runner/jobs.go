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
		Formats  []string `json:"formats"`
		Why      string   `json:"why"`
		FormOnly bool     `json:"form_only"` // AM10: fill the round's form, make no other file
		// AM11b: the directives the files are to be made with, when the job was started by something the
		// person asked of the round's documents — and what they asked (to say what became of it)
		Instructions []string  `json:"instructions"`
		Asked        *am.Asked `json:"asked"`
	}
	_ = json.Unmarshal(job.Params, &params)
	rc.formOnly = params.FormOnly
	// AM11b: a file made later for the same round is made the way the round's documents are made now
	if params.Asked != nil {
		rc.instr = params.Instructions
	} else {
		rc.instr = r.Store.InstructionsInForce(ctx, ps.run.ID)
	}
	formats := params.Formats
	if len(formats) == 0 && !rc.formOnly {
		_ = json.Unmarshal(ps.run.Formats, &formats)
	}
	ok := []string{}
	for _, f := range formats {
		if rc.pkg.Manifest.FormatAllowed(f) {
			ok = append(ok, f)
		}
	}
	if len(ok) == 0 && !rc.formOnly {
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
	if err := rc.recordFormOutcome(ctx, ps.run.ID, rendered.form, arts); err != nil {
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
	if err := rc.postOutputs(ctx, ps.run.ID, arts, ps.summary, ps.narrative, true, nil); err != nil {
		return r.fail(ctx, job, "outputs", err)
	}
	if params.Asked != nil {
		rc.sayAsked(ctx, ps.run, *params.Asked, rendered.Asked)
	}
	return r.finish(rc, am.RunSucceeded, "")
}

// formatWords: the words by which a typed message names a kind of file, in either language. They read
// what a person typed and are said to nobody. A Thai word is found anywhere in the message; an English
// one only as a whole word (am.HasWord): "excel" is not found in "excellent", nor "word" in "words",
// "in other words" or "password".
var formatWords = []struct {
	format string
	words  []string
}{
	{"xlsx", []string{"excel", "xlsx", "เอ็กเซล", "ตารางตัวเลข", "ตาราง excel", "สเปรดชีต",
		"spreadsheet", "workbook", "table of figures", "excel table"}},
	{"pptx", []string{"pptx", "powerpoint", "พาวเวอร์พอยต์", "สไลด์", "slides", "slide deck", "deck"}},
	{"docx", []string{"docx", "word", "เวิร์ด", "รายงาน word", "word report", "word file", "word document"}},
}

// pdfWords: the words by which a typed message asks for a PDF (read from what a person typed).
var pdfWords = []string{"pdf", "pdfs", "พีดีเอฟ"}

// asksForPDF: a PDF is the finished .docx converted by LibreOffice (AM2). An
// agent that offers none (or a runner without the converter) answers a
// request for one honestly instead of rendering.
func asksForPDF(text string) bool { return am.HasWord(text, pdfWords...) }

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
	var out []string
	for _, fw := range formatWords {
		if am.HasWord(text, fw.words...) {
			out = append(out, fw.format)
		}
	}
	return out
}

// questionWords: what makes a typed message a question, in either language (read from what a person typed).
var questionWords = []string{"?", "ทำไม", "เท่าไร", "เท่าไหร่", "อธิบาย", "เพราะอะไร", "อะไรบ้าง", "why", "how much", "explain",
	"how many", "what is the reason", "for what reason", "what are", "what", "which", "how"}

func isQuestion(text string) bool { return am.HasWord(text, questionWords...) }

// moreFilesWords: what asks for more files without naming a kind, in either language (read from what a person typed).
var moreFilesWords = []string{"ไฟล์อื่น", "แบบอื่น", "ขอเอกสารเพิ่ม", "ขอไฟล์เพิ่ม", "รูปแบบอื่น",
	"other file", "other files", "another file", "another kind", "other kind", "more documents", "more files", "another format",
	"other format", "other formats"}

func wantsMoreFiles(text string) bool { return am.HasWord(text, moreFilesWords...) }

// englishWords: what asks for the documents in English (read from what a person typed).
var englishWords = []string{"ภาษาอังกฤษ", "english"}

// replyJob answers a typed message that is not an answer to a card.
func (r *Runner) replyJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	// AM6: a question asked in ask mode may carry what the person's knowledge
	// base holds on it (the control plane read it). This job is the only one
	// that reads it: no step of a round ever sees it.
	var params struct {
		ItemID    uuid.UUID            `json:"item_id"`
		Text      string               `json:"text"`
		Knowledge *am.KnowledgeContext `json:"knowledge"`
	}
	_ = json.Unmarshal(job.Params, &params)
	target := job
	if job.ParentRunID != nil {
		if p, err := r.Store.RunByID(ctx, *job.ParentRunID); err == nil {
			target = p
		}
	}
	post := func(text am.PersonText) error { return r.Store.PostText(ctx, target, text) }
	var parent *am.Run
	if job.ParentRunID != nil {
		if p, err := r.Store.RunByID(ctx, *job.ParentRunID); err == nil {
			parent = &p
		}
	}
	text := strings.TrimSpace(params.Text)
	l := am.LangOf(ctx)
	// AM11b: asked only when no earlier case took the message (a case's condition is evaluated in turn)
	var askedErr error
	asksOfDocuments := func() bool {
		handled, err := r.replyToAsked(ctx, rc, job, parent, text, post, isQuestion(text) || asksWhy(text))
		askedErr = err
		return handled || err != nil
	}
	switch {
	case am.HasWord(text, englishWords...):
		_ = post(am.Text("ตอนนี้ agent ตัวนี้ทำเอกสารเป็นภาษาไทยเท่านั้นครับ ภาษาอังกฤษจะมาในรุ่นถัดไป"))
	case asksForPDF(text) && !rc.pdfOffered() && len(detectFormats(text)) == 0:
		_ = post(am.Text("agent ตัวนี้ยังทำไฟล์ PDF ไม่ได้ในตอนนี้ครับ " +
			"ระหว่างนี้เปิดไฟล์ที่ได้แล้วสั่ง “บันทึกเป็น PDF” ได้เลย"))
	case len(detectFormatsFor(text, rc.pdfOffered())) > 0 && len(rc.allowedOf(detectFormatsFor(text, rc.pdfOffered()))) == 0:
		// asked for a kind of file this agent does not make (AM3: say so, never fail a render)
		_ = post(am.Text("agent ตัวนี้ทำไฟล์ได้เฉพาะ %s ครับ — ถ้าต้องการไฟล์แบบอื่น "+
			"เปิดไฟล์ที่ได้แล้วบันทึกเป็นแบบที่ต้องการได้เลย ไฟล์เดิมของรอบนี้ยังอยู่ครบ", am.Words(formatNames(rc.outputFormats()))))
	case len(detectFormatsFor(text, rc.pdfOffered())) > 0:
		formats := rc.allowedOf(detectFormatsFor(text, rc.pdfOffered()))
		switch {
		case parent == nil:
			// the input the agent asks for first, in its package's words; "files" when it has none to
			// show — a sentence of its own, because the English of the two is not one sentence with a
			// word changed ("Send this first: Bank statement." · "Send your files first.")
			said := am.Text("ยังไม่มีรอบไหนให้ทำไฟล์ครับ ส่งไฟล์มาได้เลย แล้วผมจะทำให้ตามรูปแบบที่ขอ")
			if in := rc.pkg.Manifest.Inputs; len(in) > 0 {
				if need := am.LabelFromPackage(in[0].Label, am.NoText); !need.EmptyIn(l) {
					said = am.Text("ยังไม่มีรอบไหนให้ทำไฟล์ครับ ส่ง%sมาได้เลย แล้วผมจะทำให้ตามรูปแบบที่ขอ", am.Words(need))
				}
			}
			_ = post(said)
		case parent.Status == am.RunSucceeded:
			params, _ := json.Marshal(map[string]any{"formats": formats, "why": "message"})
			pid := parent.ID
			if _, _, err := r.Store.CreateRun(ctx, r.Store.Pool, am.Run{InstallID: parent.InstallID, ParentRunID: &pid,
				TenantID: parent.TenantID, UserID: parent.UserID, Kind: am.KindRender, Params: params,
				DefinitionVersion: parent.DefinitionVersion, Checksum: parent.Checksum, EngineChecksum: parent.EngineChecksum,
				BindingVersion: parent.BindingVersion}, ""); err != nil {
				return r.fail(ctx, job, "reply", err)
			}
			_ = post(am.Text("ได้ครับ กำลังสร้าง %s จากตัวเลขชุดเดิมของรอบนี้ ไม่คำนวณใหม่", am.Words(formatNames(formats))))
		case !am.IsTerminal(parent.Status):
			var cur []string
			_ = json.Unmarshal(parent.Formats, &cur)
			for _, f := range formats {
				if !contains(cur, f) {
					cur = append(cur, f)
				}
			}
			_ = r.Store.SetFormats(ctx, r.Store.Pool, parent.ID, cur)
			_ = post(am.Text("รับทราบครับ จะส่ง %s ให้ด้วยเมื่อรอบนี้เสร็จ", am.Words(formatNames(formats))))
		default:
			if parent.Outcome() == am.OutcomeCompletedOpen {
				_ = post(am.Text("ไฟล์ของรอบก่อนหน้าเป็นฉบับร่าง จึงยังทำไฟล์เพิ่มจากรอบนั้นไม่ได้ครับ แก้สาเหตุแล้วส่งไฟล์ใหม่เพื่อเริ่มรอบใหม่ได้เลย"))
			} else {
				_ = post(am.Text("รอบก่อนหน้าไม่สำเร็จ จึงยังทำไฟล์เพิ่มไม่ได้ครับ ส่งไฟล์ใหม่หรือพิมพ์ “ทำรอบใหม่” ได้เลย"))
			}
		}
	case wantsMoreFiles(text) && parent != nil && parent.Status == am.RunSucceeded:
		if err := r.formatCard(ctx, *parent, rc.outputFormats(), rc.pdfOffered()); err != nil {
			return r.fail(ctx, job, "reply", err)
		}
	case asksOfDocuments():
		// AM11b: words about the documents of a finished round — "ไม่ต้องใส่กราฟ", "ขอสั้นๆ ได้ไหม?" — read
		// into what the package says can be asked (replyToAsked). It comes before the question cases: a
		// request is often worded as a question, and a question about the figures asks nothing of the
		// documents and falls through to them.
		if askedErr != nil {
			return rc.stepFailed("instructions", askedErr) // (a budget that ran out is a wait, as for any step)
		}
	case (isQuestion(text) || asksWhy(text)) && parent != nil:
		_ = r.Store.PostAgentText(ctx, target, r.answerWith(ctx, rc, *parent, text, params.Knowledge))
	case (isQuestion(text) || asksWhy(text)) && params.Knowledge != nil:
		// no round to ask about yet: the knowledge base alone answers
		_ = r.Store.PostAgentText(ctx, target, r.answerFromKnowledge(ctx, rc, text, params.Knowledge))
	default:
		// what the agent is, needs and cannot do is its manifest's: the package's own words
		m := rc.pkg.Manifest
		var needs []am.PersonText
		for _, in := range m.Inputs {
			needs = append(needs, am.LabelFromPackage(in.Label, am.NoText))
		}
		_ = post(am.Text("ผมทำ%sครับ ส่งไฟล์มาได้เลย (%s) หรือถามเกี่ยวกับตัวเลขของรอบล่าสุดได้ %s",
			am.Said(m.Name, am.Text("งานของ agent นี้")), am.Words(am.Join(" · ", needs...)), am.Said(m.Cannot, am.NoText)))
	}
	if params.ItemID != uuid.Nil {
		_ = r.Store.PatchItem(ctx, r.Store.Pool, params.ItemID, am.ItemPatch{Status: "handled"})
	}
	return r.finish(rc, am.RunSucceeded, "")
}

// replyToAsked: a typed message after a finished round that asks something of its documents. What can
// be done starts a render job — the same computed table, no compute step — with the directives now in
// force and the new ones; what cannot is said with its reason. False when the package takes no
// instructions, there is no finished round, or the words asked nothing of the documents. question: the
// message reads as a question — it is taken for a request only when it asks for something the package
// can do ("ไม่ใส่กราฟได้ไหม?"); anything else worded as a question is still a question about the round,
// and is answered as one, never refused as a request.
func (r *Runner) replyToAsked(ctx context.Context, rc *runCtx, job am.Run, parent *am.Run, text string,
	post func(am.PersonText) error, question bool) (bool, error) {
	if !rc.pkg.Manifest.TakesInstructions() || parent == nil || parent.Status != am.RunSucceeded {
		return false, nil
	}
	asked, err := rc.asked(ctx, text, false)
	if question && errors.Is(err, ErrModelUnreachable) && ctx.Err() == nil {
		// AM15: words worded as a question, and no model to be reached to say whether they ask something
		// of the documents. They are answered as the question they read as — from the round's own record
		// where it can answer, and saying that the model is away where only a model could (answerWith).
		// (Words that are not a question wait for the model, like any step: stepFailed.)
		rc.modelAway = true
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if asked.Nothing() || (question && len(asked.Apply) == 0) {
		return false, nil
	}
	if len(asked.Apply) == 0 {
		// nothing to make again: what was asked and cannot be done, each with its reason
		rc.sayAsked(ctx, *parent, asked, renderAsked{})
		return true, nil
	}
	var formats []string
	_ = json.Unmarshal(parent.Formats, &formats)
	formats = rc.allowedOf(formats)
	if len(formats) == 0 {
		return false, nil
	}
	params, _ := json.Marshal(map[string]any{"formats": formats, "why": "instruction",
		"instructions": rc.pkg.Manifest.Instructions.Merge(r.Store.InstructionsInForce(ctx, parent.ID), asked.Apply), "asked": asked})
	pid := parent.ID
	if _, _, err := r.Store.CreateRun(ctx, r.Store.Pool, am.Run{InstallID: parent.InstallID, ParentRunID: &pid,
		TenantID: parent.TenantID, UserID: parent.UserID, Kind: am.KindRender, Params: params,
		DefinitionVersion: parent.DefinitionVersion, Checksum: parent.Checksum, EngineChecksum: parent.EngineChecksum,
		BindingVersion: parent.BindingVersion}, ""); err != nil {
		return false, err
	}
	return true, post(am.Text("ได้ครับ กำลังทำไฟล์ใหม่ตามที่ขอ จากตัวเลขชุดเดิมของรอบนี้ ไม่คำนวณใหม่"))
}

func formatNames(fs []string) am.PersonText {
	names := map[string]am.PersonText{"docx": am.Text("รายงาน .docx"), "pptx": am.Text("สไลด์ .pptx"),
		"xlsx": am.Text("ตารางตัวเลข .xlsx"), "pdf": am.Text("รายงาน PDF")}
	var out []am.PersonText
	for _, f := range fs {
		out = append(out, names[f])
	}
	return am.Join(" และ ", out...)
}

// formatQuestion is the "which extra files" question: only what this agent
// makes (AM3: the invoice and expense agents make one workbook — offering a
// report or slides they cannot make was a trap), plus a PDF of the report
// when one can be made (AM2).
//
// AM11: a card's questions are kept as they were asked, in one language — l, the run's.
func formatQuestion(l am.Lang, outputs []string, pdf bool) am.Question {
	names := map[string]string{"docx": am.T(l, "รายงาน .docx"), "pptx": am.T(l, "สไลด์ .pptx"), "xlsx": am.T(l, "ตารางตัวเลข .xlsx")}
	var opts []am.Option
	for _, f := range []string{"docx", "pptx", "xlsx"} {
		if contains(outputs, f) {
			opts = append(opts, am.Option{Label: names[f], Value: json.RawMessage(`"` + f + `"`)})
		}
	}
	if pdf && contains(outputs, "pdf") {
		opts = append(opts, am.Option{Label: am.T(l, "รายงาน PDF"), Value: json.RawMessage(`"pdf"`)})
	}
	def := opts[len(opts)-1]
	for _, o := range opts {
		if string(o.Value) == `"xlsx"` {
			def = o
		}
	}
	return am.Question{Key: "output_format", Code: "output_format", Severity: "cosmetic", Header: am.T(l, "ชนิดไฟล์"),
		Question: am.T(l, "ต้องการไฟล์แบบไหนเพิ่มครับ (เลือกได้หลายแบบ)"), MultiSelect: true, Options: opts,
		Default: def.Value, DefaultLabel: def.Label}
}

// allowedOf keeps the formats this agent makes.
func (rc *runCtx) allowedOf(formats []string) []string {
	var out []string
	for _, f := range formats {
		if rc.pkg.Manifest.FormatAllowed(f) {
			out = append(out, f)
		}
	}
	return out
}

// outputFormats is what the agent's manifest says it makes.
func (rc *runCtx) outputFormats() []string {
	var out []string
	for _, o := range rc.pkg.Manifest.Outputs {
		out = append(out, o.Format)
	}
	return out
}

// formatCard asks which extra formats to make (multi-select) — only when
// the user's words say "more files" without naming one.
func (r *Runner) formatCard(ctx context.Context, run am.Run, outputs []string, pdf bool) error {
	ctx = am.InRunLang(ctx, run) // the card is this run's
	q := formatQuestion(am.LangOf(ctx), outputs, pdf)
	qs, _ := json.Marshal([]am.Question{q})
	card, err := r.Store.CreateCard(ctx, r.Store.Pool, am.Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "run", Kind: "output_format", Blocking: false, Questions: qs})
	if err != nil {
		return err
	}
	rid := run.ID
	_, _, err = r.Store.AddItem(ctx, r.Store.Pool, run.InstallID, run.TenantID, run.UserID, &rid,
		am.CardItemPayload(card, am.NoText, am.Text("สร้างจากตัวเลขชุดเดิม ไม่คำนวณใหม่")), nil)
	return err
}

// answerQuestion answers from the run's own record (AM4): a question about a
// figure the round computed is answered from that figure's provenance, word
// for word, never by a model (explain.go); a "why" the record cannot support
// gets "I don't know". Anything else may go to a model, numbers guarded and
// no cause given — it has no record of any cause.
//
// AM6: an answer is what a person is shown of it — the record's words with
// the person's own files and sheets, or a model's sentence; and a model's
// call that gave no usable answer is a cost of the platform's, not a charge:
// the person got the sentence that says so, not an answer.
func (r *Runner) answerQuestion(ctx context.Context, rc *runCtx, parent am.Run, text string) am.PersonText {
	return r.answerWith(ctx, rc, parent, text, nil).Text
}

// What a model is told when it is asked about a run's figures, or about the
// person's knowledge base. These are addressed to a model and are said to no
// person — which is why they stand here by themselves (AM11): the sentences
// a person reads are in answerWith and answerFromKnowledge, and each of
// those has its English. Which language the model answers in is said after
// them (answerIn).
const (
	// the system prompt's own part, after the package's instructions: a question about the run's figures
	figuresQuestionRules = "\n\nตอนนี้ให้ตอบคำถามของผู้ใช้เกี่ยวกับตัวเลขของรอบนี้ สั้น ๆ 1–3 ประโยค ตอบเป็น JSON {\"answer\": \"...\"} ใช้ตัวเลขจากรายการที่อ้างได้เท่านั้น ถ้าข้อมูลไม่พอให้บอกตรง ๆ ว่าไม่พอ " +
		"ห้ามบอกสาเหตุหรือเหตุผลว่าทำไมตัวเลขเป็นเช่นนั้น เพราะไม่มีบันทึกสาเหตุ ถ้าถูกถามว่าทำไม ให้ตอบว่าไม่ทราบ"
	// … and what it is told about passages from the person's knowledge base, beside the run's figures
	knowledgeRules = " ด้านล่างมีข้อความจากฐานความรู้ของผู้ใช้ (เช่น ผังบัญชี ศูนย์ต้นทุน นโยบาย) ใช้เพื่ออธิบายความหมายเท่านั้น " +
		"ข้อความจากฐานความรู้ไม่ใช่ตัวเลขของรอบนี้ และใช้แทนตัวเลขของรอบนี้ไม่ได้ ตัวเลขของรอบนี้ต้องมาจากรายการที่อ้างได้เท่านั้น " +
		"ตัวเลขจากฐานความรู้ (เช่น วงเงินในนโยบาย) ยกมาได้ตรงตัวอักษร และต้องบอกว่ามาจากฐานความรู้"
	// a question answered from the knowledge base alone
	knowledgeQuestionRules = "\n\nตอนนี้ให้ตอบคำถามของผู้ใช้จากข้อความในฐานความรู้ด้านล่างเท่านั้น สั้น ๆ 1–3 ประโยค ตอบเป็น JSON {\"answer\": \"...\"} " +
		"ถ้าข้อความไม่มีคำตอบ ให้บอกตรง ๆ ว่าฐานความรู้ไม่มีคำตอบ ตัวเลขใช้ได้เฉพาะที่อยู่ในข้อความ ยกมาตรงตัวอักษร"
	// the heading the person's question stands under in the user message
	questionHeading = "\n\nคำถาม: "
	// the reason an answer that gives a cause is sent back with
	noCauseReason = "ห้ามบอกสาเหตุของตัวเลข ตอบเฉพาะค่าที่อ้างได้ หรือบอกว่าไม่ทราบ"
)

// noModel is what a question gets when no model inside the organisation can answer it.
const noModel = "ดูตัวเลขและที่มาได้ในไฟล์ของรอบนี้ และในหน้า “ขั้นตอนและที่มาของตัวเลข” ครับ (agent ตัวนี้ไม่ส่งข้อมูลของคุณให้โมเดลนอกองค์กร และตอนนี้ไม่มีโมเดลในองค์กรที่ตอบคำถามได้)"

// answerWith is answerQuestion with what the person's knowledge base holds on
// the question (AM6; nil = nothing). The model is asked with the round's
// figures and the passages; the passages explain, they never replace a figure
// of the round, and a number quoted from them word for word (a limit of the
// policy) is a quotation, not a computed figure. An answer the model wrote
// with the passages says so (Knowledge): it is not part of the round's files.
// A figure the round's record explains is still answered from the record.
func (r *Runner) answerWith(ctx context.Context, rc *runCtx, parent am.Run, text string, k *am.KnowledgeContext) am.AgentText {
	l := am.LangOf(ctx) // the run's language: the record's own answer is built in it, and a model is told to answer in it
	ps, err := r.parentState(ctx, parent.ID)
	if err != nil {
		if k != nil {
			return r.answerFromKnowledge(ctx, rc, text, k)
		}
		return am.AgentText{Text: am.Text("รอให้รอบนี้คำนวณเสร็จก่อนนะครับ แล้วถามได้เลย")}
	}
	cv, raw, err := rc.readComputed(ctx, ps.computed)
	switch {
	case err == nil:
		// a "why" the record cannot support may still be one the passages answer
		if ans, theirs := explainAnswer(l, text, raw); ans != "" && (k == nil || ans != am.T(l, dontKnow)) {
			return am.AgentText{Text: am.FromPackage(ans, am.Text(dontKnow), theirs...)}
		}
	case k != nil:
		return r.answerFromKnowledge(ctx, rc, text, k)
	case asksWhy(text):
		return am.AgentText{Text: am.Text(dontKnow)}
	}
	models := rc.textModels()
	if err != nil || !r.Model.Available() || len(models) == 0 {
		if k != nil {
			return withoutModel(am.Text(noModel), k)
		}
		return am.AgentText{Text: am.Text(noModel)}
	}
	system := rc.pkg.Instructions + figuresQuestionRules + answerIn(l)
	user := narrativePrompt(l, cv)
	quotable := cv.Quotable
	if k != nil {
		// what the model is told about passages from the person's knowledge base, beside the round's figures
		system += knowledgeRules
		user += "\n\n" + k.Text
		quotable = knowledgeGuard(l, cv.Quotable, k)
	}
	user += questionHeading + text
	if rc.modelAway {
		// AM15: the model was found away a moment ago, reading these same words
		return am.AgentText{Text: am.ModelAwayAnswer()}
	}
	said, ok, away := r.askModel(ctx, rc, models, system, user, func(answer string) error {
		if err := GuardNumbersIn(l, answer, quotable, cv.QuotableIDs); err != nil {
			return err
		}
		if givesACause(answer) {
			return errors.New(noCauseReason)
		}
		return nil
	})
	if away {
		// AM15: nobody answered — which is not the agent being unsure of the figures
		return am.AgentText{Text: am.ModelAwayAnswer()}
	}
	if !ok {
		return am.AgentText{Text: am.Text("ผมยังตอบคำถามนี้จากตัวเลขที่มีได้ไม่มั่นใจครับ ดูรายละเอียดได้ในหน้า “ขั้นตอนและที่มาของตัวเลข”")}
	}
	return am.AgentText{Text: said, Knowledge: k.Shown()}
}

// answerFromKnowledge answers a question from the person's knowledge base
// alone (AM6): there is no round to ask about yet, or its figures cannot be
// read. Every number of the answer must be one of the passages', word for word.
func (r *Runner) answerFromKnowledge(ctx context.Context, rc *runCtx, text string, k *am.KnowledgeContext) am.AgentText {
	models := rc.textModels()
	if !r.Model.Available() || len(models) == 0 {
		return withoutModel(am.Text("ตอนนี้ไม่มีโมเดลในองค์กรที่ตอบคำถามได้ (agent ตัวนี้ไม่ส่งข้อมูลของคุณให้โมเดลนอกองค์กร)"), k)
	}
	system := rc.pkg.Instructions + knowledgeQuestionRules + answerIn(am.LangOf(ctx))
	quotable := knowledgeGuard(am.LangOf(ctx), nil, k)
	if rc.modelAway {
		return withoutModel(am.ModelAwayAnswer(), k) // AM15: found away a moment ago
	}
	said, ok, away := r.askModel(ctx, rc, models, system, k.Text+questionHeading+text, func(answer string) error {
		return GuardNumbersIn(am.LangOf(ctx), answer, quotable, nil)
	})
	if away {
		return withoutModel(am.ModelAwayAnswer(), k) // AM15
	}
	if !ok {
		return withoutModel(am.Text("ผมยังตอบคำถามนี้จากฐานความรู้ได้ไม่มั่นใจครับ"), k)
	}
	return am.AgentText{Text: said, Knowledge: k.Shown()}
}

// withoutModel is a sentence of the platform's, and where in the knowledge
// base the passages that were found are.
func withoutModel(said am.PersonText, k *am.KnowledgeContext) am.AgentText {
	return am.AgentText{Text: am.Text("%s — ส่วนที่เกี่ยวข้องในคลังความรู้ %s: %s", am.Words(said), am.Name(k.Base), k.Titles()),
		Knowledge: k.Shown()}
}

// knowledgeGuard is the round's quotable figures with every number the
// passages hold, as they are written: a number of the passages is a quotation.
func knowledgeGuard(l am.Lang, quotable map[string]string, k *am.KnowledgeContext) map[string]string {
	out := make(map[string]string, len(quotable))
	for key, v := range quotable {
		out[key] = v
	}
	for i, n := range numbersIn(l, k.Text) {
		out[fmt.Sprintf("passage_%d", i)] = normalizeNumberToken(n)
	}
	return out
}

// askModel asks the models for {"answer": "..."} that passes check: what a
// person is shown of it, and whether there is one. A call that gave no usable
// answer is a cost of the platform's, not a charge (AM6): the person got the
// sentence that says so, not an answer.
//
// AM15: away says there is none because no model the question may be sent to could be reached at all.
func (r *Runner) askModel(ctx context.Context, rc *runCtx, models []string, system, user string, check func(string) error) (said am.PersonText, ok, away bool) {
	var answer string
	if rc != nil {
		// AM13b (masking.go): an answer about a round is made from that round's data — the same rule
		ctx, _ = withModelCalls(ctx, !rc.docsMayLeave)
	}
	c, _, err := r.Model.CompleteWith(ctx, models, system, user, func(s string) error {
		var v struct {
			Answer string `json:"answer"`
		}
		if err := json.Unmarshal([]byte(extractJSON(s)), &v); err != nil || strings.TrimSpace(v.Answer) == "" {
			return fmt.Errorf("ไม่ใช่ JSON ที่มี answer")
		}
		if err := check(v.Answer); err != nil {
			return err
		}
		answer = v.Answer
		return nil
	})
	said = am.NoText
	if err == nil {
		said = am.FromModel(answer, am.NoText)
	}
	credits := c.CostUSD * r.CreditsPerUSD
	// "an answer" is one the run's own language shows (a model that answered in the other one gave none)
	none := said.EmptyIn(am.LangOf(ctx))
	switch {
	case c.CostUSD <= 0:
	case none:
		// the platform paid for the call; the person got no answer of it
		_ = r.Store.CostOnly(ctx, rc.install.ID, rc.run.ID, "reply", credits, c.Model, nil, "no_usable_answer")
	default:
		_ = r.Store.Charge(ctx, rc.install.ID, rc.run.ID, "reply", credits, c.Model, nil)
	}
	return said, !none, errors.Is(err, ErrModelUnreachable) && ctx.Err() == nil
}

// previewJob reads a sample export for the install wizard's column mapping.
func (r *Runner) previewJob(ctx context.Context, job am.Run, log *slog.Logger) error {
	rc, err := r.load(ctx, job, log)
	if err != nil {
		return r.fail(ctx, job, "load", err)
	}
	var params struct {
		FileID   uuid.UUID `json:"file_id"`
		Template bool      `json:"template"`
		// AM10: a form dropped into this round, for this round only
		RunID *uuid.UUID `json:"run_id"`
	}
	_ = json.Unmarshal(job.Params, &params)
	f, err := r.Store.GetFile(ctx, job.TenantID, job.UserID, params.FileID)
	if err != nil {
		return r.fail(ctx, job, "preview", err)
	}
	if params.Template && params.RunID != nil {
		return r.inspectRoundForm(ctx, rc, job, f, *params.RunID)
	}
	if params.Template {
		return r.inspectTemplate(ctx, rc, job, f)
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
			res, err := rc.exec(ctx, map[string]any{"op": "parse", "package": rc.pkg.PyPackage, "preview": true,
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
