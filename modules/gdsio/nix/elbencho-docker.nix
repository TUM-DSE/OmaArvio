{ pkgs }:
# Pinned elbencho Docker image for reproducible benchmarks.
# To update the hash, run:
#   nix run nixpkgs#nix-prefetch-docker -- --image-name breuner/elbencho \
#     --image-tag master-ubuntu-cuda-multiarch --arch amd64 --os linux
pkgs.dockerTools.pullImage {
  imageName = "breuner/elbencho";
  imageDigest = "sha256:c030acb176e338d4e685fd60fb352f82a9dc97d08640af7772db7d13c2c47d60";
  sha256 = "sha256-Vs1UABWfOPAoULmLlaYl5D19Z5k73u71rXk+3wIsBfw=";
  finalImageTag = "master-ubuntu-cuda-multiarch";
  os = "linux";
  arch = "amd64";
}
