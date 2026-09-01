"""Verify selected-version card: renders at real size, no overlap with the
launch button, icon removed, text updates, and layout is stable."""
import io
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="janksy_pill_"))
sys.stdout = io.StringIO()
import janksy
janksy.SETTINGS_FILE = tmp / "s.json"
janksy.ACCOUNTS_FILE = tmp / "a.json"
janksy.VERSION_CACHE_FILE = tmp / "v.json"
janksy.DATA_DIR = tmp
sys.stdout = sys.__stdout__

fails = []
def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)

app = janksy.JanksyLauncher()
app.geometry("1050x700")
app.update()
app.update_idletasks()

# Force full layout: switch away and back so the cached Play tab re-maps
app.switch_tab("Settings")
app.update_idletasks()
app.switch_tab("Play")
app.update()
app.update_idletasks()

# Give the geometry manager several passes
for _ in range(5):
    app.update_idletasks()

svl = app.selected_version_lbl
card = svl.master
print(f"card: w={card.winfo_width()} h={card.winfo_height()} mapped={card.winfo_ismapped()}")
print(f"svl:  w={svl.winfo_width()} h={svl.winfo_height()}")
print(f"launch btn: w={app.launch_btn.winfo_width()} h={app.launch_btn.winfo_height()}")

# 1) The pill renders at a real size (not 1x1)
check("pill renders at real size", card.winfo_width() > 120 and card.winfo_height() > 30,
      f"(w={card.winfo_width()}, h={card.winfo_height()})")

# 2) The blue icon is gone -> card should have exactly 2 children (title + value)
kids = card.winfo_children()
check("icon removed (card has 2 children)", len(kids) == 2,
      f"(children={len(kids)})")
check("no 'pink play' icon label", not any(c.cget("text") in ("▶", ">") for c in kids))

# 3) No overlap with launch button (both in bottom bar, checked via grid cols)
bottom = app.launch_btn.master
col1_x = card.winfo_x()
col2_x = app.launch_btn.winfo_x()
col2_w = app.launch_btn.winfo_width()
print(f"bottom bar: w={bottom.winfo_width()}; pill x={col1_x}; launch x={col2_x}+{col2_w}")
check("pill left of launch button", col1_x + card.winfo_width() <= col2_x,
      f"(pill_right={col1_x + card.winfo_width()}, launch_x={col2_x})")

# 4) text updates on selection
app.select_version("1.21.11")
app.update()
check("pill text updates", svl.cget("text") == "1.21.11", f"(={svl.cget('text')!r})")
check("pill text accent on selection",
      svl.cget("text_color") == app.accent, f"(={svl.cget('text_color')})")

app.destroy()
print("\n" + ("ALL PILL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))