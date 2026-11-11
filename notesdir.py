"""Where the notes folder is: the one place the editor, the `lawnotes` command and the
updater all ask, so they always agree. Standard library only (no curses), so the updater can
import it too.

The folder is, in this order:
  1. $LAWNOTES_DIR, for one run;
  2. the folder recorded in ~/.config/lawnotes/notes_dir (written by the installer);
  3. ~/UCL/notes, if that already exists (Macs set up before the choice was recorded);
  4. ~/Law Notes.

The recorded file is plain text: one absolute path (or ~/...) on one line. Its content is not
trusted: anything empty, containing control characters, relative or not a folder is ignored
and the next rule applies. Nothing here creates, moves or deletes anything except the record.
"""
import os
import re

LEGACY = "~/UCL/notes"
DEFAULT = "~/Law Notes"
BAD = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def choice_file():
    return os.path.join(os.path.expanduser("~"), ".config", "lawnotes", "notes_dir")


def legacy():
    return os.path.expanduser(LEGACY)


def default():
    return os.path.expanduser(DEFAULT)


def clean(value):
    """The absolute folder a recorded or chosen value means, or None if it isn't usable."""
    if not isinstance(value, str) or not value or len(value) > 1024 or BAD.search(value):
        return None
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        return None
    path = os.path.normpath(path)
    if path == os.sep or path == os.path.normpath(os.path.expanduser("~")):
        return None  # the whole disk or the home folder is not a notes folder
    if os.path.exists(path) and not os.path.isdir(path):
        return None
    return path


def recorded():
    """The folder in the record, or None if there is no usable record."""
    path = choice_file()
    try:
        if not os.path.isfile(path):  # not a pipe or a device
            return None
        with open(path, encoding="utf-8", newline="") as f:  # newline="": a stray \r stays visible, and is refused
            text = f.read(2048)
    except (OSError, ValueError):
        return None
    return clean(text[:-1] if text.endswith("\n") else text)


def resolve():
    """(folder, where it came from): 'environment', 'record', 'legacy' or 'default'."""
    env = os.environ.get("LAWNOTES_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env)), "environment"
    path = recorded()
    if path:
        return path, "record"
    if os.path.lexists(legacy()):  # lexists: a link into iCloud that hasn't synced yet still counts
        return legacy(), "legacy"
    return default(), "default"


def record(folder):
    """Remember this folder for the editor, the command and the updater."""
    path = clean(folder)
    if not path:
        raise ValueError(f"{folder!r} isn't a usable notes folder")
    target = choice_file()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(path + "\n")
    os.replace(tmp, target)
    return path
