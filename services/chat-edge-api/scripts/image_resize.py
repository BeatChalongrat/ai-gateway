#!/usr/bin/env python3
"""Image resize + base64 helper for the vision pipeline.

Called by the Go ChatService whenever an uploaded image needs to
ship to a vision-capable model. The script:

  1. Opens the source via Pillow.
  2. Down-scales it when the longest side is over <max_dim> px OR
     the file is over 2 MB on disk (vision APIs charge per image
     token; an 8000×6000 photo costs a fortune compared to a
     2048×1536 down-scale that conveys the same content).
  3. Writes the (possibly resized) image to <output_path>.
  4. Writes the base64 data URL to <output_path>.b64 — sidecar
     file rather than stdout because base64 of a 2 MB image is
     ~2.7 MB of text that we don't want to round-trip through
     subprocess.Output's buffer.
  5. Prints a JSON report (sizes / dimensions / base64_path) to
     stdout so the Go caller can decide whether to cache.

Usage:  python3 image_resize.py <input_path> <output_path> [max_dim]
Output: JSON on stdout
"""

import base64
import json
import os
import sys
from typing import Any

from PIL import Image


# Vision APIs charge per image token. 2048 on the longest side is a
# good default — keeps detail for diagrams + screenshots while cap-
# ping cost. Caller can override per-image via argv[3].
DEFAULT_MAX_DIM = 2048

# Files over this size are forced through resize even if the
# dimensions are small (eg an oversized PNG with low compression).
FORCE_RESIZE_BYTES = 2 * 1024 * 1024  # 2 MB

# JPEG quality for the recompressed output. 85 is the standard
# "indistinguishable from source at viewing distance" preset.
JPEG_QUALITY = 85


# Map Pillow's format name to a MIME type for the data-URL prefix.
MIME_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def _output_format(input_path: str, src_format: str | None) -> str:
    """Pick the output format. Prefer the source format when it's
    one of the vision-API-supported types; fall back to PNG."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext == ".gif":
        return "GIF"
    if ext == ".webp":
        return "WEBP"
    if src_format and src_format.upper() in MIME_BY_FORMAT:
        return src_format.upper()
    return "PNG"


def resize_image(
    input_path: str, output_path: str, max_dim: int = DEFAULT_MAX_DIM
) -> dict[str, Any]:
    img = Image.open(input_path)
    original_size = os.path.getsize(input_path)
    src_w, src_h = img.size

    result: dict[str, Any] = {
        "original_width": src_w,
        "original_height": src_h,
        "original_size_bytes": original_size,
        "format": img.format or "PNG",
        "mode": img.mode,
        "resized": False,
    }

    output_format = _output_format(input_path, img.format)

    # Decide whether to resize. The two triggers are independent —
    # an oversized PNG with 800×600 dimensions still gets re-encoded
    # to bring the byte size down.
    over_dim = max(src_w, src_h) > max_dim
    over_bytes = original_size > FORCE_RESIZE_BYTES
    if over_dim:
        if src_w >= src_h:
            new_w = max_dim
            new_h = max(1, int(src_h * (max_dim / src_w)))
        else:
            new_h = max_dim
            new_w = max(1, int(src_w * (max_dim / src_h)))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        result["resized"] = True
        result["new_width"] = new_w
        result["new_height"] = new_h
    elif over_bytes:
        # Same dimensions, just re-encode.
        result["resized"] = True
        result["new_width"] = src_w
        result["new_height"] = src_h

    # JPEG can't carry alpha — flatten RGBA → RGB before save so
    # we don't get a "cannot write mode RGBA as JPEG" exception.
    if output_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        if img.mode != "RGB":
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background

    save_kwargs: dict[str, Any] = {"format": output_format}
    if output_format == "JPEG":
        save_kwargs["quality"] = JPEG_QUALITY
        save_kwargs["optimize"] = True
    img.save(output_path, **save_kwargs)
    out_size = os.path.getsize(output_path)
    result["output_size_bytes"] = out_size
    result["output_format"] = output_format
    result["output_path"] = output_path

    # Sidecar base64 file. We do NOT print the base64 to stdout
    # because a 2 MB image is ~2.7 MB of base64 text and that fights
    # subprocess buffer limits + crowds the log if the Go caller
    # ever logs the script output.
    with open(output_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = MIME_BY_FORMAT.get(output_format, "image/png")
    data_url = f"data:{mime};base64,{b64}"
    b64_path = output_path + ".b64"
    with open(b64_path, "w", encoding="ascii") as f:
        f.write(data_url)
    result["base64_path"] = b64_path
    result["base64_length"] = len(data_url)
    result["mime"] = mime
    return result


def main() -> int:
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "error": (
                        "Usage: image_resize.py <input> <output> [max_dim]"
                    )
                }
            )
        )
        return 1
    try:
        max_dim = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_MAX_DIM
        result = resize_image(sys.argv[1], sys.argv[2], max_dim)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
