# Law Notes (beta)

**Beta:** a small terminal editor for law notes on macOS. It's tested and in daily use, but expect rough edges. Your notes are plain Markdown files, so any other app can open them and nothing is locked in.

- Highlights case names (*Donoghue v Stevenson*), citations (`[1932] AC 562`) and statutes (`s 2(1)`, *Human Rights Act 1998*).
- Templates for case briefs, IRAC problem answers, statutes, essay plans and lecture notes.
- Search across all notes (^P), a case index across all notes (^B), and an outline of the open note (^L).
- Type the start of a case name (at least three letters) and press Tab to complete its name and most used citation. If several match, choose one; elsewhere Tab still indents or adds spaces.
- Open to-dos from every note (Option+J or F10); Enter jumps to one, and x ticks it with undo.
- Check this note for likely OSCOLA slips (Option+K or F9); Enter jumps to one, and f fixes a mechanical slip.
- Revision cards (Option+C): your case briefs and case bullets become cards to revise, due cards first; progress syncs between Macs. Only numbered iCloud conflict copies of progress are merged; other similarly named files are left alone. Unreadable progress is never overwritten.
- Exam mode (Option+X, or F8): a countdown and a word count for a timed essay or problem question. Comments, footnote lines and Markdown markers are not counted, and you can keep writing after time is up.
- Essay mode (Option+X, leave the minutes blank): a word limit kept in the note as `// Word limit 2,000`. The status bar shows the words against that limit, and the footnote count when there are footnotes. Delete the line to remove it. A running exam takes the status bar until you stop it.
- Abbreviations that expand as you type (CL → Common Law); Option+A opens your list beside the note, with suggestions from the shorthand your notes already use.
- Find and replace in one note or in all of them (Option+R), e.g. a misspelt case name or a wrong citation; ^R can undo it for any note. All notes skips read-only notes and closed links to files outside the notes folder; open an outside link to replace there. Links to the same file are replaced once, even if spelled differently. It lists notes it cannot save, including Finder-locked ones. If an open note changes elsewhere during replacement, its conflict copy is listed for you to merge.
- Links between notes: type `[[` and pick a note. Option+L follows a link, or lists the notes linking to this one.
- British English spell check using the Mac's own dictionary, which knows legal Latin.
- Saves as you type, never overwrites a note changed on another Mac, and keeps version history (^R).

- Export to Word or PDF (Option+E): case names in italics (OSCOLA), even inside bold text, and footnotes as real footnotes.
- Revision pack (Option+E): every note in the current folder as one Word or PDF document, with a title page and contents.
- Highlight text (select it, press `=`): exported to Word as a yellow highlight.
- Paste tables from Word: they become Markdown tables, and export back to Word as real tables.
- Panes like herdr: Option+D splits side by side, Option+Shift+D stacks, Option+O switches, Option+W closes.
- Themes (Option+T): NERV, Unit-01, Paper, or your own colours.
- Right-click for a menu: spelling fixes, copy and paste, formatting, split panes, exam mode.

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
- the notes folder `~/Law Notes`, linked to **iCloud Drive → Law Notes** when iCloud Drive is on, so every Mac signed in to the same Apple ID shares the same notes. The installer does not ask: it prints where your notes will live.

To keep your notes somewhere else, give the installer a full folder path by putting `LAWNOTES_DIR` on the `bash` side of the command (it is then remembered, for the editor, the `lawnotes` command and the updater alike):

```sh
curl -fsSL https://raw.githubusercontent.com/muratkali/lawnotes/main/install.sh | LAWNOTES_DIR="$HOME/Documents/Law Notes" bash
```

A custom folder is used as it is and is not linked to iCloud Drive. A Mac that already uses `~/UCL/notes` keeps using it, with nothing moved, and a new Mac whose iCloud Drive already has a **UCL Notes** folder gets `~/UCL/notes` linked to it, as before; run `lawnotes --where` to see which folder your copy uses. Running the installer again with `LAWNOTES_DIR` changes the folder for later runs but never moves notes.

Only one copy of Law Notes runs at a time. Opening it again switches to the open copy (and opens the note you asked for there).

## Export

Option+E (or F5) in the editor, or from a terminal:

```sh
lawnotes --export Tort/Negligence docx
lawnotes --export Tort/Negligence pdf
lawnotes --export Tort/Essay docx --tables
```

The last word is `docx` or `pdf`. Each file is saved to `~/Downloads`.

Law Notes replaces an unchanged export of its own. If a file was edited, or its export record is missing, it keeps that file and saves the new one as `Name (2).docx` (then `(3)` and so on); the editor shows the name and why.

For essays and coursework, choose "with tables of cases and legislation" (or add `--tables`): an OSCOLA Table of Cases and Table of Legislation go after the title (the first `# ` heading), built from the case names, citations, Acts and EU treaty articles in the note, with a page break before the note itself in Word.

Revision pack, the last choice in that menu, writes every note in the current note's folder to `Folder revision pack.docx` (or `.pdf`) in Downloads, in name order (Week 2 before Week 10). The document has a title page with the folder and today's date, a contents list, then each note under its own heading. Footnotes stay with the right note. With tables, one Table of Cases and one Table of Legislation cover the whole pack, after the contents.

Word export needs pandoc: `brew install pandoc`. PDF export uses macOS itself.

## Exam and essay mode

Option+X (or F8) has two modes. It asks for the minutes first; Esc cancels any question.

**Exam mode** is a timed essay or problem question. Give the minutes, then a word limit (leave the limit blank for none).

- The status bar shows the time left and the words in the note. After the end it shows how long since (Time's up). You can keep writing the whole time.
- The count is the body only. `//` comments and footnote lines (`[^1]: …`) are left out. So are Markdown markers (headings, bullets, *italic*, **bold**, `==highlights==`, `code`) and `[^1]` references. A link counts as the words it shows.
- You are told once at 10 minutes left and once at 5.
- Past the limit the count uses the theme's warning colour, the same as a misspelt word.
- Option+X again asks whether to stop. Closing the pane or quitting also ends it.

**Essay mode** is a word limit with no clock. Leave the minutes blank, then give the limit.

- Law Notes writes `// Word limit 2,000` at the top of the note. That line is a comment, so it is left out of the count and of exports.
- The status bar shows `1,214/2,000 words`, with `· 12 fn` after it when there are footnotes. Past the limit that count uses the warning colour too.
- Delete the line, or change the number, to change the limit.
- On a note that already has a limit, Option+X asks `c` change, `r` remove, or `e` for a timed exam. Esc cancels.
- While an exam is running, its status replaces this count.

## Find and replace

Option+R (or F4), e.g. to correct Donohue to Donoghue.

1. Type what to replace. It starts as the selected text or your last ^F search.
2. Type what to put instead.
3. Choose this note, all notes, or one match at a time (`y` replace, `n` skip, `a` all the rest, Esc stops).

Capitals must match exactly. The replacements stay highlighted until Esc. ^Z undoes a replace in an open note. Other notes are saved in version history first, so ^R can restore them. The all-notes rules for read-only notes, outside links, Finder-locked notes and conflict copies are in the feature list above.

## Revision cards

Option+C (or F7). Each case brief (^T) is a card, with the case and citation on the front and the brief on the back. So is a bullet that names a case and says something about it. Choose this note, its folder or all notes.

- Space shows the answer. Then press 1 for again, 2 for hard, 3 for good. Esc ends.
- A card comes back after 1, 3, 7, 14, 30, then 60 days. Due cards come first, then new ones.
- Progress is kept in your notes folder, so every Mac shares it. Only numbered iCloud conflict copies are merged; other similarly named files are left alone.
- If progress cannot be read, a rating is not saved and the status bar warns you.

## Updates, rollback, uninstall

Once a day, Law Notes checks for a newer release. It installs one only if the release is signed with the Law Notes release key and passes its self-test on your Mac; otherwise it stays on the current version and tells you why. The new version is used the next time you open the editor.

```sh
lawnotes --update
lawnotes --rollback
lawnotes --version
lawnotes --uninstall
```

- `--update` checks for a release now.
- `--rollback` goes back to the previous version, and skips the one you left.
- `--version` shows the installed version.
- `--where` shows which folder your notes are in.
- `--uninstall` removes the app and code. Your notes and their history stay.

Set `LAWNOTES_NO_UPDATE=1` to turn off automatic updates.

## Your notes and their history

- By default notes live in `~/Law Notes`, which is iCloud Drive → Law Notes when iCloud Drive is on, and that syncs your Macs. iCloud isn't a backup, though: a deletion reaches every Mac. Turn on Time Machine as well.
- In Finder, right-click the Law Notes folder in iCloud Drive and choose **Keep Downloaded**, so macOS never removes notes from the Mac to save space.
- Version history (^R) is kept on each Mac in `~/Library/Application Support/lawnotes/history.git`. Once a week a copy goes to iCloud Drive → Law Notes History, so ^R can also show versions saved on your other Macs.
- Only `.md` and `.txt` files are kept in history. Keep lecture PDFs elsewhere, or they're simply not versioned.

## Settings

Set these as environment variables:

- `LAWNOTES_DIR`: where notes live. Without it Law Notes uses the folder the installer recorded (in `~/.config/lawnotes/notes_dir`), else `~/UCL/notes` if that exists, else `~/Law Notes`. Set it when installing to choose a folder for good (see Install), or when starting Law Notes to use another folder for that run only.
- `LAWNOTES_LANG` (default `en_GB`): spell-check language
- `LAWNOTES_SPELL=0`: turn spell check off
- `LAWNOTES_MOUSE=0`: turn mouse support off
- `LAWNOTES_NO_UPDATE=1`: turn automatic updates off

## Developing and releasing

```sh
python3 -m unittest discover -s tests -v
python3 lawnotes.py --self-test
```

The first command runs the editor, install, update and rollback tests.

CI runs the tests on macOS with the system Python (what a fresh Mac has) and a current Python, plus shellcheck. To release:

1. Make sure CI passed on `main`.
2. Set `VERSION` in `lawnotes.py` and add a `## X.Y.Z` section to `CHANGELOG.md`, then commit and push.
3. When CI is green again, run `./release.sh X.Y.Z`. It checks all of the above, then signs the tag with the release key, which lives only in 1Password (approve the request with Touch ID), pushes it and creates the GitHub release. `~/.ssh/lawnotes_release_ed25519.pub` tells the script which key to ask for.
4. Run `lawnotes --update` on one Mac and open a note before the others update themselves.

Versions follow semver: patch for fixes, minor for features, major for changes to the notes layout or history store.
