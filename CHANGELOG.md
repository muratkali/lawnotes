# Changelog

## 1.6.1

- Tab case completion and the case index (^B) no longer reread every note after each edit: only notes that changed are scanned again.
- The help screen (^G) is shorter: the long entries for Option+X, Option+R, Option+C and Option+E are now a few lines each, and the detail is in the README.
- The bottom hint bar also lists Export, Cards, To-dos, OSCOLA and Exam, in windows wide enough to show them.
- `lawnotes --abbreviations` creates a new abbreviations file with the same header and defaults as Option+A.
- README: commands in code blocks no longer carry `#` comments, which zsh passes to the command when pasted.
- Tests wait for the editor to go quiet instead of for fixed times: the suite takes about a minute and a half instead of six.
- New installs keep their notes in `~/Law Notes` (linked to iCloud Drive → Law Notes when that is on) instead of a "UCL" folder. Set `LAWNOTES_DIR` when running the installer to choose another folder; the choice is recorded in `~/.config/lawnotes/notes_dir` and used by the editor, the `lawnotes` command and the updater alike (`notesdir.py`). A Mac that already has `~/UCL/notes` keeps using it, unchanged.
- `lawnotes --where` shows which folder your notes are in. Help, the themes guide and the installer's messages name your real folder.

## 1.6.0

- Revision cards (Option+C or F7): each case brief becomes a card (the case and citation on the front, the brief on the back), and so does a bullet that names a case and says something about it. Choose this note, its folder or all notes; Space shows the answer, then 1 again, 2 hard, 3 good. Cards due come first and come back after 1, 3, 7, 14, 30, then 60 days. Progress is kept in UCL Notes → .revision.json, so every Mac shares it, and revising on two Macs before iCloud syncs loses nothing.
- Links between notes: type `[[` and pick a note to write `[[Folder/Name]]` (add `#Heading` to go to a heading, `|other words` to show other words; a bare `[[Name]]` works when only one note has that name). Option+L on a link opens that note, or creates it if it doesn't exist yet; anywhere else it lists the notes linking here. Links are underlined in their own theme colour and dimmed when the note doesn't exist; exports show them as plain text.
- Find and replace (Option+R or F4), e.g. Donohue → Donoghue or [1932] AC 532 → [1932] AC 562: it starts with the selected text or your ^F search, then choose this note, all notes, or one match at a time (y replace, n skip, a all the rest, Esc stop). Capitals must match exactly. ^Z undoes it in an open note; before other notes change, a version is saved so ^R can restore them, and any note that couldn't be written is listed.
- Export with tables of cases and legislation, for essays and coursework: Option+E (or F5) now also offers Word or PDF "with tables of cases and legislation" (`lawnotes --export NOTE docx --tables` from a terminal). An OSCOLA Table of Cases (sorted by the first significant name, so R v Brown is under B; one entry per case, with its citation when the note gives one; UK, EU, Strasbourg and other cases apart when the citation shows which) and a Table of Legislation (Acts by title, EU treaties with their articles, the European Convention) follow the title, and in Word the note starts on a new page. Comments are left out, and the note itself isn't changed.
- Abbreviations (Option+A): your list opens in a pane beside the note (below it when the pane is narrow), one `SHORT = Long form` per line; ^S applies it straight away and Option+W closes the pane. It ends with suggestions from the shorthand your notes already use (EQ → Equity, CT → constructive trust, esp → especially…), commented out until you delete the #. Select a phrase first and the list gets a line for it, waiting for its short form. Citations, courts and judges' titles (AC, QB, HL, LJ…) and the D / V / C labels can't be abbreviations, so they never expand by mistake: saving the list says which lines it couldn't use. `SHORT =` with nothing after it turns a built-in one off.
- **Bold** and *italic* text are shown in their own colours (white and pale blue in NERV; each theme has its own, and theme files have `bold` and `italic` lines). Case names and citations inside keep their colours.
- Scrolling the note list, help and menus no longer flickers: every screen is now drawn as one synchronised update, as the note itself already was.
- Tab completes a case name you've already cited: type its first three letters or more (`Capa`) at the end of a line and press Tab for *Caparo Industries plc v Dickman [1990] 2 AC 605*, with the citation your notes use most. If several cases match, pick one from a list; Esc leaves your text. Common words (the, for, but…) never complete, and anywhere else Tab still indents a bullet or adds spaces. ^Z undoes a completion.
- Exam mode (Option+X or F8): enter the minutes and, if you like, a word limit; the status bar counts down and counts the words of the note the Word way (comments, footnote lines and Markdown markers left out), warns once at 10 and once at 5 minutes, and keeps counting after time is up. Option+X again stops it.
- Essay mode: leave the minutes blank and Option+X sets a word limit on the note instead, kept in the note as `// Word limit 2,000` so it syncs with it. Whenever the note is open its status bar shows `1,214/2,000 words`, and `· 12 fn` when there are footnotes; past the limit the count turns the warning colour. Option+X on such a note changes or removes the limit, or starts a timed exam. The line is a comment, so it is never exported or counted.
- OSCOLA check (Option+K or F9): a list of likely citation slips in the note (v., (1932) AC instead of [1932] AC, A.C., s. 3, p. 617, a footnote without a full stop); Enter jumps to one and f fixes it.
- To-dos (Option+J or F10): every open `- [ ]` across your notes in one list; Enter jumps to one, x ticks it (^Z undoes).
- Revision pack (Option+E, last choice): every note in the folder as one Word or PDF document, in name order (Week 2 before Week 10), with a title page, a contents list and optional tables of cases and legislation. A nested folder's pack is named `Tort › Duty revision pack`.
- Exporting again never destroys a file you edited: Law Notes replaces its own export only while it is unchanged; if you edited it (say in Word), the new export is saved as `Essay (2).docx` and the status bar says why. This applies to single notes, tables and revision packs, and to `lawnotes --export`.
- A case name in bold is now bold italic in Word and PDF exports, with `**` or `__`.
- Find and replace in all notes leaves read-only notes and notes linked from outside the notes folder alone and lists them; a note open in a pane that changed elsewhere meanwhile is listed for you to merge instead of being counted as replaced.
- Started without a terminal window (from a script, or with `!` in Claude Code), Law Notes says it needs one instead of logging a crash.

## 1.5.3

- Case names, law reports and Acts are highlighted in full when the notes write them out: a second “v”, “ex parte” and “Re … (No 1)”; reports such as [2005] 2 A.C. 373, (1870-71) LR 6 Ch App 597, 45 ER 1184 and 161 A2d 69; and statute titles such as “Occupiers Liability Act of 1957”, “Trusts (Jersey) Law 1984”, LPA 1925, Schedule 1 and the European Convention.

## 1.5.2

- One stray byte that isn't text (from a terminal set to another encoding, or a glitchy remote connection) made Law Notes silently swallow up to about 50 following keystrokes, until you paused for a second. Now only that byte is dropped: Law Notes reads and decodes the input itself.
- Esc followed quickly by a control key (^Q, ^S…) lost the second key; it now works.
- Tests: the random-input test could hang CI for 6 hours (the test waited for a killed editor without reading its output) and, after the stray byte, never saw its own ^Q. Found by an advisor review; both fixed, with new tests.

## 1.5.1

- Splitting splits first: the note list opens inside the new pane, with your note still in view beside it (Esc keeps the same note in both).
- Click a note in the list to open it (it opens when you let go of the button); the mouse wheel scrolls the list. Clicks work in every list: notes, themes, spelling, export.
- The note list's hint about folders wraps in narrow panes instead of being cut off.

## 1.5.0

- Themes (Option+T or F6): NERV (dark red), Unit-01 (purple and green) and Paper (light), or your own. New theme copies the current one into a file of colours (`#rrggbb`) in UCL Notes → .themes, so every Mac has it; save it and Law Notes updates straight away. Mistakes in a theme file are reported with their line.
- Right-click opens a menu at the mouse: fixes for a misspelt word, cut / copy / paste, highlight, italic, bold, undo / redo, split panes, export and themes.
- Spelling: on a misspelt word the status bar previews the correction (`recieve → receive`), and ^W makes it straight away; ^W again tries the next suggestion, then opens the full list (add to dictionary, ignore). Away from a mistake, ^W goes to the next one.
- Undo brings back a selection: clicking or moving away from selected text is an undo step, and undoing an edit made to a selection selects it again.
- Articles and EU treaties are highlighted like statutes: Article 1 (1), Article 1.1, Articles 49 and 50 TEU, Art. 21 (1) and (2) TEU, and TEU / TFEU on their own.

## 1.4.2

- Clicks, drags and the scroll wheel in Ghostty were typed into the note as text like `64;45;41M`. Ghostty's terminal description says mouse reports start with `ESC [ <`, so curses took that much as a "mouse key" and passed the rest through as typing. Law Notes now reads the rest of the report itself. Tested with Ghostty's own terminal description and in a new test.

## 1.4.1

- Tab / Shift+Tab with lines selected indent / outdent all of them by two spaces, keeping the selection (blank lines stay blank).
- Highlight text: select it and press `=` (or Option+H, which also works on the word under the cursor). It's stored as `==text==`, shown with a yellow highlighter background, and exported to Word as a real yellow highlight (and to PDF). Press again to remove it.

## 1.4.0

- Labelled **beta**: README, `lawnotes --version`, the ^G help screen and the installer's closing message.
- Paste tables from Word (⌘V or ^V): they become Markdown tables with lined-up columns, and export back to Word as real tables. A cell with several paragraphs stays one cell.
- Abbreviations expand as you type: CL → Common Law, EL → Equity Law, LCh → Lord Chancellor; add your own with `lawnotes --abbreviations` (the list lives in the notes folder, so it syncs). Whole words and exact case only; ^Z undoes an expansion; pasting never expands.
- Comments: everything after `//` on a line is highlighted and left out of exports (`https://` links aren't comments).
- Option+Down / Option+Up jump to the end / start of the note. Option arrows that arrive as Esc + arrow (Terminal with Option as Meta) are understood too.
- Shift+Option+Left/Right select by word in the Law Notes window: its Terminal profile now sends them as distinct keys (Terminal itself sends them the same as Option+Left/Right).
- The note list says how to make folders: type Folder/Name (e.g. Contract/Offer) and press Enter.
- Notes reopen where you left off.
- Enter on an empty indented bullet moves it out a level instead of deleting the line.
- The word count is cached instead of recounted on every screen update.
- A failed history snapshot is logged instead of silently skipped.
- Installer: when the home folder sits behind a link (e.g. /var → /private/var), notes in iCloud were reported as "on this Mac only"; paths are now compared after resolving links (also for the editor's "Notes in iCloud Drive/…" label). New tests cover the installer's empty-iCloud, this-Mac-only and app-not-built messages and the ~/.zshrc PATH line.

## 1.3.2

- Closing a window or pane could cut short the final save: the editor can get SIGHUP twice (the terminal hanging up, and whatever closed it), and the second one interrupted saving. Further signals are now ignored once Law Notes starts closing. Found by a new test on the Mac's built-in Python 3.9.
- Closing the window or pane no longer logs a false "CRASH": the note was already saved, but restoring the vanished terminal failed afterwards. Law Notes now restores the terminal itself and ignores that.

## 1.3.1

- Resize panes with the mouse, like herdr: drag the line between side-by-side panes, or the status bar between stacked panes.
- A one-column margin between the pane's edge and the text.
- Option+Backspace deletes the word before the cursor; Option+Fn+Backspace the word after it.

## 1.3.0

- Panes, like herdr: Option+D splits side by side, Option+Shift+D one above the other, Option+O switches, Option+W closes; clicking a pane switches to it. Each pane has its own note, view, cursor and status line; the same note in two panes shares one text and stays in sync.
- Clicking to place the cursor now also works inside herdr: Law Notes asks for the basic mouse-click mode (1000) as well as drag reporting, and logs the first mouse event of each session.
- Copying is unmistakable: what ^C copied flashes green and the status bar says "✓ Copied N words". Help explains that ⌘C can't copy text selected inside Law Notes (Terminal takes it); use ^C.

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
