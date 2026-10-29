# Continuwuity for YunoHost

A YunoHost packaging-v2 application for [Continuwuity](https://continuwuity.org/),
the community-maintained Matrix homeserver. This repository packages upstream
binaries; the homeserver source lives on
[Forgejo](https://forgejo.ellis.link/continuwuation/continuwuity).

Requires **YunoHost 12.1.18 or newer** on **amd64 or arm64**. The packaged release,
architecture-specific downloads and SHA256 checksums are pinned in
[manifest.toml](manifest.toml).

## Support status

- **Release gate (stock YunoHost package_check, level 6+):** install, upgrade,
  backup/restore, remove, multi-instance. See [Testing](doc/TESTING.md) for the
  recorded result.
- **Community-tested (reports welcome):** Dex/OIDC login, SMTP, federation/TURN,
  arm64 YunoHost, very large media stores, offsite Restic/Storj backup.

## Install

Use a dedicated web domain, such as `matrix.example.org`. To give users IDs such
as `@alice:example.org`, enter `example.org` as the **permanent Matrix identity
domain**. Leaving that field empty uses the web domain for Matrix IDs instead.

The web domain is reserved in full; subpath installation is unsupported. A
separate identity domain keeps its website and gains only Matrix `/.well-known/`
routes. Both domains need working DNS and trusted HTTPS on this server. Keep
control of the identity domain permanently: Matrix IDs cannot be renamed by
changing the API address. See the [installation notes](doc/PRE_INSTALL.md) and
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

Backups stop the homeserver while copying **both database and media**, so provision
free space for the snapshot and archive; large backups cause downtime on
non-reflink filesystems. Mounted app data and symlinked media are unsupported.
Upgrade safety backups always include complete data. Normal uninstall retains
data, and reinstall refuses to overwrite it; YunoHost's purge option removes it.

| Documentation | Contents |
| --- | --- |
| [Administrator guide](doc/ADMIN.md) | Configuration, accounts, email, calls, backups, upgrades and recovery |
| [Dex / OpenID guide](doc/DEX.md) | Client registration, account linking, access restrictions and migration |
| [Testing](doc/TESTING.md) | Automated checks and the release gate |
| [Production testing](doc/PROD_TEST.md) | Trying the package on a production YunoHost server safely |
| [Encrypted offsite backups and external storage](doc/STORAGE.md) | Current support boundary and Storj/Restic recovery plan |
| [Contributing](CONTRIBUTING.md) | Repository layout, local validation and release maintenance |

## License

Original package code and upstream are [Apache-2.0](LICENSE). The YunoHost proxy
configuration test fixture retains its AGPL-3.0-or-later license; see
[third-party notices](THIRD_PARTY.md).
