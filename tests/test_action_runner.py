import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.tasks.actions.registry import _ACTIONS, Action, get_action
from core.tasks.actions.runner import configure_vfio_trace, run_benchmark_action


class DummyRunner:
    def __init__(self):
        self.pinned = None
        self.waited = False
        self.shutdown_called = False

    def pin_vcpu(self, pin_base):
        self.pinned = pin_base

    def wait_for_ssh(self):
        self.waited = True

    def shutdown(self):
        self.shutdown_called = True


@contextmanager
def dummy_spawn(qemu_command=None, *, config=None, pin=False, shutdown=True):
    runner = DummyRunner()
    if pin:
        runner.pin_vcpu(config.get("pin_base", config["resource"].pin_base))
    try:
        yield runner
    finally:
        if shutdown:
            runner.shutdown()


class ActionRunnerTests(unittest.TestCase):
    def test_configure_vfio_trace_uses_action_output_path(self):
        old_action = _ACTIONS.get("unit")
        _ACTIONS["unit"] = Action(
            fn=lambda ctx: None,
            path_fn=lambda name, action_config: ("unit", name, action_config["marker"]),
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def fake_output_path(*parts, timestamp=None, create_dirs=True):
                    host = tmp_path / "bench-result" / Path(*parts)
                    guest = Path("/share/bench-result") / Path(*parts)
                    if create_dirs:
                        host.mkdir(parents=True, exist_ok=True)
                    return host, guest, timestamp

                config = {
                    "vfio_trace": True,
                    "action_config": {"marker": "case-a"},
                    "action_timestamp": "2026-05-28-12-00-00",
                }
                with patch(
                    "core.tasks.actions.runner.get_benchmark_output_path",
                    fake_output_path,
                ):
                    configure_vfio_trace(config, "amd-disk-large", "run-unit")

            self.assertEqual(
                Path(config["vfio_trace_file"]).name,
                "2026-05-28-12-00-00_vfio_trace.log",
            )
            self.assertEqual(Path(config["vfio_trace_file"]).parent.name, "case-a")
        finally:
            if old_action is None:
                _ACTIONS.pop("unit", None)
            else:
                _ACTIONS["unit"] = old_action

    def test_configure_vfio_trace_falls_back_for_non_action(self):
        config = {
            "vfio_trace": True,
            "action_timestamp": "2026-05-28-12-00-00",
        }

        configure_vfio_trace(config, "amd-disk-large", "attach")

        self.assertEqual(
            config["vfio_trace_file"],
            "vfio_trace_amd-disk-large_2026-05-28-12-00-00.log",
        )

    def test_action_context_uses_single_timestamp_and_output_path(self):
        seen = {}

        def fake_action(ctx, marker=None):
            seen["ctx"] = ctx
            seen["marker"] = marker

        old_action = _ACTIONS.get("unit")
        _ACTIONS["unit"] = Action(
            fn=fake_action,
            path_fn=lambda name, action_config: ("unit", name, action_config["marker"]),
        )
        try:
            self.assertIs(get_action("run-unit"), _ACTIONS["unit"])
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def fake_output_path(*parts, timestamp=None, create_dirs=True):
                    host = tmp_path / "bench-result" / Path(*parts)
                    guest = Path("/share/bench-result") / Path(*parts)
                    if create_dirs:
                        host.mkdir(parents=True, exist_ok=True)
                    return host, guest, timestamp

                config = {
                    "type": "host",
                    "resource": SimpleNamespace(pin_base=8, numa_node=[0]),
                    "action_config": {"marker": "case-a"},
                    "action_timestamp": "2026-05-28-12-00-00",
                }

                with patch(
                    "core.tasks.actions.runner.spawn_runner", dummy_spawn
                ), patch(
                    "core.tasks.actions.runner.monitor_with_sar",
                    lambda *args, **kwargs: nullcontext(),
                ), patch(
                    "core.tasks.actions.runner.get_benchmark_output_path",
                    fake_output_path,
                ):
                    run_benchmark_action("run-unit", name="host-large", config=config)

            ctx = seen["ctx"]
            self.assertEqual(seen["marker"], "case-a")
            self.assertEqual(ctx.timestamp, "2026-05-28-12-00-00")
            self.assertEqual(ctx.outputdir_host.name, "case-a")
            self.assertEqual(ctx.outputdir_guest.name, "case-a")
            self.assertTrue(ctx.is_host)
            self.assertEqual(ctx.vm.pinned, 8)
            self.assertTrue(ctx.vm.shutdown_called)
        finally:
            if old_action is None:
                _ACTIONS.pop("unit", None)
            else:
                _ACTIONS["unit"] = old_action


if __name__ == "__main__":
    unittest.main()
