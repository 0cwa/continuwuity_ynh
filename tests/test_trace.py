"""Prevent disclosure of YunoHost settings and bootstrap secrets in bash traces."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("install", "upgrade", "restore", "backup", "remove", "change_url", "config")


class LifecycleTraceTests(unittest.TestCase):
    def test_lifecycle_scripts_disable_trace_before_loading_helpers(self):
        for filename in ENTRYPOINTS:
            with self.subTest(filename=filename):
                lines = (PACKAGE / "scripts" / filename).read_text().splitlines()
                self.assertEqual(lines[0], "#!/bin/bash")
                self.assertIn("set +x", lines[:8])
                source_lines = [i for i, line in enumerate(lines) if line.startswith("source ")]
                self.assertLess(lines.index("set +x"), source_lines[0])
                # The helper library is allowed to turn xtrace back on.
                self.assertEqual(lines[source_lines[-1] + 2], "set +x")

    def test_install_entrypoint_hides_initial_password_and_generated_token(self):
        # Exercise the production install script, including its actual defaults,
        # with only YunoHost/OS side effects replaced by controlled fixtures.
        secret_password = "audit-password-sentinel-87a9f3"
        secret_token = "audit-token-sentinel-47c12f"
        harness = r'''
set -e
source() {
    if [[ "$1" == "_common.sh" ]]; then
        builtin source "$PACKAGE/scripts/_common.sh"
        continuwuity_validate_settings() { :; }
        continuwuity_write_config() { :; }
        continuwuity_permissions() { :; }
        continuwuity_bootstrap_admin() { :; }
        continuwuity_dex_setup() { :; }
        continuwuity_configure() { :; }
        continuwuity_register_service() { :; }
        continuwuity_wait_ready() { :; }
    elif [[ "$1" == "/usr/share/yunohost/helpers" ]]; then
        # Simulate a helper library which has re-enabled tracing on import.
        set -x
    fi
}
ynh_script_progression() { :; }
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_app_setting_set() { :; }
ynh_app_setting_set_default() { :; }
ynh_string_random() { printf '%s\n' "$TEST_TOKEN"; }
ynh_setup_source() { :; }
ynh_systemctl() { :; }
builtin source "$PACKAGE/scripts/install"
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", "-x", "-c", harness],
                env={
                    **os.environ,
                    "PACKAGE": str(PACKAGE),
                    "TEST_TOKEN": secret_token,
                    "app": "continuwuity",
                    "domain": "matrix.example.test",
                    "admin": "alice",
                    "password": secret_password,
                    "data_dir": directory,
                    "install_dir": directory,
                },
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret_password, result.stdout + result.stderr)
        self.assertNotIn(secret_token, result.stdout + result.stderr)

    def test_secret_defaults_disable_trace_if_a_helper_reenabled_it(self):
        token = "audit-defaults-token-secret-9dcf2"
        harness = r'''
set -e
source() { :; }
ynh_app_setting_set_default() { :; }
ynh_app_setting_set() { :; }
ynh_string_random() { printf '%s\n' "$TEST_TOKEN"; }
builtin source "$PACKAGE/scripts/_common.sh"
set -x
continuwuity_defaults
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            env={**os.environ, "PACKAGE": str(PACKAGE),
                 "TEST_TOKEN": token, "domain": "matrix.example.test"},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main()
