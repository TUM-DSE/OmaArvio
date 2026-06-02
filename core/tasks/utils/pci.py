"""PCI device information module using lspci.

This module provides functions to query PCI device information by parsing
lspci -vvv output instead of reading sysfs files directly.
"""

import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PCIeLinkInfo:
    """PCIe link capability and status information."""

    max_speed_gts: float  # e.g., 32.0 for 32 GT/s
    max_width: int  # e.g., 4 for x4
    current_speed_gts: float
    current_width: int
    max_gen: int  # PCIe generation (1-5)
    current_gen: int


@dataclass
class SRIOVInfo:
    """SR-IOV capability information."""

    initial_vfs: int = 0
    total_vfs: int = 0
    num_vfs: int = 0


@dataclass
class PCIDevice:
    """Complete PCI device information parsed from lspci."""

    # Basic identification
    address: str  # Full BDF: "0000:e1:00.0"
    short_address: str  # Short BDF: "e1:00.0"
    vendor_id: str  # "1e0f" (without 0x prefix)
    device_id: str  # "0013" (without 0x prefix)
    class_code: str  # "0108" (NVMe controller)

    # Topology
    numa_node: Optional[int] = None
    iommu_group: Optional[int] = None

    # Link information
    link: Optional[PCIeLinkInfo] = None

    # Driver information
    driver: Optional[str] = None
    kernel_modules: list[str] = field(default_factory=list)

    # Extended capabilities
    serial_number: Optional[str] = None
    sriov: Optional[SRIOVInfo] = None


# Regex patterns for parsing lspci -vvvnn output
# Header format: "e1:00.0 Non-Volatile memory controller [0108]: KIOXIA Corporation NVMe SSD [1e0f:0013] (rev 01)"
HEADER_PATTERN = re.compile(
    r"^([0-9a-f:.]+)\s+"  # BDF address
    r".+\[([0-9a-f]{4})\]:\s*"  # Device class with [class_code]
    r".+\[([0-9a-f]{4}):([0-9a-f]{4})\]"  # Vendor:Device in brackets [vvvv:dddd]
)

NUMA_PATTERN = re.compile(r"NUMA node:\s*(\d+)")
IOMMU_PATTERN = re.compile(r"IOMMU group:\s*(\d+)")

# Link capability: "LnkCap: Port #0, Speed 32GT/s, Width x4"
LNKCAP_PATTERN = re.compile(
    r"LnkCap:.*Speed\s+(\d+(?:\.\d+)?)\s*GT/s.*Width\s+x(\d+)", re.IGNORECASE
)

# Link status: "LnkSta: Speed 32GT/s, Width x4"
LNKSTA_PATTERN = re.compile(
    r"LnkSta:.*Speed\s+(\d+(?:\.\d+)?)\s*GT/s.*Width\s+x(\d+)", re.IGNORECASE
)

DSN_PATTERN = re.compile(r"Device Serial Number\s+([\w-]+)")
SRIOV_PATTERN = re.compile(
    r"Initial VFs:\s*(\d+).*Total VFs:\s*(\d+).*Number of VFs:\s*(\d+)"
)
DRIVER_PATTERN = re.compile(r"Kernel driver in use:\s*(\S+)")
MODULES_PATTERN = re.compile(r"Kernel modules:\s*(.+)")


def gts_to_gen(speed_gts: float) -> int:
    """Convert GT/s speed to PCIe generation number.

    Args:
        speed_gts: Link speed in GT/s

    Returns:
        PCIe generation number (1-6), or 0 if unknown
    """
    speed_map = {
        2.5: 1,
        5.0: 2,
        8.0: 3,
        16.0: 4,
        32.0: 5,
        64.0: 6,
    }
    return speed_map.get(speed_gts, 0)


def short_bdf(address: str) -> str:
    """Normalize PCI address to short BDF format.

    Args:
        address: PCI address in various formats (e.g., "0000:e1:00.0", "e1:00.0", "e1:00")

    Returns:
        Short BDF format (e.g., "e1:00.0")
    """
    # Remove domain if present
    if address.count(":") == 2:
        address = ":".join(address.split(":")[1:])

    # Add .0 if function not specified
    if "." not in address:
        address = f"{address}.0"

    return address


def run_lspci(device: Optional[str] = None, verbose: bool = True) -> str:
    """Execute lspci and return raw output.

    Args:
        device: Optional specific device address (e.g., "e1:00.0")
        verbose: If True, use -vvvnn for detailed output with numeric IDs

    Returns:
        Raw lspci output string

    Raises:
        RuntimeError: If lspci command fails
    """
    cmd = ["lspci", "-nn"]  # Always include numeric IDs
    if verbose:
        cmd.append("-vvv")
    if device:
        cmd.extend(["-s", device])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"lspci command failed: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError("lspci command not found - install pciutils") from e


def parse_lspci_device(output: str) -> PCIDevice:
    """Parse lspci -vvvnn output for a single device.

    Args:
        output: Raw lspci -vvvnn output for one device

    Returns:
        PCIDevice with all parsed information

    Raises:
        RuntimeError: If header cannot be parsed
    """
    lines = output.strip().split("\n")
    if not lines:
        raise RuntimeError("Empty lspci output")

    # Parse header line
    header = lines[0]
    match = HEADER_PATTERN.match(header)
    if not match:
        raise RuntimeError(f"Failed to parse lspci header: {header}")

    short_addr = match.group(1)
    # Normalize to include domain
    if short_addr.count(":") == 1:
        full_addr = f"0000:{short_addr}"
    else:
        full_addr = short_addr
        short_addr = ":".join(short_addr.split(":")[1:])

    class_code = match.group(2)
    vendor_id = match.group(3)
    device_id = match.group(4)

    # Parse remaining fields from full output
    full_output = output

    numa_node = None
    numa_match = NUMA_PATTERN.search(full_output)
    if numa_match:
        numa_node = int(numa_match.group(1))

    iommu_group = None
    iommu_match = IOMMU_PATTERN.search(full_output)
    if iommu_match:
        iommu_group = int(iommu_match.group(1))

    # Parse link info
    link = None
    lnkcap_match = LNKCAP_PATTERN.search(full_output)
    lnksta_match = LNKSTA_PATTERN.search(full_output)
    if lnkcap_match and lnksta_match:
        max_speed = float(lnkcap_match.group(1))
        max_width = int(lnkcap_match.group(2))
        current_speed = float(lnksta_match.group(1))
        current_width = int(lnksta_match.group(2))
        link = PCIeLinkInfo(
            max_speed_gts=max_speed,
            max_width=max_width,
            current_speed_gts=current_speed,
            current_width=current_width,
            max_gen=gts_to_gen(max_speed),
            current_gen=gts_to_gen(current_speed),
        )

    # Parse driver
    driver = None
    driver_match = DRIVER_PATTERN.search(full_output)
    if driver_match:
        driver = driver_match.group(1)

    # Parse kernel modules
    kernel_modules = []
    modules_match = MODULES_PATTERN.search(full_output)
    if modules_match:
        kernel_modules = [m.strip() for m in modules_match.group(1).split(",")]

    # Parse serial number
    serial_number = None
    dsn_match = DSN_PATTERN.search(full_output)
    if dsn_match:
        serial_number = dsn_match.group(1)

    # Parse SR-IOV
    sriov = None
    sriov_match = SRIOV_PATTERN.search(full_output)
    if sriov_match:
        sriov = SRIOVInfo(
            initial_vfs=int(sriov_match.group(1)),
            total_vfs=int(sriov_match.group(2)),
            num_vfs=int(sriov_match.group(3)),
        )

    return PCIDevice(
        address=full_addr,
        short_address=short_addr,
        vendor_id=vendor_id,
        device_id=device_id,
        class_code=class_code,
        numa_node=numa_node,
        iommu_group=iommu_group,
        link=link,
        driver=driver,
        kernel_modules=kernel_modules,
        serial_number=serial_number,
        sriov=sriov,
    )


def get_pci_device(address: str) -> PCIDevice:
    """Get complete PCI device information.

    Args:
        address: PCI address (e.g., "e1:00.0" or "0000:e1:00.0")

    Returns:
        PCIDevice with all available information

    Raises:
        RuntimeError: If device not found or parsing fails
    """
    normalized = short_bdf(address)
    output = run_lspci(device=normalized, verbose=True)
    if not output.strip():
        raise RuntimeError(f"Device {address} not found")
    return parse_lspci_device(output)


@contextmanager
def check_speed(
    pci_device: str, verbose: bool = False, valid_speeds: Optional[list[int]] = None
):
    """Context manager that checks PCIe link speed remains at maximum or in a set of valid speeds.

    On entry, verifies PCIe link speed is at maximum or in valid_speeds.
    On exit, verifies that PCIe link speed hasn't degraded (remains in valid_speeds or at max).
    Raises RuntimeError if speed is not valid or degrades during execution.

    Args:
        pci_device: PCI device identifier (e.g., '43:00.0')
        verbose: Enable debug output (default False)
        valid_speeds: Optional list of valid speeds (PCIe gen numbers, e.g., [3, 4, 5])

    Yields:
        PCIDevice: Device information including link speed

    Raises:
        RuntimeError: If speed is not valid or degrades during context

    Example:
        with check_speed('43:00.0', valid_speeds=[3, 4]) as device:
            print(f"Running benchmark at gen {device.link.current_gen}")
            # Speed verification happens automatically on exit
    """
    device = get_pci_device(pci_device)

    if device.link is None:
        raise RuntimeError(f"No PCIe link info available for {pci_device}")

    current = device.link.current_gen
    max_speed = device.link.max_gen

    if valid_speeds is not None:
        if current not in valid_speeds:
            raise RuntimeError(
                f"PCIe speed for {pci_device} is not in valid speeds {valid_speeds}. "
                f"Current: {current}, Max: {max_speed}"
            )
    else:
        if current != max_speed:
            raise RuntimeError(
                f"PCIe speed is not at max for {pci_device}. "
                f"Current: {current}, Max: {max_speed}"
            )

    if verbose:
        print(
            f"PCIe speed for {pci_device} verified at gen {current} "
            f"(valid: {valid_speeds if valid_speeds is not None else 'max only'})"
        )

    try:
        yield device
    finally:
        final_device = get_pci_device(pci_device)

        if final_device.link is None:
            raise RuntimeError(
                f"No PCIe link info available for {pci_device} after execution"
            )

        final_current = final_device.link.current_gen
        final_max = final_device.link.max_gen

        if valid_speeds is not None:
            if final_current not in valid_speeds:
                raise RuntimeError(
                    f"PCIe speed degraded during execution for {pci_device}. "
                    f"Current: {final_current}, Valid: {valid_speeds}"
                )
        else:
            if final_current != final_max:
                raise RuntimeError(
                    f"PCIe speed degraded during execution for {pci_device}. "
                    f"Current: {final_current}, Max: {final_max}"
                )

        if verbose:
            print(
                f"PCIe speed for {pci_device} remained at gen {final_current} "
                f"(valid: {valid_speeds if valid_speeds is not None else 'max only'})"
            )
