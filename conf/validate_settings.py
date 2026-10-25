#!/usr/bin/env python3
"""Protect package-owned identity, storage and listener invariants."""

import os
from pathlib import Path
import tomllib

import yaml


def validate_override(path):
    if not path.exists():
        return
    with path.open('rb') as stream:
        config = tomllib.load(stream)
    settings = config.get('global', {})
    protected = {'server_name', 'database_path', 'address', 'port', 'unix_socket_path',
                 'registration_token', 'registration_token_file', 'admin_execute',
                 'admin_execute_errors_ignore', 'listening'}
    if protected.intersection(settings):
        raise ValueError('overrides.toml changes package-owned identity, storage, access or startup settings.')
    if 'oidc' in settings.get('oauth', {}):
        raise ValueError('Use the managed authentication integration instead of overriding oauth.oidc.')


def main():
    app, server_name = os.environ['app'], os.environ['server_name']
    install = Path(os.environ['install_dir'])
    current = install / 'config.toml'
    if current.exists():
        with current.open('rb') as stream:
            old_name = tomllib.load(stream).get('global', {}).get('server_name')
        if old_name is not None and old_name != server_name:
            raise ValueError('The Matrix server_name is permanent. Only the public API domain can change.')
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
