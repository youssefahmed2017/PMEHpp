#!/usr/bin/env python3
"""PunishMeEvenHarder++ interpreter (first draft).

Run `python pmeh.py -h` for the command line options. The full rules are in SYNTAX.md.
"""
import argparse
import codecs
import os
import math
import random
import re
import struct
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------- constants

__version__ = "0.4.0"

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
FLOATS = {"float32": 32, "float64": 64}
FLOAT_MATH = {op: {64: f"{op}sd xmm0, xmm1", 32: f"{op}ss xmm0, xmm1"} for op in ("add", "sub", "mul", "div")}
FCMP_ASM = re.compile(r"ucomis([sd]) xmm0, xmm1; (j[a-z]+) (\w+)")
FJUMPS = {  # after ucomisd: taken when this is true (zf, cf are the real x86 flags; NaN sets both)
    "ja": lambda zf, cf: not cf and not zf,
    "jae": lambda zf, cf: not cf,
    "jb": lambda zf, cf: cf,
    "jbe": lambda zf, cf: cf or zf,
    "je": lambda zf, cf: zf,
    "jne": lambda zf, cf: not zf,
}
CVT = {  # the real conversion instructions: asm -> (from, to)
    "cvtsi2sd xmm0, rax": ("int", "float64"),
    "cvtsi2ss xmm0, rax": ("int", "float32"),
    "cvttsd2si rax, xmm0": ("float64", "int"),
    "cvttss2si rax, xmm0": ("float32", "int"),
    "cvtss2sd xmm0, xmm0": ("float32", "float64"),
    "cvtsd2ss xmm0, xmm0": ("float64", "float32"),
}
INDEFINITE = -2 ** 63            # what x86 gives when a decimal doesn't fit in a whole number

PRINT_ASM = re.compile(r"mov rax, 1; mov rdi, 1; lea rsi, \[msg\]; mov rdx, (\d+); syscall")
RAND_ASM = re.compile(r"mov rax, 318; lea rdi, \[buf\]; mov rsi, (\d+); mov rdx, 0; syscall")
READ_ASM = re.compile(r"mov rax, 0; mov rdi, (0|\[fd\]); lea rsi, \[buf\]; mov rdx, (\d+); syscall")
WRITE_ASM = re.compile(r"mov rax, 1; mov rdi, \[fd\]; lea rsi, \[msg\]; mov rdx, (\d+); syscall")
OPEN_ASM = re.compile(r"mov rax, 2; lea rdi, \[path\]; mov rsi, (\d+); mov rdx, (\d+); syscall")
CLOSE_ASM = "mov rax, 3; mov rdi, [fd]; syscall"
# Linux open() flags, as plain numbers, the way the kernel sees them
O_WRONLY, O_RDWR, O_CREAT, O_TRUNC, O_APPEND = 1, 2, 64, 512, 1024
EBADF, EINVAL = 9, 22
MATH_ASM = {
    "add": "add rax, rbx",
    "sub": "sub rax, rbx",
    "mul": "imul rax, rbx",
    "div": "cqo; idiv rbx",
    "mod": "cqo; idiv rbx; mov rax, rdx",
}
STORE_ASM = "mov [rbx + rcx], rax"
LOAD_ASM = "mov rax, [rbx + rcx]"
BUILTINS = {"print", "read", "write", "open", "close", "rand", "store", "load", "cvt", *MATH_ASM}
LIBC = {  # name: (number of arguments, gives a result back)
    "strlen": (1, True),
    "strcat": (2, False),
    "strcmp": (2, True),
    "atoi": (1, True),
    "itoa": (1, True),
    "charat": (2, True),
}
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
COMMENT_RE = re.compile(r'<<COMMNT"(?P<text>[^"]*)">>/>>')
IMPORT_RE = re.compile(r"<<import<FILE>>path:\{(?P<bits>[^}]*)\}>>/>>")
DEF_RE = re.compile(r"<<definefunc<USER>>mco:\{(?P<name>[^}]*)\}params:\{(?P<params>[^}]*)\}>>")
RETURN_RE = re.compile(r"<<return<<asm:\{(?P<asm>.*?)\}>>(?P<value>.*?)>>/>>")
CALL_RE = re.compile(r"<<startfunc<FROMUSER><<asm:\{(?P<asm>.*?)\}>>mco:\{(?P<name>[^}]*)\}"
                     r"<<>>(?P<args>.*?)(?:<<into>>\$(?P<into>" + NAME + r"))?>>/>>")
LIBC_RE = re.compile(r"<<startfunc<FROMLIBC><<asm:\{(?P<asm>.*?)\}>>mco:\{(?P<name>[^}]*)\}"
                     r"<<>>(?P<args>.*?)(?:<<into>>\$(?P<into>" + NAME + r"))?>>/>>")
TEXT_TOKEN = re.compile(r'"(?P<text>[^"]*)"|\[(?P<space>\d+)word\]|(?P<null>\[0null\])')
PRINT_TOKEN = re.compile(r'"(?P<text>[^"]*)"|\[(?P<space>\d+)word\]|\[(?P<line>\d+)line\]'
                         r'|\$(?P<var>' + NAME + r")")

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

class Garbage(str):
    """Whatever was lying around in memory. Not a number. Possibly not anything."""


class PMEHList:
    def __init__(self, type_, items):
        self.type = type_
        self.items = items
        self.item_bytes = TYPES[type_] // 8


class TextBuf:
    """A char[N] buffer: N bytes. Text ends at the first "\\0" (if there is one)."""
    def __init__(self, size, data):
        self.size = size
        self.data = data


class CText(str):
    """Text produced by a function (itoa), with its terminator."""


class F32(float):
    """A float32 value (stored exactly as a Python float)."""


def round32(x):
    """Round a decimal to float32, like the real cvtsd2ss: nearest, ties to even, too big -> inf."""
    if math.isnan(x) or math.isinf(x):
        return F32(x)
    try:
        return F32(struct.unpack("f", struct.pack("f", x))[0])
    except OverflowError:
        return F32(math.copysign(math.inf, x))


def int_to_f32(n):
    """cvtsi2ss: round a whole number straight to float32 (not via float64)."""
    size = abs(n).bit_length()
    if size <= 24:
        return F32(float(n))
    shift = size - 24
    q, r = divmod(abs(n), 1 << shift)
    half = 1 << (shift - 1)
    if r > half or (r == half and q & 1):
        q += 1
    return F32(math.copysign(float(q << shift), n))


def truncate(x):
    """cvttsd2si: chop off the decimals. NaN, inf or too big gives the "integer indefinite" value."""
    if math.isnan(x) or math.isinf(x):
        return INDEFINITE
    n = math.trunc(x)
    return n if -2 ** 63 <= n < 2 ** 63 else INDEFINITE


def float_op(op, a, b):
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if b == 0:                                       # no punishment: the hardware doesn't care either
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def show_decimal(x):
    """How decimals are printed: always 17 significant digits, like C's %.17g."""
    return "%.17g" % x


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


def flip_decimal(written, lineno=0):
    """A written decimal turned upside down: the whole thing reversed, every digit flipped."""
    negative = written.startswith("-")
    body = written[1:] if negative else written
    for d in body:
        if d in BANNED_DIGITS:
            raise Punishment(lineno, f"the digit {d} is banned. It doesn't survive being turned upside down.")
    value = float("".join(FLIP.get(c, c) for c in reversed(body)))
    return -value if negative else value


def parse_value(token, lineno):
    token = token.strip()
    if re.fullmatch(r"-?\d+\.\d+", token):
        return ("num", flip_decimal(token, lineno))
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


# ---------------------------------------------------------------- imports

def glue(source, filename="-c", folder="."):
    """Paste every imported file where its import line was, and return the glued program
    plus where each glued line came from: [(file, line)]. Line numbers in punishments count
    lines of the glued program."""
    out, origins, seen = [], [], set()

    def here():
        return len(out) + 1

    def expand(text, name, folder, chain, is_main):
        lines = text.splitlines()
        last = max((i for i, line in enumerate(lines) if line.strip()), default=None)
        if not is_main and (last is None or lines[last].strip() != VALIDATION_LINE):
            raise Punishment(here(), f"the imported file {name} must end with the validation line too. "
                             "Everyone suffers.")
        started = inside = False
        for n, line in enumerate(lines, start=1):
            text_ = line.strip()
            m = IMPORT_RE.fullmatch(text_)
            if m:
                if started:
                    raise Punishment(here(), "imports go at the very top of a file, before anything else.")
                file_name = decode_binary(m["bits"], here())[::-1]     # binary of the name, backwards
                path = os.path.join(folder, file_name)
                key = os.path.realpath(path)
                if key in chain:
                    raise Punishment(here(), f"{file_name} imports itself, through a circle of imports. "
                                     "Suffering can't be infinite.")
                if key in seen:
                    raise Punishment(here(), f"{file_name} was already imported. Once is enough.")
                try:
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                except OSError as e:
                    raise Punishment(here(), f"can't import {file_name}: {e.strerror}.")
                seen.add(key)
                expand(content, file_name, os.path.dirname(path), chain | {key}, False)
                continue
            if not is_main and n - 1 == last:
                continue                              # an imported file's validation line
            if text_ and not COMMENT_RE.fullmatch(text_):
                started = True
                if not is_main:
                    if DEF_RE.fullmatch(text_):
                        inside = True
                    elif text_ == "<<enddefine>>/>>":
                        inside = False
                    elif not inside:
                        raise Punishment(here(), f"imported files may only contain functions, but {name} "
                                         f"has this on its line {n}: {text_}")
            out.append(line)
            origins.append((name, n))

    main_path = os.path.realpath(filename) if os.path.isfile(filename) else None
    expand(source, filename, folder, {main_path} if main_path else set(), True)
    return "\n".join(out) + "\n", origins


def where(lineno, origins):
    """Where a glued line really is, for plain error messages: " (file, line N)"."""
    if origins and len({name for name, _ in origins}) > 1 and 1 <= lineno <= len(origins):
        name, n = origins[lineno - 1]
        return f" ({name}, line {n})"
    return ""


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

    m = COMMENT_RE.fullmatch(text)
    if m:
        return Stmt(lineno, "comment", {"text": m["text"][::-1]})   # written backwards

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
    if m and re.fullmatch(r"char\[.*\]", m["type"]):
        return parse_text_var(lineno, m)
    if m:
        if m["type"] not in TYPES and m["type"] not in FLOATS:
            raise Punishment(lineno, f"'{m['type']}' is not a type. Pick one: "
                             f"{', '.join([*TYPES, *FLOATS])}, or char[N].")
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
            fasm = FCMP_ASM.fullmatch(normalize_asm(m["asm"]))
            if asm:
                jump, target = asm.groups()
                bits = None
                if jump not in JUMPS:
                    raise Punishment(lineno, f"'{jump}' is not a jump I know. Try: {', '.join(JUMPS)}.")
            elif fasm:
                size, jump, target = fasm.groups()
                bits = 64 if size == "d" else 32
                if jump in JUMPS and jump not in FJUMPS:
                    raise Punishment(lineno, f"after ucomis{size}, {jump} doesn't work. Decimals use the "
                                     f"other jumps: {', '.join(FJUMPS)}.")
                if jump not in FJUMPS:
                    raise Punishment(lineno, f"'{jump}' is not a jump I know. Try: {', '.join(FJUMPS)}.")
            else:
                raise Punishment(lineno, f"the {kind} asm must look like: cmp rax, rbx; <jump> {label}\n"
                                 f"or, for decimals: ucomisd xmm0, xmm1; <jump> {label}")
            if target != label:
                raise Punishment(lineno, f"{kind} blocks must jump to '{label}', not '{target}'.")
            return Stmt(lineno, kind, {"jump": jump, "float": bits,
                                       "values": parse_two_values(m["args"], lineno, kind)})

    m = FUNC_RE.fullmatch(text)
    if m:
        return parse_func(lineno, m)

    m = LIBC_RE.fullmatch(text)
    if m:
        return parse_libc(lineno, m)

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
                             "mov rax, 0; mov rdi, 0; lea rsi, [buf]; mov rdx, MAXLEN; syscall\n"
                             "or, for a file: mov rax, 0; mov rdi, [fd]; lea rsi, [buf]; mov rdx, MAXLEN; syscall")
        if not m["into"]:
            raise Punishment(lineno, "you read input and threw it away. Add <<into>>$variable.")
        if asm_match[1] == "0":
            if m["args"].strip():
                raise Punishment(lineno, "reading the keyboard (mov rdi, 0) takes no arguments.")
            fd = ("num", 0)
        else:
            fd = parse_values(m["args"], lineno, "reading a file (mov rdi, [fd])", 1, "$fd")[0]
        return Stmt(lineno, "read", {"maxlen": int(asm_match[2]), "into": m["into"], "fd": fd})

    if name == "write":
        asm_match = WRITE_ASM.fullmatch(asm)
        if not asm_match:
            raise Punishment(lineno, "that is not the Linux x86-64 asm for write. It must be:\n  "
                             "mov rax, 1; mov rdi, [fd]; lea rsi, [msg]; mov rdx, LENGTH; syscall")
        if not m["into"]:
            raise Punishment(lineno, "write tells you how many bytes it wrote, and you ignored it. "
                             "Add <<into>>$variable.")
        fd, comma, rest = m["args"].partition(",")
        if not comma:
            raise Punishment(lineno, 'write needs the file first, then what to write: $fd,"TEXT"')
        return Stmt(lineno, "write", {"length": int(asm_match[1]), "into": m["into"],
                                      "fd": parse_value(fd, lineno),
                                      "parts": parse_print_args(rest, lineno)})

    if name == "open":
        asm_match = OPEN_ASM.fullmatch(asm)
        if not asm_match:
            raise Punishment(lineno, "that is not the Linux x86-64 asm for open. It must be:\n  "
                             "mov rax, 2; lea rdi, [path]; mov rsi, FLAGS; mov rdx, MODE; syscall")
        if not m["into"]:
            raise Punishment(lineno, "open gives you a file descriptor and you threw it away. "
                             "Add <<into>>$variable.")
        path = parse_values(m["args"], lineno, "open", 1, "$path")[0]
        return Stmt(lineno, "open", {"flags": int(asm_match[1]), "mode": int(asm_match[2]),
                                     "path": path, "into": m["into"]})

    if name == "close":
        if asm != CLOSE_ASM:
            raise Punishment(lineno, f"that is not the Linux x86-64 asm for close. It must be: {CLOSE_ASM}")
        if not m["into"]:
            raise Punishment(lineno, "close tells you if it worked, and you ignored it. Add <<into>>$variable.")
        fd = parse_values(m["args"], lineno, "close", 1, "$fd")[0]
        return Stmt(lineno, "close", {"fd": fd, "into": m["into"]})

    if name == "rand":
        asm_match = RAND_ASM.fullmatch(asm)
        if not asm_match:
            raise Punishment(lineno, "that is not the Linux x86-64 asm for getrandom. It must be:\n  "
                             "mov rax, 318; lea rdi, [buf]; mov rsi, BYTES; mov rdx, 0; syscall")
        if m["args"].strip():
            raise Punishment(lineno, "rand takes no arguments. Leave the space after <<>> empty.")
        if not m["into"]:
            raise Punishment(lineno, "you asked for randomness and threw it away. Add <<into>>$variable.")
        return Stmt(lineno, "rand", {"bytes": int(asm_match[1]), "into": m["into"]})

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

    if name == "cvt":
        if asm not in CVT:
            raise Punishment(lineno, "cvt needs one of the real conversion instructions:\n  "
                             + "\n  ".join(CVT))
        if not m["into"]:
            raise Punishment(lineno, "you converted a number and threw it away. Add <<into>>$variable.")
        return Stmt(lineno, "cvt", {"asm": asm, "into": m["into"],
                                    "value": parse_values(m["args"], lineno, "cvt", 1, "$x")[0]})

    if name in FLOAT_MATH and asm in FLOAT_MATH[name].values():
        if not m["into"]:
            raise Punishment(lineno, f"{name} makes a result and you threw it away. Add <<into>>$variable.")
        return Stmt(lineno, "fmath", {"op": name, "bits": 64 if asm == FLOAT_MATH[name][64] else 32,
                                      "into": m["into"],
                                      "values": parse_two_values(m["args"], lineno, name)})
    if asm != MATH_ASM[name]:
        others = f" (or, for decimals: {FLOAT_MATH[name][64]} / {FLOAT_MATH[name][32]})" if name in FLOAT_MATH else ""
        raise Punishment(lineno, f"wrong asm for {name}. It must be: {MATH_ASM[name]}{others}")
    if not m["into"]:
        raise Punishment(lineno, f"{name} makes a result and you threw it away. Add <<into>>$variable.")
    return Stmt(lineno, "math", {"op": name, "into": m["into"],
                                 "values": parse_two_values(m["args"], lineno, name)})


def parse_text_var(lineno, m):
    size_written = m["type"][5:-1].strip()
    if not re.fullmatch(r"\d+", size_written):
        raise Punishment(lineno, f"char[{size_written}] needs a plain number of bytes inside the brackets.")
    size = flip_number(size_written, lineno)
    if size <= 0:
        raise Punishment(lineno, f"char[{size_written}] is {size} bytes. Text needs room to exist.")
    if size > MAX_LIST_BYTES:
        raise Punishment(lineno, f"out of memory. Nobody needs {size} bytes of text.")
    if not re.fullmatch(NAME, m["name"]):
        raise Punishment(lineno, f"'{m['name']}' is not a valid variable name.")
    raw = m["value"].strip()
    if not raw:
        value = ("empty", None)
    elif re.fullmatch(r"\$" + NAME, raw):
        value = ("var", raw[1:])
    else:
        value = ("text", parse_text_literal(raw, lineno))
    return Stmt(lineno, "text", {"size": size, "name": m["name"], "value": value})


def parse_text_literal(written, lineno):
    """Text is written backwards: undo it. Returns the real characters, terminators included."""
    parts, pos = [], 0
    while pos < len(written):
        tok = TEXT_TOKEN.match(written, pos)
        if not tok:
            raise Punishment(lineno, f"I can't understand this text: {written[pos:]!r}. "
                             'Use "TEXT", [Nword] and [0null] only.')
        if tok["text"] is not None:
            if " " in tok["text"]:
                raise Punishment(lineno, "real spaces are not allowed in text. Use [Nword].")
            parts.append(("text", tok["text"]))
        elif tok["space"] is not None:
            parts.append(("space", int(tok["space"])))
        else:
            parts.append(("null", None))
        pos = tok.end()
    real = ""
    for kind, x in reversed(parts):
        if kind == "text":
            real += x[::-1]
        elif kind == "null":
            real += "\0"
        else:
            if x != len(real):
                raise Punishment(lineno, f"this space is at position {len(real)} of the real "
                                 f"(forwards) text, but you wrote [{x}word].")
            real += " "
    return real


def parse_libc(lineno, m):
    name = decode_binary(m["name"], lineno)
    asm = re.fullmatch(r"call (\S+)", normalize_asm(m["asm"]))
    if not asm:
        raise Punishment(lineno, f"the asm for calling a C library function is: call {name}")
    if asm[1] != name.lower():
        raise Punishment(lineno, f"your asm calls '{asm[1]}' but your binary spells '{name}'. Pick one.")
    if name not in LIBC:
        raise Punishment(lineno, f"'{name}' is not in the C library. Choose from: {', '.join(LIBC)}.")
    count, returns = LIBC[name]
    args = [parse_value(a, lineno) for a in m["args"].split(",")] if m["args"].strip() else []
    if len(args) != count:
        raise Punishment(lineno, f"{name} takes {count} argument(s), you gave {len(args)}.")
    if returns and not m["into"]:
        raise Punishment(lineno, f"{name} gives a result and you threw it away. Add <<into>>$variable.")
    if not returns and m["into"]:
        raise Punishment(lineno, f"{name} gives nothing back. Drop the <<into>>.")
    return Stmt(lineno, "libc", {"name": name, "args": args, "into": m["into"]})


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
                             'Use "TEXT", [Nword], [Nline] and $variables only.')
        if tok["text"] is not None:
            if " " in tok["text"]:
                raise Punishment(lineno, "real spaces are not allowed in text. Use [Nword].")
            parts.append(("text", tok["text"]))
        elif tok["space"] is not None:
            parts.append(("space", int(tok["space"])))
        elif tok["line"] is not None:
            parts.append(("line", int(tok["line"])))
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
        if not inside_def and s.kind != "comment":
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
        self.files = {}       # PMEH file descriptor -> real file descriptor

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
        if isinstance(value, TextBuf):
            raise Punishment(lineno, f"${v[1]} is text, not a number. Use atoi.")
        if isinstance(value, Garbage):
            raise Punishment(lineno, f"you tried to use {str(value)!r} as a number. That's garbage "
                             "from memory you never stored anything in.")
        if isinstance(value, float):
            raise Punishment(lineno, f"{show_decimal(value)} is a decimal. Only whole numbers work here. "
                             "Convert it with cvttsd2si.")
        return value

    def decimal(self, v, lineno, bits):
        """A value for a decimal instruction: float64 for sd, float32 for ss."""
        value = self.value(v, lineno)
        if isinstance(value, PMEHList):
            raise Punishment(lineno, f"${v[1]} is a list, not a decimal. Load an item out of it first.")
        if isinstance(value, TextBuf):
            raise Punishment(lineno, f"${v[1]} is text, not a decimal.")
        if isinstance(value, Garbage):
            raise Punishment(lineno, f"you tried to use {str(value)!r} as a decimal. That's garbage "
                             "from memory you never stored anything in.")
        if not isinstance(value, float):
            raise Punishment(lineno, f"{value} is a whole number, and decimal instructions need decimals. "
                             f"Write it like 1.0, or convert it with cvtsi2s{'d' if bits == 64 else 's'}.")
        if v[0] == "num":                             # a written decimal fits whatever it's used with
            return round32(value) if bits == 32 else value
        if bits == 32 and not isinstance(value, F32):
            raise Punishment(lineno, f"${v[1]} is a float64, and ...ss instructions need float32. "
                             "Convert it with cvtsd2ss.")
        if bits == 64 and isinstance(value, F32):
            raise Punishment(lineno, f"${v[1]} is a float32, and ...sd instructions need float64. "
                             "Convert it with cvtss2sd.")
        return value

    def assign(self, name, value, lineno):
        entry = self.touch(name, lineno)
        if entry[1] == "list":
            raise Punishment(lineno, f"${name} is a list. You can't pour a single value into it. Use store.")
        if isinstance(value, PMEHList):
            raise Punishment(lineno, f"a list can't be put into the variable ${name}.")
        if entry[1] == "char":
            if isinstance(value, TextBuf):
                value = CText(self.c_string(value) + "\0")
            if not isinstance(value, CText):
                raise Punishment(lineno, f"${name} is text. {str(value)!r} isn't. Use itoa.")
            self.write_text(name, list(value), lineno)
            return
        if isinstance(value, (TextBuf, CText)):
            raise Punishment(lineno, f"${name} holds numbers. Text doesn't go in it. Use atoi.")
        self.check_range(value, entry[1], lineno)
        entry[0] = value

    # -- text --------------------------------------------------------------

    def garbage_chars(self, count):
        junk = ""
        while len(junk) < count:
            junk += self.rng.choice(GARBAGE_JUNK)
        return list(junk[:count])

    def c_string(self, buf):
        """Read text the C way: up to the first \\0. No \\0? Keep reading into whatever is next."""
        if "\0" in buf.data:
            return "".join(buf.data[:buf.data.index("\0")])
        return "".join(buf.data) + "".join(self.garbage_chars(self.rng.randint(3, 20)))

    def get_text(self, v, lineno):
        value = self.value(v, lineno)
        if not isinstance(value, TextBuf):
            what = f"${v[1]}" if v[0] == "var" else str(v[1])
            raise Punishment(lineno, f"{what} isn't text.")
        return value

    def write_text(self, name, chars, lineno):
        """Write bytes into a buffer. Whatever doesn't fit spills into the next variables."""
        buf = self.vars[name][0]
        fits = chars[:buf.size]
        buf.data[:len(fits)] = fits
        if len(chars) > buf.size:
            self.spill(name, chars[buf.size:], lineno)

    def spill(self, after, chars, lineno):
        names = list(self.vars)
        for name in names[names.index(after) + 1:]:
            entry = self.vars[name]
            if entry[1] == "char":
                buf = entry[0]
                chunk, chars = chars[:buf.size], chars[buf.size:]
                buf.data[:len(chunk)] = chunk
            elif entry[1] == "list":
                lst = entry[0]
                for i in range(len(lst.items)):
                    chunk, chars = chars[:lst.item_bytes], chars[lst.item_bytes:]
                    if not chunk:
                        break
                    lst.items[i] = Garbage("".join(chunk))
            else:
                size = {**TYPES, **FLOATS}[entry[1]] // 8
                chunk, chars = chars[:size], chars[size:]
                entry[0] = Garbage("".join(chunk))
            if not chars:
                return
        raise Punishment(lineno, "*** stack smashing detected ***: terminated. "
                         "Your text overflowed past every variable you have.")

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
        if type_ in FLOATS:
            if not isinstance(value, float):
                raise Punishment(lineno, f"{type_} holds decimals. {value} is a whole number. "
                                 f"Convert it with cvtsi2s{'d' if type_ == 'float64' else 's'}.")
            if (type_ == "float32") != isinstance(value, F32):
                other = "float64" if type_ == "float32" else "float32"
                how = "cvtsd2ss" if type_ == "float32" else "cvtss2sd"
                raise Punishment(lineno, f"{type_} can't hold a {other} value. Convert it with {how}.")
            return
        if isinstance(value, float):
            raise Punishment(lineno, f"{type_} holds whole numbers. {show_decimal(value)} is a decimal. "
                             "Convert it with cvttsd2si.")
        bits = TYPES[type_]
        low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
        if not low <= value <= high:
            raise Punishment(lineno, f"{value} doesn't fit in {type_} ({low} to {high}). Overflow.")

    def run(self):
        try:
            self.execute()
            if self.files:
                last = self.program[-1].lineno + 1 if self.program else 1
                raise Punishment(last, f"the program ended with {len(self.files)} file(s) still open "
                                 f"(file descriptor {', '.join(map(str, sorted(self.files)))}). Close the door behind you.")
        finally:
            for real in self.files.values():
                os.close(real)
            self.files.clear()

    def execute(self):
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
                if isinstance(value, TextBuf):
                    raise Punishment(s.lineno, f"{d['type']} holds numbers. Text doesn't go in it. Use atoi.")
                if d["type"] == "float32" and d["value"][0] == "num" and isinstance(value, float):
                    value = round32(value)            # a written decimal fits whatever it's put in
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

            elif s.kind == "text":
                kind, x = d["value"]
                chars = None
                if kind == "text":
                    chars = list(x)
                elif kind == "var":
                    chars = list(self.c_string(self.get_text(d["value"], s.lineno)) + "\0")
                self.vars[d["name"]] = [TextBuf(d["size"], self.garbage_chars(d["size"])),
                                        "char", self.step]
                self.forgotten.discard(d["name"])
                if chars is not None:
                    self.write_text(d["name"], chars, s.lineno)

            elif s.kind == "libc":
                self.libc(s)

            elif s.kind == "rand":
                entry = self.touch(d["into"], s.lineno)
                if entry[1] not in TYPES:
                    raise Punishment(s.lineno, f"rand gives a number. ${d['into']} can't hold one.")
                bits = TYPES[entry[1]]
                if d["bytes"] != bits // 8:
                    raise Punishment(s.lineno, f"you asked getrandom for {d['bytes']} bytes, but "
                                     f"${d['into']} is {entry[1]}: {bits // 8} bytes.")
                value = self.rng.getrandbits(bits)
                if value >= 2 ** (bits - 1):          # random bytes are signed, like the real thing
                    value -= 2 ** bits
                self.assign(d["into"], value, s.lineno)

            # comment: does nothing, but it still counts as a line

            elif s.kind == "store":
                lst = self.get_list(d["values"][0], s.lineno)
                index = self.item_index(lst, d["values"][1], s.lineno)
                value = self.value(d["values"][2], s.lineno)
                if isinstance(value, (PMEHList, TextBuf)):
                    raise Punishment(s.lineno, "lists hold numbers. You can't store a list or text in one.")
                self.check_range(value, lst.type, s.lineno)
                lst.items[index] = value

            elif s.kind == "load":
                lst = self.get_list(d["values"][0], s.lineno)
                index = self.item_index(lst, d["values"][1], s.lineno)
                self.assign(d["into"], lst.items[index], s.lineno)

            elif s.kind == "open":
                self.assign(d["into"], self.open_file(s), s.lineno)

            elif s.kind == "close":
                fd = self.number(d["fd"], s.lineno)
                result = -EBADF
                if fd in self.files:
                    os.close(self.files.pop(fd))
                    result = 0
                self.assign(d["into"], result, s.lineno)

            elif s.kind == "write":
                self.assign(d["into"], self.write(s), s.lineno)

            elif s.kind == "read":
                typed = self.read(s)
                if self.touch(d["into"], s.lineno)[1] == "char":
                    self.assign(d["into"], CText(typed + "\0"), s.lineno)
                else:
                    if not re.fullmatch(r"-?\d+", typed):
                        raise Punishment(s.lineno, f"the user typed {typed!r}. That's not a number. "
                                         "Even the user is bad at this.")
                    self.assign(d["into"], int(typed), s.lineno)

            elif s.kind == "math":
                a, b = (self.number(v, s.lineno) for v in d["values"])
                self.assign(d["into"], self.math(d["op"], a, b, s.lineno), s.lineno)

            elif s.kind == "fmath":
                a, b = (self.decimal(v, s.lineno, d["bits"]) for v in d["values"])
                result = float_op(d["op"], a, b)
                self.assign(d["into"], round32(result) if d["bits"] == 32 else result, s.lineno)

            elif s.kind == "cvt":
                source, target = CVT[d["asm"]]
                if source == "int":
                    x = self.number(d["value"], s.lineno)
                    result = float(x) if target == "float64" else int_to_f32(x)
                else:
                    x = self.decimal(d["value"], s.lineno, FLOATS[source])
                    result = {"int": truncate, "float64": float, "float32": round32}[target](x)
                self.assign(d["into"], result, s.lineno)

            elif s.kind in ("if", "loop"):
                if d["float"]:
                    a, b = (self.decimal(v, s.lineno, d["float"]) for v in d["values"])
                    unordered = math.isnan(a) or math.isnan(b)
                    taken = FJUMPS[d["jump"]](unordered or a == b, unordered or a < b)
                else:
                    a, b = (self.number(v, s.lineno) for v in d["values"])
                    taken = JUMPS[d["jump"]](a, b)
                if taken:                             # jump taken -> skip the block
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
            elif isinstance(value, TextBuf):          # so is text (it's a char* in C)
                new_vars[name] = [value, "char", self.step]
            elif isinstance(value, float):            # decimals keep their own type
                new_vars[name] = [value, "float32" if isinstance(value, F32) else "float64", self.step]
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

    def libc(self, s):
        d, line = s.data, s.lineno
        a = d["args"]
        name = d["name"]
        if name == "strlen":
            self.assign(d["into"], len(self.c_string(self.get_text(a[0], line))), line)
        elif name == "strcat":
            dest = self.get_text(a[0], line)
            joined = self.c_string(dest) + self.c_string(self.get_text(a[1], line)) + "\0"
            self.write_text(a[0][1], list(joined), line)
        elif name == "strcmp":
            x, y = (self.c_string(self.get_text(v, line)) for v in a)
            self.assign(d["into"], (x > y) - (x < y), line)
        elif name == "atoi":
            m = re.match(r"\s*([+-]?\d+)", self.c_string(self.get_text(a[0], line)))
            self.assign(d["into"], int(m[1]) if m else 0, line)   # like C: not a number? 0.
        elif name == "itoa":
            self.assign(d["into"], CText(str(self.number(a[0], line)) + "\0"), line)
        elif name == "charat":
            buf = self.get_text(a[0], line)
            i = self.number(a[1], line)
            if not 0 <= i < buf.size:
                raise Punishment(line, f"Segmentation fault (core dumped). Byte {i} is outside "
                                 f"the text's {buf.size} bytes.")
            self.assign(d["into"], ord(buf.data[i]), line)

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
        q = q if (a < 0) == (b < 0) else -q
        return q if op == "div" else a - b * q       # mod: the remainder keeps a's sign

    def open_file(self, s):
        """The real open syscall: a file descriptor, or a negative error number."""
        d = s.data
        path = self.c_string(self.get_text(d["path"], s.lineno))
        flags, mode = d["flags"], d["mode"]
        access = flags & 3
        if access == 3 or flags & ~(3 | O_CREAT | O_TRUNC | O_APPEND) or mode > 0o7777:
            return -EINVAL
        real_flags = [os.O_RDONLY, os.O_WRONLY, os.O_RDWR][access]
        for linux, python in ((O_CREAT, os.O_CREAT), (O_TRUNC, os.O_TRUNC), (O_APPEND, os.O_APPEND)):
            if flags & linux:
                real_flags |= python
        try:
            real = os.open(path, real_flags, mode)
        except OSError as e:
            return -(e.errno or EINVAL)
        fd = 3                                        # like Linux: the lowest free number
        while fd in self.files:
            fd += 1
        self.files[fd] = real
        return fd

    def write(self, s):
        text = self.build_text(s)
        fd = self.number(s.data["fd"], s.lineno)
        if fd in (1, 2):
            stream = self.out if fd == 1 else sys.stderr
            stream.write(text)
            stream.flush()
            return len(text.encode())
        if fd not in self.files:
            return -EBADF
        try:
            return os.write(self.files[fd], text.encode())
        except OSError as e:
            return -(e.errno or EBADF)

    def read(self, s):
        """Like the real read syscall: take at most MAXLEN characters, leave the rest waiting."""
        fd = self.number(s.data["fd"], s.lineno)
        if fd != 0:
            if fd not in self.files:
                raise Punishment(s.lineno, f"read from file descriptor {fd}: Bad file descriptor. "
                                 "Did open give you a negative number?")
            try:
                data = os.read(self.files[fd], s.data["maxlen"]).decode("utf-8", errors="replace")
            except OSError as e:
                raise Punishment(s.lineno, f"read from file descriptor {fd} failed: {e.strerror}.")
            if not data and self.touch(s.data["into"], s.lineno)[1] != "char":
                raise Punishment(s.lineno, "read got nothing: the end of the file. That's not a number.")
            return data                               # files keep their newlines
        if not self.pending:
            self.out.flush()
            self.pending = self.inp.readline()
            if not self.pending:
                raise Punishment(s.lineno, "there is no input left to read. The user gave up.")
        chunk = self.pending[:s.data["maxlen"]]
        self.pending = self.pending[s.data["maxlen"]:]
        typed = chunk.rstrip("\n")
        if not typed and self.touch(s.data["into"], s.lineno)[1] != "char":
            raise Punishment(s.lineno, f"read got {chunk!r}, which is not a number. "
                             "(Leftover characters from an earlier read count too.)")
        return typed

    def print(self, s):
        print(self.build_text(s), file=self.out, flush=True)

    def build_text(self, s):
        text = ""
        for kind, x in s.data["parts"]:
            if kind == "text":
                text += x
            elif kind == "space":
                if x != len(text):
                    raise Punishment(s.lineno, f"this space is at position {len(text)}, "
                                     f"but you wrote [{x}word].")
                text += " "
            elif kind == "line":
                if x != len(text):
                    raise Punishment(s.lineno, f"this new line is at position {len(text)}, "
                                     f"but you wrote [{x}line].")
                text += "\n"
            else:
                value = self.touch(x, s.lineno)[0]
                if isinstance(value, PMEHList):
                    raise Punishment(s.lineno, f"${x} is a list. Print its items one at a time.")
                if isinstance(value, TextBuf):
                    text += self.c_string(value)
                elif isinstance(value, float):
                    text += show_decimal(value)
                else:
                    text += str(value)
        if s.data["length"] != len(text):
            raise Punishment(s.lineno, f"mov rdx, {s.data['length']} but the text {text!r} "
                             f"is {len(text)} characters long.")
        return text


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
    if re.fullmatch(r"-?\d+\.\d+", arg):
        if any(d in BANNED_DIGITS for d in arg):
            return f"{arg} can't be written directly (it has a 3, 4 or 7). Build it with math."
        written = ("-" if arg.startswith("-") else "") + "".join(FLIP.get(c, c) for c in reversed(arg.lstrip("-")))
        return f"To get {arg}, write {written}"
    if re.fullmatch(r"-?\d+", arg):
        if any(d in BANNED_DIGITS for d in arg.lstrip("-")):
            return f"{arg} can't be written directly (it has a 3, 4 or 7). Build it with math."
        written = ("-" if arg.startswith("-") else "") + "".join(FLIP[d] for d in reversed(arg.lstrip("-")))
        return f"To get {int(arg)}, write {written}"
    return f"'{arg}' in binary: " + " ".join(f"{ord(c):08b}" for c in arg)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pmeh",
        description="PunishMeEvenHarder++: the language that is harder than assembly, "
                    "because you have to write the assembly too. The rules are in SYNTAX.md.",
        epilog="examples:\n"
               "  pmeh examples/hello.pmeh          run a program\n"
               "  pmeh -k examples/hello.pmeh       only check it, don't run it\n"
               "  pmeh -c LINE -c LINE              run lines typed in the command\n"
               "  pmeh -m 15 print                  how do I write 15? what is 'print' in binary?\n"
               "  pmeh -g examples/hello.pmeh       readable errors, for cowards\n"
               "  pmeh -o hello examples/hello.pmeh compile to a real program, then run ./hello",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("file", nargs="?", help="the .pmeh program to suffer through")
    source.add_argument("-c", "--command", action="append", metavar="LINE",
                        help="run program lines given here instead of a file "
                             "(repeat -c once per line)")
    source.add_argument("-m", "--mercy", nargs="+", metavar="VALUE",
                        help="beg for help: how to write a number, or a word in binary")
    parser.add_argument("-k", "--check", action="store_true",
                        help="check the program without running it (mistakes that only "
                             "happen while running, like a wrong print length, aren't found)")
    parser.add_argument("-g", "--i-give-up", action="store_true", dest="coward",
                        help="show plain, readable error messages")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="compile to a native Linux x86-64 program called FILE instead of running "
                             "(needs 'as' and 'ld' from binutils)")
    parser.add_argument("-S", "--assembly", action="store_true",
                        help="with -o: write the generated assembly to FILE instead of a program")
    parser.add_argument("-s", "--seed", type=int, metavar="N",
                        help="make random numbers and list garbage the same every run")
    parser.add_argument("-V", "--version", action="version",
                        version=f"PunishMeEvenHarder++ {__version__} (no mercy granted)")
    return parser


def report(p, source, coward, origins=None):
    if coward:
        print(f"\nPUNISHMENT on line {p.lineno}{where(p.lineno, origins)}: {p.message}", file=sys.stderr)
        print("Coward.", file=sys.stderr)
    else:
        print(punishment_report(p, source), file=sys.stderr)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mercy:
        for value in args.mercy:
            print(mercy(value))
        return 0

    if args.command:
        source, name = "\n".join(args.command) + "\n", "-c"
    elif args.file:
        name = args.file
        try:
            with open(args.file, encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            print(f"Can't even open the file ({e}). Great start.", file=sys.stderr)
            return 1
    else:
        parser.error("give me a program file, -c LINE or -m VALUE. I'm not a mind reader.")

    folder = os.path.dirname(os.path.abspath(args.file)) if args.file else os.getcwd()
    try:
        source, origins = glue(source, args.file or "-c", folder)
    except Punishment as p:
        report(p, source, args.coward)
        return 1

    if args.output:
        import pmehc
        try:
            pmehc.build(source, args.output, coward=args.coward, assembly_only=args.assembly,
                        seed=args.seed, origins=origins)
        except Punishment as p:
            report(p, source, args.coward, origins)
            return 1
        except pmehc.CompileError as e:
            print(f"pmeh: {e}", file=sys.stderr)
            return 1
        return 0
    if args.assembly:
        parser.error("-S only works together with -o FILE.")

    try:
        program = parse(source)
        if args.check:
            print(f"{name}: no punishments found. Yet.")
            return 0
        rng = random.Random(args.seed) if args.seed is not None else None
        Machine(program, sys.stdout, rng=rng).run()
    except Punishment as p:
        sys.stdout.flush()
        report(p, source, args.coward, origins)
        return 1
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
