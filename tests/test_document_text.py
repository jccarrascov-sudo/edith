"""The memory reads the visible text of documents (Word's body), not only the window's labels."""

from __future__ import annotations

import unittest

from edith.memory import uia


class _Range:
    def __init__(self, text):
        self.text = text

    def GetText(self, n):
        return self.text[:n]


class _Pattern:
    def __init__(self, *texts):
        self.texts = texts

    def GetVisibleRanges(self):
        return [_Range(t) for t in self.texts]


class _Doc:
    def __init__(self, pattern):
        self.pattern = pattern

    def GetPattern(self, _id):
        return self.pattern


class DocumentTextTests(unittest.TestCase):
    def test_visible_ranges_become_lines(self):
        doc = _Doc(_Pattern("Primero el suelo\rLa mala asignación del suelo", "Hsieh y Moretti (2019)"))
        self.assertEqual(uia._document_text(doc, 1000).splitlines(),
                         ["Primero el suelo", "La mala asignación del suelo", "Hsieh y Moretti (2019)"])

    def test_the_budget_and_no_pattern(self):
        self.assertEqual(len(uia._document_text(_Doc(_Pattern("x" * 9000)), 6000)), 6000)
        self.assertEqual(uia._document_text(_Doc(None), 6000), "")

        class Broken:
            def GetPattern(self, _id):
                raise OSError("UIA went away")
        self.assertEqual(uia._document_text(Broken(), 6000), "")


if __name__ == "__main__":
    unittest.main()
