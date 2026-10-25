Your Matrix server is available at **https://__DOMAIN__**.

The first administrator is **@__ADMIN__:__SERVER_NAME__**. Use the Matrix
password supplied during installation. If you selected Dex, start the OAuth
browser login, authenticate with your YunoHost credentials, choose the existing
administrator username, and enter that Matrix password to link the account.

The homeserver includes its own account pages. Install or choose a separate
Matrix chat client to send messages. Open the **Apps → Continuwuity → Config
panel** to enable invitation-token registration, optional email, or settings for
an existing TURN/MatrixRTC service.

Email is disabled until configured. Enabling it checks the YunoHost mail service
and the installation domain's trusted TLS certificate without sending a message.

Keep DNS and certificates for **__SERVER_NAME__** permanently. See the
Administrator and Dex guides in the package repository for operating details.
