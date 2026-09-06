"""コマンドラインオプション、診断、ログ設定を提供する。"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mntui import __version__
from mntui.commands.runner import CommandRunner
from mntui.errors import MntuiError
from mntui.services.capabilities import capability_report, detect_capabilities
from mntui.services.storage import StorageService
from mntui.ui.app import run_ui


def main(argv: list[str] | None = None) -> int:
    """CLIを起動し、利用者向けの終了コードを返す。

    Args:
        argv: 引数一覧。省略時はプロセス引数。

    Returns:
        正常時は0、依存不足は2、実行失敗は1、中断は130。
    """
    parser = argparse.ArgumentParser(description="Safely inspect and manage Linux storage")
    parser.add_argument("--version", action="version", version=f"mntui {__version__}")
    parser.add_argument("--check", action="store_true", help="Report host capabilities; no changes")
    parser.add_argument("--dry-run", action="store_true", help="Preview operations without changes")
    parser.add_argument("--log-file", type=Path, help="Write timestamped operation logs")
    args = parser.parse_args(argv)
    try:
        handlers: list[logging.Handler] = [logging.NullHandler()]
        if args.log_file:
            handlers = [logging.FileHandler(args.log_file)]
        logging.basicConfig(
            level=logging.INFO,
            handlers=handlers,
            force=True,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        capabilities = detect_capabilities()
        if args.check:
            print(capability_report(capabilities))
            return 0 if capabilities.core_available else 2
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print(capability_report(capabilities))
            print(
                "Interactive terminal required. Use mntui --check for diagnostics.", file=sys.stderr
            )
            return 2
        service = StorageService(CommandRunner(), capabilities, dry_run=args.dry_run)
        asyncio.run(run_ui(service))
        return 0 if capabilities.core_available else 2
    except (KeyboardInterrupt, EOFError):
        return 130
    except (MntuiError, OSError) as exc:
        print(f"mntui: {exc}", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger(__name__).exception("Unexpected failure")
        print("mntui: unexpected error; use --log-file for details.", file=sys.stderr)
        return 1
