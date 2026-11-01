#!/usr/bin/env python3
"""PunishMeEvenHarder++ interpreter (first draft).

Usage:
    python pmeh.py program.pmeh
    python pmeh.py --mercy 15       # how do I write the number 15?
    python pmeh.py --mercy print    # what is 'print' in binary?

The full rules are in SYNTAX.md.
"""
import random
import re
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- constants

VALIDATION_LINE = ('<EXCPROGRAM>VALIDATE:COMMNT"I\'d rather die than code in '
                   'this programming language"[NOMERCYGRANTED]')
MEMORY_SPAN = 3          # a variable unused for this many executed lines is erased
MAX_STEPS = 1_000_000    # protection against infinite suffering

FLIP = {"0": "0", "1": "1", "2": "5", "5": "2", "6": "9", "8": "8", "9": "6"}
BANNED_DIGITS = "347"
TYPES = {"int8": 8, "int16": 16, "int32": 32, "int64": 64}

PRINT_ASM = re.compile(r"mov rax, 1; mov rdi, 1; lea rsi, \[msg\]; mov rdx, (\d+); syscall")
MATH_ASM = {
    "add": "add rax, rbx",
    "sub": "sub rax, rbx",
    "mul": "imul rax, rbx",
    "div": "cqo; idiv rbx",
}
BUILTINS = {"print", *MATH_ASM}
CMP_ASM = re.compile(r"cmp rax, rbx; (j[a-z]+) (\w+)")
JUMPS = {  # jump is TAKEN when this is true
    "jg": lambda a, b: a > b,
    "jl": lambda a, b: a < b,
    "je": lambda a, b: a == b,
    "jne": lambda a, b: a != b,
    "jge": lambda a, b: a >= b,
    "jle": lambda a, b: a <= b,
}

NAME = r"[A-Za-z_]\w*"
FUNC_RE = re.compile(r"<<startfunc<FROMBUILTIN><<asm:\{(?P<asm>.*?)\}>>mco:\{(?P<name>[^}]*)\}"
                     r"<<>>(?P<args>.*?)(?:<<into>>\$(?P<into>" + NAME + r"))?>>/>>")
VAR_RE = re.compile(r"<<startvar<STACK>>type:\{(?P<type>[^}]*)\}name:\{(?P<name>[^}]*)\}"
                    r"<<>>(?P<value>.*?)>>/>>")
IF_RE = re.compile(r"<<startif<<asm:\{(?P<asm>.*?)\}>>(?P<args>.*?)>>")
LOOP_RE = re.compile(r"<<startloop<<asm:\{(?P<asm>.*?)\}>>(?P<args>.*?)>>")
PRINT_TOKEN = re.compile(r'"(?P<text>[^"]*)"|\[(?P<space>\d+)word\]|\$(?P<var>' + NAME + r")")

INSULTS = [
    "Have you considered a career in something without keyboards?",
    "Even the assembler is embarrassed for you.",
    "I've seen better code written by a cat walking on a keyboard.",
    "This is why your variables forget you.",
    "Bold of you to call that a program.",
    "Take a deep breath. Then delete everything.",
    "Somewhere, a compiler is crying because of you.",
    "Wow. Just wow.",
]
COMPLIMENTS = [
    "It worked. I'm as surprised as you are.",
    "Fine. You win this time.",
    "Program finished. Don't let it go to your head.",
    "No errors. Statistically, that was luck.",
]


class Punishment(Exception):
    def __init__(self, lineno, message):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno
        self.message = message


@dataclass
class Stmt:
    lineno: int
    kind: str
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def flip_number(written, lineno=0):
    """Turn a written number into its real value (also works the other way round)."""
    negative = written.startswith("-")
    digits = written[1:] if negative else written
    for d in digits:
        if d in BANNED_DIGITS:
            raise Punishment(lineno, f"the digit {d} is banned. It doesn't survive being turned upside down.")
    value = int("".join(FLIP[d] for d in reversed(digits)))
    return -value if negative else value


def normalize_asm(asm):
    parts = []
    for part in asm.split(";"):
        part = re.sub(r"\s*,\s*", ", ", part.strip().lower())
        parts.append(" ".join(part.split()))
    return "; ".join(parts)


def decode_binary(bits, lineno):
    chunks = bits.split()
    if not chunks or any(not re.fullmatch(r"[01]{8}", c) for c in chunks):
        raise Punishment(lineno, "the function name must be 8-bit binary bytes separated by spaces.")
    return "".join(chr(int(c, 2)) for c in chunks)


def parse_value(token, lineno):
    token = token.strip()
    if re.fullmatch(r"-?\d+", token):
        return ("num", flip_number(token, lineno))
    if re.fullmatch(r"\$" + NAME, token):
        return ("var", token[1:])
    raise Punishment(lineno, f"'{token}' is neither a number nor a $variable.")


def parse_two_values(args, lineno, what):
    parts = args.split(",")
    if len(parts) != 2:
        raise Punishment(lineno, f"{what} needs exactly two values, like $x,1.")
    return [parse_value(p, lineno) for p in parts]


# ---------------------------------------------------------------- parser

def parse(source):
    lines = [(n, raw.strip()) for n, raw in enumerate(source.splitlines(), start=1)]
    lines = [(n, text) for n, text in lines if text]
    if not lines or lines[-1][1] != VALIDATION_LINE:
        lineno = lines[-1][0] if lines else 1
        raise Punishment(lineno, "the last line must be the validation line, letter for letter:\n  "
                         + VALIDATION_LINE)

    program = []
    for lineno, text in lines[:-1]:
        if text == VALIDATION_LINE:
            raise Punishment(lineno, "the validation line goes at the END. You can't give up halfway.")
        program.append(parse_line(lineno, text))
    link_blocks(program)
    return program


def parse_line(lineno, text):
    if text == "<<else>>":
        return Stmt(lineno, "else")
    if text == "<<endif>>/>>":
        return Stmt(lineno, "endif")
    if text == "<<endloop>>/>>":
        return Stmt(lineno, "endloop")

    m = VAR_RE.fullmatch(text)
    if m:
        if m["type"] not in TYPES:
            raise Punishment(lineno, f"'{m['type']}' is not a type. Pick one: {', '.join(TYPES)}.")
        if not re.fullmatch(NAME, m["name"]):
            raise Punishment(lineno, f"'{m['name']}' is not a valid variable name.")
        return Stmt(lineno, "var", {"type": m["type"], "name": m["name"],
                                    "value": parse_value(m["value"], lineno)})

    for kind, regex, label in (("if", IF_RE, "skip"), ("loop", LOOP_RE, "end")):
        m = regex.fullmatch(text)
        if m:
            asm = CMP_ASM.fullmatch(normalize_asm(m["asm"]))
            if not asm:
                raise Punishment(lineno, f"the {kind} asm must look like: cmp rax, rbx; <jump> {label}")
            jump, target = asm.groups()
            if jump not in JUMPS:
                raise Punishment(lineno, f"'{jump}' is not a jump I know. Try: {', '.join(JUMPS)}.")
            if target != label:
                raise Punishment(lineno, f"{kind} blocks must jump to '{label}', not '{target}'.")
            return Stmt(lineno, kind, {"jump": jump,
                                       "values": parse_two_values(m["args"], lineno, kind)})

    m = FUNC_RE.fullmatch(text)
    if m:
        return parse_func(lineno, m)

    raise Punishment(lineno, "I have no idea what this line is supposed to be.")


def parse_func(lineno, m):
    name = decode_binary(m["name"], lineno)
    if name not in BUILTINS:
        raise Punishment(lineno, f"your binary spells '{name}', which is not a built-in function.")
    asm = normalize_asm(m["asm"])

    if name == "print":
        asm_match = PRINT_ASM.fullmatch(asm)
        if not asm_match:
            raise Punishment(lineno, "that is not the Linux x86-64 asm for printing. It must be:\n  "
                             "mov rax, 1; mov rdi, 1; lea rsi, [msg]; mov rdx, LENGTH; syscall")
        if m["into"]:
            raise Punishment(lineno, "print gives nothing back. There's nothing to put <<into>> anything.")
        return Stmt(lineno, "print", {"length": int(asm_match[1]),
                                      "parts": parse_print_args(m["args"], lineno)})

    if asm != MATH_ASM[name]:
        raise Punishment(lineno, f"wrong asm for {name}. It must be: {MATH_ASM[name]}")
    if not m["into"]:
        raise Punishment(lineno, f"{name} makes a result and you threw it away. Add <<into>>$variable.")
    return Stmt(lineno, "math", {"op": name, "into": m["into"],
                                 "values": parse_two_values(m["args"], lineno, name)})


def parse_print_args(args, lineno):
    parts, pos = [], 0
    while pos < len(args):
        tok = PRINT_TOKEN.match(args, pos)
        if not tok:
            raise Punishment(lineno, f"I can't print this part: {args[pos:]!r}. "
                             'Use "TEXT", [Nword] and $variables only.')
        if tok["text"] is not None:
            if " " in tok["text"]:
                raise Punishment(lineno, "real spaces are not allowed in text. Use [Nword].")
            parts.append(("text", tok["text"]))
        elif tok["space"] is not None:
            parts.append(("space", int(tok["space"])))
        else:
            parts.append(("var", tok["var"]))
        pos = tok.end()
    return parts


def link_blocks(program):
    """Match every if/else/endif and loop/endloop."""
    open_blocks = []
    for index, s in enumerate(program):
        if s.kind in ("if", "loop"):
            s.data["else"] = None
            open_blocks.append(index)
        elif s.kind == "else":
            if not open_blocks or program[open_blocks[-1]].kind != "if":
                raise Punishment(s.lineno, "<<else>> without an if. Else WHAT?")
            opener = program[open_blocks[-1]]
            if opener.data["else"] is not None:
                raise Punishment(s.lineno, "one if, one else. Not two.")
            opener.data["else"] = index
        elif s.kind in ("endif", "endloop"):
            wanted = "if" if s.kind == "endif" else "loop"
            if not open_blocks or program[open_blocks[-1]].kind != wanted:
                raise Punishment(s.lineno, f"this closes a {wanted} that was never opened.")
            opener_index = open_blocks.pop()
            opener = program[opener_index]
            opener.data["end"] = index
            s.data["start"] = opener_index
            if opener.data["else"] is not None:
                program[opener.data["else"]].data["end"] = index
    if open_blocks:
        s = program[open_blocks[-1]]
        raise Punishment(s.lineno, f"this {s.kind} is never closed.")


# ---------------------------------------------------------------- machine

class Machine:
    def __init__(self, program, out):
        self.program = program
        self.out = out
        self.vars = {}        # name -> [value, type, step last touched]
        self.forgotten = set()
        self.step = 0

    def touch(self, name, lineno):
        if name not in self.vars:
            if name in self.forgotten:
                raise Punishment(lineno, f"${name} was forgotten. You ignored it and it left you.")
            raise Punishment(lineno, f"${name} never existed. Declare it with <<startvar<STACK>>.")
        entry = self.vars[name]
        ignored = self.step - entry[2] - 1
        if ignored >= MEMORY_SPAN:
            del self.vars[name]
            self.forgotten.add(name)
            raise Punishment(lineno, f"${name} was ignored for {ignored} lines and forgot itself. "
                             f"Use every variable at least once every {MEMORY_SPAN} lines.")
        entry[2] = self.step
        return entry

    def value(self, v, lineno):
        kind, x = v
        return x if kind == "num" else self.touch(x, lineno)[0]

    def check_range(self, value, type_, lineno):
        bits = TYPES[type_]
        low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
        if not low <= value <= high:
            raise Punishment(lineno, f"{value} doesn't fit in {type_} ({low} to {high}). Overflow.")

    def run(self):
        pc = 0
        program = self.program
        while pc < len(program):
            self.step += 1
            if self.step > MAX_STEPS:
                raise Punishment(program[pc].lineno, "infinite suffering detected. Even I have limits.")
            s = program[pc]
            d = s.data
            pc += 1

            if s.kind == "var":
                value = self.value(d["value"], s.lineno)
                self.check_range(value, d["type"], s.lineno)
                self.vars[d["name"]] = [value, d["type"], self.step]
                self.forgotten.discard(d["name"])

            elif s.kind == "print":
                self.print(s)

            elif s.kind == "math":
                a, b = (self.value(v, s.lineno) for v in d["values"])
                result = self.math(d["op"], a, b, s.lineno)
                entry = self.touch(d["into"], s.lineno)
                self.check_range(result, entry[1], s.lineno)
                entry[0] = result

            elif s.kind in ("if", "loop"):
                a, b = (self.value(v, s.lineno) for v in d["values"])
                if JUMPS[d["jump"]](a, b):            # jump taken -> skip the block
                    if s.kind == "loop":
                        pc = d["end"] + 1
                    elif d["else"] is not None:
                        pc = d["else"] + 1
                    else:
                        pc = d["end"]

            elif s.kind == "else":                    # reached the end of the "then" part
                pc = d["end"]

            elif s.kind == "endloop":
                pc = d["start"]

            # endif: nothing to do

    def math(self, op, a, b, lineno):
        if op == "add":
            return a + b
        if op == "sub":
            return a - b
        if op == "mul":
            return a * b
        if b == 0:
            raise Punishment(lineno, "division by zero. The CPU would have crashed too.")
        q = abs(a) // abs(b)                          # idiv rounds toward zero
        return q if (a < 0) == (b < 0) else -q

    def print(self, s):
        text = ""
        for kind, x in s.data["parts"]:
            if kind == "text":
                text += x
            elif kind == "space":
                if x != len(text):
                    raise Punishment(s.lineno, f"this space is at position {len(text)}, "
                                     f"but you wrote [{x}word].")
                text += " "
            else:
                text += str(self.touch(x, s.lineno)[0])
        if s.data["length"] != len(text):
            raise Punishment(s.lineno, f"mov rdx, {s.data['length']} but the text {text!r} "
                             f"is {len(text)} characters long.")
        print(text, file=self.out)


# ---------------------------------------------------------------- command line

def mercy(arg):
    if re.fullmatch(r"-?\d+", arg):
        if any(d in BANNED_DIGITS for d in arg.lstrip("-")):
            return f"{arg} can't be written directly (it has a 3, 4 or 7). Build it with math."
        written = ("-" if arg.startswith("-") else "") + "".join(FLIP[d] for d in reversed(arg.lstrip("-")))
        return f"To get {int(arg)}, write {written}"
    return f"'{arg}' in binary: " + " ".join(f"{ord(c):08b}" for c in arg)


def main(argv):
    if len(argv) == 3 and argv[1] == "--mercy":
        print(mercy(argv[2]))
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"Can't even open the file ({e}). Great start.", file=sys.stderr)
        return 1
    try:
        Machine(parse(source), sys.stdout).run()
    except Punishment as p:
        sys.stdout.flush()
        print(f"\nPUNISHMENT on line {p.lineno}: {p.message}", file=sys.stderr)
        print(random.choice(INSULTS), file=sys.stderr)
        return 1
    sys.stdout.flush()
    print(f"\n{random.choice(COMPLIMENTS)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
