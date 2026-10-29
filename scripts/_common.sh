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
        # The domain is shared with other apps: refuse anything that already
        # serves Matrix discovery (any location form, alias or static file).
        local conflict
        conflict="$(grep -rlsE --exclude="$(basename -- "$delegation")" \
            '^[^#]*well-known/matrix' "/etc/nginx/conf.d/$server_name.d/" | head -n 1)" || true
        [[ -z "$conflict" ]] \
            || ynh_die "Matrix discovery is already configured in $conflict. Resolve that conflict before delegating this domain."
        # YunoHost serves /.well-known/acme-challenge from /var/www/.well-known.
        [[ ! -e "/var/www/.well-known/matrix" ]] \
            || ynh_die "A static Matrix discovery path exists at /var/www/.well-known/matrix. Resolve that conflict before delegating this domain."
        local previous=""
        if [[ -f "$delegation" ]]; then
            previous="$(mktemp)"
            cp -p -- "$delegation" "$previous"
        fi
        ynh_config_add --template=delegation.conf --destination="$delegation"
        # Never leave a broken vhost fragment for a later nginx reload.
        if ! nginx -t >/dev/null 2>&1; then
            if [[ -n "$previous" ]]; then
                mv -f -- "$previous" "$delegation"
            else
                rm -f -- "$delegation"
            fi
            ynh_die "nginx rejected the Matrix delegation for $server_name; it was not applied."
        fi
        [[ -z "$previous" ]] || rm -f -- "$previous"
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
