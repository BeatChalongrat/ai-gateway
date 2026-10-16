package agentmarket

// The English of the sentences AM22 added to the control plane: a value one slot takes given to two slots of a
// customer's form (template.go), and a form the round had nothing for (roundform.go). One entry per line, sorted
// by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"ไม่ได้กรอก %s ในรอบนี้ — %s แบบฟอร์มนี้ยังใช้กับรอบอื่นได้": "%s was not filled in this run — %s The form can still be used for other runs",
		"” รับค่าเดียวกัน (“": "” take the same value (“",
		"” และช่อง “":         "” and field “",
		"”) ซึ่งใส่ได้ช่องเดียว เลือกช่องที่ถูกต้องเพียงช่องเดียว": "”), which only one field takes; keep the one field that is right",
	})
}
