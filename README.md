# mntui
<img width="1281" height="603" alt="image" src="https://github.com/user-attachments/assets/89c28902-6b1c-429b-a86f-06d9d510afd1" />

A simple TUI for managing disks, filesystems, and mounts on Linux.
Inspired by `nmtui` and `cfdisk`, mntui provides a compact, keyboard-driven
interface suitable for local terminals and SSH sessions.

> **WARNING: Partitioning and formatting permanently destroy data.** Keep verified
> backups. Review the device path, model, size, and serial before confirming an
> operation. Version 0.1.0 is an early MVP; validate it in a disposable VM before
> using it on valuable storage.

```text
mntui — DRY RUN

Disks
  /dev/nvme0n1   512.00 GB   Example SSD   System
  /dev/sda      4000.00 GB   Example HDD
  Environment / available features

                  <Details>  <Quit>
```

_Screenshot placeholder: replace this illustrative terminal view with a release screenshot._

## Features

- Inspect physical disks, partitions, nested device topology, filesystem UUIDs,
  labels, model, serial, size, mount points, GPT/MBR type, read-only and removable flags.
- Identify system disks using root/boot/system mounts and active swap reported by
  `lsblk`, plus active swap files from `/proc/swaps`; propagate protection through nested devices.
- Initialize an unused, unpartitioned disk with GPT and one full-size Linux partition.
- Format an ordinary partition as ext4, XFS, or Btrfs.
- Mount an existing supported filesystem by UUID; optionally create an empty mount
  directory and persist the mount in `/etc/fstab`.
- Unmount without force or lazy options; detect nested mounts and mntui's own working
  directory, and report the kernel's busy errors.
- Review typed plans, confirm destructive actions by typing the full device path,
  preview with dry-run, and write timestamped logs.
- Diagnose core, feature-specific, and optional commands without installing packages.

Partitioning and formatting are separate, independently confirmed actions. After
initialization, refresh by returning to the disk list, then select the new partition.

## Installation and running from source

Requires Linux and Python **3.12 or newer** for source installations:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
mntui --check
mntui
```

Listing works without root where host permissions permit. Changes require root;
mntui checks the effective UID and never invokes `sudo` itself:

```bash
sudo .venv/bin/mntui
```

A compiled standalone distribution does not need Python installed on the target.
The target still needs the Linux utilities below. Build for the target architecture
and an appropriately old Linux/glibc baseline; binaries are not universally portable
between all Linux distributions.

## Usage

```bash
mntui
mntui --version
mntui --check
mntui --dry-run
mntui --log-file /tmp/mntui.log
```

Use arrows and Space to select entries, Tab / Shift+Tab to move between controls,
and Enter to activate a button. Escape returns or cancels; `q` returns from selection
screens or quits the disk list. Typing `q` in a text field enters the literal letter.
Execution displays progress and a result dialog. Do not interrupt disk writes.

`--dry-run` runs discovery and validation, then reports planned changes. It does not
partition, format, create mount directories, mount, unmount, or modify fstab. An
explicit `--log-file` may still write diagnostic logs. Dry-run does not require root,
although host permissions can prevent UUID probing.

Exit codes: **0** success (including missing optional features), **2** missing core
commands, invalid CLI arguments, or missing interactive terminal; **1** startup or
unhandled execution failure; **130** interruption. Errors handled inside the TUI are
shown in dialogs and do not change the normal eventual quit status.

## Runtime commands and supported filesystems

| Category | Commands | Typical package |
| --- | --- | --- |
| Core | `lsblk`, `blkid`, `findmnt`, `mount`, `umount` | util-linux |
| GPT creation | `sfdisk` | util-linux; fdisk on Debian/Ubuntu |
| ext4 creation | `mkfs.ext4` | e2fsprogs |
| XFS creation | `mkfs.xfs` | xfsprogs |
| Btrfs creation | `mkfs.btrfs` | btrfs-progs |
| Optional, reserved for future features | `smartctl`, `wipefs` | smartmontools, util-linux |

Debian/Ubuntu package `mount` provides `mount` and `umount`. Missing mkfs tools only
disable the associated formatting action; existing supported filesystems can still
be mounted. SMART and signature-wiping features are not implemented in this MVP,
even if their tools are detected. Mount persistence uses pass number 2 for ext4 and
0 for XFS/Btrfs, with `defaults,nofail`.

`mntui --check` reads `/etc/os-release` and provides administrator-only installation
examples. **mntui never installs packages or runs a package manager.** Typical manual
commands (package availability can vary by release):

| Distribution | Administrator command |
| --- | --- |
| Debian / Ubuntu | `apt install util-linux fdisk mount e2fsprogs xfsprogs btrfs-progs` |
| Fedora / RHEL / Rocky / AlmaLinux | `dnf install util-linux e2fsprogs xfsprogs btrfs-progs` |
| Arch Linux | `pacman -S util-linux e2fsprogs xfsprogs btrfs-progs` |
| openSUSE | `zypper install util-linux e2fsprogs xfsprogs btrfs-progs` |
| Alpine Linux | `apk add util-linux e2fsprogs xfsprogs btrfs-progs` |

On unknown distributions the report gives generic package names. Some enterprise
releases do not supply Btrfs packages; leave that feature disabled if unavailable.

Example (abbreviated):

```text
mntui Environment Check
Distribution: ubuntu

Core
  ✓ lsblk
  ✓ blkid
  ✓ findmnt
  ✓ mount
  ✓ umount

Filesystems
  ✓ mkfs.ext4
  ✗ mkfs.xfs — xfsprogs (administrator: apt install xfsprogs)
  ✗ mkfs.btrfs — btrfs-progs (administrator: apt install btrfs-progs)

Status: mntui can run.
```

## Safety and limits

- System disks, read-only devices, and layered LVM/RAID/encrypted storage are blocked
  for changes. Formatting is limited to ordinary partitions. Initialization requires
  no existing partition table, filesystem, or child devices.
- Destructive actions reject mounts anywhere on the parent disk and kernel holders.
  An unresolved or pseudo-filesystem root (for example, a container overlay) blocks
  destructive actions conservatively. Detection is limited to the current host's
  visible devices and mount namespace; run on the host, not in a container.
- The service rebuilds and compares the plan immediately before execution, checks
  every required command before the first write, and checks the target block-device
  identity. Other storage tools must not operate on the same disk concurrently;
  there is no system-wide transaction spanning all Linux utilities.
- Mount targets must be absolute, empty directories without symlink components and
  outside protected system paths. UUID ambiguity is rejected.
- fstab updates preserve unrelated bytes and comments, reject duplicate sources or
  mount targets (including resolvable aliases), use a cooperative lock and unique
  backup beside fstab, validate a staged file, replace atomically, and validate again.
  A failed final validation restores the backup. Backups are retained for manual recovery.
  External editors do not honor mntui's lock; avoid concurrent fstab edits.
- Disk operations cannot be rolled back. If mounting succeeds but persistence fails,
  the filesystem stays mounted and fstab is restored. Errors report how many steps
  completed. Unmounting does not remove an existing persistent entry.
- No forced unmount, automatic package installation, privilege escalation, repair,
  or automatic formatting occurs. Missing commands produce application errors.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy --strict src/
pytest
```

`requirements-dev.lock` pins the environment used for validation. For that same
Python/platform combination, install it before `pip install --no-deps -e .` to
reproduce dependency versions. Runtime dependency ranges remain in pyproject.toml.
Public application APIs use Japanese Google-style docstrings with English section
names; tests include an AST audit for missing Japanese docstrings.

Tests use mocked command outputs and temporary fstab files. The UI smoke test uses
an in-memory terminal. **No test formats or changes real block devices.** Future
loop-device integration tests must carry the `destructive` marker; collection skips
them unless `MNTUI_RUN_DESTRUCTIVE_TESTS=1`. No destructive loop tests are included yet.

## Nuitka builds

Install the development environment and a C compiler, Python development headers,
and `patchelf`. The lock includes a virtual-environment patchelf wheel used for the
validated Linux build; ensure `.venv/bin` is on PATH. Build from the repository root:

```bash
. .venv/bin/activate
export NUITKA_CACHE_DIR=/tmp/mntui-nuitka-cache
python -m nuitka --mode=standalone --include-package=mntui --nofollow-import-to=mypy,pytest,pydantic.mypy,pygments \
  --output-dir=build/standalone --output-filename=mntui --jobs=4 \
  src/mntui/__main__.py
python -m nuitka --mode=onefile --include-package=mntui --nofollow-import-to=mypy,pytest,pydantic.mypy,pygments \
  --output-dir=build/onefile --output-filename=mntui --jobs=4 \
  src/mntui/__main__.py
```

Distribute the **entire** `build/standalone/__main__.dist/` directory for standalone
mode, or `build/onefile/mntui` for onefile mode. Smoke-check each with `--version`
and `--check`. Onefile extracts its Python runtime into a temporary directory.
Nuitka packages Python modules and native Python dependencies; no storage utilities
are included or downloaded by the application. Repeatable commands and dependency
pins do not guarantee byte-for-byte identical binaries across toolchains.

## Architecture

```text
prompt_toolkit TUI / CLI
          │
          ▼
StorageService → typed StoragePlan / StorageOperation
          │
          ├── capability detection / package guidance
          ├── safety checks / mount-point validation
          ├── Discovery (lsblk + findmnt)
          └── FstabManager (backup + validation + atomic update)
                        │
                        ▼
                 CommandRunner
                        │
                        ▼
               Linux storage utilities
```

Pydantic v2 models live in `models/`; command execution and discovery adapters in
`commands/`; orchestration, host capabilities, and fstab management in `services/`;
terminal interaction in `ui/`. There is one subprocess entry point using argument
arrays, captured output, controlled locale, typed errors, and timeouts.

## Roadmap

Multiple partitions; LVM; LUKS; mdadm RAID; SMART; filesystem and partition resizing;
disk labels; NFS/CIFS/iSCSI; Btrfs subvolumes; swap management; cloning; filesystem
checks and repair; enhanced removable-media handling. These are outside v0.1.0.
There is no desktop or web UI.

## License

MIT — see [LICENSE](LICENSE).

## GitHub binary releases

[Linux binary release](.github/workflows/release.yml) runs source checks for pull
requests. Pushes to `main` and manual **Actions → Linux binary release → Run workflow**
runs also build downloadable Actions artifacts. Pushing a version tag publishes
the tested assets to GitHub Releases after all checks and builds pass:

```bash
# First commit and push the application and workflow to main.
# Both pyproject.toml and src/mntui/__init__.py must match this version.
git tag -a v0.1.0 -m "mntui 0.1.0"
git push origin v0.1.0
```

Release assets:

- `mntui-linux-x86_64`: onefile executable; run `chmod +x` after downloading.
- `mntui-linux-x86_64-standalone.tar.gz`: standalone directory, including its libraries.
- `LICENSE` and `SHA256SUMS`: license and SHA-256 checksums.

Builds use Ubuntu 22.04 x86_64 and Python 3.14.4 with pinned Python dependencies.
The standalone payload retained by Nuitka's onefile build supplies the archive,
so both formats are produced by one compilation. Other architectures and musl-based
hosts are not covered. Linux storage utilities remain host dependencies.

The workflow uses the repository's automatic `GITHUB_TOKEN`; no personal access
token is required. Only the publication job receives `contents: write`. A draft
release receives all assets before publication. Existing release assets are never
overwritten automatically; inspect or remove a failed draft before retrying its tag.
Repository or organization policies must allow GitHub Actions and release creation.

Verify downloads with `sha256sum --check SHA256SUMS`, then run
`./mntui-linux-x86_64 --check` before using the TUI.
