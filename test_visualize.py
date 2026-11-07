import tempfile
from pathlib import Path
import unittest

import tmach
import visualize


class GraphTests(unittest.TestCase):
    def machine(self, spec):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.tmach'
            path.write_text(spec)
            return tmach.parse_spec(str(path))

    def test_cycles_and_halting_branches(self):
        machine = self.machine('main:\n(start) -> (start, main, S) (start, acc, S) (start, rej, S)')
        configs, depths, edges, frontier = visualize.explore(machine, '')
        self.assertEqual([c.state for c in configs], ['main', 'acc', 'rej'])
        self.assertEqual([(s, t) for s, t, _ in edges], [(0, 0), (0, 1), (0, 2)])
        self.assertEqual(depths, [0, 1, 1])
        self.assertFalse(frontier)

    def test_missing_rule_rejects(self):
        machine = self.machine('main:\n(0) -> (0, acc, S)')
        configs, _, edges, frontier = visualize.explore(machine, '')
        self.assertEqual([c.state for c in configs], ['main', 'rej'])
        self.assertIn('no rule matches', edges[0][2])
        self.assertFalse(frontier)

    def test_infinite_graph_limits(self):
        machine = self.machine('main:\n(x) -> (x, main, R)')
        for nodes, depth, expected in [(3, 50, 3), (500, 2, 3)]:
            configs, _, edges, frontier = visualize.explore(machine, '', nodes, depth)
            self.assertEqual(len(configs), expected)
            self.assertEqual(len(edges), expected - 1)
            self.assertEqual(frontier, {expected - 1})

    def test_export_and_head_details(self):
        machine = self.machine('main:\n(start) -> (start, acc, R)')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'graph.html'
            self.assertEqual(visualize.write_graph(machine, '', output), (2, 1, False))
            html = output.read_text()
            self.assertIn('const configDetails', html)
            self.assertIn('Complete reachable graph.', html)
            self.assertIn('start [_] _', html)
            self.assertIn("network.on('click'", html)


if __name__ == '__main__':
    unittest.main()
