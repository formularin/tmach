import contextlib
import io
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

    def test_invalid_specs(self):
        for text in ['alphabet: _', 'alphabet: start', 'alphabet: a, a']:
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
