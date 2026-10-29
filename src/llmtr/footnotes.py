"""Real Word footnotes (page-bottom), built by hand because python-docx lacks support."""
from __future__ import annotations

from xml.sax.saxutils import escape

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
CT = ("application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml")


def _footnote_xml(items: list[tuple[int, str]]) -> bytes:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<w:footnotes %s>' % NS,
           '<w:footnote w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" '
           'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p></w:footnote>',
           '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing '
           'w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:continuationSeparator/>'
           '</w:r></w:p></w:footnote>']
    for fid, text in items:
        out.append(
            '<w:footnote w:id="%d"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/>'
            '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
            '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>'
            '<w:r><w:rPr><w:sz w:val="17"/></w:rPr><w:t xml:space="preserve"> %s</w:t></w:r>'
            '</w:p></w:footnote>' % (fid, escape(text)))
    out.append('</w:footnotes>')
    return "".join(out).encode("utf-8")


class FootnoteStore:
    """Collects notes and writes them as real Word footnotes at save time."""

    def __init__(self):
        self.items: list[tuple[int, str]] = []
        self._next = 1

    def add(self, text: str) -> int:
        fid = self._next
        self._next += 1
        self.items.append((fid, text))
        return fid

    def reference(self, par, fid: int) -> None:
        run = par.add_run()
        rpr = run._r.get_or_add_rPr()
        va = OxmlElement("w:vertAlign"); va.set(qn("w:val"), "superscript"); rpr.append(va)
        sz = OxmlElement("w:sz"); sz.set(qn("w:val"), "17"); rpr.append(sz)
        ref = OxmlElement("w:footnoteReference")
        ref.set(qn("w:id"), str(fid))
        run._r.append(ref)

    def attach(self, doc) -> None:
        if not self.items:
            return
        part = Part(PackURI("/word/footnotes.xml"), CT, _footnote_xml(self.items),
                    doc.part.package)
        doc.part.relate_to(part, RT.FOOTNOTES)
