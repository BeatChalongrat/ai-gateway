# Prompt — rebuild + publish + เปิดแอป

วางใน Claude Code ที่ root repo

```
Rebuild แล้วเปิดแอปให้ผมที ทำตามลำดับนี้ อย่าข้ามขั้น

1. ปิดทุกอย่างที่ล็อกไฟล์อยู่
   - SranDesktop.exe
   - SranDesktop.BrowserHost.exe  ← ตัวนี้ Chrome จะ respawn ให้ใหม่ระหว่าง publish
     ถ้ามันโผล่กลับมาแล้ว publish ล้ม ให้ sweep ซ้ำแล้วลองใหม่
   ยืนยันว่าไม่เหลือ process ก่อนไปขั้นถัดไป

2. dotnet build SranDesktop/SranDesktop.sln -c Debug
   ต้อง 0 errors ถ้าแดงหยุดแล้วรายงาน อย่า publish ทับของเดิมที่ยังใช้ได้

3. dotnet publish ทับ SranDesktop/publish/win-x64
   เสร็จแล้วเช็ค LastWriteTime ของ publish/win-x64/SranDesktop.exe
   ต้องเป็นเวลาปัจจุบัน ไม่ใช่ของเก่า — ถ้าไม่ขยับแปลว่า publish ไม่ได้เขียนจริง

4. เปิดแอปจาก publish/win-x64/SranDesktop.exe

5. รายงานกลับ 3 อย่าง
   - build id ที่มุมขวาล่างเขียนว่าอะไร
   - LastWriteTime ของ exe
   - git status --porcelain | wc -l  (ต้องไม่ลดลงจาก 363)

ข้อห้ามเดิมยังอยู่: ห้าม checkout / reset / clean / stash / switch / restore
ยังไม่ commit ผิดครั้งเดียวคืองาน 11 วัน
```

---

## หรือรันเองก็ได้ (PowerShell)

```powershell
cd "C:\Users\Beat Beat\Desktop\ai-gateway"

taskkill /IM SranDesktop.exe /F /T 2>$null
taskkill /IM SranDesktop.BrowserHost.exe /F /T 2>$null
Start-Sleep 2

dotnet build SranDesktop\SranDesktop.sln -c Debug
if ($LASTEXITCODE -ne 0) { Write-Host "BUILD FAILED - หยุด" -ForegroundColor Red; return }

dotnet publish SranDesktop\src\SranDesktop.App\SranDesktop.App.csproj `
    -c Debug -r win-x64 --self-contained false `
    -o SranDesktop\publish\win-x64

$exe = "SranDesktop\publish\win-x64\SranDesktop.exe"
(Get-Item $exe).LastWriteTime      # ต้องเป็นเวลาปัจจุบัน
Start-Process $exe
```

ถ้า publish ล้มเพราะไฟล์ถูกล็อก แปลว่า BrowserHost respawn กลับมา — sweep แล้วรันซ้ำ
