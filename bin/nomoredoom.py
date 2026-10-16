#!/usr/bin/env python3
"""Put Nomoredoom on an Android phone and talk to it over adb: from a computer, or from the phone itself.

    python bin/nomoredoom.py pair [ADDRESS] CODE   over Wi-Fi: pair with the phone's six-digit code
    python bin/nomoredoom.py connect [ADDRESS]     over Wi-Fi: connect to a phone that is paired
    python bin/nomoredoom.py doctor          what is ready and what is missing
    python bin/nomoredoom.py install         put the app on the phone and switch it on
    python bin/nomoredoom.py status          what the app sees right now
    python bin/nomoredoom.py set popups on   popups after 10 minutes of short videos (off unless switched on)
    python bin/nomoredoom.py gags push       copy the popups' GIFs and lines to the phone
    python bin/nomoredoom.py agent link      cover the videos while Claude Code is working
    python bin/nomoredoom.py disconnect      when you are done: switch Wireless debugging off on the phone

Run any command with -h for its options. Standard library only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import ipaddress
import itertools
import json
import os
import platform
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Iterator

PKG = "io.github.mendypf.nomoredoom"
SERVICE = f"{PKG}/{PKG}.WatchService"
RECEIVER = f"{PKG}/.AdbReceiver"
ACTION = f"{PKG}.SET"
WATCHED = {
    "com.instagram.android": "Instagram",
    "com.google.android.youtube": "YouTube",
    "com.zhiliaoapp.musically": "TikTok",
    "com.ss.android.ugc.trill": "TikTok (regional)",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_APK = REPO_ROOT / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
RELEASE_APK = "https://github.com/Mendypf/Nomoredoom/releases/latest/download/nomoredoom.apk"
# SHA-256 of the certificate every published release is signed with. A download signed by any
# other key is not installed. (Android itself checks that the signature covers the whole file.)
RELEASE_CERT = "906ec86208a210b13f67aedd9c1a8314ce669afdd3c2034e99e9b34fefb16381"
SIGNATURE_SCHEMES = (0x7109871A, 0xF05368C0, 0x1B93AD61)  # APK signature scheme v2, v3 and v3.1
MAX_APK = 20 * 1024 * 1024
MAX_ADB_ZIP = 60 * 1024 * 1024
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "nomoredoom"
DEFAULT_GAGS = REPO_ROOT / "app" / "src" / "main" / "res" / "raw" / "gags.txt"
GIPHY_FILE = "https://media.giphy.com/media/{id}/giphy.gif"
MAX_GIF = 12 * 1024 * 1024
# The most GIFs one jokes file may bring, and the most they may weigh together. The built-in file
# has 27, about 40 MB.
MAX_GIFS = 100
MAX_GIFS_TOTAL = 150 * 1024 * 1024
# A rules or jokes file travels inside one adb command, and Windows refuses a command line
# over 32,767 characters. 20 KB of text is 27 KB once encoded.
MAX_PUSHED_TEXT = 20_000
# The name this tool gives GIPHY when it asks for a GIF: its own, and where to find it.
WHO_ASKS = "nomoredoom (+https://github.com/Mendypf/Nomoredoom)"
PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-{os}.zip"


class Problem(Exception):
    """Something the person (or their agent) has to fix. The message says what."""


class Refused(Problem):
    """The app on the phone got the command and said no. Its reason is the message."""


class NoPhone(Problem):
    """adb is here, and no phone is connected to it."""


def plain(text: str, most: int = 200) -> str:
    """Words that came from the phone or from a file, made fit to show: what can be seen, cut to size.

    A person reads them and so does an agent, so nothing rides along unseen: no escape codes, no
    characters that turn the text around, no second line.
    """
    return "".join(ch if ch.isprintable() else " " if ch.isspace() else "" for ch in text)[:most]


# ---------------------------------------------------------------- adb

def on_the_phone() -> bool:
    """True when this tool runs on an Android phone itself (inside Termux), not on a computer."""
    return sys.platform == "android" or Path("/system/bin/app_process").exists()


def adb_candidates(explicit: str | None = None) -> list[str]:
    """Every place adb may be, best first. None of them is the folder this tool was started from."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    on_path = shutil.which("adb")
    if on_path and not Path(on_path).is_absolute():
        on_path = None  # on Windows a search of PATH starts in the current folder
    sdks = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if os.environ.get("LOCALAPPDATA"):
        sdks.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"))
    home = Path.home()
    sdks += [str(home / "Library" / "Android" / "sdk"), str(home / "Android" / "Sdk")]
    found = [explicit, os.environ.get("ADB"), on_path]
    found += [str(Path(sdk) / "platform-tools" / exe) for sdk in sdks if sdk]
    found.append(str(CACHE / "platform-tools" / exe))
    return [candidate for candidate in found if candidate]


def find_adb(explicit: str | None = None) -> str | None:
    return next((candidate for candidate in adb_candidates(explicit) if Path(candidate).is_file()), None)


def download_adb(system: str | None = None) -> str:
    """Google's own adb, unpacked into this tool's cache folder. Nothing is installed system-wide."""
    system = system or ("Android" if on_the_phone() else platform.system())
    if system == "Android":
        raise Problem("on the phone itself adb comes from Termux. Run: pkg install android-tools")
    name = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(system)
    if not name:
        raise Problem(f"no adb download for {system}; install Android platform-tools by hand")
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / "platform-tools.zip"
    archive.write_bytes(download(PLATFORM_TOOLS.format(os=name), MAX_ADB_ZIP))
    with zipfile.ZipFile(archive) as z:
        z.extractall(CACHE)
    archive.unlink()
    adb = CACHE / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    if os.name != "nt":
        adb.chmod(0o755)
    return str(adb)


def download(url: str, limit: int, agent: str | None = None) -> bytes:
    """What is at a web address. A Problem when it does not answer within a minute or is over `limit` bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": agent} if agent else {})
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            data = reply.read(limit + 1)
    except (OSError, http.client.HTTPException) as error:
        raise Problem(f"could not download {url} ({error})") from error
    if len(data) > limit:
        raise Problem(f"{url} is over the {limit // 1024} KB this tool accepts")
    return data


class Phone:
    def __init__(self, adb: str, serial: str | None):
        self.adb = adb
        self.serial = serial

    def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        """What adb printed. `stderr=False` leaves out what it said on the side: warnings, and its own errors."""
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        out = (done.stdout or "") + (done.stderr or "")
        if check and done.returncode != 0:
            raise Problem(f"adb {' '.join(args[:3])} failed: {out.strip()[:400]}")
        return out if stderr else (done.stdout or "")

    def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        return self.run("shell", command, check=check, timeout=timeout, stderr=stderr)

    def prop(self, name: str) -> str:
        return plain(self.shell(f"getprop {name}", check=False).strip(), 80)


def need_adb(args: argparse.Namespace) -> str:
    adb = find_adb(args.adb)
    if not adb and getattr(args, "get_adb", False):
        adb = download_adb()
        print(f"Downloaded Google's adb from dl.google.com into {CACHE}. Nothing was installed system-wide.",
              file=sys.stderr)
    if not adb:
        raise Problem(
            "adb was not found. It is Google's tool for talking to a phone. On a computer, run "
            f"`doctor --get-adb` once to download it (about 15 MB, into {CACHE}), or install Android "
            "platform-tools yourself. Inside Termux on the phone, run: pkg install android-tools"
        )
    return adb


def adb_trouble(said: str) -> str | None:
    """What to do when adb itself cannot start; None when it started."""
    if "CANNOT LINK EXECUTABLE" in said:
        return "adb inside Termux is out of step with the library it needs. Run: pkg install -y libc++"
    return None


def run_adb(adb: str, *args: str, timeout: int = 30) -> str:
    done = subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    said = (done.stdout or "") + (done.stderr or "")
    trouble = adb_trouble(said)
    if trouble:
        raise Problem(trouble)
    return said


def connect(args: argparse.Namespace) -> Phone:
    adb = need_adb(args)

    def identity(name: str) -> str:
        return run_adb(adb, "-s", name, "shell", "getprop", "ro.serialno").strip()

    return Phone(adb, pick_phone(run_adb(adb, "devices"), args.serial, identity, here=on_the_phone()))


def pick_phone(listing: str, serial: str | None, identity=None, here: bool = False) -> str:
    """The one phone to talk to, out of what `adb devices` printed. A Problem when the choice is not clear.

    `identity` tells which names are the same phone: over Wi-Fi adb can list one phone twice, under its
    address and under a name it found on the network. `here` is true when this tool runs on the phone itself.
    """
    rows = [line.split("\t") for line in listing.splitlines()[1:] if "\t" in line]
    if serial:
        rows = [r for r in rows if r[0] == serial]
    ready = [r[0] for r in rows if r[1].strip() == "device"]
    waiting = [r[0] for r in rows if r[1].strip() == "unauthorized"]
    if len(ready) > 1 and identity:
        first_name: dict[str, str] = {}
        for name in ready:
            first_name.setdefault(identity(name) or name, name)
        ready = list(first_name.values())
    if waiting and not ready:
        raise Problem("the phone is plugged in but has not allowed this computer. "
                      "Unlock it and tap Allow on the 'Allow USB debugging?' box.")
    if not ready and here:
        raise NoPhone(
            "nothing is connected yet. This tool is running on the phone itself and reaches it through Wireless "
            "debugging: Settings > About phone > tap 'Build number' seven times, then Settings > System > "
            "Developer options > Wireless debugging, switch it on, tap 'Pair device with pairing code', and run: "
            "pair CODE (the six digits in that box). A phone that is paired already only needs: connect"
        )
    if not ready:
        raise NoPhone(
            "no phone found. Over Wi-Fi: on the phone, Settings > About phone > tap 'Build number' seven times, then "
            "Settings > System > Developer options > turn on 'Wireless debugging', and run pair and connect (pair -h "
            "says how). With a cable: turn on 'USB debugging' in that same menu instead, plug in the cable, and tap "
            "Allow."
        )
    if len(ready) > 1:
        raise Problem(f"more than one phone is connected ({', '.join(ready)}); pick one with -s SERIAL")
    if waiting:
        # An emulator left running would otherwise be taken for the phone that was just plugged in.
        raise Problem(f"{', '.join(waiting)} is plugged in but has not allowed this computer: unlock it and tap Allow "
                      f"on the 'Allow USB debugging?' box. To use {ready[0]} instead, add -s {ready[0]}")
    return ready[0]


# ---------------------------------------------------------------- over Wi-Fi

HERE = "127.0.0.1"  # the phone itself, when this tool runs on it
# Android asks the kernel for any free port for its debugging port and for its pairing box. Linux gives
# such a caller the lower half of this range first, odd numbers before even ones. (An Android 16
# emulator handed out 33187, 38203, 39957, 41605 and 46445.)
KERNEL_PORTS = (32768, 61000)
A_CNXN = 0x4E584E43  # the first word of adb's own hello


def likely_ports(low: int = KERNEL_PORTS[0], high: int = KERNEL_PORTS[1]) -> list[int]:
    """Every port Android may have picked, likeliest first."""
    half = low + (((high - low) >> 2) << 1)
    return [port for start, stop in ((low, half), (half, high)) for odd in (1, 0)
            for port in range(start, stop) if port % 2 == odd]


def listening(host: str, ports: Iterable[int]) -> Iterator[int]:
    """The ports on `host` that take a connection, as they are found.

    Nothing is sent, and a pairing box stays open through it (tried on Android 16).
    """
    def takes(port: int) -> bool:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return True
        except OSError:
            return False

    ports = iter(ports)
    with ThreadPoolExecutor(max_workers=32) as pool:
        while True:
            some = list(itertools.islice(ports, 1024))
            if not some:
                return
            yield from (port for port, taken in zip(some, pool.map(takes, some)) if taken)


def door(host: str, port: int, wait: float = 4) -> str:
    """What is behind a port that is, or was, the phone's debugging port.

    'open': Wireless debugging answers there. 'shut': the phone answered that nothing listens there,
    or something that is not adb does. 'unknown': no answer at all. A phone out of reach gives no
    answer either, so that proves nothing.

    This sends adb's hello, and anything but the pairing handshake closes an open pairing box
    ("Pairing unsuccessful"). So it is only used once pairing is over.
    """
    hello = b"host::\0"
    head = struct.pack("<6I", A_CNXN, 0x01000001, 1024 * 1024, len(hello), sum(hello), A_CNXN ^ 0xFFFFFFFF)
    try:
        with socket.create_connection((host, port), timeout=wait) as talk:
            talk.sendall(head + hello)
            # "STLS", switch to TLS, is what Wireless debugging answers every hello with.
            return "open" if talk.recv(24)[:4] == b"STLS" else "shut"
    except (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        return "shut"
    except OSError:
        return "unknown"


def says_adb(host: str, port: int) -> bool:
    """True when the phone's debugging port is what answers there."""
    return door(host, port, wait=2) == "open"


def close_by(name: str) -> bool:
    """True for an address a phone on the person's own network has: 192.168.x.x, 10.x.x.x and the like."""
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False  # a name. The phone's screen shows numbers
    return address.version == 4 and (address.is_private or address.is_loopback or address.is_link_local)


def address_of(text: str, host: str | None = None, anywhere: bool = False) -> tuple[str, int]:
    """'192.168.1.20:37123' as (host, port). A port alone will do when `host` says whose it is.

    Only an address on the person's own network is taken, unless `anywhere` says otherwise. This tool
    is for the phone in the room, and an address far away is a mistake or a trick more often than not.
    """
    found = re.fullmatch(r"(?:([A-Za-z0-9][A-Za-z0-9.-]*):)?(\d{1,5})", text.strip())
    name = (found.group(1) or host) if found else None
    if not name or not 0 < int(found.group(2)) < 65536:
        raise Problem(f"{text!r} is not an address. It looks like 192.168.1.20:37123: what the phone shows, colon included")
    if not anywhere and not close_by(name):
        raise Problem(f"{name} is not an address on your own network (those look like 192.168.1.20 or 10.0.0.7), and "
                      "this tool only connects to the phone next to you. If your network does hand out other "
                      "addresses, as some universities do, add --any-address")
    return name, int(found.group(2))


def pairing_words(words: list[str]) -> tuple[str | None, str]:
    """What was typed after `pair`, in either order: (where the pairing box is, or None; the six digits)."""
    codes = [word for word in words if re.fullmatch(r"\d{6}", word)]
    rest = [word for word in words if word not in codes]
    if len(codes) != 1 or len(rest) > 1:
        raise Problem("pair takes the six digits in the phone's pairing box and where that box is, like "
                      "pair 192.168.1.20:37123 123456. On the phone itself the six digits are enough.")
    return (rest[0] if rest else None), codes[0]


def pairing_problem(said: str, answers: bool) -> str | None:
    """What went wrong with `adb pair`, for the person holding the phone; None when it worked.

    adb's own words do not tell a wrong code from a closed box, so `answers` does: whether
    anything takes a connection at that address.
    """
    if "Successfully paired" in said:
        return None
    if not answers:
        return ("no pairing box is open at that address. The box closes when Settings leaves the screen, and it "
                "comes back with a new port and a new code: open it again and send both")
    return ("the pairing box did not take that. Read the six digits again: they stay the same while the box is open. "
            "If they match, the port is not the pairing box's. The box shows its own port under the code, a "
            "different one from the Wireless debugging screen behind it")


def connecting_problem(said: str, answers: bool = True, is_adb: bool = True) -> str | None:
    """What went wrong with `adb connect`; None when it worked.

    `answers` says whether anything takes a connection there, and `is_adb` whether what answers is
    the phone's debugging port.
    """
    if "connected to" in said:
        return None
    if "authenticate" in said:
        return "the phone does not know this program yet. Run pair first, with the six digits from 'Pair device with pairing code'"
    if not answers:
        return ("nothing is listening at that address. Check that Wireless debugging is switched on (Android "
                "switches it off when Wi-Fi drops) and read the port again: it is a new one every time the "
                "setting is switched on")
    if not is_adb:
        return ("something answers there, but it is not the phone's debugging port. The pairing box shows one port "
                "and the Wireless debugging screen another: connect takes the one on the screen itself")
    return f"the phone did not let this program in. adb said: {said.strip()[:200]}"


def join(adb: str, host: str, port: int) -> None:
    """`adb connect`, with the reason in plain words when it does not work."""
    said = run_adb(adb, "connect", f"{host}:{port}", timeout=40)
    if "connected to" in said:
        return
    answers = next(iter(listening(host, [port])), None) is not None
    problem = connecting_problem(said, answers, answers and says_adb(host, port))
    run_adb(adb, "disconnect", f"{host}:{port}")  # adb keeps an address that failed on its list, as "offline"
    raise Problem(problem)


def pair_here(adb: str, code: str) -> list[int]:
    """Pair with the pairing box open on this phone, wherever it is. Brings back the open ports that were not it."""
    passed = []
    for port in listening(HERE, likely_ports()):
        try:
            said = run_adb(adb, "pair", f"{HERE}:{port}", code, timeout=15)
        except subprocess.TimeoutExpired:  # something else lives there, and it never answers
            said = ""
        if "Successfully paired" in said:
            return passed
        passed.append(port)
    raise Problem(
        "no pairing box on this phone took that code. The box has to be open while this runs, and it closes when "
        "Settings leaves the screen: put Settings and this app side by side in split screen, tap 'Pair device "
        "with pairing code', and send the six digits showing now. If that still fails, add the port shown "
        "under the code: pair PORT CODE"
    )


def connect_here(adb: str, first: Iterable[int] = ()) -> int:
    """Find this phone's own debugging port and connect to it. Brings back the port."""
    asked: set[int] = set()
    for port in itertools.chain(first, listening(HERE, likely_ports())):
        if port in asked:
            continue
        asked.add(port)
        if says_adb(HERE, port):
            join(adb, HERE, port)
            return port
    raise Problem(
        "found no debugging port on this phone. Is Wireless debugging switched on? It is under Settings > System > "
        "Developer options, and Android switches it off when Wi-Fi drops. If it is on, add the port shown on "
        "that screen: connect PORT"
    )


def wireless_state(phone: Phone) -> str:
    """Whether Wireless debugging is switched on: 'on', 'off', or 'unknown' when the phone did not say.

    Only the phone's own answer counts. An error from adb, or no answer, is not "off".
    """
    try:
        says = phone.shell("settings get global adb_wifi_enabled", check=False, timeout=15, stderr=False).strip()
    except subprocess.TimeoutExpired:
        return "unknown"
    return {"1": "on", "0": "off", "null": "off"}.get(says, "unknown")  # null: a phone that never had the setting


def wireless_off(phone: Phone) -> str:
    """Switches Wireless debugging off on the phone, and says what it then saw: 'off', 'on' or 'unknown'.

    Over Wi-Fi the command cuts the connection it travels on, so the phone may never answer the
    question that follows. Then the proof is its debugging port: the phone answers, twice, that
    nothing listens there any more. Silence is no proof. A phone whose Wi-Fi dropped is silent
    too, with the setting still on.
    """
    try:
        phone.shell("settings put global adb_wifi_enabled 0", check=False, timeout=15)
    except subprocess.TimeoutExpired:
        pass
    state = wireless_state(phone)
    if state != "unknown":  # still in reach (a cable, or the door has not shut yet): the phone knows best
        return state
    where = re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", phone.serial or "")
    if not where:
        return "unknown"  # adb found this phone by a name on the network: there is no port of its own to knock on
    heard: list[str] = []
    for _ in range(4):
        heard.append(door(where.group(1), int(where.group(2))))
        if heard[-2:] == ["shut", "shut"]:
            return "off"
        time.sleep(1)
    return "on" if heard[-1] == "open" else "unknown"


# ---------------------------------------------------------------- talking to the app

def installed(phone: Phone, package: str = PKG) -> bool:
    return f"package:" in phone.shell(f"pm path {package}", check=False)


def lookalikes(phone: Phone) -> list[str]:
    """Apps that look like TikTok under a package name this build does not listen to.

    The phone is asked for names with one of three words in them, never for the list of everything
    the person has installed.
    """
    names: set[str] = set()
    for word in ("tiktok", "musically", "ugc."):
        listing = phone.shell(f"pm list packages {word}", check=False)
        names.update(line.split(":", 1)[1].strip() for line in listing.splitlines() if line.startswith("package:"))
    return sorted(n for n in names if re.fullmatch(r"[A-Za-z0-9_.]+", n)
                  and re.search(r"tiktok|musically|ugc\.(trill|aweme)", n) and n not in WATCHED)


def version_of(phone: Phone, package: str) -> str | None:
    found = re.search(r"versionName=(\S+)", phone.shell(f"dumpsys package {package}", check=False))
    return plain(found.group(1), 40) if found else None


A_SERVICE = re.compile(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+")  # package/class, as Android lists a service


def enabled_services(phone: Phone, to_write: bool = False) -> list[str]:
    """The accessibility services that are switched on, as the phone lists them.

    `to_write` is for when the list is about to be written back with one entry added or taken out.
    Then the read has to succeed and every entry has to look like a service name, or nothing is
    written. One stray line written back would switch off the person's other services, a screen
    reader among them.
    """
    raw = phone.shell("settings get secure enabled_accessibility_services", check=to_write, stderr=False).strip()
    found = [s for s in raw.split(":") if s and s != "null"]
    if to_write and not all(A_SERVICE.fullmatch(s) for s in found):
        raise Problem("the phone's list of accessibility services came back with something in it that is not a "
                      "service name, so nothing was changed. Switch Nomoredoom on or off on the phone itself: "
                      "Settings > Accessibility > Nomoredoom")
    return found


def same_component(a: str, b: str) -> bool:
    def full(c: str) -> str:
        pkg, _, cls = c.partition("/")
        return f"{pkg}/{pkg + cls if cls.startswith('.') else cls}"
    return full(a) == full(b)


def is_on(phone: Phone) -> bool:
    return any(same_component(s, SERVICE) for s in enabled_services(phone))


def status(phone: Phone) -> dict:
    """What the running service reports about itself; {'connected': 'false'} when it is not running."""
    out = phone.shell(f"dumpsys activity service {SERVICE}", check=False)
    said = dict(re.findall(r"^\s*([a-z_]+)=(.*?)\s*$", out, re.M))
    if said.get("connected") != "true":
        return {"connected": "false"}
    # Only the fields this tool knows, each one cut to size: `line` is text out of a jokes file.
    return {name: plain(said[name]) for name in STATUS_FIELDS if name in said}


STATUS_FIELDS = (
    "version", "connected", "watching", "covered", "safe", "sitting_s", "limit_min", "fresh_min", "send_home", "popups", "speed",
    "card", "drawn", "line", "gif", "card_box", "gif_box", "close_at", "close", "gags", "gifs_on_phone", "today_s",
    "rules", "packages", "popped", "spots", "line_box", "agent", "agent_busy", "agent_check", "agent_answer_s", "look",
)


def words_in(path: Path) -> str:
    """A rules or jokes file as text, without the invisible mark some Windows editors put first."""
    if not path.is_file():
        raise Problem(f"{path} is not a file")
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise Problem(f"{path} is not saved as UTF-8. Save it again as UTF-8 (a > in PowerShell 5 writes UTF-16)") from error


def packed(data: bytes, what: str) -> str:
    """A rules or jokes file as one word of an adb command."""
    if len(data) > MAX_PUSHED_TEXT:
        raise Problem(f"the {what} file is {len(data) // 1000} KB; the most that can be sent is "
                      f"{MAX_PUSHED_TEXT // 1000} KB")
    return base64.b64encode(data).decode()


def send(phone: Phone, *extras: str) -> str:
    out = phone.shell(f"am broadcast -n {RECEIVER} -a {ACTION} {' '.join(extras)}")
    found = re.search(r"result=(\d+)(?:, data=\"(.*)\")?", out)
    code, data = (int(found.group(1)), found.group(2) or "") if found else (0, out.strip())
    if code == 1:
        return plain(data, 300)
    if code == 2:
        raise Refused(f"the app refused: {plain(data, 300)}")
    raise Problem("the app did not answer. Is it installed? Run: python bin/nomoredoom.py doctor")


def switch_on(phone: Phone) -> None:
    services = enabled_services(phone, to_write=True)
    # Android 13+ greys out the accessibility switch for apps installed from a file.
    # adb is allowed to lift that for one app; harmless where the rule does not exist.
    phone.shell(f"appops set {PKG} ACCESS_RESTRICTED_SETTINGS allow", check=False)
    if not any(same_component(s, SERVICE) for s in services):
        services.append(SERVICE)
        # Quoted: another app's service can have a $ in its name, which the phone's shell would expand.
        phone.shell(f"settings put secure enabled_accessibility_services {shlex.quote(':'.join(services))}")
    phone.shell("settings put secure accessibility_enabled 1")


def switch_off(phone: Phone) -> None:
    services = enabled_services(phone, to_write=True)
    left = [s for s in services if not same_component(s, SERVICE)]
    if len(left) == len(services):
        return  # it is not on the list: nothing to take out, and nothing is written
    if left:
        phone.shell(f"settings put secure enabled_accessibility_services {shlex.quote(':'.join(left))}")
    else:
        phone.shell("settings delete secure enabled_accessibility_services", check=False)


def wait_until_running(phone: Phone, seconds: int = 15) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        info = status(phone)
        if info.get("connected") == "true":
            return info
        time.sleep(1)
    if not is_on(phone):
        says = plain(phone.shell(f"appops get {PKG} ACCESS_RESTRICTED_SETTINGS", check=False).strip(), 120) or "nothing"
        raise Problem(
            "Android switched Nomoredoom straight back off. Possible causes, most likely first. "
            f"1: Android counts the app as restricted because it came from a file (it says: {says}). The way "
            "past that is 'Allow restricted settings': steps 2 to 4 of 'Workflow: install by hand' in SKILL.md. "
            "2: a work profile or a device admin limits accessibility apps. "
            "3: on Android 17, Advanced Protection with its limit on accessibility apps switched on "
            "(Settings > Security & privacy > Advanced Protection). "
            "Nomoredoom cannot run under 2 or 3 while they are on."
        )
    raise Problem(
        "the app is installed and switched on in settings, but Android has not started it. "
        "Open Settings > Accessibility > Nomoredoom on the phone and check the switch."
    )


# ---------------------------------------------------------------- the jokes and their GIFs

def gag_refs(text: str) -> list[tuple[str, str]]:
    """Every GIF a jokes file asks for, once each: (the name the phone looks for, where to get it).

    The phone checks the whole file itself and refuses a bad one; this only finds the GIFs.
    """
    refs: dict[str, str] = {}
    for row in text.splitlines():
        parts = [part.strip() for part in row.split("|")]
        if row.lstrip().startswith("#") or len(parts) < 3 or not parts[2]:
            continue
        page = re.fullmatch(r"https://(?:www\.)?giphy\.com/gifs/([A-Za-z0-9-]+)/?", parts[2])
        direct = re.fullmatch(r"https://media\d*\.giphy\.com/media/([A-Za-z0-9]+)/[A-Za-z0-9._-]+", parts[2])
        own = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.(?:gif|webp)", parts[2], re.I)
        if page or direct:
            giphy_id = page.group(1).rsplit("-", 1)[-1] if page else direct.group(1)
            refs.setdefault(giphy_id, GIPHY_FILE.format(id=giphy_id))
        elif own:
            refs.setdefault(own.group(1), parts[2])
    return list(refs.items())


def looks_like_a_gif(data: bytes) -> bool:
    return data[:6] in (b"GIF87a", b"GIF89a") or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def fetch_gif(name: str, source: str, beside: Path) -> Path | None:
    """The GIF as a file on this computer: downloaded once from GIPHY, or found next to the jokes file."""
    if not source.startswith("https://"):
        mine = beside / source
        ok = mine.is_file() and mine.stat().st_size <= MAX_GIF and looks_like_a_gif(mine.read_bytes()[:16])
        return mine if ok else None
    cached = CACHE / "gags" / f"{name}.gif"
    if not cached.is_file():
        try:
            data = download(source, MAX_GIF, agent=WHO_ASKS)
        except Problem:
            return None
        if not looks_like_a_gif(data):
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return cached


def files_on_phone(phone: Phone, folder: str) -> dict[str, int]:
    """The GIFs already in the app's folder with their sizes, so one cut short by a pulled cable is sent again."""
    found: dict[str, int] = {}
    for row in phone.shell(f"cd '{folder}' && stat -c '%s %n' *", check=False).splitlines():
        size, _, name = row.strip().partition(" ")
        if size.isdigit() and re.fullmatch(r"[A-Za-z0-9_-]+\.(?:gif|webp)", name):
            found[name] = int(size)
    return found


def gags_folder(phone: Phone) -> str:
    """The app's own folder for GIFs on the phone. A Problem on Android 10 and older, which has none."""
    folder = send(phone, "--ez", "gags_dir", "true").partition("gags_dir=")[2]
    # It goes into shell commands, so it has to look like a plain path and nothing more.
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", folder):
        raise Problem(f"the app named a folder this tool will not use: {folder[:80]!r}")
    return folder


def push_gags(phone: Phone, pack: Path) -> str:
    """Copies a jokes file and its GIFs to the phone. The phone itself never goes online."""
    text = words_in(pack)
    lines = packed(text.encode(), "jokes")
    wanted = gag_refs(text)
    if len(wanted) > MAX_GIFS:
        raise Problem(f"the jokes file names {len(wanted)} different GIFs. The most this tool copies is {MAX_GIFS}")
    try:
        folder = gags_folder(phone)
    except Refused as why:  # Android 10 and older: lines only
        send(phone, "--es", "gags_b64", lines)
        return f"the lines are on the phone, without GIFs ({why})"
    # The phone reads the lines first and refuses a bad file whole. Only for a file it took is anything
    # downloaded, and only then is anything it already has removed.
    send(phone, "--es", "gags_b64", lines)
    found, missing, weight = [], [], 0
    for name, source in wanted:
        local = fetch_gif(name, source, pack.parent)
        if local is None:
            missing.append(name)
            continue
        weight += local.stat().st_size
        if weight > MAX_GIFS_TOTAL:
            raise Problem(f"the GIFs in this jokes file weigh more than {MAX_GIFS_TOTAL // (1024 * 1024)} MB together. "
                          "Its lines are on the phone and none of its GIFs were copied. To go back to the built-in "
                          "set: python bin/nomoredoom.py gags push")
        found.append((name, local))
    on_phone = files_on_phone(phone, folder)
    keep = {name for name, _ in wanted}
    for old in on_phone:
        if old.rsplit(".", 1)[0] not in keep:
            phone.shell(f"rm -f '{folder}/{old}'", check=False)
    for name, local in found:
        target = f"{name}{local.suffix.lower()}"
        if on_phone.get(target) != local.stat().st_size:
            phone.run("push", str(local), f"{folder}/{target}", timeout=180)
    note = f" Could not get: {', '.join(missing)}." if missing else ""
    return f"{len(found)} GIFs and their lines are on the phone.{note}"


def clear_gags(phone: Phone) -> str:
    try:
        folder = gags_folder(phone)
        for old in files_on_phone(phone, folder):
            phone.shell(f"rm -f '{folder}/{old}'", check=False)
    except Refused:  # Android 10 and older: there is no folder to empty
        pass
    return send(phone, "--es", "gags_b64", '""')


# ---------------------------------------------------------------- commands

def cmd_doctor(args: argparse.Namespace) -> int:
    report: dict = {"ready": False}
    try:
        phone = connect(args)
    except Problem as problem:
        report["problem"] = str(problem)
        return finish(args, report, [f"NOT READY  {problem}"])

    lines = []
    release, sdk, model = phone.prop("ro.build.version.release"), phone.prop("ro.build.version.sdk"), phone.prop("ro.product.model")
    report.update(adb=phone.adb, serial=phone.serial, model=model, android=release, sdk=sdk)
    lines.append(f"ok         phone: {model}, Android {release}")
    wireless = wireless_state(phone)
    report["wireless_debugging"] = {"on": True, "off": False}.get(wireless)
    if wireless == "on":
        lines.append(f"NOTE       Wireless debugging is on. {CLOSE_UP}")
    if sdk.isdigit() and int(sdk) < 26:
        lines.append("NOT READY  Nomoredoom needs Android 8 or newer")
        return finish(args, report, lines)

    apps = {name: version_of(phone, pkg) for pkg, name in WATCHED.items() if installed(phone, pkg)}
    report["short_video_apps"] = apps
    lines.append("ok         short-video apps on the phone: " + (", ".join(f"{n} {v}" for n, v in apps.items()) or "none"))
    unwatched = lookalikes(phone)
    report["unwatched_lookalikes"] = unwatched
    if unwatched:
        lines.append(f"NOTE       not watched: {', '.join(unwatched)}. The list of apps is fixed when the app is built: "
                     "add the package to app/src/main/res/xml/watch_service.xml and to rules.txt, then rebuild.")

    report["installed"] = installed(phone)
    if not report["installed"]:
        lines.append("MISSING    Nomoredoom is not installed. Run: python bin/nomoredoom.py install")
        return finish(args, report, lines)
    report["version"] = version_of(phone, PKG)
    lines.append(f"ok         Nomoredoom {report['version']} is installed")

    report["switched_on"] = is_on(phone)
    if not report["switched_on"]:
        lines.append("MISSING    Nomoredoom is switched off. Run: python bin/nomoredoom.py on")
        return finish(args, report, lines)

    info = status(phone)
    report["running"] = info.get("connected") == "true"
    if not report["running"]:
        lines.append("NOT READY  switched on in settings, but Android is not running it. Open Settings > Accessibility > Nomoredoom.")
        return finish(args, report, lines)
    report.update(status=info, ready=True)
    if info.get("popups") == "true":
        lines.append(f"ok         running. Limit {info.get('limit_min')} min, {int(info.get('sitting_s', 0)) // 60} min used this sitting")
    else:
        lines.append("ok         running. It covers the videos while the agent works"
                     + ("" if info.get("agent") == "linked" else ", once it is linked: run agent link"))
    return finish(args, report, lines)


def finish(args: argparse.Namespace, report: dict, lines: list[str]) -> int:
    print(json.dumps(report, indent=2) if args.json else "\n".join(lines))
    return 0 if report.get("ready") else 1


def find_apk(args: argparse.Namespace) -> Path:
    if args.apk:
        apk = Path(args.apk)
        if not apk.is_file():
            raise Problem(f"{apk} is not a file")
        return apk
    if LOCAL_APK.is_file() and not args.release:
        return LOCAL_APK
    CACHE.mkdir(parents=True, exist_ok=True)
    apk = CACHE / "nomoredoom.apk"
    try:
        apk.write_bytes(download(RELEASE_APK, MAX_APK))
    except Problem as problem:
        raise Problem(f"{problem}. Build the app with ./gradlew :app:assembleRelease, or pass --apk PATH.") from problem
    if not signed_only_by(apk, RELEASE_CERT):
        signers = ", ".join(sorted(apk_signers(apk))) or "nobody this tool can read"
        apk.unlink()
        raise Problem(f"the downloaded app is not signed with Nomoredoom's key alone (signed by {signers}); not installing it")
    return apk


def signed_only_by(apk: Path, certificate: str) -> bool:
    return apk_signers(apk) == {certificate}


def about_apk(apk: Path) -> str:
    """One line on the file about to go onto the phone: where it is from, who signed it, and its SHA-256."""
    if apk == CACHE / "nomoredoom.apk":
        origin = "the published release, downloaded just now"
    elif apk == LOCAL_APK:
        origin = "the build in this folder, made " + time.strftime("%Y-%m-%d %H:%M", time.localtime(apk.stat().st_mtime))
    else:
        origin = "the file you named"
    signer = ("signed with Nomoredoom's release key" if signed_only_by(apk, RELEASE_CERT)
              else "not signed with Nomoredoom's release key")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    return f"{apk.name} ({origin}; {apk.stat().st_size // 1000} KB; {signer}; SHA-256 {digest})"


def closing_record(data: bytes) -> int:
    """Where a zip's closing record starts, found the way Android finds it; -1 when there is none.

    A comment may follow the record, and the bytes of another closing record can be put in that
    comment. Android goes by the last record whose comment runs exactly to the end of the file.
    """
    at = len(data) - 22
    lowest = max(0, at - 0xFFFF)
    while at >= lowest:
        at = data.rfind(b"PK\x05\x06", lowest, at + 4)
        if at < 0:
            return -1
        if struct.unpack_from("<H", data, at + 20)[0] == len(data) - at - 22:
            return at
        at -= 1
    return -1


def apk_signers(apk: Path) -> set[str]:
    """SHA-256 of every certificate an APK names as a signer. Empty when it has none or cannot be read.

    Every signature scheme in the file is read (v2, v3, v3.1), because which one a phone goes by
    depends on its Android version: a file could carry one key in the first and another in the next.

    This only reads who signed the file. Whether a signature is good for the file's contents is
    checked by Android when it installs, and it refuses the file if not.
    """
    data = apk.read_bytes()
    end = closing_record(data)  # it says where the file list starts
    if end < 0:
        return set()
    files_at = struct.unpack_from("<I", data, end + 16)[0]
    if files_at < 32 or data[files_at - 16:files_at] != b"APK Sig Block 42":
        return set()
    size = struct.unpack_from("<Q", data, files_at - 24)[0]
    at, stop = files_at - size, files_at - 24  # the signing block is a run of (length, id, value)
    if at < 8:
        return set()
    found: set[str] = set()
    try:
        while at + 12 <= stop:
            length, block_id = struct.unpack_from("<QI", data, at)
            if length < 4 or at + 8 + length > stop:
                raise ValueError("an entry runs past the end of the signing block")
            if block_id in SIGNATURE_SCHEMES:
                signers = _pieces(_piece(data[at + 12:at + 8 + length])[0])
                if not signers:
                    raise ValueError("a signature scheme without a signer")
                for signer in signers:
                    signed_data = _piece(signer)[0]
                    _, after_digests = _piece(signed_data)
                    certificate = _piece(_piece(signed_data, after_digests)[0])[0]  # the first one is the signer's
                    if not certificate:
                        raise ValueError("a signer without a certificate")
                    found.add(hashlib.sha256(certificate).hexdigest())
            at += 8 + length
    except (struct.error, ValueError):
        return set()  # one part that cannot be read, and none of it is believed
    return found


def _piece(data: bytes, at: int = 0) -> tuple[bytes, int]:
    """The length-prefixed piece that starts at `at`, and where the next one starts."""
    end = at + 4 + struct.unpack_from("<I", data, at)[0]
    if end > len(data):
        raise ValueError("a length runs past the end")
    return data[at + 4:end], end


def _pieces(data: bytes) -> list[bytes]:
    found, at = [], 0
    while at < len(data):
        piece, at = _piece(data, at)
        found.append(piece)
    return found


def put_on(phone: Phone, apk: Path, replace: bool) -> None:
    """`adb install`. A Problem when the phone refuses, or holds the install for a tap nobody gives."""
    def install() -> str:
        try:
            return phone.run("install", "-r", str(apk), check=False, timeout=180)
        except subprocess.TimeoutExpired:
            raise Problem(
                "the phone has not finished the install after three minutes. It is probably showing a question: "
                "look at the phone and tap Install, Allow or 'Install anyway' (Play Protect asks about an app it "
                "has not seen before; Xiaomi, Oppo and Vivo phones ask 'Install via USB'), then run install again"
            ) from None

    out = install()
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        if not replace:
            raise Problem("a copy signed with a different key is already on the phone. "
                          "Run again with --replace to remove it first (its minutes and settings are lost).")
        phone.run("uninstall", PKG, check=False)
        out = install()
    if "Success" not in out:
        raise Problem(f"the phone refused the install: {plain(out.strip()[-300:], 300)}")


CLOSE_UP = "When you have finished with the phone, run: python bin/nomoredoom.py disconnect"
FAR_AWAY = ("take an address that is not on a home network. Without this the tool only connects to addresses like "
            "192.168.x.x and 10.x.x.x")
BY_HAND = "Settings > System > Developer options > Wireless debugging"


def cmd_install(args: argparse.Namespace) -> int:
    phone = connect(args)
    apk = find_apk(args)
    print(f"Installing on {phone.prop('ro.product.model')}: {about_apk(apk)}")
    put_on(phone, apk, args.replace)
    switch_on(phone)
    info = wait_until_running(phone)
    if info.get("popups") == "true":
        print(f"Nomoredoom {info.get('version')} is on. Limit: {info.get('limit_min')} minutes of Reels, Shorts and TikTok per sitting.")
    elif info.get("agent") == "linked":
        print(f"Nomoredoom {info.get('version')} is on. It covers Reels, Shorts and TikTok while Claude Code is working.")
    else:
        print(f"Nomoredoom {info.get('version')} is on. It covers Reels, Shorts and TikTok while Claude Code is working, "
              "once this computer is linked: python bin/nomoredoom.py agent link")
    print("Its accessibility service was switched on from here, so Android's own 'full control' box did not show "
          "on the phone. What the app can see with that service is in the README, under 'What it can see'.")
    if not args.no_gifs and DEFAULT_GAGS.is_file():
        try:
            print(push_gags(phone, DEFAULT_GAGS))
        except Problem as problem:
            print(f"The popups will show their lines without GIFs for now ({problem}). Try: python bin/nomoredoom.py gags push")
    wireless = wireless_state(phone)
    if wireless == "on":
        print(f"Wireless debugging is still on. {CLOSE_UP}")
    elif wireless == "unknown":
        print(f"The phone stopped answering, so this tool cannot tell whether Wireless debugging went off. {CLOSE_UP}")
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    where, code = pairing_words([word for word in (args.address, args.code) if word])
    here = on_the_phone()
    if where is None and not here:
        raise Problem("from a computer, pair needs to know where the pairing box is: the address and port shown "
                      "under the six digits, like pair 192.168.1.20:37123 123456")
    adb = need_adb(args)
    passed: list[int] = []
    if where is None:
        host = HERE
        passed = pair_here(adb, code)
    else:
        host, port = address_of(where, host=HERE if here else None, anywhere=args.any_address)
        said = run_adb(adb, "pair", f"{host}:{port}", code)
        if "Successfully paired" not in said:
            raise Problem(pairing_problem(said, answers=next(iter(listening(host, [port])), None) is not None))
    if not (here and host in (HERE, "localhost")):
        print("Paired. Next: connect ADDRESS, with the address and port on the Wireless debugging screen itself. "
              "That port is not the one in the pairing box.")
        return 0
    try:
        port = connect_here(adb, first=passed)
    except Problem as problem:
        raise Problem(f"paired, but then: {problem}") from problem
    print(f"Paired and connected to this phone on port {port}. Next: doctor, then install. {CLOSE_UP}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    here = on_the_phone()
    if not args.address and not here:
        raise Problem("from a computer, connect needs the address and port on the phone's Wireless debugging screen, "
                      "like connect 192.168.1.20:41235")
    adb = need_adb(args)
    if args.address:
        host, port = address_of(args.address, host=HERE if here else None, anywhere=args.any_address)
        join(adb, host, port)
    else:
        host, port = HERE, connect_here(adb)
    print(f"Connected to {host}:{port}. Next: doctor, then install. {CLOSE_UP}")
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    """Closing up: Wireless debugging goes off on the phone, and the person is told what is left."""
    adb = need_adb(args)
    try:
        phone = connect(args)
    except NoPhone:
        print(f"No phone is connected, so this tool cannot check. Look at the phone: {BY_HAND} should be off. "
              "(Android switches it off by itself when Wi-Fi drops or the phone restarts.)")
        return 1
    serial = phone.serial or ""
    over_wifi = bool(re.fullmatch(r"[A-Za-z0-9.-]+:\d{1,5}", serial)) or "_adb-tls-connect" in serial
    if wireless_state(phone) == "off":
        print("Wireless debugging is already off on the phone.")
    else:  # on, or the phone did not say: either way the command goes out, and only what is seen after it counts
        now = wireless_off(phone)
        if now == "on":
            raise Problem(f"could not switch Wireless debugging off from here. Ask the person to switch it off on "
                          f"the phone: {BY_HAND}")
        if now == "unknown":
            if over_wifi:
                run_adb(adb, "disconnect", serial)
            print("This tool sent the command that switches Wireless debugging off, then lost sight of the phone "
                  "before it could see the result. That happens when the setting goes off, and also when Wi-Fi "
                  f"drops. So somebody has to look at the phone: {BY_HAND} should be off.")
            return 1
        print("Wireless debugging is off on the phone. Nothing can connect to it until someone switches it on "
              "again. Nomoredoom keeps running without it.")
        print("This program stays on the phone's list of paired devices, and that list does nothing while the "
              "setting is off. To erase it too: switch Wireless debugging on, tap this program's entry under "
              "'Paired devices', tap Forget, and switch it off again.")
    if over_wifi:
        run_adb(adb, "disconnect", serial)  # or the address stays on this side's list as "offline"
    elif not serial.startswith("emulator-"):
        print("USB debugging is still on: this tool is talking through it. Switch it off on the phone when you "
              "are done: Settings > System > Developer options > USB debugging. Nomoredoom keeps running without it.")
    return 0


def cmd_on(args: argparse.Namespace) -> int:
    phone = connect(args)
    if not installed(phone):
        raise Problem("Nomoredoom is not installed. Run: python bin/nomoredoom.py install")
    switch_on(phone)
    wait_until_running(phone)
    print("Nomoredoom is on.")
    return 0


def cmd_off(args: argparse.Namespace) -> int:
    switch_off(connect(args))
    print("Nomoredoom is off. The app and its settings stay on the phone.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    phone = connect(args)
    if not installed(phone):
        print("Nomoredoom is not on the phone.")
        return 0
    try:
        switch_off(phone)
    except Problem:
        pass  # Android takes a removed app off its list of services by itself
    said = phone.run("uninstall", PKG, check=False)
    if "Success" not in said:
        raise Problem(f"the phone did not remove Nomoredoom. It said: {plain(said.strip(), 200) or 'nothing'}. On the "
                      "phone itself: hold the Nomoredoom icon, tap App info, tap Uninstall")
    print("Nomoredoom is gone from the phone.")
    if AGENT_FILE.exists():
        unlink_computer(linked_settings() or claude_settings())
        print("The link to the coding agent is gone from this computer too.")
    return 0


def on_screen(info: dict) -> str:
    """What Nomoredoom is drawing over the screen right now, in words."""
    drawn = info.get("drawn", "-")
    if "card" in drawn:
        kind = {"hold": "a card to hold", "farewell": "the goodbye card",
                "agent": "the card for while the agent works, with no X"}.get(info.get("card", ""), "a card")
        x = {"ready": "its X is ready to press", "wait": "its X is still counting down"}.get(info.get("close", ""))
        return f"{kind}, {x}" if x else kind
    return "the small note at the top" if "pill" in drawn else "nothing"


def cmd_status(args: argparse.Namespace) -> int:
    info = status(connect(args))
    if args.json:
        print(json.dumps(info, indent=2))
    elif info.get("connected") != "true":
        print("Nomoredoom is not running. Run: python bin/nomoredoom.py doctor")
    else:
        used = int(info.get("sitting_s", 0))
        print(f"watching now : {info.get('watching')}")
        if info.get("popups") == "true":
            print(f"this sitting : {used // 60}:{used % 60:02d} of {info.get('limit_min')}:00")
        else:
            print("popups       : off (python bin/nomoredoom.py set popups on)")
        print(f"on screen    : {on_screen(info)}")
        print(f"gifs on phone: {info.get('gifs_on_phone', '0')} ({info.get('gags')} jokes)")
        print(f"today        : {info.get('today_s') or 'nothing yet'} (seconds)")
        print(f"rules        : {info.get('rules')}")
        if info.get("agent") == "linked":
            print(f"agent        : {'working' if info.get('agent_busy') == 'yes' else 'not working'} "
                  f"(last asked {info.get('agent_answer_s', '-')}s ago: {info.get('agent_check', '-')})")
    return 0 if info.get("connected") == "true" else 1


def cmd_set(args: argparse.Namespace) -> int:
    phone = connect(args)
    if args.name in ("home", "popups"):
        if args.value not in ("on", "off"):
            raise Problem(f"{args.name} takes on or off")
        reply = send(phone, "--ez", args.name, "true" if args.value == "on" else "false")
    else:
        if not args.value.isdigit():
            raise Problem(f"{args.name} takes a number of minutes")
        reply = send(phone, "--ei", args.name, args.value)
    print(reply)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    send(connect(args), "--ez", "demo", "true")
    print("Playing on the phone now, for about 20 seconds: a small note at the top, then three sample popups. "
          "Nothing is counted. Run status while it plays to see what is on the screen.")
    return 0


VIEW_ROWS = (
    re.compile(r"\s*tree [A-Za-z0-9_.]+ window_height=\d+"),
    re.compile(r"\s*tree cut short after \d+ views: the app was slow to answer"),
    re.compile(r"\s*id=[A-Za-z0-9_.:/$-]+ height=\d+% px=\d+x\d+ class=[A-Za-z0-9_.$]+"),
)


def view_rows(out: str) -> list[str]:
    """The rows of `tree` that are shaped like what the app prints. Whatever else is in there is left out."""
    return [row for row in out.splitlines() if any(shape.fullmatch(row) for shape in VIEW_ROWS)]


def cmd_tree(args: argparse.Namespace) -> int:
    rows = view_rows(connect(args).shell(f"dumpsys activity service {SERVICE} tree", check=False))
    print("\n".join(rows) if rows else "Nothing. Open Reels, Shorts or TikTok on the phone first, with Nomoredoom on.")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    if args.action == "reset":
        print(send(connect(args), "--es", "rules_b64", '""'))
        return 0
    if not args.file:
        raise Problem("rules push needs a file")
    rules = packed(words_in(Path(args.file)).encode(), "rules")
    print(send(connect(args), "--es", "rules_b64", rules))
    return 0


def cmd_gags(args: argparse.Namespace) -> int:
    if args.action == "reset":
        print(clear_gags(connect(args)))
        return 0
    pack = Path(args.file) if args.file else DEFAULT_GAGS
    if not pack.is_file():
        raise Problem(f"{pack} is not a file")
    print(push_gags(connect(args), pack))
    return 0


def seconds_of(clock: str) -> int:
    """'11:30' or '11' (whole minutes), as seconds."""
    found = re.fullmatch(r"(\d{1,4})(?::([0-5]?\d))?", clock)
    if not found:
        raise Problem("skip takes minutes and seconds, like 11:30")
    return int(found.group(1)) * 60 + int(found.group(2) or 0)


def cmd_skip(args: argparse.Namespace) -> int:
    total = seconds_of(args.to)
    send(connect(args), "--ei", "skip_s", str(total))
    print(f"This sitting now stands at {total // 60}:{total % 60:02d}.")
    return 0


def cmd_speed(args: argparse.Namespace) -> int:
    print(send(connect(args), "--ei", "speed", str(args.times)))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    send(connect(args), "--ez", "reset", "true")
    print("This sitting starts over at 0:00. Today's total stays as it was.")
    return 0


# ---- the coding agent
#
# While the person's coding agent works, the phone covers Reels, Shorts and TikTok and piles every GIF on it, one
# after another. The two never talk directly: the agent's hooks post one short line to a ntfy topic ("working 3f9a01c2"
# or "done 3f9a01c2", the tag telling two sessions apart), and the phone asks that topic for its last 20 minutes
# while a short video is in front. A session that stops posting (a crash, a laptop asleep) drops out of that window
# by itself, so a working session posts "working" again every few minutes.
#
# ntfy.sh takes 250 messages a day from one address, so a session posts "working" at its first tool call of a turn
# rather than when the turn starts (a quick answer costs nothing), and "done" only after a "working".

NTFY = "https://ntfy.sh"
TOPIC_URL = re.compile(r"https://[A-Za-z0-9.-]{1,253}(?::\d{1,5})?/[A-Za-z0-9_-]{1,64}")
SIGNAL_LINE = re.compile(r"(working|done) ([A-Za-z0-9]{1,32})")
WINDOW_MINUTES = 20  # the phone looks this far back (AgentSignal.WINDOW_MINUTES in the app)
HEARTBEAT_S = 8 * 60  # a working session says so again this often, well inside the window
RETRY_S = 60  # after a post that failed, wait this long before the next try
AGENT_FILE = CACHE / "agent.json"
SESSIONS_FILE = CACHE / "agent-sessions.json"
HOOK_LOG = CACHE / "agent-hook.log"
WORKING_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit")
DONE_EVENTS = ("Stop", "StopFailure", "SessionEnd")
# The events `agent link` wires up in Claude Code: long-standing ones, so an older Claude Code reads the file too.
CLAUDE_HOOKS = {
    "PreToolUse": {"matcher": "*", "async": True, "timeout": 15},
    "Stop": {"async": True, "timeout": 15},
    "SessionEnd": {"timeout": 8},  # not in the background: Claude Code may be gone before it finishes
}


def claude_settings() -> Path:
    """Claude Code's own settings file for this person (not a project's)."""
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(home) if home else Path.home() / ".claude") / "settings.json"


def linked_url() -> str | None:
    try:
        url = json.loads(AGENT_FILE.read_text(encoding="utf-8")).get("url")
    except (OSError, ValueError, AttributeError):
        return None
    return url if isinstance(url, str) and TOPIC_URL.fullmatch(url) else None


def new_topic(server: str = NTFY) -> str:
    """A topic nobody can guess. Whoever knows it can read and post "working" and "done", and nothing else."""
    import secrets
    url = f"{server.rstrip('/')}/nomoredoom-{secrets.token_urlsafe(18)}"
    if not TOPIC_URL.fullmatch(url):
        raise Problem(f"{server} is not a server address like https://ntfy.sh")
    return url


def post_signal(url: str, word: str, tag: str, timeout: float = 5) -> None:
    """Posts "working TAG" or "done TAG" to the topic. OSError when it did not go through."""
    if word not in ("working", "done") or not SIGNAL_LINE.fullmatch(f"{word} {tag}"):
        raise ValueError(f"not a signal: {word} {tag}")
    request = urllib.request.Request(url, data=f"{word} {tag}".encode(), method="POST",
                                     headers={"User-Agent": WHO_ASKS, "Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            reply.read(4096)
    except http.client.HTTPException as error:
        raise OSError(str(error)) from error


def read_signal(url: str) -> dict[str, bool]:
    """Each session the topic heard from in the phone's window, and whether its last word was "working"."""
    raw = download(f"{url}/raw?poll=1&since={WINDOW_MINUTES}m", 64 * 1024, WHO_ASKS).decode("utf-8", "replace")
    last: dict[str, bool] = {}
    for line in raw.splitlines():
        found = SIGNAL_LINE.fullmatch(line.strip())
        if found:
            last[found.group(2)] = found.group(1) == "working"
    return last


def session_tag(session_id: str) -> str:
    """Tells two sessions apart without saying anything about either."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:8]


def load_sessions(path: Path, now: float) -> dict[str, float]:
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(seen, dict):
        return {}
    return {tag: float(at) for tag, at in seen.items()
            if isinstance(tag, str) and isinstance(at, (int, float)) and now - at < 24 * 3600}


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    replace(temp, path)


def replace(temp: Path, path: Path) -> None:
    """os.replace, a few times over: on Windows a virus scanner often holds a file it just saw written."""
    for attempt in range(6):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 5:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def on_hook(event: dict, url: str, now: float, sessions_file: Path | None = None, post=None,
            spawn=None) -> str | None:
    """What one hook call posts, if anything: "working", "done" or None. Remembers which sessions said working,
    and makes sure a working session has a watcher for the Esc key (see watch_transcript)."""
    sessions_file = sessions_file or SESSIONS_FILE
    post = post or post_signal
    spawn = spawn or start_watcher
    name = event.get("hook_event_name")
    tag = session_tag(str(event.get("session_id") or "no-session"))
    transcript = event.get("transcript_path")
    # Where the transcript ends now, before the post: an Esc pressed while "working" is on its way still counts.
    offset = transcript_size(transcript) if name in WORKING_EVENTS else None
    sessions = load_sessions(sessions_file, now)
    said = sessions.get(tag)
    word = None
    if name in WORKING_EVENTS and (said is None or now - said >= HEARTBEAT_S):
        word = "working"
    elif name in DONE_EVENTS and said is not None:
        word = "done"
    if word is not None:
        if word == "working":
            # Written down before the post, as a try: when this file cannot be saved, nothing is posted (rather than
            # a post at every tool call), and when the post fails the next try comes in a minute, not at every tool
            # call, since the server may be turning this address away for the day.
            sessions[tag] = now - HEARTBEAT_S + RETRY_S
            save_json(sessions_file, sessions)
        post(url, word, tag)
        if word == "working":
            sessions[tag] = now
        else:
            sessions.pop(tag, None)
        save_json(sessions_file, sessions)
    if offset is not None and tag in sessions and claim_watch(tag, now, sessions_file.parent):
        spawn(tag, transcript, sessions_file.parent, offset)
    return word


# ---- the Esc key
#
# Claude Code runs no hook when the person stops a turn (Esc in the terminal, the stop button in VS Code or the desktop
# app), so on its own "done" would only come 20 minutes later, when the phone stops trusting "working". What it does
# do every time is write one line to the session's transcript: a user message whose text starts with
# "[Request interrupted by user". So while a session is working, a small watcher reads the new lines of that file
# once a second and posts "done" the moment that line shows up. It ends with the turn, or after 20 quiet minutes.

INTERRUPTED = "[Request interrupted by user"
WATCH_EVERY_S = 1.0
WATCH_ALIVE_S = 10  # a watcher that has not touched its file for this long is gone
WATCH_MAX_S = 12 * 3600
WATCH_CHUNK = 8 * 1024 * 1024


def watch_mark(tag: str, folder: Path) -> Path:
    return folder / f"agent-watch-{tag}"


def transcript_size(path: object) -> int | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def claim_watch(tag: str, now: float, folder: Path) -> bool:
    """True for the one hook call that gets to start this session's watcher: none is running, and no hook call
    running at the same moment (Claude often uses several tools at once) got there first."""
    mark = watch_mark(tag, folder)
    try:
        if now - mark.stat().st_mtime < WATCH_ALIVE_S:
            return False  # a watcher is running
        mark.unlink()  # left by a watcher that was stopped before it could clean up
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.close(os.open(mark, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return False
    return True


def start_watcher(tag: str, transcript: str, folder: Path, offset: int) -> None:
    """Starts `agent watch` as a program of its own, so it outlives the hook call that started it."""
    args = [sys.executable, str(Path(__file__).resolve()), "agent", "watch", "--tag", tag,
            "--transcript", transcript, "--offset", str(offset)]
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
             "close_fds": True, "cwd": str(folder)}
    try:
        if os.name != "nt":
            subprocess.Popen(args, start_new_session=True, **quiet)
            return
        alone = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            # Out of Claude Code's job as well, where the job allows it, so the watcher outlives Claude Code's hooks.
            subprocess.Popen(args, creationflags=alone | subprocess.CREATE_BREAKAWAY_FROM_JOB, **quiet)
        except OSError:
            subprocess.Popen(args, creationflags=alone, **quiet)
    except OSError:
        watch_mark(tag, folder).unlink(missing_ok=True)  # so the next hook call tries again
        raise


def is_interrupt(line: bytes) -> bool:
    """True for the line Claude Code writes when the person stops the turn."""
    if INTERRUPTED.encode() not in line:
        return False
    try:
        entry = json.loads(line)
    except ValueError:
        return False
    if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [part.get("text") for part in content if isinstance(part, dict) and part.get("type") == "text"]
    else:
        texts = []
    return any(isinstance(text, str) and text.startswith(INTERRUPTED) for text in texts)


def watch_transcript(tag: str, transcript: Path, offset: int, url: str, sessions_file: Path,
                     now=time.time, sleep=time.sleep, post=None) -> str:
    """Reads what Claude Code adds to the transcript until the turn ends. Says how it ended: "interrupted" (and posted
    done), "done" (the Stop hook got there), "quiet", "gone" or "too long"."""
    post = post or post_signal
    mark = watch_mark(tag, sessions_file.parent)
    started = last_change = now()
    pending = b""
    try:
        while now() - started < WATCH_MAX_S:
            mark.touch()
            if tag not in load_sessions(sessions_file, now()):
                return "done"
            try:
                size = transcript.stat().st_size
            except OSError:
                return "gone"
            if size < offset:  # the file was cut short or replaced
                offset, pending = size, b""
            if size > offset:
                with transcript.open("rb") as file:
                    file.seek(offset)
                    chunk = file.read(min(size - offset, WATCH_CHUNK))
                offset += len(chunk)
                last_change = now()
                *lines, pending = (pending + chunk).split(b"\n")
                if len(pending) > 4 * WATCH_CHUNK:
                    pending = b""
                if any(is_interrupt(line) for line in lines):
                    for attempt in range(3):
                        try:
                            post(url, "done", tag)
                            break
                        except OSError:
                            sleep(2)
                    sessions = load_sessions(sessions_file, now())
                    sessions.pop(tag, None)
                    save_json(sessions_file, sessions)
                    return "interrupted"
            elif now() - last_change > WINDOW_MINUTES * 60:
                return "quiet"
            sleep(WATCH_EVERY_S)
        return "too long"
    finally:
        try:
            mark.unlink()
        except OSError:
            pass


def hook_command() -> str:
    """How Claude Code should run this tool: a line that works in Git Bash and in PowerShell alike."""
    script = Path(__file__).resolve().as_posix()
    if any(c in script for c in '"$`'):
        raise Problem(f"this tool sits at {script}; move it to a folder without \" $ ` in its name first")
    # "; exit 0" because should this file ever be gone (its folder moved or deleted), Python's own exit code 2
    # would tell Claude Code to refuse every tool call. Git Bash and PowerShell both read the line the same way.
    for name in (("python", "py") if os.name == "nt" else ("python3", "python")):
        found = shutil.which(name)
        if found and Path(found).resolve() == Path(sys.executable).resolve():
            return f'{name} "{script}" agent hook; exit 0'
    python = Path(sys.executable).resolve().as_posix()
    if " " in python or any(c in python for c in '"$`'):
        raise Problem(f"Python is at {python}, a path Claude Code's shell may not run; put Python on the PATH first")
    return f'{python} "{script}" agent hook; exit 0'


OUR_HOOK = re.compile(r'(nomoredoom|shortstop)\.py" agent hook(; exit 0)?$')


def ours(hook: object) -> bool:
    """This tool's hook as it writes it now, or as it wrote it before (without "; exit 0", or under its old name)."""
    return isinstance(hook, dict) and bool(OUR_HOOK.search(str(hook.get("command", ""))))


def without_our_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                group = {**group, "hooks": [h for h in group["hooks"] if not ours(h)]}
                if not group["hooks"]:
                    continue
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise Problem(f"{path} is not valid JSON ({error}); nothing was changed. Fix it or link with --no-claude")
    if not isinstance(settings, dict):
        raise Problem(f"{path} does not hold a settings object; nothing was changed")
    return settings


def add_claude_hooks(path: Path, command: str) -> list[str]:
    """Adds this tool's hooks to a Claude Code settings file, leaving every other setting and hook as it was."""
    settings = without_our_hooks(read_settings(path))
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise Problem(f'{path} has a "hooks" entry that is not an object; nothing was changed')
    for event in CLAUDE_HOOKS:
        if not isinstance(hooks.get(event, []), list):
            raise Problem(f'{path} has a "{event}" hook entry that is not a list; nothing was changed')
    for event, options in CLAUDE_HOOKS.items():
        hook = {"type": "command", "command": command, **{k: v for k, v in options.items() if k != "matcher"}}
        group = {"matcher": options["matcher"], "hooks": [hook]} if "matcher" in options else {"hooks": [hook]}
        hooks.setdefault(event, []).append(group)
    if path.exists():
        backup = path.with_name(path.name + ".before-nomoredoom")
        if not backup.exists():
            shutil.copy2(path, backup)
    save_settings(path, settings)
    return list(CLAUDE_HOOKS)


def save_settings(path: Path, settings: dict) -> None:
    """Writes Claude Code's settings into the real file (through a link to it, if that is what the path is), with
    the file's permissions and its non-English text kept as they were."""
    target = path.resolve()
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if target.exists():
        shutil.copymode(target, temp)
    replace(temp, target)


def remove_claude_hooks(path: Path) -> bool:
    """Takes this tool's hooks out again. True when there were any."""
    if not path.exists():
        return False
    settings = read_settings(path)
    before = json.dumps(settings, sort_keys=True)
    after = without_our_hooks(settings)
    if json.dumps(after, sort_keys=True) == before:
        return False
    save_settings(path, after)
    return True


def linked_settings() -> Path | None:
    """The Claude Code settings file `agent link` put the hooks in."""
    try:
        name = json.loads(AGENT_FILE.read_text(encoding="utf-8")).get("settings")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(name) if isinstance(name, str) and name else None


def unlink_computer(settings: Path) -> None:
    """Takes the computer's half of the link off: this tool's hooks, the topic and the record of sessions."""
    try:
        removed = remove_claude_hooks(settings)
        print(f"Took this tool's hooks out of {settings}." if removed else f"No hooks of this tool in {settings}.")
    except Problem:
        print(f"Could not read {settings}, so this tool's hooks may still be in it. Fix the file, then run: "
              "python bin/nomoredoom.py agent unlink")
    for done_with in (AGENT_FILE, SESSIONS_FILE):
        if done_with.exists():
            done_with.unlink()


def agent_hook() -> int:
    """Called by Claude Code with the event on stdin. Prints nothing and always exits 0: a hook must never get in
    the agent's way. Trouble goes to a small log file next to the link."""
    try:
        url = linked_url()
        if url:
            source = getattr(sys.stdin, "buffer", sys.stdin)  # bytes: Claude Code sends UTF-8 on every system
            raw = source.read(4 * 1024 * 1024)
            event = json.loads((raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw) or "{}")
            if isinstance(event, dict):
                on_hook(event, url, time.time())
    except Exception as error:  # noqa: BLE001 - anything at all, so Claude Code never sees this tool fail
        log_trouble("hook", error)
    return 0


def agent_watch(args: argparse.Namespace) -> int:
    """Started by the hook, never by hand. Like the hook it prints nothing and always exits 0."""
    try:
        url = linked_url()
        if url and args.tag and args.transcript:
            watch_transcript(args.tag, Path(args.transcript), max(0, args.offset), url, SESSIONS_FILE)
    except Exception as error:  # noqa: BLE001 - same reason as in agent_hook
        log_trouble("watch", error)
    return 0


def log_trouble(who: str, error: BaseException) -> None:
    """The last few problems of the hook and the watcher, in a small file next to the link."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        old = HOOK_LOG.read_text(encoding="utf-8")[-4000:] if HOOK_LOG.exists() else ""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {who} {plain(repr(error), 300)}\n"
        HOOK_LOG.write_text(old + line, encoding="utf-8")
    except OSError:
        pass


def cmd_agent(args: argparse.Namespace) -> int:
    if args.action == "hook":
        return agent_hook()
    if args.action == "watch":
        return agent_watch(args)
    settings = Path(args.settings) if args.settings else claude_settings()

    if args.action == "link":
        command = None
        if not args.no_claude:
            command = hook_command()
            read_settings(settings)  # a file that cannot be read stops the link here, before the phone hears of it
        url = linked_url()
        if not url or (args.server and not url.startswith(args.server.rstrip("/") + "/")):
            url = new_topic(args.server or NTFY)
        print(send(connect(args), "--es", "agent_url", url))
        save_json(AGENT_FILE, {"url": url, **({"settings": str(settings)} if command else {})})
        if os.name != "nt":
            os.chmod(AGENT_FILE, 0o600)  # whoever reads the topic can cover the videos: for this user's eyes only
        print(f"The phone asks {url} whether the agent is working, and only while Reels, Shorts or TikTok is in front.")
        if args.no_claude:
            print("Claude Code was left alone. Any agent can post with: python bin/nomoredoom.py agent send working|done")
        else:
            existed = settings.exists()
            events = add_claude_hooks(settings, command)
            print(f"Claude Code now runs this tool on {', '.join(events)} (in {settings})." + (
                " The file as it was before is next to it, ending in .before-nomoredoom." if existed else ""))
            print("Sessions started from now on use it. Undo it all with: python bin/nomoredoom.py agent unlink")
        return 0

    if args.action == "unlink":
        unlink_computer(settings if args.settings else (linked_settings() or settings))
        try:
            print(send(connect(args), "--es", "agent_url", '""'))
            print("The phone no longer asks anything.")
        except Problem as problem:
            print(f"Could not reach the phone ({problem}). It keeps asking a topic nobody posts to any more, which "
                  "never covers a video. Run agent unlink again with the phone connected to stop that too.")
        return 0

    url = linked_url()
    if not url:
        raise Problem("not linked yet. Run: python bin/nomoredoom.py agent link")
    if args.action == "send":
        if args.word not in ("working", "done"):
            raise Problem("send takes working or done")
        try:
            post_signal(url, args.word, args.tag)
        except (OSError, ValueError) as error:
            raise Problem(f"could not post to {url} ({error})") from error
        print(f'Posted "{args.word} {args.tag}".')
        return 0
    sessions = read_signal(url)
    working = [tag for tag, busy in sessions.items() if busy]
    print("working" if working else "not working", f"({len(working)} of {len(sessions)} sessions heard from in the "
          f"last {WINDOW_MINUTES} minutes say working)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial", help="which phone, when more than one is connected")
    parser.add_argument("--adb", help="path to adb, if it is not found by itself")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=text, description=text)
        p.set_defaults(func=func)
        return p

    p = add("doctor", cmd_doctor, "check the computer, the phone and the app; says what is missing")
    p.add_argument("--json", action="store_true")
    p.add_argument("--get-adb", action="store_true", help="download Google's adb if this computer has none")

    p = add("pair", cmd_pair, "over Wi-Fi: pair with the phone (its Wireless debugging setting). "
                              "On the phone itself this also connects")
    p.add_argument("address", nargs="?", help="the address and port in the phone's pairing box, like "
                                              "192.168.1.20:37123. On the phone itself: the port alone, or nothing")
    p.add_argument("code", help="the six digits in that box")
    p.add_argument("--get-adb", action="store_true", help="download Google's adb if this computer has none")
    p.add_argument("--any-address", action="store_true", help=FAR_AWAY)

    p = add("connect", cmd_connect, "over Wi-Fi: connect to a phone that is paired")
    p.add_argument("address", nargs="?", help="the address and port on the Wireless debugging screen itself. "
                                              "On the phone itself: the port alone, or nothing")
    p.add_argument("--get-adb", action="store_true", help="download Google's adb if this computer has none")
    p.add_argument("--any-address", action="store_true", help=FAR_AWAY)

    add("disconnect", cmd_disconnect, "when you are done: switch Wireless debugging off on the phone and "
                                      "drop the connection")

    p = add("install", cmd_install, "put the app on the phone and switch it on")
    p.add_argument("--apk", help="install this file instead of the published release")
    p.add_argument("--release", action="store_true", help="download the published release even when a local build exists")
    p.add_argument("--replace", action="store_true", help="remove a copy signed with another key first")
    p.add_argument("--no-gifs", action="store_true", help="skip downloading the popups' GIFs; they show their lines alone")
    p.add_argument("--get-adb", action="store_true", help="download Google's adb if this computer has none")

    add("on", cmd_on, "switch the app on")
    add("off", cmd_off, "switch the app off; it stays installed")
    add("uninstall", cmd_uninstall, "remove the app from the phone")

    p = add("status", cmd_status, "what the app sees right now")
    p.add_argument("--json", action="store_true")

    p = add("set", cmd_set, "change a setting: popups on|off, limit MINUTES, fresh MINUTES, home on|off")
    p.add_argument("name", choices=["popups", "limit", "fresh", "home"])
    p.add_argument("value")

    add("demo", cmd_demo, "play the popups once on the phone")
    add("tree", cmd_tree, "list the named views showing in the watched app in front (for fixing the rules)")

    p = add("rules", cmd_rules, "push a rules file to the phone, or go back to the built-in one")
    p.add_argument("action", choices=["push", "reset"])
    p.add_argument("file", nargs="?")

    p = add("gags", cmd_gags, "copy a jokes file and its GIFs to the phone (default: the built-in one), or remove them")
    p.add_argument("action", choices=["push", "reset"])
    p.add_argument("file", nargs="?")

    p = add("skip", cmd_skip, "testing: jump this sitting to MM:SS")
    p.add_argument("to")
    p = add("speed", cmd_speed, "testing: count every watched second N times (resets when the app restarts)")
    p.add_argument("times", type=int)
    add("reset", cmd_reset, "testing: start the sitting over")

    p = add("agent", cmd_agent, "cover Reels, Shorts and TikTok while a coding agent is working: link, unlink, "
                                "check, send working|done, and hook and watch (what Claude Code runs)")
    p.add_argument("action", choices=["link", "unlink", "check", "send", "hook", "watch"])
    p.add_argument("word", nargs="?", help="for send: working or done")
    p.add_argument("--tag", default="manual", help="for send: which session this is (letters and digits)")
    p.add_argument("--server", help=f"for link: a ntfy server of your own instead of {NTFY}")
    p.add_argument("--no-claude", action="store_true", help="for link: leave Claude Code's settings alone")
    p.add_argument("--settings", help="the Claude Code settings file to change (default: your own, "
                                      "~/.claude/settings.json)")
    p.add_argument("--transcript", help=argparse.SUPPRESS)  # for watch, which the hook starts
    p.add_argument("--offset", type=int, default=0, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Problem as problem:
        print(f"nomoredoom: {plain(str(problem), 2000)}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("nomoredoom: the phone did not answer in time. Is it unlocked and still connected?", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, zipfile.BadZipFile) as error:
        # A file that could not be read or written. 1 is kept for "not ready", so this is a 2 like any other problem.
        print(f"nomoredoom: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
