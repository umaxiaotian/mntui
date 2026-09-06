# Validation

Validated on Linux with Python 3.14.4, Pydantic 2.13.5,
prompt_toolkit 3.0.53, and Nuitka 4.2.1. Exact Python dependency
versions are recorded in `requirements-dev.lock`.

- `pytest`: 61 tests passed, including mocked storage commands, fstab rollback,
  stale plans, missing commands, dry-run, Btrfs mount verification, and TUI navigation.
- `ruff check .`: passed.
- `ruff format --check .`: passed.
- `mypy --strict src/`: passed (15 source files).
- Japanese public API docstrings: audited by the test suite.
- Source CLI: `--version` and `--check` passed.
- Empty-PATH CLI: exited with status 2 and a readable dependency report, no traceback.
- Host discovery: read-only inspection succeeded and identified the system disk.
- TUI: startup, quit, and Tab navigation passed using an in-memory terminal.

The development sandbox prevents asyncio worker completion; the full test suite
was therefore run outside it. No real block device was partitioned, formatted,
mounted, or unmounted. No host fstab was changed.

Real write-path acceptance testing in a disposable Linux VM remains necessary
before a production release. No destructive loop-device tests are included.

## Compiled executables

Both final-source Nuitka builds succeeded:

- Standalone: `build/standalone/__main__.dist/mntui` (distribute the entire directory).
- Onefile: `build/onefile/mntui` (approximately 16.6 MB compressed payload).

For both formats, `--version` succeeded even with an empty command-search PATH,
`--check` returned 0 on this host, and `--check` with an empty PATH returned 2
without a traceback. The standalone TUI was also started with `--dry-run` in a
real pseudo-terminal and exited normally using `q`. The distribution was checked
to contain none of the Linux storage command executables.
