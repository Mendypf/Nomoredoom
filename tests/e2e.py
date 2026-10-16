#!/usr/bin/env python3
"""End-to-end test on an emulator: the real app, real Android, stand-in short-video apps.

It installs nothing by itself. Before running it:

    ./gradlew :app:assembleRelease :fakes:assembleDebug
    adb install -r fakes/build/outputs/apk/<each>/debug/*.apk     (emulator only)
    python bin/nomoredoom.py install

Then:  python tests/e2e.py [--shots DIR]

Every check drives the phone the way a person would (opens a screen, presses a
button, turns the screen off) and then asks two independent witnesses what
happened: the app's own report, and Android's list of windows on screen. The
coding agent is played by this test: it posts "working" and "done" to a fresh
ntfy.sh topic of its own.
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
LINE = "Stop doom scrolling"

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


def today(state: dict, name: str | None = None) -> int:
    """The seconds the app counted today: on one screen (Shorts, Reels, TikTok), or on all of them."""
    total = 0
    for part in state.get("today_s", "").split(","):
        key, _, seconds = part.partition(":")
        if seconds.isdigit() and name in (None, key):
            total += int(seconds)
    return total


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
        """Home, then wait until the app has seen it: it looks once a second, so the next step starts clean."""
        self.phone.shell("input keyevent KEYCODE_HOME")
        deadline = time.time() + 6
        while time.time() < deadline:
            state = self.app()
            if state.get("watching") == "-" and state.get("drawn") == "-":
                return
            time.sleep(0.3)

    def tap(self, spot: str) -> None:
        x, y = spot.split(",")
        self.phone.shell(f"input tap {x} {y}")

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


def memes_up(state: dict) -> bool:
    return state.get("drawn") == "memes" and state.get("cover") == "on"


def memes_on(screen: str):
    """The memes are up over this screen. The app looks once a second, so for a moment after Home it can still
    have the last app's memes up: naming the screen keeps a check from passing on those."""
    return lambda state: memes_up(state) and state.get("watching") == screen


def nothing_drawn(state: dict) -> bool:
    return state.get("drawn") == "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial")
    parser.add_argument("--adb")
    parser.add_argument("--shots", type=Path, help="save a screenshot of the memes at each step here")
    args = parser.parse_args()
    phone = cli.connect(args)
    if "emulator" not in (phone.serial or "") and phone.prop("ro.kernel.qemu") != "1":
        print("This test opens stand-in apps and changes the app's settings. Run it on an emulator, not a real phone.")
        return 2
    run = Run(phone, args.shots)

    cli.switch_on(phone)
    cli.wait_until_running(phone)
    cli.send(phone, "--es", "rules_b64", '""', "--es", "agent_url", '""')
    cli.clear_gags(phone)  # the built-in list and no GIFs, until the section that copies some over
    run.home()
    width, height = run.size()
    three_buttons = phone.shell("settings get secure navigation_mode", check=False).strip() in ("0", "null", "")

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
    run.wait("another app in front stops the count", lambda s: s.get("watching") == "-", 8)
    run.home()

    print("Counting, with no coding agent linked")
    run.open(YOUTUBE, "short")
    run.wait("starts counting", lambda s: s.get("watching") == "Shorts")
    start = today(run.app(), "Shorts")
    time.sleep(6)
    used = today(run.app(), "Shorts") - start
    run.check("about one second per second", 4 <= used <= 8, f"{used}s after ~6s")
    run.check("nothing is drawn", run.overlay_windows() == 0 and nothing_drawn(run.app()))
    run.phone.shell("cmd statusbar expand-notifications")
    run.wait("the notification shade over Shorts is seen", lambda s: s.get("covered") == "true", 6)
    held = today(run.app(), "Shorts")
    time.sleep(2.5)
    run.check("no time is counted under the shade", today(run.app(), "Shorts") == held)
    run.phone.shell("cmd statusbar collapse")
    run.wait("shade closed, counting again", lambda s: s.get("watching") == "Shorts" and s.get("covered") == "false", 8)
    run.phone.shell("input keyevent KEYCODE_SLEEP")
    run.wait("screen off stops the count", lambda s: s.get("watching") == "-", 5)
    frozen = today(run.app(), "Shorts")
    time.sleep(3)
    run.check("no time passes with the screen off", today(run.app(), "Shorts") == frozen)
    run.phone.shell("input keyevent KEYCODE_WAKEUP")
    run.phone.shell("wm dismiss-keyguard", check=False)
    run.wait("screen back on, counting again", lambda s: s.get("watching") == "Shorts", 10)
    run.home()

    print("Linking the coding agent")
    topic = cli.new_topic()  # a fresh topic on ntfy.sh, used by this run only
    tag = "e2e" + str(int(time.time()))[-6:]
    run.open(YOUTUBE, "short")
    run.wait("not linked: Shorts plays", lambda s: s.get("watching") == "Shorts" and s.get("agent") == "-")
    time.sleep(3)
    run.check("and asks nothing", run.app().get("agent_check") == "-", str(run.app()))
    run.home()
    cli.send(phone, "--es", "agent_url", topic)
    run.check("linked to a topic", run.app().get("agent") == "linked", str(run.app()))
    run.open(YOUTUBE, "short")
    run.wait("linked, the agent not working: it asks, and Shorts plays",
             lambda s: s.get("agent_check") == "ok" and s.get("watching") == "Shorts" and nothing_drawn(s), 15)
    run.home()

    print("The memes, while the coding agent works")
    with tempfile.TemporaryDirectory() as folder:
        for name, data in (("wide", WIDE_GIF), ("tall", TALL_GIF)):
            (Path(folder) / f"{name}.gif").write_bytes(base64.b64decode(data))
        pack = Path(folder) / "gags.txt"
        pack.write_text("# two test GIFs\nwide.gif\ntall.gif\n", encoding="utf-8")
        said = cli.push_gags(phone, pack)
    run.check("a memes file and its two GIFs go over the cable", said.startswith("2 GIFs"), said)
    run.check("the app sees them in its own folder", run.app().get("gifs_on_phone") == "2", str(run.app().get("gifs_on_phone")))
    cli.post_signal(topic, "working", tag)
    run.open(YOUTUBE, "short")
    run.wait("the agent is working: the memes cover Shorts within seconds", memes_on("Shorts"), 15)
    held = today(run.app(), "Shorts")
    state = run.app()
    run.check("Android shows one Nomoredoom window", run.overlay_windows() == 1, f"{run.overlay_windows()} windows")
    run.check("with the line on top", state.get("line") == LINE, state.get("line", ""))
    run.wait("the first GIF slams on, playing", lambda s: s.get("gif", "").endswith(":playing") and int(s.get("popped", 0)) >= 1, 5)
    run.wait("more pile on fast: six within a few seconds", lambda s: int(s.get("popped", 0)) >= 6, 6)
    run.shot("1-memes")
    early = int(run.app().get("popped", 0))
    run.wait("and they keep coming", lambda s: int(s.get("popped", 0)) >= max(early + 3, 13), 12)
    state = run.app()
    spots = [[int(n) for n in spot.split(",")] for spot in state.get("spots", "").split(";") if spot]
    run.check("the oldest go, so at most nine are on screen", 1 <= len(spots) <= 9, f"{len(spots)} on screen")
    run.check("each one lands at its own spot", len({(x, y) for x, y, *_ in spots}) == len(spots), str(spots))
    run.check("at its own angle, a little tilted", len({tilt for *_, tilt in spots}) > 1 and all(abs(tilt) <= 14 for *_, tilt in spots), str(spots))
    run.check("and with its middle on the screen", all(0 <= x <= width and 0 <= y <= height for x, y, *_ in spots), str(spots))
    line = box(state, "line_box")
    run.check("the line sits on top in the middle",
              abs((line[0] + line[2]) / 2 - width / 2) < width * 0.1 and abs((line[1] + line[3]) / 2 - height / 2) < height * 0.1, str(line))
    run.check("no time is counted under the memes", today(run.app(), "Shorts") == held, f"{held}s, then {today(run.app(), 'Shorts')}s")
    run.home()
    run.wait("Home still leaves, memes and all", lambda s: s.get("watching") == "-" and nothing_drawn(s), 5)
    run.check("and Android agrees nothing of ours is left", run.overlay_windows() == 0)

    print("Never a trap")
    run.open(YOUTUBE, "short", tappable=True)
    run.wait("the memes are up over an app that reacts to touches", memes_on("Shorts"), 15)
    run.tap(f"{width // 2},{height // 4}")
    run.tap(f"{width // 2},{height // 2}")
    time.sleep(1.2)
    state = run.app()
    run.check("touches on the memes do not reach the app underneath, and do not close them",
              state.get("watching") == "Shorts" and memes_up(state), str(state))
    if three_buttons:
        density = int(phone.shell("wm density").split()[-1])
        run.tap(f"{width // 2},{height - 24 * density // 160}")  # the middle of the 48dp button bar
        run.wait("the phone's own Home button still works with the memes up",
                 lambda s: s.get("watching") == "-" and nothing_drawn(s), 5)
        run.check("and it really went to the home screen", "launcher" in run.in_front().lower(), run.in_front())
    else:
        print("  skip  the Home button check needs the three-button bar; this phone uses gestures")
        run.home()
    run.open(YOUTUBE, "short", tappable=True)
    run.wait("the memes are back on return", memes_on("Shorts"), 15)
    run.phone.shell("input keyevent KEYCODE_BACK")
    run.wait("the Back key still reaches the app under the memes",
             lambda s: s.get("watching") == "-" and nothing_drawn(s), 5)
    run.home()

    print("The memes over a thin player")
    run.open(INSTAGRAM, "short")
    run.wait("the memes come up over Reels, where the player is a thin strip under them", memes_on("Reels"), 15)
    seen = []
    for _ in range(6):
        time.sleep(0.7)
        seen.append(run.app())
    run.check("our own memes do not hide the player from us: they stay up, steady",
              all(s.get("watching") == "Reels" and memes_up(s) for s in seen),
              str([(s.get("watching"), s.get("drawn")) for s in seen]))
    run.home()

    print("Staying out of the system's way")
    run.open(YOUTUBE, "short")
    run.wait("the memes are up", memes_on("Shorts"), 15)
    left, top, right, bottom = (int(n) for n in run.app()["safe"].split(","))
    run.check("the app keeps the memes below the status bar and above the button bar",
              top > 0 and (bottom < height or phone.shell("settings get secure navigation_mode", check=False).strip() == "2"),
              run.app()["safe"])
    state = run.app()
    run.check("the line is inside that space", inside(box(state, "line_box"), (left, top, right, bottom)), str(state))
    frames = run.overlay_frames()
    run.check("and Android's own touch map agrees",
              bool(frames) and all(f[1] >= top and f[3] <= bottom for f in frames), f"{frames} vs safe {top}..{bottom}")
    bars = [touch for title, _, touch in run.touch_map() if title in ("StatusBar", "Taskbar", "NavigationBar0") and touch]
    run.check("no Nomoredoom window overlaps the status bar or the button bar",
              bool(frames) and bool(bars) and not any(f[1] < b[3] and b[1] < f[3] for f in frames for b in bars),
              f"{frames} vs bars {bars}")
    run.shot("2-memes-clear-of-the-bars")

    if run.ring():
        banner = run.wait_for_banner()
        if banner:
            run.wait("a ringing phone: the memes move down to leave the call banner free",
                     lambda s: int(s.get("safe", "0,0,0,0").split(",")[1]) >= banner and memes_up(s), 6)
            frames = run.overlay_frames()
            run.check("and Android's touch map shows nothing of ours over the banner",
                      bool(frames) and all(f[1] >= banner for f in frames), f"{frames} vs banner bottom {banner}")
            run.shot("3-call-banner-left-free")
        else:
            print("  skip  this emulator showed no call banner")
        run.hang_up()
        run.wait("call over: the memes take the space back",
                 lambda s: int(s.get("safe", "0,9999,0,0").split(",")[1]) == top, 10)
    else:
        print("  skip  this emulator cannot ring")

    run.phone.shell("cmd statusbar expand-notifications")
    run.wait("pulling down the notification shade takes the memes off",
             lambda s: nothing_drawn(s) and s.get("covered") == "true", 6)
    run.check("and Android agrees nothing of ours is drawn", run.overlay_windows() == 0)
    run.phone.shell("cmd statusbar collapse")
    run.wait("closing the shade brings the memes back", memes_on("Shorts"), 10)
    run.home()

    print("GIFs that will not open, or are not there")
    try:
        cli.send(phone, "--es", "gags_b64", base64.b64encode(b"wide.gif\nnot a gif\n").decode())
        run.check("a memes file with a bad line is refused", False)
    except cli.Problem as problem:
        run.check("a memes file with a bad line is refused, naming the line", "line 2" in str(problem), str(problem))
    state = run.app()
    run.check("and the memes that were there stay", state.get("gags") == "pushed" and state.get("gifs_on_phone") == "2", str(state))
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "fake.gif").write_bytes(b"GIF89a but the rest is not a picture")
        pack = Path(folder) / "gags.txt"
        pack.write_text("fake.gif\n", encoding="utf-8")
        said = cli.push_gags(phone, pack)
    run.check("a file that only starts like a GIF still goes over the cable", said.startswith("1 GIF "), said)
    run.check("and the GIFs the new file does not name come off the phone", run.app().get("gifs_on_phone") == "1",
              str(run.app().get("gifs_on_phone")))
    run.open(YOUTUBE, "short")
    run.wait("a file that is not a picture is left out, and the memes still cover Shorts with their line",
             lambda s: memes_on("Shorts")(s) and s.get("gif") == "fake:broken" and s.get("popped") == "0" and s.get("line") == LINE, 15)
    run.home()
    cli.clear_gags(phone)
    state = run.app()
    run.check("taking the memes off leaves the built-in list and no GIFs",
              state.get("gags") == "built-in" and state.get("gifs_on_phone") == "0", str(state))
    run.open(YOUTUBE, "short")
    run.wait("with no GIFs on the phone, the purple screen and its line still cover Shorts",
             lambda s: memes_on("Shorts")(s) and s.get("gif") == "-" and s.get("popped") == "0" and s.get("line") == LINE, 15)
    run.shot("4-memes-without-gifs")

    print("When the coding agent is done")
    held = today(run.app(), "Shorts")
    cli.post_signal(topic, "done", tag)
    # The phone asks every 10 s, and ntfy.sh shows a new line in its answers 1-2 s after the post (measured
    # 2026-10-15), so "done" can take 10 + 2 s plus the question itself, which a slow emulator stretches.
    run.wait("the agent is done: the memes go by themselves", lambda s: nothing_drawn(s) and s.get("cover") == "-", 20)
    run.check("and Android agrees nothing of ours is drawn", run.overlay_windows() == 0)
    time.sleep(3)
    run.check("and Shorts counts again", today(run.app(), "Shorts") > held, f"{held}s, then {today(run.app(), 'Shorts')}s")
    run.home()
    cli.send(phone, "--es", "agent_url", '""')
    run.check("unlinked", run.app().get("agent") == "-")
    cli.post_signal(topic, "working", tag)  # the old topic says working again; an unlinked phone must not care
    run.open(YOUTUBE, "short")
    run.wait("unlinked: Shorts plays", lambda s: s.get("watching") == "Shorts")
    time.sleep(4)
    state = run.app()
    run.check("and nothing covers it, though the old topic says working",
              nothing_drawn(state) and state.get("agent_check") == "-" and state.get("watching") == "Shorts", str(state))
    run.home()

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
    before = today(run.app())
    cli.switch_off(phone)
    time.sleep(2)
    run.check("switched off: not running", run.app().get("connected") == "false")
    cli.switch_on(phone)
    cli.wait_until_running(phone)
    run.check("switched back on: today's count was kept", abs(today(run.app()) - before) <= 2,
              f"{before}s before, {today(run.app())}s after")

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
    cli.send(phone, "--es", "rules_b64", renamed)
    run.open(YOUTUBE, "short")
    time.sleep(2.5)
    run.check("with the view renamed in the rules, the old screen stops matching", run.app().get("watching") == "-")
    cli.send(phone, "--es", "rules_b64", '""')
    run.open(YOUTUBE, "home")
    run.open(YOUTUBE, "short")
    run.wait("back on the built-in rules it matches again", lambda s: s.get("watching") == "Shorts")
    run.home()

    print(f"\n{run.passed} passed, {len(run.failures)} failed")
    for failure in run.failures:
        print(f"  FAILED: {failure}")
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
