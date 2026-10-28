# Testing and release acceptance

## Automated checks

See [Contributing](../CONTRIBUTING.md#local-validation) for dependencies and the
commands used locally and in CI.

The unit tests cover metadata and rendered configuration, bootstrap credential
handling, consistent stopped snapshots and failure recovery, Dex client
ownership/rollback, and configuration or domain-change transactions. Service
and YunoHost commands in lifecycle tests are controlled fixtures; these tests
check decisions and file preservation, not a full operating-system install.

Metadata compatibility checks decode `manifest.toml`, `tests.toml`, and
`config_panel.toml` with both YunoHost's `toml==0.10.2` decoder and Python's
`tomllib`, requiring identical structures. This catches cases such as translated
multiline help being moved into an invalid top-level language field by the
YunoHost decoder, even when the file passes a schema check using `tomllib`.

The smoke test executes the actual manifest-pinned upstream binary. It verifies
its checksum, starts real RocksDB and local HTTP listeners, bootstraps an admin
offline, logs in with the exact initial password, checks discovery and closed
registration, creates room history and media, restores a stopped data copy, and
checks invitation-token registration. Its isolated local SMTP fixture checks
upstream's SMTP startup path without sending real mail.

The [CI workflow](../.github/workflows/package.yml) also runs ShellCheck, official
YunoHost metadata schema validation and Nginx configuration validation. Consult
the workflow for runner selection; no CI badge is proof of a completed YunoHost
installation or validation on every supported architecture.

## Required YunoHost acceptance run

Run YunoHost's maintained
[package_check](https://github.com/YunoHost/package_check) against a disposable
YunoHost host meeting the minimum in `manifest.toml`, using the scenarios in
`tests.toml`. A real Dex scenario needs an existing Dex app with a trusted
certificate and working HTTPS discovery; the default local-password test
intentionally does not fabricate that setup. The
[official app-testing guide](https://doc.yunohost.org/en/dev/packaging/test/)
documents the runner setup and invocation.

| Scenario | Required observations |
| --- | --- |
| Fresh local-password install | Domain root, first admin already claimed, no unauthenticated registration, login succeeds |
| Existing retained data | Reinstall stops with a useful error instead of reusing or overwriting an identity |
| Multiple instances | Distinct system users/ports/data and distinct Matrix identity domains |
| Configuration | Token rotation, registration toggle, upload limit and ordinary settings persist; invalid new config restores old working state |
| SMTP | Invalid DNS/TLS/auth rejected before applying; enable successfully and receive an actual recovery email |
| Dex install | Owned client added; first admin links correctly; new user's OAuth login succeeds |
| Dex authorization | Confirm intended LDAP admission policy, username selection, and behavior of existing sessions after provider account changes |
| Backup and restore | Room history, media, access tokens, signing identity and OIDC mappings preserved |
| Upgrade and rollback | Full safety backup exists; simulate upgrade failure; old binary and old database recover together |
| API domain change | Permanent IDs remain; discovery, SMTP mapping and Dex callback update; move back to identity domain works |
| Failed domain change | Previous domain, callback, files, permission URLs and service state recovered |
| Removal | Only this instance's service/nginx/Dex client removed; data retained unless purge requested |
| Remote federation | Another homeserver resolves discovery and can join/exchange messages |
| Calls | Real clients obtain TURN credentials; relay/SFU networking works outside the local LAN |
| arm64 | Fresh install, upgrade and restore with the arm64 release binary |

Actual public federation, trusted certificates, YunoHost resource provisioning,
browser-driven Dex account linking and external email delivery require the
corresponding real services. They have not been simulated into a production
readiness claim.

See [release maintenance](../CONTRIBUTING.md#updating-the-packaged-release) for
version, asset and checksum updates.
