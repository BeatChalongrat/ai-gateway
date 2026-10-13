package agentmarket

// The English of the sentences AM18 (part B) added to the control plane: the language of the documents
// asked for, what is kept as the install's own way, and a request that could mean two things
// (instructions_am18.go). One entry per line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"agent นี้ทำเอกสารได้เป็น%sเท่านั้น":                         "this agent makes its documents in %s only",
		"agent นี้ทำเอกสารได้เป็น%sเท่านั้น และไม่ให้โมเดลแปลเอกสาร": "this agent makes its documents in %s only, and never has a model translate them",
		"ขอไว้ในแชต — ล้างได้ที่นี่":                                 "Asked for in the chat — clear it here",
		"คำขอ":         "Request",
		"คำขอที่จำไว้": "Standing requests",
		"จำไว้เป็นค่าตั้งต้นของ agent นี้ต่อไป: %s — ดูหรือล้างได้ในหน้าตั้งค่าของ agent": "Kept as this agent's own way from now on: %s — see or clear it on the agent's settings page",
		"ทำตามที่ขอไว้ก่อนหน้า: %s — ล้างได้ในหน้าตั้งค่าของ agent":                       "Made as asked earlier: %s — clear it on the agent's settings page",
		"ทำเอกสารเป็นภาษาอังกฤษ":                      "Make the documents in English",
		"ทำเอกสารเป็นภาษาอื่น":                        "Make the documents in another language",
		"ทำเอกสารเป็นภาษาไทย":                         "Make the documents in Thai",
		"ที่ขอมาทำได้มากกว่าหนึ่งแบบ จะเอาแบบไหนครับ": "What was asked can be done in more than one way — which one?",
		"ภาษาอังกฤษ":                                  "English",
		"ภาษาไทย":                                     "Thai",
		"ภาษาไทยและภาษาอังกฤษ":                        "Thai and English",
		"ส่วน %s ใช้กับรอบนี้เท่านั้น":                "%s is for this round only",
		"เอกสารของรอบนี้เป็น%sอยู่แล้ว":               "This round's documents are already in %s",
		"เอกสารรอบนี้ทำเป็น%sตามที่ขอ":                "This round's documents are in %s, as asked",
		"ไฟล์ของรอบที่เสร็จแล้วคงเป็นภาษาเดิม กำลังเริ่มรอบใหม่จากไฟล์ชุดเดิมเป็น%sตามที่ขอ": "A finished round's files keep their language. A new round is starting from the same files, in %s as asked",
		"“%s” ทำได้มากกว่าหนึ่งแบบ จะเอาแบบไหนครับ":                                          "“%s” can be done in more than one way — which one?",
	})
}
