# Changelog

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
