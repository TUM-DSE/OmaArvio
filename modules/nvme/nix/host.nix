# nvme host packages: nvme-cli and fio (used for preconditioning).
# fio can be overridden to the dpdk-spdk custom build via the flake dpdk-spdk input.

{ pkgs, fio }:
[ pkgs.nvme-cli fio pkgs.openssl pkgs.rust-parallel ]
