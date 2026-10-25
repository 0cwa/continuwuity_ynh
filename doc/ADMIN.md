# Administrator guide

## Web pages and administration

The **installation domain** is the public homeserver address. It serves the
Matrix client and federation APIs, OAuth endpoints, and Continuwuity's own
welcome and personal account pages. It does not install a chat client or a
graphical dashboard for server-wide Matrix administration.

For an installation on `matrix.example.org` with identity domain `example.org`:

| Purpose | Where to go |
| --- | --- |
| Welcome/status page | `https://matrix.example.org/` |
| Your own account and device/session settings | `https://matrix.example.org/_continuwuity/account/` |
| Chatting | A separately installed Matrix client connected to `https://matrix.example.org` |
| Package settings: registration, email, upload limits, calls | Existing YunoHost webadmin → Apps → Continuwuity → Config panel |
| Matrix server administration: users, rooms, federation | The `#admins:example.org` room in a Matrix client, using the administrator account |
| Service health and logs | YunoHost Services or the shell commands below |

YunoHost's webadmin remains at its existing `/yunohost/admin` address. Choosing
an installation domain does not create another YunoHost administration portal.
Continuwuity's **Manage your account** page controls the signed-in user's own
account; use the Matrix admin room for server-wide operations. See the upstream
[admin command reference](https://continuwuity.org/reference/admin/).

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

Open the Continuwuity admin room, `#admins:<identity-domain>`, in a Matrix client.
Its bot accepts upstream commands such as:

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

### Choosing a root domain or a subdomain

**Recommended: install on `matrix.example.org`, with `example.org` as the
permanent Matrix identity.** This keeps IDs such as `@alice:example.org` while
letting the root domain keep its normal website or YunoHost portal.

The root/apex domain is the bare hostname, such as `example.org`; the root path
is `/` on any hostname. This package always installs at the root path of its
chosen web domain and reserves the whole domain for the app. Installing on
`example.org` assigns `https://example.org/` to Continuwuity's welcome page and
prevents another packaged app from sharing that hostname, even at `/blog`.
YunoHost checks for conflicting apps before installation. Separate subdomains
such as `www.example.org`, `chat.example.org`, and `auth.example.org` remain
independent and may host other apps. Installing at `/matrix` is unsupported.

Using `example.org` only as the **identity domain** has a smaller effect: the
package adds the three exact discovery routes listed below. It can coexist with
a normal website there. If that website already supplies Matrix discovery for
another homeserver, resolve the conflict before installing; two homeservers
cannot use the same Matrix identity domain.

The package does not change YunoHost's main domain or the identity domain's
default app. Keep that domain's website/portal choice as desired; you do not need
to set Continuwuity as its default app for discovery to work. YunoHost's own
administration and portal routes remain separate from the homeserver.
If the installation domain already has a homepage redirect (a default app or
public-apps-page setting), that redirect can still control what opens at `/`.
Check those domain settings if the homeserver address redirects elsewhere;
the Matrix API and discovery paths are separate from that root-only redirect.

### How discovery connects the domains

The web/API domain and permanent identity domain may differ. For example:

| Setting | Example |
| --- | --- |
| API domain | `matrix.example.org` |
| Permanent server name | `example.org` |
| User ID | `@alice:example.org` |
| Client discovery | `https://example.org/.well-known/matrix/client` points to `https://matrix.example.org` |
| Federation discovery | `https://example.org/.well-known/matrix/server` advertises `matrix.example.org:443` |
| Support discovery | `https://example.org/.well-known/matrix/support` publishes the configured administrator contact |

The package adds only its own discovery snippet on the identity domain. Existing
conflicting Matrix discovery rules are not overwritten. Both domains must exist
in YunoHost and retain working DNS and HTTPS certificates. The Matrix API stays
public at the reverse proxy: Matrix validates user tokens and federation
signatures itself. SSOwat login redirects would break clients and federation.
See upstream's [delegation guide](https://continuwuity.org/guides/delegation) for
the protocol. This package manages discovery on the same YunoHost server; an
identity domain whose website is hosted elsewhere needs a separately planned
delegation setup.

### What can change later

YunoHost's **Change URL** can move the API to another dedicated domain. It
preserves `server_name`, database identity and all Matrix IDs; updates discovery,
SMTP mapping and the optional Dex callback; and rolls back its own files and
Dex fragment if reconfiguration fails. Keep the old identity domain indefinitely.
Changing to a subpath is unsupported.

For example, installing directly on `example.org` now and moving the API to
`matrix.example.org` later can free the root homepage while keeping
`@alice:example.org`. But installing on `matrix.example.org` with an empty identity
field fixes the identity as `matrix.example.org`: moving the API later does not
shorten those IDs to `@alice:example.org`. Decide that suffix before first use.

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
Before stopping the service, the package measures the data directory and
requires available staging space of at least 110% of its allocated size plus
64 MiB; the final YunoHost archive requires additional space. Provision
additional capacity and monitor backup duration as media grows. Routine
upgrades/settings changes no longer recursively change ownership of every
database and media file.

All data is included even for automatic upgrade backups and when
`BACKUP_CORE_ONLY` or `do_not_backup_data` would normally omit data. Restoring
old code against a newly migrated RocksDB database is unsafe. The restore script
rejects archives without a complete data snapshot, and replaces retained data
rather than merging incompatible database files. Before changing retained data,
the restore now preflights Dex (when enabled) and moves any nonempty original
data directory into a root-only rollback directory on the same filesystem.
If the new data/service fails to restore, the original database and media are
moved back, but YunoHost may still remove the failed application: you must
recover the app itself from a verified complete archive. The temporary original
data directory is discarded only after the new service passes readiness.

Back up **Dex as well** for delegated authentication, and restore it before this
app. The Continuwuity backup carries its client secret and subject mappings, but
does not copy or overwrite Dex's shared LDAP connector and other clients.

Restoration requires the same identity domain and matching architecture for the
archived binary. Cross-architecture restoration is not implemented: restore on
the original architecture, or plan a tested binary/data migration separately.
The package also rejects symlinked data directories and mounted/symlinked
media. External rclone/FUSE primary media is not a supported storage mode; do
not mount it manually, because a lost mount could otherwise expose an empty
underlying directory. For a supported recovery option, see the
[Storj encrypted offsite backup guide](STORAGE.md).

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
