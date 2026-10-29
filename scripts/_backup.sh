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

continuwuity_validate_rocksdb_marker() {
    local directory="$1" error_message="$2"
    [[ -f "$directory/CURRENT" && -s "$directory/CURRENT" && ! -L "$directory/CURRENT" ]] \
        || ynh_die "$error_message"
}

# Live media on bind/FUSE/object-store mounts is not supported by this package:
# YunoHost snapshots would otherwise copy remote objects as local files and a
# failed/unmounted mount could silently produce an incomplete archive.
continuwuity_validate_media_tree() {
    local media="$1" scope="$2" media_symlink
    if [[ "$scope" == archived ]]; then
        [[ ! -L "$media" ]] \
            || ynh_die "The archived media directory is a symlink. Refusing an incomplete or unsafe restoration."
    else
        [[ ! -L "$media" ]] \
            || ynh_die "Symlinked media is not backup/restore safe. Use local media storage."
    fi
    if [[ -d "$media" ]]; then
        # A nested symlink can point outside the data tree, bypassing the mount
        # check and producing an archive that omits live or restored media.
        media_symlink="$(find "$media" -type l -print -quit)" \
            || ynh_die "Cannot inspect $scope media symlinks; refusing an unsafe backup/restore."
        if [[ -n "$media_symlink" ]]; then
            if [[ "$scope" == archived ]]; then
                ynh_die "The archived media tree contains a symlink. Refusing an incomplete or unsafe restoration."
            fi
            ynh_die "Symlinked media is not backup/restore safe. Use local media storage."
        fi
    fi
}

continuwuity_require_local_media() {
    local mounts target
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
    # Check mounts first so a remote media tree is rejected without traversing
    # its contents.
    continuwuity_validate_media_tree "$data_dir/media" live
}

continuwuity_check_snapshot_space() {
    # Preflight before stopping the database. This is a minimum staging
    # requirement, not a promise about the later compressed YunoHost archive.
    _continuwuity_check_space "$data_dir" "$YNH_APP_BACKUP_DIR" \
        "a complete stopped-data snapshot" \
        "backup staging filesystem. The final YunoHost archive needs additional space"
}

continuwuity_check_restore_space() {
    # The original data is only renamed (not freed) until the restore succeeds.
    _continuwuity_check_space "$1" "$(dirname -- "$data_dir")" \
        "keeping the original data while restoring" \
        "data filesystem"
}

_continuwuity_check_space() {
    local bytes free reserve usage capacity
    # Parsing a pipeline masks du/df errors if awk succeeds after partial output.
    usage="$(du --summarize --block-size=1 -- "$1")" \
        || ynh_die "Unable to measure database and media; refusing incomplete operation."
    bytes="${usage%%[[:space:]]*}"
    capacity="$(df --portability --block-size=1 -- "$2")" \
        || ynh_die "Unable to check free space."
    free="$(awk 'END {print $4}' <<< "$capacity")"
    [[ "$bytes" =~ ^[0-9]+$ && "$free" =~ ^[0-9]+$ ]] \
        || ynh_die "Invalid capacity information."
    reserve=$((bytes / 10 + 67108864)) # 10% headroom plus 64 MiB.
    if (( free < bytes + reserve )); then
        ynh_die "Insufficient free space for $3: need at least $((bytes + reserve)) bytes in the $4, available $free bytes."
    fi
}

continuwuity_snapshot_data() (
    # This subshell leaves YunoHost's parent EXIT/error traps untouched.
    set -eu
    continuwuity_validate_data_path

    local snapshot="${YNH_APP_BACKUP_DIR:?}${data_dir}"
    local snapshot_parent staging_dir="" staged_data
    local initial_state restart_required=0
    [[ -d "$data_dir" ]] \
        || ynh_die "The Continuwuity data directory is missing; refusing an incomplete backup."
    continuwuity_validate_rocksdb_marker "$data_dir" \
        "The Continuwuity data directory lacks a valid RocksDB CURRENT marker; refusing an incomplete backup."
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
        if [[ -n "$staging_dir" && -d "$staging_dir" ]]; then
            if ! rm -rf -- "$staging_dir"; then
                ynh_print_warn "Could not remove the incomplete data snapshot staging directory at $staging_dir."
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
    snapshot_parent="$(dirname -- "$snapshot")"
    mkdir -p -- "$snapshot_parent"
    staging_dir="$(mktemp -d --tmpdir="$snapshot_parent" ".${app}.snapshot.XXXXXX")"
    staged_data="$staging_dir/$app"
    cp --archive --reflink=auto -- "$data_dir" "$staging_dir"
    [[ -d "$staged_data" && ! -L "$staged_data" ]] \
        || ynh_die "The Continuwuity data snapshot copy is incomplete."
    continuwuity_validate_rocksdb_marker "$staged_data" \
        "The copied data snapshot lacks a valid RocksDB CURRENT marker; refusing an incomplete backup."
    # Publish the complete directory with one same-filesystem rename. The
    # archive core consumes only the canonical path after this script exits.
    # Whether mv -n fails when it skips varies across coreutils versions; the
    # check below decides either way.
    mv --no-clobber --no-target-directory -- "$staged_data" "$snapshot" || true
    [[ ! -e "$staged_data" && -d "$snapshot" && ! -L "$snapshot" ]] \
        || ynh_die "A data snapshot appeared during copying; refusing to overwrite $snapshot."
    # The backup core automatically includes files created in its workspace.
)
