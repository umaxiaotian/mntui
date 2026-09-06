"""外部コマンドの期待される失敗を検証する。"""

import subprocess
from unittest.mock import Mock

import pytest

from mntui.commands.runner import CommandRunner
from mntui.errors import CommandExecutionError, CommandNotFoundError, CommandTimeoutError


def test_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(CommandNotFoundError):
        CommandRunner().run(["absent"])


@pytest.mark.parametrize(
    "error,expected",
    [
        (FileNotFoundError(), CommandNotFoundError),
        (PermissionError(), CommandExecutionError),
        (subprocess.TimeoutExpired("x", 1), CommandTimeoutError),
    ],
)
def test_os_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/bin/x")
    monkeypatch.setattr("subprocess.run", Mock(side_effect=error))
    with pytest.raises(expected):
        CommandRunner().run(["x"])


def test_arguments_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/bin/x")
    run = Mock(return_value=subprocess.CompletedProcess(["x"], 1, "out", "busy"))
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(CommandExecutionError, match="busy"):
        CommandRunner().run(["x", "; rm -rf /"])
    assert run.call_args.args[0] == ["x", "; rm -rf /"]
    assert not run.call_args.kwargs.get("shell", False)
