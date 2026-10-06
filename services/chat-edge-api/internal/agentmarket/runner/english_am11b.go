package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

// The English of the sentences AM11b added to the runner for an agent that does what it is asked (jobs.go).
// One entry per line, sorted by the Thai sentence.

func init() {
	am.AddEnglish(map[string]string{
		"ได้ครับ กำลังทำไฟล์ใหม่ตามที่ขอ จากตัวเลขชุดเดิมของรอบนี้ ไม่คำนวณใหม่": "Certainly. Making the files again as asked, from this run's same figures — nothing is recalculated.",
	})
}
