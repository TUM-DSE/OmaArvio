# fio host packages: FIO benchmark runner, storage formatting tools, and jobs.
# fio can be overridden to the dpdk-spdk custom build via the flake dpdk-spdk input.

{ pkgs, fio, fio-cuda, fio-jobs }:
[
  fio
  fio-cuda
  # Jobs directory in the Nix store; exposed through sharedData.
  fio-jobs
  pkgs.cryptsetup
  pkgs.fsverity-utils
  pkgs.parted
  pkgs.f2fs-tools
  pkgs.e2fsprogs
  pkgs.gettext
]
