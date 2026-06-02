{ pkgsCuda }:

let
  # cuFile probes for rcu_flavor_bp which was removed in liburcu 0.13+; build 0.12.5.
  liburcu-compat = import ./liburcu-0_12.nix { pkgs = pkgsCuda; };
in

pkgsCuda.fio.overrideAttrs (old: {
  buildInputs = old.buildInputs ++ [
    pkgsCuda.cudaPackages.libcufile
    pkgsCuda.cudaPackages.cuda_cudart
  ];

  nativeBuildInputs = old.nativeBuildInputs ++ [
    pkgsCuda.cudaPackages.cuda_nvcc
    pkgsCuda.autoAddDriverRunpath
    pkgsCuda.makeWrapper
  ];

  configureFlags = (old.configureFlags or [ ]) ++ [
    "--enable-cuda"
    "--enable-libcufile"
  ];

  preConfigure = (old.preConfigure or "") + ''
    export NIX_CFLAGS_COMPILE="$NIX_CFLAGS_COMPILE -I${pkgsCuda.cudaPackages.cuda_nvcc}/include"
  '';

  env = (old.env or { }) // {
    LDFLAGS = toString [
      "-L${pkgsCuda.lib.getOutput "stubs" pkgsCuda.cudaPackages.cuda_cudart}/lib/stubs"
    ];
  };

  # libcufile dlopen's several libs at runtime; wrap the binary so they can be found.
  postInstall = (old.postInstall or "") + ''
    mv $out/bin/fio $out/bin/fio-cuda

    wrapProgram $out/bin/fio-cuda \
      --suffix LD_LIBRARY_PATH : $out/lib:${pkgsCuda.lib.makeLibraryPath [ pkgsCuda.util-linux pkgsCuda.systemd pkgsCuda.numactl liburcu-compat ]}
  '';
})
