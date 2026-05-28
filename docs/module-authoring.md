# Module Authoring Guide

This guide documents how to add a new module to the test framework. Treat the
current `core/`, `modules/`, and root `flake.nix` structure as the source of
truth.

## Module Contract

Create a module under `modules/<name>/`. Use a Python-safe directory name because
the task package is imported as `modules.<name>.tasks`. Use underscores in the
directory and Python package name when needed, for example `modules/dpdk_spdk`.
The root flake input may still use a hyphenated `mod-*` name, such as
`mod-dpdk-spdk`.

Every module should have a `flake.nix` that imports `nixpkgs` and `core`, then
exports only the surfaces it needs:

```nix
{
  description = "example module: short purpose";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    core = {
      url = "path:../../core";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, core }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
    in
    {
      packages.${system} = {
        example-tool = pkgs.callPackage ./nix/example-tool.nix { };
      };

      nixosModules.guest = import ./nix/guest.nix { inherit self; };

      lib = {
        hostPackages = pkgs:
          import ./nix/host.nix {
            inherit pkgs;
            inherit (self.packages.${system}) example-tool;
          };
      } // core.lib.mkModulePythonLib { name = "example"; inherit self; };
    };
}
```

The exported surfaces are:

- `packages.${system}`: optional package outputs built by this module.
- `nixosModules.guest`: optional NixOS module merged into guest images.
- `lib.hostPackages`: host/devShell packages needed while running benchmarks on
  the host.
- `core.lib.mkModulePythonLib`: registers the module's Python tasks with
  `oma-arvio`.

Use `mkModulePythonLib` options when needed:

- `packages = [ ... ]`: pre-built derivations that should be runtime inputs for
  `oma-arvio`, but not general host benchmark packages. `nvidia_cc` uses this for
  `gpu-admin-tools`.
- `pythonPackages = ps: [ ... ]`: extra Python packages merged into the
  `oma-arvio` Python environment.

## Expected Layout

A typical module uses this shape:

```text
modules/<name>/
|-- __init__.py
|-- flake.nix
|-- nix/
|   |-- guest.nix
|   |-- host.nix
|   `-- package-or-tool.nix
`-- tasks/
    |-- __init__.py
    |-- actions/
    |   |-- __init__.py
    |   `-- benchmark.py
    |-- plotting/
    |   `-- plot_benchmark.py
    `-- commands.py
```

Only add the pieces the module needs.

### Guest Image Configuration

Put guest image additions in `nix/guest.nix`. This is for packages, services,
kernel modules, kernel parameters, environment variables, and guest-only system
configuration.

Keep guest config module-specific. Examples in the current tree:

- `modules/dpdk_spdk/nix/guest.nix`: hugepages, VFIO modules, DPDK/SPDK tools.
- `modules/nvidia_cc/nix/guest.nix`: NVIDIA driver and confidential computing
  mode configuration.
- `modules/gdsio/nix/guest.nix`: Docker, NVIDIA container toolkit, and GDS
  image preload service.

Do not put generic VM baseline behavior in a module. Generic networking, SSH,
`/share`, base tools, and ratsd are owned by `core/nix/guest-base.nix`.

### Host Package Requirements

Put host runner and devShell packages in `nix/host.nix`, and expose them through
`lib.hostPackages`.

Use this for binaries needed by host benchmark actions, setup commands, or
manual debugging in `nix develop`. Return an empty list if the module only adds
Python tasks or inv-only tools:

```nix
{ pkgs, example-tool }:

[
  example-tool
  pkgs.pciutils
]
```

### Library Configuration

Use `packages.${system}` for derivations that should be available to the root
flake or other modules. Pass package outputs explicitly into `host.nix` and
`guest.nix` instead of rebuilding or looking them up by path.

If a module depends on another module's package, declare that dependency as a
flake input and wire it from the root flake with `follows`. `fio` and `nvme`
optionally consume `mod-dpdk-spdk` to use the custom SPDK-compatible `fio`.

### Task Commands

Task modules are loaded through `core.lib.mkModulePythonLib`, which copies
`tasks/` into a generated Python package. The import path is
`modules.<name>.tasks`.

Each module's `tasks/__init__.py` should expose a `register(ns)` function:

```python
"""example module tasks."""


def register(ns):
    from core.tasks.actions.module import register_module
    from modules.example.tasks import commands

    register_module(ns, actions="modules.example.tasks.actions", commands=commands)
```

Use task commands for user-facing `inv <collection>.<task>` commands. These
commands usually prepare configuration, select devices, and call
`core.tasks.vm.start` with `action="run-<action-name>"`.

### VM Actions

Use actions when code should run inside the unified VM/host benchmark path.
Actions are registered with `@register_action` from `core.tasks.actions.registry`
and are invoked through `inv vm.start --action run-<name>` or through a module
task that calls `vm_start(...)`.

```python
from core.tasks.actions.registry import register_action
from core.tasks.actions import ActionContext


def _example_path(name: str, action_config: dict) -> tuple[str, ...]:
    variant = action_config.get("variant", "default")
    return ("example", name, variant)


@register_action("example", path_fn=_example_path)
def run_example(ctx: ActionContext, variant="default", **kwargs):
    result = ctx.vm.ssh_cmd(["example-tool", "--variant", variant], check=True)
    (ctx.outputdir_host / f"{ctx.timestamp}.txt").write_text(result.stdout)
```

The action runner computes the output directory once from `path_fn` and passes it
through `ActionContext`. Do not call `get_benchmark_output_path` inside actions;
use `ctx.outputdir_host`, `ctx.outputdir_guest`, and `ctx.timestamp` so SAR,
perf, VFIO traces, and benchmark outputs land together.

For host-specific devices, construct `Devices` from `core.tasks.utils.device`
instead of reading `config.toml` directly. `Devices(hostname=None)` resolves
the current host by default and exposes full and short PCI BDF forms. Storage
benchmarks should call `devices.storage_target(setup, qemu_nvme=..., spdk=...)`
to select host NVMe, passthrough NVMe, or QEMU-emulated NVMe consistently.

### Plotting Tasks

If the module adds plotting, keep parsers and plotting code under
`tasks/plotting/` and expose a normal Invoke command from the module. For
paper-style Matplotlib plots, use `core.tasks.plotting.common` for shared sizes,
colors, hatches, font settings, and axis styling. For SAR data, use the core SAR
parser and plotting helpers where possible.

Avoid inventing a separate visual style unless the plot is intentionally
interactive or exploratory. Existing examples include:

- `modules/fio/tasks/plotting/plot_fio.py` for interactive FIO plots.
- `core/tasks/plotting/common.py` for shared paper-plot styling.
- `core/tasks/plotting/plot_sar.py` for SAR visualization.

### Packaged Assets

For static jobs, scripts, templates, or container image inputs, package them in
Nix and expose them through `lib.sharedData`. Avoid relying on relative
source-tree paths or environment variables at runtime.

Declare a `sharedData` attribute in the module's `lib` pointing to a Nix
derivation whose contents are the files to share:

```nix
lib = {
  hostPackages = pkgs: import ./nix/host.nix { ... };
  sharedData = self.packages.${system}.my-jobs;
} // core.lib.mkModulePythonLib { name = "example"; inherit self; };
```

The root flake assembles all `lib.sharedData` derivations into a single
`moduleSharedData` package structured as `modules/<module_name>/`. This is
exposed as `/shared` in both the guest VM (via virtfs at launch time, identical
to `/share`) and in the HostRunner's systemd scope (via `BindPaths`).
Relative symlinks inside a module's shared data are preserved when they resolve
within that module's shared data root. Symlinks that resolve outside that root
are copied into the assembled package instead.

Python code accesses assets via the stable path `/shared/modules/<name>/`:

```python
def _jobs_dir() -> Path:
    return Path("/shared/modules/example")
```

For FIO-based actions, pass `job_dirs` explicitly rather than relying on a
global search path. Modules that provide their own FIO jobs expose them at
`/shared/modules/<name>/` and pass `job_dirs=["/shared/modules/<name>"]` (or a
combined list including the base fio module) when calling `run_fio`:

```python
vm_start(action="run-fio", action_config={
    "job": "my_job",
    "job_dirs": ["/shared/modules/example", "/shared/modules/fio"],
    ...
})
```

## Root Flake Integration

Add the module as a root `mod-*` input in `flake.nix`:

```nix
mod-example = {
  url = "path:./modules/example";
  inputs.nixpkgs.follows = "nixpkgs";
  inputs.core.follows = "core";
};
```

For cross-module dependencies, add a module input and wire it with `follows`:

```nix
mod-example = {
  url = "path:./modules/example";
  inputs.nixpkgs.follows = "nixpkgs";
  inputs.core.follows = "core";
  inputs.mod-nvidia-cc.follows = "mod-nvidia-cc";
};
```

The root flake auto-discovers active modules from input names matching `mod-*`:

```nix
activeModules =
  map (name: inputs.${name})
    (builtins.filter
      (name: builtins.match "^mod-.*" name != null)
      (builtins.attrNames inputs));
```

Once the input is present, root composition happens automatically:

- `m.lib.hostPackages` is appended to host/devShell package lists.
- `m.nixosModules.guest` is merged into `snp-guest` and `direct-guest`.
- `m.packages.${system}` is merged into root `packages`.
- `m.lib.taskModule` is loaded by `oma-arvio`, and `register(ns)` is called if it
  exists.
- `m.lib.invPackages` and `m.lib.invPythonPackages` are included in the
  `oma-arvio` runtime.

If adding a new `.nix` file, add it to git before relying on Nix evaluation.
Path flakes only include tracked files in some workflows.

## Guidelines

- Keep `core/` generic. VM orchestration, common utilities, action registry, and
  shared plotting style belong there; benchmark- or hardware-specific behavior
  belongs in modules.
- Keep task commands and VM actions separate. Commands are user-facing
  orchestration; actions are benchmark implementations run through the common
  host/VM execution path.
- Declare every cross-module dependency in both the module flake and the root
  flake wiring. Python imports between modules must match the flake graph.
- Use explicit Nix wiring for packages, assets, and environment variables.
  Avoid hardcoded `/nix/store` paths or assumptions about the source tree being
  the runtime location.
- Keep module dependencies explicit in both the module flake and root flake.
  Use `inputs.<dep>.follows = "mod-<dep>"` for active root modules.
- Do not import from a root `tasks/` tree. Prefer `core.tasks.*` and declared
  `modules.<name>.tasks.*` dependencies.
- Keep names consistent: the module directory, `mkModulePythonLib` name, and
  Python import path must match. Hyphens are acceptable in root `mod-*` input
  names, but Python module paths need underscores.
- Keep `nix/guest.nix` declarative and idempotent. Avoid runtime network pulls
  in guest boot services; package inputs through Nix when practical.
- Keep result layout under `bench-result/<component>/...` by defining an action
  `path_fn`; the action runner owns `get_benchmark_output_path`.

## Verification Checklist

After adding a module, run the smallest checks that exercise the surfaces you
changed:

```bash
# Flake/module shape
nix flake show ./modules/<name>
nix flake show .

# Python task import and registration
nix develop -c oma-arvio --list

# Optional: confirm action registration after module imports
nix develop -c python -c 'import modules.<name>.tasks; from core.tasks.actions import ACTIONS; print(sorted(ACTIONS))'
```

For documentation and examples, check for stale path/name mistakes:

```bash
rg 'modules/dpdk[-_]spdk|modules\.dpdk[-_]spdk|mod-dpdk-spdk' docs CLAUDE.md
```
