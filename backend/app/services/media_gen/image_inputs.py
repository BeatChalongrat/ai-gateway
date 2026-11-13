"""Validate bounded inline reference images. Never fetch user-controlled URLs."""
import base64
import binascii
import io
import warnings

from PIL import Image

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 16_000_000


def validate_image_inputs(values: list[str]) -> list[tuple[str, bytes, str]]:
    if not 1 <= len(values) <= 4:
        raise ValueError("Supply 1–4 reference images")
    result, total = [], 0
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
    for index, value in enumerate(values):
        header, sep, encoded = value.partition(",")
        mime = header.removeprefix("data:").removesuffix(";base64")
        if not sep or header != f"data:{mime};base64" or mime not in formats:
            raise ValueError("Reference images must be inline PNG, JPEG or WebP data URLs")
        if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("Reference image exceeds 4 MiB")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid reference image encoding") from exc
        total += len(raw)
        if not raw or len(raw) > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Reference images exceed the 4 MiB each / 12 MiB total limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as img:
                    if img.format != formats[mime] or img.width * img.height > MAX_PIXELS:
                        raise ValueError("Reference image format or dimensions are unsupported")
                    if getattr(img, "n_frames", 1) != 1:
                        raise ValueError("Animated reference images are unsupported")
                    img.verify()
        except Exception as exc:
            raise ValueError("Invalid, animated or oversized reference image") from exc
        result.append((f"reference-{index}.{formats[mime].lower()}", raw, mime))
    return result
