"""外部コマンドの検出とディストリビューション別の案内を提供する。"""

import shlex
import shutil
from pathlib import Path

from mntui.models.domain import CommandAvailability, SystemCapabilities

COMMANDS = {
    "lsblk": ("Core", "util-linux"),
    "blkid": ("Core", "util-linux"),
    "findmnt": ("Core", "util-linux"),
    "mount": ("Core", "util-linux"),
    "umount": ("Core", "util-linux"),
    "sfdisk": ("Partitioning", "util-linux"),
    "mkfs.ext4": ("Filesystems", "e2fsprogs"),
    "mkfs.xfs": ("Filesystems", "xfsprogs"),
    "mkfs.btrfs": ("Filesystems", "btrfs-progs"),
    "smartctl": ("Optional", "smartmontools"),
    "wipefs": ("Optional", "util-linux"),
}


def detect_distribution(path: Path = Path("/etc/os-release")) -> str:
    """os-releaseをコードとして実行せず読み取る。

    Args:
        path: ディストリビューション情報ファイル。

    Returns:
        IDの値。不明の場合はunknown。
    """
    try:
        for line in path.read_text().splitlines():
            if line.startswith("ID="):
                values = shlex.split(line[3:])
                return values[0] if len(values) == 1 else "unknown"
    except (OSError, ValueError, UnicodeError):
        pass
    return "unknown"


def package_hint(command: str, distribution: str) -> str:
    """ホストに変更を加えず、典型的な提供パッケージを案内する。

    Args:
        command: コマンド名。
        distribution: os-releaseのID。

    Returns:
        パッケージ名と、既知の環境に対するインストール例。
    """
    package = COMMANDS[command][1]
    if distribution in {"debian", "ubuntu"}:
        package = {"sfdisk": "fdisk", "mount": "mount", "umount": "mount"}.get(command, package)
    managers = {
        "debian": "apt install",
        "ubuntu": "apt install",
        "fedora": "dnf install",
        "rhel": "dnf install",
        "rocky": "dnf install",
        "almalinux": "dnf install",
        "arch": "pacman -S",
        "opensuse": "zypper install",
        "opensuse-leap": "zypper install",
        "opensuse-tumbleweed": "zypper install",
        "alpine": "apk add",
    }
    manager = managers.get(distribution)
    return f"{package} (administrator: {manager} {package})" if manager else package


def detect_capabilities() -> SystemCapabilities:
    """ホスト機能を一度検出し、画面用の型付き状態へ変換する。

    Returns:
        コマンド一覧と利用可能な機能。
    """
    distribution = detect_distribution()
    commands = []
    for name, (category, _) in COMMANDS.items():
        found = shutil.which(name)
        commands.append(
            CommandAvailability(
                name=name,
                path=Path(found) if found else None,
                available=found is not None,
                required=category == "Core",
                category=category,
                package=package_hint(name, distribution),
            )
        )
    available = {item.name for item in commands if item.available}
    return SystemCapabilities(
        commands=tuple(commands),
        distribution=distribution,
        can_discover_disks={"lsblk", "findmnt"} <= available,
        can_mount={"mount", "findmnt", "blkid"} <= available,
        can_unmount={"umount", "findmnt"} <= available,
        can_partition="sfdisk" in available,
        supported_filesystems=frozenset(
            fs for fs in ("ext4", "xfs", "btrfs") if f"mkfs.{fs}" in available
        ),
        smart_supported="smartctl" in available,
        core_available=all(item.available for item in commands if item.required),
    )


def capability_report(capabilities: SystemCapabilities) -> str:
    """不足している機能と導入案内を端末向けに整形する。

    Args:
        capabilities: 検出済みホスト機能。

    Returns:
        人間が読める診断レポート。
    """
    lines = ["mntui Environment Check", f"Distribution: {capabilities.distribution}"]
    for category in ("Core", "Partitioning", "Filesystems", "Optional"):
        lines.extend(["", category])
        for command in capabilities.commands:
            if command.category == category:
                lines.append(
                    f"  {'✓' if command.available else '✗'} {command.name}"
                    + (f" — {command.package}" if not command.available else "")
                )
    lines.extend(
        [
            "",
            "Status: "
            + (
                "mntui can run."
                if capabilities.core_available
                else "Missing core dependencies; cannot run safely."
            ),
        ]
    )
    missing = [
        item.name for item in capabilities.commands if not item.available and not item.required
    ]
    if missing:
        lines.append("Unavailable features (required command): " + ", ".join(missing))
    return "\n".join(lines)
