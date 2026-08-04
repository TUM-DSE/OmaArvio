{ openssl, fetchFromGitHub }:

openssl.overrideAttrs (old: {
  pname = "openssl-aegis";
  version = "3.6.0";

  src = fetchFromGitHub {
    owner = "aegis-aead";
    repo = "openssl";
    rev = "7404a2302bcee4643dfa4178167eb6617d9097f9";
    hash = "sha256-4a+tdlDBqDwtbVHOJmdqZJu7l93LjDmvBYa/EufBIug=";
  };

  configureFlags = old.configureFlags ++ [ "enable-aegis" ];

  doCheck = false;

  # Upstream installs the driver as bin/openssl, the same name pkgs.openssl
  # uses, and nothing about this build distinguishes the two on a PATH. Both
  # environments we assemble resolve the collision by position, not by content:
  # buildEnv keys its collision table on the store path of the *first*
  # occurrence of a package, so a later duplicate -- even one wrapped in
  # lib.hiPrio -- is skipped outright, and a shell's PATH ignores priorities
  # altogether. Whichever build sorts first therefore claims `openssl`, and the
  # loser contributes no files at all, dropping out of the environment's
  # closure. Install the driver under a second name that is ours alone, so
  # callers that need the AEGIS EVP algorithms can ask for it unambiguously.
  # This is also the name meta.mainProgram already advertises, so lib.getExe
  # resolves to an existing file for the first time.
  postInstall = old.postInstall + ''
    ln -s openssl "$bin/bin/openssl-aegis"
  '';

  meta = old.meta // {
    description = "OpenSSL with support for AEGIS cipher suites";
    mainProgram = "openssl-aegis";
  };
})
