# nvme guest additions: openssl + rust-parallel, used for fast pseudo-random test file generation.

{}: { pkgs, ... }:

{
  environment.systemPackages = [ pkgs.openssl pkgs.rust-parallel ];
}
