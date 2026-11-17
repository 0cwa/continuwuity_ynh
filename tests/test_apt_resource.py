"""Verify the YunoHost conditional apt resource."""

from pathlib import Path
import subprocess
import tomllib
import unittest


class AptResourceTests(unittest.TestCase):
    def test_only_s3_mode_requires_rclone(self):
        manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "manifest.toml").read_text())
        apt = manifest["resources"]["apt"]
        self.assertNotIn("rclone", apt["packages"])
        for backend, expected in (("local", ""), ("s3", "rclone"), ("", "")):
            with self.subTest(backend=backend):
                result = subprocess.run(
                    ["bash", "-c", apt["packages_from_raw_bash"]],
                    env={"media_backend": backend}, capture_output=True, text=True, check=True,
                )
                self.assertEqual(result.stdout.strip(), expected)


if __name__ == "__main__":
    unittest.main()
