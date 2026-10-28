#!/bin/bash

# Shared lifecycle entrypoint, sourced before YunoHost's helpers.
# Helper modules define functions only; YunoHost settings are read at call time.
# shellcheck source=_settings.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/_settings.sh"
# shellcheck source=_service.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/_service.sh"
# shellcheck source=_dex.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/_dex.sh"

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
