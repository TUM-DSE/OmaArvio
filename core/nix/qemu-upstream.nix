{ pkgs }:
# Currently unused as we can use NixOS package without any modifications
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
  glusterfsSupport = false;
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
  dontWrapGapps = true;
  dontStrip = true;
  configureFlags = old.configureFlags ++ [
    "--target-list=x86_64-softmmu"
  ];

})
