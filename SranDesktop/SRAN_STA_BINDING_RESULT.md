# SRAN Desktop — STA Binding Test Result

**สถานะ:** ✅ เสร็จ — `dotnet build` ผ่าน, `dotnet test` เขียวทั้งหมด (main **1259** + STA **4**, 0 fail, ยืนยันเสถียร 2 รอบ)
**เป้าหมาย:** ปิดช่องว่างข้อ 1 ของ gate รอบ 1 — เทสตอนนี้ยืนยัน **binding บนการ์ดจริง** ไม่ใช่แค่ VM state ถ้าลบ `{Binding HumanError}` → เทส **แดง**

---

## ⚠️ สิ่งที่พบระหว่างทำ + วิธีแก้ (สำคัญ — ต้องรีวิว)

**ปัญหา:** การโฮสต์ `MessageBubble` จริงต้องมี `System.Windows.Application.Current` (StaticResource สาย main-tree เช่น `Geo_ChevronLeft` บรรทัด 159, `ActionIconButton BasedOn IconButton` บรรทัด 179 ถูก resolve ตอน construct) — **แต่** การสร้าง `Application.Current` เป็น side effect ระดับโปรเซส: production หลายจุดใช้ `Application.Current?.Dispatcher.Invoke(...)` (`ChatViewModel.cs:633,660,953,2303`) ที่เป็น no-op เมื่อ `Current==null` (สภาพในเทสปกติ) แต่จะ **marshal ข้าม thread** เมื่อมี Application → **ทำ `ChatViewModelServerReadTests` พังทันที** (พิสูจน์แล้ว: รันเดี่ยวผ่าน แต่รันรวมกับ STA fixture พัง 2 เคส)

**วิธีแก้ (สะอาด):** แยก STA test ไปไว้ใน **assembly ใหม่ `SranDesktop.UiTests`** — `dotnet test` รันแต่ละ test assembly **คนละ process** ดังนั้น `Application.Current` ในโปรเซส UiTests **ไม่แตะ** โปรเซส `SranDesktop.Tests` เลย → main suite กลับมา 1259 เขียวสนิท ไม่ต้อง reflection hack / ไม่ต้องปิด parallelization

> ทางเลือกที่ **ไม่** เลือก (เพราะเปราะ/รุกล้ำ): สร้าง Application ใน SranDesktop.Tests แล้ว reflection-null `Application.Current` + `[assembly: DisableTestParallelization]` — รุกล้ำทั้ง suite ของทีม จึงเลี่ยง

---

## ไฟล์ที่เพิ่ม / แก้

| ไฟล์ | ชนิด | สาระ |
|---|---|---|
| `src/SranDesktop.UiTests/SranDesktop.UiTests.csproj` | **project ใหม่** | net8.0-windows, UseWPF, ref Presentation+Application, xunit+FluentAssertions |
| `src/SranDesktop.UiTests/MessageBubbleBindingTests.cs` | test ใหม่ | 4 STA cases + STA fixture |
| `SranDesktop.sln` | แก้ | เพิ่ม project ใหม่ |
| `src/SranDesktop.Presentation/Controls/MessageBubble.xaml` | **production (x:Name seam)** | เพิ่ม `x:Name="ToolExecutionsList"` — ดู diff ด้านล่าง |

### diff ของ x:Name seam (ให้รีวิว — production change เดียว)
```xml
-            <!-- Inline tool activity: one collapsible row per tool call (Claude-style) -->
-            <ItemsControl ItemsSource="{Binding ToolExecutions}" Margin="0,0,0,4">
+            <!-- Inline tool activity: one collapsible row per tool call (Claude-style).
+                 x:Name is a test seam: the STA binding gate (MessageBubbleBindingTests)
+                 locates this ItemsControl to extract its real ItemTemplate. -->
+            <ItemsControl x:Name="ToolExecutionsList"
+                          ItemsSource="{Binding ToolExecutions}" Margin="0,0,0,4">
```
เปลี่ยนแค่ชื่อ element เดียว ไม่แตะ logic/layout อื่น

---

## เทสทำอะไร (4 cases)

โฮสต์ **ItemTemplate จริง** ของ `ToolExecutionsList` จาก `MessageBubble.xaml` (ดึงจาก `new MessageBubble()` → `FindName` → `.ItemTemplate`) มา apply กับ `ToolExecutionItemViewModel` ผ่าน `ContentControl` แล้วเดิน visual tree

| เทส | สถานะการ์ด | assert |
|---|---|---|
| `Failed_Card_Shows_Localized_HumanError_And_Fail_Glyph` | failed | มี TextBlock `.Text == vm.HumanError` **visible** (regression guard); fail glyph (Geo_Close) visible; verifying label ไม่ visible |
| `VerificationPending_Card_Shows_Amber_Verifying_Label_Only` | verification_pending | verifying label (`loc:T tool.verifying`) visible; fail glyph ไม่ visible; `HumanError==null` |
| `Verified_Card_Shows_Check_Not_Amber_Or_Error` | verified | check glyph (Geo_Check) visible; Geo_Close + verifying ไม่ visible |
| `Expanded_Failed_Card_Exposes_Raw_Code_In_Output` | failed + `IsExpanded=true` | TextBlock ที่ผูก `FullResult` (มี "FocusLost") visible → progressive disclosure ไม่หาย |

**element ที่ assert:** `TextBlock` (HumanError / verifying label / FullResult) + `IconView` (Geo_Close / Geo_Check) — เทียบ **ข้อความ** (ผ่าน `LocaleService.T(key)` ไม่ใช่ literal) และ **effective visibility** ไม่ใช่สี pixel

### ✅ พิสูจน์ว่า guard ทำงานจริง (mutation test)
เปลี่ยน `Text="{Binding HumanError}"` → `Text="{Binding BindingRemovedForMutationTest}"` ชั่วคราว → `Failed_Card_...` **แดงทันที** (Failed: 1) แล้ว revert กลับเขียว → ตาข่ายกัน binding หลุดตอนรื้อรอบ 2 ใช้ได้จริง

---

## วิธีจัดการกับดัก WPF

1. **STA:** `StaWpfApplicationFixture` สร้าง dedicated STA thread (`SetApartmentState(STA)`) + `Dispatcher.Run`; ทุก test body รันผ่าน `_dispatcher.Invoke(...)` ให้ WPF object ทั้งหมดอยู่ thread เดียว
2. **Resource resolve:** merge dict เดียวกับ App.xaml (`Themes/Dark.xaml` + `Styles.xaml` + `Icons.xaml`) เข้า `Application.Resources` → DynamicResource brushes / `Geo_*` / `IconButton` resolve; การสร้าง `Application` ยัง register pack scheme ที่ URI ต้องใช้
3. **Template realization:** `ContentControl{ContentTemplate=template,Content=vm}` ใน `Border` แล้ว `Measure`/`Arrange`/`UpdateLayout` — **ไม่ต้อง `Show()`** (ทำงานในเมมโมรี, ไม่ต้องมี desktop session)
4. **Visibility:** in-memory tree ที่ไม่เคย Show ทำให้ `IsVisible==false` เสมอ → เทสใช้ **effective visibility** เดินขึ้น ancestor เช็ค `Visibility==Visible` แทน

---

## Build / Test / เวลา
```
dotnet build SranDesktop.sln -c Debug      → Build succeeded
dotnet test  SranDesktop.sln -c Debug
  SranDesktop.UiTests.dll  → Passed! Failed: 0, Passed: 4   (~0.6–2s)
  SranDesktop.Tests.dll    → Passed! Failed: 0, Passed: 1259 (~31–37s)
```
เสถียร 2 รอบติด (ไม่ flaky) — STA cases เป็น deterministic in-memory ไม่พึ่ง GUI session

---

## ข้อจำกัดที่เหลือ (โปร่งใส)
1. **ยังไม่ยืนยัน theme/สีจริง** — เทสเช็ค element/visibility/text ไม่เช็คว่า amber = สี #E8A23D จริง (ตามกติกา "ห้าม assert pixel")
2. **ยังไม่ยืนยัน animation** (entrance/pulse) และ **การสลับ theme runtime**
3. **verified state** ป้อนผ่าน `Complete(ok, requireVerification:false)` (เส้นทาง nested-child จริง) — ทดสอบ visual ของ green check ถูกต้อง แต่ไม่ได้ผ่าน durable `UnifiedResult.Verified` (ต้องประกอบ AgentToolResult 20+ field — เลี่ยงเพราะไม่จำเป็นต่อ visual)
4. **CI:** เทสรันในเมมโมรี ไม่ต้องมี desktop session แต่ต้องรันบน **Windows** (net8.0-windows + STA) — GitHub `windows-latest` / self-hosted Windows รันได้

**หยุดรอรีวิว** — โดยเฉพาะ x:Name seam + การเพิ่ม project `SranDesktop.UiTests` ก่อนเริ่มรอบ 2 (UI retrying state + grouping + R2/R4)
