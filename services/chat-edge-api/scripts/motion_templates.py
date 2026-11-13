"""Offline, editable motion graphics for run_python on Web and Desktop.

This is a deterministic scene renderer, not a generative video model.
catalog(); render(template_id, data, '/job/out/brief.mp4').
Outputs MP4, poster PNG, contact sheet PNG and an editable storyboard JSON.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps

TEMPLATES = {
    "motion.explainer": "Titles and short explanations, one idea per scene",
    "motion.metrics": "Animated bars with supplied values and explicit units",
    "motion.slideshow": "User-supplied images with titles and captions",
}
FONT = Path(__file__).parent / "fonts" / "Sarabun-Regular.ttf"
BOLD = FONT.with_name("Sarabun-Bold.ttf")


def catalog():
    return {
        "version": 1, "templates": TEMPLATES,
        "contract": {"title": "Required", "width": 960, "height": 540, "fps": 12,
                     "scenes": [{"title": "Required", "body": "Optional short caption", "duration": 3,
                                 "image": "Optional local mounted file path",
                                 "bars": [{"label": "Revenue", "value": 42}], "unit": "%"}]},
        "limits": {"scenes": 12, "duration_seconds": 30, "fps": [6, 24], "max_dimensions": [1280, 720]},
        "rules": ["Use supplied facts only; preserve labels, language and units.",
                  "Keep local image assets available when re-rendering the storyboard.",
                  "No audio, realistic footage generation, remote assets or automatic narration.",
                  "Review the contact sheet AND the complete MP4 before final delivery."],
    }


def _text(value, field, limit=500):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field} must be text of at most {limit} characters")
    return value


def _number(value, field, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{field} must be between {lo} and {hi}")
    return value


def _validate(template, data):
    if template not in TEMPLATES:
        raise ValueError("Unknown motion template; call catalog()")
    if not isinstance(data, dict) or not _text(data.get("title"), "title", 120).strip():
        raise ValueError("Supply a title")
    w, h, fps = data.get("width", 960), data.get("height", 540), data.get("fps", 12)
    for value, name, low, high in ((w, "width", 320, 1280), (h, "height", 240, 720), (fps, "fps", 6, 24)):
        _number(value, name, low, high)
        if not isinstance(value, int) or (name != "fps" and value % 2):
            raise ValueError("Dimensions must be even integers; fps must be an integer")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
        raise ValueError("Supply 1–12 scenes")
    frames = 0
    for scene in scenes:
        if not isinstance(scene, dict) or not _text(scene.get("title"), "scene.title", 120).strip():
            raise ValueError("Supply a scene title")
        _text(scene.get("body", ""), "scene.body")
        frames += round(_number(scene.get("duration", 3), "duration", 1, 10) * fps)
        bars = scene.get("bars", [])
        if not isinstance(bars, list) or len(bars) > 5:
            raise ValueError("Use at most 5 bars per scene")
        if template == "motion.metrics" and not bars:
            raise ValueError("Metric scenes require supplied values")
        for bar in bars:
            if not isinstance(bar, dict):
                raise ValueError("Bars must be objects")
            _text(bar.get("label"), "bar.label", 45)
            _number(bar.get("value"), "bar.value", 0, 1e12)
        _text(scene.get("unit", ""), "unit", 20)
        if template == "motion.slideshow" and not scene.get("image"):
            raise ValueError("Slideshow scenes need local image assets")
        if bars and scene.get("image"):
            raise ValueError("Use either an image or bars in a scene")
    if frames > 30 * fps:
        raise ValueError("Total duration exceeds 30 seconds; split the storyboard")
    return w, h, fps, frames


def _wrap(draw, text, font, width):
    # Character wrapping also handles Thai strings with no spaces.
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if draw.textlength(line + char, font=font) > width and line:
                lines.append(line.rstrip())
                line = ""
            line += char
        lines.append(line.rstrip())
    return lines


def _block(draw, text, box, size, color, bold=False):
    x, y, width, height = box
    if not text:
        return
    size = max(12, size)
    for point in range(size, max(11, size // 2) - 1, -1):
        font = ImageFont.truetype(str(BOLD if bold else FONT), point)
        lines = _wrap(draw, text, font, width)
        line_height = math.ceil(point * 1.55)
        if len(lines) * line_height <= height:
            for index, line in enumerate(lines):
                draw.text((x, y + index * line_height), line, font=font, fill=color)
            return
    raise ValueError("Text does not fit the scene; shorten it or split into more scenes")


def _asset(scene):
    if not scene.get("image"):
        return None
    path = Path(scene["image"])
    if not path.is_file() or path.stat().st_size > 12 * 1024 * 1024:
        raise ValueError("Image asset must be a local file at most 12 MiB")
    with Image.open(path) as image:
        if image.width * image.height > 16_000_000 or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Use a still image up to 16 megapixels")
        return ImageOps.exif_transpose(image).convert("RGB")


def _frame(scene, index, count, w, h, progress, asset):
    bg, fg, accent = "#101b30", "#f2f6ff", "#55d9c0"
    image = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(image)
    pad = int(w * .055)
    draw.rectangle((pad, int(h*.09), pad + int(w*.08), int(h*.105)), fill=accent)
    _block(draw, scene["title"], (pad, int(h*.13), w-2*pad, int(h*.19)), int(h*.065), fg, True)
    bars = scene.get("bars", [])
    if asset is not None:
        thumb = ImageOps.contain(asset, (int(w*.49), int(h*.52)))
        image.paste(thumb, (w-pad-int(w*.49)+(int(w*.49)-thumb.width)//2, int(h*.36)+(int(h*.52)-thumb.height)//2))
        _block(draw, scene.get("body", ""), (pad, int(h*.37), int(w*.34), int(h*.48)), int(h*.04), fg)
    elif bars:
        _block(draw, scene.get("body", ""), (pad, int(h*.32), w-2*pad, int(h*.11)), int(h*.033), fg)
        ceiling = max(1, max(bar["value"] for bar in bars))
        row = int(h*.44/len(bars))
        for i, bar in enumerate(bars):
            y = int(h*.46) + i*row
            _block(draw, bar["label"], (pad, y, int(w*.25), row-2), int(h*.03), fg)
            start = int(w*.34)
            length = int(w*.39 * bar["value"]/ceiling * progress)
            if length:
                draw.rounded_rectangle((start, y+3, start+length, y+int(row*.5)), radius=3, fill=accent)
            label = f'{bar["value"]:g}{scene.get("unit", "")}'
            _block(draw, label, (int(w*.76), y, w-pad-int(w*.76), row-2), int(h*.03), fg)
    else:
        _block(draw, scene.get("body", ""), (pad, int(h*.38), w-2*pad, int(h*.46)), int(h*.046), fg)
    draw.rectangle((0, h-5, int(w*((index+progress)/count)), h), fill=accent)
    if progress < .22:
        image = Image.blend(Image.new("RGB", (w, h), bg), image, min(1, progress/.22))
    return image


def render(template_id, data, output_path):
    w, h, fps, frames = _validate(template_id, data)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable; rebuild the code sandbox with its updated Dockerfile")
    output = Path(output_path)
    if output.suffix.lower() != ".mp4":
        raise ValueError("Output must end in .mp4")
    outputs = [output, output.with_suffix(".poster.png"), output.with_suffix(".contact.png"), output.with_suffix(".storyboard.json")]
    if any(path.exists() for path in outputs):
        raise ValueError("Output already exists; use a new filename for revisions")
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = [_asset(scene) for scene in data["scenes"]]
    # Preflight all text and assets before starting the encoder or publishing files.
    posters = [_frame(scene, i, len(assets), w, h, 1, assets[i]) for i, scene in enumerate(data["scenes"])]
    with tempfile.TemporaryDirectory(prefix="motion-", dir=output.parent) as scratch:
        temp = Path(scratch)
        video = temp / output.name
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264", "-threads", "1",
                   "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)]
        # stderr goes to disk, avoiding a pipe deadlock on encoder failure.
        with (temp/"encode.log").open("wb+") as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            try:
                for i, scene in enumerate(data["scenes"]):
                    n = round(scene.get("duration", 3)*fps)
                    for frame in range(n):
                        progress = min(1, (frame+1)/max(1, fps*.8))
                        process.stdin.write(_frame(scene, i, len(assets), w, h, progress, assets[i]).tobytes())
                process.stdin.close()
                if process.wait(timeout=30) != 0:
                    raise RuntimeError("Motion encoder failed")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if video.stat().st_size > 15 * 1024 * 1024:
            raise ValueError("Video exceeds the attachment budget; reduce duration or dimensions")
        posters[0].save(temp/outputs[1].name)
        thumbs = [ImageOps.contain(p, (320, 180)) for p in posters]
        sheet = Image.new("RGB", (640, 180*math.ceil(len(thumbs)/2)), "#101b30")
        for i, thumb in enumerate(thumbs):
            sheet.paste(thumb, ((i%2)*320, (i//2)*180))
        sheet.save(temp/outputs[2].name)
        (temp/outputs[3].name).write_text(json.dumps({"version": 1, "template_id": template_id, "data": data}, ensure_ascii=False, indent=2), encoding="utf-8")
        # Exclusive copies prevent accidental overwrite even if a target appears mid-render.
        published = []
        try:
            for path in outputs:
                with path.open("xb") as dest:
                    published.append(path)
                    with (temp/path.name).open("rb") as src:
                        shutil.copyfileobj(src, dest)
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            raise
    return {"status": "rendered", "renderer": "motion_templates.v1", "template_id": template_id,
            "duration_seconds": frames/fps, "fps": fps, "width": w, "height": h, "audio": False,
            "files": [str(path) for path in outputs], "visual_review_required": True}
