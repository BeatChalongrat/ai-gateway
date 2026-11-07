package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

// The English of the sentences AM23 added to the runner: a round's last message, which names no model (deliver.go),
// a workbook printed to PDF (pdf.go), a file the round's content cannot be (plan_am23.go), and a file converted on the way in
// (convert_am23.go).
// One entry per line, sorted by the Thai sentence.

func init() {
	am.AddEnglish(map[string]string{
		"%s · ผู้ช่วย AI ขององค์กร (%s) อ่านซ้ำ %s ฉบับที่เป็นสแกนหรือรูปถ่าย เพื่อเทียบกับ OCR เท่านั้น":                                           "%s · The organisation's AI assistant (%s) re-read %s scanned or photographed documents, only to compare with OCR",
		"%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายร่างโดยผู้ช่วย AI ขององค์กร ที่รันในองค์กร":                                                            "%s Every figure comes from scripts. The commentary was drafted by the organisation's AI assistant, which runs inside the organisation.",
		"%s ตัวเลขทุกตัวมาจากสคริปต์ คำอธิบายร่างโดยผู้ช่วย AI ขององค์กร ผ่านผู้ให้บริการภายนอก ซึ่งเห็นเฉพาะตารางที่คำนวณแล้ว":                     "%s Every figure comes from scripts. The commentary was drafted by the organisation's AI assistant through an outside provider, which saw only the calculated tables.",
		"pdf: แปลงเป็น PDF (ไม่มีเอกสารที่ PDF นี้พิมพ์ในรอบนี้)":                                                                                   "pdf: converting to PDF (the document this PDF prints was not made in this run)",
		"ช่วยกดเลือกจากปุ่มในการ์ดด้านบนได้ไหมครับ คำถามนี้ไม่มีคำตอบให้เลือกแทน":                                                                   "Could you tap one of the buttons on the card above? This question has no answer it can pick for you",
		"ตัวเลข %s ตัว อยู่ใน PDF ตามที่พิมพ์":                                                                                                      "%s figures, in the PDF as printed",
		"ตัวเลขใน PDF ตรงกับที่ไฟล์ Excel พิมพ์":                                                                                                    "The figures in the PDF are the ones the workbook prints",
		"ตัวแปลงเอกสารเปิดไฟล์นี้ไม่ได้ ไฟล์อาจเสียหายหรือตั้งรหัสผ่านไว้":                                                                          "the document converter could not open it; it may be damaged or protected by a password",
		"สำเนาที่แปลงไม่มีข้อมูลเลย ไฟล์อาจเสียหาย":                                                                                                 "the converted copy holds no data at all; the file may be damaged",
		"สำเนาที่แปลงไม่ใช่ตารางที่อ่านได้ ตัวแปลงอ่านเนื้อไฟล์เป็นตัวอักษรที่ถอดไม่ได้ ไฟล์อาจเสียหาย":                                             "the converted copy is not a table that can be read: the converter read the file as characters it could not decode; the file may be damaged",
		"เนื้อไฟล์ไม่ใช่ไฟล์ %s จริง":                                                                                                               "its content is not a %s file",
		"ไฟล์ .xls รับได้ไม่เกิน 65,536 แถว ไม่มีแถวหัวตารางที่ตรึงซ้ำทุกหน้า และไม่มีพื้นที่พิมพ์ ซึ่งกระดาษทำการต้องใช้ จึงทำเป็น .xlsx ให้แทน":   "A .xls file holds at most 65,536 rows, with no header row frozen on every page and no print area, which the working paper depends on, so it is made as .xlsx instead",
		"ไฟล์ “%s” อ่านไม่ได้: แปลงเป็น %s ไม่สำเร็จ — %s จึงไม่ได้ใช้ไฟล์นี้ในรอบนี้ ส่งไฟล์ที่บันทึกเป็น %s มาแทนได้":                             "“%s” could not be read: converting it to %s failed — %s. It was not used in this run; send it saved as %s instead",
		"ไฟล์ “%s” เป็นไฟล์ %s รุ่นเก่า จึงแปลงเป็น %s ก่อนอ่าน ตัวเลขอ่านจากสำเนาที่แปลง ส่วนที่มาที่อ้างถึงคือไฟล์ต้นฉบับและ SHA-256 ของไฟล์นั้น": "“%s” is an older %s file, so it was converted to %s before it was read. The figures were read from the converted copy; the source cited is the original file and its SHA-256",
		"ไม่พบใน PDF: %s": "Not in the PDF: %s",
	})
}
