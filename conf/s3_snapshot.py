#!/usr/bin/env python3
"""Create and restore verified generations of S3 media.

Environment contract (secrets are read only from the environment):
``media_s3_endpoint``, ``media_s3_region``, ``media_s3_bucket``,
non-empty ``media_s3_key_prefix``,
``media_s3_access_key_id``, ``media_s3_secret_access_key``, and the distinct
``media_s3_backup_bucket``. Restore also requires ``media_s3_restore_bucket``
and a non-empty, disjoint ``media_s3_restore_key_prefix``.

The live service should be stopped or otherwise quiesced by the caller during
``create``: verification hashes object contents with SHA-256, but cannot make a
changing live source transactionally atomic. Static credentials are required
because YunoHost runs the service as the app user and lifecycle hooks as root;
the two users' implicit AWS credential chains cannot be assumed to match.
SHA-256 verification downloads each nonempty source/snapshot/restore inventory
once per verification pass. A create therefore reads live and copied media
once each; verify reads snapshot media once; restore reads snapshot and restored
media once each (in addition to a read-only verify preflight if the caller ran
one). This trades bandwidth and provider egress for content-level proof. Hash
streams use four bounded transfers and digest lines are consumed one at a time;
stderr is sent to DEVNULL so it cannot block the stdout reader. Only inventory
metadata is retained in memory, not media payloads. Copy requests server-side
copying across the two equivalent rclone configurations; it checks rclone's
per-object result lines and refuses to publish a manifest if any object fell
back to client-side transfer.
The local manifest is the commit marker and belongs in the same protected
backup archive. Failed copies leave partial generation prefixes as safe
orphans; the helper never deletes them. Rclone transfers/checkers are bounded
to 4/8. Restore uses ``--ignore-existing`` as a second guard against a target
race, then verifies the complete target inventory; a race can leave a partial
fresh target, which is reported and must be discarded manually before retry.

The YunoHost backup hook creates generations, and the restore hook verifies and
materializes a selected generation through this helper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class SnapshotError(Exception):
    """A validation or remote-operation failure safe to show to an operator."""


GENERATION_ROOT = "continuwuity-generations"
COMMIT_MARKER = ".continuwuity-snapshot-commit.json"
MAX_COMMIT_MARKER_BYTES = 4096
MAX_TRANSFERS = "4"
MAX_CHECKERS = "8"


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SnapshotError(f"required environment setting is missing: {name}")
    return value


def _bucket(value: str, name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,62}", value):
        raise SnapshotError(f"invalid bucket name in {name}")
    return value


def _prefix(value: str, name: str, allow_empty: bool = True) -> str:
    if value == "" and allow_empty:
        return value
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        raise SnapshotError(f"invalid object prefix in {name}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SnapshotError(f"invalid object prefix in {name}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SnapshotError(f"invalid object prefix in {name}")
    return value


def _endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise SnapshotError("media_s3_endpoint must be an http(s) URL without credentials, query, or fragment")
    return value.rstrip("/")


def _config(env: dict[str, str], *, restore: bool = False) -> tuple[dict[str, str], str, str, str, str, str, str]:
    endpoint_value = env.get("media_s3_endpoint", "")
    endpoint = _endpoint(endpoint_value) if endpoint_value else ""
    region = _required(env, "media_s3_region")
    access_key = _required(env, "media_s3_access_key_id")
    secret_key = _required(env, "media_s3_secret_access_key")
    if not access_key or not secret_key:
        raise SnapshotError("static S3 access key and secret are required for lifecycle snapshots")
    source_bucket = _bucket(_required(env, "media_s3_bucket"), "media_s3_bucket")
    source_prefix = _prefix(env.get("media_s3_key_prefix", ""), "media_s3_key_prefix", allow_empty=False)
    snapshot_bucket = _bucket(_required(env, "media_s3_backup_bucket"), "media_s3_backup_bucket")
    if source_bucket.lower() == snapshot_bucket.lower():
        raise SnapshotError("live and snapshot buckets must be different buckets")

    # Keep unrelated process secrets and personal rclone/AWS config out of the
    # child. Only runtime plumbing is inherited; credentials are injected below.
    rclone_env = {"PATH": env.get("PATH", os.defpath), "RCLONE_CONFIG": "/dev/null"}
    for name in ("TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name in env:
            rclone_env[name] = env[name]
    for remote in ("LIVE", "SNAPSHOT", "RESTORE"):
        rclone_env[f"RCLONE_CONFIG_{remote}_TYPE"] = "s3"
        rclone_env[f"RCLONE_CONFIG_{remote}_PROVIDER"] = "Other" if endpoint else "AWS"
        rclone_env[f"RCLONE_CONFIG_{remote}_ENV_AUTH"] = "false"
        rclone_env[f"RCLONE_CONFIG_{remote}_ACCESS_KEY_ID"] = access_key
        rclone_env[f"RCLONE_CONFIG_{remote}_SECRET_ACCESS_KEY"] = secret_key
        rclone_env[f"RCLONE_CONFIG_{remote}_REGION"] = region
        if endpoint:
            rclone_env[f"RCLONE_CONFIG_{remote}_ENDPOINT"] = endpoint
    target_bucket = ""
    target_prefix = ""
    if restore:
        target_bucket = _bucket(_required(env, "media_s3_restore_bucket"), "media_s3_restore_bucket")
        target_prefix = _prefix(_required(env, "media_s3_restore_key_prefix"), "media_s3_restore_key_prefix", allow_empty=False)
        if target_bucket.lower() == snapshot_bucket.lower():
            raise SnapshotError("restore destination must not be the snapshot bucket")
        if target_bucket.lower() == source_bucket.lower() and _overlaps(source_prefix, target_prefix):
            raise SnapshotError("restore destination overlaps the live media prefix")
    return rclone_env, endpoint, source_bucket, source_prefix, snapshot_bucket, target_bucket, target_prefix


def _overlaps(left: str, right: str) -> bool:
    # An empty source prefix denotes the entire bucket, so no same-bucket
    # restore prefix can be disjoint from it.
    if not left or not right:
        return True
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _remote(remote: str, bucket: str, prefix: str) -> str:
    return f"{remote}:{bucket}" + (f"/{prefix}" if prefix else "")


def _run_rclone(env: dict[str, str], *args: str) -> str:
    try:
        result = subprocess.run(
            ["rclone", *args], env=env, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError:
        raise SnapshotError("rclone is unavailable") from None
    if result.returncode:
        # Do not relay rclone diagnostics: some third-party backends include
        # request details, and the operator-facing error must never leak secrets.
        raise SnapshotError(f"rclone operation failed (exit {result.returncode})")
    return result.stdout


def _inventory(env: dict[str, str], remote: str) -> list[dict[str, object]]:
    output = _run_rclone(env, "lsjson", "--recursive", "--files-only", remote)
    try:
        entries = json.loads(output)
    except json.JSONDecodeError:
        raise SnapshotError("rclone returned an invalid object inventory") from None
    if not isinstance(entries, list):
        raise SnapshotError("rclone returned an invalid object inventory")
    inventory: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("Path"), str) or not isinstance(entry.get("Size"), int):
            raise SnapshotError("rclone returned an invalid object inventory")
        path = entry["Path"]
        _prefix(path, "remote inventory", allow_empty=False)
        if path in seen or entry["Size"] < 0:
            raise SnapshotError("rclone returned an invalid or duplicate object inventory")
        seen.add(path)
        inventory.append({"path": path, "size": entry["Size"]})
    inventory.sort(key=lambda item: str(item["path"]))
    return inventory


def _content_inventory(
    env: dict[str, str], remote: str, inventory: list[dict[str, object]], *, exclude_marker: bool = False
) -> list[dict[str, object]]:
    """Return SHA-256 content fingerprints, downloading where providers lack them."""
    if not inventory:
        return []
    args = ["hashsum", "SHA256", "--download", "--transfers", MAX_TRANSFERS,
            "--checkers", MAX_CHECKERS]
    if exclude_marker:
        args.extend(("--exclude", f"/{COMMIT_MARKER}"))
    args.append(remote)
    command = ["rclone", *args]
    try:
        process = subprocess.Popen(
            command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
    except OSError:
        raise SnapshotError("rclone is unavailable") from None
    assert process.stdout is not None
    digests: dict[str, str] = {}
    invalid_output = False
    try:
        for line in process.stdout:
            line = line.rstrip("\n")
            try:
                digest, path = line.split("  ", 1)
            except ValueError:
                invalid_output = True
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or path in digests:
                invalid_output = True
                continue
            digests[path] = digest
    except OSError:
        process.kill()
        process.wait()
        raise SnapshotError("could not read the rclone SHA-256 inventory") from None
    finally:
        process.stdout.close()
    if process.wait() != 0:
        raise SnapshotError("rclone SHA-256 operation failed")
    if invalid_output:
        raise SnapshotError("rclone returned an invalid SHA-256 inventory")
    paths = {str(item["path"]) for item in inventory}
    if set(digests) != paths:
        raise SnapshotError("rclone SHA-256 inventory is incomplete")
    return [{**item, "sha256": digests[str(item["path"])]} for item in inventory]


def _copy(
    env: dict[str, str], source: str, destination: str, object_count: int, *, exclude_marker: bool = False
) -> None:
    args = ["copy", source, destination, "--transfers", MAX_TRANSFERS,
            "--checkers", MAX_CHECKERS, "--ignore-existing", "--create-empty-src-dirs=false",
            "--server-side-across-configs", "--log-level", "INFO", "--stats", "0"]
    if exclude_marker:
        args.extend(("--exclude", f"/{COMMIT_MARKER}"))
    command = ["rclone", *args]
    try:
        process = subprocess.Popen(
            command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
    except OSError:
        raise SnapshotError("rclone is unavailable") from None
    assert process.stderr is not None
    server_copy_count = 0
    try:
        for line in process.stderr:
            if "Copied (server-side copy)" in line:
                server_copy_count += 1
    except OSError:
        process.kill()
        process.wait()
        raise SnapshotError("could not read the rclone copy result") from None
    finally:
        process.stderr.close()
    if process.wait() != 0:
        raise SnapshotError("rclone copy operation failed")
    if server_copy_count != object_count:
        raise SnapshotError("rclone did not server-side-copy every object")


def _assert_same(actual: list[dict[str, object]], expected: list[dict[str, object]], what: str) -> None:
    if actual != expected:
        raise SnapshotError(f"{what} inventory does not match the verified snapshot")


def _marker_bytes(generation: str, inventory: list[dict[str, object]]) -> str:
    canonical_inventory = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    marker = {
        "schema_version": 1,
        "generation": generation,
        "inventory_sha256": hashlib.sha256(canonical_inventory).hexdigest(),
    }
    return json.dumps(marker, sort_keys=True, separators=(",", ":"))


def _publish_remote_marker(env: dict[str, str], remote_path: str, contents: str) -> None:
    # rcat overwrites existing objects. Upload through copyto with
    # --ignore-existing so a raced or colliding marker is preserved and then
    # rejected by the read-back check instead of overwritten.
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="continuwuity-s3-marker.") as file:
            temporary = file.name
            os.fchmod(file.fileno(), 0o600)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        _run_rclone(env, "copyto", temporary, remote_path, "--ignore-existing", "--log-level", "ERROR")
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_remote_marker(env: dict[str, str], remote_path: str, expected_size: int) -> str:
    if expected_size > MAX_COMMIT_MARKER_BYTES:
        raise SnapshotError("snapshot commit marker exceeds the size limit")
    try:
        process = subprocess.Popen(
            ["rclone", "cat", remote_path], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise SnapshotError("snapshot commit marker is unreadable") from None
    assert process.stdout is not None
    payload = bytearray()
    oversized = False
    try:
        while len(payload) <= expected_size:
            chunk = process.stdout.read(min(1024, expected_size + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > expected_size:
                oversized = True
                process.kill()
                break
    except OSError:
        process.kill()
        process.wait()
        raise SnapshotError("snapshot commit marker is unreadable") from None
    finally:
        process.stdout.close()
    returncode = process.wait()
    if oversized:
        raise SnapshotError("snapshot commit marker exceeds the expected size")
    if returncode != 0 or len(payload) != expected_size:
        raise SnapshotError("snapshot commit marker is missing or unreadable")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        raise SnapshotError("snapshot commit marker is invalid") from None


def _verify_snapshot_remote(env: dict[str, str], remote: str, generation: str, expected: list[dict[str, object]]) -> None:
    actual = _inventory(env, remote)
    marker_path = f"{remote}/{COMMIT_MARKER}"
    marker_entry = next((item for item in actual if item["path"] == COMMIT_MARKER), None)
    if marker_entry is None:
        raise SnapshotError("snapshot commit marker is missing")
    expected_marker = _marker_bytes(generation, expected)
    expected_marker_size = len(expected_marker.encode("utf-8"))
    if marker_entry["size"] != expected_marker_size:
        raise SnapshotError("snapshot commit marker size does not match the manifest")
    data_entries = [item for item in actual if item["path"] != COMMIT_MARKER]
    actual_content = _content_inventory(env, remote, data_entries, exclude_marker=True)
    _assert_same(actual_content, expected, "snapshot")
    marker_text = _read_remote_marker(env, marker_path, expected_marker_size)
    if marker_text != expected_marker:
        raise SnapshotError("snapshot commit marker does not match the manifest")


def _publish_manifest(path: Path, data: dict[str, object]) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise SnapshotError("manifest directory does not exist")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent, prefix=".s3-snapshot.", delete=False) as file:
            temporary = file.name
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file, sort_keys=True, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        # Hard-link creation is atomic and fails if an old manifest exists.
        os.link(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        raise SnapshotError("manifest already exists; refusing to overwrite it") from None
    except OSError:
        raise SnapshotError("could not publish the verified manifest") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def create(manifest: Path, env: dict[str, str]) -> str:
    rclone_env, endpoint, source_bucket, source_prefix, snapshot_bucket, _, _ = _config(env)
    source = _remote("LIVE", source_bucket, source_prefix)
    source_inventory = _inventory(rclone_env, source)
    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    snapshot_prefix = f"{GENERATION_ROOT}/{generation}"
    snapshot = _remote("SNAPSHOT", snapshot_bucket, snapshot_prefix)
    if _inventory(rclone_env, snapshot):
        raise SnapshotError("new snapshot generation prefix is not empty")
    if any(item["path"] == COMMIT_MARKER for item in source_inventory):
        raise SnapshotError("live media contains a reserved snapshot marker path")
    source_content = _content_inventory(rclone_env, source, source_inventory)
    if source_content:
        _copy(rclone_env, source, snapshot, len(source_content))
    copied_inventory = _content_inventory(rclone_env, snapshot, _inventory(rclone_env, snapshot))
    _assert_same(copied_inventory, source_content, "snapshot")
    marker_path = f"{snapshot}/{COMMIT_MARKER}"
    _publish_remote_marker(rclone_env, marker_path, _marker_bytes(generation, source_content))
    _verify_snapshot_remote(rclone_env, snapshot, generation, source_content)
    payload = {
        "schema_version": 1,
        "generation": generation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "source": {"bucket": source_bucket, "prefix": source_prefix},
        "snapshot": {"bucket": snapshot_bucket, "prefix": snapshot_prefix},
        "inventory": source_content,
    }
    _publish_manifest(manifest, payload)
    return generation


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        raise SnapshotError("manifest is missing or invalid") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SnapshotError("manifest version is unsupported")
    if not isinstance(data.get("generation"), str) or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{32}", data["generation"]):
        raise SnapshotError("manifest generation is malformed")
    source = data.get("source")
    snapshot = data.get("snapshot")
    inventory = data.get("inventory")
    if not isinstance(source, dict) or not isinstance(snapshot, dict) or not isinstance(inventory, list):
        raise SnapshotError("manifest is malformed")
    if not all(isinstance(source.get(key), str) for key in ("bucket", "prefix")) or not all(isinstance(snapshot.get(key), str) for key in ("bucket", "prefix")):
        raise SnapshotError("manifest is malformed")
    _bucket(source["bucket"], "manifest source bucket")
    _bucket(snapshot["bucket"], "manifest snapshot bucket")
    if snapshot.get("prefix") != f"{GENERATION_ROOT}/{data['generation']}":
        raise SnapshotError("manifest snapshot path is malformed")
    # Validate untrusted paths and entry structure before making remote calls.
    _prefix(source["prefix"], "manifest source prefix")
    _prefix(snapshot["prefix"], "manifest snapshot prefix", allow_empty=False)
    checked: list[dict[str, object]] = []
    for item in inventory:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("size"), int):
            raise SnapshotError("manifest inventory is malformed")
        _prefix(item["path"], "manifest object path", allow_empty=False)
        if item["size"] < 0 or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise SnapshotError("manifest inventory is malformed")
        checked.append(item)
    if checked != sorted(checked, key=lambda item: str(item["path"])) or len({item["path"] for item in checked}) != len(checked):
        raise SnapshotError("manifest inventory is malformed")
    return data


def _verified_snapshot(manifest: Path, env: dict[str, str]) -> tuple[dict[str, str], dict[str, object]]:
    rclone_env, endpoint, source_bucket, source_prefix, snapshot_bucket, _, _ = _config(env)
    data = _load_manifest(manifest)
    source = data["source"]
    snapshot_info = data["snapshot"]
    if data.get("endpoint") != endpoint or source.get("bucket") != source_bucket or source.get("prefix") != source_prefix or snapshot_info.get("bucket") != snapshot_bucket:
        raise SnapshotError("manifest does not match the configured S3 source and snapshot bucket")
    snapshot = _remote("SNAPSHOT", snapshot_bucket, snapshot_info["prefix"])
    expected = data["inventory"]
    _verify_snapshot_remote(rclone_env, snapshot, data["generation"], expected)
    return rclone_env, data


def verify(manifest: Path, env: dict[str, str]) -> str:
    _, data = _verified_snapshot(manifest, env)
    return str(data["generation"])


def restore(manifest: Path, env: dict[str, str]) -> None:
    _, _, _, _, _, target_bucket, target_prefix = _config(env, restore=True)
    rclone_env, data = _verified_snapshot(manifest, env)
    snapshot_info = data["snapshot"]
    expected = data["inventory"]
    snapshot = _remote("SNAPSHOT", str(snapshot_info["bucket"]), str(snapshot_info["prefix"]))
    destination = _remote("RESTORE", target_bucket, target_prefix)
    if _inventory(rclone_env, destination):
        raise SnapshotError("restore destination is not empty; refusing to overwrite it")
    if expected:
        _copy(rclone_env, snapshot, destination, len(expected), exclude_marker=True)
    restored_inventory = _content_inventory(rclone_env, destination, _inventory(rclone_env, destination))
    _assert_same(restored_inventory, expected, "restore destination")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("create", "verify", "restore"):
        sub = subparsers.add_parser(operation)
        sub.add_argument("--manifest", required=True, type=Path, help="local JSON manifest path (never overwritten)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "create":
            generation = create(args.manifest, dict(os.environ))
            print(f"S3 snapshot verified: {generation}")
        elif args.operation == "verify":
            generation = verify(args.manifest, dict(os.environ))
            print(f"S3 snapshot verified: {generation}")
        else:
            restore(args.manifest, dict(os.environ))
            print("S3 snapshot restored to the verified empty prefix")
    except SnapshotError as error:
        print(f"S3 snapshot error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
