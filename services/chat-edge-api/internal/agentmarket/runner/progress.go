package runner

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// syncProgress rebuilds a run's inline progress card from its step rows —
// the card always says what the audit trail says.
func (r *Runner) syncProgress(ctx context.Context, runID uuid.UUID) {
	run, err := r.Store.RunByID(ctx, runID)
	if err != nil || (run.Kind != am.KindRun && run.Kind != am.KindSelfTest) {
		return
	}
	item, err := r.Store.ItemFor(ctx, run.InstallID, "progress", "run_id", run.ID.String())
	if err != nil {
		return
	}
	steps, err := r.Store.ListSteps(ctx, run.ID)
	if err != nil {
		return
	}
	p := BuildProgress(run, steps, r.Now())
	if webSteps(steps) || r.fromRequest(ctx, run) {
		p = buildWebProgress(run, steps, r.Now())
	}
	// AM11: the card is the run's and is written in its language — also when the run is ended under a
	// context of its own (a cancel, a failure), which carries none
	_ = r.Store.UpdateItem(am.InRunLang(ctx, run), item.ID, p)
}

// BuildProgress turns step rows into the progress card's lines.
func BuildProgress(run am.Run, steps []am.Step, now time.Time) am.Progress {
	p := am.Progress{RunID: run.ID.String(), Kind: run.Kind, Status: run.Status, Outcome: run.Outcome(), Reason: run.StatusReason,
		StartedAt: run.StartedAt, FinishedAt: run.FinishedAt, AwaitingMS: run.AwaitingMS}
	if run.StartedAt != nil {
		end := now
		if run.FinishedAt != nil {
			end = *run.FinishedAt
		}
		p.ElapsedMS = end.Sub(*run.StartedAt).Milliseconds()
	}
	status := func(s string) string {
		switch s {
		case "succeeded":
			return "done"
		case "running":
			return "running"
		case "awaiting":
			return "waiting"
		case "failed":
			return "failed"
		case "skipped":
			return "skipped"
		}
		return "pending"
	}
	// Parse steps collapse into one line.
	var parsed, parsing, parseFailed int
	var names []string
	latest := map[string]am.Step{}
	var clar []am.Step
	for _, s := range steps {
		switch {
		case strings.HasPrefix(s.Name, "parse:"):
			names = append(names, strings.TrimPrefix(s.Name, "parse:"))
			switch s.Status {
			case "succeeded":
				parsed++
			case "failed":
				parseFailed++
			default:
				parsing++
			}
		case strings.HasPrefix(s.Name, "clarify:"):
			clar = append(clar, s)
		default:
			latest[s.Name] = s
		}
	}
	// every line is built here from the step rows: a label of the platform's,
	// and a detail made of counts, names and the package's own words
	add := func(key string, label am.PersonText, st string, detail am.PersonText) {
		p.Steps = append(p.Steps, am.ProgressStep{Key: key, Label: label, Status: st, Detail: detail})
	}
	none := am.NoText
	if len(names) > 0 {
		st := "done"
		if parsing > 0 {
			st = "running"
		}
		if parseFailed > 0 {
			st = "failed"
		}
		add("parse", am.Text("อ่านไฟล์ %s ไฟล์", am.Count(len(names))), st, am.Text("%s", am.FileNames(names)))
	} else {
		add("parse", am.Text("อ่านไฟล์"), "pending", none)
	}
	if s, ok := latest["preflight"]; ok {
		detail := none
		var o struct {
			Stats map[string]any `json:"stats"`
		}
		if json.Unmarshal(s.Output, &o) == nil && o.Stats != nil {
			if n := numberOf(o.Stats["rows"]); n > 0 {
				detail = am.Text("%s แถว", am.Count(int(n)))
				if d, c := strOf(o.Stats["debit_total"]), strOf(o.Stats["credit_total"]); d != "" && d == c {
					detail = am.Text("%s แถว · เดบิตเครดิตสมดุล", am.Count(int(n)))
				}
			}
		}
		add("preflight", am.Text("ตรวจความครบถ้วน"), status(s.Status), detail)
	} else {
		add("preflight", am.Text("ตรวจความครบถ้วน"), "pending", none)
	}
	for _, s := range clar {
		label := am.Text("ถามคุณก่อนคำนวณ")
		if strings.Contains(s.Name, "postcompute") {
			label = am.Text("ถามเรื่องที่ตัวเลขชี้ให้เห็น")
		}
		st := status(s.Status)
		detail := none
		if st == "done" {
			detail = am.Text("ได้คำตอบแล้ว")
			if s.DurationMS != nil {
				detail = am.Text("ได้คำตอบแล้ว · รอ %s", am.Words(humanMS(*s.DurationMS)))
			}
		} else if st == "waiting" {
			detail = am.Text("รอคำตอบของคุณ")
		}
		add(s.Name, label, st, detail)
	}
	if s, ok := latest["compute"]; ok {
		add("compute", am.Text("คำนวณตัวเลขด้วยสคริปต์ (ไม่ใช้โมเดล)"), status(s.Status), none)
	} else {
		add("compute", am.Text("คำนวณตัวเลขด้วยสคริปต์ (ไม่ใช้โมเดล)"), "pending", none)
	}
	if s, ok := latest["postcompute"]; ok {
		detail := none
		if c, ok := latest["compute"]; ok {
			var o struct {
				Summary map[string]any `json:"summary"`
			}
			if json.Unmarshal(c.Output, &o) == nil && o.Summary != nil {
				detail = am.Text("พบ %s รายการ", am.Count(int(numberOf(o.Summary["exceptions"]))))
			}
		}
		add("postcompute", am.Text("ตรวจกฎหารายการผิดปกติ"), status(s.Status), detail)
	} else {
		add("postcompute", am.Text("ตรวจกฎหารายการผิดปกติ"), "pending", none)
	}
	if s, ok := latest["narrative"]; ok {
		var o narrativeOut
		_ = json.Unmarshal(s.Output, &o)
		detail := none
		switch {
		case o.Model != "" && o.SelfHosted:
			detail = am.Text("โมเดล %s ในองค์กร", am.ModelName(o.Model))
		case o.Model != "":
			detail = am.Text("โมเดล %s", am.ModelName(o.Model))
		case s.Status == "succeeded":
			detail = am.Text("เขียนจากกฎของ agent")
		}
		add("narrative", am.Text("เขียนคำอธิบาย"), status(s.Status), detail)
	} else {
		add("narrative", am.Text("เขียนคำอธิบาย"), "pending", none)
	}
	if s, ok := latest["render"]; ok {
		detail := none
		var o renderOut
		if json.Unmarshal(s.Output, &o) == nil {
			var th struct {
				Name string `json:"name"`
			}
			_ = json.Unmarshal(o.Theme, &th)
			if th.Name != "" {
				detail = am.Text("ธีม “%s”", am.Typed(th.Name, 60))
			}
		}
		add("render", am.Text("สร้างไฟล์ผลลัพธ์"), status(s.Status), detail)
		q := qualitySummary(o)
		if s.Status == "failed" {
			// AM5: which line failed is said here; why is said by the round's
			// message (in words) and by the run page (in full).
			add("quality", am.Text("ตรวจคุณภาพเอกสาร"), "failed", none)
		} else if !q.Empty() {
			add("quality", am.Text("ตรวจคุณภาพเอกสาร"), status(s.Status), q)
		}
	} else {
		add("render", am.Text("สร้างไฟล์ผลลัพธ์"), "pending", none)
	}
	// AM5: the package's own verdict on the round, in its words. AM6: a gate
	// that was checked and did not pass is a verdict, not a step that failed —
	// its line reads "open".
	if s, ok := latest["gate"]; ok {
		var g gateView
		_ = json.Unmarshal(s.Output, &g)
		st := status(s.Status)
		if st == "failed" {
			st = "open"
		}
		what, _ := g.words()
		add("gate", am.Text("ตรวจว่างานของรอบนี้ครบตามเกณฑ์ของ agent"), st, what)
	}
	if run.Status == am.RunFailed && !run.GateOpen {
		for i := range p.Steps {
			if p.Steps[i].Status == "running" {
				p.Steps[i].Status = "failed"
			}
		}
	}
	return p
}

// qualitySummary says what the gate asserted (AM8): how many files were opened again and how many
// figures read back out of them matched the computed table — not "14 of 14", which counted one check
// three times and a font check like an accounting assertion.
func qualitySummary(o renderOut) am.PersonText {
	files, values, failed := 0, 0, 0
	for _, a := range o.Artifacts {
		var q struct {
			Checks []struct {
				OK     bool `json:"ok"`
				Values int  `json:"values"`
			} `json:"checks"`
		}
		_ = json.Unmarshal(a.Quality, &q)
		if len(q.Checks) == 0 {
			continue
		}
		files++
		for _, c := range q.Checks {
			values += c.Values
			if !c.OK {
				failed++
			}
		}
	}
	switch {
	case files == 0:
		return am.NoText
	case failed > 0:
		return am.Text("เปิดไฟล์ตรวจซ้ำ %s ไฟล์ · ไม่ผ่าน %s เรื่อง", am.Count(files), am.Count(failed))
	case values > 0:
		return am.Text("เปิดไฟล์ตรวจซ้ำ %s ไฟล์ · ตัวเลข %s ค่าตรงกับที่คำนวณ", am.Count(files), am.Count(values))
	}
	return am.Text("เปิดไฟล์ตรวจซ้ำ %s ไฟล์ · ผ่านทุกเรื่อง", am.Count(files))
}

func humanMS(ms int64) am.PersonText {
	d := time.Duration(ms) * time.Millisecond
	switch {
	case d < time.Minute:
		return am.Text("%s วินาที", am.Count(int(d.Seconds())))
	case d < time.Hour:
		return am.Text("%s นาที", am.Count(int(d.Minutes())))
	case d < 48*time.Hour:
		return am.Text("%s ชั่วโมง", am.Count(int(d.Hours())))
	}
	return am.Text("%s วัน", am.Count(int(d.Hours()/24)))
}
