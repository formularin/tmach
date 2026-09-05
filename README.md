# tm — a multi-tape Turing machine simulator

There are lots of Turing machine simulators, but this one is especially designed
to be useful for a homework problem I have been assigned (and not yet completed).
For example, the specific Turing machine variant is the one being used in class,
and notation for specifying the machine is heavily based upon what I had
scribbled in my notebook before realizing how helpful a simulator would be.
However, this program may still be useful to you, especially if you want a
lightweight thinking aid rather than a large piece of software with its own
learning curve.

Most of the text in this repository (guess: including what you are currently
reading?) was written by Claude Opus 5.

`tm.py` reads a Turing machine written as a plain text file, runs it on an input
string, and lets you step forwards and backwards through its configurations via
an interface within the terminal.

```
python tm.py <spec> <input>
```

| Argument | Meaning |
| --- | --- |
| `spec` | path to the machine specification (required) |
| `input` | the input string, over `{0, 1}` (optional, defaults to empty) |

## The machine model

Tapes are infinite only to the right. Cell 0 of every tape holds the start
symbol, and every head begins there. Tape 1 holds the input followed by
blanks; every other tape is entirely blank.

The input alphabet is always `{0, 1}` and the tape alphabet is always
$\Gamma$ = `{0, 1, _, start}`, where `_` is the blank. What a specification
chooses is:

- the number of tapes $k \geq 1$
- the state set (which always contains the halting states `acc` and `rej`),
- the transition function
  $\delta : \Gamma^k \times Q \to \Gamma^k \times Q \times \{L, R, S\}^k$, where
  `S` leaves a head in place,
- the initial state.

A machine may attempt invalid actions, but their intended effects do not occur.
These consist of the following:

- writing anything other than `start` over cell 0,
- writing `start` into any cell other than cell 0,
- moving left from cell 0.

## Specification format

Every non-empty line is either a state header or a transition. Whitespace is
otherwise insignificant, but each state header and each transition must be on its
own line.

The number of tapes is not declared. It is inferred from the arity of the
transition function.

### States

A state header is a name followed by a colon:

```
scan_left:
```

State names are any valid Python variable name except `start`, which is reserved
for the start symbol. **The first state defined in the file is the initial
state.** `acc` and `rej` are the halting states; they are always available as
transition targets and cannot be given transitions of their own.

### Transitions

A transition names the $k$ symbols under the heads, then what to write, where to
go, and how to move each head:

```
(read_1, ..., read_k) -> (write_1, ..., write_k, next_state, move_1, ..., move_k)
```

Each read and write slot is either a literal — `0`, `1`, `_`, or `start` — or a
variable. A variable matches any character in $\Gamma$, including `start`. Note
that `_` is the blank symbol, not Python's throwaway name. Moves are `L`, `R`,
or `S`.

A variable name may not be the name of any state in the file. A variable used on
the right-hand side must have been bound on the left, so that it has a value to
write.

**Precedence.** Earlier rules win over later ones. A rule that matches no
character tuple left unmatched by the rules above it causes a parsing error.

**Gaps reject.** If no rule in the current state matches, the machine halts in
`rej`.

**Unification.** A name repeated within one pattern unifies: `(a, a)` matches
only `(0, 0)`, `(1, 1)`, `(_, _)`, and `(start, start)`.

### Example

`example.tm` accepts every input and populates the second tape with $2^k$ ones,
where $k$ is the length of the input:

```
main:
  (start, start) -> (start, start, main,      S, R)
  (start, _) ->     (start, 1,     scan_left, R, S)
  (a, b) ->         (a,     b,     scan_left, R, S)
scan_left:
  (_, b) ->     (_, b,     acc,        S, S)
  (a, 1) ->     (a, 0,     copy,       S, R)
  (a, 0) ->     (a, 0,     scan_left,  S, L)
  (a, start) -> (a, start, erase,      S, R)
erase:
  (a, _) -> (a, _, main,  S, L)
  (a, b) -> (a, 1, erase, S, R)
copy:
  (a, _) -> (a, 1, scan_left, S, L)
  (a, 0) -> (a, 0, copy,      S, R)
  (a, 1) -> (a, 1, copy,      S, R)
```

As an example of the precedence rule, note that `(a, b)` in the third rule of
`main` applies to any pair of characters other than `(start, start)` and
`(start, _)`, which the two rules above it already claimed.

## Program behavior

### Rule redundancy check

At load time, the program enumerates all $4^k$ character tuples per state
and rejects any rule whose matched set is entirely covered by earlier rules. That
is exponential in the number of tapes, so above 8 tapes the check is skipped with
a warning on stderr rather than hanging.

### Terminal handling

When stdin is a terminal, keys are read one at a time in raw mode via `termios`,
and the screen is cleared on each redraw. When stdin is a pipe, input falls back
to one line per key and the screen is not cleared, which makes runs scriptable:

```
printf 'n\nn\nn\nq\n' | python tm.py example.tm 11
```

Ctrl-C and Ctrl-D exit the same way `q` does. The view auto-sizes to the terminal
window, and a tape longer than the view scrolls to keep the head visible and
labels the visible range.

