# gdsio host packages for the devShell (Docker + GDS container images).

{ pkgs, elbencho-docker, gds-base-docker, gdsio-jobs }:

with pkgs; [
  docker

  # Load the pinned elbencho image once before host benchmarks:
  #   load-elbencho-docker
  (pkgs.writeShellScriptBin "load-elbencho-docker" ''
    docker load -i ${elbencho-docker}
  '')

  # Builds GDS base image from Dockerfile in Nix store:
  #   build-gds-base-docker
  gds-base-docker

  # Jobs directory in the Nix store; exposed through sharedData.
  gdsio-jobs
]
