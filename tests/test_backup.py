"""Exercise the real shell snapshot helper with a simulated systemd service."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -eu
source "$PACKAGE/scripts/_backup.sh"

ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_print_warn() { printf '%s\n' "$*" >&2; }
systemctl() {
    case "$1" in
        show) cat "$STATE_FILE" ;;
        stop)
            printf 'stop\n' >> "$EVENTS_FILE"
            [[ "${FAIL_STOP:-0}" != 1 ]] || return 17
            printf 'inactive\n' > "$STATE_FILE"
            ;;
        start)
            printf 'start\n' >> "$EVENTS_FILE"
            [[ "${FAIL_START:-0}" != 1 ]] || return 19
            printf 'active\n' > "$STATE_FILE"
            # Simulate the server writing immediately after restart.
            printf 'changed after restart\n' > "$data_dir/CURRENT"
            ;;
        *) return 99 ;;
    esac
}
ynh_systemctl() {
    local action= service= argument
    for argument in "$@"; do
        case "$argument" in
            --action=*) action="${argument#*=}" ;;
            --service=*) service="${argument#*=}" ;;
        esac
    done
    systemctl "$action" "$service"
}
cp() {
    printf 'copy:%s\n' "$(cat "$STATE_FILE")" >> "$EVENTS_FILE"
    [[ "$(cat "$STATE_FILE")" == inactive ]] || return 31
    [[ "${FAIL_COPY:-0}" != 1 ]] || return 23
    command cp "$@"
}

trap 'printf "parent-exit\n" >> "$EVENTS_FILE"' EXIT
continuwuity_snapshot_data
'''


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "source" / "continuwuity"
        self.data.mkdir(parents=True)
        (self.data / "CURRENT").write_text("original database\n")
        (self.data / "media").mkdir()
        (self.data / "media" / "example").write_bytes(b"media payload")
        self.backup = self.root / "backup"
        self.backup.mkdir()
        self.state = self.root / "state"
        self.state.write_text("active\n")
        self.events = self.root / "events"
        self.events.touch()
        self.snapshot = self.backup / str(self.data).lstrip("/")

    def run_snapshot(self, **overrides):
        env = dict(os.environ)
        env.update(
            PACKAGE=str(PACKAGE),
            app="continuwuity",
            data_dir=str(self.data),
            YNH_APP_BACKUP_DIR=str(self.backup),
            STATE_FILE=str(self.state),
            EVENTS_FILE=str(self.events),
        )
        env.update(overrides)
        return subprocess.run(
            ["bash", "-c", HARNESS], env=env, capture_output=True, text=True
        )

    def event_lines(self):
        return self.events.read_text().splitlines()

    def test_snapshot_is_copied_while_stopped_and_survives_live_writes(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.event_lines(), ["stop", "copy:inactive", "start", "parent-exit"]
        )
        self.assertEqual((self.snapshot / "CURRENT").read_text(), "original database\n")
        self.assertEqual((self.data / "CURRENT").read_text(), "changed after restart\n")
        self.assertEqual((self.snapshot / "media/example").read_bytes(), b"media payload")

    def test_previously_stopped_service_is_left_stopped(self):
        self.state.write_text("inactive\n")
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.event_lines(), ["stop", "copy:inactive", "parent-exit"])
        self.assertEqual(self.state.read_text(), "inactive\n")

    def test_activating_service_is_stopped_and_restarted(self):
        self.state.write_text("activating\n")
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.event_lines(), ["stop", "copy:inactive", "start", "parent-exit"]
        )

    def test_failed_copy_still_restarts_service_and_preserves_failure(self):
        result = self.run_snapshot(FAIL_COPY="1")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(
            self.event_lines(), ["stop", "copy:inactive", "start", "parent-exit"]
        )
        self.assertFalse(self.snapshot.exists())

    def test_failed_stop_prevents_copy(self):
        result = self.run_snapshot(FAIL_STOP="1")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.event_lines(), ["stop", "start", "parent-exit"])
        self.assertFalse(self.snapshot.exists())

    def test_failed_restart_fails_backup(self):
        result = self.run_snapshot(FAIL_START="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not be restarted", result.stderr)
        self.assertEqual(self.event_lines()[-1], "parent-exit")

    def test_upgrade_flags_do_not_omit_database_or_media(self):
        result = self.run_snapshot(BACKUP_CORE_ONLY="1", do_not_backup_data="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.snapshot / "CURRENT").is_file())
        self.assertTrue((self.snapshot / "media/example").is_file())

    def test_unsafe_data_path_is_rejected_before_stop(self):
        result = self.run_snapshot(data_dir=str(self.root / "source"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe data directory", result.stderr)
        self.assertEqual(self.event_lines(), ["parent-exit"])

    def test_symlink_data_path_is_rejected_before_stop(self):
        alternate = self.root / "alternate"
        alternate.mkdir()
        link = alternate / "continuwuity"
        link.symlink_to(self.data, target_is_directory=True)
        result = self.run_snapshot(data_dir=str(link))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.event_lines(), ["parent-exit"])

    def test_existing_snapshot_is_never_overwritten(self):
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "CURRENT").write_text("previous snapshot\n")
        result = self.run_snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.snapshot / "CURRENT").read_text(), "previous snapshot\n")
        self.assertEqual(self.event_lines(), ["parent-exit"])


if __name__ == "__main__":
    unittest.main()
