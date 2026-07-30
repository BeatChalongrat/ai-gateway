# Fix Prompts — Notepad/RichEdit Typing + Resilience

แก้ 2 จุดที่เหลือจากเทสจริง (บั๊ก deadlock กลุ่ม A แก้แล้ว):
1. พิมพ์ลง Windows 11 Notepad ผ่าน UIA ไม่เสถียร (Fill in field ❌ ซ้ำทั้ง GPT-4o และ mini)
2. โมเดลยอมแพ้เร็วเมื่อ action ล้มเหลว (GPT-4o หยุดหลัง 1-2 fail ทั้งที่งานยังไม่จบ)

วิธีใช้: วางทีละ prompt ใน Claude Code (Opus) ที่ root repo, build+test เขียวก่อนไปต่อ
`dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PROMPT T1 — พิมพ์ลง RichEdit/Windows 11 Notepad ให้เสถียรและ verify ได้

**ก่อนแก้ (ปัจจุบัน):** `SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/UiaSetValueTool.cs`
เมธอด `SetValue`:
- พยายาม `ValuePattern.SetValue` ก่อน แต่ **Windows 11 Notepad (RichEditD2DPT) ไม่ expose
  ValuePattern** → ตก fallback keyboard เสมอ
- fallback: ForceForegroundWindow → SetFocus → Ctrl+A → TypeText → read-back ด้วย `ReadValue`
  แล้วเทียบ `string.Equals(afterFallback, text, Ordinal)` แบบตรงเป๊ะ
- ปัญหา: read-back ของ RichEdit มาจาก `TextPattern.DocumentRange.GetText` ซึ่งมัก **มี \r\n
  ต่อท้ายหรือ whitespace ต่าง** → เทียบไม่ตรง → คืน `UiaKeyboardValueNotVerified`
  (พิมพ์ลงจริงแต่ถูกรายงานว่า "ไม่ verify" → step ล้มเหลว) + บางครั้ง foreground/focus ยัง
  ไม่นิ่งตอนพิมพ์ ทำให้คีย์ไม่ลง

**หลังแก้ (เป้าหมาย):** พิมพ์ลง Notepad/RichEdit สำเร็จและ verify ผ่านด้วยการเทียบแบบ
normalize + รองรับ control ที่มีแค่ TextPattern → "Fill in field" เขียว ไม่ใช่แดง

```
งาน:
1. เพิ่มการเทียบ read-back แบบ normalize: เทียบหลัง TrimEnd('\r','\n',' ') ทั้งสองฝั่ง และ
   สำหรับ editor แบบ document (TextPattern) ให้ยอมรับเมื่อ read-back "ลงท้ายด้วย/มีเท่ากับ"
   ข้อความที่พิมพ์ (คำนวณจาก normalized equality) แทน Ordinal exact match
   - ใช้ helper เดียวร่วมกันทั้ง ValuePattern path และ fallback path
2. เพิ่ม RichEdit/TextPattern-aware path: เมื่อไม่มี ValuePattern (IsReadOnly หรือ pattern หาย)
   แต่มี TextPattern → ถือเป็น editable document control, ใช้ focus+select-all+type แล้ว verify
   ด้วย TextPattern.GetText (normalized) — อย่าคืน fail เพียงเพราะไม่มี ValuePattern
3. เพิ่มความนิ่งของ foreground/focus ก่อนพิมพ์: หลัง ForceForegroundWindow ให้ยืนยันว่า
   GetForegroundWindow()==hWnd จริง (retry สั้น ๆ เช่น 3 ครั้ง x 80ms) ก่อน TypeText;
   ถ้ายังไม่ foreground ให้คืน error ที่ retryable=true (เพื่อให้ durable runtime ลองใหม่หลัง
   fresh observation) แทนการพิมพ์ทิ้งลงหน้าต่างผิด
4. ตรวจ ReadScreenElementsTool / UiaWalker ว่าจับ control แก้ไขได้ของ Win11 Notepad
   (ControlType.Document/Edit ที่มี TextPattern) เป็น element ที่ set ได้ — ถ้า element_id 0
   ไม่ใช่ตัวแก้ไข ให้ผู้แก้ตรวจว่า snapshot จัดลำดับ/ทำเครื่องหมาย editable ถูก

ข้อห้าม: อย่าอ่อนข้อ credential block (LooksLikeCredential) และ WrongWindow/PID check —
การ verify ต้องยังผูกกับ control เดิม (same hwnd/pid) ห้าม verify แบบหลวมข้ามหน้าต่าง

Acceptance (tests อยู่ในโปรเจกต์ net8.0-windows SranDesktop.Tests):
- unit test: normalized read-back comparison → "hello" กับ "hello\r\n" ถือว่า match
- unit test: control ที่มีแค่ TextPattern (ไม่มี ValuePattern) → set ได้และ verify ผ่าน
- unit test: foreground ยืนยันไม่ได้ → คืน error retryable=true (ไม่พิมพ์ทิ้ง)
- build + test เขียวทั้งหมด
- (manual note ในรายงาน) ต้องลองจริงกับ Windows 11 Notepad หลัง publish
```

---

## PROMPT R1 — ไม่ให้ task ยอมแพ้เมื่อโมเดลหยุดส่ง tool call ทั้งที่ยังไม่จบ

**ก่อนแก้ (ปัจจุบัน):** `SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs`
ใน `RunAsync` เมื่อ `pendingToolCalls.Count == 0` (โมเดลไม่เรียก tool แล้ว) → เรียก
`EvaluateCompletionAsync` แล้ว "จบ turn ทันที" ไม่ว่าจะ complete หรือไม่ → ถ้าโมเดล (เช่น GPT-4o)
เจอ action fail ไม่กี่ครั้งแล้ว "เลิกเรียก tool" task จะถูกปิดด้วยสถานะ not-complete
โดยไม่พยายามต่อ (เทสจริง: GPT-4o เปิดแอปได้ พิมพ์ fail 1 ครั้ง แล้วหยุดเลย)

**หลังแก้ (เป้าหมาย):** เมื่อโมเดลหยุดแต่ durable task ยัง **not-complete และ recoverable**
และยังมี budget เหลือ → ป้อน nudge กลับเข้า loop ให้โมเดลลองวิธีอื่น/แก้แผน แทนที่จะจบทันที
(มีเพดานกันวนไม่รู้จบ) → task พยายามฟื้นต่อเหมือน Claude desktop ไม่ทิ้งกลางคัน

```
งาน:
1. ในบล็อก pendingToolCalls.Count == 0 (ที่มี durableContext != null):
   หลัง EvaluateCompletionAsync ถ้า completion.Completed == false และ
   - task ยัง executable (ไม่ Failed/Cancelled/Completed)
   - ยังมี action/retry budget เหลือ
   - จำนวน "no-tool-call nudge" ติดต่อกันยังไม่เกินเพดาน (เช่น const MaxRecoveryNudges = 3)
   → แทนที่จะ yield done ให้ append system message สั้น ๆ เข้า messages เช่น:
     "The task is NOT complete: <completion.Message>. Step '<failed step>' failed with
      '<failure_code>'. Do not stop. Try a DIFFERENT capability or approach, refresh state
      with an observation first, or call update_plan. Continue until the Definition of Done
      is verified or all safe options are exhausted."
     แล้ว `continue;` ให้ loop รันรอบใหม่ (นับ nudge +1)
   - รีเซ็ตตัวนับ nudge เป็น 0 ทุกครั้งที่มี tool call เกิดขึ้นจริงในรอบถัดไป
2. ถ้าเกินเพดาน nudge หรือหมด budget → จบแบบ "paused/incomplete" ตามเดิม (ยังไม่เคลมสำเร็จ)
   และคง TaskId ไว้ให้ resume ได้
3. ใช้ AgentVerificationModeEvent/สถานะที่มีอยู่แล้ว surface ให้ UI เห็นว่า "กำลังพยายามฟื้น
   (recovery attempt n/‌3)" — ผู้ใช้จะได้รู้ว่าไม่ได้ค้าง

ข้อห้าม: ห้ามแตะสัญญา "ไม่เคลมสำเร็จจนกว่า DoD ผ่าน" — nudge นี้แค่กระตุ้นให้ลองต่อ
ไม่ใช่ทำให้ผ่านง่ายขึ้น; เพดาน nudge ต้องมีจริงเพื่อกันวนไม่รู้จบและกัน token ไหม้

Acceptance:
- unit test: durable task not-complete + มี budget + failed step → orchestrator ป้อน nudge
  แล้ววน loop ต่อ (ไม่ yield done) จนกว่าจะจบหรือเกินเพดาน
- unit test: เกิน MaxRecoveryNudges → จบแบบ incomplete/paused มี TaskId (ไม่วนต่อ)
- unit test เดิมเรื่อง false-success ยังผ่าน (ไม่เคลมสำเร็จเกินจริง)
- build + test เขียว
```

---

**ลำดับ:** T1 ก่อน (แก้คอขวดที่เห็นซ้ำทุกโมเดล) → R1 (ให้ฟื้นต่อแทนยอมแพ้)
**ทุก prompt:** build+test เขียวก่อนถือว่าเสร็จ, ห้าม claim สำเร็จถ้า test ไม่ผ่าน, รายงาน before/after ต่อไฟล์
**หลังแก้ + publish:** ผมจะรันเทสจริงซ้ำ (เปิด Notepad+พิมพ์+เซฟ) ด้วย GPT-4o และ mini —
คาดหวัง "Fill in field" เขียว, DoD ครบ 3 ข้อ, และรายงานสำเร็จพร้อม evidence จริง
