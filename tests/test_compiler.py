"""The compiler must behave exactly like the interpreter.

Each case runs through both (with plain -g errors) and must give the same output, the same
exit code, and the same punished line. Skipped where `as`/`ld` or x86-64 Linux are missing.
"""
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import pmeh  # noqa: E402
from test_pmeh import (ADD, CALL, CLOSE, COMMENT, DEF, END, FREAD, FVAR, LIST, LOAD, MOD, MUL,  # noqa: E402
                       OPEN, PATH, PRINT, RAND, READ, RET, SQ, STORE, TEXT, VAR, WRITE, cvt, fif, fop,
                       libc, prog, square_def)

CAN_COMPILE = (sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64")
               and shutil.which("as") and shutil.which("ld"))
DIV = "<<startfunc<FROMBUILTIN><<asm:{cqo; idiv rbx}>>mco:{01100100 01101001 01110110}<<>>%s<<into>>$%s>>/>>"
SUB = "<<startfunc<FROMBUILTIN><<asm:{sub rax, rbx}>>mco:{01110011 01110101 01100010}<<>>%s<<into>>$%s>>/>>"
LOOP = "<<startloop<<asm:{cmp rax, rbx; %s end}>>%s>>"
ENDLOOP = "<<endloop>>/>>"
IF = "<<startif<<asm:{cmp rax, rbx; %s skip}>>%s>>"
ELSE, ENDIF = "<<else>>", "<<endif>>/>>"
INT8 = "<<startvar<STACK>>type:{int8}name:{%s}<<>>%s>>/>>"


def punished_line(stderr):
    m = re.search(r"PUNISHMENT on line (\d+)", stderr)
    return int(m[1]) if m else None


@unittest.skipUnless(CAN_COMPILE, "needs x86-64 Linux with as and ld")
class CompilerMatchesInterpreter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def workdir(self, name, files):
        """A fresh folder holding only `files`, so file programs start the same way."""
        folder = pathlib.Path(self.tmp.name) / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        for file_name, content in files.items():
            (folder / file_name).write_text(content)
        return folder

    @staticmethod
    def contents(folder):
        return {p.name: p.read_text() for p in sorted(folder.iterdir())}

    def both(self, source, typed="", files=None, same_output=True, path=None):
        files = files or {}
        exe = os.path.join(self.tmp.name, "program")
        if path is None:
            path = os.path.join(self.tmp.name, "program.pmeh")
            with open(path, "w") as f:
                f.write(source)
        folder = self.workdir("interpreter", files)
        interp = subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", path],
                                input=typed, capture_output=True, text=True, cwd=folder)
        interp_files = self.contents(folder)
        built = subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", "-o", exe, path],
                               capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        folder = self.workdir("compiled", files)
        native = subprocess.run([exe], input=typed, capture_output=True, text=True, cwd=folder)
        self.assertEqual(self.contents(folder), interp_files)
        if same_output:
            self.assertEqual(native.stdout, interp.stdout)
        self.assertEqual(native.returncode, interp.returncode, native.stderr + interp.stderr)
        self.assertEqual(punished_line(native.stderr), punished_line(interp.stderr),
                         "\ncompiled: " + native.stderr + "\ninterpreter: " + interp.stderr)
        return native

    def example(self, name, typed=""):
        return self.both(None, typed, path=str(ROOT / "examples" / f"{name}.pmeh"))

    # ---- examples

    def test_examples(self):
        for name in ("hello", "countdown", "three", "big_or_small", "square", "factorial",
                     "sum_list", "text", "overflow", "diary", "decimals", "temperature"):
            with self.subTest(name):
                self.example(name)

    def test_double(self):
        for typed in ("4\n", "15\n", "99\n", "abc\n", "", "\n", "150\n"):
            with self.subTest(typed):
                self.example("double", typed)

    def test_guess(self):
        # the secret is random, so check the game's rules instead of comparing outputs
        exe = os.path.join(self.tmp.name, "guess")
        subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", "-o", exe,
                        str(ROOT / "examples" / "guess.pmeh")], check=True)
        counting_up = "".join(f"{n}\n" for n in range(1, 100))
        secrets = set()
        for _ in range(20):
            lines = subprocess.run([exe], input=counting_up, capture_output=True, text=True).stdout.splitlines()
            secret = int(lines[-1])
            self.assertTrue(1 <= secret <= 99)
            self.assertEqual(lines.count("TOO LOW"), secret - 1)
            secrets.add(secret)
        self.assertGreater(len(secrets), 5)
        self.example("guess", "")                           # no input at all: same punishment

    def test_dice_rolls_one_to_six(self):
        # random, so the two can't be compared: just check the compiled die is a fair-looking die
        exe = os.path.join(self.tmp.name, "dice")
        subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-o", exe,
                        str(ROOT / "examples" / "dice.pmeh")], check=True)
        rolls = {subprocess.run([exe], capture_output=True, text=True).stdout for _ in range(80)}
        self.assertEqual(rolls, {f"ROLLED: {n}\n" for n in range(1, 7)})

    # ---- punishments happen on the same line

    def test_forgotten_variable(self):
        self.both(prog(VAR % ("x", "2"), VAR % ("a", "1"), VAR % ("b", "1"), VAR % ("c", "1"),
                       PRINT % (1, "$x")))

    def test_comments_count_as_lines(self):
        self.both(prog(VAR % ("x", "1"), COMMENT % "A", COMMENT % "B", COMMENT % "C", PRINT % (1, "$x")))

    def test_never_existed(self):
        self.both(prog(PRINT % (1, "$nope")))

    def test_frozen_during_call(self):
        long_body = [VAR % ("p", "$a"), VAR % ("q", "$a"), VAR % ("r", "$a"), VAR % ("s", "$a"),
                     MUL % ("$a,$a", "a"), RET % "$a"]
        self.both(prog(*square_def(body=long_body), VAR % ("keep", "1"), VAR % ("x", "5"),
                       CALL % ("square", SQ, "$x", "x"), PRINT % (1, "$keep")))

    def test_no_globals(self):
        self.both(prog(*square_def(body=[MUL % ("$a,$x", "a"), RET % "$a"]), VAR % ("x", "1"),
                       CALL % ("square", SQ, "$x", "x")))

    def test_two_parameters(self):
        add_name = "01100001 01100100 01100100 01011111"
        self.both(prog(DEF % (add_name, "rdi:$a,rsi:$b"), ADD % ("$a,$b", "a"), RET % "$a", END,
                       VAR % ("x", "0"), CALL % ("add_", add_name, "5,6", "x"), PRINT % (2, "$x")))

    def test_stack_overflow(self):
        self.both(prog(*square_def(body=[CALL % ("square", SQ, "$a", "a"), RET % "$a"]),
                       VAR % ("x", "1"), CALL % ("square", SQ, "$x", "x")))

    def test_missing_return(self):
        self.both(prog(*square_def(body=[MUL % ("$a,$a", "a")]), VAR % ("x", "1"),
                       CALL % ("square", SQ, "$x", "x")))

    def test_overflows(self):
        self.both(prog(INT8 % ("x", "666")))
        self.both(prog(INT8 % ("x", "001"), MUL % ("$x,5", "x")))                   # 100 * 2 > 127
        self.both(prog(VAR % ("x", "2"), MUL % ("$x,$x", "x"), MUL % ("$x,$x", "x"),
                       MUL % ("$x,$x", "x"), MUL % ("$x,$x", "x"), MUL % ("$x,$x", "x"),
                       MUL % ("$x,$x", "x"), MUL % ("$x,$x", "x")))                 # 5^128

    def test_division_and_mod(self):
        for a, b in (("6", "5"), ("-6", "5"), ("6", "-5"), ("01", "5"), ("1", "0")):
            with self.subTest(a=a, b=b):
                self.both(prog(VAR % ("x", a), DIV % (f"$x,{b}", "x"), PRINT % (2, "$x")))
                self.both(prog(VAR % ("x", a), MOD % (f"$x,{b}", "x"), PRINT % (2, "$x")))

    def test_print_mistakes(self):
        self.both(prog(PRINT % (3, '"HI"')))
        self.both(prog(PRINT % (5, '"HI"[1word]"YO"')))
        self.both(prog(PRINT % (3, '"A"[1line]"B"')))
        self.both(prog(VAR % ("x", "-21"), PRINT % (3, "$x")))

    def test_loops_and_ifs(self):
        self.both(prog(VAR % ("x", "01"), LOOP % ("jle", "$x,0"),
                       IF % ("jne", "$x,8"), PRINT % (5, '"EIGHT"'), ELSE, PRINT % (1, "$x"), ENDIF,
                       SUB % ("$x,1", "x"), ENDLOOP))

    def test_infinite_loop(self):
        self.both(prog(VAR % ("x", "1"), LOOP % ("je", "$x,0"), ENDLOOP))

    def test_write(self):
        self.both(prog(VAR % ("n", "0"), WRITE(2, '1,"HI"'), WRITE(2, '1,"YO"'), PRINT % (1, "$n")))
        self.both(prog(VAR % ("n", "0"), WRITE(2, '8,"HI"'), PRINT % (2, "$n")))

    def test_read_leftovers(self):
        src = prog(VAR % ("n", "0"), READ % (2, "n"), PRINT % (2, "$n"), READ % (2, "n"), PRINT % (1, "$n"))
        self.both(src, "123\n")
        self.both(src, "12\n34\n")

    # ---- lists

    def test_list_offsets(self):
        self.both(prog(LIST % ("int64", "91"), STORE % "$xs,8,21", VAR % ("a", "0"),
                       LOAD % ("$xs,8", "a"), PRINT % (2, "$a")))
        self.both(prog(LIST % ("int8", "5"), STORE % "$xs,1,9", VAR % ("a", "0"),
                       LOAD % ("$xs,1", "a"), PRINT % (1, "$a")))

    def test_list_mistakes(self):
        for src in (
            prog(LIST % ("int64", "91"), VAR % ("a", "0"), LOAD % ("$xs,91", "a")),       # segfault
            prog(LIST % ("int64", "91"), VAR % ("a", "0"), LOAD % ("$xs,-8", "a")),       # segfault
            prog(LIST % ("int64", "91"), VAR % ("a", "0"), LOAD % ("$xs,5", "a")),        # misaligned
            prog(LIST % ("int64", "21")),                                                  # 15 bytes
            prog(LIST % ("int64", "0")),
            prog(LIST % ("int64", "8"), PRINT % (1, "$xs")),
            prog(LIST % ("int64", "8"), VAR % ("a", "0"), ADD % ("$xs,1", "a")),
            prog(LIST % ("int64", "8"), VAR % ("a", "0"), VAR % ("b", "0"), VAR % ("c", "0"),
                 LOAD % ("$xs,0", "a")),                                                   # forgotten
            prog(LIST % ("int8", "8"), STORE % "$xs,0,666"),                               # overflow
            prog(LIST % ("int64", "8"), STORE % "$xs,0,$xs"),
            prog(LIST % ("int64", "8"), VAR % ("a", "1"), LOAD % ("$a,0", "a")),
            prog(LIST % ("int64", "8"), LOAD % ("$xs,0", "xs")),
            prog(LIST % ("int64", "8"), VAR % ("a", "$xs")),
        ):
            with self.subTest(src.splitlines()[-2]):
                self.both(src)

    def test_list_size_from_a_variable(self):
        self.both(prog(VAR % ("n", "91"), LIST % ("int64", "$n"), STORE % "$xs,8,6",
                       VAR % ("a", "0"), LOAD % ("$xs,8", "a"), PRINT % (1, "$a")))

    def test_lists_pass_by_reference(self):
        fill = "01100110 01101001 01101100 01101100"
        self.both(prog(DEF % (fill, "rdi:$l"), STORE % "$l,0,6", RET % "0", END,
                       LIST % ("int64", "8"), VAR % ("r", "0"), CALL % ("fill", fill, "$xs", "r"),
                       LOAD % ("$xs,0", "r"), PRINT % (1, "$r")))

    def test_garbage_items(self):
        # an item never stored is a random number or junk: math on it works or is punished
        src = prog(LIST % ("int64", "8"), VAR % ("a", "0"), LOAD % ("$xs,0", "a"), ADD % ("$a,0", "a"))
        exe = os.path.join(self.tmp.name, "garbage")
        path = os.path.join(self.tmp.name, "garbage.pmeh")
        pathlib.Path(path).write_text(src)
        subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", "-o", exe, path], check=True)
        results = {subprocess.run([exe], capture_output=True, text=True).returncode for _ in range(40)}
        self.assertEqual(results, {0, 1})

    # ---- text

    def test_text_functions(self):
        self.both(prog(TEXT % ("8", "s", '[0null]"21"'), VAR % ("n", "0"),
                       libc("atoi", "$s", "n"), PRINT % (2, "$n"),
                       libc("strlen", "$s", "n"), PRINT % (1, "$n"),
                       libc("charat", "$s,0", "n"), PRINT % (2, "$n"),
                       TEXT % ("8", "t", '[0null]"21"'), libc("strcmp", "$s,$t", "n"), PRINT % (1, "$n"),
                       libc("itoa", "$n", "t"), PRINT % (1, "$t")))
        self.both(prog(TEXT % ("8", "s", '[0null]"ANANAB"'), VAR % ("n", "1"),
                       libc("atoi", "$s", "n"), PRINT % (1, "$n")))
        self.both(prog(TEXT % ("8", "a", '[0null]"BA"'), TEXT % ("8", "b", '[0null]"CA"'), VAR % ("n", "0"),
                       libc("strcmp", "$a,$b", "n"), PRINT % (2, "$n"), libc("strcmp", "$b,$a", "n"),
                       PRINT % (1, "$n")))

    def test_text_mistakes(self):
        overflow = [TEXT % ("8", "s", '[0null]"OLLEH"'), VAR % ("n", "1"),
                    TEXT % ("8", "t", '[0null]"CBA"'), libc("strcat", "$s,$t")]
        for src in (
            prog(TEXT % ("2", "s", '"IH"'), PRINT % (2, "$s")),                 # no terminator
            prog(*overflow, PRINT % (1, "$n")),                                # \0 spilled into $n
            prog(*overflow, ADD % ("$n,1", "n")),
            prog(VAR % ("n", "1"), TEXT % ("5", "s", '[0null]"OLLEH"')),        # stack smashing
            prog(TEXT % ("8", "s", '[0null]"IH"'), VAR % ("n", "0"), ADD % ("$s,1", "n")),
            prog(TEXT % ("8", "s", '[0null]"IH"'), VAR % ("n", "0"), libc("charat", "$s,8", "n")),
            prog(TEXT % ("8", "s", '[0null]"IH"'), VAR % ("n", "0"), libc("itoa", "5", "n")),
            prog(VAR % ("n", "0"), libc("strlen", "$n", "n")),
            prog(TEXT % ("8", "s", ""), RAND % (8, "s")),
            prog(TEXT % ("8", "s", '[0null]"IH"'), TEXT % ("8", "t", "$s"), PRINT % (2, "$t")),
        ):
            with self.subTest(src.splitlines()[-2]):
                self.both(src)

    def test_overflow_into_a_list(self):
        # $s holds 2 bytes: "IABCDEF" + 0 keeps "IA" and spills "BCDEF" + 0 into the list after it
        out = self.both(prog(TEXT % ("8", "t", '[0null]"FEDCBA"'), TEXT % ("5", "s", '[0null]"I"'),
                             LIST % ("int16", "8"), libc("strcat", "$s,$t"), VAR % ("a", "0"),
                             LOAD % ("$xs,0", "a"), PRINT % (2, "$a")))
        self.assertEqual(out.stdout, "BC\n")

    def test_read_text(self):
        src = prog(TEXT % ("8", "s", ""), READ % (5, "s"), PRINT % (3, "$s"))
        self.both(src, "Bob\n")
        self.both(src, "\n")

    def test_text_by_reference(self):
        shout = " ".join(f"{ord(c):08b}" for c in "shout")
        self.both(prog(DEF % (shout, "rdi:$x,rsi:$y"), libc("strcat", "$x,$y"), RET % "0", END,
                       TEXT % ("8", "a", '[0null]"IH"'), TEXT % ("8", "b", '[0null]"!"'), VAR % ("r", "0"),
                       CALL % ("shout", shout, "$a,$b", "r"), PRINT % (3, "$a")))

    # ---- files

    def test_files(self):
        start = [VAR % ("fd", "0"), VAR % ("n", "0"), PATH]
        for src, files in (
            (prog(*start, OPEN(577), WRITE(2, '$fd,"HI"'), CLOSE(), PRINT % (1, "$n")), {}),
            (prog(*start, OPEN(1089), WRITE(1, '$fd,"B"'), CLOSE()), {"test.txt": "A"}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(0), PRINT % (2, "$fd")), {}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(3), PRINT % (3, "$fd")), {}),
            (prog(VAR % ("fd", "8"), VAR % ("n", "0"), WRITE(2, '$fd,"HI"'), PRINT % (2, "$n")), {}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(0), TEXT % ("8", "t", ""),
                  FREAD(2, "t"), PRINT % (2, "$t"), FREAD(2, "t"), PRINT % (1, "$t"),
                  FREAD(2, "t"), PRINT % (0, "$t"), CLOSE()), {"test.txt": "ABC"}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(0), VAR % ("x", "0"), FREAD(8, "x"), CLOSE()),
             {"test.txt": "42"}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(0), VAR % ("x", "0"), FREAD(8, "x"), CLOSE()),
             {"test.txt": "42\n"}),
            (prog(VAR % ("fd", "8"), TEXT % ("8", "t", ""), FREAD(2, "t")), {}),
            (prog(VAR % ("fd", "0"), PATH, OPEN(577)), {}),
            (prog(VAR % ("fd", "0"), CLOSE(), PRINT % (2, "$fd")), {}),
        ):
            with self.subTest(src.splitlines()[-2]):
                self.both(src, files=files)

    # ---- decimals

    def test_decimal_math_and_printing(self):
        for a, b, op, suffix in (("1.0", "5.0", "add", "sd"), ("1.0", "5.0", "add", "ss"),
                                 ("0.1", "0.0", "div", "sd"), ("0.0", "0.0", "div", "sd"),
                                 ("-0.1", "0.0", "div", "ss"), ("0.1", "-0.0", "div", "sd"),
                                 ("8.6", "1.0", "mul", "sd"), ("1.0", "1.0", "sub", "sd"),
                                 ("5.1", "1.0", "sub", "ss"), ("1.0", "0.0", "mul", "sd")):
            kind, bits = ("float64", 64) if suffix == "sd" else ("float32", 32)
            x, y = (pmeh.flip_decimal(v) for v in (a, b))
            if bits == 32:
                x, y = pmeh.round32(x), pmeh.round32(y)
            result = pmeh.float_op(op, x, y)
            text = pmeh.show_decimal(pmeh.round32(result) if bits == 32 else result)
            src = prog(FVAR % (kind, "x", a), fop(op, f"$x,{b}", "x", suffix), PRINT % (len(text), "$x"))
            with self.subTest(a=a, b=b, op=op, suffix=suffix):
                self.assertEqual(self.both(src).stdout, text + "\n")

    def test_printing_thousands_of_decimals_like_c(self):
        """The compiled program works out decimal digits itself, with big-number arithmetic in asm.
        Feed it 3,000 decimals across every exponent and compare each line with %.17g."""
        import math
        import random
        import struct
        import pmehc
        rng = random.Random(5)
        values = [0.1, 1.0, 1e16, 1e17, 1e20, 1e-4, 1e-5, 5e-324, 1.7976931348623157e308, -0.0, 0.0,
                  2.2250738585072014e-308, 99999999999999999.0, 1 / 3, math.inf, -math.inf, math.nan]
        while len(values) < 2000:
            x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
            if not math.isnan(x):
                values.append(x)
        values += [rng.uniform(-1000, 1000) * 10 ** rng.randint(-8, 8) for _ in range(1000)]
        program = pmeh.parse(prog(FVAR % ("float64", "x", "0.0")))
        asm = pmehc.Compiler(program, "", coward=True).compile()
        printing = []
        for x in values:
            printing += ["mov qword ptr [rip + outlen], 0", f"mov rax, {pmehc.bits_of(x)}", "call append_float",
                         "mov al, 10", "call append_char", "mov rax, 1", "mov rdi, 1",
                         "mov rsi, qword ptr [rip + outptr]", "mov rdx, qword ptr [rip + outlen]", "syscall"]
        asm = asm.replace("\nL1:\n", "\n" + "\n".join("    " + line for line in printing) + "\nL1:\n", 1)
        base = os.path.join(self.tmp.name, "decimals")
        pathlib.Path(base + ".s").write_text(asm)
        subprocess.run(["as", "--64", "-o", base + ".o", base + ".s"], check=True)
        subprocess.run(["ld", "-o", base, base + ".o"], check=True)
        lines = subprocess.run([base], capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(lines, ["%.17g" % x for x in values])

    def test_nan_jumps(self):
        for jump in ("je", "jne", "jb", "jbe", "ja", "jae"):
            for a, b in (("$x,$x", "0.0"), ("0.1,0.5", None), ("0.5,0.1", None), ("0.1,0.1", None)):
                with self.subTest(jump=jump, a=a):
                    self.both(prog(FVAR % ("float64", "x", "0.0"), fop("div", "$x,0.0", "x"),
                                   fif(jump, a), PRINT % (3, '"RAN"'), "<<endif>>/>>", PRINT % (1, '"."')))

    def test_conversions(self):
        for src in (
            prog(VAR % ("n", "6"), FVAR % ("float64", "y", "0.0"), cvt("cvtsi2sd xmm0, rax", "$n", "y"),
                 PRINT % (1, "$y")),
            prog(FVAR % ("float64", "x", "6.5"), VAR % ("n", "0"), cvt("cvttsd2si rax, xmm0", "$x", "n"),
                 PRINT % (1, "$n")),
            prog(FVAR % ("float64", "x", "-6.5"), VAR % ("n", "0"), cvt("cvttsd2si rax, xmm0", "$x", "n"),
                 PRINT % (2, "$n")),
            prog(FVAR % ("float64", "x", "0.0"), fop("div", "$x,0.0", "x"), VAR % ("n", "0"),
                 cvt("cvttsd2si rax, xmm0", "$x", "n"), PRINT % (20, "$n")),
            prog(FVAR % ("float64", "x", "0.0000000000000000000005"), VAR % ("n", "0"),
                 cvt("cvttsd2si rax, xmm0", "$x", "n"), PRINT % (20, "$n")),     # 5e21: too big
            prog(FVAR % ("float64", "x", "1.0"), FVAR % ("float32", "y", "0.0"),
                 cvt("cvtsd2ss xmm0, xmm0", "$x", "y"), PRINT % (19, "$y")),
            prog(FVAR % ("float32", "x", "1.0"), FVAR % ("float64", "y", "0.0"),
                 cvt("cvtss2sd xmm0, xmm0", "$x", "y"), PRINT % (19, "$y")),
            prog(FVAR % ("float32", "x", "2.5"), VAR % ("n", "0"), cvt("cvttss2si rax, xmm0", "$x", "n"),
                 PRINT % (1, "$n")),
        ):
            with self.subTest(src.splitlines()[-2][:60]):
                self.both(src)

    def test_int_to_float32_rounds_once(self):
        # build 2^53 + 2^29 + 1 = (2^24 + 1) * 2^29 + 1, convert it to float32 and back, and print it.
        # Rounded straight to float32 (like the real cvtsi2ss) it becomes 2^53 + 2^30.
        src = prog(VAR % ("a", "8"), MUL % ("$a,$a", "a"), MUL % ("$a,$a", "a"), MUL % ("$a,$a", "a"),  # 2^24
                   VAR % ("b", "$a"), MUL % ("$b,8", "b"), MUL % ("$b,5", "b"),          # 5 means 2
                   ADD % ("$a,1", "a"), MUL % ("$b,5", "b"),                               # b = 2^29
                   MUL % ("$a,$b", "a"), ADD % ("$a,1", "a"),
                   FVAR % ("float32", "f", "0.0"), cvt("cvtsi2ss xmm0, rax", "$a", "f"),
                   VAR % ("d", "0"), cvt("cvttss2si rax, xmm0", "$f", "d"), PRINT % (16, "$d"))
        out = self.both(src)
        self.assertEqual(out.stdout, f"{2 ** 53 + 2 ** 30}\n")

    def test_decimal_mistakes(self):
        for src in (
            prog(FVAR % ("float64", "x", "0")),
            prog(VAR % ("x", "1.0")),
            prog(FVAR % ("float64", "x", "1.0"), FVAR % ("float32", "y", "$x")),
            prog(FVAR % ("float64", "x", "1.0"), fop("add", "$x,1", "x")),
            prog(FVAR % ("float32", "x", "1.0"), fop("add", "$x,$x", "x")),
            prog(FVAR % ("float64", "x", "1.0"), VAR % ("n", "0"), ADD % ("$x,1", "n")),
            prog(LIST % ("int64", "8"), STORE % "$xs,0,1.0"),
            prog(TEXT % ("5", "s", '[0null]"I"'), FVAR % ("float64", "x", "1.0"),
                 TEXT % ("8", "t", '[0null]"FEDCBA"'), libc("strcat", "$s,$t"), fop("add", "$x,$x", "x")),
        ):
            with self.subTest(src.splitlines()[-2][:60]):
                self.both(src)

    def test_decimal_parameters(self):
        dbl = " ".join(f"{ord(c):08b}" for c in "dbl")
        self.both(prog(DEF % (dbl, "rdi:$a"), fop("add", "$a,$a", "a"), RET % "$a", END,
                       FVAR % ("float64", "x", "1.0"), CALL % ("dbl", dbl, "$x", "x"), PRINT % (3, "$x")))

    # ---- imports

    def test_import_error_line_is_the_same(self):
        lib = pathlib.Path(self.tmp.name) / "lib" / "sq.pmeh"
        lib.parent.mkdir(exist_ok=True)
        lib.write_text("\n".join([*square_def(body=[MUL % ("$a,$a", "a")]), pmeh.VALIDATION_LINE]) + "\n")
        name = " ".join(f"{ord(c):08b}" for c in "lib/sq.pmeh"[::-1])
        out = self.both(prog("<<import<FILE>>path:{%s}>>/>>" % name, VAR % ("x", "5"),
                             CALL % ("square", SQ, "$x", "x")))
        self.assertIn("(lib/sq.pmeh, line 3)", out.stderr)

    def test_rand(self):
        self.both(prog(VAR % ("x", "0"), RAND % (4, "x")))
        out = self.both(prog(INT8 % ("x", "0"), RAND % (1, "x"), VAR % ("y", "$x"))).returncode
        self.assertEqual(out, 0)


if __name__ == "__main__":
    unittest.main()
