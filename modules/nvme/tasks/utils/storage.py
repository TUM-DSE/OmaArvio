#!/usr/bin/env python3
"""Generic block-device setup helpers for storage benchmarks."""

from __future__ import annotations

import shlex
import time

from core.tasks.qemu import HostRunner, QemuVm
from core.tasks.utils.utils import parse_size_to_mb

# Fixed key/IV: content only needs to be high-entropy/non-compressible, not
# secret or unique per run - using a fixed key/IV makes the generated test
# file deterministic across runs. Values are arbitrary random bytes, not secrets.
_FILL_KEY = "b5032473a56388a75d264d8de756700df200fad4e280e686ac37128f516c3c86"
_FILL_IV = "42c311bbb9f75add783b29c1374e6daf"

# Cap on parallel openssl|dd workers for create_test_file(). AES-256-CTR via
# AES-NI tops out around ~2GB/s on a single core, so splitting across cores
# is needed to keep up with NVMe write speeds.
_MAX_PARALLEL = 8

# The partition nodes a caller derives from get_partition_name() are created
# by udev after the kernel's partition rescan, so they are not there when
# parted or partprobe returns. On a device whose namespace was just formatted
# udev has a queue to work through first, and whatever runs next - mkfs,
# cryptsetup - then fails with a bare "does not exist".
_DEVICE_WAIT_S = 30
# udevadm settle blocks on udev's queue instead of polling it, so the common
# case costs one settle; the sleep only paces retries after a settle that
# emptied the queue without producing the node.
_SETTLE_S = 5
_POLL_S = 0.2


def parse_job_filesystem(job_name: str) -> str:
    for fs_type in ["ext4", "f2fs"]:
        if fs_type in job_name:
            return fs_type
    raise ValueError(f"Cannot determine filesystem from job name: {job_name}")


def make_filesystem(
    vm: QemuVm | HostRunner, device: str, fs_type: str, verity: bool = False
) -> None:
    if fs_type == "ext4":
        cmd = ["mkfs.ext4", "-F"]
        if verity:
            cmd.extend(["-O", "verity"])
        cmd.append(device)
    elif fs_type == "f2fs":
        cmd = ["mkfs.f2fs", "-f"]
        if verity:
            cmd.extend(["-O", "verity"])
        cmd.append(device)
    else:
        raise ValueError(f"Unsupported filesystem type: {fs_type}")

    vm.ssh_cmd(cmd, check=True, bypass=True)


def create_test_file(
    vm: QemuVm | HostRunner,
    mount_point: str,
    filename: str = "testfile",
    size_mb: int | None = None,
) -> None:
    if size_mb is None:
        size_mb = 9216
    print(f"Creating test file with pseudo-random data ({size_mb}MB)...")
    target = shlex.quote(f"{mount_point}/{filename}")

    nproc_result = vm.ssh_cmd(["nproc"], check=True, bypass=True)
    n = max(1, min(int(nproc_result.stdout.strip()), _MAX_PARALLEL, size_mb))

    # AES-CTR keystream at byte offset `off` only depends on IV + off/16
    # (16-byte blocks), so each chunk can continue the single-stream
    # keystream independently - the concatenated output is byte-identical
    # to a single openssl process covering the whole file.
    iv_int = int(_FILL_IV, 16)
    base, rem = divmod(size_mb, n)
    jobs = []
    off_mb = 0
    for i in range(n):
        chunk_mb = base + (1 if i < rem else 0)
        block_off = (off_mb * 1024 * 1024) // 16
        chunk_iv = format((iv_int + block_off) % (2**128), "032x")
        jobs.append(
            f"openssl enc -aes-256-ctr -K {_FILL_KEY} -iv {chunk_iv} -nosalt -in /dev/zero 2>/dev/null | "
            f"dd of={target} bs=1M count={chunk_mb} seek={off_mb} conv=notrunc iflag=fullblock status=none"
        )
        off_mb += chunk_mb

    job_args = " ".join(shlex.quote(job) for job in jobs)
    cmd = (
        f"truncate -s {size_mb}M {target} && "
        f"printf '%s\\n' {job_args} | "
        f"rust-parallel -s --shell-path sh -j {n} && "
        f"sync -f {target}"
    )
    start = time.monotonic()
    vm.ssh_cmd(["sh", "-c", cmd], check=True, bypass=True)
    elapsed = time.monotonic() - start
    throughput_mb_s = size_mb / elapsed if elapsed > 0 else float("inf")
    print(f"Test file created in {elapsed:.2f}s ({throughput_mb_s:.1f} MB/s).")


def get_partition_name(device: str, partition_num: int) -> str:
    if device.startswith("/dev/disk/by-id/"):
        return f"{device}-part{partition_num}"
    if "nvme" in device:
        return f"{device}p{partition_num}"
    return f"{device}{partition_num}"


def wait_for_block_devices(
    vm: QemuVm | HostRunner,
    paths: list[str],
    timeout_s: int = _DEVICE_WAIT_S,
) -> None:
    """Block until every path is a block device in the guest.

    Raises:
        RuntimeError: if any path is still missing after timeout_s.
    """

    def missing_devices() -> list[str]:
        return [
            path
            for path in paths
            if vm.ssh_cmd(["test", "-b", path], check=False, bypass=True).returncode
        ]

    deadline = time.monotonic() + timeout_s
    while True:
        vm.ssh_cmd(
            ["udevadm", "settle", f"--timeout={_SETTLE_S}"], check=False, bypass=True
        )
        missing = missing_devices()
        if not missing:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_S)

    # Separates the two failures that reach the caller as one: a kernel that
    # never took the partition table lists no partition here, a udev that never
    # published the symlink lists one.
    lsblk = vm.ssh_cmd(["lsblk", "-o", "NAME,SIZE,TYPE"], check=False, bypass=True)
    print(lsblk.stdout)
    raise RuntimeError(f"block devices missing after {timeout_s}s: {' '.join(missing)}")


def create_partition(
    vm: QemuVm | HostRunner, device: str, partitions: list[tuple[str, str]]
) -> None:
    parted_cmd = ["parted", "-s", device, "mklabel", "gpt"]
    for start, end in partitions:
        parted_cmd.extend(["mkpart", "primary", start, end])

    vm.ssh_cmd(parted_cmd, check=True, bypass=True)
    # Advisory: the kernel also rescans when parted closes the device, and
    # partprobe reports another device's failure as its own. The wait below
    # is what gates the caller.
    vm.ssh_cmd(["partprobe", device], check=False, bypass=True)
    wait_for_block_devices(
        vm, [get_partition_name(device, n) for n in range(1, len(partitions) + 1)]
    )


def format_luks_device_with_mode(
    vm: QemuVm | HostRunner,
    device: str,
    fs_type: str,
    size: str = "10G",
    passphrase: str = "test",
    cipher: str = "aes-xts-plain64",
    integrity: str | None = None,
    key_size: int | None = None,
) -> str:
    print(
        f"Formatting LUKS device: {device} "
        f"(cipher: {cipher}, size: {size}, fs: {fs_type})"
    )

    create_partition(vm, device, [("0%", size)])
    partition = get_partition_name(device, 1)

    luks_cmd = [
        "cryptsetup",
        "luksFormat",
        "--batch-mode",
        "--type=luks2",
        f"--cipher={cipher}",
    ]
    if integrity:
        luks_cmd.append(f"--integrity={integrity}")
    if key_size:
        luks_cmd.append(f"--key-size={key_size}")
    luks_cmd.append(partition)

    vm.ssh_cmd(luks_cmd, check=True, input=f"{passphrase}\n{passphrase}\n", bypass=True)
    vm.ssh_cmd(
        ["cryptsetup", "open", partition, "luks-encrypted"],
        check=True,
        input=f"{passphrase}\n",
        bypass=True,
    )

    make_filesystem(vm, "/dev/mapper/luks-encrypted", fs_type)
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(
        ["mount", "/dev/mapper/luks-encrypted", "/mnt/encrypted"],
        check=True,
        bypass=True,
    )
    create_test_file(vm, "/mnt/encrypted", size_mb=int(parse_size_to_mb(size) * 0.8))
    return "/mnt/encrypted/testfile"


def format_luks_aes_device(
    vm: QemuVm | HostRunner,
    device: str,
    fs_type: str,
    size: str = "10G",
    passphrase: str = "test",
) -> str:
    return format_luks_device_with_mode(
        vm, device, fs_type, size, passphrase, cipher="aes-xts-plain64"
    )


def format_luks_aegis128_device(
    vm: QemuVm | HostRunner,
    device: str,
    fs_type: str,
    size: str = "10G",
    passphrase: str = "test",
) -> str:
    return format_luks_device_with_mode(
        vm,
        device,
        fs_type,
        size,
        passphrase,
        cipher="aegis128-plain64",
        integrity="aead",
        key_size=128,
    )


def format_luks_aes_xts_device(
    vm: QemuVm | HostRunner,
    device: str,
    fs_type: str,
    size: str = "10G",
    passphrase: str = "test",
) -> str:
    return format_luks_device_with_mode(
        vm,
        device,
        fs_type,
        size,
        passphrase,
        cipher="aes-xts-random",
        integrity="hmac-sha256",
    )


def format_dmverity_device(
    vm: QemuVm | HostRunner, device: str, fs_type: str, size: str = "10G"
) -> str:
    print(f"Formatting dm-verity device: {device} (size: {size}, fs: {fs_type})")
    create_partition(
        vm, device, [("0%", size), (size, f"{parse_size_to_mb(size) + 2048}M")]
    )
    data_partition = get_partition_name(device, 1)
    hash_partition = get_partition_name(device, 2)

    make_filesystem(vm, data_partition, fs_type)
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(["mount", data_partition, "/mnt/encrypted"], check=True, bypass=True)
    create_test_file(vm, "/mnt/encrypted", size_mb=int(parse_size_to_mb(size) * 0.8))
    vm.ssh_cmd(["umount", "/mnt/encrypted"], check=True, bypass=True)

    print("Creating dm-verity hash table...")
    result = vm.ssh_cmd(
        ["veritysetup", "format", data_partition, hash_partition],
        check=False,
        bypass=True,
    )
    root_hash = None
    for line in result.stdout.splitlines():
        if "Root hash:" in line:
            root_hash = line.split("Root hash:")[1].strip()
            break
    if not root_hash:
        raise RuntimeError("Failed to extract root hash from veritysetup output")

    vm.ssh_cmd(
        ["veritysetup", "open", data_partition, "verity", hash_partition, root_hash],
        check=True,
        bypass=True,
    )
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(
        ["mount", "-o", "ro", "/dev/mapper/verity", "/mnt/encrypted"],
        check=True,
        bypass=True,
    )
    return "/mnt/encrypted/testfile"


def format_fsverity_device(
    vm: QemuVm | HostRunner, device: str, fs_type: str, size: str = "10G"
) -> str:
    print(f"Formatting fs-verity device: {device} (size: {size}, fs: {fs_type})")
    create_partition(vm, device, [("0%", size)])
    partition = get_partition_name(device, 1)
    make_filesystem(vm, partition, fs_type, verity=True)
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(["mount", partition, "/mnt/encrypted"], check=True, bypass=True)
    create_test_file(vm, "/mnt/encrypted", size_mb=int(parse_size_to_mb(size) * 0.8))
    vm.ssh_cmd(
        ["fsverity", "enable", "/mnt/encrypted/testfile"], check=True, bypass=True
    )
    vm.ssh_cmd(
        ["fsverity", "measure", "/mnt/encrypted/testfile"], check=True, bypass=True
    )
    return "/mnt/encrypted/testfile"


def format_plain_device(
    vm: QemuVm | HostRunner,
    device: str,
    fs_type: str,
    size: str = "10G",
    file_size: str | None = None,
    mount_options: list[str] | None = None,
) -> str:
    print(f"Formatting plain device: {device} (size: {size}, fs: {fs_type})")
    create_partition(vm, device, [("0%", size)])
    partition = get_partition_name(device, 1)
    make_filesystem(vm, partition, fs_type)
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)

    mount_cmd = ["mount"]
    if mount_options:
        mount_cmd += ["-o", ",".join(mount_options)]
    mount_cmd += [partition, "/mnt/encrypted"]
    vm.ssh_cmd(mount_cmd, check=True, bypass=True)

    fs_size_mb = (
        parse_size_to_mb(file_size)
        if file_size is not None
        else int(parse_size_to_mb(size) * 0.8)
    )
    create_test_file(vm, "/mnt/encrypted", size_mb=fs_size_mb)
    return "/mnt/encrypted/testfile"


def cleanup_encrypted_device(
    vm: QemuVm | HostRunner, encryption_type: str, device: str
) -> None:
    print(f"Cleaning up {encryption_type} device...")
    try:
        vm.ssh_cmd(["umount", "/mnt/encrypted"], check=False, bypass=True)
        if encryption_type.startswith("luks-"):
            vm.ssh_cmd(
                ["cryptsetup", "close", "luks-encrypted"], check=False, bypass=True
            )
        elif encryption_type == "dmverity":
            vm.ssh_cmd(["veritysetup", "close", "verity"], check=False, bypass=True)
        vm.ssh_cmd(["wipefs", "-a", device], check=False, bypass=True)
    except Exception as exc:
        print(f"Warning: Cleanup error (non-fatal): {exc}")
