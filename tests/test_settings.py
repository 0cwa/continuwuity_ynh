"""Protect the immutable Matrix identity and package-owned storage/listener."""

import importlib.util
import os
from pathlib import Path
import subprocess
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

    def test_s3_backend_and_configuration_table_cannot_be_overridden(self):
        unsafe_overrides = (
            '[global]\nmedia_backend = "local"\n',
            '[global.media_s3]\nbucket = "other-bucket"\n',
        )
        for content in unsafe_overrides:
            with self.subTest(content=content):
                self.override.write_text(content)
                with self.assertRaisesRegex(ValueError, 'package-owned'):
                    SETTINGS.validate_override(self.override)

    def s3_environment(self, **overrides):
        return {
            'media_backend': 's3',
            'media_s3_endpoint': 'https://objects.example.test',
            'media_s3_bucket': 'live-media',
            'media_s3_backup_bucket': 'media-snapshots',
            'media_s3_region': 'us-east-1',
            'media_s3_key_prefix': 'continuwuity/media',
            'media_s3_access_key_id': 'access-key',
            'media_s3_secret_access_key': 'secret-key',
            **overrides,
        }

    def test_valid_s3_settings_use_distinct_bucket_and_instance_prefix(self):
        with patch.dict(os.environ, self.s3_environment(), clear=True):
            SETTINGS.validate_s3_settings('s3')
        with patch.dict(
            os.environ, self.s3_environment(media_s3_endpoint=''), clear=True
        ):
            SETTINGS.validate_s3_settings('s3')

    def test_s3_requires_nonempty_safe_prefix(self):
        for prefix in ('', '/media', 'media/', 'media//private', '../private', 'media/../other', 'a\\b'):
            with self.subTest(prefix=prefix), patch.dict(
                os.environ, self.s3_environment(media_s3_key_prefix=prefix), clear=True
            ):
                with self.assertRaisesRegex(ValueError, 'key prefix'):
                    SETTINGS.validate_s3_settings('s3')

    def test_s3_requires_distinct_backup_bucket_and_https_endpoint(self):
        invalid = (
            {'media_s3_backup_bucket': 'live-media'},
            {'media_s3_endpoint': 'http://objects.example.test'},
            {'media_s3_endpoint': 'https://user:password@objects.example.test'},
            {'media_s3_endpoint': 'https://objects.example.test/?token=secret'},
        )
        for change in invalid:
            with self.subTest(change=change), patch.dict(
                os.environ, self.s3_environment(**change), clear=True
            ):
                with self.assertRaises(ValueError):
                    SETTINGS.validate_s3_settings('s3')

    def test_s3_requires_a_static_credential_pair(self):
        for change, message in (
            ({'media_s3_access_key_id': '', 'media_s3_secret_access_key': ''}, 'requires both a static'),
            ({'media_s3_secret_access_key': ''}, 'requires both a static'),
        ):
            with self.subTest(change=change), patch.dict(
                os.environ, self.s3_environment(**change), clear=True
            ):
                with self.assertRaisesRegex(ValueError, message):
                    SETTINGS.validate_s3_settings('s3')

    def test_restore_target_must_be_paired_and_disjoint_from_live_media(self):
        with patch.dict(
            os.environ,
            self.s3_environment(
                media_s3_restore_bucket='live-media',
                media_s3_restore_key_prefix='continuwuity/media/recovery',
            ),
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, 'separate bucket or a disjoint'):
                SETTINGS.validate_s3_settings('s3')
        with patch.dict(
            os.environ,
            self.s3_environment(media_s3_restore_bucket='media-snapshots'),
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, 'supplied together'):
                SETTINGS.validate_s3_settings('s3')
        with patch.dict(
            os.environ,
            self.s3_environment(
                media_s3_restore_bucket='media-snapshots',
                media_s3_restore_key_prefix='continuwuity/media-recovery',
            ),
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, 'cannot be the media snapshot bucket'):
                SETTINGS.validate_s3_settings('s3')

    def test_existing_backend_cannot_be_switched_without_migration(self):
        self.override.write_text('[global]\nmedia_backend = "local"\n')
        with self.assertRaisesRegex(ValueError, 'cannot be switched'):
            SETTINGS.validate_existing_backend(self.override, 's3')

    def test_s3_destination_is_fixed_but_restore_override_does_not_change_saved_settings(self):
        self.override.write_text(
            '[global]\nmedia_backend = "s3"\n'
            '[global.media_s3]\n'
            'bucket = "live-media"\n'
            'region = "us-east-1"\n'
            'endpoint = "https://objects.example.test"\n'
            'key_prefix = "continuwuity/media"\n'
        )
        values = self.s3_environment(
            media_s3_restore_bucket='media-snapshots',
            media_s3_restore_key_prefix='continuwuity/media-recovery',
        )
        with patch.dict(os.environ, values, clear=True):
            SETTINGS.validate_existing_backend(self.override, 's3')
        changed = {**values, 'media_s3_key_prefix': 'other/media'}
        with patch.dict(os.environ, changed, clear=True):
            with self.assertRaisesRegex(ValueError, 'fixed at installation'):
                SETTINGS.validate_existing_backend(self.override, 's3')

    def write_other_instance(self, app_id, *, endpoint='https://objects.example.test',
                             bucket='live-media', prefix='tenant/media', backend='s3'):
        app_directory = self.directory / 'apps' / app_id
        app_directory.mkdir(parents=True, exist_ok=True)
        (app_directory / 'settings.yml').write_text(
            f'media_backend: {backend}\n'
            f'media_s3_endpoint: {endpoint!r}\n'
            f'media_s3_bucket: {bucket}\n'
            f'media_s3_key_prefix: {prefix!r}\n'
        )
        return self.directory / 'apps'

    def test_instances_with_same_endpoint_and_bucket_need_disjoint_prefixes(self):
        for other_prefix in (
            'tenant/media',
            'tenant',
            'tenant/media/private',
            '',
        ):
            with self.subTest(other_prefix=other_prefix):
                apps = self.write_other_instance(
                    'continuwuity__2',
                    endpoint='https://OBJECTS.example.test:443/',
                    prefix=other_prefix,
                )
                with patch.dict(os.environ, self.s3_environment(), clear=True):
                    with self.assertRaisesRegex(ValueError, 'overlaps S3 media storage'):
                        SETTINGS.validate_unique_s3_prefix(
                            'continuwuity', 's3', 'https://objects.example.test',
                            'live-media', 'tenant/media', apps,
                        )

    def test_disjoint_prefixes_and_different_storage_destinations_are_allowed(self):
        apps = self.write_other_instance('continuwuity__2', prefix='tenant/other')
        with patch.dict(os.environ, self.s3_environment(), clear=True):
            SETTINGS.validate_unique_s3_prefix(
                'continuwuity', 's3', 'https://objects.example.test',
                'live-media', 'tenant/media', apps,
            )
        # A second instance can use the same key prefix in a different bucket.
        settings_file = apps / 'continuwuity__2' / 'settings.yml'
        settings_file.write_text(
            'media_backend: s3\n'
            'media_s3_endpoint: https://objects.example.test\n'
            'media_s3_bucket: other-media\n'
            'media_s3_key_prefix: tenant/media\n'
        )
        with patch.dict(os.environ, self.s3_environment(), clear=True):
            SETTINGS.validate_unique_s3_prefix(
                'continuwuity', 's3', 'https://objects.example.test',
                'live-media', 'tenant/media', apps,
            )
        # A different endpoint is an independent object-store namespace too.
        settings_file.write_text(
            'media_backend: s3\n'
            'media_s3_endpoint: https://other.example.test\n'
            'media_s3_bucket: live-media\n'
            'media_s3_key_prefix: tenant/media\n'
        )
        with patch.dict(os.environ, self.s3_environment(), clear=True):
            SETTINGS.validate_unique_s3_prefix(
                'continuwuity', 's3', 'https://objects.example.test',
                'live-media', 'tenant/media', apps,
            )

    def test_omitted_aws_endpoint_matches_explicit_regional_aws_endpoint(self):
        apps = self.write_other_instance(
            'continuwuity__2', endpoint='https://s3.us-east-1.amazonaws.com',
            prefix='tenant/media',
        )
        with patch.dict(os.environ, self.s3_environment(media_s3_endpoint=''), clear=True):
            with self.assertRaisesRegex(ValueError, 'overlaps S3 media storage'):
                SETTINGS.validate_unique_s3_prefix(
                    'continuwuity', 's3', '', 'live-media', 'tenant/media', apps,
                )

    def test_restore_target_prefix_is_checked_against_other_instances(self):
        apps = self.write_other_instance('continuwuity__2', prefix='restore')
        environment = self.s3_environment(
            media_s3_restore_bucket='live-media',
            media_s3_restore_key_prefix='restore/generation-1',
        )
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, 'restore target key prefix'):
                SETTINGS.validate_unique_s3_prefix(
                    'continuwuity', 's3', 'https://objects.example.test',
                    'live-media', 'tenant/media', apps,
                )

    def test_shell_config_writer_passes_restore_location_only_as_render_values(self):
        install = self.directory / 'install'
        install.mkdir()
        (install / 'overrides.toml').write_text('# managed test fixture\n')
        output = self.directory / 'rendered-values'
        script = r'''
set -euo pipefail
source "$PACKAGE_ROOT/scripts/_settings.sh"
app=continuwuity__2
domain=matrix.example.test
mail_pwd=unused
install_dir="$TEST_INSTALL"
registration_token=registration-secret
turn_secret=turn-secret
smtp_enabled=false
media_backend=s3
media_s3_bucket=live-media
media_s3_key_prefix=continuwuity__2/media
media_s3_restore_bucket=media-snapshots
media_s3_restore_key_prefix=continuwuity__2/recovery/generation-1
continuwuity_validate_settings() { :; }
continuwuity_check_smtp() { :; }
ynh_config_add() {
    python3 -c 'import os; print(os.environ["media_s3_config_bucket"]); print(os.environ["media_s3_config_key_prefix"])' > "$TEST_OUTPUT"
}
continuwuity_write_config
'''
        result = subprocess.run(
            ['bash', '-c', script],
            env={
                **os.environ,
                'PACKAGE_ROOT': str(PACKAGE),
                'TEST_INSTALL': str(install),
                'TEST_OUTPUT': str(output),
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text().splitlines(), [
            'media-snapshots', 'continuwuity__2/recovery/generation-1',
        ])

    def test_existing_matrix_identity_cannot_be_renamed(self):
        (self.directory / "config.toml").write_text('[global]\nserver_name = "original.example.org"\n')
        with patch.dict(os.environ, app="continuwuity", server_name="renamed.example.org", install_dir=str(self.directory)):
            with self.assertRaisesRegex(ValueError, "permanent"):
                SETTINGS.main()


if __name__ == "__main__":
    unittest.main()
