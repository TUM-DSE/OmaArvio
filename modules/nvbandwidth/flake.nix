{
  description = "nvbandwidth module: NVIDIA GPU bandwidth measurement";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    mod-nvidia-cc = {
      url = "path:../nvidia_cc";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
  };

  outputs = { self, nixpkgs, core, mod-nvidia-cc }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        cudaSupport = true;
        config.cudaVersion = "13";
      };
    in
    {
      packages.${system} = {
        nvbandwidth = pkgs.callPackage ./nix/nvbandwidth.nix {
          cudaPackages = pkgs.cudaPackages_13_0;
        };
      };

      nixosModules.guest = import ./nix/guest.nix { inherit self; };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            inherit (self.packages.${system}) nvbandwidth;
          };
      } // core.lib.mkModulePythonLib { name = "nvbandwidth"; inherit self; };
    };
}
