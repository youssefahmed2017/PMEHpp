"""Tests for the PunishMeEvenHarder++ interpreter. Run: python -m unittest discover tests"""
import io
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pmeh  # noqa: E402

V = pmeh.VALIDATION_LINE
PRINT = ("<<startfunc<FROMBUILTIN><<asm:{mov rax, 1; mov rdi, 1; lea rsi, [msg]; mov rdx, %d; syscall}>>"
         "mco:{01110000 01110010 01101001 01101110 01110100}<<>>%s>>/>>")


READ = ("<<startfunc<FROMBUILTIN><<asm:{mov rax, 0; mov rdi, 0; lea rsi, [buf]; mov rdx, %d; syscall}>>"
        "mco:{01110010 01100101 01100001 01100100}<<>><<into>>$%s>>/>>")


def run(source, typed=""):
    out = io.StringIO()
    pmeh.Machine(pmeh.parse(source), out, io.StringIO(typed)).run()
    return out.getvalue()


def run_file(name, typed=""):
    return run((ROOT / "examples" / name).read_text(), typed)


class Examples(unittest.TestCase):
    def test_hello(self):
        self.assertEqual(run_file("hello.pmeh"), "HELLO WORLD\n")

    def test_countdown(self):
        self.assertEqual(run_file("countdown.pmeh"), "5\n4\n3\n2\n1\n")

    def test_three(self):
        self.assertEqual(run_file("three.pmeh"), "THREE = 3\n")

    def test_big_or_small(self):
        self.assertEqual(run_file("big_or_small.pmeh"), "BIG\n")

    def test_double(self):
        for typed, answer in [("4\n", "8"), ("15\n", "30"), ("99\n", "198")]:
            self.assertEqual(run_file("double.pmeh", typed), "NUMBER?\n" + answer + "\n")


class Input(unittest.TestCase):
    VAR = "<<startvar<STACK>>type:{%s}name:{n}<<>>0>>/>>"

    def program(self, *lines, type_="int64"):
        return "\n".join([self.VAR % type_, *lines, pmeh.VALIDATION_LINE])

    def test_read_and_print(self):
        self.assertEqual(run(self.program(READ % (3, "n"), PRINT % (2, "$n")), "42\n"), "42\n")

    def test_typed_numbers_are_not_flipped(self):
        self.assertEqual(run(self.program(READ % (3, "n"), PRINT % (2, "$n")), "21\n"), "21\n")

    def test_leftover_goes_to_next_read(self):
        src = self.program(READ % (2, "n"), PRINT % (2, "$n"), READ % (2, "n"), PRINT % (1, "$n"))
        self.assertEqual(run(src, "123\n"), "12\n3\n")

    def test_leftover_enter_is_not_a_number(self):
        src = self.program(READ % (2, "n"), READ % (3, "n"))
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(src, "12\n34\n")
        self.assertIn("not a number", ctx.exception.message)

    def test_not_a_number(self):
        with self.assertRaises(pmeh.Punishment):
            run(self.program(READ % (5, "n")), "abc\n")

    def test_no_input(self):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(self.program(READ % (3, "n")), "")
        self.assertIn("no input left", ctx.exception.message)

    def test_overflow(self):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(self.program(READ % (4, "n"), type_="int8"), "300\n")
        self.assertIn("Overflow", ctx.exception.message)


SQ = "01110011 01110001 01110101 01100001 01110010 01100101"   # square
MUL = "<<startfunc<FROMBUILTIN><<asm:{imul rax, rbx}>>mco:{01101101 01110101 01101100}<<>>%s<<into>>$%s>>/>>"
ADD = "<<startfunc<FROMBUILTIN><<asm:{add rax, rbx}>>mco:{01100001 01100100 01100100}<<>>%s<<into>>$%s>>/>>"
DEF = "<<definefunc<USER>>mco:{%s}params:{%s}>>"
RET = "<<return<<asm:{ret}>>%s>>/>>"
END = "<<enddefine>>/>>"
CALL = "<<startfunc<FROMUSER><<asm:{call %s}>>mco:{%s}<<>>%s<<into>>$%s>>/>>"
VAR = "<<startvar<STACK>>type:{int64}name:{%s}<<>>%s>>/>>"


def square_def(params="rdi:$a", body=None):
    body = body if body is not None else [MUL % ("$a,$a", "a"), RET % "$a"]
    return [DEF % (SQ, params), *body, END]


def prog(*lines):
    return "\n".join([*lines, pmeh.VALIDATION_LINE])


class Functions(unittest.TestCase):
    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_square_example(self):
        self.assertEqual(run_file("square.pmeh"), "4\n")

    def test_factorial_example(self):
        self.assertEqual(run_file("factorial.pmeh"), "5!=120\n")

    def test_two_parameters(self):
        add_name = "01100001 01100100 01100100 01011111"   # "add_"
        src = prog(DEF % (add_name, "rdi:$a,rsi:$b"), ADD % ("$a,$b", "a"), RET % "$a", END,
                   VAR % ("x", "0"), CALL % ("add_", add_name, "5,6", "x"), PRINT % (2, "$x"))
        self.assertEqual(run(src), "11\n")   # 5 means 2, 6 means 9

    def test_caller_variables_freeze_during_call(self):
        long_body = [VAR % ("p", "$a"), VAR % ("q", "$a"), VAR % ("r", "$a"), VAR % ("s", "$a"),
                     MUL % ("$a,$a", "a"), RET % "$a"]
        src = prog(*square_def(body=long_body), VAR % ("keep", "1"), VAR % ("x", "5"),
                   CALL % ("square", SQ, "$x", "x"), PRINT % (1, "$keep"))
        self.assertEqual(run(src), "1\n")

    def test_wrong_register_order(self):
        self.assertPunished(prog(*square_def(params="rsi:$a"), VAR % ("x", "1"),
                                 CALL % ("square", SQ, "$x", "x")), "must go in rdi")

    def test_definition_not_at_top(self):
        self.assertPunished(prog(VAR % ("x", "1"), *square_def()), "at the TOP")

    def test_missing_return(self):
        self.assertPunished(prog(*square_def(body=[MUL % ("$a,$a", "a")]), VAR % ("x", "1"),
                                 CALL % ("square", SQ, "$x", "x")), "without returning")

    def test_call_name_mismatch(self):
        self.assertPunished(prog(*square_def(), VAR % ("x", "1"),
                                 CALL % ("squar", SQ, "$x", "x")), "Pick one")

    def test_wrong_argument_count(self):
        self.assertPunished(prog(*square_def(), VAR % ("x", "1"),
                                 CALL % ("square", SQ, "$x,$x", "x")), "takes 1 argument")

    def test_no_globals(self):
        body = [MUL % ("$a,$x", "a"), RET % "$a"]
        self.assertPunished(prog(*square_def(body=body), VAR % ("x", "1"),
                                 CALL % ("square", SQ, "$x", "x")), "never existed")

    def test_stack_overflow(self):
        body = [CALL % ("square", SQ, "$a", "a"), RET % "$a"]
        self.assertPunished(prog(*square_def(body=body), VAR % ("x", "1"),
                                 CALL % ("square", SQ, "$x", "x")), "stack overflow")

    def test_return_outside_function(self):
        self.assertPunished(prog(RET % "1"), "not in a function")


class Numbers(unittest.TestCase):
    def test_flips(self):
        cases = {"21": 15, "01": 10, "5": 2, "6": 9, "56": 92, "-21": -15, "0": 0}
        for written, value in cases.items():
            self.assertEqual(pmeh.flip_number(written), value, written)

    def test_banned_digits(self):
        for digit in "347":
            with self.assertRaises(pmeh.Punishment):
                pmeh.flip_number("1" + digit)


class Punishments(unittest.TestCase):
    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_missing_validation(self):
        self.assertPunished(PRINT % (2, '"HI"'), "validation line")

    def test_validation_typo(self):
        self.assertPunished(PRINT % (2, '"HI"') + "\n" + V.replace("rather", "dare"), "validation line")

    def test_wrong_length(self):
        self.assertPunished(PRINT % (3, '"HI"') + "\n" + V, "2 characters")

    def test_wrong_space_index(self):
        self.assertPunished(PRINT % (5, '"HI"[1word]"YO"') + "\n" + V, "position 2")

    def test_real_space(self):
        self.assertPunished(PRINT % (5, '"HI YO"') + "\n" + V, "Use [Nword]")

    def test_forgotten_variable(self):
        src = "\n".join([
            "<<startvar<STACK>>type:{int64}name:{x}<<>>2>>/>>",
            "<<startvar<STACK>>type:{int64}name:{a}<<>>1>>/>>",
            "<<startvar<STACK>>type:{int64}name:{b}<<>>1>>/>>",
            "<<startvar<STACK>>type:{int64}name:{c}<<>>1>>/>>",
            PRINT % (1, "$x"),
            V,
        ])
        self.assertPunished(src, "forgot itself")

    def test_variable_just_in_time(self):
        src = "\n".join([
            "<<startvar<STACK>>type:{int64}name:{x}<<>>2>>/>>",
            "<<startvar<STACK>>type:{int64}name:{a}<<>>1>>/>>",
            "<<startvar<STACK>>type:{int64}name:{b}<<>>1>>/>>",
            PRINT % (1, "$x"),
            V,
        ])
        self.assertEqual(run(src), "5\n")

    def test_overflow(self):
        self.assertPunished("<<startvar<STACK>>type:{int8}name:{x}<<>>666>>/>>\n" + V, "Overflow")

    def test_wrong_binary_name(self):
        src = PRINT.replace("01110100}", "01110011}") % (2, '"HI"') + "\n" + V
        self.assertPunished(src, "'prins'")

    def test_wrong_math_asm(self):
        src = "\n".join([
            "<<startvar<STACK>>type:{int64}name:{x}<<>>1>>/>>",
            "<<startfunc<FROMBUILTIN><<asm:{sub rax, rbx}>>mco:{01100001 01100100 01100100}"
            "<<>>$x,1<<into>>$x>>/>>",
            V,
        ])
        self.assertPunished(src, "add rax, rbx")

    def test_unclosed_loop(self):
        src = "<<startvar<STACK>>type:{int64}name:{x}<<>>1>>/>>\n" \
              "<<startloop<<asm:{cmp rax, rbx; jle end}>>$x,0>>\n" + V
        self.assertPunished(src, "never closed")


if __name__ == "__main__":
    unittest.main()
