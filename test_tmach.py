import contextlib
import io
from collections import deque
from itertools import combinations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tmach


class MachineTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'machine.tmach'
            path.write_text(text)
            return tmach.parse_spec(str(path))

    def test_accepting_branch_after_cycle(self):
        machine = self.parse('main:\n(start) -> (start, main, S), (start, acc, S)')
        path = tmach.accepting_path(machine, tmach.initial_config(machine, ''))
        self.assertTrue(machine.nondeterministic)
        self.assertEqual([c.state for c in path], ['main', 'acc'])
        self.assertEqual(tmach.advance(machine, path, 0, 10)[0], 1)
        self.assertEqual(tmach.rewind(1, 1)[0], 0)

    def test_nth_path_includes_cycles(self):
        machine = self.parse('main:\n(start) -> (start, main, S), (start, acc, S)')
        initial = tmach.initial_config(machine, '')
        for nth in range(1, 5):
            path = tmach.accepting_path(machine, initial, nth=nth)
            self.assertEqual([c.state for c in path], ['main'] * nth + ['acc'])

    def test_path_order_and_convergence(self):
        # Reverse file order: textual ordering puts a before z. Both merge
        # before acceptance, and the longer direct branch must come last.
        machine = self.parse(
            'main:\n(start) -> (start, z, S), (start, long, S), (start, a, S)\n'
            'z:\n(x) -> (x, merge, S)\n'
            'a:\n(x) -> (x, merge, S)\n'
            'merge:\n(x) -> (x, acc, S)\n'
            'long:\n(x) -> (x, a, S)'
        )
        initial = tmach.initial_config(machine, '')
        for nth, branch in enumerate(('a', 'z', 'long'), 1):
            path = tmach.accepting_path(machine, initial, nth=nth)
            self.assertEqual(path[1].state, branch)
        self.assertIsNone(tmach.accepting_path(machine, initial, nth=4))

    def test_duplicate_alternatives_count(self):
        machine = self.parse('main:\n(start) -> (start, acc, S), (start, acc, S)')
        initial = tmach.initial_config(machine, '')
        self.assertIsNotNone(tmach.accepting_path(machine, initial, nth=2))
        self.assertIsNone(tmach.accepting_path(machine, initial, nth=3))

    def test_nth_rejection_cycle_terminates(self):
        machine = self.parse('main:\n(start) -> (start, main, S), (start, rej, S)')
        self.assertIsNone(tmach.accepting_path(machine, tmach.initial_config(machine, ''), nth=3))

    def test_nth_cli_and_validation(self):
        machine = self.parse('main:\n(start) -> (start, acc, S)')
        with patch('tmach.parse_spec', return_value=machine), patch('tmach.run') as run:
            self.assertEqual(tmach.main(['unused', '--accepting-path', '3']), 0)
            run.assert_called_once_with(machine, '', nth=3)
        for value in ('0', '-1', 'abc'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                tmach.main(['unused', '--accepting-path', value])
        with self.assertRaises(tmach.TMError):
            tmach.accepting_path(machine, tmach.initial_config(machine, ''), nth=0)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            tmach.run(machine, '', nth=2)
        self.assertIn('Fewer than 2 accepting paths exist.', output.getvalue())

    def test_finite_rejecting_graph(self):
        machine = self.parse('main:\n(start) -> (start, main, S) (start, rej, S)')
        self.assertIsNone(tmach.accepting_path(machine, tmach.initial_config(machine, '')))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch('tmach.read_key', side_effect=AssertionError('UI should not open')):
                tmach.run(machine, '')
        self.assertIn('No accepting path', output.getvalue())

    def test_shortest_path_and_precedence(self):
        machine = self.parse('main:\n(start) -> (start, slow, S) (start, acc, S)\n(x) -> (x, rej, S)\nslow:\n(x) -> (x, acc, S)')
        initial = tmach.initial_config(machine, '')
        self.assertEqual([c.state for c in tmach.successors(machine, initial)], ['slow', 'acc'])
        self.assertEqual(len(tmach.accepting_path(machine, initial)), 2)

    def test_search_progress_counts_unique_configurations(self):
        for target in ('acc', 'rej'):
            with self.subTest(target=target):
                machine = self.parse(
                    f'main:\n(start) -> (start, main, S) (start, {target}, S)'
                )
                counts = []
                tmach.accepting_path(machine, tmach.initial_config(machine, ''), counts.append)
                self.assertEqual(counts, [1, 2])

    def test_search_displays_final_count(self):
        machine = self.parse('main:\n(start) -> (start, main, S) (start, acc, S)')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch('tmach.read_key', return_value='q'):
                tmach.run(machine, '')
        self.assertIn('Configurations seen: 1', output.getvalue())
        self.assertIn('Configurations seen: 2\n', output.getvalue())

    def test_custom_alphabet_and_rendering(self):
        machine = self.parse('alphabet: a, b, 2\nmain:\n(start) -> (start, scan, R)\nscan:\n(a) -> (b, acc, R)')
        self.assertEqual(machine.alphabet, ('a', 'b', '2'))
        config = tmach.step(machine, tmach.step(machine, tmach.initial_config(machine, 'a')))
        self.assertEqual(config.tapes[0][1], 'b')
        self.assertIn(' b ', tmach.render(machine, config, 2, 'a', 10))

    def test_multiline_alternatives(self):
        machine = self.parse(
            'main:\n'
            '  (start) -> (start, main, S)\n'
            '  # comment between alternatives\n'
            '            (start, rej, S), # trailing comment\n'
            '\n'
            '            (start, acc, S)\n'
            '  (x) -> (x, rej, S)\n'
        )
        initial = tmach.initial_config(machine, '')
        self.assertTrue(machine.nondeterministic)
        self.assertEqual([c.state for c in tmach.successors(machine, initial)], ['main', 'rej', 'acc'])
        self.assertEqual(len(tmach.accepting_path(machine, initial)), 2)
        self.assertEqual([r.line_no for r in machine.states['main'][:3]], [2, 4, 6])

    def test_multiline_errors(self):
        with self.assertRaisesRegex(tmach.TMError, r':3: unknown target state'):
            self.parse('main:\n(start) -> (start, acc, S)\n(start, missing, S)')
        for text in ('(start, acc, S)', 'main:\n(start, acc, S)',
                     'main:\n(start) -> (start, acc, S)\nother:\n(start, acc, S)'):
            with self.subTest(text=text), self.assertRaisesRegex(tmach.TMError, 'alternative appears before'):
                self.parse(text)
        with self.assertRaisesRegex(tmach.TMError, 'can never fire'):
            self.parse('main:\n(x) -> (x, acc, S)\n(x, rej, S)\n(start) -> (start, acc, S)')

    def test_symmetric_example(self):
        machine = tmach.parse_spec('symmetric.tmach')
        self.assertTrue(machine.nondeterministic)
        path = tmach.accepting_path(machine, tmach.initial_config(machine, '_wz_s'))
        self.assertIsNotNone(path)
        self.assertEqual(path[-1].state, tmach.ACCEPT)

    def test_symmetric_all_undirected_graphs(self):
        machine = tmach.parse_spec(str(Path(__file__).with_name('symmetric.tmach')))
        possible_edges = list(combinations('wxyz', 2))
        for mask in range(1 << len(possible_edges)):
            edges = [edge for i, edge in enumerate(possible_edges) if mask & (1 << i)]
            neighbors = {node: set() for node in 'wxyz'}
            for first, second in edges:
                neighbors[first].add(second)
                neighbors[second].add(first)

            reachable = {'w'}
            pending = ['w']
            while pending:
                for neighbor in neighbors[pending.pop()] - reachable:
                    reachable.add(neighbor)
                    pending.append(neighbor)

            # Blank cells separate edge pairs; s bounds the input, including
            # the empty graph, so the machine's configuration graph is finite.
            input_string = '_' + '_'.join(''.join(edge) for edge in edges) + '_s'
            with self.subTest(edges=edges, input=input_string):
                path = tmach.accepting_path(
                    machine, tmach.initial_config(machine, input_string)
                )
                self.assertEqual(path is not None, 'z' in reachable)

                self.assert_symmetric_graph(machine, input_string, 'z' in reachable)
                reversed_input = '_' + '_'.join(
                    ''.join(reversed(edge)) for edge in reversed(edges)
                ) + '_s'
                self.assert_symmetric_graph(machine, reversed_input, 'z' in reachable)

    def assert_symmetric_graph(self, machine, input_string, accepts):
        initial = tmach.initial_config(machine, input_string)
        pending = deque([initial])
        seen = {tmach._config_key(initial)}
        edges = set()
        accepted = False
        while pending:
            config = pending.popleft()
            source = tmach._config_key(config)
            accepted |= config.state == tmach.ACCEPT
            self.assertNotEqual(config.state, tmach.REJECT)
            self.assertEqual(config.tapes[0], initial.tapes[0])
            self.assertLessEqual(config.heads[0], len(input_string))
            self.assertLessEqual(config.heads[1], 1)
            self.assertFalse(config.notes)
            if config.state in tmach.HALTING:
                continue
            successors = list(tmach.successors(machine, config))
            self.assertTrue(successors, 'Missing rules would implicitly reject')
            for nxt in successors:
                target = tmach._config_key(nxt)
                if nxt.state not in tmach.HALTING:
                    edges.add((source, target))
                if target not in seen:
                    seen.add(target)
                    self.assertLess(len(seen), 10000, 'Expected a finite graph')
                    pending.append(nxt)
        self.assertEqual(accepted, accepts)
        self.assertEqual(edges, {(target, source) for source, target in edges})

    def test_symmetric_loops_duplicates_and_disconnected_endpoints(self):
        machine = tmach.parse_spec(str(Path(__file__).with_name('symmetric.tmach')))
        for input_string, accepts in [
            ('_ww_xx_yy_zz_s', False),
            ('_wx_yz_wx_zy_s', False),
            ('_xx_yw_zx_xy_xy_ww_s', True),
            ('_zw_zw_zz_s', True),
        ]:
            with self.subTest(input=input_string):
                self.assert_symmetric_graph(machine, input_string, accepts)

    def test_comments(self):
        machine = self.parse(
            '# machine description\n'
            '  # indented comment\n'
            'alphabet: a, b # input symbols\n'
            'main: # initial state\n'
            '  (start) -> (start, scan, R) # move onto input\n'
            '# comment between states\n'
            'scan:\n'
            '  (a) -> (b, rej, S), (a, acc, S)# alternatives\n'
            '# trailing comment\n'
        )
        path = tmach.accepting_path(machine, tmach.initial_config(machine, 'a'))
        self.assertEqual([config.state for config in path], ['main', 'scan', 'acc'])
        self.assertEqual(machine.states['main'][0].line_no, 5)
        self.assertEqual(machine.states['scan'][0].line_no, 8)

    def test_comments_preserve_error_line_numbers(self):
        with self.assertRaisesRegex(tmach.TMError, r':4: unknown target state'):
            self.parse('# comment\nmain: # state\n# another comment\n(start) -> (start, missing, S) # bad target')
        with self.assertRaisesRegex(tmach.TMError, 'specification is empty'):
            self.parse('# only comments\n  # another comment\n')

    def test_underscore_input_alphabet(self):
        machine = self.parse(
            'alphabet: a, _\n'
            'main:\n(start) -> (start, scan, R)\n'
            'scan:\n(_) -> (_, scan, R)\n(a) -> (a, acc, S)'
        )
        self.assertEqual(machine.alphabet, ('a', '_'))
        config = tmach.initial_config(machine, '__a')
        for _ in range(4):
            config = tmach.step(machine, config)
        self.assertEqual(config.state, tmach.ACCEPT)
        self.assertEqual(config.heads, (3,))
        with patch('tmach.parse_spec', return_value=machine), patch('tmach.run') as run:
            self.assertEqual(tmach.main(['unused', '__a']), 0)
            run.assert_called_once_with(machine, '__a')

    def test_invalid_specs(self):
        for text in ['alphabet: start', 'alphabet: a, a']:
            with self.subTest(text=text), self.assertRaises(tmach.TMError):
                self.parse(text + '\nmain:\n(start) -> (start, acc, S)')
        with self.assertRaises(tmach.TMError):
            self.parse('main:\n(start) -> (start, acc, S) (start, rej)')
        with self.assertRaises(tmach.TMError):
            self.parse('main:\n(x) -> (x, acc, S) (x, rej, S)\n(start) -> (start, acc, S)')

    def test_blank_storage_is_canonical(self):
        first = tmach.Config('main', ((tmach.START,),), (0,), None, ())
        padded = tmach.Config('main', ((tmach.START, tmach.BLANK),), (0,), 'other', ())
        self.assertEqual(tmach._config_key(first), tmach._config_key(padded))

    def test_existing_example(self):
        machine = tmach.parse_spec('example.tmach')
        self.assertFalse(machine.nondeterministic)
        config = tmach.initial_config(machine, '11')
        for _ in range(1000):
            nxt = tmach.step(machine, config)
            if nxt is None:
                break
            config = nxt
        self.assertEqual(config.state, tmach.ACCEPT)
        self.assertEqual(config.tapes[1].count('1'), 4)

    def test_cli_validates_declared_alphabet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'machine.tmach'
            path.write_text('alphabet: a\nmain:\n(start) -> (start, acc, S)')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tmach.main([str(path), '0']), 1)
            with patch('tmach.run') as run:
                self.assertEqual(tmach.main([str(path), 'a']), 0)
                run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
