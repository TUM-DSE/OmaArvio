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
  # Temporary use qemu branch with upstream guest_memfd fixes
  # See https://patchew.org/QEMU/20260527223036.4614-1-michael.roth@amd.com/
  src = pkgs.fetchFromGitHub {
    owner = "patchew-project";
    repo = "qemu";
    rev = "56e33c4d0b6cfb84144d9f033a87b1fcf947f9be";
    hash = "sha256-KirJMAVRoy8SPNXrvsxSVX5F3TdzaX2efCFIG1AzE50=";
    fetchSubmodules = false;
    leaveDotGit = true;
    postFetch = ''
      cd "$out"
      ${pkgs.git}/bin/git submodule update --init --recursive
      (
        for p in subprojects/*.wrap; do
          ${pkgs.meson}/bin/meson subprojects download "$(basename "$p" .wrap)"
          rm -rf subprojects/$(basename "$p" .wrap)/.git
        done
      )
      find subprojects -type d -name .git -prune -execdir rm -r {} +
    '';
  };

  dontWrapGapps = true;
  dontStrip = true;
  configureFlags = old.configureFlags ++ [
    "--target-list=x86_64-softmmu"
  ];

})
