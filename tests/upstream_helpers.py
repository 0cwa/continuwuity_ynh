"""Local servers, HTTP requests and pinned-binary selection for the smoke test."""

import hashlib
import json
import platform
import shutil
import socketserver
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.request

from package_helpers import PACKAGE


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
