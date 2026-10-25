#!/usr/bin/env python3
"""Write private offline first-account commands without exposing the password.

Upstream v26.9.1 splits admin commands on whitespace; it does not interpret
shell quoting. Reject whitespace rather than silently changing a password.
After installation, Matrix clients can set a password containing whitespace.
"""

import json
import os
from pathlib import Path
import re
import sys


def write_bootstrap(path: Path, username: str, password: str) -> None:
    if not re.fullmatch(r"[a-z0-9._=\-]+", username):
        raise ValueError("The Matrix administrator username has an unsupported character.")
    if not password or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in password):
        raise ValueError("The initial Matrix password must not contain whitespace or control characters.")

    # This is input to Continuwuity's command parser, never a shell command.
    # -- protects passwords beginning with '-' from Clap option parsing.
    commands = [f"users create-user -- {username} {password}", "server shutdown"]
    content = "[global]\nadmin_execute = " + json.dumps(commands, ensure_ascii=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: bootstrap.py OUTPUT_PATH", file=sys.stderr)
        return 2
    try:
        write_bootstrap(
            Path(sys.argv[1]),
            os.environ.get("CONTINUWUITY_BOOTSTRAP_USER", ""),
            os.environ.get("CONTINUWUITY_BOOTSTRAP_PASSWORD", ""),
        )
    except (OSError, ValueError):
        # Never emit the password, command text, environment, or file contents.
        print("Could not create private bootstrap configuration; check the username, password constraints and output path.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
