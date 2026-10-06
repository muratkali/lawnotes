"""Editor tests: the buffer, saving, highlighting, and a real session in a pseudo-terminal.

Run: python3 -m unittest discover -s tests -v
Uses only temporary folders; never touches your notes, history or install.
"""
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="lawnotes-tests-")
os.environ.update(LAWNOTES_DIR=os.path.join(TMP, "notes"), LAWNOTES_HISTORY=os.path.join(TMP, "history.git"),
                  LAWNOTES_SPELL="0", LAWNOTES_NO_UPDATE="1", HOME=os.path.join(TMP, "home"))
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, ROOT)
import lawnotes  # noqa: E402  (after the environment above)


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)
        self.path = os.path.join(self.dir, "Tort", "Duty.md")

    def test_list_continues_and_ends(self):
        b = lawnotes.Buffer(self.path)
        b.insert("- first")
        b.newline()
        b.insert("second")
        b.newline()
        b.newline()  # Enter on an empty bullet ends the list
        self.assertEqual(b.lines, ["- first", "- second", ""])

    def test_numbered_list_and_shift_enter(self):
        b = lawnotes.Buffer(self.path)
        b.insert("1. offer")
        b.newline(plain=True)
        b.insert("unilateral")
        b.newline()
        self.assertEqual(b.lines, ["1. offer", "   unilateral", "2. "])

    def test_undo_steps(self):
        b = lawnotes.Buffer(self.path)
        b.insert("alpha")
        b.newline()
        b.insert("beta")
        b.cy, b.cx = 0, 5  # move away, then type: a separate undo step
        b.insert("!")
        b.undo()
        self.assertEqual(b.lines, ["alpha", "beta"])

    def test_save_round_trip_and_undo(self):
        b = lawnotes.Buffer(self.path)
        b.insert("Donoghue v Stevenson")
        b.save()
        self.assertEqual(open(self.path).read(), "Donoghue v Stevenson\n")
        b.insert(" [1932] AC 562")
        self.assertTrue(b.undo())
        self.assertEqual(b.lines, ["Donoghue v Stevenson"])

    def test_conflict_copy_never_overwrites(self):
        b = lawnotes.Buffer(self.path)
        b.insert("mine")
        b.save()
        time.sleep(0.02)
        with open(self.path, "w") as f:
            f.write("from another Mac\n")
        b.insert(" more")
        note = b.save()
        self.assertIn("changed elsewhere", note)
        self.assertEqual(open(self.path).read(), "from another Mac\n")
        self.assertIn("conflict", b.path)
        self.assertEqual(open(b.path).read(), "mine more\n")

    def test_failed_save_raises_and_keeps_text(self):
        b = lawnotes.Buffer(os.path.join(self.dir, "ro", "n.md"))
        os.makedirs(os.path.dirname(b.path))
        os.chmod(os.path.dirname(b.path), 0o500)
        try:
            b.insert("keep me")
            with self.assertRaises(OSError):
                b.save()
            self.assertEqual(b.lines, ["keep me"])
            self.assertTrue(b.dirty)
        finally:
            os.chmod(os.path.dirname(b.path), 0o700)

    def test_encodings_and_control_characters(self):
        p = os.path.join(self.dir, "w.md")
        with open(p, "wb") as f:
            f.write("Lord Atkin’s “neighbour”".encode("cp1252") + b"\x00x")
        b = lawnotes.Buffer(p)
        self.assertEqual(b.lines, ["Lord Atkin’s “neighbour”�x"])
        self.assertIn("Windows-1252", b.note)

    def test_symlinked_note_stays_a_link(self):
        real = os.path.join(self.dir, "real.md")
        open(real, "w").write("a\n")
        link = os.path.join(self.dir, "link.md")
        os.symlink(real, link)
        b = lawnotes.Buffer(link)
        b.insert("b")
        b.save()
        self.assertTrue(os.path.islink(link))
        self.assertEqual(open(real).read(), "ba\n")


class TextTests(unittest.TestCase):
    def test_paste_keeps_special_spaces(self):
        self.assertEqual(lawnotes.clean_text("v Stevenson [1932] AC\r\n562\x07"), "v Stevenson [1932] AC\n562")

    def test_case_and_citation_patterns(self):
        cases = lambda s: [m.group() for m in lawnotes.CASE_RE.finditer(s)]
        self.assertEqual(cases("Lord Atkin in Donoghue v Stevenson"), ["Donoghue v Stevenson"])
        self.assertEqual(cases("Caparo Industries plc v Dickman"), ["Caparo Industries plc v Dickman"])
        self.assertEqual(cases("R v Brown and Re Polemis"), ["R v Brown", "Re Polemis"])
        self.assertEqual([m.group() for m in lawnotes.CITE_RE.finditer("[2019] EWCA Civ 1234")], ["[2019] EWCA Civ 1234"])
        self.assertEqual([m.group() for m in lawnotes.STATUTE_RE.finditer("s 2(1) of the Human Rights Act 1998")],
                         ["s 2(1)", "Human Rights Act 1998"])

    def test_italic_typo_does_not_crash(self):
        """Scrolling tort.md crashed: pair_number() overflowed on an italic misspelt word."""
        import curses
        self.assertEqual(lawnotes.pair_of(curses.A_ITALIC | curses.A_BOLD | (3 << 8)), 3)

        class Speller:
            def lookup(self, line):
                return ((0, 3),)  # "teh"
        app = object.__new__(lawnotes.App)
        app.speller, app.ignored, app.protected = Speller(), set(), {9}
        app.highlight = lambda line: [curses.A_ITALIC | (1 << 8)] * len(line)
        self.assertEqual(app.misspelt("teh case"), [(0, 3)])

    def test_party_labels(self):
        found = lambda t: [m.group() for m in lawnotes.PARTY_RE.finditer(t)]
        self.assertEqual(found("D punched V. C sued D1 and D2."), ["D", "V", "C", "D1", "D2"])
        self.assertEqual(found("D's intention"), ["D's"])
        self.assertEqual(found("Part V of the Act; a C-section"), [])

    def test_wrap(self):
        self.assertEqual(lawnotes.wrap_starts("hello world foo", 8), (0, 6, 12))
        self.assertEqual(lawnotes.wrap_starts("x" * 20, 8), (0, 8, 16))

    def test_resolve_note(self):
        self.assertEqual(lawnotes.resolve_note("Tort/Duty"), os.path.join(lawnotes.NOTES_DIR, "Tort", "Duty.md"))

    def test_self_test_passes(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes.py"), "--self-test"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(lawnotes.find_tool("pandoc"), "export needs pandoc (brew install pandoc)")
class ExportTests(unittest.TestCase):
    NOTE = [
        "# Negligence",
        "",
        "Duty: Donoghue v Stevenson [1932] AC 562 applies.[^1]",
        "",
        "-- the claimant's burden",
        "",
        "    - the burden of proof, on balance",
        "    - ",
        "    indented text that is not code",
        "",
        "[^1]: Lord Atkin's neighbour principle.",
    ]

    def setUp(self):
        self.out = tempfile.mkdtemp(dir=TMP)
        lawnotes.EXPORT_DIR = self.out

    def test_word(self):
        import zipfile
        path = lawnotes.export_note(os.path.join(TMP, "Negligence.md"), self.NOTE, "docx")
        z = zipfile.ZipFile(path)
        doc, notes = z.read("word/document.xml").decode(), z.read("word/footnotes.xml").decode()
        self.assertIn("neighbour principle", notes)                      # a real footnote
        self.assertRegex(doc, r"<w:i ?/>.*?Donoghue v Stevenson")          # case name in italics (OSCOLA)
        self.assertIn("the burden of proof", doc)                         # nothing eaten by a "table"
        self.assertNotIn("<w:tbl>", doc)
        self.assertNotIn("SourceCode", doc)                               # indentation isn't code
        self.assertIn("Times New Roman", z.read("word/styles.xml").decode())

    def test_pdf(self):
        path = lawnotes.export_note(os.path.join(TMP, "Negligence.md"), self.NOTE, "pdf")
        data = open(path, "rb").read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 2000)

    def test_markdown_preparation(self):
        md = lawnotes.export_markdown(self.NOTE).split("\n")
        self.assertIn("Duty: *Donoghue v Stevenson* [1932] AC 562 applies.[^1]", md)
        self.assertIn("- the burden of proof, on balance", md)          # outside a list: not code
        self.assertNotIn("    - ", md)                                     # empty bullet dropped
        self.assertEqual(lawnotes.export_markdown(["*Re Polemis* applies"]).strip(), "*Re Polemis* applies")


class Session:
    """The editor running in a pseudo-terminal, driven by keystrokes."""

    def __init__(self, *args, env=None):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ.update(TERM="xterm-256color", **(env or {}))
            os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "lawnotes.py"), *args])
        import fcntl
        import termios
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.out = self.read(2)

    def read(self, seconds=0.4):
        out, end = b"", time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.02)[0]:
                try:
                    out += os.read(self.fd, 65536)
                except OSError:
                    break
        return out

    def keys(self, data, seconds=0.4):
        try:
            os.write(self.fd, data)
        except OSError:
            pass
        out = self.read(seconds)
        self.out += out
        return re.sub(rb"\x1b\[[0-9;?<>]*[A-Za-z~]|\x1b\(B|\x1b[=>]", b" ", out).decode(errors="replace")

    def quit(self):
        self.keys(b"\x11", 1.5)
        for _ in range(30):
            if os.waitpid(self.pid, os.WNOHANG)[0] == self.pid:
                return True
            time.sleep(0.1)
        os.kill(self.pid, 9)
        os.waitpid(self.pid, 0)
        return False


class SessionTests(unittest.TestCase):
    def test_double_and_triple_click(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Click.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("Duty owed by D\nDonoghue v Stevenson [1932] AC 562 applies\nthird line\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note)
        s.keys(click(2, 1) + click(2, 1) + b"Breach", 0.5)            # double-click a word
        s.keys(click(6, 2) + click(6, 2) + b"Caparo", 0.5)            # double-click inside a case name
        s.keys(click(3, 3) + click(3, 3) + click(3, 3) + b"X\x13", 0.6)  # triple-click a line
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["Breach owed by D", "Caparo [1932] AC 562 applies", "X"])

    def test_star_wraps_selection(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Wrap.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("the ratio decidendi of the case\nD and V\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note)
        s.keys(click(6, 1) + click(6, 1) + b"**", 0.5)                 # double-click "ratio", * twice
        s.keys(b"\x1bOB\x01" + b"\x1b[1;2C" * 7 + b"_\x13", 0.6)    # Shift+Right over "D and V", _
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["the **ratio** decidendi of the case", "_D and V_"])

    def test_wheel_moves_view_not_cursor_and_copy(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Scroll2.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(80)))
        fake = tempfile.mkdtemp(dir=TMP)
        clip = os.path.join(fake, "clipboard")
        with open(os.path.join(fake, "pbcopy"), "w") as f:
            f.write(f"#!/bin/sh\ncat > '{clip}'\n")
        os.chmod(os.path.join(fake, "pbcopy"), 0o755)
        s = Session(note, env={"PATH": fake + os.pathsep + os.environ["PATH"]})
        screen = s.keys(b"\x1b[<65;10;10M" * 6, 0.6)                 # wheel down 18 rows
        self.assertIn("44", screen)  # the view moved: rows beyond the first screen were drawn
        out = s.keys(b"\x03", 0.3)                                        # copy with nothing selected
        self.assertEqual(open(clip).read(), "line 0\n")                 # the cursor's line, not the view's
        self.assertIn("Copied 2 words", out)                              # the status bar says so
        flashed = s.out[-4000:]
        self.assertIn(b"48;5;120", flashed)                               # and the line flashed green
        s.keys(b"Q\x13", 0.5)                                             # typing goes where the cursor was
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines()[0], "Qline 0")    # the flash didn't select it

    def test_panes_split_switch_and_close(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Panes")
        os.makedirs(folder, exist_ok=True)
        a = os.path.join(folder, "A.md")
        open(a, "w").write("# A\n")
        s = Session(a)
        s.keys(b"\x1bd", 0.8)                          # Option+D: split, the note list opens
        s.keys(b"Panes/B\r", 0.8)                      # a new note in the right pane
        s.keys(b"right", 0.4)
        s.keys(b"\x1bo", 0.4)                          # Option+O: back to the left pane
        s.keys(b"left", 0.4)
        s.keys(b"\x1b[<0;9;1M\x1b[<0;9;1m", 0.4)       # click in the left pane, column 8
        s.keys(b"Z", 0.4)
        s.keys(b"\x1bw", 0.5)                          # Option+W closes the left pane
        s.keys(b"\x1bw", 0.5)                          # only one left: nothing happens
        self.assertTrue(s.quit())
        self.assertEqual(open(a).read(), "left# AZ\n")
        self.assertEqual(open(os.path.join(folder, "B.md")).read(), "# B\n\nright\n")

    def test_same_note_in_two_panes_stays_in_sync(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Sync")
        os.makedirs(folder, exist_ok=True)
        c = os.path.join(folder, "C.md")
        open(c, "w").write("# C\n")
        s = Session(c)
        s.keys(b"\x1bd", 0.8)
        s.keys(b"\x1b", 0.5)                           # Esc: keep the same note in the new pane
        s.keys(b"X", 0.3)
        s.keys(b"\x1bo", 0.3)
        s.keys(b"\x05Y", 0.3)                          # left pane: end of line, type
        self.assertTrue(s.quit())
        self.assertEqual(open(c).read(), "X# CY\n")    # both edits, one text
        self.assertEqual(os.listdir(folder), ["C.md"])  # no conflict copies

    def test_split_scroll_events_neither_close_nor_type(self):
        """A scroll event whose ESC arrives 120 ms before the rest (seen through herdr)."""
        wheel = b"\x1b[<65;50;10M"
        s = Session()  # the note list
        for _ in range(3):
            os.write(s.fd, wheel[:1]); time.sleep(0.12); os.write(s.fd, wheel[1:]); s.read(0.2)
        self.assertEqual(os.waitpid(s.pid, os.WNOHANG)[0], 0, "the note list closed")
        s.keys(b"\x11", 1)
        os.waitpid(s.pid, 0)
        note = os.path.join(lawnotes.NOTES_DIR, "Scroll.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(60)))
        s = Session(note)
        for _ in range(3):
            os.write(s.fd, wheel[:1]); time.sleep(0.12); os.write(s.fd, wheel[1:]); s.read(0.2)
        s.keys(b"\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertNotIn("<65", open(note).read())

    def test_exit_reason_logged_locally_and_in_icloud(self):
        icloud = os.path.join(os.environ["HOME"], "Library", "Mobile Documents", "com~apple~CloudDocs")
        os.makedirs(icloud, exist_ok=True)
        s = Session("Logged")
        self.assertTrue(s.quit())
        local = open(lawnotes.LOG_FILE).read()
        self.assertIn("exited: quit with ^Q", local)
        logs = os.listdir(os.path.join(icloud, "Law Notes Logs"))
        self.assertEqual(len(logs), 1)
        self.assertIn("exited: quit with ^Q", open(os.path.join(icloud, "Law Notes Logs", logs[0])).read())

    def test_type_save_quit(self):
        s = Session("Contract/Offer")
        path = os.path.join(lawnotes.NOTES_DIR, "Contract", "Offer.md")
        s.keys(b"\x1bOB\x1bOB- Offer: Carlill v Carbolic Smoke Ball Co", 0.5)
        s.keys(b"\x1b[27;2;13~unilateral\r\r", 0.5)
        running = json.load(open(lawnotes.RUNNING_FILE))
        self.assertEqual(running["pid"], s.pid)
        self.assertTrue(s.quit())
        self.assertEqual(open(path).read(), "# Offer\n\n- Offer: Carlill v Carbolic Smoke Ball Co\n  unilateral\n\n")
        self.assertFalse(os.path.exists(lawnotes.RUNNING_FILE))
        self.assertNotIn(b"Traceback", s.out)

    def test_open_request_and_outside_change(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Req")
        a, b = os.path.join(folder, "A.md"), os.path.join(folder, "B.md")
        os.makedirs(folder, exist_ok=True)
        open(a, "w").write("# A\n")
        open(b, "w").write("# B\n")
        s = Session(a)
        with open(lawnotes.REQUEST_FILE, "w") as f:
            json.dump({"note": "Req/B", "time": time.time()}, f)
        s.keys(b"", 1.5)  # the launcher's request opens B
        time.sleep(0.05)
        open(b, "w").write("# B\n\nfrom another Mac\n")
        screen = s.keys(b"", 1.5)
        self.assertIn("reloaded", screen)
        s.keys(b"\x1bOB\x1bOB\x05!\x13", 0.6)
        self.assertTrue(s.quit())
        self.assertEqual(open(b).read(), "# B\n\nfrom another Mac!\n")
        self.assertEqual(sorted(os.listdir(folder)), ["A.md", "B.md"])  # no conflict copy

    def test_history_snapshots_notes_only(self):
        d = os.path.join(lawnotes.NOTES_DIR, "Hist")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "Note.md"), "w").write("# Note\n")
        open(os.path.join(d, "lecture.pdf"), "wb").write(b"%PDF" + b"0" * 1000)
        s = Session(os.path.join(d, "Note.md"))
        self.assertTrue(s.quit())
        files = subprocess.run(["git", f"--git-dir={lawnotes.HISTORY_DIR}", "ls-tree", "-r", "--name-only", "HEAD"],
                               capture_output=True, text=True).stdout.split()
        self.assertIn("Hist/Note.md", files)
        self.assertNotIn("Hist/lecture.pdf", files)


FAKE_HERDR = r"""#!/bin/bash
# Stands in for herdr: answers like the real one and logs every call.
echo "$*" >> "$HERDR_LOG"
case "$1 $2" in
  "pane get") echo '{"result":{"pane":{"pane_id":"w1:p1","tab_id":"w1:t1","workspace_id":"w1"}}}' ;;
  "tab create") echo '{"result":{"root_pane":{"pane_id":"w1:p2"},"tab":{"tab_id":"w1:t2"}}}' ;;
  "pane split") echo '{"result":{"pane":{"pane_id":"w1:p3"}}}' ;;
  *) ;;  # pane run, tab focus: success, no output
esac
"""


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(self.bin, "herdr"), "w") as f:
            f.write(FAKE_HERDR)
        os.chmod(os.path.join(self.bin, "herdr"), 0o755)
        self.log = os.path.join(self.bin, "calls.log")
        self.env = {**os.environ, "PATH": self.bin + os.pathsep + os.environ["PATH"],
                    "HERDR_PANE_ID": "w1:p1", "HERDR_LOG": self.log}

    def launch(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes"), *args], env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_tab_opens_once_and_editor_not_started_here(self):
        r = self.launch("Tort/Duty")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = open(self.log).read()
        self.assertIn("tab create --workspace w1 --label Notes", calls)
        self.assertIn("pane run w1:p2", calls)
        self.assertIn("Tort/Duty", calls)
        self.assertNotIn("Traceback", r.stderr)  # the editor would crash here without a terminal

    def test_split(self):
        r = self.launch("--split")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pane run w1:p3", open(self.log).read())

    def test_open_copy_is_brought_forward(self):
        os.makedirs(os.path.dirname(lawnotes.RUNNING_FILE), exist_ok=True)
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "lawnotes.py"])
        try:
            with open(lawnotes.RUNNING_FILE, "w") as f:
                json.dump({"pid": sleeper.pid, "pane": "w1:p9"}, f)
            r = self.launch("Contract/Offer")
            self.assertIn("already open", r.stdout)
            calls = open(self.log).read()
            self.assertIn("tab focus w1:t1", calls)
            self.assertNotIn("tab create", calls)
            self.assertEqual(json.load(open(lawnotes.REQUEST_FILE))["note"], "Contract/Offer")
        finally:
            sleeper.kill()
            for f in (lawnotes.RUNNING_FILE, lawnotes.REQUEST_FILE):
                if os.path.exists(f):
                    os.remove(f)


if __name__ == "__main__":
    unittest.main()
