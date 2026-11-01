# PunishMeEvenHarder++

A programming language that is harder than assembly, because you have to write the assembly too.

The full rules are in [SYNTAX.md](SYNTAX.md).

## Run a program

    python pmeh.py examples/hello.pmeh

Examples:
- `examples/hello.pmeh`: prints HELLO WORLD
- `examples/countdown.pmeh`: counts down from 5
- `examples/three.pmeh`: makes the forbidden number 3
- `examples/big_or_small.pmeh`: if / else
- `examples/double.pmeh`: asks for a number and doubles it
- `examples/square.pmeh`: a function
- `examples/factorial.pmeh`: a function that calls itself (recursion)

## Beg for mercy

    python pmeh.py --mercy 15      # how do I write 15?  -> 21
    python pmeh.py --mercy print   # 'print' in binary

## Errors

Error reports are scrambled on purpose (see SYNTAX.md section 9). For a plain one:

    python pmeh.py --i-give-up program.pmeh

## Tests

    python -m unittest discover tests
