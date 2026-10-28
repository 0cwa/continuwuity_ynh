# Continuwuity for YunoHost

A YunoHost packaging-v2 application for [Continuwuity](https://continuwuity.org/),
the community-maintained Matrix homeserver. This repository packages upstream
binaries; the homeserver source lives on
[Forgejo](https://forgejo.ellis.link/continuwuation/continuwuity).

Requires **YunoHost 12.1.18 or newer** on **amd64 or arm64**. The packaged release,
architecture-specific downloads and SHA256 checksums are pinned in
[manifest.toml](manifest.toml).

**This is an independent package under development, not a production-validated
release.** Automated checks do not replace the
[required YunoHost acceptance run](doc/TESTING.md#required-yunohost-acceptance-run).

## Install

Use a dedicated web domain, such as `matrix.example.org`. To give users IDs such
as `@alice:example.org`, enter `example.org` as the **permanent Matrix identity
domain**. Leaving that field empty uses the web domain for Matrix IDs instead.

The web domain is reserved in full; subpath installation is unsupported. A
separate identity domain keeps its website or portal and gains only Matrix
`/.well-known/` discovery routes. Both domains must already exist on this
YunoHost server with working DNS and trusted HTTPS certificates, and the
identity domain's Matrix discovery paths must be available. Keep control of the
identity domain permanently: Matrix IDs cannot be renamed by changing the API
address. See the [installation notes](doc/PRE_INSTALL.md) and
[domain guide](doc/ADMIN.md#federation-delegation-and-domain-changes).

```bash
sudo yunohost app install https://github.com/0cwa/continuwuity_ynh
```

Choose the first administrator's YunoHost username and a separate Matrix
password when prompted. The initial password cannot contain whitespace; it can
be changed in a Matrix client after installation. Do not put credentials in the
command line.

Local-password mode has independent Matrix accounts and closed registration by
default. For YunoHost credentials through **Dex**, install Dex with working HTTPS
first, read the [Dex guide](doc/DEX.md), and select it during installation. Dex
requires OAuth-capable Matrix clients; it is not direct LDAP or SSOwat login.
The reserved administrator links their account using the initial Matrix password.
Authentication mode is not a routine config-panel toggle: changing it requires
an account migration.

Connect a separate Matrix client to `https://matrix.example.org`. The homeserver
web root provides a welcome page and personal account-management links, not a
chat client or graphical server-administration dashboard.

## Operate

Manage package settings in **YunoHost → Apps → Continuwuity → Config panel**.
Manage Matrix users and rooms through Continuwuity's admin room in a Matrix
client. [Administration entry points](doc/ADMIN.md#web-pages-and-administration)
explain the distinction.

The package manages the service account, loopback backend, Nginx/TLS integration,
Matrix discovery, offline first-admin creation, and complete database/media
backups. Optional settings cover Dex, verified authenticated SMTP, an existing
TURN server, and a MatrixRTC LiveKit authorization service. It does not deploy
the separate calling services.

Backups stop the homeserver while copying **both database and media**; provision
space for the snapshot and archive. Upgrade safety backups include the complete
data even when `BACKUP_CORE_ONLY` or `do_not_backup_data` is set. Normal uninstall
retains data, and reinstall refuses to overwrite it. Restore a backup to recover
that identity; YunoHost's explicit purge option removes retained data.

| Documentation | Contents |
| --- | --- |
| [Administrator guide](doc/ADMIN.md) | Configuration, accounts, email, calls, backups, upgrades and recovery |
| [Dex / OpenID guide](doc/DEX.md) | Client registration, account linking, access restrictions and migration |
| [Testing and acceptance](doc/TESTING.md) | Automated coverage, its limits and real-host release requirements |
| [Contributing](CONTRIBUTING.md) | Repository layout, local validation and release maintenance |

## License

Original package code and upstream are [Apache-2.0](LICENSE). The YunoHost proxy
configuration test fixture retains its AGPL-3.0-or-later license; see
[third-party notices](THIRD_PARTY.md).
