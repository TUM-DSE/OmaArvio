{
  description = "dpdk-spdk module: DPDK/SPDK packages and storage benchmark actions";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    dpdk-src = {
      url = "github:DPDK/dpdk/v26.03";
      flake = false;
    };
    spdk-src = {
      url = "git+https://github.com/spdk/spdk?ref=refs/tags/v26.01&submodules=1";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, core, dpdk-src, spdk-src }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        cudaSupport = true;
        config.cudaVersion = "13";
      };
      config = { enableDebug = false; };
    in
    {
      packages.${system} = {
        dpdk = pkgs.callPackage ./nix/dpdk-local.nix {
          pkgs = pkgs;
          src = dpdk-src;
          minimal = true;
          enableDebug = config.enableDebug;
        };
        spdk = pkgs.callPackage ./nix/spdk-local.nix {
          pkgs = pkgs;
          src = spdk-src;
          dpdk-local = self.packages.${system}.dpdk;
          enableDebug = config.enableDebug;
        };
        # Expose the custom fio (built for SPDK plugin compatibility) so that the
        # nvme and fio modules can override their fio package via the dpdk-spdk input.
        fio = self.packages.${system}.spdk.fio;
      };

      nixosModules.guest = import ./nix/guest.nix { inherit self; };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            inherit (self.packages.${system}) spdk dpdk;
          };
      } // core.lib.mkModulePythonLib { name = "dpdk_spdk"; inherit self; };
    };
}
