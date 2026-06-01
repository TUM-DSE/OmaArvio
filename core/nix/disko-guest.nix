# Unified disko configuration for both snp-guest and direct-guest
# Parameterized by enableBootloader to support both GRUB-based boot and direct kernel boot
{ enableBootloader ? true }:

{ config, lib, pkgs, modulesPath, ... }:

{
  imports = [
    "${modulesPath}/profiles/qemu-guest.nix"
  ];

  disko.devices = {
    disk = {
      main = {
        imageSize = "64G";
        device = "/dev/disk/by-id/some-disk-id";
        type = "disk";
        content = {
          type = "gpt";
          partitions = (lib.optionalAttrs enableBootloader {
            ESP = {
              type = "EF00";
              size = "500M";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "umask=0077" ];
              };
            };
          }) // {
            root = {
              size = "100%";
              content = {
                type = "filesystem";
                format = "ext4";
                mountpoint = "/";
              };
            };
          };
        };
      };
    };
  };

  # Boot configuration varies based on use case
  boot.loader.grub.enable = lib.mkForce enableBootloader;
  boot.loader.initScript.enable = lib.mkForce (if enableBootloader then false else true);
  boot.isContainer = lib.mkForce (if enableBootloader then false else true);
  boot.initrd.enable = lib.mkForce enableBootloader;
  boot.initrd.systemd.network.wait-online.enable = false;
  boot.loader.grub.efiSupport = lib.mkForce enableBootloader;
  boot.loader.grub.devices = lib.mkIf enableBootloader [ "nodev" ];
  boot.loader.grub.efiInstallAsRemovable = lib.mkForce enableBootloader;
  boot.loader.timeout = lib.mkForce 0;
  boot.growPartition = true;
}
