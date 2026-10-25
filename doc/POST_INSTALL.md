Your Matrix homeserver address is **https://__DOMAIN__**. Opening this address
normally shows a welcome/status page with a **Manage your account** link. If it
redirects elsewhere, check that domain's existing homepage redirect settings.
The personal
account pages are at **https://__DOMAIN__/_continuwuity/account/**.

Your permanent Matrix identity domain is **__SERVER_NAME__**. It supplies the
suffix in Matrix IDs; it can differ from the homeserver's web address. If the
domains differ, this package adds only Matrix discovery URLs on the identity
domain, leaving its homepage available for your website or portal.

The first administrator is **@__ADMIN__:__SERVER_NAME__**. Use the Matrix
password supplied during installation. If you selected Dex, start the OAuth
browser login, authenticate with your YunoHost credentials, choose the existing
administrator username, and enter that Matrix password to link the account.

Install or choose a separate Matrix chat client to send messages. The homeserver
does not include a chat client or a graphical server-administration dashboard.
For Matrix administration, sign into your client as the administrator and open
the **#admins:__SERVER_NAME__** room to use `!admin` commands.

In your existing YunoHost webadmin, open **Apps → Continuwuity → Config panel**
to enable invitation-token registration, optional email, or settings for an
existing TURN/MatrixRTC service.

Email is disabled until configured. Enabling it checks the YunoHost mail service
and the installation domain's trusted TLS certificate without sending a message.

Keep DNS and certificates for **__SERVER_NAME__** permanently. See the
Administrator and Dex guides in the package repository for operating details.
