{
  description = "crypto module: OpenSSL-AEGIS and tcrypt crypto benchmark actions";

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
      };
    in
    {
      packages.${system} = {
        openssl-aegis = pkgs.callPackage ./nix/openssl-aegis.nix { };
      };

      nixosModules.guest = import ./nix/guest.nix { inherit self; };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            inherit (self.packages.${system}) openssl-aegis;
          };
      } // core.lib.mkModulePythonLib { name = "crypto"; inherit self; };
    };
}
