package runner

import (
	"context"
	"encoding/json"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── A file the round's content cannot be (AM23 §1.5) ─────────────────────
//
// A report or a deck asked for that this round's content cannot be — a deck of 800 unmatched lines, a document of a
// round with nothing to say — is refused before any file is written, with why and what can be made instead, both
// computed by the package from the round's own figures (deliverables/document.py plan): one question, answered in
// one tap. Asked with the round, it is a blocking card of the clarification engine (never answered by its default:
// re-asked at most twice, then left for a tap); asked of a finished round, a card on that round whose answer makes
// what was picked. The words are the package's, the same whichever way the file was asked for.

// fileRefusedCode is the code of a refusal's question (and the head of its key: "file_refused.pptx").
const fileRefusedCode = "file_refused"

// planRefusal is one file the engine's plan says this round's content cannot be.
type planRefusal struct {
	Format   string `json:"format"`
	Code     string `json:"code"`
	Header   string `json:"header"`
	Question string `json:"question"`
	Options  []struct {
		Label string          `json:"label"`
		Value json.RawMessage `json:"value"`
	} `json:"options"`
}

// fileChoice is what a refusal's option makes instead: these formats, and a deck of the rows at or above an amount.
type fileChoice struct {
	Formats  []string `json:"formats"`
	DeckOver string   `json:"deck_over,omitempty"`
}

// planFiles asks the engine which documents of formats this round's content cannot be. Nothing is asked when the
// set holds no report or deck: a workbook holds anything.
func (rc *runCtx) planFiles(ctx context.Context, formats []string, computed blobRef, computeKey string, narrative narrativeOut,
	deckOver string) ([]planRefusal, error) {
	docs := false
	for _, f := range formats {
		docs = docs || f == "docx" || f == "pptx"
	}
	if !docs || rc.pkg.Manifest.FromRequest() {
		return nil, nil
	}
	key := am.StepKey(rc.run.ID, "plan", computeKey, formats, deckOver, string(narrative.Narrative))
	raw, err := rc.step(ctx, "plan", "check", key, map[string]any{"formats": formats, "deck_over": deckOver}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			cpath, cleanup, err := rc.localBlob(ctx, computed.Key)
			if err != nil {
				return nil, 0, "", "", err
			}
			defer cleanup()
			req := map[string]any{"op": "plan", "package": rc.pkg.PyPackage, "computed": "computed.json", "formats": formats,
				"narrative": json.RawMessage(narrative.Narrative)}
			if deckOver != "" {
				req["deck_over"] = deckOver
			}
			res, err := rc.exec(ctx, req, []Input{{Name: "computed.json", LocalPath: cpath}})
			if err != nil {
				return nil, 0, "", "", err
			}
			defer res.Cleanup()
			return json.RawMessage(mustJSON(res.Result)), rc.sandboxCredits(res.Elapsed), "deliverables/document.py", "", nil
		})
	if err != nil {
		return nil, err
	}
	var out struct {
		Refusals []planRefusal `json:"refusals"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out.Refusals, nil
}

// refusalFinding is a refusal as a question of the clarification engine: blocking, its options the package's, its
// default the first of them — the workbook alone, what the round makes anyway — which a person may tap and no
// one else picks (clarify.go: never taken after the re-asks).
func refusalFinding(r planRefusal) am.Finding {
	f := am.Finding{Key: fileRefusedCode + "." + r.Format, Code: fileRefusedCode, Severity: "blocking", Moment: "postcompute",
		Header: r.Header, Question: r.Question, Kind: "question"}
	for _, o := range r.Options {
		f.Options = append(f.Options, am.Option{Label: o.Label, Value: o.Value})
	}
	if len(f.Options) > 0 {
		f.Default = f.Options[0].Value
	}
	return f
}

// askPlan is the round's own refusals, asked before its files are made: actPark while a question waits.
func (rc *runCtx) askPlan(ctx context.Context, computed blobRef, computeKey string, narrative narrativeOut) (int, error) {
	refusals, err := rc.planFiles(ctx, rc.formats(), computed, computeKey, narrative, rc.deckOver)
	if err != nil || len(refusals) == 0 {
		return actNone, err
	}
	var findings []am.Finding
	for _, r := range refusals {
		if _, done := rc.answers[fileRefusedCode+"."+r.Format]; !done {
			findings = append(findings, refusalFinding(r))
		}
	}
	if len(findings) == 0 {
		return actNone, nil
	}
	return rc.clarify(ctx, "postcompute", findings, nil, "", preflightOut{})
}

// fileChoices applies what was picked on a round's refusals to its set of files: the refused one out, the picked
// ones in (only those the agent makes), and the deck's threshold kept for the render.
func (rc *runCtx) fileChoices(formats []string) []string {
	out := append([]string{}, formats...)
	for key, a := range rc.answers {
		refused, ok := strings.CutPrefix(key, fileRefusedCode+".")
		if !ok {
			continue
		}
		var ch fileChoice
		if json.Unmarshal(a.Value, &ch) != nil {
			continue
		}
		var kept []string
		for _, f := range out {
			if f != refused {
				kept = append(kept, f)
			}
		}
		made, _ := rc.pkg.Manifest.MadeOf(ch.Formats)
		for _, f := range made {
			if !contains(kept, f) {
				kept = append(kept, f)
			}
		}
		if ch.DeckOver != "" {
			rc.deckOver = ch.DeckOver
		}
		out = kept
	}
	return out
}

// refusalCard asks a refusal of a finished round: a card on the round (it does not wait — the round is done), its
// one question the package's, its answer making what was picked (the API's applyFormats).
func (r *Runner) refusalCard(ctx context.Context, run am.Run, ref planRefusal) error {
	ctx = am.InRunLang(ctx, run)
	f := refusalFinding(ref)
	q := am.Question{Key: "output_format", Code: fileRefusedCode, Severity: "cosmetic", Header: f.Header, Question: f.Question,
		Options: f.Options, Default: f.Default}
	for _, o := range f.Options {
		if am.JSONEqual(o.Value, f.Default) {
			q.DefaultLabel = o.Label
		}
	}
	qs, _ := json.Marshal([]am.Question{q})
	card, err := r.Store.CreateCard(ctx, r.Store.Pool, am.Card{RunID: run.ID, InstallID: run.InstallID, TenantID: run.TenantID,
		UserID: run.UserID, Moment: "run", Kind: "output_format", Blocking: false, Questions: qs})
	if err != nil {
		return err
	}
	rid := run.ID
	_, _, err = r.Store.AddItem(ctx, r.Store.Pool, run.InstallID, run.TenantID, run.UserID, &rid, am.CardItemPayload(card, am.NoText, am.NoText), nil)
	return err
}

// refusalIn: one of the questions is a refusal's (never answered by its default).
func refusalIn(qs []am.Question) bool {
	for _, q := range qs {
		if q.Code == fileRefusedCode {
			return true
		}
	}
	return false
}

// xlsSaid is the one sentence a request for a .xls file is answered with (AM23 §1.6): never made, and why.
func xlsSaid() am.PersonText {
	return am.Text("ไฟล์ .xls รับได้ไม่เกิน 65,536 แถว ไม่มีแถวหัวตารางที่ตรึงซ้ำทุกหน้า และไม่มีพื้นที่พิมพ์ ซึ่งกระดาษทำการต้องใช้ จึงทำเป็น .xlsx ให้แทน")
}

// xlsWords: what a typed message names a .xls file by (read from what a person typed).
var xlsWords = []string{"xls", ".xls", "excel 97", "excel 2003", "เอ็กเซลเก่า"}

func asksForXLS(text string) bool { return am.HasWord(text, xlsWords...) }

// formatsInWords (AM23 §1.5): the kinds of file the words typed with a round's files ask for are the round's request
// too — what the agent makes joins the round's set, what it does not is refused by name with what it makes, and a
// .xls is answered with why it is never made — said once, before any work (until AM23 the words reached only the
// reader of the package's directives, which filed "ขอเป็น Word" under "other" and said, after the workbook was made,
// that it could only sort the items).
func (rc *runCtx) formatsInWords(ctx context.Context) error {
	if rc.run.Kind != am.KindRun || rc.pkg.Manifest.FromRequest() {
		return nil
	}
	text := strings.TrimSpace(rc.run.Message)
	asked := detectFormatsFor(text, rc.pdfOffered())
	if len(asked) == 0 && !asksForXLS(text) {
		return nil
	}
	// (a step, recorded: a round that waits and goes on says it once)
	key := am.StepKey(rc.run.ID, "formats_asked", text)
	_, err := rc.step(ctx, "formats_asked", "check", key, map[string]any{"asked": asked, "xls": asksForXLS(text)}, true,
		func(ctx context.Context) (any, float64, string, string, error) {
			var cur []string
			_ = json.Unmarshal(rc.run.Formats, &cur)
			made, notMade := rc.pkg.Manifest.MadeOf(asked)
			for _, f := range made {
				if !contains(cur, f) {
					cur = append(cur, f)
				}
			}
			if err := rc.r.Store.SetFormats(ctx, rc.r.Store.Pool, rc.run.ID, cur); err != nil {
				return nil, 0, "", "", err
			}
			if asksForXLS(text) {
				_ = rc.r.Store.PostText(ctx, rc.run, xlsSaid())
			}
			if len(notMade) > 0 {
				_ = rc.r.Store.PostText(ctx, rc.run, am.NotMadeText(notMade, rc.pkg.Manifest.OutputFormats()))
			}
			return map[string]any{"formats": cur, "not_made": notMade}, 0, "", "", nil
		})
	if err != nil {
		return err
	}
	if run, err := rc.r.Store.RunByID(ctx, rc.run.ID); err == nil {
		rc.run.Formats = run.Formats
	}
	return nil
}

// isFormatRequest: words the package's directives were read out of that ask for a kind of file — answered as a
// format request (formatsInWords), never as a directive the agent cannot follow.
func isFormatRequest(words string) bool {
	return len(detectFormats(words)) > 0 || asksForPDF(words) || asksForXLS(words)
}
