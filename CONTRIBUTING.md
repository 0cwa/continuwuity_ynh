# Contributing

This repository contains the YunoHost package, not the upstream homeserver.
Start with [README.md](README.md) for installation and
[doc/TESTING.md](doc/TESTING.md) for checks and the release gate. Track proposed
work and review history in issues and pull requests.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `manifest.toml`, `config_panel.toml`, `tests.toml` | Package resources, user-facing settings and YunoHost acceptance scenarios |
| `scripts/install`, `upgrade`, `backup`, `restore`, `remove`, `change_url`, `config` | YunoHost lifecycle entrypoints and their transactions |
| `scripts/_common.sh` | Shared helper loading, web delegation and configuration orchestration |
| `scripts/_settings.sh` | Defaults, validation, SMTP preflight and configuration/credential rendering |
| `scripts/_service.sh` | Permissions, service registration/readiness and offline administrator bootstrap |
| `scripts/_dex.sh`, `scripts/_backup.sh` | Dex client ownership and consistent backup lifecycle |
| `conf/` | Deployed templates and Python bootstrap/settings validation helpers |
| `tests/test_*.py` | Configuration and lifecycle regression tests with controlled host commands |
| `tests/package_helpers.py`, `tests/upstream_helpers.py` | Shared template rendering and local upstream smoke infrastructure |
| `tests/smoke_upstream.py` | Real upstream bootstrap, API, registration and database/media restore scenarios |
| `doc/`, `tests/fixtures/`, `THIRD_PARTY.md` | Operator documentation, test fixtures and their provenance |

Shell helper modules define functions without performing host changes when
sourced. `_common.sh` is loaded before YunoHost's helpers; settings and host
commands are available when lifecycle functions run. Keep the tracing guards
around secret-bearing operations, and preserve the lifecycle failure/rollback
checks when changing these boundaries.

## Local validation

Use Python 3.11 or newer, Bash, ShellCheck and Nginx. From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -v
shellcheck --severity=warning --external-sources --source-path=SCRIPTDIR --exclude=SC1091,SC2034,SC2154 scripts/*
python tests/validate_schema.py --download
python tests/check_nginx.py
python tests/smoke_upstream.py --download
```

Schema validation downloads the official YunoHost schemas. The smoke check
downloads and checksum-verifies the manifest's binary for the host architecture;
it uses temporary data and local listeners, not a running YunoHost installation.
For an already downloaded binary, use `--binary /absolute/path/to/continuwuity`
instead of `--download`; checksum verification still applies. Retained smoke
logs can contain test credentials, so treat `--keep-directory` output as private.

The same checks run in [.github/workflows/package.yml](.github/workflows/package.yml).
Prefer regressions that verify behavior, failure recovery or security boundaries.
Add failure-injection and edge-case hardening only in response to a reported problem.
Keep fixture support separate from discoverable test modules so standalone
validation scripts do not import a test suite.

## Updating the packaged release

Review the upstream release notes and tagged configuration. Download both
generic static architecture assets, compute SHA256 from their actual bytes,
and update the manifest URLs, hashes and version together. Do not substitute
CPU-specific maxperf builds or assume future releases keep the same asset names.

Review configuration and database migration implications, rerun the automated
checks, and pass the release gate in [doc/TESTING.md](doc/TESTING.md) before
publishing. Keep full database-and-media safety backups while upstream uses
RocksDB migrations.
