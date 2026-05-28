{ pkgs, src, dpdk-local, enableDebug ? true }:

with pkgs;

let
  # SPDK's FIO plugin needs a built FIO directory with config-host.h and other generated files
  # We create a custom FIO build that preserves the entire build directory
  fio-for-spdk = fio.overrideAttrs (old: {
    outputs = [ "out" ];

    # Don't strip the build directory - we need it for SPDK
    dontStrip = true;

    # After building but before install, save the build directory
    preInstall = ''
      # Save the entire build directory to a separate location
      mkdir -p $out/fio-build
      cp -r . $out/fio-build/
    '';

    # Normal install to $out/bin, etc.
    # The build directory is preserved in $out/fio-build
  });

  # Point to the build directory with all generated headers
  fio-src = "${fio-for-spdk}/fio-build";

  # Build SPDK with FIO support
  spdk-with-fio-base = (spdk.override {
    # Override the dpdk dependency
    dpdk = dpdk-local;
  }).overrideAttrs (old: {
    version = "tum-dse-fork";

    patches = [ ];

    src = src;

    dpdk' = dpdk-local;

    # Override configureFlags to use our custom DPDK and FIO
    configureFlags = [
      "--with-fio=${fio-src}"
      "--with-dpdk=${dpdk-local}"
      "--with-crypto"
      "--with-shared"
    ];


    postPatch = ''
      patchShebangs .
      substituteInPlace python/Makefile \
        --replace-fail "uv pip install --prefix=\$(CONFIG_PREFIX)" \
                       "python3 -m pip install --no-deps --no-build-isolation --prefix=\$(CONFIG_PREFIX)"
    '';



    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++
      lib.optionals enableDebug [ breakpointHook ];

    # Replace dpdk' with dpdk-local in buildInputs
    buildInputs = (builtins.map
      (input:
        if input.pname or "" == "dpdk" then dpdk-local else input
      )
      (old.buildInputs or [ ]));
  });

  # Conditionally apply debug symbols
  spdk-with-fio = if enableDebug then enableDebugging spdk-with-fio-base else spdk-with-fio-base;


  # Create a wrapper script that sets up LD_PRELOAD for FIO
  spdk-fio-wrapper = writeScriptBin "spdk-fio" ''
    #!/usr/bin/env bash
    # SPDK FIO Plugin Wrapper
    # This script runs fio with the SPDK bdev plugin preloaded

    SPDK_FIO_PLUGIN="${spdk-with-fio}/lib/fio/spdk_nvme"

    if [ ! -f "$SPDK_FIO_PLUGIN" ]; then
      echo "Error: SPDK FIO plugin not found at $SPDK_FIO_PLUGIN" >&2
      echo "Make sure SPDK was built with --with-fio option" >&2
      exit 1
    fi

    # Run fio with SPDK plugin preloaded
    # Use the FIO binary from our custom build to ensure version compatibility
    exec env LD_PRELOAD="$SPDK_FIO_PLUGIN" ${fio-for-spdk}/bin/fio "$@"
  '';

in
# Return a derivation that includes both SPDK and the FIO wrapper
symlinkJoin {
  name = "spdk-local-with-fio";
  paths = [ spdk-with-fio spdk-fio-wrapper ];

  # Expose the FIO plugin path as a passthru attribute
  passthru = {
    fioPlugin = "${spdk-with-fio}/build/fio/spdk_bdev";
    spdkSrc = spdk-with-fio;
    fioSrc = fio-src;
    fio = fio-for-spdk; # The FIO package used for building
    dpdk = dpdk-local; # DPDK package used for building SPDK
  };
}
