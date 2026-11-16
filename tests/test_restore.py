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
python3() {
    if [[ "${1:-}" == "$YNH_APP_BASEDIR/conf/s3_snapshot.py" ]]; then
        local operation="$2" manifest="${4:-}"
        printf 's3:%s\n' "$operation" >> "$TEST_ROOT/events.log"
        [[ -s "$manifest" ]] || return 41
        if [[ "$operation" == restore ]]; then
            [[ -n "${media_s3_restore_bucket:-}" && -n "${media_s3_restore_key_prefix:-}" ]] || return 43
            [[ "${FAIL_S3_VERIFY:-0}" != 1 ]] || return 42
            printf 'target:%s/%s\n' "$media_s3_restore_bucket" "$media_s3_restore_key_prefix" >> "$TEST_ROOT/events.log"
            [[ "${FAIL_S3_RESTORE:-0}" != 1 ]] || return 44
        fi
        return 0
    fi
    command python3 "$@"
}
ynh_app_setting_set() {
    local key= value= argument
    for argument in "$@"; do
        case "$argument" in
            --key=*) key="${argument#*=}" ;;
            --value=*) value="${argument#*=}" ;;
        esac
    done
    printf '%s=%s\n' "$key" "$value" >> "$TEST_ROOT/settings.log"
    if [[ "${FAIL_SETTING_KEY:-}" == "$key" ]]; then
        unset FAIL_SETTING_KEY
        return 45
    fi
}
systemctl() {
    printf '%s\n' "$*" >> "$TEST_ROOT/systemctl.log"
    printf 'systemctl:%s\n' "$*" >> "$TEST_ROOT/events.log"
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
df() {
    if [[ "${FAIL_CAPACITY:-0}" == 1 ]]; then
        printf 'Filesystem 1B-blocks Used Available Capacity Mounted\n'
        printf 'limited 1000 999 1 99%% /\n'
    else
        command df "$@"
    fi
}
find() {
    if [[ "${FAIL_ARCHIVE_FIND:-0}" == 1 \
        && "$1" == "$YNH_APP_BACKUP_DIR$data_dir/media" ]]; then
        printf '%s/partial-result\n' "$1"
        return 42
    fi
    command find "$@"
}
ynh_restore() {
    printf 'ynh_restore:%s\n' "$1" >> "$TEST_ROOT/events.log"
    if [[ "$1" == "$install_dir" ]]; then
        if [[ -f "$YNH_APP_BACKUP_DIR$install_dir/config.toml" ]]; then
            cp -- "$YNH_APP_BACKUP_DIR$install_dir/config.toml" "$install_dir/config.toml"
        fi
        if [[ -f "$YNH_APP_BACKUP_DIR$install_dir/overrides.toml" ]]; then
            cp -- "$YNH_APP_BACKUP_DIR$install_dir/overrides.toml" "$install_dir/overrides.toml"
        fi
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
    if [[ "${media_backend:-local}" == s3 ]]; then
        printf '[global]\nmedia_backend = "s3"\n[global.media_s3]\nbucket = "%s"\nkey_prefix = "%s"\n' \
            "${media_s3_restore_bucket:-$media_s3_bucket}" \
            "${media_s3_restore_key_prefix:-$media_s3_key_prefix}" \
            > "$install_dir/config.toml"
    else
        printf '[global]\n' > "$install_dir/config.toml"
    fi
    if [[ ! -f "$install_dir/overrides.toml" ]]; then
        printf '# generated overrides\n' > "$install_dir/overrides.toml"
    fi
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
        self.archive_install = self.backup / self.install.relative_to("/")
        self.archive_install.mkdir(parents=True)
        (self.archive_install / "config.toml").write_text("[global]\n")
        (self.archive_install / "overrides.toml").write_text("# archived overrides\n")

    def invoke(self, **variables):
        env = {
            **os.environ,
            "PACKAGE": str(PACKAGE),
            "YNH_APP_BASEDIR": str(PACKAGE),
            "TEST_ROOT": str(self.root),
            "app": "continuwuity",
            "data_dir": str(self.data),
            "install_dir": str(self.install),
            "YNH_APP_BACKUP_DIR": str(self.backup),
            "auth_type": "password",
            "media_backend": "local",
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

    def assert_original_database(self):
        self.assertEqual((self.data / "CURRENT").read_text(), "original database\n")
        self.assertFalse((self.data / "media" / "new.bin").exists())

    def prepare_s3_archive(self):
        (self.snapshot / "media" / "new.bin").unlink(missing_ok=True)
        (self.archive_install / "config.toml").write_text(
            '[global]\nmedia_backend = "s3"\n'
            '[global.media_s3]\nkey_prefix = "continuwuity/media"\n'
        )
        (self.backup / "continuwuity-s3-media.json").write_text(
            '{"schema_version":1,"generation":"20260101T000000Z-' + "0" * 32 + '"}\n'
        )
        (self.data / "media" / "original.bin").unlink(missing_ok=True)
        (self.install / "config.toml").write_text(
            '[global]\nmedia_backend = "s3"\n'
            '[global.media_s3]\nkey_prefix = "continuwuity/media"\n'
        )

    @staticmethod
    def s3_settings():
        return {
            "media_backend": "s3",
            "media_s3_endpoint": "https://s3.example.test",
            "media_s3_region": "us-east-1",
            "media_s3_bucket": "live-media",
            "media_s3_key_prefix": "continuwuity/media",
            "media_s3_backup_bucket": "media-snapshots",
            "media_s3_access_key_id": "access-secret-test",
            "media_s3_secret_access_key": "private-secret-test",
        }

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

    def test_s3_restore_copies_to_new_prefix_before_switch_and_persists_after_ready(self):
        self.prepare_s3_archive()
        settings = self.s3_settings()
        settings["media_backend"] = "local"
        result = self.invoke(**settings)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = (self.root / "events.log").read_text().splitlines()
        self.assertEqual(events[0], "s3:restore")
        self.assertTrue(events[1].startswith("target:live-media/.ynh-restore-"))
        self.assertLess(events.index("s3:restore"), events.index(f"ynh_restore:{self.install}"))
        config = (self.install / "config.toml").read_text()
        self.assertIn('media_backend = "s3"', config)
        target_prefix = next(
            line.split("=", 1)[1]
            for line in (self.root / "settings.log").read_text().splitlines()
            if line.startswith("media_s3_key_prefix=")
        )
        self.assertTrue(target_prefix.startswith(".ynh-restore-"))
        self.assertIn(f'key_prefix = "{target_prefix}"', config)
        self.assertIn(f"media_s3_key_prefix={target_prefix}", (self.root / "settings.log").read_text())
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")
        self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_missing_s3_manifest_fails_before_local_mutation(self):
        self.prepare_s3_archive()
        (self.backup / "continuwuity-s3-media.json").unlink()
        result = self.invoke(**self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no matching media generation manifest", result.stderr)
        self.assert_original_database()
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_unsafe_current_config_fails_before_remote_restore(self):
        self.prepare_s3_archive()
        config = self.install / "config.toml"
        config.unlink()
        external = self.root / "external-config.toml"
        external.write_text("old config\n")
        config.symlink_to(external)
        result = self.invoke(**self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing Continuwuity config is not a regular file", result.stderr)
        self.assert_original_database()
        self.assertTrue(config.is_symlink())
        self.assertEqual(external.read_text(), "old config\n")
        self.assertFalse((self.root / "events.log").exists())
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_tampered_s3_snapshot_fails_before_local_mutation(self):
        self.prepare_s3_archive()
        old_config = (self.install / "config.toml").read_text()
        result = self.invoke(FAIL_S3_VERIFY="1", **self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not restore and verify the S3 media generation", result.stderr)
        self.assert_original_database()
        self.assertEqual((self.install / "config.toml").read_text(), old_config)
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_s3_copy_failure_leaves_current_data_and_config_untouched(self):
        self.prepare_s3_archive()
        old_config = (self.install / "config.toml").read_text()
        result = self.invoke(FAIL_S3_RESTORE="1", **self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty target prefix", result.stderr)
        self.assert_original_database()
        self.assertEqual((self.install / "config.toml").read_text(), old_config)
        self.assertFalse((self.root / "systemctl.log").exists())

    def test_failed_s3_start_restores_old_database_and_config_and_keeps_remote_prefix(self):
        self.prepare_s3_archive()
        old_config = (self.install / "config.toml").read_text()
        result = self.invoke(FAIL_AT="ready", **self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original data recovered", result.stderr)
        self.assertIn("was left untouched and may be an orphan", result.stderr)
        self.assertEqual((self.data / "CURRENT").read_text(), "original database\n")
        self.assertEqual((self.install / "config.toml").read_text(), old_config)
        self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_failed_s3_cleanup_stop_preserves_both_local_trees_and_marks_prefix_in_use(self):
        self.prepare_s3_archive()
        old_config = (self.install / "config.toml").read_text()
        result = self.invoke(
            FAIL_AT="ready", FAIL_CLEANUP_STOP="1", **self.s3_settings()
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not stop continuwuity.service", result.stderr)
        self.assertIn("may still be in use by continuwuity.service; do not delete it", result.stderr)
        rollback_dirs = list(self.data.parent.glob(".continuwuity-restore-rollback.*"))
        self.assertEqual(len(rollback_dirs), 1)
        self.assertEqual((rollback_dirs[0] / "original" / "CURRENT").read_text(), "original database\n")
        self.assertEqual((rollback_dirs[0] / "original-config.toml").read_text(), old_config)
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")
        self.assertNotEqual((self.install / "config.toml").read_text(), old_config)

    def test_failed_s3_setting_persist_rolls_back_prefix_and_generated_config(self):
        self.prepare_s3_archive()
        old_config = (self.install / "config.toml").read_text()
        result = self.invoke(FAIL_SETTING_KEY="media_s3_key_prefix", **self.s3_settings())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original data recovered", result.stderr)
        self.assertEqual((self.data / "CURRENT").read_text(), "original database\n")
        self.assertEqual((self.install / "config.toml").read_text(), old_config)
        self.assertIn("media_s3_key_prefix=continuwuity/media", (self.root / "settings.log").read_text())
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

    def test_failed_local_restore_restores_preexisting_config_and_overrides(self):
        config = self.install / "config.toml"
        overrides = self.install / "overrides.toml"
        config.write_text("# previous live config\n")
        overrides.write_text("# previous live overrides\n")

        result = self.invoke(FAIL_AT="ready")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assert_original_database()
        self.assertEqual(config.read_text(), "# previous live config\n")
        self.assertEqual(overrides.read_text(), "# previous live overrides\n")
        self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_failed_local_restore_removes_config_and_overrides_that_were_absent(self):
        config = self.install / "config.toml"
        overrides = self.install / "overrides.toml"
        config.unlink(missing_ok=True)
        overrides.unlink(missing_ok=True)

        result = self.invoke(FAIL_AT="ready")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assert_original_database()
        self.assertFalse(config.exists())
        self.assertFalse(config.is_symlink())
        self.assertFalse(overrides.exists())
        self.assertFalse(overrides.is_symlink())
        self.assertFalse(list(self.data.parent.glob(".continuwuity-restore-rollback.*")))

    def test_failed_service_stop_during_rollback_retains_original_outside_live_data(self):
        result = self.invoke(FAIL_AT="ready", FAIL_CLEANUP_STOP="1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("Could not stop continuwuity.service", result.stderr)
        self.assertIn("Automatic rollback failed", result.stderr)

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
        self.assertIn("Automatic rollback failed", result.stderr)

        rollback_dirs = list(self.data.parent.glob(".continuwuity-restore-rollback.*"))
        self.assertEqual(len(rollback_dirs), 1)
        self.assertEqual(
            (rollback_dirs[0] / "original" / "CURRENT").read_text(),
            "original database\n",
        )
        self.assertEqual((self.data / "CURRENT").read_text(), "restored database\n")

    def test_insufficient_space_for_both_copies_is_rejected_before_touching_data(self):
        result = self.invoke(FAIL_CAPACITY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Insufficient free space", result.stderr)
        self.assert_original_data()
        self.assertFalse((self.root / "systemctl.log").exists())

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
