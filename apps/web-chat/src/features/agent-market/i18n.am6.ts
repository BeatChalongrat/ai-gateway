// AM6 — the agent's model menu is the chat's, and the workspace composer's
// affordances: what the menu says about models an agent cannot use, the
// short placeholder, the suggestions above the composer, and what is said
// about a knowledge base. Thai and English, like the rest of Agent Market
// (AM11): the same keys in both, checked by the type.

export const am6Th = {
  // the model menu: the two lines that are not on the rows

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
  // the same, on the line that closes the round — a sentence of its own for each
  // outcome, with its period and without (AM11: no fragment is glued into another,
  // so each language keeps its own word order)
  "round.open.notTied": "รอบ {period} · เสร็จแล้ว · ยังไม่ลงตัว",
  "round.open.notTiedNoPeriod": "รอบนี้เสร็จแล้ว · ยังไม่ลงตัว",
  "round.open.gate": "รอบ {period} · เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent",
  "round.open.gateNoPeriod": "รอบนี้เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent",
  "stepStatus.open": "เสร็จแล้ว มีหมายเหตุ",
  "outputs.draftDone": "ร่าง — งานของรอบนี้เสร็จและส่งไฟล์ให้แล้ว",
  "review.unavailable": "ยังตรวจทานและลงชื่อรอบนี้ไม่ได้ เพราะไฟล์ของรอบนี้เป็นร่าง",
  "review.unavailableWhy": "ยังตรวจทานและลงชื่อรอบนี้ไม่ได้: {why}",
  "dash.kpi.runsSubOpen": "สำเร็จ {ok} · {state} {open} · ไม่สำเร็จ {failed}",
  "dash.open": "รอบที่เสร็จแล้วแต่ยังไม่ผ่านเกณฑ์ของ agent",

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

  // how long a round takes, said before it starts (AM11b: the time, and only the time)
  "estimate.aria": "เวลาโดยประมาณของรอบ",

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

export const am6En = {
  // the model menu: the two lines that are not on the rows

  // the composer
  "composer.placeholderTo": "Message {name}…",
  "composer.placeholderAgent": "Message the agent…",
  "composer.suggest.aria": "Examples of what you can do",
  "composer.suggest.attach": "Attach files to start a new run",
  "composer.suggest.ask": "Ask about the latest run's figures",
  "composer.suggest.askText": "Explain the figures of the latest run",
  "composer.suggest.formats": "Request the latest run's files in another format",
  "composer.suggest.sample": "See the results from the sample files",
  "composer.suggest.datasheet": "See what this agent reads",

  // knowledge answers questions; it is never the source of a figure in a deliverable
  "knowledge.askOnly": "The knowledge base is used only to answer questions, never for the figures in documents",
  "knowledge.from": "From the knowledge base “{base}” · not part of the report",

  // a run that delivered its work while the agent's gate did not pass: a
  // result, charged like any run that delivers, and never said as a failure
  "outcome.open.notTied": "Finished · not reconciled",
  "outcome.open.gate": "Finished · the agent's criteria not yet met",
  // the same, where it stands in a line of counts ("Succeeded 5 · finished, not reconciled 2 · failed 1")
  "outcome.open.notTiedCount": "finished, not reconciled",
  "outcome.open.gateCount": "finished, criteria not met",
  // the same, on the line that closes the run
  "round.open.notTied": "{period} run · finished · not reconciled",
  "round.open.notTiedNoPeriod": "This run is finished · not reconciled",
  "round.open.gate": "{period} run · finished · the agent's criteria not yet met",
  "round.open.gateNoPeriod": "This run is finished · the agent's criteria not yet met",
  "stepStatus.open": "Done, with a note",
  "outputs.draftDone": "Draft — this run's work is finished and its files have been delivered",
  "review.unavailable": "This run cannot be reviewed and signed off yet, because its files are a draft.",
  "review.unavailableWhy": "This run cannot be reviewed and signed off yet: {why}",
  "dash.kpi.runsSubOpen": "Succeeded {ok} · {state} {open} · failed {failed}",
  "dash.open": "Runs that finished without meeting the agent's criteria",

  // the invoice ↔ PO agent: how deep every line was tested is never hidden
  "po.passed": "Passed · ready to submit for approval",
  "po.blocked": "Blocked",
  "po.person": "Needs a decision",
  "po.invoices": "Invoices: {n}",
  "po.baht": "THB {baht}",
  "po.depth": "3-way match: {three} lines · 2-way match: {two} lines",
  "po.depthUnlinked": " · not yet matched to a PO: {n} lines",
  "po.twoWayNote": "Lines with a 2-way match compare the quantity with what was ordered only. They are not checked against a goods receipt.",
  "po.reasons": "Reasons for not passing",
  "po.reasonLine": "Invoices: {n} · THB {baht}",
  "po.noApproval": "The agent approves no invoice for payment. Approval and payment belong to people.",

  // a receipt confirmed here: offered, never required
  "receipt.title": "Confirm goods received (optional)",
  "receipt.body": "These lines have been invoiced but have no goods receipt yet. If you know the goods have arrived, you can confirm it here. Your name is recorded as the person who confirmed. If you do not confirm, this run's results still stand.",
  "receipt.line": "PO {po} · line {line}",
  "receipt.lineNoNumber": "PO {po}",
  "receipt.ordered": "Ordered {qty} {unit}",
  "receipt.all": "All received",
  "receipt.partial": "Partly received",
  "receipt.quantity": "Quantity received",
  "receipt.confirm": "Confirm this quantity",
  "receipt.done": "Goods receipt recorded. This run is being recalculated with it — the new set of files will appear below.",
  "receipt.none": "No lines are waiting for a goods receipt to be confirmed.",
  "receipt.badQuantity": "Enter the quantity received as a number greater than 0.",

  // settings an agent declares
  "declared.title": "Values used for checks",
  "declared.body": "The values the agent uses to decide what passes. You can change them at any time; a change applies from the next run.",
  "declared.unset": "Not set yet",
  "declared.askOnce": "the agent asks once, in the first run",
  "declared.range": "Enter a value from {min} to {max}",
  "declared.maxLength": "No more than {n} characters",
  "declared.notNumber": "Enter a number.",

  // whom an agent is made for
  "scope.generic": "Works for any company",
  "scope.org": "For this organisation only",
  "jurisdiction.TH": "Thai law and tax",

  // how long a run takes, said before it starts (AM11b: the time, and only the time)
  "estimate.aria": "About how long a run takes",

  // reminders: nothing runs without a person
  "remind.title": "Reminders to do a run",
  "remind.body": "Set the day and time you want to be reminded. When the time comes, the system opens a run that waits for your files, and notifies you.",
  "remind.none": "No reminders set yet.",
  "remind.status.active": "On",
  "remind.status.stopped": "Stopped",
  "remind.waiting": "The {period} run is waiting for files",
  "remind.waitingNoPeriod": "A run is waiting for files",
  "remind.openRound": "Open this run",
  "remind.snooze": "Snooze",
  "remind.snoozeDays": "Snooze {n} d",
  "remind.skip": "Skip this run",
  "remind.stop": "Stop",
  "remind.resume": "Turn on again",
  "remind.delete": "Delete",
  "remind.actions": "Manage the reminder {label}",
  "remind.add": "Add reminder",
  "remind.addTitle": "Add reminder",
  "remind.label": "Reminder name",
  "remind.labelPlaceholder": "e.g. Month-end close",
  "remind.every": "Remind",
  "remind.every.month": "Every month",
  "remind.every.week": "Every week",
  "remind.day": "Day of the month",
  "remind.weekday": "Day",
  "remind.weekday.1": "Monday",
  "remind.weekday.2": "Tuesday",
  "remind.weekday.3": "Wednesday",
  "remind.weekday.4": "Thursday",
  "remind.weekday.5": "Friday",
  "remind.weekday.6": "Saturday",
  "remind.weekday.7": "Sunday",
  "remind.time": "Time (Thai time)",
  "remind.needLabel": "Name the reminder first.",
  "remind.badTime": "Enter the time as hours:minutes, e.g. 09:00.",
  "remind.reach.inApp": "Notify in the app",
  "remind.reach.desktop": "Notify on the desktop (while the app is running, even with its window closed)",
  "remind.reach.browser": "Notify in the browser (once notifications are allowed)",
  "remind.reach.email": "Send an email",
  "remind.reach.noEmail": "No email yet",

  // a notification, in the app
  "notify.aria": "Notifications from agents",
  "notify.open": "Open",
  "notify.snooze1": "Snooze 1 day",
  "notify.skip": "Skip this run",
  "notify.stop": "Stop reminding",
  "notify.close": "Close",
  "notify.snoozed": "Reminder snoozed for 1 day.",
  "notify.skipped": "This run was skipped.",
  "notify.stopped": "This reminder is stopped. You can turn it on again in the agent's settings.",

  // a drafted e-mail: drafting needs nothing; sending needs a person's approval of that very message
  "email.draft": "Draft an email",
  "email.drafted": "Email drafted — it is in the chat below.",
  "email.title": "Email draft",
  "email.subject": "Subject",
  "email.body": "Message",
  "email.attachments": "Attachments",
  "email.copy": "Copy",
  "email.copied": "Copied",
  "email.to": "To",
  "email.cc": "Cc",
  "email.recipientsHint": "Type the recipients' email addresses yourself, separated by commas. The system does not read recipients from documents.",
  "email.send": "Send this email",
  "email.confirmTitle": "Check the email before sending",
  "email.confirmBody": "This email will be sent exactly as shown below, once, and only to the recipients you typed.",
  "email.confirm": "Confirm and send",
  "email.sending": "Sending…",
  "email.sentTitle": "Sent",
  "email.sentLine": "Sent to {to} on {when} · approved by {who}",
  "email.sentCc": "Cc {cc}",
  "email.failedLine": "Sending to {to} failed on {when} · approved by {who}",
  "email.noRecipient": "Enter at least one recipient's email address.",
  "email.none": "None",
} as const satisfies Record<keyof typeof am6Th, string>;
