#!/usr/bin/env python3
"""Exercise the S3 backup/restore lifecycle in a disposable YunoHost guest.

The workflow supplies an amd64 or arm64 candidate binary and a SeaweedFS
binary. SeaweedFS, its HTTPS reverse proxy, the package, and the app all run in
the same Incus guest. No production S3 account or external service is used.
"""

from __future__ import annotations

from collections import defaultdict, deque
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import ssl
import subprocess
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request


APP = "continuwuity"
DOMAIN = "s3-acceptance.test"
ADMIN = "acceptance"
ADMIN_PASSWORD = "Test-Admin-Password-8742"
APP_PASSWORD = "Matrix-Test-Password-8347"
ACCESS_KEY = "acceptance-key"
SECRET_KEY = "acceptance-secret"
LIVE_BUCKET = "acceptance-live"
SNAPSHOT_BUCKET = "acceptance-snapshots"
LIVE_PREFIX = "continuwuity/media"
S3_ENDPOINT = "https://127.0.0.1:18443"
S3_PORT = 18333
S3_TLS_PORT = 18443
ARTIFACT_TLS_PORT = 18444
MANIFEST_NAME = "continuwuity-s3-media.json"
COMMIT_MARKER = ".continuwuity-snapshot-commit.json"

PACKAGE_DIR = Path(os.environ.get("ACCEPTANCE_PACKAGE", "/opt/continuwuity-package"))
ARTIFACT_DIR = Path(os.environ.get("ACCEPTANCE_ARTIFACTS", "/opt/continuwuity-artifacts"))
FIXTURE_DIR = Path("/opt/continuwuity-s3-acceptance")
ARCH = os.environ.get("ACCEPTANCE_ARCH", "")


def _redact(value: str) -> str:
    for secret in (ADMIN_PASSWORD, APP_PASSWORD, ACCESS_KEY, SECRET_KEY):
        value = value.replace(secret, "[redacted]")
    return value


def _redact_diagnostic(value: str) -> str:
    value = _redact(value)
    value = re.sub(r"(?i)https?://[^\s\"'<>]+", "[redacted URL]", value)
    value = re.sub(
        r"(?i)\b(?:acceptance|live|snapshot|restore):[^\s\"'<>]+",
        "[redacted S3 location]",
        value,
    )
    for path_prefix in (LIVE_BUCKET, SNAPSHOT_BUCKET, LIVE_PREFIX, "continuwuity-generations"):
        value = re.sub(
            re.escape(path_prefix) + r"/[^\s\"'<>]*",
            "[redacted S3 location]",
            value,
            flags=re.IGNORECASE,
        )
    for endpoint in (S3_ENDPOINT, f"127.0.0.1:{S3_PORT}", f"127.0.0.1:{S3_TLS_PORT}"):
        value = value.replace(endpoint, "[redacted endpoint]")
    for identifier in (LIVE_BUCKET, SNAPSHOT_BUCKET, LIVE_PREFIX):
        value = value.replace(identifier, "[redacted S3 identifier]")
    return value


def _process_tree(root_pid: int) -> None:
    """Print process names and kernel wait channels, never command arguments."""
    processes: dict[int, tuple[int, str, str, str]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            pid = int(entry.name)
            stat = (entry / "stat").read_text(encoding="utf-8")
            fields = stat.rsplit(")", 1)[1].split()
            state, parent_pid = fields[0], int(fields[1])
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
            wait_channel = (entry / "wchan").read_text(encoding="utf-8").strip()
        except (OSError, IndexError, ValueError):
            continue
        processes[pid] = (parent_pid, state, comm, wait_channel)

    children: dict[int, list[int]] = defaultdict(list)
    for pid, (parent_pid, _, _, _) in processes.items():
        children[parent_pid].append(pid)
    print("Process tree (PID, state, name, wchan; arguments omitted):", flush=True)
    if root_pid not in processes:
        print(f"  timed command process {root_pid} exited before inspection", flush=True)
        return

    remaining = 64

    def visit(pid: int, depth: int) -> None:
        nonlocal remaining
        if remaining <= 0:
            return
        remaining -= 1
        parent_pid, state, comm, wait_channel = processes[pid]
        indent = "  " * depth
        print(
            f"{indent}pid={pid} ppid={parent_pid} state={state} "
            f"name={comm} wchan={wait_channel or 'unknown'}",
            flush=True,
        )
        for child_pid in sorted(children.get(pid, ())):
            visit(child_pid, depth + 1)

    visit(root_pid, 0)


def _print_log_lines(label: str, lines: list[str]) -> None:
    print(f"{label} (sanitized tail):", flush=True)
    if not lines:
        print("  no log lines available", flush=True)
        return
    for line in lines:
        print(_redact_diagnostic(line.rstrip())[:1200], flush=True)


def _recent_operation_log_tails() -> None:
    root = Path("/var/log/yunohost/categories/operation")
    try:
        logs = sorted(
            root.rglob("*.log"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )[:2]
    except OSError:
        logs = []
    for index, path in enumerate(logs, start=1):
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                lines = list(deque(source, maxlen=80))
        except OSError:
            lines = []
        _print_log_lines(f"Recent YunoHost operation log {index}", lines)
    if not logs:
        print("No recent YunoHost operation log files found.", flush=True)


def _journal_tail(label: str, argv: list[str]) -> None:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        _print_log_lines(label, [])
        return
    lines = (result.stdout + result.stderr).splitlines()[-80:]
    _print_log_lines(label, lines)


def _timeout_diagnostics(label: str, pid: int) -> None:
    print(f"== Timeout diagnostics for {label}; command arguments omitted", flush=True)
    _process_tree(pid)
    _recent_operation_log_tails()
    _journal_tail(
        "Continuwuity service journal",
        ["journalctl", "-u", f"{APP}.service", "--no-pager", "-n", "80", "-o", "short-iso"],
    )
    _journal_tail(
        "Recent system journal",
        ["journalctl", "--since", "20 minutes ago", "--no-pager", "-n", "100", "-o", "short-iso"],
    )


def command(
    label: str,
    argv: list[str],
    *,
    expected_success: bool | None = True,
    timeout_seconds: int = 60,
    env: dict[str, str] | None = None,
    log_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a guest command without echoing credential-bearing arguments."""
    print(f"== {label}", flush=True)
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _timeout_diagnostics(label, process.pid)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate(timeout=5)
        output = "\n".join(part for part in (stdout, stderr) if part)
        if output:
            print(_redact_diagnostic(output.rstrip()), flush=True)
        raise RuntimeError(f"{label} exceeded its {timeout_seconds}-second timeout") from None
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    output = "\n".join(part for part in (stdout, stderr) if part)
    if log_output and output:
        print(_redact_diagnostic(output.rstrip()), flush=True)
    elif not log_output and result.returncode != 0 and stderr:
        print(_redact_diagnostic(stderr.rstrip()), flush=True)
    if expected_success is True and result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit status {result.returncode}")
    if expected_success is False and result.returncode == 0:
        raise RuntimeError(f"{label} unexpectedly succeeded")
    return result


def prepare_package(binary: Path) -> None:
    if not PACKAGE_DIR.is_dir() or not binary.is_file():
        raise RuntimeError("The workflow did not provide the package and candidate binary.")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest_path = PACKAGE_DIR / "manifest.toml"
    manifest = rewrite_candidate_manifest(
        manifest_path.read_text(encoding="utf-8"), ARCH, binary.name, digest
    )
    manifest_path.write_text(manifest, encoding="utf-8")
    candidate = tomllib.loads(manifest)["resources"]["sources"]["main"][ARCH]
    if candidate["url"] != f"https://127.0.0.1:{ARTIFACT_TLS_PORT}/{binary.name}" or candidate["sha256"] != digest:
        raise RuntimeError("The temporary package manifest does not pin the supplied candidate binary.")


def rewrite_candidate_manifest(manifest: str, architecture: str, binary_name: str, digest: str) -> str:
    """Point the temporary package manifest at the supplied guest-local binary."""
    url = f"https://127.0.0.1:{ARTIFACT_TLS_PORT}/{binary_name}"
    for key, value in ((f"{architecture}.url", url), (f"{architecture}.sha256", digest)):
        pattern = re.compile(rf"^(?P<indent>[ \t]*){re.escape(key)}\s*=\s*.*$", re.MULTILINE)
        manifest, count = pattern.subn(
            lambda match: f'{match.group("indent")}{key} = "{value}"',
            manifest,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"Could not set the candidate source field {key}.")
    return manifest


def setup_s3_fixture(binary: Path, weed: Path) -> subprocess.Popen[bytes]:
    FIXTURE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # nginx runs as an unprivileged user, so keep its static document root
    # outside the mode-0700 fixture directory containing TLS keys and data.
    artifact_root = ARTIFACT_DIR / "http"
    artifact_root.mkdir(mode=0o755, exist_ok=True)
    served_binary = artifact_root / binary.name
    shutil.copyfile(binary, served_binary)
    served_binary.chmod(0o755)

    ca_key = FIXTURE_DIR / "test-ca.key"
    ca_cert = FIXTURE_DIR / "test-ca.crt"
    key = FIXTURE_DIR / "server.key"
    csr = FIXTURE_DIR / "server.csr"
    cert = FIXTURE_DIR / "server.crt"
    extensions = FIXTURE_DIR / "server.ext"
    # The S3 client rejects a self-signed leaf, so serve a normal leaf signed
    # by a separate CA and trust only that CA in the guest.
    command(
        "Create a guest-local TLS certificate authority",
        [
            "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
            "-keyout", str(ca_key), "-out", str(ca_cert), "-days", "1",
            "-subj", "/CN=Local S3 Repro CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ],
    )
    command(
        "Create a guest-local TLS server key and request",
        [
            "openssl", "req", "-new", "-nodes", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(csr), "-subj", "/CN=127.0.0.1",
        ],
    )
    extensions.write_text(
        "subjectAltName=IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    command(
        "Sign the guest-local TLS server certificate",
        [
            "openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_cert),
            "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(cert),
            "-days", "1", "-extfile", str(extensions),
        ],
    )
    ca_key.chmod(0o600)
    key.chmod(0o600)
    trusted_cert = Path("/usr/local/share/ca-certificates/continuwuity-s3-acceptance-ca.crt")
    trusted_cert.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ca_cert, trusted_cert)
    command("Trust the guest-local TLS certificate authority", ["update-ca-certificates"])

    nginx_conf = Path("/etc/nginx/conf.d/continuwuity-s3-acceptance.conf")
    nginx_conf.write_text(
        f"""
server {{
    listen 127.0.0.1:{S3_TLS_PORT} ssl;
    server_name 127.0.0.1;
    ssl_certificate {cert};
    ssl_certificate_key {key};
    client_max_body_size 16m;
    location / {{
        proxy_pass http://127.0.0.1:{S3_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_request_buffering off;
    }}
}}
server {{
    listen 127.0.0.1:{ARTIFACT_TLS_PORT} ssl;
    server_name 127.0.0.1;
    ssl_certificate {cert};
    ssl_certificate_key {key};
    root {artifact_root};
    location / {{
        try_files $uri =404;
    }}
}}
""".lstrip(),
        encoding="utf-8",
    )
    command("Validate and reload the loopback TLS proxy", ["nginx", "-t"])
    command("Reload nginx for the loopback TLS proxy", ["systemctl", "reload", "nginx"])

    weed_target = Path("/usr/local/bin/weed")
    shutil.copyfile(weed, weed_target)
    weed_target.chmod(0o755)
    data_dir = FIXTURE_DIR / "seaweed-data"
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = (FIXTURE_DIR / "seaweed.log").open("wb")
    process = subprocess.Popen(
        [
            str(weed_target), "server", "-ip=127.0.0.1", "-ip.bind=127.0.0.1",
            f"-dir={data_dir}", "-master.port=19333", "-master.volumeSizeLimitMB=64",
            "-volume.port=18080", "-volume.max=2", "-volume.preStopSeconds=0",
            "-filer", "-filer.port=18888", "-s3", f"-s3.port={S3_PORT}",
            "-master.telemetry=false",
        ],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    create_bucket(f"https://127.0.0.1:{S3_TLS_PORT}/{LIVE_BUCKET}", process)
    create_bucket(f"https://127.0.0.1:{S3_TLS_PORT}/{SNAPSHOT_BUCKET}", process)
    print("PASS: disposable SeaweedFS S3 endpoint is reachable over verified HTTPS", flush=True)
    return process


def create_bucket(url: str, process: subprocess.Popen[bytes]) -> None:
    context = ssl.create_default_context()
    last_error: Exception | None = None
    for _ in range(45):
        if process.poll() is not None:
            raise RuntimeError("The guest-local SeaweedFS process stopped during startup.")
        request = urllib.request.Request(url, method="PUT")
        try:
            with urllib.request.urlopen(request, context=context, timeout=3) as response:
                if response.status in (200, 201, 204):
                    return
                last_error = RuntimeError(f"Unexpected bucket create status {response.status}")
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return
            last_error = error
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f"Could not create a disposable S3 bucket: {type(last_error).__name__}")


def ensure_yunohost() -> None:
    installed_marker = Path("/etc/yunohost/installed")
    if not installed_marker.exists():
        command(
            "Postinstall the disposable YunoHost guest",
            [
                "yunohost", "tools", "postinstall", "--domain", DOMAIN,
                "--username", ADMIN, "--fullname", "Acceptance User",
                "--password", ADMIN_PASSWORD, "--i-have-read-terms-of-services",
            ],
            timeout_seconds=15 * 60,
        )
        return

    domain = command(
        "Check whether the package test domain exists",
        ["yunohost", "domain", "info", DOMAIN],
        expected_success=None,
    )
    if domain.returncode != 0:
        command("Add the package test domain", ["yunohost", "domain", "add", DOMAIN])
    user = command(
        "Check whether the package test user exists",
        ["yunohost", "user", "info", ADMIN],
        expected_success=None,
    )
    if user.returncode != 0:
        help_result = command(
            "Read the installed YunoHost user creation help",
            ["yunohost", "user", "create", "--help"],
        )
        help_text = help_result.stdout + help_result.stderr
        required_options = ("--fullname", "--domain", "--password")
        if any(option not in help_text for option in required_options):
            raise RuntimeError(
                "The installed YunoHost user creation CLI does not expose the required "
                "--fullname, --domain, and --password options."
            )
        command(
            "Create the package test user",
            [
                "yunohost", "user", "create", ADMIN,
                "--fullname", "Acceptance User", "--domain", DOMAIN,
                "--password", ADMIN_PASSWORD,
            ],
        )


def http_request(port: int, method: str, path: str, *, token: str = "", body: bytes | dict | None = None) -> tuple[int, bytes, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"Host": DOMAIN}
    payload: bytes | None
    if isinstance(body, dict):
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        payload = body
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                result = {}
            return response.status, raw, result
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            result = {}
        return error.code, raw, result


def app_port() -> int:
    import yaml

    settings_file = Path(f"/etc/yunohost/apps/{APP}/settings.yml")
    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    port = int(settings.get("port", 8008))
    if not 1 <= port <= 65535:
        raise RuntimeError("YunoHost did not save a valid Continuwuity port.")
    return port


def login_and_upload(port: int, label: str, payload: bytes) -> tuple[str, str]:
    status, _, result = http_request(
        port,
        "POST",
        "/_matrix/client/v3/login",
        body={
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": ADMIN},
            "password": APP_PASSWORD,
        },
    )
    if status != 200 or not result.get("access_token"):
        raise RuntimeError(f"Matrix administrator login failed before {label}.")
    token = result["access_token"]
    status, _, result = http_request(
        port,
        "POST",
        "/_matrix/media/v3/upload?" + urllib.parse.urlencode({"filename": f"{label}.txt"}),
        token=token,
        body=payload,
    )
    if status != 200 or not result.get("content_uri", "").startswith("mxc://"):
        errcode = result.get("errcode", "") if isinstance(result, dict) else ""
        error = result.get("error", "") if isinstance(result, dict) else ""
        detail = f", {errcode}: {_redact(str(error))[:240]}" if error or errcode else ""
        raise RuntimeError(f"Matrix media upload failed during {label} (HTTP {status}{detail}).")
    return token, result["content_uri"]


def probe_s3_from_service_context() -> None:
    """Separate loopback reachability from CA validation using the app sandbox."""
    base = [
        "systemd-run", "--quiet", "--pipe", "--wait", "--collect",
        f"--property=User={APP}", f"--property=Group={APP}",
        "--property=NoNewPrivileges=true", "--property=PrivateTmp=true",
        "--property=PrivateDevices=true", "--property=ProtectSystem=strict",
        "--property=ProtectHome=read-only",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "--", "/usr/bin/curl", "--silent", "--show-error", "--max-time", "5",
        "--output", "/dev/null", "--write-out", "%{http_code}",
    ]
    url = f"{S3_ENDPOINT}/{LIVE_BUCKET}"

    def run_probe(label: str, *, insecure: bool) -> subprocess.CompletedProcess[str]:
        command_line = [*base]
        if insecure:
            command_line.append("--insecure")
        command_line.append(url)
        result = subprocess.run(command_line, capture_output=True, text=True, check=False, timeout=15)
        print(
            f"{label}: exit={result.returncode}, HTTP={result.stdout.strip() or 'none'}",
            flush=True,
        )
        if result.stderr:
            print(_redact(result.stderr.strip()), flush=True)
        return result

    trusted = run_probe("App systemd context with CA verification", insecure=False)
    unverified = run_probe("App systemd context with TLS verification disabled", insecure=True)
    if trusted.returncode != 0 and unverified.returncode == 0:
        raise RuntimeError("The app systemd context cannot validate the local S3 TLS certificate.")
    if trusted.returncode != 0 and unverified.returncode != 0:
        raise RuntimeError("The app systemd context cannot reach the loopback S3 listener even with TLS verification disabled.")
    print("PASS: app systemd context can reach the loopback S3 endpoint with verified TLS", flush=True)


def report_service_proxy_variable_names() -> None:
    result = command(
        "Read the Continuwuity service PID for environment inspection",
        ["systemctl", "show", "--property=MainPID", "--value", f"{APP}.service"],
    )
    try:
        pid = int(result.stdout.strip()) if result.returncode == 0 else 0
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if pid > 0 else []
    except (OSError, ValueError):
        entries = []
    names = sorted(
        key.decode("utf-8", errors="replace")
        for entry in entries
        if entry and b"=" in entry
        for key, _ in (entry.split(b"=", 1),)
        if b"proxy" in key.lower()
    )
    print(
        "Continuwuity.service proxy environment variable names "
        f"(values omitted): {', '.join(names) if names else 'none'}",
        flush=True,
    )


def report_s3_config() -> None:
    config = tomllib.loads(Path("/var/www/continuwuity/config.toml").read_text(encoding="utf-8"))
    global_config = config.get("global", {})
    media_s3 = global_config.get("media_s3", {})
    print(
        "Candidate config S3 fields: "
        f"backend={global_config.get('media_backend')!r}, "
        f"endpoint={media_s3.get('endpoint')!r}, "
        f"allow_http={media_s3.get('allow_http')!r}, "
        f"access_key_present={bool(media_s3.get('access_key_id'))}, "
        f"secret_key_present={bool(media_s3.get('secret_access_key'))}",
        flush=True,
    )
    if global_config.get("media_backend") != "s3" or media_s3.get("endpoint") != S3_ENDPOINT:
        raise RuntimeError("The installed binary is not configured for the expected S3 endpoint.")


def settings() -> dict:
    import yaml

    return yaml.safe_load(Path(f"/etc/yunohost/apps/{APP}/settings.yml").read_text(encoding="utf-8"))


def rclone_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "RCLONE_CONFIG": "/dev/null",
            "RCLONE_CONFIG_ACCEPTANCE_TYPE": "s3",
            "RCLONE_CONFIG_ACCEPTANCE_PROVIDER": "Other",
            "RCLONE_CONFIG_ACCEPTANCE_ENV_AUTH": "false",
            "RCLONE_CONFIG_ACCEPTANCE_ACCESS_KEY_ID": ACCESS_KEY,
            "RCLONE_CONFIG_ACCEPTANCE_SECRET_ACCESS_KEY": SECRET_KEY,
            "RCLONE_CONFIG_ACCEPTANCE_REGION": "us-east-1",
            "RCLONE_CONFIG_ACCEPTANCE_ENDPOINT": S3_ENDPOINT,
            "RCLONE_CONFIG_ACCEPTANCE_FORCE_PATH_STYLE": "true",
        }
    )
    return environment


def rclone_text(*arguments: str) -> str:
    action = arguments[0]
    result = command(
        f"Read remote snapshot marker with rclone {action}",
        ["rclone", *arguments],
        env=rclone_environment(),
        log_output=False,
    )
    return result.stdout


def backup_archive(existing: set[Path], label: str) -> tuple[Path, str]:
    command(
        f"Create the {label} YunoHost archive",
        ["yunohost", "backup", "create", "--apps", APP],
        timeout_seconds=10 * 60,
    )
    archive_dir = Path("/home/yunohost.backup/archives")
    created = sorted(
        (path for path in archive_dir.iterdir() if path.is_file() and path not in existing and (path.name.endswith(".tar") or path.name.endswith(".tar.gz"))),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if len(created) != 1:
        raise RuntimeError(f"Expected one new {label} archive; found {len(created)}.")
    archive = created[0]
    name = archive.name.removesuffix(".tar.gz").removesuffix(".tar")
    print(f"Archive created: {archive.name}", flush=True)
    return archive, name


def archives() -> set[Path]:
    archive_dir = Path("/home/yunohost.backup/archives")
    archive_dir.mkdir(parents=True, exist_ok=True)
    return {
        path for path in archive_dir.iterdir()
        if path.is_file() and (path.name.endswith(".tar") or path.name.endswith(".tar.gz"))
    }


def snapshot_manifest(archive: Path, data_dir: Path) -> tuple[dict, str]:
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        marker_member = next(
            (member for member in members if PurePosixPath(member.name).name == MANIFEST_NAME),
            None,
        )
        if marker_member is None:
            raise RuntimeError("The YunoHost archive does not contain the S3 snapshot manifest.")
        manifest_file = tar.extractfile(marker_member)
        if manifest_file is None:
            raise RuntimeError("The S3 snapshot manifest in the archive is unreadable.")
        manifest = json.load(manifest_file)
        current_relative = str(data_dir).lstrip("/") + "/CURRENT"
        if not any(member.isfile() and member.name.lstrip("/").endswith(current_relative) for member in members):
            raise RuntimeError("The YunoHost archive does not contain the RocksDB CURRENT marker.")
    if manifest.get("schema_version") != 1 or not manifest.get("inventory"):
        raise RuntimeError("The archived S3 manifest has no verified media inventory.")
    generation = manifest.get("generation", "")
    snapshot = manifest.get("snapshot", {})
    if not generation or snapshot.get("bucket") != SNAPSHOT_BUCKET:
        raise RuntimeError("The archived S3 manifest names an unexpected snapshot location.")
    marker_key = f"ACCEPTANCE:{SNAPSHOT_BUCKET}/{snapshot.get('prefix', '')}/{COMMIT_MARKER}"
    remote_marker = json.loads(rclone_text("cat", marker_key))
    if remote_marker.get("generation") != generation:
        raise RuntimeError("The remote S3 snapshot commit marker does not match the YunoHost archive.")
    print(f"PASS: archive includes RocksDB CURRENT and the committed S3 generation {generation}", flush=True)
    return manifest, generation


def download_media(port: int, token: str, content_uri: str, expected: bytes, label: str) -> None:
    media = content_uri.removeprefix("mxc://")
    path = "/_matrix/client/v1/media/download/" + media
    status, body, _ = http_request(port, "GET", path, token=token)
    if status != 200 or body != expected:
        raise RuntimeError(f"{label} media did not restore byte-for-byte through Matrix.")
    print(f"PASS: {label} media downloads through the restored Matrix server", flush=True)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(b"L\0" + relative.encode() + b"\0" + os.readlink(path).encode() + b"\0")
        elif path.is_file():
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            digest.update(b"F\0" + relative.encode() + b"\0" + file_hash.encode() + b"\0")
    return digest.hexdigest()


def archive_data_digest(archive: Path, data_dir: Path) -> str:
    relative_root = str(data_dir).lstrip("/").rstrip("/") + "/"
    digest = hashlib.sha256()
    found = False
    with tarfile.open(archive, "r:*") as tar:
        for member in sorted(tar.getmembers(), key=lambda item: item.name):
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            name = name.lstrip("/")
            root_offset = name.find(relative_root)
            if root_offset < 0:
                continue
            relative = name[root_offset + len(relative_root):]
            if member.issym():
                digest.update(b"L\0" + relative.encode() + b"\0" + member.linkname.encode() + b"\0")
                found = True
                continue
            if not member.isfile():
                continue
            found = True
            stream = tar.extractfile(member)
            if stream is None:
                raise RuntimeError("The archived RocksDB data contains an unreadable file.")
            file_hash = hashlib.sha256(stream.read()).hexdigest()
            digest.update(b"F\0" + relative.encode() + b"\0" + file_hash.encode() + b"\0")
    if not found:
        raise RuntimeError("The YunoHost archive contains no Continuwuity database files.")
    return digest.hexdigest()


def break_archived_override(archive: Path) -> None:
    temporary = archive.with_suffix(archive.suffix + ".rewriting")
    replaced = 0
    try:
        with archive.open("rb") as stream:
            is_gzip = stream.read(2) == b"\x1f\x8b"
        target_mode = "w:gz" if is_gzip else "w"
        with tarfile.open(archive, "r:*") as source, tarfile.open(temporary, target_mode) as target:
            for member in source.getmembers():
                normalized = member.name.lstrip("/")
                if normalized.endswith("var/www/continuwuity/overrides.toml"):
                    replacement = copy.copy(member)
                    content = b'[global]\nallow_registration = "invalid"\n'
                    replacement.size = len(content)
                    target.addfile(replacement, io.BytesIO(content))
                    replaced += 1
                elif member.isfile():
                    stream = source.extractfile(member)
                    if stream is None:
                        raise RuntimeError("Could not read a file while preparing the forced restore failure.")
                    target.addfile(member, stream)
                else:
                    target.addfile(member)
        if replaced != 1:
            raise RuntimeError(f"Expected one archived overrides.toml file; found {replaced}.")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    print("Prepared an invalidly typed archived override to fail during service startup after the data swap", flush=True)


def install_package() -> None:
    args = {
        "domain": DOMAIN,
        "path": "/",
        "admin": ADMIN,
        "password": APP_PASSWORD,
        "auth_type": "password",
        "server_name": DOMAIN,
        "media_backend": "s3",
        "media_s3_endpoint": S3_ENDPOINT,
        "media_s3_bucket": LIVE_BUCKET,
        "media_s3_backup_bucket": SNAPSHOT_BUCKET,
        "media_s3_region": "us-east-1",
        "media_s3_key_prefix": LIVE_PREFIX,
        "media_s3_access_key_id": ACCESS_KEY,
        "media_s3_secret_access_key": SECRET_KEY,
    }
    command(
        "Install the package with native S3 media storage",
        [
            "yunohost", "app", "install", str(PACKAGE_DIR), "--force",
            "--args", urllib.parse.urlencode(args),
        ],
        timeout_seconds=15 * 60,
    )


def remove_package(label: str) -> None:
    command(
        f"Remove the app for {label}",
        ["yunohost", "app", "remove", APP, "--force"],
        timeout_seconds=10 * 60,
    )


def restore_archive(name: str, *, expected_success: bool) -> subprocess.CompletedProcess[str]:
    return command(
        f"Restore {name}",
        ["yunohost", "backup", "restore", name, "--apps", APP],
        expected_success=expected_success,
        timeout_seconds=10 * 60,
    )


def main() -> None:
    if ARCH not in ("amd64", "arm64"):
        raise RuntimeError("ACCEPTANCE_ARCH must be amd64 or arm64.")
    binary = ARTIFACT_DIR / f"conduwuit-linux-static-{ARCH}"
    weed = ARTIFACT_DIR / "weed"
    if not weed.is_file():
        raise RuntimeError("The workflow did not provide the pinned SeaweedFS binary.")
    prepare_package(binary)
    server = setup_s3_fixture(binary, weed)
    try:
        ensure_yunohost()
        install_package()
        report_s3_config()
        report_service_proxy_variable_names()
        probe_s3_from_service_context()
        port = app_port()

        initial_payload = b"native YunoHost S3 backup acceptance media\n"
        token, initial_uri = login_and_upload(port, "initial-archive-media", initial_payload)
        if not (Path("/home/yunohost.app/continuwuity/CURRENT").is_file()):
            raise RuntimeError("The live YunoHost app did not create its RocksDB database.")
        media_dir = Path("/home/yunohost.app/continuwuity/media")
        if media_dir.exists() and any(media_dir.iterdir()):
            raise RuntimeError("The S3 media upload fell back to local disk storage.")

        original_state = archives()
        archive, archive_name = backup_archive(original_state, "primary")
        data_dir = Path("/home/yunohost.app/continuwuity")
        snapshot_manifest(archive, data_dir)

        remove_package("successful restore")
        restore_archive(archive_name, expected_success=True)
        port = app_port()
        download_media(port, token, initial_uri, initial_payload, "initial archived")
        restored_prefix = settings().get("media_s3_key_prefix", "")
        if not restored_prefix.startswith(".ynh-restore-"):
            raise RuntimeError("Successful restore did not switch the live media setting to its fresh S3 prefix.")
        status, _, whoami = http_request(port, "GET", "/_matrix/client/v3/account/whoami", token=token)
        if status != 200 or whoami.get("user_id") != f"@{ADMIN}:{DOMAIN}":
            raise RuntimeError("The successful YunoHost restore did not recover the Matrix database session.")

        baseline_payload = b"retained pre-failure Matrix media\n"
        token, _ = login_and_upload(port, "retained-before-failure", baseline_payload)
        second_archive, second_name = backup_archive(archives(), "forced-failure baseline")
        snapshot_manifest(second_archive, data_dir)

        newer_payload = b"state written after the forced-failure archive\n"
        token, newer_uri = login_and_upload(port, "post-snapshot-state", newer_payload)
        download_media(port, token, newer_uri, newer_payload, "post-snapshot")
        remove_package("forced restore failure")
        retained_digest = tree_digest(data_dir)
        archived_digest = archive_data_digest(second_archive, data_dir)
        if retained_digest == archived_digest:
            raise RuntimeError("The forced-failure restore would not replace a distinguishable retained database state.")

        break_archived_override(second_archive)
        failed_restore = restore_archive(second_name, expected_success=False)
        failed_output = failed_restore.stdout + failed_restore.stderr
        for checkpoint in (
            "Restoring Continuwuity and its complete data snapshot",
            "Starting Continuwuity",
            "Original data recovered at",
        ):
            if checkpoint not in failed_output:
                raise RuntimeError(f"The forced restore failure did not reach the expected rollback checkpoint: {checkpoint}.")
        if data_dir.is_symlink() or not (data_dir / "CURRENT").is_file() or (data_dir / "CURRENT").is_symlink() or (data_dir / "CURRENT").stat().st_size == 0:
            raise RuntimeError("The failed restore did not leave a safe, readable retained RocksDB directory.")
        service_state = command(
            "Check Continuwuity service state after the forced restore failure",
            ["systemctl", "is-active", "--quiet", f"{APP}.service"],
            expected_success=None,
        )
        if service_state.returncode == 0:
            raise RuntimeError("The failed restore left the service active against the retained data directory.")
        restored_digest = tree_digest(data_dir)
        if restored_digest != retained_digest:
            raise RuntimeError("The forced restore failure did not roll back the original retained database tree.")
        if list(data_dir.parent.glob(f".{APP}-restore-rollback.*")):
            raise RuntimeError("The failed restore left a rollback transaction directory beside the retained data.")
        print(f"PASS: forced restore failure left the exact retained database tree safe at {data_dir}", flush=True)
        print("PASS: real YunoHost install, archive, S3 generation, successful restore, media readback, and retained-data recovery", flush=True)
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
