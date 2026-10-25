"""Run the change-url transaction with temporary vhosts and simulated services."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -eu
source() { :; }  # Dependencies below are controlled substitutes for YunoHost.
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_print_warn() { printf '%s\n' "$*" >&2; }
ynh_script_progression() { :; }
continuwuity_validate_settings() { :; }
continuwuity_permissions() { :; }
systemctl() {
    local unit="${*: -1}"
    local state_file="$TEST_ROOT/main-state"
    [[ "$unit" != dex.service ]] || state_file="$TEST_ROOT/dex-state"
    case "$1" in
        show) cat "$state_file" ;;
        stop|start|restart)
            printf '%s:%s\n' "$1" "$unit" >> "$EVENTS_FILE"
            if [[ "$1" == stop ]]; then
                printf 'inactive\n' > "$state_file"
            else
                printf 'active\n' > "$state_file"
            fi
            ;;
        daemon-reload) printf 'daemon-reload\n' >> "$EVENTS_FILE" ;;
        *) return 98 ;;
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
    if [[ "$action" == reload ]]; then
        printf 'reload:%s\n' "$service" >> "$EVENTS_FILE"
    else
        systemctl "$action" "$service.service"
    fi
}
ynh_config_change_url_nginx() {
    printf 'move-nginx\n' >> "$EVENTS_FILE"
    # Model nginx rejecting duplicate exact discovery locations.
    if [[ "$domain" == "$server_name" \
        && -e "$TEST_ETC/nginx/conf.d/$server_name.d/$app-well-known.conf" ]]; then
        return 40
    fi
    rm "$TEST_ETC/nginx/conf.d/$old_domain.d/$app.conf"
    printf 'new primary\n' > "$TEST_ETC/nginx/conf.d/$domain.d/$app.conf"
}
ynh_permission_url() {
    printf 'permission change\n' > "$TEST_ETC/yunohost/apps/$app/settings.yml"
}
ynh_app_setting_set() {
    printf 'new domain setting\n' > "$TEST_ETC/yunohost/apps/$app/settings.yml"
    printf 'persist-domain\n' >> "$EVENTS_FILE"
}
_continuwuity_dex_load() {
    dex_client_file="$TEST_ROOT/dex/config.yaml.d/$app"
}
continuwuity_dex_setup() {
    [[ "$auth_type" == dex ]] || return 0
    printf 'callback https://%s\n' "$domain" > "$dex_client_file"
    printf 'new oidc\n' > "$install_dir/oidc.toml"
    printf 'new secret\n' > "$install_dir/oidc-client-secret"
    [[ "${FAIL_PHASE:-}" != dex ]] || exit 22
}
continuwuity_configure() {
    printf 'url=%s identity=%s\n' "$domain" "$server_name" > "$install_dir/config.toml"
    printf 'new smtp\n' > "$install_dir/smtp-uri"
    printf 'new settings\n' > "$TEST_ETC/yunohost/apps/$app/settings.yml"
    printf 'new unit\n' > "$TEST_ETC/systemd/system/$app.service"
    if [[ "$domain" != "$server_name" ]]; then
        printf 'delegate %s\n' "$domain" > "$TEST_ETC/nginx/conf.d/$server_name.d/$app-well-known.conf"
    fi
    [[ "${FAIL_PHASE:-}" != configure ]] || exit 23
}
continuwuity_wait_ready() {
    printf 'ready:%s\n' "$domain" >> "$EVENTS_FILE"
    [[ "${FAIL_PHASE:-}" != ready || "$domain" == "$old_domain" ]] || exit 24
}
yunohost() {
    if [[ "$1" == tools ]]; then
        printf 'regen-mail\n' >> "$EVENTS_FILE"
    else
        printf 'ssowatconf\n' >> "$EVENTS_FILE"
    fi
}
nginx() { printf 'nginx-test\n' >> "$EVENTS_FILE"; }
trap 'printf "parent-exit\n" >> "$EVENTS_FILE"' EXIT
'''


class ChangeURLTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.etc = self.root / "etc"
        self.install = self.root / "install"
        self.install.mkdir()
        for name in (
            "config.toml", "overrides.toml", "oidc.toml", "registration-token",
            "turn-secret", "smtp-uri",
        ):
            (self.install / name).write_text(f"original {name}\n")
        for name in ("old.example", "new.example", "identity.example"):
            (self.etc / "nginx/conf.d" / f"{name}.d").mkdir(parents=True)
        self.old_nginx = self.etc / "nginx/conf.d/old.example.d/continuwuity.conf"
        self.new_nginx = self.etc / "nginx/conf.d/new.example.d/continuwuity.conf"
        self.delegation = self.etc / "nginx/conf.d/identity.example.d/continuwuity-well-known.conf"
        self.old_nginx.write_text("original primary\n")
        self.delegation.write_text("original delegation\n")
        self.unit = self.etc / "systemd/system/continuwuity.service"
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("original unit\n")
        self.settings = self.etc / "yunohost/apps/continuwuity/settings.yml"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("original settings\n")
        self.dex_client = self.root / "dex/config.yaml.d/continuwuity"
        self.dex_client.parent.mkdir(parents=True)
        self.dex_client.write_text("original callback\n")
        (self.root / "main-state").write_text("active\n")
        (self.root / "dex-state").write_text("active\n")
        self.events = self.root / "events"
        self.events.touch()

    def run_change(self, **overrides):
        env = dict(os.environ)
        env.update(
            TEST_ROOT=str(self.root), TEST_ETC=str(self.etc),
            TMPDIR=str(self.root),
            EVENTS_FILE=str(self.events), app="continuwuity",
            install_dir=str(self.install), domain="new.example",
            old_domain="old.example", server_name="identity.example",
            path="/", old_path="/", auth_type="dex", dex_app="dex",
        )
        env.update(overrides)
        # Remap only system-path literals; execute the production transaction.
        script = (PACKAGE / "scripts/change_url").read_text().replace(
            '"/etc/', '"$TEST_ETC/'
        )
        return subprocess.run(
            ["bash", "-c", HARNESS + "\n" + script],
            env=env, capture_output=True, text=True,
        )

    def assert_original_state(self):
        self.assertEqual(self.old_nginx.read_text(), "original primary\n")
        self.assertFalse(self.new_nginx.exists())
        self.assertEqual(self.delegation.read_text(), "original delegation\n")
        self.assertEqual(self.settings.read_text(), "original settings\n")
        self.assertEqual(self.unit.read_text(), "original unit\n")
        self.assertEqual(self.dex_client.read_text(), "original callback\n")
        self.assertFalse((self.install / "oidc-client-secret").exists())
        for name in ("config.toml", "oidc.toml", "smtp-uri"):
            self.assertEqual((self.install / name).read_text(), f"original {name}\n")
        self.assertEqual((self.root / "main-state").read_text(), "active\n")
        self.assertIn("stop:continuwuity.service", self.events.read_text())
        self.assertEqual(self.events.read_text().splitlines().count("regen-mail"), 2)
        self.assertEqual(self.events.read_text().splitlines()[-1], "parent-exit")

    def test_success_preserves_matrix_identity_and_moves_callback(self):
        result = self.run_change()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.old_nginx.exists())
        self.assertTrue(self.new_nginx.is_file())
        self.assertEqual(
            (self.install / "config.toml").read_text(),
            "url=new.example identity=identity.example\n",
        )
        self.assertEqual(self.dex_client.read_text(), "callback https://new.example\n")

    def test_return_to_identity_removes_delegation_before_nginx_reload(self):
        result = self.run_change(domain="identity.example")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.delegation.exists())
        self.assertTrue(
            (self.etc / "nginx/conf.d/identity.example.d/continuwuity.conf").exists()
        )

    def test_stopped_service_remains_stopped_after_success(self):
        (self.root / "main-state").write_text("inactive\n")
        result = self.run_change()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "main-state").read_text(), "inactive\n")
        self.assertNotIn("start:continuwuity.service", self.events.read_text())

    def test_configuration_failure_restores_exact_files_and_callback(self):
        result = self.run_change(FAIL_PHASE="configure")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_state()

    def test_readiness_failure_restores_previous_service(self):
        result = self.run_change(FAIL_PHASE="ready")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_state()
        self.assertIn("ready:old.example", self.events.read_text())

    def test_partial_dex_failure_restores_previous_client(self):
        result = self.run_change(FAIL_PHASE="dex")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_state()

    def test_failure_returning_to_identity_restores_delegation(self):
        result = self.run_change(domain="identity.example", FAIL_PHASE="configure")
        self.assertNotEqual(result.returncode, 0)
        self.assert_original_state()
        self.assertFalse(
            (self.etc / "nginx/conf.d/identity.example.d/continuwuity.conf").exists()
        )

    def test_unexpected_destination_file_is_not_overwritten(self):
        self.new_nginx.write_text("unrelated configuration\n")
        result = self.run_change()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.new_nginx.read_text(), "unrelated configuration\n")
        self.assertEqual(self.events.read_text().splitlines(), ["parent-exit"])


if __name__ == "__main__":
    unittest.main()
