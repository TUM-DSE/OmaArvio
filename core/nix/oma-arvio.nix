{ pkgs, self, modules }:

let
  hostPkgs = builtins.concatLists (map (m: (m.lib.hostPackages or (_: [ ])) pkgs) modules);
  guestMods = builtins.filter (x: x != null)
    (map (m: m.nixosModules.guest or null) modules);
  modPkgs = builtins.foldl' (acc: m: acc // (m.packages.${pkgs.stdenv.hostPlatform.system} or { })) { } modules;

  # ── Core: installed as a proper Python package via pyproject.toml ────────────
  corePkg = self.lib.pythonPackage pkgs;

  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    corePkg
    invoke
    click
    binary
    lxml
    ipython
    psutil
    plotly
    matplotlib
    seaborn
    requests
    ijson
    qemu
  ] ++ builtins.concatLists (map (f: f ps) moduleInvPythonPkgs));

  # ── Modules: each exports lib.pythonSrc — a derivation with tasks/ laid out
  # under $out/modules/<name>/tasks/ so the path can be added to PYTHONPATH. ──
  moduleSrcPkgs = builtins.filter (x: x != null)
    (map (m: if m ? lib.pythonSrc then m.lib.pythonSrc pkgs else null) modules);

  # ── Inv-only packages ────────────────────────────────────────────────────────
  # Core inv tools (VM management, git, attestation helpers)
  coreInvPkgs = self.lib.invPackages pkgs;
  # Module inv tools (e.g. gpu-admin-tools from nvidia_cc)
  moduleInvPkgs = builtins.concatLists
    (map (m: m.lib.invPackages or [ ]) modules);
  # Module Python package selectors (ps: [...]) merged into pythonEnv
  moduleInvPythonPkgs = map (m: m.lib.invPythonPackages or (_ps: [ ])) modules;

  # Aggregate per-module sharedData derivations into modules/<name>/ structure.
  moduleSharedData = pkgs.runCommand "module-shared-data"
    {
      nativeBuildInputs = [ pkgs.rsync ];
    } ''
    mkdir -p $out/modules
    ${pkgs.lib.concatStrings (map (m:
      pkgs.lib.optionalString (m.lib ? sharedData) ''
        module_out="$out/modules/${m.lib.pythonName}"
        mkdir -p "$module_out"
        rsync -a --copy-unsafe-links ${m.lib.sharedData}/ "$module_out"/
      ''
    ) modules)}
  '';

  # Colon-separated PYTHONPATH string (Nix store paths, known at build time)
  modulePythonPath = builtins.concatStringsSep ":"
    (map builtins.toString moduleSrcPkgs);

  # ── Auto-generated main.py ───────────────────────────────────────────────────
  # taskModuleNames is derived from lib.taskModule declared by each active module.
  taskModuleNames = builtins.filter (x: x != null)
    (map (m: m.lib.taskModule or null) modules);

  mainScript = pkgs.writeText "oma-arvio-main.py" ''
    import importlib
    from invoke import Collection, Program
    from core.tasks import build, vm
    from core.tasks.utils import utils

    ns = Collection()
    ns.add_collection(Collection.from_module(build))
    ns.add_collection(Collection.from_module(vm))
    ns.add_collection(Collection.from_module(utils))

    for mod_name in ${builtins.toJSON taskModuleNames}:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "register"):
            mod.register(ns)

    Program(namespace=ns, name="oma-arvio").run()
  '';
  # Shared shell snippet (avoids duplication)
  runDirect = ''
    nix_pythonpath="${modulePythonPath}"
    exec env PYTHONPATH="$nix_pythonpath''${PYTHONPATH:+:$PYTHONPATH}" \
      MODULE_SHARED_DATA="${moduleSharedData}" \
      python3 ${mainScript} "$@"
  '';

  runSudo = ''
    nix_pythonpath="${modulePythonPath}"
    exec sudo -H env \
      PATH="$PATH" \
      PYTHONPATH="$nix_pythonpath''${PYTHONPATH:+:$PYTHONPATH}" \
      MODULE_SHARED_DATA="${moduleSharedData}" \
      "$(command -v python3)" ${mainScript} "$@"
  '';

  # ── oma-arvio: runs as current user; oma-arvio-root: escalates via sudo ──────
  omaArvio = pkgs.writeShellApplication {
    name = "oma-arvio";
    runtimeInputs = [ pythonEnv ] ++ self.lib.hostPackages pkgs ++ hostPkgs ++ coreInvPkgs ++ moduleInvPkgs;
    text = runDirect;
  };

  omaArvioRoot = pkgs.writeShellApplication {
    name = "oma-arvio-root";
    runtimeInputs = [ pythonEnv ] ++ self.lib.hostPackages pkgs ++ hostPkgs ++ coreInvPkgs ++ moduleInvPkgs;
    text = runSudo;
  };
in
pkgs.symlinkJoin {
  name = "oma-arvio";
  paths = [ omaArvio omaArvioRoot ];
  passthru = {
    inherit moduleSharedData guestMods modPkgs hostPkgs;
  };
}
