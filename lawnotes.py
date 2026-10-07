#!/usr/bin/env python3
"""
lawnotes — a small terminal editor for law notes.

    lawnotes                 open Law Notes (inside herdr: in a "Notes" tab)
    lawnotes Tort/Duty       open or create ~/UCL/notes/Tort/Duty.md
    lawnotes --help          all launcher options (update, rollback, uninstall…)

Installed copies update themselves once a day to the newest signed release that
passes its self-test (set LAWNOTES_NO_UPDATE=1 to stop that). See update.py.

Notes are plain Markdown kept in ~/UCL/notes (set LAWNOTES_DIR to change it), which is a
link to iCloud Drive/UCL Notes. Version history is kept on this Mac only, in
~/Library/Application Support/lawnotes/history.git.
Changes autosave a few seconds after you stop typing, and on quit.

Use Folder/Name when creating a note (e.g. Contract/Offer) to file it in a folder.
Misspelt words are highlighted using the macOS spell checker (British English;
set LAWNOTES_LANG=en_US to change, LAWNOTES_SPELL=0 to turn it off).

Keys (press ^G inside the editor for this list)
  ^G help        ^S save          ^O open note     ^N new note     ^Q save & quit
  ^F find (Enter again or F3 for the next match; Esc clears the highlight)
  ^T insert template (case brief, IRAC, statute, essay plan, lecture)
  ^L outline — jump to a heading or a case mentioned in this note
  ^W spelling — suggestions for the highlighted word, or add it to your dictionary
  Shift+Enter   new line without a new bullet (Option+Enter also works)
  ^P search all notes   ^B case index (every case across all notes)
  ^R version history — restore an earlier version of this note
  Shift+arrows select   ^C copy   ^X cut   ^V paste (Mac clipboard); click to place the cursor
  ^K cut line    ^U paste lines   ^Z undo          ^Y redo
  ^A / ^E line start / end        Option/Ctrl+arrows move by word
  Tab / Shift-Tab indent or outdent a bullet
"""
import bisect
import contextlib
import curses
import datetime
import difflib
import hashlib
import json
import locale
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import traceback
import unicodedata
from functools import lru_cache

VERSION = "1.4.0"
HELP_TITLE = "Help — Law Notes (beta)"
APP_DIR = os.path.dirname(os.path.realpath(__file__))
NOTES_DIR = os.path.abspath(os.path.expanduser(os.environ.get("LAWNOTES_DIR", "~/UCL/notes")))
NOTE_EXTS = (".md", ".txt")
MAX_TEXT_WIDTH = 90
LEFT_PAD = 1  # columns of space before the text in each pane
AUTOSAVE_AFTER = 3  # seconds idle after a change
AUTOSAVE_MAX = 15   # ...or this long after the first unsaved change, even while typing
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
CACHE_DIR = os.path.expanduser("~/.cache/lawnotes")
SPELL_LANG = os.environ.get("LAWNOTES_LANG", "en_GB")
SPELL_ON = os.environ.get("LAWNOTES_SPELL", "1") != "0"
MOUSE_ON = os.environ.get("LAWNOTES_MOUSE", "1") != "0"
# Snapshots live in a private git store on this Mac, outside the notes folder, because the notes
# folder may be synced (iCloud Drive) and cloud sync corrupts git stores.
HISTORY_DIR = os.path.expanduser(os.environ.get("LAWNOTES_HISTORY", "~/Library/Application Support/lawnotes/history.git"))
ICLOUD_DIR = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
HISTORY_BUNDLES = os.path.join(ICLOUD_DIR, "Law Notes History")  # weekly copy of each Mac's history
BUNDLE_EVERY = 7 * 24 * 3600
RUNNING_FILE = os.path.join(CACHE_DIR, "running.json")      # single instance: who has Law Notes open
REQUEST_FILE = os.path.join(CACHE_DIR, "open-request.json")  # the launcher asks the open editor to open a note
MESSAGE_FILE = os.path.join(CACHE_DIR, "update-message")     # written by update.py
POSITIONS_FILE = os.path.join(CACHE_DIR, "positions.json")   # where each note was left
LOG_FILE = os.path.join(CACHE_DIR, "lawnotes.log")           # starts, exits and why, crashes
ICLOUD_LOGS = os.path.join(ICLOUD_DIR, "Law Notes Logs")     # the same log, one file per Mac
ESCAPE_WAIT = 250  # ms to wait for the rest of an escape sequence: through herdr or a busy Mac,
                   # a scroll event's ESC can arrive well before the rest (it closed the note list)
SNAPSHOT_EVERY = 600  # seconds between automatic snapshots while you work
# Only notes go into history: no PDFs or other files kept in the notes folder.
HISTORY_EXCLUDE = "*\n!*/\n!*.md\n!*.txt\n.*\n*~\n"

# Legal Latin and terms of art the system dictionary doesn't know.
LAW_WORDS = set("""
mens rea actus reus ratio decidendi obiter dicta dictum stare decisis per incuriam res ipsa loquitur
volenti non fit injuria novus interveniens ex turpi causa quantum meruit valebant certiorari mandamus
ultra vires intra prima facie de minimis bona fide fides mala inter alia sub judice habeas corpus amicus
curiae caveat emptor consensus ad idem damnum sine injuria nemo judex sua audi alteram partem nolle
prosequi ratione materiae personae lex loci forum conveniens judicata autrefois acquit convict sui
generis vivos mortis donatio cestui que nec vi clam precario prendre contra proferentem eiusdem
ejusdem noscitur sociis expressio unius exclusio alterius pari materia mutatis mutandis partes erga
omnes jus cogens locus standi personam rem fortiori curiam se silentio functus officio facto jure
initio estoppel tortfeasor tortfeasors tortious tortiously misfeasance nonfeasance malfeasance
justiciable justiciability defeasible indefeasible bailee bailor bailment mortgagor mortgagee lessor
lessee assignor assignee obligor obligee promisor promisee offeror offeree testator testatrix
intestacy settlor laches subrogation novation rescission rescissory restitutionary unconscionability
voidable vitiate vitiated vitiating vitiates proportionality legitimacy sovereignty parliamentary
""".split())

TEMPLATES = [
    ("Case brief", [
        "## ",
        "- **Citation:** ",
        "- **Court:** ",
        "- **Facts:** ",
        "- **Issue:** ",
        "- **Held:** ",
        "- **Ratio:** ",
        "- **Obiter:** ",
        "- **Significance:** ",
        "",
    ]),
    ("Problem question (IRAC)", [
        "## Problem: ",
        "",
        "### Issue",
        "- ",
        "",
        "### Rule",
        "- ",
        "",
        "### Application",
        "- ",
        "",
        "### Conclusion",
        "- ",
        "",
    ]),
    ("Statutory provision", [
        "## ",
        "> ",
        "- **Purpose:** ",
        "- **Key terms:** ",
        "- **Key cases:** ",
        "- **Critique:** ",
        "",
    ]),
    ("Essay plan", [
        "## Essay: ",
        "- **Thesis:** ",
        "- **Point 1:** ",
        "- **Point 2:** ",
        "- **Point 3:** ",
        "- **Counter-argument:** ",
        "- **Conclusion:** ",
        "",
    ]),
    ("Lecture notes", [
        "# Lecture: ",
        "*{date}* · *Module:* ",
        "",
        "## Key points",
        "- ",
        "",
        "## Cases",
        "- ",
        "",
        "## Questions to follow up",
        "- [ ] ",
        "",
    ]),
]

# --- Legal highlighting ------------------------------------------------------

# Capitalised words that start sentences rather than case names.
_STOP = r"(?!(?:In|The|See|As|Per|Following|Applying|Cf|But|And|Also|This|That|Under|After|Before|From|Since|Note|Compare|Contrast|Unlike|Like|Whereas|However|Thus|Here|Then)\b)"
_WORD = r"[A-Z][\w'’\-]*"
_NAME = rf"{_WORD}(?:\s+(?:(?:of|the|for|&)\s+)*{_WORD})*(?:\s+(?:plc|Ltd|LLP|Co|Inc)\.?)?"
CASE_RE = re.compile(rf"\b{_STOP}(?:{_NAME}\s+v\.?\s+{_NAME}|Re\s+{_NAME})")
CITE_RE = re.compile(
    r"[\[(]\d{4}[\])](?:\s+\d+)?\s+[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)?\s+\d+(?:\s+\([A-Za-z]+\))?"
)
STATUTE_RE = re.compile(
    r"\b(?:ss?|arts?|regs?|sch|para|[Ss]ection|[Aa]rticle)\.?\s?\d+[A-Z]*(?:\(\w+\))*"
    rf"|\b{_STOP}(?:[A-Z][a-z]+\s+)+(?:\([A-Za-z ]+\)\s+)?Act\s+\d{{4}}"
)
# Party labels in problem answers: D (defendant), V (victim), C (claimant), also D1, D's.
# Not after Part/Schedule/Chapter, where V is a Roman numeral.
PARTY_RE = re.compile(r"(?<!Part )(?<!Schedule )(?<!Chapter )(?<!Book )\b([DVC])\d?(?:['\u2019]s)?\b(?!-)")
HEADING_RE = re.compile(r"^(#{1,6})\s")
BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+[.)]|>)(\s+)(\[[ xX]\]\s+)?")
BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
ITALIC_RE = re.compile(r"(?<![*\w])[*_][^*_\s][^*_]*[*_](?![*\w])")


def pair_of(attr):
    """The colour pair inside a curses attribute. curses.pair_number() overflows on attributes
    that include A_ITALIC (bit 31) with macOS's ncurses: it crashed drawing italic typos."""
    return (attr & curses.A_COLOR) >> 8


def next_marker(marker):
    if marker[:-1].isdigit():
        return str(int(marker[:-1]) + 1) + marker[-1]
    return marker


@lru_cache(maxsize=8192)
def wrap_starts(s, w):
    """Offsets where each soft-wrapped row of `s` begins (word wrap at width w)."""
    starts = [0]
    i = 0
    while len(s) - i > w:
        j = s.rfind(" ", i, i + w)
        i = j + 1 if j > i else i + w
        starts.append(i)
    return tuple(starts)


# --- Text buffer -------------------------------------------------------------

class Buffer:
    def __init__(self, path):
        self.path = path
        self.lines = [""]
        self.cy = self.cx = 0
        self.dirty = False
        self.changed_at = 0.0
        self.dirty_since = None   # when the first unsaved change was made
        self.disk_mtime = None    # file's mtime when we last read or wrote it
        self.note = None          # message to show after opening (e.g. encoding)
        self.undo_stack, self.redo_stack = [], []
        self._last_kind, self._last_time = None, 0.0
        self._after = None  # cursor position after the last edit: moving away starts a new undo step
        self.version = 0    # bumped on every change, so derived values (word count) can be cached
        self._words = (None, 0)
        if os.path.exists(path):
            self.load()

    def load(self):
        with open(self.path, "rb") as f:
            data = f.read()
        self.disk_mtime = os.stat(self.path).st_mtime_ns
        for enc, label in (("utf-8-sig", None), ("cp1252", "Windows-1252"), ("latin-1", "Latin-1")):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if label:
            self.note = f"Opened as {label} text — it will be saved as UTF-8"
        text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        text = CONTROL_RE.sub("\ufffd", text)  # stray control bytes can't be drawn
        self.lines = text.split("\n")
        self.version = getattr(self, "version", 0) + 1
        if len(self.lines) > 1 and self.lines[-1] == "":
            self.lines.pop()

    def save(self):
        """Write the note so it is never half-written and never overwrites a newer copy
        saved elsewhere (another pane or app); in that case ours goes to a conflict copy.
        Returns a message when that happened. Raises OSError if the write fails."""
        note = None
        target = os.path.realpath(self.path)  # write through symlinks
        try:
            st = os.stat(target)
        except FileNotFoundError:
            st = None
        if st and st.st_mtime_ns != self.disk_mtime:
            base, ext = os.path.splitext(self.path)
            self.path = target = f"{base} (conflict {datetime.datetime.now():%d %b %H.%M.%S}){ext}"
            note = f"This note was changed elsewhere — your version is saved as “{os.path.basename(target)}”"
            st = None
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, f".{os.path.basename(target)}.saving~")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines) + "\n")
                f.flush()
                os.fsync(f.fileno())  # on disk before it replaces the old version
            if st:
                os.chmod(tmp, st.st_mode & 0o7777)
            os.replace(tmp, target)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.disk_mtime = os.stat(target).st_mtime_ns
        self.dirty, self.dirty_since = False, None
        self._last_kind = None  # typing after a save is a new undo step
        return note

    # Undo: snapshot the whole buffer before each edit, merging runs of typing.
    def checkpoint(self, kind, boundary=False):
        now = time.time()
        merge = (kind in ("type", "delete") and kind == self._last_kind and now - self._last_time < 2
                 and not boundary and (self.cy, self.cx) == self._after)
        if not merge:
            self.undo_stack.append((self.lines[:], self.cy, self.cx))
            del self.undo_stack[:-300]
            self.redo_stack.clear()
        self._last_kind, self._last_time = kind, now
        if not self.dirty:
            self.dirty_since = now
        self.dirty, self.changed_at = True, now
        self.version += 1

    def _swap(self, src, dst):
        if not src:
            return False
        dst.append((self.lines[:], self.cy, self.cx))
        self.lines, self.cy, self.cx = src.pop()
        self.version += 1
        self._last_kind = None
        if not self.dirty:
            self.dirty_since = time.time()
        self.dirty, self.changed_at = True, time.time()
        return True

    def word_count(self):
        """Words in the note, recounted only after a change (it ran on every frame)."""
        key = (self.version, len(self.lines))
        if self._words[0] != key:
            self._words = (key, sum(len(l.split()) for l in self.lines))
        return self._words[1]

    def undo(self):
        return self._swap(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._swap(self.redo_stack, self.undo_stack)

    # Edits
    def insert(self, text, kind="type"):
        self.checkpoint(kind, boundary=(text == " "))
        parts = text.split("\n")
        line = self.lines[self.cy]
        before, after = line[:self.cx], line[self.cx:]
        if len(parts) == 1:
            self.lines[self.cy] = before + text + after
            self.cx += len(text)
        else:
            self.lines[self.cy:self.cy + 1] = [before + parts[0], *parts[1:-1], parts[-1] + after]
            self.cy += len(parts) - 1
            self.cx = len(parts[-1])
        self._after = (self.cy, self.cx)

    def newline(self, plain=False):
        """Split the line, continuing bullets/numbering; Enter on an empty bullet ends the list.
        plain (Shift+Enter): no new bullet, just indent to line up under the bullet's text."""
        self.checkpoint("enter")
        line = self.lines[self.cy]
        m = BULLET_RE.match(line)
        if plain and m and self.cx >= m.end() and m.group(2) != ">":
            prefix = " " * m.end()
        elif plain:
            prefix = re.match(r"\s*", line).group()[:self.cx]
        elif not m and (parent := self.parent_bullet()) and self.cx >= parent.end():
            # Enter on a Shift+Enter continuation line starts the next bullet
            prefix = parent.group(1) + next_marker(parent.group(2)) + " " + ("[ ] " if parent.group(4) else "")
        elif m and self.cx >= m.end():
            if not line[m.end():].strip():
                if m.group(1):  # an empty nested bullet moves out a level
                    outdented = m.group(1)[:-2] + m.group(2) + " " + (m.group(4) or "")
                    self.lines[self.cy] = outdented
                    self.cx = len(outdented)
                else:           # an empty top-level bullet ends the list
                    self.lines[self.cy] = ""
                    self.cx = 0
                return
            prefix = m.group(1) + next_marker(m.group(2)) + " " + ("[ ] " if m.group(4) else "")
        else:
            prefix = re.match(r"\s*", line).group()[:self.cx]
        self.lines[self.cy] = line[:self.cx]
        self.lines.insert(self.cy + 1, prefix + line[self.cx:])
        self.cy += 1
        self.cx = len(prefix)

    def parent_bullet(self):
        """If the current line is an indented continuation of a bullet, that bullet's match."""
        indent = len(self.lines[self.cy]) - len(self.lines[self.cy].lstrip(" "))
        if not indent or not self.lines[self.cy].strip():
            return None
        for i in range(self.cy - 1, -1, -1):
            line = self.lines[i]
            m = BULLET_RE.match(line)
            if m:
                return m if m.end() == indent and m.group(2) != ">" else None
            if not line.strip() or len(line) - len(line.lstrip(" ")) != indent:
                return None
        return None

    def backspace(self):
        if self.cx == 0 and self.cy == 0:
            return
        self.checkpoint("delete")
        if self.cx > 0:
            line = self.lines[self.cy]
            self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
            self.cx -= 1
        else:
            prev = self.lines[self.cy - 1]
            self.lines[self.cy - 1] = prev + self.lines.pop(self.cy)
            self.cy -= 1
            self.cx = len(prev)
        self._after = (self.cy, self.cx)

    def delete(self):
        line = self.lines[self.cy]
        if self.cx == len(line) and self.cy == len(self.lines) - 1:
            return
        self.checkpoint("delete")
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
        else:
            self.lines[self.cy] = line + self.lines.pop(self.cy + 1)
        self._after = (self.cy, self.cx)

    def delete_word_left(self):
        """Option+Backspace: the word before the cursor (and the spaces after it)."""
        if self.cx == 0:
            return self.backspace()
        line, i = self.lines[self.cy], self.cx
        while i > 0 and not line[i - 1].isalnum():
            i -= 1
        while i > 0 and line[i - 1].isalnum():
            i -= 1
        self.checkpoint("delete", boundary=True)
        self.lines[self.cy] = line[:i] + line[self.cx:]
        self.cx = i
        self._after = (self.cy, self.cx)

    def delete_word_right(self):
        """Option+Fn+Backspace: the word after the cursor."""
        line, i = self.lines[self.cy], self.cx
        if i >= len(line):
            return self.delete()
        while i < len(line) and not line[i].isalnum():
            i += 1
        while i < len(line) and line[i].isalnum():
            i += 1
        self.checkpoint("delete", boundary=True)
        self.lines[self.cy] = line[:self.cx] + line[i:]
        self._after = (self.cy, self.cx)

    def cut_line(self):
        self.checkpoint("cut")
        line = self.lines[self.cy]
        if len(self.lines) == 1:
            self.lines[0] = ""
        else:
            del self.lines[self.cy]
            self.cy = min(self.cy, len(self.lines) - 1)
        self.cx = 0
        return line

    def indent(self):
        self.checkpoint("indent")
        self.lines[self.cy] = "  " + self.lines[self.cy]
        self.cx += 2

    def outdent(self):
        line = self.lines[self.cy]
        n = min(2, len(line) - len(line.lstrip(" ")))
        if n:
            self.checkpoint("indent")
            self.lines[self.cy] = line[n:]
            self.cx = max(0, self.cx - n)

    # Movement
    def left(self):
        if self.cx > 0:
            self.cx -= 1
        elif self.cy > 0:
            self.cy -= 1
            self.cx = len(self.lines[self.cy])

    def right(self):
        if self.cx < len(self.lines[self.cy]):
            self.cx += 1
        elif self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0

    def word_left(self):
        if self.cx == 0:
            return self.left()
        line, i = self.lines[self.cy], self.cx
        while i > 0 and not line[i - 1].isalnum():
            i -= 1
        while i > 0 and line[i - 1].isalnum():
            i -= 1
        self.cx = i

    def word_right(self):
        line, i = self.lines[self.cy], self.cx
        if i >= len(line):
            return self.right()
        while i < len(line) and not line[i].isalnum():
            i += 1
        while i < len(line) and line[i].isalnum():
            i += 1
        self.cx = i


# --- Spell checking ----------------------------------------------------------

SPELL_SWIFT = r'''// lawnotes spelling helper: one JSON request per line on stdin, one JSON reply per line on stdout.
// Uses the macOS system spell checker (same dictionary as Notes and Pages, including learned words).
import AppKit

let checker = NSSpellChecker.shared
let lang = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "en_GB"
_ = checker.setLanguage(lang)
setvbuf(stdout, nil, _IOLBF, 0)

// Python indexes strings by code point; NSString by UTF-16 unit.
func scalars(_ s: String, _ utf16: Int) -> Int {
    let i = String.Index(utf16Offset: utf16, in: s)
    return s.unicodeScalars.distance(from: s.unicodeScalars.startIndex, to: i)
}

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
    let text = req["text"] as? String ?? ""
    var reply: [String: Any] = ["id": req["id"] ?? 0]
    switch req["op"] as? String ?? "" {
    case "check":
        var bad: [[Int]] = []
        var start = 0
        let length = (text as NSString).length
        while start < length {
            let r = checker.checkSpelling(of: text, startingAt: start, language: lang, wrap: false,
                                          inSpellDocumentWithTag: 0, wordCount: nil)
            if r.location == NSNotFound || r.length == 0 || r.location < start { break }
            let a = scalars(text, r.location)
            bad.append([a, scalars(text, r.location + r.length) - a])
            start = r.location + r.length
        }
        reply["bad"] = bad
    case "suggest":
        let r = NSRange(location: 0, length: (text as NSString).length)
        reply["words"] = checker.guesses(forWordRange: r, in: text, language: lang, inSpellDocumentWithTag: 0) ?? []
    case "learn":
        checker.learnWord(text)
        reply["ok"] = true
    default:
        break
    }
    if let out = try? JSONSerialization.data(withJSONObject: reply), let s = String(data: out, encoding: .utf8) {
        print(s)
    }
}
'''


class Speller:
    """Spelling via a tiny compiled Swift helper around the macOS spell checker.

    Lines are checked in the background: lookup() returns None until the reply
    arrives, and poll() collects replies without ever blocking typing."""

    def __init__(self, lang):
        self.lang = lang
        self.proc = None
        self.cache, self.waiting, self.replies = {}, {}, {}
        self.in_flight = set()
        self.next_id, self.last_send = 0, 0.0
        self.partial = b""
        self.state = "starting"
        threading.Thread(target=self._start, daemon=True).start()

    def _start(self):
        try:
            exe = os.path.join(CACHE_DIR, "spell-helper")
            stamp = exe + ".sha256"
            digest = hashlib.sha256(SPELL_SWIFT.encode()).hexdigest()
            fresh = os.path.exists(exe) and os.path.exists(stamp) and open(stamp).read() == digest
            if not fresh:  # compile once (a few seconds), then reuse
                if not shutil.which("swiftc"):
                    raise RuntimeError("swiftc not found")
                self.state = "compiling"
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(exe + ".swift", "w") as f:
                    f.write(SPELL_SWIFT)
                subprocess.run(["swiftc", "-O", exe + ".swift", "-o", exe],
                               check=True, capture_output=True, timeout=600)
                with open(stamp, "w") as f:
                    f.write(digest)
            self.proc = subprocess.Popen([exe, self.lang], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            os.set_blocking(self.proc.stdout.fileno(), False)
            self.state = "ready"
        except Exception:
            self.state = "off"

    @property
    def busy(self):
        """True while replies are due soon, so the editor should check back quickly."""
        return self.state in ("starting", "compiling") or (
            bool(self.waiting) and time.time() - self.last_send < 3)

    def _send(self, op, text):
        if self.state != "ready":
            return None
        self.next_id += 1
        try:
            self.proc.stdin.write((json.dumps({"id": self.next_id, "op": op, "text": text}) + "\n").encode())
            self.proc.stdin.flush()
        except OSError:
            self.state = "off"
            return None
        self.last_send = time.time()
        return self.next_id

    def lookup(self, line):
        """Misspelt (start, length) spans in a line, () if none, or None while being checked."""
        if line in self.cache:
            return self.cache[line]
        if self.state == "off" or not line.strip():
            return ()
        if line not in self.in_flight:
            rid = self._send("check", line)
            if rid:
                self.waiting[rid] = line
                self.in_flight.add(line)
        return None

    def poll(self, wait=0.0):
        """Collect replies from the helper; True if new spelling results arrived."""
        if self.state != "ready":
            return False
        fd = self.proc.stdout.fileno()
        if wait and not select.select([fd], [], [], wait)[0]:
            return False
        try:
            data = os.read(fd, 1 << 16)
        except BlockingIOError:
            return False
        except OSError:
            data = b""
        if not data:  # helper exited
            self.state = "off"
            return False
        self.partial += data
        *lines, self.partial = self.partial.split(b"\n")
        got = False
        for raw in lines:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            rid = msg.get("id")
            if rid in self.waiting:
                line = self.waiting.pop(rid)
                self.in_flight.discard(line)
                self.cache[line] = tuple((a, n) for a, n in msg.get("bad", []))
                got = True
            else:
                self.replies[rid] = msg
        if len(self.cache) > 20000:
            self.cache.clear()
        return got

    def wait_for(self, lines, timeout=2.0):
        """Check several lines and wait (briefly) until all have results."""
        for line in lines:
            self.lookup(line)
        end = time.time() + timeout
        while self.waiting and self.state == "ready" and time.time() < end:
            self.poll(0.05)

    def call(self, op, text, timeout=2.0):
        rid = self._send(op, text)
        end = time.time() + timeout
        while rid and rid not in self.replies and self.state == "ready" and time.time() < end:
            self.poll(0.05)
        return self.replies.pop(rid, None)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
            except OSError:
                pass


# --- Clipboard and version history -------------------------------------------

# --- Export to Word and PDF -----------------------------------------------------

EXPORT_DIR = os.path.expanduser(os.environ.get("LAWNOTES_EXPORT_DIR", "~/Downloads"))
EXPORT_CSS = """html { color-scheme: light; }
body { font-family: "Times New Roman", Times, serif; font-size: 12pt; line-height: 1.5;
       color: #000; background: #fff; }
h1 { font-size: 16pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; }
.footnotes, section.footnotes { font-size: 10pt; }"""


class ExportError(Exception):
    pass


def find_tool(name):
    """Find a program even when Law Notes runs with the minimal PATH macOS gives apps."""
    extra = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]
    return shutil.which(name, path=os.pathsep.join([os.environ.get("PATH", "")] + extra))


def export_markdown(lines):
    """A note as Markdown for pandoc: case names in italics (OSCOLA), and indented lines that
    aren't part of a list kept as text (Markdown would otherwise turn them into code blocks)."""
    out, in_list = [], False  # in_list: the last non-blank line belongs to a list
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if re.fullmatch(r"[-*+]\s*", stripped):  # an empty bullet carries nothing, and a lone "-"
            continue                              # under text can make pandoc see a table or heading
        if stripped:
            if indent >= 4 and not in_list:  # indentation outside a list is layout, not code
                line, indent = stripped, 0
            if BULLET_RE.match(line) and not stripped.startswith(">"):
                in_list = True
            elif indent == 0:
                in_list = False
        if not HEADING_RE.match(line):
            line = CASE_RE.sub(lambda m: m.group() if line[max(0, m.start() - 1):m.start()] in ("*", "_")
                               else f"*{m.group()}*", line)
        out.append(line)
    return "\n".join(out) + "\n"


def compiled_helper(name):
    """A small Swift helper shipped as app/<name>.swift, compiled once into the cache."""
    source = os.path.join(APP_DIR, "app", name + ".swift")
    exe = os.path.join(CACHE_DIR, name)
    with open(source, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    stamp = exe + ".sha256"
    if not (os.path.exists(exe) and os.path.exists(stamp) and open(stamp).read() == digest):
        if not find_tool("swiftc"):
            raise ExportError("PDF export needs Apple's Command Line Tools (xcode-select --install)")
        os.makedirs(CACHE_DIR, exist_ok=True)
        r = subprocess.run([find_tool("swiftc"), "-O", source, "-o", exe], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ExportError("couldn't build the PDF helper: " + r.stderr.strip()[-200:])
        with open(stamp, "w") as f:
            f.write(digest)
    return exe


def reference_docx(pandoc):
    """pandoc's Word template with Times New Roman and 1.5 line spacing, made once per pandoc."""
    version = subprocess.run([pandoc, "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    path = os.path.join(CACHE_DIR, "reference-" + re.sub(r"[^\w.]", "_", version) + ".docx")
    if os.path.exists(path):
        return path
    import zipfile
    raw = subprocess.run([pandoc, "--print-default-data-file", "reference.docx"], capture_output=True).stdout
    os.makedirs(CACHE_DIR, exist_ok=True)
    src = path + ".src"
    with open(src, "wb") as f:
        f.write(raw)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path + ".tmp", "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                xml = data.decode("utf-8")
                xml = re.sub(r"<w:rFonts [^>]*/>", '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
                             'w:cs="Times New Roman" w:eastAsia="Times New Roman"/>', xml)
                xml = xml.replace("<w:pPrDefault>", '<w:pPrDefault><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr>', 1) \
                    if "<w:pPrDefault/>" not in xml and "<w:pPrDefault><w:pPr>" not in xml else xml
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    os.remove(src)
    os.replace(path + ".tmp", path)
    return path


def export_note(path, lines, fmt):
    """Write the note as .docx or .pdf into EXPORT_DIR; returns the new file's path."""
    pandoc = find_tool("pandoc")
    if not pandoc:
        raise ExportError("export needs pandoc: run  brew install pandoc  (free)")
    name = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(EXPORT_DIR, exist_ok=True)
    out = os.path.join(EXPORT_DIR, f"{name}.{fmt}")
    text = export_markdown(lines)
    # each line of a note stays a line; no "simple tables", which plain notes trigger by accident
    source = "markdown+hard_line_breaks-simple_tables-multiline_tables"
    if fmt == "docx":
        r = subprocess.run([pandoc, "-f", source, "-t", "docx", "--reference-doc", reference_docx(pandoc),
                            "-o", out], input=text, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise ExportError(r.stderr.strip()[-200:] or "pandoc failed")
        return out
    if fmt == "pdf":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            html, css = os.path.join(d, "note.html"), os.path.join(d, "note.css")
            with open(css, "w") as f:
                f.write(EXPORT_CSS)  # our own stylesheet replaces pandoc's (which follows dark mode)
            r = subprocess.run([pandoc, "-f", source, "-t", "html5", "-s", "--metadata", f"pagetitle={name}",
                                "-c", css, "--embed-resources", "-o", html],
                               input=text, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise ExportError(r.stderr.strip()[-200:] or "pandoc failed")
            r = subprocess.run([compiled_helper("html2pdf"), html, os.path.abspath(out)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(out):
                raise ExportError("couldn't make the PDF")
        return out
    raise ExportError(f"unknown format {fmt}")


def table_from_text(text):
    """A table copied from Word (or Excel, Pages) arrives as plain text: cells separated by tabs,
    rows by line breaks. Returns it as a Markdown pipe table with lined-up columns, or None if
    the text isn't a table. A tab-free line after a complete row is taken as another paragraph
    of that row's last cell (Word's plain text can't tell this from a new row's first cell)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n").split("\n")
    if len(lines) < 2 or 2 * sum("\t" in line for line in lines) < len(lines):
        return None
    cols = max(line.count("\t") for line in lines) + 1
    rows, cur = [], None
    for line in lines:
        if cur is None and rows and "\t" not in line:
            rows[-1] += "<br>" + line      # more paragraphs of the previous row's last cell
            continue
        cur = line if cur is None else cur + "<br>" + line
        if cur.count("\t") >= cols - 1:
            rows.append(cur)
            cur = None
    if cur is not None:
        rows.append(cur)
    if len(rows) < 2:
        return None
    cells = [[clean_text(c).strip().replace("|", "\\|") for c in row.split("\t")] for row in rows]
    for row in cells:
        row += [""] * (cols - len(row))
    widths = [max(3, max(len(row[i]) for row in cells)) for i in range(cols)]
    line = lambda row: "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"
    rule = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([line(cells[0]), rule] + [line(row) for row in cells[1:]])


def clean_text(text):
    """Pasted text: keep special spaces (as spaces) and line separators, drop control codes."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4))
    out = []
    for c in text:
        cat = unicodedata.category(c)
        if c == "\n" or c.isprintable():
            out.append(c)
        elif cat == "Zs":  # non-breaking and thin spaces from Westlaw, Lexis and PDFs
            out.append(" ")
        elif cat in ("Zl", "Zp"):
            out.append("\n")
    return "".join(out)


def clipboard_set(text):
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=3,
                       env={**os.environ, "LC_CTYPE": "UTF-8"})
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def clipboard_get():
    try:
        r = subprocess.run(["pbpaste"], capture_output=True, timeout=3, env={**os.environ, "LC_CTYPE": "UTF-8"})
        return r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None


def notes_location():
    """Where the notes really are, in words: 'iCloud Drive/UCL Notes' or '~/UCL/notes'."""
    real, icloud = os.path.realpath(NOTES_DIR), os.path.realpath(ICLOUD_DIR)
    if real.startswith(icloud + os.sep):
        return "iCloud Drive/" + os.path.relpath(real, icloud)
    return NOTES_DIR.replace(os.path.expanduser("~"), "~", 1)


class History:
    """Automatic snapshots of the notes folder in a private git store (HISTORY_DIR).

    Snapshots run in the background every few minutes while you work, when you switch
    notes and when you quit; ^R lists the versions of the open note."""

    def __init__(self):
        self.ok = shutil.which("git") is not None
        self.last = 0.0
        self.lock = threading.Lock()

    def git(self, *args):
        return subprocess.run(
            ["git", f"--git-dir={HISTORY_DIR}", f"--work-tree={os.path.realpath(NOTES_DIR)}", "-c", "user.name=lawnotes",
             "-c", "user.email=lawnotes@localhost", "-c", "core.quotepath=off", "-c", "commit.gpgsign=false", *args],
            capture_output=True, timeout=60)

    def _ensure(self):
        if not os.path.exists(os.path.join(HISTORY_DIR, "HEAD")):
            os.makedirs(os.path.dirname(HISTORY_DIR), exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", HISTORY_DIR], check=True, capture_output=True, timeout=30)
        exclude = os.path.join(HISTORY_DIR, "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        try:
            current = open(exclude).read()
        except OSError:
            current = ""
        if current != HISTORY_EXCLUDE:
            with open(exclude, "w") as f:
                f.write(HISTORY_EXCLUDE)
            self.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".")  # forget non-notes added before

    def snapshot(self, wait=False):
        if not self.ok:
            return
        self.last = time.time()

        def work():
            with self.lock:
                try:
                    self._ensure()
                    self.git("add", "-A", ".")
                    if self.git("diff", "--cached", "--quiet").returncode == 1:
                        self.git("commit", "-q", "-m", f"Snapshot {datetime.datetime.now():%Y-%m-%d %H:%M}")
                    self.export_bundle()
                except (OSError, subprocess.SubprocessError) as e:
                    log(f"history snapshot failed: {type(e).__name__}: {e}")
        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def mac_name():
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = ""
        return re.sub(r"[/:]", "-", name or os.uname().nodename)

    def export_bundle(self):
        """Weekly: copy this Mac's history into iCloud Drive as one file (safe to sync,
        unlike a live git store), so it survives this Mac and other Macs can read it."""
        if not os.path.isdir(ICLOUD_DIR):
            return
        target = os.path.join(HISTORY_BUNDLES, self.mac_name() + ".bundle")
        try:
            if time.time() - os.path.getmtime(target) < BUNDLE_EVERY:
                return
        except OSError:
            pass
        os.makedirs(HISTORY_BUNDLES, exist_ok=True)
        tmp = os.path.join(HISTORY_BUNDLES, "." + os.path.basename(target) + ".tmp")
        if self.git("bundle", "create", tmp, "--branches").returncode == 0:
            os.replace(tmp, target)

    def import_bundles(self):
        """Read the other Macs' weekly history copies, so ^R can offer their versions too."""
        own = self.mac_name() + ".bundle"
        try:
            names = [n for n in os.listdir(HISTORY_BUNDLES) if n.endswith(".bundle") and n != own]
        except OSError:
            return
        for n in names:
            mac = re.sub(r"[^\w.-]", "_", n[:-len(".bundle")])
            self.git("fetch", "-q", os.path.join(HISTORY_BUNDLES, n), f"+refs/heads/*:refs/remotes/{mac}/*")

    def rel(self, path):
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(NOTES_DIR))
        return None if rel.startswith("..") else rel

    def versions(self, path):
        """[(commit, unix time, other Mac's name or "")] newest first, for one note."""
        rel = self.rel(path)
        if not (self.ok and rel and os.path.exists(os.path.join(HISTORY_DIR, "HEAD"))):
            return []
        with self.lock:
            self.import_bundles()
            r = self.git("log", "--all", "--source", "--format=%H %ct %S", "--", rel)
        out, seen = [], set()
        for line in r.stdout.decode().splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 2 or parts[0] in seen:
                continue
            seen.add(parts[0])
            ref = parts[2] if len(parts) > 2 else ""
            mac = ref.split("/")[2] if ref.startswith("refs/remotes/") else ""
            out.append((parts[0], int(parts[1]), mac))
        return sorted(out, key=lambda v: -v[1])

    def content(self, commit, path):
        r = self.git("show", f"{commit}:{self.rel(path)}")
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


# --- Panes ---------------------------------------------------------------------

class Pane:
    """One view of a note: which note, where it's scrolled, its cursor and selection."""

    def __init__(self):
        self.buf = None
        self.top = self.top_seg = 0
        self.want_x = self.anchor = None
        self.free_view = self.hold_selection = False
        self.row_map = []
        self.rows, self.left, self.wrapw = 20, 0, 80
        self.cy = self.cx = 0
        self.rect = (0, 0, 24, 80)  # top, left, height, width on screen


class Layout:
    """A tree of splits: a leaf holds a pane; 'v' puts children side by side, 'h' stacks them."""

    MIN_W, MIN_H = 16, 3  # smallest pane a border drag can leave

    def __init__(self, kind, a=None, b=None, pane=None):
        self.kind, self.a, self.b, self.pane = kind, a, b, pane
        self.ratio = 0.5            # share of the space the first child gets
        self.rect = (0, 0, 0, 0)

    @classmethod
    def leaf(cls, pane):
        return cls("leaf", pane=pane)

    def leaves(self):
        return [self.pane] if self.kind == "leaf" else self.a.leaves() + self.b.leaves()

    def replace(self, pane, node):
        """Put node where the leaf holding pane is."""
        if self.kind == "leaf":
            if self.pane is pane:
                self.kind, self.a, self.b, self.pane = node.kind, node.a, node.b, node.pane
                self.ratio = node.ratio
                return True
            return False
        return self.a.replace(pane, node) or self.b.replace(pane, node)

    def remove(self, pane):
        """Remove pane's leaf; its sibling takes the parent's place. Returns that sibling."""
        for child, other in ((self.a, self.b), (self.b, self.a)):
            if child is None:
                continue
            if child.kind == "leaf" and child.pane is pane:
                self.kind, self.a, self.b, self.pane = other.kind, other.a, other.b, other.pane
                self.ratio = other.ratio
                return self
            if child.kind != "leaf":
                found = child.remove(pane)
                if found:
                    return found
        return None

    def place(self, y, x, h, w):
        """Give every pane its rectangle. Returns the borders that can be dragged with the
        mouse: ("v", node, y, x, height) for the line between side-by-side panes, and
        ("h", node, y, x, width) for the status line above the border of stacked panes."""
        self.rect = (y, x, h, w)
        if self.kind == "leaf":
            self.pane.rect = (y, x, h, w)
            return []
        if self.kind == "v":
            w1 = min(max(1, round((w - 1) * self.ratio)), max(1, w - 2))
            return (self.a.place(y, x, h, w1) + [("v", self, y, x + w1, h)]
                    + self.b.place(y, x + w1 + 1, h, w - w1 - 1))
        h1 = min(max(1, round(h * self.ratio)), max(1, h - 1))
        return (self.a.place(y, x, h1, w) + [("h", self, y + h1 - 1, x, w)]
                + self.b.place(y + h1, x, h - h1, w))

    def drag_to(self, y, x):
        """Move this split's border to the mouse position, keeping both sides usable."""
        ny, nx, nh, nw = self.rect
        if self.kind == "v":
            w1 = min(max(x - nx, self.MIN_W), nw - 1 - self.MIN_W)
            self.ratio = w1 / max(1, nw - 1)
        else:
            h1 = min(max(y - ny + 1, self.MIN_H), nh - self.MIN_H)
            self.ratio = h1 / max(1, nh)


# --- Terminal UI -------------------------------------------------------------

HINTS = [("^G", "Help"), ("^S", "Save"), ("^O", "Open"), ("^P", "Search all"), ("^B", "Cases"),
         ("^F", "Find"), ("^T", "Template"), ("^W", "Spelling"), ("^R", "History"), ("⌥D", "Split"),
         ("^Q", "Quit")]

# ^G help screen: (key, description); key "#" marks a section heading, "" a plain line.
HELP = [
    ("", "Law Notes is in beta: it's tested and your notes are plain Markdown files, but expect rough edges. If it ever closes unexpectedly, the reason is in iCloud Drive → Law Notes Logs."),
    ("#", "Panes"),
    ("Option+D", "Split side by side; Option+Shift+D splits one above the other. The new pane opens the note list (Esc keeps the same note)."),
    ("Option+O", "Next pane. Clicking a pane also switches to it."),
    ("Option+W", "Close the pane (the note is saved first)."),
    ("Drag", "Drag the line between side-by-side panes to resize them; for stacked panes, drag the upper pane's status bar."),
    ("", "The same note in two panes stays in sync: both show your edits as you type."),
    ("#", "Notes and folders"),
    ("^O", "Open a note. Type to filter the list, Enter to open."),
    ("^N", "New note. Use Folder/Name to file it in a folder, e.g. Contract/Offer — the folder is created for you."),
    ("^S", "Save now. Notes also save by themselves a few seconds after you stop typing, and when you quit."),
    ("^Q", "Save and quit."),
    ("#", "Writing"),
    ("Enter", "New line. Continues bullets and numbered lists; Enter on an empty bullet ends the list."),
    ("Shift+Enter", "New line under the same bullet, without a new bullet. Option+Enter does the same."),
    ("Tab", "Indent a bullet (Shift+Tab to outdent)."),
    ("^T", "Insert a template: case brief, problem question (IRAC), statute, essay plan, lecture notes."),
    ("^W", "Spelling: suggestions for the highlighted word, add it to your dictionary, or ignore it."),
    ("#", "Searching"),
    ("^P", "Search all notes: every line that mentions what you type, in every note. Enter jumps there."),
    ("^B", "Case index: every case cited in any note, with its citation and the notes that mention it."),
    ("^L", "Outline: jump to any heading or case in this note."),
    ("#", "Moving around"),
    ("^F", "Find. Press ^F and Enter again (or F3) for the next match; Esc clears the highlight."),
    ("^A  ^E", "Start / end of the line."),
    ("Option+⌫", "Delete the word before the cursor (Option+Fn+⌫: the word after it)."),
    ("Option+←→", "Previous / next word."),
    ("PgUp PgDn", "Page up / down."),
    ("#", "Selecting, copying and pasting"),
    ("Shift+arrows", "Select text (Shift+Option+arrows selects by word). Click with the mouse to place the cursor, drag to select."),
    ("Double-click", "Selects a word, or a whole case name, citation or statute. Triple-click selects the line."),
    ("Scroll", "The mouse wheel moves the view only; the cursor stays where it was, and typing carries on there."),
    ("Tables", "Copy a table in Word and paste it (⌘V or ^V): it becomes a Markdown table with lined-up columns, and exports back to Word as a real table."),
    ("^C  ^X  ^V", "Copy / cut / paste using the Mac clipboard: what you copy flashes green. With nothing selected, ^C and ^X take the whole line. ⌘V pastes too, but ⌘C can't copy text selected inside Law Notes (Terminal takes ⌘C): use ^C."),
    ("Typing", "With text selected, typing or Backspace replaces it."),
    ("*  _", "With text selected, * wraps it in *italics*; press again for **bold**. _ works the same way."),
    ("#", "Editing"),
    ("^K", "Cut the line (press repeatedly to cut several lines)."),
    ("^U", "Paste the cut lines above the cursor."),
    ("^Z  ^Y", "Undo / redo."),
    ("Option+E", "Export the note to Word (.docx) or PDF, into Downloads. Case names are put in italics (OSCOLA), [^1] footnotes become real footnotes. F5 does the same."),
    ("^R", "Version history: snapshots of your notes are taken automatically every few minutes while you work. Pick one to restore it (^Z undoes the restore)."),
    ("#", "Formatting (Markdown)"),
    ("# Title", "Heading;  ## Section  for a subheading."),
    ("- item", "Bullet;  1. item  numbered;  - [ ] item  to-do;  > text  quote."),
    ("**bold**", "Bold;  *italic*  italic."),
    ("#", "Colours"),
    ("", "Case names gold, citations like [1932] AC 562 pink, statutes like s 2(1) or Human Rights Act 1998 green, misspelt words highlighted in red."),
    ("D  V  C", "Party labels in problem answers: D (defendant) orange, V (victim) pink, C (claimant) blue; also D1, D2, D's."),
    ("", "If Law Notes ever closes or crashes unexpectedly, the reason is logged in iCloud Drive → Law Notes Logs."),
    ("", "Notes are plain Markdown files in ~/UCL/notes, which points to iCloud Drive → UCL Notes, so they're backed up off this Mac and open on iPhone or iPad (Files app). Any other app can open them too."),
]
ENTER = ("\n", "\r", curses.KEY_ENTER)
# Escape sequences curses may pass through untranslated (e.g. arrows sent as ESC [ A)
CSI_KEYS = {
    "[A": curses.KEY_UP, "OA": curses.KEY_UP, "[B": curses.KEY_DOWN, "OB": curses.KEY_DOWN,
    "[C": curses.KEY_RIGHT, "OC": curses.KEY_RIGHT, "[D": curses.KEY_LEFT, "OD": curses.KEY_LEFT,
    "[H": curses.KEY_HOME, "OH": curses.KEY_HOME, "[1~": curses.KEY_HOME, "[7~": curses.KEY_HOME,
    "[F": curses.KEY_END, "OF": curses.KEY_END, "[4~": curses.KEY_END, "[8~": curses.KEY_END,
    "[3~": curses.KEY_DC, "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE, "[Z": curses.KEY_BTAB,
    "[27;2;13~": "shift-enter", "[13;2u": "shift-enter",
    "[1;3D": "word-left", "[1;5D": "word-left", "[1;3C": "word-right", "[1;5C": "word-right",
    "[3;3~": "delete-word-right", "[3;5~": "delete-word-right",
    "[1;2D": curses.KEY_SLEFT, "[1;2C": curses.KEY_SRIGHT, "[1;2A": curses.KEY_SR, "[1;2B": curses.KEY_SF,
    "[1;2H": curses.KEY_SHOME, "[1;2F": curses.KEY_SEND,
    "[1;4D": "sel-word-left", "[1;6D": "sel-word-left", "[1;4C": "sel-word-right", "[1;6C": "sel-word-right",
}
# Option/Ctrl(+Shift)+arrows as curses names them
NAMED_KEYS = {"kLFT3": "word-left", "kLFT5": "word-left", "kRIT3": "word-right", "kRIT5": "word-right",
              "kLFT4": "sel-word-left", "kLFT6": "sel-word-left", "kRIT4": "sel-word-right", "kRIT6": "sel-word-right",
              "kDC3": "delete-word-right", "kDC5": "delete-word-right"}
# Shifted movement extends the selection
SELECT_MOVES = {curses.KEY_SLEFT: curses.KEY_LEFT, curses.KEY_SRIGHT: curses.KEY_RIGHT,
                curses.KEY_SR: curses.KEY_UP, curses.KEY_SF: curses.KEY_DOWN,
                curses.KEY_SHOME: curses.KEY_HOME, curses.KEY_SEND: curses.KEY_END,
                curses.KEY_SPREVIOUS: curses.KEY_PPAGE, curses.KEY_SNEXT: curses.KEY_NPAGE,
                "sel-word-left": "word-left", "sel-word-right": "word-right"}
BACKSPACE = ("\x7f", "\x08", curses.KEY_BACKSPACE)


class App:
    def __init__(self, scr):
        self.scr = scr
        self.buf = None
        self.pane = Pane()
        self.layout = Layout.leaf(self.pane)
        self.borders, self.resizing = [], None  # pane borders, and the one being dragged
        self.top = self.top_seg = 0
        self.want_x = None
        self.rows, self.left, self.wrapw = 20, 0, 80
        self.msg, self.msg_until = "", 0.0
        self.save_failed, self.last_save_try, self.recovery_path = False, 0.0, None
        self.search = ""
        self.clip, self.cutting = [], False
        self._hl = {}
        self.ignored = set()
        self.anchor = None      # selection start (line, col); the cursor is the other end
        self.free_view = False  # scrolled with the wheel: the view moves, the cursor stays put
        self.flash = None       # ((line, col), (line, col), until): what ^C just copied, flashed green
        self.last_click = (0.0, None, 0)  # time, position, count: for double and triple clicks
        self.hold_selection = False       # a double/triple click's release mustn't collapse it
        self.row_map = []       # screen row -> (line, start, end, last segment) for mouse clicks
        self.history = History()
        self.speller = Speller(SPELL_LANG) if SPELL_ON and sys.platform == "darwin" else None
        curses.raw()  # deliver ^S ^Q ^Z ^C to us instead of the terminal
        scr.timeout(1000)
        self.init_colors()

    def init_colors(self):
        """NERV theme: blood-red background, warning-orange headings, Unit-01 green and purple."""
        italic = getattr(curses, "A_ITALIC", 0)
        colors = curses.has_colors()
        if colors:
            curses.start_color()
        rich = colors and curses.COLORS >= 256
        C = curses
        # (256-colour, 8-colour fallback) for each role
        palette = {
            "text":    ((224, 52), (C.COLOR_WHITE, C.COLOR_RED)),
            "h1":      ((208, 52), (C.COLOR_YELLOW, C.COLOR_RED)),
            "h":       ((214, 52), (C.COLOR_YELLOW, C.COLOR_RED)),
            "case":    ((220, 52), (C.COLOR_YELLOW, C.COLOR_RED)),
            "cite":    ((217, 52), (C.COLOR_WHITE, C.COLOR_RED)),
            "statute": ((119, 52), (C.COLOR_GREEN, C.COLOR_RED)),
            "bullet":  ((141, 52), (C.COLOR_MAGENTA, C.COLOR_RED)),
            "quote":   ((181, 52), (C.COLOR_WHITE, C.COLOR_RED)),
            "match":   ((16, 208), (C.COLOR_BLACK, C.COLOR_YELLOW)),
            "bar":     ((230, 88), (C.COLOR_WHITE, C.COLOR_BLACK)),
            "key":     ((16, 208), (C.COLOR_BLACK, C.COLOR_YELLOW)),
            "dim":     ((174, 52), (C.COLOR_WHITE, C.COLOR_RED)),
            "sel":     ((16, 208), (C.COLOR_BLACK, C.COLOR_YELLOW)),
            "spell":   ((231, 160), (C.COLOR_WHITE, C.COLOR_MAGENTA)),
            "party_D": ((16, 214), (C.COLOR_BLACK, C.COLOR_YELLOW)),
            "party_V": ((16, 218), (C.COLOR_BLACK, C.COLOR_MAGENTA)),
            "party_C": ((16, 117), (C.COLOR_BLACK, C.COLOR_CYAN)),
            "copied":  ((16, 120), (C.COLOR_BLACK, C.COLOR_GREEN)),
            "bar_off": ((181, 88), (C.COLOR_WHITE, C.COLOR_BLACK)),
        }
        pairs = {}
        for n, (role, (c256, c8)) in enumerate(palette.items(), start=1):
            if colors:
                curses.init_pair(n, *(c256 if rich else c8))
                pairs[role] = curses.color_pair(n)
            else:
                pairs[role] = curses.A_REVERSE if role in ("match", "bar", "key", "sel") else 0
        # Words inside case names, citations and statutes are never marked as misspelt
        self.protected = {pair_of(pairs[r]) for r in ("case", "cite", "statute")} if colors else set()
        self.scr.bkgd(" ", pairs["text"])

        self.A = {
            "text": pairs["text"],
            "h1": pairs["h1"] | curses.A_BOLD,
            "h": pairs["h"] | curses.A_BOLD,
            "case": pairs["case"] | italic,
            "cite": pairs["cite"],
            "statute": pairs["statute"],
            "bullet": pairs["bullet"] | curses.A_BOLD,
            "quote": pairs["quote"] | italic,
            "match": pairs["match"],
            "bar": pairs["bar"],
            "key": pairs["key"] | curses.A_BOLD,
            "dim": pairs["dim"],
            "sel": pairs["sel"] | curses.A_BOLD,
            "spell": pairs["spell"] | curses.A_UNDERLINE,
            "party_D": pairs["party_D"] | curses.A_BOLD,
            "party_V": pairs["party_V"] | curses.A_BOLD,
            "party_C": pairs["party_C"] | curses.A_BOLD,
            "copied": pairs["copied"] | curses.A_BOLD,
            "bar_off": pairs["bar_off"],
        }
        self.italic = italic

    def log_once(self, event):
        seen = self.__dict__.setdefault("_logged", set())
        if event not in seen and len(seen) < 30:
            seen.add(event)
            log(event)

    def say(self, msg, secs=4):
        self.msg, self.msg_until = msg, time.time() + secs

    # Files
    def list_notes(self):
        found = []
        for root, dirs, files in os.walk(NOTES_DIR):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in files:
                if f.endswith(NOTE_EXTS) and not f.startswith("."):
                    p = os.path.join(root, f)
                    found.append((os.path.getmtime(p), os.path.relpath(p, NOTES_DIR), p))
        found.sort(reverse=True)
        return found

    def remember_position(self):
        """Note where the cursor is in the open note, for next time (at most 300 notes)."""
        b = self.buf
        if not b:
            return
        try:
            with open(POSITIONS_FILE) as f:
                positions = json.load(f)
        except (OSError, ValueError):
            positions = {}
        key = os.path.realpath(b.path)
        positions.pop(key, None)
        positions[key] = [b.cy, b.cx]
        positions = dict(list(positions.items())[-300:])
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(POSITIONS_FILE + ".tmp", "w") as f:
                json.dump(positions, f)
            os.replace(POSITIONS_FILE + ".tmp", POSITIONS_FILE)
        except OSError:
            pass

    def recall_position(self):
        try:
            with open(POSITIONS_FILE) as f:
                cy, cx = json.load(f)[os.path.realpath(self.buf.path)]
            b = self.buf
            b.cy = max(0, min(int(cy), len(b.lines) - 1))
            b.cx = max(0, min(int(cx), len(b.lines[b.cy])))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def open(self, path):
        if not self.save_if_dirty():
            self.say("Can't switch notes until this one saves", 8)
            return False
        self.history.snapshot()
        self.remember_position()
        shared = [b for p, b in self.buffers() if p is not self.pane
                  and os.path.realpath(b.path) == os.path.realpath(path)]
        self.buf = shared[0] if shared else Buffer(path)  # same note in two panes: one shared text
        if shared:
            self.buf.cy = self.buf.cx = 0
        self.top = self.top_seg = 0
        self.free_view = False
        self.search = ""
        self.recovery_path = None
        if not os.path.exists(path):
            title = os.path.splitext(os.path.basename(path))[0].replace("-", " ").replace("_", " ")
            self.buf.lines = [f"# {title}", "", ""]
            self.buf.cy = 2
            if self.safe_save():
                self.say("New note created · ^T inserts a template")
        else:
            if not shared:
                self.recall_position()  # back where you left off
            if self.buf.note:
                self.say(self.buf.note, 8)
        return True

    def safe_save(self, announce=False):
        """Save without ever crashing. On failure keep the text, write a recovery copy and
        show a warning that stays until a save succeeds. Returns True if saved."""
        b = self.buf
        self.last_save_try = time.time()
        try:
            note = b.save()
        except OSError as e:
            self.save_failed = True
            self.say(f"SAVE FAILED ({e.strerror or e}) — {self.write_recovery()}", 3600)
            return False
        if note:
            self.say(note, 12)
        elif self.save_failed or announce:
            self.say("Saved ✓")
        self.save_failed = False
        if time.time() - self.history.last >= SNAPSHOT_EVERY:
            self.history.snapshot()
        return True

    def write_recovery(self):
        b = self.buf
        if not self.recovery_path:
            name = os.path.splitext(os.path.basename(b.path))[0]
            self.recovery_path = os.path.join(
                CACHE_DIR, "recovery", f"{name} {datetime.datetime.now():%Y-%m-%d %H.%M.%S}.md")
        try:
            os.makedirs(os.path.dirname(self.recovery_path), exist_ok=True)
            with open(self.recovery_path, "w", encoding="utf-8") as f:
                f.write("\n".join(b.lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return "a copy is in " + self.recovery_path.replace(os.path.expanduser("~"), "~", 1)
        except OSError:
            return "no copy could be written either: keep this pane open and copy your text out"

    def save_pane_buffer(self, pane):
        with self.viewing(pane):
            return self.save_if_dirty()

    def save_if_dirty(self):
        if self.buf and self.buf.dirty:
            return self.safe_save()
        return True

    def check_outside_change(self):
        """A note open here but changed elsewhere (another Mac via iCloud, another app):
        reload it while it has no unsaved changes, instead of making a conflict copy later."""
        b = self.buf
        if b.dirty or b.disk_mtime is None:
            return
        try:
            mtime = os.stat(os.path.realpath(b.path)).st_mtime_ns
        except OSError:
            return
        if mtime != b.disk_mtime:
            cy, cx = b.cy, b.cx
            b.load()
            b.cy = min(cy, len(b.lines) - 1)
            b.cx = min(cx, len(b.lines[b.cy]))
            b.undo_stack.clear()  # undoing past the reload would overwrite the other version
            b.redo_stack.clear()
            self.say("This note changed on another Mac or in another app — reloaded", 8)

    def check_inbox(self):
        """Requests from the launcher ("open this note") and messages from the updater."""
        try:
            with open(REQUEST_FILE) as f:
                req = json.load(f)
            os.remove(REQUEST_FILE)
            path = requested_note(req)
            if path:
                self.goto(path, 0)
        except (OSError, ValueError, TypeError):
            pass
        try:
            with open(MESSAGE_FILE) as f:
                msg = f.read().strip()
            os.remove(MESSAGE_FILE)
            if msg:
                self.say(msg, 15)
        except OSError:
            pass

    def autosave(self):
        b, now = self.buf, time.time()
        if not b.dirty:
            return
        if self.save_failed and now - self.last_save_try < 10:
            return  # retry a failing save every 10 s, not every keystroke
        if now - b.changed_at >= AUTOSAVE_AFTER or now - (b.dirty_since or now) >= AUTOSAVE_MAX:
            self.safe_save()

    def open_picker(self):
        notes = self.list_notes()

        def build(query):
            words = query.lower().split()
            items = []
            for mtime, rel, path in notes:
                if all(w in rel.lower() for w in words):
                    when = datetime.datetime.fromtimestamp(mtime).strftime("%d %b %H:%M")
                    items.append((f"{rel}  ·  {when}", ("open", path)))
            name = query.strip().strip("/")
            if name:
                folder, _, base = name.rpartition("/")
                label = f"+ New note “{base}” in folder {folder}/" if folder else f"+ New note “{name}”"
                items.append((label, ("new", name)))
            return items

        choice = self.pick(f"Notes in {notes_location()}", build,
                           "No notes yet. Type a name and press Enter.",
                           guide="New note or folder: type Folder/Name and press Enter, e.g. Contract/Offer "
                                 "makes the Contract folder (Contract/Offer/Postal rule nests deeper).")
        if choice is None:
            return False
        action, arg = choice
        if action == "new":
            try:
                arg = note_path(arg)
            except ValueError as e:
                self.say(str(e), 6)
                return False
        return self.open(arg)

    # Layout helpers
    def segs(self, i):
        return wrap_starts(self.buf.lines[i], self.wrapw)

    def seg_of(self, i, cx):
        starts = self.segs(i)
        return bisect.bisect_right(starts, cx) - 1, starts

    def scroll(self):
        b = self.buf
        self.top = min(self.top, len(b.lines) - 1)
        self.top_seg = min(self.top_seg, len(self.segs(self.top)) - 1)
        ck, _ = self.seg_of(b.cy, b.cx)
        if (b.cy, ck) < (self.top, self.top_seg):
            self.top, self.top_seg = b.cy, ck
            return
        if b.cy - self.top <= self.rows:
            if b.cy == self.top:
                count = ck - self.top_seg + 1
            else:
                count = len(self.segs(self.top)) - self.top_seg + ck + 1
                count += sum(len(self.segs(j)) for j in range(self.top + 1, b.cy))
            if count <= self.rows:
                return
        i, s = b.cy, ck
        for _ in range(self.rows - 1):
            if s > 0:
                s -= 1
            elif i > 0:
                i -= 1
                s = len(self.segs(i)) - 1
            else:
                break
        self.top, self.top_seg = i, s

    def highlight(self, line):
        if line in self._hl:
            return self._hl[line]
        A, n = self.A, len(line)
        m = HEADING_RE.match(line)
        if m:
            attrs = [A["h1"] if len(m.group(1)) == 1 else A["h"]] * n
        else:
            quote = line.lstrip().startswith(">")
            attrs = [A["quote"] if quote else A["text"]] * n
            for mm in PARTY_RE.finditer(line):
                attrs[mm.start():mm.end()] = [A["party_" + mm.group(1)]] * (mm.end() - mm.start())
            for rx, key in ((STATUTE_RE, "statute"), (CITE_RE, "cite"), (CASE_RE, "case")):
                for mm in rx.finditer(line):
                    attrs[mm.start():mm.end()] = [A[key]] * (mm.end() - mm.start())
            for rx, extra in ((BOLD_RE, curses.A_BOLD), (ITALIC_RE, self.italic)):
                for mm in rx.finditer(line):
                    for k in range(mm.start(), mm.end()):
                        attrs[k] |= extra
            bm = BULLET_RE.match(line)
            if bm and not quote:
                attrs[bm.start(2):bm.end(2)] = [A["bullet"]] * len(bm.group(2))
        if len(self._hl) > 5000:
            self._hl.clear()
        self._hl[line] = attrs
        return attrs

    # Drawing
    def put(self, y, x, text, attr=0):
        try:
            self.scr.addstr(y, x, text, attr)
        except (curses.error, ValueError):
            pass

    # Panes: each has its own note, view, cursor and selection. The App's view attributes
    # always hold the active pane's state; other panes are swapped in briefly (viewing()).
    VIEW_ATTRS = ("buf", "top", "top_seg", "want_x", "anchor", "free_view", "row_map",
                  "rows", "left", "wrapw", "hold_selection")

    def store_view(self):
        for a in self.VIEW_ATTRS:
            setattr(self.pane, a, getattr(self, a))
        if self.buf:
            self.pane.cy, self.pane.cx = self.buf.cy, self.buf.cx

    def load_view(self, pane):
        for a in self.VIEW_ATTRS:
            setattr(self, a, getattr(pane, a))
        self.pane = pane
        if self.buf:  # panes showing the same note share its text but keep their own cursor
            self.buf.cy = min(pane.cy, len(self.buf.lines) - 1)
            self.buf.cx = min(pane.cx, len(self.buf.lines[self.buf.cy]))

    @contextlib.contextmanager
    def viewing(self, pane):
        if pane is self.pane:
            yield
            return
        active = self.pane
        self.store_view()
        self.load_view(pane)
        try:
            yield
        finally:
            self.store_view()
            self.load_view(active)

    def activate(self, pane):
        if pane is not self.pane:
            self.store_view()
            self.load_view(pane)

    def panes(self):
        return self.layout.leaves()

    def buffers(self):
        """(pane, buffer) for each note on screen, once per note."""
        seen, out = set(), []
        for p in self.panes():
            b = self.buf if p is self.pane else p.buf
            if b and id(b) not in seen:
                seen.add(id(b))
                out.append((p, b))
        return out

    def split(self, kind):
        """Option+D (side by side) or Option+Shift+D (stacked): the new pane opens the note list."""
        rect = self.pane.rect
        if (kind == "v" and rect[3] < 50) or (kind == "h" and rect[2] < 8):
            self.say("Not enough room to split this pane", 5)
            return
        self.store_view()
        new = Pane()
        for a in self.VIEW_ATTRS:  # starts on the same note, same place
            setattr(new, a, getattr(self.pane, a))
        new.cy, new.cx, new.anchor = self.pane.cy, self.pane.cx, None
        self.layout.replace(self.pane, Layout(kind, Layout.leaf(self.pane), Layout.leaf(new)))
        self.load_view(new)
        self.draw()
        if not self.open_picker():
            self.say("Both panes show this note; Option+W closes a pane", 5)

    def close_pane(self):
        if len(self.panes()) == 1:
            self.say("This is the only pane; ^Q quits", 4)
            return
        b = self.buf
        if b.dirty and sum(1 for p, _ in self.buffers() if (self.buf if p is self.pane else p.buf) is b) == 1:
            if not self.safe_save():
                return
        sibling = self.layout.remove(self.pane)
        self.pane = sibling.leaves()[0]  # the closed pane's state is simply dropped
        for a in self.VIEW_ATTRS:
            setattr(self, a, getattr(self.pane, a))
        self.load_view(self.pane)

    def next_pane(self):
        panes = self.panes()
        if len(panes) > 1:
            self.activate(panes[(panes.index(self.pane) + 1) % len(panes)])

    def pane_at(self, y, x):
        for pane in self.panes():
            py, px, ph, pw = pane.rect
            if py <= y < py + ph and px <= x < px + pw:
                return pane
        return None

    def draw(self):
        scr = self.scr
        scr.erase()
        H, W = scr.getmaxyx()
        if H < 5 or W < 30:
            self.put(0, 0, "Window too small"[:W - 1])
            scr.refresh()
            return
        self.borders = self.layout.place(0, 0, H - 1, W)
        for kind, node, y, x, h in self.borders:  # the line between side-by-side panes
            if kind == "v":
                look = self.A["key"] if node is self.resizing else self.A["dim"]
                for r in range(h):
                    self.put(y + r, x, "│", look)
        cursor = None
        for pane in self.panes():
            with self.viewing(pane):
                c = self.draw_pane(*pane.rect, active=pane is self.pane)
            if pane is self.pane:
                cursor = c
        x = 1
        for key, label in HINTS:
            if x + len(key) + len(label) + 2 >= W:
                break
            self.put(H - 1, x, key, self.A["key"])
            self.put(H - 1, x + len(key) + 1, label, self.A["dim"])
            x += len(key) + len(label) + 3

        try:
            curses.curs_set(1 if cursor else 0)  # scrolled away from the cursor: hide it
        except curses.error:
            pass
        if cursor:
            scr.move(*cursor)
        sys.stdout.write("\x1b[?2026h")  # begin synchronised update (ignored where unsupported)
        sys.stdout.flush()
        scr.refresh()
        sys.stdout.write("\x1b[?2026l")
        sys.stdout.flush()

    def draw_pane(self, top_y, left_x, height, width, active):
        """Draw the current view into a rectangle: text rows, then this pane's status line.
        Returns where the cursor goes (screen coordinates), or None."""
        b = self.buf
        H, W = self.scr.getmaxyx()
        self.rows = max(1, height - 1)
        textw = min(MAX_TEXT_WIDTH, width - 1 - LEFT_PAD)
        self.left = left_x + LEFT_PAD  # a little room between the border and the text
        self.wrapw = max(10, textw - 1)
        if self.free_view:  # keep the scrolled view; just make sure it's still inside the note
            self.top = min(self.top, len(b.lines) - 1)
            self.top_seg = min(self.top_seg, len(self.segs(self.top)) - 1)
        else:
            self.scroll()

        ck, _ = self.seg_of(b.cy, b.cx)
        q = self.search.lower()
        cursor = None
        on_typo = False
        sel = self.selection()
        self.row_map = []
        y, i, s = 0, self.top, self.top_seg
        while y < self.rows and i < len(b.lines):
            line = b.lines[i]
            starts = self.segs(i)
            attrs = self.highlight(line)
            typos = self.misspelt(line)
            if typos:
                attrs = attrs[:]
                for a, e in typos:
                    if i == b.cy and e == b.cx:
                        continue  # still typing this word
                    attrs[a:e] = [self.A["spell"]] * (e - a)
                    if i == b.cy and a <= b.cx < e:
                        on_typo = True
            flash = self.flash[:2] if self.flash and time.time() < self.flash[2] else None
            for span, look in ((sel, "sel"), (flash, "copied")):
                if span and span[0][0] <= i <= span[1][0]:
                    attrs = attrs[:]
                    s0 = span[0][1] if i == span[0][0] else 0
                    s1 = span[1][1] if i == span[1][0] else len(line)
                    attrs[s0:s1] = [self.A[look]] * (s1 - s0)
            if q and q in line.lower():
                attrs = attrs[:]
                low, j = line.lower(), line.lower().find(q)
                while j >= 0:
                    attrs[j:j + len(q)] = [self.A["match"]] * len(q)
                    j = low.find(q, j + len(q))
            for k in range(s, len(starts)):
                if y >= self.rows:
                    break
                a = starts[k]
                e = starts[k + 1] if k + 1 < len(starts) else len(line)
                j = a
                while j < e:
                    r = j
                    while r < e and attrs[r] == attrs[j]:
                        r += 1
                    self.put(top_y + y, self.left + j - a, line[j:r], attrs[j])
                    j = r
                if i == b.cy and k == ck:
                    cursor = (top_y + y, self.left + b.cx - a)
                self.row_map.append((i, a, e, k == len(starts) - 1))
                y += 1
            i, s = i + 1, 0

        # Status bar
        path = b.path
        name = os.path.relpath(path, NOTES_DIR) if path.startswith(NOTES_DIR + os.sep) else path
        words = b.word_count()
        left = f" {name}  {'● unsaved' if b.dirty else '✓ saved'}"
        if active and time.time() < self.msg_until:
            left += f"   {self.msg}"
        elif active and on_typo:
            left += "   ^W fix spelling"
        elif active and self.speller and self.speller.state == "compiling":
            left += "   preparing spell check…"
        right = f"Ln {b.cy + 1}/{len(b.lines)}   {words:,} words "
        barw = width - (1 if left_x + width >= W else 0)  # never write the screen's last cell
        bar = left + " " * max(1, barw - len(left) - len(right)) + right
        look = ("match" if self.save_failed else "bar") if active else "bar_off"
        self.put(top_y + height - 1, left_x, bar[:barw].ljust(barw), self.A[look])
        return cursor if active else None

    # Input helpers
    def get_key(self):
        try:
            return self.scr.get_wch()
        except curses.error:
            return None

    def read_escape(self):
        """After ESC: ('paste', text), ('alt', 'b'|'f'), ('key', name) or None for a plain Esc."""
        self.scr.timeout(ESCAPE_WAIT)
        try:
            first = self.get_key()
            if first in ("\r", "\n"):
                return ("key", "shift-enter")  # Option+Enter on terminals using Option as Meta
            if first in ("b", "f", "e", "d", "D", "w", "o", "\x7f", "\x08"):
                return ("alt", first)
            if first not in ("[", "O"):
                return None
            seq = first
            while len(seq) < 24:
                ch = self.get_key()
                if not isinstance(ch, str):
                    break
                seq += ch
                if "@" <= ch <= "~":  # final byte of the sequence
                    break
            if seq == "[200~":
                return ("paste", self.read_paste())
            m = re.fullmatch(r"\[<(\d+);(\d+);(\d+)([Mm])", seq)  # SGR mouse report
            if m:
                return ("mouse", (int(m.group(1)), int(m.group(2)) - 1, int(m.group(3)) - 1, m.group(4) == "M"))
            if seq not in CSI_KEYS:
                self.log_once("unrecognised key sequence ESC" + repr(seq)[1:-1])
            return ("key", CSI_KEYS.get(seq))  # None: a key we don't use, swallowed whole
        finally:
            self.scr.timeout(1000)

    def read_paste(self):
        chars, end = [], list("\x1b[201~")
        self.scr.timeout(500)
        while True:
            ch = self.get_key()
            if ch is None:
                break
            if isinstance(ch, int):
                if ch != curses.KEY_ENTER:
                    continue
                ch = "\n"
            chars.append(ch)
            if chars[-6:] == end:
                del chars[-6:]
                break
        return "".join(chars)  # raw: tabs matter for tables; callers clean it

    def prompt(self, label, text=""):
        while True:
            H, W = self.scr.getmaxyx()
            shown = (label + text)[-(W - 2):]
            self.scr.move(H - 1, 0)
            self.scr.clrtoeol()
            self.put(H - 1, 1, shown, self.A["h"])
            self.scr.move(H - 1, min(1 + len(shown), W - 1))
            self.scr.refresh()
            k = self.get_key()
            if k is None:
                continue
            if k in ENTER:
                return text
            if k == "\x1b":
                r = self.read_escape()
                if r is None:
                    return None
                if r[0] == "paste":
                    text += clean_text(r[1]).split("\n")[0]
            elif k in ("\x03", "\x07", "\x11"):
                return None
            elif k in BACKSPACE:
                text = text[:-1]
            elif isinstance(k, str) and k.isprintable():
                text += k

    def pick(self, title, build, empty_hint="", guide=""):
        """Full-screen filterable list. build(query) -> [(label, value)]. Returns value or None."""
        query, sel, off = "", 0, 0
        while True:
            items = build(query)
            sel = max(0, min(sel, len(items) - 1))
            H, W = self.scr.getmaxyx()
            top = 4 if guide else 3  # first row of the list
            listh = max(1, H - top - 2)
            off = min(off, sel)
            off = max(off, sel - listh + 1)
            self.scr.erase()
            self.put(0, 1, title[:W - 2], self.A["h1"])
            self.put(1, 1, ("Filter: " + query)[:W - 2])
            if guide:
                self.put(2, 1, guide[:W - 2], self.A["dim"])
            hint = empty_hint(query) if callable(empty_hint) else empty_hint
            if not items and hint:
                self.put(top, 1, hint[:W - 2], self.A["dim"])
            for r, (label, _) in enumerate(items[off:off + listh]):
                attr = self.A["sel"] if off + r == sel else self.A["text"]
                self.put(top + r, 0, (" " + label)[:W - 1].ljust(W - 1), attr)
            self.put(H - 1, 1, "type to filter · ↑↓ choose · Enter select · Esc cancel"[:W - 3], self.A["dim"])
            self.scr.move(1, min(9 + len(query), W - 1))
            self.scr.refresh()

            k = self.get_key()
            if k is None or k == curses.KEY_RESIZE:
                continue
            if k in ENTER:
                return items[sel][1] if items else None
            if k == "\x1b":
                r = self.read_escape()
                if r is None:
                    return None
                if r[0] == "paste":
                    query, sel = query + clean_text(r[1]).split("\n")[0], 0
                if r[0] == "key" and isinstance(r[1], int):
                    k = r[1]  # arrows the terminal sent as ESC [ A etc.
            if k in ("\x03", "\x11"):
                return None
            elif k == curses.KEY_UP:
                sel -= 1
            elif k == curses.KEY_DOWN:
                sel += 1
            elif k == curses.KEY_PPAGE:
                sel -= listh
            elif k == curses.KEY_NPAGE:
                sel += listh
            elif k in BACKSPACE:
                query, sel = query[:-1], 0
            elif isinstance(k, str) and k.isprintable():
                query, sel = query + k, 0

    # Commands
    def move_vertical(self, delta):
        b = self.buf
        k, starts = self.seg_of(b.cy, b.cx)
        if self.want_x is None:
            self.want_x = b.cx - starts[k]
        for _ in range(abs(delta)):
            if delta < 0:
                if k > 0:
                    k -= 1
                elif b.cy > 0:
                    b.cy -= 1
                    starts = self.segs(b.cy)
                    k = len(starts) - 1
                else:
                    b.cx = 0
                    return
            else:
                if k < len(starts) - 1:
                    k += 1
                elif b.cy < len(b.lines) - 1:
                    b.cy += 1
                    starts = self.segs(b.cy)
                    k = 0
                else:
                    b.cx = len(b.lines[b.cy])
                    return
        end = starts[k + 1] - 1 if k + 1 < len(starts) else len(b.lines[b.cy])
        b.cx = min(starts[k] + self.want_x, end)

    def misspelt(self, line):
        """(start, end) of words to mark as misspelt; skips names, citations and legal Latin."""
        bad = self.speller.lookup(line) if self.speller else ()
        if not bad:
            return []
        hl = self.highlight(line)
        out = []
        for a, n in bad:
            e = min(a + n, len(line))
            word = line[a:e]
            if (not word or word[0].isupper() or any(c.isdigit() for c in word)
                    or word.lower() in LAW_WORDS or word.lower() in self.ignored
                    or any(pair_of(hl[k]) in self.protected for k in range(a, e))):
                continue
            out.append((a, e))
        return out

    def fix_spelling(self):
        sp = self.speller
        if not sp or sp.state == "off":
            self.say("Spell check isn't available on this computer")
            return
        if sp.state != "ready":
            self.say("Spell check is still starting — try again in a moment")
            return
        b = self.buf
        sp.wait_for(b.lines)
        n, target = len(b.lines), None
        for step in range(n + 1):  # from the cursor onwards, wrapping round
            i = (b.cy + step) % n
            for a, e in self.misspelt(b.lines[i]):
                if (step == 0 and e < b.cx) or (step == n and a >= b.cx):
                    continue
                target = (i, a, e)
                break
            if target:
                break
        if not target:
            self.say("No spelling mistakes found ✓")
            return
        i, a, e = target
        b.cy, b.cx = i, e
        word, line = b.lines[i][a:e], b.lines[i]
        guesses = ((sp.call("suggest", word) or {}).get("words") or [])[:10]
        items = [(g, ("replace", g)) for g in guesses]
        items += [(f"+ Add “{word}” to my dictionary", ("learn", word)),
                  (f"  Ignore “{word}” in this session", ("ignore", word))]

        def build(query):
            hits = [it for it in items if query.lower() in it[0].lower()]
            return hits + ([(f"Replace with “{query}”", ("replace", query))] if query.strip() else [])

        context = line[max(0, a - 25):e + 25].strip()
        choice = self.pick(f"Spelling: “{word}”  in  …{context}…", build, "Type the correct spelling and press Enter.")
        if not choice:
            return
        action, value = choice
        if action == "replace":
            b.checkpoint("spell")
            b.lines[i] = line[:a] + value + line[e:]
            b.cx = a + len(value)
        elif action == "learn":
            sp.call("learn", word)
            sp.cache.clear()
            self.say(f"Added “{word}” to your Mac's dictionary")
        else:
            self.ignored.add(word.lower())

    def show_help(self):
        top = 0
        while True:
            H, W = self.scr.getmaxyx()
            keyw = min(13, max(8, W // 4))
            rows = []  # (text, attr, key, x) after wrapping to the window
            for key, desc in HELP:
                if key == "#":
                    rows += [("", 0, "", 1), (desc, self.A["h1"], "", 1)]
                    continue
                x = keyw + 2 if key else 1
                for n, part in enumerate(textwrap.wrap(desc, max(10, W - x - 1))):
                    rows.append((part, self.A["text"], key if n == 0 else "", x))
            view = max(1, H - 3)
            top = max(0, min(top, len(rows) - view))
            self.scr.erase()
            self.put(0, 1, HELP_TITLE[:W - 2], self.A["h1"])
            for r, (text, attr, key, x) in enumerate(rows[top:top + view]):
                if key:
                    self.put(r + 1, 1, key[:keyw], self.A["key"])
                self.put(r + 1, x, text, attr)
            more = "↑↓ scroll · " if len(rows) > view else ""
            self.put(H - 1, 1, (more + "any other key closes help")[:W - 2], self.A["dim"])
            self.scr.refresh()
            k = self.get_key()
            if k is None or k == curses.KEY_RESIZE:
                continue
            if k == "\x1b":
                r = self.read_escape()
                if r and r[0] == "key" and r[1] in (curses.KEY_UP, curses.KEY_DOWN):
                    k = r[1]
                else:
                    return
            if k == curses.KEY_UP:
                top -= 1
            elif k == curses.KEY_DOWN:
                top += 1
            elif k == curses.KEY_PPAGE:
                top -= view
            elif k == curses.KEY_NPAGE:
                top += view
            else:
                return

    def all_notes(self):
        """[(path, relative name, lines)] for every note; the open note uses its unsaved text."""
        out, current = [], os.path.realpath(self.buf.path)
        for _, rel, path in self.list_notes():
            if os.path.realpath(path) == current:
                out.append((path, rel, self.buf.lines))
                current = None
            else:
                try:
                    out.append((path, rel, Buffer(path).lines))
                except OSError:
                    pass
        if current:  # a note opened from outside the notes folder
            out.insert(0, (self.buf.path, os.path.basename(self.buf.path), self.buf.lines))
        return out

    def goto(self, path, line, col=0):
        if os.path.realpath(path) != os.path.realpath(self.buf.path) and not self.open(path):
            return
        b = self.buf
        b.cy = min(line, len(b.lines) - 1)
        b.cx = min(col, len(b.lines[b.cy]))

    @staticmethod
    def snippet(line, j, n):
        a = max(0, j - 30)
        return ("…" if a else "") + line[a:j + n + 50].strip()

    def search_all(self):
        notes = self.all_notes()

        def build(query):
            q = query.strip().lower()
            if len(q) < 2:
                return []
            items = []
            for path, rel, lines in notes:
                for i, line in enumerate(lines):
                    j = line.lower().find(q)
                    if j >= 0:
                        items.append((f"{rel}:{i + 1}   {self.snippet(line, j, len(q))}", (path, i, j, query.strip())))
                        if len(items) >= 400:
                            return items
            return items

        choice = self.pick(f"Search all notes ({len(notes)})", build,
                           lambda q: "Type at least two letters." if len(q.strip()) < 2 else "No matches.")
        if choice:
            path, i, j, q = choice
            self.goto(path, i, j)
            self.search = q  # highlight it in the note; Esc clears

    def case_index(self):
        cases = {}
        for path, rel, lines in self.all_notes():
            for i, line in enumerate(lines):
                for m in CASE_RE.finditer(line):
                    name = " ".join(m.group().split())
                    c = cases.setdefault(name.lower(), {"name": name, "cite": "", "hits": []})
                    if not c["cite"]:
                        cm = CITE_RE.match(line, m.end() + len(line[m.end():]) - len(line[m.end():].lstrip(" ,")))
                        c["cite"] = cm.group() if cm else ""
                    c["hits"].append((path, rel, i, m.start(), len(m.group())))
        entries = []
        for c in sorted(cases.values(), key=lambda c: c["name"].lower()):
            notes = len({h[1] for h in c["hits"]})
            label = f"{c['name']} {c['cite']}".strip() + f"   · {notes} note{'s' * (notes != 1)}"
            entries.append((label, c))

        def build(query):
            words = query.lower().split()
            return [e for e in entries if all(w in e[0].lower() for w in words)]

        c = self.pick(f"Case index — {len(entries)} cases across all notes", build,
                      "No cases found yet. Case names like  Donoghue v Stevenson  are picked up automatically.")
        if not c:
            return
        hits = c["hits"]
        if len(hits) > 1:
            lines_by_path = {p: l for p, _, l in self.all_notes()}
            items = [(f"{rel}:{i + 1}   {self.snippet(lines_by_path.get(path, [''] * (i + 1))[i], j, n)}", h)
                     for h in hits for path, rel, i, j, n in [h]]
            hit = self.pick(f"{c['name']} — where it's mentioned", lambda q: [
                it for it in items if q.lower() in it[0].lower()])
            if not hit:
                return
        else:
            hit = hits[0]
        path, _, i, j, _ = hit
        self.goto(path, i, j)

    def history_menu(self):
        b, h = self.buf, self.history
        if not h.ok:
            self.say("Version history needs git, which isn't installed")
            return
        if not h.rel(b.path):
            self.say("Version history only covers notes in the notes folder")
            return
        self.safe_save()
        h.snapshot(wait=True)  # so the list includes the latest version
        versions = h.versions(b.path)[:40]
        current = b.lines
        items = []
        for n, (commit, when, mac) in enumerate(versions):
            text = h.content(commit, b.path)
            if text is None:
                continue
            lines = text.split("\n")
            if len(lines) > 1 and lines[-1] == "":
                lines.pop()
            changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in
                          difflib.SequenceMatcher(None, lines, current, autojunk=False).get_opcodes() if tag != "equal")
            stamp = datetime.datetime.fromtimestamp(when).strftime("%a %d %b %H:%M")
            words = sum(len(l.split()) for l in lines)
            diff = "same as now" if not changed else f"{changed} line{'s' * (changed != 1)} different"
            where = f" · from {mac.replace('_', ' ')}" if mac else ""
            items.append((f"{stamp}   {words:,} words · {diff}{where}", (lines, stamp)))
        if not items:
            self.say("No earlier versions yet — snapshots are taken every few minutes while you work")
            return
        choice = self.pick("Version history of this note — Enter restores (^Z undoes)", lambda q: [
            it for it in items if q.lower() in it[0].lower()])
        if choice:
            lines, stamp = choice
            b.checkpoint("restore")
            b.lines = lines[:] or [""]
            b.cy = min(b.cy, len(b.lines) - 1)
            b.cx = min(b.cx, len(b.lines[b.cy]))
            self.say(f"Restored the version from {stamp} · ^Z to undo", 8)

    def find(self, again=False):
        if not (again and self.search):
            text = self.prompt("Find: ", self.search)
            if not text:
                return
            self.search = text
        b, q = self.buf, self.search.lower()
        n = len(b.lines)
        for step in range(n + 1):
            i = (b.cy + step) % n
            line = b.lines[i].lower()
            j = line.find(q, b.cx + 1) if step == 0 else line.find(q)
            if j >= 0:
                wrapped = step > 0 and i <= b.cy
                b.cy, b.cx = i, j
                if wrapped:
                    self.say("Search wrapped to top")
                return
        self.say(f"“{self.search}” not found")

    def outline(self):
        entries, seen = [], set()
        for i, line in enumerate(self.buf.lines):
            m = HEADING_RE.match(line)
            if m:
                entries.append(("  " * (len(m.group(1)) - 1) + line[m.end():].strip(), (i, 0)))
                continue
            for cm in CASE_RE.finditer(line):
                name = cm.group()
                if name not in seen:
                    seen.add(name)
                    entries.append(("      ⚖  " + name, (i, cm.start())))

        def build(query):
            words = query.lower().split()
            return [e for e in entries if all(w in e[0].lower() for w in words)]

        target = self.pick("Outline — headings and cases in this note", build,
                           "No headings or cases found yet.")
        if target:
            self.buf.cy, self.buf.cx = target

    def insert_template(self):
        choice = self.pick("Insert template", lambda q: [
            (name, lines) for name, lines in TEMPLATES if q.lower() in name.lower()])
        if not choice:
            return
        b = self.buf
        today = datetime.date.today().strftime("%a %d %B %Y")
        lines = [l.replace("{date}", today) for l in choice]
        b.checkpoint("template")
        if b.lines[b.cy].strip():
            b.lines[b.cy + 1:b.cy + 1] = [""] + lines
            b.cy += 2
        else:
            b.lines[b.cy:b.cy + 1] = lines
        b.cx = len(b.lines[b.cy])
        self.say("Type the heading, then ↓ to fill in each line")

    def new_note(self):
        name = self.prompt("New note (Folder/Name makes the folder, e.g. Contract/Offer): ")
        if not name or not name.strip("/ "):
            return
        try:
            self.open(note_path(name))
        except ValueError as e:
            self.say(str(e), 6)

    # Selection, clipboard, mouse
    def selection(self):
        """((line, col), (line, col)) in order, or None when nothing is selected."""
        if self.anchor is None:
            return None
        b = self.buf
        ay = min(self.anchor[0], len(b.lines) - 1)
        a, c = (ay, min(self.anchor[1], len(b.lines[ay]))), (b.cy, b.cx)
        if a == c:
            return None
        return (a, c) if a < c else (c, a)

    def selected_text(self):
        (y1, x1), (y2, x2) = self.selection()
        lines = self.buf.lines
        if y1 == y2:
            return lines[y1][x1:x2]
        return "\n".join([lines[y1][x1:], *lines[y1 + 1:y2], lines[y2][:x2]])

    def delete_selection(self):
        (y1, x1), (y2, x2) = self.selection()
        b = self.buf
        b.checkpoint("cut")
        b.lines[y1:y2 + 1] = [b.lines[y1][:x1] + b.lines[y2][x2:]]
        b.cy, b.cx = y1, x1
        self.anchor = None

    def export(self):
        choice = self.pick("Export this note — saved to " + EXPORT_DIR.replace(os.path.expanduser("~"), "~", 1),
                           lambda q: [(label, fmt) for label, fmt in (
                               ("Word document (.docx) — footnotes stay footnotes", "docx"),
                               ("PDF (A4, Times 12, footnotes as endnotes)", "pdf"))
                               if q.lower() in label.lower()])
        if not choice:
            return
        self.safe_save()
        self.say("Exporting…", 30)
        self.draw()
        try:
            out = export_note(self.buf.path, self.buf.lines, choice)
            self.say("Exported to " + out.replace(os.path.expanduser("~"), "~", 1), 10)
            log(f"exported {os.path.basename(self.buf.path)} as {choice}")
        except (ExportError, OSError, subprocess.SubprocessError) as e:
            self.say(f"Export failed: {e}", 12)

    def wrap_selection(self, mark):
        (y1, x1), (y2, x2) = self.selection()
        b = self.buf
        b.checkpoint("wrap")
        b.lines[y2] = b.lines[y2][:x2] + mark + b.lines[y2][x2:]
        b.lines[y1] = b.lines[y1][:x1] + mark + b.lines[y1][x1:]
        self.anchor = (y1, x1 + 1)  # select the same words, now inside the marks
        b.cy, b.cx = y2, x2 + (1 if y1 == y2 else 0)
        self.want_x = None

    def copy(self, cut=False):
        b = self.buf
        if self.selection():
            text = self.selected_text()
        else:  # nothing selected: the whole line
            text = b.lines[b.cy] + "\n"
        self.clip_text = text
        where = "clipboard" if clipboard_set(text) else "editor clipboard"
        if not cut:  # flash what was copied green (a whole line isn't selected, so typing can't replace it)
            span = self.selection() or ((b.cy, 0), (b.cy, len(b.lines[b.cy])))
            self.flash = (span[0], span[1], time.time() + 0.7)
        words = len(text.split())
        if cut:
            if self.selection():
                self.delete_selection()
            else:
                b.cut_line()
        what = f"{words} word{'s' * (words != 1)}" if words else "it"
        self.say(f"✓ {'Cut' if cut else 'Copied'} {what} to the {where}" + ("" if cut else " — paste with ^V or ⌘V"), 5)

    def paste_clipboard(self):
        text = clipboard_get()
        self.paste_text(text if text is not None else getattr(self, "clip_text", ""))

    def paste_text(self, raw):
        """Paste (⌘V or ^V): a table from Word becomes a Markdown table on its own lines."""
        b = self.buf
        table = table_from_text(raw)
        if table:
            before = b.lines[b.cy][:b.cx]
            b.insert(("\n\n" if before.strip() else "") + table + "\n", kind="paste")
            rows = table.count("\n") - 1
            self.say(f"Pasted a table: {rows} row{'s' * (rows != 1)} (exports to Word as a real table)", 6)
            return
        text = clean_text(raw)
        if text:
            b.insert(text, kind="paste")

    def screen_to_buf(self, y, x):
        if not self.row_map or not 0 <= y < self.rows:
            return None
        if y >= len(self.row_map):  # below the text: end of the last line
            i = self.row_map[-1][0]
            return i, len(self.buf.lines[i])
        i, a, e, last = self.row_map[y]
        return i, min(a + max(0, x - self.left), e if last else max(a, e - 1))

    def mouse(self, button, x, y, pressed):
        """Click places the cursor, drag selects, Shift+click extends, wheel scrolls.
        Returns True to keep the selection."""
        if self.resizing is not None:  # dragging a pane border
            if button & 32:
                self.resizing.drag_to(y, x)
            elif not pressed:
                self.resizing = None
            return self.anchor is not None
        if pressed and not button & (3 | 32 | 64):
            for kind, node, by, bx, size in self.borders:
                if (kind == "v" and x == bx and by <= y < by + size) or \
                        (kind == "h" and y == by and bx <= x < bx + size):
                    self.resizing = node  # grabbed a border: drag to resize
                    return self.anchor is not None
        pane = self.pane_at(y, x)
        if pane is None:
            return self.anchor is not None
        if pane is not self.pane:
            self.activate(pane)  # clicking or scrolling a pane makes it the active one
        y -= pane.rect[0]
        b = self.buf
        if button & 64:  # wheel (64 up, 65 down): move the view only, the cursor stays put
            self.scroll_view(3 if button & 1 else -3)
            return self.anchor is not None
        if button & 3:  # middle or right button: ignore
            return self.anchor is not None
        pos = self.screen_to_buf(y, x)
        if pos is None:
            return self.anchor is not None
        if button & 32:  # dragging with the button held
            self.hold_selection = False
            b.cy, b.cx = pos
            return True
        if pressed:
            if button & 4:  # Shift+click extends the selection
                self.anchor = self.anchor or (b.cy, b.cx)
                b.cy, b.cx = pos
                return True
            now, (t, p, n) = time.time(), self.last_click
            n = n + 1 if now - t < 0.4 and p and p[0] == pos[0] and abs(p[1] - pos[1]) <= 1 else 1
            self.last_click = (now, pos, n)
            if n >= 2:  # double click: a word (or a whole case name); triple: the line
                span = self.word_at(*pos) if n == 2 else (0, len(b.lines[pos[0]]))
                if span:
                    self.anchor = (pos[0], span[0])
                    b.cy, b.cx = pos[0], span[1]
                    self.hold_selection = True
                    return True
            self.anchor = pos
            b.cy, b.cx = pos
            return True
        if self.hold_selection:  # release after a double or triple click
            self.hold_selection = False
            return True
        b.cy, b.cx = pos  # released
        return self.anchor is not None and self.anchor != pos

    def word_at(self, y, x):
        """What a double click selects: a case name, citation or statute if x is inside one,
        else the word under x. (start, end) or None."""
        line = self.buf.lines[y]
        for rx in (CASE_RE, CITE_RE, STATUTE_RE, re.compile(r"[\w\u2019'-]+")):
            for m in rx.finditer(line):
                if m.start() <= x < m.end():
                    return m.start(), m.end()
        return None

    def scroll_view(self, rows):
        self.free_view = True
        i, s = self.top, self.top_seg
        for _ in range(abs(rows)):
            if rows > 0:
                if s < len(self.segs(i)) - 1:
                    s += 1
                elif i < len(self.buf.lines) - 1:
                    i, s = i + 1, 0
            elif s > 0:
                s -= 1
            elif i > 0:
                i -= 1
                s = len(self.segs(i)) - 1
        self.top, self.top_seg = i, s

    def normalise(self, key):
        """One key name for every way a terminal can send it."""
        if key == "\x1b":
            r = self.read_escape()
            if r is None:
                return "esc"
            if r[0] in ("paste", "mouse"):
                return r
            if r[0] == "alt":
                return {"b": "word-left", "f": "word-right", "e": "export", "d": "split-right",
                        "D": "split-down", "w": "close-pane", "o": "next-pane",
                        "\x7f": "delete-word-left", "\x08": "delete-word-left"}[r[1]]
            return r[1]  # a curses key, a name, or None for keys we don't use
        if isinstance(key, int) and key != curses.KEY_RESIZE:
            return NAMED_KEYS.get(curses.keyname(key).decode(errors="ignore"), key)
        return key

    def handle(self, key):
        key = self.normalise(key)
        if key is None:
            return
        b = self.buf
        self.flash = None
        if not (isinstance(key, tuple) and key[0] == "mouse" and key[1][0] & 64):
            self.free_view = False  # anything but the wheel brings the view back to the cursor
        extend = key in SELECT_MOVES
        if extend:
            key = SELECT_MOVES[key]
            if self.anchor is None:
                self.anchor = (b.cy, b.cx)
        keep_selection = extend
        vertical = key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE)
        was_cutting, self.cutting = self.cutting, False

        # * or _ with text selected wraps it (Markdown italic; again for bold) and keeps it selected
        if key in ("*", "_") and self.selection():
            self.wrap_selection(key)
            return

        # Typing, Enter, paste or Backspace replace selected text
        deleting = key in BACKSPACE or key in (curses.KEY_DC, "\x04", "delete-word-left", "delete-word-right")
        typing = ((isinstance(key, tuple) and key[0] == "paste") or key in ENTER or key in ("shift-enter", "\t", "\x16")
                  or (isinstance(key, str) and len(key) == 1
                      and (key.isprintable() or unicodedata.category(key) == "Zs")))
        if (typing or deleting) and self.selection():
            self.delete_selection()
            if deleting:
                key = None

        if key is None:
            pass
        elif isinstance(key, tuple) and key[0] == "mouse":
            self.log_once("mouse events are arriving")
            keep_selection = self.mouse(*key[1])
        elif isinstance(key, tuple):  # bracketed paste (⌘V)
            self.paste_text(key[1])
        elif key == "esc":
            self.search = ""
        elif key == "shift-enter":
            b.newline(plain=True)
        elif key == "delete-word-left":
            b.delete_word_left()
        elif key == "delete-word-right":
            b.delete_word_right()
        elif key == "word-left":
            b.word_left()
        elif key == "word-right":
            b.word_right()
        elif key in ENTER:
            b.newline()
        elif key in BACKSPACE:
            b.backspace()
        elif key == curses.KEY_DC or key == "\x04":
            b.delete()
        elif key == "\t":
            if BULLET_RE.match(b.lines[b.cy]):
                b.indent()
            else:
                b.insert("    ", kind="tab")
        elif key == curses.KEY_BTAB:
            b.outdent()
        elif key == "\x13":
            self.safe_save(announce=True)
        elif key == "\x11":
            if all(self.save_pane_buffer(p) for p, _ in self.buffers()):
                return "quit"
            self.say("Not quitting: the note couldn't be saved. " + self.msg, 3600)
        elif key == "\x03":
            self.copy()
            keep_selection = True
        elif key == "\x18":
            self.copy(cut=True)
        elif key == "\x16":
            self.paste_clipboard()
        elif key == "\x10":
            self.search_all()
        elif key == "\x02":
            self.case_index()
        elif key == "\x12":
            self.history_menu()
        elif key == "\x0f":
            self.open_picker()
        elif key == "\x0e":
            self.new_note()
        elif key == "\x06":
            self.find()
        elif key in ("\x07", curses.KEY_F1):
            self.show_help()
        elif key == curses.KEY_F3:
            self.find(again=True)
        elif key == "\x17":
            self.fix_spelling()
        elif key == "\x14":
            self.insert_template()
        elif key == "split-right":
            self.split("v")
        elif key == "split-down":
            self.split("h")
        elif key == "close-pane":
            self.close_pane()
        elif key == "next-pane":
            self.next_pane()
        elif key in ("export", curses.KEY_F5):
            self.export()
        elif key == "\x0c":
            self.outline()
        elif key == "\x0b":
            if self.selection():
                self.copy(cut=True)
            else:
                line = b.cut_line()
                self.clip = (self.clip if was_cutting else []) + [line]
                self.cutting = True
        elif key == "\x15":
            if self.clip:
                b.cx = 0
                b.insert("\n".join(self.clip) + "\n", kind="paste")
        elif key == "\x1a":
            if not b.undo():
                self.say("Nothing to undo")
        elif key == "\x19":
            if not b.redo():
                self.say("Nothing to redo")
        elif key in ("\x01", curses.KEY_HOME):
            b.cx = 0
        elif key in ("\x05", curses.KEY_END):
            b.cx = len(b.lines[b.cy])
        elif key == curses.KEY_LEFT:
            b.left()
        elif key == curses.KEY_RIGHT:
            b.right()
        elif key == curses.KEY_UP:
            self.move_vertical(-1)
        elif key == curses.KEY_DOWN:
            self.move_vertical(1)
        elif key == curses.KEY_PPAGE:
            self.move_vertical(-(self.rows - 1))
        elif key == curses.KEY_NPAGE:
            self.move_vertical(self.rows - 1)
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            b.insert(key)
        elif isinstance(key, str) and len(key) == 1 and unicodedata.category(key) == "Zs":
            b.insert(" ")  # e.g. Option+Space types a non-breaking space

        if not keep_selection:
            self.anchor = None
        if not vertical:
            self.want_x = None

    def run(self, path):
        # Closing the pane, quitting herdr or closing Terminal sends SIGHUP: save on the way out
        def stop(signum, frame):
            # Closing a window or pane can deliver SIGHUP twice (the terminal hanging up, and
            # whatever closed it); a second one must not interrupt the save on the way out.
            for sig in (signal.SIGHUP, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            self.exit_reason = {signal.SIGHUP: "window or pane closed (SIGHUP)",
                                signal.SIGTERM: "asked to quit (SIGTERM)"}.get(signum, f"signal {signum}")
            raise SystemExit(0)
        for sig in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, stop)
        # Bracketed paste (pasted lists aren't re-bulleted) and modifyOtherKeys 1 (Shift+Enter differs from Enter)
        sys.stdout.write("\x1b[?2004h\x1b[>4;1m")
        if MOUSE_ON:  # button presses and drags, in the SGR format (wheel-down works, unlike X10)
            sys.stdout.write("\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        sys.stdout.flush()
        write_running()
        self.exit_reason = "unknown"
        H, W = self.scr.getmaxyx()
        where = (f"herdr pane {os.environ['HERDR_PANE_ID']}" if os.environ.get("HERDR_PANE_ID")
                 else os.environ.get("TERM_PROGRAM", "a terminal"))
        log(f"started Law Notes {VERSION} in {where}, {W}x{H}")
        try:
            if not path:
                path = take_request()  # the launcher asked for a note before this window opened
            if path:
                self.open(path)
            elif not self.open_picker():
                self.exit_reason = "note list closed before opening a note (Esc or ^Q)"
                return
            if time.time() >= self.msg_until:
                self.say("Tip: ^G shows every shortcut")
            while True:
                self.check_inbox()
                if self.speller:
                    self.speller.poll()
                self.draw()
                busy = self.speller and self.speller.busy
                self.scr.timeout(50 if busy or self.flash else 1000)  # redraw promptly when a flash ends
                key = self.get_key()
                self.scr.timeout(1000)
                if key is None:
                    if self.flash and time.time() >= self.flash[2]:
                        self.flash = None
                    for pane, _ in self.buffers():
                        with self.viewing(pane):
                            self.autosave()
                            self.check_outside_change()
                    continue
                # Apply everything already typed before redrawing, so fast typing never queues
                quit = False
                for _ in range(500):
                    if self.handle(key) == "quit":
                        quit = True
                        break
                    self.scr.timeout(0)
                    key = self.get_key()
                    self.scr.timeout(1000)
                    if key is None:
                        break
                if quit:
                    self.exit_reason = "quit with ^Q"
                    break
                self.autosave()
        finally:
            for pane, b in (self.buffers() if self.buf else []):
                with self.viewing(pane):
                    self.remember_position()
                    if self.buf.dirty and not self.safe_save():
                        self.write_recovery()
            self.history.snapshot(wait=True)
            clear_running()
            if sys.exc_info()[0] not in (None, SystemExit):
                self.exit_reason = "crashed (see the traceback below)"
            log("exited: " + self.exit_reason)
            if self.speller:
                self.speller.close()
            try:
                sys.stdout.write("\x1b[?2004l\x1b[>4;0m\x1b[?1006l\x1b[?1002l\x1b[?1000l")
                sys.stdout.flush()
            except OSError:  # the terminal is gone: don't let Python fail flushing it at exit
                sys.stdout = open(os.devnull, "w")


def log(event, detail=""):
    """Append to this Mac's Law Notes log, locally and in iCloud Drive (if it's on), so a close
    or crash can be explained afterwards. Never raises; keeps each file under ~200 KB."""
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {event}\n" + detail
    targets = [LOG_FILE]
    if os.path.isdir(ICLOUD_DIR):
        targets.append(os.path.join(ICLOUD_LOGS, History.mac_name() + ".log"))
    for path in targets:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 200_000:
                with open(path) as f:
                    keep = f.read()[-100_000:]
                with open(path, "w") as f:
                    f.write(keep)
            with open(path, "a") as f:
                f.write(line)
        except OSError:
            pass


def inside_notes(path):
    notes = os.path.realpath(NOTES_DIR)
    return os.path.realpath(path).startswith(notes + os.sep)


def note_path(name):
    """A note name ('Tort/Duty') as a path in the notes folder. Raises ValueError for names that
    would leave the folder ('../../x') or contain control characters."""
    if CONTROL_RE.search(name):
        raise ValueError("a note name can't contain control characters")
    path = os.path.normpath(os.path.join(NOTES_DIR, name.strip().strip("/")))
    if not path.endswith(NOTE_EXTS):
        path += ".md"
    if not inside_notes(path):
        raise ValueError(f"“{name}” would be outside the notes folder")
    return path


def run_curses(fn):
    """Like curses.wrapper, but restoring the terminal can't fail: after the window or pane is
    closed (SIGHUP) the terminal is gone, and nocbreak()/endwin() return ERR."""
    scr = curses.initscr()
    try:
        curses.noecho()
        curses.cbreak()
        scr.keypad(True)
        return fn(scr)
    finally:
        for step in (lambda: scr.keypad(False), curses.echo, curses.nocbreak, curses.endwin):
            try:
                step()
            except curses.error:
                pass


def resolve_note(arg):
    """'Tort/Duty' means ~/UCL/notes/Tort/Duty.md; an existing file path is used as is."""
    if CONTROL_RE.search(arg):
        raise ValueError("a note name can't contain control characters")
    path = os.path.expanduser(arg.strip())
    if not os.path.isabs(path) and not os.path.exists(path):
        return note_path(path)
    return os.path.abspath(path)


def requested_note(req):
    """A note asked for through REQUEST_FILE, which any program on this Mac could write: only
    notes inside the notes folder, or existing .md/.txt files, are accepted."""
    if not (isinstance(req, dict) and isinstance(req.get("note"), str) and req["note"]
            and time.time() - float(req.get("time", 0)) < 120):
        return None
    path = resolve_note(req["note"])
    if inside_notes(path) or (os.path.isfile(path) and path.endswith(NOTE_EXTS)):
        return path
    log("ignored a request to open " + repr(path) + " (not a note)")
    return None


def take_request():
    try:
        with open(REQUEST_FILE) as f:
            req = json.load(f)
        os.remove(REQUEST_FILE)
        return requested_note(req)
    except (OSError, ValueError, TypeError):
        pass
    return None


def write_running():
    """Tell the launcher Law Notes is open here, so it focuses this copy instead of starting another."""
    try:
        tty = os.ttyname(sys.stdin.fileno())
    except OSError:
        tty = ""
    info = {"pid": os.getpid(), "pane": os.environ.get("HERDR_PANE_ID", ""), "tty": tty,
            "terminal": os.environ.get("TERM_PROGRAM", ""), "started": time.time()}
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = RUNNING_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(info, f)
        os.replace(tmp, RUNNING_FILE)
    except OSError:
        pass


def clear_running():
    try:
        with open(RUNNING_FILE) as f:
            if json.load(f).get("pid") == os.getpid():
                os.remove(RUNNING_FILE)
    except (OSError, ValueError):
        pass


def self_test():
    """Quick checks of the parts that keep notes safe; update.py runs this before switching
    to a new version, so a broken release is never used."""
    import tempfile
    results = []

    def check(name, fn):
        try:
            results.append((name, bool(fn()), ""))
        except Exception as e:  # report, don't crash: the point is a clear pass/fail
            results.append((name, False, f"{type(e).__name__}: {e}"))

    with tempfile.TemporaryDirectory() as d:
        note = os.path.join(d, "Tort", "Duty.md")

        def save_and_reload():
            b = Buffer(note)
            b.insert("- Duty: Donoghue v Stevenson [1932] AC 562")
            b.newline()
            b.insert("Caparo")
            b.save()
            return Buffer(note).lines == ["- Duty: Donoghue v Stevenson [1932] AC 562", "- Caparo"]

        def conflict_copy():
            b = Buffer(note)
            time.sleep(0.02)
            with open(note, "w") as f:
                f.write("changed elsewhere\n")
            b.insert("mine ")
            msg = b.save()
            return msg and open(note).read() == "changed elsewhere\n" and os.path.exists(b.path) and b.path != note

        def windows_text():
            p = os.path.join(d, "w.md")
            with open(p, "wb") as f:
                f.write("Lord Atkin\u2019s test".encode("cp1252"))
            return Buffer(p).lines == ["Lord Atkin\u2019s test"]

        check("save and reload a note", save_and_reload)
        check("never overwrite a note changed elsewhere", conflict_copy)
        check("open Windows-encoded notes", windows_text)
        check("keep spaces in pasted citations", lambda: clean_text("v\u00a0Stevenson") == "v Stevenson")
        check("find case names", lambda: [m.group() for m in CASE_RE.finditer(
            "Lord Atkin in Donoghue v Stevenson")] == ["Donoghue v Stevenson"])
        check("wrap lines", lambda: wrap_starts("hello world foo", 8) == (0, 6, 12))
        check("terminal library", lambda: callable(curses.wrapper))
    for name, ok, err in results:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{('  ' + err) if err else ''}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"Law Notes {VERSION} self-test: {'passed' if not failed else f'{failed} failed'}")
    return 0 if not failed else 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "--self-test":
        sys.exit(self_test())
    if args and args[0] == "--export":
        if len(args) != 3 or args[2] not in ("docx", "pdf"):
            print("usage: lawnotes --export NOTE docx|pdf", file=sys.stderr)
            sys.exit(2)
        try:
            note = resolve_note(args[1])
            print(export_note(note, Buffer(note).lines, args[2]))
        except (ExportError, ValueError, OSError) as e:
            print(f"lawnotes: {e}", file=sys.stderr)
            sys.exit(1)
        return
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args and args[0] in ("-V", "--version"):
        print(f"Law Notes {VERSION} (beta)")
        return
    try:
        path = resolve_note(args[0]) if args and args[0].strip() else None
    except ValueError as e:
        print(f"lawnotes: {e}", file=sys.stderr)
        sys.exit(2)
    os.makedirs(NOTES_DIR, exist_ok=True)
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    try:
        run_curses(lambda scr: App(scr).run(path))
    except Exception:
        log("CRASH", traceback.format_exc())
        where = "iCloud Drive → Law Notes Logs" if os.path.isdir(ICLOUD_DIR) else LOG_FILE
        print(f"lawnotes stopped with an error. Your note was saved first if at all possible "
              f"(otherwise see {CACHE_DIR}/recovery). Details are in {where}.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
