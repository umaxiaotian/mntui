"""実ディスクへ書き込まないテスト用アダプターを提供する。"""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from mntui.commands.runner import CommandRunner
from mntui.models.domain import CommandResult, Disk, Filesystem, Partition
from mntui.services.capabilities import detect_capabilities
from mntui.services.storage import StorageService


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """明示的な環境変数がない破壊的テストを無効にする。"""
    if os.environ.get("MNTUI_RUN_DESTRUCTIVE_TESTS") != "1":
        for item in items:
            if "destructive" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Requires MNTUI_RUN_DESTRUCTIVE_TESTS=1"))


@pytest.fixture
def runner() -> Mock:
    """成功する読み取りコマンドを模倣する。"""
    mock = Mock(spec=CommandRunner)

    def run(arguments: list[str], **kwargs: object) -> CommandResult:
        output = ""
        if arguments[0] == "findmnt":
            output = (
                '{"filesystems": [{"source": "/dev/system1", "target": "/", "maj:min": "8:1"}]}'
            )
        elif arguments[0] == "blkid":
            output = "/dev/test1\n"
        return CommandResult(arguments=tuple(arguments), stdout=output, stderr="", returncode=0)

    mock.run.side_effect = run
    return mock


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Mock) -> StorageService:
    """模擬ディスクと一時fstabを使用するサービスを構成する。"""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    fstab = tmp_path / "fstab"
    fstab.write_text("# keep this comment\n")
    service = StorageService(runner, detect_capabilities(), fstab_path=fstab)
    partition = Partition(
        name="test1",
        path=Path("/dev/test1"),
        major_minor="240:1",
        size=900,
        filesystem=Filesystem(type="ext4", uuid="abc-123"),
    )
    disk = Disk(
        name="test", path=Path("/dev/test"), major_minor="240:0", size=1000, children=(partition,)
    )
    service.discovery.disks = Mock(return_value=(disk,))
    return service
