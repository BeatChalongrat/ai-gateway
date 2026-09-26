"""Page images for OCR: deskew, scale, and pick the better Tesseract layout mode.

A label and its value sit at opposite ends of a row; a scan rotated by
0.8° moves the right end of an A4 row by most of a line, so rows are
straightened before OCR (projection-profile search, no OpenCV in the
sandbox). Photos are upscaled so thermal-receipt text reaches the ~30 px
glyph height Tesseract reads best, and are read twice (page segmentation
modes 4 and 6); the reading with more money figures (then the higher mean
confidence) wins — a photo's right-hand amount column is what Tesseract's
default layout analysis drops.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps


def deskew_angle(img: Image.Image, limit: float = 8.0, step: float = 0.25) -> float:
    g = ImageOps.grayscale(img)
    scale = 900 / max(g.width, 1)
    if scale < 1:
        g = g.resize((int(g.width * scale), int(g.height * scale)))
    a = np.asarray(g).astype(np.float32)
    thr = a.mean() - 0.5 * a.std()
    ink = Image.fromarray(((a < thr) * 255).astype(np.uint8))
    best, best_score = 0.0, -1.0
    ang = -limit
    while ang <= limit + 1e-9:
        r = np.asarray(ink.rotate(ang, resample=Image.NEAREST, fillcolor=0)).astype(np.float32)
        prof = r.sum(axis=1)
        score = float(np.var(prof))
        if score > best_score:
            best, best_score = ang, score
        ang += step
    return best


def _longest_runs(mask: np.ndarray, axis: int) -> np.ndarray:
    """Per row (axis=1) or column (axis=0): the longest run of True."""
    m = mask if axis == 1 else mask.T
    best = np.zeros(m.shape[0], dtype=np.int32)
    cur = np.zeros(m.shape[0], dtype=np.int32)
    for j in range(m.shape[1]):
        col = m[:, j]
        cur = np.where(col, cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def remove_rules(img: Image.Image, dpi: int = 300) -> Image.Image:
    """Whiten table ruling lines: Tesseract drops whole rows of a bordered table.

    A rule is a run of dark pixels far longer than any glyph stroke: > 1.3 cm
    horizontally, > 0.6 cm vertically at the page's DPI (a 16 pt glyph is
    ~0.5 cm tall). Only those pixels are whitened; text crossing a rule keeps
    the rest of its strokes."""
    a = np.asarray(img).astype(np.int16)
    dark = a < 110
    hmin, vmin = int(dpi * 0.52), int(dpi * 0.24)
    out = a.copy()
    rows = np.where(_longest_runs(dark, 1) > hmin)[0]
    for r in rows:
        line = dark[r]
        run = 0
        for j in range(len(line) + 1):
            if j < len(line) and line[j]:
                run += 1
                continue
            if run > hmin:
                out[r, j - run:j] = 255
            run = 0
    cols = np.where(_longest_runs(dark, 0) > vmin)[0]
    for c in cols:
        col = dark[:, c]
        run = 0
        for i in range(len(col) + 1):
            if i < len(col) and col[i]:
                run += 1
                continue
            if run > vmin:
                out[i - run:i, c] = 255
            run = 0
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def crop_paper(img: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    """A photo of a receipt on a table: the bounding box of the bright paper (Otsu threshold,
    rows / columns that are mostly paper), so deskew sees the print and not the table."""
    a = np.asarray(img).astype(np.float32)
    hist, edges = np.histogram(a, bins=64, range=(0, 255))
    total, sum_all = a.size, float((hist * (edges[:-1] + 2)).sum())
    best_t, best_var, w0, s0 = 128.0, -1.0, 0, 0.0
    for i in range(64):
        w0 += hist[i]
        s0 += hist[i] * (edges[i] + 2)
        if w0 == 0 or w0 == total:
            continue
        m0, m1 = s0 / w0, (sum_all - s0) / (total - w0)
        var = w0 * (total - w0) * (m0 - m1) ** 2
        if var > best_var:
            best_var, best_t = var, edges[i + 1]
    paper = a > best_t
    rows = np.where(paper.mean(axis=1) > 0.35)[0]
    cols = np.where(paper.mean(axis=0) > 0.35)[0]
    if len(rows) < a.shape[0] * 0.2 or len(cols) < a.shape[1] * 0.2:
        return img, None
    box = (int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1)
    return img.crop(box), box


def prepare(img: Image.Image, *, photo: bool, dpi: int = 300) -> tuple[Image.Image, dict]:
    img = ImageOps.exif_transpose(img).convert("L")
    box = None
    if photo:
        img, box = crop_paper(img)
    img = ImageOps.autocontrast(img, cutoff=1)
    angle = deskew_angle(img, limit=8.0 if photo else 2.0, step=0.25 if photo else 0.1)
    if abs(angle) >= 0.1:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    scale = 1.0
    if photo and img.width < 2000:
        scale = 2000 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img, {"rotate": round(angle, 2), "scale": round(scale, 3), "crop": box}


def _score(res: dict) -> tuple:
    """How legible an OCR reading is for an invoice: figures, tax IDs, then confidence."""
    from . import thai_text as T

    text = " ".join(w["text"] for w in res["words"])
    confs = [w["conf"] for w in res["words"] if w["conf"] >= 0]
    good = sum(1 for w in res["words"] if w["conf"] >= 80)
    return (len(T.money_tokens(text, lenient=True)) + 3 * len(T.tax_id_candidates(text)) + good / 25,
            sum(confs) / len(confs) if confs else 0)


def best_ocr(img: Image.Image, ocr_fn, *, photo: bool, dpi: int = 300) -> tuple[dict, str]:
    """``ocr_fn(image, config)`` → extract_text.ocr_image_words result.

    A scan is read as it is and with its table rules erased (a bordered table
    loses whole rows in the first reading; text touching a border loses
    strokes in the second); a photo in two page-segmentation modes. The more
    legible reading wins (``_score``)."""
    if photo:
        tries = [(img, "--oem 1 --psm 4", "psm4"), (img, "--oem 1 --psm 6", "psm6")]
    else:
        tries = [(img, "--oem 1 --psm 3", "plain"), (remove_rules(img, dpi), "--oem 1 --psm 3", "rules_removed")]
    best, best_key, best_name = None, None, tries[0][2]
    for im, mode, name in tries:
        res = ocr_fn(im, mode)
        key = _score(res)
        if best_key is None or key > best_key:
            best, best_key, best_name = res, key, name
    return best, best_name
