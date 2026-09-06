"""デバイス解析とシステム保護の伝播を検証する。"""

import json
from pathlib import Path

import pytest

from mntui.commands.discovery import MountRecord, parse_devices
from mntui.errors import CommandExecutionError


@pytest.mark.parametrize("point", ["/", "/boot", "/boot/efi", "[SWAP]", "/usr"])
def test_system_propagation(point: str) -> None:
    node = {
        "name": "sda",
        "path": "/dev/sda",
        "type": "disk",
        "maj:min": "8:0",
        "size": 1000,
        "model": "Example",
        "rm": 0,
        "ro": False,
        "children": [
            {
                "name": "sda1",
                "path": "/dev/sda1",
                "type": "part",
                "maj:min": "8:1",
                "size": 900,
                "fstype": "ext4",
                "uuid": "abc",
                "mountpoints": [point, None],
            }
        ],
    }
    disks = parse_devices(json.dumps({"blockdevices": [node]}), [])
    assert disks[0].is_system_disk
    assert disks[0].children[0].filesystem.uuid == "abc"
    assert disks[0].path == Path("/dev/sda")


def test_layered_system_device() -> None:
    child = {"name": "dm-0", "path": "/dev/dm-0", "type": "crypt", "maj:min": "253:0", "size": 9}
    parent = {
        "name": "sda",
        "path": "/dev/sda",
        "type": "disk",
        "maj:min": "8:0",
        "size": 10,
        "children": [child],
    }
    mounts = [MountRecord(source="/dev/mapper/root", target="/", **{"maj:min": "253:0"})]
    assert parse_devices(json.dumps({"blockdevices": [parent]}), mounts)[0].is_system_disk


@pytest.mark.parametrize("output", ["invalid", "{}", '{"blockdevices":[{"name":"x"}]}'])
def test_malformed(output: str) -> None:
    with pytest.raises(CommandExecutionError):
        parse_devices(output, [])


def test_active_swap_file(tmp_path: Path) -> None:
    import os

    from mntui.commands.discovery import active_swap_devices

    swap = tmp_path / "swapfile"
    swap.touch()
    listing = tmp_path / "swaps"
    listing.write_text(f"Filename Type Size Used Priority\n{swap} file 1024 0 -2\n")
    identity = swap.stat().st_dev
    assert active_swap_devices(listing) == {f"{os.major(identity)}:{os.minor(identity)}"}
