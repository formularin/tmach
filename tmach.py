#!/usr/bin/env python3
"""Multi-tape Turing machine simulator.

Reads a machine description (see README.md for the format), runs it on an input
string, and lets you step forwards and backwards through the configurations.

    python tmach.py examples/exponential.tmach 1011
"""

from __future__ import annotations

import argparse
import keyword
import re
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass
from itertools import product
from typing import Callable, Optional

# --- tape alphabet -----------------------------------------------------------

BLANK = "_"
START = "start"
MOVES = ("L", "R", "S")
ACCEPT, REJECT = "acc", "rej"
HALTING = frozenset((ACCEPT, REJECT))

CELL = {"0": "0", "1": "1", BLANK: "_", START: ">"}

HEADER_RE = re.compile(r"^([A-Za-z_]\w*)\s*:$")
TRANSITION_RE = re.compile(r"^\(([^()]*)\)\s*->\s*((?:\([^()]*\)\s*,?\s*)+)$")
CONTINUATION_RE = re.compile(r"^(?:\([^()]*\)\s*,?\s*)+$")
RHS_RE = re.compile(r"\(([^()]*)\)")

# Redundancy checking enumerates |Gamma|^k tuples; bound the work.
MAX_COVERAGE_TUPLES = 1 << 16


class TMError(Exception):
    """Anything the user needs to fix: a bad spec, a bad input, an unwritable file."""


# --- parsed representation ---------------------------------------------------


@dataclass(frozen=True)
class Elem:
    """One slot of a transition tuple: either a literal symbol or a variable."""

    value: str
    is_var: bool

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Rule:
    line_no: int
    state: str
    group_line: int  # first line of this transition, including its alternatives
    pattern: tuple  # k Elems matched against the symbols under the heads
    writes: tuple  # k Elems written back
    next_state: str
    moves: tuple  # k of "L" / "R" / "S"

    @property
    def text(self) -> str:
        lhs = ", ".join(str(e) for e in self.pattern)
        rhs = ", ".join(
            [str(e) for e in self.writes] + [self.next_state] + list(self.moves)
        )
        return f"({lhs}) -> ({rhs})"


@dataclass(frozen=True)
class Machine:
    path: str
    num_tapes: int
    states: dict  # state name -> list of Rules, in file order
    initial: str
    alphabet: tuple = ("0", "1")

    @property
    def nondeterministic(self) -> bool:
        return any(len({r.group_line for r in rules}) < len(rules) for rules in self.states.values())


# --- specification parsing ---------------------------------------------------


def _check_identifier(name: str, what: str, where: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise TMError(f"{where}: {what} {name!r} is not a valid Python variable name")


def parse_spec(path: str) -> Machine:
    try:
        with open(path, encoding="utf-8") as handle:
            raw_lines = handle.read().splitlines()
    except OSError as exc:
        raise TMError(f"cannot read spec file {path!r}: {exc.strerror}") from None

    lines = [(n, line.split("#", 1)[0].strip()) for n, line in enumerate(raw_lines, 1)]
    lines = [(n, line) for n, line in lines if line]
    if not lines:
        raise TMError(f"{path}: the specification is empty")

    alphabet = ("0", "1")
    if lines and lines[0][1].startswith("alphabet:"):
        line_no, declaration = lines.pop(0)
        value = declaration.split(":", 1)[1].strip()
        alphabet = tuple(_split_fields(value, f"{path}:{line_no}", "alphabet")) if value else ()
        if len(set(alphabet)) != len(alphabet):
            raise TMError(f"{path}:{line_no}: duplicate input alphabet symbol")
        for symbol in alphabet:
            if symbol == START or len(symbol) != 1 or symbol.isspace() or symbol in "(),:>\\":
                raise TMError(f"{path}:{line_no}: invalid input alphabet symbol {symbol!r}; use single characters other than start")
    literals = frozenset(alphabet) | {BLANK, START}
    order, pending, first = _split_into_states(path, lines)
    num_tapes, arity_line = _infer_num_tapes(path, first)
    states = _parse_rules(path, num_tapes, arity_line, order, pending, literals)
    _check_redundancy(path, num_tapes, states, tuple(dict.fromkeys(alphabet + (BLANK, START))))
    return Machine(path=path, num_tapes=num_tapes, states=states, initial=order[0], alphabet=alphabet)


def _split_into_states(path: str, lines: list) -> tuple:
    """First pass: group transition lines under their state headers."""
    order: list = []
    pending: dict = {}
    current: Optional[str] = None
    first: Optional[tuple] = None

    for line_no, text in lines:
        where = f"{path}:{line_no}"
        header = HEADER_RE.match(text)
        if header:
            name = header.group(1)
            _check_identifier(name, "state name", where)
            if name == START:
                raise TMError(f"{where}: {START!r} is reserved for the start symbol and cannot name a state")
            if name in HALTING:
                raise TMError(f"{where}: {name!r} is a halting state and cannot be given transitions")
            if name in pending:
                raise TMError(f"{where}: state {name!r} is defined more than once")
            order.append(name)
            pending[name] = []
            current = name
        elif TRANSITION_RE.match(text):
            if current is None:
                raise TMError(f"{where}: transition appears before any state is defined")
            pending[current].append((line_no, text, line_no))
            if first is None:
                first = (line_no, text)
        elif CONTINUATION_RE.fullmatch(text):
            if current is None or not pending[current]:
                raise TMError(f"{where}: alternative appears before a transition in this state")
            _, previous_text, group_line = pending[current][-1]
            lhs_text = TRANSITION_RE.match(previous_text).group(1)
            pending[current].append((line_no, f"({lhs_text}) -> {text}", group_line))
        else:
            raise TMError(
                f"{where}: cannot parse {text!r}; expected a state header such as 'main:' "
                "or a transition such as '(0, 1) -> (1, 0, main, R, L)'"
            )

    if not order:
        raise TMError(f"{path}: no states are defined")
    return order, pending, first


def _infer_num_tapes(path: str, first: Optional[tuple]) -> tuple:
    """The number of tapes is the arity of the transition function.

    It is read off the first transition in the file; every later transition is
    then checked against it.
    """
    if first is None:
        raise TMError(
            f"{path}: cannot infer the number of tapes because the specification has no transitions"
        )
    line_no, text = first
    where = f"{path}:{line_no}"
    lhs_text, rhs_text = TRANSITION_RE.match(text).groups()
    num_tapes = len(_split_fields(lhs_text, where, "left-hand side"))
    rhs = _split_fields(RHS_RE.findall(rhs_text)[0], where, "right-hand side")
    if len(rhs) != 2 * num_tapes + 1:
        raise TMError(
            f"{where}: the number of tapes is inferred from this, the first transition, whose "
            f"{num_tapes}-entry left-hand side calls for a right-hand side of {2 * num_tapes + 1} "
            f"entries ({num_tapes} symbol(s), a state, {num_tapes} move(s)), not {len(rhs)}"
        )
    return num_tapes, line_no


def _parse_rules(path: str, num_tapes: int, arity_line: int, order: list, pending: dict, literals: frozenset) -> dict:
    """Second pass: parse each transition now that every state name is known."""
    state_names = frozenset(order) | HALTING
    states = {name: [] for name in order}

    for state in order:
        for line_no, text, group_line in pending[state]:
            where = f"{path}:{line_no}"
            lhs_text, rhs_text = TRANSITION_RE.match(text).groups()
            lhs = _split_fields(lhs_text, where, "left-hand side")
            for alternative in RHS_RE.findall(rhs_text):
                rhs = _split_fields(alternative, where, "right-hand side")

                source = f"{num_tapes} tape(s), inferred from the transition on line {arity_line}"
                if len(lhs) != num_tapes:
                    raise TMError(
                        f"{where}: the left-hand side has {len(lhs)} entries but the machine has {source}"
                    )
                if len(rhs) != 2 * num_tapes + 1:
                    raise TMError(
                        f"{where}: the right-hand side has {len(rhs)} entries but should have "
                        f"{2 * num_tapes + 1} ({num_tapes} symbol(s), a state, {num_tapes} move(s)) "
                        f"for {source}"
                    )

                pattern = tuple(_parse_elem(tok, where, state_names, literals) for tok in lhs)
                bound = {e.value for e in pattern if e.is_var}

                writes = tuple(_parse_elem(tok, where, state_names, literals) for tok in rhs[:num_tapes])
                for elem in writes:
                    if elem.is_var and elem.value not in bound:
                        raise TMError(
                            f"{where}: {elem.value!r} is written but never matched on the left-hand side, "
                            "so it has no value"
                        )

                next_state = rhs[num_tapes]
                if next_state not in state_names:
                    raise TMError(
                        f"{where}: unknown target state {next_state!r} (defined states: "
                        + ", ".join(sorted(state_names))
                        + ")"
                    )

                moves = tuple(tok.upper() for tok in rhs[num_tapes + 1 :])
                for move in moves:
                    if move not in MOVES:
                        raise TMError(f"{where}: invalid head move {move!r}; expected one of L, R, S")

                states[state].append(
                    Rule(
                        line_no=line_no,
                        state=state,
                        group_line=group_line,
                        pattern=pattern,
                        writes=writes,
                        next_state=next_state,
                        moves=moves,
                    )
                )
    return states


def _split_fields(text: str, where: str, side: str) -> list:
    fields = [field.strip() for field in text.split(",")]
    if any(not field for field in fields):
        raise TMError(f"{where}: empty entry in the {side}")
    return fields


def _parse_elem(token: str, where: str, state_names: frozenset, literals: frozenset) -> Elem:
    if token in literals:
        return Elem(token, is_var=False)
    _check_identifier(token, "symbol or variable", where)
    if token in state_names:
        raise TMError(
            f"{where}: {token!r} is already the name of a state and cannot be used as a variable"
        )
    return Elem(token, is_var=True)


def match(pattern: tuple, symbols: tuple) -> Optional[dict]:
    """Return the variable binding if `pattern` matches `symbols`, else None.

    A variable repeated within one pattern must bind to the same symbol everywhere.
    """
    binding: dict = {}
    for elem, symbol in zip(pattern, symbols):
        if elem.is_var:
            if binding.setdefault(elem.value, symbol) != symbol:
                return None
        elif elem.value != symbol:
            return None
    return binding


def _check_redundancy(path: str, num_tapes: int, states: dict, gamma: tuple) -> None:
    """Reject rules that can never fire because earlier rules already cover them."""
    if len(gamma) ** num_tapes > MAX_COVERAGE_TUPLES:
        print(
            f"warning: {num_tapes} tapes is too many to check rule redundancy; skipping that check",
            file=sys.stderr,
        )
        return

    all_tuples = list(product(gamma, repeat=num_tapes))
    for state, rules in states.items():
        covered: set = set()
        previous_line = None
        for rule in rules:
            if rule.group_line == previous_line:
                continue
            previous_line = rule.group_line
            fresh = {t for t in all_tuples if t not in covered and match(rule.pattern, t) is not None}
            if not fresh:
                raise TMError(
                    f"{path}:{rule.line_no}: this rule can never fire; every symbol tuple matched by "
                    f"{rule.text} is already matched by an earlier rule of state {state!r}"
                )
            covered |= fresh


# --- simulation --------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    state: str
    tapes: tuple  # k tuples of symbols; index 0 always holds START
    heads: tuple
    prev: Optional[str]  # the transition that produced this configuration
    notes: tuple  # invalid actions that were suppressed on the way here


def initial_config(machine: Machine, input_string: str) -> Config:
    tapes = [(START,) + tuple(input_string)] + [(START,)] * (machine.num_tapes - 1)
    return Config(
        state=machine.initial,
        tapes=tuple(tapes),
        heads=tuple([0] * machine.num_tapes),
        prev=None,
        notes=(),
    )


def _binding_note(rule: Rule, binding: dict) -> str:
    names: list = []
    for elem in rule.pattern:
        if elem.is_var and elem.value not in names:
            names.append(elem.value)
    if not names:
        return ""
    return "  [" + ", ".join(f"{name} = {binding[name]}" for name in names) + "]"


def step(machine: Machine, config: Config) -> Optional[Config]:
    """Advance one configuration, or return None if the machine has halted."""
    if config.state in HALTING:
        return None

    read = tuple(config.tapes[i][config.heads[i]] for i in range(machine.num_tapes))
    for rule in machine.states[config.state]:
        binding = match(rule.pattern, read)
        if binding is not None:
            break
    else:
        # An unspecified transition is a rejection.
        read_text = "(" + ", ".join(read) + ")"
        return Config(
            state=REJECT,
            tapes=config.tapes,
            heads=config.heads,
            prev=f"{config.state}: no rule matches {read_text}, so the machine rejects",
            notes=(),
        )

    return _apply_rule(machine, config, rule, binding)


def _apply_rule(machine: Machine, config: Config, rule: Rule, binding: dict) -> Config:
    tapes = [list(tape) for tape in config.tapes]
    heads = list(config.heads)
    notes: list = []

    for i in range(machine.num_tapes):
        elem = rule.writes[i]
        symbol = binding[elem.value] if elem.is_var else elem.value
        if heads[i] == 0:
            if symbol != START:
                notes.append(f"tape {i + 1}: the start symbol cannot be overwritten (write of {symbol} ignored)")
        elif symbol == START:
            notes.append(f"tape {i + 1}: the start symbol cannot be written elsewhere (write ignored)")
        else:
            tapes[i][heads[i]] = symbol

    for i in range(machine.num_tapes):
        move = rule.moves[i]
        if move == "L":
            if heads[i] == 0:
                notes.append(f"tape {i + 1}: cannot move left of cell 0 (head stays)")
            else:
                heads[i] -= 1
        elif move == "R":
            heads[i] += 1
            if heads[i] == len(tapes[i]):
                tapes[i].append(BLANK)

    return Config(
        state=rule.next_state,
        tapes=tuple(tuple(tape) for tape in tapes),
        heads=tuple(heads),
        prev=f"{rule.state}: {rule.text}{_binding_note(rule, binding)}",
        notes=tuple(notes),
    )


def successors(machine: Machine, config: Config):
    """Yield all alternatives of the first matching transition line."""
    if config.state in HALTING:
        return
    read = tuple(config.tapes[i][config.heads[i]] for i in range(machine.num_tapes))
    chosen_line = None
    for rule in machine.states[config.state]:
        if chosen_line is not None and rule.group_line != chosen_line:
            break
        binding = match(rule.pattern, read)
        if binding is not None:
            chosen_line = rule.group_line
            yield _apply_rule(machine, config, rule, binding)


def _config_key(config: Config) -> tuple:
    # Unvisited trailing blanks do not distinguish configurations.
    tapes = []
    for tape in config.tapes:
        end = len(tape)
        while end > 1 and tape[end - 1] == BLANK:
            end -= 1
        tapes.append(tape[:end])
    return config.state, tuple(tapes), config.heads


def accepting_path(
    machine: Machine, initial: Config, on_progress: Optional[Callable[[int], None]] = None,
    *, nth: int = 1,
) -> Optional[list]:
    """Return the nth accepting transition path (one-based), or None.

    Paths are ordered by transition count, then lexicographically by their
    sequence of Config.prev strings (source state, normalized rule, bindings).
    Identical transition strings retain alternative order from the spec, and
    duplicate alternatives count separately. Cycles and converging paths count.

    Sorted successors and FIFO traversal implement this shortlex order. Only
    the first nth arrivals at each canonical configuration need expansion:
    any later prefix has nth earlier prefixes with the same possible suffixes,
    so cannot contribute one of the first nth accepting paths. This also makes
    unsuccessful searches terminate on finite configuration graphs.
    """
    if nth < 1:
        raise TMError("accepting path number must be a positive integer")
    # Nodes have separate parents even when their configurations coincide.
    nodes = [(initial, None)]
    queue = deque([0])
    arrivals = {_config_key(initial): 1}
    found = 0
    if on_progress is not None:
        on_progress(1)
    while queue:
        node = queue.popleft()
        config, _ = nodes[node]
        if config.state == ACCEPT:
            found += 1
            if found == nth:
                path = []
                while node is not None:
                    config, node = nodes[node]
                    path.append(config)
                return path[::-1]
            continue
        for nxt in sorted(successors(machine, config), key=lambda c: c.prev):
            key = _config_key(nxt)
            count = arrivals.get(key, 0)
            if count >= nth:
                continue
            arrivals[key] = count + 1
            nodes.append((nxt, node))
            queue.append(len(nodes) - 1)
            if count == 0 and on_progress is not None:
                on_progress(len(arrivals))
    return None


# --- rendering ---------------------------------------------------------------


def _tape_block(index: int, tape: tuple, head: int, ncells: int, label_w: int) -> list:
    last_used = max((i for i, s in enumerate(tape) if s != BLANK), default=0)
    end = max(last_used, head) + 1  # one cell of context past the interesting part

    if end + 1 > ncells:
        lo = max(0, min(head - ncells // 2, end + 1 - ncells))
    else:
        lo = 0
    hi = min(lo + ncells - 1, max(end, head))

    cells = [tape[i] if i < len(tape) else BLANK for i in range(lo, hi + 1)]
    pad = " " * (label_w + 1)
    label = f"tape {index}".ljust(label_w)
    window = f"   (cells {lo}-{hi})" if lo > 0 else ""

    return [
        pad + "┌" + "┬".join("───" for _ in cells) + "┐",
        label + " │" + "│".join(f" {CELL.get(c, c)} " for c in cells) + "│",
        pad + "└" + "┴".join("───" for _ in cells) + "┘" + window,
        pad + " " * (4 * (head - lo) + 2) + f"^ cell {head}",
    ]


def render(machine: Machine, config: Config, index: int, input_string: str, ncells: int) -> str:
    if config.state == ACCEPT:
        state_line = f"{ACCEPT}   (halted: input accepted)"
    elif config.state == REJECT:
        state_line = f"{REJECT}   (halted: input rejected)"
    else:
        state_line = config.state

    tape_word = "tape" if machine.num_tapes == 1 else "tapes"
    lines = [
        f"spec:   {machine.path}   ({machine.num_tapes} {tape_word})",
        f'input:  "{input_string}"',
        f"step:   {index}",
        f"state:  {state_line}",
        f"last:   {config.prev if config.prev else '-- initial configuration --'}",
    ]
    for note in config.notes:
        lines.append(f"note:   {note}")
    lines.append("")

    label_w = len(f"tape {machine.num_tapes}")
    for i in range(machine.num_tapes):
        lines.extend(_tape_block(i + 1, config.tapes[i], config.heads[i], ncells, label_w))
        lines.append("")

    lines.append("legend: > = start symbol, _ = blank")
    return "\n".join(lines)


# --- interactive loop --------------------------------------------------------


def read_key() -> str:
    """Read one keystroke, falling back to line input when stdin is not a terminal."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.strip()[:1].lower() if line else "q"
    try:
        import termios
        import tty
    except ImportError:
        line = sys.stdin.readline()
        return line.strip()[:1].lower() if line else "q"

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    if char in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
        raise KeyboardInterrupt
    return char.lower()


def _steps(count: int) -> str:
    return f"{count} step" if count == 1 else f"{count} steps"


def advance(machine: Machine, history: list, index: int, rate: int) -> tuple:
    """Play `rate` steps forward, extending the history as needed."""
    played = 0
    while played < rate:
        if index + 1 == len(history):
            nxt = step(machine, history[index])
            if nxt is None:
                break
            history.append(nxt)
        index += 1
        played += 1

    if played == 0:
        return index, "the machine has halted; there is no next configuration."
    if played < rate:
        return index, f"played {_steps(played)}, then the machine halted."
    return index, ""


def rewind(index: int, rate: int) -> tuple:
    """Rewind `rate` steps, stopping at the initial configuration."""
    if index == 0:
        return index, "already at the initial configuration."
    rewound = min(rate, index)
    if rewound < rate:
        return 0, f"rewound {_steps(rewound)} to the initial configuration."
    return index - rewound, ""


def terminal_cells(label_w: int) -> int:
    columns = shutil.get_terminal_size((80, 24)).columns
    return max(4, (columns - label_w - 12) // 4)


def run(machine: Machine, input_string: str, nth: int = 1) -> None:
    if nth < 1:
        raise TMError("accepting path number must be a positive integer")
    history = [initial_config(machine, input_string)]
    if machine.nondeterministic or nth != 1:
        print("Searching for an accepting path...", flush=True)
        seen = 0
        last_update = None

        def show_progress(count: int) -> None:
            nonlocal seen, last_update
            seen = count
            now = time.monotonic()
            if last_update is None or now - last_update >= 0.1:
                print(f"\rConfigurations seen: {seen}", end="", flush=True)
                last_update = now

        try:
            history = accepting_path(machine, history[0], on_progress=show_progress, nth=nth)
        finally:
            print(f"\rConfigurations seen: {seen}", flush=True)
        if history is None:
            print("No accepting path exists." if nth == 1 else
                  f"Fewer than {nth} accepting paths exist.")
            return
    index = 0
    rate = 1  # how many steps "n" and "p" play at a time
    message = ""
    interactive = sys.stdin.isatty()
    label_w = len(f"tape {machine.num_tapes}")

    while True:
        config = history[index]
        if interactive:
            sys.stdout.write("\033[2J\033[H")
        print(render(machine, config, index, input_string, terminal_cells(label_w)))
        if message:
            print(message)
        message = ""
        print(
            f"\n[n]ext  [p]revious  [f]aster  [s]lower  [q]uit   (rate: {_steps(rate)}) > ",
            end="",
            flush=True,
        )

        try:
            key = read_key()
        except (KeyboardInterrupt, EOFError):
            print("\ninterrupted.")
            return
        print(key)

        if key == "q":
            print("bye.")
            return
        if key == "n":
            index, message = advance(machine, history, index, rate)
        elif key == "p":
            index, message = rewind(index, rate)
        elif key == "f":
            rate += 1
            message = f"step rate: {_steps(rate)} at a time."
        elif key == "s":
            if rate == 1:
                message = "step rate is already 1 step at a time."
            else:
                rate -= 1
                message = f"step rate: {_steps(rate)} at a time."
        else:
            message = f"unknown key {key!r}; use n, p, f, s, or q."


# --- entry point -------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Step through a multi-tape Turing machine described by a spec file."
    )
    parser.add_argument("spec", help="path to the machine specification")
    parser.add_argument("input", nargs="?", default="", help="input string over the declared alphabet (default: empty)")
    parser.add_argument("--accepting-path", type=int, default=1, metavar="N",
                        help="show the Nth accepting path, shortest first (default: 1)")
    args = parser.parse_args(argv)
    if args.accepting_path < 1:
        parser.error("--accepting-path must be a positive integer")

    try:
        machine = parse_spec(args.spec)
        bad = [c for c in args.input if c not in machine.alphabet]
        if bad:
            raise TMError(f"input must use the input alphabet {machine.alphabet!r}; found {bad[0]!r}")
        if args.accepting_path == 1:
            run(machine, args.input)
        else:
            run(machine, args.input, nth=args.accepting_path)
    except TMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
