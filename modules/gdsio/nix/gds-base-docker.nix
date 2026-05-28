{ pkgs }:
# gds-base Docker build helper.
# Bundles the Dockerfile in the Nix store and provides build-gds-base-docker,
# which builds the image using docker. Requires network access (apt-get inside
# the container for gds-tools-13-2 from the CUDA apt repo).
pkgs.writeShellScriptBin "build-gds-base-docker" ''
  docker build -t gds-base:latest ${../images/gds-base}
''
