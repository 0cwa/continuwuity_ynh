#!/bin/bash

# Service ownership, readiness and offline administrator bootstrap.
# Loaded by _common.sh; YunoHost supplies settings and helpers at call time.

continuwuity_permissions() {
    # The daemon can write only its data, not its executable or credentials.
    chown -R "root:$app" "$install_dir"
    chmod 750 "$install_dir" "$install_dir/continuwuity"
    find "$install_dir" -maxdepth 1 -type f ! -name continuwuity -exec chmod 640 {} +
    # Resources create the data directory with app ownership, and archive
    # restore preserves ownership of its contents. Never recursively chown
    # database/media on routine maintenance.
    chown "$app:$app" "$data_dir"
    chmod 700 "$data_dir"
}

continuwuity_register_service() {
    yunohost service add "$app" --description="Continuwuity Matrix homeserver"
}

continuwuity_wait_ready() {
    local attempt consecutive=0
    for attempt in $(seq 1 90); do
        if curl --silent --fail --max-time 2 \
            -H "Host: $domain" "http://127.0.0.1:$port/_matrix/client/versions" >/dev/null; then
            consecutive=$((consecutive + 1))
            # Background workers (including SMTP/OIDC) can fail just after the
            # listener opens, so require several consecutive healthy probes.
            if [[ "$consecutive" -ge 3 ]] && systemctl is-active --quiet "$app.service"; then
                return 0
            fi
        else
            consecutive=0
        fi
        if systemctl is-failed --quiet "$app.service"; then
            ynh_die "Continuwuity failed to start. Inspect its service journal."
        fi
        sleep 2
    done
    ynh_die "Continuwuity did not become ready. Inspect journalctl -u $app."
}

continuwuity_bootstrap_admin() (
    # Never expose an unclaimed server, and never print upstream's password-bearing
    # admin command result to the YunoHost operation log.
    set -eu
    set +x
    local bootstrap="$install_dir/.bootstrap.toml"
    local bootstrap_log
    bootstrap_log="$(mktemp)"
    chmod 600 "$bootstrap_log"
    trap 'rm -f -- "$bootstrap" "$bootstrap_log"' EXIT
    export CONTINUWUITY_BOOTSTRAP_USER="$admin"
    export CONTINUWUITY_BOOTSTRAP_PASSWORD="$password"
    python3 "$YNH_APP_BASEDIR/conf/bootstrap.py" "$bootstrap"
    unset CONTINUWUITY_BOOTSTRAP_USER CONTINUWUITY_BOOTSTRAP_PASSWORD
    chown "$app:$app" "$bootstrap"
    if ! ynh_exec_as_app "$install_dir/continuwuity" \
        --config "$install_dir/config.toml" --config "$bootstrap" \
        -O 'allow_announcements_check=false' --maintenance \
        >"$bootstrap_log" 2>&1; then
        ynh_die "Offline administrator creation failed. No public server was started. Check the selected username and package configuration."
    fi
)

# A removal without --purge retains the data directory, which is what stock
# package_check expects to reinstall over. Never adopt or delete that data:
# a fresh install has a new identity and signing key, so move it aside intact.
continuwuity_retire_retained_data() {
    [[ -d "$data_dir" && ! -L "$data_dir" ]] || return 0
    [[ -n "$(find "$data_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]] || return 0

    local retired
    retired="${data_dir}.preinstall-$(date +%Y%m%d%H%M%S)"
    [[ ! -e "$retired" ]] || retired="${retired}-$$"
    mv -- "$data_dir" "$retired" \
        || ynh_die "Could not move retained data in $data_dir aside; leaving it untouched."
    mkdir -- "$data_dir"
    chown --reference="$retired" -- "$data_dir"
    chmod --reference="$retired" -- "$data_dir"
    ynh_print_warn "Retained data from a previous installation was found and moved to $retired. The new homeserver starts empty. To recover the old one, restore its YunoHost backup, or remove this app, stop the service and swap the two directories back."
}
