# Core inv-only packages: tools used by inv commands for VM management and orchestration.
# These go into oma-arvio runtimeInputs only — NOT into devShells.benchmarking.

{ pkgs }:

with pkgs; [
  git
  git-lfs
  qemu
  snphost
  snpguest
]
