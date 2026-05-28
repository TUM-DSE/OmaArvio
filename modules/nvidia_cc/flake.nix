{
  description = "nvidia-cc module: NVIDIA Confidential Computing GPU management";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, core }:
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
        gpu-admin-tools = pkgs.callPackage ./nix/gpu-admin-tools.nix { };
      };

      nixosModules.guest = import ./nix/guest.nix;

      lib = {
        hostPackages = _pkgs: [ ]; # no host-runner packages in this module
      } // core.lib.mkModulePythonLib {
        name = "nvidia_cc";
        inherit self;
        packages = [ self.packages.${system}.gpu-admin-tools ];
      };
    };
}
