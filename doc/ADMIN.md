# Administrator guide

## Layout and service management

For a first installation, application settings are in
`/etc/yunohost/apps/continuwuity/settings.yml`, the binary and configuration in
`/var/www/continuwuity`, and all database and media files in
`/home/yunohost.app/continuwuity`. Additional instances use their complete app ID,
such as `continuwuity__2`, in each path.

The executable and configuration belong to root and are readable by the app's
group. The daemon can write to its own data directory. Systemd adds filesystem
and process restrictions; Nginx connects to a loopback-only listener on a
YunoHost-allocated port. No separate public port 8008 or 8448 is required.

Use the YunoHost Services screen or:

```bash
sudo yunohost service status continuwuity
sudo journalctl -u continuwuity -n 100
sudo systemctl restart continuwuity
```

Logs are in the system journal, so the package does not add a redundant file
logger or logrotate rule. The service retries after a temporary network or
identity-provider failure. Persistent failures require inspecting the journal.

## Users and registration

The administrator chosen at installation is created offline before Nginx is
configured and the service starts. Its Matrix username matches the selected
YunoHost username, but its local password is independent. The account is listed
in `admins_list` and is also made the initial admin by upstream.

Open the Continuwuity admin room in a Matrix client. Its bot accepts upstream
commands such as:

```text
!admin users create-user alice
!admin users list-users
```

Use upstream's [admin command reference](https://continuwuity.org/reference/admin/users)
for account management and recovery. Keep password-bearing command output
private. Account removal and session revocation are explicit administration
actions; this package does not automatically destroy Matrix accounts when a
YunoHost user changes or disappears.

Local registration is closed initially. Enable **Allow local account registration
with a token** in the configuration panel to invite users. The generated token
is stored in protected application settings and the `registration-token` file.
Distribute it only to invited users. The toggle never enables unrestricted open
registration. Upstream-created additional invitation tokens remain valid until
revoked through upstream's token tools.

In Dex mode, successful provider authentication controls new account creation,
independently of this local-registration setting. See [DEX.md](DEX.md).

## Configuration and advanced settings

The config panel manages registration, the registration token, optional SMTP,
upload limits, log level, TURN credentials, and MatrixRTC discovery. Upload
limits are applied to both Nginx and the homeserver. Config changes validate
cross-field requirements and restore the previous files/settings if the new
configuration fails. An intentionally stopped service remains stopped.

The service reads configuration in this order:

1. `config.toml`: generated from the package and saved YunoHost settings.
2. `overrides.toml`: administrator-owned upstream options, preserved on upgrade.
3. `oidc.toml`: the managed optional Dex integration.

Changes to `overrides.toml` require a service restart. Consult the
[configuration for the packaged release](https://forgejo.ellis.link/continuwuation/continuwuity/src/tag/v26.9.1/conduwuit-example.toml),
since the live upstream documentation can describe a newer version.

Identity, database path, listener, registration-token and startup-execution
overrides are rejected by lifecycle scripts. Those settings determine backup
coverage and how the package exposes the service. Do not bypass that protection
by editing the managed files or the service's command line. Use the managed
authentication mode for OIDC; custom providers need a deliberately maintained
package adaptation rather than an untracked override.

The package disables public access to `/metrics`; it does not claim a working
Prometheus integration. URL preview remains at upstream's default policy. Set
explicit allowlists in `overrides.toml` if you need previews.

## Email

The system-user resource provisions a dedicated SMTP identity and secret. The
configuration uses authenticated STARTTLS to the installation domain on port
587, with normal certificate verification. The protected `smtp-uri` file holds
the connection URI; credentials are percent-encoded and are not process
arguments.

Email starts **disabled** because upstream tests the SMTP connection during
startup and a failure can stop the homeserver. Before enabling it, check the
domain's YunoHost mail settings, DNS and TLS certificate. Configuring it runs
EHLO, STARTTLS, login and NOOP without sending mail. A failed check leaves the
previous configuration intact. A successful preflight does not verify delivery
to external recipients; test a password-reset email separately.

The SMTP account is restricted by YunoHost to local use. The domain must resolve
back to the local YunoHost mail service in a way compatible with that restriction.
If the check fails, inspect the local resolver, mail service and certificate.
The package does not disable TLS verification or weaken mail authentication.

Enabling SMTP supports upstream's email verification/recovery features. It does
not enable public email-only registration. When moving the API domain, the
package refreshes YunoHost's SMTP sender mapping for the new domain.

## Federation, delegation and domain changes

The web/API domain and permanent identity domain may differ. For example:

| Setting | Example |
| --- | --- |
| API domain | `matrix.example.org` |
| Permanent server name | `example.org` |
| User ID | `@alice:example.org` |
| Client discovery | `https://example.org/.well-known/matrix/client` points to `https://matrix.example.org` |
| Federation discovery | `https://example.org/.well-known/matrix/server` advertises `matrix.example.org:443` |

The package adds only its own discovery snippet on the identity domain. Existing
conflicting Matrix discovery rules are not overwritten. Both domains must exist
in YunoHost and retain working DNS and HTTPS certificates. The Matrix API stays
public at the reverse proxy: Matrix validates user tokens and federation
signatures itself. SSOwat login redirects would break clients and federation.

YunoHost's **Change URL** can move the API to another dedicated domain. It
preserves `server_name`, database identity and all Matrix IDs; updates discovery,
SMTP mapping and the optional Dex callback; and rolls back its own files and
Dex fragment if reconfiguration fails. Keep the old identity domain indefinitely.
Changing to a subpath is unsupported.

Before moving a live server, take a full backup and ensure the new domain is
ready. Discovery is cached by clients and remote servers, so a move can cause
temporary connection failures even after the local checks pass.

## Voice and video

The package configures existing calling infrastructure. It does not install or
take ownership of a shared relay or LiveKit deployment.

For a TURN server using a static authentication secret, enter its URIs and
matching secret in the config panel. Examples:

```text
turn:turn.example.org:3478?transport=udp
turn:turn.example.org:3478?transport=tcp
turns:turn.example.org:5349?transport=tcp
```

The secret is kept in `turn-secret`; clients receive time-limited credentials.
The relay's DNS, certificates, public IP, relay-port range and firewall remain
the responsibility of that service. See
[upstream TURN documentation](https://continuwuity.org/calls/turn).

For MatrixRTC, enter the HTTPS URL of the LiveKit **authorization service**, not
the LiveKit WebSocket endpoint. The package advertises it in the upstream RTC
transports endpoint. The actual authorization service and SFU must already be
working. See [upstream MatrixRTC documentation](https://continuwuity.org/calls/livekit).

## Backups and restore

Create backups through YunoHost's UI or CLI:

```bash
sudo yunohost backup create --apps continuwuity
```

The package stops the service, copies all database/media files into the backup
workspace, and restarts it if it had been running. YunoHost then archives the
frozen copy while the live homeserver can continue. If copying fails, it still
tries to restore the original service state and the backup fails explicitly.
Filesystem reflinks are used when available; otherwise the entire directory is
copied. The interruption and extra disk requirement grow with the data size.

All data is included even for automatic upgrade backups and when
`BACKUP_CORE_ONLY` or `do_not_backup_data` would normally omit data. Restoring
old code against a newly migrated RocksDB database is unsafe. The restore script
rejects archives without a complete data snapshot, and replaces retained data
rather than merging incompatible database files.

Back up **Dex as well** for delegated authentication, and restore it before this
app. The Continuwuity backup carries its client secret and subject mappings, but
does not copy or overwrite Dex's shared LDAP connector and other clients.

Restoration requires the same identity domain and matching architecture for the
archived binary. Cross-architecture restoration is not implemented: restore on
the original architecture, or plan a tested binary/data migration separately.
The package also rejects symlinked data directories; relocating data through a
symlink requires an explicitly tested backup adaptation.

## Upgrade, remove and migrate

Upgrade from this repository or an authenticated local checkout using YunoHost.
No floating `latest` binary is downloaded; every binary must match the manifest
checksum. Keep `overrides.toml` rather than editing the managed main file. Manual
changes to generated files may be saved by YunoHost's checksum helper, but are
not the active configuration after regeneration.

Normal removal deletes the service, its web configuration, its owned Dex client,
and managed installation resources. It keeps database and media unless you
explicitly request YunoHost's purge option. A new installation refuses to reuse
retained data automatically. Restore that identity from its backup instead.

This package does not automatically import another Matrix homeserver. Upstream
documents compatibility with Conduwuit data, but that is a migration requiring
identity, database, version and ownership checks. Synapse, Dendrite and Conduit
databases are not interchangeable with this package's RocksDB data. See
[upstream migration information](https://forgejo.ellis.link/continuwuation/continuwuity#can-i-migrate-my-data-from-x).

## Acceptance before production

Read [TESTING.md](TESTING.md) and run a full package check on a disposable
YunoHost VM. In particular, verify both authentication modes, a new browser
login through actual Dex, SMTP delivery, both directions of a URL change, and a
restore containing real room history and media. Local smoke tests establish
useful behavior but do not prove YunoHost deployment or remote federation.
