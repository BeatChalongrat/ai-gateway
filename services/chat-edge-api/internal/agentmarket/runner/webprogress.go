package runner

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── The progress card of a round on the web (AM12) ───────────────────────
//
// A round that works from a request reads no file: its card says what it is
// doing instead — reading the request, searching, opening pages, checking
// what was read against the stored pages, making the files. Every line is
// built from the step rows, like every other progress card.

func webSteps(steps []am.Step) bool {
	for _, s := range steps {
		if strings.HasPrefix(s.Name, "web.") {
			return true
		}
	}
	return false
}

var requestAgents sync.Map // install id → the agent works from a request

// fromRequest: the run's agent works from what a person types (so its card
// is the web's from the first moment, before any step exists).
func (r *Runner) fromRequest(ctx context.Context, run am.Run) bool {
	if v, ok := requestAgents.Load(run.InstallID); ok {
		return v.(bool)
	}
	in, err := r.Store.InstallByID(ctx, run.InstallID)
	if err != nil || r.Engine == nil {
		return false
	}
	p := r.Engine.Packages[in.Slug]
	yes := p != nil && p.Manifest.FromRequest()
	requestAgents.Store(uuid.UUID(run.InstallID), yes)
	return yes
}

func buildWebProgress(run am.Run, steps []am.Step, now time.Time) am.Progress {
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
		}
		return "pending"
	}
	add := func(key string, label am.PersonText, st string, detail am.PersonText) {
		p.Steps = append(p.Steps, am.ProgressStep{Key: key, Label: label, Status: st, Detail: detail})
	}
	none := am.NoText
	latest := map[string]am.Step{}
	var clar []am.Step
	searches, searchRunning := 0, false
	captured, notOpened, fetching := 0, 0, false
	read, reading := 0, false
	for _, s := range steps {
		switch {
		case strings.HasPrefix(s.Name, "clarify:"):
			clar = append(clar, s)
		case strings.HasPrefix(s.Name, "web.search:"):
			if s.Status == "succeeded" {
				searches++
			} else if s.Status == "running" {
				searchRunning = true
			}
		case strings.HasPrefix(s.Name, "web.fetch:"):
			var o struct {
				Outcome string `json:"outcome"`
			}
			_ = json.Unmarshal(s.Output, &o)
			switch {
			case s.Status == "running":
				fetching = true
			case o.Outcome == "captured":
				captured++
			case s.Status == "succeeded":
				notOpened++
			}
		case strings.HasPrefix(s.Name, "web.read:"):
			if s.Status == "succeeded" {
				read++
			} else if s.Status == "running" {
				reading = true
			}
		default:
			latest[s.Name] = s
		}
	}
	of := func(name string) string {
		if s, ok := latest[name]; ok {
			return status(s.Status)
		}
		return "pending"
	}
	add("web.brief", am.Text("อ่านคำขอ"), of("web.brief"), none)
	for _, s := range clar {
		if strings.Contains(s.Name, "postcompute") {
			continue
		}
		st := status(s.Status)
		detail := none
		if st == "done" {
			detail = am.Text("ได้คำตอบแล้ว")
		} else if st == "waiting" {
			detail = am.Text("รอคำตอบของคุณ")
		}
		add(s.Name, am.Text("ถามสิ่งที่ต้องรู้ก่อนค้นหา"), st, detail)
	}
	// searching, opening, reading: counted, never named (a host's name is in the table and on the run's page)
	st := "pending"
	detail := none
	switch {
	case searchRunning || of("web.plan") == "running":
		st = "running"
	case searches > 0:
		st, detail = "done", am.Text("ค้นหา %s ครั้ง", am.Count(searches))
	case of("web.select") != "pending" || captured+notOpened > 0:
		st = "done"
	}
	add("web.search", am.Text("ค้นหาผู้ขาย"), st, detail)
	st, detail = "pending", none
	switch {
	case fetching || reading || of("web.select") == "running":
		st = "running"
		if captured+notOpened > 0 {
			detail = am.Text("บันทึกแล้ว %s หน้า", am.Count(captured))
		}
	case captured+notOpened > 0:
		st = "done"
		detail = am.Text("บันทึก %s หน้า", am.Count(captured))
		if notOpened > 0 {
			detail = am.Text("บันทึก %s หน้า · ไม่ได้เปิด %s หน้า (มีเหตุผลในตาราง)", am.Count(captured), am.Count(notOpened))
		}
	case of("compute") != "pending":
		st = "done"
	}
	add("web.fetch", am.Text("เปิดหน้าเว็บและบันทึกไว้เป็นหลักฐาน"), st, detail)
	detail = none
	if c, ok := latest["compute"]; ok && c.Status == "succeeded" {
		var o struct {
			Summary map[string]any `json:"summary"`
		}
		if json.Unmarshal(c.Output, &o) == nil && o.Summary != nil {
			detail = am.Text("ได้ %s ตัวเลือก", am.Count(int(numberOf(o.Summary["offers"]))))
		}
	}
	add("compute", am.Text("ตรวจทุกค่ากับหน้าที่บันทึกไว้ และคำนวณยอด"), of("compute"), detail)
	for _, s := range clar {
		if !strings.Contains(s.Name, "postcompute") {
			continue
		}
		st := status(s.Status)
		detail := none
		if st == "done" {
			detail = am.Text("เลือกแล้ว")
		} else if st == "waiting" {
			detail = am.Text("รอคุณเลือก")
		}
		add(s.Name, am.Text("ให้คุณเลือกตัวเลือก"), st, detail)
	}
	if s, ok := latest["render"]; ok {
		var o renderOut
		_ = json.Unmarshal(s.Output, &o)
		add("render", am.Text("สร้างไฟล์ผลลัพธ์"), status(s.Status), none)
		q := qualitySummary(o)
		if s.Status == "failed" {
			add("quality", am.Text("ตรวจคุณภาพเอกสาร"), "failed", none)
		} else if !q.Empty() {
			add("quality", am.Text("ตรวจคุณภาพเอกสาร"), status(s.Status), q)
		}
	} else {
		add("render", am.Text("สร้างไฟล์ผลลัพธ์"), "pending", none)
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
