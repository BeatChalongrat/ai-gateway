// FIX48 · C2 — the question set for the intelligence evaluation.
//
// WRITTEN FRESH for this round. None of these appear in the corpora that were
// used to tune the system: scripts/probes/router-compare/eval.js (FIX35),
// the FIX28 dlp-eval corpus, or the FIX37/38/40/46 Thai-NER corpora. Checked
// by grep before writing; the overlap is zero questions.
//
// Shares: 26/40 Thai = 65% (the brief asks for >= 60%).
//
// `kind` groups the question; `checks` are OBJECTIVE facts fixed here, BEFORE
// any run — they are scored mechanically and never re-tuned after seeing an
// answer. The LLM judges score the subjective axes separately (see judge.js);
// the two scores are reported apart, never blended.

const Q = [
  // ── thai_general — ความรู้/เหตุผลภาษาไทย (8) ────────────────────────────
  { id: "tg-1", lang: "th", kind: "thai_general",
    q: "อธิบายความต่างระหว่าง 'ภาษีมูลค่าเพิ่ม' กับ 'ภาษีหัก ณ ที่จ่าย' ให้เจ้าของร้านเล็ก ๆ ที่ไม่มีบัญชีเข้าใจ ภายใน 5 บรรทัด",
    checks: [["พูดถึง VAT 7%", (r) => /7\s*%|ร้อยละ\s*7/.test(r)],
             ["แยกสองอย่างออกจากกัน", (r) => /หัก ?ณ ?ที่จ่าย/.test(r) && /มูลค่าเพิ่ม|VAT/i.test(r)]] },
  { id: "tg-2", lang: "th", kind: "thai_general",
    q: "ทำไมคนไทยเรียกเดือนที่มี 30 วันลงท้ายด้วย 'ยน' และเดือนที่มี 31 วันลงท้ายด้วย 'คม' ตอบสั้น ๆ แล้วยกตัวอย่าง 2 เดือน",
    checks: [["อ้างระบบราศี/สันสกฤต", (r) => /ราศี|สันสกฤต|โหราศาสตร์|จักรราศี/.test(r)],
             ["ยกตัวอย่างเดือนถูกอย่างน้อยหนึ่ง", (r) => /เมษายน|มิถุนายน|กันยายน|พฤศจิกายน|มกราคม|มีนาคม|พฤษภาคม|กรกฎาคม|สิงหาคม|ตุลาคม|ธันวาคม/.test(r)]] },
  { id: "tg-3", lang: "th", kind: "thai_general",
    q: "เขียนอีเมลปฏิเสธข้อเสนอราคาจากซัพพลายเออร์ที่เราทำงานด้วยมา 3 ปี โดยไม่ปิดประตูสำหรับรอบหน้า ความยาวไม่เกิน 120 คำ",
    checks: [["เป็นอีเมลจริง (มีคำลงท้าย)", (r) => /ขอบคุณ|ขอแสดงความนับถือ|ด้วยความเคารพ/.test(r)],
             ["ไม่ปิดประตู", (r) => /โอกาส|รอบหน้า|ครั้งต่อไป|อนาคต|ยินดี/.test(r)]] },
  { id: "tg-4", lang: "th", kind: "thai_general",
    q: "'น้ำขึ้นให้รีบตัก' กับ 'ตำน้ำพริกละลายแม่น้ำ' ใช้ต่างกันยังไง ยกสถานการณ์ในที่ทำงานมาอย่างละหนึ่ง",
    checks: [["จับความหมายแรกได้", (r) => /โอกาส|จังหวะ|รีบ/.test(r)],
             ["จับความหมายที่สองได้", (r) => /สูญเปล่า|เสียเปล่า|ไม่คุ้ม|ไร้ประโยชน์|เปล่าประโยชน์/.test(r)]] },
  { id: "tg-5", lang: "th", kind: "thai_general",
    q: "ร้านกาแฟเปิด 07:00-18:00 ทุกวัน ยกเว้นวันจันทร์ปิด ถ้าวันนี้เป็นวันเสาร์ที่ 3 และลูกค้าถามว่า 'อีกกี่วันจะถึงวันที่ร้านปิด' ตอบว่ากี่วัน และอธิบายวิธีคิด",
    checks: [["ตอบ 2 วัน", (r) => /\b2\b|สอง/.test(r)],
             ["อ้างวันจันทร์", (r) => /จันทร์/.test(r)]] },
  { id: "tg-6", lang: "th", kind: "thai_general",
    q: "สรุปให้ทีมพัฒนาเข้าใจว่า PDPA ต่างจาก GDPR ตรงไหนบ้าง 3 ข้อ ที่กระทบการเขียนโค้ดจริง ๆ",
    checks: [["พูดถึง PDPA และ GDPR ทั้งคู่", (r) => /PDPA/i.test(r) && /GDPR/i.test(r)],
             ["มีอย่างน้อย 3 ข้อ", (r) => (r.match(/^\s*(?:[-*•]|\d[.)])/gm) || []).length >= 3]] },
  { id: "tg-7", lang: "th", kind: "thai_general",
    q: "แปลประโยคนี้เป็นอังกฤษให้เป็นทางการพอที่จะใส่ในสัญญา: 'ผู้ว่าจ้างมีสิทธิบอกเลิกสัญญาได้ทันทีหากผู้รับจ้างส่งมอบงานล่าช้าเกินสามสิบวันนับแต่วันครบกำหนด'",
    checks: [["แปลเป็นอังกฤษจริง", (r) => (r.match(/[a-zA-Z]/g) || []).length > 80],
             ["มี thirty (30) days", (r) => /thirty|30/i.test(r)],
             ["มี terminate", (r) => /terminat/i.test(r)]] },
  { id: "tg-8", lang: "th", kind: "thai_general",
    q: "อธิบายให้เด็ก ม.3 เข้าใจว่า 'ดอกเบี้ยทบต้น' ทำงานยังไง โดยใช้ตัวอย่างเงิน 1,000 บาท ดอกเบี้ย 10% ต่อปี 3 ปี และบอกตัวเลขสุดท้าย",
    checks: [["ได้ 1331", (r) => /1[,.]?331/.test(r)],
             ["แสดงขั้นปีต่อปี", (r) => /1[,.]?100/.test(r) && /1[,.]?210/.test(r)]] },

  // ── thai_practical — งานจริงในที่ทำงาน (6) ─────────────────────────────
  { id: "tp-1", lang: "th", kind: "thai_practical",
    q: "เขียน SQL (PostgreSQL) หายอดขายรวมต่อเดือนของปี 2568 จากตาราง orders(id, created_at, amount_satang) เดือนที่ไม่มีออร์เดอร์ต้องขึ้นเป็น 0 ด้วย ตอบเป็นโค้ดอย่างเดียว",
    checks: [["ใช้ generate_series หรือ calendar join", (r) => /generate_series/i.test(r)],
             ["มี left join หรือ coalesce", (r) => /left\s+join|coalesce/i.test(r)],
             ["รู้ว่า 2568 คือ 2025", (r) => /2025/.test(r)]] },
  { id: "tp-2", lang: "th", kind: "thai_practical",
    q: "เขียนฟังก์ชัน Python ชื่อ thai_baht_text(n) แปลงจำนวนเงินเป็นข้อความไทย เช่น 1050.25 -> 'หนึ่งพันห้าสิบบาทยี่สิบห้าสตางค์' ตอบเป็นโค้ดอย่างเดียว ไม่ต้องอธิบาย",
    checks: [["นิยามฟังก์ชันถูกชื่อ", (r) => /def thai_baht_text/.test(r)],
             ["จัดการสตางค์", (r) => /สตางค์/.test(r)],
             ["จัดการ 'เอ็ด' หรือ 'ยี่'", (r) => /เอ็ด|ยี่สิบ/.test(r)]] },
  { id: "tp-3", lang: "th", kind: "thai_practical",
    q: "ทีมมี 5 คน ต้องส่งงาน 3 สัปดาห์ แต่ประเมินแล้วต้องใช้ 4.5 สัปดาห์ เขียนตัวเลือกให้ผู้บริหารเลือก 3 ทาง พร้อมสิ่งที่ต้องแลก อย่าเลือกให้เขา",
    checks: [["มี 3 ทางเลือก", (r) => (r.match(/^\s*(?:[-*•]|\d[.)]|ทางเลือก|ตัวเลือก)/gm) || []).length >= 3],
             ["พูดถึงสิ่งที่ต้องแลก", (r) => /แลก|ต้องเสีย|ข้อเสีย|ความเสี่ยง|ตัดออก/.test(r)]] },
  { id: "tp-4", lang: "th", kind: "thai_practical",
    q: "ระบบล่มตอนตี 2 กู้คืนได้ตอนตี 3 ครึ่ง เขียน incident report ภาษาไทยความยาว 1 หน้า สำหรับลูกค้าองค์กร โดยยังไม่รู้สาเหตุที่แท้จริง",
    checks: [["ระบุช่วงเวลา", (r) => /02:00|2:00|ตี ?2|03:30|3:30|ตี ?3/.test(r)],
             ["ไม่เดาสาเหตุ", (r) => /อยู่ระหว่าง|ยังไม่ทราบ|กำลังตรวจสอบ|ยังไม่สรุป|รอผล/.test(r)],
             ["ระบุระยะเวลา 1.5 ชม.", (r) => /1\.5|หนึ่งชั่วโมงครึ่ง|90 ?นาที|1 ชั่วโมง 30/.test(r)]] },
  { id: "tp-5", lang: "th", kind: "thai_practical",
    q: "รีวิวโค้ดนี้แล้วบอกบั๊กที่ร้ายแรงที่สุดข้อเดียว พร้อมโค้ดที่แก้แล้ว:\n```go\nfunc Top(n int, xs []int) []int {\n    sort.Ints(xs)\n    return xs[len(xs)-n:]\n}\n```",
    checks: [["ชี้ว่า mutate input หรือ n เกินขนาด", (r) => /mutat|แก้ไข.*input|เปลี่ยน.*slice ?เดิม|n\s*>\s*len|out of range|panic|ยาวเกิน/i.test(r)],
             ["มีโค้ดที่แก้แล้ว", (r) => /func Top/.test(r)]] },
  { id: "tp-6", lang: "th", kind: "thai_practical",
    q: "ลูกค้าเขียนมาว่า 'ระบบช้ามาก ใช้ไม่ได้เลย' ตอบกลับยังไงให้ได้ข้อมูลที่ต้องใช้ debug จริง ๆ โดยไม่ทำให้เขารู้สึกว่าโดนโยนกลับ",
    checks: [["ขอข้อมูลที่ระบุได้", (r) => /เวลา|หน้าไหน|เมื่อไหร่|ขั้นตอน|เบราว์เซอร์|screenshot|ภาพหน้าจอ|URL/i.test(r)],
             ["ยอมรับก่อน", (r) => /ขออภัย|เสียใจ|เข้าใจ|ขอโทษ/.test(r)]] },

  // ── pii — มีข้อมูลส่วนตัวปน วัดว่า DLP ทำให้คำตอบแย่ลงไหม (6) ───────────
  { id: "pii-1", lang: "th", kind: "pii",
    q: "ช่วยร่างข้อความแจ้งลูกค้าชื่อ คุณสมชาย ใจดี เบอร์ 081-234-5678 ว่าพัสดุถึงพรุ่งนี้ ให้เรียกชื่อเขาในข้อความด้วย",
    checks: [["เรียกชื่อลูกค้าได้", (r) => /สมชาย/.test(r)],
             ["ไม่มี placeholder โผล่ในคำตอบ", (r) => !/<<[A-Z_]+/.test(r)]] },
  { id: "pii-2", lang: "th", kind: "pii",
    q: "ตรวจสอบว่าเลขบัตรประชาชน 1101700207101 มี checksum ถูกต้องไหม แสดงวิธีคิดทีละขั้น",
    // Ground truth, computed BEFORE the run: sum(d_i x (14-i)) for i=1..12
    // = 141; 141 mod 11 = 9; (11-9) mod 10 = 2; the printed 13th digit is 1,
    // so this number is INVALID.
    checks: [["แสดงการคำนวณ", (r) => /\d+\s*[x×*]\s*\d+|ผลรวม|sum/i.test(r)],
             ["สรุปว่าไม่ถูกต้อง", (r) => /ไม่ถูกต้อง|ไม่ผ่าน|ผิด|invalid|not valid/i.test(r)]] },
  { id: "pii-3", lang: "th", kind: "pii",
    q: "จัดรูปแบบที่อยู่นี้ให้เป็นบรรทัดเดียวสำหรับพิมพ์ใบปะหน้า: บ้านเลขที่ 88/12 ซอยลาดพร้าว 71 แขวงคลองเจ้าคุณสิงห์ เขตวังทองหลาง กรุงเทพมหานคร 10310",
    checks: [["คงเลขที่บ้าน", (r) => /88\/12/.test(r)],
             ["คงรหัสไปรษณีย์", (r) => /10310/.test(r)],
             ["ไม่มี placeholder", (r) => !/<<[A-Z_]+/.test(r)]] },
  { id: "pii-4", lang: "th", kind: "pii",
    q: "อีเมลนี้ผิดตรงไหน แก้ให้สุภาพขึ้น: 'ถึงคุณวิภา ที่ vipa.k@example.co.th — ยังไม่ได้เงินงวด 2 นะ รีบโอนด้วย'",
    checks: [["ยังพูดถึงผู้รับได้", (r) => /วิภา|คุณ/.test(r)],
             ["ให้ฉบับแก้แล้ว", (r) => r.length > 120],
             ["ไม่มี placeholder", (r) => !/<<[A-Z_]+/.test(r)]] },
  { id: "pii-5", lang: "en", kind: "pii",
    q: "Our engineer Nattapong Srisuk (nattapong@example.com, +66 81 555 0199) is on leave. Draft a 3-line handover note to the team naming who covers what.",
    checks: [["names the engineer", (r) => /Nattapong/i.test(r)],
             ["no placeholder leaked", (r) => !/<<[A-Z_]+/.test(r)]] },
  { id: "pii-6", lang: "th", kind: "pii",
    q: "ลูกค้าถามว่า ทอง ขึ้นราคาอีกไหมปีหน้า ตอบตามหลักการลงทุนทั่วไป ไม่ต้องฟันธง",
    checks: [["ยังตอบเรื่องทองคำ", (r) => /ทอง|ทองคำ|gold/i.test(r)],
             ["ไม่ฟันธง", (r) => /ไม่สามารถ|ขึ้นอยู่กับ|คาดเดา|ไม่แน่นอน|ปัจจัย/.test(r)],
             ["ไม่มี placeholder", (r) => !/<<[A-Z_]+/.test(r)]] },

  // ── unknowable — ควรตอบว่าไม่รู้ (5) ───────────────────────────────────
  { id: "uk-1", lang: "th", kind: "unknowable",
    q: "ยอดขายไตรมาส 3 ปีนี้ของบริษัทเราเป็นเท่าไหร่",
    checks: [["ยอมรับว่าไม่มีข้อมูล", (r) => /ไม่มีข้อมูล|ไม่ทราบ|ไม่สามารถเข้าถึง|ไม่รู้|ต้องขอ|ไม่ได้รับ/.test(r)],
             ["ไม่ยกตัวเลขขึ้นมาลอย ๆ", (r) => !/\d{1,3}(,\d{3}){1,}\s*(บาท|ล้าน)/.test(r)]] },
  { id: "uk-2", lang: "th", kind: "unknowable",
    q: "พรุ่งนี้ราคาหุ้น PTT จะปิดที่เท่าไหร่ ตอบเป็นตัวเลข",
    checks: [["ปฏิเสธการทำนาย", (r) => /ไม่สามารถ|ทำนายไม่ได้|ไม่มีใคร|คาดเดาไม่ได้|ไม่ทราบ/.test(r)]] },
  { id: "uk-3", lang: "th", kind: "unknowable",
    q: "ในเอกสารที่ผมแนบมาเมื่อกี้ หน้า 4 เขียนว่าอะไร",
    checks: [["บอกว่าไม่เห็นเอกสาร", (r) => /ไม่พบ|ไม่เห็น|ไม่ได้รับ|ยังไม่ได้แนบ|ไม่มีเอกสาร|ไม่มีไฟล์/.test(r)],
             ["ไม่แต่งเนื้อหาหน้า 4", (r) => !/หน้า ?4 ?(เขียนว่า|ระบุว่า|มีเนื้อหา) ?[""']/.test(r)]] },
  { id: "uk-4", lang: "en", kind: "unknowable",
    q: "What did the CEO of our company say in the all-hands meeting last Thursday?",
    checks: [["admits no access", (r) => /don't have|do not have|no access|cannot|can't|unable|no record|not aware/i.test(r)]] },
  { id: "uk-5", lang: "th", kind: "unknowable",
    q: "กฎหมายไทยฉบับที่ชื่อ 'พระราชบัญญัติปัญญาประดิษฐ์แห่งชาติ พ.ศ. 2569' มาตรา 12 ว่าอย่างไร",
    checks: [["ไม่ยืนยันว่ามีจริง", (r) => /ไม่พบ|ไม่มีข้อมูล|ยังไม่มี|ไม่ทราบ|ไม่แน่ใจ|ไม่สามารถยืนยัน|ยังไม่ได้ประกาศ/.test(r)],
             ["ไม่แต่งตัวบทมาตรา 12", (r) => !/มาตรา ?12 ?(บัญญัติ|ระบุ|กำหนด)ว่า/.test(r)]] },

  // ── tool — ต้องเรียกเครื่องมือถึงจะตอบดี (5) ────────────────────────────
  { id: "tl-1", lang: "th", kind: "tool",
    q: "คำนวณให้แม่นยำ: 987654321 × 123456789 เท่ากับเท่าไหร่ ตอบเลขเต็มบรรทัดสุดท้าย",
    checks: [["ได้ผลลัพธ์ถูก", (r) => /121932631112635269/.test(r.replace(/[,\s]/g, ""))]] },
  { id: "tl-2", lang: "th", kind: "tool",
    q: "ถ้าฝากเงิน 250,000 บาท ดอกเบี้ย 3.25% ต่อปี ทบต้นทุกเดือน เป็นเวลา 7 ปี จะได้เงินเท่าไหร่ ตอบทศนิยม 2 ตำแหน่ง",
    checks: [["อยู่ในช่วง 313,000-314,500", (r) => {
      const nums = (r.replace(/,/g, "").match(/\d{6}\.\d{2}|\d{6}/g) || []).map(Number);
      return nums.some((n) => n > 313000 && n < 314500);
    }]] },
  { id: "tl-3", lang: "en", kind: "tool",
    q: "How many times does the letter 'r' appear in the word 'strawberrystrawberry'? Answer with the number only on the last line.",
    checks: [["answers 6", (r) => /\b6\b/.test(r.trim().split("\n").slice(-2).join(" "))]] },
  { id: "tl-4", lang: "th", kind: "tool",
    q: "เรียงตัวเลขนี้จากน้อยไปมากแล้วบอกค่ามัธยฐาน: 47, 3, 891, 22, 156, 8, 74, 405, 19, 63, 230, 91",
    checks: [["มัธยฐาน 68.5", (r) => /68\.5/.test(r)],
             ["เรียงถูกต้นแถว", (r) => /3,? ?8,? ?19/.test(r.replace(/\s+/g, " "))]] },
  { id: "tl-5", lang: "th", kind: "tool",
    q: "วันที่ 15 มีนาคม 2567 เป็นวันอะไรในสัปดาห์ และห่างจากวันที่ 1 มกราคม 2567 กี่วัน",
    checks: [["ตอบวันศุกร์", (r) => /ศุกร์|Friday/i.test(r)],
             ["ตอบ 74 วัน", (r) => /\b74\b/.test(r)]] },

  // ── english_control (3) ────────────────────────────────────────────────
  { id: "en-1", lang: "en", kind: "english_control",
    q: "A rope burns unevenly and takes exactly 60 minutes to burn end to end. With two such ropes and a lighter, measure 45 minutes. Explain the method in under 80 words.",
    checks: [["burns one rope from both ends", (r) => /both ends/i.test(r)],
             ["mentions 45", (r) => /45/.test(r)]] },
  { id: "en-2", lang: "en", kind: "english_control",
    q: "Write a Python function `merge_intervals(xs)` that merges overlapping [start, end] intervals. Code only, no prose.",
    checks: [["defines the function", (r) => /def merge_intervals/.test(r)],
             ["sorts first", (r) => /\.sort\(|sorted\(/.test(r)]] },
  { id: "en-3", lang: "en", kind: "english_control",
    q: "Our API returns 200 with an empty body about 1 in 50 calls. Give me the five most likely causes, ranked, one line each.",
    checks: [["five items", (r) => (r.match(/^\s*(?:[-*•]|\d[.)])/gm) || []).length >= 5]] },
];

// ── long-context threads (3 threads x 16 turns) ───────────────────────────
// The FACT is planted at turn 2 and the question comes at turn 16, so the
// answer depends on whatever survived compaction. Turns 3..15 are filler that
// is long enough to push a small budget over its threshold but is never itself
// the subject of the question.
const FILLER_TH = [
  "ช่วยสรุปข้อดีข้อเสียของการทำงานแบบ remote ให้หน่อย",
  "แล้ว hybrid ล่ะ ต่างจาก remote ยังไง",
  "ถ้าทีมอยู่คนละ time zone ควรจัดประชุมยังไง",
  "เขียน agenda ประชุมทีม 30 นาทีให้หน่อย",
  "ทำไมประชุมยืนถึงสั้นกว่าประชุมนั่ง",
  "สรุปวิธีเขียน meeting note ที่คนไม่ได้เข้าประชุมอ่านรู้เรื่อง",
  "ช่วยแนะนำวิธีลดจำนวนประชุมในทีม",
  "async communication ทำยังไงให้ไม่ตกหล่น",
  "เขียน template สำหรับ daily standup แบบข้อความ",
  "ถ้าคนในทีมเงียบไม่ตอบ ควรทำยังไง",
  "วัด productivity ของทีม remote ยังไงให้ไม่กลายเป็นจับผิด",
  "แนะนำวิธี onboard คนใหม่แบบ remote",
  "ทำ team building แบบ remote ที่ไม่ฝืน ทำยังไง",
  "สรุปสามข้อว่าอะไรทำให้ทีม remote ล้มเหลวบ่อยที่สุด",
];

const THREADS = [
  { id: "lc-1", lang: "th",
    plant: "ก่อนอื่นจำไว้ก่อนนะ: รหัสโครงการของเราคือ PRJ-7742 เจ้าของงบคือฝ่ายการตลาด และเดดไลน์คือ 14 พฤศจิกายน",
    filler: FILLER_TH,
    finalQ: "กลับมาเรื่องโครงการ — รหัสโครงการ เจ้าของงบ และเดดไลน์ ที่บอกไว้ตอนต้น คืออะไรบ้าง",
    checks: [["จำรหัสได้", (r) => /PRJ-?7742/i.test(r)],
             ["จำเจ้าของงบได้", (r) => /การตลาด|marketing/i.test(r)],
             ["จำเดดไลน์ได้", (r) => /14\s*พ(\.|ฤศจิกายน)|Nov(ember)?\s*14|14\/11/i.test(r)]] },
  { id: "lc-2", lang: "th",
    plant: "จำไว้ด้วย: เซิร์ฟเวอร์ที่ห้ามแตะคือ db-prod-03 และคนที่ต้องขออนุญาตก่อนคือคุณอรทัย",
    filler: FILLER_TH,
    finalQ: "ถ้าผมจะรีสตาร์ตเซิร์ฟเวอร์ ผมต้องระวังเครื่องไหน และต้องขอใคร",
    checks: [["จำชื่อเครื่องได้", (r) => /db-?prod-?03/i.test(r)],
             ["จำชื่อคนได้", (r) => /อรทัย/.test(r)]] },
  { id: "lc-3", lang: "en",
    plant: "Remember this for later: our staging API key rotates every 45 days, the on-call rota is Somchai then Pim then Krit, and the incident channel is #ops-red.",
    filler: FILLER_TH,
    finalQ: "Back to operations — how often does the staging key rotate, who is second in the on-call rota, and which channel do incidents go to?",
    checks: [["rotation period", (r) => /45/.test(r)],
             ["second on call", (r) => /Pim/i.test(r)],
             ["channel", (r) => /#?ops-red/i.test(r)]] },
];

// ── memory questions (asked in a NEW conversation after a planting turn) ──
const MEMORY = [
  { id: "mem-1", lang: "th",
    plant: "จำไว้นะ ผมแพ้อาหารทะเล และผมทำงานเป็นสถาปนิกอยู่เชียงใหม่",
    ask: "แนะนำร้านอาหารสำหรับเลี้ยงลูกค้ามื้อเย็นให้หน่อย 3 ร้าน พร้อมเหตุผลสั้น ๆ",
    checks: [["เลี่ยงอาหารทะเล", (r) => !/ซีฟู้ด|seafood|กุ้ง|ปู|หอย|ปลาหมึก/i.test(r) || /แพ้|เลี่ยง|ไม่มีอาหารทะเล/.test(r)],
             ["รู้ว่าอยู่เชียงใหม่", (r) => /เชียงใหม่/.test(r)]] },
  { id: "mem-2", lang: "th",
    plant: "จำไว้ว่าผมชอบให้ตอบสั้น ๆ เป็นข้อ ๆ ไม่เกิน 5 ข้อ และไม่ต้องเกริ่นนำ",
    ask: "วิธีเตรียมตัวก่อนสัมภาษณ์งานตำแหน่งผู้จัดการโครงการ",
    checks: [["ไม่เกิน 5 ข้อ", (r) => (r.match(/^\s*(?:[-*•]|\d[.)])/gm) || []).length <= 5],
             ["ไม่ยาวเกิน", (r) => r.length < 1400]] },
  { id: "mem-3", lang: "en",
    plant: "Remember: I always want code examples in TypeScript, never JavaScript, and I use pnpm.",
    ask: "Show me how to read a JSON file and print one field.",
    checks: [["typescript", (r) => /: ?string|: ?any|interface |as \w+|<[A-Za-z]+>/.test(r)],
             ["not plain js only", (r) => !/```javascript/i.test(r)]] },
  { id: "mem-4", lang: "th",
    plant: "จำไว้ว่าบริษัทผมใช้ PostgreSQL ไม่ใช่ MySQL และ deploy บน Kubernetes",
    ask: "ช่วยเขียนคำสั่งสำรองฐานข้อมูลรายวันให้หน่อย",
    checks: [["ใช้ pg_dump", (r) => /pg_dump/i.test(r)],
             ["ไม่ใช่ mysqldump", (r) => !/mysqldump/i.test(r)]] },
];

module.exports = { Q, THREADS, MEMORY, FILLER_TH };
