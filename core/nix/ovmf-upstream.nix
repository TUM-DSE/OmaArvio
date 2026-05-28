{ pkgs }:

with pkgs;
OVMF.fd.overrideAttrs (old: {
  src = fetchFromGitHub {
    owner = "tianocore";
    repo = "edk2";
    # master 2025-10-10
    rev = "80eaa563ec1cd2272334134d5fd35d856843fe65";
    sha256 = "sha256-ufUG/bXAxsp9YqYcGzXEZ55/f0xx/X36yp4zxZFuFXc=";
    fetchSubmodules = true;
  };
})
