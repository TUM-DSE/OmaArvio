{ lib
, python3Packages
, fetchFromGitHub
}:

python3Packages.buildPythonApplication {
  pname = "gpu-admin-tools";
  version = "2026.05.07";

  src = fetchFromGitHub {
    owner = "NVIDIA";
    repo = "gpu-admin-tools";
    rev = "v2026.05.07";
    hash = "sha256-Iph8H/uayJcbWVsFUEsbkUhXd+z9dGh1KchdXY6LUCA=";
  };

  format = "other";

  # No external Python dependencies, uses only stdlib
  propagatedBuildInputs = [ ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall

    # Install all Python modules and scripts
    mkdir -p $out/lib/gpu-admin-tools
    cp -r . $out/lib/gpu-admin-tools/

    # Create wrapper script for nvidia_gpu_tools.py
    mkdir -p $out/bin
    cat > $out/bin/nvidia-gpu-tools << 'EOF'
    #!/usr/bin/env bash
    exec ${python3Packages.python}/bin/python3 "$out_lib/nvidia_gpu_tools.py" "$@"
    EOF
    chmod +x $out/bin/nvidia-gpu-tools

    # Substitute the path in the wrapper
    substituteInPlace $out/bin/nvidia-gpu-tools \
      --replace '$out_lib' "$out/lib/gpu-admin-tools"

    runHook postInstall
  '';

  meta = with lib; {
    description = "NVIDIA GPU Admin Tools for Confidential Computing configuration";
    homepage = "https://github.com/NVIDIA/gpu-admin-tools";
    license = licenses.asl20;
    maintainers = [ ];
    platforms = platforms.linux;
    mainProgram = "nvidia-gpu-tools";
  };
}
