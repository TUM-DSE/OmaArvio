# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**OmaArvio** is a benchmark orchestration framework for confidential and conventional VMs. It manages building guest VM images, launching QEMU VMs in plain KVM, AMD SEV-SNP, or bare-metal host mode (with optional VFIO passthrough), and running benchmarks (FIO, DPDK/SPDK, crypto, GDS I/O, NVIDIA bandwidth) across all three execution environments. Results land in `bench-result/`.

The CLI entrypoint is `oma-arvio`, an [Invoke](https://www.pyinvoke.org/)-based tool assembled by Nix.

## Development Environment

This project uses Nix flakes. All commands below assume you are inside `nix develop` (or using `direnv`):

```bash
nix develop        # default shell: oma-arvio + pre-commit hooks
nix develop .#benchmarking   # host benchmark tools only (no inv)
nix develop .#kernel-build   # kernel compilation tools
```

Pre-commit hooks run `black` (Python) and `nixpkgs-fmt` (Nix) automatically on commit.

## Common Commands

```bash
# List all available inv tasks
oma-arvio --list

# Build all VM components (OVMF, QEMU, guest image)
oma-arvio build.all

# Build individual components
oma-arvio build.build-ovmf-upstream
oma-arvio build.build-qemu-upstream
oma-arvio build.build-snp-guest-image

# Start a VM and attach to console
oma-arvio vm.start --type amd --size medium
oma-arvio vm.start --type snp --size medium           # AMD SEV-SNP
oma-arvio vm.start --type host --size medium          # host runner (no VM)

# Run a benchmark action inside a VM
oma-arvio vm.start --type amd --size medium --action run-fio --action-config '{"job": "readonly"}'
oma-arvio vm.start --type snp --size large --action run-openssl

# Direct kernel boot (uses ../linux/build/arch/x86/boot/bzImage)
oma-arvio vm.start --type amd --direct --size medium

# VFIO PCIe passthrough
oma-arvio-root vm.start --type snp --size large --vfio-pcie 43:00.0

# Check Nix flake outputs
nix flake show
nix flake show ./modules/<name>

# Verify module task registration
nix develop -c oma-arvio --list
nix develop -c python -c 'import modules.<name>.tasks; from core.tasks.actions import ACTIONS; print(sorted(ACTIONS))'
```

## Architecture

### Layer Structure

```
flake.nix          ← root: wires modules together, builds guest images + oma-arvio
core/              ← generic VM/build/plotting library (Python pkg + Nix lib)
modules/<name>/    ← benchmark-specific code (one per workload)
config.toml        ← host-specific device PCI addresses and VM resource sizes
```

### `core/` — Generic Infrastructure

- **`core/tasks/vm.py`** — `inv vm.start`: CLI parsing and host/VM action routing.
- **`core/tasks/qemu_options.py`** — QEMU command and device option assembly.
- **`core/tasks/actions/runner.py`** — unified benchmark action execution, output path creation, and monitoring context setup.
- **`core/tasks/build.py`** — `inv build.*`: builds OVMF, QEMU, guest images, Linux kernel via `nix build`.
- **`core/tasks/config.py`** — `load_config()` reads `config.toml` (LRU-cached). Defines `PROJECT_ROOT`, `BUILD_DIR` (`build/`), `LINUX_DIR` (`../linux`).
- **`core/tasks/actions/registry.py`** — `ACTIONS` dict and `@register_action` decorator. Modules register their benchmark implementations here; `vm.py` dispatches `run-<name>` actions through this registry.
- **`core/tasks/qemu.py`** — `spawn_qemu` / `spawn_host_runner` context managers; `QemuVm` / `HostRunner` classes with `.ssh_cmd()`, `.pin_vcpu()`, `.wait_for_ssh()`.
- **`core/tasks/utils/utils.py`** — `get_benchmark_output_path()`: canonical output path builder (`bench-result/<components>/`). The action runner owns this call and passes paths to actions through `ActionContext`.
- **`core/tasks/plotting/common.py`** — shared Matplotlib style for paper-quality plots (sizes, colors, hatches, fonts).

### Modules — Benchmark Implementations

Each module under `modules/<name>/` follows this contract:

| Path | Purpose |
|------|---------|
| `flake.nix` | declares `packages`, `nixosModules.guest`, `lib.hostPackages`, `lib` via `mkModulePythonLib` |
| `nix/guest.nix` | NixOS module for guest image (hugepages, drivers, services) |
| `nix/host.nix` | host packages needed during benchmarks |
| `tasks/__init__.py` | exports `register(ns)` — adds Invoke collections to the CLI |
| `tasks/actions/*.py` | `@register_action` implementations — the actual benchmark logic |

Active modules: `nvme`, `fio`, `crypto`, `dpdk_spdk`, `nvidia_cc`, `nvbandwidth`, `gdsio`.

The root `flake.nix` auto-discovers any `mod-*` input and wires it in: host packages, guest NixOS modules, Python tasks, and shared data (`/shared/modules/<name>/`).

### `oma-arvio` CLI Assembly

`core/nix/oma-arvio.nix` assembles:
1. A Python env with `core` package + all module `pythonSrc` derivations on `PYTHONPATH`
2. An auto-generated `main.py` that calls each active module's `register(ns)`
3. `oma-arvio` (current user) and `oma-arvio-root` (sudo) shell wrappers

### Configuration (`config.toml`)

- `[hosts.<hostname>]` — per-host `nvme_pci`, `dev_path`, `gpu_pci`, `valid_pcie_speeds`
- `[hosts.<hostname>.vm_resources]` — size presets (`small`, `medium`, `large`, `numa`) with cpu/memory/numa_node/pin_base
- `[defaults]` — fallback values used when hostname has no host-specific entry
- `output_root` in `[defaults]` overrides where `build/` is placed

### Build Outputs

All Nix build symlinks land in `build/` (relative to repo root, or `output_root` from config):
- `build/qemu-upstream/`, `build/qemu-amd/` — QEMU binaries
- `build/ovmf-upstream-fd/` — OVMF firmware
- `build/image/snp-guest-image.raw`, `build/image/direct-guest-image.raw` — VM disk images
- Benchmark results: `bench-result/<module>/<variant>/<timestamp>/`

### Execution Modes

`vm.start --type` selects the mode:
- `amd` — plain KVM, AMD CPU
- `snp` — AMD SEV-SNP (requires `/dev/sev` access via `oma-arvio-root`)
- `host` — no VM; runs benchmark directly on host via `HostRunner` (same action API)

Direct boot (`--direct`) loads `../linux/build/arch/x86/boot/bzImage` instead of booting from disk — requires a separately built kernel.

## Adding a New Module

See [docs/module-authoring.md](docs/module-authoring.md) for the full guide. Quick checklist:
1. Create `modules/<name>/` with `__init__.py`, `flake.nix`, `nix/`, `tasks/`
2. Implement `tasks/__init__.py::register(ns)` and `tasks/actions/*.py` with `@register_action`
3. Add `mod-<name>` input to root `flake.nix` (auto-discovered)
4. `git add` all new `.nix` files before running `nix flake show` (path flakes only include tracked files)
