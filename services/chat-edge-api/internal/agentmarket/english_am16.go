package agentmarket

// The English of the sentences AM16 added to the control plane: the masking switch beside the leave
// switch (egress.go, models.go) and what is said when an answer's question left as typed (thread.go).
// One entry per line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"คำถามนี้และตัวเลขของรอบที่ถามถึง ถูกส่งให้โมเดลนอกองค์กรโดยไม่ได้ปิดบังข้อมูล":                                                                     "This question, and the figures of the run it asks about, were sent to a model outside the organisation and were not masked",
		"ปิดบังข้อมูลส่วนบุคคลก่อนส่งให้โมเดล":                                                                                                              "Personal data is masked before it is sent to a model",
		"ผู้ดูแลตั้งให้ปิดบังข้อมูลของ agent นี้ก่อนส่งให้โมเดล ภาพของหน้าเอกสารปิดบังไม่ได้ จึงให้โมเดลในองค์กรอ่านเท่านั้น":                               "An admin has set this agent's data to be masked before a model sees it. The image of a page cannot be masked, so only a model in the organisation reads it",
		"องค์กรอนุญาตให้ส่งเอกสารไปโมเดลนอกองค์กรได้ แต่ agent นี้ยังไม่ได้เปิดให้ส่งออก ซึ่งเป็นค่าตั้งต้น — ผู้ดูแลเปิดให้ agent นี้ได้ที่แอปผู้ดูแลระบบ": "The organisation allows documents to go to a model outside it, but this agent has not been allowed to send them, which is the default — an admin can allow it for this agent in the admin app",
	})
}
