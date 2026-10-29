# Testing on a production YunoHost server

Use a dedicated new subdomain and a fresh backup. Do not test on your main
Matrix domain.

1. **Back up.** Run `yunohost backup create` (system and all apps), copy the
   archive off the server, and check free space with `df -h` first.

2. **Create a test domain.** Add a dedicated subdomain such as
   `mx-test.example.org` with DNS, then run `yunohost domain cert install` for it.

3. **Install with that subdomain as the web domain.** Leave the Matrix identity
   (`server_name`) empty so the identity is that subdomain. Do not create
   delegation files on other domains.
   **Warning:** `server_name` is permanent. Never pick a domain that already
   serves a Matrix identity.

4. **Choose password authentication, not Dex.** Leave SMTP and TURN off for the
   first pass.

5. **Install and verify.** Adjust the branch if needed:

   ```bash
   yunohost app install https://github.com/0cwa/continuwuity_ynh/tree/release/ynh5 --domain mx-test.example.org
   ```

   Then check:

   ```bash
   nginx -t
   systemctl status continuwuity
   curl https://mx-test.example.org/_matrix/client/versions
   ```

   Log in with a Matrix client at `https://mx-test.example.org`.

6. **Check the operation log for the admin password.** Replace `<password>` with
   the password you entered. Find the install operation ID, then run:

   ```bash
   yunohost log list
   yunohost log show <id> --full | grep -F '<password>'
   ```

   The grep must print nothing.

7. **Exercise upgrade and backup.** Upgrade the app with
   `yunohost app upgrade continuwuity -u <url> --force`, then back up and restore
   the app only.

8. **Remove and check for leftovers.** Run `yunohost app remove continuwuity --purge`, then confirm:
   - `nginx -t` passes.
   - No `/etc/nginx/conf.d/*/continuwuity*` files remain.
   - No `continuwuity` system user remains.

   Without `--purge`, data is kept. A later reinstall moves it aside to
   `<data_dir>.preinstall-<timestamp>`. Delete that directory manually when you
   no longer need it.

9. **Only then, try Dex or a separate identity domain.** Use a throwaway domain
   and take a fresh backup first. Dex is restarted on install and remove, which
   briefly interrupts other OIDC clients.
