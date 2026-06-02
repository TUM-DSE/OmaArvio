{
  description = "cvm-dpdk-spdk core: generic VM/build/plotting abstractions";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixpkgs-2505.url = "github:NixOS/nixpkgs/nixos-25.05";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, nixpkgs-2505, disko }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      pkgs-2505 = import nixpkgs-2505 {
        inherit system;
        config.allowUnfree = true;
      };
    in
    {
      packages.${system} = {
        qemu-amd = pkgs-2505.callPackage ./nix/qemu-amd.nix { pkgs = pkgs; };
        qemu-upstream = pkgs.callPackage ./nix/qemu-upstream.nix { pkgs = pkgs; };
        ovmf-amd-sev-snp = pkgs.callPackage ./nix/ovmf-amd-sev-snp.nix { pkgs = pkgs-2505; };
        ovmf-upstream = pkgs.OVMF.fd;
      };

      # NixOS module: base guest config (networking, SSH, /share, basic tools)
      # Usage: import with optional extraEnvPackages arg
      lib.guestBaseModule = args: import ./nix/guest-base.nix args;

      # Runner tools (used during benchmarks on the host, analogous to guest packages).
      lib.hostPackages = pkgs: import ./nix/host.nix { inherit pkgs; };

      # Inv-only tools (used only by inv commands for VM management / orchestration).
      lib.invPackages = pkgs: import ./nix/inv.nix { inherit pkgs; };

      # Python package: installs core.tasks.* into site-packages.
      # Uses pyproject.toml with package-dir mapping "core" -> "." so that
      # tasks/ inside core/ is importable as core.tasks.
      # Returns the pythonSrc/pythonName/taskModule/invPackages/invPythonPackages lib
      # attributes for a module.  Takes the module name and its self (flake source root).
      #
      # Optional args:
      #   packages        — pre-built derivations added to oma-arvio runtimeInputs only
      #                     (inv-command tools that should not go into hostPackages)
      #   pythonPackages  — ps: [...] selector merged into oma-arvio's pythonEnv
      #
      # Usage (module only has these attrs):
      #   lib = core.lib.mkModulePythonLib { name = "nvme"; inherit self; };
      # Usage (module also has other lib attrs):
      #   lib = { hostPackages = ...; } // core.lib.mkModulePythonLib { name = "nvme"; inherit self; };
      # Usage (with inv-only packages):
      #   lib = { hostPackages = _pkgs: []; }
      #         // core.lib.mkModulePythonLib {
      #              name = "nvidia_cc"; inherit self;
      #              packages = [ self.packages.x86_64-linux.gpu-admin-tools ];
      #            };
      lib.mkModulePythonLib = { name, self, packages ? [ ], pythonPackages ? _ps: [ ] }:
        {
          pythonSrc = pkgs: pkgs.runCommand "cvm-mod-${name}-src" { } ''
            mkdir -p "$out/modules/${name}"
            cp -r ${self}/tasks "$out/modules/${name}/tasks"
            touch "$out/modules/${name}/__init__.py"
          '';
          pythonName = name;
          taskModule = "modules.${name}.tasks";
          invPackages = packages; # pre-built derivations → oma-arvio runtimeInputs
          invPythonPackages = pythonPackages; # ps: [...] selector  → oma-arvio pythonEnv
        };

      lib.mkOmaArvio = { pkgs, modules }:
        pkgs.callPackage ./nix/oma-arvio.nix { inherit self modules; };

      lib.mkGuestSystem = { modules }:
        let
          nixosSystem = nixpkgs.lib.nixosSystem;
          applySnpPatches = import ./nix/apply-snp-patches.nix;
          linux_snp = applySnpPatches pkgs.linux_latest;
          kernelPackages = pkgs.linuxPackagesFor linux_snp;
          kernelConfig = { config, lib, pkgs, ... }: {
            boot.kernelPackages = kernelPackages;
          };
          guestMods = builtins.filter (x: x != null)
            (map (m: m.nixosModules.guest or null) modules);
          guestConfig = import ./nix/guest-base.nix {
            extraEnvPackages = [ pkgs.snpguest ];
          };
          nixosConfigurations = {
            snp-guest = nixosSystem {
              system = "x86_64-linux";
              modules = [ disko.nixosModules.disko guestConfig kernelConfig ]
                ++ guestMods
                ++ [ (import ./nix/disko-guest.nix { enableBootloader = true; }) ];
            };
            direct-guest = nixosSystem {
              system = "x86_64-linux";
              modules = [ disko.nixosModules.disko guestConfig kernelConfig ]
                ++ guestMods
                ++ [ (import ./nix/disko-guest.nix { enableBootloader = false; }) ];
            };
          };
        in
        {
          inherit nixosConfigurations;
          packages = {
            snp-guest-image = nixosConfigurations.snp-guest.config.system.build.diskoImagesScript;
            direct-guest-image = nixosConfigurations.direct-guest.config.system.build.diskoImagesScript;
          };
        };

      lib.pythonPackage = pkgs:
        let
          src = pkgs.runCommand "cvm-core-src" { } ''
            mkdir -p "$out/nix"
            cp ${self + "/pyproject.toml"} "$out/pyproject.toml"
            cp -r ${self + "/tasks"} "$out/tasks"
            cp ${self + "/nix/ssh_key"} "$out/nix/ssh_key"
            cp ${self + "/nix/ssh_key.pub"} "$out/nix/ssh_key.pub"
          '';
        in
        pkgs.python3.pkgs.buildPythonPackage {
          pname = "cvm-core";
          version = "0.1.0";
          pyproject = true;
          inherit src;
          build-system = [ pkgs.python3.pkgs.setuptools ];
          dependencies = [ pkgs.python3.pkgs.humanfriendly ];
        };

      devShells.${system}.kernel-build = pkgs.mkShell {
        name = "linux-kernel-build";
        packages = with pkgs; [
          gnumake
          gcc
          binutils
          bc
          flex
          bison
          perl
          python3
          pkg-config
          pahole
          cpio
          rsync
          elfutils
          ncurses
          openssl
          zlib
        ];
      };
    };
}
