Continuwuity is a community maintained Matrix homeserver written in Rust. It
supports federated, encrypted messaging through Matrix clients and stores its
database and media locally.

This YunoHost package includes HTTPS federation and discovery, safe database and
media backups, upgrades, a configuration panel, and an administrator account
created before the server becomes public. Optional Dex integration authenticates
users with YunoHost credentials through native OpenID Connect. Existing TURN and
MatrixRTC services can be configured for calls.

A dedicated domain is required. This package supports amd64 and arm64 and does
not include a separate Matrix chat client.
