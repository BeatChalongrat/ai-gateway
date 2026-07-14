# Prompt: Run SRAN Desktop & Keep It Open for Testing

> ให้ AI build และ run SRAN Desktop แล้วเปิดทิ้งไว้ให้ user เล่นดู

---

## Task

### 1. Build & Run

```powershell
# ถ้า dotnet อยู่ใน PATH:
cd "C:\Users\Beat Beat\Desktop\ai-gateway\SranDesktop\src\SranDesktop.App"
dotnet run

# ถ้าไม่อยู่ใน PATH:
cd "C:\Users\Beat Beat\Desktop\ai-gateway\SranDesktop\src\SranDesktop.App"
& "$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe" run
```

ถ้า build error ให้แก้แล้ว report

### 2. Keep Running

**อย่าปิด app** — เปิดทิ้งไว้ให้ user ลองเล่นเอง

### 3. Screenshot หน้าจอที่เห็น

หลัง app ขึ้นมา ให้ screenshot หน้าจอแรกที่เห็น (น่าจะเป็น login screen) แล้ว report ว่า:
- App รันสำเร็จหรือไม่
- หน้าจอแรกคืออะไร
- มี error/warning อะไรใน console หรือไม่

### 4. ถ้า User ขอให้ทำอะไรเพิ่ม

User อาจจะขอให้:
- กด login
- พิมพ์ข้อความ
- สลับ theme
- เปิด settings
- toggle agent mode
- screenshot state ต่างๆ

ให้ทำตามที่ user บอก

## หมายเหตุ

- App จะพยายาม connect ไปที่ backend (chat-edge :4000, gateway :8080) ถ้า backend ไม่ได้รันอยู่ app จะยังเปิดได้แต่ chat จะใช้ไม่ได้ — ไม่เป็นไร แค่ดู UI ก็พอ
- ถ้า app crash ทันทีหลังเปิด ให้ดู error message แล้ว report
- ถ้า login ต้องการ credentials ที่ไม่มี ให้ report ว่า "ต้อง login ก่อน" แล้วรอ user ให้ credentials

---

*End of prompt*
