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

  # A name of our own: bin/openssl collides with pkgs.openssl, and on a shared
  # PATH the loser contributes nothing at all.
  postInstall = old.postInstall + ''
    ln -s openssl "$bin/bin/openssl-aegis"
  '';

  meta = old.meta // {
    description = "OpenSSL with support for AEGIS cipher suites";
    mainProgram = "openssl-aegis";
  };
})
