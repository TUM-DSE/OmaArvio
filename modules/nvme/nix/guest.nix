# nvme guest additions: openssl, used for fast pseudo-random test file generation.

{}: { pkgs, ... }:

{
  environment.systemPackages = [ pkgs.openssl ];
}
