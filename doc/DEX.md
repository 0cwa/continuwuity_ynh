# YunoHost authentication through Dex

Continuwuity 26.9.1 has native support for delegated OpenID Connect (OIDC)
authentication. This package can connect it directly to the separately installed
[YunoHost Dex app](https://github.com/YunoHost-Apps/dex_ynh). Matrix Authentication
Service (MAS) is not needed for this integration.

## Choose the authentication mode before installing

The default **password** mode uses Continuwuity's own accounts and passwords. Its
OAuth and legacy Matrix login flows are both available.

The optional **dex** mode sends users to Dex to authenticate with their YunoHost
LDAP credentials. Continuwuity creates and maintains a dedicated OIDC client in
Dex, uses its own OAuth endpoints for Matrix clients, and stores the association
between a Dex subject and a Matrix account in its database.

**Delegated OIDC disables legacy Matrix logins.** Choose a Matrix client that
supports OAuth / next-generation Matrix authentication. A client supporting only
legacy password login or the older `m.login.sso` flow will not work. Even the
local administrator's bootstrap password cannot be used for a normal Matrix
password login while delegated authentication is enabled; it is used to prove
ownership when linking that account through the OAuth flow.

## Installation and first sign-in

1. Install Dex through YunoHost first. Its HTTPS URL must have a valid trusted
   certificate, working DNS, and be reachable from both the homeserver and users'
   browsers. A self-signed certificate does not pass this package's checks.
2. Install this package with authentication mode `dex` and select the existing
   Dex application. No manual client ID, client secret, or callback entry is
   needed in Dex's install questions.
3. Choose the YunoHost account that will administer the homeserver and a strong
   local bootstrap password. The package creates the administrator account
   before exposing the homeserver, so the first person to sign in cannot claim
   server administration.
4. In an OAuth-capable Matrix client, choose this homeserver, open the browser
   sign-in flow, and choose **Continue with YunoHost**. Authenticate at Dex using
   the selected administrator's YunoHost credentials.
5. Enter the administrator's existing Matrix username when Continuwuity asks
   for a user ID, then supply its local bootstrap password to link it. The
   YunoHost password and local bootstrap password are separate credentials.
   Subsequent logins use Dex.

For other users, the first successful Dex login prompts for a Matrix username
and creates an account, or asks for the old Matrix password if the requested
account already exists. Only one Dex subject may be linked to each account.

The stock Dex package's LDAP connector exports a display name as the OIDC `name`
claim, but does not configure `preferredUsernameAttr`. Therefore this package
keeps Continuwuity's username prompt enabled. It never treats a changeable human
display name as a stable Matrix username. Matrix usernames are not automatically
reserved for their matching YunoHost usernames. Reserve needed accounts before
opening the service to additional users if that naming policy matters.

Display names are imported on registration. Existing names and room-specific
names remain under the user's control. The email scope is requested; upstream
only uses the email claim for its account email feature when SMTP is configured.
No avatar URL claims are imported.

## What the package manages

- A client named after the application instance in Dex's
  `config.yaml.d/<continuwuity-app-id>` directory. The package checks its own
  ownership marker before replacing or deleting the fragment.
- A single exact callback URL:
  `https://<homeserver-web-domain>/_continuwuity/oidc/complete`.
- A stable random client secret stored in protected YunoHost application
  settings and in `oidc-client-secret` in this app's installation directory.
  The latter is readable by root and the homeserver's group, with mode `0640`.
- An `oidc.toml` configuration file using upstream's `client_secret_file`
  option. Secrets are not passed to the homeserver as process arguments.
- Re-registration during upgrades, restores and web-domain changes; removal of
  this app's own Dex client when the app is removed. The helper validates HTTPS
  discovery and the exact issuer before enabling delegated authentication.

Dex's configuration monitor and its own startup script regenerate the combined
configuration. This package does not rewrite Dex's LDAP connector, edit other
clients, install Dex automatically, or remove it on uninstall.

The callback follows the homeserver's web/API domain. The Matrix server name
which appears after `:` in a Matrix ID is permanent; changing the web domain
must not change that identity.

## Access control and account lifecycle

YunoHost's SSO portal is not an authentication gateway for Matrix API requests.
The API, OAuth routes, and federation endpoints must remain publicly reachable;
Continuwuity validates Matrix tokens, while Dex validates interactive logins.
Signing into the YunoHost portal alone does not imply a shared Dex login session.

The stock Dex LDAP connector permits YunoHost LDAP users to authenticate. This
package does not claim that YunoHost app-permission groups restrict Dex logins,
and it does not silently change the shared connector to impose such a filter.
If only a subset of YunoHost users should access this homeserver, apply an
appropriate identity-provider policy and test it before allowing general use.
OIDC account creation is controlled by successful identity-provider
authentication; the local-password registration toggle does not prevent it.

Deleting or disabling a YunoHost/Dex user prevents subsequent authentication at
the provider. It does **not** automatically revoke Matrix sessions or deactivate
the account in Continuwuity. Deactivate users or revoke their Matrix sessions
with Continuwuity's administrative tools when access must end immediately.
The package intentionally does not interpret YunoHost user deletion as a request
to destroy Matrix history or identities.

## Backup, restore and recovery

Back up both Continuwuity and Dex. The Continuwuity backup includes the Matrix
database, its subject mappings, and the client credentials/settings. Dex's LDAP
connector identity and issuer must remain consistent to preserve subject
associations. Restore Dex before restoring a Continuwuity instance that uses
delegated authentication. The package re-adds its client with the saved secret;
it does not restore or replace Dex's global configuration.

Changing Dex's URL, removing Dex, or changing its connector IDs can break login.
Coordinate such changes across all apps using Dex. Existing Matrix sessions may
continue to work while fresh logins fail. If the issuer URL changes, refresh the
Continuwuity package configuration through an upgrade after Dex works at its new
URL, and verify account linkage before general use.

Changing the authentication mode is an account migration, not a transparent
toggle. To migrate existing local accounts to OIDC, authenticate at Dex and
prove ownership with each account's old local password. Administrators can
inspect and repair subject mappings with upstream's `!admin oidc link` and
`!admin oidc unlink` commands. Never link an unverified subject to an existing
account merely because its display name matches.

To return to local authentication, follow upstream's decommissioning procedure:
remove the delegated-authentication configuration, restart, and use
`!admin users reset-password --convert-to-local-account` for affected users.
An OIDC shadow account does not acquire a working local password just because
Dex is disabled. Keep the package's stored authentication mode consistent so a
subsequent upgrade does not re-enable an old integration. If bots need tokens in
delegated mode, use upstream's documented administrator token-issuance flow.

## Primary references

- [Continuwuity delegated authentication](https://continuwuity.org/authentication/oidc)
- [Continuwuity authentication flows](https://continuwuity.org/authentication)
- [Released v26.9.1 configuration](https://forgejo.ellis.link/continuwuation/continuwuity/src/tag/v26.9.1/conduwuit-example.toml)
- [Released OIDC implementation](https://forgejo.ellis.link/continuwuation/continuwuity/src/tag/v26.9.1/src/service/oidc/mod.rs)
- [Dex YunoHost client-fragment interface](https://github.com/YunoHost-Apps/dex_ynh/blob/master/doc/ADMIN.md)
- [Dex YunoHost LDAP configuration](https://github.com/YunoHost-Apps/dex_ynh/blob/master/conf/config.yaml.head)
- [Dex LDAP claim mapping](https://github.com/dexidp/dex/blob/v2.46.1/connector/ldap/ldap.go)
