#!/usr/bin/env python3
"""Validate package metadata against official YunoHost JSON schemas."""

import argparse
import json
from pathlib import Path
import tomllib
import urllib.request

import jsonschema


PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA_URL = "https://raw.githubusercontent.com/YunoHost/apps/main/schemas/"
FILES = {
    "manifest.toml": "manifest.v2.schema.json",
    "tests.toml": "tests.v1.schema.json",
    "config_panel.toml": "config_panel.v1.schema.json",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--schema-directory", type=Path, help="Directory containing the three official schema files.")
    source.add_argument("--download", action="store_true", help="Read the current official schemas from YunoHost/apps.")
    arguments = parser.parse_args()
    failures = []
    for filename, schema_name in FILES.items():
        if arguments.download:
            with urllib.request.urlopen(SCHEMA_URL + schema_name, timeout=30) as response:
                schema = json.load(response)
        else:
            schema = json.loads((arguments.schema_directory / schema_name).read_text())
        validator = jsonschema.validators.validator_for(schema)
        validator.check_schema(schema)
        document = tomllib.loads((PACKAGE / filename).read_text())
        errors = list(validator(schema).iter_errors(document))
        for error in errors:
            failures.append(f"{filename}:{'.'.join(map(str, error.path))}: {error.message}")
        if not errors:
            print(f"PASS: {filename} matches {schema_name}")
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
