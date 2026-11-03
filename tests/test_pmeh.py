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
        import random
        counting_up = "".join(f"{n}\n" for n in range(1, 100))
        secrets = set()
        for seed in range(30):
            out = io.StringIO()
            source = (ROOT / "examples" / "guess.pmeh").read_text()
            pmeh.Machine(pmeh.parse(source), out, io.StringIO(counting_up), random.Random(seed)).run()
            lines = out.getvalue().splitlines()
            secret = int(lines[-1])                      # counting up from 1: tries == secret
            self.assertTrue(1 <= secret <= 99)
            self.assertEqual(lines[:2], ["GUESS MY NUMBER!", "IT IS 1-99."])
            self.assertEqual(lines.count("TOO LOW"), secret - 1)
            self.assertNotIn("TOO HIGH", lines)
            self.assertEqual(lines[-3:-1], ["CORRECT!", "YOU WON! TRIES:"])
            secrets.add(secret)
        self.assertGreater(len(secrets), 10)               # really random

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


def bincall(name, asm, args, into=None):
    binary = " ".join(f"{ord(c):08b}" for c in name)
    return ("<<startfunc<FROMBUILTIN><<asm:{%s}>>mco:{%s}<<>>%s%s>>/>>"
            % (asm, binary, args, f"<<into>>${into}" if into else ""))


def OPEN(flags, into="fd", path="$p"):
    return bincall("open", f"mov rax, 2; lea rdi, [path]; mov rsi, {flags}; mov rdx, 420; syscall", path, into)


def WRITE(length, args, into="n"):
    return bincall("write", f"mov rax, 1; mov rdi, [fd]; lea rsi, [msg]; mov rdx, {length}; syscall", args, into)


def FREAD(maxlen, into, fd="$fd"):
    return bincall("read", f"mov rax, 0; mov rdi, [fd]; lea rsi, [buf]; mov rdx, {maxlen}; syscall", fd, into)


def CLOSE(into="fd", fd="$fd"):
    return bincall("close", "mov rax, 3; mov rdi, [fd]; syscall", fd, into)


PATH = TEXT % ("01", "p", '[0null]"txt.tset"')     # "test.txt", 10 bytes


class Files(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self.old_dir = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        import os
        os.chdir(self.old_dir)
        self.tmp.cleanup()

    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_diary_example(self):
        self.assertEqual(run_file("diary.pmeh"), "DEAR DIARY,\nI SUFFER.\n")
        self.assertEqual(pathlib.Path("diary.txt").read_text(), "DEAR DIARY,\nI SUFFER.\n")

    def test_write_returns_bytes_written(self):
        src = prog(VAR % ("fd", "0"), VAR % ("n", "0"), PATH, OPEN(577),
                   WRITE(2, '$fd,"HI"'), CLOSE(), PRINT % (1, "$n"))
        self.assertEqual(run(src), "2\n")
        self.assertEqual(pathlib.Path("test.txt").read_text(), "HI")

    def test_append(self):
        pathlib.Path("test.txt").write_text("A")
        src = prog(VAR % ("fd", "0"), VAR % ("n", "0"), PATH, OPEN(1089), WRITE(1, '$fd,"B"'), CLOSE())
        run(src)
        self.assertEqual(pathlib.Path("test.txt").read_text(), "AB")

    def test_open_missing_file_gives_negative_error(self):
        src = prog(VAR % ("fd", "0"), PATH, OPEN(0), PRINT % (2, "$fd"))
        self.assertEqual(run(src), "-2\n")              # ENOENT, like the real syscall

    def test_bad_flags(self):
        src = prog(VAR % ("fd", "0"), PATH, OPEN(3), PRINT % (3, "$fd"))
        self.assertEqual(run(src), "-22\n")             # EINVAL

    def test_write_to_closed_file(self):
        src = prog(VAR % ("fd", "8"), VAR % ("n", "0"), WRITE(2, '$fd,"HI"'), PRINT % (2, "$n"))
        self.assertEqual(run(src), "-9\n")              # EBADF

    def test_write_to_screen_has_no_newline(self):
        src = prog(VAR % ("n", "0"), WRITE(2, '1,"HI"'), WRITE(2, '1,"YO"'))
        self.assertEqual(run(src), "HIYO")

    def test_read_until_end_of_file(self):
        pathlib.Path("test.txt").write_text("ABC")
        src = prog(VAR % ("fd", "0"), PATH, OPEN(0), TEXT % ("8", "t", ""),
                   FREAD(2, "t"), PRINT % (2, "$t"), FREAD(2, "t"), PRINT % (1, "$t"),
                   FREAD(2, "t"), PRINT % (0, "$t"), CLOSE())
        self.assertEqual(run(src), "AB\nC\n\n")

    def test_read_bad_file_descriptor(self):
        src = prog(VAR % ("fd", "8"), TEXT % ("8", "t", ""), FREAD(2, "t"))
        self.assertPunished(src, "Bad file descriptor")

    def test_forgot_to_close(self):
        src = prog(VAR % ("fd", "0"), PATH, OPEN(577))
        self.assertPunished(src, "still open")

    def test_newline_position(self):
        self.assertEqual(run(prog(PRINT % (3, '"A"[1line]"B"'))), "A\nB\n")
        self.assertPunished(prog(PRINT % (3, '"A"[0line]"B"')), "position 1")


FVAR = "<<startvar<STACK>>type:{%s}name:{%s}<<>>%s>>/>>"


def fop(op, args, into, suffix="sd"):
    return bincall(op, f"{op}{suffix} xmm0, xmm1", args, into)


def cvt(asm, arg, into):
    return bincall("cvt", asm, arg, into)


def fif(jump, args, suffix="d"):
    return "<<startif<<asm:{ucomis%s xmm0, xmm1; %s skip}>>%s>>" % (suffix, jump, args)


class Decimals(unittest.TestCase):
    def assertPunished(self, source, fragment):
        with self.assertRaises(pmeh.Punishment) as ctx:
            run(source)
        self.assertIn(fragment, ctx.exception.message)

    def test_written_upside_down(self):
        for written, value in [("1.0", 0.1), ("25.1", 1.25), ("-5.0", -0.2), ("0.001", 100.0), ("2.5", 2.5)]:
            self.assertEqual(pmeh.flip_decimal(written), value, written)
        with self.assertRaises(pmeh.Punishment):
            pmeh.flip_decimal("1.3")

    def test_example(self):
        self.assertEqual(run_file("decimals.pmeh"), "0.1+0.2=0.30000000000000004\n0.1F=0.10000000149011612\n"
                                                    "0.3/0=inf\nADDING 0.1 UNTIL 1: 1.0999999999999999\n")

    def test_printing_like_c(self):
        for x in (1.0, 1e20, 1e16, 1e17, 1e-5, -0.0, 5e-324, 0.1):
            self.assertEqual(pmeh.show_decimal(x), "%.17g" % x)
        self.assertEqual(pmeh.show_decimal(1.0), "1")

    def test_float32_math(self):
        src = prog(FVAR % ("float32", "a", "1.0"), FVAR % ("float32", "b", "5.0"),
                   fop("add", "$a,$b", "a", "ss"), PRINT % (19, "$a"))
        self.assertEqual(run(src), "0.30000001192092896\n")

    def test_division_by_zero_is_not_punished(self):
        for a, b, expected in (("0.1", "0.0", "inf"), ("-0.1", "0.0", "-inf"), ("0.0", "0.0", "nan"),
                               ("0.1", "-0.0", "-inf")):
            src = prog(FVAR % ("float64", "x", a), fop("div", f"$x,{b}", "x"), PRINT % (len(expected), "$x"))
            self.assertEqual(run(src), expected + "\n", (a, b))

    def test_nan_comparisons_like_x86(self):
        # NaN sets ZF and CF, so je, jb and jbe are taken (the block is skipped), the rest are not
        for jump, skipped in (("je", True), ("jne", False), ("jb", True), ("jbe", True),
                              ("ja", False), ("jae", False)):
            src = prog(FVAR % ("float64", "x", "0.0"), fop("div", "$x,0.0", "x"), fif(jump, "$x,$x"),
                       PRINT % (3, '"RAN"'), "<<endif>>/>>")
            self.assertEqual(run(src), "" if skipped else "RAN\n", jump)

    def test_conversions(self):
        cases = [
            ("cvtsi2sd xmm0, rax", "int64", "6", "float64", "9"),
            ("cvttsd2si rax, xmm0", "float64", "6.5", "int64", "2"),          # 6.5 is 2.9 upside down
            ("cvttsd2si rax, xmm0", "float64", "-6.5", "int64", "-2"),
            ("cvttsd2si rax, xmm0", "float64", "1.0e20", None, None),
            ("cvtsd2ss xmm0, xmm0", "float64", "1.0", "float32", "0.10000000149011612"),
            ("cvtss2sd xmm0, xmm0", "float32", "1.0", "float64", "0.10000000149011612"),
            ("cvttss2si rax, xmm0", "float32", "2.5", "int64", "2"),
        ]
        for asm, from_type, value, to_type, expected in cases:
            if to_type is None:
                continue
            src = prog(FVAR % (from_type, "x", value), FVAR % (to_type, "y", "0.0" if "float" in to_type else "0"),
                       cvt(asm, "$x", "y"), PRINT % (len(expected), "$y"))
            self.assertEqual(run(src), expected + "\n", asm)

    def test_nan_to_whole_number_is_indefinite(self):
        src = prog(FVAR % ("float64", "x", "0.0"), fop("div", "$x,0.0", "x"), VAR % ("n", "0"),
                   cvt("cvttsd2si rax, xmm0", "$x", "n"), PRINT % (20, "$n"))
        self.assertEqual(run(src), "-9223372036854775808\n")

    def test_int_to_float32_rounds_once(self):
        self.assertEqual(pmeh.int_to_f32(2 ** 53 + 2 ** 29 + 1), 2 ** 53 + 2 ** 30)
        self.assertNotEqual(pmeh.round32(float(2 ** 53 + 2 ** 29 + 1)), 2 ** 53 + 2 ** 30)

    def test_mistakes(self):
        for src, fragment in (
            (prog(FVAR % ("float64", "x", "0")), "holds decimals"),
            (prog(VAR % ("x", "1.0")), "holds whole numbers"),
            (prog(FVAR % ("float64", "x", "1.0"), FVAR % ("float32", "y", "$x")), "can't hold a float64"),
            (prog(FVAR % ("float64", "x", "1.0"), fop("add", "$x,1", "x")), "whole number"),
            (prog(FVAR % ("float32", "x", "1.0"), fop("add", "$x,$x", "x")), "need float64"),
            (prog(FVAR % ("float64", "x", "1.0"), VAR % ("n", "0"), ADD % ("$x,1", "n")), "is a decimal"),
            (prog(LIST % ("int64", "8"), STORE % "$xs,0,1.0"), "holds whole numbers"),
            (prog(FVAR % ("float64", "x", "1.0"), "<<startif<<asm:{ucomisd xmm0, xmm1; jg skip}>>$x,$x>>",
                  "<<endif>>/>>"), "doesn't work"),
        ):
            with self.subTest(fragment):
                self.assertPunished(src, fragment)

    def test_decimal_parameters(self):
        dbl = " ".join(f"{ord(c):08b}" for c in "dbl")
        src = prog(DEF % (dbl, "rdi:$a"), fop("add", "$a,$a", "a"), RET % "$a", END,
                   FVAR % ("float64", "x", "1.0"), CALL % ("dbl", dbl, "$x", "x"), PRINT % (19, "$x"))
        self.assertEqual(run(src), "0.20000000000000001\n")            # 0.1 + 0.1, all 17 digits


class Imports(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def import_line(name):
        return "<<import<FILE>>path:{%s}>>/>>" % " ".join(f"{ord(c):08b}" for c in name[::-1])

    def write(self, name, *lines):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([*lines, V]) + "\n")
        return path

    def run_main(self, *lines):
        path = self.write("main.pmeh", *lines)
        source, origins = pmeh.glue(path.read_text(), str(path), str(self.dir))
        return run(source), origins

    def lib(self, name="lib/sq.pmeh", body=None):
        return self.write(name, *square_def(body=body))

    def test_import(self):
        self.lib()
        out, _ = self.run_main(self.import_line("lib/sq.pmeh"), VAR % ("x", "5"),
                               CALL % ("square", SQ, "$x", "x"), PRINT % (1, "$x"))
        self.assertEqual(out, "4\n")

    def test_temperature_example(self):
        path = ROOT / "examples" / "temperature.pmeh"
        source, _ = pmeh.glue(path.read_text(), str(path), str(path.parent))
        self.assertEqual(run(source), "100C=212F\n21.5C=70.700000000000003F\n")

    def test_error_line_counts_glued_files(self):
        self.lib(body=[MUL % ("$a,$a", "a")])                       # no return
        with self.assertRaises(pmeh.Punishment) as ctx:
            self.run_main(self.import_line("lib/sq.pmeh"), VAR % ("x", "5"), CALL % ("square", SQ, "$x", "x"))
        path = self.dir / "main.pmeh"
        _, origins = pmeh.glue(path.read_text(), str(path), str(self.dir))
        self.assertEqual(ctx.exception.lineno, 3)                   # the enddefine, 3rd glued line
        self.assertEqual(pmeh.where(3, origins), " (lib/sq.pmeh, line 3)")

    def test_mistakes(self):
        self.lib()
        self.write("other.pmeh", VAR % ("x", "1"))
        self.write("loop.pmeh", self.import_line("main.pmeh"))
        (self.dir / "novalidation.pmeh").write_text(square_def()[0] + "\n")
        for lines, fragment in (
            ([self.import_line("lib/sq.pmeh"), self.import_line("lib/sq.pmeh")], "already imported"),
            ([self.import_line("nope.pmeh")], "can't import"),
            ([self.import_line("other.pmeh")], "may only contain functions"),
            ([self.import_line("loop.pmeh")], "circle"),
            ([self.import_line("novalidation.pmeh")], "validation line too"),
            ([VAR % ("x", "1"), self.import_line("lib/sq.pmeh")], "very top"),
        ):
            with self.subTest(fragment):
                with self.assertRaises(pmeh.Punishment) as ctx:
                    self.run_main(*lines)
                self.assertIn(fragment, ctx.exception.message)


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
