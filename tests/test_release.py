"""Install, update and rollback tests against a throwaway "GitHub" with a throwaway release key.

Run: python3 -m unittest discover -s tests -v
Everything happens in a temporary HOME; the real install, app and notes are never touched.
"""
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
        self.assertIn(os.path.join(self.share, "current", "lawnotes") + "' --here", text)
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

    def test_6_rollback(self):
        r = self.lawnotes_cmd("--rollback")
        self.assertIn("Back on v9.0.1", r.stdout)
        self.assertEqual(self.current(), "v9.0.1")
        r = self.lawnotes_cmd("--update")  # skips the rolled-back v9.0.4, takes v9.0.5
        self.assertEqual(self.current(), "v9.0.5")

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
