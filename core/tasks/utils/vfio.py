from core.tasks.procs import run
from core.tasks.utils.pci import get_pci_device


from pathlib import Path
from typing import Optional


def get_pci_ids(pci_addr: str) -> tuple[str, str]:
    """Get vendor and device IDs for a PCIe device using lspci.

    Args:
        pci_addr: PCIe device address (e.g., "01:00.0")

    Returns:
        Tuple of (vendor_id, device_id) with "0x" prefix.
        Example: ("0x10de", "0x2321") for NVIDIA GPU
    """
    device = get_pci_device(pci_addr)
    return (f"0x{device.vendor_id}", f"0x{device.device_id}")


def bind_device_to_vfio(pci_addr: str) -> Optional[str]:
    """Bind a PCIe device to vfio-pci driver.

    Args:
        pci_addr: PCIe device address (e.g., "01:00.0")

    Returns:
        The name of the original driver that was bound, or None if no driver was bound.
        Use this value with unbind_device_from_vfio() to restore the device.
    """
    # Check if already bound to vfio-pci
    driver_path = Path(f"/sys/bus/pci/devices/0000:{pci_addr}/driver")
    original_driver = None

    if driver_path.exists():
        current_driver = driver_path.resolve().name
        if current_driver == "vfio-pci":
            print(f"Device {pci_addr} already bound to vfio-pci")
            return None

        # Save original driver before unbinding
        original_driver = current_driver
        print(f"Unbinding {pci_addr} from {current_driver}")
        unbind_path = driver_path / "unbind"
        with open(unbind_path, "w") as f:
            f.write(f"0000:{pci_addr}")

    # Perform device reset
    reset_path = Path(f"/sys/bus/pci/devices/0000:{pci_addr}/reset")
    if reset_path.exists():
        print(f"Resetting device {pci_addr}")
        try:
            with open(reset_path, "w") as f:
                f.write("1")
        except Exception as e:
            print(f"Warning: Failed to reset device: {e}")

    vendor_id, device_id = get_pci_ids(pci_addr)

    # Load vfio-pci module if not loaded
    run(["modprobe", "vfio-pci"])

    # Bind to vfio-pci
    print(f"Binding {pci_addr} ({vendor_id}:{device_id}) to vfio-pci")
    try:
        new_id_path = Path("/sys/bus/pci/drivers/vfio-pci/new_id")
        with open(new_id_path, "w") as f:
            f.write(f"{vendor_id} {device_id}")
        print(f"Successfully bound {pci_addr} to vfio-pci (new_id)")
    except FileExistsError:
        bind_path = Path("/sys/bus/pci/drivers/vfio-pci/bind")
        with open(bind_path, "w") as f:
            f.write(f"0000:{pci_addr}")
        print(f"Successfully bound {pci_addr} to vfio-pci (bind)")
    return original_driver


def unbind_device_from_vfio(pci_addr: str, original_driver: str) -> None:
    """Unbind a PCIe device from vfio-pci and restore to original driver.

    Args:
        pci_addr: PCIe device address (e.g., "01:00.0")
        original_driver: Driver name to restore (from bind_device_to_vfio() return value)
    """
    driver_path = Path(f"/sys/bus/pci/devices/0000:{pci_addr}/driver")

    # Verify currently bound to vfio-pci
    if not driver_path.exists():
        print(f"Warning: Device {pci_addr} has no driver bound, skipping unbind")
        return

    current_driver = driver_path.resolve().name
    if current_driver != "vfio-pci":
        print(
            f"Warning: Device {pci_addr} not bound to vfio-pci (current: {current_driver}), skipping"
        )
        return

    # Unbind from vfio-pci
    print(f"Unbinding {pci_addr} from vfio-pci")
    unbind_path = driver_path / "unbind"
    with open(unbind_path, "w") as f:
        f.write(f"0000:{pci_addr}")

    # Perform device reset
    reset_path = Path(f"/sys/bus/pci/devices/0000:{pci_addr}/reset")
    if reset_path.exists():
        print(f"Resetting device {pci_addr}")
        try:
            with open(reset_path, "w") as f:
                f.write("1")
        except Exception as e:
            print(f"Warning: Failed to reset device: {e}")

    # Bind to original driver
    print(f"Restoring {pci_addr} to {original_driver}")
    bind_path = Path(f"/sys/bus/pci/drivers/{original_driver}/bind")
    try:
        with open(bind_path, "w") as f:
            f.write(f"0000:{pci_addr}")
        print(f"Successfully restored {pci_addr} to {original_driver}")
    except Exception as e:
        # Fallback: trigger automatic driver probe
        print(f"Warning: Direct bind to {original_driver} failed: {e}")
        print(f"Attempting automatic driver probe...")
        probe_path = Path("/sys/bus/pci/drivers_probe")
        try:
            with open(probe_path, "w") as f:
                f.write(f"0000:{pci_addr}")
            print(f"Triggered driver probe for {pci_addr}")
        except Exception as e2:
            print(f"Error: Failed to probe drivers: {e2}")
