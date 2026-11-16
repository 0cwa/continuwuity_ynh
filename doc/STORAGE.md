# Media storage and offsite backup

Local POSIX storage is the default. Native S3 media mode uses the upstream
`26.11.0-alpha.2` prerelease and remains an experimental alpha, not a
production-ready option. The local S3 YunoHost acceptance test passed on amd64,
and the ARM binary passed an S3 smoke test; no real-provider test has been run.
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
speed. In particular, low-resource xli plans may have a long interruption;
measure with representative data before relying on S3 mode. Every run creates a
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

## Restic-encrypted offsite copies for local mode

This protects an entire recovery generation (RocksDB, media, signing identity,
OIDC account mappings and settings) without putting live database or media I/O
on object storage. Restic encrypts locally **before** sending data to Storj's
S3 gateway, so no rclone crypt layer is required for confidentiality in this
backup-only design. It also keeps versioned snapshots instead of propagating
deletions as a plain sync would.

### Prerequisites and keys

1. Create a **private, dedicated** Storj bucket and S3 gateway access grant.
   Limit its scope to the backup bucket/prefix where possible; configure TLS.
2. On YunoHost, install `restic` from your Debian/YunoHost package sources.
3. Create a root-only secrets directory and a strong Restic repository password:

   ```bash
   sudo install -d -m 0700 /etc/continuwuity-offsite
   sudo sh -c 'umask 077; openssl rand -base64 48 > /etc/continuwuity-offsite/restic-password'
   sudoedit /etc/continuwuity-offsite/env
   sudo chmod 600 /etc/continuwuity-offsite/env
   ```

   The `env` file should contain **actual credentials entered locally**, not
   copied into Git or YunoHost logs:

   ```bash
   RESTIC_REPOSITORY='s3:https://gateway.storjshare.io/REPLACE-BUCKET/continuwuity-backups'
   RESTIC_PASSWORD_FILE='/etc/continuwuity-offsite/restic-password'
   AWS_ACCESS_KEY_ID='REPLACE-WITH-STORJ-S3-ACCESS-KEY'
   AWS_SECRET_ACCESS_KEY='REPLACE-WITH-STORJ-S3-SECRET-KEY'
   ```

   Use your Storj gateway endpoint if it differs. Keep the repository password
   **and** S3 access/recovery grants in an independent, offline password manager.
   Losing the Restic password makes the encrypted archive unrecoverable.

### Create, upload, and verify a recovery generation

The YunoHost backup must complete successfully *before* Restic runs. A
plain Restic backup of the live RocksDB directory does **not** establish a
consistent database snapshot.

```bash
# If Dex is configured, back up its actual installed app ID too (e.g. dex).
sudo yunohost backup create --apps continuwuity

# In a trusted root-only shell; avoid shell tracing, public operation logs,
# or credentials in command-line arguments.
sudo bash -c '
  set -e
  set +x
  set -a
  source /etc/continuwuity-offsite/env
  set +a
  restic init  # ONLY the first time
  restic backup --tag continuwuity-ynh /home/yunohost.backup/archives
  restic snapshots
  restic check
'
```

Replace the YunoHost backup command with `--apps continuwuity dex` if this
instance uses the stock Dex app, and use the correct instance IDs for
multi-instance installations. Perform `restic init` only when the remote
repository does not exist; on later runs omit that line. Uploading the entire
YunoHost archives folder can include other YunoHost backups; use a separate
staging directory if you require tighter data scope.

**Operations:** keep the last known-good local YunoHost backup until Restic
reports success; run `restic check` periodically (including sampled data
readback). Monitor the timer/job, backup size, last success, remote bandwidth
and Restic failures. Add a verified retention policy only after testing
recovery, for example `restic forget --keep-daily 7 --keep-weekly 4
--keep-monthly 12 --prune`. A stolen backup account that can delete objects
is still a risk; keep an independent recovery copy as well.

### Recovery drill (required)

1. On an **isolated** host, supply the separate repository password and S3
   credentials. Run `restic snapshots`, then `restic restore latest --target
   /root/restic-recovery`; locate the restored YunoHost backup archive and
   matching backup metadata inside its `home/yunohost.backup/archives` path.
2. Import that backup into the test YunoHost backup archive location using
   YunoHost's documented import procedure. Restore Dex **before** Continuwuity
   if OIDC was enabled.
3. Verify the restored Matrix signing identity, login, room history, new and
   previously uploaded media, and the ability to federate. Record the tested
   archive timestamp, architecture and restore duration. Never perform the
   first recovery drill against the only production copy.

### References

- [Restic S3-compatible repositories](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#s3-compatible-storage)
- [Storj Restic guide](https://storj.dev/dcs/third-party-tools/restic)
- [rclone Storj native/gateway comparison](https://rclone.org/storj/)
- [rclone crypt](https://rclone.org/crypt/)
- [rclone mount and VFS write-back](https://rclone.org/commands/rclone_mount/)
- [rclone S3 Storj gateway metadata caveat](https://rclone.org/s3/#storj)
- [Continuwuity maintenance](https://continuwuity.org/maintenance)
