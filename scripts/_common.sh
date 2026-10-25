#!/bin/bash

# Common lifecycle helpers. This file is sourced before YunoHost's helpers.
# shellcheck source=_dex.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/_dex.sh"

continuwuity_defaults() {
    # Some YunoHost helpers re-enable xtrace unconditionally; prevent later
    # secret-bearing operations from being recorded in operation logs.
    set +x
    ynh_app_setting_set_default --key=server_name --value="$domain"
    ynh_app_setting_set_default --key=auth_type --value=password
    ynh_app_setting_set_default --key=dex_app --value=""
    ynh_app_setting_set_default --key=allow_registration --value=false
    ynh_app_setting_set_default --key=allow_federation --value=true
    ynh_app_setting_set_default --key=max_upload_size --value=50
    ynh_app_setting_set_default --key=log_level --value=info
    # Upstream requires SMTP to work at startup. Enable from the config panel
    # after the installation domain's mail and TLS are ready.
    ynh_app_setting_set_default --key=smtp_enabled --value=false
    ynh_app_setting_set_default --key=turn_uris --value=""
    ynh_app_setting_set_default --key=turn_secret --value=""
    ynh_app_setting_set_default --key=turn_ttl --value=86400
    ynh_app_setting_set_default --key=rtc_service_url --value=""
    if [[ -z "${registration_token:-}" ]]; then
        registration_token="$(ynh_string_random --length=48)"
        ynh_app_setting_set --key=registration_token --value="$registration_token"
    fi
}

continuwuity_validate_settings() {
    # Some YunoHost helpers re-enable xtrace unconditionally; prevent later
    # secret-bearing operations from being recorded in operation logs.
    set +x
    # These values also enter nginx filenames, service files and permission URLs.
    local hostname
    for hostname in "$domain" "$server_name"; do
        [[ "$hostname" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$hostname" == *.* && "$hostname" != *..* ]] \
            || ynh_die "Use a lowercase DNS domain without a scheme, port or path."
        [[ -d "/etc/nginx/conf.d/$hostname.d" ]] \
            || ynh_die "Add the domain $hostname to YunoHost before installing or restoring Continuwuity."
    done
    [[ "${path:-/}" == "/" ]] || ynh_die "Continuwuity needs a dedicated domain at its root path."
    [[ "$admin" =~ ^[a-z0-9._=-]+$ && "$admin" != conduit && "$admin" != conduwuit ]] \
        || ynh_die "Choose an administrator with a supported Matrix username."
    [[ "$auth_type" == password || "$auth_type" == dex ]] || ynh_die "Unsupported authentication mode."
    [[ "$allow_registration" == true || "$allow_registration" == false ]] || ynh_die "Invalid registration setting."
    [[ "$allow_federation" == true || "$allow_federation" == false ]] || ynh_die "Invalid federation setting."
    [[ "$smtp_enabled" == true || "$smtp_enabled" == false ]] || ynh_die "Invalid email setting."
    [[ "$max_upload_size" =~ ^[0-9]+$ && "$max_upload_size" -ge 1 && "$max_upload_size" -le 1024 ]] \
        || ynh_die "Upload size must be between 1 and 1024 MiB."
    [[ "$log_level" =~ ^(error|warn|info|debug)$ ]] || ynh_die "Invalid log level."
    [[ "$turn_ttl" =~ ^[0-9]+$ && "$turn_ttl" -ge 60 && "$turn_ttl" -le 604800 ]] \
        || ynh_die "TURN credential lifetime must be between 60 and 604800 seconds."
    [[ -z "$turn_uris" || -n "$turn_secret" ]] || ynh_die "TURN URIs require a shared TURN secret."
    [[ -z "$rtc_service_url" || "$rtc_service_url" =~ ^https://[^[:space:]\"\\]+$ ]] \
        || ynh_die "The MatrixRTC authorization service must use an HTTPS URL."
    turn_uris_json="$(export turn_uris; python3 - <<'PY'
import json, os, re
uris = [uri.strip() for uri in os.environ['turn_uris'].split(',') if uri.strip()]
if any(not re.fullmatch(r'turns?:[A-Za-z0-9.\-\[\]:]+(?:\?transport=(?:udp|tcp))?', uri) for uri in uris):
    raise SystemExit('TURN URIs must use turn: or turns: and an optional ?transport=udp or tcp.')
print(json.dumps(uris))
PY
    )" || ynh_die "Invalid TURN URI list."
    # Reject conflicting Matrix identities and overrides that would break the
    # package's networking or complete-data backup guarantees.
    (export app server_name install_dir; python3 "$YNH_APP_BASEDIR/conf/validate_settings.py")
    dex_service=""
    if [[ "$auth_type" == dex ]]; then
        [[ "$dex_app" =~ ^dex(__[0-9]+)?$ ]] || ynh_die "Select an installed Dex app."
        dex_service="$dex_app.service"
    fi
}

continuwuity_check_smtp() (
    [[ "$smtp_enabled" == true ]] || exit 0
    set +x
    export app domain mail_pwd
    python3 - <<'PY'
import os, smtplib, ssl
try:
    with smtplib.SMTP(os.environ['domain'], 587, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(os.environ['app'], os.environ['mail_pwd'])
        smtp.noop()
except Exception as error:
    raise SystemExit('YunoHost SMTP validation failed (' + type(error).__name__ + '). Check mail, DNS and the domain certificate before enabling email.') from None
PY
)

continuwuity_write_config() {
    # Some YunoHost helpers re-enable xtrace unconditionally; prevent later
    # secret-bearing operations from being recorded in operation logs.
    set +x
    continuwuity_validate_settings
    continuwuity_check_smtp
    ynh_config_add --jinja --template=config.toml --destination="$install_dir/config.toml"
    if [[ ! -f "$install_dir/overrides.toml" ]]; then
        cp "$YNH_APP_BASEDIR/conf/overrides.toml" "$install_dir/overrides.toml"
    fi
    (
        set +x
        umask 077
        printf '%s\n' "$registration_token" > "$install_dir/registration-token"
        printf '%s' "$turn_secret" > "$install_dir/turn-secret"
        export app domain mail_pwd install_dir
        python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import quote
e = os.environ
uri = f"smtp://{quote(e['app'], safe='')}:{quote(e['mail_pwd'], safe='')}@{e['domain']}:587?tls=required"
Path(e['install_dir'], 'smtp-uri').write_text(uri, encoding='utf-8')
PY
    )
}

continuwuity_delegation() {
    local delegation="/etc/nginx/conf.d/$server_name.d/$app-well-known.conf"
    if [[ "$server_name" != "$domain" ]]; then
        local candidate
        for candidate in "/etc/nginx/conf.d/$server_name.d/"*.conf; do
            [[ -f "$candidate" && "$candidate" != "$delegation" ]] || continue
            if grep -Eq '^[[:space:]]*location[^#]*well-known/matrix' "$candidate"; then
                ynh_die "Matrix discovery is already configured in $candidate. Resolve that conflict before delegating this domain."
            fi
        done
        ynh_config_add --template=delegation.conf --destination="$delegation"
        ynh_permission_url --permission=main --add_url="$server_name/.well-known/matrix"
    elif [[ -f "$delegation" ]]; then
        ynh_safe_rm "$delegation"
        ynh_permission_url --permission=main --remove_url="$server_name/.well-known/matrix"
    fi
}

continuwuity_configure() {
    continuwuity_write_config
    continuwuity_delegation
    ynh_config_add_systemd
    ynh_config_add_nginx
}

continuwuity_permissions() {
    # The daemon can write only its data, not its executable or credentials.
    chown -R "root:$app" "$install_dir"
    chmod 750 "$install_dir" "$install_dir/continuwuity"
    find "$install_dir" -maxdepth 1 -type f ! -name continuwuity -exec chmod 640 {} +
    # Resources create the data directory with app ownership, and archive
    # restore preserves the ownership of its contents. Never recursively chown
    # database/media on routine maintenance (especially FUSE/cloud storage).
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
