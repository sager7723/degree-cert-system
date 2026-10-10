# Web dependencies

`wheels/` contains original Python wheels obtained from the Python Package Index
([PyPI](https://pypi.org/)). `requirements-linux.lock` pins all eight versions and
their complete wheel SHA-256 hashes. The installer uses `--no-index` and
`--require-hashes`, so installing this bundle needs no package download.

The bundle targets CPython 3.12 on Ubuntu x86_64. MarkupSafe includes a native
CPython 3.12 manylinux x86_64 extension; the other seven wheels are platform
independent. No source compilation or system package installation is required.

| Package | Version | License | Upstream source |
| --- | --- | --- | --- |
| Flask | 3.1.3 | BSD-3-Clause | https://github.com/pallets/flask/ |
| Werkzeug | 3.1.8 | BSD-3-Clause | https://github.com/pallets/werkzeug/ |
| Jinja2 | 3.1.6 | BSD-3-Clause | https://github.com/pallets/jinja/ |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| blinker | 1.9.0 | MIT | https://github.com/pallets-eco/blinker/ |
| itsdangerous | 2.2.0 | BSD-3-Clause | https://github.com/pallets/itsdangerous/ |
| MarkupSafe | 3.0.3 | BSD-3-Clause | https://github.com/pallets/markupsafe/ |
| waitress | 3.0.2 | ZPL-2.1 | https://github.com/Pylons/waitress/ |

All original copyright notices and full license texts remain in each wheel's
`.dist-info/LICENSE.txt` or `.dist-info/licenses/LICENSE.txt`. Werkzeug also
retains `werkzeug/debug/shared/ICON_LICENSE.md`. Python's standard-library
SQLite module supplies database support; there is no additional database wheel.

The hashes verify the distributed archives. They do not establish that the
Ubuntu chain is available; the installation script runs the application's
separate `doctor` check against the configured real chain.
