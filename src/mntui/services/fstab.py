"""fstabの検証、重複検出、バックアップ付き更新を提供する。"""

import fcntl
import os
import re
import shutil
import tempfile
from pathlib import Path

from mntui.commands.runner import CommandRunner
from mntui.errors import UnsafeOperationError
from mntui.models.domain import FstabEntry


def _decode(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def _encode(value: str) -> str:
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def parse_fstab(text: str) -> tuple[FstabEntry, ...]:
    """コメントを除く設定行を検証して読み取る。

    Args:
        text: 設定ファイルの内容。

    Returns:
        設定エントリー。

    Raises:
        UnsafeOperationError: 既存設定が不正な場合。
    """
    entries = []
    try:
        for line in text.splitlines():
            parts = line.split("#", 1)[0].split()
            if not parts:
                continue
            if not 4 <= len(parts) <= 6:
                raise ValueError("expected 4–6 fields")
            entries.append(
                FstabEntry(
                    source=_decode(parts[0]),
                    target=Path(_decode(parts[1])),
                    filesystem=parts[2],
                    options=parts[3],
                    dump=int(parts[4]) if len(parts) > 4 else 0,
                    pass_number=int(parts[5]) if len(parts) > 5 else 0,
                )
            )
    except ValueError as exc:
        raise UnsafeOperationError(f"Invalid existing fstab: {exc}") from exc
    return tuple(entries)


class FstabManager:
    """原子的な置換と検証失敗時の復旧で設定を保護する。"""

    def __init__(self, runner: CommandRunner, path: Path = Path("/etc/fstab")) -> None:
        """更新対象を設定する。

        Args:
            runner: 検証コマンドの実行器。
            path: 更新対象ファイル。
        """
        self.runner = runner
        self.path = path

    def proposed(self, entry: FstabEntry, aliases: tuple[str, ...] = ()) -> str:
        """既存行を保持し、競合がなければ追加後の内容を返す。

        Args:
            entry: 追加する設定。
            aliases: 同一ファイルシステムを表す別名。

        Returns:
            検証対象の全文。

        Raises:
            UnsafeOperationError: 同じデバイスやマウント先が既に設定されている場合。
        """
        if self.path.is_symlink() or not self.path.is_file():
            raise UnsafeOperationError("fstab must be an existing regular file, not a symlink")
        text = self.path.read_text()
        for existing in parse_fstab(text):
            if (
                existing.source in (entry.source, *aliases)
                or existing.target.resolve() == entry.target.resolve()
            ):
                raise UnsafeOperationError("fstab already contains this filesystem or mount point")
            if existing.source.startswith("/") and Path(existing.source).resolve() in {
                Path(alias).resolve() for alias in aliases if alias.startswith("/")
            }:
                raise UnsafeOperationError("fstab contains an alias of this device")
            if existing.source.startswith(("UUID=", "LABEL=", "PARTUUID=", "PARTLABEL=")):
                resolved = self.runner.run(
                    ["blkid", "-c", "/dev/null", "-t", existing.source, "-o", "device"],
                    check=False,
                )
                if resolved.returncode not in {0, 2}:
                    raise UnsafeOperationError("Cannot resolve existing fstab device safely")
                alias_paths = {Path(alias).resolve() for alias in aliases if alias.startswith("/")}
                if any(
                    Path(line).resolve() in alias_paths for line in resolved.stdout.splitlines()
                ):
                    raise UnsafeOperationError(
                        "fstab already contains this filesystem under another identifier"
                    )
        fields = (
            entry.source,
            str(entry.target),
            entry.filesystem,
            entry.options,
            str(entry.dump),
            str(entry.pass_number),
        )
        if any("\n" in field or "\r" in field or "#" in field for field in fields):
            raise UnsafeOperationError("Unsafe fstab field")
        return (
            text
            + ("\n" if text and not text.endswith("\n") else "")
            + ("\t".join(_encode(field) for field in fields) + "\n")
        )

    def update(self, entry: FstabEntry, aliases: tuple[str, ...] = ()) -> Path:
        """ロック、バックアップ、検証を伴って設定を更新する。

        Args:
            entry: 追加する設定。
            aliases: 同じデバイスの別名。

        Returns:
            保持されたバックアップファイルのパス。

        Raises:
            MntuiError: 検証に失敗して設定を復旧した場合。
        """
        self.runner.require(("findmnt", "blkid"))
        with self.path.with_name(self.path.name + ".mntui.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            proposed = self.proposed(entry, aliases)
            original = self.path.read_bytes()
            fd, backup_name = tempfile.mkstemp(
                prefix=self.path.name + ".backup-", dir=self.path.parent
            )
            os.close(fd)
            backup = Path(backup_name)
            shutil.copy2(self.path, backup)
            with backup.open("rb") as backup_stream:
                os.fsync(backup_stream.fileno())
            fd, candidate_name = tempfile.mkstemp(prefix=".mntui-", dir=self.path.parent)
            candidate = Path(candidate_name)
            replaced = False
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(proposed)
                    stream.flush()
                    os.fsync(stream.fileno())
                stat = self.path.stat()
                os.chown(candidate, stat.st_uid, stat.st_gid)
                shutil.copystat(self.path, candidate)
                self.runner.run(["findmnt", "--verify", "--tab-file", str(candidate)])
                if self.path.read_bytes() != original:
                    raise UnsafeOperationError("fstab changed concurrently; retry after inspection")
                os.replace(candidate, self.path)
                replaced = True
                self.runner.run(["findmnt", "--verify", "--tab-file", str(self.path)])
            except BaseException:
                if replaced:
                    shutil.copy2(backup, candidate)
                    os.replace(candidate, self.path)
                raise
            finally:
                candidate.unlink(missing_ok=True)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return backup
