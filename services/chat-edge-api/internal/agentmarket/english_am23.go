package agentmarket

// The English of the sentences AM23 added to the control plane: a kind of file an agent does not make, refused by
// name with what it makes (outputset.go), and the round that stops before any work for it (failure.go). One entry
// per line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"agent ตัวนี้ไม่ได้ทำ %s — ทำได้: %s":                                 "This agent does not make %s. It makes: %s",
		"agent ตัวนี้ไม่ได้ทำไฟล์ชนิดที่รอบนี้ขอ จึงยังไม่ได้เริ่มทำงาน":      "This agent does not make the kind of file this run asked for, so it has not started",
		"บอกบนเอกสารว่าผู้ช่วย AI ขององค์กรร่างคำอธิบาย":                      "Say on documents that the organisation's AI assistant drafted the commentary",
		"เลือกชุดไฟล์ที่ agent ทำได้ในหน้าตั้งค่าของ agent แล้วส่งไฟล์มาใหม่": "Choose a set of files the agent makes on the agent's settings page, then send the files again",
	})
}
