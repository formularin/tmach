# tm — a multi-tape Turing machine simulator

`tm.py` reads a Turing machine written as a plain text file, runs it on an input
string, and lets you step forwards and backwards through its configurations.

```
python tm.py example.tm 1011
```

| Argument | Meaning |
| --- | --- |
| `spec` | path to the machine specification (required) |
| `input` | the input string, over `{0, 1}` (optional, defaults to empty) |

## The machine model

Tapes are infinite only to the right. Cell 0 of every tape holds the **start
symbol**, and every head begins there. Tape 1 holds the input followed by
blanks; every other tape is entirely blank.

The input alphabet is always `{0, 1}` and the tape alphabet is always
Γ = `{0, 1, _, start}`, where `_` is the blank. What a specification chooses is:

- the number of tapes *k* ≥ 1, which is not written down anywhere but follows
  from the arity of the transitions,
- the state set (which always contains the halting states `acc` and `rej`),
- the transition function δ : Γᵏ × Q → Γᵏ × Q × {L, R, S}ᵏ, where `S` leaves a
  head in place,
- the initial state.

A machine may attempt invalid actions, but their intended effects do not occur —
see [Invalid actions](#invalid-actions).

## Specification format

Every non-empty line is either a state header or a transition. Whitespace is
otherwise insignificant, but each state header and each transition must be on its
own line.

The number of tapes is not declared. It is **inferred from the arity of the
transition function**: the first transition in the file has *k* entries on its
left-hand side, and every transition thereafter is checked against that *k*.

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

A transition names the *k* symbols under the heads, then what to write, where to
go, and how to move each head:

```
(read₁, …, read_k) -> (write₁, …, write_k, next_state, move₁, …, move_k)
```

Each read and write slot is either a literal — `0`, `1`, `_`, or `start` — or a
variable. **A variable matches any character in Γ, including `start`.** Note
that `_` is the blank symbol, not Python's throwaway name. Moves are `L`, `R`,
or `S`.

A variable name may not be the name of any state in the file. A variable used on
the right-hand side must have been bound on the left, so that it has a value to
write.

**Precedence.** Earlier rules win over later ones. A rule that matches no
character tuple left unmatched by the rules above it is an error — the
specification is rejected rather than silently containing dead code.

**Gaps reject.** If no rule in the current state matches, the machine halts in
`rej`.

### Example

`example.tm` accepts every input and populates the second tape with 2ᵏ ones,
where *k* is the length of the input:

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

`(a, b)` in the third rule of `main` applies to any pair of characters other than
`(start, start)` and `(start, _)`, which the two rules above it already claimed.

> **Known bug in this example.** It loops forever on inputs of length ≥ 2. After
> the first doubling round, `scan_left` cannot distinguish the ones `copy` just
> appended from the unprocessed ones to its left, so it reprocesses them
> indefinitely. It is correct for inputs of length 0 and 1. It is shipped as-is
> because it exercises every syntactic feature.

## Interface

The simulator reads single keys:

| Key | Action |
| --- | --- |
| `n` | play forward by the step rate |
| `p` | rewind by the step rate |
| `f` | faster — increase the step rate by one |
| `s` | slower — decrease the step rate by one |
| `q` | stop |

The **step rate** is how many steps `n` and `p` cover at a time. It starts at 1,
so both keys move one configuration until you press `f`. The current rate is
shown on the prompt line:

```
[n]ext  [p]revious  [f]aster  [s]lower  [q]uit   (rate: 4 steps) >
```

Each configuration is shown with the current state, the transition that produced
it, and one framed row per tape with a caret under the head:

```
spec:   example.tm   (2 tapes)
input:  "11"
step:   3
state:  copy
last:   scan_left: (a, 1) -> (a, 0, copy, S, R)  [a = 1]

       ┌───┬───┬───┬───┐
tape 1 │ > │ 1 │ 1 │ _ │
       └───┴───┴───┴───┘
             ^ cell 1

       ┌───┬───┬───┬───┐
tape 2 │ > │ 0 │ _ │ _ │
       └───┴───┴───┴───┘
                 ^ cell 2

legend: > = start symbol, _ = blank
```

## Design decisions

These are the choices the specification left open, and how `tm.py` resolves
them.

### Invalid actions

Three actions are impossible; each is ignored while the rest of the transition
still applies, and each prints a `note:` line so the suppression is visible
rather than silent:

- writing anything other than `start` over cell 0,
- writing `start` into any cell other than cell 0 — `start` is an invariant, not
  a symbol a machine can plant as an extra sentinel,
- moving `L` from cell 0, which leaves the head where it is.

### Variables

A variable matches any character in Γ, including `start` — the note under the
example requires this, since `(a, b)` there has to cover `(start, 0)`.

A name repeated within one pattern **unifies**: `(a, a)` matches only `(0, 0)`,
`(1, 1)`, `(_, _)`, and `(start, start)`. This is forced by the right-hand side —
if `a` could hold two different characters at once, writing `a` back would have
no defined meaning.

### Inferring the number of tapes

The arity is taken from the *first* transition in the file: its left-hand side
fixes *k*, and its right-hand side is required to have 2*k* + 1 entries. Every
later transition is then measured against that *k*, and a mismatch names the line
the count came from, so a typo in one rule is reported as a disagreement with a
specific earlier rule rather than as a vague arity error.

A specification with states but no transitions is rejected, since there is
nothing to infer *k* from. Such a machine would reject every input anyway.

### Rule precedence and validation

Matching is first-match-wins at run time, scanning a state's rules in file order.

At load time, the redundancy check enumerates all 4ᵏ character tuples per state
and rejects any rule whose matched set is entirely covered by earlier rules. That
is exponential in the number of tapes, so above 8 tapes the check is skipped with
a warning on stderr rather than hanging.

### Stepping backwards, and the step rate

`p` walks a stored list of past configurations, so it returns the genuine
previous configuration rather than trying to invert a transition. Stepping
forward again from a rewound position reuses the stored configuration.

Raising the step rate plays several steps per keystroke rather than skipping
them: every intermediate configuration is still computed and kept in the history,
so a fast `n` followed by a slow `p` walks back through everything that happened.

Both keys stop at the ends rather than overshooting. `n` halts early if the
machine reaches `acc` or `rej`, reporting how far it actually got
(`played 3 steps, then the machine halted.`); `p` stops at the initial
configuration. The rate itself has no upper bound but floors at 1.

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

### Errors

Every failure exits with status 1 and a single `error:` line naming the file and
line number where possible — an unparseable line, wrong tuple arity, unknown target
state, unbound right-hand-side variable, a variable colliding with a state name,
a transition before any state header, duplicate or `acc`/`rej` state headers, an
invalid move, a missing or empty specification, a specification with no
transitions to infer an arity from, and an input string outside `{0, 1}`.
