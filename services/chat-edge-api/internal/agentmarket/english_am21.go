package agentmarket

// The English of the sentences AM21 added to the control plane: the organisation's look — a theme, its
// name on every document, its brand colours — and what is refused there (orglook.go). One entry per
// line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"ชื่อองค์กรบนเอกสารต้องไม่มีรหัสงานหรือรหัสรุ่น เช่น %s":                                      "The organisation's name on documents may not carry a build or card marker such as %s",
		"ชื่อองค์กรบนเอกสารต้องไม่มีรหัสงานหรือรหัสรุ่น เช่น %s — เอกสารนี้ผู้ตรวจสอบของลูกค้าจะอ่าน": "The organisation's name on documents may not carry a build or card marker such as %s — a customer's auditor reads these documents",
		"ชื่อองค์กรบนเอกสารต้องไม่เกิน %s ตัวอักษร":                                                   "The organisation's name on documents is at most %s characters",
		"ธีมต้องเป็นหนึ่งในธีมที่มี: กรมท่า เขียวหยก ถ่านหิน":                                         "The theme must be one of those there are: navy, teal, charcoal",
		"สี %s %s มีคอนทราสต์ %s กับพื้นขาว ต่ำกว่า %s ที่อ่านได้ จึงรับไม่ได้":                       "The %s colour %s has a contrast of %s against white, under the %s a reader needs, so it is refused",
		"สี %s ต้องเป็นรหัสฐานสิบหก 6 หลัก เช่น 12264A":                                               "The %s colour must be six hex digits, such as 12264A",
	})
}
