# nvidia-cc NixOS guest additions:
# NVIDIA driver, Confidential Computing mode service, persistenced override.

{ pkgs, lib, config, ... }:

{
  boot.kernelParams = [ "iommu.strict=0" "iommu.passthrough=1" ];

  # Force static BAR1 (required for CC mode) + UVM / IOMMU workaround
  boot.extraModprobeConfig = lib.mkAfter ''
    options nvidia NVreg_RegistryDwords="RMForceStaticBar1=1;RmForceDisableIomapWC=1;"
    install nvidia ${pkgs.kmod}/bin/modprobe ecdsa_generic; ${pkgs.kmod}/bin/modprobe ecdh; ${pkgs.kmod}/bin/modprobe --ignore-install nvidia
  '';

  hardware.graphics.enable = true;

  hardware.nvidia.datacenter.enable = false;
  hardware.nvidia.package = config.boot.kernelPackages.nvidiaPackages.production;
  hardware.nvidia.open = true;
  systemd.services.nvidia-fabricmanager.enable = lib.mkForce false;
  hardware.nvidia.nvidiaPersistenced = true;

  # Enable Confidential Computing mode after persistenced starts
  systemd.services.nvidia-cc-mode = {
    description = "Enable NVIDIA Confidential Computing mode";
    after = [ "nvidia-persistenced.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "-${config.hardware.nvidia.package.bin}/bin/nvidia-smi conf-compute -srs 1";
      RemainAfterExit = true;
    };
  };

  # Add UVM persistence mode to nvidia-persistenced
  systemd.services.nvidia-persistenced.serviceConfig.ExecStart = lib.mkForce [
    "" # Clear existing value
    "${config.hardware.nvidia.package.persistenced}/bin/nvidia-persistenced --uvm-persistence-mode --verbose"
  ];

  services.xserver.videoDrivers = [ "nvidia" ];
}
