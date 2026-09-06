"""依存不足、機能無効化、導入案内を検証する。"""

from pathlib import Path

import pytest

from mntui.cli import main
from mntui.services.capabilities import detect_capabilities, detect_distribution, package_hint


def test_optional_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: None if name in {"mkfs.xfs", "smartctl"} else f"/bin/{name}"
    )
    capabilities = detect_capabilities()
    assert capabilities.core_available
    assert capabilities.supported_filesystems == {"ext4", "btrfs"}
    assert not capabilities.smart_supported
    assert main(["--check"]) == 0


def test_core_missing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert main(["--check"]) == 2
    assert "Missing core dependencies" in capsys.readouterr().out


@pytest.mark.parametrize(
    "distro,manager",
    [
        ("debian", "apt"),
        ("ubuntu", "apt"),
        ("fedora", "dnf"),
        ("rhel", "dnf"),
        ("rocky", "dnf"),
        ("almalinux", "dnf"),
        ("arch", "pacman"),
        ("opensuse", "zypper"),
        ("alpine", "apk"),
    ],
)
def test_distribution_hints(tmp_path: Path, distro: str, manager: str) -> None:
    path = tmp_path / "os-release"
    path.write_text(f'NAME="example"\nID="{distro}"\n')
    assert detect_distribution(path) == distro
    assert manager in package_hint("mkfs.ext4", distro)


def test_unknown_distribution(tmp_path: Path) -> None:
    assert detect_distribution(tmp_path / "missing") == "unknown"
    assert package_hint("sfdisk", "unknown") == "util-linux"
    assert "fdisk" in package_hint("sfdisk", "debian")
