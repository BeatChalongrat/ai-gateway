package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

const (
	actNone = iota
	actPark
	actCancel
	actAgain
)

// clarify runs the engine for one moment and acts on its plan: record
// defaults and remembered answers, create the card, park the run.
func (rc *runCtx) clarify(ctx context.Context, moment string, findings []am.Finding, pyAssumptions []am.Assumption,
	stepKey string, pre preflightOut) (int, error) {
	st := rc.r.Store
	if err := st.AddAssumptions(ctx, st.Pool, rc.run.ID, pyAssumptions); err != nil {
		return actNone, err
	}
	if v, ok := rc.answers["missing_file"]; ok && jsonString(v.Value) == "cancel" {
		return actCancel, nil
	}
	cards, err := st.RunCards(ctx, rc.run.ID)
	if err != nil {
		return actNone, err
	}
	asked, open := map[string]bool{}, map[string]bool{}
	openFiles := false
	for _, c := range cards {
		var qs []am.Question
		_ = json.Unmarshal(c.Questions, &qs)
		for _, q := range qs {
			asked[q.Key] = true
			if c.Status == "open" || c.Status == "interpreting" {
				open[q.Key] = true
			}
		}
		if c.Kind == "request_files" && (c.Status == "open" || c.Status == "interpreting") {
			openFiles = true
		}
	}
	// A request-files card closes itself once the files it asked for arrive.
	stillMissing := false
	for _, f := range findings {
		if f.Kind == "request_files" {
			stillMissing = true
		}
	}
	if openFiles && !stillMissing && moment == "preflight" {
		for _, c := range cards {
			if c.Kind == "request_files" && (c.Status == "open" || c.Status == "interpreting") {
				ans, _ := json.Marshal(map[string]any{"upload": true})
				if cd, changed, err := st.CloseCard(ctx, st.Pool, c.ID, "answered", ans, "upload", nil); err == nil && changed {
					_ = st.SyncCardItem(ctx, st.Pool, cd)
				}
			}
		}
		if err := rc.settleClarifySteps(ctx); err != nil {
			return actNone, err
		}
	}
	// While a replacement file is being asked for, the checks' own
	// missing-file finding is the same question: don't ask it twice.
	if openFiles && stillMissing {
		var kept []am.Finding
		for _, f := range findings {
			if f.Kind != "request_files" {
				kept = append(kept, f)
			}
		}
		findings = kept
		return actPark, nil
	}
	plan, err := am.Plan(am.PlanInput{Moment: moment, Findings: findings, Answers: rc.answers, Asked: asked,
		OpenKeys: open, Remembered: rc.remembered})
	if err != nil {
		return actNone, err
	}
	again := false
	if len(plan.AutoAnswers) > 0 {
		for _, a := range plan.AutoAnswers {
			if !a.Cosmetic {
				again = true // a remembered answer changes the numbers: re-run the checks with it
			}
		}
		if err := st.MergeAnswers(ctx, st.Pool, rc.run.ID, plan.AutoAnswers); err != nil {
			return actNone, err
		}
		if err := st.AddAssumptions(ctx, st.Pool, rc.run.ID, plan.Assumptions); err != nil {
			return actNone, err
		}
		if err := rc.reloadAnswers(ctx); err != nil {
			return actNone, err
		}
	}
	if plan.Card != nil {
		if err := rc.createCard(ctx, moment, plan.Card, pre, len(findings)); err != nil {
			return actNone, err
		}
		if plan.Card.Blocking {
			return actPark, nil
		}
	}
	if plan.Park {
		return actPark, nil
	}
	if again {
		return actAgain, nil
	}
	return actNone, nil
}

func (rc *runCtx) createCard(ctx context.Context, moment string, plan *am.CardPlan, pre preflightOut, total int) error {
	st := rc.r.Store
	qs, _ := json.Marshal(plan.Questions)
	card, err := st.CreateCard(ctx, st.Pool, am.Card{RunID: rc.run.ID, InstallID: rc.run.InstallID, TenantID: rc.run.TenantID,
		UserID: rc.run.UserID, Moment: moment, Kind: plan.Kind, Blocking: plan.Blocking, Questions: qs})
	if err != nil {
		return err
	}
	key := am.StepKey(rc.run.ID, "clarify", card.ID.String())
	stp, _, err := st.BeginStep(ctx, rc.run.ID, fmt.Sprintf("clarify:%s#%d", moment, card.Seq), "clarification", key,
		mustJSON(map[string]any{"card_id": card.ID, "keys": keysOf(plan.Questions), "blocking": plan.Blocking}))
	if err != nil {
		return err
	}
	if plan.Blocking {
		if err := st.AwaitStep(ctx, stp.ID); err != nil {
			return err
		}
	} else {
		_ = st.FinishStep(ctx, stp.ID, "succeeded", mustJSON(map[string]any{"defaults_applied": keysOf(plan.Questions)}), "", "", "", 0)
	}
	intro := cardIntro(moment, plan, pre, len(plan.Questions))
	rid := rc.run.ID
	if intro != "" {
		if _, _, err := st.AddItem(ctx, st.Pool, rc.run.InstallID, rc.run.TenantID, rc.run.UserID, &rid, "agent_text",
			map[string]any{"text": intro, "run_id": rid.String()}, nil); err != nil {
			return err
		}
	}
	_, _, err = st.AddItem(ctx, st.Pool, rc.run.InstallID, rc.run.TenantID, rc.run.UserID, &rid, "card",
		am.CardItemPayload(card, "", cardNote(plan)), nil)
	return err
}

// cardNote is the line under a card. A blocking card has none: the web card
// itself says a rememberable answer becomes next round's default.
func cardNote(plan *am.CardPlan) string {
	if plan.Blocking {
		return ""
	}
	return "ผมใช้ค่าตั้งต้นไปก่อนแล้ว ตอบภายหลังได้ จะสร้างไฟล์ใหม่ให้โดยไม่คำนวณใหม่"
}

func keysOf(qs []am.Question) []string {
	var k []string
	for _, q := range qs {
		k = append(k, q.Key)
	}
	return k
}

func cardIntro(moment string, plan *am.CardPlan, pre preflightOut, n int) string {
	if plan.Kind == "request_files" {
		return "ยังขาดไฟล์ที่ต้องใช้ครับ ลากไฟล์มาวางในช่องด้านล่าง หรือในแชทได้เลย"
	}
	if !plan.Blocking {
		return fmt.Sprintf("มี %d เรื่องเล็ก ๆ เรื่องการแสดงผล ผมใช้ค่าตั้งต้นไปก่อน ตอบภายหลังได้ครับ", n)
	}
	if moment == "postcompute" {
		return fmt.Sprintf("คำนวณเสร็จแล้ว แต่ตัวเลขชี้ให้เห็น %d เรื่องที่ต้องให้คุณตัดสินก่อนทำรายงานครับ", n)
	}
	rows := numberOf(pre.Stats["rows"])
	intro := ""
	if rows > 0 {
		intro = fmt.Sprintf("อ่านครบแล้ว %s รายการ", thousands(rows))
		if d, c := strOf(pre.Stats["debit_total"]), strOf(pre.Stats["credit_total"]); d != "" && d == c {
			intro += " เดบิตกับเครดิตสมดุลดี"
		}
		intro += " "
	}
	return intro + fmt.Sprintf("มี %d เรื่องที่ผมไม่แน่ใจ ขอถามก่อนเริ่มคำนวณครับ", n)
}

func numberOf(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case json.Number:
		n, _ := x.Int64()
		return n
	}
	return 0
}

func strOf(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func thousands(n int64) string {
	s := strconv.FormatInt(n, 10)
	var b strings.Builder
	for i, ch := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(ch)
	}
	return b.String()
}

func jsonString(raw json.RawMessage) string {
	var s string
	_ = json.Unmarshal(raw, &s)
	return s
}

func mustJSON(v any) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

// settleClarifySteps closes clarification steps whose card is no longer
// open, recording the wait as the step's duration.
func (rc *runCtx) settleClarifySteps(ctx context.Context) error {
	st := rc.r.Store
	steps, err := st.AwaitingSteps(ctx, rc.run.ID)
	if err != nil {
		return err
	}
	for _, s := range steps {
		var in struct {
			CardID uuid.UUID `json:"card_id"`
		}
		_ = json.Unmarshal(s.Input, &in)
		c, err := st.CardByID(ctx, in.CardID)
		if err != nil || c.Status == "open" || c.Status == "interpreting" {
			continue
		}
		via := ""
		if c.AnsweredVia != nil {
			via = *c.AnsweredVia
		}
		status := "succeeded"
		if c.Status == "superseded" || c.Status == "cancelled" {
			status = "skipped"
		}
		if err := st.FinishStep(ctx, s.ID, status, mustJSON(map[string]any{"answer": c.Answer, "status": c.Status, "via": via}),
			"", "", "", 0); err != nil {
			return err
		}
	}
	return nil
}

// interpretCards reads free-text answers. Keywords first; then one
// constrained model call that may only pick one of the card's own options.
// Uninterpretable twice → the default is used and written as an assumption.
func (rc *runCtx) interpretCards(ctx context.Context) (bool, error) {
	st := rc.r.Store
	cards, err := st.RunCards(ctx, rc.run.ID)
	if err != nil {
		return false, err
	}
	parked := false
	for _, c := range cards {
		if c.Status != "interpreting" || c.AnswerText == nil {
			continue
		}
		var qs []am.Question
		if err := json.Unmarshal(c.Questions, &qs); err != nil {
			return false, err
		}
		answers := map[string]am.Answer{}
		if len(c.Answer) > 0 && string(c.Answer) != "null" {
			_ = json.Unmarshal(c.Answer, &answers)
		}
		text := *c.AnswerText
		var missing []am.Question
		for _, q := range qs {
			if _, ok := answers[q.Key]; ok {
				continue
			}
			if o, ok := am.InterpretText(q, text); ok {
				answers[q.Key] = am.Answer{Value: o.Value, Label: o.Label, Moment: c.Moment, CardID: c.ID.String(), Via: "text",
					Cosmetic: q.Severity == "cosmetic", Code: q.Code}
				continue
			}
			if o, ok := rc.classify(ctx, q, text); ok {
				answers[q.Key] = am.Answer{Value: o.Value, Label: o.Label, Moment: c.Moment, CardID: c.ID.String(), Via: "text",
					Cosmetic: q.Severity == "cosmetic", Code: q.Code}
				continue
			}
			missing = append(missing, q)
		}
		status, via := "answered", "text"
		var assumptions []am.Assumption
		if len(missing) > 0 {
			if c.Attempts < am.MaxReprompts {
				if err := st.ReopenForReask(ctx, c.ID, "uninterpretable"); err != nil {
					return false, err
				}
				c.Status, c.Attempts = "open", c.Attempts+1
				_ = st.SyncCardItem(ctx, st.Pool, c)
				labels := []string{}
				for _, q := range missing {
					labels = append(labels, "“"+q.Header+"”")
				}
				_ = st.PostText(ctx, rc.run, fmt.Sprintf("ขอโทษครับ ผมยังจับไม่ได้ว่า “%s” ตอบเรื่อง %s ว่าอย่างไร ช่วยกดเลือกจากปุ่มในการ์ดด้านบนได้ไหมครับ (ถามซ้ำครั้งที่ %d จาก %d)",
					trim(text, 80), strings.Join(labels, ", "), c.Attempts, am.MaxReprompts), nil)
				parked = true
				continue
			}
			// Asked twice already: take the default and say so in the report.
			for _, q := range missing {
				answers[q.Key] = am.Answer{Value: q.Default, Label: q.DefaultLabel, Moment: c.Moment, CardID: c.ID.String(),
					Via: "reprompt_default", Cosmetic: q.Severity == "cosmetic", Code: q.Code}
				assumptions = append(assumptions, am.Assumption{Code: q.Code, Source: "reprompt_default",
					Text:           fmt.Sprintf("%s: ใช้ค่าตั้งต้น “%s” หลังถามซ้ำ %d ครั้งแล้วยังไม่ได้คำตอบที่ชัด", q.Header, q.DefaultLabel, am.MaxReprompts),
					ChangesNumbers: q.Severity != "cosmetic"})
			}
			status, via = "defaulted", "reprompt_default"
		}
		ab, _ := json.Marshal(answers)
		cd, changed, err := st.CloseCard(ctx, st.Pool, c.ID, status, ab, via, nil)
		if err != nil {
			return false, err
		}
		if !changed {
			continue
		}
		_ = st.SyncCardItem(ctx, st.Pool, cd)
		if c.Kind == "late_file" || c.Kind == "output_format" {
			continue // those are acted on by the control plane's card path
		}
		if err := st.MergeAnswers(ctx, st.Pool, rc.run.ID, answers); err != nil {
			return false, err
		}
		if err := st.AddAssumptions(ctx, st.Pool, rc.run.ID, assumptions); err != nil {
			return false, err
		}
		if n, err := st.RememberAnswers(ctx, st.Pool, rc.run, c.ID, qs, answers); err == nil && n > 0 {
			_ = st.PostNotice(ctx, rc.run, "จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้", "defaults")
		}
	}
	if err := rc.settleClarifySteps(ctx); err != nil {
		return false, err
	}
	return parked, nil
}

// classify asks the model to pick one of a question's own options for a
// sentence the user typed. It can only return an option; it cannot invent
// one, and it never decides what to ask.
func (rc *runCtx) classify(ctx context.Context, q am.Question, text string) (am.Option, bool) {
	models := rc.textModels()
	if !rc.r.Model.Available() || len(models) == 0 {
		return am.Option{}, false
	}
	var opts strings.Builder
	for i, o := range q.Options {
		fmt.Fprintf(&opts, "%d. %s — %s\n", i+1, o.Label, o.Description)
	}
	system := "คุณแปลคำตอบภาษาไทยของผู้ใช้ให้เป็นหนึ่งในตัวเลือกที่กำหนด ตอบเป็น JSON {\"choice\": หมายเลข} หรือ {\"choice\": null} ถ้าคำตอบไม่ตรงกับตัวเลือกใดชัดเจน ห้ามเดา"
	user := fmt.Sprintf("คำถาม: %s\nตัวเลือก:\n%sคำตอบของผู้ใช้: %s", q.Question, opts.String(), text)
	var choice *int
	c, _, err := rc.r.Model.CompleteWith(ctx, models, system, user, func(s string) error {
		var v struct {
			Choice *int `json:"choice"`
		}
		if err := json.Unmarshal([]byte(extractJSON(s)), &v); err != nil {
			return fmt.Errorf("not JSON")
		}
		if v.Choice != nil && (*v.Choice < 1 || *v.Choice > len(q.Options)) {
			return fmt.Errorf("choice out of range")
		}
		choice = v.Choice
		return nil
	})
	if c.CostUSD > 0 { // paid whether or not an answer was usable
		_ = rc.r.Store.Charge(ctx, rc.install.ID, rc.run.ID, "classify:"+q.Key+":"+trim(text, 40), c.CostUSD*rc.r.CreditsPerUSD, c.Model, nil)
	}
	if err != nil || choice == nil {
		return am.Option{}, false
	}
	return q.Options[*choice-1], true
}

func extractJSON(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "```json")
	s = strings.TrimPrefix(s, "```")
	s = strings.TrimSuffix(s, "```")
	if i := strings.Index(s, "{"); i >= 0 {
		if j := strings.LastIndex(s, "}"); j > i {
			return s[i : j+1]
		}
	}
	return s
}

// consumeReplacements acts on "send a corrected file instead": the file
// leaves the run, the answers about it are dropped (they were about the old
// bytes), and the run waits for the replacement.
func (rc *runCtx) consumeReplacements(ctx context.Context) (bool, error) {
	st := rc.r.Store
	var drop []string
	var removeNames []string
	for key, a := range rc.answers {
		if jsonString(a.Value) != "replace" {
			continue
		}
		drop = append(drop, key)
		if i := strings.Index(key, ":"); i >= 0 {
			removeNames = append(removeNames, key[i+1:])
		} else if key == "unbalanced" {
			files, err := st.RunFiles(ctx, rc.run.ID, "active")
			if err != nil {
				return false, err
			}
			// The primary is the file the last preflight named primary.
			if p := rc.lastPrimary(ctx); p != "" {
				removeNames = append(removeNames, p)
			} else if len(files) == 1 {
				removeNames = append(removeNames, files[0].Name)
			}
		}
	}
	if len(drop) == 0 {
		return false, nil
	}
	if err := st.SetFileStatus(ctx, st.Pool, rc.run.ID, removeNames, "removed"); err != nil {
		return false, err
	}
	// Every answer about data that is leaving is stale.
	for key, a := range rc.answers {
		if a.Moment == "postcompute" {
			drop = append(drop, key)
		}
	}
	if err := st.DeleteAnswers(ctx, rc.run.ID, drop); err != nil {
		return false, err
	}
	q := am.Question{Key: "replace_file:" + strings.Join(removeNames, ","), Code: "missing_file", Severity: "blocking",
		Header: "ขอไฟล์ใหม่", Question: "ส่งไฟล์ที่แก้แล้วแทน “" + strings.Join(removeNames, "”, “") + "” ได้เลยครับ ผมจะตรวจใหม่ตั้งแต่ต้น",
		Options: []am.Option{{Label: "รอก่อน จะอัปโหลดให้", Value: json.RawMessage(`"wait"`)},
			{Label: "ยกเลิกรอบนี้", Value: json.RawMessage(`"cancel"`)}},
		Default: json.RawMessage(`"wait"`), DefaultLabel: "รอก่อน จะอัปโหลดให้",
		Request: mustJSON([]map[string]any{{"role": "primary", "label": "ไฟล์ที่แก้แล้ว", "why": "ใช้แทนไฟล์เดิม", "required": true}})}
	if err := rc.createCard(ctx, "preflight", &am.CardPlan{Kind: "request_files", Blocking: true, Questions: []am.Question{q}},
		preflightOut{}, 1); err != nil {
		return false, err
	}
	if err := rc.reloadAnswers(ctx); err != nil {
		return false, err
	}
	return true, nil
}

func (rc *runCtx) lastPrimary(ctx context.Context) string {
	var roles map[string]string
	var raw json.RawMessage
	err := rc.r.Store.Pool.QueryRow(ctx, `SELECT output->'roles' FROM agent_market.agent_run_steps
		WHERE run_id = $1 AND name = 'preflight' AND status = 'succeeded' ORDER BY seq DESC LIMIT 1`, rc.run.ID).Scan(&raw)
	if err != nil || json.Unmarshal(raw, &roles) != nil {
		return ""
	}
	for name, role := range roles {
		if role == "primary" {
			return name
		}
	}
	return ""
}
