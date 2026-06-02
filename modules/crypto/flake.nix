{
  description = "crypto module: OpenSSL-AEGIS and tcrypt crypto benchmark actions";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Pinned separately because our custom openssl-aegis branch does not build
    # against newer nixpkgs.
    nixpkgs-openssl.url = "github:NixOS/nixpkgs/2fad6eac6077f03fe109c4d4eb171cf96791faa4";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, nixpkgs-openssl, core }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      pkgs-openssl = import nixpkgs-openssl {
        inherit system;
      };
    in
    {
      packages.${system} = {
        openssl-aegis = pkgs-openssl.callPackage ./nix/openssl-aegis.nix { };
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
