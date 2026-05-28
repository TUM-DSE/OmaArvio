# dpdk-spdk NixOS guest additions:
# hugepages, VFIO kernel modules, NOIOMMU mode, ice blacklist, DPDK build tools,
# and the SPDK/DPDK binaries built by this module.

{ self }: { pkgs, lib, config, ... }:

let
  modPkgs = self.packages.x86_64-linux;
in
{
  # Configure hugepages for DPDK (IOMMU disabled, using NOIOMMU mode)
  boot.kernelParams = [
    "hugepagesz=1G"
    "hugepages=4"
    "pci=realloc,nocrs"
  ];

  boot.kernelModules = [
    "vfio_pci"
    "vfio_iommu_type1"
    "vfio"
  ];

  boot.initrd.kernelModules = [
    "vfio_pci"
    "vfio_iommu_type1"
    "vfio"
  ];

  # VFIO NOIOMMU mode + ice driver blacklist (ice conflicts with DPDK)
  boot.extraModprobeConfig = lib.mkAfter ''
    blacklist ice
    options vfio enable_unsafe_noiommu_mode=1
  '';

  environment.systemPackages = (with modPkgs; [ spdk dpdk dpdk.examples ]);
}
