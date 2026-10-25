# Third-party material

`tests/fixtures/proxy_params_no_auth` is a copy of YunoHost's Nginx proxy include
from commit `a437f4b0a36c35b79bec13e125d503c93a51c8b3`, used to test the package
against the actual directives supplied by YunoHost.

- Copyright: YunoHost contributors.
- License: GNU Affero General Public License, version 3 or later.
- Original source: <https://github.com/YunoHost/yunohost/blob/a437f4b0a36c35b79bec13e125d503c93a51c8b3/conf/nginx/proxy_params_no_auth>.
- License text: [LICENSES/AGPL-3.0-or-later.txt](LICENSES/AGPL-3.0-or-later.txt).
- Modifications: attribution comments were added; configuration directives are
  unchanged. The file is a test fixture and is not installed onto the server.

The pinned Continuwuity executable is downloaded from upstream at installation
time, not vendored in this repository. Continuwuity is licensed under Apache-2.0.
