# Demo: guess the number

`examples/guess.pmeh` is a small game that uses most of PMEH: functions, random numbers,
remainder, user input, loops, if-statements, math and comments. It is 51 lines and 4.3 KB.
In Python it would be about 10 lines.

## Play it

    pmeh examples/guess.pmeh

or compile it into a real program first:

    pmeh -o guess examples/guess.pmeh
    ./guess

A game might go like this (the secret here happened to be 63):
```
GUESS MY NUMBER!
IT IS 1-99.
YOUR GUESS?
50
TOO LOW
YOUR GUESS?
75
TOO HIGH
YOUR GUESS?
63
CORRECT!
YOU WON! TRIES:
3
```

The secret is different every game. Type numbers of at most 2 digits (the read allows 3
characters, including Enter).

## How it works

### The comments
```
<<COMMNT".REBMUN EHT SSEUG">>/>>
```
Comments are written backwards, so that one says `GUESS THE NUMBER.` The program has five of
them, and every single one sits in one of the few places where a comment can't kill the program
(see "The hidden puzzle" below).

### The secret number: `pick`
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 318; lea rdi, [buf]; mov rsi, 8; mov rdx, 0; syscall}>>…<<into>>$x>>/>>
```
`pick` asks Linux for 8 random bytes (the real `getrandom` system call). That gives any number an
`int64` can hold, negatives included, so turning it into 1 to 99 takes three more steps:

1. `mod` by 99. The program writes `66`, because 99 upside down is 66.
2. If the result is negative (a negative number keeps its sign in `mod`), multiply it by -1.
3. Add 1.

`pick` has no parameters (`params:{}`), but like every PMEH function it still has to return
something into a variable.

### The other functions (all at the top, as the rules demand)
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

Every variable must be used at least once every 3 lines, or it's erased. Comments count as lines
too. Most of the strange choices in this program exist only to survive that rule:

- **The order of the variables.** `secret`, then `guess`, then `tries`, each declared just
  before its first use. In a different order, one of them is forgotten before the loop starts.
- **`turn` receives `$tries` and never uses it.** The loop is 4 lines long, so `tries` would
  go 3 lines untouched. Passing it to `turn` counts as using it.
- **`<<startvar<STACK>>type:{int64}name:{g}<<>>$s>>/>>`** starts the guess as a copy of the
  secret. The value doesn't matter (the read overwrites it). It's there to touch `$s`.
- **Every branch in `turn` returns right away**, instead of sharing one return at the end.
  The shared return was too many lines after the last use of `$g`.
- **Functions freeze the caller's variables**, so the long `turn` function doesn't make the
  main loop forget anything. That's the only reason the game is possible at all.
- **Comments only go at the top and as the first line of a function.** Anywhere else they push a
  variable over the limit. For example, one comment between `<<endloop>>/>>` and the final
  `report` call gives:
  `PUNISHMENT on line 51: $tries was ignored for 3 lines and forgot itself.`
  Explaining your code breaks it.

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

## Testing a random game

The tests play by typing 1, 2, 3, … 99 in order. Then the number of tries always equals the
secret, and "TOO LOW" appears exactly one time fewer, whatever the secret was.
