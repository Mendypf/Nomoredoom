#!/usr/bin/env python3
"""End-to-end test on an emulator: the real app, real Android, stand-in short-video apps.

It installs nothing by itself. Before running it:

    ./gradlew :app:assembleRelease :fakes:assembleDebug
    adb install -r fakes/build/outputs/apk/<each>/debug/*.apk     (emulator only)
    python bin/nomoredoom.py install

Then:  python tests/e2e.py [--shots DIR]

Every check drives the phone the way a person would (opens a screen, presses a
button, turns the screen off) and then asks two independent witnesses what
happened: the app's own report, and Android's list of windows on screen.
"""
from __future__ import annotations

import argparse
import base64
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import nomoredoom as cli  # noqa: E402

FAKE = "io.github.mendypf.nomoredoom.fakes.FakeActivity"
YOUTUBE, INSTAGRAM, TIKTOK = "com.google.android.youtube", "com.instagram.android", "com.zhiliaoapp.musically"
CALLER = "5550100"

# Two tiny two-frame GIFs made for this test: one wide (48 x 27), one tall (27 x 96).
WIDE_GIF = (
    "R0lGODlhMAAbAIEAAFo+uAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQAFAAAACwAAAAAMAAbAAAIPAABCBxIsKDBgwgTKlzIsKHDhxAjSpxI"
    "saLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjyqwYEAAh+QQBFAABACwAAAAAMAAbAIGmkfUAAAAAAAAAAAAIPAABCBxIsKDBgwgTKlzIsKHD"
    "hxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjyqwYEAA7"
)
TALL_GIF = (
    "R0lGODlhGwBgAIEAACoOawAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQAFAAAACwAAAAAGwBgAAAIVAABCBxIsKDBgwgTKlzIsKHDhxAjSpxI"
    "saLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJs6bNmzhz6tzJs6fPn0CDCh1KtKjRoyQDAgAh+QQBFAABACwAAAAAGwBgAIHXzP8AAAAA"
    "AAAAAAAIVAABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJs6bNmzhz6tzJs6fPn0CDCh1KtKjR"
    "oyQDAgA7"
)


def box(state: dict, name: str) -> tuple[int, ...]:
    """A box the app reported, as (left, top, right, bottom); zeros when it is not there."""
    return tuple(int(n) for n in state.get(name, "0,0,0,0").split(","))


def inside(inner: tuple[int, ...], outer: tuple[int, ...]) -> bool:
    return inner[2] > inner[0] and inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


class Run:
    def __init__(self, phone: cli.Phone, shots: Path | None):
        self.phone = phone
        self.shots = shots
        self.failures: list[str] = []
        self.passed = 0

    # ---- driving the phone

    def open(self, package: str, screen: str, tappable: bool = False) -> None:
        extra = " --ez tappable true" if tappable else ""
        self.phone.shell(f"am start -n {package}/{FAKE} --es screen {screen}{extra}")

    def size(self) -> tuple[int, int]:
        width, height = self.phone.shell("wm size").split()[-1].split("x")
        return int(width), int(height)

    def home(self) -> None:
        self.phone.shell("input keyevent KEYCODE_HOME")

    def tap(self, spot: str) -> None:
        x, y = spot.split(",")
        self.phone.shell(f"input tap {x} {y}")

    def skip(self, to: str) -> None:
        minutes, _, seconds = to.partition(":")
        cli.send(self.phone, "--ei", "skip_s", str(int(minutes) * 60 + int(seconds)))

    def shot(self, name: str) -> None:
        if not self.shots:
            return
        self.shots.mkdir(parents=True, exist_ok=True)
        time.sleep(0.6)  # let the fade-in finish
        cmd = [self.phone.adb, "-s", self.phone.serial, "exec-out", "screencap", "-p"]
        (self.shots / f"{name}.png").write_bytes(subprocess.run(cmd, capture_output=True, timeout=30).stdout)

    # ---- the two witnesses

    def app(self) -> dict:
        return cli.status(self.phone)

    def overlay_windows(self) -> int:
        """How many Nomoredoom windows Android itself says are on screen."""
        out = self.phone.shell("dumpsys window windows", check=False)
        return sum(1 for line in out.splitlines() if "Window{" in line and " Nomoredoom}" in line)

    def touch_map(self) -> list[tuple[str, tuple[int, ...], tuple[int, ...] | None]]:
        """Android's own list of who gets a touch, top window first: (title, frame, touchable part or None)."""
        rows = []
        box = r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]"
        pattern = re.compile(rf"\s+\d+: name=\S+ (.*?), id=\d+.*?frame={box}.*?touchableRegion=(?:<empty>|{box})")
        for line in self.phone.shell("dumpsys input", check=False).splitlines():
            found = pattern.match(line)
            if found:
                numbers = found.groups()[1:]
                frame = tuple(int(n) for n in numbers[:4])
                touch = tuple(int(n) for n in numbers[4:]) if numbers[4] is not None else None
                rows.append((found.group(1), frame, touch))
        return rows

    def overlay_frames(self) -> list[tuple[int, ...]]:
        """Where Android itself says each Nomoredoom window is: (left, top, right, bottom)."""
        return [frame for title, frame, _ in self.touch_map() if title == "Nomoredoom"]

    def ring(self) -> bool:
        """Make the emulator's phone ring. False when this emulator has no phone line."""
        done = subprocess.run([self.phone.adb, "-s", self.phone.serial, "emu", "gsm", "call", CALLER],
                              capture_output=True, text=True, timeout=30)
        return done.returncode == 0 and "KO" not in done.stdout

    def hang_up(self) -> None:
        subprocess.run([self.phone.adb, "-s", self.phone.serial, "emu", "gsm", "cancel", CALLER],
                       capture_output=True, text=True, timeout=30)

    def wait_for_banner(self, seconds: float = 12.0) -> int | None:
        """The bottom edge of the call banner, read from Android's touch map (not from the app)."""
        _, height = self.size()
        deadline = time.time() + seconds
        while time.time() < deadline:
            for title, _, touch in self.touch_map():
                if title == "NotificationShade" and touch and 150 < touch[3] < height // 2:
                    time.sleep(1.0)  # let it finish sliding in
                    return touch[3]
            time.sleep(0.5)
        return None

    def in_front(self) -> str:
        out = self.phone.shell("dumpsys window displays", check=False)
        focus = [line for line in out.splitlines() if "mCurrentFocus" in line or "mFocusedApp" in line]
        return " ".join(focus)

    # ---- checking

    def wait(self, what: str, test, seconds: float = 8.0) -> bool:
        deadline = time.time() + seconds
        last = None
        while time.time() < deadline:
            last = self.app()
            if test(last):
                self.passed += 1
                print(f"  ok    {what}")
                return True
            time.sleep(0.4)
        self.failures.append(f"{what}  (app says: {last})")
        print(f"  FAIL  {what}  (app says: {last})")
        return False

    def check(self, what: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"  ok    {what}")
        else:
            self.failures.append(f"{what}  {detail}")
            print(f"  FAIL  {what}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial")
    parser.add_argument("--adb")
    parser.add_argument("--shots", type=Path, help="save a screenshot of each popup here")
    args = parser.parse_args()
    phone = cli.connect(args)
    if "emulator" not in (phone.serial or "") and phone.prop("ro.kernel.qemu") != "1":
        print("This test opens stand-in apps and resets the count. Run it on an emulator, not a real phone.")
        return 2
    run = Run(phone, args.shots)

    cli.switch_on(phone)
    cli.wait_until_running(phone)
    run.check("the popups are off as the app comes", run.app().get("popups") == "false", str(run.app().get("popups")))
    cli.send(phone, "--ez", "popups", "true", "--ei", "limit", "10", "--ei", "fresh", "60", "--ez", "home", "true",
             "--es", "rules_b64", '""')
    cli.clear_gags(phone)  # the built-in lines, no GIFs, until the section that tests them
    cli.send(phone, "--ei", "speed", "1", "--ez", "reset", "true")
    run.home()

    print("What counts as a short-video screen")
    for package, name in ((YOUTUBE, "Shorts"), (INSTAGRAM, "Reels")):
        run.open(package, "home")
        time.sleep(1.5)
        run.check(f"{name}: another screen with a hidden copy of the player does not count",
                  run.app().get("watching") == "-")
        if package == YOUTUBE:
            run.open(package, "small")
            time.sleep(1.5)
            run.check(f"{name}: a shelf-sized view with the player's name does not count",
                      run.app().get("watching") == "-")
        run.open(package, "short")
        run.wait(f"{name}: the full-screen player counts", lambda s, n=name: s.get("watching") == n)
        run.home()
        run.wait(f"{name}: going to the home screen stops the count", lambda s: s.get("watching") == "-", 4)
    run.open(TIKTOK, "home")
    run.wait("TikTok: any screen of the app counts", lambda s: s.get("watching") == "TikTok")
    run.phone.shell("am start -a android.settings.SETTINGS")
    run.wait("another app in front stops the count", lambda s: s.get("watching") == "-", 4)
    run.home()

    print("Counting")
    cli.send(phone, "--ez", "reset", "true")
    run.open(YOUTUBE, "short")
    run.wait("starts counting", lambda s: s.get("watching") == "Shorts")
    time.sleep(6)
    used = int(run.app().get("sitting_s", -1))
    run.check("about one second per second", 4 <= used <= 8, f"{used}s after ~6s")
    run.check("nothing is drawn before the limit", run.overlay_windows() == 0 and run.app().get("drawn") == "-")
    run.phone.shell("input keyevent KEYCODE_SLEEP")
    run.wait("screen off stops the count", lambda s: s.get("watching") == "-", 5)
    frozen = int(run.app().get("sitting_s", -1))
    time.sleep(3)
    run.check("no time passes with the screen off", int(run.app().get("sitting_s", -2)) == frozen)
    run.phone.shell("input keyevent KEYCODE_WAKEUP")
    run.phone.shell("wm dismiss-keyguard", check=False)
    run.wait("screen back on, counting again", lambda s: s.get("watching") == "Shorts", 10)

    print("The popups, in order")
    run.skip("9:57")
    run.wait("the pill at 10 minutes", lambda s: "pill" in s.get("drawn", ""), 10)
    run.shot("1-pill")
    run.check("Android shows one Nomoredoom window", run.overlay_windows() == 1, f"{run.overlay_windows()} windows")
    run.wait("the pill leaves by itself", lambda s: "pill" not in s.get("drawn", "x"), 10)

    run.skip("10:58")
    run.wait("a card at 11 minutes, its X ready at once", lambda s: s.get("card") == "tap" and s.get("close") == "ready", 10)
    run.shot("2-first-card")
    held = int(run.app().get("sitting_s", 0))
    time.sleep(3)
    run.check("time stands still while the card is up", int(run.app().get("sitting_s", -1)) == held)
    run.tap(run.app()["close_at"])
    run.wait("a tap on the X closes it", lambda s: s.get("card") == "-" and "card" not in s.get("drawn", ""))

    run.skip("12:00")
    run.wait("a card at 12 minutes, its X still counting down", lambda s: s.get("card") == "hold" and s.get("close") == "wait", 10)
    run.shot("3-countdown-card")
    run.wait("three seconds later the X is ready", lambda s: s.get("close") == "ready", 6)
    run.tap(run.app()["close_at"])
    run.wait("and a tap on it closes the card", lambda s: s.get("card") == "-")
    run.check("the screen stays dimmed between cards", "dim" in run.app().get("drawn", ""))
    run.shot("4-dimmed")

    run.skip("16:00")
    run.wait("a later card with a longer countdown", lambda s: s.get("card") == "hold" and s.get("close") == "wait", 10)
    run.tap(run.app()["close_at"])
    time.sleep(1)
    run.check("a tap on the X before the countdown ends does nothing", run.app().get("card") == "hold")
    run.home()
    run.wait("leaving with the phone's own Home takes the card off", lambda s: s.get("watching") == "-" and s.get("drawn") == "-")
    run.check("the home screen is in front", "launcher" in run.in_front().lower(), run.in_front())
    run.check("no Nomoredoom window is left on the home screen", run.overlay_windows() == 0)

    run.open(YOUTUBE, "short")
    run.skip("20:00")
    run.wait("the goodbye card at 20 minutes", lambda s: s.get("card") == "farewell", 10)
    run.shot("5-going-home")
    run.wait("then it leaves by itself", lambda s: s.get("watching") == "-" and s.get("drawn") == "-", 10)
    run.check("the home screen is in front again", "launcher" in run.in_front().lower(), run.in_front())

    print("Never a trap")
    width, height = run.size()
    cli.send(phone, "--ez", "reset", "true")
    run.open(YOUTUBE, "short", tappable=True)
    run.skip("12:00")
    run.wait("a card is up over an app that reacts to touches", lambda s: s.get("card") == "hold" and "close_at" in s, 10)
    run.tap(f"{width // 2},{height // 4}")
    run.tap(f"{width // 2},{height // 2}")
    time.sleep(1.2)
    state = run.app()
    run.check("touches on the backdrop and on the card do not reach the app underneath",
              state.get("watching") == "Shorts" and state.get("card") == "hold", str(state))
    if phone.shell("settings get secure navigation_mode", check=False).strip() in ("0", "null", ""):
        density = int(phone.shell("wm density").split()[-1])
        run.tap(f"{width // 2},{height - 24 * density // 160}")  # the middle of the 48dp button bar
        run.wait("the phone's own Home button still works with a card up",
                 lambda s: s.get("watching") == "-" and s.get("drawn") == "-", 5)
        run.check("and it really went to the home screen", "launcher" in run.in_front().lower(), run.in_front())
    else:
        print("  skip  the Home button check needs the three-button bar; this phone uses gestures")
        run.home()
    run.open(YOUTUBE, "short", tappable=True)
    run.wait("the unanswered card is back on return", lambda s: s.get("card") == "hold" and "card" in s.get("drawn", ""), 8)
    run.phone.shell("input keyevent KEYCODE_BACK")
    run.wait("the Back key still reaches the app under a card", lambda s: s.get("watching") == "-" and s.get("drawn") == "-", 5)
    run.open(YOUTUBE, "short", tappable=True)
    run.wait("card again, and its X comes ready", lambda s: s.get("card") == "hold" and s.get("close") == "ready", 12)
    run.tap(run.app()["close_at"])
    run.wait("closed; only the dimming is left", lambda s: s.get("drawn") == "dim", 5)
    run.tap(f"{width // 2},{height // 4}")
    run.wait("a touch goes through the dimmed screen to the app", lambda s: s.get("watching") == "-", 5)
    run.home()

    print("A card over a thin player")
    cli.send(phone, "--ez", "reset", "true")
    run.open(INSTAGRAM, "short")
    run.skip("12:00")
    run.wait("a card comes up over Reels, where the player is a thin strip under the card",
             lambda s: s.get("card") == "hold" and "card" in s.get("drawn", ""), 10)
    seen = []
    for _ in range(6):
        time.sleep(0.7)
        seen.append(run.app())
    run.check("our own card does not hide the player from us: it stays up, steady",
              all(s.get("watching") == "Reels" and "card" in s.get("drawn", "") for s in seen),
              str([(s.get("watching"), s.get("drawn")) for s in seen]))
    run.home()

    print("Staying out of the system's way")
    cli.send(phone, "--ez", "reset", "true")
    run.open(YOUTUBE, "short")
    run.skip("12:00")
    run.wait("a card is up", lambda s: s.get("card") == "hold" and "card" in s.get("drawn", ""), 10)
    left, top, right, bottom = (int(n) for n in run.app()["safe"].split(","))
    run.check("the app keeps its popups below the status bar and above the button bar",
              top > 0 and (bottom < height or phone.shell("settings get secure navigation_mode", check=False).strip() == "2"),
              run.app()["safe"])
    state = run.app()
    run.check("the card and its X are inside that space",
              inside(box(state, "card_box"), (left, top, right, bottom)) and top < int(state["close_at"].split(",")[1]) < bottom,
              str(state))
    frames = run.overlay_frames()
    run.check("and Android's own touch map agrees",
              bool(frames) and all(f[1] >= top and f[3] <= bottom for f in frames), f"{frames} vs safe {top}..{bottom}")
    bars = [touch for title, _, touch in run.touch_map() if title in ("StatusBar", "Taskbar", "NavigationBar0") and touch]
    run.check("no Nomoredoom window overlaps the status bar or the button bar",
              bool(bars) and not any(f[1] < b[3] and b[1] < f[3] for f in frames for b in bars), f"{frames} vs bars {bars}")
    run.shot("6-card-clear-of-the-bars")

    if run.ring():
        banner = run.wait_for_banner()
        if banner:
            run.wait("a ringing phone: the popups move down to leave the call banner free",
                     lambda s: int(s.get("safe", "0,0,0,0").split(",")[1]) >= banner and "card" in s.get("drawn", ""), 6)
            frames = run.overlay_frames()
            run.check("and Android's touch map shows nothing of ours over the banner",
                      bool(frames) and all(f[1] >= banner for f in frames), f"{frames} vs banner bottom {banner}")
            run.shot("7-call-banner-left-free")
        else:
            print("  skip  this emulator showed no call banner")
        run.hang_up()
        run.wait("call over: the popups take the space back",
                 lambda s: int(s.get("safe", "0,9999,0,0").split(",")[1]) == top, 10)
    else:
        print("  skip  this emulator cannot ring")

    run.phone.shell("cmd statusbar expand-notifications")
    run.wait("pulling down the notification shade takes the popups off",
             lambda s: s.get("drawn") == "-" and s.get("covered") == "true", 6)
    run.check("and Android agrees nothing of ours is drawn", run.overlay_windows() == 0)
    held = int(run.app().get("sitting_s", -1))
    time.sleep(2.5)
    run.check("no time is counted under the shade", int(run.app().get("sitting_s", -2)) == held)
    run.phone.shell("cmd statusbar collapse")
    run.wait("closing the shade brings the card back", lambda s: "card" in s.get("drawn", ""), 8)
    run.home()

    print("The jokes")
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "wide.gif").write_bytes(base64.b64decode(WIDE_GIF))
        (Path(folder) / "tall.gif").write_bytes(base64.b64decode(TALL_GIF))
        (Path(folder) / "fake.gif").write_bytes(b"GIF89a but the rest is not a picture")
        pack = Path(folder) / "gags.txt"
        pack.write_text(
            "+1 | Wide one, {minutes} minutes. | wide.gif\n"
            "+2 | Tall one. | tall.gif | stamp=bottom-left\n"
            "+4 | Words only.\n"
            "+6 | Broken one. | fake.gif\n",
            encoding="utf-8",
        )
        said = cli.push_gags(phone, pack)
    run.check("a jokes file and its three GIFs go over the cable", said.startswith("3 GIFs"), said)
    run.check("the app sees them in its own folder", run.app().get("gifs_on_phone") == "3", str(run.app().get("gifs_on_phone")))
    cli.send(phone, "--ez", "reset", "true")
    run.open(YOUTUBE, "short")
    run.skip("11:00")
    run.wait("a card plays its GIF and fills the minutes into its line",
             lambda s: s.get("gif") == "wide:playing" and s.get("line") == "Wide one, 11 minutes.", 10)
    state = run.app()
    safe = box(state, "safe")
    card, gif = box(state, "card_box"), box(state, "gif_box")
    run.check("the GIF fills the card from edge to edge, inside the free space",
              inside(card, safe) and gif[0] == card[0] and gif[2] == card[2] and gif[1] == card[1], str(state))
    run.check("the X sits on the GIF's top right corner",
              gif[0] + (gif[2] - gif[0]) * 3 // 4 < int(state["close_at"].split(",")[0]) < gif[2]
              and gif[1] < int(state["close_at"].split(",")[1]) < gif[3], str(state))
    run.shot("8-gif-card")
    run.skip("12:00")
    run.wait("a tall GIF plays too", lambda s: s.get("gif") == "tall:playing", 10)
    state = run.app()
    card, gif = box(state, "card_box"), box(state, "gif_box")
    run.check("a tall GIF is shrunk to fit: the card and its line stay on screen",
              inside(card, safe) and card[3] > gif[3] and (gif[3] - gif[1]) <= (safe[3] - safe[1]) * 0.63, str(state))
    run.shot("9-tall-gif-card")
    run.skip("14:00")
    run.wait("a line without a GIF comes as words", lambda s: s.get("line") == "Words only." and s.get("gif") == "-" and "card" in s.get("drawn", ""), 10)
    run.check("and that card has no picture", "gif_box" not in run.app())
    run.skip("16:00")
    run.wait("a file that is not a picture is left out and the card still comes",
             lambda s: s.get("gif") == "fake:broken" and s.get("line") == "Broken one." and "close_at" in s, 10)
    try:
        cli.send(phone, "--es", "gags_b64", base64.b64encode(b"+1 | fine\nsoon | broken\n").decode())
        run.check("a jokes file with a bad line is refused", False)
    except cli.Problem as problem:
        run.check("a jokes file with a bad line is refused, naming the line", "line 2" in str(problem), str(problem))
    run.check("and the jokes that were there stay", run.app().get("gags") == "pushed")
    cli.clear_gags(phone)
    state = run.app()
    run.check("taking the jokes off leaves the built-in lines and no GIFs",
              state.get("gags") == "built-in" and state.get("gifs_on_phone") == "0", str(state))
    run.skip("11:00")
    run.wait("without its GIF a built-in joke shows its line and the minutes",
             lambda s: s.get("gif", "").endswith(":missing") and len(s.get("line", "")) > 3 and "gif_box" not in s
             and "card" in s.get("drawn", ""), 10)
    run.shot("10-card-without-gif")
    run.home()

    print("The demo")
    cli.send(phone, "--ez", "reset", "true")
    cli.send(phone, "--ez", "demo", "true")
    first = time.monotonic()
    time.sleep(2.0)
    cli.send(phone, "--ez", "demo", "true")  # started over while the first one's note is still up
    time.sleep(max(0.0, first + 3.3 - time.monotonic()))
    run.check("a demo started over keeps its note up for its own three seconds", run.app().get("drawn") == "pill",
              str(run.app().get("drawn")))
    run.wait("then a card whose X is ready at once", lambda s: "card" in s.get("drawn", "") and s.get("close") == "ready", 6)
    run.tap(run.app()["close_at"])
    run.wait("then one with a countdown", lambda s: s.get("close") == "wait", 5)
    run.wait("left alone, the demo plays on to its goodbye card, which has no X",
             lambda s: "card" in s.get("drawn", "") and "close_at" not in s, 14)
    run.wait("and ends by itself", lambda s: s.get("drawn") == "-", 6)
    state = run.app()
    run.check("the demo counted nothing and sent nobody home",
              state.get("sitting_s") == "0" and state.get("card") == "-", str(state))
    cli.send(phone, "--ez", "demo", "true")
    run.open(YOUTUBE, "short")
    run.skip("11:00")
    run.wait("a demo cut short does not swallow the real popups",
             lambda s: s.get("card") == "tap" and "card" in s.get("drawn", "") and s.get("close") == "ready", 10)
    run.home()

    print("While the coding agent works")
    topic = cli.new_topic()  # a fresh topic on ntfy.sh, used by this run only
    tag = "e2e" + str(int(time.time()))[-6:]
    with tempfile.TemporaryDirectory() as folder:
        for name, data in (("wide", WIDE_GIF), ("tall", TALL_GIF)):
            (Path(folder) / f"{name}.gif").write_bytes(base64.b64decode(data))
        pack = Path(folder) / "gags.txt"
        pack.write_text("+1 | Wide one. | wide.gif\n+2 | Tall one. | tall.gif\n", encoding="utf-8")
        cli.push_gags(phone, pack)
    cli.send(phone, "--ez", "reset", "true", "--ez", "popups", "false", "--es", "agent_url", '""')
    run.open(YOUTUBE, "short")
    run.wait("not linked: Shorts plays as before", lambda s: s.get("watching") == "Shorts" and s.get("agent") == "-")
    time.sleep(3)
    run.check("and asks nothing", run.app().get("agent_check") == "-", str(run.app()))
    run.home()
    cli.send(phone, "--es", "agent_url", topic)
    run.check("linked to a topic", run.app().get("agent") == "linked", str(run.app()))
    cli.post_signal(topic, "working", tag)
    run.open(YOUTUBE, "short")
    run.wait("the agent is working: its card covers Shorts at once, long before the limit",
             lambda s: s.get("card") == "agent" and "card" in s.get("drawn", ""), 15)
    run.shot("11-agent-card")
    state = run.app()
    run.check("the card has no X", "close_at" not in state, str(state))
    run.check("with its line", state.get("line") == "Stop doom scrolling", state.get("line", ""))
    run.wait("its first GIF slams on, playing", lambda s: s.get("gif", "").endswith(":playing") and int(s.get("popped", 0)) >= 1, 5)
    held = int(run.app().get("sitting_s", -1))
    run.wait("more pile on fast: six within a few seconds", lambda s: int(s.get("popped", 0)) >= 6, 6)
    run.shot("11-agent-pile")
    early = int(run.app().get("popped", 0))
    run.wait("and they keep coming", lambda s: int(s.get("popped", 0)) >= max(early + 3, 13), 12)
    state = run.app()
    spots = [[int(n) for n in spot.split(",")] for spot in state.get("spots", "").split(";") if spot]
    width, height = run.size()
    run.check("the oldest go, so at most nine are on screen", 1 <= len(spots) <= 9, f"{len(spots)} on screen")
    run.check("each one lands at its own spot", len({(x, y) for x, y, *_ in spots}) == len(spots), str(spots))
    run.check("at its own angle, a little tilted", len({tilt for *_, tilt in spots}) > 1 and all(abs(tilt) <= 14 for *_, tilt in spots), str(spots))
    run.check("and with its middle on the screen", all(0 <= x <= width and 0 <= y <= height for x, y, *_ in spots), str(spots))
    line = box(state, "line_box")
    run.check("the line sits on top in the middle",
              abs((line[0] + line[2]) / 2 - width / 2) < width * 0.1 and abs((line[1] + line[3]) / 2 - height / 2) < height * 0.1, str(line))
    run.check("no time is counted under it", int(run.app().get("sitting_s", -2)) == held)
    run.tap(f"{width // 2},{height // 2}")
    time.sleep(1)
    run.check("touching it does not close it", run.app().get("card") == "agent")
    run.home()
    run.wait("Home still leaves, card and all", lambda s: s.get("watching") == "-" and s.get("drawn") == "-", 5)
    run.open(YOUTUBE, "short")
    run.wait("back in Shorts it is there again", lambda s: s.get("card") == "agent", 8)
    cli.post_signal(topic, "done", tag)
    # The phone asks every 10 s, and ntfy.sh shows a new line in its answers 1-2 s after the post (measured
    # 2026-10-15), so "done" can take 10 + 2 s plus the question itself, which a slow emulator stretches.
    run.wait("the agent is done: the card goes by itself", lambda s: s.get("card") == "-" and s.get("drawn") == "-", 20)
    run.check("and Shorts counts again", int(run.app().get("sitting_s", -1)) >= held)
    run.home()
    cli.send(phone, "--es", "agent_url", '""')
    run.check("unlinked", run.app().get("agent") == "-")
    run.open(YOUTUBE, "short")
    run.wait("popups off and no agent: Shorts plays", lambda s: s.get("watching") == "Shorts")
    run.skip("25:00")
    time.sleep(4)
    state = run.app()
    run.check("popups off: 25 minutes in, nothing covers Shorts",
              state.get("drawn") == "-" and state.get("watching") == "Shorts", str(state))
    run.check("and nobody is sent home", "launcher" not in run.in_front().lower(), run.in_front())
    run.home()
    cli.send(phone, "--ez", "popups", "true", "--ez", "reset", "true")
    cli.clear_gags(phone)

    print("What Android itself says about the app")
    package = phone.shell(f"dumpsys package {cli.PKG}", check=False)
    asked: list[str] = []
    rows = package.splitlines()
    for at, row in enumerate(rows):
        if row.strip() == "requested permissions:":
            indent = len(row) - len(row.lstrip())
            for below in rows[at + 1:]:
                if len(below) - len(below.lstrip()) <= indent:
                    break
                asked.append(below.strip().split(":")[0])
            break
    run.check("it asks for internet and nothing else",
              "versionName=" in package and asked == ["android.permission.INTERNET"], str(asked))
    listening = run.app().get("packages", "").split(",")
    run.check("it is listening to the short-video apps and nothing else", sorted(listening) == sorted(cli.WATCHED), str(listening))

    print("Staying put")
    before = int(run.app().get("sitting_s", -1))
    cli.switch_off(phone)
    time.sleep(2)
    run.check("switched off: not running", run.app().get("connected") == "false")
    cli.switch_on(phone)
    cli.wait_until_running(phone)
    run.check("switched back on: the sitting was kept", abs(int(run.app().get("sitting_s", -99)) - before) <= 2,
              f"{before}s before, {run.app().get('sitting_s')}s after")

    print("Rules pushed from a computer")
    broken = base64.b64encode(b"com.whatsapp | Chats | *\n").decode()
    try:
        cli.send(phone, "--es", "rules_b64", broken)
        run.check("a rule for an app outside the three is refused", False)
    except cli.Problem as problem:
        run.check("a rule for an app outside the three is refused", "com.whatsapp" in str(problem), str(problem))
    renamed = base64.b64encode(
        b"com.google.android.youtube | Shorts | some_new_name\n"
        b"com.instagram.android | Reels | clips_viewer_view_pager\n"
        b"com.zhiliaoapp.musically | TikTok | *\n"
    ).decode()
    cli.send(phone, "--es", "rules_b64", renamed, "--ez", "reset", "true")
    run.open(YOUTUBE, "short")
    time.sleep(2.5)
    run.check("with the view renamed in the rules, the old screen stops matching", run.app().get("watching") == "-")
    cli.send(phone, "--es", "rules_b64", '""')
    run.open(YOUTUBE, "home")
    run.open(YOUTUBE, "short")
    run.wait("back on the built-in rules it matches again", lambda s: s.get("watching") == "Shorts")
    run.home()
    cli.send(phone, "--ez", "reset", "true")

    print(f"\n{run.passed} passed, {len(run.failures)} failed")
    for failure in run.failures:
        print(f"  FAILED: {failure}")
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
