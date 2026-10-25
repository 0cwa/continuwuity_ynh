#!/bin/bash

# Optional delegated authentication through the separately installed Dex app.
# Requires YunoHost helpers and the app/domain/install_dir/auth_type/dex_app
# settings. The main service must read both config.toml and oidc.toml.
# Only this package's client fragment is changed; Dex's LDAP connector and
# other clients remain owned by the Dex package and its administrator.

_continuwuity_dex_is_installed() {
    [[ -f "/etc/yunohost/apps/$dex_app/settings.yml" ]]
}

_continuwuity_dex_load() {
    [[ "${dex_app:-}" =~ ^dex(__[0-9]+)?$ ]] \
        || ynh_die "Select an installed YunoHost Dex app for delegated authentication."
    [[ "$app" =~ ^continuwuity(__[0-9]+)?$ ]] \
        || ynh_die "Invalid Continuwuity app identifier."
    _continuwuity_dex_is_installed \
        || ynh_die "The selected Dex app is not installed. Restore/install Dex before this app."

    dex_install_dir=$(ynh_app_setting_get --app="$dex_app" --key=install_dir)
    local dex_domain dex_path
    dex_domain=$(ynh_app_setting_get --app="$dex_app" --key=domain)
    dex_path=$(ynh_app_setting_get --app="$dex_app" --key=path)
    [[ "$dex_install_dir" == /* && "$dex_install_dir" != / \
        && -d "$dex_install_dir/config.yaml.d" \
        && ! -L "$dex_install_dir/config.yaml.d" \
        && -x "$dex_install_dir/regenerate_config.sh" \
        && -n "$dex_domain" && "$dex_path" == /* ]] \
        || ynh_die "Dex is missing or its supported config.yaml.d interface is unavailable. Restore/install Dex first."

    oidc_client_id="$app"
    # Preserve the exact issuer, including a trailing slash for a root install.
    oidc_discovery_url="https://${dex_domain}${dex_path}"
    dex_client_file="$dex_install_dir/config.yaml.d/$app"
    dex_client_marker="# Managed by continuwuity_ynh for $app; do not edit."
    if [[ -e "$dex_client_file" || -L "$dex_client_file" ]]; then
        [[ -f "$dex_client_file" && ! -L "$dex_client_file" \
            && "$(head -n 1 "$dex_client_file")" == "$dex_client_marker" ]] \
            || ynh_die "Dex already has a client fragment named $app that this package does not own. Resolve the name conflict before continuing."
    fi
}

_continuwuity_dex_write_secret() (
    # Do not expose credentials in shell tracing, process arguments, or stdout.
    set +x
    local dex_secret dex_secret_tmp
    dex_secret="${oidc_client_secret:-}"
    if [[ -z "$dex_secret" ]]; then
        dex_secret=$(ynh_app_setting_get --app="$app" --key=oidc_client_secret)
    fi
    if [[ -z "$dex_secret" ]]; then
        dex_secret=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
        ynh_app_setting_set --app="$app" --key=oidc_client_secret --value="$dex_secret"
    fi
    [[ -n "$dex_secret" ]] || ynh_die "Could not obtain the Dex client secret."
    dex_secret_tmp=$(mktemp "$install_dir/.oidc-client-secret.XXXXXXXX")
    printf '%s\n' "$dex_secret" > "$dex_secret_tmp"
    chown root:"$app" "$dex_secret_tmp"
    chmod 640 "$dex_secret_tmp"
    mv -f "$dex_secret_tmp" "$install_dir/oidc-client-secret"
)

_continuwuity_dex_discovery_check() {
    local dex_metadata="$1"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 5 --max-time 10 --retry 3 --retry-delay 1 \
        --retry-connrefused --retry-max-time 30 \
        "${oidc_discovery_url%/}/.well-known/openid-configuration" \
        --output "$dex_metadata" || return 1
    python3 - "$oidc_discovery_url" "$dex_metadata" <<'PY'
import json
import sys
from urllib.parse import urlsplit

with open(sys.argv[2], encoding="utf-8") as source:
    metadata = json.load(source)
if metadata.get("issuer") != sys.argv[1]:
    raise SystemExit("Dex discovery returned an unexpected issuer URL.")
for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
    endpoint = metadata.get(key, "")
    if not isinstance(endpoint, str) or urlsplit(endpoint).scheme != "https":
        raise SystemExit(f"Dex discovery has an invalid {key}.")
PY
}

continuwuity_dex_setup() {
    if [[ "${auth_type:-password}" == password ]]; then
        # An always-present file keeps systemd and offline admin commands simple.
        install --owner=root --group="$app" --mode=640 /dev/null "$install_dir/oidc.toml"
        return
    fi
    [[ "$auth_type" == dex ]] \
        || ynh_die "Unsupported authentication mode: $auth_type"

    _continuwuity_dex_load
    _continuwuity_dex_write_secret
    ynh_app_setting_set --app="$app" --key=oidc_client_id --value="$oidc_client_id"
    ynh_app_setting_set --app="$app" --key=oidc_discovery_url --value="$oidc_discovery_url"

    local dex_workdir dex_had_client=false dex_changed=false
    # Stage next to (not inside) the watched directory, on the same filesystem.
    dex_workdir=$(mktemp -d "$dex_install_dir/.continuwuity-client.XXXXXXXX")
    if [[ -f "$dex_client_file" ]]; then
        dex_had_client=true
        cp -p "$dex_client_file" "$dex_workdir/previous-client"
    fi

    # JSON flow mappings are valid YAML. Reading the secret from a protected
    # file avoids passing it to Python through argv or the environment.
    python3 - "$dex_workdir/client" "$dex_client_marker" "$oidc_client_id" \
        "$domain" "$install_dir/oidc-client-secret" <<'PY'
import json
import sys

with open(sys.argv[5], encoding="utf-8") as source:
    secret = source.read().strip()
client = {
    "id": sys.argv[3],
    "redirectURIs": [f"https://{sys.argv[4]}/_continuwuity/oidc/complete"],
    "name": f"Continuwuity ({sys.argv[3]})",
    "secret": secret,
}
with open(sys.argv[1], "w", encoding="utf-8") as target:
    target.write(sys.argv[2] + "\n- " + json.dumps(client) + "\n")
PY
    chown "$dex_app:$dex_app" "$dex_workdir/client"
    chmod 640 "$dex_workdir/client"
    if ! cmp -s "$dex_workdir/client" "$dex_client_file"; then
        # Move a complete file into the monitored directory so Dex cannot load
        # a half-written client. Its ExecStartPre regenerates the combined YAML.
        mv -f "$dex_workdir/client" "$dex_client_file"
        dex_changed=true
    fi

    if { [[ "$dex_changed" == true ]] && ! systemctl restart "$dex_app.service"; } \
        || ! _continuwuity_dex_discovery_check "$dex_workdir/discovery.json"; then
        if [[ "$dex_changed" == true ]]; then
            if [[ "$dex_had_client" == true ]]; then
                mv -f "$dex_workdir/previous-client" "$dex_client_file"
            else
                rm -f "$dex_client_file"
            fi
            systemctl restart "$dex_app.service" || true
        fi
        rm -rf "$dex_workdir"
        ynh_die "Dex did not pass startup and HTTPS OIDC discovery checks. Its previous client fragment was restored. Check Dex's certificate, DNS, service, and issuer before retrying."
    fi

    local dex_oidc_tmp
    dex_oidc_tmp=$(mktemp "$install_dir/.oidc.XXXXXXXX")
    python3 - "$dex_oidc_tmp" "$oidc_discovery_url" \
        "$oidc_client_id" "$install_dir/oidc-client-secret" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as target:
    target.write("# Managed by continuwuity_ynh. Authentication settings are in YunoHost.\n")
    target.write("[global.oauth.oidc]\n")
    for key, value in (
        ("discovery_url", sys.argv[2]),
        ("client_id", sys.argv[3]),
        ("client_secret_file", sys.argv[4]),
        ("provider_name", "YunoHost"),
    ):
        target.write(f"{key} = {json.dumps(value)}\n")
    target.write('additional_scopes = ["profile", "email"]\n')
    # The stock Dex YunoHost LDAP connector exports displayName as `name`,
    # but does not export preferred_username. Never use a display name as an ID.
    target.write("prompt_for_localpart = true\n")
    target.write('profile_key_map = { displayname = "name" }\n')
    target.write('profile_key_import_mode = "on_registration"\n')
PY
    chown root:"$app" "$dex_oidc_tmp"
    chmod 640 "$dex_oidc_tmp"
    mv -f "$dex_oidc_tmp" "$install_dir/oidc.toml"
    rm -rf "$dex_workdir"
}

continuwuity_dex_remove() {
    [[ "${auth_type:-password}" == dex && "${dex_app:-}" =~ ^dex(__[0-9]+)?$ ]] || return 0
    _continuwuity_dex_is_installed || return 0
    local dex_remove_dir dex_remove_file dex_remove_marker
    dex_remove_dir=$(ynh_app_setting_get --app="$dex_app" --key=install_dir) || return 0
    [[ "$dex_remove_dir" == /* && "$dex_remove_dir" != / \
        && -d "$dex_remove_dir/config.yaml.d" \
        && ! -L "$dex_remove_dir/config.yaml.d" ]] || return 0
    dex_remove_file="$dex_remove_dir/config.yaml.d/$app"
    dex_remove_marker="# Managed by continuwuity_ynh for $app; do not edit."
    if [[ -f "$dex_remove_file" && ! -L "$dex_remove_file" \
        && "$(head -n 1 "$dex_remove_file")" == "$dex_remove_marker" ]]; then
        rm -f "$dex_remove_file"
        # Removal must remain possible if Dex has already been stopped/removed.
        if systemctl is-active --quiet "$dex_app.service"; then
            systemctl restart "$dex_app.service" \
                || ynh_print_warn "Removed Continuwuity's Dex client; Dex needs a manual restart."
        fi
    fi
}
