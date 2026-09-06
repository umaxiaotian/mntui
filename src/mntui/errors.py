"""利用者に表示できるアプリケーション例外を定義する。"""


class MntuiError(Exception):
    """操作を安全に中止し、理由を利用者に通知する例外。"""


class CommandNotFoundError(MntuiError):
    """必要な外部コマンドが存在しない場合の例外。"""


class CommandExecutionError(MntuiError):
    """外部コマンドが失敗した場合の例外。"""


class CommandTimeoutError(CommandExecutionError):
    """外部コマンドが制限時間を超えた場合の例外。"""


class UnsafeOperationError(MntuiError):
    """安全条件を満たさない操作を拒否する例外。"""
