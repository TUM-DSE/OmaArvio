# Core NixOS guest configuration (networking, SSH, /share, basic tools).
# Module-specific additions (DPDK hugepages, NVIDIA driver, Docker) live in the
# respective modules/*/nix/guest.nix files.

{ extraEnvPackages ? [ ] }:

{ pkgs, lib, modulesPath, config, ... }:

let
  keys = map (key: "${builtins.getEnv "HOME"}/.ssh/${key}") [
    "id_rsa.pub"
    "id_ecdsa.pub"
    "id_ed25519.pub"
  ];
in
{
  imports = [
    (modulesPath + "/profiles/qemu-guest.nix")
  ];

  # Enable all non-free firmware
  hardware.enableAllFirmware = true;
  hardware.enableRedistributableFirmware = true;
  hardware.firmware = [ pkgs.linux-firmware ];
  hardware.firmwareCompression = "none";
  nixpkgs.config.allowUnfree = true;

  # Create symlink from /lib/firmware to NixOS firmware location
  systemd.tmpfiles.rules = [
    "L+ /lib/firmware - - - - /run/current-system/firmware"
  ];

  nix.extraOptions = ''
    experimental-features = nix-command flakes
    keep-outputs = true
    keep-derivations = true
    auto-optimise-store = false
  '';
  nix.gc.automatic = false;

  # virtio-console login (udev doesn't pick up hvc0 automatically)
  systemd.services."serial-getty" = {
    wantedBy = [ "multi-user.target" ];
    serviceConfig.ExecStart = "${pkgs.util-linux}/sbin/agetty  --login-program ${pkgs.shadow}/bin/login --autologin root hvc0 --keep-baud vt100";
  };
  systemd.services."serial-getty@hvc0".enable = false;

  systemd.network.enable = true;
  # qemu network (for ssh)
  systemd.network.networks."10-eth0" = {
    matchConfig.Name = "eth0";
    address = [ "10.0.2.15/24" ];
    routes = [{ Gateway = "10.0.2.2"; }];
    networkConfig.DHCP = "no";
    linkConfig.RequiredForOnline = "routable";
  };
  # virtio-net
  systemd.network.networks."20-eth1" = {
    matchConfig.Name = "eth1";
    address = [ "172.44.0.2/24" ];
    networkConfig.DHCP = "no";
  };
  networking.useNetworkd = true;
  networking.firewall.enable = false;
  services.resolved.enable = true;

  # don't wait for network to be online
  systemd.services.systemd-networkd-wait-online.enable = false;
  systemd.services.NetworkManager-wait-online.enable = false;
  systemd.network.wait-online.enable = false;
  systemd.network.wait-online.anyInterface = false;

  # no auto-updates
  systemd.services.update-prefetch.enable = false;

  # override defaults from nixpkgs/modules/virtualization/container-config.nix
  services.udev.enable = lib.mkForce true;
  services.lvm.enable = lib.mkForce true;

  # login with empty password
  users.extraUsers.root.initialHashedPassword = "";
  services.openssh.enable = true;

  users.users.root.openssh.authorizedKeys.keyFiles =
    lib.filter builtins.pathExists keys;
  users.users.root.openssh.authorizedKeys.keys =
    [ (builtins.readFile ./ssh_key.pub) ];

  time.timeZone = "Europe/Berlin";
  i18n.defaultLocale = "en_US.UTF-8";
  system.stateVersion = "23.11";

  # host-file sharing
  fileSystems."/share" = {
    device = "share";
    fsType = "9p";
    options = [ "trans=virtio" "nofail" "msize=104857600" ];
  };

  # module shared data (job files, assets) — mounted from host at VM launch via -virtfs
  fileSystems."/shared" = {
    device = "shared";
    fsType = "9p";
    options = [ "trans=virtio" "nofail" "msize=104857600" ];
  };

  services.getty.helpLine = ''
    Log in as "root" with an empty password.
    If you are connect via serial console:
    Type Ctrl-a c to switch to the qemu console
    and `quit` to stop the VM.
  '';
  services.getty.autologinUser = lib.mkDefault "root";

  documentation.doc.enable = false;
  documentation.man.enable = false;
  documentation.nixos.enable = false;
  documentation.info.enable = false;
  programs.bash.completion.enable = false;
  programs.command-not-found.enable = false;

  environment.systemPackages = (with pkgs; [
    devmem2
    tmux
    vim
    git
    fio
    iperf
    cryptsetup
    lvm2
    jq
    sysstat # mpstat, iostat, sar

    # NVMe and PCI tools
    nvme-cli
    pciutils # lspci, setpci

    # Encryption and integrity tools
    fsverity-utils
    f2fs-tools
    parted

    kmod

    # Kernel module build tools
    gnumake
    gcc
    gdb
    pkg-config
    config.boot.kernelPackages.kernel.dev
  ]) ++ extraEnvPackages;

  # Set up environment for kernel module development
  environment.variables.KERNELDIR = "${config.boot.kernelPackages.kernel.dev}/lib/modules/${config.boot.kernelPackages.kernel.modDirVersion}/build";

  boot.loader.grub.enable = false;
  boot.initrd.enable = false;
  boot.isContainer = true;
  boot.loader.initScript.enable = true;
  networking.useHostResolvConf = lib.mkForce false;
}
