# Code-signing the SRAN Desktop installer (FIX-E)

> ขอบเขตปัจจุบัน 2026-11-12: ขั้นตอน Inno Setup ด้านล่างเป็นของ WPF เดิม
> แอปที่ใช้งานปัจจุบันสร้างจาก `apps/desktop` ด้วย Electron packaging
> สถานะ certificate ด้านล่างเป็นบันทึกเดิม ไม่ได้ตรวจคลัง certificate ใหม่ในรอบนี้.

สถานะ: **ท่อพร้อมแล้ว แต่ยังไม่มีใบ certificate** — ระหว่างนี้ build แบบไม่เซ็นได้ตามปกติ
(`ISCC.exe build\SranDesktop.iss` เฉย ๆ = installer ไม่เซ็น เหมือนเดิมทุกอย่าง)

## 1. ต้องซื้อ cert แบบไหน

| แบบ | ผลกับ SmartScreen | หมายเหตุ |
|---|---|---|
| **OV** (Organization Validation) | ยังเจอ "Unknown publisher" ช่วงแรก จนชื่อสะสม reputation (มักไม่กี่สัปดาห์ถึงเดือน) | ถูกกว่า, ออกเร็ว |
| **EV** (Extended Validation) | ไม่รับประกันการข้าม SmartScreen ทันที เช่นเดียวกับ OV | ตรวจข้อกำหนดการเก็บกุญแจและบริการเซ็นกับผู้ออกใบรับรอง |

อย่าเลือก EV โดยตั้งสมมติฐานว่าจะไม่มีคำเตือน SmartScreen ตั้งแต่วันแรก
Microsoft ระบุว่าพฤติกรรมให้ reputation ทันทีแบบเดิมไม่มีแล้ว:
[SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

> cert แบบไฟล์ .pfx ใช้กับท่อนี้ได้ตรง ๆ; ถ้าเป็น EV token/HSM ให้แก้พารามิเตอร์ signtool
> จาก `/f ... /p ...` เป็นของ provider (เช่น `/csp`/`/kc` หรือ `signtool sign /sha1 <thumbprint>`)
> — โครง `#ifdef SIGN` ใน .iss ไม่ต้องแก้

## 2. ขั้นตอน build แบบเซ็น (เมื่อมี cert แล้ว)

ตั้ง env ในเชลล์ build เท่านั้น (ห้าม commit ค่าเหล่านี้ลง repo เด็ดขาด):

```powershell
$env:SRAN_CERT_PFX      = "D:\secure\sran-codesign.pfx"   # เก็บนอก repo
$env:SRAN_CERT_PASSWORD = "..."
```

จากนั้นตามลำดับ:

```powershell
# 1) publish ตามปกติ
dotnet publish src/SranDesktop.App/SranDesktop.App.csproj -c Release -p:PublishProfile=win-x64

# 2) เซ็น payload exes (SranDesktop.exe + BrowserHost.exe) — no-op ถ้า env ไม่ตั้ง
powershell -File build\sign-publish.ps1

# 3) compile installer แบบเซ็น (Setup.exe + uninstaller)
ISCC.exe /DSIGN `
  "/Ssran=signtool.exe sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f $env:SRAN_CERT_PFX /p $env:SRAN_CERT_PASSWORD `$f" `
  build\SranDesktop.iss
```

- ไม่ใส่ `/DSIGN` = build ไม่เซ็น (พฤติกรรมเดิม 100% — CI/dev ไม่พัง)
- `/Ssran=...` คือการนิยาม "Sign Tool" ชื่อ `sran` ที่ [Setup] `SignTool=sran` อ้างถึง
  (ใช้ Inno IDE ก็ได้: Tools → Configure Sign Tools → ชื่อ `sran` คำสั่งเดียวกัน)
- `$f` คือ placeholder ของ Inno แทน path ไฟล์ที่จะเซ็น

## 3. เช็คผล

```powershell
Get-AuthenticodeSignature installer\SranDesktop-1.0.0-Setup.exe   # Status ต้องเป็น Valid
Get-AuthenticodeSignature publish\win-x64\SranDesktop.exe
```

ทดสอบบนเครื่อง/VM ที่ไม่เคยติดตั้ง: ดับเบิลคลิก Setup ต้องไม่เจอ "Windows protected your PC"
(OV ช่วงสะสมชื่ออาจยังเจอ — เป็นเรื่อง reputation ไม่ใช่ config ผิด)
