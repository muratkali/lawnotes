# Changelog

## 1.2.0

- Export to Word (.docx) or PDF with Option+E (or F5), or `lawnotes --export NOTE docx|pdf`, into Downloads. Case names are put in italics (OSCOLA), `[^1]` footnotes become real Word footnotes, each line of a note stays a line, and Times New Roman 12 with 1.5 spacing is used. Word export needs pandoc (`brew install pandoc`); PDF is made by macOS itself, no LaTeX needed.
- Security: note names can't leave the notes folder or contain control characters; "open this note" requests from other programs only open notes; a release tag moved on GitHub is ignored instead of stopping all updates; releases are signed through the ssh-agent so the release key can have a passphrase; GitHub Actions are pinned to exact commits with read-only permissions.
- New tests: wrong-key and moved-tag releases, hostile note content (terminal control codes), path traversal, hostile requests, 400 rounds of random input, and export.

## 1.1.6

- With text selected, `*` wraps it in `*…*` (italic) and keeps it selected, so a second `*` makes it `**…**` (bold). `_` works the same way.

## 1.1.5

- Double-click selects a word, or a whole case name, citation or statute; triple-click selects the line.
- Scrolling with the mouse moves the view only: the cursor stays where it was, and typing carries on there.
- ^C with nothing selected briefly highlights the line it copied.

## 1.1.4

- Scrolling a note crashed Law Notes when a misspelt word in italics (for example inside a case name) came into view: `curses.pair_number()` overflows on italic text with macOS's ncurses. Fixed, with a test.
- Scrolling could close Law Notes: when a scroll event's first byte arrived more than ~55 ms before the rest (likely through herdr or on a busy Mac), it was read as Esc, which closes the note list. Escape sequences now get 250 ms to arrive.
- A log of every start, every exit and why it happened, crashes with their details, and unrecognised key sequences, kept locally and in iCloud Drive → Law Notes Logs (one file per Mac).
- Party labels are highlighted: D (defendant) orange, V (victim) pink, C (claimant) blue, including D1, D2 and D's.

## 1.1.3

- Law Notes.app's window now starts the editor. Terminal runs a profile's command without a shell, so the quoted command did nothing; the window now runs a small `lawnotes-window` script by its plain path, and logs each start to `~/.cache/lawnotes/window.log`.

## 1.1.2

- Updates are switched by the new version's own updater, so a release always builds its own app and window profile. Updating from 1.1.0 to 1.1.1 failed halfway because the old updater couldn't read the new profile.
- If switching fails, Law Notes goes back to the previous version instead of stopping halfway, and the background updater reports problems in the editor instead of failing silently.

## 1.1.1

- Inside herdr, `lawnotes` started a second editor in the current pane as well as the Notes tab (herdr's `pane run` succeeds without printing anything). Fixed, with tests.
- Shift+Enter works in the dedicated Law Notes window: its Terminal profile now sends Shift+Return as a distinct key, like herdr does.

## 1.1.0

- Safer updates: only signed releases are installed, each must pass a self-test first, the previous version is kept, and `lawnotes --rollback` goes back. The updater (`update.py`) runs outside the editor.
- Law Notes.app opens one dedicated, dark red Law Notes window. It no longer depends on herdr, and the app finds herdr and Python properly.
- Inside herdr, `lawnotes` opens a "Notes" tab. `--split` keeps the old split-pane behaviour.
- Only one copy runs at a time: launching again brings the open copy forward and opens the note there.
- A note that changes on another Mac is reloaded instead of producing a conflict copy.
- History snapshots only `.md` and `.txt` files, copies itself to iCloud Drive weekly, and ^R shows versions from your other Macs.
- The installer reports where your notes are, warns about an empty or local-only notes folder, and gains `lawnotes --uninstall`.
- Undo: a save or a cursor move starts a new undo step. "X v Y and Z v W" is now read as two cases.
- Tests and CI on macOS with the system Python 3.9 and a current Python.

## 1.0.0

- First release: a terminal editor for law notes with legal highlighting, templates, spell check, search across notes, case index, version history and iCloud-friendly saving.
