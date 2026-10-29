# Testing and release acceptance

## Automated checks

See [Contributing](../CONTRIBUTING.md#local-validation) for dependencies and the
commands used locally and in CI.

The unit tests cover metadata and rendered configuration, installation-trace
credential redaction (including helpers that re-enable tracing), backup
capacity/mount preflight (including failed disk probes and data-root mounts),
bootstrap credential handling, consistent stopped snapshots and failure recovery,
restore archive RocksDB-marker/symlink validation and rollback when late
steps fail, Dex client ownership/rollback, and configuration
or domain-change transactions. Service
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

GitHub Actions runs the *real* binary/API/RocksDB/media/restore smoke test on
**both native amd64 and native arm64** Ubuntu runners. Both release binaries
are thus downloaded, SHA256 checked and executed. Nginx syntax, shell checks,
YunoHost metadata schema and lifecycle fixtures are separate CI gates.
No CI badge should be read as proof of a completed YunoHost installation,
including a real arm64 YunoHost upgrade/restore.

## Required YunoHost acceptance run

Run YunoHost's maintained
[package_check](https://github.com/YunoHost/package_check) against a disposable
host meeting the minimum in `manifest.toml`, using the scenarios in `tests.toml`. A real Dex scenario needs
an existing Dex app with a trusted certificate and working HTTPS discovery;
the default local-password test intentionally does not fabricate that setup.
The [official app-testing guide](https://doc.yunohost.org/en/dev/packaging/test/)
documents the current runner setup and invocation. On a dedicated Incus/LXD
package-check runner (never a production host), use:

```bash
git clone https://github.com/YunoHost/package_check.git
cd package_check
./package_check.sh --branch main https://github.com/0cwa/continuwuity_ynh
```

For an unmerged audit branch, replace `main` with that branch. Capture the
runner image, YunoHost version, architecture, `Test_results.log`, and exact
commit SHA. Repeat with the oldest declared compatible YunoHost release
(currently 12.1.18) and a current stable release; a green generic CI run is
**not** a substitute. The CI workflow does not currently provision Incus or a
YunoHost VM and this real-host gate is still **unverified**.

Disposable-host runs on YunoHost 12.1.41.2 (package_check adaptation, backup/restore at 1 GiB and 10 GiB, and a forced same-version upgrade failure with automatic safety-backup restore) were performed during development. They used offline adaptations, did not exercise a binary-version change, and are not an overall package_check or release-gate pass.

| Scenario | Required observations |
| --- | --- |
| Fresh local-password install | Domain root, first admin already claimed, no unauthenticated registration, login succeeds |
| Existing retained data | Reinstall stops with a useful error instead of reusing or overwriting an identity |
| Multiple instances | Distinct system users/ports/data and distinct Matrix identity domains |
| Configuration | Token rotation, registration toggle, upload limit and ordinary settings persist; invalid new config restores old working state |
| SMTP | Invalid DNS/TLS/auth rejected before applying; enable successfully and receive an actual recovery email |
| Dex install | Owned client added; first admin links correctly; new user's OAuth login succeeds |
| Dex authorization | Confirm intended LDAP admission policy, username selection, and behavior of existing sessions after provider account changes |
| Backup and restore | Room history, media, access tokens, signing identity and OIDC mappings preserved; deliberately fail after moving data and verify original survives |
| Upgrade and rollback | Full safety backup exists; simulate upgrade failure; old binary and old database recover together |
| API domain change | Permanent IDs remain; discovery, SMTP mapping and Dex callback update; move back to identity domain works |
| Failed domain change | Previous domain, callback, files, permission URLs and service state recovered |
| Removal | Only this instance's service/nginx/Dex client removed; data retained unless purge requested |
| Remote federation | Another homeserver resolves discovery and can join/exchange messages |
| Calls | Real clients obtain TURN credentials; relay/SFU networking works outside the local LAN |
| arm64 | Native upstream smoke passes in CI; fresh real YunoHost install, upgrade and restore on arm64 still required |

Actual public federation, trusted certificates, YunoHost resource provisioning,
browser-driven Dex account linking and external email delivery require the
corresponding real services. They have not been simulated into a production
readiness claim. Before changing live-media storage, follow the separate
[external storage acceptance and encrypted offsite recovery plan](STORAGE.md).

## Updating the packaged version

See [release maintenance](../CONTRIBUTING.md#updating-the-packaged-release) for
version, asset and checksum updates.
