"""Diagnostic: geometry of the bottom bar + selected pill + launch button."""
import io
import sys
import tempfile
import time
from pathlib import Path
from PIL import Image, ImageGrab

tmp = Path(tempfile.mkdtemp(prefix="janksy_layout_"))
sys.stdout = io.StringIO()
import janksy
janksy.SETTINGS_FILE = tmp / "s.json"
janksy.ACCOUNTS_FILE = tmp / "a.json"
janksy.VERSION_CACHE_FILE = tmp / "v.json"
janksy.DATA_DIR = tmp
sys.stdout = sys.__stdout__

bg = tmp / "bg.png"
Image.new("RGBA", (1600, 900), (245, 245, 245, 255)).save(bg)  # near-white like user

app = janksy.JanksyLauncher()
app.geometry("1050x700")
app.update()
app.settings["background"] = str(bg)

# Bottom-bar-inspired: select a version so the pill shows content
app.select_version("1.21.11")
app.update()

for wname in ("status_label", "progress", "launch_btn"):
    w = getattr(app, wname)
    print(f"{wname}: x={w.winfo_x()} y={w.winfo_y()} w={w.winfo_width()} h={w.winfo_height()}")

# Find the pill card widget
pill = None
def find_pill(w, parent="root"):
    global pill
    for c in w.winfo_children():
        if c is getattr(app, "selected_version_lbl", None):
            pill = c.master
        find_pill(c)
find_pill(app._tab_frames.get("Play"))
if pill is not None:
    print(f"pill card: x={pill.winfo_x()} y={pill.winfo_y()} w={pill.winfo_width()} h={pill.winfo_height()}")
    print(f"pill master (bottom): w={pill.master.winfo_width()} h={pill.master.winfo_height()}")
    svl = app.selected_version_lbl
    print(f"selected_version_lbl: x={svl.winfo_x()} y={svl.winfo_y()} w={svl.winfo_width()} h={svl.winfo_height()} text={svl.cget('text')!r}")
    # icon
    for c in pill.winfo_children():
        print(f"  pill child: {type(c).__name__} w={c.winfo_width()} h={c.winfo_height()} x={c.winfo_x()} y={c.winfo_y()}")

# Screenshot the bottom bar region for a visual
app.update_idletasks()
app.attributes("-topmost", True)
app.update()
time.sleep(0.05)
x0, y0 = app.winfo_rootx(), app.winfo_rooty()
w, h = app.winfo_width(), app.winfo_height()
shot = ImageGrab.grab(bbox=(x0, y0, x0 + w, y0 + h)).convert("RGB")
shot.save(tmp / "shot.png")
print("screenshot saved")

app.destroy()
print("DONE")