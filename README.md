# tmach: a multi-tape Turing machine simulator

There are lots of Turing machine simulators, but I decided to make one anyways because
I thought it would be helpful for a specific homework problem I was assigned.
However, this program may still be useful to you, especially if you want a
lightweight thinking aid rather than a large piece of software with its own
learning curve.

Most of the text in this repository (guess: including what you are currently
reading?) was written by AI.

`tmach.py` reads a Turing machine written as a plain text file, runs it on an input
string, and lets you step forwards and backwards through its configurations via
an interface within the terminal.

```
python tmach.py <spec> <input>
```

| Argument | Meaning |
| --- | --- |
| `spec` | path to the machine specification (required) |
| `input` | the input string, over the declared input alphabet (optional, defaults to empty) |
| `--accepting-path N` | show the Nth accepting path (positive integer, defaults to 1) |

## The machine model

To explore the reachable **configuration graph** in a browser, use the separate
PyVis script (the terminal simulator does not need this dependency):

```sh
python -m pip install -r requirements-visualize.txt
python visualize.py examples/exponential.tmach 11 --open
```

This writes `configuration_graph.html`; you can also open that file manually.
Each node is a distinct configuration labeled with a stable ID for that export
(`C0`, `C1`, …) and its state. Click a node, or choose it from the dropdown, to see
its tapes and head positions. Click an arrow to see the transition and any
ignored-action notes. Green nodes accept, red nodes reject, and the initial node
has a thick border. You can drag nodes, zoom, fit the graph, and freeze the layout.

The script explores all branches using breadth-first search, merging repeated
configurations and preserving cycles. It includes implicit rejections from missing
rules. Exploration defaults to 500 nodes and depth 50 because reachable graphs
can be infinite. Yellow nodes have omitted successors; the page reports whether
the graph is complete. Increase the limits or choose an output path with:

```sh
python visualize.py examples/exponential.tmach 11 --max-nodes 2000 --max-depth 200 -o example-graph.html
```

The HTML embeds the graph library so the visualization works offline.

Tapes are infinite only to the right. Cell 0 of every tape holds the start
symbol, and every head begins there. Tape 1 holds the input followed by
blanks; every other tape is entirely blank.

The input alphabet defaults to `{0, 1}` and may be declared in the specification.
The tape alphabet $\Gamma$ is exactly the input alphabet plus `_` (blank) and
`start`. The blank `_` may also be declared as an input symbol; `start` cannot. A specification
also chooses:

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

After an optional alphabet declaration, every non-empty line is either a state
header or a transition. Whitespace is
otherwise insignificant. Each state header and transition pattern must start on
its own line; additional result tuples may continue on subsequent lines.

Comments begin with `#` and continue to the end of the line. They can appear on
their own or after a declaration, state header, or transition:

```
# Accept immediately
main: # initial state
  (start) -> (start, acc, S) # accept
```

`#` is reserved for comments and cannot be used as an input symbol.

The number of tapes is not declared. It is inferred from the arity of the
transition function.

### Input alphabet

To replace the default binary alphabet, put a comma-separated declaration before
any states:

```
alphabet: a, b, c
```

Symbols are distinct single characters. Whitespace, tuple delimiters `(`, `)`,
`,` and the syntax characters `:`, `>`, and `\` are not supported as input
symbols. `start` is reserved. Declaring `_` allows blank cells in the input;
these are the same blank symbol used elsewhere on the tape. An empty `alphabet:` declares an empty
input alphabet. Declared symbols are literals in transition patterns and writes;
other identifiers are variables.

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

Each read and write slot is either a literal from the tape alphabet or a
variable. A variable matches any character in $\Gamma$, including `start`. Note
that `_` is the blank symbol, not Python's throwaway name. Moves are `L`, `R`,
or `S`.

A variable name may not be the name of any state in the file. A variable used on
the right-hand side must have been bound on the left, so that it has a value to
write.

For a nondeterministic machine, list multiple result tuples after the arrow,
separated by whitespace or commas:

```
main:
  (start) -> (start, main, S), (start, acc, S)
```

Result tuples may also continue on subsequent lines, with optional commas:

```
main:
  (start) -> (start, main, S)
             (start, acc, S)
```

Continuation tuples belong to the preceding transition in the same state.
Blank lines and comments may separate alternatives. Each tuple is a possible
successor with the same pattern and variable bindings.

**Precedence.** Earlier rules win over later ones. All alternatives of the first
matching pattern are available, including continuation tuples; later patterns
do not add alternatives. A rule that matches no
character tuple left unmatched by the rules above it causes a parsing error.

**Gaps reject.** If no rule in the current state matches, the machine halts in
`rej`.

**Unification.** A name repeated within one pattern unifies: `(a, a)` matches
only `(0, 0)`, `(1, 1)`, `(_, _)`, and `(start, start)`.

### Example

`examples/exponential.tmach` accepts every input and populates the second tape with $2^k$ ones,
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

At load time, the program enumerates all $|\Gamma|^k$ character tuples per state
and rejects any rule whose matched set is entirely covered by earlier rules. That
is exponential in the number of tapes, so when there are more than 65,536 tuples the check is skipped with
a warning on stderr.

### Nondeterministic search

When any transition has multiple alternatives, the program searches for an
accepting path and lets you step forwards and backwards through it. Paths are
ordered with shorter paths first and a deterministic secondary criterion, which
is convenient for exploring the space of accepting paths. To select the third
accepting path, for example:

```sh
python tmach.py machine.tmach 11 --accepting-path 3
```

Paths that converge on the same configuration or traverse cycles are included;
duplicate alternatives count separately. If a finite reachable graph has fewer
than N accepting paths, the program reports this and exits without opening the
UI. For the default N=1, it reports `No accepting path exists.` if none exist.

An infinite graph with too few accepting paths may be searched forever: no
general algorithm can decide rejection for every such machine. Ctrl-C interrupts
the search. Retaining paths can consume substantial memory, particularly for
large N. During search, a live counter shows the number of distinct configurations
seen, including the initial configuration, ignoring irrelevant trailing blanks;
the final count is printed when search ends. Deterministic machines continue to
run interactively as before unless a later accepting path is requested.

### Terminal handling

When stdin is a terminal, keys are read one at a time in raw mode via `termios`,
and the screen is cleared on each redraw. When stdin is a pipe, input falls back
to one line per key and the screen is not cleared, which makes runs scriptable:

```
printf 'n\nn\nn\nq\n' | python tmach.py examples/exponential.tmach 11
```

Ctrl-C and Ctrl-D exit the same way `q` does. The view auto-sizes to the terminal
window, and a tape longer than the view scrolls to keep the head visible and
labels the visible range.

