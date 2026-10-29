# Testing

## Automated checks

Dependencies and the full local validation sequence are in
[CONTRIBUTING.md](../CONTRIBUTING.md#local-validation). From the repository root:

```bash
python -m unittest discover -s tests -v
shellcheck --severity=warning --external-sources --source-path=SCRIPTDIR --exclude=SC1091,SC2034,SC2154 scripts/*
python tests/validate_schema.py --download
python tests/check_nginx.py
python tests/smoke_upstream.py --download
```

- **Unit tests** cover metadata, rendered configuration, credential redaction,
  backup and restore, and configuration/domain-change rollback. Host commands
  are fixtures, so these do not perform a real YunoHost install.
- **ShellCheck** lints `scripts/`.
- **Schema check** validates `manifest.toml`, `tests.toml` and `config_panel.toml`
  against the official YunoHost schemas.
- **Nginx check** validates the rendered nginx configuration.
- **Upstream smoke test** downloads the manifest-pinned Continuwuity binary,
  verifies its SHA256, and exercises bootstrap, login, registration, media,
  and restore against real local RocksDB and HTTP listeners.

GitHub Actions runs these in
[.github/workflows/package.yml](../.github/workflows/package.yml). The upstream
smoke test runs on native amd64 and arm64 runners. A green CI run does not
prove a YunoHost installation works.

## Release gate

A release requires one thing: YunoHost's stock, unmodified
[package_check](https://github.com/YunoHost/package_check) on **amd64, YunoHost
stable** with **every test passing** and **no linter critical issues or errors
other than `AppCatalog.*`**. Catalog checks hold the computed level at 0 until
the app is accepted into the catalog, so the level itself is not the gate. The
`package_check` CI workflow runs this on every pull request. To run it by hand,
use a disposable Incus/LXD runner, never a production host:

```bash
git clone https://github.com/YunoHost/package_check.git
cd package_check
./package_check.sh --branch main https://github.com/0cwa/continuwuity_ynh
```

For a non-`main` branch, replace `main` with that branch. Record each run in
this table:

| Result line |
| --- |
| package_check `<commit>`, YunoHost `<version>`, tests passed, `<date>` |
| package_check `f7d32ca`, YunoHost 12.1.41.2, 5/5 (linter, install, multi-instance, backup/restore, upgrade), 2026-10-29 |

Real-host behavior that package_check does not cover, such as Dex, SMTP, TURN,
and arm64 YunoHost, is tracked in [PROD_TEST.md](PROD_TEST.md) and is not part
of the release gate.
