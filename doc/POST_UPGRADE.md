Continuwuity has been upgraded. The package preserves your Matrix identity,
database, media, registration token, optional Dex client, and `overrides.toml`.

The automatic safety backup contains the full database and media so that a
failed upgrade can recover an earlier database state. Check login and federation
after upgrading. In Dex mode, also verify a new browser login; an existing
Matrix session alone does not test your identity provider.
