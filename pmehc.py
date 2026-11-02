"""The PMEH compiler: turns a PMEH program into a real x86-64 Linux executable.

The program is translated to x86-64 assembly (GNU as, Intel syntax), then assembled with `as`
and linked with `ld`. The result is a static program that talks to Linux directly with system
calls. No Python, no C library.

Every PMEH rule still applies in the compiled program: forgetful variables, frozen variables
during calls, overflow checks, exact print lengths, and the scrambled error reports. Error
reports are built while compiling, so each one is baked into the executable.

Not supported yet (the interpreter runs them fine): lists, text, files and C library functions.
"""
import os
import random
import shutil
import subprocess
import tempfile

import pmeh
from pmeh import MAX_DEPTH, MAX_STEPS, MEMORY_SPAN, PARAM_REGISTERS, TYPES, Punishment

UNSUPPORTED = {
    "list": "lists", "store": "lists", "load": "lists",
    "text": "text variables", "libc": "C library functions",
    "open": "files", "close": "files",
}
SLOT = 32            # bytes per variable: value, last step touched, state, bits
VALUE, LAST, STATE, BITS = 0, 8, 16, 24
ALIVE = 1


class CompileError(Exception):
    pass


# ---------------------------------------------------------------- runtime (hand-written asm)

RUNTIME = r"""
# ---- runtime ------------------------------------------------------------

# punish: rsi = report, rdx = length. Write it to stderr and exit with status 1.
punish:
    mov rax, 1
    mov rdi, 2
    syscall
    mov rax, 60
    mov rdi, 1
    syscall

# append: copy rcx bytes from rsi to the end of outbuf.
append:
    lea rdi, [rip + outbuf]
    add rdi, qword ptr [rip + outlen]
    add qword ptr [rip + outlen], rcx
    rep movsb
    ret

# append_char: add the byte in al to outbuf.
append_char:
    lea rdi, [rip + outbuf]
    add rdi, qword ptr [rip + outlen]
    mov byte ptr [rdi], al
    inc qword ptr [rip + outlen]
    ret

# append_int: add the signed number in rax to outbuf, in decimal.
append_int:
    mov rcx, rax
    test rax, rax
    jns 1f
    mov al, '-'
    push rcx
    call append_char
    pop rcx
    neg rcx                     # as an unsigned number, this works even for the smallest int64
1:  lea rsi, [rip + numbuf + 24]
    mov rax, rcx
    mov r8, 10
2:  xor edx, edx
    div r8
    add dl, '0'
    dec rsi
    mov byte ptr [rsi], dl
    test rax, rax
    jnz 2b
    lea rcx, [rip + numbuf + 24]
    sub rcx, rsi
    jmp append

# fill_pending: if nothing typed is waiting, read one line from the keyboard (like readline).
fill_pending:
    cmp qword ptr [rip + pend_len], 0
    jne 3f
    mov qword ptr [rip + pend_start], 0
1:  cmp qword ptr [rip + pend_len], 65536
    jge 3f
    mov rax, 0
    mov rdi, 0
    lea rsi, [rip + pend]
    add rsi, qword ptr [rip + pend_len]
    mov rdx, 1
    syscall
    cmp rax, 1
    jne 3f
    lea rsi, [rip + pend]
    add rsi, qword ptr [rip + pend_len]
    inc qword ptr [rip + pend_len]
    cmp byte ptr [rsi], 10
    jne 1b
3:  ret

# take_chunk: rdi = MAXLEN. Move up to MAXLEN waiting characters into chunk.
# Returns the length in rax (trailing newlines removed), or -1 if there is no input left.
take_chunk:
    push rdi
    call fill_pending
    pop rcx
    cmp qword ptr [rip + pend_len], 0
    jne 1f
    mov rax, -1
    ret
1:  cmp rcx, qword ptr [rip + pend_len]
    jle 2f
    mov rcx, qword ptr [rip + pend_len]
2:  lea rsi, [rip + pend]
    add rsi, qword ptr [rip + pend_start]
    add qword ptr [rip + pend_start], rcx
    sub qword ptr [rip + pend_len], rcx
    lea rdi, [rip + chunk]
    mov rax, rcx
    rep movsb
3:  test rax, rax
    jz 4f
    lea rsi, [rip + chunk]
    cmp byte ptr [rsi + rax - 1], 10
    jne 4f
    dec rax
    jmp 3b
4:  ret

# parse_int: rcx = length of chunk. Returns the number in rax and a status in rdx:
# 0 = fine, 1 = not a number, 2 = too big for 64 bits.
parse_int:
    lea rsi, [rip + chunk]
    xor r9, r9                  # 1 if negative
    test rcx, rcx
    jz 8f
    cmp byte ptr [rsi], '-'
    jne 1f
    mov r9, 1
    inc rsi
    dec rcx
    jz 8f
1:  xor rax, rax                # build the number as a negative value, so -2^63 fits
2:  movzx r8, byte ptr [rsi]
    sub r8, '0'
    cmp r8, 9
    ja 8f
    imul rax, rax, 10
    jo 9f
    sub rax, r8
    jo 9f
    inc rsi
    dec rcx
    jnz 2b
    test r9, r9
    jnz 3f
    neg rax
    jo 9f
3:  xor edx, edx
    ret
8:  mov rdx, 1
    ret
9:  mov rdx, 2
    ret
"""


# ---------------------------------------------------------------- compiler

class Compiler:
    def __init__(self, program, source, coward=False, rng=None):
        self.program = program
        self.source = source
        self.coward = coward
        self.rng = rng or random.Random()
        self.out = []
        self.errors = {}        # (lineno, message) -> label
        self.strings = {}       # text -> label
        self.label_count = 0
        self.outbuf_size = 64
        self.scope_of = []      # statement index -> def index, or None for the main program
        self.slots = {}         # scope -> {variable name: slot number}

    # -- small helpers

    def emit(self, line):
        self.out.append("    " + line if not line.endswith(":") else line)

    def new_label(self):
        self.label_count += 1
        return f"T{self.label_count}"

    def error(self, lineno, message):
        key = (lineno, message)
        if key not in self.errors:
            self.errors[key] = f"E{len(self.errors)}"
        return self.errors[key]

    def string(self, text):
        if text not in self.strings:
            self.strings[text] = f"S{len(self.strings)}"
        return self.strings[text]

    def slot(self, scope, name, field):
        offset = SLOT * (self.slots[scope][name] + 1) - field
        return f"qword ptr [rbp - {offset}]"

    def label(self, index):
        return f"L{index}"

    # -- analysis

    def analyse(self):
        scope = None
        end = None
        for i, s in enumerate(self.program):
            if s.kind in UNSUPPORTED:
                raise CompileError(f"line {s.lineno}: the compiler can't do {UNSUPPORTED[s.kind]} yet. "
                                   "Run this program with the interpreter instead.")
            if s.kind == "def":
                self.scope_of.append(None)        # the def line itself runs in the main program
                scope, end = i, s.data["end"]
                self.slots[i] = {}
                for p in s.data["params"]:
                    self.add_name(i, p)
                continue
            self.scope_of.append(scope)
            if i == end:
                scope = end = None
        self.slots.setdefault(None, {})
        for i, s in enumerate(self.program):
            for name in self.names_in(s):
                self.add_name(self.scope_of[i], name)

    def add_name(self, scope, name):
        names = self.slots.setdefault(scope, {})
        names.setdefault(name, len(names))

    def names_in(self, s):
        d = s.data
        names = []
        for key in ("values", "args"):
            names += [x for kind, x in d.get(key, []) if kind == "var"]
        for key in ("value", "fd"):
            if key in d and d[key][0] == "var":
                names.append(d[key][1])
        names += [x for kind, x in d.get("parts", []) if kind == "var"]
        if d.get("into"):
            names.append(d["into"])
        if s.kind == "var":
            names.append(d["name"])
        return names

    # -- code for values

    def touch(self, scope, name, lineno):
        """Check that a variable exists and hasn't been forgotten, then mark it as used."""
        self.emit(f"cmp {self.slot(scope, name, STATE)}, {ALIVE}")
        self.emit(f"jne {self.error(lineno, f'${name} never existed. Declare it with <<startvar<STACK>>.')}")
        self.emit("mov rax, qword ptr [rip + step]")
        self.emit(f"sub rax, {self.slot(scope, name, LAST)}")
        self.emit("dec rax")
        self.emit(f"cmp rax, {MEMORY_SPAN}")
        self.emit(f"jge {self.error(lineno, f'${name} was ignored for {MEMORY_SPAN} or more lines and forgot itself. Use every variable at least once every {MEMORY_SPAN} lines.')}")
        self.emit("mov rax, qword ptr [rip + step]")
        self.emit(f"mov {self.slot(scope, name, LAST)}, rax")

    def value(self, scope, v, lineno):
        """Put a value in rax."""
        kind, x = v
        if kind == "num":
            self.emit(f"mov rax, {x}")
        else:
            self.touch(scope, x, lineno)
            self.emit(f"mov rax, {self.slot(scope, x, VALUE)}")

    def two_values(self, scope, values, lineno):
        """First value in rax, second in rbx (evaluated in that order)."""
        self.value(scope, values[0], lineno)
        self.emit("push rax")
        self.value(scope, values[1], lineno)
        self.emit("mov rbx, rax")
        self.emit("pop rax")

    def check_bits(self, bits_operand, lineno, what):
        """Overflow check: does rax fit in a signed number of `bits_operand` bits?"""
        self.emit("mov ecx, 64")
        self.emit(f"sub rcx, {bits_operand}")
        self.emit("mov rdx, rax")
        self.emit("shl rdx, cl")
        self.emit("sar rdx, cl")
        self.emit("cmp rdx, rax")
        self.emit(f"jne {self.error(lineno, f'the value is too big for {what}. Overflow.')}")

    def assign(self, scope, name, lineno):
        """Store rax in a variable: it must be alive, and the value must fit its type."""
        self.emit("push rax")
        self.touch(scope, name, lineno)
        self.emit("pop rax")
        self.check_bits(self.slot(scope, name, BITS), lineno, f"${name}")
        self.emit(f"mov {self.slot(scope, name, VALUE)}, rax")

    # -- code for statements

    def statement(self, i, s):
        scope = self.scope_of[i] if s.kind != "enddefine" else self.function_of(i)
        d, line = s.data, s.lineno
        self.emit(f"{self.label(i)}:")
        self.emit("inc qword ptr [rip + step]")
        self.emit(f"cmp qword ptr [rip + step], {MAX_STEPS}")
        self.emit(f"jg {self.error(line, 'infinite suffering detected. Even I have limits.')}")

        if s.kind == "var":
            self.value(scope, d["value"], line)
            self.check_bits(TYPES[d["type"]], line, d["type"])
            self.emit(f"mov {self.slot(scope, d['name'], VALUE)}, rax")
            self.emit(f"mov {self.slot(scope, d['name'], BITS)}, {TYPES[d['type']]}")
            self.emit(f"mov {self.slot(scope, d['name'], STATE)}, {ALIVE}")
            self.emit("mov rax, qword ptr [rip + step]")
            self.emit(f"mov {self.slot(scope, d['name'], LAST)}, rax")

        elif s.kind == "print":
            self.build_text(scope, s)
            self.emit("mov al, 10")
            self.emit("call append_char")
            self.syscall_write(1)

        elif s.kind == "write":
            self.build_text(scope, s)
            self.value(scope, d["fd"], line)
            ok, done = self.new_label(), self.new_label()
            self.emit("cmp rax, 1")
            self.emit(f"je {ok}")
            self.emit("cmp rax, 2")
            self.emit(f"je {ok}")
            self.emit("mov rax, -9")              # EBADF: the compiler has no open files
            self.emit(f"jmp {done}")
            self.emit(f"{ok}:")
            self.emit("mov rdi, rax")
            self.emit("mov rax, 1")
            self.emit("lea rsi, [rip + outbuf]")
            self.emit("mov rdx, qword ptr [rip + outlen]")
            self.emit("syscall")
            self.emit(f"{done}:")
            self.assign(scope, d["into"], line)

        elif s.kind == "read":
            self.read(scope, s)

        elif s.kind == "rand":
            self.rand(scope, s)

        elif s.kind == "math":
            self.two_values(scope, d["values"], line)
            self.math(d["op"], line)
            self.assign(scope, d["into"], line)

        elif s.kind in ("if", "loop"):
            self.two_values(scope, d["values"], line)
            self.emit("cmp rax, rbx")
            if s.kind == "loop":
                target = d["end"] + 1
            elif d["else"] is not None:
                target = d["else"] + 1
            else:
                target = d["end"]
            self.emit(f"{d['jump']} {self.label(target)}")   # the PMEH jump is the real jump

        elif s.kind == "else":
            self.emit(f"jmp {self.label(d['end'])}")

        elif s.kind == "endloop":
            self.emit(f"jmp {self.label(d['start'])}")

        elif s.kind == "def":
            self.emit(f"jmp {self.label(d['end'] + 1)}")

        elif s.kind == "call":
            self.call(scope, s)

        elif s.kind == "return":
            self.value(scope, d["value"], line)
            self.emit("leave")
            self.emit("ret")

        elif s.kind == "enddefine":
            name = self.program[scope].data["name"]
            self.emit(f"jmp {self.error(line, f'{name} ended without returning anything.')}")

        # endif and comment: nothing, but the line still counts

    def function_of(self, i):
        for j in range(i, -1, -1):
            if self.program[j].kind == "def":
                return j
        raise AssertionError("enddefine without def")

    def build_text(self, scope, s):
        d, line = s.data, s.lineno
        self.emit("mov qword ptr [rip + outlen], 0")
        size = 1
        for kind, x in d["parts"]:
            if kind == "text":
                size += len(x.encode())
                if x:
                    self.emit(f"lea rsi, [rip + {self.string(x)}]")
                    self.emit(f"mov rcx, {len(x.encode())}")
                    self.emit("call append")
            elif kind in ("space", "line"):
                size += 1
                word, token = ("space", "word") if kind == "space" else ("new line", "line")
                message = f"this {word} is not at position {x}, but you wrote [{x}{token}]."
                self.emit(f"cmp qword ptr [rip + outlen], {x}")
                self.emit(f"jne {self.error(line, message)}")
                self.emit(f"mov al, {32 if kind == 'space' else 10}")
                self.emit("call append_char")
            else:
                size += 20
                self.touch(scope, x, line)
                self.emit(f"mov rax, {self.slot(scope, x, VALUE)}")
                self.emit("call append_int")
        self.outbuf_size = max(self.outbuf_size, size)
        length = d["length"]
        message = f"mov rdx, {length} but the text is a different length."
        self.emit(f"cmp qword ptr [rip + outlen], {length}")
        self.emit(f"jne {self.error(line, message)}")

    def syscall_write(self, fd):
        self.emit("mov rax, 1")
        self.emit(f"mov rdi, {fd}")
        self.emit("lea rsi, [rip + outbuf]")
        self.emit("mov rdx, qword ptr [rip + outlen]")
        self.emit("syscall")

    def math(self, op, line):
        overflow = self.error(line, "the result is too big for 64 bits. Overflow.")
        if op in ("add", "sub"):
            self.emit(f"{op} rax, rbx")
            self.emit(f"jo {overflow}")
        elif op == "mul":
            self.emit("imul rax, rbx")
            self.emit(f"jo {overflow}")
        else:
            self.emit("test rbx, rbx")
            self.emit(f"jz {self.error(line, 'division by zero. The CPU would have crashed too.')}")
            normal, done = self.new_label(), self.new_label()
            self.emit("cmp rbx, -1")                     # the one division idiv can't do
            self.emit(f"jne {normal}")
            self.emit("mov rcx, 0x8000000000000000")
            self.emit("cmp rax, rcx")
            self.emit(f"jne {normal}")
            if op == "div":
                self.emit(f"jmp {overflow}")
            else:
                self.emit("xor eax, eax")
                self.emit(f"jmp {done}")
            self.emit(f"{normal}:")
            self.emit(pmeh.MATH_ASM[op])                 # the asm you wrote: cqo; idiv rbx (; mov rax, rdx)
            self.emit(f"{done}:")

    def read(self, scope, s):
        d, line = s.data, s.lineno
        self.value(scope, d["fd"], line)
        self.emit("test rax, rax")
        self.emit(f"jnz {self.error(line, 'read from a file descriptor that is not open: Bad file descriptor.')}")
        self.emit(f"mov rdi, {d['maxlen']}")
        self.emit("call take_chunk")
        self.emit("cmp rax, -1")
        self.emit(f"je {self.error(line, 'there is no input left to read. The user gave up.')}")
        typed = self.new_label()
        self.emit("test rax, rax")
        self.emit(f"jnz {typed}")
        self.touch(scope, d["into"], line)
        self.emit(f"jmp {self.error(line, 'read got nothing but Enter, which is not a number. (Leftover characters from an earlier read count too.)')}")
        self.emit(f"{typed}:")
        self.emit("push rax")
        self.touch(scope, d["into"], line)
        self.emit("pop rcx")
        self.emit("call parse_int")
        self.emit("cmp rdx, 1")
        self.emit(f"je {self.error(line, 'the user typed something that is not a number. Even the user is bad at this.')}")
        self.emit("cmp rdx, 2")
        message = "the number typed is too big for $" + d["into"] + ". Overflow."
        self.emit(f"je {self.error(line, message)}")
        self.assign(scope, d["into"], line)

    def rand(self, scope, s):
        d, line = s.data, s.lineno
        size = d["bytes"]
        self.touch(scope, d["into"], line)
        self.emit(f"mov rax, {size * 8}")
        self.emit(f"cmp rax, {self.slot(scope, d['into'], BITS)}")
        message = f"you asked getrandom for {size} bytes, but $" + d["into"] + " is a different size."
        self.emit(f"jne {self.error(line, message)}")
        self.emit("mov rax, 318")                        # the asm you wrote: getrandom
        self.emit("lea rdi, [rip + randbuf]")
        self.emit(f"mov rsi, {size}")
        self.emit("mov rdx, 0")
        self.emit("syscall")
        load = {1: "movsx rax, byte ptr", 2: "movsx rax, word ptr", 4: "movsxd rax, dword ptr",
                8: "mov rax, qword ptr"}.get(size, "mov rax, qword ptr")
        self.emit(f"{load} [rip + randbuf]")
        self.assign(scope, d["into"], line)

    def call(self, scope, s):
        d, line = s.data, s.lineno
        caller_slots = self.slots[scope]
        self.emit(f"cmp qword ptr [rip + depth], {MAX_DEPTH}")
        self.emit(f"jge {self.error(line, f'stack overflow: more than {MAX_DEPTH} calls deep. You recursed into the abyss.')}")
        for v in d["args"]:
            self.value(scope, v, line)
            self.emit("push rax")
        self.touch(scope, d["into"], line)
        for register in reversed(PARAM_REGISTERS[:len(d["args"])]):
            self.emit(f"pop {register}")
        self.emit("push qword ptr [rip + step]")
        self.emit("inc qword ptr [rip + depth]")
        self.emit(f"call F{d['target']}")
        self.emit("dec qword ptr [rip + depth]")
        # the caller's variables were frozen: move their "last used" forward by the time spent
        self.emit("pop rcx")
        self.emit("mov rdx, qword ptr [rip + step]")
        self.emit("sub rdx, rcx")
        for name in caller_slots:
            self.emit(f"add {self.slot(scope, name, LAST)}, rdx")
        self.assign(scope, d["into"], line)

    def frame(self, scope):
        """Make room for the variables and mark them all as never existed (state 0).
        Only r10 and r11 are used, so arguments in rdi, rsi, rdx, rcx, r8, r9 survive."""
        size = SLOT * len(self.slots[scope])
        size += -size % 16
        if size:
            self.emit(f"sub rsp, {size}")
            self.emit("mov r10, rsp")
            self.emit(f"mov r11, {size // 8}")
            loop = self.new_label()
            self.emit(f"{loop}:")
            self.emit("mov qword ptr [r10], 0")
            self.emit("add r10, 8")
            self.emit("dec r11")
            self.emit(f"jnz {loop}")

    def function(self, index):
        d = self.program[index].data
        self.emit(f"F{index}:")
        self.emit("push rbp")
        self.emit("mov rbp, rsp")
        self.frame(index)
        for name, register in zip(d["params"], PARAM_REGISTERS):
            self.emit(f"mov {self.slot(index, name, VALUE)}, {register}")
            self.emit(f"mov {self.slot(index, name, BITS)}, 64")
            self.emit(f"mov {self.slot(index, name, STATE)}, {ALIVE}")
            self.emit("mov rax, qword ptr [rip + step]")
            self.emit(f"mov {self.slot(index, name, LAST)}, rax")
        self.emit(f"jmp {self.label(index + 1)}")

    def compile(self):
        self.analyse()
        self.emit(".intel_syntax noprefix")
        self.emit(".text")
        self.emit(".globl _start")
        self.emit("_start:")
        self.emit("mov rbp, rsp")
        self.frame(None)
        for i, s in enumerate(self.program):
            self.statement(i, s)
        self.emit(f"{self.label(len(self.program))}:")
        self.emit("mov rax, 60")
        self.emit("xor edi, edi")
        self.emit("syscall")
        for i, s in enumerate(self.program):
            if s.kind == "def":
                self.function(i)
        self.out.append(RUNTIME)
        # every error report, scrambled at compile time
        for (lineno, message), label in self.errors.items():
            p = Punishment(lineno, message)
            if self.coward:
                report = f"\nPUNISHMENT on line {lineno}: {message}\nCoward.\n"
            else:
                report = pmeh.punishment_report(p, self.source, self.rng) + "\n"
            data = report.encode()
            self.emit(f"{label}:")
            self.emit(f"lea rsi, [rip + {label}_text]")
            self.emit(f"mov rdx, {len(data)}")
            self.emit("jmp punish")
            self.strings_bytes(f"{label}_text", data)
        self.emit(".section .rodata")
        for text, label in self.strings.items():
            self.strings_bytes(label, text.encode())
        self.emit(".bss")
        for name, size in (("step", 8), ("depth", 8), ("outlen", 8), ("pend_start", 8), ("pend_len", 8),
                           ("outbuf", self.outbuf_size), ("numbuf", 32), ("pend", 65536),
                           ("chunk", 65536), ("randbuf", 16)):
            self.emit(f"{name}: .zero {size}")
        return "\n".join(self.out) + "\n"

    def strings_bytes(self, label, data):
        self.out.append(f"{label}:")
        for i in range(0, len(data), 32):
            self.out.append("    .byte " + ", ".join(str(b) for b in data[i:i + 32]))


def compile_to_asm(source, coward=False, seed=None):
    program = pmeh.parse(source)
    rng = random.Random(seed) if seed is not None else None
    return Compiler(program, source, coward, rng).compile()


def build(source, output, coward=False, assembly_only=False, seed=None):
    """Compile PMEH source to an executable (or to an assembly file)."""
    asm = compile_to_asm(source, coward, seed)
    if assembly_only:
        with open(output, "w") as f:
            f.write(asm)
        return
    for tool in ("as", "ld"):
        if not shutil.which(tool):
            raise CompileError(f"'{tool}' is not installed. It comes with binutils "
                               "(on Arch: sudo pacman -S binutils).")
    with tempfile.TemporaryDirectory() as tmp:
        asm_path = os.path.join(tmp, "program.s")
        obj_path = os.path.join(tmp, "program.o")
        with open(asm_path, "w") as f:
            f.write(asm)
        for command in (["as", "--64", "-o", obj_path, asm_path], ["ld", "-o", output, obj_path]):
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise CompileError(f"{command[0]} failed. This is a compiler bug, not your fault "
                                   f"(for once):\n{result.stderr}")
