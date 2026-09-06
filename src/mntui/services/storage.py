"""利用者の意図を検証済み計画へ変換し、バックエンド操作を統括する。"""

import logging
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path

from mntui.commands.discovery import Discovery, read_mounts, walk
from mntui.commands.runner import CommandRunner
from mntui.errors import MntuiError, UnsafeOperationError
from mntui.models.domain import (
    BlockDevice,
    Disk,
    FstabEntry,
    MountPoint,
    OperationResult,
    OperationType,
    StorageOperation,
    StoragePlan,
    SystemCapabilities,
)
from mntui.services.fstab import FstabManager

logger = logging.getLogger(__name__)


def validate_mount_point(path: Path, *, create: bool = False) -> Path:
    """システム領域、リンク、空でないディレクトリーを拒否する。

    Args:
        path: 要求されたマウント先。
        create: 存在しないディレクトリーを許可するかどうか。

    Returns:
        検証済みの絶対パス。

    Raises:
        UnsafeOperationError: 安全にマウントできない場合。
    """
    MountPoint(path=path)
    forbidden = {
        "/",
        "/boot",
        "/etc",
        "/usr",
        "/var",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/dev",
        "/proc",
        "/sys",
        "/run",
    }
    if any(str(parent) in forbidden - {"/"} for parent in (path, *path.parents)) or path == Path(
        "/"
    ):
        raise UnsafeOperationError("Mounting over a system directory is blocked")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise UnsafeOperationError("Mount point must not traverse symlinks")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()) or path.is_mount():
            raise UnsafeOperationError("Mount point must be an empty, unmounted directory")
    elif not create:
        raise UnsafeOperationError(
            "Mount point does not exist; explicitly request directory creation"
        )
    return path


class StorageService:
    """画面からコマンドを分離し、計画単位で安全条件を検証する。"""

    def __init__(
        self,
        runner: CommandRunner,
        capabilities: SystemCapabilities,
        *,
        dry_run: bool = False,
        fstab_path: Path = Path("/etc/fstab"),
    ) -> None:
        """ホストアダプターと実行モードを設定する。

        Args:
            runner: コマンド実行器。
            capabilities: 検出済み機能。
            dry_run: 変更を実行しないモード。
            fstab_path: 永続マウント設定のパス。
        """
        self.runner = runner
        self.capabilities = capabilities
        self.discovery = Discovery(runner)
        self.fstab = FstabManager(runner, fstab_path)
        self.dry_run = dry_run

    def disks(self) -> tuple[Disk, ...]:
        """画面用のディスク状態を取得する。

        Returns:
            現在の物理ディスク一覧。
        """
        return self.discovery.disks()

    def _locate(self, path: Path) -> tuple[Disk, BlockDevice]:
        for disk in self.disks():
            for device in walk(disk):
                if device.path == path:
                    return disk, device
        raise UnsafeOperationError(f"Device no longer exists: {path}")

    def _safe(self, disk: Disk, device: BlockDevice, destructive: bool) -> None:
        if disk.is_system_disk or device.is_system_disk:
            raise UnsafeOperationError("Operations on running-system disks are blocked")
        if disk.read_only or device.read_only:
            raise UnsafeOperationError("Device is read-only")
        if device.type not in {"disk", "part"}:
            raise UnsafeOperationError("Only ordinary disks and partitions are supported")
        if any(child.type not in {"disk", "part"} for child in walk(disk)):
            raise UnsafeOperationError("LVM, RAID, encrypted or layered devices are not supported")
        if destructive:
            if any(child.mount_points for child in walk(disk)):
                raise UnsafeOperationError(
                    "Unmount all filesystems on this disk before erasing data"
                )
            mounts = read_mounts(self.runner)
            if any(
                mount.major_minor in {child.major_minor for child in walk(disk)} for mount in mounts
            ):
                raise UnsafeOperationError("Disk has an active mount")
            # 別名経由の使用やカーネルのホルダーも保守的に拒否する。
            for child in walk(disk):
                holders = Path("/sys/dev/block") / child.major_minor / "holders"
                if holders.exists() and any(holders.iterdir()):
                    raise UnsafeOperationError("Device has active kernel holders")
            root = next((mount for mount in mounts if mount.target == "/"), None)
            if root is None or root.major_minor.startswith("0:"):
                raise UnsafeOperationError(
                    "Cannot resolve the root backing disk; destructive actions blocked"
                )

    def _entry(self, device: BlockDevice, target: Path) -> tuple[FstabEntry, tuple[str, ...]]:
        fs = device.filesystem
        if fs is None or not fs.uuid or not re.fullmatch(r"[A-Za-z0-9-]+", fs.uuid):
            raise UnsafeOperationError("A valid filesystem UUID is required")
        if fs.type not in {"ext4", "xfs", "btrfs"}:
            raise UnsafeOperationError("Only ext4, XFS and Btrfs mounts are supported in this MVP")
        result = self.runner.run(
            ["blkid", "-c", "/dev/null", "-t", f"UUID={fs.uuid}", "-o", "device"]
        )
        paths = {Path(line).resolve() for line in result.stdout.splitlines() if line}
        if paths != {device.path.resolve()}:
            raise UnsafeOperationError("Filesystem UUID is ambiguous or stale; refresh discovery")
        aliases = [str(device.path), f"/dev/disk/by-uuid/{fs.uuid}"]
        if fs.label:
            aliases.extend([f"LABEL={fs.label}", f"/dev/disk/by-label/{fs.label}"])
        return FstabEntry(
            source=f"UUID={fs.uuid}",
            target=target,
            filesystem=fs.type,
            pass_number=2 if fs.type == "ext4" else 0,
        ), tuple(aliases)

    def plan(
        self,
        target: Path,
        action: OperationType,
        *,
        filesystem: str | None = None,
        mount_point: Path | None = None,
        create_directory: bool = False,
        persistent: bool = False,
    ) -> StoragePlan:
        """安全条件と依存コマンドを検証し、表示可能な計画を作成する。

        Args:
            target: 対象デバイス。
            action: 要求する主要操作。
            filesystem: 作成するファイルシステム。
            mount_point: マウントまたはアンマウント先。
            create_directory: ディレクトリー作成を許可するかどうか。
            persistent: fstabへ設定を追加するかどうか。

        Returns:
            実行前の状態を保持した操作計画。

        Raises:
            UnsafeOperationError: 安全条件を満たさない場合。
        """
        disk, device = self._locate(target)
        destructive = action in {OperationType.INITIALIZE, OperationType.FORMAT}
        self._safe(disk, device, destructive)
        operations: list[StorageOperation] = []
        required = {"lsblk", "findmnt", "blkid"}
        if action == OperationType.INITIALIZE:
            if (
                device.type != "disk"
                or device.children
                or device.filesystem
                or device.partition_table
            ):
                raise UnsafeOperationError(
                    "Initialization is limited to unused, unpartitioned disks"
                )
            if not self.capabilities.can_partition:
                raise UnsafeOperationError(
                    "GPT creation unavailable; install sfdisk (mntui --check)"
                )
            required.add("sfdisk")
        elif action == OperationType.FORMAT:
            if device.type != "part" or device.children:
                raise UnsafeOperationError("Format only a plain partition")
            if filesystem not in self.capabilities.supported_filesystems:
                raise UnsafeOperationError(
                    f"Filesystem creation unavailable: {filesystem}; run mntui --check"
                )
            required.add(f"mkfs.{filesystem}")
        elif action == OperationType.MOUNT:
            if mount_point is None or device.mount_points:
                raise UnsafeOperationError("Choose an unmounted filesystem and a mount point")
            validate_mount_point(mount_point, create=create_directory)
            entry, aliases = self._entry(device, mount_point)
            required.add("mount")
            if persistent:
                self.fstab.proposed(entry, aliases)
            if not mount_point.exists():
                operations.append(
                    StorageOperation(
                        type=OperationType.CREATE_MOUNT_POINT,
                        device=target,
                        mount_point=mount_point,
                    )
                )
        elif action == OperationType.UNMOUNT:
            if mount_point is None or str(mount_point) not in device.mount_points:
                raise UnsafeOperationError("Choose an active mount point for this filesystem")
            if any(
                m.target.startswith(str(mount_point).rstrip("/") + "/")
                for m in read_mounts(self.runner)
            ):
                raise UnsafeOperationError("Unmount nested mounts first")
            required.add("umount")
        else:
            raise UnsafeOperationError("Unsupported primary operation")
        operations.append(
            StorageOperation(
                type=action, device=target, filesystem=filesystem, mount_point=mount_point
            )
        )
        if persistent:
            if action != OperationType.MOUNT:
                raise UnsafeOperationError("Persistence is only supported for mounting")
            operations.append(
                StorageOperation(
                    type=OperationType.UPDATE_FSTAB, device=target, mount_point=mount_point
                )
            )
        self.runner.require(tuple(sorted(required)))
        return StoragePlan(
            target=target,
            snapshot=disk.model_dump_json(),
            operations=tuple(operations),
            required_commands=tuple(sorted(required)),
            destructive=destructive,
        )

    def describe(self, plan: StoragePlan) -> str:
        """実行内容と取り消せない操作の警告を整形する。

        Args:
            plan: 表示対象の計画。

        Returns:
            コマンドと警告を含む確認文。
        """
        lines = ["Planned operations" + (" [DRY RUN]" if self.dry_run else "")]
        _, device = self._locate(plan.target)
        for index, operation in enumerate(plan.operations, 1):
            command = self._command(operation, device)
            detail = " ".join(command) if command else f"{operation.type}: {operation.mount_point}"
            lines.append(f"{index}. {detail}")
            if operation.type == OperationType.INITIALIZE:
                lines.append("   stdin: label: gpt; one Linux partition using all available space")
        if plan.destructive:
            lines.append(f"WARNING: ALL DATA ON {plan.target} WILL BE DESTROYED.")
        lines.append("Completed steps cannot be rolled back if a later step fails.")
        return "\n".join(lines)

    def _command(self, operation: StorageOperation, device: BlockDevice) -> list[str]:
        match operation.type:
            case OperationType.INITIALIZE:
                return ["sfdisk", "--lock=yes", "--wipe", "always", str(operation.device)]
            case OperationType.FORMAT:
                flags = ["-F"] if operation.filesystem == "ext4" else ["-f"]
                return [f"mkfs.{operation.filesystem}", *flags, str(operation.device)]
            case OperationType.MOUNT:
                entry, _ = self._entry(device, self._point(operation))
                return ["mount", "--source", entry.source, "--target", str(entry.target)]
            case OperationType.UNMOUNT:
                return ["umount", "--", str(self._point(operation))]
            case _:
                return []

    def _point(self, operation: StorageOperation) -> Path:
        if operation.mount_point is None:
            raise UnsafeOperationError("Missing mount point")
        return operation.mount_point

    def execute(
        self,
        plan: StoragePlan,
        confirmation: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[OperationResult, ...]:
        """確認済み計画を再検証して順番に実行する。

        Args:
            plan: 表示および確認した計画。
            confirmation: 破壊操作ではデバイス絶対パス、その他ではyes。
            progress: 各操作の開始通知先。

        Returns:
            完了した操作結果。

        Raises:
            UnsafeOperationError: 確認不足、状態変化、権限不足の場合。
            MntuiError: 操作に失敗した場合。後続操作は実行しない。
        """
        if confirmation != (str(plan.target) if plan.destructive else "yes"):
            raise UnsafeOperationError("Confirmation did not match; nothing changed")
        if not self.dry_run and os.geteuid() != 0:
            raise UnsafeOperationError("Root privileges required; restart using sudo mntui")
        # 呼び出し側が計画オブジェクトを加工しても検証を回避できないよう再生成する。
        main = next(
            (
                op
                for op in plan.operations
                if op.type
                in {
                    OperationType.INITIALIZE,
                    OperationType.FORMAT,
                    OperationType.MOUNT,
                    OperationType.UNMOUNT,
                }
            ),
            None,
        )
        if main is None:
            raise UnsafeOperationError("Empty or invalid plan")
        fresh = self.plan(
            plan.target,
            main.type,
            filesystem=main.filesystem,
            mount_point=main.mount_point,
            create_directory=any(
                op.type == OperationType.CREATE_MOUNT_POINT for op in plan.operations
            ),
            persistent=any(op.type == OperationType.UPDATE_FSTAB for op in plan.operations),
        )
        if fresh != plan:
            raise UnsafeOperationError(
                "Device state or plan changed; refresh and confirm a new plan"
            )
        self.runner.require(plan.required_commands)
        _, device = self._locate(plan.target)
        if not self.dry_run:
            device_stat = device.path.stat()
            identity = f"{os.major(device_stat.st_rdev)}:{os.minor(device_stat.st_rdev)}"
            if not stat.S_ISBLK(device_stat.st_mode) or identity != device.major_minor:
                raise UnsafeOperationError("Target is not the expected block device")
        results = []
        for operation in plan.operations:
            if progress:
                progress(f"{'Preview' if self.dry_run else 'Running'}: {operation.type}")
            logger.info(
                "operation=%s target=%s dry_run=%s", operation.type, plan.target, self.dry_run
            )
            if self.dry_run:
                results.append(
                    OperationResult(operation=operation, message="Would execute", dry_run=True)
                )
                continue
            try:
                self._execute_operation(operation, device)
            except (MntuiError, OSError) as exc:
                raise MntuiError(
                    f"Stopped at {operation.type}: {exc}. Completed {len(results)} step(s); "
                    "inspect current state before retrying."
                ) from exc
            logger.info("operation=%s target=%s result=success", operation.type, plan.target)
            results.append(OperationResult(operation=operation, message="Completed"))
        return tuple(results)

    def _execute_operation(self, operation: StorageOperation, device: BlockDevice) -> None:
        if operation.type == OperationType.CREATE_MOUNT_POINT:
            point = validate_mount_point(self._point(operation), create=True)
            point.mkdir(parents=True, exist_ok=False)
        elif operation.type == OperationType.UPDATE_FSTAB:
            entry, aliases = self._entry(device, self._point(operation))
            self.fstab.update(entry, aliases)
        else:
            if operation.type == OperationType.MOUNT:
                validate_mount_point(self._point(operation))
            if operation.type == OperationType.UNMOUNT:
                point = self._point(operation)
                current = Path.cwd()
                if current == point or point in current.parents:
                    raise UnsafeOperationError(
                        "mntui working directory is on this mount; change directory first"
                    )
            self.runner.run(
                self._command(operation, device),
                input_text="label: gpt\n, , L\n"
                if operation.type == OperationType.INITIALIZE
                else None,
                timeout=600 if operation.type == OperationType.FORMAT else 30,
            )
            if operation.type in {OperationType.MOUNT, OperationType.UNMOUNT}:
                matches = [
                    mount
                    for mount in read_mounts(self.runner)
                    if mount.target == str(operation.mount_point)
                ]
                if operation.type == OperationType.MOUNT:
                    if len(matches) != 1:
                        raise MntuiError("Mount verification failed")
                    mount = matches[0]
                    # Btrfsの匿名デバイス番号は物理デバイスの番号と一致しない。
                    btrfs_source_matches = (
                        device.filesystem is not None
                        and device.filesystem.type == "btrfs"
                        and Path(mount.source.split("[", 1)[0]).resolve() == device.path.resolve()
                    )
                    if mount.major_minor != device.major_minor and not btrfs_source_matches:
                        raise MntuiError("Mount verification failed")
                elif matches:
                    raise MntuiError("Unmount verification failed")

            elif operation.type == OperationType.FORMAT:
                _, updated = self._locate(device.path)
                if updated.filesystem is None or updated.filesystem.type != operation.filesystem:
                    raise MntuiError("Filesystem verification failed; refresh before retrying")
            elif operation.type == OperationType.INITIALIZE:
                _, updated = self._locate(device.path)
                if updated.partition_table != "gpt" or len(updated.children) != 1:
                    raise MntuiError("Partition verification failed; refresh before retrying")
