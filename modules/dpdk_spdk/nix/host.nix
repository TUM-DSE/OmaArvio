# dpdk-spdk host packages for the devShell.
# Storage/FIO tools moved to modules/fio; nvme-cli moved to modules/nvme.

{ pkgs, spdk, dpdk }:

with pkgs; [
  spdk
  dpdk

  # Hardware / performance tools
  numactl
  pciutils
  kmod
  gdb
]
