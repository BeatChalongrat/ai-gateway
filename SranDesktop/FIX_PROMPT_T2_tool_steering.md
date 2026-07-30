# Fix Prompt — T2: Tool-selection steering + anti-churn (ให้ T1 ได้ใช้จริง)

**บริบทจาก live test:** หลังแก้ deadlock (กลุ่ม A) แล้ว การพิมพ์ลง Windows 11 Notepad ยังล้มเหลว
ไม่ใช่เพราะ T1 ผิด แต่เพราะ **โมเดลเลือก tool ผิด** — mini เรียก `computer_type` ("Type text") และ
clipboard (Ctrl+A/Ctrl+V) วนซ้ำ ไม่เคยเรียก `uia_set_value` (ตัวที่ T1 แก้และ verify ได้) →
พิมพ์ไม่ผ่านสักที และ R1 (recovery) กลับทำให้ "วนยาว" เพราะโมเดลลองวิธีเดิมที่ fail ซ้ำ ๆ

**เป้าหมาย T2:** บังคับ/ชี้นำให้พิมพ์ลง native control ผ่าน `uia_set_value` ก่อนเสมอ และห้ามวน
capability ที่ fail ซ้ำในสเต็ปเดิม → ทำให้ T1 ได้ทำงานจริง และ R1 "สลับวิธี" แทน "วนที่เดิม"

วิธีใช้: วางใน Claude Code (Opus) ที่ root repo, build+test เขียวก่อนถือว่าเสร็จ
`dotnet build SranDesktop/SranDesktop.sln` / `dotnet test SranDesktop/SranDesktop.sln`

---

## PROMPT T2 (3 ส่วนที่เกี่ยวเนื่องกัน)

### ส่วน A — เพิ่ม TypingGuidance เข้า system prompt (steer หลัก)

```
ไฟล์: SranDesktop/src/SranDesktop.Presentation/ViewModels/ChatViewModel.cs
ก่อน: มี FileSavingGuidance (A3) ฉีดเข้า system prompt ผ่าน {FileSavingGuidance} แต่ไม่มีคำแนะนำ
      เรื่อง "จะพิมพ์ข้อความลง control ของแอปยังไง" → โมเดลเดาเอง มักใช้ computer_type/clipboard ที่ verify ยาก

งาน:
1. เพิ่ม const ใหม่ TypingGuidance (สไตล์เดียวกับ FileSavingGuidance, ใกล้กัน, verifiable เดี่ยว ๆ) เนื้อหา:
   ## Typing into a desktop app — prefer uia_set_value (IMPORTANT)
   - To put text into a native app's editable control (Notepad, text boxes, editors):
     FIRST call read_screen_elements to get the control's element id, THEN call uia_set_value
     with that element_id + snapshot_id. This binds to the real control and verifies by read-back.
   - Do NOT use computer_type or clipboard paste (Ctrl+A/Ctrl+V) as the primary way to fill a control —
     they are unverifiable and weak models loop on them. Use computer_type ONLY when read_screen_elements
     finds no editable element (e.g. a canvas/game).
   - If a typing attempt fails, do NOT repeat the same tool — switch approach: re-observe with
     read_screen_elements and use uia_set_value on the correct element.
2. ฉีดเข้า system prompt ใน BuildGatewayMessages ต่อจาก {FileSavingGuidance} ด้วย {TypingGuidance}
```

### ส่วน B — RecommendedCapabilities ให้ desktop TextValue นำด้วย uia_set_value

```
ไฟล์: SranDesktop/src/SranDesktop.Application/Services/DurableTaskRuntime.cs
      เมธอด CapabilityRouter.RecommendedCapabilities (บรรทัด ~2148)
ก่อน: OutcomeKind.TextValue => ["browser_dom", "uia_set_value", "computer_type"]
      → นำด้วย browser_dom (ไม่เกี่ยวกับงาน desktop) และมี computer_type ปนอันดับต้น
หลัง: แยก desktop vs browser (มี helper IsBrowserOutcome อยู่แล้ว):
      - TextValue ที่เป็น browser  => ["browser_dom", "uia_set_value"]
      - TextValue ที่เป็น desktop => ["uia_set_value", "computer_type"]  (uia_set_value นำเสมอ,
        computer_type เป็น fallback ท้าย)
```

### ส่วน C — anti-churn: ห้ามวน capability ที่ fail ซ้ำในสเต็ปเดิม

```
ไฟล์: SranDesktop/src/SranDesktop.Application/Services/DurableTaskRuntime.cs (PrepareActionAsync)
ก่อน: BlindRetryBlocked กันเฉพาะ "action เดิมเป๊ะ" (tool+args เดิม + ไม่มี fresh observation) แต่
      โมเดลวนได้เรื่อย ๆ ถ้าสลับ args นิดหน่อยหรือมี observation คั่น — live test เห็น computer_type
      + clipboard fail ซ้ำหลายรอบจน step 44/100 (R1 เลี้ยงให้วน)
หลัง: เพิ่มกติกา "capability ที่ fail แล้วต้องเปลี่ยน":
      - ติดตามจำนวนครั้งที่แต่ละ capability (toolName) ล้มเหลวใน step ปัจจุบัน (จาก action history ของ step)
      - ถ้า capability หนึ่งล้มเหลวถึงเพดาน (เช่น 2 ครั้ง) ในสเต็ปเดียวกัน → Block การ dispatch
        capability นั้นซ้ำสำหรับ step นี้ ด้วย FailureCode ใหม่ เช่น "CapabilityExhaustedForStep"
        พร้อม reason ชี้นำ capability ที่แนะนำถัดไป (จาก RecommendedCapabilities ของ outcome —
        เช่น "computer_type ล้มเหลวซ้ำ; ใช้ read_screen_elements + uia_set_value แทน")
      - ต้องยอมให้ capability อื่น (โดยเฉพาะ observation + uia_set_value) ยัง dispatch ได้ตามปกติ
      - reason นี้จะไหลกลับเป็น tool result → รวมกับ R1 nudge ทำให้โมเดล "สลับวิธี" ไม่ใช่ "วนเดิม"

ข้อห้าม (ทั้ง 3 ส่วน):
- อย่าปิด computer_type/clipboard ทิ้ง (ยังจำเป็นกับ canvas/เกม/เคสไม่มี UIA element)
- อย่าแตะสัญญา false-success / การ verify — steering คือ "จัดลำดับ + กันวน" ไม่ใช่ทำให้ผ่านง่ายขึ้น
- อย่าให้ anti-churn ไปบล็อก observation tools หรือ uia_set_value
```

**Acceptance:**
- unit test: RecommendedCapabilities — desktop TextValue → uia_set_value เป็นตัวแรก; browser TextValue → browser_dom เป็นตัวแรก
- unit test: system prompt ที่ build แล้วมีสตริง TypingGuidance (และยังมี FileSavingGuidance)
- unit test: anti-churn — computer_type ล้มเหลว 2 ครั้งใน step เดียว → dispatch computer_type ครั้งที่ 3
  ถูก Block ("CapabilityExhaustedForStep") ขณะที่ uia_set_value / read_screen_elements ยัง dispatch ได้
- unit test เดิมเรื่อง false-success + BlindRetryBlocked ยังผ่าน
- build + test เขียวทั้งหมด

**หลังแก้ + publish:** ผมจะรันเทสจริง (เปิด Notepad + พิมพ์) ด้วย mini และ GPT-4o —
คาดหวัง: โมเดลเรียก read_screen_elements → uia_set_value, "Fill in field" เขียว, DoD ข้อ 2 verify,
และไม่วนยาว (สลับวิธีเมื่อ computer_type fail)

**ถัดจากนี้:** เหลือ Group B (clarification ที่ตันตอนโจทย์ซับซ้อน) เป็นด่านสุดท้ายของชุดนี้
