# SRAN Desktop — Round 1 Gate Tests Result

**สถานะ:** ✅ เสร็จ — `dotnet build` ผ่าน, `dotnet test` **1259/1259 ผ่าน** (1249 เดิม + 10 เคสใหม่)
**ขอบเขต:** แปลง manual gate 2 เคสของรอบ 1 (R3′ amber path, R5 human error) เป็นเทสอัตโนมัติ deterministic
**Production change:** ❌ ไม่มี · **Test seam:** ❌ ไม่ต้องมี (ดูเหตุผลด้านล่าง)

ทิศทางตามที่อนุมัติ: **Test 1 = deterministic ผ่าน `Complete()`** · **Test 2 = `Complete()` ตรง + bilingual**

---

## ทำไมไม่มี seam / ไม่ใช้ FlaUI (สรุปการตัดสินใจ)

- **Pipeline การ์ดของจริงคือ `ToolExecutionItemViewModel.Complete(AgentToolResultEvent)`** — ตัวเดียวกับที่ `ChatViewModel` เรียกกับทุก `AgentToolResultEvent` (arm `AgentToolResultEvent` → `item.Complete(result)`) การป้อน event สังเคราะห์เข้า `Complete()` โดยตรง = **เดินเส้นทาง production จริง** โดยแทน boundary LLM/orchestrator ด้วย event ที่ประกอบเอง → ไม่ต้องมี hook/`#if DEBUG`/fake tool ใด ๆ
- `InternalsVisibleTo SranDesktop.Tests` มีครบ 3 ชั้น (Application/Infrastructure/Presentation) แล้ว — เข้าถึง `internal` ได้โดยไม่ต้องเปลี่ยน accessibility
- **FlaUI + แอปจริง + agent สั่งพิมพ์ + Notepad จริง** ถูกตัดออกเพราะ (ก) ต้องมี LLM สด/secret/desktop runner + non-deterministic, (ข) กฎห้าม inject input เข้าแอปจริงของผู้ใช้ (เคยเขียนทับไฟล์ Notepad มาก่อน) — จึงเลือก deterministic ที่ gate ค่าเดียวกันได้และรันใน CI ได้

**assert แบบ state ไม่ใช่ pixel:** เทสเช็ค `IsFailed` / `IsVerificationPending` / `HumanError` (proxy ของสีการ์ด) และ resolve ข้อความจาก `LocaleService`/`Dict` ตาม key — ไม่ hardcode สี/ไม่ hardcode literal string

---

## ไฟล์ที่เพิ่ม (1 ไฟล์, test-only)

`src/SranDesktop.Tests/Agent/Round1GateTests.cs` — 4 เทส (10 เคส):

| เทส | พิสูจน์ | assert หลัก |
|---|---|---|
| `ReadBackUnavailable_Result_Is_Amber_Not_Failed` | R3′ — Notepad RichEdit read-back-ไม่ได้ → **amber** | `IsVerificationPending==true`, `IsFailed==false`, `IsOk==false`, `HumanError==null`, marker=`ReadBackUnavailable` |
| `EffectMismatch_Result_Is_Also_Amber_With_Its_Own_Marker` | mismatch จริงยัง amber (รอบ 1 ไม่ทำให้แดง) + marker แยก | `IsVerificationPending==true`, marker=`EffectMismatch` |
| `FocusLost_Failure_Shows_Localized_HumanError_In_Both_Locales` | R5 — การ์ดแดงโชว์ human error ถูก locale + code ดิบยังอยู่ | `IsFailed==true`; `HumanError`=Dict.Th/En; TH≠EN; `FullResult` มี "FocusLost" |
| `Every_Red_Code_Resolves_To_A_Sentence_In_Both_Locales` (theory ×7) | ไม่มี key ค้าง/ไม่ resolve บนการ์ดแดง | ทุก red code → resolve ≠ key, ไม่ว่าง ทั้ง th/en |

**Red codes ที่ครอบ:** FocusLost, TargetNotFound, StaleObservation, WrongWindow, UnboundInput, ApplicationNotReady, ForegroundWindowMismatch

---

## วิธีรัน

```bash
# ทั้ง suite (รวม gate)
dotnet test SranDesktop.sln -c Debug

# เฉพาะ gate รอบ 1
dotnet test SranDesktop.sln -c Debug --filter "FullyQualifiedName~Round1GateTests"
```

- **Prerequisite:** ไม่มี — deterministic ล้วน ไม่ต้องมี GUI session / Notepad / LLM / network / secret
- **รันบน CI headless ได้** (แม้ target เป็น `net8.0-windows` ก็เป็น logic test ไม่เปิดหน้าต่าง)
- **เวลารันโดยประมาณ:** ทั้ง suite ~30s; เฉพาะ Round1GateTests < 1s
- Build/Test: `Build succeeded` · `Passed! Failed: 0, Passed: 1259, Skipped: 0`

---

## seam diff ให้รีวิว
**ไม่มี** — งานนี้เป็น test-only 100% ไม่แตะ production logic และไม่ต้องเพิ่ม seam (mapping จริงเข้าถึงได้ผ่าน `InternalsVisibleTo` ที่มีอยู่)

---

## ข้อจำกัด / สิ่งที่เทสนี้ **ไม่** ครอบ (โปร่งใส)

1. **ไม่ยืนยัน XAML binding จริง** — เทสยืนยัน VM (`IsFailed`/`HumanError`) ถูก แต่ไม่ได้ยืนยันว่า `MessageBubble.xaml` ผูก binding เหล่านั้นถูกต้อง (เช่นเผลอลบ `{Binding HumanError}` เทสยังเขียว) → ถ้าต้องการปิดช่องนี้ ทำ **STA binding test** (โฮสต์ `MessageBubble` จริงใน STA window เช็ค visual tree) ได้ในรอบถัดไป — ยังไม่ต้อง FlaUI
2. **ไม่ยืนยันพฤติกรรม RichEdit จริง** — เทส amber ใช้ ComputerAction สังเคราะห์ที่ตั้ง `EffectVerified=false, FailureCode="ReadBackUnavailable"` (จำลองสิ่งที่ tool คืน) **ไม่ได้** พิสูจน์ว่า Win11 Notepad คืน `after is null` จริง → การพิสูจน์ read-back จริงต้อง integration test บน control ที่เทสเปิด-ปิดเอง (interactive-only) ซึ่งคุณเลือกไม่ทำในรอบนี้
3. **LocaleService เป็น singleton** — เทส bilingual set locale แล้ว restore ใน `finally` ตาม pattern เดิมของ repo (`ToolCardHumanizeTests`); มีช่อง race เชิงทฤษฎีกับ test class อื่นที่แตะ locale พร้อมกัน (โค้ดเดิมก็มีอยู่แล้ว ไม่ได้เพิ่มความเสี่ยง)

---

## ถ้าจะทำ FlaUI e2e จริงในอนาคต (สำหรับ CI)
ต้องมี **CI runner ที่มี interactive desktop session** (self-hosted Windows runner แบบ autologon + ไม่ล็อกหน้าจอ; `windows-latest` ของ GitHub-hosted รัน UIA/หน้าต่างได้จำกัด) + target **control ที่ throwaway ของเราเอง** ไม่ใช่แอปผู้ใช้ + มาร์ค category แยกให้ข้ามได้เมื่อไม่มี desktop

**หยุดรอรีวิว** — ไม่มี seam ให้อนุมัติ; รอไฟเขียวก่อนไปรอบ 2 (UI retrying state + grouping + R2/R4)
