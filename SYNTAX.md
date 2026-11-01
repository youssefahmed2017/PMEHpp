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
