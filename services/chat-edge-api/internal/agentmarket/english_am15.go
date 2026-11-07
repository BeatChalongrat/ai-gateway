package agentmarket

// The English of the sentences AM15 added to the control plane: a run that waits for its model, a run
// that goes on when the model is back, and a question only a model could answer (modeldown.go). One
// entry per line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"ตอนนี้ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้ จึงยังตอบคำถามนี้ไม่ได้ครับ ลองถามอีกครั้งภายหลัง — ตัวเลขและที่มาดูได้ในไฟล์ของรอบนี้ และในหน้า “ขั้นตอนและที่มาของตัวเลข”":                                 "The model this agent uses cannot be reached right now, so this question cannot be answered yet. Please ask again later. The figures and where they come from are in this run's files and on the “Steps and source of figures” page.",
		"ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้หลายครั้งติดกัน รอบนี้จึงหยุดไว้โดยยังไม่ได้ทำไฟล์":                                                                                                                 "The model this agent uses could not be reached several times in a row, so this run stopped before any file was made.",
		"ติดต่อโมเดลได้แล้ว รอบนี้ทำต่อจากขั้นที่ค้างไว้ครับ":                                                                                                                                                 "The model can be reached again. This run continues from the step it stopped at.",
		"หยุดรอไว้ก่อนครับ: ตอนนี้ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้ จึงยังไม่ทำไฟล์ออกมา — เมื่อติดต่อโมเดลได้ รอบนี้จะทำต่อจากขั้นที่ค้างไว้เอง ไม่ต้องส่งไฟล์หรือพิมพ์ใหม่ ไฟล์และคำตอบที่ให้ไว้ยังอยู่ครบ": "Waiting: the model this agent uses cannot be reached right now, so no file has been made yet. When the model can be reached, this run continues by itself from the step it stopped at — there is no need to send the files or type anything again. Your files and the answers you gave are all still here.",
	})
}
