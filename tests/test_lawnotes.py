"""Editor tests: the buffer, saving, highlighting, and a real session in a pseudo-terminal.

Run: python3 -m unittest discover -s tests -v
Uses only temporary folders; never touches your notes, history or install.
"""
import datetime
import json
import os
import pty
import re
import select
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
from unittest import mock
from unittest.mock import patch

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


class AutocompleteTests(unittest.TestCase):
    def setUp(self):
        self.app = lawnotes.App.__new__(lawnotes.App)
        self.app._cases = (0.0, None, None)
        self.app.buf = lawnotes.Buffer(os.path.join(TMP, "autocomplete-draft.md"))
        self.source = os.path.join(TMP, "autocomplete-source.md")
        self.lines = ["Caparo Industries plc v Dickman [1990] AC 605",
                      "Caparo Industries plc v Dickman [1990] 2 AC 605",
                      "Caparo Industries plc v Dickman [1990] 2 AC 605",
                      "Caparo Manufacturing Ltd v Clark",
                      "Donoghue v Stevenson [1932] AC 562"]
        self.stamp = 1
        self.scans = 0
        self.app.list_notes = lambda: [(self.stamp, "Source.md", self.source)]

        def all_notes(only=None):
            self.scans += 1
            return [(self.source, "Source.md", self.lines)]

        self.app.all_notes = all_notes
        self.app.buffers = lambda: [(None, self.app.buf)]

    def draft(self, text, col=None):
        self.app.buf.lines = [text]
        self.app.buf.cy = 0
        self.app.buf.cx = len(text) if col is None else col

    def test_single_match_citation_and_one_step_undo(self):
        self.draft("dono")
        self.assertTrue(self.app.complete_case())
        self.assertEqual(self.app.buf.lines, ["Donoghue v Stevenson [1932] AC 562"])
        self.assertTrue(self.app.buf.undo())
        self.assertEqual(self.app.buf.lines, ["dono"])

    def test_multiple_matches_ranked_citation_frequency_and_cache(self):
        shown = []

        def pick(title, build, **kwargs):
            shown.extend(label for label, _ in build(""))
            return build("")[0][1]

        self.app.pick = pick
        self.draft("Capa")
        with patch.object(lawnotes.time, "time", return_value=100):
            self.assertTrue(self.app.complete_case())
        self.assertEqual(shown[0], "Caparo Industries plc v Dickman [1990] 2 AC 605")
        self.assertEqual(self.app.buf.lines[0], shown[0])
        self.assertEqual(self.scans, 1)
        self.draft("Dono")
        with patch.object(lawnotes.time, "time", return_value=101):
            self.assertTrue(self.app.complete_case())
        self.assertEqual(self.scans, 2)  # completion edited the open buffer
        with patch.object(lawnotes.time, "time", return_value=102):
            self.app.case_entries()
        self.assertEqual(self.scans, 3)
        self.lines.append("Newcastle v United")
        self.stamp += 1
        self.draft("New")
        with patch.object(lawnotes.time, "time", return_value=108):
            self.assertTrue(self.app.complete_case())
        self.assertEqual(self.scans, 4)

    def test_tab_contexts_leave_text_untouched(self):
        for text, col in (("Ca", None), ("CapaX", 4), ("// Capa", None),
                          ("read // Capa", None), ("`Capa`", 5), ("[[Capa", None),
                          ("[[Capa]]", 6), ("[^Capa", None), ("[^Capa]", 6),
                          ("Capa.", None), ("123Capa", None)):
            with self.subTest(text=text):
                self.draft(text, col)
                self.assertFalse(self.app.complete_case())
                self.assertEqual(self.app.buf.lines, [text])
        self.assertEqual(self.scans, 0)  # no scan for an excluded context

    def test_midline_and_common_words_do_not_complete(self):
        self.lines += ["Theakston v MGN Ltd", "Forbes v Smith", "Butler v Jones", "Perry v Brown"]
        self.draft("Donoghue v Stevenson", len("Donoghue"))
        self.assertFalse(self.app.complete_case())
        for word in ("the", "for", "but", "per", "not", "with", "his", "can"):
            with self.subTest(word=word):
                self.draft(word)
                self.assertFalse(self.app.complete_case())
        self.assertEqual(self.scans, 0)

    def test_unsaved_open_case_refreshes_cached_entries(self):
        b = self.app.buf
        self.app.all_notes = lambda only=None: [(self.source, "Source.md", self.lines),
                                      (b.path, "Draft.md", b.lines)]
        self.draft("Dono")
        self.assertTrue(self.app.complete_case())
        b.checkpoint("type")
        b.lines = ["Omega v Beta", "Ome"]
        b.cy, b.cx = 1, 3
        self.assertTrue(self.app.complete_case())
        self.assertEqual(b.lines[1], "Omega v Beta")

    def test_unsaved_case_in_another_pane_is_indexed(self):
        other = lawnotes.Buffer(os.path.join(TMP, "autocomplete-other.md"))
        other.lines = ["Meridian v North"]
        other.version += 1
        self.app.buffers = lambda: [(None, self.app.buf), (None, other)]
        self.assertTrue(any(c["name"] == "Meridian v North" for _, c in self.app.case_entries()))
        other.checkpoint("type")
        other.lines = ["Orion v North"]
        names = [c["name"] for _, c in self.app.case_entries()]
        self.assertIn("Orion v North", names)
        self.assertNotIn("Meridian v North", names)

    def test_apostrophe_and_hyphen_are_part_of_prefix(self):
        self.lines += ["O'Brien v Jones", "O’Connor v Smith", "Smith-Jones v Clark"]
        for prefix, expected in (("O'Bri", "O'Brien v Jones"),
                                 ("O’Con", "O’Connor v Smith"),
                                 ("Smith-Jo", "Smith-Jones v Clark")):
            with self.subTest(prefix=prefix):
                self.draft(prefix)
                self.assertTrue(self.app.complete_case())
                self.assertEqual(self.app.buf.lines, [expected])
        self.draft("O'B")
        self.assertFalse(self.app.complete_case())

    def test_picker_shows_at_most_ten_and_name_without_citation(self):
        self.lines = ["Alpha0 v Beta"] * 3 + [f"Alpha{i} v Beta" for i in range(1, 15)]
        shown = []

        def pick(title, build, **kwargs):
            shown.extend(label for label, _ in build(""))
            self.assertEqual([label for label, _ in build("Alpha9")], ["Alpha9 v Beta"])
            return None

        self.app.pick = pick
        self.draft("Alp")
        self.assertTrue(self.app.complete_case())
        self.assertEqual(len(shown), 10)
        self.assertEqual(shown[0], "Alpha0 v Beta")
        self.assertEqual(self.app.buf.lines, ["Alp"])

    def test_case_index_keeps_first_citation_and_comment_hits(self):
        self.lines.append("// Commented v Case [2020] AC 1")
        entries = self.app.case_entries()
        cap = next((label, c) for label, c in entries if c["name"] == "Caparo Industries plc v Dickman")
        self.assertIn("[1990] AC 605", cap[0])
        self.assertEqual(cap[1]["best_cite"], "[1990] 2 AC 605")
        self.assertTrue(any(c["name"] == "Commented v Case" for _, c in entries))

    def test_case_index_drops_shortened_and_renamed_hits(self):
        with open(self.source, "w") as f:
            f.write("header\nDonoghue v Stevenson\nDonoghue v Stevenson\n")
        self.lines = ["header", "Donoghue v Stevenson", "Donoghue v Stevenson"]
        choice = next(c for _, c in self.app.case_entries() if c["name"] == "Donoghue v Stevenson")
        self.app.pick = lambda *args, **kwargs: choice
        messages = []
        self.app.say = messages.append
        self.app.goto = lambda *args, **kwargs: self.fail("^B opened a stale hit")
        self.lines = [""]
        self.app.case_index()
        self.assertIn("no longer", messages[-1])

        self.app._cases = (0.0, None, None)
        self.lines = ["Donoghue v Stevenson"]
        self.app.case_entries()
        renamed = self.source + ".renamed"
        os.rename(self.source, renamed)
        self.app.all_notes = lambda only=None: [(renamed, "Renamed.md", self.lines)]
        self.app.case_index()
        self.assertFalse(os.path.exists(self.source))
        self.assertIn("no longer", messages[-1])


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
        self.assertTrue(again.startswith(mine.split("# Suggested")[0].rstrip("\n")
                                         + "\nEQ = Equity\nAC = Appeal Cases\nC = claimant\n\n# Suggested"))
        self.assertTrue(again.endswith("# CT = constructive trust\n"))  # lines of yours in the block move above it
        self.assertEqual(lawnotes.abbreviations_text(again, []).count("EQ = Equity"), 1)
        odd = "AA = after  # Suggested from your notes once\nBB = bb\n"  # the mark mid-line is just text
        self.assertEqual(lawnotes.abbreviations_text("x\n" + odd, []), "x\n" + odd)
        with open(f, "wb") as out:
            out.write(b"EQ = \xe9quit\xe9\n")                       # not UTF-8: built-ins, no crash
        self.assertEqual(lawnotes.load_abbreviations(f), lawnotes.DEFAULT_ABBREVIATIONS)
        self.assertEqual(lawnotes.abbreviation_rejects(f), [])
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

    def test_night_review_fixes(self):
        self.assertEqual(lawnotes.CITE_RE.search("Francovich v Italy [1991] ECR I-5357").group(), "[1991] ECR I-5357")
        self.assertEqual(lawnotes.authorities(["Case C-6/90 Francovich v Italy [1991] ECR I-5357"])["cases"],
                         [("EU cases", [("Francovich v Italy", "[1991] ECR I-5357")])])
        ab = lawnotes.DEFAULT_ABBREVIATIONS
        self.assertIsNone(lawnotes.expand_abbreviation("see [[Tort/CL", 13, ab))  # inside a link
        self.assertEqual(lawnotes.expand_abbreviation("[[Tort]] CL", 11, ab), ("[[Tort]] Common Law", 19))

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

    def test_without_a_terminal_it_says_so_and_logs_no_crash(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lawnotes.py")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a terminal window", r.stderr)
        log = open(lawnotes.LOG_FILE).read()
        self.assertIn("not started: no terminal window", log)
        self.assertNotIn("CRASH", log)

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
        with self.assertRaises(OSError):                            # ...but a rating never overwrites it
            lawnotes.save_progress({"x": {"box": 3, "at": 100.0}})
        self.assertEqual(open(path).read(), "{not json")
        os.remove(path)
        lawnotes.save_progress({"x": {"box": 3, "at": 100.0}, "y": {"box": 1, "at": 50.0}})
        # another Mac: a later review of y, an older one of x, and an iCloud conflict copy
        copy = os.path.join(self.dir, ".revision 2.json")
        json.dump({"version": 1, "cards": {"x": {"box": 1, "at": 90.0}, "y": {"box": 2, "at": 60.0},
                                           "z": {"box": 4, "at": 10.0}}}, open(copy, "w"))
        merged = lawnotes.save_progress({"w": {"box": 1, "at": 120.0}})
        self.assertEqual({k: v["box"] for k, v in merged.items()}, {"x": 3, "y": 2, "z": 4, "w": 1})
        self.assertEqual(json.load(open(path))["cards"], merged)
        self.assertEqual(sorted(os.listdir(self.dir)), [".revision.json"])  # copy merged; no temp file left
        open(copy, "w").write('{"cards": {"half')                  # an unreadable copy is kept, not merged
        lawnotes.save_progress({"v": {"box": 1, "at": 130.0}})
        self.assertEqual(sorted(os.listdir(self.dir)), [".revision 2.json", ".revision.json"])

    def test_only_numbered_revision_copies_are_merged(self):
        path = lawnotes.REVISION_FILE
        lawnotes.save_progress({"first": {"box": 1, "at": 10.0}})
        kept = {}
        for name in (".revision-backup.json", ".revisions.json", ".revision 1.json",
                     ".revision 02.json", ".revision 2-backup.json"):
            other = os.path.join(self.dir, name)
            data = json.dumps({"version": 1, "cards": {name: {"box": 4, "at": 20.0}}})
            with open(other, "w") as f:
                f.write(data)
            kept[other] = data
        copy = os.path.join(self.dir, ".revision 2.json")
        with open(copy, "w") as f:
            json.dump({"version": 1, "cards": {"copy": {"box": 2, "at": 30.0}}}, f)
        later_copy = os.path.join(self.dir, ".revision 10.json")
        with open(later_copy, "w") as f:
            json.dump({"version": 1, "cards": {"later": {"box": 3, "at": 35.0}}}, f)
        merged = lawnotes.save_progress({"rated": {"box": 1, "at": 40.0}})
        self.assertEqual(set(merged), {"first", "copy", "later", "rated"})
        with open(path) as f:
            self.assertEqual(set(json.load(f)["cards"]), set(merged))
        self.assertFalse(os.path.exists(copy))
        self.assertFalse(os.path.exists(later_copy))
        for other, data in kept.items():
            with open(other) as f:
                self.assertEqual(f.read(), data)

    def test_deep_progress_is_left_alone(self):
        path = lawnotes.REVISION_FILE
        data = '{"cards":' + '{"x":' * 2000 + '0' + '}' * 2001
        with open(path, "w") as f:
            f.write(data)
        self.assertIsNone(lawnotes.read_progress(path, strict=True))
        with self.assertRaises(OSError):
            lawnotes.save_progress({"rated": {"box": 1, "at": 40.0}})
        with open(path) as f:
            self.assertEqual(f.read(), data)

    def test_progress_records_have_scalar_values(self):
        path = lawnotes.REVISION_FILE
        good = {"box": 1, "due": "2026-10-27", "at": 10.5, "reviews": 2}
        with open(path, "w") as f:
            json.dump({"version": 1, "cards": {"case": good}}, f)
        self.assertEqual(lawnotes.read_progress(path, strict=True), {"case": good})
        for bad in ({"box": [1]}, {"at": {"nested": 10}}, ["not a record"]):
            with self.subTest(record=bad):
                data = json.dumps({"version": 1, "cards": {"case": bad}})
                with open(path, "w") as f:
                    f.write(data)
                self.assertIsNone(lawnotes.read_progress(path, strict=True))
                self.assertEqual(lawnotes.read_progress(path), {})
                with self.assertRaises(OSError):
                    lawnotes.save_progress({"rated": {"box": 1, "at": 40.0}})
                with open(path) as f:
                    self.assertEqual(f.read(), data)

    def test_progress_dump_errors_keep_original_and_remove_temp(self):
        path = lawnotes.REVISION_FILE
        data = '{"version":1,"cards":{"case":{"box":1,"at":10.0}}}'
        with open(path, "w") as f:
            f.write(data)
        for error in (RecursionError("too deep"), ValueError("bad value")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(lawnotes.json, "dump", side_effect=error):
                    with self.assertRaises(OSError):
                        lawnotes.save_progress({"rated": {"box": 1, "at": 40.0}})
                with open(path) as f:
                    self.assertEqual(f.read(), data)
                self.assertFalse(os.path.exists(path + ".saving~"))

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

    def test_todos_order_filter_tick_and_undo(self):
        a = self.note("Contract/Offer.md", "- [ ] Read Carlill\n  - [ ] Write answer\n- [x] Done\n")
        b = self.note("Tort/Duty.md", "- [ ] Check duty\n* [ ] Not a to-do\n")
        os.utime(a, (100, 100))
        os.utime(b, (200, 200))
        app = self.app(a)

        def pick(title, build, *args, **kwargs):
            self.assertEqual(title(), "To-dos · 3 in 2 notes")
            self.assertEqual([label for label, _ in build("")],
                             ["Check duty · Tort/Duty", "Read Carlill · Contract/Offer",
                              "Write answer · Contract/Offer"])
            self.assertEqual([label for label, _ in build("read contract")],
                             ["Read Carlill · Contract/Offer"])
            self.assertTrue(kwargs["on_key"]("x", build("")[0][1]))
            self.assertEqual(title(), "To-dos · 2 in 1 note")
            return None

        app.pick = pick
        app.todos()
        self.assertEqual(app.buf.path, b)
        self.assertEqual(app.buf.lines[0], "- [x] Check duty")
        self.assertEqual(open(b).read().splitlines()[0], "- [ ] Check duty")
        self.assertTrue(app.buf.undo())
        self.assertEqual(app.buf.lines[0], "- [ ] Check duty")

    def test_todos_empty_message(self):
        a = self.note("Done.md", "- [x] Finished\n")
        app = self.app(a)
        app.todos()
        self.assertEqual(app.msg, "No open to-dos in your notes")

    def test_todos_narrow_labels_keep_the_note(self):
        a = self.note("Contract/Offer.md", "- [ ] Read the whole judgment about unilateral offers\n")
        app = self.app(a)
        app.scr = type("Screen", (), {"getmaxyx": lambda self: (40, 40)})()

        def pick(title, build, *args, **kwargs):
            label = build("unilateral")[0][0]  # the full text is still searchable
            self.assertLessEqual(len(label), 38)
            self.assertTrue(label.endswith(" · Contract/Offer"))
            return None

        app.pick = pick
        app.todos()

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
        self.assertIn("1 to check", title)
        self.assertEqual([path for _, path in problems], [d])
        self.assertTrue(problems[0][0].startswith("ro/D.md — not changed"))
        right.buf.undo()                                             # ^Z in an open note
        self.assertEqual(right.buf.lines, ["Donohue", "Donohue again"])
        h = app.history                                              # ^R has the closed notes as they were
        self.assertEqual(h.content(h.versions(c)[0][0], c), "See Donohue.\nDonohue twice: Donohue\n")
        self.assertEqual(sorted(os.listdir(os.path.dirname(c))), ["C.md"])  # no conflict or temp files

    def test_all_notes_leaves_notes_a_save_would_change(self):
        a = self.note("A.md", "Donohue\n")
        raw = {p: open(p, "rb").read() for p in (
            self.note("Tabs.md", "Donohue\n\tindented\n"), self.note("Win.md", "Donohue\r\nline\r\n"))}
        latin = os.path.join(lawnotes.NOTES_DIR, "Latin.md")
        open(latin, "wb").write(b"Donohue caf\xe9\n")
        raw[latin] = open(latin, "rb").read()
        bare = self.note("Bare.md", "Donohue")                       # no final newline: still fine
        app = self.app(a)
        app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
        for p, data in raw.items():
            self.assertEqual(open(p, "rb").read(), data, p)
        self.assertEqual(open(bare).read(), "Donoghue\n")
        self.assertIn("Replaced 2 in 2 notes", app.msg)
        self.assertEqual(sorted(path for _, path in app.picks[0][1]), sorted(raw))
        self.assertIn("tabs, Windows line endings or another encoding", app.picks[0][1][0][0])

    def test_all_notes_needs_working_history(self):
        a = self.note("A.md", "Donohue\n")
        c = self.note("C.md", "Donohue\n")
        open(lawnotes.HISTORY_DIR, "w").write("not a git store")      # history broken: closed notes wait
        app = self.app(a)
        app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
        self.assertEqual(open(a).read(), "Donoghue\n")              # the open note: ^Z undoes it
        self.assertEqual(open(c).read(), "Donohue\n")
        self.assertIn("version history isn't working", app.picks[0][1][0][0])

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

    def test_case_emphasis_inside_markdown_formatting(self):
        import xml.etree.ElementTree as ET
        import zipfile
        lines = ["**Donoghue v Stevenson** [1932] AC 562",
                 "**see Donoghue v Stevenson for this**", "*Donoghue v Stevenson*",
                 "_Donoghue v Stevenson_", "***Donoghue v Stevenson***",
                 "==Donoghue v Stevenson==", "[[Cases|Donoghue v Stevenson]]",
                 "__Donoghue v Stevenson__", "_**Donoghue v Stevenson**_",
                 "**_Donoghue v Stevenson_**"]
        expected = ["***Donoghue v Stevenson*** [1932] AC 562",
                    "**see *Donoghue v Stevenson* for this**", "*Donoghue v Stevenson*",
                    "_Donoghue v Stevenson_", "***Donoghue v Stevenson***",
                    "==*Donoghue v Stevenson*==", "*Donoghue v Stevenson*",
                    "___Donoghue v Stevenson___", "_**Donoghue v Stevenson**_",
                    "**_Donoghue v Stevenson_**"]
        self.assertEqual(lawnotes.export_markdown(lines).splitlines(), expected)
        html = subprocess.run([lawnotes.find_tool("pandoc"), "-f", "markdown", "-t", "html5"],
                              input=expected[7], capture_output=True, text=True, check=True).stdout
        self.assertIn("<strong><em>Donoghue v Stevenson</em></strong>", html)
        path = lawnotes.export_note(os.path.join(TMP, "Case emphasis.md"),
                                    [part for line in lines for part in (line, "")], "docx")
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = [p for p in root.iter(w + "p") if "Donoghue v Stevenson" in
                      "".join(t.text or "" for t in p.iter(w + "t"))]
        self.assertEqual(len(paragraphs), len(lines))
        for i, paragraph in enumerate(paragraphs):
            runs = [r for r in paragraph.iter(w + "r") if "Donoghue v Stevenson" in
                    "".join(t.text or "" for t in r.iter(w + "t"))]
            self.assertEqual(len(runs), 1, lines[i])
            props = runs[0].find(w + "rPr")
            self.assertIsNotNone(props, lines[i])
            self.assertIsNotNone(props.find(w + "i"), lines[i])
            if i in (0, 1, 4, 7, 8, 9):
                self.assertIsNotNone(props.find(w + "b"), lines[i])
            else:
                self.assertIsNone(props.find(w + "b"), lines[i])

    def test_bold_case_export_key_in_narrow_pane(self):
        import xml.etree.ElementTree as ET
        import zipfile
        note = os.path.join(lawnotes.NOTES_DIR, "Bold export.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        with open(note, "w") as f:
            f.write("**Donoghue v Stevenson** [1932] AC 562\n\n"
                    "__Donoghue v Stevenson__\n")
        s = Session(note, env={"LAWNOTES_EXPORT_DIR": self.out}, size=(40, 40))
        self.assertIn("Export this note", s.keys(b"\x1be", 0.6))
        s.keys(b"\r", 0.6)
        out = os.path.join(self.out, "Bold export.docx")
        for _ in range(100):
            if os.path.exists(out):
                break
            s.out += s.read(0.1)
        self.assertTrue(s.quit())
        with zipfile.ZipFile(out) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        runs = [r for r in root.iter(w + "r") if "Donoghue v Stevenson" in
                "".join(t.text or "" for t in r.iter(w + "t"))]
        self.assertEqual(len(runs), 2)
        for run in runs:
            props = run.find(w + "rPr")
            self.assertIsNotNone(props.find(w + "b"))
            self.assertIsNotNone(props.find(w + "i"))

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


class OscolaTests(unittest.TestCase):
    def test_round_year_before_a_volume(self):
        for line, year in (("Carlill v Carbolic Smoke Ball Co (1893) 1 QB 256.", "[1893]"),
                           ("Example v Other (2020) 1 WLR 100", "[2020]")):  # years organise these series
            self.assertEqual([(i[3], i[4]) for i in lawnotes.oscola_issues([line])],
                             [("Use square brackets", year)])
        self.assertEqual(lawnotes.oscola_issues(["Rylands v Fletcher (1868) LR 3 HL 330."]), [])

    def test_likely_slips_and_fixes(self):
        lines = ["Donoghue v. Stevenson (1932) AC 562; R vs Smith [2020] UKSC 1",
                 "Caparo vs. Dickman [1990] A.C. 605, p. 12",
                 "s. 2; ss. 3; para. 4",
                 "[^1]: See Donoghue v Stevenson [1932] QB 1"]
        issues = lawnotes.oscola_issues(lines)
        self.assertEqual([item[3] for item in issues],
                         ["Use v, not v.", "Use square brackets", "Use v, not vs",
                          "Use v, not vs.", "Remove full stops", "Drop p. for page",
                          "Remove provision stop", "Remove provision stop", "Remove provision stop",
                          "Add final full stop"])
        for i, start, end, _, fix in reversed(issues):
            lines[i] = lines[i][:start] + fix + lines[i][end:]
        self.assertEqual(lines, ["Donoghue v Stevenson [1932] AC 562; R v Smith [2020] UKSC 1",
                                 "Caparo v Dickman [1990] AC 605, 12",
                                 "s 2; ss 3; para 4",
                                 "[^1]: See Donoghue v Stevenson [1932] QB 1."])
        self.assertEqual(lawnotes.oscola_issues(lines), [])

    def test_correct_citations_and_ignored_text(self):
        lines = ["Donoghue v Stevenson [1932] AC 562",
                 "Example v Other (1868) LR 3 HL 330",
                 "R v Smith [2020] UKSC 1; s 2; para 3",
                 "[^1]: The judgment ends here.",
                 "[^2]: ‘ibid.’",
                 "`Donoghue v. Stevenson (1932) AC 562`",
                 "See https://example.test/x // Donoghue vs Stevenson and s. 2",
                 "The A.C. was on; p. 12 of the essay"]
        self.assertEqual(lawnotes.oscola_issues(lines), [])

    def test_year_series_with_no_volume_number(self):
        for series in ("AC", "QB", "KB", "Ch", "Fam", "WLR", "All ER", "UKSC", "UKHL",
                       "UKPC", "EWCA", "EWCA Civ", "EWCA Crim", "EWHC"):
            with self.subTest(series=series):
                self.assertEqual([issue[3] for issue in lawnotes.oscola_issues([f"(2001) {series} 12"])],
                                 ["Use square brackets"])

    def test_dotted_report_series(self):
        for series in ("A.C.", "Q.B.", "W.L.R."):
            with self.subTest(series=series):
                self.assertEqual([issue[3] for issue in lawnotes.oscola_issues([f"[2001] {series} 12"])],
                                 ["Remove full stops"])
class ExamTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(dir=TMP), "Essay.md")

    def test_body_words_skip_comments_footnotes_and_markers(self):
        w = lawnotes.body_words
        self.assertEqual(w(["# Duty of care"]), 3)
        self.assertEqual(w(["The **neighbour** principle in *Donoghue v Stevenson*."]), 7)
        self.assertEqual(w(["- the burden of proof"]), 4)
        self.assertEqual(w(["> Lord Atkin's test"]), 3)
        self.assertEqual(w(["See the case.[^1] // from the lecture"]), 3)
        self.assertEqual(w(["[^1]: This footnote is not counted."]), 0)
        self.assertEqual(w(["[[Tort/Duty|duty of care]]"]), 3)
        self.assertEqual(w(["==highlighted words== here"]), 3)
        self.assertEqual(w(["s 2(1) of the Act"]), 5)
        self.assertEqual(w(["[1932] AC 562"]), 3)
        self.assertEqual(w(["| Case | Held |"]), 2)
        self.assertEqual(w(["| --- | --- |"]), 0)
        self.assertEqual(w(["```"]), 0)
        self.assertEqual(w(["https://ucl.ac.uk/notes stays"]), 2)  # the // in a URL stays
        self.assertEqual(w(["// just a note"]), 0)
        self.assertEqual(w(["- **Held:** the duty"]), 3)
        self.assertEqual(w(["- [ ] task"]), 1)
        self.assertEqual(w(["D_1 remains"]), 2)
        self.assertEqual(w(["## Title ##"]), 1)
        self.assertEqual(w(["`ratio decidendi`"]), 2)
        raw = ["Duty // note", "[^1]: hidden words here"]
        self.assertEqual(w(raw), 1)
        self.assertGreater(sum(len(line.split()) for line in raw), 1)

    def test_body_word_count_follows_edits(self):
        b = lawnotes.Buffer(self.path)
        b.insert("# Duty")
        self.assertEqual(b.body_word_count(), 1)
        self.assertEqual(b.word_count(), 2)  # the heading mark counts in the ordinary total
        b.newline()
        b.insert("hello // hidden")
        self.assertEqual(b.body_word_count(), 2)
        b.newline()
        b.insert("[^1]: not in the essay")
        self.assertEqual(b.body_word_count(), 2)
        self.assertGreater(b.word_count(), b.body_word_count())
        b.insert(" more")  # still on the footnote line
        self.assertEqual(b.body_word_count(), 2)
        b.cy, b.cx = 0, len(b.lines[0])
        b.insert(" more")
        self.assertEqual(b.body_word_count(), 3)

    def test_exam_status_clock_limit_and_warnings(self):
        text, count, over = lawnotes.exam_status(90 * 60, 1214, 2000)
        self.assertEqual(text, "1:30:00  1,214/2,000 words")
        self.assertEqual(count, "1,214/2,000 words")
        self.assertFalse(over)
        self.assertEqual(lawnotes.exam_status(45 * 60, 12, None)[0], "45:00  12 words")
        self.assertEqual(lawnotes.exam_status(59, 1, None)[0], "00:59  1 word")
        self.assertEqual(lawnotes.exam_clock(3600), "1:00:00")
        self.assertEqual(lawnotes.exam_clock(3599), "59:59")
        text, count, over = lawnotes.exam_status(0, 1987, 2000)
        self.assertEqual(text, "Time's up · 1,987 words · +0:00")
        self.assertFalse(over)
        text, count, over = lawnotes.exam_status(-192, 2100, 2000)
        self.assertEqual(text, "Time's up · 2,100 words · +3:12")
        self.assertEqual(count, "2,100 words")
        self.assertTrue(over)
        self.assertFalse(lawnotes.exam_status(10, 2000, 2000)[2])  # on the limit is not past it
        self.assertTrue(lawnotes.exam_status(10, 2001, 2000)[2])
        self.assertEqual(lawnotes.exam_over(3661), "+1:01:01")
        self.assertEqual(lawnotes.exam_warning(90, 600.9, False, False)[0], "10 minutes left")
        self.assertIsNone(lawnotes.exam_warning(90, 601, False, False)[0])
        self.assertIsNone(lawnotes.exam_warning(10, 600, False, False)[0])  # started at 10:00
        msg, told10, told5 = lawnotes.exam_warning(90, 200, False, False)
        self.assertEqual((msg, told10, told5), ("5 minutes left", True, True))
        self.assertEqual(lawnotes.exam_warning(8, 300, False, False)[0], "5 minutes left")
        self.assertIsNone(lawnotes.exam_warning(4, 240, False, False)[0])
        self.assertIsNone(lawnotes.exam_warning(90, 0, False, False)[0])
        # a 40-column pane keeps the clock and the count, and cuts the name before them
        essential = "1:30:00  1,214/2,000 words"
        line = lawnotes.exam_bar_text("Contract/Negligence Essay.md", "10 minutes left", essential, 39)
        self.assertEqual(len(line), 39)
        self.assertTrue(line.endswith(essential), line)
        self.assertIn("10 min", line)
        self.assertNotIn("Negligence", line)
        plain = lawnotes.exam_bar_text("Exam.md", "", essential, 39)
        self.assertIn("Exam.md", plain)
        self.assertTrue(plain.endswith(essential))
        ended, _, _ = lawnotes.exam_status(-192, 1987, None)
        self.assertTrue(lawnotes.exam_bar_text("Exam.md", "", ended, 39).endswith(ended))

    def test_help_puts_exam_with_revising(self):
        rev = next(i for i, (k, d) in enumerate(lawnotes.HELP) if k == "#" and d == "Revising")
        nxt = next(i for i, (k, d) in enumerate(lawnotes.HELP) if i > rev and k == "#")
        text = dict(lawnotes.HELP[rev:nxt])["Option+X"]
        self.assertIn("F8", text)
        self.assertIn("keep writing", text)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for detail in ("`//` comments", "`[^1]: …`", "Markdown markers", "warning colour", "keep writing"):
            self.assertIn(detail, readme)


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
        self.out = self.settle(2, quiet=0.3, cap=15)

    def read(self, seconds=0.4):
        out, end = b"", time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], max(0, min(0.02, end - time.time())))[0]:
                try:
                    out += os.read(self.fd, 65536)
                except OSError:
                    break
        return out

    def settle(self, seconds, quiet=0.08, cap=3):
        """Read until the editor's output goes quiet: at least one chunk, then `quiet` s of silence,
        up to `cap` s in all. If nothing arrives within `seconds` it stops there, so a test that
        asserts "nothing happened" still waits as long as it always did. A slow runner just gets
        more time (up to `cap`) once the editor has started answering."""
        out, start = b"", time.monotonic()
        last = None
        while True:
            now = time.monotonic()
            if last is None:
                if now - start >= seconds:
                    break
            elif now - last >= quiet or now - start >= max(cap, seconds):
                break
            if select.select([self.fd], [], [], 0.01)[0]:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    break
                if chunk:
                    out += chunk
                    last = time.monotonic()
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
        if seconds < 0.05:
            out = self.read(seconds)  # fire and forget (the fuzz test)
        else:
            # a lone Esc is only resolved by the editor after ESCAPE_WAIT (250 ms)
            out = self.settle(seconds)
        self.out += out
        return re.sub(rb"\x1b\[[0-9;?<>]*[A-Za-z~]|\x1b\(B|\x1b[=>]", b" ", out).decode(errors="replace")

    def keys_until(self, data, expected, seconds=3):
        """Send keys, then keep reading until the expected screen text appears or time runs out."""
        out = self.keys(data, 0.1)
        deadline = time.monotonic() + seconds
        while expected not in out and time.monotonic() < deadline:
            out += self.keys(b"", 0.1)
        return out

    def quit(self, seconds=8):
        """^Q, then wait for the editor to exit; kill it and return False if it doesn't. The exit
        snapshots the shared test notes folder, which grows as the suite runs: hence the time."""
        self.keys(b"\x11", 0)
        for _ in range(int(seconds * 10)):
            if os.waitpid(self.pid, os.WNOHANG)[0] == self.pid:
                return True
            self.out += self.read(0.1)
        os.kill(self.pid, 9)
        while os.waitpid(self.pid, os.WNOHANG)[0] != self.pid:
            self.out += self.read(0.1)  # a killed editor can't finish exiting until its output is read
        return False


class SessionTests(unittest.TestCase):
    def test_todos_keys_in_narrow_pane(self):
        folder = os.path.join(tempfile.mkdtemp(dir=TMP), "notes", "Todos")
        os.makedirs(folder, exist_ok=True)
        env = {"LAWNOTES_DIR": os.path.dirname(folder)}
        a, b = os.path.join(folder, "A.md"), os.path.join(folder, "B.md")
        open(a, "w").write("- [ ] Read Carlill\n- [x] Done\n")
        open(b, "w").write("- [ ] Write answer\n")
        os.utime(a, (100, 100))
        os.utime(b, (200, 200))
        s = Session(a, env=env, size=(40, 40))
        screen = s.keys(b"\x1bj", 0.6)
        self.assertIn("To-dos · 2 in 2 notes", screen)
        self.assertIn("Write answer · Todos/B", screen)
        screen = s.keys(b"x", 0.6)
        self.assertIn("1 in 1", screen)  # curses redraws only the changed part of the title
        self.assertIn("Read Carlill · Todos/A", screen)
        s.keys(b"\x1b\x1a", 0.6)  # leave list, then undo the tick in B
        self.assertTrue(s.quit())
        self.assertEqual(open(b).read(), "- [ ] Write answer\n")

        s = Session(a, env=env, size=(40, 40))
        screen = s.keys(b"\x1b[21~", 0.6)  # F10
        self.assertIn("To-dos · 2 in 2 notes", screen)
        s.keys(b"\r\x05!\x13", 0.6)  # Enter jumps to B's to-do
        self.assertTrue(s.quit())
        self.assertEqual(open(b).read(), "- [ ] Write answer!\n")
    def test_oscola_keys_fix_undo_and_jump_in_narrow_pane(self):
        note = os.path.join(lawnotes.NOTES_DIR, "OSCOLA.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        original = "Donoghue v. Stevenson [1932] AC 562\nCaparo v. Dickman [1990] AC 605\n"
        open(note, "w").write(original)
        s = Session(note, size=(40, 40))
        screen = s.keys(b"\x1bk", 0.6)
        self.assertIn("OSCOLA", screen)
        self.assertIn("Use v, not v.", screen)
        screen = s.keys(b"f", 0.6)
        self.assertIn("Caparo v. Dickman", screen)  # the list was rebuilt after the first fix
        screen = s.keys(b"f", 0.6)
        self.assertIn("No OSCOLA slips found in this note", screen)
        s.keys(b"\x1a\x1a", 0.4)  # each fix has its own undo step
        screen = s.keys(b"\x1b[20~", 0.6)
        self.assertIn("Use v, not v.", screen)
        s.keys(b"\rX\x13", 0.5)
        self.assertTrue(s.quit())
        self.assertEqual(open(note).read(), "Donoghue Xv. Stevenson [1932] AC 562\n"
                           "Caparo v. Dickman [1990] AC 605\n")

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

    def test_case_tab_completion_picker_escape_undo_and_fallback(self):
        root = os.path.join(tempfile.mkdtemp(dir=TMP), "notes")
        os.makedirs(root)
        source, draft = os.path.join(root, "Cases.md"), os.path.join(root, "Draft.md")
        with open(source, "w") as f:
            f.write("Caparo Industries plc v Dickman [1990] AC 605\n"
                    "Caparo Industries plc v Dickman [1990] 2 AC 605\n"
                    "Caparo Industries plc v Dickman [1990] 2 AC 605\n"
                    "Caparo Manufacturing Ltd v Clark\n"
                    "Donoghue v Stevenson [1932] AC 562\n")
        with open(draft, "w") as f:
            f.write("Dono\nCapa\n// Capa\n- x\n")
        def saved_lines():
            with open(draft) as f:
                return f.read().splitlines()

        s = Session(draft, env={"LAWNOTES_DIR": root}, size=(40, 40))
        s.keys(b"\x05\t", 0.6)
        s.keys(b"\x13", 0.4)
        self.assertEqual(saved_lines()[0], "Donoghue v Stevenson [1932] AC 562")
        s.keys(b"\x1a\x13", 0.4)
        self.assertEqual(saved_lines()[0], "Dono")
        screen = s.keys(b"\x1bOB\x05\t", 0.6)
        self.assertIn("Complete case", screen)
        self.assertIn("Caparo Industries", screen)
        s.keys(b"\x1b\x13", 0.4)
        self.assertEqual(saved_lines()[1], "Capa")
        s.keys(b"\t\r\x13", 0.6)
        self.assertEqual(saved_lines()[1],
                         "Caparo Industries plc v Dickman [1990] 2 AC 605")
        s.keys(b"\x1bOB\x05\t\x1bOB\x05\t\x13", 0.6)
        self.assertTrue(s.quit())
        self.assertEqual(saved_lines()[2:], ["// Capa    ", "  - x"])

    def test_case_index_ignores_stale_hits_without_creating_a_note(self):
        root = os.path.join(tempfile.mkdtemp(dir=TMP), "notes")
        os.makedirs(root)
        short = os.path.join(root, "Short.md")
        renamed = os.path.join(root, "Renamed.md")
        draft = os.path.join(root, "Draft.md")
        with open(short, "w") as f:
            f.write("header\nDonoghue v Stevenson\nDonoghue v Stevenson\n")
        with open(renamed, "w") as f:
            f.write("Caparo v Dickman\n")
        with open(draft, "w") as f:
            f.write("# Draft\n")
        s = Session(draft, env={"LAWNOTES_DIR": root}, size=(40, 40))
        self.assertIn("Donoghue", s.keys(b"\x02Donoghue", 0.5))
        with open(short, "w") as f:
            f.write("gone\n")
        self.assertIn("no longer", s.keys(b"\r", 0.5))
        self.assertIn("Caparo", s.keys(b"\x02Caparo", 0.5))
        moved = os.path.join(root, "Moved.md")
        os.rename(renamed, moved)
        self.assertIn("no longer", s.keys(b"\r", 0.5))
        self.assertTrue(s.quit())
        self.assertFalse(os.path.exists(renamed))
        with open(draft) as f:
            self.assertEqual(f.read(), "# Draft\n")

    def test_case_tab_keeps_midline_bullet_and_common_word(self):
        root = os.path.join(tempfile.mkdtemp(dir=TMP), "notes")
        os.makedirs(root)
        source, draft = os.path.join(root, "Cases.md"), os.path.join(root, "Draft.md")
        with open(source, "w") as f:
            f.write("Donoghue v Stevenson [1932] AC 562\nTheakston v MGN Ltd\n")
        with open(draft, "w") as f:
            f.write("- Donoghue v Stevenson\nthe\n")
        s = Session(draft, env={"LAWNOTES_DIR": root}, size=(40, 40))
        s.keys(b"\x01" + b"\x1bOC" * 11 + b"\t\x13", 0.6)
        s.keys(b"\x1bOB\x05\t\x13", 0.6)
        self.assertTrue(s.quit())
        with open(draft) as f:
            self.assertEqual(f.read(), "  - Donoghue v Stevenson\nthe    \n")

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
        self.assertIn("X#", s.keys_until(b"X", "X#"))
        s.keys(b"\x1bo", 0.3)
        self.assertIn("CY", s.keys_until(b"\x05Y", "CY"))  # left pane: end of line, type
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

    def test_deep_revision_progress_warns_at_each_width(self):
        messages = ((100, "Revised 1 card: 0 good, 0 hard, 1 again · 1 left · PROGRESS NOT SAVED"),
                    (40, "PROGRESS NOT SAVED; file left alone"),
                    (30, "PROGRESS NOT SAVED"))
        for width, expected in messages:
            with self.subTest(width=width):
                notes_dir = os.path.join(tempfile.mkdtemp(dir=TMP), "notes")
                os.makedirs(notes_dir)
                note = os.path.join(notes_dir, "DeepProgress.md")
                with open(note, "w") as f:
                    f.write("## R v Brown [1994] 1 AC 212\n- **Held:** Consent is no defence.\n")
                progress = os.path.join(notes_dir, ".revision.json")
                data = '{"cards":' + '{"x":' * 2000 + '0' + '}' * 2001
                with open(progress, "w") as f:
                    f.write(data)
                s = Session(note, env={"LAWNOTES_DIR": notes_dir}, size=(40, width))
                try:
                    self.assertIn("This note", s.keys(b"\x1bc", 0.8))
                    self.assertIn("R v Brown", s.keys(b"\r", 0.5))
                    self.assertIn("Good", s.keys(b" ", 0.5))
                    s.keys(b"1", 0.5)
                    self.assertIn(expected, s.keys(b"\x1b", 0.7))
                    self.assertTrue(s.quit())
                finally:
                    try:
                        os.kill(s.pid, 9)
                    except OSError:
                        pass
                    try:
                        os.waitpid(s.pid, 0)
                    except OSError:
                        pass
                with open(progress) as f:
                    self.assertEqual(f.read(), data)

    def test_revision_rating_keeps_other_json_files(self):
        notes_dir = os.path.join(tempfile.mkdtemp(dir=TMP), "notes")
        os.makedirs(notes_dir)
        note = os.path.join(notes_dir, "Copies.md")
        with open(note, "w") as f:
            f.write("## R v Brown [1994] 1 AC 212\n- **Held:** Consent is no defence.\n")
        kept = {}
        for name in (".revision-backup.json", ".revisions.json"):
            path = os.path.join(notes_dir, name)
            data = json.dumps({"version": 1, "cards": {name: {"box": 4, "at": 20.0}}})
            with open(path, "w") as f:
                f.write(data)
            kept[path] = data
        copy = os.path.join(notes_dir, ".revision 2.json")
        with open(copy, "w") as f:
            json.dump({"version": 1, "cards": {"copy": {"box": 2, "at": 30.0}}}, f)
        s = Session(note, env={"LAWNOTES_DIR": notes_dir}, size=(40, 40))
        try:
            self.assertIn("This note", s.keys(b"\x1bc", 0.8))
            self.assertIn("R v Brown", s.keys(b"\r", 0.5))
            self.assertIn("Good", s.keys(b" ", 0.5))
            s.keys(b"3", 0.7)
            self.assertTrue(s.quit())
        finally:
            try:
                os.kill(s.pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(s.pid, 0)
            except OSError:
                pass
        with open(os.path.join(notes_dir, ".revision.json")) as f:
            cards = json.load(f)["cards"]
        self.assertIn("copy", cards)
        self.assertIn("Copies.md::r v brown", cards)
        self.assertFalse(os.path.exists(copy))
        for path, data in kept.items():
            with open(path) as f:
                self.assertEqual(f.read(), data)

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

    def test_exam_mode(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Exam.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        text = "# Duty\n\nThe neighbour principle. // ignore me\n[^1]: not counted at all\nStill body\n"
        with open(note, "w") as f:
            f.write(text)
        lines = text.splitlines()
        start = lawnotes.body_words(lines)
        lines[-1] += " here"
        grown = lawnotes.body_words(lines)
        lines[-1] += " yes"
        more = lawnotes.body_words(lines)
        s = Session(note, size=(40, 40))
        self.assertIn("Exam", s.keys(b"\x1b[<2;4;1M\x1b[<2;4;1m", 0.6))  # right-click
        s.keys(b"\x1b", 0.4)
        self.assertIn("Minutes", s.keys(b"\x1bx", 0.5))                    # Option+X
        self.assertNotIn("Word limit", s.keys(b"\x1b", 0.4))               # Esc cancels
        s.keys(b"\x1bx", 0.4)
        self.assertIn("number", s.keys(b"nope\r", 0.4))
        s.keys(b"\x1b", 0.4)
        s.keys(b"\x1bx", 0.4)
        s.keys(b"90\r", 0.4)
        self.assertNotIn("1:30:00", s.keys(b"\x1b", 0.4))                  # Esc at the word limit
        self.assertIn("Minutes", s.keys(b"\x1b[19~", 0.5))                 # F8
        s.keys(b"90\r", 0.4)
        screen = s.keys(b"2,000\r", 0.6)
        self.assertRegex(screen, r"1:(30:00|29:5\d)")                  # the clock may already have ticked
        self.assertIn(f"{start}/2,000 words", screen)                      # readable at 40 columns
        self.assertRegex(s.keys(b"\x1b[1;3B here", 0.5), rf"\b{grown}\b")  # only the count is redrawn
        self.assertIn("Stop the exam", s.keys(b"\x1bx", 0.5))
        self.assertNotIn("Exam stopped", s.keys(b"n", 0.4))                 # n keeps it going
        self.assertIn("Stop the exam", s.keys(b"\x1bx", 0.5))
        screen = s.keys(b"y", 0.5)
        self.assertIn("Exam stopped", screen)
        self.assertNotIn("/2,000", screen)
        s.keys(b"\x1bx", 0.4)
        s.keys(b"90\r", 0.4)
        screen = s.keys(b"\r", 0.5)                                        # blank word limit
        self.assertRegex(screen, rf"1:\d\d:\d\d  {grown} words")
        self.assertNotIn(f"{grown}/", screen)
        self.assertIn("Stop the exam", s.keys(b"\x1bx", 0.4))
        s.keys(b"y", 0.4)
        self.assertNotIn(b"48;5;160", s.out)                               # warning colour, not yet
        s.keys(b"\x1bx", 0.4)
        s.keys(b"90\r", 0.4)
        self.assertIn(f"{grown}/2 words", s.keys(b"2\r", 0.5))             # already past the limit
        self.assertIn(b"48;5;160", s.out)
        self.assertRegex(s.keys(b"\x1b[1;3B yes", 0.5), rf"\b{more}\b")   # still editable
        self.assertIn("Notes in", s.keys(b"\x1bD", 0.8))                   # the new pane has no exam
        s.keys(b"\x1b", 0.6)                                               # keep this note
        s.keys(b"\x1bo", 0.5)                                              # back on the exam pane
        s.keys(b"\x1bw", 0.6)                                              # closing it ends the exam:
        self.assertIn("Minutes", s.keys(b"\x1bx", 0.5))                    # a new one, not "Stop the exam"
        s.keys(b"\x1b", 0.4)
        self.assertTrue(s.quit())
        saved = open(note).read()
        self.assertIn("Still body here yes", saved)
        self.assertIn("// ignore me", saved)
        self.assertIn("[^1]: not counted at all", saved)


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


    def test_abbreviations_file_has_one_source_for_its_header(self):
        notes = tempfile.mkdtemp(dir=TMP)
        self.env["LAWNOTES_DIR"] = notes
        r = self.launch("--abbreviations")
        self.assertNotIn("Traceback", r.stderr)
        with open(os.path.join(notes, ".abbreviations.txt"), encoding="utf-8") as f:
            made = f.read()
        self.assertEqual(made, lawnotes.abbreviations_text("", []))
        self.assertTrue(made.startswith(lawnotes.ABBREV_HEADER))
        self.assertIn("(nothing) turns one off", made)
        with open(os.path.join(ROOT, "lawnotes"), encoding="utf-8") as f:
            self.assertNotIn("Exact case; whole words only", f.read())

    def test_hint_bar_starts_as_before_and_lists_newer_features(self):
        keys = [k for k, _ in lawnotes.HINTS]
        self.assertEqual(keys[:11], ["^G", "^S", "^O", "^P", "^B", "^F", "^T", "^W", "^R", "⌥D", "^Q"])
        self.assertEqual(keys[11:], ["⌥E", "⌥C", "⌥J", "⌥K", "⌥X"])


class RevisionPackTests(unittest.TestCase):
    WHEN = datetime.date(2026, 10, 22)

    def setUp(self):
        self.out = tempfile.mkdtemp(dir=TMP)
        self._export = lawnotes.EXPORT_DIR
        lawnotes.EXPORT_DIR = self.out

    def tearDown(self):
        lawnotes.EXPORT_DIR = self._export

    def test_date_is_british(self):
        self.assertEqual(lawnotes.british_date(datetime.date(2026, 10, 22)), "22 October 2026")
        self.assertEqual(lawnotes.british_date(datetime.date(2026, 1, 1)), "1 January 2026")

    def test_footnotes_are_renamed_per_note(self):
        notes = [
            ("Offer", ["Duty.[^1] and more.[^2]", "", "[^1]: Neighbour.", "[^2]: Proximity."]),
            ("Acceptance", ["Posted.[^1]", "", "[^1]: Postal rule."]),
        ]
        md = lawnotes.revision_pack_markdown(notes, "Contract", self.WHEN)
        self.assertIn("[^n1-1]", md)
        self.assertIn("[^n1-2]", md)
        self.assertIn("[^n2-1]", md)
        self.assertIn("[^n1-1]: Neighbour.", md)
        self.assertIn("[^n2-1]: Postal rule.", md)
        self.assertNotRegex(md, r"\[\^1\]")
        self.assertNotRegex(md, r"\[\^2\]")
        # one note on its own is unchanged
        alone = lawnotes.export_markdown(["Duty.[^1]", "", "[^1]: Neighbour."])
        self.assertIn("[^1]", alone)
        self.assertNotIn("[^n", alone)

    def test_headings_drop_one_level(self):
        lines = ["# A", "## B", "### C", "#### D", "##### E", "###### F",
                 "## See [[Tort/X|the duty]]"]
        md = lawnotes.revision_pack_markdown([("Note", lines)], "Tort", self.WHEN)
        self.assertIn("\n# Note\n", md)
        self.assertIn("\n## A\n", md)
        self.assertIn("\n### B\n", md)
        self.assertIn("\n#### C\n", md)
        self.assertIn("\n##### D\n", md)
        self.assertIn("\n###### E\n", md)
        self.assertIn("\n###### F\n", md)          # already as deep as Markdown goes
        self.assertNotIn("#######", md)
        self.assertNotIn("\n# A\n", md)
        self.assertIn("\n### See the duty\n", md)
        self.assertNotIn("[[", md)

    def test_order_comments_links_and_italics(self):
        notes = [
            ("Offer", ["See [[Contract/Acceptance|acceptance]] and Donoghue v Stevenson.[^1] // HIDEME",
                       "", "[^1]: The neighbour.", "// whole line"]),
            ("Acceptance", ["Posted."]),
        ]
        md = lawnotes.revision_pack_markdown(notes, 'Say "hi"', self.WHEN)
        self.assertLess(md.index("\n# Offer\n"), md.index("\n# Acceptance\n"))
        self.assertIn('title: "Say \\"hi\\""', md)
        self.assertIn('date: "22 October 2026"', md)
        self.assertIn("acceptance and *Donoghue v Stevenson*", md)
        self.assertNotIn("HIDEME", md)
        self.assertNotIn("whole line", md)
        self.assertNotIn("[[", md)
        self.assertLess(md.index('w:type="page"'), md.index("\n# Offer\n"))

    def test_one_table_set_after_the_contents_slot(self):
        notes = [
            ("Offer", ["Donoghue v Stevenson [1932] AC 562", "Human Rights Act 1998",
                       "// Fake v Case [1999] AC 1"]),
            ("Acceptance", ["Donoghue v Stevenson [1932] AC 562", "R v Brown"]),
        ]
        md = lawnotes.revision_pack_markdown(notes, "Tort", self.WHEN, tables=True)
        self.assertEqual(md.count("## Table of Cases"), 1)
        self.assertEqual(md.count("## Table of Legislation"), 1)
        front = md.split("\n# Offer\n")[0]
        self.assertEqual(front.count("Donoghue v Stevenson"), 1)   # cited in both notes, listed once
        self.assertIn("R v Brown", front)
        self.assertIn("Human Rights Act 1998", front)
        self.assertNotIn("Fake v Case", md)
        self.assertLess(md.index("## Table of Cases"), md.index('w:type="page"'))
        self.assertLess(md.index('w:type="page"'), md.index("\n# Offer\n"))
        plain = lawnotes.revision_pack_markdown(notes, "Tort", self.WHEN)
        self.assertNotIn("Table of Cases", plain)
        empty = lawnotes.revision_pack_markdown([("A", ["No authority here."])], "Tort", self.WHEN, tables=True)
        self.assertNotIn("Table of Cases", empty)

    def test_folder_notes_in_name_order(self):
        listed = [
            (30, "Tort/New.md", "/n/Tort/New.md"),
            (20, "Tortoise/No.md", "/n/Tortoise/No.md"),
            (10, "Tort/Old/Deep.md", "/n/Tort/Old/Deep.md"),
            (0, "Other.md", "/n/Other.md"),
        ]
        self.assertEqual(lawnotes.notes_in_folder(listed, "Tort"), [
            ("New", "/n/Tort/New.md"),
            ("Old/Deep", "/n/Tort/Old/Deep.md"),
        ])
        self.assertEqual(lawnotes.pack_export_name("Tort"), "Tort revision pack")
        self.assertEqual(lawnotes.pack_export_name("Tort/Duty"), "Tort › Duty revision pack")
        self.assertEqual(lawnotes.pack_export_name("Tort/Duty/Breach"), "Tort › Duty › Breach revision pack")
        self.assertNotEqual(lawnotes.pack_export_name("Tort/Duty"), lawnotes.pack_export_name("Tort-Duty"))
        self.assertEqual(lawnotes.pack_export_name("Tort: Duty"), "Tort: Duty revision pack")
        self.assertEqual(lawnotes.pack_export_name("Tort›Duty"), "Tort›Duty revision pack")
        self.assertEqual(lawnotes.pack_export_name("\x01"), "Notes revision pack")
        md = lawnotes.revision_pack_markdown([("Offer", ["An offer."])], "Tort/Duty", self.WHEN)
        self.assertIn('title: "Tort › Duty"', md)
        folder = os.path.join(lawnotes.NOTES_DIR, "PackOrder")
        os.makedirs(os.path.join(folder, "Sub"), exist_ok=True)
        for name, when in (("Week 10.md", 1_000_000_000), ("Sub/Mid.md", 1_100_000_000), ("Week 2.md", 1_200_000_000)):
            path = os.path.join(folder, name)
            open(path, "w").write("x\n")
            os.utime(path, (when, when))
        side = os.path.join(lawnotes.NOTES_DIR, "PackOrder2", "No.md")
        os.makedirs(os.path.dirname(side), exist_ok=True)
        open(side, "w").write("no\n")
        names = [name for name, _ in lawnotes.notes_in_folder(lawnotes.App.list_notes(object()), "PackOrder")]
        self.assertEqual(names, ["Sub/Mid", "Week 2", "Week 10"])  # by name, not by date

    def test_note_folder_and_help(self):
        root = os.path.join(lawnotes.NOTES_DIR, "LoosePack.md")
        nested = os.path.join(lawnotes.NOTES_DIR, "Fold", "Deep", "Cap.md")
        os.makedirs(os.path.dirname(nested), exist_ok=True)
        open(root, "w").write("x\n")
        open(nested, "w").write("x\n")
        self.assertIsNone(lawnotes.note_folder(root))
        self.assertEqual(lawnotes.note_folder(nested), "Fold/Deep")
        outside = os.path.join(TMP, "outside-pack.md")
        open(outside, "w").write("x\n")
        self.assertIsNone(lawnotes.note_folder(outside))
        text = next(desc for key, desc in lawnotes.HELP if key == "Option+E")
        self.assertIn("Revision pack", text)
        self.assertIn("this note's folder", text)

    @unittest.skipUnless(lawnotes.find_tool("pandoc"), "export needs pandoc (brew install pandoc)")
    def test_export_pack_docx(self):
        import zipfile
        notes = [
            ("Offer", ["# The rule", "Duty: Donoghue v Stevenson [1932] AC 562.[^1]",
                       "Human Rights Act 1998.", "See [[Tort/X|the duty]]. // HIDEME", "",
                       "[^1]: Neighbour principle."]),
            ("Acceptance", ["R v Brown.[^1]", "", "[^1]: Postal rule.", "// Fake v Case [1999] AC 1"]),
        ]
        path = lawnotes.export_revision_pack("Tort/Duty", notes, "docx", tables=True, when=self.WHEN)
        self.assertEqual(os.path.basename(path), "Tort › Duty revision pack.docx")
        z = zipfile.ZipFile(path)
        doc, notes_xml = z.read("word/document.xml").decode(), z.read("word/footnotes.xml").decode()
        self.assertIn("Tort › Duty", doc)
        self.assertIn("22 October 2026", doc)
        self.assertIn("Contents", doc)
        self.assertIn("Table of Cases", doc)
        self.assertIn("Table of Legislation", doc)
        self.assertLess(doc.index("Table of Cases"), doc.index('w:type="page"'))
        self.assertLess(doc.index('w:type="page"'), doc.index(">Offer<"))
        self.assertLess(doc.index(">Offer<"), doc.index(">The rule<"))
        self.assertLess(doc.index(">The rule<"), doc.index(">Acceptance<"))
        self.assertIn('w:val="Heading1"', doc[doc.index(">Offer<") - 400:doc.index(">Offer<")])
        self.assertIn('w:val="Heading2"', doc[doc.index(">The rule<") - 400:doc.index(">The rule<")])
        self.assertIn("Neighbour principle", notes_xml)
        self.assertIn("Postal rule", notes_xml)
        self.assertIn("the duty", doc)
        self.assertNotIn("HIDEME", doc)
        self.assertNotIn("Fake v Case", doc)
        self.assertNotIn("page-break-before", doc)   # the HTML break is not written into Word
        self.assertIn("Times New Roman", z.read("word/styles.xml").decode())

    @unittest.skipUnless(lawnotes.find_tool("pandoc"), "export needs pandoc (brew install pandoc)")
    def test_colon_folder_exports_as_a_word_document(self):
        import zipfile
        path = lawnotes.export_revision_pack("Tort: Duty", [("Offer", ["An offer."])], "docx", when=self.WHEN)
        self.assertEqual(os.path.basename(path), "Tort: Duty revision pack.docx")
        self.assertIn("Tort: Duty", zipfile.ZipFile(path).read("word/document.xml").decode())

    def test_menu_at_forty_columns_exports_the_folder(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "RevPack")
        os.makedirs(os.path.join(folder, "Deep"), exist_ok=True)
        offer = os.path.join(folder, "Offer.md")
        older = os.path.join(folder, "Acceptance.md")
        mid = os.path.join(folder, "Deep", "Caparo.md")
        open(offer, "w").write("# The rule\n\nDuty.[^1]\n\n[^1]: Neighbour principle.\n")
        open(older, "w").write("Posted.[^1] // HIDEME\n\n[^1]: Postal rule.\n")
        open(mid, "w").write("Three-stage test.\n")
        os.utime(offer, (100, 100))          # oldest: saving it on export must not move it first
        os.utime(mid, (200, 200))
        os.utime(older, (300, 300))
        side = os.path.join(lawnotes.NOTES_DIR, "RevPack2", "No.md")
        os.makedirs(os.path.dirname(side), exist_ok=True)
        open(side, "w").write("NOTINPACK2\n")
        open(os.path.join(lawnotes.NOTES_DIR, "NotInPack.md"), "w").write("NOTINPACK\n")
        loose = os.path.join(lawnotes.NOTES_DIR, "LooseMenu.md")
        open(loose, "w").write("Just a note.\n")
        narrow = Session(loose, size=(40, 40))
        try:
            screen = narrow.keys(b"\x1be", 0.7)       # Option+E, and this note is not in a folder
            self.assertNotIn("Revision pack", screen)
            narrow.keys(b"\x1b", 0.5)
            self.assertTrue(narrow.quit())
        finally:
            try:
                os.kill(narrow.pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(narrow.pid, 0)
            except OSError:
                pass
        s = Session(offer, env={"LAWNOTES_EXPORT_DIR": self.out}, size=(40, 40))
        try:
            screen = s.keys(b"UNSAVEDWORD\x1be", 0.8)
            self.assertIn("Revision pack: every note in RevPack", screen)
            if not lawnotes.find_tool("pandoc"):
                s.keys(b"\x1b", 0.5)
                self.assertTrue(s.quit())
                return
            screen = s.keys(b"Revision pack\r", 0.6)
            self.assertIn("Word document", screen)
            self.assertIn("with tables", screen)
            s.keys(b"Word document\r", 0.4)
            out = os.path.join(self.out, "RevPack revision pack.docx")
            for _ in range(120):
                if os.path.exists(out):
                    break
                s.out += s.read(0.1)
            self.assertTrue(os.path.exists(out), s.out[-1500:].decode(errors="replace"))
            self.assertTrue(s.quit())
        finally:
            try:
                os.kill(s.pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(s.pid, 0)
            except OSError:
                pass
        if not lawnotes.find_tool("pandoc"):
            return
        import zipfile
        doc = zipfile.ZipFile(out).read("word/document.xml").decode()
        notes_xml = zipfile.ZipFile(out).read("word/footnotes.xml").decode()
        text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", doc))
        self.assertLess(text.index("Acceptance"), text.index("Deep/Caparo"))
        self.assertLess(text.index("Deep/Caparo"), text.index("Offer"))
        self.assertLess(text.index("Offer"), text.index("UNSAVEDWORD"))
        self.assertIn("Neighbour principle", notes_xml)
        self.assertIn("Postal rule", notes_xml)
        self.assertNotIn("HIDEME", doc + notes_xml)
        self.assertNotIn("NOTINPACK", doc)
        self.assertIn("UNSAVEDWORD", open(offer).read())          # the open note was saved
        self.assertIn("HIDEME", open(older).read())               # the note itself is left alone


class ReplaceEdgeTests(unittest.TestCase):
    """Replace-all permission and conflict handling in an isolated notes folder."""

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
        app = object.__new__(lawnotes.App)
        app.pane = lawnotes.Pane()
        app.layout = lawnotes.Layout.leaf(app.pane)
        for a in lawnotes.App.VIEW_ATTRS:
            setattr(app, a, getattr(app.pane, a))
        app.buf = lawnotes.Buffer(path)
        app.history = lawnotes.History()
        app.history.snapshot = lambda wait=False: True  # replace edges do not depend on git timing
        app.search, app.msg, app.msg_until = "", "", 0.0
        app.save_failed, app.last_save_try, app.recovery_path, app.theme = False, 0.0, None, "NERV"
        app.draw = lambda refresh=True: None
        app.picks = []
        app.pick = lambda title, build, *a, **k: app.picks.append((title, build(""))) or None
        return app

    def test_read_only_open_and_closed_notes_are_skipped(self):
        a = self.note("A.md", "Donohue\n")
        closed = self.note("Tort/Duty.md", "Donohue\n")
        writable = self.note("B.md", "Donohue\n")
        before = {}
        for p in (a, closed):
            with open(p, "rb") as f:
                before[p] = f.read()
        app = self.app(a)
        for p in before:
            os.chmod(p, 0o444)
        try:
            self.assertTrue(all(not os.access(p, os.W_OK) for p in before))
            app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
            self.assertEqual(app.buf.lines, ["Donohue"])
            self.assertEqual(app.msg, "Replaced 1 in 1 note · 2 to check")
            self.assertEqual({p for _, p in app.picks[0][1]}, {a, closed})
            self.assertTrue(all("read-only" in label for label, _ in app.picks[0][1]))
            for p, data in before.items():
                with open(p, "rb") as f:
                    self.assertEqual(f.read(), data)
            with open(writable) as f:
                self.assertEqual(f.read(), "Donoghue\n")
        finally:
            for p in before:
                os.chmod(p, 0o644)

    def test_open_note_conflict_copy_is_listed_not_counted(self):
        a = self.note("A.md", "Donohue\n")
        c = self.note("Tort/Duty.md", "Donohue\n")
        app = self.app(a)
        right = lawnotes.Pane()
        right.buf = lawnotes.Buffer(c)
        app.layout = lawnotes.Layout("v", lawnotes.Layout.leaf(app.pane), lawnotes.Layout.leaf(right))
        notes = app.notes_containing("Donohue")
        save = lawnotes.Buffer.save

        def change_while_saving(buf):
            if buf is right.buf:
                with open(c, "w") as f:
                    f.write("Changed on another Mac\n")
                os.utime(c, ns=(buf.disk_mtime + 1000000000,) * 2)
            return save(buf)

        with mock.patch.object(lawnotes.Buffer, "save", change_while_saving):
            app.replace_everywhere("Donohue", "Donoghue", notes)
        with open(c) as f:
            self.assertEqual(f.read(), "Changed on another Mac\n")
        self.assertEqual(app.msg, "Replaced 1 in 1 note · 1 to check")
        label, conflict = app.picks[0][1][0]
        self.assertTrue(label.startswith("Saved as a conflict copy: Tort/Duty"), label)
        self.assertEqual(conflict, right.buf.path)
        self.assertNotEqual(c, conflict)
        with open(conflict) as f:
            self.assertEqual(f.read(), "Donoghue\n")

    def test_finder_locked_note_is_listed_if_flags_are_available(self):
        if not hasattr(os, "chflags") or not hasattr(stat, "UF_IMMUTABLE"):
            self.skipTest("Finder lock flags are unavailable")
        a = self.note("A.md", "Donohue\n")
        locked = self.note("Tort/Duty.md", "Donohue\n")
        try:
            os.chflags(locked, stat.UF_IMMUTABLE)
        except OSError as e:
            self.skipTest(f"Finder lock flag cannot be set here: {e.strerror or e}")
        try:
            self.assertTrue(os.stat(locked).st_flags & stat.UF_IMMUTABLE)
            app = self.app(a)
            app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
            with open(locked, "rb") as f:
                self.assertEqual(f.read(), b"Donohue\n")
            self.assertEqual(app.msg, "Replaced 1 in 1 note · 1 to check")
            self.assertEqual(app.picks[0][1][0][1], locked)
        finally:
            os.chflags(locked, 0)

    def test_replace_keys_skip_read_only_note_at_40_columns(self):
        a = self.note("ReplaceEdge/A.md", "DonohoeRO2 v Stevenson\n")
        b = self.note("ReplaceEdge/B.md", "DonohoeRO2 [1932] AC 562\n")
        with open(a, "rb") as f:
            before = f.read()
        os.chmod(a, 0o444)
        try:
            s = Session(a, env={"LAWNOTES_DIR": lawnotes.NOTES_DIR,
                                "LAWNOTES_HISTORY": lawnotes.HISTORY_DIR}, size=(40, 40))
            s.keys(b"\x1br", 0.5)
            s.keys(b"DonohoeRO2\rDonoghueRO2\r", 0.8)
            screen = s.keys(b"\x1b[B\r", 1.5)
            self.assertIn("Replaced 1 in 1 note", screen)
            self.assertIn("ReplaceEdge/A.md — read-only", screen)
            s.keys(b"\x1b", 0.5)
            self.assertTrue(s.quit())
            with open(a, "rb") as f:
                self.assertEqual(f.read(), before)
            with open(b) as f:
                self.assertEqual(f.read(), "DonoghueRO2 [1932] AC 562\n")
        finally:
            os.chmod(a, 0o644)

    def test_closed_outside_link_is_listed_but_inside_link_is_replaced(self):
        a = self.note("A.md", "No match\n")
        outside = os.path.join(self.dir, "notes-other", "Outside.md")
        os.makedirs(os.path.dirname(outside))
        with open(outside, "wb") as f:
            f.write(b"Donohue\n")
        inside = self.note(".targets/Inside.md", "Donohue\n")
        outside_link = os.path.join(lawnotes.NOTES_DIR, "Tort", "Outside.md")
        inside_link = os.path.join(lawnotes.NOTES_DIR, "Tort", "Inside.md")
        os.makedirs(os.path.dirname(outside_link))
        os.symlink(outside, outside_link)
        os.symlink(inside, inside_link)
        app = self.app(a)
        app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
        with open(outside, "rb") as f:
            self.assertEqual(f.read(), b"Donohue\n")
        with open(inside, "rb") as f:
            self.assertEqual(f.read(), b"Donoghue\n")
        self.assertTrue(os.path.islink(outside_link))
        self.assertTrue(os.path.islink(inside_link))
        self.assertEqual(app.msg, "Replaced 1 in 1 note · 1 to check")
        self.assertEqual(app.picks[0][1], [
            ("Tort/Outside.md — linked from outside the notes folder: open it to replace there", outside_link)])

    def test_symlinked_notes_folder_still_replaces_closed_note(self):
        real_notes = os.path.join(self.dir, "real-notes")
        os.makedirs(real_notes)
        os.symlink(real_notes, lawnotes.NOTES_DIR)
        a = self.note("A.md", "No match\n")
        duty = self.note("Tort/Duty.md", "Donohue\n")
        app = self.app(a)
        app.replace_everywhere("Donohue", "Donoghue", app.notes_containing("Donohue"))
        with open(duty) as f:
            self.assertEqual(f.read(), "Donoghue\n")
        self.assertIn("Replaced 1 in 1 note", app.msg)
        self.assertFalse(app.picks)

    def test_replace_keys_list_outside_link_at_40_columns(self):
        real_notes = os.path.join(self.dir, "real-notes")
        os.makedirs(real_notes)
        os.symlink(real_notes, lawnotes.NOTES_DIR)
        a = self.note("A.md", "OutsideSpellAL\n")
        outside = os.path.join(self.dir, "notes-other", "Outside.md")
        os.makedirs(os.path.dirname(outside))
        with open(outside, "wb") as f:
            f.write(b"OutsideSpellAL\n")
        link = os.path.join(lawnotes.NOTES_DIR, "Tort", "Outside.md")
        os.makedirs(os.path.dirname(link))
        os.symlink(outside, link)
        s = Session(a, env={"LAWNOTES_DIR": lawnotes.NOTES_DIR,
                            "LAWNOTES_HISTORY": lawnotes.HISTORY_DIR}, size=(40, 40))
        s.keys(b"\x1br", 0.5)
        s.keys(b"OutsideSpellAL\rOutsideReplAL\r", 0.8)
        screen = s.keys(b"\x1b[B\r", 1.5)
        s.keys(b"\x1b", 0.5)
        self.assertTrue(s.quit())
        self.assertIn("Tort/Outside.md — linked from outside", screen)
        with open(outside, "rb") as f:
            self.assertEqual(f.read(), b"OutsideSpellAL\n")
        with open(a) as f:
            self.assertEqual(f.read(), "OutsideReplAL\n")

    def test_mis_cased_inside_links_replace_once(self):
        lawnotes.NOTES_DIR = os.path.join(self.dir, "MyNotes")
        a = self.note("A.md", "No match\n")
        inside = self.note(".targets/Inside.md", "Don\n")
        exact = os.path.join(lawnotes.NOTES_DIR, "Tort", "AExact.md")
        alias = os.path.join(lawnotes.NOTES_DIR, "Tort", "ZCase.md")
        os.makedirs(os.path.dirname(exact))
        os.symlink(inside, exact)
        os.symlink(inside.replace("MyNotes", "mynotes"), alias)
        try:
            available = os.path.samefile(alias, inside)
        except OSError:
            available = False
        if not available:
            self.skipTest("filesystem does not resolve differently cased paths")
        app = self.app(a)
        notes = app.notes_containing("Don")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], alias)
        app.replace_everywhere("Don", "DonDon", notes)
        with open(inside) as f:
            self.assertEqual(f.read(), "DonDon\n")
        self.assertIn("Replaced 1 in 1 note", app.msg)
        self.assertFalse(app.picks)

    def test_unicode_spelling_inside_links_replace_once(self):
        lawnotes.NOTES_DIR = os.path.join(self.dir, "Café")
        a = self.note("A.md", "No match\n")
        inside = self.note(".targets/Inside.md", "Don\n")
        exact = os.path.join(lawnotes.NOTES_DIR, "Tort", "AExact.md")
        alias = os.path.join(lawnotes.NOTES_DIR, "Tort", "ZUnicode.md")
        os.makedirs(os.path.dirname(exact))
        os.symlink(inside, exact)
        os.symlink(unicodedata.normalize("NFD", inside), alias)
        try:
            available = os.path.samefile(alias, inside)
        except OSError:
            available = False
        if not available:
            self.skipTest("filesystem does not resolve NFC and NFD paths as one file")
        app = self.app(a)
        notes = app.notes_containing("Don")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], alias)
        app.replace_everywhere("Don", "DonDon", notes)
        with open(inside) as f:
            self.assertEqual(f.read(), "DonDon\n")
        self.assertFalse(app.picks)

    def test_hard_links_are_one_note_for_replace(self):
        a = self.note("A.md", "No match\n")
        first = self.note("Tort/A.md", "Don\n")
        os.link(first, os.path.join(lawnotes.NOTES_DIR, "Tort", "B.md"))
        self.assertEqual(len(self.app(a).notes_containing("Don")), 1)

    def test_replace_keys_accept_mis_cased_inside_link_at_40_columns(self):
        lawnotes.NOTES_DIR = os.path.join(self.dir, "MyNotes")
        a = self.note("A.md", "No match\n")
        inside = self.note(".targets/Inside.md", "CaseSpellALI185\n")
        link = os.path.join(lawnotes.NOTES_DIR, "Tort", "Case.md")
        os.makedirs(os.path.dirname(link))
        os.symlink(inside.replace("MyNotes", "mynotes"), link)
        try:
            available = os.path.samefile(link, inside)
        except OSError:
            available = False
        if not available:
            self.skipTest("filesystem does not resolve differently cased paths")
        s = Session(a, env={"LAWNOTES_DIR": lawnotes.NOTES_DIR,
                            "LAWNOTES_HISTORY": lawnotes.HISTORY_DIR}, size=(40, 40))
        s.keys(b"\x1br", 0.5)
        s.keys(b"CaseSpellALI185\rCaseReplaceALI185\r", 0.8)
        screen = s.keys(b"\r", 1.5)
        self.assertTrue(s.quit())
        self.assertIn("Replaced 1 in 1 note", screen)
        with open(inside) as f:
            self.assertEqual(f.read(), "CaseReplaceALI185\n")


@unittest.skipUnless(lawnotes.find_tool("pandoc"), "export needs pandoc (brew install pandoc)")
class ExportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(dir=TMP)
        self.previous_export = lawnotes.EXPORT_DIR
        lawnotes.EXPORT_DIR = self.out

    def tearDown(self):
        lawnotes.EXPORT_DIR = self.previous_export

    def test_export_replaces_only_its_unchanged_file(self):
        note = os.path.join(TMP, "Owned export.md")
        first = lawnotes.export_note(note, ["First"], "docx", tables=True)
        with open(lawnotes.export_records_file()) as f:
            saved = json.load(f)[first]
        self.assertEqual(saved, {"size": os.stat(first).st_size, "mtime_ns": os.stat(first).st_mtime_ns})
        second = lawnotes.export_note(note, ["Second"], "docx", tables=True)
        self.assertEqual(first, second)
        self.assertEqual(os.listdir(self.out), ["Owned export.docx"])
        with open(first, "wb") as f:
            f.write(b"Word edits and comments")
        third, reason = lawnotes.export_note(note, ["Third"], "docx", tables=True, details=True)
        self.assertEqual(os.path.basename(third), "Owned export (2).docx")
        self.assertEqual(reason, "Owned export.docx changed")
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"Word edits and comments")
        self.assertEqual(lawnotes.export_note(note, ["Fourth"], "docx", tables=True), third)
        self.assertEqual(len(os.listdir(self.out)), 2)
        with open(third, "wb") as f:
            f.write(b"More Word edits")
        fourth = lawnotes.export_note(note, ["Fifth"], "docx")
        self.assertEqual(os.path.basename(fourth), "Owned export (3).docx")

    def test_damaged_export_record_keeps_existing_file(self):
        note = os.path.join(TMP, "Damaged record.md")
        first = lawnotes.export_note(note, ["First"], "docx")
        with open(lawnotes.export_records_file(), "w") as f:
            f.write("{broken")
        second, reason = lawnotes.export_note(note, ["Second"], "docx", details=True)
        self.assertEqual(os.path.basename(second), "Damaged record (2).docx")
        self.assertEqual(reason, "no export record")
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertNotEqual(a.read(), b.read())

    def test_missing_export_record_keeps_existing_file(self):
        note = os.path.join(TMP, "No record.md")
        first = lawnotes.export_note(note, ["First"], "docx")
        os.remove(lawnotes.export_records_file())
        second, reason = lawnotes.export_note(note, ["Second"], "docx", details=True)
        self.assertEqual(os.path.basename(second), "No record (2).docx")
        self.assertEqual(reason, "no export record")
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertNotEqual(a.read(), b.read())

    def test_same_size_with_new_mtime_is_kept(self):
        note = os.path.join(TMP, "Same size.md")
        first = lawnotes.export_note(note, ["First"], "docx")
        old = os.stat(first)
        with open(first, "r+b") as f:
            f.write(b"X")
        os.utime(first, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
        second = lawnotes.export_note(note, ["Second"], "docx")
        self.assertEqual(os.path.basename(second), "Same size (2).docx")
        with open(first, "rb") as f:
            self.assertEqual(f.read(1), b"X")

    def test_name_claim_race_uses_next_free_name(self):
        first = os.path.join(self.out, "Raced.docx")
        original_link = os.link

        def claim_first(src, dst):
            if dst == first:
                with open(dst, "wb") as f:
                    f.write(b"Other export")
                raise FileExistsError(dst)
            return original_link(src, dst)

        def render(path):
            with open(path, "wb") as f:
                f.write(b"Our export")

        with mock.patch.object(lawnotes.os, "link", side_effect=claim_first):
            out, reason = lawnotes.write_export("Raced", "docx", render)
        self.assertEqual(os.path.basename(out), "Raced (2).docx")
        self.assertEqual(reason, "name already used")
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"Other export")

    def test_export_cleanup_ignores_extra_hidden_file(self):
        def render(path):
            with open(path, "wb") as f:
                f.write(b"Exported")
            with open(os.path.join(os.path.dirname(path), ".DS_Store"), "wb") as f:
                f.write(b"Finder")

        out, _reason = lawnotes.write_export("Cleanup", "docx", render)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"Exported")
        self.assertFalse(any(name.startswith(".lawnotes-") for name in os.listdir(self.out)))

    def test_late_save_keeps_the_original(self):
        def render(path):
            with open(path, "wb") as f:
                f.write(b"ours")

        real_lstat = os.lstat
        for kind in ("size", "mtime", "inode"):
            with self.subTest(kind=kind):
                name = "Late " + kind
                first, _reason = lawnotes.write_export(name, "docx", render)
                before = real_lstat(first)
                changed = False

                def late_stat(path, *args, **kwargs):
                    nonlocal changed
                    old = real_lstat(path, *args, **kwargs)
                    if path == first and not args and not kwargs and not changed:
                        changed = True
                        if kind == "inode":
                            replacement = os.path.join(self.out, "replacement")
                            with open(replacement, "wb") as f:
                                f.write(b"edit")
                            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                            os.replace(replacement, first)
                        else:
                            with open(first, "wb") as f:
                                f.write(b"edited" if kind == "size" else b"edit")
                            if kind == "mtime":
                                os.utime(first, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
                    return old

                with mock.patch.object(lawnotes.os, "lstat", side_effect=late_stat):
                    second, reason = lawnotes.write_export(name, "docx", render)
                self.assertTrue(changed)
                self.assertEqual(os.path.basename(second), name + " (2).docx")
                self.assertEqual(reason, name + ".docx changed")
                with open(first, "rb") as f:
                    self.assertEqual(f.read(), b"edited" if kind == "size" else b"edit")

    def test_missing_export_files_are_pruned_from_records(self):
        def render(path):
            with open(path, "wb") as f:
                f.write(b"Exported")

        cache = os.path.join(tempfile.mkdtemp(dir=TMP), "cache")
        with mock.patch.object(lawnotes, "CACHE_DIR", cache):
            old, _reason = lawnotes.write_export("Old", "docx", render)
            os.remove(old)
            new, _reason = lawnotes.write_export("New", "docx", render)
            with open(lawnotes.export_records_file()) as f:
                records = json.load(f)
        self.assertNotIn(old, records)
        self.assertIn(new, records)

    def test_pdf_name_with_converter_stub(self):
        def run(args, **_kwargs):
            if args[0] == "pdf-helper":
                with open(args[-1], "wb") as f:
                    f.write(b"%PDF from helper")
            else:
                with open(args[-1], "w") as f:
                    f.write("<html><body>Note</body></html>")
            return subprocess.CompletedProcess(args, 0, stderr="")

        note = os.path.join(TMP, "PDF copy.md")
        with mock.patch.object(lawnotes, "compiled_helper", return_value="pdf-helper"), \
                mock.patch.object(lawnotes.subprocess, "run", side_effect=run):
            first = lawnotes.export_note(note, ["First"], "pdf")
            with open(first, "wb") as f:
                f.write(b"Edited PDF")
            second = lawnotes.export_note(note, ["Second"], "pdf")
        self.assertEqual(os.path.basename(second), "PDF copy (2).pdf")
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"Edited PDF")

    def test_cli_preserves_edited_export(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Cli export.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        with open(note, "w") as f:
            f.write("First\n")
        env = dict(os.environ, LAWNOTES_EXPORT_DIR=self.out)
        command = [sys.executable, os.path.join(ROOT, "lawnotes.py"), "--export", "Cli export", "docx"]
        first = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        path = first.stdout.strip()
        with open(path, "wb") as f:
            f.write(b"Edited in Word")
        second = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(os.path.basename(second.stdout.strip()), "Cli export (2).docx")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"Edited in Word")

    def test_menu_names_the_copy_at_forty_columns(self):
        note = os.path.join(lawnotes.NOTES_DIR, "Safe.md")
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        with open(note, "w") as f:
            f.write("First\n")
        first = lawnotes.export_note(note, ["First"], "docx")
        with open(first, "wb") as f:
            f.write(b"Edited in Word")
        s = Session(note, env={"LAWNOTES_EXPORT_DIR": self.out}, size=(40, 40))
        try:
            self.assertIn("Word document", s.keys(b"\x1be", 0.5))
            screen = s.keys(b"Word document\r", 1.5)
            for _ in range(30):
                if "(2).docx saved: Safe.docx changed" in screen:
                    break
                screen += s.keys(b"", 0.1)
            self.assertIn("(2).docx saved: Safe.docx changed", screen)
            self.assertTrue(os.path.exists(os.path.join(self.out, "Safe (2).docx")))
            self.assertTrue(s.quit())
        finally:
            try:
                os.kill(s.pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(s.pid, 0)
            except OSError:
                pass
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"Edited in Word")

    def test_pack_preserves_edited_export(self):
        notes = [("Offer", ["First rule."])]
        first = lawnotes.export_revision_pack("Safe", notes, "docx")
        self.assertEqual(lawnotes.export_revision_pack("Safe", notes, "docx"), first)
        with open(first, "wb") as f:
            f.write(b"Edited pack")
        second = lawnotes.export_revision_pack("Safe", notes, "docx")
        self.assertEqual(os.path.basename(second), "Safe revision pack (2).docx")
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"Edited pack")


class EssayModeTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(dir=TMP), "Essay.md")

    def test_limit_line_is_read_tolerantly_and_left_out(self):
        accept = ("// Word limit 2,000", "// word limit 2000", "// WORD LIMIT: 2,000",
                  "//word limit:2,000", "   // Word limit 2,000", "// Word limit 2,000 extra")
        for line in accept:
            self.assertEqual(lawnotes.word_limit_at([line])[1], 2000, line)
            self.assertEqual(lawnotes.body_words([line, "Duty of care"]), 3, line)
            self.assertEqual(lawnotes.export_markdown([line, "Duty of care"]), "Duty of care\n", line)
        self.assertIsNone(lawnotes.word_limit_at(["Duty // Word limit 2,000"])[1])  # not its own line
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit"])[1])
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit 0"])[1])
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit ,"])[1])
        self.assertEqual(lawnotes.word_limit_at(["# Title", "// word limit: 1,500"])[1], 1500)
        self.assertEqual(lawnotes.word_limit_at(["// Word limit 10", "// Word limit 20"]), (0, 10))
        self.assertEqual(lawnotes.word_limit_line(2000), "// Word limit 2,000")
        self.assertEqual(lawnotes.word_limit_line(1000000), "// Word limit 1,000,000")
        # The prompt's range, and nothing past it. A huge digit string is not a limit, and is
        # not handed to int() (Python 3.11 rejects anything over about 4,300 digits).
        self.assertEqual(lawnotes.word_limit_at(["// Word limit 1"])[1], 1)
        self.assertEqual(lawnotes.word_limit_at([lawnotes.word_limit_line(1000000)])[1], 1000000)
        self.assertEqual(lawnotes.word_limit_at(["// Word limit 1,000,000"])[1], 1000000)
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit 1000001"])[1])
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit 1,000,001"])[1])
        self.assertIsNone(lawnotes.word_limit_at(["// Word limit 10000000"])[1])
        huge = "// Word limit " + "9" * 5000
        self.assertEqual(lawnotes.word_limit_at([huge, "// Word limit 12"]), (1, 12))
        capped = lawnotes.Buffer(self.path)
        capped.lines = [huge]
        capped.touch()
        self.assertIsNone(capped.word_limit())
        md = lawnotes.export_markdown(["Duty // Word limit 9", "// Word limit 2,000"])
        self.assertIn("Duty", md)
        self.assertNotIn("2,000", md)
        pack = lawnotes.revision_pack_markdown(
            [("Essay", ["// Word limit 2,000", "Duty owed by D."])], "Tort", datetime.date(2026, 10, 26))
        self.assertNotIn("Word limit", pack)
        self.assertIn("Duty owed", pack)

    def test_essay_status_count_footnotes_and_narrow_bar(self):
        # exam mode's wording is unchanged: x/y only while the clock is running
        self.assertEqual(lawnotes.exam_words_text(1214, 2000, True), "1,214/2,000 words")
        self.assertEqual(lawnotes.exam_words_text(1214, 2000, False), "1,214 words")
        self.assertEqual(lawnotes.exam_words_text(1, None, False), "1 word")
        self.assertEqual(lawnotes.exam_words_text(1214, 2000, False, essay=True), "1,214/2,000 words")
        text, count, over = lawnotes.essay_status(1214, 2000, 12)
        self.assertEqual((text, count, over), ("1,214/2,000 words · 12 fn", "1,214/2,000 words", False))
        self.assertEqual(lawnotes.essay_status(1214, 2000, 0)[0], "1,214/2,000 words")
        self.assertEqual(lawnotes.essay_status(1, 2000, 1), ("1/2,000 words · 1 fn", "1/2,000 words", False))
        self.assertFalse(lawnotes.essay_status(2000, 2000, 0)[2])
        self.assertTrue(lawnotes.essay_status(2001, 2000, 3)[2])
        essential = "1,214/2,000 words · 12 fn"
        for message in ("", "Word limit 2,000"):
            line = lawnotes.exam_bar_text("EssayLimit/Negligence Essay.md", message, essential, 39)
            self.assertEqual(len(line), 39, line)
            self.assertTrue(line.endswith(essential), line)
            self.assertNotIn("Essay.md", line)

    def test_essay_bar_keeps_the_save_state_at_forty_columns(self):
        # A full-width pane leaves the last cell alone, so a 40-column screen draws 39.
        name = "EssayLimit/Negligence Essay.md"
        status, count, _ = lawnotes.essay_status(1214, 2000, 12)
        for dirty, state in ((True, "● unsaved"), (False, "✓ saved")):
            for width in (39, 40):
                line = lawnotes.essay_bar_text(name, "", status, dirty, width)
                self.assertEqual(len(line), width, line)
                self.assertIn(state, line)
                self.assertTrue(line.endswith(status), line)
                self.assertLess(line.index(state), line.index(count))
                self.assertNotIn(name, line)
        wide = lawnotes.essay_bar_text(name, "", status, False, 80)
        self.assertIn(name, wide)
        self.assertLess(wide.index(name), wide.index("✓ saved"))
        self.assertLess(wide.index("✓ saved"), wide.index(count))
        self.assertTrue(wide.endswith(status))

    def test_essay_message_stays_whole_at_forty_columns(self):
        message = "Exam stopped"  # 12 characters, the note the Session test looks for
        self.assertEqual(len(message), 12)
        status, count, _ = lawnotes.essay_status(1214, 2000, 0)
        self.assertEqual(status, "1,214/2,000 words")
        name = "EssayLimit/Negligence Essay.md"
        for dirty, state in ((True, "● unsaved"), (False, "✓ saved")):
            line = lawnotes.essay_bar_text(name, message, status, dirty, 40)
            self.assertEqual(len(line), 40, line)
            self.assertIn(message, line)
            self.assertTrue(line.endswith(" " + status), line)  # a space stays before the count
            self.assertNotIn(state, line)  # dropped before the message is cut
            self.assertLess(line.index(message), line.index(count))
        # Plenty of room: the save state stays, and so does the message.
        wide = lawnotes.essay_bar_text(name, message, status, False, 80)
        self.assertIn(message, wide)
        self.assertIn(name, wide)
        self.assertIn("✓ saved", wide)
        self.assertTrue(wide.endswith(status))
        # The 40-column pane draws 39 columns. This is the line that cut "Exam stopped".
        over = lawnotes.essay_status(5, 2, 2)[0]
        line = lawnotes.essay_bar_text(name, message, over, False, 39)
        self.assertEqual(len(line), 39, line)
        self.assertIn(message, line)
        self.assertTrue(line.endswith(" " + over), line)
        self.assertNotIn("✓ saved", line)

    def test_narrow_message_keeps_a_space_before_the_count(self):
        # 30 columns, the long count: five columns left. "Exam stopped" used to be cut to
        # " Exa" and printed against the count ("Exa1,214…").
        message = "Exam stopped"
        status, count, _ = lawnotes.essay_status(1214, 2000, 12)
        self.assertEqual(len(status), 25)
        name = "Negligence"
        line = lawnotes.essay_bar_text(name, message, status, True, 30)
        self.assertEqual(len(line), 30, line)
        self.assertTrue(line.endswith(" " + status), line)
        self.assertNotIn("Exam", line)  # six characters will not fit, so the message is left off
        self.assertNotIn("●", line)
        # Eight columns of room: the longest prefix that fits, and nothing shorter than six.
        wider = lawnotes.essay_bar_text(name, message, status, False, 33)
        self.assertEqual(len(wider), 33, wider)
        self.assertIn("Exam s", wider)
        self.assertNotIn("Exam st", wider)
        self.assertTrue(wider.endswith(" " + status), wider)
        self.assertNotIn("✓", wider)
        # A whole message shorter than six is kept when it fits, and left off when it does not.
        short = lawnotes.essay_bar_text(name, "Done", status, False, 31)
        self.assertIn("Done", short)
        self.assertTrue(short.endswith(" " + status), short)
        squeezed = lawnotes.essay_bar_text(name, "Done", status, True, 30)
        self.assertNotIn("Done", squeezed)
        self.assertNotIn("Don", squeezed)
        self.assertTrue(squeezed.endswith(" " + status), squeezed)

    def test_count_is_cached_until_the_text_changes(self):
        b = lawnotes.Buffer(self.path)
        b.lines = ["// Word limit 2,000", "Hello there"]
        self.assertEqual(b.word_limit(), 2000)
        self.assertEqual(b.body_word_count(), 2)
        self.assertEqual(b.footnote_count(), 0)
        b.lines[0] = "// word limit: 1,500"  # same lines, no edit yet: the cache stands
        self.assertEqual(b.word_limit(), 2000)
        b.touch()
        self.assertEqual(b.word_limit(), 1500)
        self.assertEqual(b.footnote_count(), 0)  # warm the cache, then edit in place
        self.assertEqual(b.body_word_count(), 2)
        b.lines[1] = "[^1]: hidden words that do not count"
        self.assertEqual(b.footnote_count(), 0)
        self.assertEqual(b.body_word_count(), 2)
        b.touch()
        self.assertEqual(b.footnote_count(), 1)
        self.assertEqual(b.body_word_count(), 0)  # the footnote line is not part of the essay
        self.assertGreater(b.word_count(), 0)
        del b.lines[0]
        b.touch()
        self.assertIsNone(b.word_limit())

    def review_app(self, lines):
        """An App without a screen, for a replace that redraws (and so fills the caches) between matches."""
        app = object.__new__(lawnotes.App)
        app.pane = lawnotes.Pane()
        app.layout = lawnotes.Layout.leaf(app.pane)
        for attr in lawnotes.App.VIEW_ATTRS:
            setattr(app, attr, getattr(app.pane, attr))
        app.buf = lawnotes.Buffer(self.path)
        app.buf.lines = list(lines)
        app.search = ""
        app.say = lambda *args, **kwargs: None
        return app

    def answering(self, app, answers):
        """ask() as the review uses it: the note is drawn, which reads the limit and the footnotes."""
        pending = iter(answers)

        def ask(question, keys):
            app.buf.word_limit()
            app.buf.footnote_count()
            return next(pending)

        app.ask = ask

    def test_replace_rereads_the_limit_and_the_footnotes(self):
        # y, then a. The first match is not the limit line. The redraw after it caches 2,000;
        # replacing the limit line itself has to drop that cache, still as one undo step.
        app = self.review_app(["Pay 2,000 now", "// Word limit 2,000", "[^1]: a note"])
        self.answering(app, ["y", "a"])
        b = app.buf
        app.replace_review("2,000", "1,500", "“1,500”")
        self.assertEqual(b.lines, ["Pay 1,500 now", "// Word limit 1,500", "[^1]: a note"])
        self.assertEqual(b.word_limit(), 1500)
        self.assertEqual(b.footnote_count(), 1)
        self.assertEqual(len(b.undo_stack), 1)
        self.assertTrue(b.undo())
        self.assertEqual(b.lines[1], "// Word limit 2,000")
        self.assertEqual(b.word_limit(), 2000)

        # The same gap, for the footnote count: each y is its own edit, and the redraw between
        # them caches the count from the line that is still a footnote.
        app = self.review_app(["[^1]: one", "[^1]: two"])
        self.answering(app, ["y", "y"])
        b = app.buf
        app.replace_review("[^1]:", "note", "“note”")
        self.assertEqual(b.lines, ["note one", "note two"])
        self.assertEqual(b.footnote_count(), 0)
        self.assertEqual(len(b.undo_stack), 1)

        # Every match at once. The checkpoint is before the text changes; the cache still follows.
        app = self.review_app(["Pay 1,500 now", "// Word limit 1,500"])
        b = app.buf
        b.word_limit()
        self.assertEqual(app.replace_in_view("1,500", "1,250"), 2)
        self.assertEqual(b.lines[1], "// Word limit 1,250")
        self.assertEqual(b.word_limit(), 1250)
        self.assertEqual(len(b.undo_stack), 1)
        self.assertEqual(app.replace_in_view("absent", "x"), 0)  # nothing changed: no new undo step
        self.assertEqual(len(b.undo_stack), 1)

    def test_set_and_clear_write_the_line_at_the_top(self):
        b = lawnotes.Buffer(self.path)
        b.lines = ["# Duty", "", "The neighbour principle."]
        b.cy = b.cx = 0
        app = type("App", (), {"buf": b, "top": 0, "top_seg": 0, "anchor": None, "msg": ""})()
        app.say = lambda msg, secs=4: setattr(app, "msg", msg)
        lawnotes.App.set_word_limit(app, 2000)
        self.assertEqual(b.lines[0], "// Word limit 2,000")
        self.assertEqual(b.lines[1:], ["# Duty", "", "The neighbour principle."])
        self.assertEqual((b.cy, b.cx), (1, 0))  # the cursor stays with the heading
        self.assertEqual(b.word_limit(), 2000)
        self.assertEqual(app.msg, "Word limit 2,000")
        lawnotes.App.set_word_limit(app, 1500)
        self.assertEqual(b.lines[0], "// Word limit 1,500")
        self.assertEqual(b.cy, 1)  # replacing the line does not shift the note again
        self.assertEqual(len(b.undo_stack), 2)
        lawnotes.App.set_word_limit(app, 1500)  # the same number is not a new edit
        self.assertEqual(len(b.undo_stack), 2)
        lawnotes.App.clear_word_limit(app)
        self.assertEqual(b.lines[0], "# Duty")
        self.assertIsNone(b.word_limit())
        self.assertEqual(app.msg, "Word limit removed")
        self.assertTrue(b.undo())
        self.assertEqual(b.lines[0], "// Word limit 1,500")
        self.assertEqual(b.word_limit(), 1500)
        only = lawnotes.Buffer(self.path + ".2")
        only.lines = ["// Word limit 2,000"]
        app.buf = only
        lawnotes.App.clear_word_limit(app)
        self.assertEqual(only.lines, [""])

    def test_help_readme_and_questions(self):
        text = next(desc for key, desc in lawnotes.HELP if key == "Option+X")
        self.assertIn("Word limit 2,000", text)
        self.assertIn("c change", text)
        self.assertIn("F8", text)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("`· 12 fn`", readme)
        self.assertIn("warning colour", readme)
        self.assertIn("Essay mode (Option+X, leave the minutes blank)", readme)
        self.assertIn("`// Word limit 2,000`", readme)
        import inspect
        src = inspect.getsource(lawnotes.App.exam_mode)
        questions = [
            "Stop the exam?  y yes · n no",
            "Minutes (e.g. 90): ",
            "Minutes as a number, e.g. 90: ",
            "Word limit (blank for none): ",
            "A number, or blank for none: ",
            "c change · r remove · e exam",
            "New word limit: ",
            "A number, e.g. 2,000: ",
            "Minutes (blank for word limit): ",
            "Word limit for this note: ",
        ]
        for question in questions:
            self.assertIn(question, src)
            self.assertLessEqual(len(question), 38, question)

    def test_essay_mode_keys_in_narrow_pane(self):
        folder = os.path.join(lawnotes.NOTES_DIR, "EssayLimit")
        os.makedirs(folder, exist_ok=True)
        note = os.path.join(folder, "Negligence Essay.md")
        body = "# Duty\n\nThe neighbour principle.\n[^1]: not counted at all\n[^2]: also hidden\n"
        with open(note, "w") as f:
            f.write(body)
        lines = body.splitlines()
        start = lawnotes.body_words(lines)
        edited = lines[:]
        edited[2] += " here"
        grown = lawnotes.body_words(edited)
        self.assertGreater(grown, start)
        at = lambda n: lawnotes.essay_status(n, 2000, 2)[0]
        over = lawnotes.essay_status(grown, 2, 2)[0]
        s = Session(note, size=(40, 40))
        try:
            # Minutes given: the exam, exactly as before. Blank is not offered once it has started.
            self.assertIn("Minutes (blank for word limit): ", s.keys(b"\x1bx", 0.5))
            self.assertNotIn("Word limit for this note", s.keys(b"\x1b", 0.4))
            s.keys(b"\x1bx", 0.4)
            self.assertIn("number", s.keys(b"nope\r", 0.4))
            self.assertIn("Word limit (blank for none): ", s.keys(b"45\r", 0.4))
            screen = s.keys(b"\r", 0.5)
            self.assertRegex(screen, r"4[45]:[0-5]\d")
            self.assertNotIn("// Word limit", screen)
            self.assertNotIn("/2,000", screen)
            self.assertIn("Stop the exam", s.keys(b"\x1bx", 0.4))
            s.keys(b"y", 0.4)

            # Blank minutes: a limit on this note. Esc writes nothing.
            self.assertIn("Word limit for this note: ", s.keys(b"\x1bx\r", 0.5))
            self.assertNotIn("// Word limit", s.keys(b"\x1b", 0.4))
            s.keys(b"\x1bx\r", 0.4)
            self.assertIn("2,000", s.keys(b"nope\r", 0.4))
            screen = s.keys(b"2,000\r", 0.6)
            self.assertIn("// Word limit 2,000", screen)
            self.assertIn(at(start), screen)
            self.assertNotIn("Negligence Essay.md", screen)  # 40 columns: the count stays, the name goes
            self.assertNotIn(b"48;5;160", s.out)
            self.assertRegex(s.keys(b"\x1b[B\x1b[B\x05 here", 0.5), rf"\b{grown}\b")

            # Already has a limit: change, or a timed exam, or remove. Esc leaves it.
            self.assertIn("c change · r remove · e exam",
                          s.keys_until(b"\x1bx", "c change · r remove · e exam"))
            self.assertIn("New word limit: ", s.keys(b"\x1bxc", 0.5))
            s.keys(b"\x1b", 0.3)  # Esc leaves the limit
            self.assertIn("c change · r remove · e exam",
                          s.keys_until(b"\x1bx", "c change · r remove · e exam"))
            s.keys(b"c", 0.3)
            self.assertIn("number", s.keys(b"nope\r", 0.4))
            screen = s.keys(b"2\r", 0.5)
            self.assertIn(lawnotes.essay_status(grown, 2, 2)[1], screen)  # past the limit, in the warning colour
            self.assertNotIn("// Word limit 2,000", screen)
            self.assertIn(b"48;5;160", s.out)

            # A running exam replaces the essay count. Its minutes still reject a blank line.
            self.assertIn("c change · r remove · e exam",
                          s.keys_until(b"\x1bx", "c change · r remove · e exam"))
            self.assertIn("Minutes (e.g. 90): ", s.keys(b"e", 0.4))
            screen = s.keys(b"\r", 0.4)
            self.assertIn("number", screen)
            self.assertNotIn("Word limit for this note", screen)
            s.keys(b"90\r", 0.4)
            screen = s.keys(b"\r", 0.5)
            self.assertRegex(screen, r"1:(30:00|29:5\d)")
            self.assertNotIn(over, screen)
            self.assertIn("Stop the exam", s.keys(b"\x1bx", 0.4))
            screen = s.keys(b"y", 0.5)
            self.assertIn("Exam stopped", screen)
            # The warning colour is painted in a second pass, so the raw stream has a gap
            # between the count and the footnote tail.
            self.assertRegex(screen, r"5/2 words\s+·\s*2 fn")

            self.assertIn("r remove", s.keys(b"\x1bx", 0.4))
            screen = s.keys(b"r", 0.5)
            self.assertIn("Word limit removed", screen)
            self.assertNotIn(over, screen)
            screen = s.keys(b"\x1a", 0.5)  # undo puts the line back; it is part of the note
            self.assertRegex(screen, r"5/2 words\s+·\s*2 fn")
            self.assertTrue(s.quit())
        finally:
            try:
                os.kill(s.pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(s.pid, 0)
            except OSError:
                pass
        with open(note) as f:
            saved = f.read()
        self.assertTrue(saved.startswith("// Word limit 2\n"), saved)
        self.assertIn("The neighbour principle. here\n", saved)
        self.assertIn("[^1]: not counted at all\n", saved)
        self.assertIn("[^2]: also hidden\n", saved)
        self.assertEqual(saved.count("Word limit"), 1)


class Screen:
    """Just enough of a terminal to read back what curses drew: cursor moves, erase, text."""

    def __init__(self, data, rows, cols):
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.y = self.x = 0
        text = data.decode("utf-8", "replace")
        pos, last = 0, ""
        for m in re.finditer(r"\x1b\[([?>]?)([0-9;]*)([@-~])|\x1b[()][A-Za-z0-9]|\x1b[=>78]|([\r\n\b])|([^\x1b\r\n\b]+)", text):
            if m.group(5):
                for ch in m.group(5):
                    if self.x >= cols:
                        self.x = cols - 1
                    self.grid[self.y][self.x] = ch
                    self.x += 1
                last = m.group(5)[-1]
            elif m.group(4):
                if m.group(4) == "\r":
                    self.x = 0
                elif m.group(4) == "\n":
                    self.y = min(rows - 1, self.y + 1)
                else:
                    self.x = max(0, self.x - 1)
            elif m.group(3) and not m.group(1):
                self.csi(m.group(3), [int(p) if p else 0 for p in m.group(2).split(";")], last)

    def csi(self, cmd, p, last):
        n = p[0] or 1
        if cmd in "Hf":
            self.y, self.x = min(self.rows - 1, (p[0] or 1) - 1), min(self.cols - 1, ((p[1] if len(p) > 1 else 0) or 1) - 1)
        elif cmd == "A":
            self.y = max(0, self.y - n)
        elif cmd == "B":
            self.y = min(self.rows - 1, self.y + n)
        elif cmd == "C":
            self.x = min(self.cols - 1, self.x + n)
        elif cmd == "D":
            self.x = max(0, self.x - n)
        elif cmd == "G":
            self.x = min(self.cols - 1, n - 1)
        elif cmd == "d":
            self.y = min(self.rows - 1, n - 1)
        elif cmd == "K":
            a, e = {0: (self.x, self.cols), 1: (0, self.x + 1), 2: (0, self.cols)}[p[0]]
            self.grid[self.y][a:e] = [" "] * (e - a)
        elif cmd == "J":
            if p[0] in (0, 2):
                self.grid[self.y][self.x:] = [" "] * (self.cols - self.x)
                for r in range(self.y + 1, self.rows):
                    self.grid[r] = [" "] * self.cols
            if p[0] in (1, 2):
                for r in range(0, self.y):
                    self.grid[r] = [" "] * self.cols
                self.grid[self.y][:self.x + 1] = [" "] * (self.x + 1)
        elif cmd == "X":
            self.grid[self.y][self.x:self.x + n] = [" "] * len(self.grid[self.y][self.x:self.x + n])
        elif cmd == "P":
            row = self.grid[self.y]
            row[self.x:] = row[self.x + n:] + [" "] * min(n, self.cols - self.x)
        elif cmd == "@":
            row = self.grid[self.y]
            row[self.x:] = ([" "] * n + row[self.x:])[:self.cols - self.x]
        elif cmd == "b":
            for _ in range(n):
                if self.x < self.cols:
                    self.grid[self.y][self.x] = last
                    self.x += 1

    def row(self, y):
        return "".join(self.grid[y]).rstrip()


class IndentTests(unittest.TestCase):
    """Soft-wrapped list items hang under their text. Display only: the file is never changed."""

    def test_hang_by_marker(self):
        words = " ".join(["word"] * 30)
        for marker, hang in (("- ", 2), ("* ", 2), ("1. ", 3), ("12) ", 4), ("- [ ] ", 6), ("- [x] ", 6),
                             ("> ", 2), ("    - ", 6), ("  1. ", 5)):
            line = marker + words
            starts, got = lawnotes.wrap_layout(line, 40)
            self.assertEqual(got, hang, marker)
            self.assertGreater(len(starts), 2, marker)
            for k in range(len(starts) - 1):  # no row is wider than the pane allows
                self.assertLessEqual(starts[k + 1] - starts[k], 40 - (hang if k else 0), marker)

    def test_hang_changes_only_the_wrap(self):
        line = "- " + " ".join(["word"] * 30)
        starts, hang = lawnotes.wrap_layout(line, 40)
        self.assertEqual(starts, lawnotes.wrap_starts(line, 40, 2))
        self.assertNotEqual(starts, lawnotes.wrap_starts(line, 40))
        rows = [line[a:b] for a, b in zip(starts, starts[1:] + (len(line),))]
        self.assertEqual("".join(rows), line)  # no character added or lost
        self.assertEqual(starts[1], lawnotes.wrap_starts(line, 40)[1])  # the first row is as wide as ever

    def test_hang_is_capped(self):
        deep = " " * 30 + "- " + " ".join(["word"] * 30)
        self.assertEqual(lawnotes.wrap_layout(deep, 40)[1], 0)      # more than half the width
        self.assertEqual(lawnotes.wrap_layout("- " + "word " * 30, 19)[1], 0)  # very narrow pane
        self.assertEqual(lawnotes.wrap_layout("- " + "word " * 30, 20)[1], 2)
        self.assertEqual(lawnotes.wrap_layout(" " * 20 + "- " + "word " * 30, 40)[1], 0)  # 22 > 40 // 2
        self.assertEqual(lawnotes.wrap_layout(" " * 18 + "- " + "word " * 30, 40)[1], 20)

    def test_no_marker_and_unbroken_word(self):
        plain = " ".join(["word"] * 30)
        self.assertEqual(lawnotes.wrap_layout(plain, 40), (lawnotes.wrap_starts(plain, 40), 0))
        self.assertEqual(lawnotes.wrap_layout("- " + "x" * 100, 40)[0], (0, 2, 40, 78))  # breaks after "- ", then 38-wide rows
        self.assertEqual(lawnotes.wrap_layout("-" + "x" * 100, 40), (lawnotes.wrap_starts("-" + "x" * 100, 40), 0))
        self.assertEqual(lawnotes.wrap_layout("x" * 100, 40)[0], (0, 40, 80))
        self.assertEqual(lawnotes.wrap_layout("- short", 40), ((0,), 0))

    ITEM = "- " + " ".join(f"w{i:02d}" for i in range(60))

    def run_session(self, name, text, cols, script):
        """Run `script(session, screen_of, x0)` on a note holding `text`; returns the saved file."""
        note = os.path.join(lawnotes.NOTES_DIR, name)
        os.makedirs(lawnotes.NOTES_DIR, exist_ok=True)
        with open(note, "w") as f:
            f.write(text)
        s = Session(note, size=(30, cols))
        try:
            script(s, lambda: Screen(s.out, 30, cols))
        finally:
            s.quit()
        with open(note) as f:
            return f.read()

    @staticmethod
    def rows_of(scr, first="- w00"):
        """(y of the item's first row, its column, the y values of the rows below it)."""
        y0 = next(y for y in range(scr.rows) if first in scr.row(y))
        x0 = scr.row(y0).index(first)
        ys = [y0]
        while scr.row(ys[-1] + 1).lstrip().startswith("w"):
            ys.append(ys[-1] + 1)
        for y in ys[1:]:  # every test below starts from a hanging indent
            assert len(scr.row(y)) - len(scr.row(y).lstrip()) == x0 + 2, scr.row(y)
        return x0, ys

    def test_continuation_rows_are_drawn_indented(self):
        lines = ["- " + "alpha beta gamma delta " * 5, "* " + "alpha beta gamma delta " * 5,
                 "1. " + "alpha beta gamma delta " * 5, "- [ ] " + "alpha beta gamma delta " * 5,
                 "    - " + "alpha beta gamma delta " * 5, "> " + "alpha beta gamma delta " * 5,
                 "plain " + "alpha beta gamma delta " * 5]
        hangs = [2, 2, 3, 6, 6, 2, 0]
        for cols in (40, 100):
            def script(s, screen):
                scr = screen()
                y = 0
                for line, hang in zip(lines, hangs):
                    first = scr.row(y)
                    x0 = len(first) - len(first.lstrip()) if not line.startswith(" ") else first.index("-") - 4
                    for k in range(1, 4):
                        row = scr.row(y + k)
                        if not row.lstrip().startswith(("alpha", "beta", "gamma", "delta")):
                            break
                        self.assertEqual(len(row) - len(row.lstrip()) - x0, hang, (cols, line, row))
                    else:
                        pass
                    y += 1
                    while scr.row(y).lstrip().startswith(("alpha", "beta", "gamma", "delta")):
                        y += 1
                self.assertGreater(y, 7)
            saved = self.run_session(f"Hang{cols}.md", "\n".join(lines) + "\n", cols, script)
            self.assertEqual(saved, "\n".join(lines) + "\n")

    def test_quoted_before_after_at_40(self):
        def script(s, screen):
            scr = screen()
            x0, ys = self.rows_of(scr)
            self.assertGreater(len(ys), 3)
            self.assertEqual([scr.row(y).strip() for y in ys[:3]], [
                "- w00 w01 w02 w03 w04 w05 w06 w07", "w08 w09 w10 w11 w12 w13 w14 w15",
                "w16 w17 w18 w19 w20 w21 w22 w23"])
            self.assertEqual([len(scr.row(y)) - len(scr.row(y).lstrip()) - x0 for y in ys[:3]], [0, 2, 2])
        self.run_session("Show40.md", self.ITEM + "\n", 40, script)

    def test_typing_at_end_goes_where_the_cursor_is(self):
        for cols in (40, 100):
            def script(s, screen):
                s.keys(b"\x05ZQ", 0.5)
                scr = screen()
                self.assertEqual(scr.row(scr.y)[:scr.x][-2:], "ZQ", (cols, scr.y, scr.x))
                self.assertEqual(scr.row(scr.y)[scr.x:], "")
                x0, ys = self.rows_of(scr)
                self.assertEqual(scr.y, next(y for y in reversed(ys)))
                s.keys(b"\x13", 0.5)
            saved = self.run_session(f"Type{cols}.md", self.ITEM + "\ntail\n", cols, script)
            self.assertEqual(saved, self.ITEM + "ZQ\ntail\n")

    def index_at(self, scr, y, x, x0, ys):
        """Index in ITEM of the character drawn at screen (y, x)."""
        row = scr.row(y)
        start = x0 if y == ys[0] else len(row) - len(row.lstrip())
        return self.ITEM.index(row[start:]) + (x - start)

    def test_up_down_keep_the_visual_column(self):
        for cols in (40, 100):
            def script(s, screen):
                scr = screen()
                x0, ys = self.rows_of(scr)
                col = x0 + 12
                s.keys(f"\x1b[<0;{col + 1};{ys[0] + 1}M\x1b[<0;{col + 1};{ys[0] + 1}m".encode(), 0.5)
                scr = screen()
                self.assertEqual((scr.y, scr.x), (ys[0], col))
                for y in ys[1:3]:
                    s.keys(b"\x1b[B", 0.4)
                    scr = screen()
                    self.assertEqual((scr.y, scr.x), (y, col), cols)
                s.keys(b"\x1b[A", 0.4)
                scr = screen()
                self.assertEqual((scr.y, scr.x), (ys[1], col), cols)
                s.keys(b"!\x13", 0.5)
                scr = screen()
                self.assertEqual(scr.row(scr.y)[scr.x - 1], "!")
            saved = self.run_session(f"Updown{cols}.md", self.ITEM + "\ntail\n", cols, script)
            self.assertEqual(saved.replace("!", ""), self.ITEM + "\ntail\n")
            self.assertEqual(saved.count("!"), 1)

    def test_click_on_continuation_row(self):
        for cols in (40, 100):
            where = {}

            def script(s, screen):
                scr = screen()
                x0, ys = self.rows_of(scr)
                y, x = ys[2], x0 + 9
                where["idx"] = self.index_at(scr, y, x, x0, ys)
                s.keys(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode(), 0.5)
                scr = screen()
                self.assertEqual((scr.y, scr.x), (y, x))
                s.keys(b"!\x13", 0.5)
            saved = self.run_session(f"Click{cols}.md", self.ITEM + "\ntail\n", cols, script)
            i = where["idx"]
            self.assertEqual(saved, self.ITEM[:i] + "!" + self.ITEM[i:] + "\ntail\n", cols)

    def test_click_in_the_indent_goes_to_row_start(self):
        where = {}

        def script(s, screen):
            scr = screen()
            x0, ys = self.rows_of(scr)
            row = scr.row(ys[1])
            where["idx"] = self.ITEM.index(row.lstrip())
            s.keys(f"\x1b[<0;{x0 + 1};{ys[1] + 1}M\x1b[<0;{x0 + 1};{ys[1] + 1}m".encode(), 0.5)
            s.keys(b"!\x13", 0.5)
        saved = self.run_session("Gap.md", self.ITEM + "\n", 40, script)
        i = where["idx"]
        self.assertEqual(saved, self.ITEM[:i] + "!" + self.ITEM[i:] + "\n")

    def test_drag_select_across_continuation_rows(self):
        where = {}

        def script(s, screen):
            scr = screen()
            x0, ys = self.rows_of(scr)
            a, b = (ys[0], x0 + 6), (ys[2], x0 + 6)
            where["a"] = self.index_at(scr, a[0], a[1], x0, ys)
            where["b"] = self.index_at(scr, b[0], b[1], x0, ys)
            s.keys(f"\x1b[<0;{a[1] + 1};{a[0] + 1}M\x1b[<32;{b[1] + 1};{b[0] + 1}M\x1b[<0;{b[1] + 1};{b[0] + 1}m".encode(), 0.5)
            s.keys(b"!\x13", 0.5)
        saved = self.run_session("Drag.md", self.ITEM + "\n", 40, script)
        self.assertEqual(saved, self.ITEM[:where["a"]] + "!" + self.ITEM[where["b"]:] + "\n")


class ScanSpeedTests(unittest.TestCase):
    """The incremental case index equals a full rebuild, and the light reader equals Buffer."""

    NAMES = ["Donoghue v Stevenson [1932] AC 562", "Caparo Industries plc v Dickman [1990] 2 AC 605",
             "Caparo Industries plc v Dickman [1990] 1 AC 605", "Re Polemis [1921] 3 KB 560",
             "Bolton v Stone [1951] AC 850", "Hedley Byrne v Heller", "Rylands v Fletcher (1868) LR 3 HL 330"]

    def setUp(self):
        self.saved = lawnotes.NOTES_DIR
        self.dir = tempfile.mkdtemp(dir=TMP)
        lawnotes.NOTES_DIR = os.path.join(self.dir, "notes")
        os.makedirs(lawnotes.NOTES_DIR)
        self.clock = 1_000_000.0
        self.tick = 0
        self.addCleanup(setattr, lawnotes, "NOTES_DIR", self.saved)

    def write(self, rel, lines):
        path = os.path.join(lawnotes.NOTES_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.tick += 1
        os.utime(path, (1_500_000_000 + self.tick, 1_500_000_000 + self.tick))  # a distinct stamp each time
        return path

    def app(self, buf):
        app = lawnotes.App.__new__(lawnotes.App)
        app._cases = (0.0, None, None)
        app._case_scans = {}
        app.buf = buf
        app.buffers = lambda: [(None, app.buf)]
        return app

    def entries(self, app):
        self.clock += 10  # past the five-second limit on re-listing the folder
        with patch.object(lawnotes.time, "time", return_value=self.clock):
            return app.case_entries()

    @staticmethod
    def shape(entries):
        return [(label, c["name"], c["cite"], c["best_cite"], list(c["cites"].items()), c["hits"])
                for label, c in entries]

    def same_as_rebuild(self, app):
        got = self.shape(self.entries(app))
        fresh = self.app(app.buf)
        self.assertEqual(got, self.shape(self.entries(fresh)))
        return got

    def test_edit_open_buffer_disk_change_delete_and_add(self):
        a = self.write("Tort/A.md", [self.NAMES[0], "text"])
        b = self.write("Tort/B.md", [self.NAMES[1], self.NAMES[3]])
        self.write("C.md", [self.NAMES[0] + " again"])
        buf = lawnotes.Buffer(a)
        app = self.app(buf)
        before = self.same_as_rebuild(app)
        buf.lines.append("See " + self.NAMES[4])
        buf.version += 1
        after = self.same_as_rebuild(app)
        self.assertNotEqual(before, after)
        self.write("Tort/B.md", [self.NAMES[2], "Re Polemis"])
        self.same_as_rebuild(app)
        os.remove(b)
        self.same_as_rebuild(app)
        self.write("New.md", [self.NAMES[5], self.NAMES[0]])
        self.assertTrue(any("Hedley" in label for label, *_ in self.same_as_rebuild(app)))

    def test_open_note_outside_the_folder_and_unsaved_text(self):
        self.write("In.md", [self.NAMES[0]])
        outside = os.path.join(self.dir, "Outside.md")
        buf = lawnotes.Buffer(outside)
        buf.lines = [self.NAMES[4]]
        buf.version += 1
        app = self.app(buf)
        got = self.same_as_rebuild(app)
        self.assertEqual(got[0][1], "Bolton v Stone")
        buf.lines = [self.NAMES[5]]
        buf.version += 1
        self.assertTrue(any(name == "Hedley Byrne v Heller" for _, name, *_ in self.same_as_rebuild(app)))

    def test_unchanged_notes_are_not_read_again(self):
        for i in range(5):
            self.write(f"N{i}.md", [self.NAMES[i % len(self.NAMES)]])
        buf = lawnotes.Buffer(self.write("Open.md", [self.NAMES[0]]))
        app = self.app(buf)
        self.entries(app)
        reads = []
        real = lawnotes.read_note

        def counting(path):
            reads.append(path)
            return real(path)

        with patch.object(lawnotes, "read_note", counting):
            buf.lines.append(self.NAMES[3])
            buf.version += 1
            self.entries(app)
            self.assertEqual(reads, [])
            changed = self.write("N2.md", [self.NAMES[6]])
            self.entries(app)
            self.assertEqual(reads, [changed])

    def test_random_steps_equal_a_full_rebuild(self):
        import random
        rng = random.Random(1110)
        for d in ("Tort", "Contract", ""):
            for i in range(4):
                self.write(os.path.join(d, f"N{i}.md"), [rng.choice(self.NAMES) for _ in range(rng.randint(0, 3))])
        buf = lawnotes.Buffer(self.write("Tort/N0.md", ["start"]))
        app = self.app(buf)
        for step in range(48):
            action = rng.choice(["edit", "modify", "add", "delete", "switch", "nothing"])
            files = [os.path.join(r, f) for r, _, fs in os.walk(lawnotes.NOTES_DIR) for f in fs]
            if action == "edit":
                buf.lines.insert(rng.randint(0, len(buf.lines)), rng.choice(self.NAMES + ["plain"]))
                buf.version += 1
            elif action == "modify" and files:
                path = rng.choice(files)
                self.write(os.path.relpath(path, lawnotes.NOTES_DIR),
                           [rng.choice(self.NAMES + ["plain"]) for _ in range(rng.randint(0, 4))])
            elif action == "add":
                self.write(f"Added/{step}.md", [rng.choice(self.NAMES) for _ in range(rng.randint(1, 3))])
            elif action == "delete" and len(files) > 2:
                victim = rng.choice([p for p in files if os.path.realpath(p) != os.path.realpath(buf.path)])
                os.remove(victim)
            elif action == "switch" and files:
                app.buf = buf = lawnotes.Buffer(rng.choice(files))
            self.same_as_rebuild(app)

    def test_light_reader_equals_buffer(self):
        samples = {
            "bom.md": b"\xef\xbb\xbfDonoghue v Stevenson\nline\n",
            "cp1252.md": "caf\N{LATIN SMALL LETTER E WITH ACUTE} \N{LEFT DOUBLE QUOTATION MARK}q\N{EM DASH}\n".encode("cp1252"),
            "latin.md": b"caf\xe9 \x81\n",
            "crlf.md": b"one\r\ntwo\rthree\r\n\r\n",
            "tabs.md": b"\tindented\n a\tb\n",
            "control.md": b"bell\x07 nul\x00 del\x7f end\n",
            "empty.md": b"",
            "blank.md": b"\n\n",
            "nonl.md": b"no newline",
        }
        for name, data in samples.items():
            path = os.path.join(self.dir, name)
            with open(path, "wb") as f:
                f.write(data)
            buf = lawnotes.Buffer(path)
            lines, note, mtime = lawnotes.read_note(path)
            self.assertEqual(lines, buf.lines, name)
            self.assertEqual(note, buf.note, name)
            self.assertEqual(mtime, buf.disk_mtime, name)


class KeyTableTests(unittest.TestCase):
    TOP = "┌" + "─" * 44 + "┐"
    BOTTOM = "└" + "─" * 44 + "┘"
    LINE = "├" + "─" * 44 + "┤"
    # The rows of today's right-click menu, as drawn (label left, shortcut right).
    UNDO_REDO = [" Undo                                    ^Z ", " Redo                                    ^Y ", LINE]
    SPLITS = [" Split side by side                Option+D ", " Split one above the other   Option+Shift+D "]
    PANES = [" Next pane                         Option+O ", " Close this pane                   Option+W "]
    NOTES = [" Replace…                          Option+R ", " Revision cards…                   Option+C ",
             " Open to-dos…                      Option+J ", " Check OSCOLA…                     Option+K "]
    TAIL = [" Abbreviations…                    Option+A ", " Export…                           Option+E ",
            " Themes…                           Option+T ", BOTTOM]
    PLAIN = [TOP, " Paste                                   ^V ", " Select word                   double-click ",
             " Highlight word                    Option+H ", LINE] + UNDO_REDO + SPLITS
    LINKS = " Notes linking here                Option+L "
    EXAM = " Exam…                             Option+X "

    def menu(self, selected=False, panes=1, exam=None, link=None, pick=0):
        """Open the right-click menu on a bare App: the rows it draws, and the keys the pick-th choice queues."""
        app = lawnotes.App.__new__(lawnotes.App)
        app.buf = lawnotes.Buffer(os.path.join(TMP, "key-table.md"))
        app.buf.lines = ["hello world"]
        app.pane = lawnotes.Pane()
        app.pane.exam = exam
        app.queued, app.anchor = [], None
        app.selection = lambda: selected
        app.panes = lambda: [None] * panes
        app.link_at = lambda: link
        app.misspelt = lambda line: []
        app.scr = mock.Mock(getmaxyx=lambda: (40, 100))
        app.A = {"bar": 0, "key": 1}
        drawn = []
        app.draw = drawn.clear
        app.refresh = lambda: None
        app.put = lambda y, x, text, attr=0: drawn.append(text)
        keys = iter([lawnotes.curses.KEY_DOWN] * pick + ["\n"])
        app.get_key = lambda: next(keys)
        app.normalise = lambda key: key
        with mock.patch("curses.curs_set"):
            app.context_menu(5, 5)
        return [row for row in drawn if row != "│"], app.queued

    def test_every_command_names_a_real_method(self):
        for c in lawnotes.COMMANDS:
            if c.method:
                self.assertTrue(callable(getattr(lawnotes.App, c.method, None)), c)

    def test_no_key_appears_twice(self):
        keys = [key for c in lawnotes.COMMANDS for key in c.keys]
        self.assertEqual(sorted(map(repr, keys)), sorted(map(repr, set(keys))))

    def test_keys_call_their_methods(self):
        app = lawnotes.App.__new__(lawnotes.App)
        for key, method, args in [("\x16", "paste_clipboard", ()), (lawnotes.curses.KEY_F9, "check_oscola", ()),
                                  ("split-down", "split", ("h",)), ("\x07", "show_help", ())]:
            with self.subTest(key=key):
                app.buf, app.anchor, app.cutting, app.flash = lawnotes.Buffer(os.path.join(TMP, "k.md")), None, False, 1
                app.normalise = lambda k: k
                with mock.patch.object(lawnotes.App, method) as called:
                    app._handle(key)
                called.assert_called_once_with(*args)

    def test_menu_with_nothing_selected(self):
        rows, _ = self.menu()
        self.assertEqual(rows, self.PLAIN + [self.LINE, self.LINKS] + self.NOTES + [self.EXAM] + self.TAIL)

    def test_menu_with_text_selected(self):
        rows, _ = self.menu(selected=((0, 0), (0, 5)))
        self.assertEqual(rows, [
            self.TOP, " Cut                                     ^X ", " Copy                                    ^C ",
            " Paste                                   ^V ", self.LINE,
            " Highlight                                = ", " Italic                                   * ",
            " Bold                                    ** ", self.LINE] + self.UNDO_REDO + self.SPLITS
            + [self.LINE, self.LINKS] + self.NOTES + [self.EXAM] + self.TAIL)

    def test_menu_with_two_panes(self):
        rows, _ = self.menu(panes=2)
        self.assertEqual(rows, self.PLAIN + self.PANES + [self.LINE, self.LINKS] + self.NOTES + [self.EXAM] + self.TAIL)

    def test_menu_while_an_exam_runs(self):
        rows, _ = self.menu(exam=object())
        self.assertEqual(rows, self.PLAIN + [self.LINE, self.LINKS] + self.NOTES
                         + [" Stop exam                         Option+X "] + self.TAIL)

    def test_menu_on_a_link(self):
        rows, _ = self.menu(link="Tort/Duty")
        self.assertEqual(rows, self.PLAIN + [self.LINE, " Open linked note                  Option+L "]
                         + self.NOTES + [self.EXAM] + self.TAIL)

    def test_menu_choices_queue_these_keys(self):
        every = [self.menu(panes=2, pick=i)[1] for i in (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)]
        self.assertEqual(every, [
            ["\x16"], ["highlight"], ["\x1a"], ["\x19"], ["split-right"], ["split-down"], ["next-pane"], ["close-pane"],
            ["links"], ["replace"], ["cards"], ["todos"], ["oscola"], ["exam"], ["abbreviations"], ["export"],
            ["themes"]])
        selected = ((0, 0), (0, 5))
        self.assertEqual([self.menu(selected=selected, pick=i)[1] for i in range(6)],
                         [["\x18"], ["\x03"], ["\x16"], ["="], ["*"], ["*", "*"]])


if __name__ == "__main__":
    unittest.main()
