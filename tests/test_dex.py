"""Exercise the real Dex shell helper without changing host services/accounts."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -euo pipefail
app=continuwuity
dex_app=dex
domain=${TEST_DOMAIN:-matrix.example.org}
install_dir="$DEX_TEST_ROOT/continuwuity"
auth_type=${TEST_AUTH_TYPE:-dex}

ynh_die() { printf '%s\n' "$1" >&2; exit 1; }
ynh_print_warn() { printf '%s\n' "$1" >&2; }
ynh_app_setting_get() {
    local selected_app= selected_key= argument
    for argument in "$@"; do
        case "$argument" in
            --app=*) selected_app=${argument#--app=} ;;
            --key=*) selected_key=${argument#--key=} ;;
        esac
    done
    local settings_file="$DEX_TEST_ROOT/settings/$selected_app/$selected_key"
    if [[ -f "$settings_file" ]]; then cat "$settings_file"; fi
}
ynh_app_setting_set() {
    local selected_app= selected_key= selected_value= argument
    for argument in "$@"; do
        case "$argument" in
            --app=*) selected_app=${argument#--app=} ;;
            --key=*) selected_key=${argument#--key=} ;;
            --value=*) selected_value=${argument#--value=} ;;
        esac
    done
    printf '%s' "$selected_value" > "$DEX_TEST_ROOT/settings/$selected_app/$selected_key"
}
chown() { :; }
install() {
    local -a install_arguments=()
    local argument
    for argument in "$@"; do
        case "$argument" in --owner=*|--group=*) ;; *) install_arguments+=("$argument");; esac
    done
    command install "${install_arguments[@]}"
}
systemctl() {
    printf '%s\n' "$*" >> "$DEX_TEST_ROOT/systemctl.log"
    if [[ "$1" == restart && -e "$DEX_TEST_ROOT/fail-restart-once" ]]; then
        rm "$DEX_TEST_ROOT/fail-restart-once"
        return 1
    fi
}
curl() {
    local output= argument
    while (($#)); do
        argument=$1
        shift
        if [[ "$argument" == --output ]]; then output=$1; shift; fi
    done
    local issuer="$oidc_discovery_url"
    if [[ -f "$DEX_TEST_ROOT/bad-issuer" ]]; then issuer=https://wrong.example.org; fi
    python3 - "$output" "$issuer" <<'PY'
import json, os, sys
metadata = {
    "issuer": sys.argv[2],
    "authorization_endpoint": "https://login.example.org/dex/auth",
    "token_endpoint": "https://login.example.org/dex/token",
    "jwks_uri": "https://login.example.org/dex/keys",
}
insecure_endpoint = os.environ.get("TEST_INSECURE_ENDPOINT")
if insecure_endpoint in metadata:
    metadata[insecure_endpoint] = metadata[insecure_endpoint].replace("https://", "http://", 1)
with open(sys.argv[1], "w") as target:
    json.dump(metadata, target)
PY
}
source "$DEX_HELPER"
_continuwuity_dex_is_installed() { [[ -d "$DEX_TEST_ROOT/settings/dex" ]]; }
"${TEST_ACTION:-continuwuity_dex_setup}"
'''


class DexLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "continuwuity"
        self.dex = self.root / "dex"
        self.clients = self.dex / "config.yaml.d"
        self.client = self.clients / "continuwuity"
        self.app.mkdir()
        self.clients.mkdir(parents=True)
        (self.dex / "regenerate_config.sh").touch(mode=0o700)
        for app in ("continuwuity", "dex"):
            (self.root / "settings" / app).mkdir(parents=True)
        for key, value in {
            "install_dir": str(self.dex),
            "domain": "login.example.org",
            "path": "/dex",
        }.items():
            (self.root / "settings" / "dex" / key).write_text(value)
        (self.clients / "another-app").write_text("# another application's client\n")

    def invoke(self, success=True, **settings):
        environment = {
            **os.environ,
            "DEX_TEST_ROOT": str(self.root),
            "DEX_HELPER": str(PACKAGE_ROOT / "scripts" / "_dex.sh"),
            **settings,
        }
        result = subprocess.run(
            ["bash", "-c", HARNESS], env=environment, capture_output=True, text=True
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def client_data(self):
        # The emitted YAML client uses a JSON flow mapping, after its marker.
        return json.loads(self.client.read_text().splitlines()[1][2:])

    def test_password_mode_needs_no_dex(self):
        self.invoke(TEST_AUTH_TYPE="password")
        self.assertEqual(tomllib.loads((self.app / "oidc.toml").read_text()), {})
        self.assertFalse(self.client.exists())

    def test_registration_credentials_and_configuration_survive_upgrade(self):
        result = self.invoke()
        secret = (self.app / "oidc-client-secret").read_text().strip()
        self.assertEqual(len(secret), 64)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(self.client_data()["secret"], secret)
        self.assertEqual(
            self.client_data()["redirectURIs"],
            ["https://matrix.example.org/_continuwuity/oidc/complete"],
        )
        oidc = tomllib.loads((self.app / "oidc.toml").read_text())["global"]["oauth"]["oidc"]
        self.assertEqual(oidc["discovery_url"], "https://login.example.org/dex")
        self.assertEqual(oidc["client_secret_file"], str(self.app / "oidc-client-secret"))
        self.assertTrue(oidc["prompt_for_localpart"])
        self.assertNotIn("client_secret", oidc)
        self.assertEqual((self.app / "oidc-client-secret").stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.app / "oidc.toml").stat().st_mode & 0o777, 0o640)
        original = self.client.read_bytes()
        self.invoke()
        self.assertEqual(self.client.read_bytes(), original)
        self.assertEqual((self.app / "oidc-client-secret").read_text().strip(), secret)
        self.assertEqual((self.clients / "another-app").read_text(), "# another application's client\n")
        self.assertEqual((self.root / "systemctl.log").read_text().count("restart"), 1)

    def test_domain_change_updates_only_this_client(self):
        self.invoke()
        secret = self.client_data()["secret"]
        self.invoke(TEST_DOMAIN="new-matrix.example.org")
        self.assertEqual(self.client_data()["secret"], secret)
        self.assertEqual(
            self.client_data()["redirectURIs"],
            ["https://new-matrix.example.org/_continuwuity/oidc/complete"],
        )
        self.assertTrue((self.clients / "another-app").exists())

    def test_unowned_fragment_is_never_overwritten_or_removed(self):
        self.client.write_text("# configured by the administrator\n")
        self.invoke(success=False)
        self.invoke(TEST_ACTION="continuwuity_dex_remove")
        self.assertEqual(self.client.read_text(), "# configured by the administrator\n")

    def test_bad_discovery_rolls_back_changed_client_and_config(self):
        self.invoke()
        client_before = self.client.read_bytes()
        config_before = (self.app / "oidc.toml").read_bytes()
        (self.root / "bad-issuer").touch()
        self.invoke(success=False, TEST_DOMAIN="new-matrix.example.org")
        self.assertEqual(self.client.read_bytes(), client_before)
        self.assertEqual((self.app / "oidc.toml").read_bytes(), config_before)
        self.assertFalse(list(self.dex.glob(".continuwuity-client.*")))

    def test_http_discovery_endpoints_roll_back_changed_client_and_config(self):
        self.invoke()
        client_before = self.client.read_bytes()
        config_before = (self.app / "oidc.toml").read_bytes()
        for endpoint in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            with self.subTest(endpoint=endpoint):
                self.invoke(
                    success=False,
                    TEST_DOMAIN="new-matrix.example.org",
                    TEST_INSECURE_ENDPOINT=endpoint,
                )
                self.assertEqual(self.client.read_bytes(), client_before)
                self.assertEqual((self.app / "oidc.toml").read_bytes(), config_before)
                self.assertFalse(list(self.dex.glob(".continuwuity-client.*")))

    def test_failed_dex_restart_rolls_back_first_registration(self):
        (self.root / "fail-restart-once").touch()
        self.invoke(success=False)
        self.assertFalse(self.client.exists())
        self.assertFalse((self.app / "oidc.toml").exists())
        self.assertTrue((self.clients / "another-app").exists())

    def test_removal_leaves_other_clients_and_is_repeatable(self):
        self.invoke()
        self.invoke(TEST_ACTION="continuwuity_dex_remove")
        self.invoke(TEST_ACTION="continuwuity_dex_remove")
        self.assertFalse(self.client.exists())
        self.assertTrue((self.clients / "another-app").exists())


if __name__ == "__main__":
    unittest.main()
