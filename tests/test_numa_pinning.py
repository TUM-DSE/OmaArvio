import unittest
from unittest.mock import patch

from core.tasks.resources import (
    VMResource,
    host_nodes_spare_cpus,
    parse_cpulist,
    vcpu_pin_map,
)

# A host whose CPU numbering is neither node-major nor contiguous: node 1 has
# its SMT siblings in a second block, node 2 is deliberately small.
FAKE_TOPOLOGY = {
    0: list(range(0, 16)),
    1: list(range(96, 112)) + list(range(288, 304)),
    2: list(range(200, 204)),
}


def fake_host_node_cpus(node: int) -> list[int]:
    return list(FAKE_TOPOLOGY[node])


def resource(cpu: int, numa_node: list[int], pin_base: int = 0) -> VMResource:
    return VMResource(cpu=cpu, memory=64, pin_base=pin_base, numa_node=numa_node)


class ParseCpulistTests(unittest.TestCase):
    def test_single_range(self):
        self.assertEqual(parse_cpulist("0-7"), [0, 1, 2, 3, 4, 5, 6, 7])

    def test_multiple_ranges(self):
        cpus = parse_cpulist("96-191,288-383")
        self.assertEqual(len(cpus), 192)
        self.assertEqual(cpus[0], 96)
        self.assertEqual(cpus[95], 191)
        self.assertEqual(cpus[96], 288)
        self.assertEqual(cpus[-1], 383)

    def test_single_cpu(self):
        self.assertEqual(parse_cpulist("0"), [0])

    def test_mixed_range_and_single(self):
        self.assertEqual(parse_cpulist("0-1,4"), [0, 1, 4])

    def test_trailing_newline_and_empty_fields(self):
        self.assertEqual(parse_cpulist(" 0-1, ,4\n"), [0, 1, 4])
        self.assertEqual(parse_cpulist("\n"), [])


@patch("core.tasks.resources.host_node_cpus", side_effect=fake_host_node_cpus)
class VcpuPinMapTests(unittest.TestCase):
    def test_two_cells_split_evenly_over_their_nodes(self, _cpus):
        pin_map = vcpu_pin_map(resource(16, [0, 1]))

        self.assertEqual(pin_map[:8], list(range(0, 8)))
        self.assertEqual(pin_map[8:], list(range(96, 104)))

    def test_non_contiguous_node_cpulist(self, _cpus):
        # 24 vCPUs on one cell exhaust node 1's first block and continue into
        # its second one, exactly as the node's cpulist orders them.
        pin_map = vcpu_pin_map(resource(24, [1]))

        self.assertEqual(pin_map, list(range(96, 112)) + list(range(288, 296)))

    def test_pin_base_is_an_offset_into_each_nodes_cpulist(self, _cpus):
        pin_map = vcpu_pin_map(resource(8, [0, 1]), pin_base=4)

        self.assertEqual(pin_map[:4], [4, 5, 6, 7])
        self.assertEqual(pin_map[4:], [100, 101, 102, 103])

    def test_num_vcpus_overrides_the_resource(self, _cpus):
        pin_map = vcpu_pin_map(resource(64, [0, 1]), num_vcpus=4)

        self.assertEqual(pin_map, [0, 1, 96, 97])

    def test_uneven_split_is_rejected(self, _cpus):
        with self.assertRaises(ValueError) as ctx:
            vcpu_pin_map(resource(15, [0, 1]))

        self.assertIn("15 vCPUs", str(ctx.exception))

    def test_node_too_small_for_pin_base_plus_cell_is_rejected(self, _cpus):
        with self.assertRaises(ValueError) as ctx:
            vcpu_pin_map(resource(8, [0, 2]), pin_base=2)

        message = str(ctx.exception)
        self.assertIn("node 2", message)
        self.assertIn("4 CPUs", message)
        self.assertIn("6", message)

    def test_empty_numa_node_is_rejected(self, _cpus):
        with self.assertRaises(ValueError):
            vcpu_pin_map(resource(8, []))

    def test_spare_cpus_skip_the_pinned_ones(self, _cpus):
        pin_map = vcpu_pin_map(resource(8, [0, 1]))
        spare = host_nodes_spare_cpus([0, 1], set(pin_map))

        # Node 0 gives up its remaining 12 CPUs before node 1's start.
        self.assertEqual(spare[:12], list(range(4, 16)))
        self.assertEqual(spare[12:16], [100, 101, 102, 103])
        self.assertEqual(len(spare), len(FAKE_TOPOLOGY[0]) + len(FAKE_TOPOLOGY[1]) - 8)


if __name__ == "__main__":
    unittest.main()
