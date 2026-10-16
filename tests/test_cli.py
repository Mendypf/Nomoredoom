"""Checks for the command-line tool that need no phone.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

# The tests never touch this computer's own link or its Claude Code settings: before the tool is imported, its
# folder and Claude Code's settings point at a scratch folder. (Without this, the uninstall test once unlinked the
# real laptop of the person running the tests, hooks and all.)
SCRATCH_HOME = Path(tempfile.mkdtemp(prefix="nomoredoom-tests-"))
os.environ["XDG_CACHE_HOME"] = str(SCRATCH_HOME / "cache")
os.environ["CLAUDE_CONFIG_DIR"] = str(SCRATCH_HOME / "claude")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import nomoredoom as cli  # noqa: E402

TALKBACK = "com.google.android.marvin.talkback/com.google.android.marvin.talkback.TalkBackService"


class FakePhone:
    """Answers `adb shell` commands from a table and remembers what it was asked."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.asked: list[str] = []
        self.quiet: list[str] = []  # asked for without what adb says on the side
        self.ran: list[tuple[str, ...]] = []

    def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        self.ran.append(args)
        return ""

    def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        self.asked.append(command)
        if not stderr:
            self.quiet.append(command)
        return next((answer for start, answer in self.answers.items() if command.startswith(start)), "")

    def prop(self, name: str) -> str:
        return self.shell(f"getprop {name}", check=False).strip()


def run_tool_aloud(*argv: str) -> tuple[int, str, str]:
    """The exit code, what the tool printed, and what it said about a problem."""
    printed, heard = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(heard):
        code = cli.main(list(argv))
    return code, printed.getvalue(), heard.getvalue()


def run_tool(*argv: str) -> tuple[int, str]:
    """The exit code and what was printed for the person, as when the tool is run from a terminal."""
    heard = io.StringIO()
    with contextlib.redirect_stderr(heard), contextlib.redirect_stdout(io.StringIO()):
        code = cli.main(list(argv))
    return code, heard.getvalue()


def prefixed(data: bytes) -> bytes:
    return struct.pack("<I", len(data)) + data


V2, V3, V3_1 = 0x7109871A, 0xF05368C0, 0x1B93AD61
PADDING = struct.pack("<QI", 4 + 5, 0x42726577) + b"\0" * 5
PLAIN_ZIP = b"the zipped files" + b"PK\x05\x06" + b"\0" * 8 + struct.pack("<II", 0, 16) + b"\0\0"


def sha(certificate: bytes) -> str:
    return hashlib.sha256(certificate).hexdigest()


def scheme(block_id: int, *certificates: bytes) -> bytes:
    """One signature scheme in the signing block, with one signer per certificate."""
    def signer(certificate: bytes) -> bytes:
        signed_data = prefixed(prefixed(b"digest")) + prefixed(prefixed(certificate)) + prefixed(b"")
        return prefixed(signed_data) + prefixed(b"signatures") + prefixed(b"public key")
    value = prefixed(b"".join(prefixed(signer(certificate)) for certificate in certificates))
    return struct.pack("<QI", len(value) + 4, block_id) + value


def apk_with(*entries: bytes) -> bytes:
    """The smallest file laid out like a signed APK: contents, signing block, file list, closing record."""
    pairs = b"".join(entries)
    size = len(pairs) + 8 + 16
    block = struct.pack("<Q", size) + pairs + struct.pack("<Q", size) + b"APK Sig Block 42"
    contents = b"the zipped files"
    files_at = len(contents) + len(block)
    closing = b"PK\x05\x06" + b"\0" * 8 + struct.pack("<II", 0, files_at) + b"\0\0"
    return contents + block + closing


def signed_apk(certificate: bytes) -> bytes:
    return apk_with(scheme(V2, certificate))


class ApkSignerTest(unittest.TestCase):
    def on_disk(self, data: bytes, ask):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "app.apk"
            apk.write_bytes(data)
            return ask(apk)

    def signers_of(self, data: bytes) -> set[str]:
        return self.on_disk(data, cli.apk_signers)

    def test_reads_the_certificate_of_a_v2_signed_file(self):
        self.assertEqual({sha(b"a certificate")}, self.signers_of(signed_apk(b"a certificate")))

    def test_reads_the_newer_signature_schemes_too(self):
        for block_id in (V3, V3_1):
            self.assertEqual({sha(b"another one")}, self.signers_of(apk_with(scheme(block_id, b"another one"))))

    def test_skips_other_entries_in_the_signing_block(self):
        self.assertEqual({sha(b"a certificate")}, self.signers_of(apk_with(PADDING, scheme(V2, b"a certificate"))))

    def test_names_every_signer_of_every_scheme(self):
        two_schemes = apk_with(scheme(V2, b"ours"), scheme(V3, b"somebody else"))
        self.assertEqual({sha(b"ours"), sha(b"somebody else")}, self.signers_of(two_schemes))
        two_signers = apk_with(scheme(V2, b"ours", b"a second signer"))
        self.assertEqual({sha(b"ours"), sha(b"a second signer")}, self.signers_of(two_signers))

    def test_a_file_is_ours_only_when_nobody_else_signed_it(self):
        def ours(data: bytes) -> bool:
            return self.on_disk(data, lambda apk: cli.signed_only_by(apk, sha(b"ours")))

        self.assertTrue(ours(apk_with(scheme(V2, b"ours"))))
        self.assertTrue(ours(apk_with(scheme(V2, b"ours"), scheme(V3, b"ours"))))
        # Android 9 and newer go by the v3 signature when there is one, whatever the v2 one says.
        self.assertFalse(ours(apk_with(scheme(V2, b"ours"), scheme(V3, b"somebody else"))))
        self.assertFalse(ours(apk_with(scheme(V3, b"somebody else"), scheme(V2, b"ours"))))
        self.assertFalse(ours(apk_with(scheme(V2, b"ours"), scheme(V3_1, b"somebody else"))))
        self.assertFalse(ours(apk_with(scheme(V2, b"ours", b"somebody else"))))
        self.assertFalse(ours(PLAIN_ZIP))

    def test_one_scheme_that_cannot_be_read_spoils_the_whole_file(self):
        nonsense = struct.pack("<QI", 4 + 8, V3) + struct.pack("<I", 0x7FFFFFFF) + b"\0" * 4
        self.assertEqual(set(), self.signers_of(apk_with(scheme(V2, b"ours"), nonsense)))
        no_signers = struct.pack("<QI", 4 + 4, V3) + prefixed(b"")
        self.assertEqual(set(), self.signers_of(apk_with(scheme(V2, b"ours"), no_signers)))

    def test_a_file_that_is_not_signed_has_no_signer(self):
        self.assertEqual(set(), self.signers_of(PLAIN_ZIP))
        self.assertEqual(set(), self.signers_of(b"not a zip at all"))
        self.assertEqual(set(), self.signers_of(b""))

    def test_a_cut_off_file_has_no_signer(self):
        whole = signed_apk(b"a certificate")
        for keep in (len(whole) - 30, len(whole) // 2, 40):
            self.assertEqual(set(), self.signers_of(whole[:keep]), f"first {keep} bytes")

    def test_a_signing_block_with_nonsense_sizes_has_no_signer(self):
        whole = bytearray(signed_apk(b"a certificate"))
        at = len(b"the zipped files") + 8 + 12 + 12  # the length of the digests, set far past the end
        whole[at:at + 4] = struct.pack("<I", 0x7FFFFFFF)
        self.assertEqual(set(), self.signers_of(bytes(whole)))

    def test_a_block_without_the_magic_words_is_not_a_signature(self):
        whole = signed_apk(b"a certificate")
        self.assertEqual(set(), self.signers_of(whole.replace(b"APK Sig Block 42", b"APK Sig Block 41")))

    def test_a_certificate_that_claims_to_be_longer_than_the_file_is_refused(self):
        whole = bytearray(signed_apk(b"a certificate"))
        at = whole.index(prefixed(b"a certificate"))
        whole[at:at + 4] = struct.pack("<I", 4096)
        self.assertEqual(set(), self.signers_of(bytes(whole)))

    def test_an_empty_certificate_is_refused(self):
        self.assertEqual(set(), self.signers_of(signed_apk(b"")))

    def test_a_closing_record_hidden_in_the_zip_comment_is_not_the_one_read(self):
        # Android goes by the closing record whose comment runs exactly to the end of the file.
        # A second record tucked inside that comment must not send this tool to another signing block.
        ours, theirs = b"the release certificate", b"someone else's certificate"
        real, decoy = signed_apk(theirs), signed_apk(ours)
        files_at = struct.unpack_from("<I", decoy, len(decoy) - 6)[0] + len(real)  # where the decoy's file list lands
        decoy = decoy[:-6] + struct.pack("<IH", files_at, 9)  # and it claims a comment that is not there
        whole = real[:-2] + struct.pack("<H", len(decoy)) + decoy
        self.assertEqual({sha(theirs)}, self.signers_of(whole))

    def test_a_comment_after_the_closing_record_is_fine(self):
        commented = signed_apk(b"a certificate")[:-2] + struct.pack("<H", 11) + b"hello there"
        self.assertEqual({sha(b"a certificate")}, self.signers_of(commented))

    def test_the_pinned_release_key_is_a_sha256(self):
        self.assertRegex(cli.RELEASE_CERT, r"^[0-9a-f]{64}$")


class GagsTest(unittest.TestCase):
    PACK = (
        "# the memes, one GIF a line\n"
        "\n"
        "https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI\n"
        "  https://media.giphy.com/media/NMkDjJP2ZC29G/giphy.gif\n"
        "my-cat.GIF\n"
        "https://giphy.com/gifs/judge-judy-hurry-up-Emg9qPKR5hquI\n"
        "https://example.com/cat.gif\n"
    )

    def test_lists_each_gif_once_with_the_name_the_phone_looks_for(self):
        self.assertEqual(
            [
                ("Emg9qPKR5hquI", "https://media.giphy.com/media/Emg9qPKR5hquI/giphy.gif"),
                ("NMkDjJP2ZC29G", "https://media.giphy.com/media/NMkDjJP2ZC29G/giphy.gif"),
                ("my-cat", "my-cat.GIF"),
            ],
            cli.gag_refs(self.PACK),
        )

    def test_every_line_of_the_built_in_memes_file_is_a_gif_this_tool_finds(self):
        text = cli.DEFAULT_GAGS.read_text(encoding="utf-8")
        rows = [r for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")]
        self.assertEqual(27, len(rows))
        self.assertEqual(len(set(rows)), len(cli.gag_refs(text)))

    def test_tells_a_picture_from_anything_else_by_its_first_bytes(self):
        self.assertTrue(cli.looks_like_a_gif(b"GIF89a" + b"\0" * 20))
        self.assertTrue(cli.looks_like_a_gif(b"GIF87a" + b"\0" * 20))
        self.assertTrue(cli.looks_like_a_gif(b"RIFF\x10\0\0\0WEBPVP8X"))
        self.assertFalse(cli.looks_like_a_gif(b"<!doctype html><html>"))
        self.assertFalse(cli.looks_like_a_gif(b"RIFF\x10\0\0\0WAVEfmt "))
        self.assertFalse(cli.looks_like_a_gif(b""))

    def test_reads_the_gifs_already_on_the_phone_with_their_sizes(self):
        listing = "123 a.gif\n9 b.webp\nstat: '*': No such file or directory\n12 not ours.gif\n7 notes.txt\n"
        phone = FakePhone({"cd '/sdcard/x' && stat": listing})
        self.assertEqual({"a.gif": 123, "b.webp": 9}, cli.files_on_phone(phone, "/sdcard/x"))

    def test_pushing_sends_what_is_missing_or_cut_short_and_removes_what_is_no_longer_wanted(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            for name, size in (("same", 40), ("cut", 50), ("new", 60)):
                (Path(folder) / f"{name}.gif").write_bytes(b"GIF89a" + b"\0" * (size - 6))
            pack.write_text("same.gif\ncut.gif\nnew.gif\n", encoding="utf-8")
            phone = FakePhone({
                "am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"',
                "cd '/sdcard/x' && stat": "40 same.gif\n12 cut.gif\n99 old.gif\n",
            })
            said = cli.push_gags(phone, pack)
        pushed = sorted(Path(args[1]).name for args in phone.ran if args[0] == "push")
        self.assertEqual(["cut.gif", "new.gif"], pushed)
        self.assertIn("rm -f '/sdcard/x/old.gif'", phone.asked)
        self.assertIn("3 GIFs", said)

    def test_a_memes_file_too_big_for_one_command_is_refused_before_the_phone_is_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            pack.write_text("".join(f"https://giphy.com/gifs/{'slug-' * 30}id{n}\n" for n in range(200)),
                            encoding="utf-8")
            phone = FakePhone({"am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"'})
            with self.assertRaises(cli.Problem) as refused:
                cli.push_gags(phone, pack)
        self.assertIn("20 KB", str(refused.exception))
        self.assertEqual(([], []), (phone.asked, phone.ran))

    def test_a_memes_file_the_phone_refuses_changes_nothing_else(self):
        class Picky(FakePhone):
            def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                self.asked.append(command)
                if "gags_dir" in command:
                    return 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"'
                if "gags_b64" in command:
                    return 'Broadcast completed: result=2, data="gags line 1: more than 200 GIFs"'
                return "40 old.gif\n" if command.startswith("cd ") else ""

        fetched = []
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            pack.write_text("https://giphy.com/gifs/some-thing-abc123\n", encoding="utf-8")
            phone = Picky({})
            with mock.patch.object(cli, "fetch_gif", lambda *asked: fetched.append(asked)), self.assertRaises(cli.Refused):
                cli.push_gags(phone, pack)
        self.assertEqual([], fetched)  # nothing is downloaded for a file the phone would not take
        self.assertFalse([c for c in phone.asked if c.startswith("rm ")])  # and the GIFs it has stay
        self.assertEqual([], phone.ran)

    def test_a_memes_file_naming_too_many_gifs_is_refused_before_the_phone_is_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            pack.write_text("".join(f"https://giphy.com/gifs/id{n:04d}\n" for n in range(cli.MAX_GIFS + 1)),
                            encoding="utf-8")
            phone = FakePhone({"am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"'})
            with self.assertRaises(cli.Problem) as refused:
                cli.push_gags(phone, pack)
        self.assertIn(str(cli.MAX_GIFS), str(refused.exception))
        self.assertEqual(([], []), (phone.asked, phone.ran))

    def test_gifs_that_add_up_to_too_much_are_not_copied_to_the_phone(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            for name in ("a", "b", "c"):
                (Path(folder) / f"{name}.gif").write_bytes(b"GIF89a" + b"\0" * 54)
            pack.write_text("a.gif\nb.gif\nc.gif\n", encoding="utf-8")
            phone = FakePhone({
                "am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"',
                "cd '/sdcard/x' && stat": "99 old.gif\n",
            })
            with mock.patch.object(cli, "MAX_GIFS_TOTAL", 150), self.assertRaises(cli.Problem) as refused:
                cli.push_gags(phone, pack)
        self.assertIn("MB", str(refused.exception))
        self.assertEqual([], [args for args in phone.ran if args[0] == "push"])
        self.assertFalse([c for c in phone.asked if c.startswith("rm ")])

    def test_a_folder_name_from_the_phone_that_could_run_a_command_is_not_used(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            pack.write_text("a.gif\n", encoding="utf-8")
            phone = FakePhone({"am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x\';reboot;\'"'})
            with self.assertRaises(cli.Problem):
                cli.push_gags(phone, pack)
        self.assertFalse([c for c in phone.asked if "reboot" in c and not c.startswith("am broadcast")])

    def test_a_memes_file_with_the_mark_windows_puts_first_reaches_the_phone_without_it(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder) / "gags.txt"
            pack.write_bytes(b"\xef\xbb\xbf# mine\na.gif\n")
            phone = FakePhone({"am broadcast": 'Broadcast completed: result=1, data="gags_dir=/sdcard/x"'})
            cli.push_gags(phone, pack)
        sent = next(c for c in phone.asked if "gags_b64" in c).split()[-1]
        self.assertEqual(b"# mine\na.gif\n", base64.b64decode(sent))

    def test_a_file_of_your_own_is_looked_for_next_to_the_memes_file(self):
        with tempfile.TemporaryDirectory() as folder:
            mine = Path(folder) / "my-cat.GIF"
            mine.write_bytes(b"GIF89a" + b"\0" * 20)
            self.assertEqual(mine, cli.fetch_gif("my-cat", "my-cat.GIF", Path(folder)))
            self.assertIsNone(cli.fetch_gif("gone", "gone.gif", Path(folder)))
            (Path(folder) / "fake.gif").write_bytes(b"MZ this is a program")
            self.assertIsNone(cli.fetch_gif("fake", "fake.gif", Path(folder)))


class FilesFromThePersonTest(unittest.TestCase):
    def in_a_file(self, data: bytes, ask):
        with tempfile.TemporaryDirectory() as folder:
            mine = Path(folder) / "mine.txt"
            mine.write_bytes(data)
            return ask(mine)

    def test_the_invisible_mark_some_windows_editors_put_first_is_dropped(self):
        self.assertEqual("# my rules\n", self.in_a_file(b"\xef\xbb\xbf# my rules\n", cli.words_in))

    def test_a_file_that_is_not_utf8_is_a_message_that_says_so(self):
        with self.assertRaises(cli.Problem) as stopped:
            self.in_a_file("# café\n".encode("utf-16"), cli.words_in)
        self.assertIn("UTF-8", str(stopped.exception))

    def test_a_file_that_is_not_there_is_a_message(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.words_in(Path("no") / "such" / "file.txt")
        self.assertIn("not a file", str(stopped.exception))

    def test_a_rules_file_that_is_not_there_stops_the_tool_with_a_message_before_the_phone_is_asked(self):
        asked = []
        with mock.patch.object(cli, "connect", lambda args: asked.append(args)):
            code, said = run_tool("rules", "push", "no-such-file.txt")
        self.assertEqual((2, []), (code, asked))
        self.assertIn("not a file", said)


class ExitCodeTest(unittest.TestCase):
    """1 is kept for "not ready". Anything that went wrong is a 2 with one line of words."""

    def test_a_file_error_inside_any_command_is_a_message_not_a_traceback(self):
        def broken(args):
            raise PermissionError(13, "Permission denied", "somewhere")
        with mock.patch.object(cli, "connect", broken):
            code, said = run_tool("status")
        self.assertEqual(2, code)
        self.assertTrue(said.startswith("nomoredoom: "), said)

    def test_a_phone_that_stops_answering_is_not_blamed_on_a_cable(self):
        def silent(args):
            raise subprocess.TimeoutExpired("adb", 60)
        with mock.patch.object(cli, "connect", silent):
            code, said = run_tool("status")
        self.assertEqual(2, code)
        self.assertNotIn("plugged", said)


class FindingAdbTest(unittest.TestCase):
    def test_never_runs_an_adb_lying_in_the_folder_it_was_started_from(self):
        exe = "adb.exe" if os.name == "nt" else "adb"
        dropped = ("LOCALAPPDATA", "ANDROID_HOME", "ANDROID_SDK_ROOT", "ADB", "PATH")
        kept = {name: value for name, value in os.environ.items() if name.upper() not in dropped}
        started_in = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            for planted in (Path(folder) / exe, Path(folder) / "Android" / "Sdk" / "platform-tools" / exe):
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_bytes(b"not adb")
                planted.chmod(0o755)
            empty = Path(folder) / "empty"
            empty.mkdir()
            os.chdir(folder)
            try:
                with mock.patch.dict(os.environ, {**kept, "PATH": str(empty)}, clear=True), \
                        mock.patch.object(cli, "CACHE", Path(folder) / "no-cache"), \
                        mock.patch.object(cli.Path, "home", lambda: Path(folder) / "no-home"):
                    self.assertIsNone(cli.find_adb())
            finally:
                os.chdir(started_in)

    def test_no_adb_names_the_command_that_downloads_it(self):
        with mock.patch.object(cli, "find_adb", lambda explicit=None: None), self.assertRaises(cli.Problem) as stopped:
            cli.need_adb(argparse.Namespace(adb=None, get_adb=False))
        self.assertIn("doctor --get-adb", str(stopped.exception))


class InstallTest(unittest.TestCase):
    def test_downloading_adb_says_where_it_came_from(self):
        heard = io.StringIO()
        with mock.patch.object(cli, "find_adb", lambda explicit=None: None), \
                mock.patch.object(cli, "download_adb", lambda: "/cache/adb"), contextlib.redirect_stderr(heard):
            self.assertEqual("/cache/adb", cli.need_adb(argparse.Namespace(adb=None, get_adb=True)))
        self.assertIn("dl.google.com", heard.getvalue())

    def test_says_where_the_app_file_came_from_and_who_signed_it(self):
        with tempfile.TemporaryDirectory() as folder:
            mine = Path(folder) / "app-release.apk"
            mine.write_bytes(signed_apk(b"my own key"))
            said = cli.about_apk(mine)
            self.assertIn(hashlib.sha256(mine.read_bytes()).hexdigest(), said)
            self.assertIn("not signed with Nomoredoom's release key", said)
            with mock.patch.object(cli, "LOCAL_APK", mine):
                self.assertIn("the build in this folder", cli.about_apk(mine))
            with mock.patch.object(cli, "RELEASE_CERT", sha(b"my own key")):
                self.assertNotIn("not signed", cli.about_apk(mine))
            fetched = Path(folder) / "nomoredoom.apk"
            fetched.write_bytes(signed_apk(b"my own key"))
            with mock.patch.object(cli, "CACHE", Path(folder)):
                self.assertIn("the published release", cli.about_apk(fetched))

    def uninstalling(self, answer: str, there: bool = True):
        class Holding(FakePhone):
            def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                self.ran.append(args)
                return answer

        phone = Holding({"pm path": "package:/data/app/nomoredoom/base.apk\n" if there else ""})
        with mock.patch.object(cli, "connect", lambda args: phone):
            code, printed, heard = run_tool_aloud("uninstall")
        return code, printed, heard, phone

    def test_uninstall_says_gone_once_the_phone_says_success(self):
        code, printed, heard, phone = self.uninstalling("Success\n")
        self.assertEqual(0, code)
        self.assertIn("gone", printed)

    def test_an_uninstall_the_phone_refuses_is_not_reported_as_done(self):
        code, printed, heard, phone = self.uninstalling("Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]\n")
        self.assertEqual(2, code)
        self.assertNotIn("gone", printed)
        self.assertIn("DELETE_FAILED_DEVICE_POLICY_MANAGER", heard)

    def test_uninstalling_what_is_not_there_says_so(self):
        code, printed, heard, phone = self.uninstalling("", there=False)
        self.assertEqual(0, code)
        self.assertIn("not on the phone", printed)
        self.assertEqual([], phone.ran)

    def test_an_install_the_phone_is_holding_for_a_tap_says_to_look_at_the_phone(self):
        class Waiting(FakePhone):
            def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                raise subprocess.TimeoutExpired("adb install", timeout)
        with self.assertRaises(cli.Problem) as stopped:
            cli.put_on(Waiting({}), Path("app.apk"), replace=False)
        self.assertIn("look at the phone", str(stopped.exception))

    def test_a_copy_signed_with_another_key_is_replaced_only_when_asked(self):
        class HasAnother(FakePhone):
            def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                self.ran.append(args)
                first_try = args[0] == "install" and sum(1 for ran in self.ran if ran[0] == "install") == 1
                return "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]" if first_try else "Success"
        with self.assertRaises(cli.Problem):
            cli.put_on(HasAnother({}), Path("app.apk"), replace=False)
        phone = HasAnother({})
        cli.put_on(phone, Path("app.apk"), replace=True)
        self.assertEqual(["install", "uninstall", "install"], [ran[0] for ran in phone.ran])


class SwitchTest(unittest.TestCase):
    def test_short_and_long_spellings_of_a_service_are_the_same(self):
        self.assertTrue(cli.same_component("a.b/.Service", "a.b/a.b.Service"))
        self.assertTrue(cli.same_component(cli.SERVICE, f"{cli.PKG}/.WatchService"))
        self.assertFalse(cli.same_component("a.b/.Service", "a.b/.Other"))
        self.assertFalse(cli.same_component("a.b/.Service", "a.c/.Service"))

    def test_nothing_switched_on_reads_as_an_empty_list(self):
        self.assertEqual([], cli.enabled_services(FakePhone({"settings get": "null\n"})))
        self.assertEqual([], cli.enabled_services(FakePhone({"settings get": "\n"})))

    def test_switching_on_keeps_the_services_that_were_already_on(self):
        phone = FakePhone({"settings get secure enabled_accessibility_services": TALKBACK + "\n"})
        cli.switch_on(phone)
        written = [c for c in phone.asked if c.startswith("settings put secure enabled_accessibility_services")]
        self.assertEqual([f"settings put secure enabled_accessibility_services {TALKBACK}:{cli.SERVICE}"], written)

    def test_switching_on_twice_does_not_list_it_twice(self):
        phone = FakePhone({"settings get secure enabled_accessibility_services": f"{cli.PKG}/.WatchService\n"})
        cli.switch_on(phone)
        self.assertFalse([c for c in phone.asked if c.startswith("settings put secure enabled_accessibility_services")])

    def test_switching_off_leaves_the_other_services_on(self):
        phone = FakePhone({"settings get secure enabled_accessibility_services": f"{TALKBACK}:{cli.SERVICE}\n"})
        cli.switch_off(phone)
        self.assertIn(f"settings put secure enabled_accessibility_services {TALKBACK}", phone.asked)

    def test_another_service_s_name_reaches_the_phone_s_shell_in_quotes(self):
        # Unquoted, the phone's shell would read $Service as a variable and cut the name short.
        other = "com.example.reader/com.example.reader.Outer$Service"
        phone = FakePhone({"settings get secure enabled_accessibility_services": other + "\n"})
        cli.switch_on(phone)
        self.assertIn(f"settings put secure enabled_accessibility_services '{other}:{cli.SERVICE}'", phone.asked)

        phone = FakePhone({"settings get secure enabled_accessibility_services": f"{other}:{cli.SERVICE}\n"})
        cli.switch_off(phone)
        self.assertIn(f"settings put secure enabled_accessibility_services '{other}'", phone.asked)

    def test_switched_straight_back_off_names_the_likely_cause_first_and_what_android_says(self):
        phone = FakePhone({
            "settings get secure enabled_accessibility_services": "null",
            "appops get": "ACCESS_RESTRICTED_SETTINGS: ignore",
        })
        with self.assertRaises(cli.Problem) as stopped:
            cli.wait_until_running(phone, seconds=0)
        said = str(stopped.exception)
        self.assertIn("ACCESS_RESTRICTED_SETTINGS: ignore", said)
        self.assertLess(said.index("Allow restricted settings"), said.index("Advanced Protection"))

    def test_a_list_that_holds_anything_but_service_names_is_never_written_back(self):
        # One stray line in what came back, written back, would switch the person's screen reader off.
        noisy = "WARNING: linker: libfoo.so has text relocations\n" + TALKBACK + ":" + cli.SERVICE + "\n"
        for change in (cli.switch_on, cli.switch_off):
            phone = FakePhone({"settings get secure enabled_accessibility_services": noisy})
            with self.assertRaises(cli.Problem, msg=change.__name__):
                change(phone)
            self.assertFalse([c for c in phone.asked if c.startswith(("settings put", "settings delete", "appops set"))])

    def test_the_list_is_read_without_what_adb_says_on_the_side(self):
        phone = FakePhone({"settings get secure enabled_accessibility_services": TALKBACK + "\n"})
        cli.switch_on(phone)
        self.assertEqual(["settings get secure enabled_accessibility_services"], phone.quiet)

    def test_switching_off_what_is_not_on_writes_nothing(self):
        # An empty answer can also be a read that failed. Nothing is cleared on the strength of it.
        for listed in ("", "null\n", TALKBACK + "\n"):
            phone = FakePhone({"settings get secure enabled_accessibility_services": listed})
            cli.switch_off(phone)
            self.assertFalse([c for c in phone.asked if c.startswith(("settings put", "settings delete"))], listed)

    def test_a_phone_can_be_asked_without_what_is_said_on_the_side(self):
        both = "import sys; print('the answer'); print('a remark', file=sys.stderr)"
        phone = cli.Phone(sys.executable, None)
        self.assertEqual("the answer", phone.run("-c", both, stderr=False).strip())
        self.assertIn("a remark", phone.run("-c", both))

    def test_switching_off_the_only_service_clears_the_list(self):
        phone = FakePhone({"settings get secure enabled_accessibility_services": cli.SERVICE + "\n"})
        cli.switch_off(phone)
        self.assertIn("settings delete secure enabled_accessibility_services", phone.asked)


class DownloadTest(unittest.TestCase):
    def test_brings_back_a_file_within_the_size_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "small.bin"
            source.write_bytes(b"x" * 100)
            self.assertEqual(b"x" * 100, cli.download(source.as_uri(), limit=100))

    def test_refuses_a_file_over_the_size_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "big.bin"
            source.write_bytes(b"x" * 100)
            with self.assertRaises(cli.Problem):
                cli.download(source.as_uri(), limit=99)

    def test_an_address_with_nothing_behind_it_is_a_message_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(cli.Problem):
                cli.download((Path(folder) / "gone.bin").as_uri(), limit=10)


class PickPhoneTest(unittest.TestCase):
    HEAD = "List of devices attached\n"

    def test_the_one_phone_that_is_ready_is_the_phone(self):
        self.assertEqual("R5CT", cli.pick_phone(self.HEAD + "R5CT\tdevice\n\n", None))

    def test_a_phone_that_has_not_allowed_this_computer_is_the_problem_named(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(self.HEAD + "R5CT\tunauthorized\n", None)
        self.assertIn("Allow", str(stopped.exception))

    def test_no_phone_at_all_says_how_to_connect_one(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(self.HEAD, None)
        self.assertIn("USB debugging", str(stopped.exception))

    def test_no_phone_at_all_names_the_way_with_no_cable_too(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(self.HEAD, None)
        self.assertIn("Wireless debugging", str(stopped.exception))

    def test_on_the_phone_itself_no_phone_means_pair_first(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(self.HEAD, None, here=True)
        self.assertIn("pair CODE", str(stopped.exception))
        self.assertNotIn("cable", str(stopped.exception))

    def test_two_ready_phones_need_a_choice(self):
        both = self.HEAD + "R5CT\tdevice\nemulator-5554\tdevice\n"
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(both, None)
        self.assertIn("-s", str(stopped.exception))
        self.assertEqual("R5CT", cli.pick_phone(both, "R5CT"))

    def test_an_emulator_is_not_taken_for_the_phone_that_is_still_waiting_for_allow(self):
        mixed = self.HEAD + "emulator-5554\tdevice\nR5CT\tunauthorized\n"
        with self.assertRaises(cli.Problem) as stopped:
            cli.pick_phone(mixed, None)
        self.assertIn("R5CT", str(stopped.exception))
        self.assertEqual("emulator-5554", cli.pick_phone(mixed, "emulator-5554"))


class OverWifiTest(unittest.TestCase):
    HEAD = "List of devices attached\n"
    TWICE = HEAD + "127.0.0.1:39957\tdevice\nemulator-5554\tdevice\n"

    def test_a_phone_adb_lists_twice_is_one_phone(self):
        # adb names a phone once per way it reaches it: its Wi-Fi address, a name found on the network, an emulator port.
        same = {"127.0.0.1:39957": "SERIAL1", "emulator-5554": "SERIAL1"}
        self.assertEqual("127.0.0.1:39957", cli.pick_phone(self.TWICE, None, identity=same.get))

    def test_two_different_phones_still_need_a_choice(self):
        different = {"127.0.0.1:39957": "SERIAL1", "emulator-5554": "SERIAL2"}
        with self.assertRaises(cli.Problem):
            cli.pick_phone(self.TWICE, None, identity=different.get)

    def test_a_phone_that_does_not_say_who_it_is_counts_as_its_own(self):
        with self.assertRaises(cli.Problem):
            cli.pick_phone(self.TWICE, None, identity=lambda name: "")

    def test_reads_an_address_with_its_port(self):
        self.assertEqual(("127.0.0.1", 41605), cli.address_of("127.0.0.1:41605"))
        self.assertEqual(("192.168.1.20", 37000), cli.address_of(" 192.168.1.20:37000 "))
        for wrong in ("127.0.0.1", "41605", "phone:port", "127.0.0.1:0", "127.0.0.1:99999", "a b:5", ""):
            with self.assertRaises(cli.Problem, msg=wrong):
                cli.address_of(wrong)

    def test_only_an_address_on_the_person_s_own_network_is_taken(self):
        for near in ("192.168.1.20:5555", "10.0.0.7:5555", "172.16.4.4:5555", "127.0.0.1:5555", "169.254.3.3:5555"):
            self.assertEqual(5555, cli.address_of(near)[1], near)
        for far in ("8.8.8.8:5555", "example.com:5555", "100.64.0.1:5555", "phone.lan:5555"):
            with self.assertRaises(cli.Problem, msg=far) as stopped:
                cli.address_of(far)
            self.assertIn("--any-address", str(stopped.exception))

    def test_any_other_address_is_taken_only_when_asked_and_never_one_that_reads_as_an_option(self):
        self.assertEqual(("8.8.8.8", 5555), cli.address_of("8.8.8.8:5555", anywhere=True))
        self.assertEqual(("phone.lan", 5555), cli.address_of("phone.lan:5555", anywhere=True))
        for wrong in ("-x:5555", "--help:5555", ".:5555"):
            with self.assertRaises(cli.Problem, msg=wrong):
                cli.address_of(wrong, anywhere=True)

    def test_on_the_phone_itself_the_port_alone_is_an_address(self):
        self.assertEqual(("127.0.0.1", 41605), cli.address_of("41605", host=cli.HERE))
        with self.assertRaises(cli.Problem):
            cli.address_of("41605")

    def test_takes_the_code_alone_or_with_where_the_pairing_box_is(self):
        self.assertEqual((None, "947926"), cli.pairing_words(["947926"]))
        self.assertEqual(("127.0.0.1:41605", "947926"), cli.pairing_words(["127.0.0.1:41605", "947926"]))
        self.assertEqual(("41605", "947926"), cli.pairing_words(["947926", "41605"]))
        for wrong in ([], ["12345"], ["94792a"], ["127.0.0.1:41605"], ["947926", "123456"], ["1", "2", "947926"]):
            with self.assertRaises(cli.Problem, msg=str(wrong)):
                cli.pairing_words(wrong)

    def test_looks_first_where_android_puts_its_ports(self):
        ports = cli.likely_ports()
        self.assertEqual(list(range(32768, 61000)), sorted(ports))
        first = ports[:7058]
        self.assertTrue(all(port % 2 == 1 and port < 46884 for port in first))
        for seen_on_the_test_phone in (39957, 41605, 46209, 33483, 46445, 33187, 38203):
            self.assertIn(seen_on_the_test_phone, first)

    def test_finds_the_ports_that_take_a_connection(self):
        with socket.socket() as open_one, socket.socket() as closed_one:
            open_one.bind(("127.0.0.1", 0))
            open_one.listen()
            closed_one.bind(("127.0.0.1", 0))  # taken, and nobody is listening
            ports = [closed_one.getsockname()[1], open_one.getsockname()[1]]
            self.assertEqual(ports[1:], list(cli.listening("127.0.0.1", ports)))

    def test_tells_the_phone_s_debugging_port_from_any_other(self):
        def answering(reply: bytes) -> socket.socket:
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen()

            def serve() -> None:
                with contextlib.suppress(OSError):
                    peer, _ = server.accept()
                    with peer:
                        if peer.recv(64)[:4] == b"CNXN":
                            peer.sendall(reply)

            threading.Thread(target=serve, daemon=True).start()
            return server

        wants_tls = struct.pack("<6I", 0x534C5453, 0x01000000, 0, 0, 0, 0x534C5453 ^ 0xFFFFFFFF)  # adb's "STLS"
        with answering(wants_tls) as debugging, answering(b"HTTP/1.1 400 Bad Request\r\n\r\n") as web:
            self.assertTrue(cli.says_adb("127.0.0.1", debugging.getsockname()[1]))
            self.assertFalse(cli.says_adb("127.0.0.1", web.getsockname()[1]))
        with socket.socket() as nobody:
            nobody.bind(("127.0.0.1", 0))
            self.assertFalse(cli.says_adb("127.0.0.1", nobody.getsockname()[1]))

    def test_knows_what_adb_said_after_pairing(self):
        self.assertIsNone(cli.pairing_problem("Successfully paired to 127.0.0.1:41605 [guid=adb-X]", answers=True))
        # The adb in Termux says the same thing for a wrong code and for a box that has closed.
        fault = "error: protocol fault (couldn't read status message): Success"
        self.assertIn("six digits", cli.pairing_problem(fault, answers=True))
        self.assertIn("closes", cli.pairing_problem(fault, answers=False))
        self.assertIn("six digits", cli.pairing_problem("Failed: Wrong password or connection was dropped.", answers=True))

    def test_knows_what_adb_said_after_connecting(self):
        self.assertIsNone(cli.connecting_problem("connected to 127.0.0.1:39957"))
        self.assertIsNone(cli.connecting_problem("already connected to 127.0.0.1:39957"))
        self.assertIn("pair", cli.connecting_problem("failed to authenticate to 127.0.0.1:39957"))
        refused = "failed to connect to '127.0.0.1:1': Connection refused"
        self.assertIn("switched on", cli.connecting_problem(refused, answers=False))
        self.assertIn("pairing box", cli.connecting_problem("failed to connect to 127.0.0.1:46445", answers=True, is_adb=False))

    def test_adb_that_cannot_start_inside_termux_gets_its_fix(self):
        said = 'CANNOT LINK EXECUTABLE "/data/data/com.termux/files/usr/bin/adb": cannot locate symbol'
        self.assertIn("pkg install -y libc++", cli.adb_trouble(said))
        self.assertIsNone(cli.adb_trouble("List of devices attached\n"))

    def test_on_the_phone_itself_adb_comes_from_termux_not_from_a_download(self):
        with self.assertRaises(cli.Problem) as stopped:
            cli.download_adb(system="Android")
        self.assertIn("pkg install android-tools", str(stopped.exception))


class OnThePhoneTest(unittest.TestCase):
    """The tool running inside Termux, on the phone it installs on. adb and the ports are stand-ins."""

    PAIRED = "Successfully paired to 127.0.0.1:41605 [guid=adb-X]"
    FAULT = "error: protocol fault (couldn't read status message): Success"

    def stand_in(self, answers: dict):
        ran: list[tuple[str, ...]] = []

        def run_adb(adb: str, *args: str, timeout: int = 30) -> str:
            ran.append(args)
            return answers.get(args, self.FAULT)

        return ran, mock.patch.object(cli, "run_adb", run_adb)

    def open_ports(self, *ports: int):
        return mock.patch.object(cli, "listening", lambda host, among: iter(ports))

    def test_the_code_alone_finds_the_pairing_box(self):
        ran, adb = self.stand_in({("pair", "127.0.0.1:41605", "947926"): self.PAIRED})
        with adb, self.open_ports(39957, 41605, 43001):
            self.assertEqual([39957], cli.pair_here("adb", "947926"))
        self.assertEqual([("pair", "127.0.0.1:39957", "947926"), ("pair", "127.0.0.1:41605", "947926")], ran)

    def test_a_code_no_box_takes_is_a_message_about_the_box(self):
        ran, adb = self.stand_in({})
        with adb, self.open_ports(39957), self.assertRaises(cli.Problem) as stopped:
            cli.pair_here("adb", "947926")
        self.assertIn("split screen", str(stopped.exception))

    def test_a_port_where_something_else_never_answers_is_passed_over(self):
        def run_adb(adb: str, *args: str, timeout: int = 30) -> str:
            if args[1] == "127.0.0.1:38111":
                raise subprocess.TimeoutExpired("adb", timeout)
            return self.PAIRED

        with mock.patch.object(cli, "run_adb", run_adb), self.open_ports(38111, 41605):
            self.assertEqual([38111], cli.pair_here("adb", "947926"))

    def test_connecting_asks_adb_only_about_the_debugging_port(self):
        ran, adb = self.stand_in({("connect", "127.0.0.1:39957"): "connected to 127.0.0.1:39957"})
        with adb, self.open_ports(38111, 39957), mock.patch.object(cli, "says_adb", lambda host, port: port == 39957):
            self.assertEqual(39957, cli.connect_here("adb"))
        self.assertEqual([("connect", "127.0.0.1:39957")], ran)

    def test_ports_met_while_pairing_are_asked_first_and_never_twice(self):
        asked = []

        def says_adb(host: str, port: int) -> bool:
            asked.append(port)
            return port == 39957

        ran, adb = self.stand_in({("connect", "127.0.0.1:39957"): "connected to 127.0.0.1:39957"})
        with adb, self.open_ports(33001, 39957), mock.patch.object(cli, "says_adb", says_adb):
            self.assertEqual(39957, cli.connect_here("adb", first=[38111, 39957]))
        self.assertEqual([38111, 39957], asked)

    def test_no_debugging_port_says_to_switch_the_setting_on(self):
        ran, adb = self.stand_in({})
        with adb, self.open_ports(), self.assertRaises(cli.Problem) as stopped:
            cli.connect_here("adb")
        self.assertIn("Wireless debugging", str(stopped.exception))

    def test_a_phone_that_does_not_know_this_program_says_to_pair(self):
        ran, adb = self.stand_in({("connect", "127.0.0.1:39957"): "failed to authenticate to 127.0.0.1:39957"})
        with adb, self.open_ports(39957), mock.patch.object(cli, "says_adb", lambda host, port: True), \
                self.assertRaises(cli.Problem) as stopped:
            cli.connect_here("adb")
        self.assertIn("pair", str(stopped.exception))
        self.assertIn(("disconnect", "127.0.0.1:39957"), ran)


class FromAComputerTest(unittest.TestCase):
    def test_the_code_alone_is_not_enough(self):
        with mock.patch.object(cli, "on_the_phone", lambda: False):
            code, said = run_tool("pair", "947926")
        self.assertEqual(2, code)
        self.assertIn("pairing box", said)

    def test_connect_needs_the_address(self):
        with mock.patch.object(cli, "on_the_phone", lambda: False):
            code, said = run_tool("connect")
        self.assertEqual(2, code)
        self.assertIn("Wireless debugging screen", said)


class ReadingThePhoneTest(unittest.TestCase):
    def test_reads_what_the_service_reports(self):
        report = "SERVICE io.github.mendypf.nomoredoom/.WatchService 1a2b pid=4242\n  Client:\n    version=0.1.0\n" \
                 "    connected=true\n    watching=Shorts\n    cover=on\n    today_s=Shorts:612,Reels:40\n"
        info = cli.status(FakePhone({"dumpsys activity service": report}))
        self.assertEqual("Shorts", info["watching"])
        self.assertEqual("on", info["cover"])
        self.assertEqual("Shorts:612,Reels:40", info["today_s"])

    def test_a_service_that_is_not_running_reads_as_not_connected(self):
        self.assertEqual({"connected": "false"}, cli.status(FakePhone({"dumpsys activity service": "No services match\n"})))
        self.assertEqual({"connected": "false"}, cli.status(FakePhone({})))

    def test_spots_a_tiktok_this_build_does_not_listen_to(self):
        listing = "package:com.android.settings\npackage:com.zhiliaoapp.musically\npackage:com.zhiliaoapp.musically.go\n" \
                  "package:com.ss.android.ugc.aweme\npackage:com.google.android.youtube\n"
        found = cli.lookalikes(FakePhone({"pm list packages": listing}))
        self.assertEqual(["com.ss.android.ugc.aweme", "com.zhiliaoapp.musically.go"], found)


class ObedientPhone(FakePhone):
    """A phone that is still reachable after Wireless debugging goes off (a cable, or an emulator)."""

    def __init__(self, wireless: str = "1", serial: str = "R58M12345"):
        super().__init__({})
        self.wireless, self.serial, self.adb = wireless, serial, "adb"

    def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        self.asked.append(command)
        if command.startswith("settings put global adb_wifi_enabled 0"):
            self.wireless = "0"
            return ""
        if command.startswith("settings get global adb_wifi_enabled"):
            return self.wireless + "\n"
        return ""


class CutOffPhone(FakePhone):
    """Over Wi-Fi the command that closes the door cuts the line it travels on: no answer comes back."""

    def __init__(self, serial: str = "192.168.1.20:41235", reads_before: bool = True):
        super().__init__({})
        self.serial, self.adb, self.cut, self.reads_before = serial, "adb", False, reads_before

    def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        self.asked.append(command)
        if command.startswith("settings put global adb_wifi_enabled 0"):
            self.cut = True
            return ""
        if command.startswith("settings get global adb_wifi_enabled") and self.reads_before and not self.cut:
            return "1\n"
        return "error: device offline\n"

    def run(self, *args: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
        self.ran.append(args)
        return "error: device '192.168.1.20:41235' not found\n"


class ClosingUpTest(unittest.TestCase):
    """When the work is done, Wireless debugging goes off again and the person hears about it.

    The tool says "off" only when it has seen that. A phone it lost sight of is "cannot tell".
    """

    def setUp(self):
        quiet = mock.patch.object(cli.time, "sleep", lambda seconds: None)
        quiet.start()
        self.addCleanup(quiet.stop)

    def tool(self, phone, *argv: str, door: str = "shut"):
        ran: list[tuple[str, ...]] = []

        def run_adb(adb: str, *args: str, timeout: int = 30) -> str:
            ran.append(args)
            return ""

        with mock.patch.object(cli, "connect", lambda args: phone), mock.patch.object(cli, "need_adb", lambda args: "adb"), \
                mock.patch.object(cli, "run_adb", run_adb), mock.patch.object(cli, "door", lambda host, port: door):
            code, printed, heard = run_tool_aloud(*argv)
        return code, printed, heard, ran

    def knocking(self, *answers: str):
        """The phone's debugging port answering a knock this way each time; the last answer repeats."""
        heard = list(answers)
        asked: list[tuple[str, int]] = []

        def door(host: str, port: int) -> str:
            asked.append((host, port))
            return heard.pop(0) if len(heard) > 1 else heard[0]

        return asked, mock.patch.object(cli, "door", door)

    def test_reads_whether_wireless_debugging_is_on_off_or_cannot_be_told(self):
        def state(answer: str) -> str:
            return cli.wireless_state(FakePhone({"settings get global adb_wifi_enabled": answer}))

        self.assertEqual("on", state("1\n"))
        self.assertEqual("off", state("0\n"))
        self.assertEqual("off", state("null\n"))  # a phone that never had the setting
        for no_answer in ("error: device offline\n", "error: closed\n", "", "adb: device '192.168.1.20:41235' not found\n"):
            self.assertEqual("unknown", state(no_answer), no_answer)

    def test_a_phone_that_never_answers_cannot_be_told_either(self):
        class Silent(FakePhone):
            def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                raise subprocess.TimeoutExpired("adb", timeout)

        self.assertEqual("unknown", cli.wireless_state(Silent({})))

    def test_a_knock_tells_open_from_shut_from_silence(self):
        wants_tls = struct.pack("<6I", 0x534C5453, 0x01000000, 0, 0, 0, 0x534C5453 ^ 0xFFFFFFFF)  # adb's "STLS"
        done = threading.Event()

        def serving(act) -> socket.socket:
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen()

            def serve() -> None:
                with contextlib.suppress(OSError):
                    peer, _ = server.accept()
                    with peer:
                        act(peer)

            threading.Thread(target=serve, daemon=True).start()
            return server

        def debugging(peer: socket.socket) -> None:
            if peer.recv(64)[:4] == b"CNXN":
                peer.sendall(wants_tls)

        def something_else(peer: socket.socket) -> None:
            peer.recv(64)
            peer.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")

        def says_nothing(peer: socket.socket) -> None:
            done.wait(10)

        with serving(debugging) as on, serving(something_else) as other, serving(says_nothing) as mute:
            self.assertEqual("open", cli.door("127.0.0.1", on.getsockname()[1]))
            self.assertEqual("shut", cli.door("127.0.0.1", other.getsockname()[1]))
            self.assertEqual("unknown", cli.door("127.0.0.1", mute.getsockname()[1], wait=0.3))
            done.set()
        with socket.socket() as nobody:
            nobody.bind(("127.0.0.1", 0))  # taken, and nobody is listening: the answer is "refused"
            self.assertEqual("shut", cli.door("127.0.0.1", nobody.getsockname()[1], wait=8))

    def test_a_phone_still_in_reach_is_asked_whether_it_went_off(self):
        phone = ObedientPhone()
        self.assertEqual("off", cli.wireless_off(phone))
        self.assertIn("settings put global adb_wifi_enabled 0", phone.asked)

    def test_a_phone_that_says_it_is_still_on_is_believed(self):
        class Stubborn(ObedientPhone):
            def shell(self, command: str, check: bool = True, timeout: int = 60, stderr: bool = True) -> str:
                self.asked.append(command)
                return "1\n" if command.startswith("settings get") else ""

        self.assertEqual("on", cli.wireless_off(Stubborn()))

    def test_over_wifi_the_proof_is_the_phone_answering_twice_that_nothing_listens_there(self):
        asked, door = self.knocking("shut")
        with door:
            self.assertEqual("off", cli.wireless_off(CutOffPhone()))
        self.assertEqual([("192.168.1.20", 41235)] * 2, asked)

    def test_a_debugging_port_that_still_answers_means_it_is_still_on(self):
        asked, door = self.knocking("open")
        with door:
            self.assertEqual("on", cli.wireless_off(CutOffPhone()))

    def test_a_phone_that_went_silent_proves_nothing(self):
        # A dropped Wi-Fi link looks the same from here, with the setting still on.
        asked, door = self.knocking("unknown")
        with door:
            self.assertEqual("unknown", cli.wireless_off(CutOffPhone()))

    def test_one_closed_answer_followed_by_silence_is_not_proof(self):
        asked, door = self.knocking("shut", "unknown")
        with door:
            self.assertEqual("unknown", cli.wireless_off(CutOffPhone()))

    def test_a_door_that_takes_a_moment_to_shut_still_counts(self):
        asked, door = self.knocking("open", "shut")
        with door:
            self.assertEqual("off", cli.wireless_off(CutOffPhone()))

    def test_a_phone_adb_found_by_name_has_no_port_to_knock_on(self):
        asked, door = self.knocking("shut")
        with door:
            self.assertEqual("unknown", cli.wireless_off(CutOffPhone(serial="adb-R58M12345-AbCdEf._adb-tls-connect._tcp")))
        self.assertEqual([], asked)

    def test_disconnect_switches_it_off_and_says_what_is_left(self):
        phone = CutOffPhone()
        code, printed, heard, ran = self.tool(phone, "disconnect")
        self.assertEqual((0, ""), (code, heard))
        self.assertIn("settings put global adb_wifi_enabled 0", phone.asked)
        self.assertIn("Wireless debugging is off", printed)
        self.assertIn("Nomoredoom keeps running", printed)
        self.assertIn("Forget", printed)  # how to erase the pairing too
        self.assertIn(("disconnect", "192.168.1.20:41235"), ran)  # and the address comes off this side's list

    def test_disconnect_that_cannot_close_it_says_how_to_by_hand(self):
        code, printed, heard, ran = self.tool(CutOffPhone(), "disconnect", door="open")
        self.assertEqual(2, code)
        self.assertIn("Developer options", heard)
        self.assertNotIn("is off", printed)

    def test_disconnect_never_says_off_about_a_phone_it_could_not_read(self):
        # The Wi-Fi link hiccups: the phone answers nothing, before or after. That is not "already off".
        phone = CutOffPhone(reads_before=False)
        code, printed, heard, ran = self.tool(phone, "disconnect", door="unknown")
        self.assertEqual(1, code)  # not confirmed: somebody has to look
        self.assertNotIn("already off", printed)
        self.assertNotIn("is off", printed)
        self.assertIn("Developer options", printed)
        self.assertIn("settings put global adb_wifi_enabled 0", phone.asked)  # it still tried
        self.assertIn(("disconnect", "192.168.1.20:41235"), ran)

    def test_disconnect_that_loses_the_phone_half_way_asks_for_a_look(self):
        code, printed, heard, ran = self.tool(CutOffPhone(), "disconnect", door="unknown")
        self.assertEqual(1, code)
        self.assertNotIn("is off", printed)
        self.assertIn("Developer options", printed)

    def test_a_phone_that_could_not_be_read_but_whose_door_is_shut_is_off(self):
        code, printed, heard, ran = self.tool(CutOffPhone(reads_before=False), "disconnect", door="shut")
        self.assertEqual(0, code)
        self.assertIn("Wireless debugging is off", printed)

    def test_disconnect_when_it_is_off_already_says_so(self):
        code, printed, heard, ran = self.tool(ObedientPhone(wireless="0", serial="emulator-5554"), "disconnect")
        self.assertEqual(0, code)
        self.assertIn("already off", printed)

    def test_disconnect_over_a_cable_names_the_switch_that_is_still_on(self):
        code, printed, heard, ran = self.tool(ObedientPhone(wireless="0"), "disconnect")
        self.assertEqual(0, code)
        self.assertIn("USB debugging is still on", printed)

    def test_disconnect_with_no_phone_in_reach_says_to_look_at_the_phone(self):
        def nobody(args):
            raise cli.NoPhone("no phone found")

        with mock.patch.object(cli, "connect", nobody), mock.patch.object(cli, "need_adb", lambda args: "adb"):
            code, printed, heard = run_tool_aloud("disconnect")
        self.assertEqual(1, code)  # not confirmed: somebody has to look
        self.assertIn("Wireless debugging should be off", printed)

    def test_no_phone_is_its_own_kind_of_problem(self):
        with self.assertRaises(cli.NoPhone):
            cli.pick_phone("List of devices attached\n\n", None)
        with self.assertRaises(cli.NoPhone):
            cli.pick_phone("List of devices attached\n\n", None, here=True)
        self.assertTrue(issubclass(cli.NoPhone, cli.Problem))

    def installed_on(self, phone):
        running = {"version": "0.1.0"}
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "nomoredoom.apk"
            apk.write_bytes(b"x" * 2000)
            with mock.patch.object(cli, "find_apk", lambda args: apk), mock.patch.object(cli, "put_on", lambda *a: None), \
                    mock.patch.object(cli, "switch_on", lambda phone: None), \
                    mock.patch.object(cli, "wait_until_running", lambda phone: running):
                return self.tool(phone, "install", "--no-gifs")

    def test_an_install_over_wifi_ends_by_saying_how_to_close_up(self):
        code, printed, heard, ran = self.installed_on(ObedientPhone(wireless="1", serial="192.168.1.20:41235"))
        self.assertEqual(0, code)
        self.assertIn("disconnect", printed.strip().splitlines()[-1])
        self.assertIn("Wireless debugging is still on", printed)

    def test_an_install_with_wireless_debugging_off_does_not_bring_it_up(self):
        code, printed, heard, ran = self.installed_on(ObedientPhone(wireless="0"))
        self.assertEqual(0, code)
        self.assertNotIn("Wireless debugging", printed)

    def test_an_install_that_loses_sight_of_the_phone_still_says_how_to_close_up(self):
        code, printed, heard, ran = self.installed_on(ObedientPhone(wireless="error: closed", serial="192.168.1.20:41235"))
        self.assertEqual(0, code)
        self.assertIn("disconnect", printed.strip().splitlines()[-1])
        self.assertNotIn("is still on", printed)

    def test_an_install_says_that_it_switched_the_accessibility_service_on_itself(self):
        # Android's own "full control" box does not show on this path, so the tool says what it did.
        code, printed, heard, ran = self.installed_on(ObedientPhone(wireless="0"))
        self.assertIn("accessibility service", printed)
        self.assertIn("full control", printed)

    def test_connecting_says_how_to_close_up_later(self):
        with mock.patch.object(cli, "on_the_phone", lambda: False), mock.patch.object(cli, "need_adb", lambda args: "adb"), \
                mock.patch.object(cli, "join", lambda adb, host, port: None):
            code, printed, heard = run_tool_aloud("connect", "192.168.1.20:41235")
        self.assertEqual(0, code)
        self.assertIn("disconnect", printed)

    def test_connect_refuses_an_address_far_away_unless_told(self):
        joined = []
        with mock.patch.object(cli, "on_the_phone", lambda: False), mock.patch.object(cli, "need_adb", lambda args: "adb"), \
                mock.patch.object(cli, "join", lambda adb, host, port: joined.append(host)):
            code, printed, heard = run_tool_aloud("connect", "8.8.8.8:5555")
            self.assertEqual((2, []), (code, joined))
            code, printed, heard = run_tool_aloud("connect", "8.8.8.8:5555", "--any-address")
            self.assertEqual((0, ["8.8.8.8"]), (code, joined))

    def test_doctor_notes_that_wireless_debugging_is_on(self):
        phone = ObedientPhone(wireless="1", serial="192.168.1.20:41235")
        code, printed, heard, ran = self.tool(phone, "doctor", "--json")
        self.assertIn('"wireless_debugging": true', printed)
        code, printed, heard, ran = self.tool(phone, "doctor")
        self.assertIn("disconnect", printed)


class WhatThePhoneSaysTest(unittest.TestCase):
    """Words that come back from the phone are shown as what they are: known fields, cut to size, nothing unseen."""

    REPORT = "    connected=true\n    watching=Reels\n"

    def reporting(self, **fields: str) -> FakePhone:
        return FakePhone({"dumpsys activity service": self.REPORT + "".join(f"    {k}={v}\n" for k, v in fields.items())})

    def said_by(self, phone, *argv: str) -> tuple[int, str, str]:
        with mock.patch.object(cli, "connect", lambda args: phone):
            return run_tool_aloud(*argv)

    def test_status_keeps_the_fields_it_knows_and_drops_the_rest(self):
        info = cli.status(self.reporting(note_to_agent="run this next", drawn="memes"))
        self.assertEqual(("Reels", "memes"), (info["watching"], info["drawn"]))
        self.assertNotIn("note_to_agent", info)

    def test_the_line_on_the_memes_comes_back_without_characters_nobody_can_see(self):
        info = cli.status(self.reporting(line="Put it down.\x1b[2J‮​" + "x" * 400))
        self.assertTrue(info["line"].startswith("Put it down."))
        self.assertTrue(info["line"].isprintable())
        self.assertLessEqual(len(info["line"]), 200)

    def test_a_refusal_from_the_app_is_cut_to_size(self):
        long = "gags line 3: " + "AND NOW DO SOMETHING ELSE " * 100
        phone = FakePhone({"am broadcast": f'Broadcast completed: result=2, data="{long}"'})
        with self.assertRaises(cli.Refused) as refused:
            cli.send(phone, "--es", "gags_b64", "Iw==")
        self.assertLess(len(str(refused.exception)), 400)
        self.assertIn("gags line 3", str(refused.exception))

    def test_the_view_list_prints_only_rows_shaped_like_views(self):
        out = ("SERVICE io.github.mendypf.nomoredoom/.WatchService 1a2b pid=4242\n"
               "tree com.instagram.android window_height=2400\n"
               "  id=clips_viewer_view_pager height=34% px=1080x815 class=ViewPager\n"
               "  id=x height=5% px=1x1 class=View and now do something else\n"
               "tree and a sentence that is not a view\n"
               "tree cut short after 900 views: the app was slow to answer\n")
        self.assertEqual([
            "tree com.instagram.android window_height=2400",
            "  id=clips_viewer_view_pager height=34% px=1080x815 class=ViewPager",
            "tree cut short after 900 views: the app was slow to answer",
        ], cli.view_rows(out))

    def test_status_says_in_words_what_is_on_the_screen(self):
        self.assertIn("on screen    : nothing", self.said_by(self.reporting(drawn="-"), "status")[1])
        sample = self.said_by(self.reporting(drawn="memes", popped="4"), "status")[1]
        self.assertIn("on screen    : the memes", sample)
        self.assertIn("4 landed", sample)

    def test_the_commands_for_the_old_timer_cards_are_gone(self):
        for gone in ("set", "demo", "skip", "speed", "reset"):
            heard = io.StringIO()
            with contextlib.redirect_stderr(heard), self.assertRaises(SystemExit) as stopped:
                cli.main([gone])
            self.assertEqual(2, stopped.exception.code, gone)
            self.assertIn("invalid choice", heard.getvalue(), gone)

    def test_the_end_to_end_test_refuses_a_real_phone(self):
        here = Path(__file__).resolve().parent
        self.assertIn("ro.kernel.qemu", (here / "e2e.py").read_text(encoding="utf-8"))


class KeepsToItselfTest(unittest.TestCase):
    """What the tool does not read, and how it names itself on the web."""

    def test_the_tests_run_away_from_this_computer_s_own_link_and_settings(self):
        for path in (cli.CACHE, cli.AGENT_FILE, cli.SESSIONS_FILE, cli.claude_settings()):
            self.assertTrue(str(path.resolve()).startswith(str(SCRATCH_HOME.resolve())), path)  # 3.8 has no is_relative_to

    def test_asks_the_phone_only_for_app_names_that_look_like_tiktok(self):
        phone = FakePhone({"pm list packages": "package:com.zhiliaoapp.musically.go\npackage:com.zhiliaoapp.musically\n"})
        self.assertEqual(["com.zhiliaoapp.musically.go"], cli.lookalikes(phone))
        self.assertTrue(phone.asked)
        for command in phone.asked:  # never the list of everything the person has installed
            self.assertRegex(command, r"^pm list packages [a-z.]+$")

    def test_gifs_are_fetched_under_the_tool_s_own_name(self):
        seen = {}

        def download(url: str, limit: int, agent: str | None = None) -> bytes:
            seen["agent"] = agent
            return b"GIF89a" + bytes(20)

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(cli, "CACHE", Path(folder)), \
                mock.patch.object(cli, "download", download):
            self.assertIsNotNone(cli.fetch_gif("abc", "https://media.giphy.com/media/abc/giphy.gif", Path(folder)))
        self.assertIn("nomoredoom", seen["agent"])
        self.assertNotIn("Mozilla", seen["agent"])


class AgentTest(unittest.TestCase):
    """The coding agent's side: what its hooks post, and what linking does to Claude Code's settings."""

    URL = "https://ntfy.sh/nomoredoom-test"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.sessions = self.dir / "sessions.json"
        self.posted: list[tuple[str, str]] = []

    def post(self, url: str, word: str, tag: str) -> None:
        self.posted.append((word, tag))

    def hook(self, name: str, at: float, session: str = "s1", post=None) -> str | None:
        event = {"hook_event_name": name, "session_id": session}
        return cli.on_hook(event, self.URL, at, self.sessions, post or self.post)

    def test_a_turn_posts_working_at_its_first_tool_call_and_done_at_its_end(self):
        self.assertEqual("working", self.hook("PreToolUse", 1000))
        self.assertIsNone(self.hook("PreToolUse", 1010))
        self.assertIsNone(self.hook("PreToolUse", 1100))
        self.assertEqual("done", self.hook("Stop", 1200))
        self.assertEqual([w for w, _ in self.posted], ["working", "done"])

    def test_a_turn_without_tools_posts_nothing(self):
        self.assertIsNone(self.hook("Stop", 1000))
        self.assertIsNone(self.hook("SessionEnd", 1001))
        self.assertEqual([], self.posted)

    def test_a_long_turn_says_working_again_inside_the_phones_window(self):
        self.hook("PreToolUse", 1000)
        self.assertIsNone(self.hook("PreToolUse", 1000 + cli.HEARTBEAT_S - 1))
        self.assertEqual("working", self.hook("PreToolUse", 1000 + cli.HEARTBEAT_S))
        self.assertLess(cli.HEARTBEAT_S * 2, cli.WINDOW_MINUTES * 60, "two beats fit in the window")

    def test_two_sessions_are_told_apart(self):
        self.hook("PreToolUse", 1000, "a")
        self.hook("PreToolUse", 1001, "b")
        self.hook("Stop", 1002, "a")
        tags = {tag for _, tag in self.posted}
        self.assertEqual(2, len(tags))
        self.assertEqual(self.posted[2], ("done", self.posted[0][1]))

    def test_the_tag_says_nothing_about_the_session(self):
        self.hook("PreToolUse", 1000, "4f1c2b9e-session-id")
        tag = self.posted[0][1]
        self.assertRegex(tag, r"^[0-9a-f]{8}$")
        self.assertNotIn(tag, "4f1c2b9e-session-id")

    def test_a_post_that_fails_is_tried_again_a_minute_later_not_at_every_tool_call(self):
        def down(url, word, tag):
            raise OSError("429 too many requests")
        with self.assertRaises(OSError):
            self.hook("PreToolUse", 1000, post=down)
        self.assertIsNone(self.hook("PreToolUse", 1000 + cli.RETRY_S - 1))
        self.assertEqual("working", self.hook("PreToolUse", 1000 + cli.RETRY_S))

    def test_a_broken_sessions_file_starts_over(self):
        self.sessions.write_text("{not json", encoding="utf-8")
        self.assertEqual("working", self.hook("PreToolUse", 1000))

    def test_only_working_and_done_with_a_plain_tag_can_be_posted(self):
        for word, tag in (("hello", "a1"), ("working", "a 1"), ("done", ""), ("working", "x" * 33)):
            with self.assertRaises(ValueError):
                cli.post_signal(self.URL, word, tag)

    def test_the_hook_prints_nothing_and_exits_0_even_when_everything_goes_wrong(self):
        def down(url, word, tag):
            raise OSError("no network")
        stdin = io.StringIO('{"hook_event_name": "PreToolUse", "session_id": "s1"}')
        with mock.patch.object(cli, "linked_url", lambda: self.URL), mock.patch.object(cli, "post_signal", down), \
                mock.patch.object(cli, "SESSIONS_FILE", self.sessions), \
                mock.patch.object(cli, "HOOK_LOG", self.dir / "hook.log"), mock.patch.object(sys, "stdin", stdin):
            code, printed, said = run_tool_aloud("agent", "hook")
        self.assertEqual((0, "", ""), (code, printed, said))
        self.assertIn("no network", (self.dir / "hook.log").read_text(encoding="utf-8"))

    def test_the_hook_does_nothing_until_linked(self):
        stdin = io.StringIO("this is not even json")
        with mock.patch.object(cli, "linked_url", lambda: None), mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(cli, "post_signal", self.post):
            self.assertEqual((0, "", ""), run_tool_aloud("agent", "hook"))
        self.assertEqual([], self.posted)

    def test_a_new_topic_cannot_be_guessed_and_fits_the_phones_rules(self):
        first, second = cli.new_topic(), cli.new_topic()
        self.assertNotEqual(first, second)
        for url in (first, second, cli.new_topic("https://ntfy.example.org/")):
            self.assertRegex(url, cli.TOPIC_URL)
            self.assertGreaterEqual(len(url.rsplit("/", 1)[1]), 30)
        with self.assertRaises(cli.Problem):
            cli.new_topic("http://ntfy.sh")

    # ---- Claude Code's settings

    COMMAND = 'python "C:/somewhere/nomoredoom/bin/nomoredoom.py" agent hook'
    THEIRS = {
        "model": "opus",
        "statusLine": {"type": "command", "command": "node statusline.js"},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "check-bash.sh"}]}],
            "Notification": [{"hooks": [{"type": "command", "command": "beep"}]}],
        },
    }

    def settings_file(self, data: object | None = None) -> Path:
        path = self.dir / "settings.json"
        if data is not None:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def test_linking_adds_three_hooks_and_keeps_everything_else(self):
        path = self.settings_file(self.THEIRS)
        self.assertEqual(["PreToolUse", "Stop", "SessionEnd"], cli.add_claude_hooks(path, self.COMMAND))
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("opus", after["model"])
        self.assertEqual(self.THEIRS["statusLine"], after["statusLine"])
        self.assertEqual(self.THEIRS["hooks"]["Notification"], after["hooks"]["Notification"])
        self.assertEqual(self.THEIRS["hooks"]["PreToolUse"][0], after["hooks"]["PreToolUse"][0])
        ours = after["hooks"]["PreToolUse"][1]
        self.assertEqual("*", ours["matcher"])
        self.assertEqual({"type": "command", "command": self.COMMAND, "async": True, "timeout": 15}, ours["hooks"][0])
        self.assertTrue(after["hooks"]["Stop"][0]["hooks"][0]["async"])
        self.assertNotIn("async", after["hooks"]["SessionEnd"][0]["hooks"][0])

    def test_linking_twice_leaves_one_set_of_hooks(self):
        path = self.settings_file(self.THEIRS)
        cli.add_claude_hooks(path, self.COMMAND)
        cli.add_claude_hooks(path, self.COMMAND.replace("somewhere", "elsewhere"))
        after = json.loads(path.read_text(encoding="utf-8"))
        for event in ("PreToolUse", "Stop", "SessionEnd"):
            commands = [h["command"] for g in after["hooks"][event] for h in g["hooks"]]
            self.assertEqual(1, sum(c.endswith("agent hook") for c in commands), event)
            self.assertNotIn("somewhere", " ".join(commands))

    def test_unlinking_gives_back_the_settings_as_they_were(self):
        path = self.settings_file(self.THEIRS)
        cli.add_claude_hooks(path, self.COMMAND)
        self.assertTrue(cli.remove_claude_hooks(path))
        self.assertEqual(self.THEIRS, json.loads(path.read_text(encoding="utf-8")))
        self.assertFalse(cli.remove_claude_hooks(path))

    def test_a_settings_file_that_did_not_exist_is_made_and_emptied_again(self):
        path = self.settings_file()
        cli.add_claude_hooks(path, self.COMMAND)
        self.assertIn("hooks", json.loads(path.read_text(encoding="utf-8")))
        cli.remove_claude_hooks(path)
        self.assertEqual({}, json.loads(path.read_text(encoding="utf-8")))

    def test_the_file_from_before_is_kept_once(self):
        path = self.settings_file(self.THEIRS)
        cli.add_claude_hooks(path, self.COMMAND)
        cli.add_claude_hooks(path, self.COMMAND)
        backup = path.with_name("settings.json.before-nomoredoom")
        self.assertEqual(self.THEIRS, json.loads(backup.read_text(encoding="utf-8")))

    def test_a_settings_file_it_cannot_read_is_left_untouched(self):
        path = self.settings_file()
        for text in ("{ not json", "[1, 2]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(cli.Problem):
                cli.add_claude_hooks(path, self.COMMAND)
            self.assertEqual(text, path.read_text(encoding="utf-8"))

    def test_the_hook_line_names_this_tool_and_is_recognised_as_ours(self):
        command = cli.hook_command()
        self.assertTrue(command.endswith('nomoredoom.py" agent hook; exit 0'), command)
        self.assertTrue(cli.ours({"type": "command", "command": 'python "C:/x/shortstop.py" agent hook'}),
                        "a hook from before the rename comes off too")
        self.assertTrue(cli.ours({"type": "command", "command": command}))
        self.assertFalse(cli.ours({"type": "command", "command": "python other.py agent hook"}))
        self.assertNotIn("\\", command, "forward slashes work in Git Bash and in PowerShell")


def transcript_line(entry: dict) -> bytes:
    return (json.dumps(entry) + "\n").encode()


ESC_LINE = transcript_line({"type": "user", "isSidechain": False, "message": {
    "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}})
ESC_IN_A_TOOL = transcript_line({"type": "user", "message": {
    "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}})
WORK_LINE = transcript_line({"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "sleep 30"}}]}})


class FakeClock:
    """time.time and time.sleep for the watcher, with things that happen at given seconds."""

    def __init__(self, events: dict[float, object] | None = None):
        self.t = 1000.0
        self.events = sorted((events or {}).items())

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        while self.events and self.events[0][0] <= self.t - 1000:
            self.events.pop(0)[1]()


class EscTest(unittest.TestCase):
    """The watcher that turns Claude Code's Esc line into "done"."""

    URL = "https://ntfy.sh/nomoredoom-test"
    TAG = "ab12cd34"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.sessions = self.dir / "agent-sessions.json"
        self.transcript = self.dir / "session.jsonl"
        self.transcript.write_bytes(WORK_LINE)
        cli.save_json(self.sessions, {self.TAG: 1000.0})
        self.posted: list[tuple[str, str]] = []

    def post(self, url, word, tag):
        self.posted.append((word, tag))

    def append(self, data: bytes) -> None:
        with self.transcript.open("ab") as file:
            file.write(data)

    def add(self, data: bytes):
        return lambda: self.append(data)

    def watch(self, clock: FakeClock, offset: int | None = None) -> str:
        start = self.transcript.stat().st_size if offset is None else offset
        return cli.watch_transcript(self.TAG, self.transcript, start, self.URL, self.sessions,
                                    now=clock.now, sleep=clock.sleep, post=self.post)

    def test_the_esc_line_is_known_in_both_its_forms_and_nothing_else_is(self):
        self.assertTrue(cli.is_interrupt(ESC_LINE.strip()))
        self.assertTrue(cli.is_interrupt(ESC_IN_A_TOOL.strip()))
        self.assertTrue(cli.is_interrupt(transcript_line(
            {"type": "user", "message": {"content": "[Request interrupted by user]"}}).strip()))
        for line in (
            transcript_line({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "[Request interrupted by user for tool use]"}]}}),
            transcript_line({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "[Request interrupted by user]"}]}}),
            transcript_line({"type": "user", "isSidechain": True, "message": {"content": [
                {"type": "text", "text": "[Request interrupted by user]"}]}}),
            transcript_line({"type": "user", "message": {"content": [
                {"type": "text", "text": "Why did you write [Request interrupted by user] there?"}]}}),
            b'{"type": "user", "message": [Request interrupted by user',
            WORK_LINE,
        ):
            self.assertFalse(cli.is_interrupt(line.strip()), line)

    def test_esc_posts_done_at_once_and_ends_the_session(self):
        clock = FakeClock({3: self.add(WORK_LINE), 6: self.add(ESC_LINE)})
        self.assertEqual("interrupted", self.watch(clock))
        self.assertEqual([("done", self.TAG)], self.posted)
        self.assertLessEqual(clock.t - 1000, 7, "within a second of the line")
        self.assertNotIn(self.TAG, json.loads(self.sessions.read_text(encoding="utf-8")))
        self.assertFalse(cli.watch_mark(self.TAG, self.dir).exists(), "it cleans up after itself")

    def test_a_line_written_in_two_pieces_is_still_read(self):
        clock = FakeClock({2: self.add(ESC_LINE[:30]), 4: self.add(ESC_LINE[30:])})
        self.assertEqual("interrupted", self.watch(clock))

    def test_an_esc_from_before_the_watcher_started_is_not_this_turn(self):
        self.append(ESC_LINE)
        clock = FakeClock({5: lambda: cli.save_json(self.sessions, {})})
        self.assertEqual("done", self.watch(clock))
        self.assertEqual([], self.posted)

    def test_when_the_turn_ends_normally_the_watcher_leaves_without_posting(self):
        clock = FakeClock({4: lambda: cli.save_json(self.sessions, {})})
        self.assertEqual("done", self.watch(clock))
        self.assertEqual([], self.posted)

    def test_a_quiet_session_is_left_to_the_phones_window(self):
        clock = FakeClock()
        self.assertEqual("quiet", self.watch(clock))
        self.assertGreater(clock.t - 1000, cli.WINDOW_MINUTES * 60)
        self.assertEqual([], self.posted)

    def test_a_transcript_that_goes_away_ends_the_watch(self):
        clock = FakeClock({2: self.transcript.unlink})
        self.assertEqual("gone", self.watch(clock))

    def test_a_failed_post_is_tried_again(self):
        tries = []

        def flaky(url, word, tag):
            tries.append(word)
            if len(tries) < 2:
                raise OSError("network down")
        clock = FakeClock({2: self.add(ESC_LINE)})
        result = cli.watch_transcript(self.TAG, self.transcript, self.transcript.stat().st_size, self.URL,
                                      self.sessions, now=clock.now, sleep=clock.sleep, post=flaky)
        self.assertEqual(("interrupted", ["done", "done"]), (result, tries))

    def test_a_working_session_gets_one_watcher(self):
        started = []
        event = {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": str(self.transcript)}

        def spawn(tag, transcript, folder, offset):
            started.append((tag, transcript, offset))
        cli.save_json(self.sessions, {})
        cli.on_hook(event, self.URL, time.time(), self.sessions, self.post, spawn)
        cli.on_hook(event, self.URL, time.time() + 1, self.sessions, self.post, spawn)
        self.assertEqual([(cli.session_tag("s1"), str(self.transcript), len(WORK_LINE))], started)
        stale = time.time() - cli.WATCH_ALIVE_S - 5
        os.utime(cli.watch_mark(started[0][0], self.dir), (stale, stale))
        cli.on_hook(event, self.URL, time.time() + 2, self.sessions, self.post, spawn)
        self.assertEqual(2, len(started), "a watcher that stopped is replaced")
        cli.on_hook({**event, "hook_event_name": "Stop"}, self.URL, time.time() + 3, self.sessions, self.post, spawn)
        self.assertEqual(2, len(started), "the end of a turn starts nothing")

    def test_tools_used_at_the_same_moment_start_one_watcher_between_them(self):
        now = time.time()
        self.assertEqual([True, False, False], [cli.claim_watch(self.TAG, now, self.dir) for _ in range(3)])
        stale = now - cli.WATCH_ALIVE_S - 5
        os.utime(cli.watch_mark(self.TAG, self.dir), (stale, stale))
        self.assertTrue(cli.claim_watch(self.TAG, now, self.dir), "the mark of a watcher that was stopped is reused")

    def test_an_esc_pressed_while_working_is_still_being_posted_counts(self):
        spawned = {}

        def slow_post(url, word, tag):
            self.append(ESC_LINE)  # the person stops Claude while the hook is still sending "working"

        def spawn(tag, transcript, folder, offset):
            spawned.update(tag=tag, offset=offset)
        cli.save_json(self.sessions, {})
        cli.on_hook({"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": str(self.transcript)},
                    self.URL, time.time(), self.sessions, slow_post, spawn)
        clock = FakeClock()
        result = cli.watch_transcript(spawned["tag"], self.transcript, spawned["offset"], self.URL, self.sessions,
                                      now=clock.now, sleep=clock.sleep, post=self.post)
        self.assertEqual(("interrupted", [("done", spawned["tag"])]), (result, self.posted))

    def test_no_watcher_without_a_transcript_or_when_the_post_failed(self):
        started = []
        spawn = lambda tag, transcript, folder, offset: started.append(tag)  # noqa: E731
        cli.save_json(self.sessions, {})
        cli.on_hook({"hook_event_name": "PreToolUse", "session_id": "s1"}, self.URL, 1000, self.sessions,
                    self.post, spawn)

        def down(url, word, tag):
            raise OSError("down")
        with self.assertRaises(OSError):
            cli.on_hook({"hook_event_name": "PreToolUse", "session_id": "s2", "transcript_path": str(self.transcript)},
                        self.URL, 1000, self.sessions, down, spawn)
        self.assertEqual([], started)

    def test_the_watch_command_prints_nothing_and_exits_0_even_when_it_fails(self):
        with mock.patch.object(cli, "linked_url", lambda: self.URL), \
                mock.patch.object(cli, "watch_transcript", mock.Mock(side_effect=RuntimeError("boom"))), \
                mock.patch.object(cli, "HOOK_LOG", self.dir / "hook.log"):
            result = run_tool_aloud("agent", "watch", "--tag", self.TAG, "--transcript", str(self.transcript))
        self.assertEqual((0, "", ""), result)
        self.assertIn("watch RuntimeError('boom')", (self.dir / "hook.log").read_text(encoding="utf-8"))


class SafetyTest(unittest.TestCase):
    """What the review of 2026-10-15 asked for."""

    def test_a_hook_whose_script_is_gone_never_blocks_claude_code(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash here")
        command = cli.hook_command().replace(Path(cli.__file__).resolve().as_posix(), "/no/such/folder/nomoredoom.py")
        ran = subprocess.run([bash, "-c", command], input="{}", capture_output=True, text=True, timeout=60)
        self.assertEqual(0, ran.returncode, ran.stderr)  # exit 2 would make Claude Code refuse the tool call

    def test_only_agent_link_reaches_the_network(self):
        root = Path(__file__).resolve().parent.parent
        network = re.compile(r"java\.net|javax\.net|okhttp|\bSocket\b|URLConnection|WebView|DownloadManager")
        sources = list((root / "app/src/main").rglob("*.kt")) + list((root / "core/src/main").rglob("*.kt"))
        self.assertGreater(len(sources), 10)
        reaching = [p.name for p in sources if p.name != "AgentLink.kt" and network.search(p.read_text("utf-8"))]
        self.assertEqual([], reaching)

    def test_settings_are_written_through_a_link_with_their_text_as_it_was(self):
        with tempfile.TemporaryDirectory() as folder:
            real = Path(folder) / "real.json"
            real.write_text('{"note": "שלום"}', encoding="utf-8")
            link = Path(folder) / "settings.json"
            try:
                link.symlink_to(real)
            except OSError:
                link = real  # no links allowed here (Windows without developer mode): the text still counts
            cli.add_claude_hooks(link, 'python "C:/x/nomoredoom.py" agent hook; exit 0')
            self.assertTrue(link == real or link.is_symlink(), "the link is still a link")
            self.assertIn("שלום", real.read_text(encoding="utf-8"))
            self.assertIn("PreToolUse", real.read_text(encoding="utf-8"))

    def test_a_home_that_cannot_be_written_to_posts_nothing(self):
        posted = []
        with tempfile.TemporaryDirectory() as folder:
            sessions = Path(folder) / "agent-sessions.json"
            with mock.patch.object(cli, "save_json", mock.Mock(side_effect=OSError("read-only"))):
                for n in range(3):
                    with self.assertRaises(OSError):
                        cli.on_hook({"hook_event_name": "PreToolUse", "session_id": "s1"}, "https://ntfy.sh/x",
                                    1000 + n, sessions, lambda *a: posted.append(a), lambda *a: None)
        self.assertEqual([], posted)

    def test_the_hook_reads_claude_codes_utf8_on_any_code_page(self):
        seen = []
        event = json.dumps({"hook_event_name": "PreToolUse", "transcript_path": "C:/Users/Zoë/t.jsonl"},
                           ensure_ascii=False).encode("utf-8")
        stdin = io.TextIOWrapper(io.BytesIO(event), encoding="cp1252")
        with mock.patch.object(cli, "linked_url", lambda: "https://ntfy.sh/x"), \
                mock.patch.object(cli, "on_hook", lambda e, *a: seen.append(e)), mock.patch.object(sys, "stdin", stdin):
            cli.agent_hook()
        self.assertEqual("C:/Users/Zoë/t.jsonl", seen[0]["transcript_path"])


if __name__ == "__main__":
    unittest.main()
