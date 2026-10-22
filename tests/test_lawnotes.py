"""Editor tests: the buffer, saving, highlighting, and a real session in a pseudo-terminal.

Run: python3 -m unittest discover -s tests -v
Uses only temporary folders; never touches your notes, history or install.
"""
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="lawnotes-tests-")
os.environ.update(LAWNOTES_DIR=os.path.join(TMP, "notes"), LAWNOTES_HISTORY=os.path.join(TMP, "history.git"),
                  LAWNOTES_SPELL="0", LAWNOTES_NO_UPDATE="1", HOME=os.path.join(TMP, "home"))
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, ROOT)
import lawnotes  # noqa: E402  (after the environment above)


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)
        self.path = os.path.join(self.dir, "Tort", "Duty.md")

    def test_list_continues_and_ends(self):
        b = lawnotes.Buffer(self.path)
        b.insert("- first")
        b.newline()
        b.insert("second")
        b.newline()
        b.newline()  # Enter on an empty bullet ends the list
        self.assertEqual(b.lines, ["- first", "- second", ""])

    def test_numbered_list_and_shift_enter(self):
        b = lawnotes.Buffer(self.path)
        b.insert("1. offer")
        b.newline(plain=True)
        b.insert("unilateral")
        b.newline()
        self.assertEqual(b.lines, ["1. offer", "   unilateral", "2. "])

    def test_undo_steps(self):
        b = lawnotes.Buffer(self.path)
        b.insert("alpha")
        b.newline()
        b.insert("beta")
        b.cy, b.cx = 0, 5  # move away, then type: a separate undo step
        b.insert("!")
        b.undo()
        self.assertEqual(b.lines, ["alpha", "beta"])

    def test_delete_words(self):
        b = lawnotes.Buffer(self.path)
        b.insert("the ratio decidendi")
        b.delete_word_left()
        self.assertEqual(b.lines, ["the ratio "])
        b.delete_word_left()
        self.assertEqual(b.lines, ["the "])
        b.cx = 0
        b.delete_word_right()
        self.assertEqual(b.lines, [" "])
        b.undo()
        self.assertEqual(b.lines, ["the "])

    def test_enter_on_empty_nested_bullet_outdents(self):
        b = lawnotes.Buffer(self.path)
        b.lines, b.cy, b.cx = ["- duty", "  - "], 1, 4
        b.newline()
        self.assertEqual((b.lines, b.cy, b.cx), (["- duty", "- "], 1, 2))  # outdented, not deleted
        b.newline()
        self.assertEqual(b.lines, ["- duty", ""])                       # top level: ends the list

    def test_word_count_follows_edits(self):
        b = lawnotes.Buffer(self.path)
        b.insert("Donoghue v Stevenson")
        self.assertEqual(b.word_count(), 3)
        b.save()                              # a save starts a new undo step
        b.insert(" neighbour test")
        self.assertEqual(b.word_count(), 5)
        b.undo()
        self.assertEqual(b.word_count(), 3)

    def test_save_round_trip_and_undo(self):
        b = lawnotes.Buffer(self.path)
        b.insert("Donoghue v Stevenson")
        b.save()
        self.assertEqual(open(self.path).read(), "Donoghue v Stevenson\n")
        b.insert(" [1932] AC 562")
        self.assertTrue(b.undo())
        self.assertEqual(b.lines, ["Donoghue v Stevenson"])

    def test_conflict_copy_never_overwrites(self):
        b = lawnotes.Buffer(self.path)
        b.insert("mine")
        b.save()
        time.sleep(0.02)
        with open(self.path, "w") as f:
            f.write("from another Mac\n")
        b.insert(" more")
        note = b.save()
        self.assertIn("changed elsewhere", note)
        self.assertEqual(open(self.path).read(), "from another Mac\n")
        self.assertIn("conflict", b.path)
        self.assertEqual(open(b.path).read(), "mine more\n")

    def test_failed_save_raises_and_keeps_text(self):
        b = lawnotes.Buffer(os.path.join(self.dir, "ro", "n.md"))
        os.makedirs(os.path.dirname(b.path))
        os.chmod(os.path.dirname(b.path), 0o500)
        try:
            b.insert("keep me")
            with self.assertRaises(OSError):
                b.save()
            self.assertEqual(b.lines, ["keep me"])
            self.assertTrue(b.dirty)
        finally:
            os.chmod(os.path.dirname(b.path), 0o700)

    def test_encodings_and_control_characters(self):
        p = os.path.join(self.dir, "w.md")
        with open(p, "wb") as f:
            f.write("Lord Atkin’s “neighbour”".encode("cp1252") + b"\x00x")
        b = lawnotes.Buffer(p)
        self.assertEqual(b.lines, ["Lord Atkin’s “neighbour”�x"])
        self.assertIn("Windows-1252", b.note)

    def test_symlinked_note_stays_a_link(self):
        real = os.path.join(self.dir, "real.md")
        open(real, "w").write("a\n")
        link = os.path.join(self.dir, "link.md")
        os.symlink(real, link)
        b = lawnotes.Buffer(link)
        b.insert("b")
        b.save()
        self.assertTrue(os.path.islink(link))
        self.assertEqual(open(real).read(), "ba\n")


class TextTests(unittest.TestCase):
    def test_paste_keeps_special_spaces(self):
        self.assertEqual(lawnotes.clean_text("v Stevenson [1932] AC\r\n562\x07"), "v Stevenson [1932] AC\n562")

    def test_case_and_citation_patterns(self):
        cases = lambda s: [m.group() for m in lawnotes.CASE_RE.finditer(s)]
        self.assertEqual(cases("Lord Atkin in Donoghue v Stevenson"), ["Donoghue v Stevenson"])
        self.assertEqual(cases("Caparo Industries plc v Dickman"), ["Caparo Industries plc v Dickman"])
        self.assertEqual(cases("R v Brown and Re Polemis"), ["R v Brown", "Re Polemis"])
        self.assertEqual([m.group() for m in lawnotes.CITE_RE.finditer("[2019] EWCA Civ 1234")], ["[2019] EWCA Civ 1234"])
        self.assertEqual([m.group() for m in lawnotes.STATUTE_RE.finditer("s 2(1) of the Human Rights Act 1998")],
                         ["s 2(1)", "Human Rights Act 1998"])

    def test_articles_and_treaties(self):
        statutes = lambda s: [m.group() for m in lawnotes.STATUTE_RE.finditer(s)]
        self.assertEqual(statutes("Article 1 (1) and Article 1.1. apply"), ["Article 1 (1)", "Article 1.1"])
        self.assertEqual(statutes("Read Art. 1 TEU. See Art. 3 (5) TEU and Art. 21 (1) and (2) TEU"),
                         ["Art. 1 TEU", "Art. 3 (5) TEU", "Art. 21 (1) and (2) TEU"])
        self.assertEqual(statutes("Articles 49 and 50 TEU; Articles 4 (1) and 5 (in toto)"),
                         ["Articles 49 and 50 TEU", "Articles 4 (1) and 5"])
        self.assertEqual(statutes("the TFEU and the TEU, not TEUs"), ["TFEU", "TEU"])
        self.assertEqual(statutes("Part 1 of the Arts and Humanities course"), [])

    def test_notes_citations_are_highlighted_in_full(self):
        """Citations from the notes that were missed, or coloured only in part."""
        def whole(rx, text):
            self.assertEqual([m.group() for m in rx.finditer(text)], [text], text)

        for text in ("Henningsen v. Bloomfield Motors, Inc.",
                     "Leigh v Sillavan Ltd v Aliakmon Shipping Co Ltd (The Aliakmon)",
                     "Re Baden No. 1", "Re Baden (No 1)", "Re Baden (No 2)",
                     "Re Adams and the Kensington Vestry",
                     "Re Baden’s Trust Deeds (No 1)", "Re Baden’s Deed Trusts (No 1)",
                     "Re Baden’s Deed Trusts (No 2)",
                     "R v District Auditor, ex parte West Yorkshire MCC",
                     "Morice v Bishop v Durham",
                     "Non-Marine Underwriters, Lloyds of London v Scalera",
                     "R v Deputy Governor of Parkhurst Prison, Ex parte Hague",
                     "Freeman v Home Office (No. 2)",
                     "Majrowski v Guy’s and St Thomas’s NHS Trust"):
            whole(lawnotes.CASE_RE, text)
        self.assertEqual([m.group() for m in lawnotes.CASE_RE.finditer(
            "Donoghue v Stevenson, Lord Atkin held")], ["Donoghue v Stevenson"])
        for text in ("115 NY 506", "22 NE 188 (1889)", "32 NJ 358", "161 A2d 69 (1960)",
                     "[1986] 785 (HL)", "(1870-71) LR 6 Ch App 597", "45 ER 1184",
                     "(1984) 50 P & CR 119", "(1862) 2 De G F & J 264", "(1857) 3 K&J 497",
                     "(1841) Cr & Ph 240", "[2005] 2 A.C. 373", "[2016] A.C. 219",
                     "(1844) 1 Car & Kir 257", "(1838) 8 AD & E 602", "112 ER 966",
                     "(1830) 4 Car & P 239", "172 ER 687", "87 ER 928", "129 ER 278",
                     "(1830) 4 C& P 349", "[1968] 1 Q.B. 379", "[2008] 1 A.C. 962",
                     "[1985] 1 W.L.R. 1242", "[1990] 2 A.C. 1", "124 N.W. 221 (Minn. 1910)"):
            whole(lawnotes.CITE_RE, text)
        for text in ("meet at 10 AM 30", "see page 3 Week 4", "Part 2 Chapter 3", "Year 2 Term 1",
                     "Lecture 5 Slides 12", "In 2019 Parliament 2 times"):
            self.assertEqual([m.group() for m in lawnotes.CITE_RE.finditer(text)], [], text)
        self.assertEqual([m.group() for m in lawnotes.STATUTE_RE.finditer("an Act of Parliament 2013")], [])
        for text in ("LPA 1925", "subsection 3", "Schedule 1", "S1 (2)",
                     "Sale of Goods (Amendment) Act 1995", "Law of Property Act 1925",
                     "Trusts (Jersey) Law 1984",
                     "Trusts of Land and Appointment of Trustees Act 1996",
                     "Occupiers Liability Act of 1957", "Occupiers Liability Act of 1984",
                     "The Law Reform (Contributory Negligence) Act of 1945",
                     "Protection from Harassment Act of 1997",
                     "Automated and Electric Vehicles Act 2018",
                     "European Convention", "EU Convention of Human Rights",
                     "European Convention for the Protection of Human Rights",
                     "European Convention for PHR"):
            whole(lawnotes.STATUTE_RE, text)

    # An essay as the notes write it: citations in the forms of CHANGELOG 1.5.0 and 1.5.3
    ESSAY = [
        "# Duty of Care in Negligence",
        "",
        "Lord Atkin in Donoghue v Stevenson set out the neighbour principle.  // check the HL report",
        "- Donoghue v Stevenson [1932] AC 562 (HL): the snail in the bottle",
        "- Caparo Industries plc v Dickman [1990] 2 A.C. 605, three-stage test; *R v Brown* [1994] 1 AC 212",
        "- Re Baden’s Deed Trusts (No 2) and Re Adams and the Kensington Vestry",
        "- Henningsen v. Bloomfield Motors, Inc. 161 A2d 69 (1960)",
        "- Leigh v Sillavan Ltd v Aliakmon Shipping Co Ltd (The Aliakmon) [1986] 1 AC 785",
        "- Costa v ENEL [1964] ECR 585; Osman v United Kingdom (2000) 29 EHRR 245",
        "// Fake v Case [2000] 1 AC 1 is only a comment",
        "",
        "## Statute",
        "s 2(1) of the Human Rights Act 1998, the Occupiers Liability Act of 1957 and LPA 1925.",
        "Law of Property Act 1925; The Law Reform (Contributory Negligence) Act of 1945; TLATA 1996.",
        "Trusts (Jersey) Law 1984. Articles 49 and 50 TEU; Art. 3 (5) TEU; Art. 21 (1) and (2) TEU.",
        "Article 267 TFEU and the European Convention for the Protection of Human Rights.",
    ]

    def test_tables_of_cases_and_legislation(self):
        found = lawnotes.authorities(self.ESSAY)
        self.assertEqual(found["cases"], [
            ("UK cases", [
                ("Re Adams and the Kensington Vestry", ""),          # sorted under A: "Re" is skipped
                ("Re Baden’s Deed Trusts (No 2)", ""),
                ("R v Brown", "[1994] 1 AC 212"),                     # under B: "R v" is skipped
                ("Caparo Industries plc v Dickman", "[1990] 2 AC 605"),  # OSCOLA: no full stops
                ("Donoghue v Stevenson", "[1932] AC 562 (HL)"),      # once, in its cited form
                ("Leigh v Sillavan Ltd v Aliakmon Shipping Co Ltd (The Aliakmon)", "[1986] 1 AC 785"),
            ]),
            ("EU cases", [("Costa v ENEL", "[1964] ECR 585")]),
            ("European Court of Human Rights", [("Osman v United Kingdom", "(2000) 29 EHRR 245")]),
            ("Cases from other jurisdictions", [("Henningsen v Bloomfield Motors, Inc.", "161 A2d 69 (1960)")]),
        ])
        self.assertEqual(found["legislation"], [
            ("UK statutes", ["Human Rights Act 1998", "Law of Property Act 1925",  # LPA 1925 is this Act
                             "Law Reform (Contributory Negligence) Act 1945",
                             "Occupiers Liability Act 1957", "TLATA 1996"]),     # no full title to match
            ("Other legislation", ["Trusts (Jersey) Law 1984"]),
            ("EU treaties", ["Treaty on European Union (TEU) arts 3(5), 21(1) and (2), 49 and 50",
                             "Treaty on the Functioning of the European Union (TFEU) art 267"]),
            ("Treaties and conventions", [lawnotes.ECHR_TITLE]),
        ])
        self.assertEqual(lawnotes._sort_key("The Moorcock"), ["moorcock"])
        self.assertEqual(lawnotes._sort_key("R v Deputy Governor of Parkhurst Prison, Ex parte Hague")[:2], ["deputy", "governor"])
        self.assertEqual(lawnotes.authorities(["Duty of care // see Caparo v Dickman", "no authority here"]),
                         {"cases": [], "legislation": []})

    def test_tables_go_after_the_title(self):
        md = lawnotes.export_markdown(self.ESSAY, tables=True)
        body = md.index("Lord Atkin in *Donoghue v Stevenson*")
        self.assertLess(md.index("# Duty of Care"), md.index("## Table of Cases"))
        self.assertLess(md.index("## Table of Cases"), md.index("## Table of Legislation"))
        self.assertLess(md.index('<w:br w:type="page"/>'), body)              # a new page for the essay
        self.assertIn("\nDonoghue v Stevenson \\[1932\\] AC 562 (HL)\n", md)   # roman in the table (OSCOLA)
        self.assertIn("\nR v Brown \\[1994\\] 1 AC 212\n", md)
        self.assertNotIn("Fake v Case", md)                                     # comments stay out
        self.assertEqual(md[body:], lawnotes.export_markdown(self.ESSAY)[lawnotes.export_markdown(self.ESSAY).index(
            "Lord Atkin"):])                                                    # the note itself unchanged
        self.assertEqual(lawnotes.export_markdown(["Duty of care", "Breach"], tables=True), "Duty of care\nBreach\n")
        untitled = lawnotes.export_markdown(["Donoghue v Stevenson applies"], tables=True)
        self.assertTrue(untitled.startswith("\n## Table of Cases\n\nDonoghue v Stevenson\n"))  # no title: first

    def test_theme_files(self):
        for name, palette in lawnotes.THEMES.items():
            self.assertEqual(lawnotes.parse_theme(lawnotes.theme_text(name, palette)), (palette, []), name)
        palette, problems = lawnotes.parse_theme(
            "# mine\nbackground = #000000\ntext = #ffffff\nselection = #000 on #ff8700\nbogus = 1\nkeys = #zzz\n")
        self.assertEqual(palette["text"], (231, 16))     # white on the new black background
        self.assertEqual(palette["h1"], (208, 16))       # unchanged colour, new background
        self.assertEqual(palette["sel"], (16, 208))
        self.assertEqual(palette["bar"], lawnotes.THEMES["NERV"]["bar"])  # its own background stays
        self.assertEqual(problems, ["line 5: unknown name “bogus”",
                                    "line 6: “#zzz” isn't a colour: use #rrggbb or a number 0–255"])

    def test_older_theme_files_get_a_link_colour(self):
        for name in lawnotes.THEMES:
            self.assertIn("link", lawnotes.THEMES[name], name)
        def without_links(name):
            text = lawnotes.theme_text("Old", lawnotes.THEMES[name])
            return "".join(l for l in text.splitlines(True) if not l.startswith("links ="))
        palette, problems = lawnotes.parse_theme(without_links("NERV"))   # a theme file from before links
        self.assertEqual((palette["link"], problems), (lawnotes.THEMES["NERV"]["link"], []))
        palette, problems = lawnotes.parse_theme(without_links("Paper"))  # light: NERV's colour would be unreadable
        self.assertEqual((palette["link"], problems), ((lawnotes.THEMES["Paper"]["h"][0], 230), []))

    def test_links_find_their_note(self):
        index = lawnotes.link_index(["Tort/Negligence.md", "Contract/Offer.md", "Land/Offer.md", "Index.md",
                                     "Misc/Old.txt", os.path.join("Tort", "Duty", "Breach.md")])
        find = lambda t: lawnotes.link_matches(t, index)
        self.assertEqual(find("Tort/Negligence"), ["Tort/Negligence.md"])
        self.assertEqual(find("tort/negligence "), ["Tort/Negligence.md"])      # any case, spaces trimmed
        self.assertEqual(find("Negligence"), ["Tort/Negligence.md"])            # bare name: the only one
        self.assertEqual(find("Breach"), [os.path.join("Tort", "Duty", "Breach.md")])
        self.assertEqual(find("Offer"), ["Contract/Offer.md", "Land/Offer.md"])  # two: ambiguous
        self.assertEqual(find("Land/Offer"), ["Land/Offer.md"])
        self.assertEqual(find("Index"), ["Index.md"])
        self.assertEqual(find("Misc/Old"), ["Misc/Old.txt"])
        self.assertEqual(find("Tort/Missing"), [])
        self.assertEqual(find("Missing"), [])
        self.assertEqual(lawnotes.link_name(os.path.join("Tort", "Negligence.md")), "Tort/Negligence")

    def test_link_syntax_and_export(self):
        line = "See [[Tort/Negligence#Duty of care|the duty]], [[Offer]] and [[Tort/Duty/Breach#Standard]]."
        self.assertEqual([m.groups() for m in lawnotes.LINK_RE.finditer(line)], [
            ("Tort/Negligence", "Duty of care", "the duty"), ("Offer", None, None),
            ("Tort/Duty/Breach", "Standard", None)])
        self.assertEqual([m.group() for m in lawnotes.LINK_RE.finditer("[[ ]] [x] [[]] [[a]")], [])
        md = lawnotes.export_markdown([line, "# [[Tort/Negligence]]", "[[Cases|Donoghue v Stevenson]] applies"])
        self.assertEqual(md.split("\n")[:3], ["See the duty, Offer and Breach.", "# Negligence",
                                              "*Donoghue v Stevenson* applies"])  # shown text italicised as a case

    @staticmethod
    def highlighter():
        roles = ["text", "h1", "h", "case", "cite", "statute", "bullet", "quote", "mark", "comment", "link",
                 "dim", "party_D", "party_V", "party_C", "bold", "italic"]
        app = object.__new__(lawnotes.App)
        app.A = {r: (n + 1) << 8 for n, r in enumerate(roles)}
        app._hl, app.italic, app._links = {}, 0, (0, None)
        return app

    def test_bold_and_italic_colours(self):
        app = self.highlighter()
        A = app.A
        line = "a **duty** of *care*: *Donoghue v Stevenson* [1932] AC 562"
        attrs = app.highlight(line)
        self.assertEqual(set(attrs[2:10]), {A["bold"]})               # **duty**, stars included
        self.assertEqual(set(attrs[14:20]), {A["italic"]})            # *care*
        self.assertEqual(attrs[0], A["text"])
        case = line.index("Donoghue")
        self.assertEqual(attrs[case], A["case"])                       # a case name keeps its own colour
        self.assertEqual(attrs[line.index("*Donoghue")], A["italic"])  # its stars take the italic colour
        quote = app.highlight("> **held**")
        self.assertEqual(quote[3], A["bold"])                          # in quotes too
        palette, problems = lawnotes.parse_theme("background = #ffffd7\ntext = #262626\n")
        self.assertEqual((problems, palette["bold"][0], palette["italic"][0]), ([], 235, 235))  # older light theme files: plain

    def test_link_highlight_and_hint(self):
        app = self.highlighter()
        app._links = (time.time() + 60, lawnotes.link_index(["Tort/Negligence.md"]))
        line = "a [[Negligence]] b [[Nowhere]]"
        attrs = app.highlight(line)
        self.assertEqual(attrs[2], app.A["link"])
        self.assertEqual(attrs[19], app.A["dim"])      # no such note: dimmed
        self.assertEqual(attrs[0], app.A["text"])
        app.buf = lawnotes.Buffer(os.path.join(TMP, "Hint.md"))
        app.buf.lines, app.buf.cy, app.buf.cx = [line], 0, 16      # just after the first link
        self.assertEqual(app.link_hint(), "→ Tort/Negligence   Option+L opens it")
        app.buf.cx = 22
        self.assertEqual(app.link_hint(), "No note “Nowhere” yet   Option+L creates it")
        app.buf.cx = 17
        self.assertIsNone(app.link_hint())

    def test_spelling_preview_and_next_suggestion(self):
        class Speller:
            state = "ready"
            guesses = {"recieve": ["receive", "relieve", "reprieve", "recede"]}

            def lookup(self, line):
                i = line.find("recieve")
                return ((i, 7),) if i >= 0 else ()

            def wait_for(self, lines):
                pass

            def suggestions(self, word):
                return self.guesses.get(word, [])
        app = object.__new__(lawnotes.App)
        app.speller, app.ignored, app.protected, app.spell_cycle = Speller(), set(), set(), None
        app.highlight = lambda line: [0] * len(line)
        menus = []
        app.spelling_menu = lambda *args: menus.append(args)
        b = app.buf = lawnotes.Buffer(os.path.join(TMP, "Spell.md"))
        b.lines, b.cy, b.cx = ["fine", "I recieve it"], 0, 0
        self.assertIsNone(app.spell_hint())
        app.fix_spelling()                                    # not on a mistake: go to the next one
        self.assertEqual((b.cy, b.cx), (1, 2))
        self.assertEqual(app.spell_hint(), "recieve → receive   ^W to fix")
        app.fix_spelling()                                    # makes the correction shown
        self.assertEqual(b.lines[1], "I receive it")
        self.assertEqual(app.spell_hint(), "✓ recieve → receive   ^W next: relieve · ^Z undo")
        app.fix_spelling()
        app.fix_spelling()
        self.assertEqual(b.lines[1], "I reprieve it")
        self.assertEqual(app.spell_hint(), "✓ recieve → reprieve   ^W more options · ^Z undo")
        b.undo()                                              # one ^Z undoes the whole fix
        self.assertEqual(b.lines[1], "I recieve it")
        b.cy, b.cx = 1, 12
        app.fix_spelling()
        app.fix_spelling()
        app.fix_spelling()
        app.fix_spelling()                                    # past the third: back, and the full list
        self.assertEqual(b.lines[1], "I recieve it")
        self.assertEqual(menus, [(1, 2, "recieve", ["receive", "relieve", "reprieve", "recede"])])

    def test_italic_typo_does_not_crash(self):
        """Scrolling tort.md crashed: pair_number() overflowed on an italic misspelt word."""
        import curses
        self.assertEqual(lawnotes.pair_of(curses.A_ITALIC | curses.A_BOLD | (3 << 8)), 3)

        class Speller:
            def lookup(self, line):
                return ((0, 3),)  # "teh"
        app = object.__new__(lawnotes.App)
        app.speller, app.ignored, app.protected = Speller(), set(), {9}
        app.highlight = lambda line: [curses.A_ITALIC | (1 << 8)] * len(line)
        self.assertEqual(app.misspelt("teh case"), [(0, 3)])

    def test_word_table_becomes_markdown(self):
        word = ("Case\tCourt\tHeld\r"
                "Donoghue v Stevenson\tHL\tDuty of care owed\r"
                "Caparo v Dickman\tHL\tThree-stage test\rFair, just | reasonable\r")
        self.assertEqual(lawnotes.table_from_text(word), "\n".join([
            "| Case                 | Court | Held                                         |",
            "|----------------------|-------|----------------------------------------------|",
            "| Donoghue v Stevenson | HL    | Duty of care owed                            |",
            "| Caparo v Dickman     | HL    | Three-stage test<br>Fair, just \\| reasonable |",
        ]))
        self.assertIsNone(lawnotes.table_from_text("one line\twith a tab"))     # not a table
        self.assertIsNone(lawnotes.table_from_text("Para one\nPara\ttwo\nThree\nFour"))

    def test_comments(self):
        spans = lambda t: [m.group() for m in lawnotes.COMMENT_RE.finditer(t)]
        self.assertEqual(spans("Duty of care // check Caparo"), ["// check Caparo"])
        self.assertEqual(spans("// whole line"), ["// whole line"])
        self.assertEqual(spans("see https://www.bailii.org/x"), [])           # a link isn't a comment
        md = lawnotes.export_markdown(["Duty of care // check Caparo", "// to self", "Breach"])
        self.assertEqual(md, "Duty of care\nBreach\n")                      # left out of exports

    def test_abbreviations(self):
        ab = {"CL": "Common Law", "EL": "Equity Law", "LCh": "Lord Chancellor"}
        self.assertEqual(lawnotes.expand_abbreviation("At CL", 5, ab), ("At Common Law", 13))
        self.assertEqual(lawnotes.expand_abbreviation("the LCh", 7, ab), ("the Lord Chancellor", 19))
        self.assertIsNone(lawnotes.expand_abbreviation("CLR", 3, ab))          # whole words only
        self.assertIsNone(lawnotes.expand_abbreviation("cl", 2, ab))           # exact case
        self.assertIsNone(lawnotes.expand_abbreviation("xCL", 3, ab))
        self.assertEqual(lawnotes.load_abbreviations(os.path.join(TMP, "none.txt")), lawnotes.DEFAULT_ABBREVIATIONS)
        f = os.path.join(TMP, "abbr.txt")
        open(f, "w").write("# mine\nRA = reasonable adult\nCL = common law\n")
        self.assertEqual(lawnotes.load_abbreviations(f)["RA"], "reasonable adult")
        self.assertEqual(lawnotes.load_abbreviations(f)["CL"], "common law")    # yours win

    def test_abbreviations_file_and_suggestions(self):
        notes = [["In EQ, B holds on a RT for A", "a CT; [1932] AC 562; esp. here"], ["EQ again", "an ET/RT", "EQUITY"]]
        suggested = lawnotes.abbreviation_suggestions(notes, {"RT": "resulting trust"})
        self.assertEqual(suggested, [("EQ", "Equity", 2), ("CT", "constructive trust", 1), ("esp", "especially", 1)])
        self.assertFalse(set(lawnotes.SUGGESTED_ABBREVIATIONS) & lawnotes.NEVER_EXPAND)
        new = lawnotes.abbreviations_text("", suggested)                  # a new list: header, built-ins, suggestions
        self.assertTrue(new.startswith("# Abbreviations"))
        self.assertIn("CL = Common Law\nEL = Equity Law\nLCh = Lord Chancellor\n\n# Suggested from your notes", new)
        self.assertTrue(new.endswith("# EQ = Equity\n# CT = constructive trust\n# esp = especially\n"))
        mine = new.replace("# EQ = Equity", "EQ = Equity") + "AC = Appeal Cases\nC = claimant\n"
        f = os.path.join(tempfile.mkdtemp(dir=TMP), ".abbreviations.txt")
        with open(f, "w") as out:
            out.write(mine)
        ab = lawnotes.load_abbreviations(f)
        self.assertEqual((ab["EQ"], "CT" in ab, "AC" in ab, "C" in ab), ("Equity", False, False, False))
        self.assertEqual([r.split(" ")[0] for r in lawnotes.abbreviation_rejects(f)], ["AC", "C"])
        again = lawnotes.abbreviations_text(mine, [("CT", "constructive trust", 1)])  # only the block is rewritten
        self.assertTrue(again.startswith(mine.split("# Suggested")[0].rstrip("\n") + "\n\n# Suggested"))
        self.assertTrue(again.endswith("# CT = constructive trust\n"))
        self.assertNotIn("AC = Appeal Cases", again)                    # (it sat after the mark, in the block)
        self.assertEqual(lawnotes.abbreviations_text("CL =\n", []), "CL =\n")  # nothing to suggest: as it was
        for short in ("AC", "QB", "LJ", "D", "C", "C T", "2x"):             # citations, judges, parties, junk
            self.assertIsNotNone(lawnotes.abbreviation_problem(short), short)
        self.assertIsNone(lawnotes.abbreviation_problem("DoC"))

    def test_highlight_markup(self):
        spans = lambda t: [m.group() for m in lawnotes.MARK_RE.finditer(t)]
        self.assertEqual(spans("The ==ratio decidendi== binds; ==Caparo== too"), ["==ratio decidendi==", "==Caparo=="])
        self.assertEqual(spans("a == b and c == d"), [])                 # not a highlight

    def test_party_labels(self):
        found = lambda t: [m.group() for m in lawnotes.PARTY_RE.finditer(t)]
        self.assertEqual(found("D punched V. C sued D1 and D2."), ["D", "V", "C", "D1", "D2"])
        self.assertEqual(found("D's intention"), ["D's"])
        self.assertEqual(found("Part V of the Act; a C-section"), [])

    def test_wrap(self):
        self.assertEqual(lawnotes.wrap_starts("hello world foo", 8), (0, 6, 12))
        self.assertEqual(lawnotes.wrap_starts("x" * 20, 8), (0, 8, 16))

    def test_resolve_note(self):
        self.assertEqual(lawnotes.resolve_note("Tort/Duty"), os.path.join(lawnotes.NOTES_DIR, "Tort", "Duty.md"))

    def test_version_says_beta(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes.py"), "--version"],
                           capture_output=True, text=True)
        self.assertIn("beta", r.stdout)
        self.assertIn("beta", lawnotes.HELP_TITLE)

    def test_failed_history_snapshot_is_logged(self):
        broken = os.path.join(TMP, "history-is-a-file")
        open(broken, "w").write("not a git store")
        saved = lawnotes.HISTORY_DIR
        lawnotes.HISTORY_DIR = broken
        try:
            lawnotes.History().snapshot(wait=True)
        finally:
            lawnotes.HISTORY_DIR = saved
        self.assertIn("history snapshot failed", open(lawnotes.LOG_FILE).read())

    def test_self_test_passes(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes.py"), "--self-test"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


BRIEF_NOTE = """# Negligence

## Donoghue v Stevenson
- **Citation:** [1932] AC 562
- **Court:** HL
- **Facts:** snail in a bottle of ginger beer // check the report
- **Issue:** 
- **Held:** a manufacturer owes a duty of care to the ultimate consumer of its products
- **Obiter:** 

## Caparo Industries plc v Dickman [1990] 2 AC 605
- **Citation:** 
- **Facts:** 

## Duty of care
- Caparo Industries plc v Dickman [1990] 2 AC 605: three-stage test
  - foreseeability, proximity, fair just and reasonable
  - Robinson v Chief Constable of West Yorkshire [2018] UKSC 4: not a test for every case
- Anns v Merton [1978] AC 728
- [ ] read Smith v Jones on breach
- Duty: Donoghue v Stevenson [1932] AC 562
> Hedley Byrne v Heller on negligent misstatement

## Problem: Is D liable under Caparo v Dickman?
- Whether D owes V a duty, applying Caparo Industries plc v Dickman to the facts
"""


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.saved = lawnotes.REVISION_FILE
        self.dir = tempfile.mkdtemp(dir=TMP)
        lawnotes.REVISION_FILE = os.path.join(self.dir, ".revision.json")

    def tearDown(self):
        lawnotes.REVISION_FILE = self.saved

    def test_cards_from_briefs_and_bullets(self):
        cards = lawnotes.note_cards("Tort/Negligence.md", BRIEF_NOTE.split("\n"))
        self.assertEqual([(c["front"], c["line"]) for c in cards], [
            ("Donoghue v Stevenson [1932] AC 562", 2),             # the brief; citation from its field
            ("Caparo Industries plc v Dickman", 15),               # unfilled brief: the bullet instead
            ("Robinson v Chief Constable of West Yorkshire", 17),  # a sub-bullet about another case
        ])                                                         # not: Anns (nothing said), to-dos, quotes
        donoghue, caparo, robinson = cards
        self.assertEqual(donoghue["id"], "Tort/Negligence.md::donoghue v stevenson")
        self.assertEqual(donoghue["back"], [                       # blank fields and comments left out
            "- **Court:** HL",
            "- **Facts:** snail in a bottle of ginger beer",
            "- **Held:** a manufacturer owes a duty of care to the ultimate consumer of its products"])
        self.assertEqual(caparo["back"], [                         # the bullet and its sub-bullets only,
            "- Caparo Industries plc v Dickman [1990] 2 AC 605: three-stage test",  # not the problem answer
            "  - foreseeability, proximity, fair just and reasonable",
            "  - Robinson v Chief Constable of West Yorkshire [2018] UKSC 4: not a test for every case"])
        self.assertEqual(robinson["back"], ["- Robinson v Chief Constable of West Yorkshire [2018] UKSC 4: "
                                            "not a test for every case"])
        self.assertEqual(lawnotes.note_cards("x.md", ["# Duty of care", "- foreseeability", "## R v Brown",
                                                      "- **Held:** consent no defence to ABH"])[0]["front"],
                         "R v Brown")
        self.assertEqual(lawnotes.note_cards("x.md", lawnotes.TEMPLATES[0][1]), [])  # an empty template

    def test_schedule_and_queue(self):
        now = time.mktime((2026, 10, 15, 12, 0, 0, 0, 0, -1))
        good = lawnotes.schedule(None, 3, now)
        self.assertEqual((good["box"], good["due"], good["reviews"]), (1, "2026-10-16", 1))
        good = lawnotes.schedule(good, 3, now)
        self.assertEqual((good["box"], good["due"]), (2, "2026-10-18"))
        self.assertEqual(lawnotes.schedule(good, 2, now)["due"], "2026-10-18")  # hard: same interval
        self.assertEqual(lawnotes.schedule(None, 2, now)["due"], "2026-10-16")  # ...at least a day
        again = lawnotes.schedule(good, 1, now)
        self.assertEqual((again["box"], again["due"], again["reviews"]), (0, "2026-10-15", 3))
        self.assertEqual(lawnotes.schedule({"box": "junk"}, 3, now)["box"], 1)  # a damaged entry
        cards = [{"id": n} for n in "abcde"]
        progress = {"a": {"due": "2026-10-20"}, "b": {"due": "2026-10-15"}, "d": {"due": "2026-10-01"}}
        self.assertEqual([c["id"] for c in lawnotes.revision_queue(cards, progress, "2026-10-15")],
                         ["d", "b", "c", "e"])                     # due (oldest first), new; a waits

    def test_progress_merges_and_saves_atomically(self):
        path = lawnotes.REVISION_FILE
        self.assertEqual(lawnotes.load_progress(), {})
        open(path, "w").write("{not json")
        self.assertEqual(lawnotes.load_progress(), {})              # damaged: starts again, no crash
        lawnotes.save_progress({"x": {"box": 3, "at": 100.0}, "y": {"box": 1, "at": 50.0}})
        # another Mac: a later review of y, an older one of x, and an iCloud conflict copy
        copy = os.path.join(self.dir, ".revision 2.json")
        json.dump({"version": 1, "cards": {"x": {"box": 1, "at": 90.0}, "y": {"box": 2, "at": 60.0},
                                           "z": {"box": 4, "at": 10.0}}}, open(copy, "w"))
        merged = lawnotes.save_progress({"w": {"box": 1, "at": 120.0}})
        self.assertEqual({k: v["box"] for k, v in merged.items()}, {"x": 3, "y": 2, "z": 4, "w": 1})
        self.assertEqual(json.load(open(path))["cards"], merged)
        self.assertEqual(sorted(os.listdir(self.dir)), [".revision.json"])  # copy merged; no temp file left

    def test_history_leaves_progress_out(self):
        self.assertIn(".*", lawnotes.HISTORY_EXCLUDE.split("\n"))
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(os.path.join(lawnotes.NOTES_DIR, "Rev.md"), "w").write("# Rev\n")
        lawnotes.REVISION_FILE = self.saved
        lawnotes.save_progress({"Rev.md::r v brown": {"box": 1, "at": 1.0}})
        lawnotes.History().snapshot(wait=True)
        files = subprocess.run(["git", f"--git-dir={lawnotes.HISTORY_DIR}", "ls-tree", "-r", "--name-only", "HEAD"],
                               capture_output=True, text=True).stdout.split()
        self.assertIn("Rev.md", files)
        self.assertNotIn(".revision.json", files)


class ReplaceTests(unittest.TestCase):
    """Find and replace, in a notes folder and history store of its own."""

    def setUp(self):
        self.saved = lawnotes.NOTES_DIR, lawnotes.HISTORY_DIR
        self.dir = tempfile.mkdtemp(dir=TMP)
        lawnotes.NOTES_DIR = os.path.join(self.dir, "notes")
        lawnotes.HISTORY_DIR = os.path.join(self.dir, "history.git")

    def tearDown(self):
        lawnotes.NOTES_DIR, lawnotes.HISTORY_DIR = self.saved

    def note(self, rel, text):
        path = os.path.join(lawnotes.NOTES_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def app(self, path):
        """An App without a screen: enough for replacing, with pick() and ask() answered by the test."""
        app = object.__new__(lawnotes.App)
        app.pane = lawnotes.Pane()
        app.layout = lawnotes.Layout.leaf(app.pane)
        for a in lawnotes.App.VIEW_ATTRS:
            setattr(app, a, getattr(app.pane, a))
        app.buf = lawnotes.Buffer(path)
        app.history = lawnotes.History()
        app.search, app.msg, app.msg_until = "", "", 0.0
        app.save_failed, app.last_save_try, app.recovery_path, app.theme = False, 0.0, None, "NERV"
        app.draw = lambda refresh=True: None
        app.picks = []
        app.pick = lambda title, build, *a, **k: app.picks.append((title, build(""))) or app.choice
        app.choice = None
        return app

    def test_replace_lines_and_cursor(self):
        self.assertEqual(lawnotes.replace_lines(["[1932] AC 532 and [1932] AC 532", "ac 532"], "AC 532", "AC 562"),
                         (["[1932] AC 562 and [1932] AC 562", "ac 532"], 2))  # capitals must match
        self.assertEqual(lawnotes.replace_lines(["x"], "y", "z"), (["x"], 0))
        line = "Donohue v Stevenson; Donohue"
        self.assertEqual(lawnotes.moved_col(line, 10, "Donohue", "Donoghue"), 11)  # after one match
        self.assertEqual(lawnotes.moved_col(line, 0, "Donohue", "Donoghue"), 0)    # at a match's start
        self.assertEqual(lawnotes.moved_col(line, 3, "Donohue", "Donoghue"), 8)    # inside: end of it
        self.assertEqual(lawnotes.moved_col(line, 28, "Donohue", "D"), 16)          # end of line

    def test_this_note_is_one_undo_step(self):
        a = self.note("A.md", "Donohue v Stevenson\nper Lord Atkin in Donohue\n")
        app = self.app(a)
        b = app.buf
        b.cy, b.cx = 1, 25
        app.choice = "note"
        prompts = iter(["Donohue", "Donoghue"])
        app.prompt = lambda label, text="": next(prompts)
        app.replace()
        self.assertEqual([label for label, _ in app.picks[0][1]][:2],
                         ["This note — 2 matches", "All notes — 2 matches in 1 note"])
        self.assertEqual(b.lines, ["Donoghue v Stevenson", "per Lord Atkin in Donoghue"])
        self.assertEqual((b.cy, b.cx), (1, 26))
        self.assertEqual(app.search, "Donoghue")                     # the replacements are highlighted
        b.undo()
        self.assertEqual(b.lines, ["Donohue v Stevenson", "per Lord Atkin in Donohue"])

    def test_selection_starts_the_prompt(self):
        a = self.note("Sel.md", "see Caparo v Dickman\n")
        app = self.app(a)
        app.anchor, app.buf.cy, app.buf.cx = (0, 4), 0, 10
        asked = []
        app.prompt = lambda label, text="": asked.append((label, text))
        app.replace()
        self.assertEqual(asked, [("Replace: ", "Caparo")])

    def test_one_at_a_time(self):
        a = self.note("Review.md", "Donohue and Donohue\nDonohue\nDonohue\n")
        app = self.app(a)
        app.buf.cy, app.buf.cx = 2, 3
        answers, questions = iter(["n", "y", "a"]), []
        app.ask = lambda q, keys: questions.append((q, app.selection())) or next(answers)
        app.replace_review("Donohue", "Donoghue", "“Donoghue”")
        b = app.buf
        self.assertEqual(b.lines, ["Donohue and Donoghue", "Donoghue", "Donoghue"])
        self.assertIn("(1 of 4)", questions[0][0])
        self.assertEqual(questions[1][1], ((0, 12), (0, 19)))        # each match is selected in turn
        self.assertIsNone(app.anchor)
        b.undo()                                                     # one ^Z undoes them all
        self.assertEqual(b.lines, ["Donohue and Donohue", "Donohue", "Donohue"])
        self.assertEqual((b.cy, b.cx), (2, 3))                       # and goes back to where you were
        self.assertEqual(app.search, "Donoghue")                     # the replacements were highlighted
        app.search = "neighbour"
        answers = iter([None])                                       # Esc at once: nothing changes
        app.replace_review("Donohue", "Donoghue", "“Donoghue”")
        self.assertEqual(b.lines, ["Donohue and Donohue", "Donohue", "Donohue"])
        self.assertEqual(app.search, "neighbour")                    # the find highlight is kept

    def test_all_notes(self):
        a = self.note("A.md", "Donohue v Stevenson [1932] AC 532\n")
        bpath = self.note("B.md", "Donohue\n")
        c = self.note("Tort/C.md", "See Donohue.\nDonohue twice: Donohue\n")
        d = self.note("ro/D.md", "Donohue\n")
        untouched = {p: open(p).read() for p in (
            self.note(".hidden.md", "Donohue\n"), self.note(".themes/Mine.txt", "Donohue\n"),
            self.note("slides.rtf", "Donohue\n"), self.note("E.md", "donohue\n"))}
        app = self.app(a)
        app.buf.cx = 19                                              # after "Stevenson"
        right = lawnotes.Pane()                                      # B open in a second pane,
        right.buf = lawnotes.Buffer(bpath)                           # with an unsaved change
        right.buf.lines.append("Donohue again")
        right.buf.dirty = True
        app.layout = lawnotes.Layout("v", lawnotes.Layout.leaf(app.pane), lawnotes.Layout.leaf(right))
        os.chmod(os.path.dirname(d), 0o500)
        try:
            notes = app.notes_containing("Donohue")
            self.assertEqual(sorted(rel for _, rel, _, _ in notes), ["A.md", "B.md", "Tort/C.md", "ro/D.md"])
            app.replace_everywhere("Donohue", "Donoghue", notes)
        finally:
            os.chmod(os.path.dirname(d), 0o700)
        self.assertEqual(app.buf.lines, ["Donoghue v Stevenson [1932] AC 532"])
        self.assertEqual(app.buf.cx, 20)
        self.assertEqual(open(a).read(), "Donoghue v Stevenson [1932] AC 532\n")  # open notes are saved
        self.assertEqual(open(bpath).read(), "Donoghue\nDonoghue again\n")
        self.assertEqual(open(c).read(), "See Donoghue.\nDonoghue twice: Donoghue\n")
        self.assertEqual(open(d).read(), "Donohue\n")
        for p, text in untouched.items():
            self.assertEqual(open(p).read(), text, p)
        self.assertIn("Replaced 6 in 3 notes", app.msg)
        title, problems = app.picks[0]
        self.assertIn("1 couldn't be written", title)
        self.assertEqual([path for _, path in problems], [d])
        self.assertTrue(problems[0][0].startswith("ro/D.md — not changed"))
        right.buf.undo()                                             # ^Z in an open note
        self.assertEqual(right.buf.lines, ["Donohue", "Donohue again"])
        h = app.history                                              # ^R has the closed notes as they were
        self.assertEqual(h.content(h.versions(c)[0][0], c), "See Donohue.\nDonohue twice: Donohue\n")
        self.assertEqual(sorted(os.listdir(os.path.dirname(c))), ["C.md"])  # no conflict or temp files


@unittest.skipUnless(lawnotes.find_tool("pandoc"), "export needs pandoc (brew install pandoc)")
class ExportTests(unittest.TestCase):
    NOTE = [
        "# Negligence",
        "",
        "Duty: Donoghue v Stevenson [1932] AC 562 applies.[^1]",
        "",
        "-- the claimant's burden",
        "",
        "    - the burden of proof, on balance",
        "    - ",
        "    indented text that is not code",
        "",
        "[^1]: Lord Atkin's neighbour principle.",
    ]

    def setUp(self):
        self.out = tempfile.mkdtemp(dir=TMP)
        lawnotes.EXPORT_DIR = self.out

    def test_word(self):
        import zipfile
        path = lawnotes.export_note(os.path.join(TMP, "Negligence.md"), self.NOTE, "docx")
        z = zipfile.ZipFile(path)
        doc, notes = z.read("word/document.xml").decode(), z.read("word/footnotes.xml").decode()
        self.assertIn("neighbour principle", notes)                      # a real footnote
        self.assertRegex(doc, r"<w:i ?/>.*?Donoghue v Stevenson")          # case name in italics (OSCOLA)
        self.assertIn("the burden of proof", doc)                         # nothing eaten by a "table"
        self.assertNotIn("<w:tbl>", doc)
        self.assertNotIn("SourceCode", doc)                               # indentation isn't code
        self.assertIn("Times New Roman", z.read("word/styles.xml").decode())

    def test_pdf(self):
        path = lawnotes.export_note(os.path.join(TMP, "Negligence.md"), self.NOTE, "pdf")
        data = open(path, "rb").read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 2000)

    def test_pasted_table_exports_as_a_word_table(self):
        import zipfile
        table = lawnotes.table_from_text("Case\tHeld\nDonoghue v Stevenson\tDuty owed\n")
        path = lawnotes.export_note(os.path.join(TMP, "Table.md"), ["# Cases", ""] + table.split("\n"), "docx")
        doc = zipfile.ZipFile(path).read("word/document.xml").decode()
        self.assertIn("<w:tbl>", doc)
        self.assertIn("Duty owed", doc)

    def test_highlight_exports_as_word_highlight(self):
        import zipfile
        path = lawnotes.export_note(os.path.join(TMP, "Mark.md"), ["The ==ratio decidendi== binds."], "docx")
        doc = zipfile.ZipFile(path).read("word/document.xml").decode()
        self.assertRegex(doc, r'<w:highlight w:val="yellow" ?/>')
        self.assertIn("ratio decidendi", doc)
        self.assertNotIn("==", doc)

    def test_word_with_tables(self):
        import zipfile
        path = lawnotes.export_note(os.path.join(TMP, "Essay.md"), TextTests.ESSAY, "docx", tables=True)
        doc = zipfile.ZipFile(path).read("word/document.xml").decode()
        order = [doc.index(t) for t in ("Duty of Care in Negligence", "Table of Cases", "UK cases",
                                        "Re Adams and the Kensington Vestry", "Table of Legislation",
                                        "Human Rights Act 1998", '<w:br w:type="page"', "Lord Atkin in")]
        self.assertEqual(order, sorted(order))                 # title, tables, page break, then the note
        self.assertNotIn("Fake v Case", doc)
        plain = lawnotes.export_note(os.path.join(TMP, "Essay.md"), TextTests.ESSAY, "docx")
        self.assertNotIn("Table of Cases", zipfile.ZipFile(plain).read("word/document.xml").decode())

    def test_pdf_with_tables(self):
        path = lawnotes.export_note(os.path.join(TMP, "Essay.md"), TextTests.ESSAY, "pdf", tables=True)
        self.assertTrue(open(path, "rb").read().startswith(b"%PDF"))

    def test_export_menu_offers_tables(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Essay.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("\n".join(TextTests.ESSAY) + "\n")
        s = Session(note, env={"LAWNOTES_EXPORT_DIR": self.out})
        screen = s.keys(b"\x1b[15~", 0.6)                                       # F5
        self.assertIn("with tables of cases and legislation", screen)
        s.keys(b"Word (.docx) with\r", 0.3)
        out = os.path.join(self.out, "Essay.docx")
        for _ in range(100):
            if os.path.exists(out):
                break
            s.out += s.read(0.1)
        self.assertTrue(s.quit())
        import zipfile
        self.assertIn("Table of Legislation", zipfile.ZipFile(out).read("word/document.xml").decode())
        self.assertEqual(open(note).read(), "\n".join(TextTests.ESSAY) + "\n")  # the note isn't changed

    def test_links_export_as_plain_text(self):
        import zipfile
        path = lawnotes.export_note(os.path.join(TMP, "Links.md"),
                                    ["See [[Tort/Negligence#Duty|the duty of care]] and [[Contract/Offer]]."], "docx")
        doc = zipfile.ZipFile(path).read("word/document.xml").decode()
        self.assertIn("the duty of care", doc)
        self.assertIn("Offer", doc)
        self.assertNotIn("[[", doc)
        self.assertNotIn("Contract/", doc)

    def test_markdown_preparation(self):
        md = lawnotes.export_markdown(self.NOTE).split("\n")
        self.assertIn("Duty: *Donoghue v Stevenson* [1932] AC 562 applies.[^1]", md)
        self.assertIn("- the burden of proof, on balance", md)          # outside a list: not code
        self.assertNotIn("    - ", md)                                     # empty bullet dropped
        self.assertEqual(lawnotes.export_markdown(["*Re Polemis* applies"]).strip(), "*Re Polemis* applies")


class Session:
    """The editor running in a pseudo-terminal, driven by keystrokes."""

    def __init__(self, *args, env=None, size=(30, 100)):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ.update({"TERM": "xterm-256color", **(env or {})})
            os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "lawnotes.py"), *args])
        import fcntl
        import termios
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
        self.out = self.read(2)

    def read(self, seconds=0.4):
        out, end = b"", time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.02)[0]:
                try:
                    out += os.read(self.fd, 65536)
                except OSError:
                    break
        return out

    def keys(self, data, seconds=0.4):
        # Write only as fast as the editor reads: a full terminal input buffer must never block the
        # test forever (an editor that stops reading fails the test, showing what it was doing).
        deadline = time.time() + 30
        while data:
            if not select.select([], [self.fd], [], 0.1)[1]:
                self.out += self.read(0.05)  # keep draining its output so it can't block either
                if time.time() > deadline:
                    raise AssertionError("the editor stopped reading input for 30 s; last screen:\n"
                                         + self.out[-1500:].decode(errors="replace"))
                continue
            os.set_blocking(self.fd, False)
            try:
                data = data[os.write(self.fd, data[:256]):]
            except BlockingIOError:
                continue
            except OSError:
                break
            finally:
                os.set_blocking(self.fd, True)
        out = self.read(seconds)
        self.out += out
        return re.sub(rb"\x1b\[[0-9;?<>]*[A-Za-z~]|\x1b\(B|\x1b[=>]", b" ", out).decode(errors="replace")

    def quit(self, seconds=3):
        """^Q, then wait for the editor to exit; kill it and return False if it doesn't."""
        self.keys(b"\x11", 1.5)
        for _ in range(int(seconds * 10)):
            if os.waitpid(self.pid, os.WNOHANG)[0] == self.pid:
                return True
            self.out += self.read(0.1)
        os.kill(self.pid, 9)
        while os.waitpid(self.pid, os.WNOHANG)[0] != self.pid:
            self.out += self.read(0.1)  # a killed editor can't finish exiting until its output is read
        return False


class SessionTests(unittest.TestCase):
    def test_double_and_triple_click(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Click.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("Duty owed by D\nDonoghue v Stevenson [1932] AC 562 applies\nthird line\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note)
        s.keys(click(2, 1) + click(2, 1) + b"Breach", 0.5)            # double-click a word
        s.keys(click(6, 2) + click(6, 2) + b"Caparo", 0.5)            # double-click inside a case name
        s.keys(click(3, 3) + click(3, 3) + click(3, 3) + b"X\x13", 0.6)  # triple-click a line
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["Breach owed by D", "Caparo [1932] AC 562 applies", "X"])

    def test_clicks_where_terminfo_claims_mouse_prefix(self):
        # Ghostty's terminfo has kmous=\E[<, so curses returns KEY_MOUSE for ESC [ < and leaves the
        # rest of each report; it used to be typed into the note ("64;45;41M64;45;41M...").
        terminfo = tempfile.mkdtemp(dir=TMP)
        src = os.path.join(terminfo, "kmous.src")
        open(src, "w").write("lawnotes-kmous|xterm-256color plus Ghostty mouse key,\n\tkmous=\\E[<, use=xterm-256color,\n")
        subprocess.run(["tic", "-o", terminfo, src], check=True, capture_output=True)
        note = os.path.join(lawnotes.NOTES_DIR, "Ghostty.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("Duty owed by D\nthird line\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note, env={"TERM": "lawnotes-kmous", "TERMINFO": terminfo})
        s.keys(click(2, 1) + click(2, 1) + b"Breach", 0.5)        # double-click a word
        s.keys(b"\x1b[<65;5;2M" * 3 + click(3, 2) + b"X\x13", 0.6)  # wheel, then click and type
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["Breach owed by D", "tXhird line"])

    def test_undo_brings_back_selection(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Undo.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("alpha beta gamma\n")
        select5 = b"\x01" + b"\x1b[1;2C" * 5                    # select "alpha"
        s = Session(note)
        s.keys(select5 + b"\x1b[C" + b"\x1a" + b"X", 0.6)        # move away, ^Z: selected again, X replaces it
        s.keys(b"\x01" + b"\x1b[1;2C" * 6 + b"Y\x1a" + b"Z\x13", 0.6)  # "X beta": typed over, ^Z selects it again
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["Z gamma"])

    def test_stray_byte_loses_only_itself(self):
        # get_wch() used to swallow the next ~50 keystrokes after a byte that isn't UTF-8
        note = os.path.join(lawnotes.NOTES_DIR, "Stray.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("")
        s = Session(note)
        s.keys(b"\xc5The duty of care was owed by D to V.\xff Caf\xc3\xa9\x13", 0.8)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "The duty of care was owed by D to V. Café\n")

    def test_esc_then_quick_control_key(self):
        # a control key right behind an Esc is its own key, not part of the Esc
        note = os.path.join(lawnotes.NOTES_DIR, "EscQuit.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("text\n")
        s = Session(note)
        s.keys(b"\x1b\x11", 1.5)
        for _ in range(20):
            if os.waitpid(s.pid, os.WNOHANG)[0] == s.pid:
                break
            s.out += s.read(0.1)
        else:
            self.fail("Esc then ^Q in one write didn't quit")

    def test_right_click_menu(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Menu.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("alpha beta\n")
        s = Session(note)
        screen = s.keys(b"\x1b[<2;8;1M\x1b[<2;8;1m", 0.5)          # right-click "beta"
        self.assertIn("Select word", screen)
        s.keys(b"\x1b[<0;11;4M\x1b[<0;11;4m", 0.5)                 # click "Select word" (the 2nd item)
        s.keys(b"X", 0.3)                                            # typing replaces the selected word
        s.keys(b"\x01" + b"\x1b[1;2C" * 5, 0.4)                     # select "alpha"
        s.keys(b"\x1b[<2;4;1M\x1b[<2;4;1m", 0.5)                  # right-click inside the selection
        s.keys(b"\x1b[B" * 5 + b"\r\x13", 0.6)                      # Cut Copy Paste Highlight Italic Bold
        s.keys(b"\x1b[<2;4;1M\x1b[<2;4;1m", 0.5)
        screen = s.keys(b"\x1b[B" * 5 + b"\r", 0.8)                 # Paste Select Highlight Undo Redo Split
        self.assertIn("Notes in", screen)                             # the new pane shows the note list
        s.keys(b"\x1b", 0.8)                                         # Esc: the same note in the new pane
        self.assertNotIn("only pane", s.keys(b"\x1bw", 0.6))         # Option+W closes one of two panes
        self.assertIn("only pane", s.keys(b"\x1bw", 0.6))
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["**alpha** X"])

    def test_split_then_click_a_note(self):
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        left, right = (os.path.join(lawnotes.NOTES_DIR, n) for n in ("SplitLeft.md", "SplitRight.md"))
        open(left, "w").write("left note\n")
        open(right, "w").write("right note\n")
        future = time.time() + 3600
        os.utime(right, (future, future))                          # newest: first in the list
        s = Session(left)
        screen = s.keys(b"\x1bd", 0.8)                             # Option+D: split, list in the new pane
        self.assertIn("Notes in", screen)
        self.assertIn("SplitRight.md", screen)
        s.keys(b"\x1b[<0;70;8M", 0.3)                              # press on the first note (below the guide)...
        s.keys(b"\x1b[<0;70;8m", 0.6)                              # ...and let go: it opens
        s.keys(b"\x05!\x13", 0.5)                                   # type at the end of its line
        self.assertTrue(s.quit())
        self.assertEqual(open(right).read(), "right note!\n")
        self.assertEqual(open(left).read(), "left note\n")

    def test_new_theme_and_switching(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Themed.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("text\n")
        settings = os.path.join(lawnotes.NOTES_DIR, ".settings.json")
        s = Session(note)
        s.keys(b"\x1b[17~", 0.5)                                 # F6: themes
        s.keys(b"new\r", 0.5)
        s.keys(b"Mine\r", 0.8)                                   # a copy of NERV, opened to change
        screen = s.keys(b"\x13", 0.6)                            # saving it applies it
        self.assertIn("updated", screen)
        s.keys(b"\x1b[17~" + b"Paper\r", 0.8)
        self.assertTrue(s.quit())
        mine = os.path.join(lawnotes.NOTES_DIR, ".themes", "Mine.txt")
        self.assertEqual(open(mine).read(), lawnotes.theme_text("Mine", lawnotes.THEMES["NERV"]))
        self.assertEqual(json.load(open(settings)), {"theme": "Paper"})

    def test_star_wraps_selection(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Wrap.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("the ratio decidendi of the case\nD and V\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note)
        s.keys(click(6, 1) + click(6, 1) + b"**", 0.5)                 # double-click "ratio", * twice
        s.keys(b"\x1bOB\x01" + b"\x1b[1;2C" * 7 + b"_\x13", 0.6)    # Shift+Right over "D and V", _
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines(), ["the **ratio** decidendi of the case", "_D and V_"])

    def test_wheel_moves_view_not_cursor_and_copy(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Scroll2.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(80)))
        fake = tempfile.mkdtemp(dir=TMP)
        clip = os.path.join(fake, "clipboard")
        with open(os.path.join(fake, "pbcopy"), "w") as f:
            f.write(f"#!/bin/sh\ncat > '{clip}'\n")
        os.chmod(os.path.join(fake, "pbcopy"), 0o755)
        s = Session(note, env={"PATH": fake + os.pathsep + os.environ["PATH"]})
        screen = s.keys(b"\x1b[<65;10;10M" * 6, 0.6)                 # wheel down 18 rows
        self.assertIn("44", screen)  # the view moved: rows beyond the first screen were drawn
        out = s.keys(b"\x03", 0.3)                                        # copy with nothing selected
        self.assertEqual(open(clip).read(), "line 0\n")                 # the cursor's line, not the view's
        self.assertIn("Copied 2 words", out)                              # the status bar says so
        flashed = s.out[-4000:]
        self.assertIn(b"48;5;120", flashed)                               # and the line flashed green
        s.keys(b"Q\x13", 0.5)                                             # typing goes where the cursor was
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines()[0], "Qline 0")    # the flash didn't select it

    def test_panes_split_switch_and_close(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Panes")
        os.makedirs(folder, exist_ok=True)
        a = os.path.join(folder, "A.md")
        open(a, "w").write("# A\n")
        s = Session(a)
        s.keys(b"\x1bd", 0.8)                          # Option+D: split, the note list opens
        s.keys(b"Panes/B\r", 0.8)                      # a new note in the right pane
        s.keys(b"right", 0.4)
        s.keys(b"\x1bo", 0.4)                          # Option+O: back to the left pane
        s.keys(b"left", 0.4)
        s.keys(b"\x1b[<0;9;1M\x1b[<0;9;1m", 0.4)       # click in the left pane, column 8
        s.keys(b"Z", 0.4)
        s.keys(b"\x1bw", 0.5)                          # Option+W closes the left pane
        s.keys(b"\x1bw", 0.5)                          # only one left: nothing happens
        self.assertTrue(s.quit())
        self.assertEqual(open(a).read(), "left# AZ\n")
        self.assertEqual(open(os.path.join(folder, "B.md")).read(), "# B\n\nright\n")

    def test_closing_the_terminal_saves_and_logs_no_crash(self):
        import signal
        note = os.path.join(lawnotes.NOTES_DIR, "Closed.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("# Closed\n")
        s = Session(note)
        s.keys(b"\x05 typed", 0.4)
        os.close(s.fd)                    # the window or pane goes away...
        os.kill(s.pid, signal.SIGHUP)     # ...and SIGHUP can arrive twice: the second
        os.kill(s.pid, signal.SIGHUP)     # must not interrupt the save
        for _ in range(50):
            if os.waitpid(s.pid, os.WNOHANG)[0] == s.pid:
                break
            time.sleep(0.1)
        self.assertEqual(open(note).read(), "# Closed typed\n")
        mine = [l for l in open(lawnotes.LOG_FILE).read().splitlines() if f"[{s.pid}]" in l]
        self.assertIn("exited: window or pane closed (SIGHUP)", "\n".join(mine))
        self.assertFalse(any("CRASH" in l for l in mine), mine)

    def test_paste_a_word_table(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Pasted.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("# Cases\n")
        s = Session(note)
        s.keys(b"\x05\x1b[200~Case\tHeld\rDonoghue v Stevenson\tDuty owed\r\x1b[201~\x13", 0.8)  # \u2318V
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "# Cases\n\n"
                         "| Case                 | Held      |\n"
                         "|----------------------|-----------|\n"
                         "| Donoghue v Stevenson | Duty owed |\n\n")

    def test_typing_expands_abbreviations(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Abbr.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("x\n")
        s = Session(note)
        s.keys(b"\x05 The CL and EL; the LCh.", 0.5)
        s.keys(b"\x1b[200~ CL pasted \x1b[201~", 0.4)               # pasting doesn't expand
        s.keys(b" CLR\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "x The Common Law and Equity Law; the Lord Chancellor. CL pasted  CLR\n")

    def test_option_a_opens_abbreviations_beside_the_note(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Trusts", "Abbrev.md")
        os.makedirs(os.path.dirname(note), exist_ok=True)
        open(note, "w").write("In EQ, B holds on a constructive trust\n")
        mine = os.path.join(lawnotes.NOTES_DIR, ".abbreviations.txt")
        self.assertFalse(os.path.exists(mine))
        try:
            s = Session(note)
            # select "constructive trust" (Shift+End from its start), then Option+A
            s.keys(b"\x01" + b"\x1b[1;3C" * 6 + b"\x1b[1;2F", 0.3)
            screen = s.keys(b"\x1ba", 1.0)
            self.assertIn("In EQ, B holds", screen)                         # the note stays in view
            self.assertIn("LCh = Lord Chancellor", screen)                  # beside the list
            self.assertIn("# EQ = Equity", screen)                          # suggested from the note
            self.assertIn(" = constructive trust", screen)                  # a line for it, waiting for its short form
            self.assertIn("Type the short", screen)
            screen = s.keys(b"CT\x13", 0.6)
            self.assertIn("Abbreviations updated", screen)
            s.keys(b"\x1bw", 0.6)                                          # Option+W: back to the note alone
            s.keys(b"\x1b[1;2F\x05 so CT and EL.\x13", 0.6)                 # the new one expands straight away
            self.assertTrue(s.quit())
            self.assertEqual(open(note).read(), "In EQ, B holds on a constructive trust so constructive trust and Equity Law.\n")
            lines = open(mine).read().splitlines()
            self.assertEqual(lines[2:7], ["CL = Common Law", "EL = Equity Law", "LCh = Lord Chancellor", "CT = constructive trust", ""])
            self.assertEqual(lines[-1], "# EQ = Equity")
        finally:
            if os.path.exists(mine):
                os.remove(mine)

    def test_option_a_in_a_narrow_pane_stacks_the_list(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Narrow.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("narrow note\n")
        mine = os.path.join(lawnotes.NOTES_DIR, ".abbreviations.txt")
        try:
            s = Session(note, size=(40, 40))
            screen = s.keys(b"\x1ba", 1.0)
            self.assertIn("narrow note", screen)                            # above
            self.assertIn("CL = Common Law", screen)                        # below
            s.keys(b"\x1bw", 0.4)
            self.assertTrue(s.quit())
        finally:
            if os.path.exists(mine):
                os.remove(mine)

    def test_lists_scroll_without_half_drawn_frames(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Many")
        os.makedirs(folder, exist_ok=True)
        for i in range(80):
            open(os.path.join(folder, f"Note {i:02}.md"), "w").write(f"# Note {i}\n")
        s = Session(os.path.join(folder, "Note 00.md"))
        s.keys(b"\x0f", 0.6)                                             # ^O, then the wheel over the list
        before = len(s.out)
        s.keys(b"\x1b[<65;20;20M" * 12, 0.6)
        drawn = s.out[before:]
        outside = re.sub(rb"\x1b\[\?2026h.*?\x1b\[\?2026l", b"", drawn, flags=re.S)
        self.assertGreater(len(drawn), 500)
        self.assertEqual(outside, b"")                                   # every frame is one synchronised update
        s.keys(b"\x1b", 0.4)
        self.assertTrue(s.quit())

    def test_option_up_down_jump_to_ends(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Ends.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(50)))
        s = Session(note)
        s.keys(b"\x1b[1;3BZ", 0.4)        # Option+Down (herdr / xterm form): end of the note
        s.keys(b"\x1b\x1bOAA", 0.4)       # Option+Up as Esc + Up (Terminal with Option as Meta)
        s.keys(b"\x13", 0.4)
        self.assertTrue(s.quit())
        lines = open(note).read().splitlines()
        self.assertEqual((lines[0], lines[-1]), ("Aline 0", "line 49Z"))

    def test_highlight_selection_and_word(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Marks.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("the ratio decidendi binds\nOther line here\n")
        click = lambda col, row: f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m".encode()
        s = Session(note)
        s.keys(click(7, 1) + click(7, 1) + b"=", 0.5)     # double-click "ratio", =  -> ==ratio==
        s.keys(b"=", 0.4)                                  # = again on the same selection: removes it
        s.keys(b"=", 0.4)                                  # and again: back on
        s.keys(b"\x1bOB\x01\x1bh", 0.4)                   # line 2, no selection: Option+H highlights the word
        s.keys(b"\x13", 0.4)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "the ==ratio== decidendi binds\n==Other== line here\n")

    def test_tab_indents_selected_lines(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Indent.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("- alpha\n- beta\n\n- gamma\n- delta\n")
        s = Session(note)
        s.keys(b"\x1b[1;2B" * 4, 0.4)      # Shift+Down x4: lines 1-4 selected (ends at column 0 of line 5)
        s.keys(b"\t\t", 0.4)              # Tab twice: indent the selected lines (blank line left alone)
        s.keys(b"\x1b[Z", 0.4)             # Shift+Tab: back one step
        s.keys(b"\x13", 0.4)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "  - alpha\n  - beta\n\n  - gamma\n- delta\n")

    def test_links_between_notes(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Linked")
        os.makedirs(folder, exist_ok=True)
        source, target = os.path.join(folder, "Source.md"), os.path.join(folder, "Target.md")
        open(target, "w").write("# Target\n\nintro\n\n## Duty of care\n\nneighbour\n")
        open(source, "w").write("see \n[[Linked/Target#duty of care|the duty]]\n[[Linked/Missing]]\n")
        s = Session(source)
        screen = s.keys(b"\x05[[", 0.6)                      # [[ opens the note list
        self.assertIn("Link to a note", screen)
        screen = s.keys(b"Linked/Targ\r", 0.6)               # pick a note: its name and ]] are written
        self.assertIn("[[Linked/Target]]", screen)
        s.keys(b" [[", 0.6)
        s.keys(b"\x1b", 0.6)                                 # Esc leaves the [[ as typed
        s.keys(b"\x1bOB\x01\x1bl", 0.8)                      # Option+L on a link with a heading
        s.keys(b"X\x13", 0.5)                                # ...lands on that heading in Target
        screen = s.keys(b"\x1bl", 0.8)                        # off a link: the notes linking here
        self.assertIn("Notes linking here", screen)
        self.assertIn("Source.md:2", screen)
        s.keys(b"\r", 0.6)                                   # the first: Source, line 1, at the link
        s.keys(b"Y\x13", 0.5)
        s.keys(b"\x1bOB\x1bOB\x01\x1bl", 0.8)                # a link to a note that doesn't exist yet
        s.keys(b"\r", 0.8)                                   # Enter creates it, as ^N would
        self.assertTrue(s.quit())
        self.assertEqual(open(source).read().splitlines(),
                         ["see Y[[Linked/Target]] [[", "[[Linked/Target#duty of care|the duty]]", "[[Linked/Missing]]"])
        self.assertEqual(open(target).read().splitlines()[4], "X## Duty of care")
        self.assertTrue(os.path.isfile(os.path.join(folder, "Missing.md")))

    def test_note_list_explains_folders(self):
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(os.path.join(lawnotes.NOTES_DIR, "Existing.md"), "w").write("# Existing\n")
        s = Session()                               # opens on the note list
        self.assertIn(b"Folder/Name", s.out)          # shown even when notes exist
        s.keys(b"Contract/Offer\r", 0.8)            # and it works: the folder is created
        self.assertTrue(s.quit())
        self.assertTrue(os.path.isfile(os.path.join(lawnotes.NOTES_DIR, "Contract", "Offer.md")))

    def test_note_reopens_where_you_left_off(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Reopen.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(60)))
        s = Session(note)
        s.keys(b"\x1bOB" * 25 + b"\x05", 0.6)   # line 26, end of line
        self.assertTrue(s.quit())
        s = Session(note)
        s.keys(b"!\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read().splitlines()[25], "line 25!")

    def test_option_backspace_deletes_a_word(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Words.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("Donoghue v Stevenson neighbour\n")
        s = Session(note)
        s.keys(b"\x05\x1b\x7f", 0.4)        # end of line, Option+Backspace
        s.keys(b"\x01\x1b[3;3~\x13", 0.5)  # start of line, Option+Fn+Backspace
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), " v Stevenson \n")

    def test_drag_border_resizes_panes(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Drag")
        os.makedirs(folder, exist_ok=True)
        a = os.path.join(folder, "A.md")
        open(a, "w").write("# A\n")
        s = Session(a)
        s.keys(b"\x1bd", 0.8)
        s.keys(b"\x1b", 0.5)                               # both panes show A; right is active
        s.keys(b"\x1b[<0;50;5M\x1b[<32;71;5M\x1b[<0;71;5m", 0.5)  # drag the border from col 49 to 70
        s.keys(b"\x1b[<0;61;1M\x1b[<0;61;1m", 0.4)        # col 60 is now inside the left pane
        s.keys(b"L", 0.3)
        self.assertTrue(s.quit())
        self.assertEqual(open(a).read(), "# AL\n")         # typed in the left pane, at the end of line 1
        self.assertEqual(lawnotes.Layout.MIN_W, 16)

    def test_same_note_in_two_panes_stays_in_sync(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Sync")
        os.makedirs(folder, exist_ok=True)
        c = os.path.join(folder, "C.md")
        open(c, "w").write("# C\n")
        s = Session(c)
        s.keys(b"\x1bd", 0.8)
        s.keys(b"\x1b", 0.5)                           # Esc: keep the same note in the new pane
        s.keys(b"X", 0.3)
        s.keys(b"\x1bo", 0.3)
        s.keys(b"\x05Y", 0.3)                          # left pane: end of line, type
        self.assertTrue(s.quit())
        self.assertEqual(open(c).read(), "X# CY\n")    # both edits, one text
        self.assertEqual(os.listdir(folder), ["C.md"])  # no conflict copies

    def test_split_scroll_events_neither_close_nor_type(self):
        """A scroll event whose ESC arrives 120 ms before the rest (seen through herdr)."""
        wheel = b"\x1b[<65;50;10M"
        s = Session()  # the note list
        for _ in range(3):
            os.write(s.fd, wheel[:1]); time.sleep(0.12); os.write(s.fd, wheel[1:]); s.read(0.2)
        self.assertEqual(os.waitpid(s.pid, os.WNOHANG)[0], 0, "the note list closed")
        s.keys(b"\x11", 1)
        os.waitpid(s.pid, 0)
        note = os.path.join(lawnotes.NOTES_DIR, "Scroll.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("".join(f"line {i}\n" for i in range(60)))
        s = Session(note)
        for _ in range(3):
            os.write(s.fd, wheel[:1]); time.sleep(0.12); os.write(s.fd, wheel[1:]); s.read(0.2)
        s.keys(b"\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertNotIn("<65", open(note).read())

    def test_exit_reason_logged_locally_and_in_icloud(self):
        icloud = os.path.join(os.environ["HOME"], "Library", "Mobile Documents", "com~apple~CloudDocs")
        os.makedirs(icloud, exist_ok=True)
        s = Session("Logged")
        self.assertTrue(s.quit())
        local = open(lawnotes.LOG_FILE).read()
        self.assertIn("exited: quit with ^Q", local)
        logs = os.listdir(os.path.join(icloud, "Law Notes Logs"))
        self.assertEqual(len(logs), 1)
        self.assertIn("exited: quit with ^Q", open(os.path.join(icloud, "Law Notes Logs", logs[0])).read())

    def test_type_save_quit(self):
        s = Session("Contract/Offer")
        path = os.path.join(lawnotes.NOTES_DIR, "Contract", "Offer.md")
        s.keys(b"\x1bOB\x1bOB- Offer: Carlill v Carbolic Smoke Ball Co", 0.5)
        s.keys(b"\x1b[27;2;13~unilateral\r\r", 0.5)
        running = json.load(open(lawnotes.RUNNING_FILE))
        self.assertEqual(running["pid"], s.pid)
        self.assertTrue(s.quit())
        self.assertEqual(open(path).read(), "# Offer\n\n- Offer: Carlill v Carbolic Smoke Ball Co\n  unilateral\n\n")
        self.assertFalse(os.path.exists(lawnotes.RUNNING_FILE))
        self.assertNotIn(b"Traceback", s.out)

    def test_open_request_and_outside_change(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Req")
        a, b = os.path.join(folder, "A.md"), os.path.join(folder, "B.md")
        os.makedirs(folder, exist_ok=True)
        open(a, "w").write("# A\n")
        open(b, "w").write("# B\n")
        s = Session(a)
        with open(lawnotes.REQUEST_FILE, "w") as f:
            json.dump({"note": "Req/B", "time": time.time()}, f)
        s.keys(b"", 1.5)  # the launcher's request opens B
        time.sleep(0.05)
        open(b, "w").write("# B\n\nfrom another Mac\n")
        screen = s.keys(b"", 1.5)
        self.assertIn("reloaded", screen)
        s.keys(b"\x1bOB\x1bOB\x05!\x13", 0.6)
        self.assertTrue(s.quit())
        self.assertEqual(open(b).read(), "# B\n\nfrom another Mac!\n")
        self.assertEqual(sorted(os.listdir(folder)), ["A.md", "B.md"])  # no conflict copy

    def test_revision_session(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Revise")
        os.makedirs(folder, exist_ok=True)
        note = os.path.join(folder, "Negligence.md")
        open(note, "w").write(BRIEF_NOTE)
        progress = os.path.join(lawnotes.NOTES_DIR, ".revision.json")
        s = Session(note)
        screen = s.keys(b"\x1bc", 0.8)                                 # Option+C: what to revise
        self.assertIn("This note: Revise/Negligence.md", screen)
        self.assertIn("This folder: Revise/", screen)
        self.assertIn("0 due, 3 new, 3 cards", screen)
        screen = s.keys(b"\r", 0.6)                                     # this note: the first card
        self.assertIn("Donoghue v Stevenson", screen)
        self.assertIn("[1932] AC 562", screen)
        self.assertNotIn("snail", screen)
        screen = s.keys(b" ", 0.5)                                       # Space shows the back
        self.assertIn("snail in a bottle", screen)
        self.assertNotIn("Obiter", screen)
        self.assertIn("Good", screen)
        screen = s.keys(b"3", 0.5)                                       # good: the next card
        self.assertIn("Caparo Industries plc v Dickman", screen)
        s.keys(b"\r", 0.4)
        screen = s.keys(b"1", 0.5)                                       # again: comes back at the end
        self.assertIn("Robinson", screen)
        screen = s.keys(b"\x1b", 0.8)                                   # Esc ends, with a summary
        self.assertIn("Revised 2 cards: 1 good, 0 hard, 1 again · 2 left", screen)
        screen = s.keys(b"\x1b[18~", 0.8)                               # F7 opens it too
        self.assertIn("1 due, 1 new, 3 cards", screen)                  # Caparo is due again today
        s.keys(b"\x1b", 0.5)
        self.assertTrue(s.quit())
        cards = json.load(open(progress))["cards"]
        self.assertEqual(cards["Revise/Negligence.md::donoghue v stevenson"]["box"], 1)
        self.assertEqual(cards["Revise/Negligence.md::caparo industries plc v dickman"]["box"], 0)
        self.assertEqual(open(note).read(), BRIEF_NOTE)                  # the note itself is untouched

    def test_revision_card_wraps_in_a_narrow_paper_pane(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Narrow")
        os.makedirs(folder, exist_ok=True)
        note = os.path.join(folder, "Brown.md")
        open(note, "w").write("## R v Brown [1994] 1 AC 212\n- **Held:** consent is no defence to actual "
                              "bodily harm inflicted for sexual gratification\n")
        settings = os.path.join(lawnotes.NOTES_DIR, ".settings.json")
        before = open(settings).read() if os.path.exists(settings) else None
        open(settings, "w").write('{"theme": "Paper"}')
        try:
            s = Session(note, size=(20, 34))
            s.keys(b"\x1bc", 0.8)
            screen = s.keys(b"\r ", 0.8)
            for word in ("consent", "defence", "bodily", "gratification"):  # wrapped, nothing cut off
                self.assertIn(word, screen)
            self.assertIn("Good", screen)                                # the ratings fit at 34 columns
            self.assertIn(b"48;5;230", s.out)                            # Paper's cream background
            s.keys(b"2\x1b", 0.6)
            self.assertTrue(s.quit())
        finally:
            if before is None:
                os.remove(settings)
            else:
                open(settings, "w").write(before)


    def test_replace_in_all_notes(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "Replace")
        os.makedirs(folder, exist_ok=True)
        a, b = os.path.join(folder, "A.md"), os.path.join(folder, "B.md")
        open(a, "w").write("Donohoe v Stevenson\n")
        open(b, "w").write("Donohoe [1932] AC 562\n")
        s = Session(a)
        s.keys(b"\x1br", 0.5)                                    # Option+R
        s.keys(b"Donohoe\r", 0.4)
        screen = s.keys(b"Donoghue\r", 0.8)
        self.assertIn("All notes — 2 matches in 2 notes", screen)
        screen = s.keys(b"\x1b[B\r", 1.5)                         # All notes
        self.assertIn("2 in 2 notes ·", screen)                    # "Replaced 2 in 2 notes"
        self.assertEqual(open(b).read(), "Donoghue [1932] AC 562\n")
        s.keys(b"\x1a\x13", 0.5)                                  # ^Z undoes it in the open note
        self.assertTrue(s.quit())
        self.assertEqual(open(a).read(), "Donohoe v Stevenson\n")
        self.assertEqual(open(b).read(), "Donoghue [1932] AC 562\n")

    def test_replace_one_at_a_time(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Review.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        open(note, "w").write("Smyth v Jones; Smyth again; Smyth\n")
        s = Session(note)
        s.keys(b"\x1bOS", 0.5)                                    # F4
        s.keys(b"Smyth\rSmith\r", 0.8)
        screen = s.keys(b"\x1b[B\x1b[B\r", 0.8)                  # One at a time
        self.assertIn("(1 of 3)", screen)
        s.keys(b"n", 0.4)
        s.keys(b"y", 0.4)
        screen = s.keys(b"\x1b", 0.8)                             # Esc stops
        self.assertIn("Replaced 1 of 3", screen)
        s.keys(b"\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "Smyth v Jones; Smith again; Smyth\n")

    def test_history_snapshots_notes_only(self):
        d = os.path.join(lawnotes.NOTES_DIR, "Hist")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "Note.md"), "w").write("# Note\n")
        open(os.path.join(d, "lecture.pdf"), "wb").write(b"%PDF" + b"0" * 1000)
        s = Session(os.path.join(d, "Note.md"))
        self.assertTrue(s.quit())
        files = subprocess.run(["git", f"--git-dir={lawnotes.HISTORY_DIR}", "ls-tree", "-r", "--name-only", "HEAD"],
                               capture_output=True, text=True).stdout.split()
        self.assertIn("Hist/Note.md", files)
        self.assertNotIn("Hist/lecture.pdf", files)


FAKE_HERDR = r"""#!/bin/bash
# Stands in for herdr: answers like the real one and logs every call.
echo "$*" >> "$HERDR_LOG"
case "$1 $2" in
  "pane get") echo '{"result":{"pane":{"pane_id":"w1:p1","tab_id":"w1:t1","workspace_id":"w1"}}}' ;;
  "tab create") echo '{"result":{"root_pane":{"pane_id":"w1:p2"},"tab":{"tab_id":"w1:t2"}}}' ;;
  "pane split") echo '{"result":{"pane":{"pane_id":"w1:p3"}}}' ;;
  *) ;;  # pane run, tab focus: success, no output
esac
"""


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(self.bin, "herdr"), "w") as f:
            f.write(FAKE_HERDR)
        os.chmod(os.path.join(self.bin, "herdr"), 0o755)
        self.log = os.path.join(self.bin, "calls.log")
        self.env = {**os.environ, "PATH": self.bin + os.pathsep + os.environ["PATH"],
                    "HERDR_PANE_ID": "w1:p1", "HERDR_LOG": self.log}

    def launch(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes"), *args], env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_tab_opens_once_and_editor_not_started_here(self):
        r = self.launch("Tort/Duty")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = open(self.log).read()
        self.assertIn("tab create --workspace w1 --label Notes", calls)
        self.assertIn("pane run w1:p2", calls)
        self.assertIn("Tort/Duty", calls)
        self.assertNotIn("Traceback", r.stderr)  # the editor would crash here without a terminal

    def test_split(self):
        r = self.launch("--split")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pane run w1:p3", open(self.log).read())

    def test_open_copy_is_brought_forward(self):
        os.makedirs(os.path.dirname(lawnotes.RUNNING_FILE), exist_ok=True)
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "lawnotes.py"])
        try:
            with open(lawnotes.RUNNING_FILE, "w") as f:
                json.dump({"pid": sleeper.pid, "pane": "w1:p9"}, f)
            r = self.launch("Contract/Offer")
            self.assertIn("already open", r.stdout)
            calls = open(self.log).read()
            self.assertIn("tab focus w1:t1", calls)
            self.assertNotIn("tab create", calls)
            self.assertEqual(json.load(open(lawnotes.REQUEST_FILE))["note"], "Contract/Offer")
        finally:
            sleeper.kill()
            for f in (lawnotes.RUNNING_FILE, lawnotes.REQUEST_FILE):
                if os.path.exists(f):
                    os.remove(f)


if __name__ == "__main__":
    unittest.main()
