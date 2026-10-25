"""Fail-safe restoration of preexisting RocksDB and media on a YunoHost host."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -e
source() {
    if [[ "$1" == "../settings/scripts/_backup.sh" ]]; then
        builtin source "$PACKAGE/scripts/_backup.sh"
    fi
}
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_print_warn() { printf 'WARN: %s\n' "$*" >&2; }
ynh_script_progression() { :; }
systemctl() { printf '%s\n' "$*" >> "$TEST_ROOT/systemctl.log"; }
ynh_systemctl() { :; }
_continuwuity_dex_load() {
    [[ "${DEX_BROKEN:-0}" != 1 ]] || ynh_die "Dex is not available."
}
_continuwuity_dex_discovery_check() { printf '{}\n' > "$1"; }
ynh_restore() {
    if [[ "$1" == "$install_dir" ]]; then
        printf 'new binary\n' > "$install_dir/continuwuity"
        [[ "${FAIL_AT:-}" != install ]] || return 17
    elif [[ "$1" == "$data_dir" ]]; then
        mkdir -p "$data_dir"
        cp -a -- "$YNH_APP_BACKUP_DIR$data_dir/." "$data_dir/"
        [[ "${FAIL_AT:-}" != data ]] || return 18
    else
        return 31
    fi
}
continuwuity_dex_setup() { :; }
continuwuity_configure() {
    [[ "${FAIL_AT:-}" != config ]] || return 19
}
continuwuity_permissions() { :; }
continuwuity_register_service() { :; }
continuwuity_wait_ready() {
    [[ "${FAIL_AT:-}" != ready ]] || return 20
}
builtin source "$PACKAGE/scripts/restore"
'''


class RestoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "local" / "continuwuity"
        self.data.mkdir(parents=True)
        (self.data / "CURRENT").write_text("original database\n")
        (self.data / "media").mkdir()
        (self.data / "media" / "original.bin").write_text("original media\n")
        self.backup = self.root / "backup"
        self.snapshot = self.backup / self.data.relative_to("/")
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "CURRENT").write_text("restored database\n")
        (self.snapshot / "media").mkdir()
        (self.snapshot / "media" / "new.bin").write_text("restored media\n")
        self.install = self.root / "install"
        self.install.mkdir()
        (self.install / "continuwuity").write_text("original binary\n")

    def invoke(self, **variables):
        env = {
            **os.environ,
            "PACKAGE": str(PACKAGE),
            "TEST_ROOT": str(self.root),
            "app": "continuwuity",
            "data_dir": str(self.data),
            "install_dir": str(self.install),
            "YNH_APP_BACKUP_DIR": str(self.backup),
            "auth_type": "password",
            **variables,
        }
        return subprocess.run(
            ["bash", "-c", HARNESS], capture_output=True, text=True, env=env
        )

    def assert_original_data(self):
        self.assertEqual((self.data / "CURRENT").read_text(), "original database\n")
        self.assertEqual(
            (self.data / "media" / "original.bin").read_text(), "original media\n"
        )
        self.assertFalse((self.data / "media" / "new.bin").exists())

    def test_old_daemon_is_stopped_before_renaming_database(self):
        script = (PACKAGE / "scripts/restore").read_text()
        self.assertLess(
            script.index('if [[ -e "/etc/systemd/system/$app.service" ]]'),
            script.index('mv -- "$data_dir" "$restore_rollback_dir/original"'),
        )

    def test_success_replaces_data_and_removes_original_rollback_copy(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")
        self.assertTrue((self.data / "media" / "new.bin").is_file())
        self.assertFalse((self.data / "media" / "original.bin").exists())
        self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_failures_after_swap_restore_original_database_and_media(self):
        for phase in ("install", "data", "config", "ready"):
            with self.subTest(phase=phase):
                # Restore original state for each isolated failure phase.
                self.data.mkdir(exist_ok=True)
                (self.data / "CURRENT").write_text("original database\n")
                (self.data / "media").mkdir(exist_ok=True)
                (self.data / "media" / "original.bin").write_text("original media\n")
                (self.data / "media" / "new.bin").unlink(missing_ok=True)
                result = self.invoke(FAIL_AT=phase)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assert_original_data()
                self.assertIn("Original data recovered", result.stderr)
                self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_bad_dex_is_rejected_before_touching_existing_data(self):
        result = self.invoke(auth_type="dex", DEX_BROKEN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_data()
        self.assertEqual((self.install / "continuwuity").read_text(), "original binary\n")
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_missing_data_snapshot_is_rejected_before_touching_data(self):
        (self.snapshot / "CURRENT").unlink()
        (self.snapshot / "media" / "new.bin").unlink()
        (self.snapshot / "media").rmdir()
        self.snapshot.rmdir()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_data()
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_symlinked_media_cannot_be_silently_archived_or_replaced(self):
        external = self.root / "external"
        external.mkdir()
        (self.data / "media" / "original.bin").unlink()
        (self.data / "media").rmdir()
        (self.data / "media").symlink_to(external, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Symlinked media", result.stderr)
        self.assertEqual((self.data / "CURRENT").read_text(), "original database\n")
        self.assertFalse((self.root / "systemctl.log").exists())


if __name__ == "__main__":
    unittest.main()
