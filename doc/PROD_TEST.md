# Testing on a YunoHost server

Continuwuity and the optional S3 fork are alpha software. Test on a disposable
YunoHost server first; the package's automated tests do not establish readiness
for your server, S3 provider, or workload. Get permission before changing a
shared production server and arrange a maintenance window.

## Before installation

1. Check the YunoHost version, architecture, free disk and memory, service
   health, and available backup space. YunoHost 12.1.18 or newer is required.
   Verify a recent off-server backup and recovery procedure for existing apps.
2. Choose the permanent Matrix identity deliberately. For example, install on
   `matrix.example.org` and enter `example.org` as the identity domain to create
   accounts such as `@alice:example.org`. Both domains must already exist in
   YunoHost, resolve to this server, and have trusted HTTPS certificates. The
   installation reserves the whole installation domain at `/`; the identity
   domain's `/.well-known/matrix/client`, `/server`, and `/support` paths must
   be available. Do not replace an existing Matrix identity or other service.
3. Choose local accounts or Dex at installation. Local accounts are independent
   of YunoHost accounts. Dex requires an existing working Dex installation and
   OAuth-capable clients; configuring it restarts Dex and can interrupt its
   other clients. Local media is the default and uses the official upstream
   binary. S3 selects the package maintainer's experimental fork and requires
   separate live and snapshot buckets. See [storage](STORAGE.md).
4. Plan for a full database and media copy for backups and upgrade safety
   backups, even if YunoHost requests data exclusion. S3 backups also create a
   separate full remote generation and can keep the service stopped while
   media is copied and verified. Test storage and recovery costs with realistic
   data before relying on them.

## Install and verify

Install through YunoHost webadmin, or use the interactive CLI so the password
is entered at a prompt instead of placed in shell history or command arguments:

```bash
sudo yunohost app install https://github.com/0cwa/continuwuity_ynh
```

Select the installation domain, permanent identity, existing YunoHost user for
the initial Matrix administrator, authentication mode, and media backend. The
initial Matrix password is required in either authentication mode and cannot
contain whitespace; it can be changed in a Matrix client after installation.
Do not paste it into a command, chat, or diagnostic report. Check the installed
YunoHost CLI's `yunohost app install --help` if its prompts differ.

After installation:

1. Check `nginx -t` and the Continuwuity service in YunoHost. Confirm the
   installation-domain client API responds and the identity-domain Matrix
   discovery URLs point to the intended installation domain.
2. Use an actual Matrix client to sign in, create a test room, and upload and
   read media. For Dex, test a fresh OAuth login with a supported client.
3. Verify federation with an independent Matrix server and exercise any SMTP
   or call configuration you intend to use. Keep registration restricted until
   the intended access policy has been tested.
4. Create a YunoHost app backup and verify recovery on a disposable server with
   the same architecture. For S3, retain and verify the separate generation
   referenced by the archive; copying the archive alone does not copy media.
   Never overwrite production data merely to test a restore.
5. Measure backup duration, service interruption, disk use, and, for S3,
   network traffic and remote storage with representative data. A small test
   does not establish the costs for a populated homeserver.

A normal YunoHost removal retains application data unless explicitly purged.
Removing the app also removes its managed service and discovery configuration;
check for unintended effects on shared services. Preserve the identity domain,
server signing keys, database, and matching media when continuing an existing
Matrix identity. Do not purge data or delete remote snapshot generations without
first verifying what depends on them.
