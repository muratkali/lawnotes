#!/usr/bin/env python3
"""Law Notes updater: install, update, roll back and uninstall releases.

Layout (ROOT = ~/.local/share/lawnotes):
    repo.git/              bare clone of github.com/muratkali/lawnotes
    versions/vX.Y.Z/       one checkout per release
    current -> versions/vX.Y.Z
    state.json             {"current", "previous", "skip": [rolled back], "failed": [didn't pass]}
    Law Notes.terminal     the dedicated window's Terminal profile

A release is a signed tag vX.Y.Z. Before Law Notes switches to it, the tag must verify
against ~/.config/lawnotes/allowed_signers (written once by the installer and never read
from downloaded code), and the new version must pass `lawnotes.py --self-test`.
Otherwise this Mac stays on the version it has and says why.

The running version only downloads, verifies and tests a release; the switch itself
(`--switch TAG`) is done by the new version's own update.py, so a release always builds
its own app and profile. If the switch fails, `current` goes back to where it was.

    update.py --daily          background check, at most once a day (run by the launcher)
    update.py --now            check now, and repair the app and command
    update.py --rollback       go back to the previous version and skip this one
    update.py --install TAG    first install (run by install.sh)
    update.py --switch TAG     switch to an already verified and tested version (internal)
    update.py --uninstall
    update.py --status
"""
import fcntl
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.expanduser(os.environ.get("LAWNOTES_HOME", "~/.local/share/lawnotes"))
REPO = os.path.join(ROOT, "repo.git")
VERSIONS = os.path.join(ROOT, "versions")
CURRENT = os.path.join(ROOT, "current")
STATE = os.path.join(ROOT, "state.json")
PROFILE = os.path.join(ROOT, "Law Notes.terminal")
WINDOW = os.path.join(ROOT, "lawnotes-window")  # what the Law Notes window runs
SIGNERS = os.path.expanduser(os.environ.get("LAWNOTES_SIGNERS", "~/.config/lawnotes/allowed_signers"))
CACHE = os.path.expanduser("~/.cache/lawnotes")
STAMP = os.path.join(CACHE, "last-update-check")
MESSAGE = os.path.join(CACHE, "update-message")
LOCK = os.path.join(CACHE, "update.lock")
APP = os.path.expanduser(os.environ.get("LAWNOTES_APP", "~/Applications/Law Notes.app"))
BIN = os.path.expanduser("~/.local/bin/lawnotes")
NOTES = os.path.expanduser(os.environ.get("LAWNOTES_DIR", "~/UCL/notes"))
ICLOUD = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
BUNDLE_ID = "li.muratka.lawnotes"
CHECK_EVERY = 24 * 3600
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")


class UpdateError(Exception):
    pass


def say(msg):
    print(f"\033[1;33m==>\033[0m {msg}")


def git(*args, check=True):
    r = subprocess.run(["git", f"--git-dir={REPO}", *args], capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise UpdateError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()}")
    return r


def tag_key(tag):
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag or "")
    return tuple(map(int, m.groups())) if m else None


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"current": None, "previous": None, "skip": []}


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE)


def tell_editor(msg):
    """Shown in the editor's status bar the next time it's open."""
    os.makedirs(CACHE, exist_ok=True)
    with open(MESSAGE, "w") as f:
        f.write(msg)


def fetch():
    git("fetch", "-q", "origin", "refs/tags/*:refs/tags/*")


def releases():
    tags = git("tag", "-l", "v*").stdout.split()
    return sorted((t for t in tags if tag_key(t)), key=tag_key)


def verify(tag):
    if not os.path.exists(SIGNERS):
        raise UpdateError(f"no trusted release key at {SIGNERS}; run the installer again")
    r = git("-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={SIGNERS}", "verify-tag", tag, check=False)
    if r.returncode != 0:
        raise UpdateError(f"{tag} is not signed with the Law Notes release key")


def stage(tag):
    path = os.path.join(VERSIONS, tag)
    if os.path.exists(path):
        want = git("rev-parse", f"{tag}^{{commit}}").stdout.strip()
        have = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        clean = not subprocess.run(["git", "-C", path, "status", "--porcelain"],
                                   capture_output=True, text=True).stdout.strip()
        if have == want and clean:
            return path  # already checked out (install.sh does this before calling us)
        git("worktree", "remove", "--force", path, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", check=False)
    os.makedirs(VERSIONS, exist_ok=True)
    git("worktree", "add", "-q", "--detach", path, tag)
    return path


def self_test(path):
    r = subprocess.run([sys.executable, os.path.join(path, "lawnotes.py"), "--self-test"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        lines = (r.stdout + r.stderr).strip().splitlines()
        raise UpdateError("its self-test failed: " + (lines[-1] if lines else "no output"))


def point_current(tag):
    tmp = CURRENT + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(os.path.join("versions", tag), tmp)
    os.replace(tmp, CURRENT)  # atomic: a launch sees either the old or the new version


def link_command():
    os.makedirs(os.path.dirname(BIN), exist_ok=True)
    tmp = BIN + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(os.path.join(CURRENT, "lawnotes"), tmp)
    os.replace(tmp, BIN)


def build_profile(path):
    """The dedicated window: dark red, sized for prose, running the editor. Filled in as text,
    because the profile's key map holds a raw Escape character that plistlib refuses."""
    with open(os.path.join(path, "app", "Law Notes.terminal"), "rb") as f:
        template = f.read()
    # Terminal runs this command without a shell, so it must be a bare path: no quotes or arguments
    with open(WINDOW + ".tmp", "w") as f:
        f.write('#!/bin/bash\n# Run by the Law Notes window (its Terminal profile\'s command)\n'
                'echo "$(date \'+%F %T\') window started" >> "$HOME/.cache/lawnotes/window.log"\n'
                f'exec "{os.path.join(CURRENT, "lawnotes")}" --here\n')
    os.chmod(WINDOW + ".tmp", 0o755)
    os.replace(WINDOW + ".tmp", WINDOW)
    command = WINDOW.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    assert template.count(b"__LAWNOTES_COMMAND__") == 1, "profile template has no command placeholder"
    with open(PROFILE + ".tmp", "wb") as f:
        f.write(template.replace(b"__LAWNOTES_COMMAND__", command.encode()))
    os.replace(PROFILE + ".tmp", PROFILE)


def build_app(path, tag):
    """~/Applications/Law Notes.app; only ever replaces an app that is ours."""
    info_path = os.path.join(APP, "Contents", "Info.plist")
    if os.path.exists(APP):
        try:
            with open(info_path, "rb") as f:
                if plistlib.load(f).get("CFBundleIdentifier") != BUNDLE_ID:
                    return False
        except (OSError, plistlib.InvalidFileException):
            return False
    new = APP + ".new"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(os.path.join(new, "Contents", "MacOS"))
    os.makedirs(os.path.join(new, "Contents", "Resources"))
    with open(os.path.join(path, "app", "Info.plist"), "rb") as f:
        info = plistlib.load(f)
    info["CFBundleShortVersionString"] = tag.lstrip("v")
    with open(os.path.join(new, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    shutil.copy(os.path.join(path, "app", "AppIcon.icns"), os.path.join(new, "Contents", "Resources"))
    exe = os.path.join(new, "Contents", "MacOS", "law-notes")
    with open(exe, "w") as f:
        # Apps start with a minimal PATH; add the places herdr and Homebrew live
        f.write('#!/bin/bash\nexport PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
                f'exec "{os.path.join(CURRENT, "lawnotes")}" --app\n')
    os.chmod(exe, 0o755)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", new], capture_output=True)
    old = APP + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(APP):
        os.rename(APP, old)
    os.rename(new, APP)
    shutil.rmtree(old, ignore_errors=True)
    if not os.environ.get("LAWNOTES_NO_REGISTER"):
        subprocess.run([LSREGISTER, "-f", APP], capture_output=True)
    return True


def prune(state):
    keep = {state.get("current"), state.get("previous")}
    for name in os.listdir(VERSIONS) if os.path.isdir(VERSIONS) else []:
        if name not in keep:
            git("worktree", "remove", "--force", os.path.join(VERSIONS, name), check=False)
            shutil.rmtree(os.path.join(VERSIONS, name), ignore_errors=True)
    git("worktree", "prune", check=False)


def prepare(tag, state):
    """Verify, stage and self-test a release. Raises UpdateError (and cleans up) on failure."""
    verify(tag)
    path = stage(tag)
    try:
        self_test(path)
    except UpdateError:
        if tag != state.get("current"):
            shutil.rmtree(path, ignore_errors=True)
            git("worktree", "prune", check=False)
        raise
    return path


def activate(tag, state):
    """Prepare a release, then let its own update.py switch to it."""
    path = prepare(tag, state)
    r = subprocess.run([sys.executable, os.path.join(path, "update.py"), "--switch", tag],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        lines = (r.stderr or r.stdout).strip().splitlines()
        raise UpdateError("switching failed: " + (lines[-1] if lines else "no output"))


def switch(tag):
    """Run by the new version: point `current` at it and build its app, profile and command.
    Any failure puts everything back on the previous version."""
    state = load_state()
    before = os.readlink(CURRENT) if os.path.islink(CURRENT) else None
    try:
        point_current(tag)
        refresh(tag)
    except Exception as e:
        if before:
            os.symlink(before, CURRENT + ".back")
            os.replace(CURRENT + ".back", CURRENT)
            try:
                refresh(os.path.basename(before))
            except Exception:
                pass
        raise UpdateError(f"{type(e).__name__}: {e}")
    if state.get("current") != tag:
        state["previous"] = state.get("current")
        state["current"] = tag
    save_state(state)
    prune(state)


def refresh(tag):
    path = os.path.join(VERSIONS, tag)
    build_profile(path)
    link_command()
    return build_app(path, tag)


def newer(state, retry_failed=False):
    """Releases newer than this Mac's, minus ones rolled back (always) and ones that failed
    verification or their self-test (unless retrying, as `lawnotes --update` does)."""
    current = tag_key(state.get("current")) or (0, 0, 0)
    skip = set(state.get("skip", [])) | (set() if retry_failed else set(state.get("failed", [])))
    return [t for t in releases() if tag_key(t) > current and t not in skip]


def update(quiet):
    state = load_state()
    if not state.get("current"):
        raise UpdateError("Law Notes isn't installed here; run the installer")
    fetch()
    os.makedirs(CACHE, exist_ok=True)
    with open(STAMP, "w"):
        pass
    candidates = newer(state, retry_failed=not quiet)
    if not candidates:
        if not quiet:
            refresh(state["current"])
            say(f"Law Notes {state['current']} is the newest release")
        return
    tag = candidates[-1]
    try:
        activate(tag, state)
    except UpdateError as e:
        state.setdefault("failed", []).append(tag)
        save_state(state)
        msg = f"Law Notes {tag} wasn't installed: {e}. Staying on {state['current']}."
        if quiet:
            tell_editor(msg)
        raise UpdateError(msg)
    if quiet:
        tell_editor(f"Law Notes was updated to {tag} — quit (^Q) and reopen to use it")
    else:
        say(f"Updated to {tag}")


def rollback():
    state = load_state()
    prev, cur = state.get("previous"), state.get("current")
    if not prev or not os.path.isdir(os.path.join(VERSIONS, prev)):
        raise UpdateError("there is no previous version to go back to")
    point_current(prev)
    state.update(current=prev, previous=None)
    state.setdefault("skip", []).append(cur)
    refresh(prev)
    save_state(state)
    say(f"Back on {prev}. {cur} will be skipped; a newer release installs as usual.")


def setup_path():
    rcs = [os.path.expanduser(p) for p in ("~/.zshrc", "~/.zprofile")]
    if any(".local/bin" in open(p).read() for p in rcs if os.path.exists(p)):
        return
    with open(rcs[0], "a") as f:
        f.write('\n# Added by the Law Notes installer\nexport PATH="$HOME/.local/bin:$PATH"\n')
    say("Added ~/.local/bin to your PATH in ~/.zshrc: new Terminal windows will know `lawnotes`")


def count_notes(folder):
    return sum(1 for _, _, files in os.walk(folder) for f in files
               if f.endswith((".md", ".txt")) and not f.startswith("."))


def setup_notes():
    icloud_notes = os.path.join(ICLOUD, "UCL Notes")
    if not os.path.lexists(NOTES):
        os.makedirs(os.path.dirname(NOTES), exist_ok=True)
        if os.path.isdir(ICLOUD):
            os.makedirs(icloud_notes, exist_ok=True)
            os.symlink(icloud_notes, NOTES)
        else:
            os.makedirs(NOTES)
    real = os.path.realpath(NOTES)
    n = count_notes(real)
    where = "iCloud Drive/" + os.path.relpath(real, ICLOUD) if real.startswith(ICLOUD + os.sep) else NOTES
    say(f"Notes: {n} in {where.replace(os.path.expanduser('~'), '~')}")
    if real.startswith(ICLOUD + os.sep) and n == 0:
        print("    UCL Notes is empty on this Mac. If you have notes on another Mac, wait until Finder\n"
              "    shows iCloud Drive has finished syncing before writing, or you'll get duplicate notes.")
    elif not real.startswith(ICLOUD + os.sep) and os.path.isdir(ICLOUD):
        print("    These notes are on this Mac only, not in iCloud Drive.")
    if real.startswith(ICLOUD + os.sep):
        print("    Tip: in Finder, right-click iCloud Drive → UCL Notes → Keep Downloaded.")


def install(tag):
    """First install, or repair (install.sh has already verified and staged the tag)."""
    os.makedirs(ROOT, exist_ok=True)
    state = load_state()
    activate(tag, state)
    app_built = os.path.exists(os.path.join(APP, "Contents", "MacOS", "law-notes"))
    setup_path()
    setup_notes()
    herdr = shutil.which("herdr", path=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/local/bin:"
                         + os.environ.get("PATH", ""))
    say(f"Installed Law Notes {tag}")
    if app_built:
        print("    Open it from Spotlight (⌘Space → Law Notes) or the Dock: a dedicated Law Notes window.")
    else:
        print(f"    Not built: {APP} already exists and isn't Law Notes.")
    print("    Or type `lawnotes` in a terminal" + (" (inside herdr: a Notes tab)." if herdr else "."))
    print("    Updates: automatic, once a day, signed releases only. `lawnotes --update` checks now,\n"
          "    `lawnotes --rollback` goes back, LAWNOTES_NO_UPDATE=1 turns it off, `lawnotes --uninstall` removes it.")


def uninstall():
    print("This removes the Law Notes app, the `lawnotes` command and its code:")
    print(f"    {APP}\n    {BIN}\n    {ROOT}\n    {SIGNERS}")
    print(f"Your notes ({NOTES}) and their history are NOT touched.")
    if sys.stdin.isatty() and input("Remove Law Notes? [y/N] ").strip().lower() != "y":
        print("Nothing removed.")
        return
    if os.path.exists(APP):
        try:
            with open(os.path.join(APP, "Contents", "Info.plist"), "rb") as f:
                ours = plistlib.load(f).get("CFBundleIdentifier") == BUNDLE_ID
        except (OSError, plistlib.InvalidFileException):
            ours = False
        if ours:
            subprocess.run([LSREGISTER, "-u", APP], capture_output=True)
            shutil.rmtree(APP)
    if os.path.islink(BIN) and os.path.realpath(BIN).startswith(os.path.realpath(ROOT)):
        os.remove(BIN)
    shutil.rmtree(ROOT, ignore_errors=True)
    if os.path.exists(SIGNERS):
        os.remove(SIGNERS)
        try:
            os.rmdir(os.path.dirname(SIGNERS))  # only if nothing else is in it
        except OSError:
            pass
    say("Law Notes removed. The PATH line in ~/.zshrc is harmless; delete it if you like.")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "--status"
    os.makedirs(CACHE, exist_ok=True)
    try:
        if cmd == "--status":
            state = load_state()
            print(f"Law Notes {state.get('current') or 'not installed'}"
                  + (f" (previous: {state['previous']})" if state.get("previous") else ""))
            return 0
        if cmd == "--uninstall":
            uninstall()
            return 0
        if cmd == "--switch" and len(args) > 1:
            switch(args[1])  # run by an updater that already holds the lock
            return 0
        with open(LOCK, "w") as lock:  # one updater at a time
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if cmd == "--daily" else 0))
            except BlockingIOError:
                return 0
            if cmd == "--daily":
                if os.environ.get("LAWNOTES_NO_UPDATE"):
                    return 0
                try:
                    if time.time() - os.path.getmtime(STAMP) < CHECK_EVERY:
                        return 0
                except OSError:
                    pass
                update(quiet=True)
            elif cmd == "--now":
                update(quiet=False)
            elif cmd == "--rollback":
                rollback()
            elif cmd == "--install" and len(args) > 1:
                install(args[1])
            else:
                print(__doc__)
                return 2
        return 0
    except Exception as e:  # never a traceback: say what went wrong in one line
        msg = str(e) if isinstance(e, (UpdateError, OSError)) else f"{type(e).__name__}: {e}"
        if cmd == "--daily":
            tell_editor(f"Law Notes couldn't check for updates: {msg}")
        else:
            print(f"Law Notes: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
