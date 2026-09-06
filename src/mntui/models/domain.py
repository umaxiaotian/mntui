"""ディスク状態、ホスト機能、操作計画の検証済みモデルを定義する。"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    """未知のフィールドを拒否する共通モデル。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Filesystem(Model):
    """ファイルシステムの形式と永続識別子を表す。"""

    type: str
    uuid: str | None = None
    label: str | None = None


class MountPoint(Model):
    """絶対パスで指定するマウント先を表す。"""

    path: Path

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        """危険な相対パスや制御文字を拒否する。

        Args:
            value: 検証対象のパス。

        Returns:
            検証済みの絶対パス。
        """
        if (
            not value.is_absolute()
            or ".." in value.parts
            or any(ord(char) < 32 for char in str(value))
        ):
            raise ValueError(
                "Mount point must be an absolute path without '..' or control characters"
            )
        return value


class BlockDevice(Model):
    """ブロックデバイスと、その下位デバイスを表す。"""

    name: str
    path: Path
    type: str
    major_minor: str
    size: int = Field(ge=0)
    model: str | None = None
    serial: str | None = None
    filesystem: Filesystem | None = None
    mount_points: tuple[str, ...] = ()
    partition_table: str | None = None
    removable: bool = False
    read_only: bool = False
    is_system_disk: bool = False
    children: tuple["BlockDevice", ...] = ()


class Partition(BlockDevice):
    """ディスク上のパーティションを表す。"""

    type: str = "part"


class Disk(BlockDevice):
    """物理ディスクと配下の使用状態を表す。"""

    type: str = "disk"


class FstabEntry(Model):
    """永続マウント設定の一行を表す。"""

    source: str
    target: Path
    filesystem: str
    options: str = "defaults,nofail"
    dump: int = 0
    pass_number: int = 0


class OperationType(StrEnum):
    """MVPで実行できる操作の種類を定義する。"""

    INITIALIZE = "create_gpt_and_partition"
    FORMAT = "format_filesystem"
    CREATE_MOUNT_POINT = "create_mount_point"
    UPDATE_FSTAB = "update_fstab"
    MOUNT = "mount_filesystem"
    UNMOUNT = "unmount_filesystem"


class StorageOperation(Model):
    """実行対象と操作パラメータを保持する。"""

    type: OperationType
    device: Path
    filesystem: str | None = None
    mount_point: Path | None = None


class StoragePlan(Model):
    """表示した操作と、作成時点のデバイス状態を保持する。"""

    target: Path
    snapshot: str
    operations: tuple[StorageOperation, ...]
    required_commands: tuple[str, ...]
    destructive: bool = False


class CommandResult(Model):
    """コマンド引数と標準出力、終了状態を保持する。"""

    arguments: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int


class OperationResult(Model):
    """完了した操作またはドライランの結果を表す。"""

    operation: StorageOperation
    message: str
    dry_run: bool = False


class CommandAvailability(Model):
    """外部コマンドの検出結果とパッケージ案内を表す。"""

    name: str
    path: Path | None
    available: bool
    required: bool
    category: str
    package: str


class SystemCapabilities(Model):
    """画面とサービスが利用できるホスト機能を表す。"""

    commands: tuple[CommandAvailability, ...]
    distribution: str
    can_discover_disks: bool
    can_mount: bool
    can_unmount: bool
    can_partition: bool
    supported_filesystems: frozenset[str]
    smart_supported: bool
    core_available: bool
