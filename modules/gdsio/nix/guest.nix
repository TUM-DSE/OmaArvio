# gdsio NixOS guest additions:
# Docker, NVIDIA container toolkit, pre-loaded elbencho image, gds-base-docker.

{ elbencho-docker
, gds-base-docker
, gdsio-jobs
}:

{ pkgs, ... }:

{
  virtualisation.docker.enable = true;
  virtualisation.docker.enableOnBoot = true;
  virtualisation.docker.storageDriver = "overlay2";

  hardware.nvidia-container-toolkit.enable = true;

  # Pre-load elbencho Docker image from Nix store at boot (avoids internet pull)
  systemd.services.load-elbencho-docker = {
    description = "Pre-load elbencho Docker image from Nix store";
    after = [ "docker.service" ];
    requires = [ "docker.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.docker}/bin/docker load -i ${elbencho-docker}";
    };
  };

  environment.systemPackages = [
    pkgs.docker
    gds-base-docker
    gdsio-jobs
  ];
}
