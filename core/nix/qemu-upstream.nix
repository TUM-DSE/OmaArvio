{ pkgs }:
with pkgs;
(qemu_full.override {
  guestAgentSupport = false;
  seccompSupport = false;
  alsaSupport = false;
  pulseSupport = false;
  pipewireSupport = false;
  sdlSupport = false;
  jackSupport = false;
  gtkSupport = false;
  vncSupport = false;
  smartcardSupport = false;
  spiceSupport = false;
  ncursesSupport = false;
  usbredirSupport = false;
  xenSupport = false;
  cephSupport = false;
  openGLSupport = false;
  virglSupport = false;
  libiscsiSupport = false;
  smbdSupport = false;
  tpmSupport = false;
  uringSupport = false;
  canokeySupport = false;
  capstoneSupport = false;
  enableDocs = false;
  enableTools = false;
}).overrideAttrs (new: old: {
  # Fix page state change notification when a conversion range spans multiple
  # RAMBlocks (one memory-backend-memfd per NUMA node on SEV-SNP guests).
  patches = (old.patches or [ ]) ++ [
    ./patches/qemu/kvm.patch
  ];

  dontWrapGapps = true;
  dontStrip = true;

  enableParallelBuilding = true;

  configureFlags = old.configureFlags ++ [
    "--target-list=x86_64-softmmu"
  ];

})
