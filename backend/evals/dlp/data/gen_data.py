"""FIX28 Part 1 — generate the must-catch sets. EVERYTHING HERE IS SYNTHETIC.

Names, numbers, cards, ids, accounts and keys are invented (deterministic
seed) — card numbers and Thai national ids carry a valid checksum but belong
to nobody; keys are random strings in the real-world SHAPES users paste
(`sk-proj-…`, `AKIA…`, `ghp_…`, JWT, `xoxb-…`, Bearer). Values are embedded in
natural Thai chat sentences — bare strings on a line are not what users type.

Run:  python -m evals.dlp.data.gen_data   (rewrites data/must_catch/*.json)

The items are NOT tuned to the detectors: formats a Thai user actually types
are included whether or not the current regex handles them. A low score is
the finding, not a data bug.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "must_catch"
rng = random.Random(28)


def digits(n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def alnum(n: int, extra: str = "") -> str:
    return "".join(rng.choice(string.ascii_letters + string.digits + extra) for _ in range(n))


def luhn_complete(prefix: str, length: int) -> str:
    body = prefix + digits(length - len(prefix) - 1)
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


def thai_nid() -> str:
    """13 digits, first digit 1-8, mod-11 check digit."""
    body = rng.choice("12345678") + digits(11)
    s = sum(int(body[i]) * (13 - i) for i in range(12))
    return body + str((11 - s % 11) % 10)


def fmt_nid(n: str, style: str) -> str:
    if style == "dashed":
        return f"{n[0]}-{n[1:5]}-{n[5:10]}-{n[10:12]}-{n[12]}"
    if style == "spaced":
        return f"{n[0]} {n[1:5]} {n[5:10]} {n[10:12]} {n[12]}"
    return n


def fmt_card(n: str, style: str) -> str:
    g = [n[i : i + 4] for i in range(0, len(n), 4)]
    if style == "spaced":
        return " ".join(g)
    if style == "dashed":
        return "-".join(g)
    return n


def mobile(style: str) -> str:
    n = "0" + rng.choice("689") + digits(8)
    if style == "dashed":
        return f"{n[:3]}-{n[3:6]}-{n[6:]}"
    if style == "spaced":
        return f"{n[:3]} {n[3:6]} {n[6:]}"
    if style == "intl":
        return f"+66 {n[1:3]} {n[3:6]} {n[6:]}"
    if style == "intl_bare":
        return "+66" + n[1:]
    if style == "spaced2":
        return f"{n[:2]} {n[2:6]} {n[6:]}"
    return n


def landline(style: str) -> str:
    n = "0" + rng.choice("2345") + digits(7)
    if style == "dashed":
        return f"{n[:2]}-{n[2:5]}-{n[5:]}"
    if style == "paren":
        return f"({n[:2]}) {n[2:5]} {n[5:]}"
    if style == "spaced":
        return f"{n[:2]} {n[2:5]} {n[5:]}"
    return n


def bank_acct(style: str) -> str:
    n = digits(10)
    if style == "dashed":
        return f"{n[:3]}-{n[3]}-{n[4:9]}-{n[9]}"
    if style == "dashed2":
        return f"{n[:3]}-{n[3:9]}-{n[9]}"
    if style == "spaced":
        return f"{n[:3]} {n[3]} {n[4:9]} {n[9]}"
    return n


def email() -> str:
    first = rng.choice(["somchai", "pim", "nattapong", "kanya", "arthit", "ploy", "wichai", "naree", "tanawat", "supaporn"])
    last = rng.choice(["j", "k", "s", "w", "p", "t", "r"]) + rng.choice(["", digits(2), "." + rng.choice(["work", "dev", "hr"])])
    dom = rng.choice(["example.co.th", "example.com", "mail.example.org", "example.ac.th", "example.in.th", "example-corp.co.th"])
    return f"{first}.{last}@{dom}"


# ── sentence frames (Thai chat, mixed registers) ───────────────────────────

PHONE_FRAMES = [
    "ติดต่อกลับได้ที่ {v} นะครับ",
    "เบอร์ลูกค้าคือ {v} โทรไปยืนยันนัดด้วย",
    "โทร {v} ถ้าไม่ติดฝากข้อความไว้",
    "ส่งของแล้ว รบกวนโทรแจ้งคุณเอที่ {v}",
    "เบอร์ใหม่ของฉัน {v} จดไว้ด้วยนะ",
    "ช่วยเพิ่ม {v} ในรายชื่อผู้ติดต่อฉุกเฉินหน่อย",
    "ลูกค้าเปลี่ยนเบอร์เป็น {v} แล้ว อัปเดตในระบบด้วย",
    "โทรศัพท์ที่ทำงาน {v} ต่อ 204",
    "มือถือ {v} ไลน์ไอดีเดียวกัน",
    "ถ้ามีปัญหาการจัดส่งโทร {v} ได้เลย",
    "เบอร์ที่ให้ไว้ตอนสมัครคือ {v} ถูกไหม",
    "แจ้งเบอร์คนรับของ: {v} ชื่อคุณบี",
    "ขอเบอร์ร้านหน่อย ร้านนี้ {v} ใช่ไหม",
    "รบกวนโทรกลับ {v} ก่อนห้าโมงเย็น",
    "ติดต่อฝ่ายบัญชีได้ที่ {v} วันจันทร์-ศุกร์",
]
CARD_FRAMES = [
    "ช่วยตัดบัตร {v} หมดอายุ 09/28 ให้หน่อย",
    "บัตรเครดิตที่ผูกไว้คือ {v} ใช้ใบนี้จ่าย",
    "เลขบัตร {v} ชื่อบนบัตร SOMCHAI J",
    "ลูกค้าให้เลขบัตรมาเป็น {v} รบกวนเช็กว่าผ่านไหม",
    "ตอนสมัครใส่บัตร {v} ไปแล้ว ขอเปลี่ยนใบใหม่",
    "จ่ายด้วย {v} cvv 123 ได้เลย",
    "ขอยกเลิกรายการที่ตัดจากบัตร {v} เมื่อวานด้วยครับ",
    "บัตรของบริษัท {v} ใช้จองโรงแรมได้ไหม",
    "เลขหน้าบัตรคือ {v} ส่วนหลังบัตรไม่ต้อง",
    "ผมเผลอส่งเลขบัตร {v} ไปในกลุ่มไลน์ ทำยังไงดี",
]
EMAIL_FRAMES = [
    "ส่งใบเสนอราคาไปที่ {v} ด้วยนะ",
    "อีเมลลูกค้าคือ {v} cc หัวหน้าด้วย",
    "สมัครด้วยเมล {v} แต่ยังไม่ได้รับรหัสยืนยัน",
    "ติดต่อ HR ได้ที่ {v}",
    "เปลี่ยนอีเมลติดต่อเป็น {v} ให้หน่อย",
    "ผู้รับ: {v} หัวข้อ: สรุปงานสัปดาห์นี้",
    "แนบไฟล์แล้วส่งไป {v} ตอนบ่าย",
    "ทำไมเมล {v} ถึงเด้งกลับ",
    "ลงทะเบียนด้วย {v} แล้วครับ",
    "อีเมลสำรองของฉัน {v} ใช้อันนี้แทน",
]
NID_FRAMES = [
    "เลขบัตรประชาชน {v} ชื่อสมชาย",
    "เลข ปชช. {v} ใช้ยืนยันตัวตนตอนเปิดบัญชี",
    "กรอกเลขบัตร {v} ในฟอร์มแล้ว รอตรวจสอบ",
    "ลูกค้ารายนี้บัตรประชาชนเลขที่ {v} เกิด 12 มี.ค. 2530",
    "รบกวนเช็กว่าเลขประจำตัว {v} ตรงกับในระบบไหม",
    "ส่งสำเนาบัตรมาแล้ว เลข {v} หมดอายุปีหน้า",
    "ID: {v} ขอเปิดใช้บริการ",
    "เลขบัตรที่ให้ไว้ {v} พิมพ์ผิดหนึ่งหลักหรือเปล่า",
    "ยืนยันตัวตนด้วยเลขบัตร {v} เรียบร้อย",
    "ผู้เสียหายเลขบัตรประชาชน {v} แจ้งความไว้แล้ว",
]
BANK_FRAMES = [
    "โอนเข้าบัญชีกสิกร {v} ชื่อบัญชี สมหญิง",
    "เลขบัญชี {v} ธนาคารไทยพาณิชย์ สาขาสีลม",
    "โอนมาที่ {v} กรุงไทย แล้วส่งสลิปด้วย",
    "บัญชีเงินเดือนของพนักงานคือ {v} กรุงเทพ",
    "ช่วยเช็กยอดในบัญชี {v} ให้หน่อย",
    "คืนเงินเข้าบัญชีเดิม {v} ภายใน 7 วัน",
    "เลขที่บัญชีออมทรัพย์ {v} ธ.กรุงศรี",
    "แจ้งเปลี่ยนบัญชีรับเงินเป็น {v} ttb",
    "โอนแล้วนะ เข้าบัญชี {v} เช็กด้วย",
    "บัญชีร้านค้า {v} รับโอนได้ทุกธนาคาร",
]
KEY_FRAMES = [
    "คีย์ที่ใช้คือ {v} ลองยิงดูอีกที",
    "ตั้งค่า env แบบนี้ถูกไหม OPENAI_API_KEY={v}",
    "ผมได้ token มาเป็น {v} เอาไปใส่ตรงไหน",
    "header ต้องเป็น Authorization: {v} ใช่ไหม",
    "อันนี้คีย์ของ prod นะ {v} อย่าแชร์ต่อ",
    "ใน config มี api_key = \"{v}\" ต้องเปลี่ยนไหม",
    "รหัสเข้าระบบที่ได้มา {v} ใช้ได้ถึงเที่ยงคืน",
    "GitHub token: {v} สำหรับ CI",
    "คีย์นี้ {v} หมดอายุหรือยัง",
    "log ขึ้นว่า invalid key {v} ช่วยดูหน่อย",
]


def make_phone() -> list[dict]:
    items = []
    styles = ["dashed", "bare", "spaced", "intl", "intl_bare", "spaced2", "dashed", "bare", "dashed", "spaced", "intl", "bare", "dashed", "bare", "dashed"]
    for i, (frame, st) in enumerate(zip(PHONE_FRAMES, styles)):
        v = mobile(st)
        items.append({"id": f"ph{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": f"mobile/{st}"})
    for j, (frame, st) in enumerate(zip(PHONE_FRAMES[::-1][:8], ["dashed", "bare", "paren", "spaced", "dashed", "bare", "paren", "dashed"])):
        v = landline(st)
        items.append({"id": f"ph{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": f"landline/{st}"})
    # glued to Thai text, no keyword — a very common chat shape
    for v in (mobile("bare"), mobile("dashed")):
        items.append({"id": f"ph{len(items)+1:02d}", "text": f"ทักมาได้เลย{v}ว่างทั้งวัน", "value": v, "shape": "mobile/glued-no-keyword"})
    return items


def make_card() -> list[dict]:
    items = []
    specs = [("4", 16), ("5" + rng.choice("12345"), 16), ("3" + rng.choice("47"), 15), ("4", 16), ("5" + rng.choice("12345"), 16), ("6011", 16), ("4", 16), ("35" + rng.choice("2345678"), 16), ("5" + rng.choice("12345"), 16), ("3" + rng.choice("47"), 15)]
    styles = ["spaced", "dashed", "bare", "bare", "spaced", "spaced", "dashed", "bare", "spaced", "spaced"]
    for i, (frame, (pre, ln), st) in enumerate(zip(CARD_FRAMES, specs, styles)):
        v = fmt_card(luhn_complete(pre, ln), st)
        items.append({"id": f"cc{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": f"{ln}d/{st}"})
    # second pass, other frames with the remaining styles
    for i, frame in enumerate(CARD_FRAMES):
        pre, ln = specs[(i + 3) % len(specs)]
        st = ["bare", "spaced", "dashed"][i % 3]
        v = fmt_card(luhn_complete(pre, ln), st)
        items.append({"id": f"cc{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": f"{ln}d/{st}"})
    return items


def make_email() -> list[dict]:
    items = []
    for i, frame in enumerate(EMAIL_FRAMES):
        v = email()
        items.append({"id": f"em{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": "plain"})
    special = [
        ("somchai+orders@example.co.th", "plus"),
        ("Pim.Kanya@Example.COM", "mixed-case"),
        ("n.tanawat_99@sub.mail.example.org", "subdomain"),
        ("hr-team@example-corp.co.th", "hyphen-domain"),
        ("wichai@example.io", "short-tld"),
        ("ploy.s@example.ac.th", "ac.th"),
        ("arthit.w@example.in.th", "in.th"),
        ("supaporn_t@example.com", "underscore"),
        ("naree@example.co.th", "no-dot-local"),
        ("k.r@ex.th", "tiny"),
    ]
    for j, (v, shape) in enumerate(special):
        frame = EMAIL_FRAMES[j % len(EMAIL_FRAMES)]
        items.append({"id": f"em{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": shape})
    # glued to Thai with no space (users do this)
    v = email()
    items.append({"id": f"em{len(items)+1:02d}", "text": f"ส่งไปที่{v}เลยนะ", "value": v, "shape": "glued"})
    return items


def make_nid() -> list[dict]:
    items = []
    styles = ["dashed", "bare", "spaced", "dashed", "bare", "dashed", "bare", "spaced", "dashed", "bare"]
    for i, (frame, st) in enumerate(zip(NID_FRAMES, styles)):
        v = fmt_nid(thai_nid(), st)
        items.append({"id": f"nid{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": st})
    for i, frame in enumerate(NID_FRAMES):
        st = ["bare", "dashed", "spaced"][i % 3]
        v = fmt_nid(thai_nid(), st)
        items.append({"id": f"nid{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": st})
    # bare 13 digits with NO keyword at all
    for _ in range(3):
        v = thai_nid()
        items.append({"id": f"nid{len(items)+1:02d}", "text": f"ของฉันคือ {v} จำได้ไหม", "value": v, "shape": "bare-no-keyword"})
    return items


def make_bank() -> list[dict]:
    items = []
    styles = ["dashed", "bare", "dashed2", "spaced", "dashed", "bare", "dashed", "dashed2", "bare", "dashed"]
    for i, (frame, st) in enumerate(zip(BANK_FRAMES, styles)):
        v = bank_acct(st)
        items.append({"id": f"ba{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": st})
    for i, frame in enumerate(BANK_FRAMES):
        st = ["bare", "dashed", "spaced", "dashed2"][i % 4]
        v = bank_acct(st)
        items.append({"id": f"ba{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": st})
    # PromptPay by phone / by id, and a 12-digit account
    v = mobile("bare")
    items.append({"id": f"ba{len(items)+1:02d}", "text": f"พร้อมเพย์ {v} โอนมาได้เลย", "value": v, "shape": "promptpay-phone"})
    v = digits(12)
    items.append({"id": f"ba{len(items)+1:02d}", "text": f"เลขบัญชี {v[:3]}-{v[3:9]}-{v[9:]} ธนาคารออมสิน", "value": f"{v[:3]}-{v[3:9]}-{v[9:]}", "shape": "12d-dashed"})
    return items


def make_keys() -> list[dict]:
    items = []
    gens = [
        ("openai-proj", lambda: "sk-proj-" + alnum(48, "_-")),
        ("openai-legacy", lambda: "sk-" + alnum(48)),
        ("anthropic", lambda: "sk-ant-api03-" + alnum(60, "_-")),
        ("google", lambda: "AIza" + alnum(35, "_-")),
        ("aws-access", lambda: "AKIA" + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(16))),
        ("github-pat", lambda: "ghp_" + alnum(36)),
        ("slack-bot", lambda: "xoxb-" + digits(12) + "-" + digits(13) + "-" + alnum(24)),
        ("jwt", lambda: "eyJ" + alnum(20) + "." + "eyJ" + alnum(40) + "." + alnum(43, "_-")),
        ("bearer", lambda: "Bearer " + alnum(40, "-._~+/")),
        ("gateway", lambda: "gw_" + alnum(32, "_-")),
        ("hex32", lambda: "".join(rng.choice("0123456789abcdef") for _ in range(32))),
        ("stripe-live", lambda: "sk-live-" + alnum(24)),
        ("generic-secret", lambda: alnum(40)),
        ("aws-secret", lambda: alnum(40, "/+")),
        ("db-url", lambda: f"postgres://app:{alnum(16)}@db.internal:5432/prod"),
    ]
    for i, (shape, g) in enumerate(gens):
        v = g()
        frame = KEY_FRAMES[i % len(KEY_FRAMES)]
        if shape == "bearer":
            frame = "header ต้องเป็น Authorization: {v} ใช่ไหม"
        items.append({"id": f"key{i+1:02d}", "text": frame.format(v=v), "value": v, "shape": shape})
    for i, (shape, g) in enumerate(gens[:10]):
        v = g()
        frame = KEY_FRAMES[(i + 5) % len(KEY_FRAMES)]
        if shape == "bearer":
            frame = "ยิงด้วย curl -H \"Authorization: {v}\" แล้ว 401"
        items.append({"id": f"key{len(items)+1:02d}", "text": frame.format(v=v), "value": v, "shape": shape})
    return items


def make_names() -> list[dict]:
    src = json.loads((HERE / "thai_names_synthetic.json").read_text(encoding="utf-8"))["items"]
    return [{"id": p["id"], "text": p["text"], "value": p["name"], "shape": p.get("shape", "")} for p in src]


ADDRESS_SHAPES = [
    ("classic", "ที่อยู่ 99/88 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110", "99/88 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพฯ 10110"),
    ("road_only", "บ้านเลขที่ 12 ถนนพระราม 4", "12 ถนนพระราม 4"),
    ("village_name", "บ้านเลขที่ 45/6 หมู่บ้านสัมมากร", "45/6 หมู่บ้านสัมมากร"),
    ("soi_abbrev", "ที่อยู่ 5 ซ.ลาดพร้าว 101", "5 ซ.ลาดพร้าว 101"),
    ("thai_digits", "บ้านเลขที่ ๙๙/๘๘ ถนนสีลม", "๙๙/๘๘ ถนนสีลม"),
    ("postcode_only", "ส่งที่ 88 มัณฑนา 10540", "88 มัณฑนา 10540"),
    ("abbrev_m_t", "ที่อยู่ 45 ม.7 ต.บางพลี", "45 ม.7 ต.บางพลี"),
    ("soi_word", "เลขที่ 7 ซอยสุขุมวิท 24", "7 ซอยสุขุมวิท 24"),
    ("condo_with_postcode", "ที่อยู่ 199/22 คอนโดลุมพินี 10120", "199/22 คอนโดลุมพินี 10120"),
    ("district_only", "บ้านเลขที่ 12/3 แขวงบางนา", "12/3 แขวงบางนา"),
    ("moo_tambon", "บ้านเลขที่ 3 หมู่ 5 ตำบลบางรัก", "3 หมู่ 5 ตำบลบางรัก"),
    ("condo_only", "ที่อยู่ 199/22 คอนโดลุมพินี", "199/22 คอนโดลุมพินี"),
    ("building_floor", "ส่งที่ 12 อาคารเอ ชั้น 5", "12 อาคารเอ ชั้น 5"),
    ("province_bare", "อยู่ที่ 77/1 บางใหญ่ นนทบุรี", "77/1 บางใหญ่ นนทบุรี"),
    ("english_building", "ที่อยู่ 199/22 Lumpini Condo", "199/22 Lumpini Condo"),
    ("prose_gap", "ที่อยู่สำหรับจัดส่งสินค้าของลูกค้ารายนี้คือ 99/88 ถนนสุขุมวิท 10110", "99/88 ถนนสุขุมวิท 10110"),
    ("building_room", "ส่งที่ 23 ตึกบี ห้อง 402", "23 ตึกบี ห้อง 402"),
    ("industrial_estate", "อยู่ที่ 55 นิคมอุตสาหกรรมอมตะ ชลบุรี", "55 นิคมอุตสาหกรรมอมตะ ชลบุรี"),
    ("no_house_number", "ที่อยู่ ถนนสุขุมวิท กรุงเทพ", "ถนนสุขุมวิท กรุงเทพ"),
    ("room_tower_no_house_no", "ส่งที่ คอนโดไอดีโอ ตึก A ห้อง 1205", "คอนโดไอดีโอ ตึก A ห้อง 1205"),
    ("apartment", "อยู่ที่ 9/1 อพาร์ทเม้นท์สุขใจ", "9/1 อพาร์ทเม้นท์สุขใจ"),
    ("chat_full", "ส่งมาที่บ้านนะ 128/4 ซอยรามคำแหง 24 แขวงหัวหมาก เขตบางกะปิ กทม. 10240", "128/4 ซอยรามคำแหง 24 แขวงหัวหมาก เขตบางกะปิ กทม. 10240"),
    ("chat_no_keyword", "อยู่ 55/9 หมู่ 3 ต.บางเสาธง อ.บางเสาธง สมุทรปราการ 10570 ค่ะ", "55/9 หมู่ 3 ต.บางเสาธง อ.บางเสาธง สมุทรปราการ 10570"),
    ("office", "ออฟฟิศอยู่ชั้น 12 อาคารเอ็มไพร์ 195 ถนนสาทรใต้ ยานนาวา สาทร กรุงเทพ 10120", "195 ถนนสาทรใต้ ยานนาวา สาทร กรุงเทพ 10120"),
    ("upcountry", "บ้านอยู่ 23 หมู่ 7 ต.สันทราย อ.สันทราย จ.เชียงใหม่ 50210", "23 หมู่ 7 ต.สันทราย อ.สันทราย จ.เชียงใหม่ 50210"),
]


def make_addresses() -> list[dict]:
    return [{"id": f"ad_{sid}", "text": t, "value": v, "shape": sid} for sid, t, v in ADDRESS_SHAPES]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sets = {
        "phone": make_phone(),
        "credit_card": make_card(),
        "email": make_email(),
        "thai_national_id": make_nid(),
        "thai_bank_account": make_bank(),
        "api_key_secret": make_keys(),
        "thai_name": make_names(),
        "thai_address": make_addresses(),
    }
    for cat, items in sets.items():
        (OUT / f"{cat}.json").write_text(
            json.dumps({"_about": "SYNTHETIC must-catch items for the DLP eval (FIX28). Generated by gen_data.py; no real person, number, card, id, account or key.", "category": cat, "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"{cat:18} {len(items)} items")


if __name__ == "__main__":
    main()
