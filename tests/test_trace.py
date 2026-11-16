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
        continuwuity_dex_setup() { :; }
        continuwuity_configure() { :; }
        continuwuity_register_service() { :; }
        continuwuity_wait_ready() { :; }
    elif [[ "$1" == "/usr/share/yunohost/helpers" ]]; then
        # Simulate a helper library which has re-enabled tracing on import.
        set -x
    else
        builtin source "$@"
    fi
}
# Match YunoHost's progression helper, which re-enables xtrace on return.
ynh_script_progression() { set -x; }
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_app_setting_set() { :; }
ynh_app_setting_set_default() { :; }
ynh_app_setting_get() { :; }
ynh_string_random() { printf '%s\n' "$TEST_TOKEN"; }
ynh_setup_source() { set -x; }
ynh_exec_as_app() { :; }
ynh_systemctl() { :; }
chown() { :; }
builtin source "$PACKAGE/scripts/install"
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", "-x", "-c", harness],
                env={
                    **os.environ,
                    "PACKAGE": str(PACKAGE),
                    "TEST_TOKEN": secret_token,
                    "YNH_APP_BASEDIR": str(PACKAGE),
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
        trace = result.stdout + result.stderr
        sanitized_trace = trace.replace(secret_password, "[REDACTED]").replace(
            secret_token, "[REDACTED]"
        )
        self.assertEqual(result.returncode, 0, sanitized_trace)
        self.assertFalse(secret_password in trace, "Initial password appeared in the install trace.")
        self.assertFalse(secret_token in trace, "Generated registration token appeared in the install trace.")

    def test_secret_defaults_disable_trace_if_a_helper_reenabled_it(self):
        token = "audit-defaults-token-secret-9dcf2"
        harness = r'''
set -e
ynh_app_setting_set_default() { :; }
ynh_app_setting_set() { :; }
ynh_app_setting_get() { :; }
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

    def test_s3_password_settings_are_initialized_without_clobbering_existing_values(self):
        harness = r'''
set -euo pipefail
declare -A settings=()

ynh_app_setting_get() {
    local key="${1#--key=}"
    if [[ ${settings[$key]+present} ]]; then
        printf '%s\n' "${settings[$key]}"
    fi
    return 0
}

ynh_app_setting_set() {
    local key='' value='' arg
    for arg in "$@"; do
        case "$arg" in
            --key=*) key="${arg#--key=}" ;;
            --value=*) value="${arg#--value=}" ;;
        esac
    done
    settings["$key"]="$value"
}

ynh_app_setting_set_default() {
    local key='' value='' arg
    for arg in "$@"; do
        case "$arg" in
            --key=*) key="${arg#--key=}" ;;
            --value=*) value="${arg#--value=}" ;;
        esac
    done
    if [[ -z "${!key:-}" ]]; then
        ynh_app_setting_set "--key=$key" "--value=$value"
        printf -v "$key" '%s' "$value"
    fi
}

ynh_string_random() { printf 'test-registration-token\n'; }
if [[ ${INITIAL_ACCESS_PRESENT:-0} == 1 ]]; then
    settings[media_s3_access_key_id]="$INITIAL_ACCESS_VALUE"
fi
if [[ ${INITIAL_SECRET_PRESENT:-0} == 1 ]]; then
    settings[media_s3_secret_access_key]="$INITIAL_SECRET_VALUE"
fi

builtin source "$PACKAGE/scripts/_settings.sh"
continuwuity_defaults

[[ ${settings[media_s3_access_key_id]+present} ]]
[[ ${settings[media_s3_secret_access_key]+present} ]]
[[ "${settings[media_s3_access_key_id]}" == "$EXPECTED_ACCESS_VALUE" ]]
[[ "${settings[media_s3_secret_access_key]}" == "$EXPECTED_SECRET_VALUE" ]]
'''

        def run_case(*, action="upgrade", access=None, secret=None, initial=None, expected):
            initial = initial or {}
            env = {
                **os.environ,
                "PACKAGE": str(PACKAGE),
                "app": "continuwuity",
                "domain": "matrix.example.test",
                "YNH_APP_ACTION": action,
                "EXPECTED_ACCESS_VALUE": expected["media_s3_access_key_id"],
                "EXPECTED_SECRET_VALUE": expected["media_s3_secret_access_key"],
            }
            for name, value in (
                ("media_s3_access_key_id", access),
                ("media_s3_secret_access_key", secret),
            ):
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
            for name, value in initial.items():
                suffix = "ACCESS" if name == "media_s3_access_key_id" else "SECRET"
                env[f"INITIAL_{suffix}_PRESENT"] = "1"
                env[f"INITIAL_{suffix}_VALUE"] = value
            result = subprocess.run(
                ["bash", "-c", harness], capture_output=True, text=True, env=env
            )
            self.assertEqual(result.returncode, 0, "Credential settings were not initialized safely.")

        # Fresh installs need both configured static values, or empty values
        # when local media/provider credentials are in use.
        run_case(
            action="install",
            access="test-access-key",
            secret="test-secret-key",
            expected={
                "media_s3_access_key_id": "test-access-key",
                "media_s3_secret_access_key": "test-secret-key",
            },
        )
        run_case(
            action="install",
            access="",
            secret="",
            expected={"media_s3_access_key_id": "", "media_s3_secret_access_key": ""},
        )

        # Upgrades initialize truly missing keys, but preserve stored values
        # when environment variables are absent (including an empty setting).
        run_case(expected={"media_s3_access_key_id": "", "media_s3_secret_access_key": ""})
        run_case(
            initial={
                "media_s3_access_key_id": "existing-access-key",
                "media_s3_secret_access_key": "existing-secret-key",
            },
            expected={
                "media_s3_access_key_id": "existing-access-key",
                "media_s3_secret_access_key": "existing-secret-key",
            },
        )
        run_case(
            initial={"media_s3_access_key_id": ""},
            expected={"media_s3_access_key_id": "", "media_s3_secret_access_key": ""},
        )

    def test_routine_permissions_never_traverse_media(self):
        common = (PACKAGE / "scripts/_service.sh").read_text()
        self.assertNotIn('chown -R "$app:$app" "$data_dir"', common)
        self.assertIn('chown "$app:$app" "$data_dir"', common)


if __name__ == "__main__":
    unittest.main()
