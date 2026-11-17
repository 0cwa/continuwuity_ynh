# Media storage and offsite backup

Local POSIX storage is the default and uses the official upstream
`26.11.0-alpha.2` prerelease. S3 mode uses an experimental fork by package
maintainer [0cwa](https://github.com/0cwa/continuwuity), not upstream. Both
are alpha software and are not production-ready. The local S3 YunoHost
acceptance test passed on amd64, and the ARM binary passed an S3 smoke test;
no hosted S3 provider has been tested.
Do not manually mount object storage or FUSE under the data directory. The
package's local mode rejects mounted data and symlinked media rather than
risking an incomplete backup.

## Experimental native S3 media mode

S3 mode is selected during installation and requires an explicit static access
key ID and secret. The live media bucket and the distinct snapshot bucket must
use the same S3 provider, endpoint and region. Omitting the endpoint selects
AWS S3. The package fixes the backend, endpoint, region, bucket and live key
prefix at installation; changing those requires a deliberate media migration.

The package coordinates each S3 media snapshot with a stopped database copy in
the YunoHost backup archive. It copies live media into a unique generation in
the snapshot bucket, verifies object contents with SHA-256, and puts a manifest
for that exact generation in the archive beside the database. These pieces are
one recovery set, not independent backups: retain the archive, manifest and
referenced generation together. Loss of either the archive or remote objects
leaves an incomplete recovery.

SHA-256 verification reads object contents over the network and can incur
provider egress charges. The service stays stopped while the database is
copied and the remote media generation is transferred and verified. The time
scales with data size, available resources, network throughput and provider
speed. Low-resource servers may experience a long interruption; measure with
representative data before relying on S3 mode. Every run creates a
new full remote generation, so storage costs grow with the number of retained
generations. The package does not automatically delete snapshots.

Restore verifies the archive's referenced generation and copies its media into
a new empty prefix that does not overlap the existing live prefix. It switches
the restored configuration to that fresh prefix only as part of the restore;
the source generation is left intact. Failed snapshot uploads can leave partial
orphan prefixes. Failed restores can leave a new target prefix behind; the
restore reports the prefix, and it must be reviewed and manually removed before
retry if it is safe to do so. Never delete a generation while a retained backup
archive or running instance may reference it.

## Backups and offsite storage

Use YunoHost to create and restore the app backup. The package stops the service
while it prepares a consistent copy of the database and media. A full local
media backup contains the media in the YunoHost archive. Backups, including
automatic upgrade backups, include all data even when `BACKUP_CORE_ONLY` or
`do_not_backup_data` is set; see the [administrator guide](ADMIN.md#backups-and-restore)
for disk-space requirements and recovery details.

YunoHost backup apps such as Borg and Restic can schedule backups and store
archives elsewhere. Follow the [YunoHost backup
documentation](https://doc.yunohost.org/en/backup/) and its [backup
methods](https://doc.yunohost.org/en/admin/backups/backup_methods/) for setup
and recovery. Back up any separate Dex app as well if it is used for login,
and test recovery on a separate server. Do not rely on copying the live
RocksDB directory as a consistent database backup.

For S3 media, copying a YunoHost archive elsewhere does **not** copy or manage
the separately stored S3 generation. Retain the referenced generation for as
long as its archive is retained, and verify both parts during recovery tests.
