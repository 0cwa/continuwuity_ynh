- Use a **dedicated web domain** with working DNS and a trusted TLS certificate.
- Decide the permanent Matrix identity domain now. It appears after `:` in all
  user and room IDs and cannot be renamed later. Leave it blank to use the web
  domain. A different identity domain must already be configured in YunoHost;
  its `/.well-known/matrix/*` paths must be free.
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
