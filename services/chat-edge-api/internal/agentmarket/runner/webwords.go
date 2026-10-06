package runner

import (
	"context"
	"strings"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// ── What a round on the web says when its files are out (AM12) ───────────
//
// A round on the web can end three ways, and "done, every value in the table
// and in the draft was checked" is true of one of them only. A round that
// found nothing to compare says so, says why as far as it knows, and says
// what the person can do next; a round whose person chose none says there is
// no draft. Nothing here names a kind of goods: the words are a web round's.

const webSentNothing = "ยังไม่ได้ส่งอะไรให้ผู้ขายหรือให้ใคร"

// webClosing is the round's last message. done is what the round would say
// first ("เสร็จแล้วครับ", with the form's unfilled fields when it has any);
// summary is the package's own count of what the round came to.
func webClosing(done am.PersonText, summary map[string]any) am.PersonText {
	n := func(key string) int {
		v, _ := summary[key].(float64)
		return int(v)
	}
	_, counted := summary["offers"]
	switch {
	case !counted:
		// a package that does not count what it found: say what was checked, and no more
	case n("offers") == 0 && n("sources") == 0:
		return am.Text("ค้นหาแล้วครับ แต่ยังไม่พบตัวเลือกที่นำมาเทียบได้ ผลการค้นหาไม่มีหน้าใดที่ตรงกับคำขอพอจะเปิดอ่าน จึงไม่ได้เปิดหน้าใด ลองพิมพ์คำขอให้เจาะจงขึ้น หรือวางลิงก์ของหน้าที่ต้องการให้อ่านมาในข้อความ · %s",
			am.Words(am.Text(webSentNothing)))
	case n("offers") == 0:
		return am.Text("ค้นหาและเปิดหน้าเว็บแล้วครับ แต่ยังไม่พบตัวเลือกที่นำมาเทียบได้ เหตุผลของแต่ละแหล่งอยู่ในไฟล์ที่ส่งให้ ลองพิมพ์คำขอให้เจาะจงขึ้น หรือวางลิงก์ของหน้าที่ต้องการให้อ่านมาในข้อความ · %s",
			am.Words(am.Text(webSentNothing)))
	case n("chosen") == 0:
		return am.Text("%s ทุกค่าในตารางตรวจซ้ำกับหน้าเว็บที่บันทึกไว้ด้วยโปรแกรม ยอดรวมคำนวณด้วยสคริปต์ โมเดลเป็นผู้อ่านหน้าเว็บ ไม่ได้เป็นผู้กำหนดตัวเลขใด · ยังไม่มีร่างเอกสาร เพราะยังไม่ได้เลือกตัวเลือก · %s",
			am.Words(done), am.Words(am.Text(webSentNothing)))
	}
	return am.Text("%s ทุกค่าในตารางและในร่างตรวจซ้ำกับหน้าเว็บที่บันทึกไว้ด้วยโปรแกรม ยอดรวมคำนวณด้วยสคริปต์ โมเดลเป็นผู้อ่านหน้าเว็บ ไม่ได้เป็นผู้กำหนดตัวเลขใด · %s",
		am.Words(done), am.Words(am.Text(webSentNothing)))
}

// webModels are the models that answered in this round's steps on the web —
// the request's reading, the search plan, the choice of pages, each page's
// reading — in the order they first answered. The files card says which
// model read the request and the pages from this, never from the narrative
// (a round on the web has none: its documents are written by rules).
func (rc *runCtx) webModels(ctx context.Context, run am.Run) []am.OutputsWebModel {
	steps, err := rc.r.Store.ListSteps(ctx, run.ID)
	if err != nil {
		return nil
	}
	out := []am.OutputsWebModel{}
	seen := map[string]bool{}
	for _, s := range steps {
		if s.Kind != "ai" || !strings.HasPrefix(s.Name, "web.") || s.ModelID == "" || seen[s.ModelID] {
			continue
		}
		seen[s.ModelID] = true
		out = append(out, am.OutputsWebModel{Model: s.ModelID, SelfHosted: rc.r.Model != nil && rc.r.Model.IsSelfHosted(s.ModelID)})
	}
	return out
}
