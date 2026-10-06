# Agent Market — glossary (Thai ↔ English)

The words of Agent Market, settled once. Every screen, every sentence the
server writes and every document an agent makes uses these terms; a card that
adds a word adds it here first. The English is the word an accountant working
in English would use for the thing — not the Thai word carried across.

Where the two disagree, the **meaning** column decides: a term is translated
by what it does in the product, never by its dictionary entry.

## 1. Terms with an exact meaning

These have been argued over. They do not drift, in either language.

| Thai | English | Meaning — and what it must never be confused with |
|---|---|---|
| รอบ · รอบงาน | **run** | One complete piece of work for one period: files in, questions, figures, files out. A run has one language, one set of answers, one version of the agent. *Not* "round": a round is one of several goes at the same thing, and a run is never that — a second run for September is a new run. ERP users already say payment run, closing run. The code, the API and the admin app say run. |
| งวด | **period** | The accounting period a run is about ("September 2026"). งวดนี้ / งวดก่อน = **this period / prior period**. *Not* "instalment", not "cycle". |
| กระทบยอด | **reconcile** · reconciliation | Explaining the whole difference between two balances. งบกระทบยอดเงินฝากธนาคาร = **bank reconciliation statement**. |
| รายการกระทบยอด | **reconciling items** | The items that explain the difference (outstanding cheques, deposits in transit, bank charges not yet booked). *Not* "reconciled items" and not "matched items" — a matched pair is exactly what is *not* a reconciling item. |
| ลงตัว · ยังไม่ลงตัว | **reconciled · not reconciled** | Adjusted bank balance = adjusted book balance, or not. A run that is not reconciled delivers a **draft** and says the unreconciled difference. *Not* "settled", "balanced" or "tied". |
| ยอดหลังปรับปรุง | **adjusted balance** | Per bank / per books, after the reconciling items. ร่างรายการปรับปรุง = **proposed adjusting entries** — proposed, never posted. |
| จับคู่ (รายการ) | **match** | Pairing a line on one side with a line on the other (bank ↔ book, invoice ↔ PO). รายการที่จับคู่ = **matched items**. |
| จับคู่ (คอลัมน์ · ช่อง) | **map** | Saying which column of a file, or which field of a form, is which. Thai uses one word for both; English must not. จับคู่คอลัมน์ = **map columns**; จับคู่ช่อง = **map fields**. |
| ใบกำกับภาษี | **tax invoice** | The VAT document of the Thai Revenue Code. *Not* "VAT invoice", and never just "invoice" (ใบแจ้งหนี้). ใบกำกับภาษีเต็มรูป / อย่างย่อ = **full / abbreviated tax invoice** (the Revenue Department's own English). |
| สามทาง · ตรวจ 3 ทาง (PO ↔ GR ↔ ใบกำกับ) | **three-way match** (PO ↔ goods receipt ↔ invoice) | A line is tested against the order *and* the receipt. ตรวจ 2 ทาง = **two-way match** (PO ↔ invoice): quantity is compared with what was ordered only. Which one was used is said on every line; never hidden. |
| ใบสั่งซื้อ · ใบรับของ | **purchase order (PO) · goods receipt (GR)** | ค้างระหว่างรับของกับเรียกเก็บ = **GR/IR** (received not invoiced · invoiced not received) — reported, never posted. |
| ผ่าน · พร้อมเสนออนุมัติ | **passed · ready to submit for approval** | The best an agent ever says. It approves nothing and pays nothing. *Never* "approved". |
| ติดเงื่อนไข | **blocked** | An invoice fails a rule (quantity over what was received, tax invoice incomplete…); the reason is printed. The word AP teams use. *Not* "conditional", not "on hold". |
| ต้องให้คนตัดสิน | **needs a decision** | The rules cannot settle it; a person chooses from the options given. รอคนตัดสิน = **awaiting decision**; ตัดสินแล้ว = **decided**; บันทึกไว้ = **noted**. |
| ข้อยกเว้น | **exception** | A row a rule flagged for a person. The system never resolves one itself. |
| ข้อสมมติที่ใช้ | **assumptions used** | Everything a run took without being told in that run — a default, a remembered answer — each with where it came from. Heading: "Assumptions used in this run". |
| ที่มาของตัวเลข | **source of figures** | For every figure: the file, the sheet, the cells and the formula. Column head: **Source**. *Not* "provenance" (an engineer's word), not "origin of numbers", and not "audit trail" — in this product that would be mistaken for the admin's log of who did what. |
| ยืนยันแล้ว | **confirmed** | A person's word, only. Nothing the machine did is ever "confirmed". คนยืนยันแล้ว = **confirmed by a person**. |
| ระบบอ่าน · | **system-read ·** | What the machine read and nobody has looked at yet. "ระบบอ่าน · ต้องตรวจ" = **system-read · needs review**; "ระบบอ่าน · ตัวเลขผ่านการตรวจ (ยังไม่มีคนตรวจ)" = **system-read · figures pass the checks (not yet reviewed)**. Checks *pass*; people *confirm*. *Not* "verified", not "validated". |
| ช่อง (ของแบบฟอร์ม) | **field** | A blank of a form that takes a value. |
| ช่องที่ยังกรอกไม่ได้ | **fields that could not be filled** | The list on a partly filled form: every field with a label and no value, each with its reason. A form that has such a list is a **draft**, never a pass. Short form on a row: **not filled**. |
| ฉบับร่าง · ร่าง | **draft** | A file that was delivered and must not be passed on: a run not reconciled, a form with fields that could not be filled, a working paper whose header is incomplete. The file says so on its face. |
| หยุดไว้ก่อน | **held** | A question the run cannot go on without: it waits, spends nothing and takes no default. On the card: "waiting for your answer". (The code's severity `blocking`.) |
| ปรับให้แล้ว | **default applied** | A question the run can go on without: the recommended answer was used, and it is listed under *assumptions used*; answering later only re-makes the files. (The code's severity `cosmetic`.) *Not* "adjusted": on a reconciliation "adjusted" means an adjusting entry, and no figure was changed here. |
| ตรวจทานและลงชื่อ | **review and sign off** | ผู้จัดทำ = **prepared by**; ผู้สอบทาน / ผู้ตรวจทาน = **reviewed by**. Who a person is, is who is signed in. |
| กระดาษทำการ | **working paper** | The workbook an auditor can follow. *Not* "worksheet" (a sheet of a workbook). |
| หลักฐาน | **evidence** | What a figure or a decision rests on. A companion sheet is "… · Evidence". |

## 2. Everyday words

| Thai | English |
|---|---|
| agent (written in Latin letters on the Thai screen too) | agent |
| ห้องทำงาน | workspace |
| ติดตั้ง · ถอนการติดตั้ง | install · uninstall |
| ไฟล์ที่ได้ · ไฟล์ผลงาน | files · output files |
| ไฟล์ต้นทาง | source files |
| ค่าตั้งต้น · ค่าแนะนำ · ค่าที่จำไว้ | default · recommended · remembered answer |
| คำถาม · การ์ดคำถาม | question · question card |
| ผู้ดูแล | admin |
| เครดิต · งบเครดิต | credits · credit budget |
| รุ่น · เวอร์ชัน (ของ agent) | version |
| แชร์ให้เพื่อนร่วมงาน | share with a colleague |
| แจ้งปัญหา | report a problem |
| เตือน · การเตือน | remind · reminder |
| ข้าม | skip |
| เอกสารที่ยังไม่ได้รวม | documents not included |
| ไม่นับรวม | excluded |
| แบบฟอร์ม | form |
| ธีมเอกสาร | document theme |
| เอกสารข้อมูลของ agent | agent data sheet |
| ทดสอบกับข้อมูลตัวอย่าง | test with sample data |

## 3. Accounting vocabulary

| Thai | English |
|---|---|
| บัญชีแยกประเภท | general ledger (GL) |
| งบทดลอง | trial balance |
| งบกำไรขาดทุน | income statement |
| รายได้ · ค่าใช้จ่าย | revenue · expenses |
| กำไรขั้นต้น · กำไรสุทธิ · อัตรากำไรสุทธิ | gross profit · net profit · net profit margin |
| งบประมาณ · ต่างจากงบ · ต่างจากงวดก่อน | budget · variance to budget · change from prior period |
| จุด (ของร้อยละ) | pp (percentage points) |
| เดบิต · เครดิต | debit · credit |
| รหัสบัญชี · ชื่อบัญชี · ศูนย์ต้นทุน | account code · account name · cost centre |
| เกณฑ์ความมีสาระสำคัญ | materiality threshold |
| รายการเดินบัญชีธนาคาร | bank statement |
| เช็คค้างจ่าย · เงินฝากระหว่างทาง | outstanding cheques · deposits in transit |
| รายการค้างยกมา · ยกไป | items brought forward · carried forward |
| ใบแจ้งหนี้ · ใบเสร็จรับเงิน | invoice · receipt |
| ใบลดหนี้ · ใบเพิ่มหนี้ | credit note · debit note |
| ภาษีมูลค่าเพิ่ม (VAT) · ภาษีซื้อ · ภาษีขาย | VAT · input VAT · output VAT |
| ขอคืนภาษีซื้อ | claim input VAT |
| ภาษีหัก ณ ที่จ่าย | withholding tax (WHT) |
| เลขประจำตัวผู้เสียภาษี | tax ID |
| สำนักงานใหญ่ · สาขา | head office · branch |
| ผู้ขาย · ผู้ซื้อ | seller · buyer |
| การเบิกค่าใช้จ่าย · รายการเบิก | expense claim |
| นโยบายการเบิก · วงเงิน | expense policy · limit |
| แยกบิล | split purchase |
| ร้านค้าต้องห้าม | prohibited merchant |
| เบิกซ้ำ · เอกสารซ้ำ | duplicate claim · duplicate document |

## 4. Numbers, dates and money

Formatting follows the language. The value never changes, and no rule of law
is a format: VAT 7 %, the withholding rates, what a tax invoice must carry and
the 13-digit tax ID are code, per `jurisdiction`, in both languages.

| | Thai | English |
|---|---|---|
| a date | 28 ก.ย. 2569 (Buddhist era) | 28 Sep 2026 |
| a moment | 28 ก.ย. 2569 เวลา 10:05 น. | 28 Sep 2026, 10:05 |
| a period | กันยายน 2569 | September 2026 |
| an amount | 1,284,500.00 | 1,284,500.00 |
| a negative amount in a document | (12,400.00) | (12,400.00) |
| money in a sentence | 1,284,500.00 บาท | THB 1,284,500.00 |
| money in a column head | ยอดรวม (บาท) | Total (THB) |
| a percentage | 10.5% | 10.5% |
| millions | 12.48 ล้าน | 12.48 M |

A date a person typed, and a date printed on their own document, are their
data: shown as written, in either language. The time is Thai time in both.

## 5. What stays as it is, in both languages

- Names: a person's, a company's, a file's, a model's, a provider's, the
  product's ("Agent Market", "SRAN Chat").
- File formats and standards: Excel, Word, PowerPoint, PDF, CSV, XML, e-Tax,
  .xlsx, VAT, WHT, GL, PO, GR, DPI.
- Version marks: v2, 1.6.0.
- **The customer's own data**: whatever is in their files, whatever they
  typed, the labels of their own form. An English run of a Thai ledger shows
  Thai account names, because they are the customer's.

## 6. Rules for whoever writes a sentence

1. **Both languages, same commit.** A string exists in Thai and in English
   or it does not exist; the lint fails a key that is in one dictionary only
   (web: `npm run lint:i18n`; server: `TestEverySentenceHasItsEnglish`;
   packages: `test_catalogue.py`).
2. **A message says three things**: what happened, what to do next, whether
   the person's data is safe — in that order, in plain words.
3. **English is written, not converted.** No Thai politeness particle is
   carried over ("ครับ"), no sentence starts with "Please kindly". Sentence
   case for buttons and headings ("Start a new run"), no full stop on a
   label, British spelling (organisation, colour, cheque).
4. **No error's words in a sentence for a person.** The server withholds any
   text that reads like an error. In English that means a sentence must not
   contain: `not found`, `timed out`, `permission denied`, `undefined`,
   `constraint`, `violates`, `error:`, `warning:`, `exception`, `line 12`,
   a URL or an IP address. Say "could not find", "took too long", "is not
   allowed to" instead (`TestNoEnglishSentenceReadsLikeAnError`).
5. **A card's header fits 12 character cells** in either language
   ("Period", "Which files", "งวดบัญชี").
6. **A sheet name fits Excel**: 31 characters, none of `/ \ ? * [ ] :`.
7. **One term, one word.** If the word needed is not in this file, it goes
   into this file first.
