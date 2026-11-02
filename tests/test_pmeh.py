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

    def test_guess_demo(self):
        intro, prompt = "GUESS MY NUMBER!\n", "YOUR GUESS?\n"
        self.assertEqual(run_file("guess.pmeh", "50\n80\n64\n"),
                         intro + prompt + "TOO LOW\n" + prompt + "TOO HIGH\n" + prompt
                         + "CORRECT!\nYOU WON! TRIES:\n3\n")
        out = run_file("guess.pmeh", "1\n" * 11 + "64\n")
        self.assertTrue(out.endswith("YOU WON! TRIES:\n12\n"))

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


LIST = "<<startlist<HEAP>>type:{%s}name:{xs}bytes:{%s}>>/>>"
STORE = ("<<startfunc<FROMBUILTIN><<asm:{mov [rbx + rcx], rax}>>"
         "mco:{01110011 01110100 01101111 01110010 01100101}<<>>%s>>/>>")
LOAD = ("<<startfunc<FROMBUILTIN><<asm:{mov rax, [rbx + rcx]}>>"
        "mco:{01101100 01101111 01100001 01100100}<<>>%s<<into>>$%s>>/>>")


class FakeRng:
    """Makes every unstored list item the same piece of garbage."""
    def __init__(self, junk):
        self.junk = junk

    def random(self):
        return 0.0

    def choice(self, options):
        return self.junk

    def randint(self, low, high):
        return low


def run_rng(source, rng):
    out = io.StringIO()
    pmeh.Machine(pmeh.parse(source), out, io.StringIO(), rng).run()
    return out.getvalue()


class Lists(unittest.TestCase):
    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_sum_example(self):
        self.assertEqual(run_file("sum_list.pmeh"), "11\n")

    def test_byte_offsets_and_upside_down(self):
        # 16 bytes written 91; item 1 is at byte 8; value 21 means 15
        src = prog(LIST % ("int64", "91"), STORE % "$xs,8,21", VAR % ("a", "0"),
                   LOAD % ("$xs,8", "a"), PRINT % (2, "$a"))
        self.assertEqual(run(src), "15\n")

    def test_int8_items_are_one_byte(self):
        src = prog(LIST % ("int8", "5"), STORE % "$xs,1,9", VAR % ("a", "0"),
                   LOAD % ("$xs,1", "a"), PRINT % (1, "$a"))
        self.assertEqual(run(src), "6\n")

    def test_segfault(self):
        self.assertPunished(prog(LIST % ("int64", "91"), VAR % ("a", "0"),
                                 LOAD % ("$xs,91", "a")), "Segmentation fault")

    def test_misaligned(self):
        self.assertPunished(prog(LIST % ("int64", "91"), VAR % ("a", "0"),
                                 LOAD % ("$xs,5", "a")), "misaligned")

    def test_size_must_fit_items(self):
        self.assertPunished(prog(LIST % ("int64", "21")), "doesn't divide")

    def test_garbage_junk_breaks_math(self):
        src = prog(LIST % ("int64", "8"), VAR % ("a", "0"), LOAD % ("$xs,0", "a"),
                   ADD % ("$a,1", "a"))
        with self.assertRaises(pmeh.Punishment) as ctx:
            run_rng(src, FakeRng("banana"))
        self.assertIn("'banana'", ctx.exception.message)

    def test_garbage_can_be_printed(self):
        src = prog(LIST % ("int64", "8"), VAR % ("a", "0"), LOAD % ("$xs,0", "a"),
                   PRINT % (14, "$a"))
        self.assertEqual(run_rng(src, FakeRng("notexisten<<>>")), "notexisten<<>>\n")

    def test_print_list_not_allowed(self):
        self.assertPunished(prog(LIST % ("int64", "8"), PRINT % (1, "$xs")), "one at a time")

    def test_math_with_list_not_allowed(self):
        self.assertPunished(prog(LIST % ("int64", "8"), VAR % ("a", "0"),
                                 ADD % ("$xs,1", "a")), "is a list")

    def test_whole_list_forgets(self):
        self.assertPunished(prog(LIST % ("int64", "8"), VAR % ("a", "0"), VAR % ("b", "0"),
                                 VAR % ("c", "0"), LOAD % ("$xs,0", "a")), "forgot itself")

    def test_lists_pass_by_reference(self):
        fill = "01100110 01101001 01101100 01101100"   # "fill"
        src = prog(DEF % (fill, "rdi:$l"), STORE % "$l,0,6", RET % "0", END,
                   LIST % ("int64", "8"), VAR % ("r", "0"), CALL % ("fill", fill, "$xs", "r"),
                   LOAD % ("$xs,0", "r"), PRINT % (1, "$r"))
        self.assertEqual(run(src), "9\n")


TEXT = "<<startvar<STACK>>type:{char[%s]}name:{%s}<<>>%s>>/>>"
LIBCALL = "<<startfunc<FROMLIBC><<asm:{call %s}>>mco:{%s}<<>>%s%s>>/>>"


def libc(name, args, into=None):
    binary = " ".join(f"{ord(c):08b}" for c in name)
    return LIBCALL % (name, binary, args, f"<<into>>${into}" if into else "")


class Text(unittest.TestCase):
    def assertPunished(self, source, fragment, rng=None):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run_rng(source, rng) if rng else run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_examples(self):
        self.assertEqual(run_file("text.pmeh"), "HELLO WORLD\nLENGTH: 11\n")
        self.assertEqual(run_file("overflow.pmeh"), "WORLD? LD\n")

    def test_literal_is_backwards(self):
        self.assertEqual(pmeh.parse_text_literal('[0null]"OLLEH"', 1), "HELLO\0")

    def test_space_position_counts_forwards(self):
        # real text "HI YOU": the space is at position 2
        self.assertEqual(pmeh.parse_text_literal('[0null]"UOY"[2word]"IH"', 1), "HI YOU\0")
        with self.assertRaises(pmeh.Punishment):
            pmeh.parse_text_literal('[0null]"UOY"[3word]"IH"', 1)

    def test_missing_terminator_reads_garbage(self):
        src = prog(TEXT % ("2", "s", '"IH"'), PRINT % (2, "$s"))
        # char[2] is 5 bytes (2 is upside down): "HI" + 3 garbage bytes "nan",
        # then no terminator, so reading runs on into 3 more bytes "ban"
        self.assertPunished(src, "'HInanban'", FakeRng("banana"))

    # "HELLO" + "ABC" + terminator is 9 bytes in an 8-byte buffer: the terminator spills into $n
    OVERFLOW = [TEXT % ("8", "s", '[0null]"OLLEH"'), VAR % ("n", "1"),
                TEXT % ("8", "t", '[0null]"CBA"'), libc("strcat", "$s,$t")]

    def test_overflow_corrupts_number_variable(self):
        self.assertEqual(run(prog(*self.OVERFLOW, PRINT % (1, "$n"))), "\0\n")

    def test_overflow_then_math_is_garbage(self):
        self.assertPunished(prog(*self.OVERFLOW, ADD % ("$n,1", "n")), "garbage")

    def test_overflow_while_declaring_smashes_stack(self):
        self.assertPunished(prog(VAR % ("n", "1"), TEXT % ("5", "s", '[0null]"OLLEH"')),
                            "stack smashing")

    def test_stack_smashing(self):
        src = prog(TEXT % ("2", "a", '[0null]"OLLEH"'))
        self.assertPunished(src, "stack smashing")

    def test_strlen_strcmp_atoi_itoa_charat(self):
        src = prog(TEXT % ("8", "s", '[0null]"21"'), VAR % ("n", "0"),
                   libc("atoi", "$s", "n"), PRINT % (2, "$n"),          # "12" -> 12, not flipped
                   libc("strlen", "$s", "n"), PRINT % (1, "$n"),
                   libc("charat", "$s,0", "n"), PRINT % (2, "$n"),     # '1' is code 49
                   TEXT % ("8", "t", '[0null]"21"'), libc("strcmp", "$s,$t", "n"), PRINT % (1, "$n"),
                   libc("itoa", "$n", "t"), PRINT % (1, "$t"))
        self.assertEqual(run(src), "12\n2\n49\n0\n0\n")

    def test_atoi_of_banana_is_zero(self):
        src = prog(TEXT % ("8", "s", '[0null]"ANANAB"'), VAR % ("n", "1"),
                   libc("atoi", "$s", "n"), PRINT % (1, "$n"))
        self.assertEqual(run(src), "0\n")

    def test_read_text(self):
        src = prog(TEXT % ("8", "s", ""), READ % (5, "s"), PRINT % (3, "$s"))
        self.assertEqual(run(src, "Bob\n"), "Bob\n")

    def test_text_is_not_a_number(self):
        src = prog(TEXT % ("8", "s", '[0null]"IH"'), VAR % ("n", "0"), ADD % ("$s,1", "n"))
        self.assertPunished(src, "Use atoi")

    def test_text_passed_by_reference(self):
        shout = " ".join(f"{ord(c):08b}" for c in "shout")
        src = prog(DEF % (shout, "rdi:$x,rsi:$y"), libc("strcat", "$x,$y"), RET % "0", END,
                   TEXT % ("8", "a", '[0null]"IH"'), TEXT % ("8", "b", '[0null]"!"'), VAR % ("r", "0"),
                   CALL % ("shout", shout, "$a,$b", "r"), PRINT % (3, "$a"))
        self.assertEqual(run(src), "HI!\n")

    def test_unknown_libc_function(self):
        self.assertPunished(prog(TEXT % ("8", "s", ""), libc("printf", "$s")), "not in the C library")


COMMENT = '<<COMMNT"%s">>/>>'
MOD = "<<startfunc<FROMBUILTIN><<asm:{cqo; idiv rbx; mov rax, rdx}>>mco:{01101101 01101111 01100100}<<>>%s<<into>>$%s>>/>>"
RAND = ("<<startfunc<FROMBUILTIN><<asm:{mov rax, 318; lea rdi, [buf]; mov rsi, %d; mov rdx, 0; syscall}>>"
        "mco:{01110010 01100001 01101110 01100100}<<>><<into>>$%s>>/>>")


class CommentsModRand(unittest.TestCase):
    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_comment_is_backwards(self):
        program = pmeh.parse(prog(COMMENT % "EID A LLOR"))
        self.assertEqual(program[0].data["text"], "ROLL A DIE")

    def test_comments_count_as_lines(self):
        src = prog(VAR % ("x", "1"), COMMENT % "A", COMMENT % "B", COMMENT % "C", PRINT % (1, "$x"))
        self.assertPunished(src, "forgot itself")

    def test_comments_allowed_before_functions(self):
        src = prog(COMMENT % "SNOITCNUF", *square_def(), VAR % ("x", "5"),
                   CALL % ("square", SQ, "$x", "x"), PRINT % (1, "$x"))
        self.assertEqual(run(src), "4\n")

    def test_mod(self):
        for written, divisor, expected in [("6", "5", "1"), ("-6", "5", "-1"), ("01", "5", "0")]:
            # 6 means 9, 5 means 2, 01 means 10
            src = prog(VAR % ("x", written), MOD % (f"$x,{divisor}", "x"), PRINT % (len(expected), "$x"))
            self.assertEqual(run(src), expected + "\n", written)

    def test_mod_by_zero(self):
        self.assertPunished(prog(VAR % ("x", "1"), MOD % ("$x,0", "x")), "division by zero")

    def test_rand_fills_whole_type_range(self):
        import random
        src = prog("<<startvar<STACK>>type:{int8}name:{x}<<>>0>>/>>", RAND % (1, "x"))
        seen = set()
        for seed in range(400):
            m = pmeh.Machine(pmeh.parse(src), io.StringIO(), io.StringIO(), random.Random(seed))
            m.run()
            seen.add(m.vars["x"][0])
        self.assertTrue(all(-128 <= v <= 127 for v in seen))
        self.assertTrue(min(seen) < 0 < max(seen))

    def test_rand_bytes_must_match_type(self):
        self.assertPunished(prog(VAR % ("x", "0"), RAND % (4, "x")), "8 bytes")

    def test_rand_into_text(self):
        self.assertPunished(prog(TEXT % ("8", "s", ""), RAND % (8, "s")), "can't hold one")

    def test_dice_example(self):
        import random
        rolls = {run_rng((ROOT / "examples" / "dice.pmeh").read_text(), random.Random(seed))
                 for seed in range(200)}
        self.assertEqual(rolls, {f"ROLLED: {n}\n" for n in range(1, 7)})


class CommandLine(unittest.TestCase):
    def cli(self, *argv):
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = pmeh.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_run_file(self):
        self.assertEqual(self.cli(str(ROOT / "examples" / "hello.pmeh"))[:2], (0, "HELLO WORLD\n"))

    def test_command_lines(self):
        code, out, _ = self.cli("-c", PRINT % (2, '"HI"'), "-c", V)
        self.assertEqual((code, out), (0, "HI\n"))

    def test_check_does_not_run(self):
        code, out, _ = self.cli("-k", str(ROOT / "examples" / "hello.pmeh"))
        self.assertEqual(code, 0)
        self.assertNotIn("HELLO", out)

    def test_check_finds_punishment(self):
        self.assertEqual(self.cli("-k", "-c", "nonsense", "-c", V)[0], 1)

    def test_coward_errors_are_plain(self):
        code, _, err = self.cli("-g", "-c", "nonsense", "-c", V)
        self.assertEqual(code, 1)
        self.assertIn("PUNISHMENT on line 1", err)

    def test_mercy(self):
        code, out, _ = self.cli("-m", "15", "add")
        self.assertEqual(code, 0)
        self.assertIn("write 21", out)
        self.assertIn("01100001 01100100 01100100", out)

    def test_seed_makes_garbage_repeat(self):
        lines = [LIST % ("int64", "8"), VAR % ("a", "0"), LOAD % ("$xs,0", "a"),
                 PRINT % (1, "$a"), V]
        args = ["-g", "-s", "7"] + [x for line in lines for x in ("-c", line)]
        self.assertEqual(self.cli(*args), self.cli(*args))

    def test_version(self):
        code, out, _ = self.cli("-V")
        self.assertEqual(code, 0)
        self.assertIn(pmeh.__version__, out)

    def test_nothing_given(self):
        self.assertEqual(self.cli()[0], 2)

    def test_file_and_command_together(self):
        self.assertEqual(self.cli("x.pmeh", "-c", "x")[0], 2)


class ErrorReports(unittest.TestCase):
    def test_upside_down_line_numbers(self):
        self.assertEqual(pmeh.upside_down(15), "21")
        self.assertEqual(pmeh.upside_down(3), "5 <3 1")       # 2 + 1, and 2 is written 5
        for n in range(1, 500):
            parts = pmeh.upside_down(n).split(" <3 ")
            self.assertEqual(sum(pmeh.flip_number(x) for x in parts), n)

    def test_scramble_round_trip(self):
        msg = "mov rdx, 3 but the text 'HI' is 2 characters long."
        self.assertNotEqual(pmeh.scramble(msg), msg)
        self.assertEqual(pmeh.unscramble(pmeh.scramble(msg)), msg)

    def test_report_contents(self):
        import random
        source = "first\nsecond\nthird\n"
        report = pmeh.punishment_report(pmeh.Punishment(1, "Oops"), source, random.Random(1))
        self.assertIn("line 5 <3 1,", report)                 # line 1 is 3rd from the bottom
        self.assertIn("NOT the problem: ", report)
        self.assertNotIn("NOT the problem: first", report)


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
