"""Check the installed bootstrap generator's quoting and credential handling."""

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest


PACKAGE = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "bootstrap.toml"

    def generate(self, password, username="alice"):
        environment = dict(os.environ)
        environment.update(
            CONTINUWUITY_BOOTSTRAP_USER=username,
            CONTINUWUITY_BOOTSTRAP_PASSWORD=password,
        )
        return subprocess.run(
            [sys.executable, str(PACKAGE / "conf/bootstrap.py"), str(self.path)],
            env=environment, capture_output=True, text=True,
        )

    def test_punctuation_is_literal_and_password_is_not_printed(self):
        password = '-quote\'"dollar$back`tick;$(unused)back\\slash日本語'
        result = self.generate(password)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")
        commands = tomllib.loads(self.path.read_text())["global"]["admin_execute"]
        self.assertEqual(commands[0].split(), ["users", "create-user", "--", "alice", password])
        self.assertEqual(commands[1], "server shutdown")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_whitespace_is_rejected_without_partial_output(self):
        for password in ("space password", "line\nbreak", "tab\tpassword", "unicode\u2003space", ""):
            with self.subTest(password=repr(password)):
                result = self.generate(password)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.path.exists())
                if password:
                    self.assertNotIn(password, result.stdout + result.stderr)

    def test_command_injection_in_username_is_rejected(self):
        result = self.generate("test-password-123", "alice server shutdown")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.path.exists())

    def test_existing_file_is_not_overwritten(self):
        self.path.write_text("retain existing content")
        result = self.generate("test-password-123")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_text(), "retain existing content")


if __name__ == "__main__":
    unittest.main()
