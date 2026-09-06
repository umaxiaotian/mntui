"""外部プロセスの実行、時間制限、例外変換を一元管理する。"""

import logging
import os
import shutil
import subprocess

from mntui.errors import CommandExecutionError, CommandNotFoundError, CommandTimeoutError
from mntui.models.domain import CommandResult

logger = logging.getLogger(__name__)


class CommandRunner:
    """シェルを使用せずLinuxコマンドを呼び出す。"""

    def require(self, names: tuple[str, ...]) -> None:
        """実行前に必要な全コマンドの存在を検証する。

        Args:
            names: 必須コマンド名。

        Raises:
            CommandNotFoundError: コマンドが不足している場合。
        """
        missing = [name for name in names if shutil.which(name) is None]
        if missing:
            raise CommandNotFoundError(f"Missing commands: {', '.join(missing)}; run mntui --check")

    def run(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
        timeout: int = 30,
        check: bool = True,
    ) -> CommandResult:
        """コマンドを実行し、期待される失敗をアプリケーション例外へ変換する。

        Args:
            arguments: 実行ファイル名と引数。
            input_text: 標準入力へ送信する内容。
            timeout: 最大待機秒数。
            check: 非ゼロ終了を例外に変換するかどうか。

        Returns:
            コマンドの出力と終了状態。

        Raises:
            CommandNotFoundError: 実行ファイルが存在しない場合。
            CommandTimeoutError: 制限時間を超えた場合。
            CommandExecutionError: 権限不足または実行失敗の場合。
        """
        if not arguments:
            raise CommandExecutionError("Empty command")
        self.require((arguments[0],))
        logger.info("command=%r", arguments)
        try:
            process = subprocess.run(
                arguments,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError as exc:
            raise CommandNotFoundError(f"Command disappeared: {arguments[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(
                f"{arguments[0]} timed out; inspect disk state before retrying"
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise CommandExecutionError(f"Cannot execute {arguments[0]}: {exc}") from exc
        result = CommandResult(
            arguments=tuple(arguments),
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
        )
        logger.info("command=%s exit=%s stderr=%s", arguments[0], result.returncode, result.stderr)
        if check and result.returncode:
            raise CommandExecutionError(
                f"{arguments[0]} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result
