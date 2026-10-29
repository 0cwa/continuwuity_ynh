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
systemctl() {
    printf '%s\n' "$*" >> "$TEST_ROOT/systemctl.log"
    if [[ "${FAIL_CLEANUP_STOP:-0}" == 1 && -e "$TEST_ROOT/service-started" ]]; then
        return 22
    fi
}
ynh_systemctl() {
    local argument action=
    for argument in "$@"; do
        case "$argument" in --action=*) action="${argument#*=}" ;; esac
    done
    if [[ "$action" == start ]]; then
        # Simulate a systemd start that launches the process but returns an
        # error before readiness can be checked.
        : > "$TEST_ROOT/service-started"
        [[ "${FAIL_START_AFTER_LAUNCH:-0}" != 1 ]] || return 21
    fi
}
_continuwuity_dex_load() {
    [[ "${DEX_BROKEN:-0}" != 1 ]] || ynh_die "Dex is not available."
}
_continuwuity_dex_discovery_check() { printf '{}\n' > "$1"; }
find() {
    if [[ "${FAIL_ARCHIVE_FIND:-0}" == 1 \
        && "$1" == "$YNH_APP_BACKUP_DIR$data_dir/media" ]]; then
        printf '%s/partial-result\n' "$1"
        return 42
    fi
    command find "$@"
}
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
    : > "$TEST_ROOT/service-started"
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

    def test_failed_service_stop_during_rollback_retains_original_outside_live_data(self):
        result = self.invoke(FAIL_AT="ready", FAIL_CLEANUP_STOP="1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("Could not stop continuwuity.service", result.stderr)
        self.assertIn("Automatic data rollback failed", result.stderr)

        rollback_dirs = list(self.data.parent.glob(".continuwuity-restore-rollback.*"))
        self.assertEqual(len(rollback_dirs), 1)
        self.assertEqual(
            (rollback_dirs[0] / "original" / "CURRENT").read_text(),
            "original database\n",
        )
        # Keep the possibly live replacement where the daemon may still be
        # writing; do not delete or rename it after systemd failed to stop it.
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")
        self.assertFalse((rollback_dirs[0] / "failed-data").exists())

    def test_start_error_after_partial_launch_and_failed_stop_preserve_both_trees(self):
        result = self.invoke(
            FAIL_START_AFTER_LAUNCH="1", FAIL_CLEANUP_STOP="1"
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("Could not stop continuwuity.service", result.stderr)
        self.assertIn("Automatic data rollback failed", result.stderr)

        rollback_dirs = list(self.data.parent.glob(".continuwuity-restore-rollback.*"))
        self.assertEqual(len(rollback_dirs), 1)
        self.assertEqual(
            (rollback_dirs[0] / "original" / "CURRENT").read_text(),
            "original database\n",
        )
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")

    def test_bad_dex_is_rejected_before_touching_existing_data(self):
        result = self.invoke(auth_type="dex", DEX_BROKEN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_data()
        self.assertEqual(
            (self.install / "continuwuity").read_text(), "original binary\n"
        )
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

    def test_missing_empty_or_symlink_rocksdb_marker_preserves_original_data(self):
        external_marker = self.root / "external-CURRENT"
        external_marker.write_text("outside archive data\n")
        for mode in ("missing", "empty", "symlink"):
            with self.subTest(mode=mode):
                marker = self.snapshot / "CURRENT"
                marker.unlink(missing_ok=True)
                if mode == "empty":
                    marker.write_text("")
                elif mode == "symlink":
                    marker.symlink_to(external_marker)
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RocksDB CURRENT marker", result.stderr)
                self.assert_original_data()
                self.assertEqual((self.install / "continuwuity").read_text(), "original binary\n")
                self.assertFalse((self.root / "systemctl.log").exists())
                marker.unlink(missing_ok=True)
                marker.write_text("restored database\n")

    def test_symlinked_archived_media_is_rejected_before_replacement(self):
        external = self.root / "archive-external"
        external.mkdir()
        (self.snapshot / "media" / "new.bin").unlink()
        (self.snapshot / "media").rmdir()
        (self.snapshot / "media").symlink_to(external, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archived media directory is a symlink", result.stderr)
        self.assert_original_data()
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_nested_symlinked_archived_media_is_rejected_before_replacement(self):
        external = self.root / "archive-external-media"
        external.mkdir()
        (self.snapshot / "media" / "remote").symlink_to(
            external, target_is_directory=True
        )

        result = self.invoke()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archived media tree contains a symlink", result.stderr)
        self.assert_original_data()
        self.assertEqual((self.install / "continuwuity").read_text(), "original binary\n")
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_archived_media_scan_failure_with_partial_output_preserves_existing_data(self):
        result = self.invoke(FAIL_ARCHIVE_FIND="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot inspect archived media", result.stderr)
        self.assert_original_data()
        self.assertEqual((self.install / "continuwuity").read_text(), "original binary\n")
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

    def test_nested_symlinked_media_is_rejected_before_replacing_existing_data(self):
        external = self.root / "external-media"
        external.mkdir()
        (self.data / "media" / "remote").symlink_to(
            external, target_is_directory=True
        )

        result = self.invoke()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Symlinked media", result.stderr)
        self.assert_original_data()
        self.assertEqual((self.install / "continuwuity").read_text(), "original binary\n")
        self.assertFalse((self.root / "systemctl.log").exists())


if __name__ == "__main__":
    unittest.main()
