"""Debug: trace why glass mode never activates."""
import io, sys, tempfile, time, threading
from pathlib import Path
from PIL import Image

tmp = Path(tempfile.mkdtemp(prefix="janksy_dbg_"))
sys.stdout = io.StringIO()
import janksy
janksy.SETTINGS_FILE = tmp / "s.json"
janksy.ACCOUNTS_FILE = tmp / "a.json"
janksy.VERSION_CACHE_FILE = tmp / "v.json"
janksy.DATA_DIR = tmp
sys.stdout = sys.__stdout__

bg = tmp / "bg.png"
Image.new("RGBA", (1600, 900), (245, 245, 245, 255)).save(bg)

app = janksy.JanksyLauncher()
app.geometry("1050x700")
app.settings["background"] = str(bg)

print(f"Before _load: glass_mode={app._glass_mode}, glass_color={app.glass_color}")
print(f"bg_path exists: {bg.exists()}")

orig_render = app._render_background_pil
def traced_render(img, w, h):
    print(f"  _render_background_pil called: w={w} h={h}")
    try:
        result = orig_render(img, w, h)
        print(f"  _render_background_pil done: result type={type(result)}")
        return result
    except Exception as e:
        print(f"  _render_background_pil FAILED: {e}")
        raise
app._render_background_pil = traced_render

orig_apply = app._apply_background_pil
def traced_apply(*args, **kwargs):
    print(f"  _apply_background_pil called!")
    result = orig_apply(*args, **kwargs)
    print(f"  _apply_background_pil done: glass_mode={app._glass_mode}, glass_color={app.glass_color}")
    return result
app._apply_background_pil = traced_apply

app._load_background_image()
print(f"After _load_background_image, token={app._bg_render_token}")

for i in range(200):
    app.update()
    app.update_idletasks()
    if app._glass_mode:
        print(f"Glass mode True at iter {i}!")
        break

time.sleep(1)
print(f"Final: glass_mode={app._glass_mode}, glass_color={app.glass_color}")
app.destroy()
