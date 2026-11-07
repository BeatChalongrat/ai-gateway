package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

// The English of the sentences AM22 added to the runner: the customer's form the round had nothing for
// (deliver.go). One entry per line, sorted by the Thai sentence.

func init() {
	am.AddEnglish(map[string]string{
		"%s · ไม่ได้กรอกแบบฟอร์มของคุณในรอบนี้: %s": "%s · Your form was not filled in this run: %s",
		"%s คำอธิบายในรอบนี้เขียนจากกฎ เพราะคำตอบของโมเดลใช้ไม่ได้ ตัวเลขทุกตัวยังมาจากสคริปต์เหมือนเดิม": "%s The commentary in this run was written from rules, because the model's answers could not be used. Every figure still comes from the scripts, as before.",
	})
}
