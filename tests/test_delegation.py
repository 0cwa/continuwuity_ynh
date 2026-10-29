"""Exercise Matrix delegation on a shared domain with temporary nginx paths."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]

HARNESS = r'''
set -eu
source() { :; }
ynh_die() { printf '%s\n' "$*" >&2; exit 1; }
ynh_config_add() { printf 'delegate\n' > "${2#--destination=}"; }
ynh_permission_url() { printf 'permission\n' >> "$EVENTS_FILE"; }
nginx() { printf 'nginx-test\n' >> "$EVENTS_FILE"; [[ "${NGINX_FAIL:-0}" != 1 ]]; }
'''


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vhost = self.root / "etc/nginx/conf.d/identity.example.d"
        self.vhost.mkdir(parents=True)
        self.delegation = self.vhost / "continuwuity-well-known.conf"
        self.events = self.root / "events"
        self.events.touch()

    def run_delegation(self, **overrides):
        env = dict(os.environ)
        env.update(
            TEST_ROOT=str(self.root), EVENTS_FILE=str(self.events),
            app="continuwuity", domain="chat.example", server_name="identity.example",
        )
        env.update(overrides)
        text = (PACKAGE / "scripts/_common.sh").read_text()
        body = text[text.index("continuwuity_delegation() {"):text.index("continuwuity_configure() {")]
        body = body.replace('"/etc/', '"$TEST_ROOT/etc/').replace('"/var/www', '"$TEST_ROOT/var/www')
        return subprocess.run(
            ["bash", "-c", HARNESS + body + "\ncontinuwuity_delegation"],
            env=env, capture_output=True, text=True,
        )

    def test_delegation_is_written_and_tested(self):
        result = self.run_delegation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.delegation.is_file())
        self.assertEqual(self.events.read_text().splitlines(), ["nginx-test", "permission"])

    def test_regex_location_in_other_file_is_a_conflict(self):
        (self.vhost / "other.conf").write_text("location ~ ^/\\.well-known/matrix/ { }\n")
        result = self.run_delegation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already configured", result.stderr)
        self.assertFalse(self.delegation.exists())

    def test_static_webroot_path_is_a_conflict(self):
        (self.root / "var/www/.well-known/matrix").mkdir(parents=True)
        result = self.run_delegation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("static Matrix discovery", result.stderr)
        self.assertFalse(self.delegation.exists())

    def test_per_domain_static_path_is_a_conflict(self):
        (self.root / "var/www/.well-known/identity.example/matrix").mkdir(parents=True)
        result = self.run_delegation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("static Matrix discovery", result.stderr)
        self.assertFalse(self.delegation.exists())

    def test_domain_named_matrix_is_not_a_conflict(self):
        # YunoHost aliases mail autoconfig under /var/www/.well-known/<domain>/.
        (self.vhost / "other.conf").write_text(
            "location ^~ '/.well-known/autoconfig/mail/' {\n"
            "    alias /var/www/.well-known/matrix.example/autoconfig/mail/;\n}\n"
        )
        result = self.run_delegation()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quoted_exact_location_is_a_conflict(self):
        (self.vhost / "other.conf").write_text("location = '/.well-known/matrix' { }\n")
        result = self.run_delegation()
        self.assertNotEqual(result.returncode, 0)

    def test_nginx_rejection_removes_new_file_without_permission(self):
        result = self.run_delegation(NGINX_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.delegation.exists())
        self.assertEqual(self.events.read_text().splitlines(), ["nginx-test"])

    def test_nginx_rejection_restores_previous_delegation(self):
        self.delegation.write_text("previous\n")
        result = self.run_delegation(NGINX_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.delegation.read_text(), "previous\n")


if __name__ == "__main__":
    unittest.main()
