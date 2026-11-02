Continuwuity has been upgraded. The package preserves your Matrix identity,
database, media, registration token, optional Dex client, and `overrides.toml`.

The automatic safety backup contains the full database and media so that a
failed upgrade can recover an earlier database state. This remains true when
YunoHost's `BACKUP_CORE_ONLY` or `do_not_backup_data` option is set: those options
do not disable this package's safety copy. A RocksDB format migration can make
the older binary unable to read the upgraded database, so a code-only backup
would not be a safe rollback point.

Plan for at least 110% of the data directory's allocated size plus 64 MiB of
free space where the backup snapshot is staged, and additional room for the
final YunoHost archive. The service is stopped while the complete database and
media snapshot is copied. If the service was running, the backup script restarts
it after copying the snapshot, before YunoHost creates the archive. The upgrade
may stop it again. Reflinks can make the snapshot much faster and cheaper
when the filesystem supports them; otherwise a full copy is made. On a small or
slow host, copying and archiving a large media store can take a while and
compete with the homeserver for disk I/O. Schedule upgrades during a quiet
period, and expect clients to reconnect after the snapshot interruption.

Check login and federation after upgrading. In Dex mode, also verify a new
browser login; an existing Matrix session alone does not test your identity
provider.
