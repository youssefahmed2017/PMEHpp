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
from test_pmeh import (ADD, CALL, CLOSE, COMMENT, DEF, END, FREAD, LIST, LOAD, MOD, MUL, OPEN,  # noqa: E402
                       PATH, PRINT, RAND, READ, RET, SQ, STORE, TEXT, VAR, WRITE, libc, prog,
                       square_def)

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

    def both(self, source, typed="", files=None, same_output=True):
        files = files or {}
        path = os.path.join(self.tmp.name, "program.pmeh")
        exe = os.path.join(self.tmp.name, "program")
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
        return self.both((ROOT / "examples" / f"{name}.pmeh").read_text(), typed)

    # ---- examples

    def test_examples(self):
        for name in ("hello", "countdown", "three", "big_or_small", "square", "factorial",
                     "sum_list", "text", "overflow", "diary"):
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

    def test_rand(self):
        self.both(prog(VAR % ("x", "0"), RAND % (4, "x")))
        out = self.both(prog(INT8 % ("x", "0"), RAND % (1, "x"), VAR % ("y", "$x"))).returncode
        self.assertEqual(out, 0)


if __name__ == "__main__":
    unittest.main()
