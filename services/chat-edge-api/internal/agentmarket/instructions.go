package agentmarket

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/google/uuid"
)

// ── What a person may ask of an agent's documents (AM11b) ────────────────
//
// An agent took files and gave back the documents its package makes; words
// typed with the files ("ไม่ต้องใส่กราฟ", "เรียงจากรายการที่ต่างมากที่สุด") changed
// nothing, and nothing said so. Now a package says in its manifest
// ("instructions") what a person can ask of its documents — each a
// directive its own renderer applies — and what they are known to ask that
// it cannot do, with the reason.
//
// A model reads the person's words into that list and into nothing else
// (runner/asked.go): it answers with ids. It writes no figure, changes no
// rule, and applies nothing — the package's code applies a directive when
// it renders, and its gate opens the finished file and reads back that it
// was done. What the model's answer may hold is checked here (ReadAsked):
// an id the package does not declare is not one, and words the person did
// not type are not theirs.
//
// Some things no agent does because it is asked to — give a figure another
// value, skip a check, compute by another rule. Those are the platform's to
// say (NeverAsked), the same for every agent.

// InstructionSpec is a manifest's "instructions".
type InstructionSpec struct {
	Can    []Directive `json:"can"`
	Cannot []Directive `json:"cannot"`
}

// Directive is one thing a person may ask (Can) or is known to ask (Cannot).
type Directive struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	// Note (can): what the documents are like with it — said back to the person.
	Note string `json:"note,omitempty"`
	// Asks (can): how people ask for it, in the package's words — shown to the model that reads a
	// person's words, beside the label. It is how a package says that a request it can only go part of
	// the way on ("one page") is this directive; the note then tells the person how far it went.
	Asks string `json:"asks,omitempty"`
	// Unless (can): why it was not done, when the round's own data cannot take it.
	Unless string `json:"unless,omitempty"`
	// Why (cannot): the reason.
	Why string `json:"why,omitempty"`
	// Group (can): directives of one group cannot hold together — two orders of the same table. The
	// documents are made with one of a group at a time: the one asked last (Merge), and of two asked in
	// the same words, the first the package lists (ReadAsked says so of the other).
	Group string `json:"group,omitempty"`
}

// TakesInstructions: the agent's package declares something a person can ask of its documents.
func (m Manifest) TakesInstructions() bool {
	return m.Instructions != nil && len(m.Instructions.Can) > 0
}

// AskedOther is the id a model answers with for a request about the documents that is in neither list.
const AskedOther = "other"

// Never is one thing no agent does because a person asks it to: the id a
// model answers with, what it is, and why not.
type Never struct {
	ID         string
	Label, Why PersonText
}

// NeverAsked: what no agent does because it is asked to. (A function: a
// sentence's English is looked up when the sentence is made, and the
// catalogue is filled by init — after a package variable would be.)
func NeverAsked() []Never {
	return []Never{
		{"change_figures", Text("แก้หรือกำหนดตัวเลขเอง"), Text("ตัวเลขทุกตัวมาจากการคำนวณจากไฟล์ที่ส่งมา agent ไม่เปลี่ยนตัวเลขตามคำขอ")},
		{"skip_checks", Text("ข้ามหรือปิดการตรวจ"), Text("การตรวจทำทุกรอบ ข้ามหรือปิดไม่ได้")},
		{"change_rules", Text("เปลี่ยนกฎภาษีหรือเกณฑ์ที่ใช้คำนวณ"), Text("กฎภาษีและเกณฑ์ที่ใช้คำนวณเปลี่ยนด้วยคำขอในแชทไม่ได้")},
	}
}

// Asked is what a model read out of a person's words, after ReadAsked: the
// directives to apply, and what was asked that cannot be done.
type Asked struct {
	Text   string        `json:"text,omitempty"`
	Apply  []string      `json:"apply"`
	Cannot []AskedCannot `json:"cannot,omitempty"`
	Model  string        `json:"model,omitempty"`
	// Unread: no model answered — the words were read by nothing.
	Unread bool `json:"unread,omitempty"`
	// Instead: what was asked together with another directive of its group, and gave way to it.
	Instead []AskedInstead `json:"instead,omitempty"`
}

// AskedInstead: one directive that was asked and is not applied, because Kept — of the same group — was
// asked in the same words.
type AskedInstead struct {
	ID   string `json:"id"`
	Kept string `json:"kept"`
}

// AskedCannot: one request that cannot be done — the id of the reason, and the
// person's own words for it (empty when the model's quote was not theirs).
type AskedCannot struct {
	ID    string `json:"id"`
	Words string `json:"words,omitempty"`
}

// Nothing: the words asked nothing of the documents (and were read).
func (a Asked) Nothing() bool {
	return len(a.Apply) == 0 && len(a.Cannot) == 0 && len(a.Instead) == 0 && !a.Unread
}

// OfARequest is a as it stands for an agent whose round starts from the words themselves (AM12): what
// the words ask that is in neither of the package's lists is the request — the thing to find, how many,
// within what — and the agent reads that itself. It is not a request about the documents that was
// refused, so it is not kept as one. What the package lists as not possible is still said.
func (a Asked) OfARequest() Asked {
	var kept []AskedCannot
	for _, c := range a.Cannot {
		if c.ID != AskedOther {
			kept = append(kept, c)
		}
	}
	a.Cannot = kept
	return a
}

// askedSystem is the system message of the question. It holds no JSON: the
// platform's echo guard takes out of an answer whatever repeats the system
// message (AM12), so the shape of the answer is asked for in the user turn.
const askedSystem = `คุณอ่านข้อความที่ผู้ใช้พิมพ์ถึง agent ที่ทำเอกสารจากไฟล์ แล้วบอกว่าในข้อความนั้นมีคำขอเกี่ยวกับรูปแบบหรือเนื้อหาของเอกสารข้อใดบ้าง
คุณเลือกจากรายการที่ให้ไว้เท่านั้น ไม่คิดข้อใหม่ ไม่ทำเอกสารเอง ไม่คำนวณ และไม่ตอบผู้ใช้

กติกา
- ข้อความที่บอกเพียงว่าไฟล์คืออะไร ของเดือนหรือบริษัทไหน คำทักทาย คำขอบคุณ หรือคำสั่งให้เริ่มทำงาน ไม่ใช่คำขอเกี่ยวกับเอกสาร ไม่ต้องใส่อะไร
- คำขอที่ตรงกับข้อในรายการ “ทำได้” ให้ใส่ id ของข้อนั้นใน apply
- คำขอที่ตรงกับข้อในรายการ “ทำไม่ได้” ให้ใส่ใน cannot พร้อม id ของข้อนั้น และ words เป็นคำของผู้ใช้ส่วนที่ขอเรื่องนั้น คัดลอกตามที่พิมพ์มา
- คำขอเกี่ยวกับเอกสารที่ไม่ตรงกับข้อใดเลยในสองรายการ ให้ใส่ใน cannot ด้วย id other และ words เป็นคำของผู้ใช้ส่วนนั้น
- คำขอที่ไปในทางเดียวกับข้อใน “ทำได้” แม้ข้อนั้นจะทำได้ไม่ครบตามที่ขอ ให้เลือกข้อนั้น ระบบจะบอกผู้ใช้เองว่าทำได้แค่ไหน
- ถ้าไม่แน่ใจว่าคำขอเป็นเรื่องเดียวกับข้อใน “ทำได้” หรือไม่ ให้ถือว่าไม่ตรง
- ผู้ใช้อาจพิมพ์ภาษาไทยหรือภาษาอังกฤษ

ตอบเป็น JSON object เดียว ไม่มีข้อความอื่น`

// AskedPrompt is the question a model is asked about a person's words: the
// system message and the user turn (the two lists, the words, the shape).
//
// request: the words are also what the agent is asked to find (an agent that starts from a request).
// The model is told so in the user turn, and whatever it still answers as "other" is dropped by
// Asked.OfARequest — the telling is a help, not the guard.
func AskedPrompt(spec InstructionSpec, text string, request ...bool) (system, user string) {
	var b strings.Builder
	if len(request) > 0 && request[0] {
		b.WriteString("agent ตัวนี้เริ่มงานจากข้อความนี้เอง: สิ่งที่ผู้ใช้ต้องการให้หา จำนวน งบประมาณ คุณสมบัติ ยี่ห้อ สถานที่ และกำหนดส่ง " +
			"เป็นเรื่องของการค้นหา ไม่ใช่คำขอเกี่ยวกับเอกสาร ไม่ต้องใส่ทั้งใน apply และ cannot\n\n")
	}
	b.WriteString("ทำได้:\n")
	for _, d := range spec.Can {
		b.WriteString("- " + d.ID + ": " + d.Label)
		switch {
		case d.Asks != "":
			b.WriteString(" (คนมักขอว่า: " + d.Asks + ")")
		case d.Note != "":
			b.WriteString(" — " + d.Note)
		}
		b.WriteString("\n")
	}
	b.WriteString("\nทำไม่ได้:\n")
	for _, d := range spec.Cannot {
		b.WriteString("- " + d.ID + ": " + d.Label + "\n")
	}
	for _, n := range NeverAsked() {
		b.WriteString("- " + n.ID + ": " + n.Label.In(Thai) + "\n")
	}
	b.WriteString("\nข้อความของผู้ใช้:\n<<<\n" + strings.TrimSpace(text) + "\n>>>\n\n")
	b.WriteString(`รูปแบบคำตอบ: {"apply": ["id"], "cannot": [{"id": "id", "words": "คำของผู้ใช้"}]} — ไม่มีข้อใดก็ให้เป็น [] ทั้งสองช่อง`)
	return askedSystem, b.String()
}

// ReadAsked reads a model's answer to AskedPrompt and keeps only what it may
// hold: directives the package declares, each once and in the package's
// order; requests that cannot be done under an id of the two "cannot" lists
// or "other"; and for each of those the person's words only when they are
// words of the message (a quote the model made up is dropped, not shown).
func ReadAsked(spec InstructionSpec, text string, raw json.RawMessage) Asked {
	out := Asked{Text: strings.TrimSpace(text), Apply: []string{}}
	var ans struct {
		Apply  []string `json:"apply"`
		Cannot []struct {
			ID    string `json:"id"`
			Words string `json:"words"`
		} `json:"cannot"`
	}
	if json.Unmarshal(raw, &ans) != nil {
		out.Unread = true
		return out
	}
	picked := map[string]bool{}
	for _, id := range ans.Apply {
		picked[strings.TrimSpace(id)] = true
	}
	held := map[string]string{} // group → the directive of it that is applied
	for _, d := range spec.Can {
		if !picked[d.ID] {
			continue
		}
		if kept, taken := held[d.Group]; d.Group != "" && taken {
			out.Instead = append(out.Instead, AskedInstead{ID: d.ID, Kept: kept})
			continue
		}
		if d.Group != "" {
			held[d.Group] = d.ID
		}
		out.Apply = append(out.Apply, d.ID)
	}
	known := map[string]bool{AskedOther: true}
	for _, d := range spec.Cannot {
		known[d.ID] = true
	}
	for _, n := range NeverAsked() {
		known[n.ID] = true
	}
	typed := squashSpace(text)
	seen := map[string]bool{}
	for _, c := range ans.Cannot {
		id := strings.TrimSpace(c.ID)
		if !known[id] || contains(out.Apply, id) {
			continue
		}
		words := strings.TrimSpace(c.Words)
		if words == "" || len([]rune(words)) > 160 || !strings.Contains(typed, squashSpace(words)) {
			words = ""
		}
		if seen[id+"\x00"+words] {
			continue
		}
		seen[id+"\x00"+words] = true
		out.Cannot = append(out.Cannot, AskedCannot{ID: id, Words: words})
	}
	return out
}

// squashSpace is s with no space in it and in lower case: a model that copies a person's words often
// sets them as print does ("สั้น ๆ" for "สั้นๆ"), and they are still the person's words.
func squashSpace(s string) string { return strings.Join(strings.Fields(strings.ToLower(s)), "") }

func (spec InstructionSpec) can(id string) (Directive, bool) {
	for _, d := range spec.Can {
		if d.ID == id {
			return d, true
		}
	}
	return Directive{}, false
}

// Merge is what a round's documents are made with after more is asked of them: what was in force,
// less whatever shares a group with something asked now (a table that was by amount and is now asked by
// date is by date), then what is asked now.
func (spec InstructionSpec) Merge(inForce, asked []string) []string {
	taken := map[string]bool{}
	for _, id := range asked {
		if d, ok := spec.can(id); ok && d.Group != "" {
			taken[d.Group] = true
		}
	}
	out := []string{}
	add := func(id string) {
		for _, x := range out {
			if x == id {
				return
			}
		}
		out = append(out, id)
	}
	for _, id := range inForce {
		if d, ok := spec.can(id); ok && d.Group != "" && taken[d.Group] {
			continue
		}
		add(id)
	}
	for _, id := range asked {
		add(id)
	}
	return out
}

// AskedOneAtATime says, for each directive that gave way to another of its group asked in the same
// words, that it was not done and which one was.
func AskedOneAtATime(spec InstructionSpec, instead []AskedInstead) []PersonText {
	var out []PersonText
	for _, x := range instead {
		d, ok := spec.can(x.ID)
		k, ok2 := spec.can(x.Kept)
		if !ok || !ok2 {
			continue
		}
		out = append(out, Text("“%s” ทำตามที่ขอไม่ได้เพราะขอ “%s” มาด้วย และทำได้ทีละอย่าง เอกสารรอบนี้ทำตามอย่างหลัง ขออีกอย่างได้ในข้อความถัดไป",
			Words(LabelFromPackage(d.Label, NoText)), Words(LabelFromPackage(k.Label, NoText))))
	}
	return out
}

// AskedDone says what was done as asked: each directive by its label and what
// the documents are like with it, in the package's words.
func AskedDone(spec InstructionSpec, applied []string) PersonText {
	var parts []PersonText
	for _, id := range applied {
		d, ok := spec.can(id)
		if !ok {
			continue
		}
		label := LabelFromPackage(d.Label, NoText)
		if d.Note == "" {
			parts = append(parts, label)
			continue
		}
		parts = append(parts, Join(" — ", label, FromPackage(d.Note, NoText)))
	}
	if len(parts) == 0 {
		return NoText
	}
	return Text("ทำตามที่ขอแล้ว: %s", Words(Join(" · ", parts...)))
}

// AskedNotDone says, for each request that was not done, that it was not and
// why — "ทำตามที่ขอไม่ได้เพราะ…". refused are the directives the renderer could
// not apply to this round's data (the package's "unless"); cannot are what
// the model read as asked and not possible.
func AskedNotDone(spec InstructionSpec, refused []string, cannot []AskedCannot) []PersonText {
	var out []PersonText
	for _, id := range refused {
		d, ok := spec.can(id)
		if !ok {
			continue
		}
		why := Text("เอกสารของรอบนี้ทำแบบนั้นไม่ได้")
		if d.Unless != "" {
			why = FromPackage(d.Unless, why)
		}
		out = append(out, Text("“%s” ทำตามที่ขอไม่ได้เพราะ%s", Words(LabelFromPackage(d.Label, NoText)), Words(why)))
	}
	var labels []PersonText
	for _, d := range spec.Can {
		labels = append(labels, LabelFromPackage(d.Label, NoText))
	}
	for _, c := range cannot {
		what, why := NoText, NoText
		for _, d := range spec.Cannot {
			if d.ID == c.ID {
				what, why = LabelFromPackage(d.Label, NoText), FromPackage(d.Why, NoText)
			}
		}
		for _, n := range NeverAsked() {
			if n.ID == c.ID {
				what, why = n.Label, n.Why
			}
		}
		asked := Words(what)
		if c.Words != "" {
			asked = Typed(c.Words, 160)
		}
		if c.ID == AskedOther || why.EmptyIn(Thai) {
			if c.Words == "" {
				out = append(out, Text("มีคำขอที่ทำตามไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: %s", Words(Join(" · ", labels...))))
				continue
			}
			out = append(out, Text("“%s” ทำตามที่ขอไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: %s", asked, Words(Join(" · ", labels...))))
			continue
		}
		out = append(out, Text("“%s” ทำตามที่ขอไม่ได้เพราะ%s", asked, Words(why)))
	}
	return out
}

// AskedUnread: no model could read the words typed with the round.
func AskedUnread() PersonText {
	return Text("อ่านข้อความที่พิมพ์มาไม่สำเร็จในรอบนี้ เอกสารจึงเป็นแบบปกติ — ถ้ามีคำขอเรื่องรูปแบบเอกสาร พิมพ์อีกครั้งได้เลย จะทำไฟล์ใหม่จากตัวเลขชุดเดิม")
}

// InstructionsInForce: the directives a round's documents are made with now —
// those of its latest finished re-render that carried any, else those read
// out of the words the round started with. A file made later for the same
// round (another format, a PDF) is made the same way.
func (s *Store) InstructionsInForce(ctx context.Context, runID uuid.UUID) []string {
	var raw json.RawMessage
	err := s.Pool.QueryRow(ctx, `SELECT COALESCE(
		(SELECT r.params->'instructions' FROM agent_market.agent_runs r
		  WHERE r.parent_run_id = $1 AND r.kind = 'render' AND r.status = 'succeeded' AND r.params ? 'instructions'
		  ORDER BY r.created_at DESC LIMIT 1),
		(SELECT st.output->'apply' FROM agent_market.agent_run_steps st
		  WHERE st.run_id = $1 AND st.name = 'instructions' AND st.status = 'succeeded' ORDER BY st.seq DESC LIMIT 1),
		'[]'::jsonb)`, runID).Scan(&raw)
	if err != nil {
		return nil
	}
	var ids []string
	_ = json.Unmarshal(raw, &ids)
	return ids
}
