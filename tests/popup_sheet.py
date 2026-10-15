"""Screenshot popups with their GIFs on the emulator, then lay them out on one numbered sheet.

usage: python tests/popup_sheet.py . captures/funny memes [MM:SS[xN] ...]      (default: every joke in the file)

Emulator only: it opens the stand-in apps and resets the count. Needs Pillow.

Each popup is numbered by its line in the jokes file, so "take off 4 and 9" names two lines.
"""
import subprocess
import sys
import time
from pathlib import Path

repo = Path(sys.argv[1])
out = Path(sys.argv[2])
sheet_name = sys.argv[3]
sys.path.insert(0, str(repo / "bin"))
import nomoredoom as cli  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FAKE = "io.github.mendypf.nomoredoom.fakes.FakeActivity"
phone = cli.connect(type("A", (), {"adb": None, "serial": None, "get_adb": False})())
if "emulator" not in (phone.serial or "") and phone.prop("ro.kernel.qemu") != "1":
    sys.exit("This script opens stand-in apps, resets the count and screenshots the screen. "
             "Run it on an emulator, not a real phone.")
out.mkdir(parents=True, exist_ok=True)

text = cli.DEFAULT_GAGS.read_text(encoding="utf-8")
rows = [r for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")]
number = {}
per_step = {}
for i, row in enumerate(rows, 1):
    parts = [p.strip() for p in row.split("|")]
    number[parts[2].rstrip("/").rsplit("-", 1)[-1]] = i
    per_step[int(parts[0].lstrip("+"))] = per_step.get(int(parts[0].lstrip("+")), 0) + 1
limit = 10
wanted = sys.argv[4:] or [f"{limit + over}:00x{count}" for over, count in sorted(per_step.items())]


def shot(name):
    data = subprocess.run([phone.adb, "-s", phone.serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=30).stdout
    (out / f"{name}.png").write_bytes(data)


def wait(test, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        info = cli.status(phone)
        if test(info):
            return info
        time.sleep(0.25)
    return cli.status(phone)


def start():
    phone.shell(f"am start -n com.google.android.youtube/{FAKE} --es screen short")


cli.send(phone, "--ez", "reset", "true")
start()
time.sleep(2.5)
shots = []
seen = set()
for item in wanted:
    at, _, times = item.partition("x")
    minutes, seconds = at.split(":")
    for turn in range(int(times or 1)):
        if int(minutes) >= limit + 10:
            start()  # the goodbye card sends the phone home, so come back for the next one
        # Skipping again drops the card that is up, and the next one takes the next joke of the step.
        cli.send(phone, "--ei", "skip_s", str(int(minutes) * 60 + int(seconds)))
        info = wait(lambda s: "card" in s.get("drawn", "") and s.get("gif", "-") not in seen
                    and s.get("gif", "-").split(":")[-1] in ("playing", "still", "missing", "broken", "no-room"))
        seen.add(info.get("gif", "-"))
        time.sleep(0.9 if int(minutes) >= limit + 10 else 1.3)  # let the stamp land
        n = number.get(info.get("gif", "-").split(":")[0], 0)
        name = f"{sheet_name}-{n:02d}"
        shot(name)
        shots.append((n, name, minutes))
        print(n, "|", info.get("line"), "|", info.get("gif"), "|", info.get("close"))
        if int(minutes) >= limit + 10:
            wait(lambda s: s.get("drawn") == "-", 8)
phone.shell("input keyevent KEYCODE_HOME")
cli.send(phone, "--ez", "reset", "true")

shots.sort()
thumb_w, thumb_h, pad, label = 300, 667, 14, 44
cols = min(9, len(shots))
grid = (len(shots) + cols - 1) // cols
sheet = Image.new("RGB", (pad + cols * (thumb_w + pad), pad + grid * (thumb_h + label + pad)), (24, 24, 24))
draw = ImageDraw.Draw(sheet)
big = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 26)
small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 18)
for i, (n, name, minutes) in enumerate(shots):
    image = Image.open(out / f"{name}.png").convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)
    x = pad + (i % cols) * (thumb_w + pad)
    y = pad + (i // cols) * (thumb_h + label + pad)
    draw.text((x, y + 4), f"{n}", fill=(255, 255, 255), font=big)
    draw.text((x + 46, y + 12), f"at {minutes} minutes", fill=(170, 170, 170), font=small)
    sheet.paste(image, (x, y + label))
sheet.save(out / f"{sheet_name}.png")
print("sheet", out / f"{sheet_name}.png", len(shots))
