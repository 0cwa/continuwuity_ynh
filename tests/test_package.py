"""Validate the package's public configuration and install-time safety invariants."""

import json
from collections import OrderedDict
import subprocess
import tomllib
import unittest

import toml
from package_helpers import PACKAGE, render_config


class PackageTests(unittest.TestCase):
    def test_min_yunohost_supports_the_template_json_filter(self):
        manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
        requirement = manifest["integration"]["yunohost"]
        self.assertTrue(requirement.startswith(">= "))
        self.assertGreaterEqual(
            tuple(map(int, requirement[3:].split("."))), (12, 1, 18),
            "Jinja to_json was not available in the 12.1.17 helper",
        )

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
        rendered = render_config()
        config = tomllib.loads(rendered)["global"]
        self.assertEqual(config["address"], ["127.0.0.1"])
        self.assertFalse(config["allow_registration"])
        self.assertIn("registration_token_file", config)
        self.assertNotIn("media_backend", config, "Existing release binaries default to local media.")
        manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
        permissions = manifest["resources"]["permissions"]["main"]
        self.assertEqual(permissions["allowed"], "visitors")
        self.assertIs(permissions["auth_header"], False)
        self.assertNotIn("exposed", manifest["resources"]["ports"]["main"])

    def test_s3_password_questions_remain_safe_in_local_and_s3_install_modes(self):
        # YunoHost 12.1 turns hidden inputs into None but reinjects every
        # password form value into the install environment. Keep these
        # optional masked inputs visible so blank values normalize to "".
        manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
        install = manifest["install"]
        self.assertEqual(install["media_backend"]["default"], "local")
        for backend in ("local", "s3"):
            with self.subTest(backend=backend):
                for name in ("media_s3_access_key_id", "media_s3_secret_access_key"):
                    option = install[name]
                    self.assertEqual(option["type"], "password")
                    self.assertTrue(option["optional"])
                    self.assertNotIn("visible", option)

    def test_s3_configuration_renders_managed_location_and_protected_static_credentials(self):
        config = tomllib.loads(render_config(
            media_backend="s3",
            media_s3_config_bucket="live-media",
            media_s3_config_key_prefix="continuwuity/media",
            media_s3_endpoint="https://objects.example.test",
            media_s3_region="us-east-1",
            media_s3_access_key_id='access "id',
            media_s3_secret_access_key="secret\\key",
        ))["global"]
        self.assertEqual(config["media_backend"], "s3")
        self.assertEqual(config["media_s3"]["bucket"], "live-media")
        self.assertEqual(config["media_s3"]["key_prefix"], "continuwuity/media")
        self.assertEqual(config["media_s3"]["endpoint"], "https://objects.example.test")
        self.assertIs(config["media_s3"]["allow_http"], False)
        self.assertEqual(config["media_s3"]["access_key_id"], 'access "id')
        self.assertEqual(config["media_s3"]["secret_access_key"], "secret\\key")

    def test_restore_override_renders_only_the_temporary_bucket_and_prefix(self):
        config = tomllib.loads(render_config(
            media_backend="s3",
            media_s3_config_bucket="restore-media",
            media_s3_config_key_prefix="continuwuity/media-restore-42",
            media_s3_region="us-east-1",
        ))["global"]
        self.assertEqual(config["media_s3"]["bucket"], "restore-media")
        self.assertEqual(config["media_s3"]["key_prefix"], "continuwuity/media-restore-42")
        self.assertEqual(config["media_s3"]["region"], "us-east-1")

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

    def test_metadata_matches_yunohost_toml_decoder(self):
        # YunoHost 12's read_toml uses python-toml with OrderedDict, rather
        # than tomllib. Dotted multiline translation keys can become stray
        # top-level "en" options in that decoder and break manifest GET/form
        # validation even when the modern TOML parser and JSON schemas pass.
        for name in ("manifest.toml", "config_panel.toml", "tests.toml"):
            with self.subTest(name=name):
                source = (PACKAGE / name).read_text()
                self.assertEqual(
                    toml.loads(source, _dict=OrderedDict),
                    tomllib.loads(source),
                    f"{name} must decode identically in YunoHost and tomllib",
                )


if __name__ == "__main__":
    unittest.main()
