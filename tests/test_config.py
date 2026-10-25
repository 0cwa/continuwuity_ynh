"""Check config-panel failure recovery with mocked host services and helpers."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FILES = ("config.toml", "registration-token", "smtp-uri", "turn-secret")

HARNESS = r'''
set -euo pipefail
app=continuwuity
domain=matrix.example.org
install_dir="$CONFIG_TEST_ROOT/install"
registration_token="$TEST_SECRET"
failure=${TEST_FAILURE:-none}

# The production script sources YunoHost and common helpers. Supply only those
# interfaces here, then source the unchanged config script with the builtin.
source() { :; }
ynh_die() { printf '%s\n' "$1" >&2; exit 1; }
ynh_print_warn() { printf '%s\n' "$1" >&2; }
ynh_app_config_run() { ynh_app_config_apply; }
continuwuity_validate_settings() {
    if [[ "$failure" == validate ]]; then ynh_die 'Invalid settings.'; fi
}
mktemp() { command mktemp -d "$CONFIG_TEST_ROOT/config-transaction.XXXXXXXX"; }
cp() {
    local argument
    local -a arguments=()
    for argument in "$@"; do
        case "$argument" in
            /etc/yunohost/apps/continuwuity/settings.yml) argument="$CONFIG_TEST_ROOT/settings.yml" ;;
            /etc/nginx/conf.d/matrix.example.org.d/continuwuity.conf) argument="$CONFIG_TEST_ROOT/nginx.conf" ;;
        esac
        arguments+=("$argument")
    done
    if [[ "$failure" == snapshot && "${arguments[1]}" == "$install_dir/smtp-uri" ]]; then return 1; fi
    if [[ "$failure" == recovery-copy && "${arguments[1]}" == *config-transaction.*/config.toml ]]; then return 1; fi
    command cp "${arguments[@]}"
}
_ynh_app_config_apply() {
    printf 'new settings\n' > "$CONFIG_TEST_ROOT/settings.yml"
    if [[ "$failure" == settings ]]; then return 1; fi
}
continuwuity_write_config() {
    local file
    for file in config.toml registration-token smtp-uri turn-secret; do
        printf 'new %s\n' "$file" > "$install_dir/$file"
    done
    if [[ "$failure" == render ]]; then return 1; fi
}
continuwuity_permissions() { :; }
ynh_config_add_nginx() {
    printf 'new nginx\n' > "$CONFIG_TEST_ROOT/nginx.conf"
    if [[ "$failure" == nginx ]]; then return 1; fi
}
ynh_systemctl() { systemctl restart "$app.service"; }
systemctl() {
    printf '%s\n' "$*" >> "$CONFIG_TEST_ROOT/service.log"
    case "$1" in
        show) cat "$CONFIG_TEST_ROOT/state" ;;
        stop) printf inactive > "$CONFIG_TEST_ROOT/state" ;;
        restart)
            if [[ "$failure" == restart && ! -f "$CONFIG_TEST_ROOT/restart-failed" ]]; then
                touch "$CONFIG_TEST_ROOT/restart-failed"
                return 1
            fi
            printf active > "$CONFIG_TEST_ROOT/state"
            ;;
    esac
}
continuwuity_wait_ready() {
    if [[ "$failure" == ready || "$failure" == recovery-copy ]]; then
        if [[ ! -f "$CONFIG_TEST_ROOT/readiness-failed" ]]; then
            touch "$CONFIG_TEST_ROOT/readiness-failed"
            ynh_die 'Service not ready.'
        fi
    fi
}
set -x
builtin source "$CONFIG_SCRIPT" apply
'''


class ConfigTransactionTests(unittest.TestCase):
    def fixture(self, state="active"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "install").mkdir()
        for name in FILES:
            (root / "install" / name).write_text("old " + name + "\n")
        (root / "settings.yml").write_text("old settings\n")
        (root / "nginx.conf").write_text("old nginx\n")
        (root / "state").write_text(state)
        return root

    def invoke(self, root, failure="none", secret="this-token-must-never-appear-in-tracing"):
        return subprocess.run(
            ["bash", "-c", HARNESS],
            env={
                **os.environ,
                "CONFIG_TEST_ROOT": str(root),
                "CONFIG_SCRIPT": str(PACKAGE_ROOT / "scripts" / "config"),
                "TEST_SECRET": secret,
                "TEST_FAILURE": failure,
            },
            text=True,
            capture_output=True,
        )

    def assert_restored(self, root):
        self.assertEqual((root / "settings.yml").read_text(), "old settings\n")
        self.assertEqual((root / "nginx.conf").read_text(), "old nginx\n")
        for name in FILES:
            self.assertEqual((root / "install" / name).read_text(), "old " + name + "\n")

    def test_success_updates_configuration_and_preserves_active_service(self):
        root = self.fixture()
        result = self.invoke(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "settings.yml").read_text(), "new settings\n")
        self.assertEqual((root / "state").read_text(), "active")
        self.assertIn("restart continuwuity.service", (root / "service.log").read_text())
        self.assertFalse(list(root.glob("config-transaction.*")))
        self.assertNotIn("this-token-must-never-appear-in-tracing", result.stderr + result.stdout)

    def test_success_leaves_stopped_service_stopped(self):
        root = self.fixture("inactive")
        result = self.invoke(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "state").read_text(), "inactive")
        self.assertNotIn("restart", (root / "service.log").read_text())

    def test_failures_restore_original_settings_files_and_active_service(self):
        for failure in ("settings", "render", "nginx", "restart", "ready"):
            with self.subTest(failure=failure):
                root = self.fixture()
                result = self.invoke(root, failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assert_restored(root)
                self.assertEqual((root / "state").read_text(), "active")
                self.assertFalse(list(root.glob("config-transaction.*")))
                self.assertIn("service state were restored", result.stderr)

    def test_failed_update_keeps_previously_stopped_service_stopped(self):
        root = self.fixture("inactive")
        result = self.invoke(root, failure="nginx")
        self.assertNotEqual(result.returncode, 0)
        self.assert_restored(root)
        self.assertEqual((root / "state").read_text(), "inactive")
        self.assertNotIn("restart", (root / "service.log").read_text())

    def test_activating_service_is_restarted_with_new_configuration(self):
        root = self.fixture("activating")
        result = self.invoke(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "state").read_text(), "active")
        self.assertIn("restart", (root / "service.log").read_text())

    def test_invalid_token_is_not_logged_and_has_no_side_effects(self):
        root = self.fixture()
        secret = "super-secret-invalid\nregistration-token"
        result = self.invoke(root, secret=secret)
        self.assertNotEqual(result.returncode, 0)
        self.assert_restored(root)
        self.assertNotIn("super-secret-invalid", result.stdout + result.stderr)
        self.assertFalse((root / "service.log").exists())
        self.assertFalse(list(root.glob("config-transaction.*")))

    def test_snapshot_failure_cleans_up_without_touching_service(self):
        root = self.fixture()
        result = self.invoke(root, failure="snapshot")
        self.assertNotEqual(result.returncode, 0)
        self.assert_restored(root)
        self.assertFalse((root / "service.log").exists())
        self.assertFalse(list(root.glob("config-transaction.*")))

    def test_failed_recovery_keeps_private_backups_and_reports_failure(self):
        root = self.fixture()
        result = self.invoke(root, failure="recovery-copy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recovery could not complete", result.stderr)
        self.assertNotIn("service state were restored", result.stderr)
        backups = list(root.glob("config-transaction.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((backups[0] / "config.toml").read_text(), "old config.toml\n")
        self.assertEqual((root / "state").read_text(), "inactive")


if __name__ == "__main__":
    unittest.main()
