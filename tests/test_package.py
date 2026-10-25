"""Validate the package's public configuration and install-time safety invariants."""

import json
from pathlib import Path
import subprocess
import tomllib
import unittest

from jinja2 import Environment, StrictUndefined


PACKAGE = Path(__file__).resolve().parents[1]


def render_config(**overrides):
    """Use the same Jinja filter semantics as ynh_config_add --jinja."""
    values = {
        "app": "continuwuity",
        "domain": "matrix.example.test",
        "server_name": "example.test",
        "install_dir": "/var/www/continuwuity",
        "data_dir": "/home/yunohost.app/continuwuity",
        "port": 18008,
        "admin": "alice",
        "allow_registration": "false",
        "allow_federation": "true",
        "max_upload_size": 50,
        "log_level": "info",
        "smtp_enabled": "false",
        "turn_uris_json": "[]",
        "turn_ttl": 86400,
        "rtc_service_url": "",
    }
    values.update(overrides)
    environment = Environment(undefined=StrictUndefined)
    environment.filters["to_json"] = json.dumps
    return environment.from_string((PACKAGE / "conf/config.toml").read_text()).render(values)


class PackageTests(unittest.TestCase):
    def test_manifest_architectures_have_pinned_matching_assets(self):
        manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
        self.assertEqual(manifest["packaging_format"], 2)
        self.assertEqual(manifest["integration"]["helpers_version"], "2.1")
        sources = manifest["resources"]["sources"]["main"]
        upstream_version = manifest["version"].split("~", 1)[0]
        for architecture in manifest["integration"]["architectures"]:
            with self.subTest(architecture=architecture):
                asset = sources[architecture]
                self.assertIn(f"/v{upstream_version}/", asset["url"])
                self.assertTrue(asset["url"].endswith(f"-static-{architecture}"))
                self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
        self.assertIs(sources["extract"], False)

    def test_backend_stays_local_and_public_api_is_not_ssowat_protected(self):
        config = tomllib.loads(render_config())["global"]
        self.assertEqual(config["address"], ["127.0.0.1"])
        self.assertFalse(config["allow_registration"])
        self.assertIn("registration_token_file", config)
        manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
        permissions = manifest["resources"]["permissions"]["main"]
        self.assertEqual(permissions["allowed"], "visitors")
        self.assertIs(permissions["auth_header"], False)
        self.assertNotIn("exposed", manifest["resources"]["ports"]["main"])

    def test_delegated_matrix_identity_and_public_api_are_distinct(self):
        config = tomllib.loads(render_config())["global"]
        self.assertEqual(config["server_name"], "example.test")
        self.assertEqual(config["well_known"]["client"], "https://matrix.example.test")
        self.assertEqual(config["well_known"]["server"], "matrix.example.test:443")
        self.assertEqual(config["admins_list"], ["@alice:example.test"])

    def test_optional_integrations_render_valid_toml_and_reference_secret_files(self):
        config = tomllib.loads(
            render_config(
                allow_registration="true",
                smtp_enabled="true",
                turn_uris_json=json.dumps(["turn:turn.example.test:3478?transport=udp"]),
                rtc_service_url="https://call.example.test",
            )
        )["global"]
        self.assertEqual(config["turn_uris"], ["turn:turn.example.test:3478?transport=udp"])
        self.assertTrue(config["turn_secret_file"].endswith("/turn-secret"))
        self.assertIn("connection_uri_file", config["smtp"])
        self.assertFalse(config["smtp"]["require_email_for_registration"])
        self.assertEqual(config["matrix_rtc"]["foci"][0]["type"], "livekit")
        self.assertEqual(config["max_request_size"], 50 * 1024 * 1024)

    def test_template_escapes_string_values(self):
        value = 'test "quote" \\ slash\nsecond line'
        config = tomllib.loads(render_config(log_level=value))["global"]
        self.assertEqual(config["log"], value)
        self.assertNotIn("second", config)

    def test_shell_syntax(self):
        for script in sorted((PACKAGE / "scripts").iterdir()):
            if script.is_file() and script.read_bytes().startswith(b"#!/bin/bash"):
                with self.subTest(script=script.name):
                    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_metadata_toml_is_parseable(self):
        for name in ("manifest.toml", "tests.toml", "config_panel.toml"):
            with self.subTest(name=name):
                parsed = tomllib.loads((PACKAGE / name).read_text())
                self.assertTrue(parsed)


if __name__ == "__main__":
    unittest.main()
