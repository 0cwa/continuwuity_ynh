#!/usr/bin/env python3
"""Protect package-owned identity, storage and listener invariants."""

import os
import ipaddress
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

import yaml


S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
S3_REGION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
S3_PREFIX_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_override(path):
    if not path.exists():
        return
    with path.open('rb') as stream:
        config = tomllib.load(stream)
    settings = config.get('global', {})
    protected = {'server_name', 'database_path', 'address', 'port', 'unix_socket_path',
                 'registration_token', 'registration_token_file', 'admin_execute',
                 'admin_execute_errors_ignore', 'listening', 'media_backend', 'media_s3'}
    if protected.intersection(settings):
        raise ValueError('overrides.toml changes package-owned identity, storage, access or startup settings.')
    if 'oidc' in settings.get('oauth', {}):
        raise ValueError('Use the managed authentication integration instead of overriding oauth.oidc.')


def validate_bucket(value, label):
    if not S3_BUCKET.fullmatch(value) or '..' in value:
        raise ValueError(f'{label} must be a lowercase S3 bucket name (3-63 letters, numbers, dots or hyphens).')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return
    raise ValueError(f'{label} cannot be an IP address.')


def validate_prefix(value, label):
    if not value or value.startswith('/') or value.endswith('/') or '\\' in value:
        raise ValueError(f'{label} must be a nonempty relative object-key prefix.')
    components = value.split('/')
    if any(component in ('', '.', '..') or not S3_PREFIX_COMPONENT.fullmatch(component)
           for component in components):
        raise ValueError(f'{label} may contain only safe path components separated by single slashes.')


def prefixes_overlap(first, second):
    # An empty prefix covers the whole bucket, including every nonempty prefix.
    if not first or not second:
        return True
    return first == second or first.startswith(second + '/') or second.startswith(first + '/')


def normalized_s3_endpoint(value, label='The S3 endpoint'):
    if not value:
        # An omitted endpoint selects the native AWS S3 service.
        return ('aws-s3',)
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise ValueError(f'{label} must be a valid HTTPS URL.') from None
    if (parsed.scheme.lower() != 'https' or not hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in value)
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError(f'{label} must be an HTTPS URL without embedded credentials, query or fragment.')

    hostname = hostname.lower()
    aws_host = (
        hostname in ('s3.amazonaws.com', 's3.amazonaws.com.cn')
        or (hostname.startswith('s3.') and (hostname.endswith('.amazonaws.com') or hostname.endswith('.amazonaws.com.cn')))
        or (hostname.startswith('s3-') and (hostname.endswith('.amazonaws.com') or hostname.endswith('.amazonaws.com.cn')))
    )
    if aws_host and not parsed.path.strip('/'):
        return ('aws-s3',)

    if ':' in hostname:
        hostname = f'[{hostname}]'
    if port not in (None, 443):
        hostname = f'{hostname}:{port}'
    path = parsed.path.rstrip('/')
    return ('https', hostname, path)


def validate_s3_settings(backend):
    endpoint = os.environ.get('media_s3_endpoint', '')
    bucket = os.environ.get('media_s3_bucket', '')
    backup_bucket = os.environ.get('media_s3_backup_bucket', '')
    region = os.environ.get('media_s3_region', '')
    prefix = os.environ.get('media_s3_key_prefix', '')
    access_key = os.environ.get('media_s3_access_key_id', '')
    secret_key = os.environ.get('media_s3_secret_access_key', '')

    if backend not in ('local', 's3'):
        raise ValueError('Media storage must be local or s3.')
    if backend == 'local':
        if os.environ.get('media_s3_restore_bucket') or os.environ.get('media_s3_restore_key_prefix'):
            raise ValueError('S3 restore settings can only be used with S3 media storage.')
        return

    validate_bucket(bucket, 'The live media bucket')
    validate_bucket(backup_bucket, 'The media snapshot bucket')
    if bucket == backup_bucket:
        raise ValueError('The media snapshot bucket must be different from the live media bucket.')
    validate_prefix(prefix, 'The live media key prefix')
    if not region or not S3_REGION.fullmatch(region):
        raise ValueError('The S3 region contains unsupported characters.')
    normalized_s3_endpoint(endpoint)
    if not access_key or not secret_key:
        raise ValueError('S3 media storage requires both a static access key ID and secret access key; the package uses this same identity for remote snapshots.')
    if any(ord(character) < 32 or ord(character) == 127 for character in access_key + secret_key):
        raise ValueError('S3 credentials cannot contain control characters.')

    restore_bucket = os.environ.get('media_s3_restore_bucket', '')
    restore_prefix = os.environ.get('media_s3_restore_key_prefix', '')
    if bool(restore_bucket) != bool(restore_prefix):
        raise ValueError('S3 restore bucket and key prefix must be supplied together.')
    if restore_bucket:
        validate_bucket(restore_bucket, 'The restore media bucket')
        validate_prefix(restore_prefix, 'The restore media key prefix')
        if restore_bucket == backup_bucket:
            raise ValueError('The restore target cannot be the media snapshot bucket.')
        if restore_bucket == bucket and prefixes_overlap(restore_prefix, prefix):
            raise ValueError('The restore target must use a separate bucket or a disjoint key prefix.')


def validate_unique_s3_prefix(app, backend, endpoint, bucket, prefix, apps_directory):
    if backend != 's3':
        return
    endpoint_identity = normalized_s3_endpoint(endpoint)
    candidates = [('live media', bucket, prefix)]
    restore_bucket = os.environ.get('media_s3_restore_bucket', '')
    restore_prefix = os.environ.get('media_s3_restore_key_prefix', '')
    if restore_bucket and restore_prefix:
        candidates.append(('restore target', restore_bucket, restore_prefix))

    for setting_file in apps_directory.glob('*/settings.yml'):
        other = setting_file.parent.name
        if other == app or not re.fullmatch(r'continuwuity(__[0-9]+)?', other):
            continue
        with setting_file.open(encoding='utf-8') as stream:
            other_settings = yaml.safe_load(stream) or {}
        if other_settings.get('media_backend', 'local') != 's3':
            continue
        other_endpoint = other_settings.get('media_s3_endpoint', '') or ''
        if not isinstance(other_endpoint, str):
            raise ValueError(f'Cannot verify S3 media separation from {other}; its endpoint setting is invalid.')
        try:
            other_endpoint_identity = normalized_s3_endpoint(other_endpoint, f'{other} S3 endpoint')
        except ValueError:
            raise ValueError(f'Cannot verify S3 media separation from {other}; its endpoint setting is invalid.') from None
        if endpoint_identity != other_endpoint_identity:
            continue
        other_bucket = other_settings.get('media_s3_bucket', '')
        if not isinstance(other_bucket, str) or not other_bucket:
            raise ValueError(f'Cannot verify S3 media separation from {other}; its bucket setting is missing or invalid.')
        if bucket != other_bucket and all(candidate_bucket != other_bucket for _, candidate_bucket, _ in candidates):
            continue
        other_prefix = other_settings.get('media_s3_key_prefix', '')
        if not isinstance(other_prefix, str):
            raise ValueError(f'Cannot verify S3 media separation from {other}; its key prefix setting is invalid.')
        for purpose, candidate_bucket, candidate_prefix in candidates:
            if candidate_bucket == other_bucket and prefixes_overlap(candidate_prefix, other_prefix):
                raise ValueError(
                    f'The {purpose} key prefix for {app} overlaps S3 media storage for {other} at the same endpoint and bucket.'
                )


def validate_existing_backend(current, requested):
    if not current.exists():
        return
    with current.open('rb') as stream:
        config = tomllib.load(stream)
    previous = config.get('global', {}).get('media_backend', 'local')
    if previous != requested:
        raise ValueError('Media storage cannot be switched after installation without an explicit media migration.')
    if previous == 's3':
        settings = config.get('global', {}).get('media_s3') or {}
        configured = {
            'bucket': os.environ.get('media_s3_bucket', ''),
            'region': os.environ.get('media_s3_region', ''),
            'endpoint': os.environ.get('media_s3_endpoint', ''),
            'key_prefix': os.environ.get('media_s3_key_prefix', ''),
        }
        saved = {key: settings.get(key, '') for key in configured}
        if saved != configured:
            raise ValueError('S3 bucket, endpoint, region and key prefix are fixed at installation; use a media migration to change them.')


def main():
    app, server_name = os.environ['app'], os.environ['server_name']
    install = Path(os.environ['install_dir'])
    current = install / 'config.toml'
    if current.exists():
        with current.open('rb') as stream:
            old_name = tomllib.load(stream).get('global', {}).get('server_name')
        if old_name is not None and old_name != server_name:
            raise ValueError('The Matrix server_name is permanent. Only the public API domain can change.')
    backend = os.environ.get('media_backend', 'local')
    validate_s3_settings(backend)
    validate_existing_backend(current, backend)
    validate_unique_s3_prefix(
        app,
        backend,
        os.environ.get('media_s3_endpoint', ''),
        os.environ.get('media_s3_bucket', ''),
        os.environ.get('media_s3_key_prefix', ''),
        Path('/etc/yunohost/apps'),
    )
    validate_override(install / 'overrides.toml')
    matrix_apps = {'continuwuity', 'conduwuit', 'conduit', 'dendrite', 'synapse'}
    for setting_file in Path('/etc/yunohost/apps').glob('*/settings.yml'):
        other = setting_file.parent.name
        if other == app or other.split('__')[0] not in matrix_apps:
            continue
        with setting_file.open(encoding='utf-8') as stream:
            settings = yaml.safe_load(stream) or {}
        if settings.get('server_name', settings.get('domain')) == server_name:
            raise ValueError(f'The Matrix identity domain is already used by {other}. Choose a different identity domain.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, yaml.YAMLError) as error:
        raise SystemExit(str(error)) from None
