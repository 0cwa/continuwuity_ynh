"""Safety tests for the isolated S3 snapshot helper."""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "conf"))
import s3_snapshot


def settings(**overrides):
    env = {
        "media_s3_endpoint": "https://s3.example.test",
        "media_s3_region": "us-east-1",
        "media_s3_bucket": "live-media",
        "media_s3_key_prefix": "media",
        "media_s3_access_key_id": "access-secret-for-test",
        "media_s3_secret_access_key": "private-secret-for-test",
        "media_s3_backup_bucket": "media-snapshots",
        "media_s3_restore_bucket": "live-media",
        "media_s3_restore_key_prefix": "restore/generation-1",
    }
    env.update(overrides)
    return env


class FakeRclone:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.copy_mode = "normal"
        self.copy_stderr = ""
        self.copy_returncode = 0
        self.omit_hashes = False
        self.marker_race = False
        self.oversized_cat = False
        self.last_process = None

    @staticmethod
    def _remote(value):
        return value

    def run(self, command, *, env, check, text, stdout, stderr):
        self.calls.append((list(command), env.copy()))
        args = command[1:]
        if args[0] == "lsjson":
            remote = self._remote(args[-1])
            rows = []
            for path, contents in sorted(self.objects.get(remote, {}).items()):
                rows.append({"Path": path, "Size": len(contents)})
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        if args[0] == "hashsum":
            remote = args[-1]
            if self.omit_hashes:
                return subprocess.CompletedProcess(command, 0, "", "")
            lines = [hashlib.sha256(contents).hexdigest() + "  " + path
                     for path, contents in sorted(self.objects.get(remote, {}).items())
                     if path != s3_snapshot.COMMIT_MARKER or "--exclude" not in args]
            return subprocess.CompletedProcess(command, 0, "\n".join(lines) + ("\n" if lines else ""), "")
        if args[0] == "copyto":
            remote = args[2]
            parent, path = remote.rsplit("/", 1)
            if self.marker_race:
                self.objects.setdefault(parent, {})[path] = b"pre-existing marker"
            if path not in self.objects.setdefault(parent, {}):
                self.objects[parent][path] = Path(args[1]).read_bytes()
            return subprocess.CompletedProcess(command, 0, "", "")
        if args[0] == "cat":
            remote = args[-1]
            parent, path = remote.rsplit("/", 1)
            try:
                content = self.objects[parent][path].decode()
            except KeyError:
                return subprocess.CompletedProcess(command, 3, "", "not found")
            return subprocess.CompletedProcess(command, 0, content, "")
        if args[0] == "copy":
            source, destination = args[1:3]
            existing = self.objects.setdefault(destination, {})
            for path, contents in self.objects.get(source, {}).items():
                if "--exclude" in args and path == s3_snapshot.COMMIT_MARKER:
                    continue
                if path not in existing and not (self.copy_mode == "partial" and path == "two.bin"):
                    existing[path] = contents
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 2, "", "diagnostics include private-secret-for-test")

    def popen(self, command, *, env, stdin, stdout, stderr, text=None, bufsize=-1, **kwargs):
        self.calls.append((list(command), env.copy()))
        args = command[1:]
        operation = args[0]
        clean_args = [operation]
        index = 1
        while index < len(args):
            if args[index] in ("--contimeout", "--timeout", "--retries"):
                index += 2
                continue
            clean_args.append(args[index])
            index += 1
        args = clean_args
        if args[0] == "cat":
            content = b"x" * (s3_snapshot.MAX_COMMIT_MARKER_BYTES + 100) if self.oversized_cat else self.objects[
                args[-1].rsplit("/", 1)[0]
            ][args[-1].rsplit("/", 1)[1]]
            process = FakeProcess(command, stdout=content, text=False, stdout_mode=stdout, stderr_mode=stderr)
            self.last_process = process
            return process
        if args[0] == "copy":
            source, destination = args[1:3]
            existing = self.objects.setdefault(destination, {})
            log_lines = []
            for path, contents in self.objects.get(source, {}).items():
                if "--exclude" in args and path == s3_snapshot.COMMIT_MARKER:
                    continue
                if path not in existing and not (self.copy_mode == "partial" and path == "two.bin"):
                    existing[path] = contents
                    reason = "Copied (new)" if self.copy_mode == "fallback" else "Copied (server-side copy)"
                    log_lines.append(f"2026/11/16 20:59:46 INFO  : {path}: {reason}\n")
            process = FakeProcess(command, stderr="".join(log_lines) + self.copy_stderr,
                                  text=False, stdout_mode=stdout, stderr_mode=stderr)
            process.returncode = self.copy_returncode
            return process
        if args[0] == "lsjson":
            remote = args[-1]
            rows = [{"Path": path, "Size": len(contents)}
                    for path, contents in sorted(self.objects.get(remote, {}).items())]
            return FakeProcess(command, stdout=json.dumps(rows), text=True, stdout_mode=stdout, stderr_mode=stderr)
        if args[0] == "copyto":
            remote = args[2]
            parent, path = remote.rsplit("/", 1)
            if self.marker_race:
                self.objects.setdefault(parent, {})[path] = b"pre-existing marker"
            if path not in self.objects.setdefault(parent, {}):
                self.objects[parent][path] = Path(args[1]).read_bytes()
            return FakeProcess(command, text=True, stdout_mode=stdout, stderr_mode=stderr)
        remote = args[-1]
        if self.omit_hashes:
            return FakeProcess(command, "")
        lines = [hashlib.sha256(contents).hexdigest() + "  " + path
                 for path, contents in sorted(self.objects.get(remote, {}).items())
                 if path != s3_snapshot.COMMIT_MARKER or "--exclude" not in args]
        return FakeProcess(command, stdout="\n".join(lines) + ("\n" if lines else ""), text=False, stdout_mode=stdout, stderr_mode=stderr)


class FakeProcess:
    def __init__(self, command, output="", stderr="", raw_stdout=None, *, stdout=None, text=False,
                 stdout_mode=subprocess.PIPE, stderr_mode=subprocess.DEVNULL):
        self.command = command
        self.pid = 2**30
        self._text = text
        self._stdout_data = raw_stdout if raw_stdout is not None else (stdout if stdout is not None else output)
        self._stderr_data = stderr
        self.stdout = self._pipe(self._stdout_data) if stdout_mode == subprocess.PIPE else None
        self.stderr = self._pipe(self._stderr_data) if stderr_mode == subprocess.PIPE else None
        self.returncode = 0

    @staticmethod
    def _pipe(data):
        read_fd, write_fd = os.pipe()
        if isinstance(data, str):
            data = data.encode()
        if data:
            os.write(write_fd, data)
        os.close(write_fd)
        return os.fdopen(read_fd, "rb", buffering=0)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        out = self.stdout.read() if self.stdout else b""
        err = self.stderr.read() if self.stderr else b""
        if self._text:
            out, err = out.decode(), err.decode()
        return out, err

    def kill(self):
        self.returncode = -9


class S3SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / "snapshot.json"
        self.fake = FakeRclone()
        self.patch = patch.object(s3_snapshot.subprocess, "run", self.fake.run)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.popen_patch = patch.object(s3_snapshot.subprocess, "Popen", self.fake.popen)
        self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)
        self.env = settings()
        self.live = "LIVE:live-media/media"
        self.source_objects = {"one.bin": b"one", "two.bin": b"two"}
        self.fake.objects[self.live] = dict(self.source_objects)

    def snapshot_remote(self):
        return next(key for key in self.fake.objects if key.startswith("SNAPSHOT:media-snapshots/"))

    def test_create_verifies_inventory_before_publishing_manifest(self):
        generation = s3_snapshot.create(self.manifest, self.env)
        data = json.loads(self.manifest.read_text())
        self.assertEqual(generation, data["generation"])
        self.assertEqual(data["snapshot"]["prefix"], f"continuwuity-generations/{generation}")
        self.assertEqual(data["inventory"], [
            {"path": "one.bin", "size": 3, "sha256": hashlib.sha256(b"one").hexdigest()},
            {"path": "two.bin", "size": 3, "sha256": hashlib.sha256(b"two").hexdigest()},
        ])
        copy_call = next(call[0] for call in self.fake.calls if call[0][1] == "copy")
        self.assertIn("--transfers", copy_call)
        self.assertIn("4", copy_call)
        self.assertIn("--checkers", copy_call)
        self.assertIn("8", copy_call)
        self.assertIn("--ignore-existing", copy_call)
        for command, _ in self.fake.calls:
            self.assertIn("--contimeout", command)
            self.assertIn("1m", command)
            self.assertIn("--timeout", command)
            self.assertIn("5m", command)
            self.assertIn("--retries", command)
            self.assertIn("3", command)
        self.assertIn("--server-side-across-configs", copy_call)

    def test_credentials_are_only_in_child_environment(self):
        inherited = self.env | {
            "AWS_SECRET_ACCESS_KEY": "other-secret",
            "AWS_SESSION_TOKEN": "other-session-token",
            "RCLONE_CONFIG_UNRELATED_SECRET": "another-secret",
            "unrelated_secret": "accidentally-inherited-secret",
        }
        s3_snapshot.create(self.manifest, inherited)
        for command, child_env in self.fake.calls:
            self.assertNotIn("private-secret-for-test", " ".join(command))
            self.assertNotIn("access-secret-for-test", " ".join(command))
            for secret in ("other-secret", "other-session-token", "another-secret", "accidentally-inherited-secret"):
                self.assertNotIn(secret, child_env.values())
            self.assertEqual(child_env["RCLONE_CONFIG"], "/dev/null")
            self.assertNotIn("RCLONE_CONFIG_LIVE_ACL", child_env)
            if command[1] == "copy":
                self.assertEqual(child_env["RCLONE_CONFIG_LIVE_SECRET_ACCESS_KEY"], "private-secret-for-test")
                self.assertEqual(child_env["RCLONE_CONFIG_SNAPSHOT_ACCESS_KEY_ID"], "access-secret-for-test")

    def test_partial_or_changed_copy_never_publishes_manifest(self):
        self.fake.copy_mode = "partial"
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "did not server-side-copy"):
            s3_snapshot.create(self.manifest, self.env)
        self.assertFalse(self.manifest.exists())
        self.assertTrue(self.fake.objects[self.snapshot_remote()])

    def test_rclone_fallback_to_client_side_copy_is_rejected(self):
        self.fake.copy_mode = "fallback"
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "did not server-side-copy"):
            s3_snapshot.create(self.manifest, self.env)
        self.assertFalse(self.manifest.exists())

    def test_copy_failure_summary_never_exposes_or_interprets_raw_stderr(self):
        credential = "private-key-StatusCode: 403-api error AccessDenied:"
        self.fake.copy_stderr = (
            "2026/11/16 20:59:46 ERROR : one.bin: Failed to copy: "
            "original key path status=403 code=AccessDenied; "
            f"https://bucket.example.test/private-bucket/private-object credential={credential} "
            "response StatusCode: 502 RequestID: request-id-secret\n"
        )
        self.fake.copy_returncode = 1
        with self.assertRaises(s3_snapshot.SnapshotError) as raised:
            s3_snapshot.create(
                self.manifest,
                settings(media_s3_secret_access_key=credential),
            )
        message = str(raised.exception)
        self.assertIn("rclone copy operation failed", message)
        self.assertIn("server-side copies 2/2", message)
        self.assertIn("1 error lines", message)
        for sensitive_or_untrusted in (
            "private-key", "403", "AccessDenied", "502", "bucket.example.test", "private-bucket",
            "private-object", "request-id-secret", "original key path",
        ):
            self.assertNotIn(sensitive_or_untrusted, message)
        self.assertFalse(self.manifest.exists())

    def test_hostile_echoed_path_is_never_returned_in_copy_summary(self):
        malicious_path = "nested/status=403/code=AccessDenied"
        self.fake.objects[self.live][malicious_path] = b"path fixture"
        self.fake.copy_stderr = (
            f"2026/11/16 20:59:46 ERROR : {malicious_path}: "
            f"Failed to copy: echoed key {malicious_path}; StatusCode: 403\n"
        )
        self.fake.copy_returncode = 1
        with self.assertRaises(s3_snapshot.SnapshotError) as raised:
            s3_snapshot.create(self.manifest, self.env)
        message = str(raised.exception)
        self.assertIn("server-side copies 3/3", message)
        self.assertIn("1 error lines", message)
        self.assertNotIn(malicious_path, message)
        self.assertNotIn("403", message)
        self.assertNotIn("AccessDenied", message)
        self.assertFalse(self.manifest.exists())

    def test_real_rclone_copy_result_line_shape_is_recognized(self):
        severity, path, result = s3_snapshot._rclone_copy_result_parts(
            "2026/11/16 20:59:46 INFO  : sample.txt: Copied (new)", {"sample.txt"}
        )
        self.assertEqual((severity, path, result), ("INFO", "sample.txt", "Copied (new)"))
        severity, path, result = s3_snapshot._rclone_copy_result_parts(
            "2026/11/16 20:59:46 INFO  : sample.txt: Copied (server-side copy)", {"sample.txt"}
        )
        self.assertEqual((severity, path, result), ("INFO", "sample.txt", "Copied (server-side copy)"))

    def test_server_copy_text_in_object_name_cannot_count_fallback(self):
        malicious_path = "Copied (server-side copy).bin"
        self.fake.objects[self.live][malicious_path] = b"fallback object"
        self.fake.copy_mode = "fallback"
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, r"server-side copies 0/3"):
            s3_snapshot.create(self.manifest, self.env)
        self.assertFalse(self.manifest.exists())

    def test_duplicate_server_copy_result_counts_object_once(self):
        self.fake.copy_stderr = "2026/11/16 20:59:46 INFO  : one.bin: Copied (server-side copy)\n"
        generation = s3_snapshot.create(self.manifest, self.env)
        self.assertEqual(json.loads(self.manifest.read_text())["generation"], generation)

    def test_empty_source_is_a_valid_generation_and_empty_restore(self):
        self.fake.objects[self.live] = {}
        s3_snapshot.create(self.manifest, self.env)
        self.assertEqual(json.loads(self.manifest.read_text())["inventory"], [])
        s3_snapshot.restore(self.manifest, self.env)
        self.assertNotIn("RESTORE:live-media/restore/generation-1", self.fake.objects)
        self.assertEqual(sum(1 for command, _ in self.fake.calls if command[1] == "copy"), 0)

    def test_verify_checks_remote_inventory_without_copying(self):
        generation = s3_snapshot.create(self.manifest, self.env)
        copy_count = sum(1 for command, _ in self.fake.calls if command[1] == "copy")
        self.assertEqual(s3_snapshot.verify(self.manifest, self.env), generation)
        self.assertEqual(sum(1 for command, _ in self.fake.calls if command[1] == "copy"), copy_count)

    def test_missing_empty_snapshot_cannot_verify_without_commit_marker(self):
        self.fake.objects[self.live] = {}
        s3_snapshot.create(self.manifest, self.env)
        self.fake.objects.pop(self.snapshot_remote())
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "commit marker"):
            s3_snapshot.verify(self.manifest, self.env)

    def test_raced_generation_marker_is_not_overwritten(self):
        self.fake.marker_race = True
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "commit marker size does not match"):
            s3_snapshot.create(self.manifest, self.env)
        remote = self.snapshot_remote()
        self.assertEqual(self.fake.objects[remote][s3_snapshot.COMMIT_MARKER], b"pre-existing marker")
        self.assertFalse(self.manifest.exists())

    def test_oversized_raced_marker_read_is_hard_capped(self):
        s3_snapshot.create(self.manifest, self.env)
        self.fake.oversized_cat = True
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "commit marker exceeds"):
            s3_snapshot.verify(self.manifest, self.env)
        self.assertGreater(len(self.fake.last_process._stdout_data), s3_snapshot.MAX_COMMIT_MARKER_BYTES)

    def test_reported_oversized_marker_is_rejected_before_read(self):
        s3_snapshot.create(self.manifest, self.env)
        remote = self.snapshot_remote()
        self.fake.objects[remote][s3_snapshot.COMMIT_MARKER] += b"x"
        cat_calls_before = sum(1 for command, _ in self.fake.calls if command[1] == "cat")
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "commit marker size"):
            s3_snapshot.verify(self.manifest, self.env)
        cat_calls_after = sum(1 for command, _ in self.fake.calls if command[1] == "cat")
        self.assertEqual(cat_calls_after, cat_calls_before)

    def test_equal_size_corruption_is_detected_by_content_sha256(self):
        s3_snapshot.create(self.manifest, self.env)
        self.fake.objects[self.snapshot_remote()]["one.bin"] = b"BAD"
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "inventory does not match"):
            s3_snapshot.verify(self.manifest, self.env)

    def test_missing_sha256_inventory_fails_closed(self):
        self.fake.omit_hashes = True
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "SHA-256 inventory is incomplete"):
            s3_snapshot.create(self.manifest, self.env)
        self.assertFalse(self.manifest.exists())

    def test_restore_refuses_nonempty_destination_without_copying(self):
        s3_snapshot.create(self.manifest, self.env)
        target = "RESTORE:live-media/restore/generation-1"
        self.fake.objects[target] = {"existing": b"keep"}
        copy_count = sum(1 for command, _ in self.fake.calls if command[1] == "copy")
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "not empty"):
            s3_snapshot.restore(self.manifest, self.env)
        self.assertEqual(self.fake.objects[target], {"existing": b"keep"})
        self.assertEqual(sum(1 for command, _ in self.fake.calls if command[1] == "copy"), copy_count)

    def test_restore_fails_if_snapshot_was_changed_or_removed(self):
        s3_snapshot.create(self.manifest, self.env)
        remote = self.snapshot_remote()
        self.fake.objects[remote]["one.bin"] = b"tampered"
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "snapshot inventory"):
            s3_snapshot.restore(self.manifest, self.env)
        self.assertNotIn("RESTORE:live-media/restore/generation-1", self.fake.objects)

    def test_rejects_bucket_and_prefix_overlap_before_remote_access(self):
        for override, message in (
            ({"media_s3_backup_bucket": "live-media"}, "different buckets"),
            ({"media_s3_restore_key_prefix": "media/child"}, "overlaps"),
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(s3_snapshot.SnapshotError, message):
                    s3_snapshot._config(settings(**override), restore=any("restore" in key for key in override))
        self.assertEqual(self.fake.calls, [])

    def test_root_live_prefix_overlaps_every_same_bucket_restore_target(self):
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "invalid object prefix"):
            s3_snapshot._config(settings(media_s3_key_prefix=""), restore=True)

    def test_requires_nonempty_prefix_and_static_credential_pair(self):
        for overrides in (
            {"media_s3_key_prefix": ""},
            {"media_s3_access_key_id": ""},
            {"media_s3_secret_access_key": ""},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(s3_snapshot.SnapshotError):
                    s3_snapshot._config(settings(**overrides))

    def test_empty_endpoint_selects_aws_defaults_without_inheriting_env(self):
        env = settings(media_s3_endpoint="", unrelated_secret="accidentally-inherited-secret",
                       AWS_SECRET_ACCESS_KEY="other-secret", RCLONE_CONFIG="/tmp/user-rclone.conf")
        child_env, endpoint, *_ = s3_snapshot._config(env)
        self.assertEqual(endpoint, "")
        self.assertEqual(child_env["RCLONE_CONFIG_LIVE_PROVIDER"], "AWS")
        self.assertNotIn("RCLONE_CONFIG_LIVE_ENDPOINT", child_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child_env)
        self.assertNotIn("unrelated_secret", child_env)

        restore_env, _, _, _, _, _, _ = s3_snapshot._config(env, restore=True)
        self.assertEqual(restore_env["RCLONE_CONFIG_RESTORE_PROVIDER"], "AWS")
        self.assertNotIn("RCLONE_CONFIG_RESTORE_ENDPOINT", restore_env)

    def test_custom_endpoint_restore_remote_uses_other_provider(self):
        child_env, *_ = s3_snapshot._config(settings(), restore=True)
        self.assertEqual(child_env["RCLONE_CONFIG_RESTORE_PROVIDER"], "Other")
        self.assertEqual(child_env["RCLONE_CONFIG_RESTORE_ENDPOINT"], "https://s3.example.test")

    def test_rejects_malformed_source_and_manifest_paths(self):
        for prefix in ("../escape", "/absolute", "a//b", "a/./b", "a/../b"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(s3_snapshot.SnapshotError):
                    s3_snapshot.create(self.manifest, settings(media_s3_key_prefix=prefix))
        self.assertEqual(self.fake.calls, [])
        self.manifest.write_text(json.dumps({"schema_version": 1, "generation": "20260101T000000Z-" + "0" * 32,
                                             "source": {"bucket": "live-media", "prefix": "media"},
                                             "snapshot": {"bucket": "media-snapshots", "prefix": "continuwuity-generations/x"},
                                             "inventory": []}))
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "malformed"):
            s3_snapshot._load_manifest(self.manifest)

    def test_manifest_is_never_overwritten(self):
        self.manifest.write_text("existing\n")
        with self.assertRaisesRegex(s3_snapshot.SnapshotError, "already exists"):
            s3_snapshot.create(self.manifest, self.env)
        self.assertEqual(self.manifest.read_text(), "existing\n")

    def test_rclone_failure_does_not_expose_diagnostics(self):
        def failed_popen(command, **kwargs):
            process = FakeProcess(command, stderr="diagnostics include private-secret-for-test", text=True,
                                  stdout_mode=kwargs["stdout"], stderr_mode=kwargs["stderr"])
            process.returncode = 2
            return process

        with patch.object(s3_snapshot.subprocess, "Popen", failed_popen):
            with self.assertRaises(s3_snapshot.SnapshotError) as raised:
                s3_snapshot.create(self.manifest, self.env)
        self.assertNotIn("private-secret-for-test", str(raised.exception))
        self.assertFalse(self.manifest.exists())

    def test_hung_rclone_is_killed_reaped_and_caller_recovers(self):
        self.popen_patch.stop()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "rclone"
            marker = root / "child-terminated"
            pid_file = root / "child.pid"
            child = (
                "import signal,sys,time\n"
                f"signal.signal(signal.SIGTERM, lambda *_: (open({str(marker)!r}, 'w').close(), sys.exit(0)))\n"
                "time.sleep(30)"
            )
            executable.write_text(
                f"#!{sys.executable}\n"
                "import subprocess,sys,time\n"
                f"child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(30)\n"
            )
            executable.chmod(0o700)
            env = {"PATH": str(root), "RCLONE_CONFIG": "/dev/null"}
            with patch.object(s3_snapshot, "RCLONE_OPERATION_TIMEOUT_SECONDS", 0.1):
                with self.assertRaisesRegex(s3_snapshot.SnapshotError, "timed out"):
                    s3_snapshot._run_rclone(env, "lsjson", "remote:bucket")
                self.assertTrue(pid_file.exists(), "test child process did not start")
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), "timeout did not terminate the rclone child process group")

                executable.write_text(f"#!{sys.executable}\nprint('recovered')\n")
                executable.chmod(0o700)
                self.assertEqual(s3_snapshot._run_rclone(env, "lsjson", "remote:bucket"), "recovered\n")

    def test_streamed_rclone_timeout_terminates_and_reports(self):
        self.popen_patch.stop()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "rclone"
            executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            executable.chmod(0o700)
            env = {"PATH": str(root), "RCLONE_CONFIG": "/dev/null"}
            with patch.object(s3_snapshot, "RCLONE_OPERATION_TIMEOUT_SECONDS", 0.1):
                with self.assertRaisesRegex(s3_snapshot.SnapshotError, "timed out"):
                    s3_snapshot._stream_rclone_lines(env, ["hashsum", "SHA256", "remote:bucket"],
                                                    output="stdout", on_line=lambda _line: None)

    def test_marker_reader_timeout_is_bounded(self):
        self.popen_patch.stop()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "rclone"
            executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
            executable.chmod(0o700)
            env = {"PATH": str(root), "RCLONE_CONFIG": "/dev/null"}
            with patch.object(s3_snapshot, "RCLONE_OPERATION_TIMEOUT_SECONDS", 0.1):
                with self.assertRaisesRegex(s3_snapshot.SnapshotError, "timed out"):
                    s3_snapshot._read_rclone_bytes(env, "cat", "remote:bucket/marker", limit=128)


if __name__ == "__main__":
    unittest.main()
