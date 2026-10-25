#!/bin/bash

# A RocksDB directory must remain unchanged for the whole copy. ynh_backup only
# declares files and copies them *after* scripts/backup exits, so declaring the
# live directory and restarting the service is not a consistent backup.

continuwuity_validate_data_path() {
    # Refuse unsafe settings before a restore can replace retained app data.
    [[ -n "${app:-}" && "$app" =~ ^continuwuity(__[0-9]+)?$ ]] \
        || ynh_die "Refusing to use an unexpected Continuwuity application ID."
    [[ -n "${data_dir:-}" && "$data_dir" == /*/* \
        && "${data_dir##*/}" == "$app" \
        && "$(realpath -m -- "$data_dir")" == "$data_dir" \
        && ! -L "$data_dir" ]] \
        || ynh_die "Refusing to replace an unsafe data directory: ${data_dir:-unset}."
}

# Live media on bind/FUSE/object-store mounts is not supported by this package:
# YunoHost snapshots would otherwise copy remote objects as local files and a
# failed/unmounted mount could silently produce an incomplete archive.
continuwuity_require_local_media() {
    local media="$data_dir/media" mounts target
    [[ ! -L "$media" ]] \
        || ynh_die "Symlinked media is not backup/restore safe. Use local media storage."
    mounts="$(findmnt --kernel --raw --noheadings --output TARGET)" \
        || ynh_die "Cannot inspect media mounts; refusing an unsafe backup/restore."
    while IFS= read -r target; do
        case "$target" in
            "$data_dir"|"$data_dir/"*)
                # Reject mounts at the root or anywhere inside the data tree.
                ynh_die "Mounted media or data directories are not supported for YunoHost backup/restore. Migrate to local storage or use encrypted offsite backups."
                ;;
        esac
    done <<< "$mounts"
}

continuwuity_check_snapshot_space() {
    # Preflight before stopping the database. This is a minimum staging
    # requirement, not a promise about the later compressed YunoHost archive.
    local bytes free reserve usage capacity
    # Parsing a pipeline masks du/df errors if awk succeeds after partial output.
    usage="$(du --summarize --block-size=1 -- "$data_dir")" \
        || ynh_die "Unable to measure database and media; refusing incomplete backup."
    bytes="${usage%%[[:space:]]*}"
    capacity="$(df --portability --block-size=1 -- "$YNH_APP_BACKUP_DIR")" \
        || ynh_die "Unable to check backup staging free space."
    free="$(awk 'END {print $4}' <<< "$capacity")"
    [[ "$bytes" =~ ^[0-9]+$ && "$free" =~ ^[0-9]+$ ]] \
        || ynh_die "Invalid backup staging capacity information."
    reserve=$((bytes / 10 + 67108864)) # 10% headroom plus 64 MiB.
    if (( free < bytes + reserve )); then
        ynh_die "Insufficient free space for a complete stopped-data snapshot: need at least $((bytes + reserve)) bytes in the backup staging filesystem, available $free bytes. The final YunoHost archive needs additional space."
    fi
}

continuwuity_snapshot_data() (
    # This subshell leaves YunoHost's parent EXIT/error traps untouched.
    set -eu
    continuwuity_validate_data_path

    local snapshot="${YNH_APP_BACKUP_DIR:?}${data_dir}"
    local initial_state restart_required=0
    [[ -d "$data_dir" ]] \
        || ynh_die "The Continuwuity data directory is missing; refusing an incomplete backup."
    [[ ! -e "$snapshot" && ! -L "$snapshot" ]] \
        || ynh_die "A data snapshot already exists at $snapshot; refusing to overwrite it."

    continuwuity_require_local_media
    continuwuity_check_snapshot_space

    initial_state="$(systemctl show --property=ActiveState --value "$app.service")"
    case "$initial_state" in
        active|activating|reloading) restart_required=1 ;;
        inactive|failed|deactivating) ;;
        *) ynh_die "Cannot determine the service state; refusing to copy a live database." ;;
    esac

    continuwuity_snapshot_cleanup() {
        local status=$?
        trap - EXIT INT TERM
        # Preserve a copy/stop failure while still attempting to restore service.
        set +e
        if [[ "$restart_required" -eq 1 ]]; then
            if ! ynh_systemctl --service="$app" --action=start; then
                ynh_print_warn "$app could not be restarted after the backup attempt. Check its service logs."
                status=1
            fi
        fi
        exit "$status"
    }
    trap continuwuity_snapshot_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # Stop even an activating/failed unit: the helper intentionally skips units
    # that aren't active, which is too weak a guarantee for a database snapshot.
    systemctl stop "$app.service"
    mkdir -p -- "$(dirname -- "$snapshot")"
    cp --archive --reflink=auto -- "$data_dir" "$snapshot"
    # The archive uses the canonical data path, so ynh_restore can restore it.
    # The backup core automatically includes files created in its workspace.
)
