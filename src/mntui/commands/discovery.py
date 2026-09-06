"""lsblkとfindmntのJSONからデバイス構成と使用状態を取得する。"""

import os
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mntui.commands.runner import CommandRunner
from mntui.errors import CommandExecutionError
from mntui.models.domain import BlockDevice, Disk, Filesystem, Partition


class _LsblkNode(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    name: str
    path: Path
    type: str
    major_minor: str = Field(alias="maj:min")
    size: int
    model: str | None = None
    serial: str | None = None
    fstype: str | None = None
    uuid: str | None = None
    label: str | None = None
    mountpoints: list[str | None] = Field(default_factory=list)
    pttype: str | None = None
    rm: bool = False
    ro: bool = False
    children: list["_LsblkNode"] = Field(default_factory=list)


class _LsblkOutput(BaseModel):
    blockdevices: list[_LsblkNode]


class MountRecord(BaseModel):
    """カーネルが報告するマウント元とマウント先を表す。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    source: str
    target: str
    major_minor: str = Field(alias="maj:min")


class _FindmntOutput(BaseModel):
    filesystems: list[MountRecord]


def read_mounts(runner: CommandRunner) -> list[MountRecord]:
    """現在のマウント一覧を平坦なJSONとして取得する。

    Args:
        runner: コマンド実行器。

    Returns:
        マウント一覧。

    Raises:
        CommandExecutionError: 出力の解析に失敗した場合。
    """
    result = runner.run(["findmnt", "--json", "--list", "--output", "SOURCE,TARGET,MAJ:MIN"])
    try:
        return _FindmntOutput.model_validate_json(result.stdout).filesystems
    except ValidationError as exc:
        raise CommandExecutionError("Malformed findmnt output; refusing operations") from exc


def walk(device: BlockDevice) -> tuple[BlockDevice, ...]:
    """デバイスと全子孫を順に列挙する。

    Args:
        device: 起点デバイス。

    Returns:
        起点を含む全デバイス。
    """
    return (device,) + tuple(child for item in device.children for child in walk(item))


def parse_devices(
    output: str, mounts: list[MountRecord], swap_ids: frozenset[str] = frozenset()
) -> tuple[Disk, ...]:
    """JSONを検証し、システム使用状態を親ディスクへ伝播する。

    Args:
        output: lsblkのJSON出力。
        mounts: カーネルのマウント一覧。
        swap_ids: 使用中スワップを格納するデバイス識別子。

    Returns:
        下位デバイスを含む物理ディスク一覧。

    Raises:
        CommandExecutionError: JSONまたは必須フィールドが不正な場合。
    """
    protected = {"/", "/boot", "/boot/efi", "/usr", "/var", "/etc"}
    system_ids = {mount.major_minor for mount in mounts if mount.target in protected} | swap_ids

    def _convert(node: _LsblkNode) -> BlockDevice:
        children = tuple(_convert(child) for child in node.children)
        points = tuple(point for point in node.mountpoints if point)
        cls = Disk if node.type == "disk" else Partition if node.type == "part" else BlockDevice
        return cls(
            name=node.name,
            path=node.path,
            type=node.type,
            major_minor=node.major_minor,
            size=node.size,
            model=node.model,
            serial=node.serial,
            filesystem=Filesystem(type=node.fstype, uuid=node.uuid, label=node.label)
            if node.fstype
            else None,
            mount_points=points,
            partition_table=node.pttype,
            removable=node.rm,
            read_only=node.ro,
            is_system_disk=node.major_minor in system_ids
            or bool(set(points) & protected)
            or "[SWAP]" in points
            or any(child.is_system_disk for child in children),
            children=children,
        )

    try:
        nodes = _LsblkOutput.model_validate_json(output)
        return tuple(
            Disk.model_validate(_convert(node).model_dump())
            for node in nodes.blockdevices
            if node.type == "disk"
        )
    except (ValidationError, ValueError) as exc:
        raise CommandExecutionError("Malformed lsblk output; refusing operations") from exc


class Discovery:
    """実行直前にも再利用できる読み取り専用の検出アダプター。"""

    def __init__(self, runner: CommandRunner) -> None:
        """実行器を保持する。

        Args:
            runner: コマンド実行器。
        """
        self.runner = runner

    def disks(self) -> tuple[Disk, ...]:
        """物理ディスクと現在のマウント状態を再取得する。

        Returns:
            検証済みディスク一覧。
        """
        mounts = read_mounts(self.runner)
        result = self.runner.run(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--output",
                "NAME,PATH,TYPE,MAJ:MIN,SIZE,MODEL,SERIAL,FSTYPE,UUID,LABEL,MOUNTPOINTS,PTTYPE,RM,RO",
            ]
        )
        disks = parse_devices(result.stdout, mounts)
        try:
            swap_ids = active_swap_devices()
        except (OSError, ValueError):
            # スワップ使用状態が不明なら全ディスクを保護する。
            return tuple(disk.model_copy(update={"is_system_disk": True}) for disk in disks)
        return parse_devices(result.stdout, mounts, swap_ids)


def active_swap_devices(path: Path = Path("/proc/swaps")) -> frozenset[str]:
    """スワップファイルとスワップデバイスの格納先を識別する。

    Args:
        path: カーネルのスワップ一覧。

    Returns:
        使用中スワップのデバイス識別子。

    Raises:
        OSError: 一覧または使用中のパスを読めない場合。
        ValueError: 一覧が不正な場合。
    """
    identities = set()
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("Filename"):
        raise ValueError("Malformed swap list")
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5:
            raise ValueError("Malformed swap entry")
        filename = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[0])
        info = Path(filename).stat()
        device_id = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        identities.add(f"{os.major(device_id)}:{os.minor(device_id)}")
    return frozenset(identities)
