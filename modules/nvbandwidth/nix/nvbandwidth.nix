{ lib
, fetchFromGitHub
, cmake
, cudaPackages
, boost
, autoAddDriverRunpath
, breakpointHook
}:

cudaPackages.backendStdenv.mkDerivation rec {
  pname = "nvbandwidth";
  version = "0.8";

  src = fetchFromGitHub {
    owner = "NVIDIA";
    repo = "nvbandwidth";
    rev = "v${version}";
    hash = "sha256-PhJY7F0aGNoejLlhSNT3p3PjYKfywCq2nZGvHTu0Q/8=";

  };

  nativeBuildInputs = [
    cmake
    autoAddDriverRunpath
    breakpointHook
  ];

  buildInputs = [
    boost
    cudaPackages.cudatoolkit
  ];

  # Patch CMakeLists.txt to:
  # - Handle missing /etc/os-release in Nix sandbox
  # - Force shared Boost libs since that's what Nix provides
  # - Remove git-based GIT_VERSION and use package version instead
  postPatch = ''
        # Handle missing /etc/os-release in Nix sandbox
        substituteInPlace CMakeLists.txt \
          --replace-fail 'file(READ "/etc/os-release" OS_RELEASE_CONTENT)' \
                         'if(EXISTS "/etc/os-release")
            file(READ "/etc/os-release" OS_RELEASE_CONTENT)
        else()
            set(OS_RELEASE_CONTENT "")
        endif()'

        # Force shared Boost libs since that's what Nix provides
        substituteInPlace CMakeLists.txt \
          --replace-fail 'set(Boost_USE_STATIC_LIBS ON)' \
                         'set(Boost_USE_STATIC_LIBS OFF)'

        # Remove git-based GIT_VERSION
        substituteInPlace CMakeLists.txt \
          --replace-fail 'execute_process(
        COMMAND git describe --always --tags
        WORKING_DIRECTORY ''${CMAKE_CURRENT_LIST_DIR}
        OUTPUT_VARIABLE GIT_VERSION
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    set(CMAKE_CXX_FLAGS "''${CMAKE_CXX_FLAGS} -DGIT_VERSION=\\\"\"''${GIT_VERSION}\"\\\"")' \
                         ""

        # Use package version instead
        substituteInPlace CMakeLists.txt \
          --replace-fail 'target_link_libraries(nvbandwidth Boost::program_options ''${NVML_LIB_NAME} cuda)' \
                         'target_link_libraries(nvbandwidth Boost::program_options ''${NVML_LIB_NAME} cuda)
    target_compile_definitions(nvbandwidth PRIVATE GIT_VERSION="v${version}")'
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin
    cp nvbandwidth $out/bin/
    runHook postInstall
  '';

  meta = with lib; {
    description = "A tool for bandwidth measurements on NVIDIA GPUs";
    homepage = "https://github.com/NVIDIA/nvbandwidth";
    license = licenses.asl20;
    maintainers = [ ];
    platforms = platforms.linux;
    mainProgram = "nvbandwidth";
  };
}
