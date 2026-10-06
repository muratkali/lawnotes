"""Security tests: note names, requests from other programs, hostile note content, fuzzed input.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import random
import tempfile
import time
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_lawnotes import TMP, Session, lawnotes  # noqa: E402  (same temporary HOME and notes folder)


def fake_clipboard():
    """A PATH entry with stand-in pbcopy/pbpaste, so tests never touch the real clipboard."""
    d = tempfile.mkdtemp(dir=TMP)
    for name, body in (("pbcopy", "cat > \"$(dirname \"$0\")/clip\""), ("pbpaste", "cat \"$(dirname \"$0\")/clip\" 2>/dev/null")):
        with open(os.path.join(d, name), "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(os.path.join(d, name), 0o755)
    return {"PATH": d + os.pathsep + os.environ["PATH"]}


class NoteNameTests(unittest.TestCase):
    def test_names_stay_inside_the_notes_folder(self):
        self.assertTrue(lawnotes.note_path("Tort/Duty").startswith(lawnotes.NOTES_DIR + os.sep))
        for bad in ("../../.ssh/config", "Tort/../../escape", "/../../etc/passwd", "a\x1b]52;c;x\x07"):
            with self.assertRaises(ValueError, msg=bad):
                lawnotes.note_path(bad)

    def test_requests_only_open_notes(self):
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        now = time.time()
        self.assertIsNone(lawnotes.requested_note({"note": "/etc/hosts", "time": now}))  # not a note
        self.assertIsNone(lawnotes.requested_note({"note": "Tort/Duty", "time": now - 600}))  # stale
        self.assertIsNone(lawnotes.requested_note({"note": 42, "time": now}))
        self.assertEqual(lawnotes.requested_note({"note": "Tort/Duty", "time": now}),
                         os.path.join(lawnotes.NOTES_DIR, "Tort", "Duty.md"))
        with self.assertRaises(ValueError):
            lawnotes.requested_note({"note": "../../outside", "time": now})

    def test_hostile_request_is_ignored_by_the_editor(self):
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        note = os.path.join(lawnotes.NOTES_DIR, "Safe.md")
        open(note, "w").write("# Safe\n")
        s = Session(note)
        for target in ("/etc/hosts", "../../outside"):
            with open(lawnotes.REQUEST_FILE, "w") as f:
                json.dump({"note": target, "time": time.time()}, f)
            s.keys(b"", 1.5)
        s.keys(b"\x05Z\x13", 0.6)  # typing still goes into Safe.md
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "# SafeZ\n")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(lawnotes.NOTES_DIR), "outside.md")))


class HostileContentTests(unittest.TestCase):
    def test_control_codes_in_a_note_never_reach_the_terminal(self):
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        note = os.path.join(lawnotes.NOTES_DIR, "Hostile.md")
        # OSC 52 would set the clipboard; CSI 2J clears the screen; OSC 0 retitles the window
        with open(note, "wb") as f:
            f.write(b"# Hostile\n\x1b]52;c;aGk=\x07 and \x1b[2J and \x1b]0;pwned\x07\n")
        s = Session(note)
        self.assertTrue(s.quit())
        self.assertNotIn(b"\x1b]52", s.out)
        self.assertNotIn(b"\x1b]0;pwned", s.out)
        self.assertNotIn(b"\x1b[2J and", s.out)

    def test_pasted_control_codes_are_dropped(self):
        self.assertEqual(lawnotes.clean_text("a\x1b]52;c;x\x07b\x1b[31mc"), "a]52;c;xb[31mc")


class FuzzTests(unittest.TestCase):
    def test_random_input_never_crashes(self):
        rng = random.Random(1234)
        pieces = [b"\x1b", b"\x1b[", b"\x1b[<", b"\x1b[<65;", b"\x1b[200~", b"\x1b[201~", b"\x1bO",
                  b"\x1b[1;2", b"\x1b[27;2;13~", b"\r", b"\x7f", b"\t", "é§⚖中".encode(), b"\x00", b"\xff\xfe"]
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        note = os.path.join(lawnotes.NOTES_DIR, "Fuzz.md")
        open(note, "w").write("# Fuzz\n\nDonoghue v Stevenson [1932] AC 562; s 2(1); D and V.\n")
        s = Session(note, env=fake_clipboard())
        for _ in range(400):
            if rng.random() < 0.4:
                data = rng.choice(pieces)
            else:  # random bytes, but never ^Q (quit) so the session keeps going
                data = bytes(b for b in rng.randbytes(rng.randint(1, 8)) if b != 0x11)
            s.keys(data, 0.01)
        s.keys(b"", 1)
        for _ in range(4):  # back out of any prompt or list the fuzz opened, then quit
            s.keys(b"\x1b", 0.4)
        closed = s.quit() or s.quit()
        self.assertTrue(closed, "the editor didn't quit after fuzzing")
        log = open(lawnotes.LOG_FILE).read() if os.path.exists(lawnotes.LOG_FILE) else ""
        self.assertNotIn("CRASH", log)
        self.assertNotIn(b"Traceback", s.out)


if __name__ == "__main__":
    unittest.main()
