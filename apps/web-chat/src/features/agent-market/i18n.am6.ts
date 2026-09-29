// AM6 — the agent's model menu is the chat's, and the workspace composer's
// affordances: what the menu says about models an agent cannot use, the
// short placeholder, the suggestions above the composer, and what is said
// about a knowledge base. Thai, like the rest of Agent Market.

export const am6Th = {
  // the model menu: the two lines that are not on the rows
  "model.hidden": "ซ่อน {n} ตัวที่ทำงานนี้ไม่ได้",
  "model.hiddenWhy": "ซ่อน {n} ตัวที่ทำงานนี้ไม่ได้ · {why}",
  "model.blocked": "โมเดลที่เป็นสีจางเลือกไม่ได้ตามนโยบายขององค์กร",

  // the composer
  "composer.placeholderTo": "ส่งข้อความถึง {name}…",
  "composer.placeholderAgent": "ส่งข้อความถึง agent…",
  "composer.suggest.aria": "ตัวอย่างสิ่งที่ทำได้",
  "composer.suggest.attach": "แนบไฟล์เพื่อเริ่มรอบใหม่",
  "composer.suggest.ask": "ถามเกี่ยวกับตัวเลขของรอบล่าสุด",
  "composer.suggest.askText": "ช่วยอธิบายตัวเลขของรอบล่าสุด",
  "composer.suggest.formats": "ขอไฟล์แบบอื่นของรอบล่าสุด",
  "composer.suggest.sample": "ดูผลจากไฟล์ตัวอย่าง",
  "composer.suggest.datasheet": "ดูว่า agent นี้อ่านอะไรบ้าง",

  // knowledge answers questions; it is never the source of a number in a deliverable
  // (the chat's chip says ฐานความรู้: one screen, one word)
  "knowledge.askOnly": "ฐานความรู้ใช้ตอบคำถามเท่านั้น ไม่ใช้กับตัวเลขในเอกสาร",
  "knowledge.from": "จากฐานความรู้ “{base}” · ไม่ใช่ส่วนหนึ่งของรายงาน",

  // a round that delivered its work while the agent's gate did not pass: a
  // result, charged like any round that delivers, and never said as a failure
  "outcome.open.notTied": "เสร็จแล้ว · ยังไม่ลงตัว",
  "outcome.open.gate": "เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent",
  // the same, where it stands in a line of counts
  "outcome.open.notTiedCount": "เสร็จแล้วแต่ยังไม่ลงตัว",
  "outcome.open.gateCount": "เสร็จแล้วแต่ยังไม่ผ่านเกณฑ์",
  "round.open": "รอบ {period} · {state}",
  "round.openNoPeriod": "รอบนี้{state}",
  "stepStatus.open": "เสร็จแล้ว มีหมายเหตุ",
  "outputs.draftDone": "ร่าง — งานของรอบนี้เสร็จและส่งไฟล์ให้แล้ว",
  "review.unavailable": "ยังตรวจทานและลงชื่อรอบนี้ไม่ได้ เพราะไฟล์ของรอบนี้เป็นร่าง",
  "review.unavailableWhy": "ยังตรวจทานและลงชื่อรอบนี้ไม่ได้: {why}",
  "dash.kpi.runsSubOpen": "สำเร็จ {ok} · {state} {open} · ไม่สำเร็จ {failed}",
  "dash.open": "รอบที่เสร็จแล้วแต่ยังไม่ผ่านเกณฑ์ของ agent",
  "dash.costNotCharged": "ค่าใช้จ่ายที่ระบบจ่ายแต่ไม่ได้คิดเครดิต {credits} เครดิต",

  // the invoice ↔ PO agent: how deep every line was tested is never hidden
  "po.passed": "ผ่าน · พร้อมเสนออนุมัติ",
  "po.blocked": "ติดเงื่อนไข",
  "po.person": "ต้องให้คนตัดสิน",
  "po.invoices": "{n} ฉบับ",
  "po.baht": "{baht} บาท",
  "po.depth": "ตรวจ 3 ทาง {three} บรรทัด · ตรวจ 2 ทาง {two} บรรทัด",
  "po.depthUnlinked": " · ยังไม่ได้ตรวจกับ PO {n} บรรทัด",
  "po.twoWayNote": "บรรทัดที่ตรวจ 2 ทางเทียบปริมาณกับที่สั่งซื้อเท่านั้น ไม่ได้ตรวจกับใบรับของ",
  "po.reasons": "เหตุผลที่ยังไม่ผ่าน",
  "po.reasonLine": "{n} ฉบับ · {baht} บาท",
  "po.noApproval": "ไม่มีใบแจ้งหนี้ใดถูกอนุมัติจ่ายโดย agent การอนุมัติและการจ่ายเป็นของคน",

  // a receipt confirmed here: offered, never required
  "receipt.title": "ยืนยันการรับของ (ไม่บังคับ)",
  "receipt.body": "บรรทัดเหล่านี้ถูกเรียกเก็บแล้วแต่ยังไม่มีใบรับของ ถ้าคุณรู้ว่าของมาแล้ว ยืนยันที่นี่ได้ ชื่อของคุณจะถูกบันทึกว่าเป็นผู้ยืนยัน ถ้าไม่ยืนยัน ผลตรวจของรอบนี้ยังใช้ได้ตามเดิม",
  "receipt.line": "PO {po} · บรรทัด {line}",
  "receipt.lineNoNumber": "PO {po}",
  "receipt.ordered": "สั่งซื้อ {qty} {unit}",
  "receipt.all": "ของมาครบ",
  "receipt.partial": "มาบางส่วน",
  "receipt.quantity": "จำนวนที่ได้รับ",
  "receipt.confirm": "ยืนยันจำนวนนี้",
  "receipt.done": "บันทึกการรับของแล้ว รอบนี้กำลังคำนวณใหม่ด้วยใบรับของนี้ ไฟล์ชุดใหม่จะขึ้นด้านล่าง",
  "receipt.none": "ไม่มีบรรทัดที่รอยืนยันการรับของแล้ว",
  "receipt.badQuantity": "ใส่จำนวนที่ได้รับเป็นตัวเลขมากกว่า 0",

  // settings an agent declares
  "declared.title": "ค่าที่ใช้ตรวจ",
  "declared.body": "ค่าที่ agent ใช้ตัดสินว่าอะไรผ่าน แก้ได้ทุกเมื่อ มีผลกับรอบถัดไป",
  "declared.unset": "ยังไม่ได้ตั้ง",
  "declared.askOnce": "agent จะถามครั้งเดียวในรอบแรก",
  "declared.range": "ใส่ได้ตั้งแต่ {min} ถึง {max}",
  "declared.maxLength": "ไม่เกิน {n} ตัวอักษร",
  "declared.notNumber": "ใส่เป็นตัวเลข",

  // whom an agent is made for
  "scope.generic": "ใช้ได้กับทุกบริษัท",
  "scope.org": "เฉพาะองค์กรนี้",
  "jurisdiction.TH": "กฎหมายและภาษีของไทย",

  // what a round costs, said before it starts
  "estimate.aria": "ค่าใช้จ่ายของรอบ",

  // reminders: nothing runs without a person
  "remind.title": "เตือนให้ทำรอบ",
  "remind.body": "ตั้งวันและเวลาที่อยากให้เตือน เมื่อถึงเวลา ระบบเปิดรอบที่รอไฟล์ไว้ให้และแจ้งคุณ",
  "remind.none": "ยังไม่ได้ตั้งการเตือน",
  "remind.status.active": "เปิดอยู่",
  "remind.status.stopped": "หยุดอยู่",
  "remind.waiting": "รอบ {period} รอไฟล์อยู่",
  "remind.waitingNoPeriod": "มีรอบที่รอไฟล์อยู่",
  "remind.openRound": "เปิดรอบนี้",
  "remind.snooze": "เลื่อน",
  "remind.snoozeDays": "เลื่อน {n} วัน",
  "remind.skip": "ข้ามรอบนี้",
  "remind.stop": "หยุด",
  "remind.resume": "เปิดอีกครั้ง",
  "remind.delete": "ลบ",
  "remind.actions": "จัดการการเตือน {label}",
  "remind.add": "เพิ่มการเตือน",
  "remind.addTitle": "เพิ่มการเตือน",
  "remind.label": "ชื่อการเตือน",
  "remind.labelPlaceholder": "เช่น ปิดงบประจำเดือน",
  "remind.every": "เตือน",
  "remind.every.month": "ทุกเดือน",
  "remind.every.week": "ทุกสัปดาห์",
  "remind.day": "วันที่",
  "remind.weekday": "วัน",
  "remind.weekday.1": "จันทร์",
  "remind.weekday.2": "อังคาร",
  "remind.weekday.3": "พุธ",
  "remind.weekday.4": "พฤหัสบดี",
  "remind.weekday.5": "ศุกร์",
  "remind.weekday.6": "เสาร์",
  "remind.weekday.7": "อาทิตย์",
  "remind.time": "เวลา (เวลาไทย)",
  "remind.needLabel": "ตั้งชื่อการเตือนก่อน",
  "remind.badTime": "ใส่เวลาเป็น ชั่วโมง:นาที เช่น 09:00",
  "remind.reach.inApp": "แจ้งในแอป",
  "remind.reach.desktop": "แจ้งบนเดสก์ท็อป (เมื่อแอปเปิดอยู่ แม้ปิดหน้าต่าง)",
  "remind.reach.browser": "แจ้งในเบราว์เซอร์ (เมื่ออนุญาตการแจ้งเตือนแล้ว)",
  "remind.reach.email": "ส่งอีเมล",
  "remind.reach.noEmail": "ยังไม่ส่งอีเมล",

  // a notification, in the app
  "notify.aria": "การแจ้งเตือนจาก agent",
  "notify.open": "เปิด",
  "notify.snooze1": "เลื่อน 1 วัน",
  "notify.skip": "ข้ามรอบนี้",
  "notify.stop": "หยุดเตือน",
  "notify.close": "ปิด",
  "notify.snoozed": "เลื่อนการเตือนไป 1 วันแล้ว",
  "notify.skipped": "ข้ามรอบนี้แล้ว",
  "notify.stopped": "หยุดการเตือนนี้แล้ว เปิดอีกครั้งได้ที่หน้าตั้งค่าของ agent",

  // a drafted e-mail: drafting needs nothing; sending needs a person's approval of that very message
  "email.draft": "ร่างอีเมล",
  "email.drafted": "ร่างอีเมลแล้ว อยู่ในแชทด้านล่าง",
  "email.title": "ร่างอีเมล",
  "email.subject": "เรื่อง",
  "email.body": "ข้อความ",
  "email.attachments": "ไฟล์แนบ",
  "email.copy": "คัดลอก",
  "email.copied": "คัดลอกแล้ว",
  "email.to": "ถึง",
  "email.cc": "สำเนาถึง",
  "email.recipientsHint": "พิมพ์อีเมลของผู้รับเอง คั่นด้วยจุลภาค ระบบไม่อ่านผู้รับจากเอกสาร",
  "email.send": "ส่งอีเมลนี้",
  "email.confirmTitle": "ตรวจอีเมลก่อนส่ง",
  "email.confirmBody": "อีเมลนี้จะถูกส่งตามที่เห็นด้านล่าง ครั้งเดียว ถึงผู้รับที่คุณพิมพ์เท่านั้น",
  "email.confirm": "ยืนยันและส่ง",
  "email.sending": "กำลังส่ง…",
  "email.sentTitle": "ส่งแล้ว",
  "email.sentLine": "ส่งถึง {to} เมื่อ {when} · อนุมัติโดย {who}",
  "email.sentCc": "สำเนาถึง {cc}",
  "email.failedLine": "ส่งไม่สำเร็จ ถึง {to} เมื่อ {when} · อนุมัติโดย {who}",
  "email.noRecipient": "ใส่อีเมลของผู้รับอย่างน้อยหนึ่งคน",
  "email.none": "ไม่มี",
} as const;
