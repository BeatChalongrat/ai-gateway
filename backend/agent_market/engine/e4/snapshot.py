"""A captured page, as the fetcher stored it and as a step receives it.

A step never sees a URL to open. It is handed, per source, the text a person
could see on the page (``text_<id>.json``: numbered blocks, each with where
it sits in the page and the box it is drawn in) and the picture of the page
(``shot_<id>.jpg``), with the capture's own record (when, from where, with
which hashes). ``crop`` cuts the part of that picture a value was read from,
so a person can look at exactly what was shown.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

PAD_X, PAD_Y = 220, 70          # how much of the page around a value a crop shows
MAX_CROP = (1100, 520)


@dataclass
class Snapshot:
    id: str
    capture: dict
    blocks: list[dict] = field(default_factory=list)
    shot: str | None = None      # path of the page's picture, when there is one

    @property
    def url(self) -> str:
        return self.capture.get("final_url") or self.capture.get("requested_url") or ""

    @property
    def host(self) -> str:
        return ((self.capture.get("hosts") or {}).get("named")) or ""

    def block(self, i) -> dict | None:
        for b in self.blocks:
            if b["i"] == i:
                return b
        return None


def load(in_dir: str, source: dict) -> Snapshot:
    """The snapshot of one captured source (``source``: its record in the research the runner handed over)."""
    sid = str(source["id"])
    snap = Snapshot(id=sid, capture=source.get("capture") or {})
    text = os.path.join(in_dir, f"text_{sid}.json")
    if os.path.exists(text):
        with open(text, encoding="utf-8") as f:
            snap.blocks = list((json.load(f) or {}).get("blocks") or [])
    shot = os.path.join(in_dir, f"shot_{sid}.jpg")
    if os.path.exists(shot):
        snap.shot = shot
    return snap


def crop(snap: Snapshot, boxes: list[list[int]], out_dir: str) -> dict | None:
    """The part of the page's picture that holds the boxes (and a little around them), written to
    ``out_dir`` under the hash of its own bytes. None when the picture does not reach that part of the page."""
    if not snap.shot or not boxes:
        return None
    from PIL import Image

    with Image.open(snap.shot) as im:
        w, h = im.size
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        if top >= h or left >= w or right <= 0 or bottom <= 0:
            return None
        x0, y0 = max(0, left - PAD_X), max(0, top - PAD_Y)
        x1, y1 = min(w, right + PAD_X), min(h, bottom + PAD_Y)
        if x1 - x0 > MAX_CROP[0]:
            x1 = x0 + MAX_CROP[0]
        if y1 - y0 > MAX_CROP[1]:
            y1 = y0 + MAX_CROP[1]
        part = im.crop((x0, y0, x1, y1)).convert("RGB")
        tmp = os.path.join(out_dir, f"crop_tmp_{snap.id}.jpg")
        part.save(tmp, "JPEG", quality=80)
    with open(tmp, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    name = f"evidence_{sha[:32]}.jpg"
    os.replace(tmp, os.path.join(out_dir, name))
    # the value's own box inside the crop, for whoever draws a mark around it
    inner = [left - x0, top - y0, right - left, bottom - top]
    return {"name": name, "sha256": sha, "width": x1 - x0, "height": y1 - y0, "box": inner}
