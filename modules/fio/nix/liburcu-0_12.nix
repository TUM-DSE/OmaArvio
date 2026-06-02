{ pkgs }:

pkgs.stdenv.mkDerivation {
  pname = "liburcu";
  version = "0.12.5";

  src = pkgs.fetchgit {
    url = "https://git.lttng.org/userspace-rcu.git";
    rev = "69e1d3ae6bf47c03b566511371884f0ab1a8b0b1";
    sha256 = "1r39b1jaz5shkwjykffcmcjvz6w4drv61yghbs7rac4ipbmj4mla";
  };

  nativeBuildInputs = [ pkgs.autoconf pkgs.automake pkgs.libtool pkgs.perl ];

  preConfigure = "./bootstrap";
}
