"""Turn the specs into files: digital PDFs (headless Chrome), scans, photos, e-Tax.

Chrome is the host's (it shapes Thai and writes a real text layer, as the
accounting systems' PDF engines do). Scans and photos are made from the
digital page with seeded, deterministic degradations: a scan is the page
at 200 or 300 DPI, slightly rotated, blurred, noisy and JPEG-compressed,
wrapped as an image-only PDF; a photo is a thermal receipt on a table,
crumpled (a smooth displacement field + creases), unevenly lit, in
perspective, blurred and JPEG-compressed.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FIXED_DATE = "D:20260926000000+07'00'"


def chrome_pdf(html_path: Path, pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pdf_path.with_suffix(".raw.pdf")
    args = [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
            "--disable-extensions", f"--user-data-dir={pdf_path.parent / '.chrome-profile'}",
            "--virtual-time-budget=3000", f"--print-to-pdf={tmp}", html_path.resolve().as_uri()]
    subprocess.run(args, check=True, capture_output=True, timeout=120)
    normalize_pdf(tmp, pdf_path)
    tmp.unlink()


def normalize_pdf(src: Path, dst: Path, attachments: dict[str, bytes] | None = None) -> None:
    """Fixed metadata (no generation time) and, for e-Tax, the XML as an embedded file."""
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.add_metadata({"/Producer": "SRAN AM2 fixture (Chrome print)", "/Creator": "agent_market.tools.e2fix",
                    "/CreationDate": FIXED_DATE, "/ModDate": FIXED_DATE})
    for name, data in (attachments or {}).items():
        w.add_attachment(name, data)
    with open(dst, "wb") as f:
        w.write(f)


def rasterize(pdf_path: Path, dpi: int) -> list[Image.Image]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return [doc[i].render(scale=dpi / 72.0).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def _noise(img: Image.Image, sigma: float, rng: np.random.Generator) -> Image.Image:
    a = np.asarray(img).astype(np.float32)
    a += rng.normal(0, sigma, a.shape).astype(np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert(img.mode)


def scan(pdf_path: Path, out_pdf: Path, dpi: int, angle: float, noise: float, blur: float, jpeg: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    pages = []
    for page in rasterize(pdf_path, dpi):
        g = page.convert("L")
        # a scanner's paper is not quite white and its toner not quite black
        a = np.asarray(g).astype(np.float32)
        a = 18 + a * (236 - 18) / 255.0
        g = Image.fromarray(a.astype(np.uint8))
        g = g.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=236)
        g = g.filter(ImageFilter.GaussianBlur(blur * dpi / 200.0))
        g = _noise(g, noise, rng)
        g = _jpeg(g, jpeg)
        pages.append(g)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out_pdf, "PDF", resolution=float(dpi), save_all=True, append_images=pages[1:])


def _smooth_field(h: int, w: int, rng: np.random.Generator, waves: int, scale: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    f = np.zeros((h, w), np.float32)
    for _ in range(waves):
        fx, fy = rng.uniform(0.5, 3.5) / w, rng.uniform(0.5, 3.5) / h
        ph = rng.uniform(0, 2 * math.pi)
        f += np.sin(2 * math.pi * (fx * xx + fy * yy) + ph) * rng.uniform(0.3, 1.0)
    f /= max(1e-6, np.abs(f).max())
    return f * scale


def _remap(a: np.ndarray, dx: np.ndarray, dy: np.ndarray, fill: float) -> np.ndarray:
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx, sy = xx + dx, yy + dy
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = sx - x0, sy - y0
    out = np.full((h, w), fill, np.float32)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 < w - 1) & (y0 < h - 1)
    x0c, y0c = np.clip(x0, 0, w - 2), np.clip(y0, 0, h - 2)
    v = (a[y0c, x0c] * (1 - fx) * (1 - fy) + a[y0c, x0c + 1] * fx * (1 - fy)
         + a[y0c + 1, x0c] * (1 - fx) * fy + a[y0c + 1, x0c + 1] * fx * fy)
    out[ok] = v[ok]
    return out


def _perspective_coeffs(src, dst):
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(m, dtype=np.float64)
    B = np.array([c for p in src for c in p], dtype=np.float64)
    return np.linalg.solve(A, B).tolist()


def photo(pdf_path: Path, out_jpg: Path, *, crumple: float, angle: float, light: float, width: int, blur: float,
          jpeg: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    page = rasterize(pdf_path, 260)[0].convert("L")
    a = np.asarray(page).astype(np.float32)
    # crop to the printed area (thermal paper roll), keep a small margin
    ink = np.where(a < 200)
    top, bot = max(0, ink[0].min() - 40), min(a.shape[0], ink[0].max() + 60)
    a = a[top:bot, :]
    # thermal print: grey-brown ink, faded unevenly along the paper
    fade = 0.25 + 0.55 * (0.5 + 0.5 * _smooth_field(a.shape[0], a.shape[1], rng, 3, 1.0))
    a = 250 - (250 - a) * (1 - fade * 0.45)
    h, w = a.shape
    # crumple: smooth displacement plus creases
    amp = 6.0 * crumple * (w / 800.0)
    dx = _smooth_field(h, w, rng, 7, amp)
    dy = _smooth_field(h, w, rng, 7, amp)
    a = _remap(a, dx, dy, 250.0)
    shade = 1.0 - 0.18 * crumple * (0.5 + 0.5 * _smooth_field(h, w, rng, 9, 1.0))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for _ in range(int(6 * crumple)):
        y0, x0 = rng.uniform(0, h), rng.uniform(0, w)
        th = rng.uniform(0, math.pi)
        dist = np.abs((xx - x0) * math.sin(th) - (yy - y0) * math.cos(th))
        shade *= 1.0 - 0.12 * crumple * np.exp(-(dist / 2.5) ** 2)
    a = np.clip(a * shade, 0, 255)
    paper = Image.fromarray(a.astype(np.uint8)).convert("RGB")
    # warm thermal paper tint
    p = np.asarray(paper).astype(np.float32)
    p[..., 0] *= 1.0
    p[..., 1] *= 0.985
    p[..., 2] *= 0.95
    paper = Image.fromarray(np.clip(p, 0, 255).astype(np.uint8))
    # on a table: dark wood-ish background, receipt in perspective, rotated
    W = int(paper.width * 1.35)
    H = int(paper.height * 1.18)
    bg = np.zeros((H, W, 3), np.float32)
    base = np.array([96, 72, 50], np.float32)
    grain = _smooth_field(H, W, rng, 12, 1.0)[..., None]
    bg[:] = base + 18 * grain
    canvas = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
    ox, oy = (W - paper.width) // 2, (H - paper.height) // 2
    mask = Image.new("L", paper.size, 255)
    canvas.paste(paper, (ox, oy), mask)
    canvas = canvas.rotate(angle, resample=Image.BICUBIC, fillcolor=tuple(int(c) for c in base))
    # perspective: the phone is held slightly above and to one side
    k = 0.045 + 0.03 * crumple
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    dst = [(W * k, H * k * 0.6), (W * (1 - k * 0.4), 0), (W, H), (0, H * (1 - k * 0.3))]
    canvas = canvas.transform((W, H), Image.PERSPECTIVE, _perspective_coeffs(src, dst), Image.BICUBIC,
                              fillcolor=tuple(int(c) for c in base))
    # uneven light (a lamp on one side, the phone's shadow)
    arr = np.asarray(canvas).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    lamp = 1.0 - light * 0.55 * (xx / W) * (0.6 + 0.4 * yy / H)
    shadow = 1.0 - light * 0.25 * np.exp(-(((xx - W * 0.3) / (W * 0.25)) ** 2 + ((yy - H * 0.85) / (H * 0.2)) ** 2))
    arr *= (lamp * shadow)[..., None]
    canvas = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    canvas = canvas.resize((width, int(width * H / W)), Image.LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    canvas = _noise(canvas, 5.0 + 5.0 * crumple, rng)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_jpg, "JPEG", quality=jpeg)


def cleanup_profile(out_dir: Path) -> None:
    shutil.rmtree(out_dir / ".chrome-profile", ignore_errors=True)
