"""操作計画、権限、ドライラン、安全条件を検証する。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from mntui.errors import CommandNotFoundError, UnsafeOperationError
from mntui.models.domain import OperationType
from mntui.services.storage import StorageService, validate_mount_point

TARGET = Path("/dev/test1")


def test_format_plan_and_confirmation(service: StorageService) -> None:
    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    assert plan.destructive and "mkfs.ext4" in plan.required_commands
    assert "ALL DATA" in service.describe(plan)
    with pytest.raises(UnsafeOperationError, match="Confirmation"):
        service.execute(plan, "yes")


def test_dry_run(service: StorageService, runner: Mock) -> None:
    service.dry_run = True
    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    assert service.execute(plan, str(TARGET))[0].dry_run
    assert all(call.args[0][0] != "mkfs.ext4" for call in runner.run.call_args_list)


def test_missing_dependency_prevents_any_write(service: StorageService, runner: Mock) -> None:
    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    runner.require.side_effect = CommandNotFoundError("missing")
    with pytest.raises(CommandNotFoundError):
        service.execute(plan, str(TARGET))
    assert all(call.args[0][0] != "mkfs.ext4" for call in runner.run.call_args_list)


@pytest.mark.parametrize(
    "change", [{"is_system_disk": True}, {"read_only": True}, {"mount_points": ("/data",)}]
)
def test_unsafe_disk(service: StorageService, change: dict[str, object]) -> None:
    disk = service.disks()[0].model_copy(update=change)
    service.discovery.disks = Mock(return_value=(disk,))
    with pytest.raises(UnsafeOperationError):
        service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")


def test_stale_plan(service: StorageService) -> None:
    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    disk = service.disks()[0].model_copy(update={"serial": "changed"})
    service.discovery.disks = Mock(return_value=(disk,))
    with pytest.raises(UnsafeOperationError, match="changed"):
        service.execute(plan, str(TARGET))


def test_unavailable_feature(service: StorageService) -> None:
    service.capabilities = service.capabilities.model_copy(
        update={"supported_filesystems": frozenset()}
    )
    with pytest.raises(UnsafeOperationError, match="unavailable"):
        service.plan(TARGET, OperationType.FORMAT, filesystem="xfs")


def test_root_required(service: StorageService, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    with pytest.raises(UnsafeOperationError, match="Root"):
        service.execute(plan, str(TARGET))


@pytest.mark.parametrize("value", ["relative", "/etc/data", "/", "/proc/test", "/tmp/../etc"])
def test_invalid_mount_point(value: str) -> None:
    with pytest.raises((UnsafeOperationError, ValueError)):
        validate_mount_point(Path(value), create=True)


def test_mount_directory_checks(tmp_path: Path) -> None:
    point = tmp_path / "data"
    with pytest.raises(UnsafeOperationError):
        validate_mount_point(point)
    assert validate_mount_point(point, create=True) == point
    point.mkdir()
    (point / "file").touch()
    with pytest.raises(UnsafeOperationError):
        validate_mount_point(point)
    link = tmp_path / "link"
    link.symlink_to(point)
    with pytest.raises(UnsafeOperationError):
        validate_mount_point(link)


def test_mount_plan_preflights_all_steps(service: StorageService, tmp_path: Path) -> None:
    point = tmp_path / "data"
    plan = service.plan(
        TARGET, OperationType.MOUNT, mount_point=point, create_directory=True, persistent=True
    )
    assert [step.type for step in plan.operations] == [
        OperationType.CREATE_MOUNT_POINT,
        OperationType.MOUNT,
        OperationType.UPDATE_FSTAB,
    ]
    service.dry_run = True
    service.execute(plan, "yes")
    assert not point.exists()
    assert service.fstab.path.read_text() == "# keep this comment\n"


def test_initialize_unused_only(service: StorageService) -> None:
    with pytest.raises(UnsafeOperationError, match="unused"):
        service.plan(Path("/dev/test"), OperationType.INITIALIZE)
    disk = service.disks()[0].model_copy(update={"children": ()})
    service.discovery.disks = Mock(return_value=(disk,))
    plan = service.plan(disk.path, OperationType.INITIALIZE)
    assert plan.destructive
    assert "sfdisk" in plan.required_commands


def test_multistep_dependency_failure(
    service: StorageService, runner: Mock, tmp_path: Path
) -> None:
    point = tmp_path / "data"
    plan = service.plan(
        TARGET, OperationType.MOUNT, mount_point=point, create_directory=True, persistent=True
    )
    runner.require.side_effect = CommandNotFoundError("mount disappeared")
    with pytest.raises(CommandNotFoundError):
        service.execute(plan, "yes")
    assert not point.exists()
    assert service.fstab.path.read_text() == "# keep this comment\n"


def test_format_executes_expected_command(
    service: StorageService, runner: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    original_stat = Path.stat

    def fake_stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == TARGET:
            return Mock(st_mode=stat.S_IFBLK, st_rdev=os.makedev(240, 1))
        return original_stat(path, *args, **kwargs)

    plan = service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")
    monkeypatch.setattr(Path, "stat", fake_stat)
    results = service.execute(plan, str(TARGET))
    assert results[0].message == "Completed"
    assert ["mkfs.ext4", "-F", str(TARGET)] in [call.args[0] for call in runner.run.call_args_list]


def test_unmount_executes_without_force(
    service: StorageService, runner: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    disk = service.disks()[0]
    partition = disk.children[0].model_copy(update={"mount_points": ("/example",)})
    service.discovery.disks = Mock(
        return_value=(disk.model_copy(update={"children": (partition,)}),)
    )
    plan = service.plan(TARGET, OperationType.UNMOUNT, mount_point=Path("/example"))
    original_stat = Path.stat

    def fake_stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == TARGET:
            return Mock(st_mode=stat.S_IFBLK, st_rdev=os.makedev(240, 1))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    service.execute(plan, "yes")
    assert ["umount", "--", "/example"] in [call.args[0] for call in runner.run.call_args_list]


def test_unknown_root_blocks_destruction(service: StorageService, runner: Mock) -> None:
    from mntui.models.domain import CommandResult

    runner.run.return_value = CommandResult(
        arguments=(),
        stdout='{"filesystems": [{"source":"overlay", "target":"/", "maj:min":"0:1"}]}',
        stderr="",
        returncode=0,
    )
    runner.run.side_effect = None
    with pytest.raises(UnsafeOperationError, match="root backing"):
        service.plan(TARGET, OperationType.FORMAT, filesystem="ext4")


@pytest.mark.parametrize("source,success", [("/dev/test1", True), ("/dev/wrong", False)])
def test_btrfs_mount_verification(
    service: StorageService, runner: Mock, tmp_path: Path, source: str, success: bool
) -> None:
    import json

    from mntui.errors import MntuiError
    from mntui.models.domain import CommandResult, Filesystem, StorageOperation

    point = tmp_path / "data"
    point.mkdir()
    device = (
        service.disks()[0]
        .children[0]
        .model_copy(update={"filesystem": Filesystem(type="btrfs", uuid="abc-123")})
    )
    original = runner.run.side_effect

    def run(arguments: list[str], **kwargs: object) -> CommandResult:
        if arguments[0] == "findmnt":
            return CommandResult(
                arguments=tuple(arguments),
                stdout=json.dumps(
                    {"filesystems": [{"source": source, "target": str(point), "maj:min": "0:42"}]}
                ),
                stderr="",
                returncode=0,
            )
        return original(arguments, **kwargs)

    runner.run.side_effect = run
    operation = StorageOperation(type=OperationType.MOUNT, device=TARGET, mount_point=point)
    if success:
        service._execute_operation(operation, device)
    else:
        with pytest.raises(MntuiError, match="verification"):
            service._execute_operation(operation, device)
