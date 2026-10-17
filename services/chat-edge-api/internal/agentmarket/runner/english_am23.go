package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

// The English of the sentences AM23 added to the runner: a round's last message, which names no model (deliver.go).
// One entry per line, sorted by the Thai sentence.

func init() {
	am.AddEnglish(map[string]string{
		"%s · ผู้ช่วย AI ขององค์กร (%s) อ่านซ้ำ %s ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น":                       "%s · The organisation's AI assistant (%s) re-read %s scanned or photographed documents, only to compare with OCR",
		"%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายร่างโดยผู้ช่วย AI ขององค์กร ที่รันในองค์กร":                                        "%s Every figure comes from scripts. The commentary was drafted by the organisation's AI assistant, which runs inside the organisation.",
		"%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายร่างโดยผู้ช่วย AI ขององค์กร ผ่านผู้ให้บริการภายนอก ซึ่งเห็นเฉพาะตารางที่คำนวณแล้ว": "%s Every figure comes from scripts. The commentary was drafted by the organisation's AI assistant through an outside provider, which saw only the calculated tables.",
	})
}
