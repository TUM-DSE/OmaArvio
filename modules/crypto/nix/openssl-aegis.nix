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

  meta = old.meta // {
    description = "OpenSSL with support for AEGIS cipher suites";
    mainProgram = "openssl-aegis";
  };
})
