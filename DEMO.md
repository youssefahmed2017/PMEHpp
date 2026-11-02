# Demo: guess the number

`examples/guess.pmeh` is a small game that uses most of PMEH: functions, user input, loops,
if-statements and math. It is 39 lines and 3.3 KB. In Python it would be about 10 lines.

## Play it

    python pmeh.py examples/guess.pmeh

```
GUESS MY NUMBER!
YOUR GUESS?
50
TOO LOW
YOUR GUESS?
80
TOO HIGH
YOUR GUESS?
64
CORRECT!
YOU WON! TRIES:
3
```

Type numbers of at most 2 digits (the read allows 3 characters, including Enter).

## How it works

PMEH has no comments, so here is the program explained in pieces.

### The secret number
```
<<startvar<STACK>>type:{int64}name:{secret}<<>>8>>/>>
<<startfunc<FROMUSER><<asm:{call square}>>...<<>>$secret<<into>>$secret>>/>>
```
The secret is **64**. It can't be written directly, because it contains a 4, a banned digit.
So the program writes 8 (which looks the same upside down) and calls its own `square` function.

### The three functions (all at the top, as the rules demand)
- **`square(a)`** multiplies `a` by itself.
- **`turn(s, t)`** plays one turn: prints `YOUR GUESS?`, reads a number, prints a hint, and
  returns the guess.
- **`report(n)`** prints `YOU WON! TRIES:` and the number of tries.

### The main loop
```
<<startloop<<asm:{cmp rax, rbx; je end}>>$guess,$secret>>
    add 1 to $tries
    call turn
<<endloop>>/>>
```
`je end` means "stop when guess equals secret". As with every PMEH loop, you write the
condition for leaving, not for staying.

## The hidden puzzle: forgetful variables

Every variable must be used at least once every 3 lines, or it's erased. Most of the
strange choices in this program exist only to survive that rule:

- **The order of the variables.** `secret`, then `guess`, then `tries`, each declared just
  before its first use. In a different order, one of them is forgotten before the loop starts.
- **`turn` receives `$tries` and never uses it.** The loop is 4 lines long, so `tries` would
  go 3 lines untouched. Passing it to `turn` counts as using it.
- **`<<startvar<STACK>>type:{int64}name:{g}<<>>$s>>/>>`** starts the guess as a copy of the
  secret. The value doesn't matter (the read overwrites it). It's there to touch `$s`,
  which would otherwise be forgotten before the comparison.
- **Every branch in `turn` returns right away**, instead of sharing one return at the end.
  The shared return was too many lines after the last use of `$g`.
- **Functions freeze the caller's variables**, so the long `turn` function doesn't make the
  main loop forget anything. That's the only reason the game is possible at all.

## Printing a number of unknown length

`print` needs the exact length in its asm, but the number of tries can be 1 or 2 digits.
So `report` checks first:
```
<<startif<<asm:{cmp rax, rbx; jge skip}>>$n,01>>      if n < 10 (01 is 10 upside down)
    print with length 1
    return
<<endif>>/>>
print with length 2
```
Needing 100 or more tries gives a punishment. You'd have earned it.
