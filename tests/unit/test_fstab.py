"""fstabの競合検出、原子的な更新、失敗時の復旧を検証する。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from mntui.errors import CommandExecutionError, UnsafeOperationError
from mntui.models.domain import FstabEntry
from mntui.services.fstab import FstabManager, parse_fstab


def test_parse_escapes() -> None:
    entries = parse_fstab("# comment\nUUID=abc /my\\040disk ext4 defaults 0 2\n")
    assert entries[0].target == Path("/my disk")


@pytest.mark.parametrize(
    "existing",
    [
        "UUID=abc /other ext4 defaults 0 2\n",
        "UUID=other /data ext4 defaults 0 2\n",
        "/dev/test1 /other ext4 defaults 0 2\n",
    ],
)
def test_duplicates(tmp_path: Path, runner: Mock, existing: str) -> None:
    path = tmp_path / "fstab"
    path.write_text(existing)
    manager = FstabManager(runner, path)
    with pytest.raises(UnsafeOperationError):
        manager.update(
            FstabEntry(source="UUID=abc", target=Path("/data"), filesystem="ext4"), ("/dev/test1",)
        )
    assert path.read_text() == existing


def test_update_and_backup(tmp_path: Path, runner: Mock) -> None:
    path = tmp_path / "fstab"
    original = "# retained\nUUID=root / ext4 defaults 0 1\n"
    path.write_text(original)
    path.chmod(0o640)
    backup = FstabManager(runner, path).update(
        FstabEntry(source="UUID=abc", target=Path("/data"), filesystem="ext4")
    )
    assert backup.read_text() == original
    assert path.read_text().startswith(original)
    assert path.stat().st_mode & 0o777 == 0o640
    assert sum(call.args[0][0] == "findmnt" for call in runner.run.call_args_list) == 2


@pytest.mark.parametrize("fail_at", [0, 1])
def test_validation_rollback(tmp_path: Path, runner: Mock, fail_at: int) -> None:
    path = tmp_path / "fstab"
    path.write_text("# original\n")
    runner.run.side_effect = (
        [CommandExecutionError("bad")] if fail_at == 0 else [None, CommandExecutionError("bad")]
    )
    with pytest.raises(CommandExecutionError):
        FstabManager(runner, path).update(
            FstabEntry(source="UUID=abc", target=Path("/data"), filesystem="ext4")
        )
    assert path.read_text() == "# original\n"
    assert len(list(tmp_path.glob("fstab.backup-*"))) == 1


def test_partition_uuid_alias(tmp_path: Path, runner: Mock) -> None:
    path = tmp_path / "fstab"
    path.write_text("PARTUUID=partition-uuid /old ext4 defaults 0 2\n")
    with pytest.raises(UnsafeOperationError, match="another identifier"):
        FstabManager(runner, path).proposed(
            FstabEntry(source="UUID=abc", target=Path("/new"), filesystem="ext4"),
            ("/dev/test1",),
        )
