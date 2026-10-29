"""Package paths and template rendering shared by tests and smoke checks."""

import json
from pathlib import Path

from jinja2 import Environment, StrictUndefined


PACKAGE = Path(__file__).resolve().parents[1]


def render_config(**overrides):
    """Use the same Jinja filter semantics as ynh_config_add --jinja."""
    values = {
        "app": "continuwuity",
        "domain": "matrix.example.test",
        "server_name": "example.test",
        "install_dir": "/var/www/continuwuity",
        "data_dir": "/home/yunohost.app/continuwuity",
        "port": 18008,
        "admin": "alice",
        "allow_registration": "false",
        "allow_federation": "true",
        "max_upload_size": 50,
        "log_level": "info",
        "smtp_enabled": "false",
        "turn_uris_json": "[]",
        "turn_ttl": 86400,
        "rtc_service_url": "",
    }
    values.update(overrides)
    environment = Environment(undefined=StrictUndefined)
    environment.filters["to_json"] = json.dumps
    return environment.from_string((PACKAGE / "conf/config.toml").read_text()).render(values)
