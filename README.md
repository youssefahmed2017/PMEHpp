# PunishMeEvenHarder++

A programming language that is harder than assembly, because you have to write the assembly too.

The full rules are in [SYNTAX.md](SYNTAX.md).

## Install

    pip install .

This gives you the `pmeh` command, so you can run `pmeh program.pmeh` from anywhere.
(On Arch Linux and other systems that block a plain `pip install`, use `pipx install .`
or a virtual environment.) Without installing, `python pmeh.py program.pmeh` works too.

## Run a program

    python pmeh.py examples/hello.pmeh

**Demo:** a guess-the-number game. See [DEMO.md](DEMO.md) for how it works.

    python pmeh.py examples/guess.pmeh

Examples:
- `examples/guess.pmeh`: the demo game
- `examples/hello.pmeh`: prints HELLO WORLD
- `examples/countdown.pmeh`: counts down from 5
- `examples/three.pmeh`: makes the forbidden number 3
- `examples/big_or_small.pmeh`: if / else
- `examples/double.pmeh`: asks for a number and doubles it
- `examples/square.pmeh`: a function
- `examples/factorial.pmeh`: a function that calls itself (recursion)
- `examples/sum_list.pmeh`: a list
- `examples/text.pmeh`: text, strcat and strlen
- `examples/overflow.pmeh`: a buffer overflow that corrupts another variable
- `examples/dice.pmeh`: rolls a die (random numbers, mod, a comment)
- `examples/diary.pmeh`: writes a file (`diary.txt`) and reads it back

## Command line options

| Option | What it does |
|---|---|
| `-h`, `--help` | show all options |
| `-c LINE`, `--command LINE` | run program lines from the command instead of a file (one `-c` per line) |
| `-k`, `--check` | check a program without running it (mistakes that only happen while running aren't found) |
| `-m VALUE...`, `--mercy VALUE...` | how to write a number (`-m 15` gives `21`), or a word in binary (`-m print`) |
| `-g`, `--i-give-up` | plain, readable error messages, for cowards |
| `-o FILE`, `--output FILE` | compile to a real Linux program instead of running (see below) |
| `-S`, `--assembly` | with `-o`: write the generated assembly instead of a program |
| `-s N`, `--seed N` | make random numbers and list garbage the same on every run |
| `-V`, `--version` | show the version |

Error reports are scrambled on purpose (see SYNTAX.md section 14). Use `-g` for a plain one.

## Compile to a real program

PMEH can be compiled into a native Linux x86-64 program that runs without Python:

    pmeh -o guess examples/guess.pmeh
    ./guess

The compiler turns PMEH into x86-64 assembly (the asm you wrote in each statement is used for
real where it can be: the jumps, `idiv`, `getrandom`…), then builds it with `as` and `ld`
(from binutils: `sudo pacman -S binutils` on Arch). Use `-S` to see the assembly.

- **Every rule still applies** in the compiled program: forgetful variables, frozen variables
  during calls, overflow checks, exact print lengths, garbage in new lists and text, unterminated
  text reading into garbage, and buffer overflows that corrupt the variables declared after them.
  Error reports are scrambled while compiling and baked into the program (`-g` makes them plain).
- **Everything compiles**: numbers, text, lists, files, functions, input and the C library functions.
  Lists, text and garbage live on a heap the program grows itself with the `brk` system call.
- **It's fast.** Counting to 200,000 takes 0.69 s in the interpreter and 0.002 s compiled.
- `-s N` makes the garbage repeat in compiled programs too. Numbers from `rand` always come from
  the real `getrandom`, so they never repeat.
- A few error messages are worded more simply when compiled (for example, they don't quote the
  garbage), but the punished line is always the same as in the interpreter.

## Tests

    python -m unittest discover tests

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
