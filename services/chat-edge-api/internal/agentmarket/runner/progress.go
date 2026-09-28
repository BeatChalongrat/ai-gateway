package runner

import (
	"context"
	"encoding/json"
	"fmt"
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
	_ = r.Store.UpdateItem(ctx, item.ID, p)
}

// BuildProgress turns step rows into the progress card's lines.
func BuildProgress(run am.Run, steps []am.Step, now time.Time) am.Progress {
	p := am.Progress{RunID: run.ID.String(), Kind: run.Kind, Status: run.Status, Reason: run.StatusReason,
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
	add := func(key, label, st, detail string) {
		p.Steps = append(p.Steps, am.ProgressStep{Key: key, Label: label, Status: st, Detail: detail})
	}
	if len(names) > 0 {
		st := "done"
		if parsing > 0 {
			st = "running"
		}
		if parseFailed > 0 {
			st = "failed"
		}
		add("parse", fmt.Sprintf("อ่านไฟล์ %d ไฟล์", len(names)), st, strings.Join(names, ", "))
	} else {
		add("parse", "อ่านไฟล์", "pending", "")
	}
	if s, ok := latest["preflight"]; ok {
		detail := ""
		var o struct {
			Stats map[string]any `json:"stats"`
		}
		if json.Unmarshal(s.Output, &o) == nil && o.Stats != nil {
			if n := numberOf(o.Stats["rows"]); n > 0 {
				detail = thousands(n) + " แถว"
				if d, c := strOf(o.Stats["debit_total"]), strOf(o.Stats["credit_total"]); d != "" && d == c {
					detail += " · เดบิตเครดิตสมดุล"
				}
			}
		}
		add("preflight", "ตรวจความครบถ้วน", status(s.Status), detail)
	} else {
		add("preflight", "ตรวจความครบถ้วน", "pending", "")
	}
	for _, s := range clar {
		label := "ถามคุณก่อนคำนวณ"
		if strings.Contains(s.Name, "postcompute") {
			label = "ถามเรื่องที่ตัวเลขชี้ให้เห็น"
		}
		st := status(s.Status)
		detail := ""
		if st == "done" {
			detail = "ได้คำตอบแล้ว"
			if s.DurationMS != nil {
				detail += " · รอ " + humanMS(*s.DurationMS)
			}
		} else if st == "waiting" {
			detail = "รอคำตอบของคุณ"
		}
		add(s.Name, label, st, detail)
	}
	if s, ok := latest["compute"]; ok {
		add("compute", "คำนวณตัวเลขด้วยสคริปต์ (ไม่ใช้โมเดล)", status(s.Status), "")
	} else {
		add("compute", "คำนวณตัวเลขด้วยสคริปต์ (ไม่ใช้โมเดล)", "pending", "")
	}
	if s, ok := latest["postcompute"]; ok {
		detail := ""
		if c, ok := latest["compute"]; ok {
			var o struct {
				Summary map[string]any `json:"summary"`
			}
			if json.Unmarshal(c.Output, &o) == nil && o.Summary != nil {
				detail = fmt.Sprintf("พบ %d รายการ", numberOf(o.Summary["exceptions"]))
			}
		}
		add("postcompute", "ตรวจกฎหารายการผิดปกติ", status(s.Status), detail)
	} else {
		add("postcompute", "ตรวจกฎหารายการผิดปกติ", "pending", "")
	}
	if s, ok := latest["narrative"]; ok {
		var o narrativeOut
		_ = json.Unmarshal(s.Output, &o)
		detail := ""
		if o.Model != "" {
			detail = o.Model
			if o.SelfHosted {
				detail += " ในองค์กร"
			}
		} else if s.Status == "succeeded" {
			detail = "เขียนจากกฎของ agent"
		}
		add("narrative", "เขียนคำอธิบาย", status(s.Status), detail)
	} else {
		add("narrative", "เขียนคำอธิบาย", "pending", "")
	}
	if s, ok := latest["render"]; ok {
		detail := ""
		var o renderOut
		if json.Unmarshal(s.Output, &o) == nil {
			var th struct {
				Name string `json:"name"`
			}
			_ = json.Unmarshal(o.Theme, &th)
			if th.Name != "" {
				detail = "ธีม “" + th.Name + "”"
			}
		}
		add("render", "สร้างไฟล์ผลลัพธ์", status(s.Status), detail)
		q := qualitySummary(o)
		if s.Status == "failed" {
			// AM5: which line failed is said here; why is said by the round's
			// message (in words) and by the run page (in full).
			add("quality", "ตรวจคุณภาพเอกสาร", "failed", "")
		} else if q != "" {
			add("quality", "ตรวจคุณภาพเอกสาร", status(s.Status), q)
		}
	} else {
		add("render", "สร้างไฟล์ผลลัพธ์", "pending", "")
	}
	if run.Status == am.RunFailed {
		for i := range p.Steps {
			if p.Steps[i].Status == "running" {
				p.Steps[i].Status = "failed"
			}
		}
	}
	return p
}

func qualitySummary(o renderOut) string {
	pass, total := 0, 0
	for _, a := range o.Artifacts {
		var q struct {
			Checks []struct {
				OK bool `json:"ok"`
			} `json:"checks"`
		}
		_ = json.Unmarshal(a.Quality, &q)
		for _, c := range q.Checks {
			total++
			if c.OK {
				pass++
			}
		}
	}
	if total == 0 {
		return ""
	}
	return fmt.Sprintf("ผ่าน %d จาก %d ข้อ", pass, total)
}

func humanMS(ms int64) string {
	d := time.Duration(ms) * time.Millisecond
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%d วินาที", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%d นาที", int(d.Minutes()))
	case d < 48*time.Hour:
		return fmt.Sprintf("%d ชั่วโมง", int(d.Hours()))
	}
	return fmt.Sprintf("%d วัน", int(d.Hours()/24))
}
