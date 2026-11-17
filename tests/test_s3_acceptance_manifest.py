"""Regression checks for patching candidate sources in the acceptance copy."""

import hashlib
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

import s3_yunohost_acceptance as acceptance


PACKAGE = Path(__file__).resolve().parents[1]


class AcceptanceManifestTests(unittest.TestCase):
    def test_prepare_package_updates_indented_architecture_source_fields(self):
        original = (PACKAGE / "manifest.toml").read_text(encoding="utf-8")
        candidate_bytes = b"candidate binary fixture"
        digest = hashlib.sha256(candidate_bytes).hexdigest()

        rewritten = acceptance.rewrite_candidate_manifest(
            original,
            "amd64",
            "conduwuit-linux-static-amd64",
            digest,
        )
        manifest = tomllib.loads(rewritten)
        amd64 = manifest["resources"]["sources"]["s3"]["amd64"]
        self.assertEqual(
            amd64["url"],
            "https://127.0.0.1:18444/conduwuit-linux-static-amd64",
        )
        self.assertEqual(amd64["sha256"], digest)
        self.assertEqual(manifest["resources"]["sources"]["main"], tomllib.loads(original)["resources"]["sources"]["main"])
        self.assertIn(
            '    amd64.url = "https://127.0.0.1:18444/conduwuit-linux-static-amd64"',
            rewritten,
        )
        self.assertIn(f'    amd64.sha256 = "{digest}"', rewritten)

    def test_app_removal_uses_supported_yunohost_cli_arguments(self):
        with patch.object(acceptance, "command") as command:
            acceptance.remove_package("successful restore")

        self.assertEqual(
            command.call_args.args,
            (
                "Remove the app for successful restore",
                ["yunohost", "app", "remove", acceptance.APP],
            ),
        )
        self.assertEqual(command.call_args.kwargs, {"timeout_seconds": 10 * 60})


if __name__ == "__main__":
    unittest.main()
