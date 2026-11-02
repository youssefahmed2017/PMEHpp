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
from test_pmeh import (ADD, CALL, DEF, END, MOD, MUL, PRINT, RAND, READ, RET, SQ, VAR,  # noqa: E402
                       COMMENT, WRITE, square_def, prog)

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

    def both(self, source, typed=""):
        path = os.path.join(self.tmp.name, "program.pmeh")
        exe = os.path.join(self.tmp.name, "program")
        with open(path, "w") as f:
            f.write(source)
        interp = subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", path],
                                input=typed, capture_output=True, text=True)
        built = subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-g", "-o", exe, path],
                               capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        native = subprocess.run([exe], input=typed, capture_output=True, text=True)
        self.assertEqual(native.stdout, interp.stdout)
        self.assertEqual(native.returncode, interp.returncode, native.stderr + interp.stderr)
        self.assertEqual(punished_line(native.stderr), punished_line(interp.stderr),
                         "\ncompiled: " + native.stderr + "\ninterpreter: " + interp.stderr)
        return native

    def example(self, name, typed=""):
        return self.both((ROOT / "examples" / f"{name}.pmeh").read_text(), typed)

    # ---- examples

    def test_examples(self):
        for name in ("hello", "countdown", "three", "big_or_small", "square", "factorial"):
            with self.subTest(name):
                self.example(name)

    def test_double(self):
        for typed in ("4\n", "15\n", "99\n", "abc\n", "", "\n", "150\n"):
            with self.subTest(typed):
                self.example("double", typed)

    def test_guess(self):
        self.assertIn("TRIES:\n3", self.example("guess", "50\n80\n64\n").stdout)
        self.example("guess", "1\n" * 11 + "64\n")
        self.example("guess", "50\n")                       # runs out of input

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

    def test_rand(self):
        self.both(prog(VAR % ("x", "0"), RAND % (4, "x")))
        out = self.both(prog(INT8 % ("x", "0"), RAND % (1, "x"), VAR % ("y", "$x"))).returncode
        self.assertEqual(out, 0)


@unittest.skipUnless(CAN_COMPILE, "needs x86-64 Linux with as and ld")
class CompilerLimits(unittest.TestCase):
    def test_unsupported_features_are_refused(self):
        result = subprocess.run([sys.executable, str(ROOT / "pmeh.py"), "-o", os.devnull,
                                 str(ROOT / "examples" / "sum_list.pmeh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't do lists yet", result.stderr)


if __name__ == "__main__":
    unittest.main()
