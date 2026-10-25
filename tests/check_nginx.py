#!/usr/bin/env python3
"""Run real nginx -t with rendered package vhosts and YunoHost proxy parameters."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


PACKAGE = Path(__file__).resolve().parents[1]


def render(name):
    template = (PACKAGE / "conf" / name).read_text()
    for key, value in {"APP": "continuwuity", "PORT": "18008", "MAX_UPLOAD_SIZE": "50"}.items():
        template = template.replace(f"__{key}__", value)
    if re.search(r"__[A-Z_]+__", template):
        raise ValueError("An nginx template contains an unresolved placeholder.")
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", default=shutil.which("nginx"), help="Path to the nginx binary.")
    arguments = parser.parse_args()
    if not arguments.nginx:
        parser.error("Install nginx or pass --nginx /path/to/nginx")
    nginx = str(Path(arguments.nginx).resolve())
    with tempfile.TemporaryDirectory(prefix="continuwuity-nginx-") as directory:
        directory = Path(directory)
        shutil.copyfile(PACKAGE / "tests/fixtures/proxy_params_no_auth", directory / "proxy_params_no_auth")
        (directory / "homeserver.conf").write_text(render("nginx.conf"))
        (directory / "delegation.conf").write_text(render("delegation.conf"))
        configuration = directory / "nginx.conf"
        configuration.write_text(f'''
pid "{directory}/nginx.pid";
error_log stderr;
events {{}}
http {{
    access_log off;
    client_body_temp_path "{directory}/body";
    proxy_temp_path "{directory}/proxy";
    fastcgi_temp_path "{directory}/fastcgi";
    uwsgi_temp_path "{directory}/uwsgi";
    scgi_temp_path "{directory}/scgi";
    map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
    server {{
        listen 127.0.0.1:18080;
        server_name matrix.example.test;
        include homeserver.conf;
    }}
    server {{
        listen 127.0.0.1:18080;
        server_name example.test;
        location / {{ return 200 'Other existing application'; }}
        include delegation.conf;
    }}
}}
''')
        subprocess.run([nginx, "-t", "-e", "stderr", "-p", str(directory) + "/", "-c", str(configuration)], check=True)
        print("PASS: nginx parses the API and delegated discovery vhosts with YunoHost's proxy include")


if __name__ == "__main__":
    main()
