"""ディスクを変更せず端末画面の起動と終了を検証する。"""

import asyncio
from unittest.mock import Mock

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mntui.services.storage import StorageService
from mntui.ui.app import run_ui


def test_ui_starts_and_quits(service: StorageService) -> None:
    service.discovery.disks = Mock(return_value=())

    async def scenario() -> None:
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            task = asyncio.create_task(run_ui(service))
            await asyncio.sleep(0.1)
            pipe.send_text("q")
            await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())


def test_tab_reaches_confirm_button() -> None:
    from prompt_toolkit.shortcuts import radiolist_dialog

    from mntui.ui.app import _keys

    async def scenario() -> None:
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            app = _keys(radiolist_dialog(title="Test", values=[("selected", "Example")]))
            task = asyncio.create_task(app.run_async())
            await asyncio.sleep(0.1)
            pipe.send_text("\t\r")
            assert await asyncio.wait_for(task, timeout=3) == "selected"

    asyncio.run(scenario())
