{
  description = "gdsio module: GPU Direct Storage IO benchmarks (gdsio + elbencho)";

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
    mod-nvidia-cc = {
      url = "path:../nvidia_cc";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
  };

  outputs = { self, nixpkgs, core, nvme, mod-nvidia-cc }:
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
        elbencho-docker = pkgs.callPackage ./nix/elbencho-docker.nix { };
        gds-base-docker = pkgs.callPackage ./nix/gds-base-docker.nix { };

        # Jobs directory in the Nix store; exposed through sharedData.
        gdsio-jobs = pkgs.runCommand "gdsio-jobs" { } ''
          mkdir -p $out
          jobs_src=${./jobs}
          install -m 0444 $jobs_src/*.gdsio $out/
          if [ -d "$jobs_src/elbencho" ]; then
            mkdir -p $out/elbencho
            find "$jobs_src/elbencho" -name '*.elbencho' -exec install -m 0444 {} $out/elbencho/ \;
          fi
        '';
      };

      nixosModules.guest =
        import ./nix/guest.nix {
          inherit (self.packages.${system}) elbencho-docker gds-base-docker gdsio-jobs;
        };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            inherit (self.packages.${system}) elbencho-docker gds-base-docker gdsio-jobs;
          };
        sharedData = self.packages.${system}.gdsio-jobs;
      } // core.lib.mkModulePythonLib { name = "gdsio"; inherit self; };
    };
}
