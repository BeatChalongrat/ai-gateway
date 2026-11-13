import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import motion_templates as motion


class MotionTests(unittest.TestCase):
    def data(self):
        return {"title": "รายงาน", "width": 640, "height": 360, "fps": 6,
                "scenes": [{"title": "ผลการทำงาน Q3", "body": "ข้อมูลตัวอย่างสำหรับตรวจระบบ", "duration": 1,
                            "bars": [{"label": "งานสำเร็จ", "value": 42}], "unit": "%"}]}

    def test_limits(self):
        for key, value in [("width", 641), ("height", 5000), ("fps", 60), ("fps", True)]:
            data = self.data(); data[key] = value
            with self.assertRaises(ValueError): motion._validate("motion.metrics", data)
        data = self.data(); data["scenes"] = data["scenes"] * 40
        with self.assertRaises(ValueError): motion._validate("motion.metrics", data)

    def test_missing_facts_and_nonfinite_values(self):
        for value in [float("nan"), float("inf"), -1, "42", True]:
            data = self.data(); data["scenes"][0]["bars"][0]["value"] = value
            with self.assertRaises(ValueError): motion._validate("motion.metrics", data)
        data = self.data(); data["scenes"][0].pop("bars")
        with self.assertRaises(ValueError): motion._validate("motion.metrics", data)

    def test_overflow_rejected(self):
        from PIL import Image, ImageDraw
        with self.assertRaises(ValueError):
            motion._block(ImageDraw.Draw(Image.new("RGB", (320,240))), "Too much text "*200, (0,0,100,20), 24, "white")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe require the rebuilt sandbox")
    def test_actual_mp4_and_editable_storyboard(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"brief.mp4"
            data = self.data()
            result = motion.render("motion.metrics", data, output)
            self.assertEqual(len(result["files"]), 4)
            self.assertTrue(all(Path(p).stat().st_size for p in result["files"]))
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)], check=True, capture_output=True, text=True)
            video = json.loads(probe.stdout)["streams"][0]
            self.assertEqual((video["codec_name"], video["width"], video["height"], int(video["nb_frames"])), ("h264",640,360,6))
            storyboard = json.loads(output.with_suffix(".storyboard.json").read_text(encoding="utf-8"))
            self.assertEqual(storyboard["data"], data)
            with self.assertRaises(ValueError): motion.render("motion.metrics", data, output)
            self.assertFalse(any(p.name.startswith("motion-") for p in Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
