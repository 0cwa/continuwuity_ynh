#!/usr/bin/env python3
"""Run the pinned upstream binary with the package templates, without YunoHost.

This tests real RocksDB, Matrix APIs, offline administrator bootstrap, token
registration, and restoration of a stopped database/media copy. It does not
claim to replace package_check on a YunoHost VM or a real Dex browser login.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from test_package import PACKAGE, render_config


class SmtpHandler(socketserver.StreamRequestHandler):
    """Minimal local fixture for upstream's eager SMTP connection check."""

    def handle(self):
        self.wfile.write(b"220 localhost ESMTP smoke fixture\r\n")
        while line := self.rfile.readline():
            command = line.split(maxsplit=1)[0].upper()
            if command in (b"EHLO", b"HELO"):
                self.wfile.write(b"250-localhost\r\n250 OK\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            else:
                self.wfile.write(b"250 OK\r\n")


class SmtpFixture(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), SmtpHandler)

    def __enter__(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self.server_address[1]

    def __exit__(self, *arguments):
        self.shutdown()
        self.server_close()
        self.thread.join()


class Homeserver:
    def __init__(self, binary, config, directory, port):
        self.binary = binary
        self.config = config
        self.directory = directory
        self.port = port
        self.process = None
        self.log = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, data=None, token=None, raw=False):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "text/plain"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}" + path, data=data, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, body if raw else json.loads(body)

    def start(self):
        self.log = (self.directory / "server.log").open("ab")
        self.process = subprocess.Popen(
            [str(self.binary), "--config", str(self.config),
             "--config", str(self.directory / "overrides.toml"),
             "--config", str(self.directory / "oidc.toml"), "--worker-threads", "2",
             "-O", "allow_announcements_check=false"],
            stdout=self.log, stderr=self.log, stdin=subprocess.DEVNULL,
        )
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError("Upstream exited before its API was ready; inspect the local smoke log.")
            try:
                status, result = self.request("GET", "/_matrix/client/versions")
                if status == 200 and "versions" in result:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("The local Matrix API did not become ready within 15 seconds.")

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                raise RuntimeError("Upstream did not shut down cleanly; no restore test is safe.")
            if self.process.returncode != 0:
                raise RuntimeError(f"Upstream exited with status {self.process.returncode}.")
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None


def check(condition, explanation):
    if not condition:
        raise AssertionError(explanation)


def select_binary(arguments, temporary):
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise RuntimeError("The upstream smoke test supports amd64 and arm64 hosts.")
    manifest = tomllib.loads((PACKAGE / "manifest.toml").read_text())
    source = manifest["resources"]["sources"]["main"][architecture]
    binary = arguments.binary.resolve() if arguments.binary else temporary / "continuwuity"
    if arguments.download:
        with urllib.request.urlopen(source["url"], timeout=60) as response, binary.open("wb") as destination:
            shutil.copyfileobj(response, destination)
    with binary.open("rb") as source_file:
        actual_sha = hashlib.file_digest(source_file, "sha256").hexdigest()
    check(actual_sha == source["sha256"], "The binary does not match the manifest's SHA256.")
    binary.chmod(0o700)
    return binary


def run_smoke(binary, temporary, smtp_port):
    install = temporary / "install"
    install.mkdir()
    data = temporary / "data"
    restored = temporary / "restored"
    config = install / "config.toml"
    bootstrap = install / "bootstrap.toml"
    password = '-quote\'"dollar$back`tick;$(unused)back\\slash日本語'
    registration_token = "smoke-test-registration-token-123456"
    (install / "registration-token").write_text(registration_token + "\n")
    (install / "turn-secret").write_text("smoke-test-turn-secret")
    (install / "smtp-uri").write_text(f"smtp://127.0.0.1:{smtp_port}")
    shutil.copyfile(PACKAGE / "conf/overrides.toml", install / "overrides.toml")
    (install / "oidc.toml").write_text("# Local password mode\n")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    settings = dict(
        install_dir=str(install), data_dir=str(data), port=port,
        domain="matrix.example.test", server_name="example.test", admin="alice",
        log_level="warn", smtp_enabled="true",
        turn_uris_json=json.dumps(["turn:turn.example.test:3478?transport=udp"]),
        rtc_service_url="https://call.example.test",
    )
    config.write_text(render_config(**settings))
    environment = dict(os.environ)
    environment.update(CONTINUWUITY_BOOTSTRAP_USER="alice", CONTINUWUITY_BOOTSTRAP_PASSWORD=password)
    subprocess.run([sys.executable, str(PACKAGE / "conf/bootstrap.py"), str(bootstrap)],
                   env=environment, check=True)
    with (install / "bootstrap.log").open("wb") as log:
        result = subprocess.run(
            [str(binary), "--config", str(config), "--config", str(bootstrap),
             "--maintenance", "--worker-threads", "2", "-O", "allow_announcements_check=false"],
            stdout=log, stderr=log, timeout=30,
        )
    check(result.returncode == 0, "Offline administrator bootstrap failed; inspect the private smoke log.")
    bootstrap.unlink()

    server = Homeserver(binary, config, install, port)
    try:
        server.start()
        status, discovery = server.request("GET", "/.well-known/matrix/client")
        check(status == 200 and discovery["m.homeserver"]["base_url"].rstrip("/") == "https://matrix.example.test",
              "Client discovery lost the delegated API domain.")
        status, discovery = server.request("GET", "/.well-known/matrix/server")
        check(status == 200 and discovery["m.server"] == "matrix.example.test:443", "Federation discovery is incorrect.")
        status, login = server.request("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password", "identifier": {"type": "m.id.user", "user": "alice"},
            "password": password,
        })
        check(status == 200 and login.get("user_id") == "@alice:example.test", "The administrator cannot log in with the exact supplied password.")
        token = login["access_token"]
        status, result = server.request("GET", "/_matrix/client/v3/account/whoami", token=token)
        check(status == 200 and result["user_id"] == "@alice:example.test", "Authenticated API request failed.")
        status, result = server.request("POST", "/_matrix/client/v3/register", {"username": "unwanted", "password": "test-password-123"})
        check(status == 403 and result.get("errcode") == "M_FORBIDDEN", "Registration should be closed after first-account bootstrap.")
        status, room = server.request("POST", "/_matrix/client/v3/createRoom", {"name": "Smoke persistence"}, token=token)
        check(status == 200, "Room creation failed.")
        room_path = urllib.parse.quote(room["room_id"], safe="")
        status, event = server.request("PUT", f"/_matrix/client/v3/rooms/{room_path}/send/m.room.message/1",
                                     {"msgtype": "m.text", "body": "Survives a consistent offline backup"}, token=token)
        check(status == 200, "Sending a room event failed.")
        status, media = server.request("POST", "/_matrix/media/v3/upload?filename=smoke.txt", b"persistent media\n", token=token)
        check(status == 200, "Media upload failed.")
        print("PASS: pinned binary, rendered SMTP/TURN/RTC config, offline bootstrap, exact password login, discovery, closed registration, rooms and media")
    finally:
        server.stop()

    # Database and media have both stopped; a plain file copy is now consistent.
    shutil.copytree(data, restored)
    settings.update(data_dir=str(restored), allow_registration="true")
    config.write_text(render_config(**settings))
    try:
        server.start()
        status, result = server.request("GET", "/_matrix/client/v3/account/whoami", token=token)
        check(status == 200 and result["user_id"] == "@alice:example.test", "The restored database lost its login session.")
        event_path = urllib.parse.quote(event["event_id"], safe="")
        status, saved_event = server.request("GET", f"/_matrix/client/v3/rooms/{room_path}/event/{event_path}", token=token)
        check(status == 200 and saved_event["content"]["body"] == "Survives a consistent offline backup", "The restored database lost room history.")
        media_path = media["content_uri"].removeprefix("mxc://")
        status, payload = server.request("GET", "/_matrix/client/v1/media/download/" + media_path, token=token, raw=True)
        check(status == 200 and payload == b"persistent media\n", "The restored data lost uploaded media.")

        registration = {"username": "bob", "password": "smoke-password-bob"}
        status, challenge = server.request("POST", "/_matrix/client/v3/register", registration)
        check(status == 401 and any("m.login.registration_token" in flow["stages"] for flow in challenge["flows"]),
              "Enabled registration does not require a token.")
        registration["auth"] = {"type": "m.login.registration_token", "session": challenge["session"], "token": "invalid"}
        status, rejected = server.request("POST", "/_matrix/client/v3/register", registration)
        check(status in (401, 403), "An invalid registration token was accepted.")
        registration["auth"]["token"] = registration_token
        status, registered = server.request("POST", "/_matrix/client/v3/register", registration)
        if status == 401 and "m.login.dummy" not in registered.get("completed", []):
            registration["auth"] = {"type": "m.login.dummy", "session": challenge["session"]}
            status, registered = server.request("POST", "/_matrix/client/v3/register", registration)
        check(status == 200 and registered.get("user_id") == "@bob:example.test", "A valid configured registration token failed.")
        print("PASS: clean shutdown, restored RocksDB login/history/media, rejected invalid token, accepted valid token")
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path, help="Previously downloaded binary (SHA256 checked).")
    source.add_argument("--download", action="store_true", help="Download the pinned architecture-specific release from the manifest.")
    parser.add_argument("--keep-directory", type=Path, help="Use and retain this new directory, including private test logs.")
    arguments = parser.parse_args()
    if arguments.keep_directory:
        arguments.keep_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        directory = arguments.keep_directory.resolve()
        with SmtpFixture() as smtp_port:
            run_smoke(select_binary(arguments, directory), directory, smtp_port)
    else:
        with tempfile.TemporaryDirectory(prefix="continuwuity-smoke-") as temporary:
            directory = Path(temporary)
            with SmtpFixture() as smtp_port:
                run_smoke(select_binary(arguments, directory), directory, smtp_port)


if __name__ == "__main__":
    main()
