"""Install, update and rollback tests against a throwaway "GitHub" with a throwaway release key.

Run: python3 -m unittest discover -s tests -v
Everything happens in a temporary HOME; the real install, app and notes are never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*cmd, cwd=None, env=None, check=True, stdin=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, input=stdin)
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")
    return r


class ReleaseTests(unittest.TestCase):
    """One world shared by the tests below, which run in order (test_1…, test_2…)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lawnotes-release-")
        cls.home = os.path.join(cls.tmp, "home")
        cls.origin = os.path.join(cls.tmp, "origin.git")
        cls.work = os.path.join(cls.tmp, "work")
        cls.key = os.path.join(cls.tmp, "release_key")
        cls.signers = os.path.join(cls.tmp, "allowed_signers")
        os.makedirs(cls.home)
        # The code under test (the working tree, including uncommitted changes) as a fresh repo
        files = run("git", "ls-files", "--cached", "--others", "--exclude-standard", cwd=ROOT).stdout.split("\n")
        for f in filter(None, files):
            if os.path.exists(os.path.join(ROOT, f)):
                os.makedirs(os.path.dirname(os.path.join(cls.work, f)), exist_ok=True)
                shutil.copy2(os.path.join(ROOT, f), os.path.join(cls.work, f))
        cls.git("init", "-q", "-b", "main")
        cls.git("add", "-A")
        cls.git("commit", "-q", "-m", "test release")
        run("git", "init", "-q", "--bare", cls.origin)
        cls.git("remote", "add", "origin", cls.origin)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", cls.key)
        with open(cls.signers, "w") as f:
            f.write('lawnotes-release namespaces="git" ' + " ".join(open(cls.key + ".pub").read().split()[:2]) + "\n")
        cls.release("v9.0.0")
        cls.env = {**os.environ, "HOME": cls.home, "LAWNOTES_REPO": cls.origin, "LAWNOTES_SIGNERS": cls.signers,
                   "LAWNOTES_NO_REGISTER": "1", "LAWNOTES_SPELL": "0"}
        for k in ("LAWNOTES_HOME", "LAWNOTES_DIR", "LAWNOTES_HISTORY", "LAWNOTES_APP", "LAWNOTES_NO_UPDATE"):
            cls.env.pop(k, None)
        cls.share = os.path.join(cls.home, ".local", "share", "lawnotes")
        cls.lawnotes = os.path.join(cls.home, ".local", "bin", "lawnotes")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def git(cls, *args):
        return run("git", "-c", "user.name=test", "-c", "user.email=t@example.com", *args, cwd=cls.work)

    @classmethod
    def release(cls, tag, signed=True, change=None):
        if change:
            change(cls.work)
            cls.git("commit", "-q", "-am", f"changes for {tag}")
        if signed:
            cls.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={cls.key}", "tag", "-s", tag, "-m", tag)
        else:
            cls.git("tag", "-a", tag, "-m", tag)
        cls.git("push", "-q", "origin", "main", tag)

    def current(self):
        return os.path.basename(os.readlink(os.path.join(self.share, "current")))

    def lawnotes_cmd(self, *args, check=True):
        return run(sys.executable, self.lawnotes, *args, env=self.env, check=check)

    def test_1_install(self):
        r = run("bash", os.path.join(self.work, "install.sh"), env=self.env)
        self.assertIn("signature verified", r.stdout)
        self.assertIn("(beta)", r.stdout)                                    # labelled beta
        self.assertIn(".local/bin", open(os.path.join(self.home, ".zshrc")).read())  # every new shell
        self.assertEqual(self.current(), "v9.0.0")
        app = os.path.join(self.home, "Applications", "Law Notes.app", "Contents")
        self.assertIn(".local/bin", open(os.path.join(app, "MacOS", "law-notes")).read())  # PATH for herdr
        profile = os.path.join(self.share, "Law Notes.terminal")
        run("plutil", "-lint", profile)
        text = open(profile, encoding="utf-8").read()
        window = os.path.join(self.share, "lawnotes-window")
        self.assertIn(f"<string>{window}</string>", text)  # a bare path: Terminal runs it without a shell
        self.assertTrue(os.access(window, os.X_OK))
        self.assertIn('current/lawnotes" --here', open(window).read())
        self.assertIn("<key>$000D</key>", text)  # Shift+Return mapped for the editor
        self.assertIn("<key>$~F702</key>", text)  # Shift+Option+Left/Right select by word
        self.assertIn("<key>$~F703</key>", text)
        self.assertIn("<key>~F701</key>", text)   # Option+Down/Up: end/start of the note
        self.assertIn("<key>~F700</key>", text)
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Law Notes")))   # a fresh Mac gets the neutral folder
        self.assertFalse(os.path.lexists(os.path.join(self.home, "UCL")))
        self.assertEqual(self.lawnotes_cmd("--where").stdout.strip(), os.path.join(self.home, "Law Notes"))
        self.assertIn("Law Notes", self.lawnotes_cmd("--version").stdout)

    def fresh_install(self, setup=None, check=True, **env):
        """Run the installer in a new temporary HOME (set up first by setup(home)); (home, result)."""
        home = tempfile.mkdtemp(dir=self.tmp)
        if setup:
            setup(home)
        r = run("bash", os.path.join(self.work, "install.sh"), env={**self.env, "HOME": home, **env}, check=check)
        return home, r

    @staticmethod
    def icloud(home):
        return os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs")

    @staticmethod
    def snapshot(*roots):
        """Every name, link target and file content (and mtime) under the roots, to compare before and after."""
        seen = {}
        for root in roots:
            if not os.path.lexists(root):
                continue
            for base, dirs, files in os.walk(root):
                for name in dirs + files:
                    p = os.path.join(base, name)
                    if os.path.islink(p):
                        seen[p] = ("link", os.readlink(p))
                    elif os.path.isdir(p):
                        seen[p] = ("dir",)
                    else:
                        with open(p, "rb") as f:
                            seen[p] = ("file", f.read(), os.path.getmtime(p))
        return seen

    def test_1b_installer_tells_the_truth(self):
        """The messages the run review asked for: empty iCloud folder, notes on this Mac only,
        and an app that couldn't be built. Each in a fresh home."""
        def install(setup):
            return self.fresh_install(setup)[1].stdout
        icloud = self.icloud
        out = install(lambda h: os.makedirs(icloud(h)))
        self.assertIn("Law Notes is empty on this Mac", out)
        self.assertNotIn("UCL", out)
        def local_notes(h):
            os.makedirs(icloud(h))
            os.makedirs(os.path.join(h, "UCL", "notes"))
            open(os.path.join(h, "UCL", "notes", "Tort.md"), "w").write("# Tort\n")
        out = install(local_notes)
        self.assertIn("Notes: 1 in", out)
        self.assertIn("on this Mac only, not in iCloud Drive", out)
        def foreign_app(h):
            contents = os.path.join(h, "Applications", "Law Notes.app", "Contents")
            os.makedirs(contents)
            open(os.path.join(contents, "Info.plist"), "w").write(
                '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key>'
                '<string>com.example.other</string></dict></plist>')
        out = install(foreign_app)
        self.assertIn("isn't Law Notes", out)
        self.assertNotIn("Open it from Spotlight", out)

    def test_1c_existing_ucl_install_is_untouched(self):
        """The author's Mac: ~/UCL/notes is a link to iCloud Drive/UCL Notes, with notes and dotfiles.
        Installing, updating and uninstalling change nothing there, and no Law Notes folder appears."""
        def author(h):
            real = os.path.join(self.icloud(h), "UCL Notes")
            os.makedirs(os.path.join(real, "Tort"))
            os.makedirs(os.path.join(real, ".themes"))
            for name, text in (("Tort/Duty.md", "# Duty\n"), (".abbreviations.txt", "CL = Common Law\n"),
                               (".settings.json", "{}"), (".themes/Mine", "bg #000000\n")):
                with open(os.path.join(real, name), "w") as f:
                    f.write(text)
            os.makedirs(os.path.join(h, "UCL"))
            os.symlink(real, os.path.join(h, "UCL", "notes"))
        watched = lambda h: (os.path.join(h, "UCL"), self.icloud(h))
        home = tempfile.mkdtemp(dir=self.tmp)
        author(home)
        before = self.snapshot(*watched(home))
        signers = os.path.join(home, "my_allowed_signers")  # uninstall deletes the signers file: not the shared one
        shutil.copy(self.signers, signers)
        env = {**self.env, "HOME": home, "LAWNOTES_SIGNERS": signers}
        out = run("bash", os.path.join(self.work, "install.sh"), env=env).stdout
        self.assertIn("Notes: 1 in iCloud Drive/UCL Notes", out)
        self.assertIn("Your notes live in ~/UCL/notes", out)
        self.assertNotIn("Law Notes is empty", out)
        self.assertEqual(self.snapshot(*watched(home)), before)
        self.assertEqual(sorted(os.listdir(self.icloud(home))), ["UCL Notes"])
        self.assertFalse(os.path.lexists(os.path.join(home, "Law Notes")))
        self.assertFalse(os.path.lexists(os.path.join(home, ".config", "lawnotes", "notes_dir")))  # nothing recorded
        command = os.path.join(home, ".local", "bin", "lawnotes")
        self.assertEqual(run(sys.executable, command, "--where", env=env).stdout.strip(), os.path.join(home, "UCL", "notes"))
        run(sys.executable, command, "--update", env=env)  # an update: no prompt, no change to notes
        self.assertEqual(self.snapshot(*watched(home)), before)
        self.assertFalse(os.path.lexists(os.path.join(home, "Law Notes")))
        out = run(sys.executable, command, "--uninstall", env=env, stdin="").stdout  # uninstall, old folder name
        self.assertIn(f"Your notes ({os.path.join(home, 'UCL', 'notes')}) and their history are NOT touched", out)
        self.assertFalse(os.path.exists(os.path.join(home, ".local", "share", "lawnotes")))
        self.assertEqual(self.snapshot(*watched(home)), before)

    def test_1d_fresh_install_without_icloud(self):
        home, r = self.fresh_install()
        notes = os.path.join(home, "Law Notes")
        self.assertTrue(os.path.isdir(notes) and not os.path.islink(notes))
        self.assertIn("Notes: 0 in ~/Law Notes", r.stdout)
        self.assertIn("Your notes live in ~/Law Notes. To use another folder, run the installer again with", r.stdout)
        self.assertNotIn("UCL", r.stdout)
        self.assertFalse(os.path.lexists(os.path.join(home, "UCL")))
        with open(os.path.join(home, ".config", "lawnotes", "notes_dir")) as f:
            self.assertEqual(f.read(), notes + "\n")
        # the record keeps the choice even if a ~/UCL/notes folder turns up later
        os.makedirs(os.path.join(home, "UCL", "notes"))
        command = os.path.join(home, ".local", "bin", "lawnotes")
        self.assertEqual(run(sys.executable, command, "--where", env={**self.env, "HOME": home}).stdout.strip(), notes)

    def test_1e2_new_mac_with_the_ucl_notes_already_in_icloud(self):
        """The README's flow: a new Mac, iCloud Drive synced, UCL Notes already there. The old link is made."""
        def synced(h):
            real = os.path.join(self.icloud(h), "UCL Notes")
            os.makedirs(real)
            for name in ("Tort.md", "Contract.md"):
                with open(os.path.join(real, name), "w") as f:
                    f.write("# " + name + "\n")
        home = tempfile.mkdtemp(dir=self.tmp)
        synced(home)
        real = os.path.join(self.icloud(home), "UCL Notes")
        before = self.snapshot(self.icloud(home))
        env = {**self.env, "HOME": home}
        r = run("bash", os.path.join(self.work, "install.sh"), env=env)
        self.assertEqual(os.readlink(os.path.join(home, "UCL", "notes")), real)
        self.assertIn("Notes: 2 in iCloud Drive/UCL Notes", r.stdout)
        self.assertIn("Your notes live in ~/UCL/notes", r.stdout)
        self.assertNotIn("is empty on this Mac", r.stdout)
        self.assertEqual(self.snapshot(self.icloud(home)), before)  # notes unchanged, nothing added beside them
        self.assertEqual(os.listdir(self.icloud(home)), ["UCL Notes"])
        self.assertFalse(os.path.lexists(os.path.join(home, "Law Notes")))
        self.assertFalse(os.path.lexists(os.path.join(home, ".config", "lawnotes", "notes_dir")))
        command = os.path.join(home, ".local", "bin", "lawnotes")
        self.assertEqual(run(sys.executable, command, "--where", env=env).stdout.strip(), os.path.join(home, "UCL", "notes"))
        probe = "import sys; sys.path.insert(0, %r); import %s; print(%s)"
        for module, expr in (("lawnotes", "lawnotes.NOTES_DIR"), ("update", "update.NOTES")):
            out = run(sys.executable, "-B", "-c", probe % (self.work, module, expr), env=env).stdout.strip()
            self.assertEqual(out, os.path.join(home, "UCL", "notes"), module)
        # iCloud Drive without a UCL Notes folder: the new default, as before
        home, r = self.fresh_install(lambda h: os.makedirs(os.path.join(self.icloud(h), "Other")))
        self.assertTrue(os.path.islink(os.path.join(home, "Law Notes")))
        self.assertFalse(os.path.lexists(os.path.join(home, "UCL")))

    def test_1e_fresh_install_with_icloud_drive(self):
        home, r = self.fresh_install(lambda h: os.makedirs(self.icloud(h)))
        notes = os.path.join(home, "Law Notes")
        shared = os.path.join(self.icloud(home), "Law Notes")
        self.assertEqual(os.readlink(notes), shared)
        self.assertIn("Notes: 0 in iCloud Drive/Law Notes", r.stdout)
        self.assertIn("Law Notes is empty on this Mac", r.stdout)
        self.assertIn("right-click iCloud Drive → Law Notes → Keep Downloaded", r.stdout)
        self.assertIn("Your notes live in ~/Law Notes", r.stdout)
        self.assertNotIn("UCL", r.stdout)
        self.assertEqual(os.listdir(self.icloud(home)), ["Law Notes"])  # beside the History and Logs folders, not in them

    def test_1f_lawnotes_dir_at_install_is_recorded(self):
        # a folder of the student's choice: used as it is, never linked into iCloud Drive
        chosen = os.path.join(self.tmp, "chosen", "Study Notes")
        home, r = self.fresh_install(lambda h: os.makedirs(self.icloud(h)), LAWNOTES_DIR=chosen)
        self.assertTrue(os.path.isdir(chosen) and not os.path.islink(chosen))
        self.assertIn(f"Notes: 0 in {chosen}", r.stdout)
        self.assertFalse(os.path.lexists(os.path.join(home, "Law Notes")))
        self.assertEqual(os.listdir(self.icloud(home)), [])
        with open(os.path.join(home, ".config", "lawnotes", "notes_dir")) as f:
            self.assertEqual(f.read(), chosen + "\n")
        command = os.path.join(home, ".local", "bin", "lawnotes")
        env = {k: v for k, v in {**self.env, "HOME": home}.items() if k != "LAWNOTES_DIR"}  # later runs: no variable
        self.assertEqual(run(sys.executable, command, "--where", env=env).stdout.strip(), chosen)
        # asking for the old author's folder on a new Mac recreates the old link to UCL Notes
        home, r = self.fresh_install(lambda h: os.makedirs(self.icloud(h)), LAWNOTES_DIR="~/UCL/notes")
        self.assertEqual(os.readlink(os.path.join(home, "UCL", "notes")), os.path.join(self.icloud(home), "UCL Notes"))
        self.assertIn("Notes: 0 in iCloud Drive/UCL Notes", r.stdout)

    def test_1g_unusable_lawnotes_dir_stops_the_install(self):
        for bad in ("relative/folder", "/", "/tmp/x\x1b[2J"):
            home, r = self.fresh_install(check=False, LAWNOTES_DIR=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("LAWNOTES_DIR must be a full folder path", r.stderr)
            self.assertFalse(os.path.lexists(os.path.join(home, ".config", "lawnotes", "notes_dir")), bad)
            self.assertFalse(os.path.lexists(os.path.join(home, "Law Notes")), bad)
            self.assertFalse(os.path.lexists(os.path.join(home, ".local", "bin", "lawnotes")), bad)

    def test_1h_hostile_record_is_ignored_by_the_installer(self):
        def hostile(h):
            os.makedirs(os.path.join(h, ".config", "lawnotes"))
            with open(os.path.join(h, ".config", "lawnotes", "notes_dir"), "w") as f:
                f.write("../../escape\x1b[2J\n")
        home, r = self.fresh_install(hostile)
        self.assertIn("Notes: 0 in ~/Law Notes", r.stdout)
        self.assertTrue(os.path.isdir(os.path.join(home, "Law Notes")))
        self.assertFalse(os.path.lexists(os.path.join(self.tmp, "escape")))

    def test_2_signed_update(self):
        self.release("v9.0.1", change=lambda w: open(os.path.join(w, "README.md"), "a").write("\n9.0.1\n"))
        r = self.lawnotes_cmd("--update")
        self.assertIn("Updated to v9.0.1", r.stdout)
        self.assertEqual(self.current(), "v9.0.1")

    def test_3_unsigned_release_refused(self):
        self.release("v9.0.2", signed=False, change=lambda w: open(os.path.join(w, "README.md"), "a").write("x\n"))
        r = self.lawnotes_cmd("--update", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not signed", r.stderr)
        self.assertEqual(self.current(), "v9.0.1")

    def test_3b_wrong_key_and_moved_tag_refused(self):
        other = os.path.join(self.tmp, "attacker_key")
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "attacker", "-f", other)
        def evil(w):
            open(os.path.join(w, "README.md"), "a").write("evil\n")
        evil(self.work)
        self.git("commit", "-q", "-am", "evil")
        self.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={other}", "tag", "-s", "v9.0.2a", "-m", "x")
        # a well-formed version tag signed with a key this Mac doesn't trust
        self.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={other}", "tag", "-s", "v9.9.9", "-m", "x")
        self.git("push", "-q", "origin", "main", "v9.9.9")
        r = self.lawnotes_cmd("--update", check=False)
        self.assertIn("not signed with the Law Notes release key", r.stderr)
        self.assertEqual(self.current(), "v9.0.1")
        # moving an already-installed tag to other code is ignored
        repo = os.path.join(self.share, "repo.git")
        before = run("git", f"--git-dir={repo}", "rev-parse", "v9.0.1^{commit}").stdout.strip()
        self.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={self.key}", "tag", "-f", "-s", "v9.0.1", "-m", "moved")
        self.git("push", "-q", "-f", "origin", "v9.0.1")
        self.lawnotes_cmd("--update", check=False)
        self.assertEqual(run("git", f"--git-dir={repo}", "rev-parse", "v9.0.1^{commit}").stdout.strip(), before)
        # clean up so later tests see a sane origin
        self.git("push", "-q", "origin", ":refs/tags/v9.9.9")
        run("git", f"--git-dir={repo}", "tag", "-d", "v9.9.9")
        self.git("tag", "-d", "v9.9.9", "v9.0.2a")
        state = os.path.join(self.share, "state.json")
        st = json.load(open(state))
        st["failed"] = [t for t in st.get("failed", []) if t != "v9.9.9"]
        json.dump(st, open(state, "w"))

    def test_4_broken_release_refused(self):
        def break_it(w):
            with open(os.path.join(w, "lawnotes.py"), "a") as f:
                f.write("\nthis is not python (\n")
        self.release("v9.0.3", change=break_it)
        r = self.lawnotes_cmd("--update", check=False)
        self.assertIn("self-test failed", r.stderr)
        self.assertEqual(self.current(), "v9.0.1")
        self.assertEqual(self.lawnotes_cmd("--self-test").returncode, 0)  # still works

    def test_5_daily_update_and_message(self):
        def fix_it(w):
            src = open(os.path.join(w, "lawnotes.py")).read().replace("\nthis is not python (\n", "\n")
            open(os.path.join(w, "lawnotes.py"), "w").write(src)
        self.release("v9.0.4", change=fix_it)
        os.remove(os.path.join(self.home, ".cache", "lawnotes", "last-update-check"))  # as if a day passed
        run(sys.executable, os.path.join(self.share, "current", "update.py"), "--daily", env=self.env)
        self.assertEqual(self.current(), "v9.0.4")
        msg = open(os.path.join(self.home, ".cache", "lawnotes", "update-message")).read()
        self.assertIn("v9.0.4", msg)
        # a second daily run the same day does nothing (no fetch)
        self.release("v9.0.5", change=lambda w: open(os.path.join(w, "README.md"), "a").write("y\n"))
        run(sys.executable, os.path.join(self.share, "current", "update.py"), "--daily", env=self.env)
        self.assertEqual(self.current(), "v9.0.4")

    def test_5b_new_version_switches_itself(self):
        def mark(w):
            src = open(os.path.join(w, "update.py")).read().replace(
                'def switch(tag):\n', 'def switch(tag):\n    print("switched by the new version")\n', 1)
            open(os.path.join(w, "update.py"), "w").write(src)
        self.release("v9.0.6", change=mark)
        r = self.lawnotes_cmd("--update")
        self.assertEqual(self.current(), "v9.0.6")
        state = open(os.path.join(self.share, "state.json")).read()
        self.assertIn('"current": "v9.0.6"', state)
        self.assertIn('"previous": "v9.0.4"', state)
        self.release("v9.0.7", change=lambda w: open(os.path.join(w, "README.md"), "a").write("z\n"))
        # and a failing switch puts everything back
        def broken_switch(w):
            src = open(os.path.join(w, "update.py")).read().replace(
                "        point_current(tag)\n        refresh(tag)\n",
                "        point_current(tag)\n        raise RuntimeError('boom')\n", 1)
            open(os.path.join(w, "update.py"), "w").write(src)
        self.release("v9.0.8", change=broken_switch)
        r = self.lawnotes_cmd("--update", check=False)
        self.assertIn("switching failed", r.stderr)
        self.assertEqual(self.current(), "v9.0.6")
        self.assertIn('"current": "v9.0.6"', open(os.path.join(self.share, "state.json")).read())
        def fix_switch(w):
            src = open(os.path.join(w, "update.py")).read().replace(
                "        point_current(tag)\n        raise RuntimeError('boom')\n",
                "        point_current(tag)\n        refresh(tag)\n", 1)
            open(os.path.join(w, "update.py"), "w").write(src)
        self.release("v9.0.9", change=fix_switch)

    def test_6_rollback(self):
        r = self.lawnotes_cmd("--rollback")
        self.assertIn("Back on v9.0.4", r.stdout)
        self.assertEqual(self.current(), "v9.0.4")
        r = self.lawnotes_cmd("--update")  # skips the rolled-back v9.0.6, takes the newest, v9.0.9
        self.assertEqual(self.current(), "v9.0.9")

    def test_7_truncated_installer_runs_nothing(self):
        text = open(os.path.join(self.work, "install.sh")).read()
        home = os.path.join(self.tmp, "home-truncated")
        os.makedirs(home)
        r = run("bash", env={**self.env, "HOME": home}, stdin=text[: len(text) // 2], check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(home), [])

    def test_8_uninstall_keeps_notes(self):
        notes = os.path.realpath(os.path.join(self.home, "Law Notes"))
        os.makedirs(notes, exist_ok=True)
        open(os.path.join(notes, "Keep.md"), "w").write("# Keep\n")
        out = run(sys.executable, self.lawnotes, "--uninstall", env=self.env, stdin="").stdout
        self.assertIn(f"Your notes ({os.path.join(self.home, 'Law Notes')}) and their history are NOT touched", out)
        self.assertTrue(os.path.exists(os.path.join(self.home, ".config", "lawnotes", "notes_dir")))  # the choice is kept
        self.assertFalse(os.path.exists(self.share))
        self.assertFalse(os.path.lexists(self.lawnotes))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Applications", "Law Notes.app")))
        self.assertTrue(os.path.exists(os.path.join(notes, "Keep.md")))


if __name__ == "__main__":
    unittest.main()
