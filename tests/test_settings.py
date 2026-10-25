"""Protect the immutable Matrix identity and package-owned storage/listener."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_settings", PACKAGE / "conf/validate_settings.py")
SETTINGS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETTINGS)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.override = self.directory / "overrides.toml"

    def test_safe_advanced_configuration_is_preserved(self):
        content = '[global]\nurl_preview_domain_explicit_allowlist = ["example.org"]\n'
        self.override.write_text(content)
        SETTINGS.validate_override(self.override)
        self.assertEqual(self.override.read_text(), content)

    def test_overrides_cannot_redirect_storage_or_expose_listener(self):
        unsafe_settings = (
            'server_name = "other.example.org"',
            'database_path = "/unbacked-up/database"',
            'address = ["0.0.0.0"]',
            'port = 8448',
            'admin_execute = ["server shutdown"]',
            'registration_token_file = "/unmanaged/secret"',
        )
        for setting in unsafe_settings:
            with self.subTest(setting=setting):
                self.override.write_text("[global]\n" + setting + "\n")
                with self.assertRaises(ValueError):
                    SETTINGS.validate_override(self.override)

    def test_managed_oidc_cannot_be_replaced_from_overrides(self):
        self.override.write_text('[global.oauth.oidc]\ndiscovery_url = "https://unrelated.example.org"\n')
        with self.assertRaises(ValueError):
            SETTINGS.validate_override(self.override)

    def test_existing_matrix_identity_cannot_be_renamed(self):
        (self.directory / "config.toml").write_text('[global]\nserver_name = "original.example.org"\n')
        with patch.dict(os.environ, app="continuwuity", server_name="renamed.example.org", install_dir=str(self.directory)):
            with self.assertRaisesRegex(ValueError, "permanent"):
                SETTINGS.main()


if __name__ == "__main__":
    unittest.main()
