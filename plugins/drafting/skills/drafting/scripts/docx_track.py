# -*- coding: utf-8 -*-
"""docx_track — round-trip Word track-changes helper (author = "Claude").

Reusable core for the `scriere` skill. Operates on an UNPACKED .docx directory and
writes every edit as a tracked change (w:ins / w:del / w:pPrChange), so the human can
accept/reject each one in Word. Also does threaded comment replies and footnotes.

Typical use:

    from docx_track import Docx
    d = Docx.unpack("Manuscript.docx", "_work")   # reliable unzip (handles diacritics/§)
    d.replace("teh ", "the ", "typo")             # tracked del+ins
    d.insert_after_text("as outlined", " (Sandler 2015)", "cite")
    d.insert_after_ins("value sym", " (Olson [1965] 2003)", "cite after user ins")
    d.set_heading("Exploitation of the largest", 3, "promote to H3")
    d.add_comment("its unique anchor text", "Reviewer note.", "comment", occurrence="first")
    d.reply_to_comment(13, "Inserted (Sandler 2015, 190).", "reply #13")
    d.add_footnote("Opinia Timișoarei", footnote_xml_runs, "press footnote")
    d.save()
    d.repack("Manuscript_track.docx")
    # then ALWAYS: validate_docx.py Manuscript_track.docx

Design notes / gotchas live in references/ooxml-track-changes.md — read it before
extending. Matching: `replace`/`delete`/`insert_after_text` try a SINGLE direct run
first, then fall back to a RUN-SPANNING match across consecutive direct runs (Word
splits a phrase across runs unpredictably) with length-preserving normalization of
typographic vs ASCII quotes/apostrophes/spaces/dashes — so a needle written with plain
' " - or space still hits the document's ’ “ ” – or non-breaking space. The fallback
never crosses a w:ins/w:del/hyperlink boundary (it only joins plain runs), so tracked
content is left intact. If a match still misses, the anchor sits inside the user's own
w:ins — use insert_after_ins / fix_in_ins instead.
"""
import sys, os, copy, zipfile
from pathlib import Path
from lxml import etree

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# --- OOXML namespaces ---
W   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16 = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
XS  = "{http://www.w3.org/XML/1998/namespace}space"
def w(t):   return f"{{{W}}}{t}"
def w14(t): return f"{{{W14}}}{t}"
def w15(t): return f"{{{W15}}}{t}"
def w16(t): return f"{{{W16}}}{t}"

_PARSER = etree.XMLParser(remove_blank_text=False)

# Length-preserving normalization (each mapping is 1 char -> 1 char, so offsets in the
# normalized string map straight back onto the original runs). Lets a needle typed with
# ASCII ' " - or a plain space match the document's typographic ’ ‘ “ ” – — or NBSP.
_NORM = {
    " ": " ",                      # non-breaking space
    "’": "'", "‘": "'",        # ’ ‘
    "“": '"', "”": '"',        # “ ”
    "–": "-", "—": "-",        # – —
}
def _norm(s):
    return "".join(_NORM.get(c, c) for c in s)


class Docx:
    """An unpacked .docx under `root`. All edits are tracked changes by `author`."""

    def __init__(self, root, author="Claude", initials="C", date="2026-01-01T00:00:00Z"):
        self.root = Path(root)
        self.author = author
        self.initials = initials
        self.date = date
        self._id = 9000            # running id for w:ins/w:del/pPrChange + comment ids
        self._trees = {}           # filename -> parsed tree (lazy)
        self.log = []
        doc = self._tree("document.xml")
        self.body = doc.getroot().find(w("body"))

    # ---------- packing ----------
    @classmethod
    def unpack(cls, src, workdir, **kw):
        """Reliable unzip — NOT `unzip` (which leaves document.xml stale on §/diacritic paths)."""
        workdir = Path(workdir)
        if workdir.exists():
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(workdir)
        return cls(workdir, **kw)

    def repack(self, out):
        """Rezip the working dir back into a .docx (saves pending trees first)."""
        self.save()
        out = Path(out)
        files = [p for p in self.root.rglob("*") if p.is_file()]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.relative_to(self.root).as_posix())
        return out

    # ---------- internals ----------
    def _tree(self, fn):
        if fn not in self._trees:
            self._trees[fn] = etree.parse(str(self.root / "word" / fn), _PARSER)
        return self._trees[fn]

    def _nid(self):
        self._id += 1
        return str(self._id)

    def _paras(self):
        # ALL paragraphs under the body — including those nested inside w:sdt content
        # controls (Google Docs exports bury most paragraphs there) and table cells.
        # findall(w:p) only saw direct children and silently missed both.
        return list(self.body.iter(w("p")))

    @staticmethod
    def _para_text(para):
        return "".join(t.text or "" for t in para.iter(w("t")))

    @staticmethod
    def _rtext(r):
        return "".join(x.text or "" for x in r.findall(w("t")))

    def _mk_t(self, text):
        r = etree.Element(w("r")); t = etree.SubElement(r, w("t")); t.text = text
        if text and (text[0] == " " or text[-1] == " "):
            t.set(XS, "preserve")
        return r

    def _mk_t_like(self, src, text):
        """A plain run carrying `text` but cloning `src`'s formatting (w:rPr), so a kept
        prefix/suffix fragment keeps the original run's italics/style instead of going bare."""
        r = etree.Element(w("r"))
        rpr = src.find(w("rPr"))
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        t = etree.SubElement(r, w("t")); t.text = text
        if text and (text[0] == " " or text[-1] == " "):
            t.set(XS, "preserve")
        return r

    # Run children that carry NO text and NO authored meaning, so rebuilding a run without
    # them changes nothing the reader or Word sees. Only w:lastRenderedPageBreak qualifies:
    # ECMA-376 defines it as informative — Word stamps it where it last paginated and
    # recomputes it on the next pagination, so dropping it is a no-op. Do NOT add
    # w:noBreakHyphen / w:softHyphen (they ARE authored characters) or w:tab / w:br (layout).
    _IGNORABLE_IN_RUN = {w("lastRenderedPageBreak")}

    @classmethod
    def _pure_text_run(cls, r):
        """True iff the run carries only text (w:rPr + w:t) plus, optionally, ignorable
        no-op children — no w:tab/w:br/w:drawing/w:footnoteReference etc. The span fallback
        rebuilds runs as plain text, so it must refuse non-pure runs: otherwise collapsing/
        splitting one would silently drop that structural content. A refused match falls
        through to a visible MISS instead.

        w:lastRenderedPageBreak is accepted (see _IGNORABLE_IN_RUN): Word puts one in the
        run where it last paginated, which is the FIRST child of an ordinary text run, and
        refusing those made every anchor on a page boundary MISS with no stated reason —
        in any .docx Word had ever opened. The rebuilt fragments come from _mk_t_like, which
        copies w:rPr only, so the break is dropped rather than duplicated into both halves
        of a split (two page breaks would be a real, visible regression). Runs that pair the
        break with a w:drawing are still refused — the drawing is what makes them non-pure."""
        return all(ch.tag in (w("rPr"), w("t")) or ch.tag in cls._IGNORABLE_IN_RUN for ch in r)

    @staticmethod
    def _logical_children(para):
        """`para`'s meaningful descendants with w:sdt/w:sdtContent wrappers transparently
        unwrapped (recursively — Word/Google Docs nest a fresh w:sdt per edit, so a run can
        end up several sdt levels deep after a few round-trips). Returns the REAL elements
        (w:r, w:ins, w:del, w:hyperlink, w:bookmarkStart, ...) in document order; each one's
        true parent — via `.getparent()`, NOT `para` — is what owns it structurally, since a
        run may live inside some sdtContent rather than directly under the paragraph."""
        out = []
        def walk(node):
            for ch in node:
                if ch.tag == w("sdt"):
                    content = ch.find(w("sdtContent"))
                    if content is not None:
                        walk(content)
                else:
                    out.append(ch)
        walk(para)
        return out

    _TRANSPARENT = {w("proofErr"), w("bookmarkStart"), w("bookmarkEnd")}

    @classmethod
    def _run_groups(cls, para):
        """Maximal stretches of CONSECUTIVE w:r elements (sdt-transparent — see
        _logical_children). Boundaries = anything else (w:ins/w:del/hyperlink/...), so a
        span match never reaches across tracked content or a hyperlink. w:proofErr /
        w:bookmarkStart / w:bookmarkEnd carry no text and are transparent — skipped, not
        boundaries — since Word sprinkles proofErr around every spell-checked foreign
        word/proper noun (very common in non-English text with English citations), and
        treating them as boundaries fragments runs into single words, breaking any needle
        that spans more than one word next to a flagged name."""
        groups, cur = [], []
        for ch in cls._logical_children(para):
            if ch.tag == w("r"):
                cur.append(ch)
            elif ch.tag in cls._TRANSPARENT:
                continue
            elif cur:
                groups.append(cur); cur = []
        if cur:
            groups.append(cur)
        return groups

    def _locate_in_group(self, runs, needle):
        """Find `needle` across the concatenated text of `runs` (one group). Returns
        (ra, oa, rb, ob, texts, real) where ra/oa = first run + offset, rb/ob = last run +
        exclusive end offset, texts = per-run text, real = the actual document substring
        (denormalized, so a deletion shows the real ’/–/NBSP characters). Or None."""
        texts = [self._rtext(r) for r in runs]
        concat = "".join(texts)
        i = _norm(concat).find(_norm(needle))
        if i < 0:
            return None
        j = i + len(needle); real = concat[i:j]
        bounds, off = [], 0
        for t in texts:
            bounds.append(off); off += len(t)
        def loc(pos):
            for k in range(len(runs)):
                if bounds[k] <= pos < bounds[k] + len(texts[k]):
                    return k, pos - bounds[k]
            return len(runs) - 1, len(texts[-1])
        ra, oa = loc(i)
        rb, ob = loc(j - 1); ob += 1
        return ra, oa, rb, ob, texts, real

    def _ins(self, text):
        e = etree.Element(w("ins"))
        e.set(w("id"), self._nid()); e.set(w("author"), self.author); e.set(w("date"), self.date)
        e.append(self._mk_t(text)); return e

    def _del(self, text):
        e = etree.Element(w("del"))
        e.set(w("id"), self._nid()); e.set(w("author"), self.author); e.set(w("date"), self.date)
        dr = etree.SubElement(e, w("r")); dt = etree.SubElement(dr, w("delText")); dt.text = text
        if text and (text[0] == " " or text[-1] == " "):
            dt.set(XS, "preserve")
        return e

    # ---------- tracked text edits (on document.xml) ----------
    def replace(self, old, new, label=""):
        """Tracked replace: del `old` + ins `new` (`new=""` => pure tracked deletion).
        Tries a single direct run first, then a run-spanning fallback (Word often splits a
        phrase across runs) with quote/space/dash normalization."""
        if self._replace_in_run(old, new):
            self.log.append(f"OK   {label or 'replace'}"); return True
        if self._replace_span(old, new):
            self.log.append(f"OK   {label or 'replace'} (span)"); return True
        self.log.append(f"MISS {label or 'replace'}  («{old[:40]}»)"); return False

    def _replace_in_run(self, old, new):
        for para in self._paras():
            for r in self._logical_children(para):
                if r.tag != w("r"):
                    continue
                txt = self._rtext(r)
                if old in txt:
                    if not self._pure_text_run(r):
                        continue   # run also holds a footnote ref / drawing / break -> don't flatten

                    i = txt.find(old); pre, suf = txt[:i], txt[i + len(old):]
                    repl = []
                    if pre: repl.append(self._mk_t_like(r, pre))
                    repl.append(self._del(old))
                    if new: repl.append(self._ins(new))
                    if suf: repl.append(self._mk_t_like(r, suf))
                    parent = r.getparent(); idx = list(parent).index(r); parent.remove(r)
                    for k, e in enumerate(repl): parent.insert(idx + k, e)
                    return True
        return False

    def _replace_span(self, old, new):
        for para in self._paras():
            for runs in self._run_groups(para):
                loc = self._locate_in_group(runs, old)
                if not loc:
                    continue
                ra, oa, rb, ob, texts, real = loc
                if not all(self._pure_text_run(r) for r in runs[ra:rb + 1]):
                    continue   # structural content in the span -> don't collapse; visible MISS
                pre, suf = texts[ra][:oa], texts[rb][ob:]
                repl = []
                if pre: repl.append(self._mk_t_like(runs[ra], pre))
                repl.append(self._del(real))
                if new: repl.append(self._ins(new))
                if suf: repl.append(self._mk_t_like(runs[rb], suf))
                # Runs in a group can each sit under a DIFFERENT real parent (sdt-transparent
                # grouping — see _logical_children): splice new nodes in at the first run's
                # own parent/position, then remove every original run from ITS OWN parent.
                # Any sdtContent left empty by this is harmless (Word tolerates it).
                first_parent = runs[ra].getparent()
                idx = list(first_parent).index(runs[ra])
                for r in runs[ra:rb + 1]:
                    p = r.getparent(); p.remove(r)
                for k, e in enumerate(repl): first_parent.insert(idx + k, e)
                return True
        return False

    def delete(self, text, label=""):
        """Tracked deletion of `text`."""
        return self.replace(text, "", label or "delete")

    def insert_after_text(self, prefix, text, label=""):
        """Insert `text` as a tracked insertion right after `prefix`. Single direct run
        first, then a run-spanning fallback (same normalization as replace)."""
        if self._insert_after_in_run(prefix, text):
            self.log.append(f"OK   {label or 'insert_after_text'}"); return True
        if self._insert_after_span(prefix, text):
            self.log.append(f"OK   {label or 'insert_after_text'} (span)"); return True
        self.log.append(f"MISS {label or 'insert_after_text'}  (prefix «{prefix[:40]}»)"); return False

    def _insert_after_in_run(self, prefix, text):
        for para in self._paras():
            for r in self._logical_children(para):
                if r.tag != w("r"):
                    continue
                txt = self._rtext(r)
                if prefix in txt:
                    if not self._pure_text_run(r):
                        continue   # run also holds a footnote ref / drawing / break -> don't flatten

                    i = txt.find(prefix) + len(prefix); pre, suf = txt[:i], txt[i:]
                    repl = [self._mk_t_like(r, pre), self._ins(text)]
                    if suf: repl.append(self._mk_t_like(r, suf))
                    parent = r.getparent(); idx = list(parent).index(r); parent.remove(r)
                    for k, e in enumerate(repl): parent.insert(idx + k, e)
                    return True
        return False

    def _insert_after_span(self, prefix, text):
        # Split only the LAST run of the prefix span at the insertion point; runs holding
        # earlier parts of the prefix stay untouched.
        for para in self._paras():
            for runs in self._run_groups(para):
                loc = self._locate_in_group(runs, prefix)
                if not loc:
                    continue
                _ra, _oa, rb, ob, texts, _real = loc
                if not self._pure_text_run(runs[rb]):
                    continue   # would drop structural content when split; visible MISS
                head, tail = texts[rb][:ob], texts[rb][ob:]
                repl = []
                if head: repl.append(self._mk_t_like(runs[rb], head))
                repl.append(self._ins(text))
                if tail: repl.append(self._mk_t_like(runs[rb], tail))
                parent = runs[rb].getparent()
                idx = list(parent).index(runs[rb]); parent.remove(runs[rb])
                for k, e in enumerate(repl): parent.insert(idx + k, e)
                return True
        return False

    def insert_after_ins(self, contains, text, label=""):
        """Insert a NEW tracked insertion right after the USER's w:ins run containing `contains`.
        Use when your anchor sits inside text the user already inserted (tracked), not a plain run."""
        for para in self._paras():
            for e in para.iter(w("ins")):   # iter, not findall: w:ins can nest in w:sdt
                if contains in "".join(t.text or "" for t in e.iter(w("t"))):
                    par = e.getparent(); par.insert(list(par).index(e) + 1, self._ins(text))
                    self.log.append(f"OK   {label or 'insert_after_ins'}"); return True
        self.log.append(f"MISS {label or 'insert_after_ins'}  (ins «{contains[:40]}»)"); return False

    def insert_paragraph_after(self, contains, text, label=""):
        """A NEW paragraph after the one containing `contains`, as a tracked insertion.

        insert_after_text / insert_after_ins add text INSIDE the paragraph they anchor on; three
        of them in a row give one long block, not three paragraphs. Use this whenever the new
        text is a paragraph of its own. The new paragraph's own mark is marked inserted, so
        rejecting removes both the text and the break it introduced.
        """
        for para in self._paras():
            if contains not in "".join(t.text or "" for t in para.iter(w("t"))):
                continue
            p = etree.Element(w("p"))
            src = para.find(w("pPr"))
            pPr = etree.SubElement(p, w("pPr"))
            if src is not None:
                for child in src:
                    if child.tag not in (w("rPr"),):
                        pPr.append(copy.deepcopy(child))
            rPr = etree.SubElement(pPr, w("rPr"))
            mark = etree.SubElement(rPr, w("ins"))
            mark.set(w("id"), self._nid()); mark.set(w("author"), self.author)
            mark.set(w("date"), self.date)
            p.append(self._ins(text))
            para.addnext(p)
            self.log.append(f"OK   {label or 'insert_paragraph_after'}"); return True
        self.log.append(f"MISS {label or 'insert_paragraph_after'}  («{contains[:40]}»)")
        return False

    def fix_in_ins(self, old, new, label=""):
        """Correct text INSIDE a w:ins, in place (no nested tracking needed).

        A single w:t first; then across the w:t's of the same w:ins, because Word splits a
        sentence into runs wherever it likes and a needle longer than a word rarely sits whole
        in one of them.
        """
        for para in self._paras():
            for e in para.iter(w("ins")):   # iter, not findall: w:ins can nest in w:sdt
                for t in e.iter(w("t")):
                    if t.text and old in t.text:
                        t.text = t.text.replace(old, new)
                        self.log.append(f"OK   {label or 'fix_in_ins'} (in-ins)"); return True
        for para in self._paras():
            for e in para.iter(w("ins")):
                ts = list(e.iter(w("t")))
                intreg = "".join(t.text or "" for t in ts)
                if old not in intreg:
                    continue
                start = intreg.find(old); end = start + len(old)
                poz = 0
                for t in ts:
                    txt = t.text or ""
                    a, b = poz, poz + len(txt); poz = b
                    if b <= start or a >= end:
                        continue
                    cap = txt[:start - a] if a < start else ""
                    coada = txt[end - a:] if b > end else ""
                    t.text = (cap + new + coada) if a <= start else coada
                    t.set(XS, "preserve")
                self.log.append(f"OK   {label or 'fix_in_ins'} (span)"); return True
        self.log.append(f"MISS {label or 'fix_in_ins'}"); return False

    def set_heading(self, needle, level=3, label=""):
        """Change the paragraph whose full text == `needle` to HeadingN (tracked via w:pPrChange),
        dropping list numbering. `needle` must match the paragraph's stripped text exactly."""
        for para in self._paras():
            if "".join(t.text or "" for t in para.iter(w("t"))).strip() == needle:
                ppr = para.find(w("pPr"))
                if ppr is None:
                    ppr = etree.Element(w("pPr")); para.insert(0, ppr)
                old_ppr = copy.deepcopy(ppr)
                ps = ppr.find(w("pStyle"))
                if ps is None:
                    ps = etree.Element(w("pStyle")); ppr.insert(0, ps)
                ps.set(w("val"), f"Heading{level}")
                np = ppr.find(w("numPr"))
                if np is not None: ppr.remove(np)
                ch = etree.SubElement(ppr, w("pPrChange"))
                ch.set(w("id"), self._nid()); ch.set(w("author"), self.author); ch.set(w("date"), self.date)
                inner = etree.SubElement(ch, w("pPr"))
                for el in old_ppr:
                    if el.tag != w("pPrChange"): inner.append(copy.deepcopy(el))
                self.log.append(f"OK   {label or 'set_heading'}"); return True
        self.log.append(f"MISS {label or 'set_heading'}  («{needle[:40]}»)"); return False

    # ---------- new anchored comments ----------
    def _ensure_comment_parts(self, threaded=False):
        """Create word/comments.xml + word/commentsExtended.xml (with content-type
        overrides and document rels) when the document has no comments yet. Pass
        threaded=True (reply_to_comment does) to also bootstrap word/commentsIds.xml +
        word/people.xml — required for threaded replies, not for top-level comments,
        so add_comment on its own leaves them out (Word opens fine without them)."""
        wdir = self.root / "word"
        NSMAP = {"w": W, "w14": W14, "w15": W15}
        made = []
        if not (wdir / "comments.xml").exists() and "comments.xml" not in self._trees:
            self._trees["comments.xml"] = etree.ElementTree(etree.Element(w("comments"), nsmap=NSMAP))
            made.append(("comments.xml",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
                         "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"))
        if not (wdir / "commentsExtended.xml").exists() and "commentsExtended.xml" not in self._trees:
            self._trees["commentsExtended.xml"] = etree.ElementTree(etree.Element(w15("commentsEx"), nsmap=NSMAP))
            made.append(("commentsExtended.xml",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
                         "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"))
        if threaded:
            if not (wdir / "commentsIds.xml").exists() and "commentsIds.xml" not in self._trees:
                self._trees["commentsIds.xml"] = etree.ElementTree(
                    etree.Element(w16("commentsIds"), nsmap={"w16cid": W16}))
                made.append(("commentsIds.xml",
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml",
                             "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds"))
            if not (wdir / "people.xml").exists() and "people.xml" not in self._trees:
                self._trees["people.xml"] = etree.ElementTree(etree.Element(w15("people"), nsmap={"w15": W15}))
                made.append(("people.xml",
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml",
                             "http://schemas.microsoft.com/office/2011/relationships/people"))
        if not made:
            return
        CT = "http://schemas.openxmlformats.org/package/2006/content-types"
        ct_path = self.root / "[Content_Types].xml"
        ct = etree.parse(str(ct_path), _PARSER)
        for fn, ctype, _ in made:
            if not any(o.get("PartName") == f"/word/{fn}" for o in ct.getroot().findall(f"{{{CT}}}Override")):
                o = etree.SubElement(ct.getroot(), f"{{{CT}}}Override")
                o.set("PartName", f"/word/{fn}"); o.set("ContentType", ctype)
        ct.write(str(ct_path), xml_declaration=True, encoding="UTF-8", standalone=True)
        R = "http://schemas.openxmlformats.org/package/2006/relationships"
        rels_path = wdir / "_rels" / "document.xml.rels"
        rels = etree.parse(str(rels_path), _PARSER)
        nums = [int(i[3:]) for i in (r_.get("Id") or "" for r_ in rels.getroot())
                if i.startswith("rId") and i[3:].isdigit()]
        nxt = max(nums or [0])
        for k, (fn, _, rtype) in enumerate(made, 1):
            r_ = etree.SubElement(rels.getroot(), f"{{{R}}}Relationship")
            r_.set("Id", f"rId{nxt + k}"); r_.set("Type", rtype); r_.set("Target", fn)
        rels.write(str(rels_path), xml_declaration=True, encoding="UTF-8", standalone=True)

    def add_comment(self, anchor, text, label="", occurrence="first"):
        """New top-level comment (author = self.author) anchored on the PARAGRAPH that
        contains `anchor` (same quote/space/dash normalization as replace). Paragraph-level
        markers — the comment covers the whole paragraph, which is what a reviewer comment
        wants. TOC paragraphs are skipped when a non-TOC match exists; occurrence='last'
        picks the last match (e.g. when the anchor is a heading that also appears in the
        TOC as plain text). Creates the comment parts if the document has none."""
        matches = [p for p in self._paras() if _norm(anchor) in _norm(self._para_text(p))]
        def is_toc(p):
            ps = p.find(w("pPr") + "/" + w("pStyle"))
            return ps is not None and (ps.get(w("val")) or "").upper().startswith("TOC")
        non_toc = [p for p in matches if not is_toc(p)]
        if non_toc:
            matches = non_toc
        if not matches:
            self.log.append(f"MISS {label or 'comment'}  («{anchor[:40]}»)"); return False
        para = matches[-1] if occurrence == "last" else matches[0]

        self._ensure_comment_parts()
        cr = self._tree("comments.xml").getroot()
        existing = [int(c.get(w("id"))) for c in cr.findall(w("comment"))
                    if (c.get(w("id")) or "").lstrip("-").isdigit()]
        if existing:
            self._id = max(self._id, max(existing))
        cid = self._nid()
        para_id = f"{(0x32000000 + int(cid)) & 0xFFFFFFFF:08X}"

        # body: paragraph-level markers + reference run
        ppr = para.find(w("pPr"))
        start = list(para).index(ppr) + 1 if ppr is not None else 0
        cs = etree.Element(w("commentRangeStart")); cs.set(w("id"), cid)
        para.insert(start, cs)
        ce = etree.Element(w("commentRangeEnd")); ce.set(w("id"), cid)
        para.append(ce)
        rr = etree.Element(w("r")); rpr = etree.SubElement(rr, w("rPr"))
        rs = etree.SubElement(rpr, w("rStyle")); rs.set(w("val"), "CommentReference")
        cref = etree.SubElement(rr, w("commentReference")); cref.set(w("id"), cid)
        para.append(rr)

        # comments.xml: the comment body
        c = etree.SubElement(cr, w("comment"))
        c.set(w("id"), cid); c.set(w("author"), self.author)
        c.set(w("date"), self.date); c.set(w("initials"), self.initials)
        p = etree.SubElement(c, w("p")); p.set(w14("paraId"), para_id)
        pp = etree.SubElement(p, w("pPr"))
        ps = etree.SubElement(pp, w("pStyle")); ps.set(w("val"), "CommentText")
        r1 = etree.SubElement(p, w("r")); r1p = etree.SubElement(r1, w("rPr"))
        r1s = etree.SubElement(r1p, w("rStyle")); r1s.set(w("val"), "CommentReference")
        etree.SubElement(r1, w("annotationRef"))
        r2 = etree.SubElement(p, w("r"))
        t = etree.SubElement(r2, w("t")); t.text = text

        # commentsExtended.xml: top-level (no parent), not done
        er = self._tree("commentsExtended.xml").getroot()
        cex = etree.SubElement(er, w15("commentEx"))
        cex.set(w15("paraId"), para_id); cex.set(w15("done"), "0")

        self.log.append(f"OK   {label or 'comment'} (id {cid})"); return True

    def delete_comment(self, comment_id, label=""):
        """Remove a comment entirely: the w:comment in comments.xml, its sidecar entries in
        commentsExtended / commentsIds / commentsExtensible (matched by paraId, namespace-
        agnostic), and its body anchors (commentRangeStart/End + the commentReference run).
        Use to clear a resolved review comment. Replies are not auto-removed — delete them
        first if the comment is a thread parent."""
        cid = str(comment_id)
        cr = self._tree("comments.xml").getroot()
        target = None
        for c in cr.findall(w("comment")):
            if c.get(w("id")) == cid:
                target = c; break
        if target is None:
            self.log.append(f"MISS {label or 'delete_comment'}  (#{cid} not found)"); return False
        para_ids = {p.get(w14("paraId")) for p in target.findall(w("p")) if p.get(w14("paraId"))}
        cr.remove(target)

        # sidecar parts keyed by paraId (attribute localname == 'paraId', any namespace)
        for fn in ("commentsExtended.xml", "commentsIds.xml", "commentsExtensible.xml"):
            if not (self.root / "word" / fn).exists():
                continue
            root = self._tree(fn).getroot()
            for el in list(root):
                pid = next((v for k, v in el.attrib.items()
                            if etree.QName(k).localname == "paraId"), None)
                if pid in para_ids:
                    root.remove(el)

        # body anchors
        for tag in ("commentRangeStart", "commentRangeEnd"):
            for e in list(self.body.iter(w(tag))):
                if e.get(w("id")) == cid:
                    e.getparent().remove(e)
        for r in list(self.body.iter(w("r"))):
            if any(cf.get(w("id")) == cid for cf in r.findall(w("commentReference"))):
                r.getparent().remove(r)
        self.log.append(f"OK   {label or 'delete_comment'} (#{cid})"); return True

    # ---------- threaded comment replies ----------
    def reply_to_comment(self, parent_id, text, label=""):
        """Add a threaded reply (author = self.author) under existing comment `parent_id`.
        Touches comments.xml, commentsExtended.xml, commentsIds.xml, people.xml + the body anchor."""
        self._ensure_comment_parts(threaded=True)
        cr = self._tree("comments.xml").getroot()
        er = self._tree("commentsExtended.xml").getroot()
        ir = self._tree("commentsIds.xml").getroot()
        ppl = self._tree("people.xml").getroot()

        # register author once
        if not any(p.get(w15("author")) == self.author for p in ppl.findall(w15("person"))):
            pe = etree.SubElement(ppl, w15("person")); pe.set(w15("author"), self.author)
            pi = etree.SubElement(pe, w15("presenceInfo"))
            pi.set(w15("providerId"), "None"); pi.set(w15("userId"), self.author)

        def parent_paraid(pid):
            for c in cr.findall(w("comment")):
                if c.get(w("id")) == str(pid):
                    return c.find(w("p")).get(w14("paraId"))
        def in_body(tag, idv):
            for e in self.body.iter(w(tag)):
                if e.get(w("id")) == str(idv): return e
        def ref_run(idv):
            for r in self.body.iter(w("r")):
                for cf in r.findall(w("commentReference")):
                    if cf.get(w("id")) == str(idv): return r

        cid = self._nid()
        para_id = f"{(0x30000000 + int(cid)) & 0xFFFFFFFF:08X}"
        dur = f"{(0x31000000 + int(cid)) & 0xFFFFFFFF:08X}"
        ppara = parent_paraid(parent_id)
        if ppara is None:
            self.log.append(f"MISS {label or 'reply'} -> comment #{parent_id} not found"); return False

        # comments.xml: the reply comment
        c = etree.SubElement(cr, w("comment"))
        c.set(w("id"), cid); c.set(w("author"), self.author); c.set(w("date"), self.date); c.set(w("initials"), self.initials)
        p = etree.SubElement(c, w("p")); p.set(w14("paraId"), para_id); p.set(w14("textId"), "77777777")
        pp = etree.SubElement(p, w("pPr")); ps = etree.SubElement(pp, w("pStyle")); ps.set(w("val"), "CommentText")
        r1 = etree.SubElement(p, w("r")); rp = etree.SubElement(r1, w("rPr"))
        rsx = etree.SubElement(rp, w("rStyle")); rsx.set(w("val"), "CommentReference"); etree.SubElement(r1, w("annotationRef"))
        r2 = etree.SubElement(p, w("r")); t = etree.SubElement(r2, w("t")); t.text = text
        # commentsExtended.xml: link reply -> parent (this is what makes it threaded)
        ce = etree.SubElement(er, w15("commentEx"))
        ce.set(w15("paraId"), para_id); ce.set(w15("paraIdParent"), ppara); ce.set(w15("done"), "0")
        # commentsIds.xml
        ci = etree.SubElement(ir, w16("commentId")); ci.set(w16("paraId"), para_id); ci.set(w16("durableId"), dur)

        # body anchor: reuse the parent's comment range
        ps_ = in_body("commentRangeStart", parent_id); pe_ = in_body("commentRangeEnd", parent_id); pr_ = ref_run(parent_id)
        if ps_ is None or pe_ is None or pr_ is None:
            self.log.append(f"MISS {label or 'reply'} -> #{parent_id} body anchor missing"); return False
        cs = etree.Element(w("commentRangeStart")); cs.set(w("id"), cid)
        ps_.getparent().insert(ps_.getparent().index(ps_) + 1, cs)
        ce2 = etree.Element(w("commentRangeEnd")); ce2.set(w("id"), cid)
        pe_.getparent().insert(pe_.getparent().index(pe_), ce2)
        rr = etree.Element(w("r")); rpr = etree.SubElement(rr, w("rPr"))
        rs = etree.SubElement(rpr, w("rStyle")); rs.set(w("val"), "CommentReference")
        cref = etree.SubElement(rr, w("commentReference")); cref.set(w("id"), cid)
        pr_.getparent().insert(pr_.getparent().index(pr_) + 1, rr)
        self.log.append(f"OK   {label or 'reply'} -> #{parent_id} (id {cid})"); return True

    # ---------- footnotes ----------
    def add_footnote(self, anchor_text, footnote_text, label=""):
        """Insert a tracked footnote. A new footnote (FootnoteText) is added to footnotes.xml with
        `footnote_text`; a footnoteReference run (wrapped in w:ins so it's tracked) is placed right
        after `anchor_text` in the body. `footnote_text` is plain text (one paragraph)."""
        ft = self._tree("footnotes.xml").getroot()
        existing = [int(f.get(w("id"))) for f in ft.findall(w("footnote")) if f.get(w("id")) and f.get(w("id")).lstrip("-").isdigit()]
        fid = str((max(existing) + 1) if existing else 1)
        # footnotes.xml entry
        fn = etree.SubElement(ft, w("footnote")); fn.set(w("id"), fid)
        p = etree.SubElement(fn, w("p"))
        pp = etree.SubElement(p, w("pPr")); ps = etree.SubElement(pp, w("pStyle")); ps.set(w("val"), "FootnoteText")
        rref = etree.SubElement(p, w("r")); rpr = etree.SubElement(rref, w("rPr"))
        rs = etree.SubElement(rpr, w("rStyle")); rs.set(w("val"), "FootnoteReference")
        etree.SubElement(rref, w("footnoteRef"))
        rt = etree.SubElement(p, w("r")); t = etree.SubElement(rt, w("t")); t.text = " " + footnote_text
        t.set(XS, "preserve")
        # body: tracked reference run right after anchor_text. Runurile se caută și în w:ins —
        # o ancoră dintr-o inserare a noastră e la fel de bună, dar acolo referința intră
        # direct în w:ins-ul existent: un w:ins în alt w:ins nu e valid.
        for para in self._paras():
            gazde = [(para, r) for r in para.findall(w("r"))]
            gazde += [(ins_, r) for ins_ in para.findall(w("ins")) for r in ins_.findall(w("r"))]
            for gazda, r in gazde:
                txt = self._rtext(r)
                if anchor_text in txt:
                    i = txt.find(anchor_text) + len(anchor_text); pre, suf = txt[:i], txt[i:]
                    refrun = etree.Element(w("r")); rp2 = etree.SubElement(refrun, w("rPr"))
                    rs2 = etree.SubElement(rp2, w("rStyle")); rs2.set(w("val"), "FootnoteReference")
                    fr = etree.SubElement(refrun, w("footnoteReference")); fr.set(w("id"), fid)
                    if gazda is para:
                        nod = etree.Element(w("ins"))
                        nod.set(w("id"), self._nid()); nod.set(w("author"), self.author)
                        nod.set(w("date"), self.date)
                        nod.append(refrun)
                    else:
                        nod = refrun
                    repl = [self._mk_t(pre), nod]
                    if suf: repl.append(self._mk_t(suf))
                    idx = list(gazda).index(r); gazda.remove(r)
                    for k, e in enumerate(repl): gazda.insert(idx + k, e)
                    self.log.append(f"OK   {label or 'footnote'} (id {fid})"); return True
        # roll back the footnotes.xml entry if anchor not found
        ft.remove(fn)
        self.log.append(f"MISS {label or 'footnote'}  (anchor «{anchor_text[:40]}»)"); return False

    # ---------- save ----------
    def save(self):
        """Write every parsed tree back to disk."""
        for fn, tree in self._trees.items():
            tree.write(str(self.root / "word" / fn), xml_declaration=True, encoding="UTF-8", standalone=True)

    def report(self):
        ok = sum(1 for l in self.log if l.startswith("OK"))
        miss = sum(1 for l in self.log if l.startswith("MISS"))
        print("\n".join(self.log))
        print(f"\n== {ok} applied, {miss} missed ==")
        return miss == 0
