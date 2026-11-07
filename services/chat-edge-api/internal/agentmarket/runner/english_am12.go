package runner

import am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"

// The English of the sentences AM12 added to the runner (a round on the web: webprogress.go, clarify.go).
// One entry per line, sorted by the Thai sentence.

func init() {
	am.AddEnglish(map[string]string{
		"%s ทุกค่าในตารางตรวจซ้ำกับหน้าเว็บที่บันทึกไว้ด้วยโปรแกรม ยอดรวมคำนวณด้วยสคริปต์ โมเดลเป็นผู้อ่านหน้าเว็บ ไม่ได้เป็นผู้กำหนดตัวเลขใด · ยังไม่มีร่างเอกสาร เพราะยังไม่ได้เลือกตัวเลือก · %s": "%s Every value in the table was checked again against the saved web pages by program, and the totals were worked out by script. A model read the web pages; it set no figure. · There is no draft, because no candidate was chosen. · %s",
		"%s ทุกค่าในตารางและในร่างตรวจซ้ำกับหน้าเว็บที่บันทึกไว้ด้วยโปรแกรม ยอดรวมคำนวณด้วยสคริปต์ โมเดลเป็นผู้อ่านหน้าเว็บ ไม่ได้เป็นผู้กำหนดตัวเลขใด · %s":                                         "%s Every value in the table and in the draft was checked again against the saved web pages by program, and the totals were worked out by script. A model read the web pages; it set no figure. · %s",
		"ค้นหา %s ครั้ง": "%s searches",
		"ค้นหาผู้ขาย":    "Searching for sellers",
		"ค้นหาและตรวจกับหน้าเว็บที่บันทึกไว้เสร็จแล้วครับ เลือกได้จากตารางด้านล่าง":                                                                                                                          "The search is done and every value has been checked against the saved web pages. Choose from the table below.",
		"ค้นหาและเปิดหน้าเว็บแล้วครับ แต่ยังไม่พบตัวเลือกที่นำมาเทียบได้ เหตุผลของแต่ละแหล่งอยู่ในไฟล์ที่ส่งให้ ลองพิมพ์คำขอให้เจาะจงขึ้น หรือวางลิงก์ของหน้าที่ต้องการให้อ่านมาในข้อความ · %s":              "The search is done and web pages were opened, but no candidate that can be compared was found. The reason for each source is in the file delivered. Try a more specific request, or paste the link of a page you want read into your message. · %s",
		"ค้นหาแล้วครับ แต่ยังไม่พบตัวเลือกที่นำมาเทียบได้ ผลการค้นหาไม่มีหน้าใดที่ตรงกับคำขอพอจะเปิดอ่าน จึงไม่ได้เปิดหน้าใด ลองพิมพ์คำขอให้เจาะจงขึ้น หรือวางลิงก์ของหน้าที่ต้องการให้อ่านมาในข้อความ · %s": "The search is done, but no candidate that can be compared was found. No search result matched the request well enough to be opened, so no page was opened. Try a more specific request, or paste the link of a page you want read into your message. · %s",
		"ตรวจทุกค่ากับหน้าที่บันทึกไว้ และคำนวณยอด": "Checking every value against the saved pages and working out the totals",
		"ถามสิ่งที่ต้องรู้ก่อนค้นหา":                "Asking what has to be known before searching",
		"บันทึก %s หน้า": "%s pages saved",
		"บันทึก %s หน้า · ไม่ได้เปิด %s หน้า (มีเหตุผลในตาราง)": "%s pages saved · %s not opened (the reasons are in the table)",
		"บันทึกแล้ว %s หน้า":                       "%s pages saved so far",
		"มี %s เรื่องที่ต้องรู้ก่อนเริ่มค้นหาครับ": "There are %s things to know before the search starts.",
		"ยังไม่ได้ส่งอะไรให้ผู้ขายหรือให้ใคร":      "Nothing has been sent to a seller or to anyone.",
		"รอคุณเลือก": "Waiting for you to choose",
		"อ่านคำขอ":   "Reading the request",
		"เปิดหน้าเว็บและบันทึกไว้เป็นหลักฐาน": "Opening web pages and saving them as evidence",
		"เลือกแล้ว":           "Chosen",
		"ให้คุณเลือกตัวเลือก": "Your choice of candidate",
		"ได้ %s ตัวเลือก":     "%s candidates",
	})
}
