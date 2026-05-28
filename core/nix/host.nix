# Core host packages: runner/benchmark tools used during host-side execution.
# These end up in both oma-arvio runtimeInputs and devShells.benchmarking.

{ pkgs }:

with pkgs; [
  numactl
  sysstat # sar, mpstat, iostat
]
