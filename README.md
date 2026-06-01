# OmaArvio

OmaArvio (Finnish, eng. *own benchmark*) is a benchmark orchestration framework for confidential and conventional VMs. It builds guest VM images, launches QEMU VMs in plain KVM, AMD SEV-SNP, or bare-metal host mode, with optional VFIO PCIe passthrough, and runs benchmark workloads across all three execution environments.

Requires [Nix flakes](https://nixos.wiki/wiki/Flakes). Enter the development shell with `nix develop` before running any commands.

## Executables

**`oma-arvio`** — the main CLI, built on [Invoke](https://www.pyinvoke.org/). Covers building VM components (`build.*`), starting VMs (`vm.start`), and running benchmarks as actions inside or alongside VMs. Run `oma-arvio --list` to see all available tasks.

**`oma-arvio-root`** — a sudo-wrapped variant of the same CLI, required for operations that need elevated privileges: AMD SEV-SNP VMs (`--type snp`) and VFIO PCIe device passthrough (`--vfio-pcie`).

## Building

Build all VM components at once, or individually:

```bash
oma-arvio build.all                   # OVMF + QEMU + guest image

oma-arvio build.build-ovmf-upstream   # UEFI firmware
oma-arvio build.build-qemu-upstream   # QEMU binary
oma-arvio build.build-snp-guest-image # guest disk image
```

Outputs land in `build/` (or the `output_root` set in `config.toml`).

## Configuration

`config.toml` at the repo root holds host-specific settings and VM resource presets. It is loaded once at runtime by `core.tasks.config.load_config()`.

Key sections:

- `[defaults]` — fallback values (SSH port, VM IP, PCIe speed allowlist, VM size presets) used when the current hostname has no dedicated entry.
- `[hosts.<hostname>]` — per-host overrides for NVMe PCI address, disk path, GPU PCI address, and `vm_resources` size presets (`small`, `medium`, `large`, `numa`).
- `[defaults]` → `output_root` — optional override for where `build/` artifacts are written (defaults to the repo root).

## Adding a Benchmark Module

See [docs/module-authoring.md](docs/module-authoring.md) for a full walkthrough on creating a new benchmark module.

For the benchmark action lifecycle and result layout rules, see
[docs/actions.md](docs/actions.md).
