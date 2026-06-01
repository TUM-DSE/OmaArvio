# Action Execution Flow

Actions are the common execution path for benchmark and workload jobs. If a job
runs commands, touches devices, starts benchmark tools, launches containers,
collects raw workload output, or otherwise performs experiment execution, it
should be implemented as an action and called through `vm.start`.

Parsing and plotting are the main exceptions. Keep parsers and plot generators
as normal Python helpers or Invoke tasks under `tasks/plotting/` because they
operate on existing result files instead of producing a new benchmark run.

## Responsibilities

Keep these roles separate:

- Task commands are user-facing orchestration. They parse CLI options, choose
  setups, resolve devices, build `action_config`, and call `core.tasks.vm.start`
  with `action="run-<name>"`.
- Actions are workload implementations. They receive an `ActionContext`, run the
  job on the host runner or guest VM, and write timestamped output files.
- The action runner owns VM/host runner setup, monitoring, output directories,
  and the shared timestamp for the run.

Do not bypass the action runner for execution jobs. Direct `ssh_cmd`, ad hoc
subprocess calls, or module-specific runner wrappers make result layout,
monitoring, VFIO traces, and host-vs-guest behavior drift apart.

## Building an Action

Place action implementations under the module action package:

```text
modules/<name>/tasks/actions/
|-- __init__.py
`-- benchmark.py
```

Register each action with `@register_action`:

```python
from core.tasks.actions import ActionContext
from core.tasks.actions.registry import register_action


def _example_path(name: str, action_config: dict) -> tuple[str, ...]:
    job = action_config["job"]
    return ("example", name, job)


@register_action("example", path_fn=_example_path)
def run_example(ctx: ActionContext, job: str, size: str = "4k") -> None:
    result = ctx.vm.ssh_cmd(["example-tool", "--job", job, "--size", size], check=True)
    (ctx.outputdir_host / f"{ctx.timestamp}_example.json").write_text(result.stdout)
```

The action function must accept `ctx: ActionContext`. Other parameters come from
`action_config`; the runner calls `action.fn(ctx=action_ctx, **action_config)`.

Use `ctx.vm` for command execution. It is a `QemuVm` for guest runs and a
`HostRunner` for host runs. Use `ctx.is_host` only when the action genuinely
needs different host and guest commands.

Use `ctx.outputdir_host`, `ctx.outputdir_guest`, and `ctx.timestamp` for all
outputs. Do not call `get_benchmark_output_path` inside an action; the runner
has already computed the path once.

## Registration

Action registration is import-driven. Importing a module that contains
`@register_action(...)` inserts the action into the core registry under the
given name.

Each action package should import its action modules in `tasks/actions/__init__.py`
for the registration side effect:

```python
"""Register example actions."""

from . import benchmark  # noqa: F401 - side effect: @register_action calls
```

Each module task package should expose `register(ns)` and use
`register_module(...)`:

```python
def register(ns):
    from core.tasks.actions.module import register_module
    from modules.example.tasks import commands

    register_module(ns, actions="modules.example.tasks.actions", commands=commands)
```

`register_module` imports the action package and adds optional Invoke commands to
the CLI collection. The root `oma-arvio` executable loads active module task
packages from the module flakes and calls `register(ns)` when present.

The action name is registered without the `run-` prefix:

```python
@register_action("fio")
```

The CLI and task commands call it with the `run-` prefix:

```python
vm_tasks.start(ctx, type="snp", size="medium", action="run-fio", action_config={...})
```

`get_action()` strips `run-`, so `run-fio` resolves to the registered `fio`
action.

## Call Flow

```text
oma-arvio <module>.<task>
    |
    v
module task command
    - parses CLI options
    - resolves setup, devices, and job parameters
    - builds action_config
    |
    v
core.tasks.vm.start(..., action="run-<name>", action_config={...})
    |
    v
do_action(action, ...)
    |
    v
get_action("run-<name>")
    - strips the run- prefix
    - looks up the registered @register_action("<name>") handler
    |
    v
run_benchmark_action(...)
    |
    +--> prepare_action_output(...)
    |       - calls action.path_fn(name, action_config)
    |       - creates bench-result/<component>/...
    |       - creates one shared ctx.timestamp
    |
    v
HostRunner or QemuVm
    - starts host runner or VM
    - waits for SSH
    - builds ActionContext
    |
    v
monitoring
    - SAR for host and guest-visible runs
    - KVM perf for guest VM runs when enabled
    |
    v
action function
    - receives ctx and **action_config
    - runs the workload through ctx.vm
    |
    v
timestamped result files
    - written under ctx.outputdir_host or ctx.outputdir_guest
    - every run file includes ctx.timestamp in its filename
```

The full path for a normal module command is:

1. User runs a module task, for example `oma-arvio fio.run-tests ...`.
2. The task command prepares `action_config` and calls `core.tasks.vm.start`.
3. `vm.start` selects host mode or builds the requested QEMU command.
4. `vm.start` calls `do_action(action, ...)`.
5. `do_action` validates the registered action with `get_action(action)`.
6. `run_benchmark_action` prepares the output path and shared timestamp.
7. The runner starts a `HostRunner` or `QemuVm`, waits for SSH, and builds
   `ActionContext`.
8. SAR monitoring starts for host and guest-visible runs.
9. KVM perf monitoring starts for guest VM runs when enabled.
10. The action function runs the job and writes result files.

The special `attach` and `ssh-cmd` actions are VM utility modes. Benchmark jobs
should not model themselves after those shortcuts.

## Output Path

Every action has a `path_fn(name, action_config)` that returns path components.
The runner passes those components to `get_benchmark_output_path(...)`.

General layout:

```text
<repo>/bench-result/<component>/<setup-name>/<job-or-variant>/
```

Inside a guest, the same directory is visible through `/share`:

```text
/share/bench-result/<component>/<setup-name>/<job-or-variant>/
```

For example, an action path function returning `("fio", name, job, block_size)`
produces:

```text
<repo>/bench-result/fio/snp-disk-medium/libaio-ext4/4k/
/share/bench-result/fio/snp-disk-medium/libaio-ext4/4k/
```

Use path components for stable grouping dimensions such as component name,
VM/host setup name, job name, engine, filesystem, block size, or benchmark
variant. Do not include the timestamp in `path_fn`; timestamps belong in the
files produced inside the output directory.

## Timestamp Rule

Each run gets one timestamp from the action runner in this format:

```text
YYYY-MM-DD-HH-MM-SS
```

The timestamp is stored in `config["action_timestamp"]` and passed to the action
as `ctx.timestamp`. The same value is used for SAR output, perf output, VFIO
trace filenames, and benchmark output files.

Every file produced by a run must include `ctx.timestamp` in its filename:

```python
raw_file = ctx.outputdir_host / f"{ctx.timestamp}_raw.txt"
json_file = ctx.outputdir_host / f"{ctx.timestamp}_results.json"
guest_file = ctx.outputdir_guest / f"{ctx.timestamp}_guest.log"
```

This keeps repeated runs in the same job directory from overwriting each other
and makes benchmark output line up with monitoring files such as:

```text
2026-06-01-14-30-22_host_sar.txt
2026-06-01-14-30-22_guest_sar.txt
2026-06-01-14-30-22_perf.data
2026-06-01-14-30-22_perf_report.txt
2026-06-01-14-30-22_vfio_trace.log
```

Actions should also write the timestamp into structured metadata when they emit
JSON or another structured format. This makes copied files self-describing even
when they are viewed outside the result directory.

## Checklist

Before adding a new execution job:

- Put workload execution in `tasks/actions/`, not in plotting or parser code.
- Register the action with `@register_action("<name>", path_fn=...)`.
- Import the action module from `tasks/actions/__init__.py`.
- Import the action package from module `register(ns)` via `register_module`.
- Call it with `vm.start(..., action="run-<name>", action_config={...})`.
- Use `ctx.vm`, `ctx.outputdir_host`, `ctx.outputdir_guest`, and
  `ctx.timestamp`.
- Include `ctx.timestamp` in every file the run produces.
- Keep parsing and plotting as separate tasks over already-written files.
