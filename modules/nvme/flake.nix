{
  description = "nvme module: NVMe device management and SSD preconditioning utilities";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Optional fio override: defaults to nixpkgs (which has no packages.${system}.fio →
    # falls back to pkgs.fio). Override this to mod-dpdk-spdk in the root flake to inject
    # the custom fio with SPDK plugin.
    dpdk-spdk = {
      url = "github:NixOS/nixpkgs/nixos-unstable";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, core, dpdk-spdk }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
      # Use dpdk-spdk's custom fio if available, otherwise fall back to pkgs.fio
      customFio = dpdk-spdk.packages.${system}.fio or pkgs.fio;
    in
    {
      lib = {
        hostPackages = pkgs: import ./nix/host.nix { inherit pkgs; fio = customFio; };
      } // core.lib.mkModulePythonLib { name = "nvme"; inherit self; };
    };
}
