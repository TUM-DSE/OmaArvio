{ pkgs, src, minimal ? false, enableDebug ? true }:

with pkgs;

let
  dpdkPackage = (dpdk.override {
    withExamples = [ "helloworld" ];
  }).overrideAttrs (old: {
    version = "tum-dse-local";

    # Use dpdk-src flake input instead of fetching from upstream
    src = src;

    # Disable incompatible-pointer-types errors
    env.NIX_CFLAGS_COMPILE = (old.env.NIX_CFLAGS_COMPILE or "") + " -Wno-error=incompatible-pointer-types";


    outputs = [
      "out"
      "examples"
    ];

    # Set debug log level and disable warnings as errors
    mesonFlags = (builtins.filter (flag: flag != "-Denable_docs=true") (old.mesonFlags or [ ])) ++ [
      "-Dc_args=-DRTE_LOG_LEVEL=RTE_LOG_DEBUG"
      "-Dplatform=native"
      "-Ddisable_libs=''"
      "-Dwerror=false"
    ] ++ lib.optionals minimal [
      # Minimal DPDK build for SPDK
      "-Denable_drivers=bus/pci,bus/vdev,bus/auxiliary,mempool/ring,mempool/bucket,crypto/aesni_mb,crypto/qat,compress/qat,common/qat,crypto/ipsec_mb"
      "-Ddisable_drivers=crypto/mlx5,common/mlx5"
      "-Denable_libs=eal,pci,malloc,memzone,mempool,ring,kvargs,version,cryptodev,mbuf,compressdev,vhost,dmadev,security,argparse"
    ];
  });
in
if enableDebug then enableDebugging dpdkPackage else dpdkPackage
