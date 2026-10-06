package agentmarket

// The English of the sentences AM11b added to the control plane for an agent that does what it is asked
// (instructions.go, and what its data sheet says a model is shown: catalog.go). One entry per line, sorted by the
// Thai sentence.

func init() {
	AddEnglish(map[string]string{
		" · และข้อความที่คุณพิมพ์มากับไฟล์ หรือพิมพ์ขอเรื่องรูปแบบเอกสาร": " · and what you type with the files, or type to ask something of the documents",
		"กฎภาษีและเกณฑ์ที่ใช้คำนวณเปลี่ยนด้วยคำขอในแชทไม่ได้":             "tax rules and the thresholds used in the calculation cannot be changed by a request in the chat",
		"การตรวจทำทุกรอบ ข้ามหรือปิดไม่ได้":                               "the checks are made on every run and cannot be skipped or turned off",
		"ข้ามหรือปิดการตรวจ": "skipping a check or turning one off",
		"ตัวเลขทุกตัวมาจากการคำนวณจากไฟล์ที่ส่งมา agent ไม่เปลี่ยนตัวเลขตามคำขอ": "every figure is computed from the files sent, and the agent does not change a figure because it is asked to",
		"ทำตามที่ขอแล้ว: %s": "Done as asked: %s",
		"มีคำขอที่ทำตามไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: %s":                                                                           "One request cannot be done, because what can be asked of this agent's documents is only: %s",
		"อ่านข้อความที่พิมพ์มาไม่สำเร็จในรอบนี้ เอกสารจึงเป็นแบบปกติ — ถ้ามีคำขอเรื่องรูปแบบเอกสาร พิมพ์อีกครั้งได้เลย จะทำไฟล์ใหม่จากตัวเลขชุดเดิม": "The words typed with this run could not be read, so the documents are the usual ones. If you asked something of the documents, type it again: the files will be made again from the same figures.",
		"อ่านคำขอที่พิมพ์มากับรอบนี้ไม่สำเร็จ":                                                                                                       "The words typed with this run could not be read",
		"เฉพาะข้อความที่คุณพิมพ์: ที่พิมพ์มากับไฟล์หรือพิมพ์ขอเรื่องรูปแบบเอกสาร และที่พิมพ์ถามเกี่ยวกับตัวเลขหรือพิมพ์ตอบการ์ดคำถามเป็นประโยค (กับตารางหรือการ์ดของรอบนั้น) — ส่งให้โมเดลในองค์กรเท่านั้น": "Only what you type: words typed with the files or to ask something of the documents, and a question about the figures or a question card answered in a sentence (with that run's table or card) — sent only to a model inside the organisation",
		"เปลี่ยนกฎภาษีหรือเกณฑ์ที่ใช้คำนวณ":                                 "changing a tax rule or a threshold used in the calculation",
		"เอกสารของรอบนี้ทำแบบนั้นไม่ได้":                                    "this run's documents cannot be made that way",
		"แก้หรือกำหนดตัวเลขเอง":                                             "changing a figure or setting one by hand",
		"“%s” ทำตามที่ขอไม่ได้เพราะ agent นี้ปรับเอกสารตามคำขอได้เฉพาะ: %s": "“%s” cannot be done as asked because what can be asked of this agent's documents is only: %s",
		"“%s” ทำตามที่ขอไม่ได้เพราะ%s":                                      "“%s” cannot be done as asked because %s",
	})
}
