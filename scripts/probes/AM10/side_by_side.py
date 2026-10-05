"""AM10 — the report's side-by-side images (results/sbs/, gitignored).

    python scripts/probes/AM10/side_by_side.py [live|test]

Two kinds of image, made from what the walks and probes left under results/:

* web | desktop — one image per step both clients' walks photographed (walk/<target>/{web,desktop});
* one image per item of the card with its four surfaces in a row: web chat · desktop · admin app ·
  the printed or downloaded file. A surface an item does not reach is a tile that says so in words
  — never a blank and never a picture of something else.

A picture that is missing is named on its tile ("no picture: <path>") and counted; the script exits 1
when any is, so a report never shows a row the walk did not make.
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(RES, "sbs")
TILE_W, BAR, GAP = 900, 46, 12


def font(size):
    for f in (r"C:\Windows\Fonts\LeelawUI.ttf", r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def wrap(draw, text, fnt, width):
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def tile(source, crop=None, height=None):
    """A picture scaled to the tile's width, or — for a string that is not a file — its words."""
    if source and os.path.exists(source):
        im = Image.open(source).convert("RGB")
        if crop:
            im = im.crop(crop)
        im = im.resize((TILE_W, max(1, int(im.height * TILE_W / im.width))))
        if height and im.height > height:
            im = im.crop((0, 0, TILE_W, height))
        return im, True
    said = source or ""
    missing = said.endswith(".png") or said.endswith(".jpg")
    im = Image.new("RGB", (TILE_W, height or 360), "#f4f4f5")
    d = ImageDraw.Draw(im)
    f = font(22)
    text = ("no picture: " + os.path.relpath(said, RES)) if missing else said
    y = 28
    for line in wrap(d, text, f, TILE_W - 56):
        d.text((28, y), line, fill="#b91c1c" if missing else "#27272a", font=f)
        y += 32
    return im, not missing


def row(name, cells, height=None):
    """cells: [(caption, picture path or words, optional crop)] → results/sbs/<name>.png"""
    tiles, ok = [], True
    for cell in cells:
        caption, source = cell[0], cell[1]
        im, found = tile(source, cell[2] if len(cell) > 2 else None, height)
        ok = ok and found
        tiles.append((caption, im))
    h = max(im.height for _, im in tiles)
    sheet = Image.new("RGB", (len(tiles) * TILE_W + (len(tiles) - 1) * GAP, h + BAR), "white")
    d = ImageDraw.Draw(sheet)
    f = font(24)
    for i, (caption, im) in enumerate(tiles):
        x = i * (TILE_W + GAP)
        d.text((x + 10, 9), caption, fill="black", font=f)
        sheet.paste(im, (x, BAR))
        if i:
            d.rectangle((x - GAP, 0, x - 1, h + BAR), fill="#d4d4d8")
    os.makedirs(OUT, exist_ok=True)
    sheet.save(os.path.join(OUT, name + ".png"), optimize=True)
    return ok


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "live"
    walk = os.path.join(RES, "walk", target)
    admin = os.path.join(RES, "admin", target)
    w = lambda *p: os.path.join(walk, "web", *p)  # noqa: E731
    d = lambda *p: os.path.join(walk, "desktop", *p)  # noqa: E731
    a = lambda *p: os.path.join(admin, *p)  # noqa: E731
    pr = lambda *p: os.path.join(RES, "print", *p)  # noqa: E731
    missing = 0

    # every step both walks photographed
    pairs = 0
    for mode in sorted(os.listdir(w()) if os.path.isdir(w()) else []):
        for shot in sorted(os.listdir(w(mode))):
            if shot.endswith(".png") and os.path.exists(d(mode, shot)):
                row(f"{target}_pair_{mode}_{shot[:-4]}", [(f"web · {mode} · {shot[:-4]}", w(mode, shot)),
                                                          (f"desktop · {mode} · {shot[:-4]}", d(mode, shot))], height=1100)
                pairs += 1

    # the card's items, four surfaces each
    items = [
        ("A_admin", [
            ("web chat", "ไม่เกี่ยวกับหน้าแชท: บัญชีผู้ดูแลระบบใช้เข้าแอปผู้ดูแลเท่านั้น (ผู้ใช้แชทเข้าด้วยบัญชีพอร์ทัล)"),
            ("desktop", "เหมือนเว็บ: แอปเดสก์ท็อปไม่มีหน้าของผู้ดูแลระบบ"),
            ("admin app · the new admin signed in", a("first-admin-03-new-admin-accounts.png")),
            ("file", "ไม่มีไฟล์: รหัสผ่านใหม่อยู่ใน .env เท่านั้น ไม่อยู่ในรายงานหรือบันทึกใด"),
        ]),
        ("B_icons", [
            ("web chat · catalogue (light)", w("cards", "01-market-light.png")),
            ("desktop · catalogue (dark)", d("cards", "01-market-dark.png")),
            ("admin app · Agent Market", a("pages-th-catalogue.png")),
            ("the set · 32 px and full size, light and dark", os.path.join(RES, "icons", "sheet.png")),
        ]),
        ("C_cards", [
            ("web chat · a card", w("cards", "01-market-light.png")),
            ("desktop · a card", d("cards", "01-market-light.png")),
            ("admin app · spend (where cost is)", a("pages-th-spend.png")),
            ("file", "ไม่เกี่ยวกับไฟล์: การ์ดไม่ได้พิมพ์ลงเอกสาร เวลาโดยประมาณอยู่บนการ์ดความคืบหน้าระหว่างรอบทำงาน"),
        ]),
        ("D_chats", [
            ("web chat · chats under the agent", w("chats", "04-sidebar-chats.png")),
            ("desktop · two rounds side by side", d("chats", "06-two-rounds-side-by-side.png")),
            ("admin app · the deleted chat's run", a("pages-th-runs.png")),
            ("run page of the deleted chat · files intact", w("chats", "08-run-page-of-the-deleted-chat.png")),
        ]),
        ("E_form", [
            ("web chat · form read", w("form", "02-form-read.png")),
            ("desktop · form filled", d("form", "04-form-filled.png")),
            ("admin app", "ไม่มีหน้าของผู้ดูแล: แบบฟอร์มของรอบเป็นของผู้ใช้ ผู้ดูแลเห็นรอบและไฟล์ผ่านหน้า Runs เท่านั้น"),
            ("the filled form · downloaded", os.path.join(RES, "form", f"{target}-filled-form.png")),
        ]),
        ("F_language", [
            ("web chat · English", w("language", "en_chat.png")),
            ("desktop · English, menus too", d("language", "en_chat.png")),
            ("admin app · English", a("pages-en-catalogue.png")),
            ("a generated document", os.path.join(RES, "form", f"{target}-document-language.png")),
        ]),
        ("G_print", [
            ("web chat · the file card", w("chats", "06-two-rounds-side-by-side.png")),
            ("desktop · the mail connect return", d("language", "connect-return.png")),
            ("admin app", "ไม่เกี่ยวกับแอปผู้ดูแล: เป็นเรื่องของไฟล์ที่พิมพ์และหน้ากลับของการเชื่อมอีเมล"),
            ("printed · PO lines, one page across", pr("po-05.png")),
        ]),
    ]
    for name, cells in items:
        if not row(f"{target}_item_{name}", cells, height=1000):
            missing += 1
            print(f"item {name}: a picture is missing")
    print(f"pairs {pairs} · items {len(items)} · items with a missing picture {missing} → {os.path.relpath(OUT, HERE)}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
