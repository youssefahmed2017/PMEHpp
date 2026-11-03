"""The PMEH compiler: turns a PMEH program into a real x86-64 Linux executable.

The program is translated to x86-64 assembly (GNU as, Intel syntax), then assembled with `as`
and linked with `ld`. The result is a static program that talks to Linux directly with system
calls. No Python, no C library.

Every PMEH rule still applies in the compiled program: forgetful variables, frozen variables
during calls, overflow checks, exact print lengths, garbage in lists and text, buffer overflows
that corrupt the variables declared after them, and the scrambled error reports. Error reports
are built while compiling, so each one is baked into the executable.

How values are stored
---------------------
Every variable has a 48-byte slot in its function's stack frame:

    value   the number, the 64 bits of a decimal, or a pointer (garbage, list or text)
    last    the step it was last used (for forgetting)
    state   0 = never existed, 1 = alive
    type    8/16/32/64 for numbers, LIST, CHAR, F64T or F32T
    tag     what the value is: INT, GARBAGE, LIST, TEXT, FLOAT64 or FLOAT32
    order   when it was declared in this frame (buffer overflows spill in this order)

Decimals are kept as float64 bits; a float32 value is a float64 that float32 can hold exactly,
and ...ss instructions convert to float32 and back around the real instruction.

Lists, text buffers and garbage live on a heap that grows with the brk system call.
While compiled code works with a value, the value is in rax and its tag in r14.
"""
import os
import random
import struct
import shutil
import subprocess
import tempfile

import pmeh
from pmeh import (CVT, FLOAT_MATH, FLOATS, GARBAGE_JUNK, MAX_DEPTH, MAX_LIST_BYTES, MAX_STEPS, MEMORY_SPAN,
                  O_APPEND, O_CREAT, O_TRUNC, PARAM_REGISTERS, TYPES, Punishment, round32, where)

SLOT, HEADER = 48, 16          # bytes per variable; frame header holds the next declaration number
VALUE, LAST, STATE, BITS, TAG, ORDER = 0, 8, 16, 24, 32, 40
ALIVE = 1
LIST, CHAR, F64T, F32T = 1, 2, 3, 4   # variable types besides 8/16/32/64
INT, GARBAGE, LISTREF, TEXTREF, FLOAT64, FLOAT32 = 0, 1, 2, 3, 4, 5   # what a value is
FLOAT_TYPE = {"float64": F64T, "float32": F32T}
FLOAT_TAG = {64: FLOAT64, 32: FLOAT32}


def bits_of(x):
    """The 64 bits of a decimal, as a signed number for `mov rax, ...`."""
    return struct.unpack("<q", struct.pack("<d", x))[0]
OPEN_FILES = 1024


class CompileError(Exception):
    pass


# ---------------------------------------------------------------- runtime (hand-written asm)

RUNTIME = r"""
# ---- runtime ------------------------------------------------------------
# Routines may change rax, rbx, rcx, rdx, rsi, rdi, r8-r11 unless they say otherwise.
# They never change r12-r15 or rbp.

# punish: rsi = report, rdx = length. Write it to stderr and exit with status 1.
punish:
    mov rax, 1
    mov rdi, 2
    syscall
    mov rax, 60
    mov rdi, 1
    syscall

# alloc: rdi = bytes. Returns a 16-byte aligned pointer in rax. Grows the heap with brk.
# Only changes rax, rcx, rdx, rdi, r11.
alloc:
    add rdi, 15
    and rdi, -16
    mov rax, qword ptr [rip + heap_cur]
    lea rdx, [rax + rdi]
    cmp rdx, qword ptr [rip + heap_end]
    jbe 2f
    push rax
    push rdx
    lea rdi, [rdx + 16777216]
    mov rax, 12
    syscall
    pop rdx
    lea rdi, [rdx + 16777216]
    cmp rax, rdi
    jb out_of_memory
    mov qword ptr [rip + heap_end], rax
    pop rax
2:  mov qword ptr [rip + heap_cur], rdx
    ret

# out_reserve: make room for rcx more bytes of output. Keeps rcx and rsi.
out_reserve:
    mov rax, qword ptr [rip + outlen]
    add rax, rcx
    cmp rax, qword ptr [rip + outcap]
    jbe 9f
    push rsi
    push rcx
    shl rax, 1
    add rax, 64
    mov qword ptr [rip + outcap], rax
    mov rdi, rax
    call alloc
    mov rdi, rax
    mov rsi, qword ptr [rip + outptr]
    mov rcx, qword ptr [rip + outlen]
    mov qword ptr [rip + outptr], rax
    rep movsb
    pop rcx
    pop rsi
9:  ret

# append: copy rcx bytes from rsi to the output.
append:
    call out_reserve
    mov rdi, qword ptr [rip + outptr]
    add rdi, qword ptr [rip + outlen]
    add qword ptr [rip + outlen], rcx
    rep movsb
    ret

# append_char: add the byte in al to the output.
append_char:
    push rax
    mov rcx, 1
    call out_reserve
    pop rax
    mov rdi, qword ptr [rip + outptr]
    add rdi, qword ptr [rip + outlen]
    mov byte ptr [rdi], al
    inc qword ptr [rip + outlen]
    ret

# append_int: add the signed number in rax to the output, in decimal.
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

# parse_int: rsi = text, rcx = length. The whole text must be -?[0-9]+.
# Returns the number in rax and a status in rdx: 0 = fine, 1 = not a number, 2 = too big for 64 bits.
parse_int:
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

# atoi_parse: rsi = text, rcx = length. Like C atoi: spaces, a sign, digits; nothing -> 0.
# Returns rax and rdx: 0 = fine, 2 = too big for 64 bits.
atoi_parse:
1:  test rcx, rcx
    jz 7f
    movzx r8, byte ptr [rsi]
    cmp r8, 32
    je 2f
    cmp r8, 9
    jb 3f
    cmp r8, 13
    ja 3f
2:  inc rsi
    dec rcx
    jmp 1b
3:  xor r9, r9
    cmp r8, '-'
    jne 4f
    mov r9, 1
    jmp 5f
4:  cmp r8, '+'
    jne 6f
5:  inc rsi
    dec rcx
6:  xor rax, rax
    xor r10, r10                # how many digits
61: test rcx, rcx
    jz 63f
    movzx r8, byte ptr [rsi]
    sub r8, '0'
    cmp r8, 9
    ja 63f
    imul rax, rax, 10
    jo 9f
    sub rax, r8
    jo 9f
    inc r10
    inc rsi
    dec rcx
    jmp 61b
63: test r10, r10
    jz 7f
    test r9, r9
    jnz 64f
    neg rax
    jo 9f
64: xor edx, edx
    ret
7:  xor eax, eax
    xor edx, edx
    ret
9:  mov rdx, 2
    ret

# rng_next: a random 64-bit number in rax (xorshift64*). Changes rdx.
rng_next:
    mov rax, qword ptr [rip + rng_state]
    mov rdx, rax
    shr rdx, 12
    xor rax, rdx
    mov rdx, rax
    shl rdx, 25
    xor rax, rdx
    mov rdx, rax
    shr rdx, 27
    xor rax, rdx
    mov qword ptr [rip + rng_state], rax
    mov rdx, 0x2545F4914F6CDD1D
    imul rax, rdx
    ret

# rng_below: rdi = n. A random number from 0 to n-1 in rax.
rng_below:
    push rdi
    call rng_next
    pop rdi
    xor edx, edx
    div rdi
    mov rax, rdx
    ret

# junk_word: a random piece of junk ("banana", "help me"...): rsi = text, rcx = length.
junk_word:
    mov rdi, JUNK_COUNT
    call rng_below
    shl rax, 4
    lea rdx, [rip + junk_table]
    mov rsi, qword ptr [rdx + rax]
    mov rcx, qword ptr [rdx + rax + 8]
    ret

# fill_garbage: fill rcx bytes at rdi with junk words glued together.
fill_garbage:
    push r12
    push r13
    mov r12, rdi
    mov r13, rcx
1:  test r13, r13
    jz 2f
    call junk_word
    cmp rcx, r13
    jbe 3f
    mov rcx, r13
3:  mov rdi, r12
    add r12, rcx
    sub r13, rcx
    rep movsb
    jmp 1b
2:  pop r13
    pop r12
    ret

# make_garbage: copy rcx bytes from rsi into a new garbage value [length][bytes]. Pointer in rax.
make_garbage:
    push rsi
    push rcx
    lea rdi, [rcx + 8]
    call alloc
    pop rcx
    pop rsi
    mov qword ptr [rax], rcx
    lea rdi, [rax + 8]
    rep movsb
    ret

# garbage_item: rdi = bits. Half the time junk, half the time a random number that fits.
# The value in rax, its tag in rdx.
garbage_item:
    push r12
    mov r12, rdi
    call rng_next
    test rax, 1
    jz 1f
    call junk_word
    call make_garbage
    mov rdx, 1
    pop r12
    ret
1:  call rng_next
    mov ecx, 64
    sub rcx, r12
    shl rax, cl
    sar rax, cl
    xor edx, edx
    pop r12
    ret

# new_list: rdi = bits per item, rsi = number of items. Every item starts as garbage.
# A list is [bits][items][bytes per item] then 16 bytes per item: value, tag.
new_list:
    push r12
    push r13
    push r14
    push r15
    mov r12, rdi
    mov r13, rsi
    mov rdi, r13
    shl rdi, 4
    add rdi, 24
    call alloc
    mov r14, rax
    mov qword ptr [r14], r12
    mov qword ptr [r14 + 8], r13
    mov rax, r12
    shr rax, 3
    mov qword ptr [r14 + 16], rax
    lea r15, [r14 + 24]
1:  test r13, r13
    jz 2f
    mov rdi, r12
    call garbage_item
    mov qword ptr [r15], rax
    mov qword ptr [r15 + 8], rdx
    add r15, 16
    dec r13
    jmp 1b
2:  mov rax, r14
    pop r15
    pop r14
    pop r13
    pop r12
    ret

# new_text: rdi = size in bytes. A text buffer is [size][bytes], and starts full of garbage.
new_text:
    push r12
    mov r12, rdi
    add rdi, 8
    call alloc
    push rax
    mov qword ptr [rax], r12
    lea rdi, [rax + 8]
    mov rcx, r12
    call fill_garbage
    pop rax
    pop r12
    ret

# c_string: rdi = text buffer. Read it the C way, up to the first 0 byte: rsi = text, rcx = length.
# No 0 byte? Keep reading past the end into 3 to 20 bytes of garbage.
c_string:
    push r12
    push r13
    mov r12, rdi
    mov rcx, qword ptr [r12]
    mov rdx, rcx
    lea rdi, [r12 + 8]
    xor eax, eax
    repne scasb
    jne 2f
    lea rsi, [r12 + 8]
    sub rdx, rcx
    dec rdx
    mov rcx, rdx
    pop r13
    pop r12
    ret
2:  mov rdi, 18
    call rng_below
    add rax, 3
    mov r13, rax
    mov rdi, qword ptr [r12]
    add rdi, r13
    call alloc
    push rax
    mov rdi, rax
    lea rsi, [r12 + 8]
    mov rcx, qword ptr [r12]
    rep movsb
    mov rcx, r13
    call fill_garbage
    pop rsi
    mov rcx, qword ptr [r12]
    add rcx, r13
    pop r13
    pop r12
    ret

# with_nul: copy rcx bytes from rsi and add a 0 byte. The copy in rsi, its length in rcx.
with_nul:
    push rsi
    push rcx
    lea rdi, [rcx + 1]
    call alloc
    pop rcx
    pop rsi
    mov rdi, rax
    push rcx
    rep movsb
    pop rcx
    mov byte ptr [rdi], 0
    mov rsi, rax
    inc rcx
    ret

# concat: (rsi, rcx) + (r8, r9) + a 0 byte, in a new copy: rsi = text, rcx = length.
concat:
    push rsi
    push rcx
    push r8
    push r9
    lea rdi, [rcx + r9 + 1]
    call alloc
    pop r9
    pop r8
    pop rcx
    pop rsi
    mov rdi, rax
    mov rdx, rcx
    rep movsb
    mov rsi, r8
    mov rcx, r9
    rep movsb
    mov byte ptr [rdi], 0
    mov rsi, rax
    lea rcx, [rdx + r9 + 1]
    ret

# bytes_cmp: compare (rsi, rcx) with (rdi, rdx). rax = -1, 0 or 1, like strcmp.
bytes_cmp:
    mov r8, rcx
    cmp r8, rdx
    jbe 1f
    mov r8, rdx
1:  test r8, r8
    jz 3f
    mov al, byte ptr [rsi]
    cmp al, byte ptr [rdi]
    jb 4f
    ja 5f
    inc rsi
    inc rdi
    dec r8
    jmp 1b
3:  cmp rcx, rdx
    jb 4f
    ja 5f
    xor eax, eax
    ret
4:  mov rax, -1
    ret
5:  mov eax, 1
    ret

# big_mul: multiply the big number in `big` (32-bit pieces, smallest first) by edi (< 2^31).
big_mul:
    xor ecx, ecx
    xor r8, r8
    lea rsi, [rip + big]
1:  cmp rcx, qword ptr [rip + biglen]
    jge 2f
    mov eax, dword ptr [rsi + rcx*4]
    imul rax, rdi
    add rax, r8
    mov dword ptr [rsi + rcx*4], eax
    shr rax, 32
    mov r8, rax
    inc rcx
    jmp 1b
2:  test r8, r8
    jz 3f
    mov dword ptr [rsi + rcx*4], r8d
    inc qword ptr [rip + biglen]
3:  ret

# big_div: divide the big number by rdi (< 2^32). The remainder in rax.
big_div:
    mov rcx, qword ptr [rip + biglen]
    xor edx, edx
    lea rsi, [rip + big]
1:  test rcx, rcx
    jz 3f
    dec rcx
    mov eax, dword ptr [rsi + rcx*4]
    mov r8, rdx
    shl r8, 32
    or rax, r8
    xor edx, edx
    div rdi
    mov dword ptr [rsi + rcx*4], eax
    jmp 1b
3:  mov rcx, qword ptr [rip + biglen]
    test rcx, rcx
    jz 4f
    cmp dword ptr [rsi + rcx*4 - 4], 0
    jne 4f
    dec qword ptr [rip + biglen]
    jmp 3b
4:  mov rax, rdx
    ret

# append_float: add the decimal whose 64 bits are in rax to the output, exactly like C's %.17g:
# 17 significant digits, correctly rounded (ties to even), trailing zeros removed.
# The value is m * 2^e. It is turned into a whole number N (m * 2^e, or m * 5^-e with -e decimal
# places), all of N's decimal digits are worked out with big-number arithmetic, then rounded.
append_float:
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov r12, rax
    mov r13, rax
    shr r13, 52
    and r13, 0x7FF                      # exponent
    mov r14, 0x000FFFFFFFFFFFFF
    and r14, rax                        # mantissa
    cmp r13, 0x7FF
    jne 10f
    test r14, r14
    jnz 2f
    test r12, r12
    jns 1f
    mov al, '-'
    call append_char
1:  lea rsi, [rip + inf_text]
    mov rcx, 3
    call append
    jmp 99f
2:  lea rsi, [rip + nan_text]
    mov rcx, 3
    call append
    jmp 99f
10: test r12, r12
    jns 11f
    mov al, '-'
    call append_char
11: test r13, r13
    jnz 12f
    test r14, r14
    jnz 13f
    mov al, '0'
    call append_char
    jmp 99f
12: bts r14, 52
    sub r13, 1075
    jmp 14f
13: mov r13, -1074
14: lea rsi, [rip + big]
    mov dword ptr [rsi], r14d
    mov rax, r14
    shr rax, 32
    mov dword ptr [rsi + 4], eax
    mov qword ptr [rip + biglen], 2
    test eax, eax
    jnz 15f
    mov qword ptr [rip + biglen], 1
15: xor ebx, ebx                        # decimal places
    test r13, r13
    js 17f
16: test r13, r13                       # N = m * 2^e, 30 bits at a time
    jz 20f
    mov rcx, r13
    cmp rcx, 30
    jbe 161f
    mov rcx, 30
161: sub r13, rcx
    mov edi, 1
    shl rdi, cl
    call big_mul
    jmp 16b
17: neg r13                             # N = m * 5^k, with k decimal places
    mov rbx, r13
171: test r13, r13
    jz 20f
    mov rcx, r13
    cmp rcx, 13
    jbe 172f
    mov rcx, 13
172: sub r13, rcx
    mov edi, 1
173: imul rdi, rdi, 5
    dec rcx
    jnz 173b
    call big_mul
    jmp 171b
20: lea r15, [rip + digits + 1024]      # the digits of N, written backwards, 9 at a time
21: mov rdi, 1000000000
    call big_div
    cmp qword ptr [rip + biglen], 0
    je 23f
    mov rcx, 9
    mov r8, 10
22: xor edx, edx
    div r8
    add dl, '0'
    dec r15
    mov byte ptr [r15], dl
    dec rcx
    jnz 22b
    jmp 21b
23: mov r8, 10
231: xor edx, edx
    div r8
    add dl, '0'
    dec r15
    mov byte ptr [r15], dl
    test rax, rax
    jnz 231b
    lea rax, [rip + digits + 1024]
    sub rax, r15                        # how many digits
    mov r13, rax
    sub r13, rbx
    dec r13                             # the power of ten of the first digit
    mov rbx, rax
    cmp rbx, 17
    jle 40f
    movzx eax, byte ptr [r15 + 17]      # round to 17 digits
    cmp al, '5'
    ja 32f
    jb 39f
    mov rcx, 18
31: cmp rcx, rbx
    jge 311f
    cmp byte ptr [r15 + rcx], '0'
    jne 32f
    inc rcx
    jmp 31b
311: movzx eax, byte ptr [r15 + 16]     # exactly half: round to even
    test al, 1
    jz 39f
32: mov rcx, 16
33: cmp byte ptr [r15 + rcx], '9'
    jne 34f
    mov byte ptr [r15 + rcx], '0'
    dec rcx
    jns 33b
    mov byte ptr [r15], '1'             # 9999... became 10000...
    inc r13
    jmp 39f
34: inc byte ptr [r15 + rcx]
39: mov rbx, 17
40: cmp rbx, 1                          # no trailing zeros
    jle 41f
    cmp byte ptr [r15 + rbx - 1], '0'
    jne 41f
    dec rbx
    jmp 40b
41: cmp r13, -4
    jl 50f
    cmp r13, 17
    jge 50f
    test r13, r13
    js 45f
    lea rcx, [r13 + 1]                  # plain: digits, maybe zeros, maybe "." and more digits
    cmp rcx, rbx
    jbe 42f
    mov rcx, rbx
42: mov rsi, r15
    call append
    lea rax, [r13 + 1]
    sub rax, rbx
    jle 44f
    mov r12, rax
43: mov al, '0'
    call append_char
    dec r12
    jnz 43b
44: lea rcx, [r13 + 1]
    cmp rbx, rcx
    jle 99f
    mov al, '.'
    call append_char
    lea rsi, [r15 + r13 + 1]
    mov rcx, rbx
    sub rcx, r13
    dec rcx
    call append
    jmp 99f
45: mov al, '0'                         # 0.000ddd
    call append_char
    mov al, '.'
    call append_char
    mov r12, r13
    neg r12
    dec r12
    jz 47f
46: mov al, '0'
    call append_char
    dec r12
    jnz 46b
47: mov rsi, r15
    mov rcx, rbx
    call append
    jmp 99f
50: movzx eax, byte ptr [r15]           # d.ddde+XX
    call append_char
    cmp rbx, 1
    jle 51f
    mov al, '.'
    call append_char
    lea rsi, [r15 + 1]
    lea rcx, [rbx - 1]
    call append
51: mov al, 'e'
    call append_char
    mov al, '+'
    test r13, r13
    jns 52f
    mov al, '-'
    neg r13
52: call append_char
    cmp r13, 10
    jge 53f
    mov al, '0'
    call append_char
53: mov rax, r13
    call append_int
99: pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    ret

# write_text: write rcx bytes from rsi into the text variable whose slot is at rdi.
# r8 = that frame's rbp, r9 = how many slots it has. Bytes that don't fit spill into the
# variables declared after it, in order. rax = 0, or 1 if it spilled past every variable.
write_text:
    push r12
    push r13
    push r14
    push r15
    push rbx
    mov r12, rdi
    mov rbx, qword ptr [r12]            # the text buffer
    mov rdx, qword ptr [rbx]            # its size
    mov r13, rsi
    mov r14, rcx
    cmp rcx, rdx
    jbe 1f
    mov rcx, rdx
1:  lea rdi, [rbx + 8]
    mov rsi, r13
    rep movsb
    cmp r14, rdx
    jbe 80f
    add r13, rdx                        # what didn't fit
    sub r14, rdx
    mov r15, qword ptr [r12 + 40]       # declaration number of this variable
2:  inc r15
    cmp r15, qword ptr [r8 - 8]
    jge 81f                             # no variables left: the stack is smashed
    xor r10, r10
3:  cmp r10, r9
    jge 2b
    mov rax, r10
    imul rax, rax, 48
    lea rdi, [r8 - 64]
    sub rdi, rax
    cmp qword ptr [rdi + 16], 1
    jne 4f
    cmp qword ptr [rdi + 40], r15
    je 5f
4:  inc r10
    jmp 3b
5:  mov rax, qword ptr [rdi + 24]
    cmp rax, 2
    je 51f
    cmp rax, 1
    je 52f
    cmp rax, 3                          # float64 and float32 variables are 8 and 4 bytes
    jne 58f
    mov rax, 64
58: cmp rax, 4
    jne 59f
    mov rax, 32
59: shr rax, 3                          # a number variable: becomes garbage
    mov rcx, r14
    cmp rcx, rax
    jbe 53f
    mov rcx, rax
53: push rdi
    push rcx
    mov rsi, r13
    call make_garbage
    pop rcx
    pop rdi
    mov qword ptr [rdi], rax
    mov qword ptr [rdi + 32], 1
    add r13, rcx
    sub r14, rcx
    jmp 6f
51: mov rax, qword ptr [rdi]            # text: overwritten from the start
    mov rdx, qword ptr [rax]
    mov rcx, r14
    cmp rcx, rdx
    jbe 54f
    mov rcx, rdx
54: lea rdi, [rax + 8]
    mov rsi, r13
    add r13, rcx
    sub r14, rcx
    rep movsb
    jmp 6f
52: mov rbx, qword ptr [rdi]            # a list: its items become garbage
    lea r10, [rbx + 24]
    push qword ptr [rbx + 8]
55: cmp qword ptr [rsp], 0
    je 56f
    test r14, r14
    jz 56f
    mov rcx, qword ptr [rbx + 16]
    cmp rcx, r14
    jbe 57f
    mov rcx, r14
57: push rcx
    push r10
    mov rsi, r13
    call make_garbage
    pop r10
    pop rcx
    mov qword ptr [r10], rax
    mov qword ptr [r10 + 8], 1
    add r13, rcx
    sub r14, rcx
    add r10, 16
    dec qword ptr [rsp]
    jmp 55b
56: add rsp, 8
6:  test r14, r14
    jnz 2b
80: xor eax, eax
    jmp 9f
81: mov eax, 1
9:  pop rbx
    pop r15
    pop r14
    pop r13
    pop r12
    ret
"""


# ---------------------------------------------------------------- compiler

class Compiler:
    def __init__(self, program, source, coward=False, rng=None, seed=None, origins=None):
        self.origins = origins
        self.program = program
        self.source = source
        self.coward = coward
        self.rng = rng or random.Random()
        self.seed = seed
        self.out = []
        self.errors = {}        # (lineno, message) -> label
        self.strings = {}       # text -> label
        self.label_count = 0
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

    def base(self, scope, name):
        return HEADER + SLOT * (self.slots[scope][name] + 1)

    def slot(self, scope, name, field):
        return f"qword ptr [rbp - {self.base(scope, name) - field}]"

    def label(self, index):
        return f"L{index}"

    # -- analysis

    def analyse(self):
        scope = None
        end = None
        for i, s in enumerate(self.program):
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
        for key in ("value", "fd", "bytes", "path"):
            if isinstance(d.get(key), tuple) and d[key][0] == "var":
                names.append(d[key][1])
        names += [x for kind, x in d.get("parts", []) if kind == "var"]
        if d.get("into"):
            names.append(d["into"])
        if s.kind in ("var", "list", "text"):
            names.append(d["name"])
        return names

    # -- code for values (a value is in rax, its tag in r14)

    def touch(self, scope, name, lineno):
        """Check that a variable exists and hasn't been forgotten, then mark it as used. Changes rax."""
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
        kind, x = v
        if kind == "num" and isinstance(x, float):
            self.emit(f"mov rax, {bits_of(x)}")
            self.emit(f"mov r14d, {FLOAT64}")
        elif kind == "num":
            self.emit(f"mov rax, {x}")
            self.emit("xor r14d, r14d")
        else:
            self.touch(scope, x, lineno)
            self.emit(f"mov rax, {self.slot(scope, x, VALUE)}")
            self.emit(f"mov r14, {self.slot(scope, x, TAG)}")

    def number(self, scope, v, lineno):
        """A value that must be a real whole number (not garbage, a list, text or a decimal)."""
        decimal_message = "that is a decimal. Only whole numbers work here. Convert it with cvttsd2si."
        if v[0] == "num" and isinstance(v[1], float):
            self.emit(f"jmp {self.error(lineno, decimal_message)}")
            return
        self.value(scope, v, lineno)
        if v[0] != "var":
            return
        name = v[1]
        ok = self.new_label()
        self.emit(f"cmp r14, {FLOAT64}")
        self.emit(f"jae {self.error(lineno, decimal_message)}")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"je {ok}")
        self.emit(f"cmp r14, {GARBAGE}")
        self.emit(f"je {self.error(lineno, 'you tried to use garbage as a number. That is garbage from memory you never stored anything in.')}")
        self.emit(f"cmp r14, {LISTREF}")
        self.emit(f"je {self.error(lineno, f'${name} is a list, not a number. Load an item out of it first.')}")
        self.emit(f"jmp {self.error(lineno, f'${name} is text, not a number. Use atoi.')}")
        self.emit(f"{ok}:")

    def decimal(self, scope, v, lineno, bits):
        """A value for a decimal instruction (sd: float64, ss: float32). Its bits in rax."""
        whole = (f"that is a whole number, and decimal instructions need decimals. Write it like 1.0, "
                 f"or convert it with cvtsi2s{'d' if bits == 64 else 's'}.")
        if v[0] == "num":
            if not isinstance(v[1], float):
                self.emit(f"jmp {self.error(lineno, whole)}")
                return
            x = round32(v[1]) if bits == 32 else v[1]        # a written decimal fits whatever it's used with
            self.emit(f"mov rax, {bits_of(x)}")
            self.emit(f"mov r14d, {FLOAT_TAG[bits]}")
            return
        name = v[1]
        self.value(scope, v, lineno)
        ok = self.new_label()
        self.emit(f"cmp r14, {FLOAT_TAG[bits]}")
        self.emit(f"je {ok}")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"je {self.error(lineno, whole)}")
        self.emit(f"cmp r14, {GARBAGE}")
        self.emit(f"je {self.error(lineno, 'you tried to use garbage as a decimal. That is garbage from memory you never stored anything in.')}")
        self.emit(f"cmp r14, {LISTREF}")
        self.emit(f"je {self.error(lineno, f'${name} is a list, not a decimal. Load an item out of it first.')}")
        self.emit(f"cmp r14, {TEXTREF}")
        self.emit(f"je {self.error(lineno, f'${name} is text, not a decimal.')}")
        if bits == 32:
            message = f"${name} is a float64, and ...ss instructions need float32. Convert it with cvtsd2ss."
        else:
            message = f"${name} is a float32, and ...sd instructions need float64. Convert it with cvtss2sd."
        self.emit(f"jmp {self.error(lineno, message)}")
        self.emit(f"{ok}:")

    def two_decimals(self, scope, values, lineno, bits):
        """First decimal in rax, second in rbx."""
        self.decimal(scope, values[0], lineno, bits)
        self.emit("push rax")
        self.decimal(scope, values[1], lineno, bits)
        self.emit("mov rbx, rax")
        self.emit("pop rax")

    def check_kind(self, type_operand, lineno, what, store):
        """The rules for putting the value in rax (tag r14) into a number or decimal variable whose
        type is in `type_operand`: decimals into decimals of the same size, whole numbers into whole
        numbers (checked for overflow), garbage into anything. Jumps to `store` when it's fine."""
        not64, not32, whole = self.new_label(), self.new_label(), self.new_label()
        self.emit(f"cmp r14, {GARBAGE}")
        self.emit(f"je {store}")                       # garbage fits anywhere, sadly
        self.emit(f"cmp {type_operand}, {F64T}")
        self.emit(f"jne {not64}")
        self.emit(f"cmp r14, {FLOAT64}")
        self.emit(f"je {store}")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"je {self.error(lineno, f'{what} holds decimals, not whole numbers. Convert it with cvtsi2sd.')}")
        self.emit(f"jmp {self.error(lineno, f'{what} is a float64 and can not hold a float32 value. Convert it with cvtss2sd.')}")
        self.emit(f"{not64}:")
        self.emit(f"cmp {type_operand}, {F32T}")
        self.emit(f"jne {not32}")
        self.emit(f"cmp r14, {FLOAT32}")
        self.emit(f"je {store}")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"je {self.error(lineno, f'{what} holds decimals, not whole numbers. Convert it with cvtsi2ss.')}")
        self.emit(f"jmp {self.error(lineno, f'{what} is a float32 and can not hold a float64 value. Convert it with cvtsd2ss.')}")
        self.emit(f"{not32}:")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"je {whole}")
        self.emit(f"jmp {self.error(lineno, f'{what} holds whole numbers, and that is a decimal. Convert it with cvttsd2si.')}")
        self.emit(f"{whole}:")
        self.check_bits(type_operand, lineno, what)
        self.emit(f"jmp {store}")

    def two_numbers(self, scope, values, lineno):
        """First number in rax, second in rbx (evaluated in that order)."""
        self.number(scope, values[0], lineno)
        self.emit("push rax")
        self.number(scope, values[1], lineno)
        self.emit("mov rbx, rax")
        self.emit("pop rax")

    def get_list(self, scope, v, lineno):
        self.value(scope, v, lineno)
        self.emit(f"cmp r14, {LISTREF}")
        self.emit(f"jne {self.error(lineno, 'the first value must be a list. That is not one.')}")

    def get_text(self, scope, v, lineno):
        """A text value: the buffer pointer in rax."""
        self.value(scope, v, lineno)
        what = f"${v[1]}" if v[0] == "var" else str(v[1])
        self.emit(f"cmp r14, {TEXTREF}")
        self.emit(f"jne {self.error(lineno, f'{what} is not text.')}")

    def text_of(self, scope, v, lineno):
        """Read a text value the C way: rsi = text, rcx = length."""
        self.get_text(scope, v, lineno)
        self.emit("mov rdi, rax")
        self.emit("call c_string")

    def check_bits(self, bits_operand, lineno, what):
        """Overflow check: does rax fit in a signed number of `bits_operand` bits?"""
        self.emit("mov ecx, 64")
        self.emit(f"sub rcx, {bits_operand}")
        self.emit("mov rdx, rax")
        self.emit("shl rdx, cl")
        self.emit("sar rdx, cl")
        self.emit("cmp rdx, rax")
        self.emit(f"jne {self.error(lineno, f'the value is too big for {what}. Overflow.')}")

    def create(self, scope, name):
        """A declaration: give the variable its declaration number the first time."""
        done = self.new_label()
        self.emit(f"cmp {self.slot(scope, name, STATE)}, {ALIVE}")
        self.emit(f"je {done}")
        self.emit("mov rcx, qword ptr [rbp - 8]")
        self.emit(f"mov {self.slot(scope, name, ORDER)}, rcx")
        self.emit("inc qword ptr [rbp - 8]")
        self.emit(f"mov {self.slot(scope, name, STATE)}, {ALIVE}")
        self.emit(f"{done}:")
        self.emit("mov rcx, qword ptr [rip + step]")
        self.emit(f"mov {self.slot(scope, name, LAST)}, rcx")

    def write_text_into(self, scope, name, lineno):
        """Write (rsi, rcx) into a text variable, spilling into the next variables if needed."""
        self.emit(f"lea rdi, [rbp - {self.base(scope, name)}]")
        self.emit("mov r8, rbp")
        self.emit(f"mov r9, {len(self.slots[scope])}")
        self.emit("call write_text")
        self.emit("test rax, rax")
        self.emit(f"jnz {self.error(lineno, '*** stack smashing detected ***: terminated. Your text overflowed past every variable you have.')}")

    def assign(self, scope, name, lineno):
        """Store the value in rax (tag r14) in a variable, following every rule."""
        self.emit("push rax")
        self.emit("push r14")
        self.touch(scope, name, lineno)
        self.emit("pop r14")
        self.emit("pop rax")
        self.emit(f"cmp {self.slot(scope, name, BITS)}, {LIST}")
        self.emit(f"je {self.error(lineno, f'${name} is a list. You can not pour a single value into it. Use store.')}")
        self.emit(f"cmp r14, {LISTREF}")
        self.emit(f"je {self.error(lineno, f'a list can not be put into the variable ${name}.')}")
        number, store, done = self.new_label(), self.new_label(), self.new_label()
        self.emit(f"cmp {self.slot(scope, name, BITS)}, {CHAR}")
        self.emit(f"jne {number}")
        self.emit(f"cmp r14, {TEXTREF}")
        self.emit(f"jne {self.error(lineno, f'${name} is text. That value is not. Use itoa.')}")
        self.emit("mov rdi, rax")
        self.emit("call c_string")
        self.emit("call with_nul")
        self.write_text_into(scope, name, lineno)
        self.emit(f"jmp {done}")
        self.emit(f"{number}:")
        self.emit(f"cmp r14, {TEXTREF}")
        self.emit(f"je {self.error(lineno, f'${name} holds numbers. Text does not go in it. Use atoi.')}")
        self.check_kind(self.slot(scope, name, BITS), lineno, f"${name}", store)
        self.emit(f"{store}:")
        self.emit(f"mov {self.slot(scope, name, VALUE)}, rax")
        self.emit(f"mov {self.slot(scope, name, TAG)}, r14")
        self.emit(f"{done}:")

    def assign_number(self, scope, name, lineno):
        self.emit("xor r14d, r14d")
        self.assign(scope, name, lineno)

    def assign_new_text(self, scope, name, lineno):
        """Store new text (rsi, rcx, with its 0 byte) from itoa or read in a variable."""
        self.emit("push rsi")
        self.emit("push rcx")
        self.touch(scope, name, lineno)
        self.emit("pop rcx")
        self.emit("pop rsi")
        self.emit(f"cmp {self.slot(scope, name, BITS)}, {LIST}")
        self.emit(f"je {self.error(lineno, f'${name} is a list. You can not pour a single value into it. Use store.')}")
        self.emit(f"cmp {self.slot(scope, name, BITS)}, {CHAR}")
        self.emit(f"jne {self.error(lineno, f'${name} holds numbers. Text does not go in it. Use atoi.')}")
        self.write_text_into(scope, name, lineno)

    # -- code for statements

    def statement(self, i, s):
        scope = self.scope_of[i] if s.kind != "enddefine" else self.function_of(i)
        d, line = s.data, s.lineno
        self.emit(f"{self.label(i)}:")
        self.emit("inc qword ptr [rip + step]")
        self.emit(f"cmp qword ptr [rip + step], {MAX_STEPS}")
        self.emit(f"jg {self.error(line, 'infinite suffering detected. Even I have limits.')}")
        handler = getattr(self, "do_" + s.kind, None)
        if handler:
            handler(scope, s, d, line)
        # endif and comment: nothing, but the line still counts

    def do_var(self, scope, s, d, line):
        name, type_ = d["name"], d["type"]
        code = FLOAT_TYPE.get(type_) or TYPES[type_]
        kind, x = d["value"]
        if kind == "num" and type_ == "float32" and isinstance(x, float):
            self.emit(f"mov rax, {bits_of(round32(x))}")        # a written decimal fits what it's put in
            self.emit(f"mov r14d, {FLOAT32}")
        else:
            self.value(scope, d["value"], line)
        store = self.new_label()
        if kind == "var":
            self.emit(f"cmp r14, {LISTREF}")
            self.emit(f"je {self.error(line, 'a list can not be copied into a variable.')}")
            self.emit(f"cmp r14, {TEXTREF}")
            self.emit(f"je {self.error(line, type_ + ' holds numbers. Text does not go in it. Use atoi.')}")
        self.emit("mov rbx, " + str(code))
        self.check_kind("rbx", line, type_, store)
        self.emit(f"{store}:")
        self.emit(f"mov {self.slot(scope, name, VALUE)}, rax")
        self.emit(f"mov {self.slot(scope, name, TAG)}, r14")
        self.emit(f"mov {self.slot(scope, name, BITS)}, {code}")
        self.create(scope, name)

    def do_list(self, scope, s, d, line):
        name, bits = d["name"], TYPES[d["type"]]
        size = bits // 8
        self.number(scope, d["bytes"], line)
        self.emit("cmp rax, 0")
        self.emit(f"jle {self.error(line, 'a list needs more than 0 bytes. Ambitious.')}")
        self.emit(f"cmp rax, {MAX_LIST_BYTES}")
        self.emit(f"jg {self.error(line, 'out of memory. Nobody needs that many bytes.')}")
        self.emit("cqo")
        self.emit(f"mov rcx, {size}")
        self.emit("idiv rcx")
        self.emit("test rdx, rdx")
        message = f"that many bytes does not divide into {d['type']} items ({size} bytes each)."
        self.emit(f"jnz {self.error(line, message)}")
        self.emit(f"mov rdi, {bits}")
        self.emit("mov rsi, rax")
        self.emit("call new_list")
        self.emit(f"mov {self.slot(scope, name, VALUE)}, rax")
        self.emit(f"mov {self.slot(scope, name, TAG)}, {LISTREF}")
        self.emit(f"mov {self.slot(scope, name, BITS)}, {LIST}")
        self.create(scope, name)

    def do_text(self, scope, s, d, line):
        name = d["name"]
        kind, x = d["value"]
        if kind == "text":
            data = x.encode()
            self.emit(f"lea rsi, [rip + {self.string(x)}]")
            self.emit(f"mov rcx, {len(data)}")
        elif kind == "var":
            self.text_of(scope, d["value"], line)
            self.emit("call with_nul")
        if kind != "empty":
            self.emit("push rsi")
            self.emit("push rcx")
        self.emit(f"mov rdi, {d['size']}")
        self.emit("call new_text")
        self.emit(f"mov {self.slot(scope, name, VALUE)}, rax")
        self.emit(f"mov {self.slot(scope, name, TAG)}, {TEXTREF}")
        self.emit(f"mov {self.slot(scope, name, BITS)}, {CHAR}")
        self.create(scope, name)
        if kind != "empty":
            self.emit("pop rcx")
            self.emit("pop rsi")
            self.write_text_into(scope, name, line)

    def item(self, scope, values, line):
        """The list in rbx (also pushed) and the item number in rax, after every check."""
        self.get_list(scope, values[0], line)
        self.emit("push rax")
        self.number(scope, values[1], line)
        self.emit("mov rbx, qword ptr [rsp]")
        self.emit("mov rcx, qword ptr [rbx + 16]")
        self.emit("mov r8, rax")
        self.emit("cqo")
        self.emit("idiv rcx")
        self.emit("test rdx, rdx")
        self.emit(f"jnz {self.error(line, 'misaligned memory access: that offset is not a multiple of the item size.')}")
        segfault = self.error(line, "Segmentation fault (core dumped). That offset is outside the list.")
        self.emit("test r8, r8")
        self.emit(f"js {segfault}")
        self.emit("cmp rax, qword ptr [rbx + 8]")
        self.emit(f"jge {segfault}")

    def do_store(self, scope, s, d, line):
        self.item(scope, d["values"], line)
        self.emit("push rax")
        self.value(scope, d["values"][2], line)
        message = "lists hold numbers. You can not store a list or text in one."
        self.emit(f"cmp r14, {LISTREF}")
        self.emit(f"je {self.error(line, message)}")
        self.emit(f"cmp r14, {TEXTREF}")
        self.emit(f"je {self.error(line, message)}")
        store = self.new_label()
        self.emit("mov rbx, qword ptr [rsp + 8]")
        self.emit(f"cmp r14, {FLOAT64}")
        self.emit(f"jae {self.error(line, 'lists hold whole numbers, and that is a decimal. Convert it with cvttsd2si.')}")
        self.emit(f"cmp r14, {INT}")
        self.emit(f"jne {store}")
        self.check_bits("qword ptr [rbx]", line, "the list's items")
        self.emit(f"{store}:")
        self.emit("pop rcx")
        self.emit("pop rbx")
        self.emit("shl rcx, 4")
        self.emit("mov qword ptr [rbx + rcx + 24], rax")
        self.emit("mov qword ptr [rbx + rcx + 32], r14")

    def do_load(self, scope, s, d, line):
        self.item(scope, d["values"], line)
        self.emit("pop rbx")
        self.emit("shl rax, 4")
        self.emit("mov r14, qword ptr [rbx + rax + 32]")
        self.emit("mov rax, qword ptr [rbx + rax + 24]")
        self.assign(scope, d["into"], line)

    def do_libc(self, scope, s, d, line):
        a, name = d["args"], d["name"]
        if name == "strlen":
            self.text_of(scope, a[0], line)
            self.emit("mov rax, rcx")
            self.assign_number(scope, d["into"], line)
        elif name == "strcat":
            if a[0][0] != "var":                         # always "isn't text"
                self.get_text(scope, a[0], line)
                return
            self.text_of(scope, a[0], line)
            self.emit("push rsi")
            self.emit("push rcx")
            self.text_of(scope, a[1], line)
            self.emit("mov r8, rsi")
            self.emit("mov r9, rcx")
            self.emit("pop rcx")
            self.emit("pop rsi")
            self.emit("call concat")
            self.write_text_into(scope, a[0][1], line)
        elif name == "strcmp":
            self.text_of(scope, a[0], line)
            self.emit("push rsi")
            self.emit("push rcx")
            self.text_of(scope, a[1], line)
            self.emit("mov rdi, rsi")
            self.emit("mov rdx, rcx")
            self.emit("pop rcx")
            self.emit("pop rsi")
            self.emit("call bytes_cmp")
            self.assign_number(scope, d["into"], line)
        elif name == "atoi":
            self.text_of(scope, a[0], line)
            self.emit("call atoi_parse")
            self.emit("cmp rdx, 2")
            self.emit(f"je {self.error(line, 'the number in that text is too big. Overflow.')}")
            self.assign_number(scope, d["into"], line)
        elif name == "itoa":
            self.number(scope, a[0], line)
            self.emit("mov qword ptr [rip + outlen], 0")
            self.emit("call append_int")
            self.emit("mov rsi, qword ptr [rip + outptr]")
            self.emit("mov rcx, qword ptr [rip + outlen]")
            self.emit("call with_nul")
            self.assign_new_text(scope, d["into"], line)
        elif name == "charat":
            self.get_text(scope, a[0], line)
            self.emit("push rax")
            self.number(scope, a[1], line)
            self.emit("pop rbx")
            segfault = self.error(line, "Segmentation fault (core dumped). That byte is outside the text.")
            self.emit("test rax, rax")
            self.emit(f"js {segfault}")
            self.emit("cmp rax, qword ptr [rbx]")
            self.emit(f"jge {segfault}")
            self.emit("movzx rax, byte ptr [rbx + rax + 8]")
            self.assign_number(scope, d["into"], line)

    def do_print(self, scope, s, d, line):
        self.build_text(scope, s)
        self.emit("mov al, 10")
        self.emit("call append_char")
        self.emit("mov rax, 1")
        self.emit("mov rdi, 1")
        self.emit("mov rsi, qword ptr [rip + outptr]")
        self.emit("mov rdx, qword ptr [rip + outlen]")
        self.emit("syscall")

    def open_file_check(self, bad):
        """rax = file descriptor. Jumps to `bad` unless this program opened it."""
        self.emit("cmp rax, 0")
        self.emit(f"jl {bad}")
        self.emit(f"cmp rax, {OPEN_FILES}")
        self.emit(f"jge {bad}")
        self.emit("lea rcx, [rip + open_files]")
        self.emit("cmp byte ptr [rcx + rax], 0")
        self.emit(f"je {bad}")

    def do_write(self, scope, s, d, line):
        self.build_text(scope, s)
        self.number(scope, d["fd"], line)
        ok, bad, done = self.new_label(), self.new_label(), self.new_label()
        self.emit("cmp rax, 1")
        self.emit(f"je {ok}")
        self.emit("cmp rax, 2")
        self.emit(f"je {ok}")
        self.open_file_check(bad)
        self.emit(f"{ok}:")
        self.emit("mov rdi, rax")
        self.emit("mov rax, 1")
        self.emit("mov rsi, qword ptr [rip + outptr]")
        self.emit("mov rdx, qword ptr [rip + outlen]")
        self.emit("syscall")
        self.emit(f"jmp {done}")
        self.emit(f"{bad}:")
        self.emit("mov rax, -9")                         # EBADF
        self.emit(f"{done}:")
        self.assign_number(scope, d["into"], line)

    def do_open(self, scope, s, d, line):
        self.text_of(scope, d["path"], line)
        flags, mode = d["flags"], d["mode"]
        if flags & 3 == 3 or flags & ~(3 | O_CREAT | O_TRUNC | O_APPEND) or mode > 0o7777:
            self.emit("mov rax, -22")                    # EINVAL, like the real thing
        else:
            done = self.new_label()
            self.emit("call with_nul")
            self.emit("mov rdi, rsi")
            self.emit(f"mov rsi, {flags}")
            self.emit(f"mov rdx, {mode}")
            self.emit("mov rax, 2")                      # the asm you wrote: open
            self.emit("syscall")
            self.emit("cmp rax, 0")
            self.emit(f"jl {done}")
            self.emit(f"cmp rax, {OPEN_FILES}")
            self.emit(f"jge {done}")
            self.emit("lea rcx, [rip + open_files]")
            self.emit("mov byte ptr [rcx + rax], 1")
            self.emit(f"{done}:")
        self.assign_number(scope, d["into"], line)

    def do_close(self, scope, s, d, line):
        self.number(scope, d["fd"], line)
        bad, done = self.new_label(), self.new_label()
        self.open_file_check(bad)
        self.emit("mov byte ptr [rcx + rax], 0")
        self.emit("mov rdi, rax")
        self.emit("mov rax, 3")                          # the asm you wrote: close
        self.emit("syscall")
        self.emit("xor eax, eax")
        self.emit(f"jmp {done}")
        self.emit(f"{bad}:")
        self.emit("mov rax, -9")
        self.emit(f"{done}:")
        self.assign_number(scope, d["into"], line)

    def do_read(self, scope, s, d, line):
        into = d["into"]
        self.number(scope, d["fd"], line)
        keyboard, got = self.new_label(), self.new_label()
        self.emit("test rax, rax")
        self.emit(f"jz {keyboard}")
        # a file
        bad = self.error(line, "read from a file descriptor that is not open: Bad file descriptor. "
                               "Did open give you a negative number?")
        self.open_file_check(bad)
        self.emit("push rax")
        self.emit(f"mov rdi, {max(d['maxlen'], 1)}")
        self.emit("call alloc")
        self.emit("pop rdi")
        self.emit("push rax")
        self.emit("mov rsi, rax")
        self.emit(f"mov rdx, {d['maxlen']}")
        self.emit("mov rax, 0")                          # the asm you wrote: read
        self.emit("syscall")
        self.emit("pop rsi")
        self.emit("cmp rax, 0")
        self.emit(f"jl {self.error(line, 'reading the file failed.')}")
        self.emit("mov rcx, rax")
        self.emit("test rcx, rcx")
        self.emit(f"jnz {got}")
        self.emit("push rsi")
        self.touch(scope, into, line)
        self.emit("pop rsi")
        self.emit("xor ecx, ecx")
        self.emit(f"cmp {self.slot(scope, into, BITS)}, {CHAR}")
        self.emit(f"je {got}")
        self.emit(f"jmp {self.error(line, 'read got nothing: the end of the file. That is not a number.')}")
        # the keyboard
        self.emit(f"{keyboard}:")
        self.emit(f"mov rdi, {d['maxlen']}")
        self.emit("call take_chunk")
        self.emit("cmp rax, -1")
        self.emit(f"je {self.error(line, 'there is no input left to read. The user gave up.')}")
        self.emit("lea rsi, [rip + chunk]")
        self.emit("mov rcx, rax")
        self.emit("test rcx, rcx")
        self.emit(f"jnz {got}")
        self.touch(scope, into, line)
        self.emit("lea rsi, [rip + chunk]")
        self.emit("xor ecx, ecx")
        self.emit(f"cmp {self.slot(scope, into, BITS)}, {CHAR}")
        self.emit(f"je {got}")
        self.emit(f"jmp {self.error(line, 'read got nothing but Enter, which is not a number. (Leftover characters from an earlier read count too.)')}")
        # what was read is in (rsi, rcx)
        number = self.new_label()
        self.emit(f"{got}:")
        self.emit("push rsi")
        self.emit("push rcx")
        self.touch(scope, into, line)
        self.emit("pop rcx")
        self.emit("pop rsi")
        self.emit(f"cmp {self.slot(scope, into, BITS)}, {CHAR}")
        self.emit(f"jne {number}")
        self.emit("call with_nul")
        done = self.new_label()
        self.assign_new_text(scope, into, line)
        self.emit(f"jmp {done}")
        self.emit(f"{number}:")
        self.emit("call parse_int")
        self.emit("cmp rdx, 1")
        self.emit(f"je {self.error(line, 'the user typed something that is not a number. Even the user is bad at this.')}")
        self.emit("cmp rdx, 2")
        self.emit(f"je {self.error(line, 'the number typed is too big for $' + into + '. Overflow.')}")
        self.assign_number(scope, into, line)
        self.emit(f"{done}:")

    def do_rand(self, scope, s, d, line):
        size, into = d["bytes"], d["into"]
        self.touch(scope, into, line)
        self.emit(f"cmp {self.slot(scope, into, BITS)}, 8")
        self.emit(f"jl {self.error(line, 'rand gives a number. $' + into + ' can not hold one.')}")
        self.emit(f"mov rax, {size * 8}")
        self.emit(f"cmp rax, {self.slot(scope, into, BITS)}")
        message = f"you asked getrandom for {size} bytes, but $" + into + " is a different size."
        self.emit(f"jne {self.error(line, message)}")
        self.emit("mov rax, 318")                        # the asm you wrote: getrandom
        self.emit("lea rdi, [rip + randbuf]")
        self.emit(f"mov rsi, {size}")
        self.emit("mov rdx, 0")
        self.emit("syscall")
        load = {1: "movsx rax, byte ptr", 2: "movsx rax, word ptr", 4: "movsxd rax, dword ptr",
                8: "mov rax, qword ptr"}.get(size, "mov rax, qword ptr")
        self.emit(f"{load} [rip + randbuf]")
        self.assign_number(scope, into, line)

    def do_math(self, scope, s, d, line):
        self.two_numbers(scope, d["values"], line)
        op = d["op"]
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
        self.assign_number(scope, d["into"], line)

    def do_fmath(self, scope, s, d, line):
        bits, op = d["bits"], d["op"]
        self.two_decimals(scope, d["values"], line, bits)
        self.emit("movq xmm0, rax")
        self.emit("movq xmm1, rbx")
        if bits == 32:
            self.emit("cvtsd2ss xmm0, xmm0")
            self.emit("cvtsd2ss xmm1, xmm1")
        self.emit(FLOAT_MATH[op][bits])                  # the asm you wrote: addsd, mulss...
        if bits == 32:
            self.emit("cvtss2sd xmm0, xmm0")
        self.emit("movq rax, xmm0")
        self.emit(f"mov r14d, {FLOAT_TAG[bits]}")
        self.assign(scope, d["into"], line)

    def do_cvt(self, scope, s, d, line):
        source, target = CVT[d["asm"]]
        if source == "int":
            self.number(scope, d["value"], line)
        else:
            self.decimal(scope, d["value"], line, FLOATS[source])
            self.emit("movq xmm0, rax")
            if source == "float32":
                self.emit("cvtsd2ss xmm0, xmm0")         # exact: the value is a float32 already
        self.emit(d["asm"])                              # the asm you wrote: cvtsi2sd, cvttsd2si...
        if target == "int":
            self.emit("xor r14d, r14d")
        else:
            if target == "float32":
                self.emit("cvtss2sd xmm0, xmm0")
            self.emit("movq rax, xmm0")
            self.emit(f"mov r14d, {FLOAT_TAG[FLOATS[target]]}")
        self.assign(scope, d["into"], line)

    def do_if(self, scope, s, d, line):
        if d["float"]:
            bits = d["float"]
            self.two_decimals(scope, d["values"], line, bits)
            self.emit("movq xmm0, rax")
            self.emit("movq xmm1, rbx")
            if bits == 32:
                self.emit("cvtsd2ss xmm0, xmm0")
                self.emit("cvtsd2ss xmm1, xmm1")
                self.emit("ucomiss xmm0, xmm1")
            else:
                self.emit("ucomisd xmm0, xmm1")
        else:
            self.two_numbers(scope, d["values"], line)
            self.emit("cmp rax, rbx")
        if s.kind == "loop":
            target = d["end"] + 1
        elif d["else"] is not None:
            target = d["else"] + 1
        else:
            target = d["end"]
        self.emit(f"{d['jump']} {self.label(target)}")   # the PMEH jump is the real jump

    do_loop = do_if

    def do_else(self, scope, s, d, line):
        self.emit(f"jmp {self.label(d['end'])}")

    def do_endloop(self, scope, s, d, line):
        self.emit(f"jmp {self.label(d['start'])}")

    def do_def(self, scope, s, d, line):
        self.emit(f"jmp {self.label(d['end'] + 1)}")

    def do_return(self, scope, s, d, line):
        self.value(scope, d["value"], line)
        self.emit("leave")
        self.emit("ret")

    def do_enddefine(self, scope, s, d, line):
        name = self.program[scope].data["name"]
        self.emit(f"jmp {self.error(line, f'{name} ended without returning anything.')}")

    def do_call(self, scope, s, d, line):
        self.emit(f"cmp qword ptr [rip + depth], {MAX_DEPTH}")
        self.emit(f"jge {self.error(line, f'stack overflow: more than {MAX_DEPTH} calls deep. You recursed into the abyss.')}")
        for v in d["args"]:
            self.value(scope, v, line)
            self.emit("push rax")
            self.emit("push r14")
        self.touch(scope, d["into"], line)
        for i in reversed(range(len(d["args"]))):
            self.emit("pop r14")
            self.emit(f"mov qword ptr [rip + argtags + {8 * i}], r14")
            self.emit(f"pop {PARAM_REGISTERS[i]}")
        self.emit("push qword ptr [rip + step]")
        self.emit("inc qword ptr [rip + depth]")
        self.emit(f"call F{d['target']}")
        self.emit("dec qword ptr [rip + depth]")
        # the caller's variables were frozen: move their "last used" forward by the time spent
        self.emit("pop rcx")
        self.emit("mov rdx, qword ptr [rip + step]")
        self.emit("sub rdx, rcx")
        for name in self.slots[scope]:
            self.emit(f"add {self.slot(scope, name, LAST)}, rdx")
        self.assign(scope, d["into"], line)

    def function_of(self, i):
        for j in range(i, -1, -1):
            if self.program[j].kind == "def":
                return j
        raise AssertionError("enddefine without def")

    def build_text(self, scope, s):
        d, line = s.data, s.lineno
        self.emit("mov qword ptr [rip + outlen], 0")
        for kind, x in d["parts"]:
            if kind == "text":
                if x:
                    self.emit(f"lea rsi, [rip + {self.string(x)}]")
                    self.emit(f"mov rcx, {len(x.encode())}")
                    self.emit("call append")
            elif kind in ("space", "line"):
                word, token = ("space", "word") if kind == "space" else ("new line", "line")
                message = f"this {word} is not at position {x}, but you wrote [{x}{token}]."
                self.emit(f"cmp qword ptr [rip + outlen], {x}")
                self.emit(f"jne {self.error(line, message)}")
                self.emit(f"mov al, {32 if kind == 'space' else 10}")
                self.emit("call append_char")
            else:
                text, junk, done = self.new_label(), self.new_label(), self.new_label()
                self.touch(scope, x, line)
                self.emit(f"mov rax, {self.slot(scope, x, VALUE)}")
                self.emit(f"mov r14, {self.slot(scope, x, TAG)}")
                self.emit(f"cmp r14, {LISTREF}")
                self.emit(f"je {self.error(line, f'${x} is a list. Print its items one at a time.')}")
                self.emit(f"cmp r14, {TEXTREF}")
                self.emit(f"je {text}")
                self.emit(f"cmp r14, {GARBAGE}")
                self.emit(f"je {junk}")
                decimal = self.new_label()
                self.emit(f"cmp r14, {FLOAT64}")
                self.emit(f"jae {decimal}")
                self.emit("call append_int")
                self.emit(f"jmp {done}")
                self.emit(f"{decimal}:")
                self.emit("call append_float")
                self.emit(f"jmp {done}")
                self.emit(f"{text}:")
                self.emit("mov rdi, rax")
                self.emit("call c_string")
                self.emit("call append")
                self.emit(f"jmp {done}")
                self.emit(f"{junk}:")
                self.emit("lea rsi, [rax + 8]")
                self.emit("mov rcx, qword ptr [rax]")
                self.emit("call append")
                self.emit(f"{done}:")
        length = d["length"]
        message = f"mov rdx, {length} but the text is a different length."
        self.emit(f"cmp qword ptr [rip + outlen], {length}")
        self.emit(f"jne {self.error(line, message)}")

    def frame(self, scope):
        """Make room for the variables and mark them all as never existed.
        Only r10 and r11 are used, so arguments in rdi, rsi, rdx, rcx, r8, r9 survive."""
        size = HEADER + SLOT * len(self.slots[scope])
        size += -size % 16
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
        for i, (name, register) in enumerate(zip(d["params"], PARAM_REGISTERS)):
            # lists and text come by reference; everything else is an int64 parameter
            kind, done = self.new_label(), self.new_label()
            self.emit(f"mov {self.slot(index, name, VALUE)}, {register}")
            self.emit(f"mov rax, qword ptr [rip + argtags + {8 * i}]")
            self.emit(f"mov {self.slot(index, name, TAG)}, rax")
            self.emit(f"mov {self.slot(index, name, BITS)}, 64")
            self.emit(f"cmp rax, {LISTREF}")
            self.emit(f"jne {kind}")
            self.emit(f"mov {self.slot(index, name, BITS)}, {LIST}")
            self.emit(f"{kind}:")
            self.emit(f"cmp rax, {TEXTREF}")
            self.emit(f"jne {done}")
            self.emit(f"mov {self.slot(index, name, BITS)}, {CHAR}")
            self.emit(f"{done}:")
            not64, not32 = self.new_label(), self.new_label()
            self.emit(f"cmp rax, {FLOAT64}")
            self.emit(f"jne {not64}")
            self.emit(f"mov {self.slot(index, name, BITS)}, {F64T}")
            self.emit(f"{not64}:")
            self.emit(f"cmp rax, {FLOAT32}")
            self.emit(f"jne {not32}")
            self.emit(f"mov {self.slot(index, name, BITS)}, {F32T}")
            self.emit(f"{not32}:")
            self.emit(f"mov {self.slot(index, name, STATE)}, {ALIVE}")
            self.emit(f"mov {self.slot(index, name, ORDER)}, {i}")
            self.emit("mov rax, qword ptr [rip + step]")
            self.emit(f"mov {self.slot(index, name, LAST)}, rax")
        self.emit(f"mov qword ptr [rbp - 8], {len(d['params'])}")
        self.emit(f"jmp {self.label(index + 1)}")

    def start(self):
        self.emit(".intel_syntax noprefix")
        self.emit(".text")
        self.emit(".globl _start")
        self.emit("_start:")
        self.emit("mov rax, 12")                         # the heap starts at the current break
        self.emit("xor edi, edi")
        self.emit("syscall")
        self.emit("mov qword ptr [rip + heap_end], rax")
        self.emit("add rax, 15")
        self.emit("and rax, -16")
        self.emit("mov qword ptr [rip + heap_cur], rax")
        if self.seed is not None:                       # -s: the same garbage every run
            state = (self.seed * 0x9E3779B97F4A7C15 + 1) % 2 ** 64 or 1
            self.emit(f"mov rax, {state}")
            self.emit("mov qword ptr [rip + rng_state], rax")
        else:
            self.emit("mov rax, 318")
            self.emit("lea rdi, [rip + rng_state]")
            self.emit("mov rsi, 8")
            self.emit("xor edx, edx")
            self.emit("syscall")
            self.emit("or qword ptr [rip + rng_state], 1")
        self.emit("mov rbp, rsp")
        self.frame(None)

    def finish(self):
        last = self.program[-1].lineno + 1 if self.program else 1
        message = "the program ended with a file still open. Close the door behind you."
        self.emit(f"{self.label(len(self.program))}:")
        self.emit("lea rsi, [rip + open_files]")
        self.emit(f"mov rcx, {OPEN_FILES}")
        loop, done = self.new_label(), self.new_label()
        self.emit(f"{loop}:")
        self.emit("cmp byte ptr [rsi], 0")
        self.emit(f"jne {self.error(last, message)}")
        self.emit("inc rsi")
        self.emit("dec rcx")
        self.emit(f"jnz {loop}")
        self.emit("mov rax, 60")
        self.emit("xor edi, edi")
        self.emit("syscall")

    def compile(self):
        self.analyse()
        self.start()
        for i, s in enumerate(self.program):
            self.statement(i, s)
        self.finish()
        for i, s in enumerate(self.program):
            if s.kind == "def":
                self.function(i)
        self.out.append(RUNTIME.replace("JUNK_COUNT", str(len(GARBAGE_JUNK))))
        oom = b"\nPUNISHMENT: out of memory. Even the heap gave up on you.\n"
        self.emit("out_of_memory:")
        self.emit("lea rsi, [rip + oom_text]")
        self.emit(f"mov rdx, {len(oom)}")
        self.emit("jmp punish")
        self.strings_bytes("oom_text", oom)
        # every error report, scrambled at compile time
        for (lineno, message), label in self.errors.items():
            if self.coward:
                report = f"\nPUNISHMENT on line {lineno}{where(lineno, self.origins)}: {message}\nCoward.\n"
            else:
                report = pmeh.punishment_report(Punishment(lineno, message), self.source, self.rng) + "\n"
            data = report.encode()
            self.emit(f"{label}:")
            self.emit(f"lea rsi, [rip + {label}_text]")
            self.emit(f"mov rdx, {len(data)}")
            self.emit("jmp punish")
            self.strings_bytes(f"{label}_text", data)
        self.emit(".section .rodata")
        for text, label in self.strings.items():
            self.strings_bytes(label, text.encode())
        self.strings_bytes("inf_text", b"inf")
        self.strings_bytes("nan_text", b"nan")
        self.out.append(".balign 8")
        self.out.append("junk_table:")
        for i, junk in enumerate(GARBAGE_JUNK):
            self.out.append(f"    .quad J{i}, {len(junk.encode())}")
        for i, junk in enumerate(GARBAGE_JUNK):
            self.strings_bytes(f"J{i}", junk.encode())
        self.emit(".bss")
        for name, size in (("step", 8), ("depth", 8), ("outlen", 8), ("outcap", 8), ("outptr", 8),
                           ("pend_start", 8), ("pend_len", 8), ("heap_cur", 8), ("heap_end", 8),
                           ("rng_state", 8), ("argtags", 64), ("numbuf", 32), ("randbuf", 16),
                           ("open_files", OPEN_FILES), ("pend", 65536), ("chunk", 65536),
                           ("biglen", 8), ("big", 512), ("digits", 1024)):
            self.emit(f"{name}: .zero {size}")
        return "\n".join(self.out) + "\n"

    def strings_bytes(self, label, data):
        self.out.append(f"{label}:")
        for i in range(0, len(data), 32):
            self.out.append("    .byte " + ", ".join(str(b) for b in data[i:i + 32]))


def compile_to_asm(source, coward=False, seed=None, origins=None):
    program = pmeh.parse(source)
    rng = random.Random(seed) if seed is not None else None
    return Compiler(program, source, coward, rng, seed, origins).compile()


def build(source, output, coward=False, assembly_only=False, seed=None, origins=None):
    """Compile PMEH source (already glued together with its imports) to an executable,
    or to an assembly file."""
    asm = compile_to_asm(source, coward, seed, origins)
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
