#!/usr/bin/env python3
"""PunishMeEvenHarder++ interpreter (first draft).

Usage:
    python pmeh.py program.pmeh
    python pmeh.py --mercy 15       # how do I write the number 15?
    python pmeh.py --mercy print    # what is 'print' in binary?
    python pmeh.py --i-give-up program.pmeh   # readable errors, for cowards

The full rules are in SYNTAX.md.
"""
import codecs
import random
import re
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- constants

VALIDATION_LINE = ('<EXCPROGRAM>VALIDATE:COMMNT"I\'d rather die than code in '
                   'this programming language"[NOMERCYGRANTED]')
MEMORY_SPAN = 3          # a variable unused for this many executed lines is erased
MAX_STEPS = 1_000_000    # protection against infinite suffering
MAX_DEPTH = 50           # how many function calls deep you may go
MAX_LIST_BYTES = 1_000_000
GARBAGE_JUNK = ["banana", "notexisten<<>>", "undefined", "null", "NaN", "@i-wanna-cry",
                "<<startfunc<", "help me", "segfault", "}}{{", "0xDEADBEEF", "your homework",
                "<EXCPROGRAM>", "nothing", "42?", "AAAAAAAA"]
PARAM_REGISTERS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]   # Linux x86-64 calling convention

FLIP = {"0": "0", "1": "1", "2": "5", "5": "2", "6": "9", "8": "8", "9": "6"}
BANNED_DIGITS = "347"
TYPES = {"int8": 8, "int16": 16, "int32": 32, "int64": 64}

PRINT_ASM = re.compile(r"mov rax, 1; mov rdi, 1; lea rsi, \[msg\]; mov rdx, (\d+); syscall")
READ_ASM = re.compile(r"mov rax, 0; mov rdi, 0; lea rsi, \[buf\]; mov rdx, (\d+); syscall")
MATH_ASM = {
    "add": "add rax, rbx",
    "sub": "sub rax, rbx",
    "mul": "imul rax, rbx",
    "div": "cqo; idiv rbx",
}
STORE_ASM = "mov [rbx + rcx], rax"
LOAD_ASM = "mov rax, [rbx + rcx]"
BUILTINS = {"print", "read", "store", "load", *MATH_ASM}
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
LIST_RE = re.compile(r"<<startlist<HEAP>>type:\{(?P<type>[^}]*)\}name:\{(?P<name>[^}]*)\}"
                     r"bytes:\{(?P<bytes>[^}]*)\}>>/>>")
IF_RE = re.compile(r"<<startif<<asm:\{(?P<asm>.*?)\}>>(?P<args>.*?)>>")
LOOP_RE = re.compile(r"<<startloop<<asm:\{(?P<asm>.*?)\}>>(?P<args>.*?)>>")
DEF_RE = re.compile(r"<<definefunc<USER>>mco:\{(?P<name>[^}]*)\}params:\{(?P<params>[^}]*)\}>>")
RETURN_RE = re.compile(r"<<return<<asm:\{(?P<asm>.*?)\}>>(?P<value>.*?)>>/>>")
CALL_RE = re.compile(r"<<startfunc<FROMUSER><<asm:\{(?P<asm>.*?)\}>>mco:\{(?P<name>[^}]*)\}"
                     r"<<>>(?P<args>.*?)(?:<<into>>\$(?P<into>" + NAME + r"))?>>/>>")
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


class Garbage(str):
    """Whatever was lying around in memory. Not a number. Possibly not anything."""


class PMEHList:
    def __init__(self, type_, items):
        self.type = type_
        self.items = items
        self.item_bytes = TYPES[type_] // 8


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
        part = re.sub(r"\s*\+\s*", " + ", part)
        part = re.sub(r"\[\s*", "[", re.sub(r"\s*\]", "]", part))
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


def parse_values(args, lineno, what, count, example):
    parts = args.split(",")
    if len(parts) != count:
        raise Punishment(lineno, f"{what} needs exactly {count} values, like {example}.")
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
    link_calls(program)
    return program


def parse_line(lineno, text):
    if text == "<<else>>":
        return Stmt(lineno, "else")
    if text == "<<endif>>/>>":
        return Stmt(lineno, "endif")
    if text == "<<endloop>>/>>":
        return Stmt(lineno, "endloop")
    if text == "<<enddefine>>/>>":
        return Stmt(lineno, "enddefine")

    m = DEF_RE.fullmatch(text)
    if m:
        return parse_def(lineno, m)

    m = RETURN_RE.fullmatch(text)
    if m:
        if normalize_asm(m["asm"]) != "ret":
            raise Punishment(lineno, "the return asm is just: ret")
        return Stmt(lineno, "return", {"value": parse_value(m["value"], lineno)})

    m = CALL_RE.fullmatch(text)
    if m:
        name = decode_binary(m["name"], lineno)
        asm = re.fullmatch(r"call (\S+)", normalize_asm(m["asm"]))
        if not asm:
            raise Punishment(lineno, f"the asm for calling a function is: call {name}")
        if asm[1] != name.lower():
            raise Punishment(lineno, f"your asm calls '{asm[1]}' but your binary spells '{name}'. Pick one.")
        if not m["into"]:
            raise Punishment(lineno, f"{name} returns a value and you threw it away. Add <<into>>$variable.")
        args = [parse_value(a, lineno) for a in m["args"].split(",")] if m["args"].strip() else []
        return Stmt(lineno, "call", {"name": name, "args": args, "into": m["into"]})

    m = VAR_RE.fullmatch(text)
    if m:
        if m["type"] not in TYPES:
            raise Punishment(lineno, f"'{m['type']}' is not a type. Pick one: {', '.join(TYPES)}.")
        if not re.fullmatch(NAME, m["name"]):
            raise Punishment(lineno, f"'{m['name']}' is not a valid variable name.")
        return Stmt(lineno, "var", {"type": m["type"], "name": m["name"],
                                    "value": parse_value(m["value"], lineno)})

    m = LIST_RE.fullmatch(text)
    if m:
        if m["type"] not in TYPES:
            raise Punishment(lineno, f"'{m['type']}' is not a type. Pick one: {', '.join(TYPES)}.")
        if not re.fullmatch(NAME, m["name"]):
            raise Punishment(lineno, f"'{m['name']}' is not a valid list name.")
        return Stmt(lineno, "list", {"type": m["type"], "name": m["name"],
                                     "bytes": parse_value(m["bytes"], lineno)})

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

    if name == "read":
        asm_match = READ_ASM.fullmatch(asm)
        if not asm_match:
            raise Punishment(lineno, "that is not the Linux x86-64 asm for reading. It must be:\n  "
                             "mov rax, 0; mov rdi, 0; lea rsi, [buf]; mov rdx, MAXLEN; syscall")
        if m["args"].strip():
            raise Punishment(lineno, "read takes no arguments. Leave the space after <<>> empty.")
        if not m["into"]:
            raise Punishment(lineno, "you read input and threw it away. Add <<into>>$variable.")
        return Stmt(lineno, "read", {"maxlen": int(asm_match[1]), "into": m["into"]})

    if name in ("store", "load"):
        wanted = STORE_ASM if name == "store" else LOAD_ASM
        if asm != wanted:
            raise Punishment(lineno, f"wrong asm for {name}. It must be: {wanted}")
        if name == "store":
            if m["into"]:
                raise Punishment(lineno, "store gives nothing back. Drop the <<into>>.")
            return Stmt(lineno, "store", {"values": parse_values(m["args"], lineno, "store", 3, "$xs,8,$x")})
        if not m["into"]:
            raise Punishment(lineno, "you loaded an item and threw it away. Add <<into>>$variable.")
        return Stmt(lineno, "load", {"values": parse_values(m["args"], lineno, "load", 2, "$xs,8"),
                                     "into": m["into"]})

    if asm != MATH_ASM[name]:
        raise Punishment(lineno, f"wrong asm for {name}. It must be: {MATH_ASM[name]}")
    if not m["into"]:
        raise Punishment(lineno, f"{name} makes a result and you threw it away. Add <<into>>$variable.")
    return Stmt(lineno, "math", {"op": name, "into": m["into"],
                                 "values": parse_two_values(m["args"], lineno, name)})


def parse_def(lineno, m):
    name = decode_binary(m["name"], lineno)
    if not re.fullmatch(NAME, name):
        raise Punishment(lineno, f"your binary spells '{name}', which is not a valid function name.")
    if name in BUILTINS:
        raise Punishment(lineno, f"'{name}' is a built-in. You can't replace it, however much you hate it.")
    params = []
    if m["params"].strip():
        for i, part in enumerate(m["params"].split(",")):
            pm = re.fullmatch(r"\s*(\w+)\s*:\s*\$(" + NAME + r")\s*", part)
            if not pm:
                raise Punishment(lineno, f"'{part}' is not a parameter. Write them like rdi:$a")
            if i >= len(PARAM_REGISTERS):
                raise Punishment(lineno, "more than 6 parameters. Real CPUs ran out of registers, and so did I.")
            if pm[1] != PARAM_REGISTERS[i]:
                raise Punishment(lineno, f"parameter {i + 1} must go in {PARAM_REGISTERS[i]}, not {pm[1]}. "
                                 f"The order is {', '.join(PARAM_REGISTERS)}.")
            if pm[2] in params:
                raise Punishment(lineno, f"two parameters called ${pm[2]}. Be more creative.")
            params.append(pm[2])
    return Stmt(lineno, "def", {"name": name, "params": params})


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
    seen_code = False
    for index, s in enumerate(program):
        inside_def = any(program[i].kind == "def" for i in open_blocks)
        if s.kind == "def":
            if open_blocks:
                raise Punishment(s.lineno, "you can't define a function inside something else.")
            if seen_code:
                raise Punishment(s.lineno, "functions must be defined at the TOP, before any other code.")
            open_blocks.append(index)
            continue
        if s.kind == "enddefine":
            if not open_blocks or program[open_blocks[-1]].kind != "def":
                raise Punishment(s.lineno, "this closes a function that was never opened "
                                 "(or something inside it is still open).")
            program[open_blocks.pop()].data["end"] = index
            continue
        if not inside_def:
            seen_code = True
        if s.kind == "return" and not inside_def:
            raise Punishment(s.lineno, "return from WHAT? You're not in a function.")
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


def link_calls(program):
    functions = {}
    for index, s in enumerate(program):
        if s.kind == "def":
            if s.data["name"] in functions:
                raise Punishment(s.lineno, f"{s.data['name']} is already defined. Once was enough.")
            functions[s.data["name"]] = index
    for s in program:
        if s.kind == "call":
            if s.data["name"] not in functions:
                raise Punishment(s.lineno, f"there's no function called {s.data['name']}.")
            target = functions[s.data["name"]]
            wanted = len(program[target].data["params"])
            if len(s.data["args"]) != wanted:
                raise Punishment(s.lineno, f"{s.data['name']} takes {wanted} argument(s), "
                                 f"you gave {len(s.data['args'])}.")
            s.data["target"] = target


# ---------------------------------------------------------------- machine

class Machine:
    def __init__(self, program, out, inp=None, rng=None):
        self.program = program
        self.rng = rng if rng is not None else random.Random()
        self.out = out
        self.inp = inp if inp is not None else sys.stdin
        self.pending = ""     # typed characters that no read has taken yet
        self.vars = {}        # name -> [value, type, step last touched]
        self.forgotten = set()
        self.step = 0
        self.frames = []      # saved callers while a function runs

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

    def number(self, v, lineno):
        """A value that must be a real number."""
        value = self.value(v, lineno)
        if isinstance(value, PMEHList):
            raise Punishment(lineno, f"${v[1]} is a list, not a number. Load an item out of it first.")
        if isinstance(value, Garbage):
            raise Punishment(lineno, f"you tried to use {str(value)!r} as a number. That's garbage "
                             "from a list item you never stored anything in.")
        return value

    def assign(self, name, value, lineno):
        entry = self.touch(name, lineno)
        if entry[1] == "list":
            raise Punishment(lineno, f"${name} is a list. You can't pour a single value into it. Use store.")
        if isinstance(value, PMEHList):
            raise Punishment(lineno, f"a list can't be put into the variable ${name}.")
        self.check_range(value, entry[1], lineno)
        entry[0] = value

    def get_list(self, v, lineno):
        value = self.value(v, lineno)
        if not isinstance(value, PMEHList):
            raise Punishment(lineno, "the first value must be a list. That isn't one.")
        return value

    def item_index(self, lst, v, lineno):
        offset = self.number(v, lineno)
        if offset % lst.item_bytes:
            raise Punishment(lineno, f"misaligned memory access: offset {offset} is not a multiple of "
                             f"{lst.item_bytes} (the size of one {lst.type}).")
        if not 0 <= offset < len(lst.items) * lst.item_bytes:
            raise Punishment(lineno, f"Segmentation fault (core dumped). Offset {offset} is outside "
                             f"the list's {len(lst.items) * lst.item_bytes} bytes.")
        return offset // lst.item_bytes

    def garbage(self, type_):
        if self.rng.random() < 0.5:
            return Garbage(self.rng.choice(GARBAGE_JUNK))
        bits = TYPES[type_]
        return self.rng.randint(-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)

    def check_range(self, value, type_, lineno):
        if isinstance(value, Garbage):
            return                                    # garbage fits anywhere, sadly
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
                if isinstance(value, PMEHList):
                    raise Punishment(s.lineno, "a list can't be copied into a variable.")
                self.check_range(value, d["type"], s.lineno)
                self.vars[d["name"]] = [value, d["type"], self.step]
                self.forgotten.discard(d["name"])

            elif s.kind == "print":
                self.print(s)

            elif s.kind == "list":
                size = self.number(d["bytes"], s.lineno)
                item_bytes = TYPES[d["type"]] // 8
                if size <= 0:
                    raise Punishment(s.lineno, f"a list of {size} bytes. Ambitious.")
                if size % item_bytes:
                    raise Punishment(s.lineno, f"{size} bytes doesn't divide into {d['type']} items "
                                     f"({item_bytes} bytes each).")
                if size > MAX_LIST_BYTES:
                    raise Punishment(s.lineno, f"out of memory. Nobody needs {size} bytes.")
                items = [self.garbage(d["type"]) for _ in range(size // item_bytes)]
                self.vars[d["name"]] = [PMEHList(d["type"], items), "list", self.step]
                self.forgotten.discard(d["name"])

            elif s.kind == "store":
                lst = self.get_list(d["values"][0], s.lineno)
                index = self.item_index(lst, d["values"][1], s.lineno)
                value = self.value(d["values"][2], s.lineno)
                if isinstance(value, PMEHList):
                    raise Punishment(s.lineno, "you can't store a list inside a list.")
                self.check_range(value, lst.type, s.lineno)
                lst.items[index] = value

            elif s.kind == "load":
                lst = self.get_list(d["values"][0], s.lineno)
                index = self.item_index(lst, d["values"][1], s.lineno)
                self.assign(d["into"], lst.items[index], s.lineno)

            elif s.kind == "read":
                self.assign(d["into"], self.read(s), s.lineno)

            elif s.kind == "math":
                a, b = (self.number(v, s.lineno) for v in d["values"])
                self.assign(d["into"], self.math(d["op"], a, b, s.lineno), s.lineno)

            elif s.kind in ("if", "loop"):
                a, b = (self.number(v, s.lineno) for v in d["values"])
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

            elif s.kind == "def":                     # definitions only run when called
                pc = d["end"] + 1

            elif s.kind == "call":
                pc = self.call(s, pc)

            elif s.kind == "return":
                pc = self.ret(s)

            elif s.kind == "enddefine":
                raise Punishment(s.lineno, f"{program[self.frames[-1]['def']].data['name']} "
                                 "ended without returning anything.")

            # endif: nothing to do

    def call(self, s, pc):
        d = s.data
        if len(self.frames) >= MAX_DEPTH:
            raise Punishment(s.lineno, f"stack overflow: more than {MAX_DEPTH} calls deep. "
                             "You recursed into the abyss.")
        args = [self.value(v, s.lineno) for v in d["args"]]
        self.touch(d["into"], s.lineno)             # the result must have somewhere to go
        func = self.program[d["target"]]
        new_vars = {}
        for name, value in zip(func.data["params"], args):
            if isinstance(value, PMEHList):           # lists are passed by reference
                new_vars[name] = [value, "list", self.step]
            else:
                self.check_range(value, "int64", s.lineno)
                new_vars[name] = [value, "int64", self.step]
        self.frames.append({"return_to": pc, "vars": self.vars, "forgotten": self.forgotten,
                            "into": d["into"], "step": self.step, "lineno": s.lineno,
                            "def": d["target"]})
        self.vars, self.forgotten = new_vars, set()
        return d["target"] + 1

    def ret(self, s):
        value = self.value(s.data["value"], s.lineno)
        frame = self.frames.pop()
        elapsed = self.step - frame["step"]
        self.vars, self.forgotten = frame["vars"], frame["forgotten"]
        for entry in self.vars.values():            # the caller's variables were frozen
            entry[2] += elapsed
        self.assign(frame["into"], value, frame["lineno"])
        return frame["return_to"]

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

    def read(self, s):
        """Like the real read syscall: take at most MAXLEN characters, leave the rest waiting."""
        if not self.pending:
            self.out.flush()
            self.pending = self.inp.readline()
            if not self.pending:
                raise Punishment(s.lineno, "there is no input left to read. The user gave up.")
        chunk = self.pending[:s.data["maxlen"]]
        self.pending = self.pending[s.data["maxlen"]:]
        typed = chunk.rstrip("\n")
        if not typed:
            raise Punishment(s.lineno, f"read got {chunk!r}, which is not a number. "
                             "(Leftover characters from an earlier read count too.)")
        if not re.fullmatch(r"-?\d+", typed):
            raise Punishment(s.lineno, f"the user typed {typed!r}. That's not a number. "
                             "Even the user is bad at this.")
        return int(typed)

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
                value = self.touch(x, s.lineno)[0]
                if isinstance(value, PMEHList):
                    raise Punishment(s.lineno, f"${x} is a list. Print its items one at a time.")
                text += str(value)
        if s.data["length"] != len(text):
            raise Punishment(s.lineno, f"mov rdx, {s.data['length']} but the text {text!r} "
                             f"is {len(text)} characters long.")
        print(text, file=self.out, flush=True)


# ---------------------------------------------------------------- error reports
# Every error report contains everything you need. None of it is easy to read.

RED = "\x1b[1;31m"
RESET = "\x1b[0m"


def writable(n):
    return all(d in FLIP for d in str(n))


def upside_down(n):
    """Write a number the PMEH way. If it can't be written, split it into a sum."""
    if writable(n):
        return "".join(FLIP[d] for d in reversed(str(n)))
    for a in range(n - 1, 0, -1):
        if writable(a) and writable(n - a):
            return f"{upside_down(a)} <3 {upside_down(n - a)}"
    return "?"


def scramble(message):
    """Backwards, then ROT13."""
    return codecs.encode(message[::-1], "rot13")


def unscramble(text):
    return codecs.encode(text, "rot13")[::-1]


def paint(text, rng):
    """Every character in a random dim colour on black. Some are black on black."""
    out = []
    for ch in text:
        if ch == "\n":
            out.append(RESET + ch)
        else:
            out.append(f"\x1b[2;3{rng.randrange(8)};40m{ch}")
    return "".join(out) + RESET


def punishment_report(p, source, rng=random):
    lines = source.splitlines()
    from_bottom = len(lines) - p.lineno + 1
    others = [l.strip() for n, l in enumerate(lines, 1) if l.strip() and n != p.lineno]
    report = [
        f"\n{RED}PUNISHMENT.{RESET}",
        f"Location: line {upside_down(max(from_bottom, 1))}, counted from the bottom.",
        f"Reason: {paint(scramble(p.message), rng)}",
    ]
    if others:
        report.append(f"This line is definitely NOT the problem: {rng.choice(others)}")
    report.append(f"{RED}{rng.choice(INSULTS)}{RESET}")
    return "\n".join(report)


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
    coward = len(argv) == 3 and argv[1] == "--i-give-up"
    if coward:
        argv = [argv[0], argv[2]]
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
        if coward:
            print(f"\nPUNISHMENT on line {p.lineno}: {p.message}", file=sys.stderr)
            print("Coward.", file=sys.stderr)
        else:
            print(punishment_report(p, source), file=sys.stderr)
        return 1
    sys.stdout.flush()
    print(f"\n{random.choice(COMPLIMENTS)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
