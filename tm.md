## Turing Machine Definition

This Python script simulates multi-tape TMs.
The tapes are infinite only in the rightwards direction, and the head starts at the leftmost character on all tapes, which is the start character.
The start character cannot be overwritten.
To start, the input tape contains the input followed by blanks, and all other tapes are full of blanks.
The input alphabet is always $\{0, 1\}$ and the tape alphabet is always $\Gamma \{0,1,\sqcup, \mathrm{start}\}$. Here are the specifiable parts:
- Number of tapes $k\geq 1$
- State set: $Q\supseteq \{Q_\mathrm{acc}, Q_\mathrm{rej}\}$
- Transition function: $\delta:\Gamma^k\times Q \to \Gamma^k\times Q\times \{L, R, S\}^k$. Note that that $S$ here indicates that the head stays in place rather than moving left or right.
- Initial state $\in Q$
A TM may attempt to take invalid actions but their intended effects will not occur.

## Specification

A TM specification is a text file.
The first line is the number of tapes, and subsequent nonempty lines describes the transition function one-state-at-a-time.
Whitespace is of no importance other than that each new state definition and transition function must be on a new line.

### States

States can be any valid Python variable name other than `start`, since that is used to represent the start symbol.
The first state defined in the file is the initial state of the TM.
The special halting states are `acc` and `rej`.

### Transitions

One can use a valid Python variable name (that is not taken already as the name of a state) in place of a `0`, `1`, or `_` character in the transition function to indicate any possible character. Note that `_` indicates the blank character, not an unnamed variable as it does in Python.
Underspecified transition functions should have gaps filled by rejections.
Earlier patterns used in transition rules are given precedence over later ones.
If a pattern does not match any character tuples that have not already been mathed in previous rules, then the specification is invalid.

### Example

Here is an example for a TM that accepts every input, but the second tape is populated with $2^k$ 1's where $k$ is the length of the input.

```
2

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
  (a, _) -> (a, 0, scan_left, S, L)
  (a, 0) -> (a, 0, copy,      S, R)
```

Note that, for example, `(a, b)` in the third transition rule of `main` is applied for any pair of characters other than `(start, start)` and `(start, _)`.

## Interface

When the script is run, a minimal CLI interface reads user input of the keys "n", "p", and "q", to go to the next configuration, the previous configuration, or stop. 
As this happens, an output file is constantly being written and rewritten to with a text-based graphical representation of the tapes, current state, and previous transition.
The current state and previous transition are also displayed in the CLI interface.
The script should exit gracefully, giving brief but informative error messages when failures occur.

