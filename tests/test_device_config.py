import unittest
from unittest.mock import patch

from core.tasks.config import load_config
from core.tasks.utils.device import Devices


class DeviceConfigTests(unittest.TestCase):
    def test_config_loads_qemu_and_vm_devices(self):
        cfg = load_config()

        self.assertIn("vislor", cfg.hosts)
        self.assertEqual(cfg.qemu_nvme_pci, "0000:00:06.0")
        self.assertEqual(cfg.vm_device_addresses["amd"], "0000:01:00.0")

    def test_hostname_resolution_and_bdf_formats(self):
        devices = Devices("vislor")

        self.assertEqual(devices.nvme_pci, "0000:43:00.0")
        self.assertEqual(devices.nvme_short, "43:00.0")
        self.assertEqual(Devices.full_bdf("43:00.0"), "0000:43:00.0")
        self.assertEqual(Devices.short_bdf("0000:43:00.0"), "43:00.0")

    def test_hostname_defaults_to_current_host(self):
        with patch("core.tasks.utils.device.socket.gethostname", return_value="vislor"):
            devices = Devices()

        self.assertEqual(devices.hostname, "vislor")
        self.assertEqual(devices.nvme_pci, "0000:43:00.0")

    def test_storage_target_for_passthrough_and_qemu_nvme(self):
        devices = Devices("vislor")
        passthrough = devices.storage_target("amd", spdk=True)
        qemu_nvme = devices.storage_target("amd", qemu_nvme=True)

        self.assertEqual(passthrough.pci_dev, "0000:01:00.0")
        self.assertEqual(passthrough.vfio_device, "43:00.0")
        self.assertEqual(passthrough.filename, "trtype=PCIe traddr=0000.01.00.0 ns=1")
        self.assertEqual(qemu_nvme.pci_dev, devices.qemu_nvme_pci)
        self.assertIsNone(qemu_nvme.vfio_device)


if __name__ == "__main__":
    unittest.main()
