# Continuwuity for YunoHost

A YunoHost packaging-v2 application for [Continuwuity](https://continuwuity.org/),
the community maintained Matrix homeserver. This package targets **Continuwuity
26.9.1**, **YunoHost 12.1.17 or newer**, and **amd64 / arm64**.

This is an independent package under development. The repository contains the
complete installable package, automated checks, and an upstream-binary smoke
test. A full YunoHost VM acceptance run is still required before treating it as
a production-validated release. See [testing](doc/TESTING.md).

## Included integrations

| Area | Behavior |
| --- | --- |
| Installation | SHA256-pinned generic static binaries; no Rust build on the server |
| Accounts | Chosen administrator created offline before the public API starts; local registration closed by default |
| YunoHost | Dedicated service account, ports and data resources; service status and journal logs; configuration panel; multiple instances |
| Web and federation | YunoHost TLS/Nginx, loopback backend, preserved Matrix authorization headers, discovery on HTTPS port 443 |
| Delegation | Optional separate Matrix identity domain; API URL changes preserve Matrix IDs and signing identity |
| Backup and restore | Consistent stopped snapshot of database **and media**; complete data included in upgrade safety backups |
| Upgrades | Pinned sources, preserved administrator overrides, service readiness checks, YunoHost rollback support |
| OpenID Connect | Optional automatic client registration with an existing YunoHost Dex app; direct native OIDC, without MAS |
| Email | Optional authenticated YunoHost SMTP over verified STARTTLS, with a preflight connection check |
| Calls | Settings for an existing TURN server and MatrixRTC LiveKit authorization service |
| Maintenance | Automated syntax, configuration, lifecycle-failure and actual upstream API tests |

## Install

### Choose the homeserver address and Matrix identity

**Recommended: install on `matrix.example.org` and set the Matrix identity domain
to `example.org`.** This gives users IDs such as `@alice:example.org` while
keeping `https://example.org/` available for your website or YunoHost portal.
YunoHost may preselect or star its main domain in the installer; select your
dedicated Matrix subdomain instead if you want this layout.

The installer asks for two different domains:

- **Installation domain:** the public address used by Matrix clients and other
  homeservers. Opening it in a browser shows a Continuwuity welcome/status page
  with a link to **Manage your account**. The package includes personal account
  and login pages, but no chat client or graphical server-administration portal.
- **Permanent Matrix identity domain:** the suffix after `:` in user IDs and room
  aliases. Leaving this field empty uses the installation domain. This identity
  is permanent, even if you move the web/API address later.

| Installation domain | Matrix identity field | Example user ID | What happens at `https://example.org/`? |
| --- | --- | --- | --- |
| `matrix.example.org` | `example.org` | `@alice:example.org` | Your website/portal can stay; the package adds only Matrix discovery URLs. **Recommended.** |
| `matrix.example.org` | Empty | `@alice:matrix.example.org` | This package does not use `example.org`. |
| `example.org` | Empty or `example.org` | `@alice:example.org` | The web root is assigned to Continuwuity, normally showing its welcome page. That web domain is dedicated to this app. |

Choosing the root/apex domain (`example.org`) is supported if you deliberately
want to dedicate it to the homeserver. This package reserves the **whole chosen
web domain**, so another website or app cannot share that hostname, including at
a subpath such as `/blog`. It does not reserve other subdomains. Installation at
`https://example.org/matrix` is unsupported.
YunoHost rejects installations that conflict with another app already on that
hostname. Its own `/yunohost/admin` and `/yunohost/sso/` routes remain available.
Any existing homepage redirect is a separate domain setting and can still affect
what opens at `/`; this package does not change that setting.

With the recommended separate-domain layout, both domains must already exist on
this YunoHost server and have working DNS and HTTPS. The identity domain must have
available `/.well-known/matrix/client`, `/.well-known/matrix/server`, and
`/.well-known/matrix/support` paths. The package configures these automatically;
they tell clients and other servers where the homeserver is. An existing website
can keep its normal pages. Keep control of the identity domain permanently.
Both hostnames must reach this server for the automatic discovery setup. You do
not need to make Continuwuity the root domain's default app.

**Where do I administer it?** Use your existing YunoHost webadmin, under
**Apps → Continuwuity → Config panel**, for package settings. Manage Matrix users,
rooms, and other server operations in Continuwuity's admin room from a separate
Matrix client, using the administrator account created during installation.
Choosing a domain does not create a new YunoHost admin portal. See the
[administrator guide](doc/ADMIN.md#web-pages-and-administration) for the entry
points and [domain details](doc/ADMIN.md#federation-delegation-and-domain-changes).

### Installation steps

1. Add a dedicated web domain to YunoHost, for example `matrix.example.org`, and
   configure its DNS and a trusted HTTPS certificate.
2. Decide the permanent Matrix identity. Leaving the identity question empty
   uses the web domain, giving IDs such as `@alice:matrix.example.org`. To use
   `@alice:example.org`, enter `example.org`; that domain must also exist in
   YunoHost, and its Matrix discovery paths must be available.
3. Choose the first administrator's YunoHost username and a separate Matrix
   password. The initial password cannot contain whitespace because upstream's
   offline command parser splits on it. Password punctuation is preserved
   literally; the password can be changed in a client after installation.
4. Choose local passwords, or install Dex first and select **Dex** during this
   package's installation. Read the [Dex guide](doc/DEX.md) before choosing it.

For a repository accessible from your YunoHost machine:

```bash
sudo yunohost app install https://github.com/0cwa/continuwuity_ynh
```

If the repository is private, use an authenticated checkout on the YunoHost
machine, then install its absolute local path:

```bash
sudo yunohost app install /path/to/continuwuity_ynh
```

The installer asks for credentials interactively. There is no need to put
passwords or GitHub tokens in a command line.

Connect a Matrix client to `https://matrix.example.org`. The root web page is
Continuwuity's welcome page, with a link to personal account management; a full
chat client is installed separately, for example on `chat.example.org`.
In password mode, log in with the selected Matrix username and initial password.
In Dex mode, follow the browser flow and link the reserved administrator account
using its initial Matrix password.

## Authentication choices

**Local passwords** offer the broadest Matrix client compatibility. Accounts are
independent of YunoHost credentials. An administrator can create additional
accounts through Continuwuity's admin room, or enable token-protected local
registration in the YunoHost configuration panel.

**Dex** uses YunoHost credentials through Dex's LDAP connector and Continuwuity's
native delegated OIDC implementation. It disables legacy Matrix login; clients
must support OAuth / next-generation authentication. The stock Dex connector
admits YunoHost LDAP users, and users choose a Matrix username when first
linking/registering. YunoHost portal login and application-permission groups do
not automatically control Matrix sessions. See [the detailed account and access
control rules](doc/DEX.md).

The manifest intentionally does not claim direct LDAP or SSOwat single sign-on.
Authentication mode is fixed in the config panel because changing it requires
an account migration.

## Operate and maintain

- [Administrator guide](doc/ADMIN.md): users, email, calls, configuration,
  backups, domain changes, updates, and recovery.
- [Dex / OpenID guide](doc/DEX.md): automatic registration, first login, account
  mapping, access restrictions, backup order, and authentication migration.
- [Testing and acceptance](doc/TESTING.md): what automated checks cover and what
  still needs a real YunoHost host.
- [Encrypted offsite backups and external storage](doc/STORAGE.md): tested support
  boundary, a Storj/Restic recovery plan, and required future mount checks.
- [Priorities and remaining work](doc/ROADMAP.md): implementation order and
  integrations that need further validation or separate services.

### Important operational properties

Matrix identities cannot be renamed. Changing the web/API domain keeps the old
identity domain as a discovery endpoint; retain that domain's DNS and certificate.
Two packaged Matrix homeservers cannot claim the same identity domain.

Backups briefly stop the homeserver while copying the complete data directory.
The backup preflights staging free space before stopping the service; allow
additional capacity for the final archive. Large backups can cause downtime on
non-reflink filesystems. Live rclone/FUSE-mounted or symlinked media are not
supported and are rejected by the backup/restore scripts.
Code-only upgrade backups would not safely roll back a RocksDB migration, so
`BACKUP_CORE_ONLY` and `do_not_backup_data` do not skip this package's data copy.

Email is initially disabled. Enable it in the config panel after mail, DNS and
the installation domain's certificate are working. The package tests the SMTP
connection without sending a message before enabling it.

Normal uninstall keeps the data directory. Reinstall refuses to overwrite
retained data; restore a backup to recover that identity, or deliberately move
the retained data before creating a new homeserver. YunoHost's explicit purge
option removes retained application data.

## Development

```bash
python3 -m pip install -r tests/requirements.txt
python3 -m unittest discover -s tests -v
python3 tests/smoke_upstream.py --download
```

The smoke test downloads the architecture-specific binary named in the manifest,
verifies its SHA256, and uses a temporary local database. It does not modify a
running YunoHost installation or contact a real Matrix federation.

The package's original code and upstream are licensed under [Apache-2.0](LICENSE).
The YunoHost proxy configuration test fixture retains its AGPL-3.0-or-later
license; see [third-party notices](THIRD_PARTY.md). Upstream source and release notes are on
[Forgejo](https://forgejo.ellis.link/continuwuation/continuwuity).
