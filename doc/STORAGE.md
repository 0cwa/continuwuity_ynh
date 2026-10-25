# Encrypted offsite backups and external media: support boundary

The current package supports **local POSIX database and media storage** only.
Continuwuity keeps its RocksDB data and media under `$data_dir` (normally
`/home/yunohost.app/continuwuity`), with media in `$data_dir/media/`.

**Do not mount rclone, Storj, S3/FUSE, or a symlink at `media/` in production
with this package version.** The backup and restore scripts explicitly reject
mounted or symlinked media rather than creating an apparently successful but
incomplete archive. An unmounted remote cannot reliably be distinguished from
an ordinary local media directory without an explicitly managed storage mode.
This package does not yet implement that mode.

## Recommended now: Restic-encrypted backups in Storj

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

## Future experiment: Storj as *live* media storage

Upstream supports mounting an S3-backed filesystem at `$database_path/media`,
but does not implement a native transactional object-store media backend.
**RocksDB must remain on a local durable filesystem.**

A candidate for a disposable development VM is:

```text
Continuwuity /home/yunohost.app/continuwuity/
  RocksDB and signing keys ............. local durable filesystem
  media/ ................................ rclone FUSE: crypt over Storj S3
/var/cache/continuwuity-rclone/ ......... local persistent, monitored VFS cache
/etc/continuwuity-rclone/ ............... root-owned credentials + crypt keys
```

Rclone's native Storj backend keeps encryption keys local, but sends roughly
2.68 times the plaintext upload size across the network due to local erasure
coding. Storj's S3 gateway avoids this amplification. `rclone crypt` over the
gateway can provide separate client-controlled encryption for content and
filenames; names, sizes and timestamps still need metadata/privacy review.

**This is an engineering experiment, not an installation recipe.** A future
storage mode must ship as a coordinated package change with all these gates:

- Persist `media_storage=local|remote` and expected mount identity; verify it
  before startup and every backup, restore, upgrade, configuration change,
  service restart and purge. An absent mount must fail closed, never expose the
  empty underlying mountpoint to the application.
- Own and order the mount and daemon in systemd; prevent FUSE-unmount races and
  ensure `ProtectHome`, `ReadWritePaths` and mount namespaces work together.
- Make VFS write-back durability explicit. Closing a locally cached file does
  **not** mean it has reached Storj. Survive mount kill, power loss, network
  interruption, dirty-cache replay, full disk, and S3 gateway errors.
- Check file modification/birth-time behavior and media pruning. Storj's S3
  gateway has a documented multipart metadata limitation; rclone may need VFS
  `writes`/`full` and a suitable S3 upload cutoff. Benchmark large files,
  thumbnails and federated downloads.
- Define a consistent recovery generation linking a local RocksDB snapshot and
  a verified remote media checkpoint/version; never back up just the database
  and claim media is in that archive. Preserve object versions during rollback.
- Avoid recursively changing ownership or copying entire remote media on every
  config change or upgrade. Never delete the remote bucket during uninstall or
  purge without a separately explicit administrator decision.
- Test on an isolated YunoHost VM: offline boot, mid-write disconnection,
  force-killed rclone, disk exhaustion, backup with mount missing, cross-host
  restore using escrowed keys, and remote object loss.

Until those tests pass, keep media local and use **encrypted offsite backups**
to Storj or another independently controlled provider.

### References

- [Restic S3-compatible repositories](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#s3-compatible-storage)
- [Storj Restic guide](https://storj.dev/dcs/third-party-tools/restic)
- [rclone Storj native/gateway comparison](https://rclone.org/storj/)
- [rclone crypt](https://rclone.org/crypt/)
- [rclone mount and VFS write-back](https://rclone.org/commands/rclone_mount/)
- [rclone S3 Storj gateway metadata caveat](https://rclone.org/s3/#storj)
- [Continuwuity maintenance](https://continuwuity.org/maintenance)
