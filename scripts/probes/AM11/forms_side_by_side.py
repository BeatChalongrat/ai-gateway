"""AM11 (D) — a form as the customer made it, beside the same form as the agent returned it.

    python scripts/probes/AM11/forms_side_by_side.py <folder> <out.png> "<caption>" <left.png> "<left title>" <right.png> "<right title>"

Two page pictures (forms_pictures.sh) in one row, each under its title, with a caption over both. The
pictures are cropped to what is printed on the page (the white under the last line is cut), so the
fields can be read at the size the report shows them. A picture that is missing is an error, never a
blank tile.
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont

GAP, BAR, PAD = 24, 54, 28


def font(size):
    for f in (r"C:\Windows\Fonts\LeelawUI.ttf", r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def wrap(text: str, fnt, width: int) -> list[str]:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if probe.textlength(trial, font=fnt) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def printed(path: str) -> Image.Image:
    """The page, cut to what is on it (with a margin)."""
    im = Image.open(path).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    if not box:
        return im
    left, top, right, bottom = box
    return im.crop((max(0, left - PAD), max(0, top - PAD), min(im.width, right + PAD), min(im.height, bottom + PAD)))


def main(argv):
    folder, out, caption, left, left_title, right, right_title = argv
    tiles = []
    for name, title in ((left, left_title), (right, right_title)):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print("no picture:", path)
            return 1
        tiles.append((printed(path), title))
    width = max(t.width for t, _ in tiles)
    height = max(t.height for t, _ in tiles)
    total = 2 * width + 3 * GAP
    lines = wrap(caption, font(22), total - 2 * GAP)
    head = GAP + len(lines) * 30
    sheet = Image.new("RGB", (total, head + BAR + height + 2 * GAP), "#eef0f4")
    draw = ImageDraw.Draw(sheet)
    for n, line in enumerate(lines):
        draw.text((GAP, GAP + n * 30), line, font=font(22), fill="#17181c")
    for i, (tile, title) in enumerate(tiles):
        x = GAP + i * (width + GAP)
        draw.text((x, head + 12), title, font=font(18), fill="#3b4250")
        top = head + BAR
        draw.rectangle((x - 1, top - 1, x + width, top + height), outline="#c5cad3")
        sheet.paste(Image.new("RGB", (width, height), "white"), (x, top))
        sheet.paste(tile, (x, top))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sheet.save(out)
    print(out, sheet.size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
