# PunishMeEvenHarder++ syntax (draft 2)

## 1. Numbers
Every number is turned upside down: read the digits right to left, then flip each one.

| Digit    | 0 | 1 | 2 | 5 | 6 | 8 | 9 | 3, 4, 7    |
|----------|---|---|---|---|---|---|---|------------|
| Flips to | 0 | 1 | 5 | 2 | 9 | 8 | 6 | **banned** |

- `21` means **15**, `01` means **10**, `5` means **2**, `6` means **9**, `-21` means **-15**.
- Any number containing 3, 4 or 7 must be calculated, for example `add 1,5` (1 + 2 = 3).
- **Exceptions (on purpose):** the number in `[Nword]` and numbers inside `asm:{...}` are normal.

## 2. Program shape
- One statement per line. Blank lines and indentation are allowed.
- The last line must always be exactly:
```
<EXCPROGRAM>VALIDATE:COMMNT"I'd rather die than code in this programming language"[NOMERCYGRANTED]
```

## 3. Built-in functions
```
<<startfunc<FROMBUILTIN><<asm:{ASM}>>mco:{NAME IN BINARY}<<>>ARGUMENTS>>/>>
```
- `ASM` must be the real Linux x86-64 code for the operation (extra spaces don't matter).
- The name is 8-bit ASCII binary, bytes separated by spaces.
- Math results must go somewhere: add `<<into>>$var` before `>>/>>`.

| Function | Required asm | Name in binary |
|---|---|---|
| print | `mov rax, 1; mov rdi, 1; lea rsi, [msg]; mov rdx, LENGTH; syscall` | `01110000 01110010 01101001 01101110 01110100` |
| add | `add rax, rbx` | `01100001 01100100 01100100` |
| sub | `sub rax, rbx` | `01110011 01110101 01100010` |
| read | `mov rax, 0; mov rdi, 0; lea rsi, [buf]; mov rdx, MAXLEN; syscall` | `01110010 01100101 01100001 01100100` |
| store | `mov [rbx + rcx], rax` | `01110011 01110100 01101111 01110010 01100101` |
| load | `mov rax, [rbx + rcx]` | `01101100 01101111 01100001 01100100` |
| mod | `cqo; idiv rbx; mov rax, rdx` (the remainder, with the sign of the first number) | `01101101 01101111 01100100` |
| rand | `mov rax, 318; lea rdi, [buf]; mov rsi, BYTES; mov rdx, 0; syscall` | `01110010 01100001 01101110 01100100` |
| write | `mov rax, 1; mov rdi, [fd]; lea rsi, [msg]; mov rdx, LENGTH; syscall` | `01110111 01110010 01101001 01110100 01100101` |
| open | `mov rax, 2; lea rdi, [path]; mov rsi, FLAGS; mov rdx, MODE; syscall` | `01101111 01110000 01100101 01101110` |
| close | `mov rax, 3; mov rdi, [fd]; syscall` | `01100011 01101100 01101111 01110011 01100101` |
| mul | `imul rax, rbx` | `01101101 01110101 01101100` |
| cvt | one of the conversion instructions in section 14 | `01100011 01110110 01110100` |
| div | `cqo; idiv rbx` (rounds toward zero) | `01100100 01101001 01110110` |

Math arguments: `$x,1`. The first goes in `rax`, the second in `rbx`.

### Print
- Print accepts `"TEXT"`, `[Nword]`, `[Nline]` and `$variables`, glued together with nothing in between.
- Text may not contain real spaces. A space is `[Nword]`, where N is its position in the output, counting from 0.
- A new line inside the text is `[Nline]`, with N its position, the same way.
- `LENGTH` must be exactly the number of characters printed. Print adds a newline at the end (not counted).

```
"HELLO"[5word]"WORLD"      -> HELLO WORLD   (LENGTH 11)
```

## 4. Variables
```
<<startvar<STACK>>type:{int64}name:{x}<<>>21>>/>>
```
- Types: `int8`, `int16`, `int32`, `int64`. A value that doesn't fit is an overflow error.
- The value can be a number or another `$variable`.
- **Forgetful:** if a variable isn't used for 3 executed lines in a row, it is erased.
  Declaring it again brings it back.

## 5. If / else
```
<<startif<<asm:{cmp rax, rbx; jle skip}>>$x,0>>
    ...runs when x > 0...
<<else>>
    ...otherwise...
<<endif>>/>>
```
The jump says when to skip the block, so you write the opposite of your condition.
If jumps must target `skip`.

| You want | You write |
|---|---|
| x > y  | `jle` |
| x < y  | `jge` |
| x == y | `jne` |
| x != y | `je`  |
| x >= y | `jl`  |
| x <= y | `jg`  |

## 6. Loops
```
<<startloop<<asm:{cmp rax, rbx; jle end}>>$x,0>>
    ...
<<endloop>>/>>
```
The condition is checked each time round. The loop stops when the jump condition is true.
Loop jumps must target `end`.

## 7. User input
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 0; mov rdi, 0; lea rsi, [buf]; mov rdx, MAXLEN; syscall}>>mco:{01110010 01100101 01100001 01100100}<<>><<into>>$x>>/>>
```
- Read takes no arguments, and the result must go `<<into>>` an existing variable.
- Only whole numbers can be typed. The user types them **normally** (not upside down).
- There is no built-in question. Print one first.
- Like the real `read` syscall, it takes at most `MAXLEN` characters, **including the Enter key**.
  Anything extra stays waiting and is taken by the **next** read. Typing `123` with `MAXLEN` 2
  gives 12, and the next read gets `3`.
- A leftover Enter on its own is not a number, so it's an error.
- The value must fit the variable's type.

Because print's `LENGTH` must be exact, printing a number the user typed means checking its
size with if-statements first. See `examples/double.pmeh`.

## 8. Functions
### Defining
```
<<definefunc<USER>>mco:{NAME IN BINARY}params:{rdi:$a,rsi:$b}>>
    ...body...
    <<return<<asm:{ret}>>$result>>/>>
<<enddefine>>/>>
```
- All definitions must be at the **top** of the program, before any other code.
  They are skipped until called. No functions inside functions.
- Parameters use the real Linux x86-64 registers, **in order**: `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9`
  (6 at most). `params:{}` means no parameters. Parameters are always `int64`.
- A function must `return` a value. Reaching `<<enddefine>>/>>` is a punishment.
- No global variables. A function only sees its parameters and its own variables.
- A function can't use a built-in's name.

### Calling
```
<<startfunc<FROMUSER><<asm:{call NAME}>>mco:{NAME IN BINARY}<<>>$x,1<<into>>$y>>/>>
```
- The name in `call NAME` must match the binary name.
- The number of arguments must match the parameters. The result must go `<<into>>` a variable.
- While a function runs, the caller's variables are **frozen**: they don't age.
- Recursion is allowed, up to 50 calls deep. After that: stack overflow.

## 9. Lists
```
<<startlist<HEAP>>type:{int64}name:{xs}bytes:{91}>>/>>
```
- The size is in **bytes**, not items (`91` = 16 bytes = 2 `int64` items). Item sizes:
  `int8` 1, `int16` 2, `int32` 4, `int64` 8. The size must divide evenly into items.
- The size is fixed forever, and there's no way to ask for it. Remember it yourself.
- Items are reached by **byte offset**: for `int64`, item 0 is at 0, item 1 at 8, item 2 at 16 (`91`).
  Offsets are upside-down numbers too, so offsets with 3, 4 or 7 (like 24 and 32) must be calculated.

Write and read:
```
<<startfunc<FROMBUILTIN><<asm:{mov [rbx + rcx], rax}>>mco:{01110011 01110100 01101111 01110010 01100101}<<>>$xs,8,$x>>/>>
<<startfunc<FROMBUILTIN><<asm:{mov rax, [rbx + rcx]}>>mco:{01101100 01101111 01100001 01100100}<<>>$xs,8<<into>>$y>>/>>
```
- An offset that isn't a multiple of the item size: *misaligned memory access*.
- An offset outside the list: *Segmentation fault (core dumped)*.
- **Garbage:** items you never stored hold whatever was lying around in memory: a random
  number, or junk like `banana` or `notexisten<<>>`. Junk can be loaded, stored and printed,
  but doing math or comparisons with it is a punishment. Every run is different.
- The whole list forgets at once: touching any item keeps it alive.
- Lists are passed to functions **by reference**: the function changes the real list.
- Lists can't be printed, copied into variables, or used in math. Work with items.

## 10. Text
Text works like in C: a buffer of bytes, and the text ends at a special 0 byte, the **terminator**.

```
<<startvar<STACK>>type:{char[91]}name:{s}<<>>[0null]"OLLEH">>/>>
```
- `char[N]` is a buffer of N bytes (N is an upside-down number: `91` = 16).
- **Text is written backwards.** `[0null]"OLLEH"` means `HELLO` followed by the terminator.
  The whole thing is reversed, so the terminator, which ends the real text, comes first.
- Spaces are `[Nword]`, and **N counts in the real (forwards) text**:
  `[0null]"UOY"[2word]"IH"` is `HI YOU`.
- `<<>>` with nothing after it leaves the buffer full of garbage. `<<>>$t` copies another text.
- Text in `print` is still written forwards. Only text variables are backwards.

### Things C does that PMEH does too
- **No terminator?** Nothing stops you. Reading the text (print, strlen…) keeps going past the end
  into garbage: `HIbanana@i-wanna-cry…`, different every run.
- **Too long for the buffer?** Nothing stops you either. The extra bytes silently overwrite the
  **variables declared after it**: text gets overwritten, numbers become garbage, list items too.
  If the overflow goes past your last variable: `*** stack smashing detected ***`.

### C library functions
```
<<startfunc<FROMLIBC><<asm:{call strlen}>>mco:{01110011 01110100 01110010 01101100 01100101 01101110}<<>>$s<<into>>$n>>/>>
```
The name after `call` must match the binary name (get it with `pmeh -m strlen`).

| Function | Arguments | Does | Result |
|---|---|---|---|
| `strlen` | text | length, without the terminator | number |
| `strcat` | text, text | glues the second onto the end of the first (can overflow!) | nothing |
| `strcmp` | text, text | compares | **0 if equal**, -1 or 1 otherwise |
| `atoi` | text | text to number (`"12"` → 12). Not a number? **0**, like C | number |
| `itoa` | number | number to text | text |
| `charat` | text, byte | the character at that byte, as its ASCII code (`A` → 65) | number |

- Digits inside text are not upside down: the text `12` is twelve.
- `read` into a text variable stores what was typed (without Enter) plus a terminator.
- Text is passed to functions **by reference**, like lists.
- Text can't be used in math or comparisons. Use `atoi` / `strcmp`.

## 11. Comments
```
<<COMMNT"SIHT NI SEOG TNEMMOC A">>/>>
```
- Comment text is written **backwards**, like text. That one says "A COMMENT GOES IN THIS".
- A comment **counts as a line** for forgetful variables. Adding a comment can make a variable
  go 3 lines untouched, so explaining your code can break it.
- Comments may go before function definitions.

## 12. Remainder and random numbers
**`mod`** gives the remainder of a division, with the real asm (x86-64 leaves the remainder in `rdx`):
```
<<startfunc<FROMBUILTIN><<asm:{cqo; idiv rbx; mov rax, rdx}>>mco:{01101101 01101111 01100100}<<>>$x,5<<into>>$r>>/>>
```
`$x,5` is x mod 2. Like C, the remainder has the sign of the first number: -7 mod 2 is **-1**.

**`rand`** uses the real Linux `getrandom` system call (number 318):
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 318; lea rdi, [buf]; mov rsi, 8; mov rdx, 0; syscall}>>mco:{01110010 01100001 01101110 01100100}<<>><<into>>$x>>/>>
```
- `mov rsi, BYTES` must match the variable: 8 for `int64`, 4 for `int32`, 2 for `int16`, 1 for `int8`.
- The result is random bytes, so it covers the **whole range of the type, negatives included**.
  To get 1 to 6: `mod` by 6, make it positive, add 1. See `examples/dice.pmeh`.
- `pmeh -s N` makes the random numbers repeat.

## 13. Files
Files use the real Linux system calls. A **file descriptor** is a number that stands for an open
file: 0 is the keyboard, 1 the screen, 2 the error output, and files you open get 3, 4, 5…

### open
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 2; lea rdi, [path]; mov rsi, 577; mov rdx, 420; syscall}>>mco:{01101111 01110000 01100101 01101110}<<>>$path<<into>>$fd>>/>>
```
- The file name is a text variable (`$path`), so it's written backwards: `[0null]"txt.yraid"`.
- `FLAGS` is the real Linux number, made by adding these together:

  | Number | Meaning |
  |---|---|
  | 0 | read only |
  | 1 | write only |
  | 2 | read and write |
  | 64 | create the file if it doesn't exist |
  | 512 | empty the file first |
  | 1024 | add to the end of the file |

  So 577 = 1 + 64 + 512 (write, create, empty first) and 1089 = 1 + 64 + 1024 (append).
- `MODE` is the permissions for a new file, in **decimal**. The usual `0644` (rw-r--r--) is 420.
- Like the real syscall, open **doesn't punish you when it fails**. It gives a negative error
  number instead: -2 (no such file), -13 (permission denied), -22 (bad flags)… Check it yourself.

### write
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 1; mov rdi, [fd]; lea rsi, [msg]; mov rdx, 2; syscall}>>mco:{01110111 01110010 01101001 01110100 01100101}<<>>$fd,"HI"<<into>>$n>>/>>
```
- The file descriptor comes first, then the text, written like print.
- Unlike print, write **adds no newline**. Use `[Nline]`.
- `LENGTH` must be exact. The result is how many bytes were written, or -9 for a bad file descriptor.
- Writing to file descriptor 1 writes to the screen, without a newline.

### read from a file
The same `read` as for the keyboard, with `mov rdi, [fd]` and the file descriptor as an argument:
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 0; mov rdi, [fd]; lea rsi, [buf]; mov rdx, 22; syscall}>>mco:{01110010 01100101 01100001 01100100}<<>>$fd<<into>>$page>>/>>
```
- It reads up to `MAXLEN` bytes, **newlines included**, and carries on from there next time.
- At the end of the file it reads nothing: a text variable gets empty text, a number is a punishment.
- Reading a file descriptor that isn't open is a punishment.

### close
```
<<startfunc<FROMBUILTIN><<asm:{mov rax, 3; mov rdi, [fd]; syscall}>>mco:{01100011 01101100 01101111 01110011 01100101}<<>>$fd<<into>>$fd>>/>>
```
Gives 0 if it worked, -9 if that file wasn't open. **Ending the program with a file still open is
a punishment.**

## 14. Decimals
Two decimal types, with the real x86-64 floating-point instructions: `float64` (a C `double`)
and `float32` (a C `float`, with less precision).

```
<<startvar<STACK>>type:{float64}name:{x}<<>>1.0>>/>>
<<startfunc<FROMBUILTIN><<asm:{addsd xmm0, xmm1}>>mco:{01100001 01100100 01100100}<<>>$x,5.0<<into>>$x>>/>>
```
- **Decimals are upside down too.** The whole number is reversed and every digit flipped:
  `1.0` means 0.1, `5.0` means 0.2, `25.1` means 1.25, `0.1` means 1.0. `pmeh -m 0.1` tells you.
  A decimal always has a point and digits on both sides, so `0` is a whole number and `0.0` a decimal.
- **The asm decides the kind of math.** `add` with `add rax, rbx` adds whole numbers;
  `addsd xmm0, xmm1` adds float64 and `addss xmm0, xmm1` adds float32. Likewise `subsd`/`subss`,
  `mulsd`/`mulss` and `divsd`/`divss`. There is no decimal `mod`, because there is no such instruction.
- **No mixing.** Whole numbers, float64 and float32 never mix: not in math, not in comparisons, not
  in variables. Written decimals fit either size. Lists hold whole numbers only.
- **Dividing by zero is not punished**, because real hardware doesn't complain either:
  `inf`, `-inf`, or `nan` for 0/0.
- **Printing** always uses 17 significant digits, like C's `%.17g`: `0.1 + 0.2` prints
  `0.30000000000000004`, `0.1` as a float32 prints `0.10000000149011612`, and `1.0` prints `1`.
  The print `LENGTH` must still be exact.
- Reading decimals from the keyboard isn't possible. Read a whole number and convert it.

### Comparing decimals
```
<<startif<<asm:{ucomisd xmm0, xmm1; jae skip}>>$x,0.1>>
```
Decimals are compared with `ucomisd` (float64) or `ucomiss` (float32), and the real jumps that go
with them: `ja`, `jae`, `jb`, `jbe`, `je`, `jne`. `jg`, `jl` and friends are a punishment.
As always, the jump says when to **skip**, so `jae` here means "if x < 1.0".

`nan` behaves like the real thing: a comparison with `nan` sets the "equal" and "below" flags at
once, so `je`, `jb` and `jbe` are taken, and `jne`, `ja` and `jae` are not.

### Converting
```
<<startfunc<FROMBUILTIN><<asm:{cvtsi2sd xmm0, rax}>>mco:{01100011 01110110 01110100}<<>>$n<<into>>$x>>/>>
```

| asm | from | to |
|---|---|---|
| `cvtsi2sd xmm0, rax` | whole number | float64 |
| `cvtsi2ss xmm0, rax` | whole number | float32 |
| `cvttsd2si rax, xmm0` | float64 | whole number (the decimals are chopped off) |
| `cvttss2si rax, xmm0` | float32 | whole number |
| `cvtss2sd xmm0, xmm0` | float32 | float64 |
| `cvtsd2ss xmm0, xmm0` | float64 | float32 |

A decimal that doesn't fit in a whole number (too big, `inf` or `nan`) becomes
-9223372036854775808, the value real x86 gives ("integer indefinite").

## 15. Imports
```
<<import<FILE>>path:{01101000 01100101 01101101 01110000 00101110 01110100 01110010 01100101 01110110 01101110 01101111 01100011 00101111 01100010 01101001 01101100}>>/>>
```
- The path is the file name **written backwards, then in binary**. That one is `hemp.trevnoc/bil`,
  so it imports `lib/convert.pmeh`. The path is relative to the importing file.
- Imports go at the very top of a file, before anything else except comments.
- An imported file may only contain functions (and comments, and its own imports), and it must
  end with its own validation line.
- Importing the same file twice, or files importing each other in a circle, is a punishment.
- **Line numbers count the glued program.** Each imported file is pasted where its import line
  was, and punishments count lines of the whole glued result (from the bottom, as usual). Only
  `-g` tells you which file and line that really is.

See `examples/temperature.pmeh`, which imports `examples/lib/convert.pmeh`.

## 16. Error reports
Every error report contains everything you need. None of it is easy to read.

- **Location:** the line number is counted **from the bottom** of the file (blank lines count),
  and written **upside down**. If it can't be written (3, 4, 7), it's shown as a sum:
  `5 <3 1` means 2 + 1 = line 3 from the bottom.
- **Reason:** the message is written **backwards** and then **ROT13**-encoded, and every letter
  has a random dim colour. Some letters are black on black (copy and paste them to see them).
- **"This line is definitely NOT the problem":** a random line from your program that is
  not the problem. It's there to waste your time.
- The insult is the only part in bright, readable red.

Cowards can run `python pmeh.py --i-give-up program.pmeh` for a plain error message.
