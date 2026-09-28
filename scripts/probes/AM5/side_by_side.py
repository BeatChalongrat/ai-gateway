"""AM5 — side-by-side screenshots for the report (results/sbs/, gitignored).

    python scripts/probes/AM5/side_by_side.py

* desktop | web, one image per step of the live walkthrough (results/walk/live/{desktop,web});
* chat | workspace from the test-stack tour (results/ui_AC): the two surfaces that are now one;
* before (AM4) | after (AM5) for the model menu and the workspace.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(RES, "sbs")
AM4 = os.path.join(HERE, "..", "AM4", "results")
os.makedirs(OUT, exist_ok=True)


def font(size):
    for f in (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\LeelawUI.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def pair(left, right, title_l, title_r, out, height=None):
    a, b = Image.open(left).convert("RGB"), Image.open(right).convert("RGB")
    if height:  # crop long full-page shots to the top
        a, b = a.crop((0, 0, a.width, min(a.height, height))), b.crop((0, 0, b.width, min(b.height, height)))
    w = max(a.width, b.width)
    a = a.resize((w, int(a.height * w / a.width))) if a.width != w else a
    b = b.resize((w, int(b.height * w / b.width))) if b.width != w else b
    h = max(a.height, b.height)
    bar = 44
    img = Image.new("RGB", (w * 2 + 12, h + bar), "white")
    img.paste(a, (0, bar))
    img.paste(b, (w + 12, bar))
    d = ImageDraw.Draw(img)
    f = font(24)
    d.text((12, 8), title_l, fill="black", font=f)
    d.text((w + 24, 8), title_r, fill="black", font=f)
    d.rectangle((w, 0, w + 11, h + bar), fill="#dddddd")
    img.save(out, optimize=True)
    return out


made = []


def both(left, right, title_l, title_r, name, height=None):
    if os.path.exists(left) and os.path.exists(right):
        made.append(pair(left, right, title_l, title_r, os.path.join(OUT, name), height))


live = os.path.join(RES, "walk", "live")
for step in ("00-chat", "01-market", "02-agent-page", "03-workspace-empty", "5a-files-staged", "06-files-tied",
             "08-does-not-tie", "09-draft-files", "20-model-menu", "21-model-choose-your-own", "22-question-card",
             "23-left-out", "24-restored"):
    both(os.path.join(live, "desktop", f"{step}.png"), os.path.join(live, "web", f"{step}.png"),
         f"desktop 0.1.0-e60 · {step}", f"web · {step}", f"live_{step}.png")

tour = os.path.join(RES, "ui_AC")
for name, a, b in (("thread", "A1_chat_thread.png", "A6_workspace_thread_full.png"), ("working", "A1_chat_thread.png", "A5_workspace_working.png"),
                   ("phone", "A8_chat_phone.png", "A7_workspace_phone.png")):
    both(os.path.join(tour, a), os.path.join(tour, b), "chat", "agent workspace", f"one_surface_{name}.png", 1400)
both(os.path.join(tour, "A2_chat_composer_file.png"), os.path.join(tour, "A4_workspace_composer_files.png"), "chat composer", "workspace composer",
     "one_surface_composer.png")

both(os.path.join(AM4, "ui", "member_09_model_control.png"), os.path.join(tour, "B1_workspace_model_menu.png"),
     "before (AM4): the model menu", "after (AM5)", "model_menu_before_after.png", 900)
both(os.path.join(AM4, "walk", "live", "desktop", "03-workspace-empty.png"), os.path.join(live, "desktop", "03-workspace-empty.png"),
     "before (AM4, live desktop): the workspace", "after (AM5, live desktop)", "workspace_before_after.png", 900)

for m in made:
    print(os.path.relpath(m, os.path.join(HERE, "..", "..", "..")))
print(f"{len(made)} images")
