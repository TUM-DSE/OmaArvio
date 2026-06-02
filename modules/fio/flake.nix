{
  description = "fio module: FIO benchmarking infrastructure and plotting";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nvme = {
      url = "path:../nvme";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
    # Optional fio override: defaults to nixpkgs (which has no packages.${system}.fio →
    # falls back to pkgs.fio). Override this to mod-dpdk-spdk in the root flake to inject
    # the custom fio with SPDK plugin.
    dpdk-spdk = {
      url = "github:NixOS/nixpkgs/nixos-unstable";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, core, nvme, dpdk-spdk }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
      pkgsCuda = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        config.cudaSupport = true;
      };
      # Use dpdk-spdk's custom fio if available, otherwise fall back to pkgs.fio
      customFio = dpdk-spdk.packages.${system}.fio or pkgs.fio;
    in
    {
      packages.${system} = {
        # Jobs directory in the Nix store; exposed through sharedData.
        fio-jobs = pkgs.runCommand "fio-jobs" { } ''
          mkdir -p $out
          cp -a ${./jobs}/. $out/
        '';

        fio-cuda = import ./nix/fio-cuda.nix { inherit pkgsCuda; };
      };

      nixosModules.guest =
        import ./nix/guest.nix {
          inherit (self.packages.${system}) fio-jobs fio-cuda;
        };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            fio = customFio;
            fio-cuda = self.packages.${system}.fio-cuda;
            inherit (self.packages.${system}) fio-jobs;
          };
        sharedData = self.packages.${system}.fio-jobs;
      } // core.lib.mkModulePythonLib { name = "fio"; inherit self; };
    };
}
