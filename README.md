# Law Notes

A small terminal editor for law notes on macOS. Notes are plain Markdown files, so any other app can open them too.

- Highlights case names (*Donoghue v Stevenson*), citations (`[1932] AC 562`) and statutes (`s 2(1)`, *Human Rights Act 1998*).
- Templates for case briefs, IRAC problem answers, statutes, essay plans and lecture notes.
- Search across all notes (^P), a case index across all notes (^B), and an outline of the open note (^L).
- British English spell check using the Mac's own dictionary, which knows legal Latin.
- Saves as you type. Automatic version history (^R) is kept on this Mac, outside any synced folder.
- Opens in a new [herdr](https://herdr.dev) pane when run inside herdr.

Press **^G** in the editor to see every shortcut.

## Install

Run this in Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/muratkali/lawnotes/main/install.sh | bash
```

You need Apple's Command Line Tools. If they're missing, the installer opens Apple's install window; run it again afterwards.

The installer sets up:

- the code in `~/.local/share/lawnotes`
- the `lawnotes` command in `~/.local/bin`
- **Law Notes.app** in `~/Applications`, which you can open from Spotlight or drag to the Dock
- the notes folder `~/UCL/notes`, linked to **iCloud Drive → UCL Notes** if iCloud Drive is on, so every Mac signed in to the same Apple ID shares the same notes

On a new Mac, let iCloud Drive finish syncing before you install, so your existing notes are already there.

## Updates

Installed copies check GitHub once a day and update themselves. The new version is used the next time you open the editor. To update straight away:

```sh
lawnotes --update
```

`lawnotes --version` shows what's installed. Set `LAWNOTES_NO_UPDATE=1` to turn off automatic updates.

## Settings

Set these as environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LAWNOTES_DIR` | `~/UCL/notes` | where notes live |
| `LAWNOTES_LANG` | `en_GB` | spell-check language |
| `LAWNOTES_SPELL` | `1` | `0` turns spell check off |
| `LAWNOTES_MOUSE` | `1` | `0` turns mouse support off |
| `LAWNOTES_HISTORY` | `~/Library/Application Support/lawnotes/history.git` | version history store |
