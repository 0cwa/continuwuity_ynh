"""Reinstalling over data retained by a non-purging removal must be safe."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -e
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_print_warn() { printf 'WARN: %s\n' "$*" >&2; }
builtin source "$PACKAGE/scripts/_service.sh"
continuwuity_retire_retained_data
'''


class ReinstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "continuwuity"

    def run_retire(self):
        return subprocess.run(
            ["bash", "-c", HARNESS],
            env={**os.environ, "PACKAGE": str(PACKAGE), "data_dir": str(self.data)},
            capture_output=True, text=True,
        )

    def retired(self):
        return sorted(self.root.glob("continuwuity.preinstall-*"))

    def test_nonempty_data_is_moved_aside_intact_with_warning(self):
        (self.data / "rocksdb").mkdir(parents=True)
        (self.data / "rocksdb" / "CURRENT").write_text("old identity")
        self.data.chmod(0o750)
        result = self.run_retire()
        self.assertEqual(result.returncode, 0, result.stderr)
        moved = self.retired()
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0] / "rocksdb" / "CURRENT").read_text(), "old identity")
        self.assertEqual(list(self.data.iterdir()), [])
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o750)
        self.assertIn("WARN:", result.stderr)
        self.assertIn(str(moved[0]), result.stderr)
        self.assertIn("restore", result.stderr)

    def test_empty_or_missing_data_is_left_alone_without_warning(self):
        self.assertEqual(self.run_retire().returncode, 0)
        self.assertFalse(self.data.exists())
        self.data.mkdir()
        result = self.run_retire()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.retired(), [])
        self.assertNotIn("WARN", result.stderr)

    def test_symlinked_data_dir_is_never_moved(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "file").write_text("x")
        self.data.symlink_to(target)
        self.assertEqual(self.run_retire().returncode, 0)
        self.assertEqual(self.retired(), [])
        self.assertTrue((target / "file").exists())

    def test_install_moves_data_before_the_new_identity_is_created(self):
        install = (PACKAGE / "scripts/install").read_text()
        self.assertNotIn("Retained data exists", install)
        self.assertLess(
            install.index("continuwuity_retire_retained_data"),
            install.index("continuwuity_bootstrap_admin"),
        )


if __name__ == "__main__":
    unittest.main()
