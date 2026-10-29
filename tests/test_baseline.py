"""Offline regression over the bundled baseline book (no API; ~1 min).

The fixture is a born-digital monograph with a printed CONTENTS page, chapter
notes, a bibliography and an index. The assertions are loose bounds around the
current pipeline output, so a structural regression (lost chapters, notes
reclassified as paragraphs, page labels drifting) fails loudly.
"""
from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: E402,F401

import pymupdf  # noqa: E402

from llmtr import ingest  # noqa: E402

BASE = Path(__file__).resolve().parent / "fixtures" / "baseline"
FIXTURE = BASE / "source.pdf"


class Baseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest(
                "baseline fixture not bundled (copyright); place a born-digital "
                "PDF at tests/fixtures/baseline/source.pdf to run this suite")
        cls.structure = json.loads((BASE / "structure.json").read_text(encoding="utf-8"))["chapters"]
        cls.pdf_pages = len(pymupdf.open(str(FIXTURE)))
        cls.book = ingest.ingest(FIXTURE, structure=cls.structure)
        cls.blocks = cls.book["blocks"]
        cls.kinds = Counter(b["type"] for b in cls.blocks)

    def test_ingest_succeeds(self):
        self.assertTrue(self.book["ok"])

    def test_labels_cover_every_pdf_page(self):
        self.assertEqual(len(self.book["labels"]), self.pdf_pages)
        self.assertTrue(all(0 <= v < self.pdf_pages for v in self.book["page_map"].values()))

    def test_chapters_follow_the_printed_contents(self):
        h1 = [b["text"] for b in self.blocks if b["type"] == "h1"]
        self.assertEqual(len(h1), len(self.structure) + 1)      # + BIBLIOGRAPHY
        for got, want in zip(h1, self.structure):
            self.assertTrue(got.upper().startswith(want["title"][:24].upper()),
                            "%r does not start with %r" % (got, want["title"]))
        self.assertTrue(h1[-1].upper().startswith("BIBLIOGRAPHY"))
        self.assertEqual(len(h1), len({t.upper() for t in h1}))  # no duplicated chapters

    def test_block_mix_is_stable(self):
        self.assertGreaterEqual(self.kinds["note"], 650)
        self.assertGreaterEqual(self.kinds["bib"], 180)
        self.assertGreaterEqual(self.kinds["h2"], 15)
        self.assertGreaterEqual(self.kinds["p"], 900)

    def test_arabic_page_numbers_never_go_backwards(self):
        seq = [(i, int(b["page"])) for i, b in enumerate(self.blocks)
               if str(b["page"]).isdigit()]
        desc = [(i, p) for (i, p), (_j, q) in zip(seq, seq[1:]) if q < p]
        self.assertLessEqual(len(desc), 1, desc[:5])
        if desc:
            self.assertEqual(seq[[i for i, _p in seq].index(desc[0][0]) + 1][1], 1,
                             "a page rewind is only allowed when the body restarts at page 1")

    def test_text_is_clean(self):
        self.assertFalse(any("\ufffd" in b["text"] for b in self.blocks))


if __name__ == "__main__":
    unittest.main()
