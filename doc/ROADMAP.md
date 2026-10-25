# Integration priorities

Work is ordered by practical value first, then implementation effort and the
ability to verify behavior without changing shared services unexpectedly.

| Order | Integration | Status and rationale |
| --- | --- | --- |
| 1 | Reproducible installation and administrator setup | Implemented: current stable generic binary on amd64/arm64, integrity checks, offline first-admin creation, restricted service account |
| 2 | Safe upgrades, complete backups and restore | Implemented: stopped database/media snapshot, full safety backups, replacement restore, failure-state tests |
| 3 | Native YunoHost web/service/permissions integration | Implemented: loopback backend, HTTPS federation/discovery, public Matrix routes, service status/journal, config panel |
| 4 | Permanent identity and web-domain changes | Implemented: managed split-domain discovery, immutable server name, transactional API-domain changes |
| 5 | Local account and everyday administration | Implemented: closed-by-default registration, invitation tokens, preserved advanced config, upload/log settings |
| 6 | Dex OpenID authentication | Implemented as an optional integration with native upstream OIDC; automatic owned-client lifecycle and protected secrets; requires OAuth-capable clients |
| 7 | Mail and existing calling services | Implemented as opt-in settings: verified authenticated SMTP, TURN credentials, MatrixRTC discovery |
| 8 | Regression testing and maintainer guidance | Implemented: package metadata/templates, failure paths, real upstream startup/API/persistence smoke, CI and acceptance documentation |

## Next, in priority order

1. **Run complete YunoHost VM acceptance tests.** This is the release gate,
   including real Dex browser login and subject mapping, SMTP delivery, multiple
   instances, domain moves, upgrade rollback, removal and restore. Repeat on
   arm64 hardware or a compatible VM. The package does not claim a catalog
   quality level until that evidence exists.
2. **Exercise real remote federation and calls.** Local discovery and API tests
   cannot establish public DNS/TLS reachability, federation with another server,
   or media paths through a separately deployed relay/SFU.
3. **Release-update automation.** Review upstream release notes and binary assets,
   update version/checksums together, and run tests. Do not auto-track main or
   assume future releases retain today's static-asset names.
4. **Plan explicit authentication migrations and revocation.** Native OIDC is
   usable now. Automatically claiming existing usernames, editing Dex's shared
   LDAP filters, or disabling Matrix accounts on a YunoHost user hook requires a
   clear policy and end-to-end tests; those are not implicit side effects here.
5. **Add managed relay/MatrixRTC deployments only when a concrete package
   interface is available.** Automatically taking over shared coturn ports,
   certificates and global configuration would create ownership conflicts.
6. **Add assisted Conduwuit migration and architecture changes.** These need
   separately verified version/database paths and recovery procedures. Other
   homeserver database formats are not compatible imports.

The implemented config panel intentionally covers ordinary operational choices.
It is not a generated editor for the entire upstream configuration reference.
