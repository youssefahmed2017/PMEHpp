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
| mul | `imul rax, rbx` | `01101101 01110101 01101100` |
| div | `cqo; idiv rbx` (rounds toward zero) | `01100100 01101001 01110110` |

Math arguments: `$x,1`. The first goes in `rax`, the second in `rbx`.

### Print
- Print accepts `"TEXT"`, `[Nword]` and `$variables`, glued together with nothing in between.
- Text may not contain real spaces. A space is `[Nword]`, where N is its position in the output, counting from 0.
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

## 11. Error reports
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
