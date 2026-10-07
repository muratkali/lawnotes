# Law Notes (beta)

**Beta:** a small terminal editor for law notes on macOS. It's tested and in daily use, but expect rough edges. Your notes are plain Markdown files, so any other app can open them and nothing is locked in.

- Highlights case names (*Donoghue v Stevenson*), citations (`[1932] AC 562`) and statutes (`s 2(1)`, *Human Rights Act 1998*).
- Templates for case briefs, IRAC problem answers, statutes, essay plans and lecture notes.
- Search across all notes (^P), a case index across all notes (^B), and an outline of the open note (^L).
- British English spell check using the Mac's own dictionary, which knows legal Latin.
- Saves as you type, never overwrites a note changed on another Mac, and keeps version history (^R).

- Export to Word or PDF (Option+E): case names in italics (OSCOLA), footnotes as real footnotes.
- Highlight text (select it, press `=`): exported to Word as a yellow highlight.
- Paste tables from Word: they become Markdown tables, and export back to Word as real tables.
- Panes like herdr: Option+D splits side by side, Option+Shift+D stacks, Option+O switches, Option+W closes.

Press **^G** in the editor to see every shortcut.

## Install

Run this in Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/muratkali/lawnotes/main/install.sh | bash
```

You need Apple's Command Line Tools. If they're missing, the installer opens Apple's install window; run it again afterwards. On a new Mac, let iCloud Drive finish syncing first, so your existing notes are already there.

About trust: the installer script itself comes straight from `main`, like any `curl | bash` install, so only run the command above. Everything it installs is checked: it downloads the newest release tag, verifies its signature against the Law Notes release key written into the script, and runs the release's self-test before using it. From then on, this Mac trusts only that key.

The installer installs the newest **signed release** and sets up:

- **Law Notes.app** in `~/Applications`. Open it from Spotlight or the Dock: it opens one dedicated, dark red Law Notes window, or brings the open one forward.
- the `lawnotes` command. Inside [herdr](https://herdr.dev) it opens a "Notes" tab; elsewhere it runs in the current terminal.
- the notes folder `~/UCL/notes`, linked to **iCloud Drive → UCL Notes** when iCloud Drive is on, so every Mac signed in to the same Apple ID shares the same notes

Only one copy of Law Notes runs at a time. Opening it again switches to the open copy (and opens the note you asked for there).

## Export

Option+E (or F5) in the editor, or from a terminal:

```sh
lawnotes --export Tort/Negligence docx    # or pdf; saved to ~/Downloads
```

Word export needs pandoc: `brew install pandoc`. PDF export uses macOS itself.

## Updates, rollback, uninstall

Once a day, Law Notes checks for a newer release. It installs one only if the release is signed with the Law Notes release key and passes its self-test on your Mac; otherwise it stays on the current version and tells you why. The new version is used the next time you open the editor.

```sh
lawnotes --update      # check now
lawnotes --rollback    # go back to the previous version (and skip the one you left)
lawnotes --version
lawnotes --uninstall   # removes the app and code; your notes and their history stay
```

Set `LAWNOTES_NO_UPDATE=1` to turn off automatic updates.

## Your notes and their history

- Notes live in iCloud Drive → UCL Notes, which syncs your Macs. iCloud isn't a backup, though: a deletion reaches every Mac. Turn on Time Machine as well.
- In Finder, right-click UCL Notes and choose **Keep Downloaded**, so macOS never removes notes from the Mac to save space.
- Version history (^R) is kept on each Mac in `~/Library/Application Support/lawnotes/history.git`. Once a week a copy goes to iCloud Drive → Law Notes History, so ^R can also show versions saved on your other Macs.
- Only `.md` and `.txt` files are kept in history. Keep lecture PDFs elsewhere, or they're simply not versioned.

## Settings

Set these as environment variables:

- `LAWNOTES_DIR` (default `~/UCL/notes`): where notes live
- `LAWNOTES_LANG` (default `en_GB`): spell-check language
- `LAWNOTES_SPELL=0`: turn spell check off
- `LAWNOTES_MOUSE=0`: turn mouse support off
- `LAWNOTES_NO_UPDATE=1`: turn automatic updates off

## Developing and releasing

```sh
python3 -m unittest discover -s tests -v   # editor, install, update and rollback tests
python3 lawnotes.py --self-test
```

CI runs the tests on macOS with the system Python (what a fresh Mac has) and a current Python, plus shellcheck. To release:

1. Make sure CI passed on `main`.
2. Set `VERSION` in `lawnotes.py` and add a `## X.Y.Z` section to `CHANGELOG.md`, then commit and push.
3. When CI is green again, run `./release.sh X.Y.Z`. It checks all of the above, then signs the tag with the release key from 1Password (approve the request with Touch ID), pushes it and creates the GitHub release. Without 1Password, unlock the key first with `ssh-add -t 900 ~/.ssh/lawnotes_release_ed25519`.
4. Run `lawnotes --update` on one Mac and open a note before the others update themselves.

Versions follow semver: patch for fixes, minor for features, major for changes to the notes layout or history store.
