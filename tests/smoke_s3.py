#!/usr/bin/env python3
"""Exercise a candidate binary with package configuration and a disposable S3 bucket.

The caller creates the bucket and supplies AWS credentials in the environment.
The test leaves an uploaded object in the bucket, which must not contain real data.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import smoke_upstream
from upstream_helpers import SmtpFixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--bucket', required=True)
    args = parser.parse_args()

    render = smoke_upstream.render_config

    def render_with_s3(**settings):
        config = render(**settings)
        config = config.replace('[global]\n', '[global]\nmedia_backend = "s3"\n', 1)
        return config + (
            '\n[global.media_s3]\n'
            f'bucket = {json.dumps(args.bucket)}\n'
            f'endpoint = {json.dumps(args.endpoint)}\n'
            'allow_http = true\n'
        )

    smoke_upstream.render_config = render_with_s3
    with TemporaryDirectory(prefix='continuwuity-s3-smoke-') as directory:
        with SmtpFixture() as smtp_port:
            smoke_upstream.run_smoke(args.binary.resolve(), Path(directory), smtp_port)


if __name__ == '__main__':
    main()
