#!/usr/bin/env python3
"""Multi-tape Turing machine simulator.

Reads a machine description (see tm.md for the format), runs it on an input
string, and lets you step forwards and backwards through the configurations.

    python tm.py example.tm 1011
"""

from __future__ import annotations

import argparse
import keyword
import re
import shutil
import sys
from dataclasses import dataclass
from itertools import product
from typing import Optional

# --- tape alphabet -----------------------------------------------------------

BLANK = "_"
START = "start"
GAMMA = ("0", "1", BLANK, START)
LITERALS = frozenset(GAMMA)
MOVES = ("L", "R", "S")
ACCEPT, REJECT = "acc", "rej"
HALTING = frozenset((ACCEPT, REJECT))

CELL = {"0": "0", "1": "1", BLANK: "_", START: ">"}

HEADER_RE = re.compile(r"^([A-Za-z_]\w*)\s*:$")
TRANSITION_RE = re.compile(r"^\(([^()]*)\)\s*->\s*\(([^()]*)\)$")

# Redundancy checking enumerates |GAMMA|^k tuples; skip it for absurd tape counts.
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

    lines = [(n, line.strip()) for n, line in enumerate(raw_lines, 1)]
    lines = [(n, line) for n, line in lines if line]
    if not lines:
        raise TMError(f"{path}: the specification is empty")

    num_tapes = _parse_tape_count(path, *lines[0])
    order, pending = _split_into_states(path, lines[1:])
    states = _parse_rules(path, num_tapes, order, pending)
    _check_redundancy(path, num_tapes, states)
    return Machine(path=path, num_tapes=num_tapes, states=states, initial=order[0])


def _parse_tape_count(path: str, line_no: int, text: str) -> int:
    try:
        num_tapes = int(text)
    except ValueError:
        raise TMError(
            f"{path}:{line_no}: the first line must be the number of tapes, got {text!r}"
        ) from None
    if num_tapes < 1:
        raise TMError(f"{path}:{line_no}: the number of tapes must be at least 1, got {num_tapes}")
    return num_tapes


def _split_into_states(path: str, lines: list) -> tuple:
    """First pass: group transition lines under their state headers."""
    order: list = []
    pending: dict = {}
    current: Optional[str] = None

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
            pending[current].append((line_no, text))
        else:
            raise TMError(
                f"{where}: cannot parse {text!r}; expected a state header such as 'main:' "
                "or a transition such as '(0, 1) -> (1, 0, main, R, L)'"
            )

    if not order:
        raise TMError(f"{path}: no states are defined")
    return order, pending


def _parse_rules(path: str, num_tapes: int, order: list, pending: dict) -> dict:
    """Second pass: parse each transition now that every state name is known."""
    state_names = frozenset(order) | HALTING
    states = {name: [] for name in order}

    for state in order:
        for line_no, text in pending[state]:
            where = f"{path}:{line_no}"
            lhs_text, rhs_text = TRANSITION_RE.match(text).groups()
            lhs = _split_fields(lhs_text, where, "left-hand side")
            rhs = _split_fields(rhs_text, where, "right-hand side")

            if len(lhs) != num_tapes:
                raise TMError(
                    f"{where}: the left-hand side has {len(lhs)} entries but the machine has "
                    f"{num_tapes} tape(s)"
                )
            if len(rhs) != 2 * num_tapes + 1:
                raise TMError(
                    f"{where}: the right-hand side has {len(rhs)} entries but should have "
                    f"{2 * num_tapes + 1} ({num_tapes} symbol(s), a state, {num_tapes} move(s))"
                )

            pattern = tuple(_parse_elem(tok, where, state_names) for tok in lhs)
            bound = {e.value for e in pattern if e.is_var}

            writes = tuple(_parse_elem(tok, where, state_names) for tok in rhs[:num_tapes])
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


def _parse_elem(token: str, where: str, state_names: frozenset) -> Elem:
    if token in LITERALS:
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


def _check_redundancy(path: str, num_tapes: int, states: dict) -> None:
    """Reject rules that can never fire because earlier rules already cover them."""
    if len(GAMMA) ** num_tapes > MAX_COVERAGE_TUPLES:
        print(
            f"warning: {num_tapes} tapes is too many to check rule redundancy; skipping that check",
            file=sys.stderr,
        )
        return

    all_tuples = list(product(GAMMA, repeat=num_tapes))
    for state, rules in states.items():
        covered: set = set()
        for rule in rules:
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
        label + " │" + "│".join(f" {CELL[c]} " for c in cells) + "│",
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


def terminal_cells(label_w: int) -> int:
    columns = shutil.get_terminal_size((80, 24)).columns
    return max(4, (columns - label_w - 12) // 4)


def run(machine: Machine, input_string: str) -> None:
    history = [initial_config(machine, input_string)]
    index = 0
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
        print("\n[n]ext  [p]revious  [q]uit > ", end="", flush=True)

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
            if index + 1 < len(history):
                index += 1
            else:
                nxt = step(machine, config)
                if nxt is None:
                    message = "the machine has halted; there is no next configuration."
                else:
                    history.append(nxt)
                    index += 1
        elif key == "p":
            if index == 0:
                message = "already at the initial configuration."
            else:
                index -= 1
        else:
            message = f"unknown key {key!r}; use n, p, or q."


# --- entry point -------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Step through a multi-tape Turing machine described by a spec file."
    )
    parser.add_argument("spec", help="path to the machine specification")
    parser.add_argument("input", nargs="?", default="", help="input string over {0, 1} (default: empty)")
    args = parser.parse_args(argv)

    try:
        bad = [c for c in args.input if c not in ("0", "1")]
        if bad:
            raise TMError(f"input must be a string over {{0, 1}}; found {bad[0]!r}")
        machine = parse_spec(args.spec)
        run(machine, args.input)
    except TMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
