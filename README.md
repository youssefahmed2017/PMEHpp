# PunishMeEvenHarder++

A programming language that is harder than assembly, because you have to write the assembly too.

The full rules are in [SYNTAX.md](SYNTAX.md).

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

## Command line options

| Option | What it does |
|---|---|
| `-h`, `--help` | show all options |
| `-c LINE`, `--command LINE` | run program lines from the command instead of a file (one `-c` per line) |
| `-k`, `--check` | check a program without running it (mistakes that only happen while running aren't found) |
| `-m VALUE...`, `--mercy VALUE...` | how to write a number (`-m 15` gives `21`), or a word in binary (`-m print`) |
| `-g`, `--i-give-up` | plain, readable error messages, for cowards |
| `-s N`, `--seed N` | make list garbage the same on every run |
| `-V`, `--version` | show the version |

Error reports are scrambled on purpose (see SYNTAX.md section 11). Use `-g` for a plain one.

## Tests

    python -m unittest discover tests
