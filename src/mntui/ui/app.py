"""キーボード操作中心のコンパクトなストレージ管理画面を提供する。"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.shortcuts import input_dialog, message_dialog, radiolist_dialog, yes_no_dialog
from pydantic import ValidationError

from mntui.commands.discovery import walk
from mntui.errors import MntuiError
from mntui.models.domain import BlockDevice, OperationType
from mntui.services.capabilities import capability_report
from mntui.services.storage import StorageService


def _keys[T](application: Application[T], *, quit_key: bool = False) -> Application[T]:
    bindings = KeyBindings()

    @bindings.add("escape")
    def cancel(event: object) -> None:
        application.exit()

    if quit_key:
        bindings.add("q")(cancel)
    application.key_bindings = merge_key_bindings(
        [application.key_bindings, bindings] if application.key_bindings else [bindings]
    )
    return application


def _details(device: BlockDevice) -> str:
    fs = device.filesystem
    return (
        f"{device.path}  {device.size / 10**9:.2f} GB\n"
        f"Model: {device.model or '—'}   Serial: {device.serial or '—'}\n"
        f"Partition table: {device.partition_table or '—'}\n"
        f"Read-only: {device.read_only}   Removable: {device.removable}\n"
        f"System disk: {device.is_system_disk}\n"
        f"Filesystem: {fs.type if fs else '—'}   UUID: {fs.uuid if fs else '—'}\n"
        f"Label: {fs.label if fs else '—'}\n"
        f"Mounts: {', '.join(device.mount_points) or '—'}"
    )


async def _message(title: str, text: str) -> None:
    await _keys(message_dialog(title=title, text=text)).run_async()


async def _operate(service: StorageService, device: BlockDevice) -> None:
    capabilities = service.capabilities
    action = await _keys(
        radiolist_dialog(
            title=str(device.path),
            text=_details(device),
            values=[
                (
                    OperationType.INITIALIZE,
                    "Initialize unused disk (GPT + full-size partition)"
                    + ("" if capabilities.can_partition else " [Unavailable: sfdisk]"),
                ),
                (OperationType.FORMAT, "Format partition"),
                (OperationType.MOUNT, "Mount filesystem"),
                (OperationType.UNMOUNT, "Unmount filesystem"),
            ],
            ok_text="Select",
            cancel_text="Back",
        ),
        quit_key=True,
    ).run_async()
    if action is None:
        return
    filesystem = None
    mount_point = None
    create = False
    persistent = False
    if action == OperationType.FORMAT:
        filesystem = await _keys(
            radiolist_dialog(
                title="Filesystem",
                values=[
                    (
                        fs,
                        fs
                        + (
                            ""
                            if fs in capabilities.supported_filesystems
                            else f" [Unavailable: mkfs.{fs}]"
                        ),
                    )
                    for fs in ("ext4", "xfs", "btrfs")
                ],
            )
        ).run_async()
        if filesystem is None:
            return
    if action == OperationType.MOUNT:
        value = await _keys(
            input_dialog(title="Mount point", text="Absolute path, e.g. /data:")
        ).run_async()
        if not value:
            return
        mount_point = Path(value)
        if not mount_point.exists():
            create = bool(
                await _keys(
                    yes_no_dialog(
                        title="Create directory",
                        text=f"Create {mount_point}?",
                    )
                ).run_async()
            )
        persistent = bool(
            await _keys(
                yes_no_dialog(
                    title="Persistence",
                    text="Also add this filesystem to /etc/fstab?",
                )
            ).run_async()
        )
    elif action == OperationType.UNMOUNT:
        if not device.mount_points:
            await _message("Unmount", "This filesystem is not mounted.")
            return
        value = await _keys(
            radiolist_dialog(
                title="Unmount",
                text=f"Filesystem: {device.path}\nSelect the mount to unmount:",
                values=[(point, point) for point in device.mount_points],
            )
        ).run_async()
        if value is None:
            return
        mount_point = Path(value)
    plan = await asyncio.to_thread(
        service.plan,
        device.path,
        action,
        filesystem=filesystem,
        mount_point=mount_point,
        create_directory=create,
        persistent=persistent,
    )
    description = await asyncio.to_thread(service.describe, plan)
    confirmation = await _keys(
        input_dialog(
            title="Confirm plan" + (" — DRY RUN" if service.dry_run else ""),
            text=description + f"\n\nType {plan.target if plan.destructive else 'yes'} to confirm:",
        )
    ).run_async()
    if confirmation is None:
        return
    # 実行中の二重送信を防ぎ、ワーカースレッドから進捗だけを画面へ渡す。
    from prompt_toolkit.shortcuts import progress_dialog

    result_text: list[str] = []

    def execute(set_percentage: Callable[[int], None], log_text: Callable[[str], None]) -> None:
        try:
            results = service.execute(
                plan, confirmation, progress=lambda message: log_text(message + "\n")
            )
            set_percentage(100)
            result_text.append(
                "\n".join(f"{result.operation.type}: {result.message}" for result in results)
            )
        except (MntuiError, OSError, ValueError) as exc:
            result_text.append(str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Unexpected operation failure")
            result_text.append(
                "Unexpected failure; inspect current disk state and the log before retrying."
            )

    await progress_dialog(
        title="Executing plan",
        text="Please wait; do not interrupt storage commands.",
        run_callback=execute,
    ).run_async()
    await _message("Operation result", "\n".join(result_text))


async def run_ui(service: StorageService) -> None:
    """ディスク選択、詳細、計画確認、結果表示を順に行う。

    Args:
        service: 検証と実行を担当するアプリケーションサービス。
    """
    if not service.capabilities.core_available:
        await _message("Missing dependencies", capability_report(service.capabilities))
        return
    while True:
        try:
            disks = await asyncio.to_thread(service.disks)
            values = [
                (
                    str(disk.path),
                    f"{disk.path}  {disk.size / 10**9:.2f} GB  "
                    f"{disk.model or ''}  {'System' if disk.is_system_disk else ''}",
                )
                for disk in disks
            ]
            values.append(("check", "Environment / available features"))
            selection = await _keys(
                radiolist_dialog(
                    title="mntui" + (" — DRY RUN" if service.dry_run else ""),
                    text=(
                        "Disks\nArrows / Space: select   Tab: buttons\n"
                        "Enter: activate   Esc / q: back"
                    ),
                    values=values,
                    ok_text="Details",
                    cancel_text="Quit",
                ),
                quit_key=True,
            ).run_async()
            if selection is None:
                return
            if selection == "check":
                await _message("Environment", capability_report(service.capabilities))
                continue
            disk = next(disk for disk in disks if str(disk.path) == selection)
            device_path = await _keys(
                radiolist_dialog(
                    title="Disk details",
                    text=_details(disk),
                    values=[
                        (
                            str(item.path),
                            f"{item.path}  {item.size / 10**9:.2f} GB  "
                            f"{item.filesystem.type if item.filesystem else 'unformatted'}  "
                            f"{', '.join(item.mount_points)}",
                        )
                        for item in walk(disk)
                    ],
                    ok_text="Actions",
                    cancel_text="Back",
                ),
                quit_key=True,
            ).run_async()
            if device_path:
                await _operate(
                    service, next(item for item in walk(disk) if str(item.path) == device_path)
                )
        except (MntuiError, OSError, ValidationError) as exc:
            await _message("Cannot complete operation", str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Unexpected UI failure")
            await _message(
                "Error", "Unexpected error. See the log for details; refresh before retrying."
            )
