import subprocess
import unittest

from core.tasks.qemu import hugepage_split, setup_hugepages


class FakeRunner:
    """A runner that answers the node-count probe and records the setup call."""

    def __init__(self, nodes: int = 1, probe_fails: bool = False):
        self.nodes = nodes
        self.probe_fails = probe_fails
        self.calls: list[list[str]] = []

    def ssh_cmd(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "sh":
            if self.probe_fails:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout=f"{self.nodes}\n")
        return subprocess.CompletedProcess(argv, 0, stdout="")

    @property
    def setup_argv(self) -> list[str]:
        return [c for c in self.calls if c[0] == "dpdk-hugepages.py"][-1]


class HugepageSplitTests(unittest.TestCase):
    def test_single_node_keeps_the_total(self):
        self.assertEqual(hugepage_split(32, 1, 1), (32, 32))

    def test_the_total_is_divided_across_nodes(self):
        self.assertEqual(hugepage_split(32, 1, 2), (16, 32))
        self.assertEqual(hugepage_split(32, 1, 4), (8, 32))

    def test_a_remainder_rounds_up_per_node(self):
        # 3 GB over two nodes is 1.5 a node, and half a page cannot be
        # reserved: overshooting by less than a page per node is the direction
        # that does not fail the run.
        self.assertEqual(hugepage_split(3, 1, 2), (2, 4))

    def test_a_total_below_one_page_still_reserves_one(self):
        self.assertEqual(hugepage_split(1, 2, 2), (2, 4))

    def test_zero_clears(self):
        self.assertEqual(hugepage_split(0, 1, 2), (0, 0))


class SetupHugepagesTests(unittest.TestCase):
    def test_two_node_runner_gets_half_the_total_per_node(self):
        # The regression: dpdk-hugepages.py writes its argument to every node,
        # so passing the total reserved twice as much on a two-cell guest.
        vm = FakeRunner(nodes=2)
        setup_hugepages(vm, total_size_gb=32)
        self.assertEqual(vm.setup_argv[:3], ["dpdk-hugepages.py", "--setup", "16G"])

    def test_single_node_runner_is_unchanged(self):
        vm = FakeRunner(nodes=1)
        setup_hugepages(vm, total_size_gb=32)
        self.assertEqual(
            vm.setup_argv,
            ["dpdk-hugepages.py", "--setup", "32G", "--pagesize", "1G"],
        )

    def test_an_explicit_node_count_skips_the_probe(self):
        vm = FakeRunner(nodes=2)
        setup_hugepages(vm, total_size_gb=32, nodes=4)
        self.assertEqual(vm.setup_argv[2], "8G")
        self.assertEqual([c[0] for c in vm.calls], ["dpdk-hugepages.py"])

    def test_an_unreadable_node_count_assumes_one_node(self):
        vm = FakeRunner(probe_fails=True)
        setup_hugepages(vm, total_size_gb=8)
        self.assertEqual(vm.setup_argv[2], "8G")


if __name__ == "__main__":
    unittest.main()
