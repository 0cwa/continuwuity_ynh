## What the installation domain serves

This installs a Matrix homeserver with a **welcome/status page and personal
account/login pages**. It does not include a chat client or a graphical
server-administration portal. Manage package settings in the existing YunoHost
webadmin under **Apps → Continuwuity → Config panel**. Manage Matrix users and
rooms through Continuwuity's admin room in a separate Matrix client.

## Choose your domains before continuing

**Recommended:** choose **`matrix.example.org` as the installation domain** and
enter **`example.org` as the Matrix identity domain**. Users then have IDs such as
`@alice:example.org`, and your existing website or portal can stay at
`https://example.org/`.
If YunoHost preselects or stars its main domain, choose the dedicated subdomain
for this layout. You do not need to make Continuwuity the root domain's default
app.

- **Installation domain:** the public Matrix service and browser pages. This app
  reserves that whole web domain at `/`; it cannot share the same hostname with
  another web app, even at a subpath. Other subdomains remain available.
- **Matrix identity domain:** the permanent suffix in user IDs and room aliases.
  Leave it empty to use the installation domain, giving
  `@alice:matrix.example.org` in this example. Enter `example.org` for shorter
  IDs. The package adds only Matrix discovery URLs to a different identity
  domain; it does not replace that domain's homepage.

**If you choose the root/apex domain `example.org` as the installation domain,
`https://example.org/` is assigned to Continuwuity's welcome page.** Choose this only if
you want to dedicate that web domain to the homeserver. You do not need to install
on the root domain to get `@alice:example.org` IDs. Installation under a path such
as `example.org/matrix` is unsupported.
YunoHost's own admin and portal URLs remain available. Existing homepage
redirects are separate domain settings and may still affect what opens at `/`.

Both selected domains must already exist on this YunoHost server, resolve to it,
and have trusted HTTPS certificates. A separate identity domain's
`/.well-known/matrix/client`, `/.well-known/matrix/server`, and
`/.well-known/matrix/support` discovery paths must be available.
Keep the identity domain permanently: **Matrix identities cannot be
renamed**, even when changing the web/API address later.

## Accounts and operation

- The selected administrator gets a Matrix account with the same username and
  the separate password supplied here. The initial password cannot contain
  whitespace; it can be changed in a Matrix client later.
- Local registration starts closed. Additional local accounts require an admin
  command or enabling invitation-token registration in the configuration panel.
- For Dex mode, install and configure Dex first. Its HTTPS discovery URL must be
  reachable with a trusted certificate. Only OAuth-capable Matrix clients can
  log in. Dex's default LDAP connector admits all YunoHost users; the homeserver's
  portal permission is not an admission policy.
- Backups include the full database and media, even before upgrades. Budget for
  a complete extra copy and a short interruption while the snapshot is created.
- This initial package has automated and upstream-binary checks, but still needs
  its full YunoHost VM acceptance test before a production deployment.
