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
        self.assertTrue(os.path.islink(os.path.join(self.home, "UCL", "notes")) or
                        os.path.isdir(os.path.join(self.home, "UCL", "notes")))
        self.assertIn("Law Notes", self.lawnotes_cmd("--version").stdout)

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
        notes = os.path.realpath(os.path.join(self.home, "UCL", "notes"))
        os.makedirs(notes, exist_ok=True)
        open(os.path.join(notes, "Keep.md"), "w").write("# Keep\n")
        run(sys.executable, self.lawnotes, "--uninstall", env=self.env, stdin="")
        self.assertFalse(os.path.exists(self.share))
        self.assertFalse(os.path.lexists(self.lawnotes))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Applications", "Law Notes.app")))
        self.assertTrue(os.path.exists(os.path.join(notes, "Keep.md")))


if __name__ == "__main__":
    unittest.main()
