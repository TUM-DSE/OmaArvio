{
  description = "DPDK test environment with SEV-SNP support";

  inputs = {
    self.submodules = true;
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pre-commit-hooks.url = "github:cachix/pre-commit-hooks.nix";

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # ── Core ──────────────────────────────────────────────────────────────────
    core = {
      url = "path:./core";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # ── Module registry: add/remove modules here ──────────────────────────────
    mod-nvme = {
      url = "path:./modules/nvme";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
      # Inject dpdk_spdk so nvme uses the custom fio (with SPDK plugin) for preconditioning
      inputs.dpdk-spdk.follows = "mod-dpdk-spdk";
    };
    mod-fio = {
      url = "path:./modules/fio";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
      inputs.nvme.follows = "mod-nvme";
      # Inject dpdk_spdk so fio uses the custom fio binary
      inputs.dpdk-spdk.follows = "mod-dpdk-spdk";
    };
    mod-crypto = {
      url = "path:./modules/crypto";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
    mod-dpdk-spdk = {
      url = "path:./modules/dpdk_spdk";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
    mod-nvidia-cc = {
      url = "path:./modules/nvidia_cc";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
    };
    mod-nvbandwidth = {
      url = "path:./modules/nvbandwidth";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
      inputs.mod-nvidia-cc.follows = "mod-nvidia-cc";
    };
    mod-gdsio = {
      url = "path:./modules/gdsio";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.core.follows = "core";
      inputs.nvme.follows = "mod-nvme";
      inputs.mod-nvidia-cc.follows = "mod-nvidia-cc";
    };
    # ─────────────────────────────────────────────────────────────────────────
  };

  outputs =
    { self
    , nixpkgs
    , flake-utils
    , pre-commit-hooks
    , core
    , ...
    }@inputs:
    let
      # ── Auto-discover active modules: any mod-* input is active ──────────
      activeModules =
        map (name: inputs.${name})
          (builtins.filter
            (name: builtins.match "^mod-.*" name != null)
            (builtins.attrNames inputs));
      # ─────────────────────────────────────────────────────────────────────
      guestSystem = core.lib.mkGuestSystem { modules = activeModules; };
    in
    (flake-utils.lib.eachSystem [ "x86_64-linux" ] (system:
    let
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        cudaSupport = true;
        config.cudaVersion = "13";
      };

      pre-commit-check = pre-commit-hooks.lib.${system}.run {
        src = ./.;
        hooks = {
          nixpkgs-fmt.enable = true;
          black.enable = true;
        };
      };

      oma-arvio = core.lib.mkOmaArvio { inherit pkgs; modules = activeModules; };
    in
    rec {
      packages = core.packages.${system} // oma-arvio.modPkgs // {
        inherit oma-arvio;
        inherit (guestSystem.packages) snp-guest-image direct-guest-image;
        default = guestSystem.packages.snp-guest-image;
      };

      devShells.default = pkgs.mkShell {
        buildInputs = [ packages.oma-arvio ]
        ++ pre-commit-check.enabledPackages;
        shellHook = pre-commit-check.shellHook;
      };

      devShells.kernel-build = core.devShells.${system}.kernel-build;

      devShells.benchmarking = pkgs.mkShell {
        name = "benchmarking";
        packages = core.lib.hostPackages pkgs ++ oma-arvio.hostPkgs;
      };
    })) // {
      lib.nixpkgsRev = nixpkgs.shortRev;

      nixosConfigurations = guestSystem.nixosConfigurations;
    };
}
