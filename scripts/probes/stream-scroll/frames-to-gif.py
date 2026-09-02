#!/usr/bin/env python
"""Turn a probe.js screencast folder (NNN_<ms>ms.jpg) into a short GIF
and a labelled contact sheet of the moments that matter after a send.

    python scripts/probes/stream-scroll/frames-to-gif.py <frames-dir> [--scale 0.5]

Writes <frames-dir>.gif (real frame timing, every frame) and
<frames-dir>-sheet.png (9 frames: 0 → ~5 s, time-stamped).
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SHEET_MS = [0, 60, 100, 150, 200, 300, 600, 1500, 5000]


def frames_of(d: Path):
    out = []
    for p in sorted(d.glob("*.jpg")):
        m = re.search(r"_(-?\d+)ms", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


def main():
    d = Path(sys.argv[1])
    scale = 0.5
    if "--scale" in sys.argv:
        scale = float(sys.argv[sys.argv.index("--scale") + 1])
    frames = frames_of(d)
    if not frames:
        sys.exit("no frames in " + str(d))

    # GIF at real timing (durations from the timestamps; floor 20 ms)
    imgs, durs = [], []
    for i, (ms, p) in enumerate(frames):
        im = Image.open(p).convert("RGB")
        im = im.resize((int(im.width * scale), int(im.height * scale)))
        imgs.append(im.convert("P", palette=Image.ADAPTIVE, colors=128))
        nxt = frames[i + 1][0] if i + 1 < len(frames) else ms + 500
        durs.append(max(20, nxt - ms))
    gif = d.with_suffix(".gif")
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=durs, loop=0, optimize=False)
    print("wrote", gif, len(imgs), "frames")

    # contact sheet: nearest frame at each moment
    picks = []
    for want in SHEET_MS:
        best = min(frames, key=lambda f: abs(f[0] - want))
        picks.append(best)
    w0, h0 = Image.open(picks[0][1]).size
    tw, th = int(w0 * 0.34), int(h0 * 0.34)
    cols = 3
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 22)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (ms, p) in enumerate(picks):
        im = Image.open(p).convert("RGB").resize((tw, th))
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        sheet.paste(im, (x, y + 22))
        draw.text((x + 6, y + 4), f"+{ms} ms", fill="black")
    out = d.parent / (d.name + "-sheet.png")
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
