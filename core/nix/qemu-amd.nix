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
  # Use AMDESE QEMU with snp-certs-rfc3-wip1 branch for certs-path
  src = pkgs.fetchFromGitHub {
    owner = "AMDESE";
    repo = "qemu";
    rev = "cc6594b0ae3327af2a107ff6925ef002dcf228ac";
    hash = "sha256-VHhcoKjhkJQTyMu7b5/EN8IJyyK1TaDvXnVIW++TwPs=";
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
