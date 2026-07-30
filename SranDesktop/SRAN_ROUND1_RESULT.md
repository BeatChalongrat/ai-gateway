# SRAN Desktop — Round 1 Result "หยุดเลือด X แดง" (R1 + R5 + R3′)

**สถานะ:** ✅ เสร็จ — `dotnet build` ผ่าน (0 errors), `dotnet test` **1249/1249 ผ่าน** (รวมเทสใหม่ 15 เคส)
**ขอบเขต:** R1 (adaptive readiness) + R5 (human error) + R3′ (แยก marker แบบ non-regressive) ตามแนวทาง (ก) ที่อนุมัติ
**ไม่แตะ:** typing ladder (R2), auto-retry/transient→retryable (R4), grouping UI — ยกไปรอบหน้าตามที่ตกลง

---

## ไฟล์ที่แก้ (8 ไฟล์ + 1 เทสใหม่)

| ไฟล์ | งาน | สาระ |
|---|---|---|
| `Infrastructure/Mcp/Tools/ComputerTypeTool.cs` | R1 + R3′ | ค่า readiness wait 2s → `InputReadyWait` (6s) + แยก read-back unavailable/mismatch |
| `Application/Services/DurableTaskRuntime.cs` | R3′ | เพิ่ม `ReadBackUnavailable` ใน `RequiresFreshObservation` (คงพฤติกรรมเดิม) |
| `Presentation/ViewModels/AgentExecutionViewModel.cs` | R5 | `HumanError` + `ExtractFailureCode` + `FailureKey`/`HumanizeFailure` |
| `Presentation/Controls/MessageBubble.xaml` | R5 | dot verification = amber, ป้าย "กำลังยืนยันผล…", บรรทัด HumanError บนการ์ดที่ล้ม |
| `Presentation/Resources/Themes/Dark.xaml` | R5 | `AccentAmberColor`/`AccentAmberBrush` (#E8A23D) |
| `Presentation/Resources/Themes/Light.xaml` | R5 | `AccentAmberColor`/`AccentAmberBrush` (#D9822B) |
| `Presentation/Localization/Dict.cs` | R5 | 9 คีย์ (En + Th): `tool.verifying`, `tool.error.*` |
| `Tests/Agent/Round1ReliabilityTests.cs` | test | 15 เคส map FailureCode + ExtractFailureCode |

---

## รายละเอียด diff

### R1 — adaptive readiness wait (`ComputerTypeTool.cs`)
- เพิ่มค่าคงที่ตั้งชื่อชัด `internal static readonly TimeSpan InputReadyWait = TimeSpan.FromSeconds(6);`
- call site `WaitForAsync(... timeout: TimeSpan.FromSeconds(2) ...)` → `timeout: InputReadyWait`
- **poll เดิม 100ms ถึง deadline อยู่แล้ว** (`ApplicationReadinessService.cs:19-29`) → แอปที่พร้อมคืนทันทีตั้งแต่ poll แรก, เฉพาะเคสช้าเท่านั้นที่ได้เวลาเพิ่ม
- **หมายเหตุ:** `ComputerClickTool.cs` **ไม่มี** การเรียก `ApplicationReadinessService.WaitForAsync` (ใช้ acquisition/activation คนละกลไก) จึงไม่มี hard-2s ให้แก้ — R1 แตะเฉพาะ `computer_type` ตามโค้ดจริง
- **classification timeout → retryable ยกไป R4** ตามที่ตกลง (รอบนี้แค่ขยายเวลา)

### R3′ — แยก marker (non-regressive) (`ComputerTypeTool.cs` + `DurableTaskRuntime.cs`)
คงทุกอย่างที่กำหนดผลการ์ด: `Success` (ยัง `FromComputerAction`=true), `EffectVerified = exact && receiverConfirmed`, `Retryable = !exact` — **ไม่เปลี่ยน** → amber ยังเป็น amber, green ยังเป็น green
- เพิ่ม `readBackUnavailable = after is null` (ไม่มี ValuePattern/TextPattern ให้อ่านเลย ≠ อ่านได้แต่ค่าว่าง)
- แยก 3 กรณี → `read_back_status = exact | unavailable | mismatch`:
  - `unavailable` (receiver ยืนยัน + อ่านกลับไม่ได้ เช่น Notepad RichEdit) → `FailureCode="ReadBackUnavailable"` + ข้อความ "จะยืนยันด้วย observation รอบถัดไป"
  - `mismatch` (อ่านได้แต่ค่าไม่ตรง) → `FailureCode="EffectMismatch"` เหมือนเดิม
- เพิ่ม evidence token `read_back_status=<...>` (additive) — ยังคงกฎ T4: ปล่อย `read_back_value=` เฉพาะเมื่อ exact จริง
- **กัน regression retry-flow:** เพิ่ม `"ReadBackUnavailable"` ใน `RequiresFreshObservation` (DurableTaskRuntime.cs:1652-1657) → เคสนี้ยัง "ต้อง observe ใหม่ก่อน retry" เหมือนตอนที่มันเคยเป็น `EffectMismatch`

### R5 — human-readable error (`AgentExecutionViewModel.cs` + XAML + Dict + themes)
- `ExtractFailureCode(evt)`: ใช้ `ComputerAction.FailureCode` ก่อน; ถ้าไม่มี ดึง token หน้า `:` จาก `FullResult` (tool prefix hard-fail เป็น `"Error: <Code>: reason"`)
- `FailureKey(code)`: pure map เฉพาะ FailureCode ที่ **แดงจริง** → FocusLost / TargetNotFound / WrongWindow / UnboundInput / Stale* / ApplicationNotReady / capture-validation (Mismatch·HostWindow·OffScreen·Occlud) → generic เป็น fallback
- `HumanError` ตั้งค่าใน `Complete()` **เฉพาะเมื่อ `State=="failed"`** (การ์ด amber/green ไม่ได้รับผล)
- XAML:
  - dot `verification_pending` เปลี่ยนจาก `AccentBlueBrush` → `AccentAmberBrush` (ทั้ง top-level และ nested child)
  - เพิ่มป้าย amber "กำลังยืนยันผล…" ในหัวการ์ดเมื่อ `IsVerificationPending`
  - เพิ่มบรรทัด `HumanError` (สีแดง, **มองเห็นตลอด** ไม่ต้อง expand) เมื่อ `IsFailed`
- **"expander เก็บ code ดิบ"**: ใช้ของเดิม — กด chevron ขยายการ์ด → ส่วน **Output** แสดง `FullResult` (โค้ดดิบ "Error: FocusLost: …") อยู่แล้ว จึงไม่เพิ่ม expander ซ้ำ (ปุ่ม retry เดิม = regenerate turn คงไว้)

**ข้อตัดสินใจ (แจ้งเพื่อความโปร่งใส):** สเปกให้ hardcode ข้อความไทยใน VM แต่แอปเป็น bilingual (LocaleService, ตาม FIX7 เรื่องภาษา) — จึงทำเป็น **localized keys** (ค่า TH ตรงตามที่คุณให้ + เพิ่ม EN) ให้เข้ากับ pattern เดิม (`Friendly()`/`ArgLabel()` ก็ localize) ถ้าอยากได้ไทยล้วน hardcode บอกได้

---

## FailureCode ที่ map แล้ว vs ยังไม่ map

**map แล้ว (แดงจริง):** `FocusLost`, `TargetNotFound`, `WrongWindow`, `UnboundInput`, `StaleObservation`/`StaleWindow`/`CaptureStale`, `ApplicationNotReady`, `ForegroundWindowMismatch`/`HostWindowTargeted`/OffScreen/Occluded → validate

**ยังไม่ map (ตกลง generic — รอ R4/รอบหน้า):**
- `RetryBudgetExceeded`, `BlindRetryBlocked`, `FreshObservationRequired` (มาจาก durable runtime ไม่ใช่ tool — ควรมีข้อความเฉพาะรอบ UI ถัดไป)
- `DurableStateCommitFailed`, `UnknownOutcome`, `schema_out_of_date` ฯลฯ
- `OsInputRejected` (computer_click), `EffectNotVerified` — คนละ tool, ค่อยเก็บตอนทำ R2/click
- `EffectMismatch`, `ReadBackUnavailable` → เป็น amber (ไม่ใช่ failed) จึงไม่แสดง HumanError โดยตั้งใจ

---

## วิธีทดสอบ manual (หลัง publish + restart เต็ม)

> ต้องปิด+เปิด `SranDesktop.exe` ใหม่ทั้งหมด (ตาม feedback — เปิดหน้าต่างเฉย ๆ ไม่พอ)

1. **เคส Notepad RichEdit (R3′ + R1):** ปิด Notepad ก่อน → สั่ง "เปิด Notepad แล้วพิมพ์ SRAN round1 works"
   - คาดหวัง: `Type text` **ไม่ขึ้น X แดงปลอม** ถ้าตัวอักษรลงจริง — เป็น amber (dot ส้ม + "กำลังยืนยันผล…") แล้ว observation รอบถัดไป (`read_screen_elements`) ยืนยันเป็นเขียว; ถ้าพิมพ์ไม่ลงจริง → ยังต้องรายงานไม่สำเร็จอย่างซื่อสัตย์ (สัญญาข้อ 12 คงอยู่)
   - กด expand การ์ด → เห็น evidence `read_back_status=unavailable|exact|mismatch`
2. **เคสแอปหนัก (R1):** เปิดแอปหนัก (เช่น Excel) แล้วสั่งพิมพ์ทันที
   - คาดหวัง: ไม่ fail เพราะ 2s (ได้ถึง 6s), แอปที่พร้อมอยู่แล้วยังเร็วเท่าเดิม
3. **เคส FocusLost / TargetNotFound (R5):** สั่งพิมพ์แล้วสลับหน้าต่างแย่งโฟกัส
   - คาดหวัง: การ์ดแดงแสดงประโยคไทย "พิมพ์ไม่สำเร็จ: มีหน้าต่างอื่นแย่งโฟกัส" (มองเห็นทันทีไม่ต้อง expand); กด expand → เห็นโค้ดดิบใน Output
4. **สลับภาษา EN:** เปลี่ยน locale → ข้อความ error เป็นอังกฤษ ("Couldn't type: another window took focus.")

---

## Build / Test
```
dotnet build SranDesktop.sln -c Debug   → Build succeeded, 0 Error(s)
dotnet test  SranDesktop.sln -c Debug   → Passed! Failed: 0, Passed: 1249, Skipped: 0
```
(warning MSB3277 Speech version conflict = pre-existing, ไม่เกี่ยวกับรอบนี้)

**หยุดรอรีวิว** — รอบ 2 = UI retrying state + grouping (goal→steps) + R2/R4 typing resilience
