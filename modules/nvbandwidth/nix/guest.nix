{ self }: { pkgs, ... }:
{
  environment.systemPackages = [ self.packages.x86_64-linux.nvbandwidth ];
}
