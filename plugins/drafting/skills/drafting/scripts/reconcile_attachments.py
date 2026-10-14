# -*- coding: utf-8 -*-
"""
reconcile_attachments.py — collapse duplicate PDF attachments on the same item, keeping
the *editable* copy (text-native, or an OCR-overlaid scan) and never losing annotations.

Priority for the KEEPER (the user's rule): a text-extractable copy beats an image-only
scan; among equals, prefer the one carrying annotations, then the more complete text
(higher avg chars), then the lower itemID.

The one inviolable guard: **annotations live in PDF-point coordinates tied to one
rendition.** Two different-content files of the same paper have different layouts, so an
annotation cannot be moved between them — its rectangles would land on the wrong text. So:

  - byte-identical duplicates  -> collapse: keep one, move any annotations the losers hold
    that the keeper lacks (order-independent bbox signature), trash the loser file.
  - a loser with ZERO annotations, when a better keeper exists -> trash it.
  - a loser WITH annotations and DIFFERENT bytes -> NEVER trashed here. Reported as:
       * "scan-annotated"  -> OCR candidate: run the ask-first ocr_overlay flow so the scan
         itself becomes the editable keeper (SKILL.md "OCR overlay"), don't discard it;
       * "rival-annotated" -> two annotated renditions; a human decides.

Also trashes PHANTOM attachments (imported_file, no storageHash, no storage/<key> folder,
no annotations) — the stubs that cause Zotero's "Cannot change attachment linkMode" sync
error. See references/zotero-schema.md "Phantom attachments".

Writes go through zot.write_session (Zotero CLOSED, auto-backup). Trash = insert into
deletedItems (reversible), not hard delete.

CLI:
  python audit_library.py --json findings.json      # produce input first
  python reconcile_attachments.py findings.json                 # dry-run report
  python reconcile_attachments.py findings.json --apply         # collapse + trash phantoms
  python reconcile_attachments.py findings.json --apply --keep-phantoms   # skip phantom trash
"""
import sqlite3, sys, os, json, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zot

ZSTOR = os.path.join(zot.ZDIR, "storage")


def _parent_of(c, att_id):
    r = c.execute("SELECT parentItemID FROM itemAttachments WHERE itemID=?", (att_id,)).fetchone()
    return r[0] if r and r[0] else att_id


def ann_sig(typ, position):
    """Order-independent signature: bounding box over ALL rects. Multi-line highlights
    carry one rect per line and importers reorder them, so keying on rects[0] gives false
    mismatches (learned the hard way in the Mendeley migration)."""
    p = json.loads(position) if isinstance(position, str) else position
    rs = p.get("rects") or [[0, 0, 0, 0]]
    return (typ, p.get("pageIndex"), len(rs),
            round(min(r[0] for r in rs)), round(min(r[1] for r in rs)),
            round(max(r[2] for r in rs)), round(max(r[3] for r in rs)))


def choose_keeper(atts):
    """atts: list of dicts with is_scan, annotations, avg_chars, att. Higher = better."""
    def rank(a):
        return (0 if a.get("is_scan") else 1,          # text-native beats scan
                1 if a["annotations"] else 0,           # then annotated
                a.get("avg_chars", 0),                  # then more complete text
                -a["att"])                              # then lower id (stable)
    return max(atts, key=rank)


def _same_rendition(a, b):
    """Two files are the same document's same rendition (a true redundant copy) when their
    page counts match within 1. A materially different page count means a DIFFERENT document
    (chapter vs whole book, a longer consolidated version, or a mis-filed unrelated file —
    the Denning/Xia and Ordinul-65p-vs-126p traps). Never auto-trash across that line."""
    pa, pb = a.get("pages"), b.get("pages")
    if pa is None or pb is None:
        return False
    return abs(pa - pb) <= 1


def plan(findings):
    dups = findings["dup_attachments"]
    collapse = []       # (keeper, loser)         -- byte-identical: safe to collapse
    trash_empty = []    # (keeper, loser)         -- 0 annotations, SAME rendition: safe to trash
    ocr_candidates = [] # (parent, scan, keeper)  -- scan carrying annotations -> OCR, don't discard
    rival = []          # (parent, loser, keeper) -- annotated different rendition -> human
    review_diff = []    # (parent, loser, keeper) -- different page count -> maybe another document
    for g in dups:
        atts = g["attachments"]
        keeper = choose_keeper(atts)
        for a in atts:
            if a["att"] == keeper["att"]:
                continue
            same_bytes = a.get("sha1") and a["sha1"] == keeper.get("sha1")
            if same_bytes:
                collapse.append((keeper, a))
            elif a["annotations"] and a.get("is_scan"):
                ocr_candidates.append((g["parent"], a, keeper))
            elif a["annotations"]:
                rival.append((g["parent"], a, keeper))
            elif _same_rendition(a, keeper):
                trash_empty.append((keeper, a))     # 0 annotations, same paper/rendition
            else:
                review_diff.append((g["parent"], a, keeper))  # different length -> look first
    return collapse, trash_empty, ocr_candidates, rival, review_diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("findings", help="JSON from audit_library.py --json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep-phantoms", action="store_true", help="don't trash phantom stubs")
    args = ap.parse_args()
    findings = json.load(open(args.findings, encoding="utf-8"))
    collapse, trash_empty, ocr_candidates, rival, review_diff = plan(findings)
    phantoms = [] if args.keep_phantoms else findings.get("phantom_attachments", [])

    # titles for transparency ("look before you trash")
    cro = zot._ro().cursor()
    def title(iid):
        r = cro.execute("""SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID
            JOIN fields f ON f.fieldID=d.fieldID WHERE d.itemID=? AND f.fieldName IN ('title','nameOfAct') LIMIT 1""", (iid,)).fetchone()
        return (r[0] if r else "?")

    print("=== PLAN RECONCILIERE ===")
    print(f"  byte-identice de colapsat        : {len(collapse)}")
    print(f"  copii fara adnotari de trimis la cos: {len(trash_empty)}")
    for keeper, loser in trash_empty:
        print(f"    trash att {loser['att']} ({loser.get('pages','?')}p, avg={loser.get('avg_chars','?')}) "
              f"keeper att {keeper['att']} ({keeper.get('pages','?')}p, avg={keeper.get('avg_chars','?')})  {title(_parent_of(cro, loser['att']))[:46]}")
    print(f"  stub-uri fantoma la cos (sparg sync): {len(phantoms)}")
    print(f"  -- de decis, NEATINSE --")
    print(f"  pagini diferite (poate alt document): {len(review_diff)}")
    for parent, att, keeper in review_diff:
        print(f"    DIFF item {parent}: att {att['att']} ({att.get('pages','?')}p) vs keeper {keeper['att']} ({keeper.get('pages','?')}p)  {title(parent)[:44]}")
    print(f"  scanuri adnotate (candidat OCR)  : {len(ocr_candidates)}")
    for parent, att, keeper in ocr_candidates[:12]:
        print(f"    OCR? item {parent}: scan att {att['att']} ({att['annotations']} adn) vs keeper {keeper['att']}")
    print(f"  rivali text adnotati (uman)      : {len(rival)}")
    for parent, att, keeper in rival[:12]:
        print(f"    RIVAL item {parent}: att {att['att']} ({att['annotations']} adn) vs keeper {keeper['att']} ({keeper['annotations']} adn)")

    if not args.apply:
        print("\n[DRY RUN] --apply pentru a colapsa + trimite la cos.")
        return

    # verify Zotero closed handled by write_session; hashing/scan already done in audit
    con_ro = zot._ro().cursor()
    moved = 0
    with zot.write_session("reconcile-attachments") as w:
        # 1. collapse byte-identical: move unique annotations, then trash loser
        for keeper, loser in collapse:
            keep_sigs = set()
            for typ, pos in w.execute("SELECT type,position FROM itemAnnotations WHERE parentItemID=?", (keeper["att"],)).fetchall():
                try: keep_sigs.add(ann_sig(typ, pos))
                except Exception: pass
            for annid, typ, pos in w.execute("SELECT itemID,type,position FROM itemAnnotations WHERE parentItemID=?", (loser["att"],)).fetchall():
                try: s = ann_sig(typ, pos)
                except Exception: continue
                if s not in keep_sigs:
                    w.execute("UPDATE itemAnnotations SET parentItemID=? WHERE itemID=?", (keeper["att"], annid))
                    keep_sigs.add(s); moved += 1
            # trash the loser attachment (+ any annotation rows still on it = duplicates)
            w.execute("INSERT OR IGNORE INTO deletedItems (itemID,dateDeleted) VALUES (?,?)", (loser["att"], zot.now()))
            zot.touch(w, loser["att"])
        # 2. trash zero-annotation losers
        for keeper, loser in trash_empty:
            w.execute("INSERT OR IGNORE INTO deletedItems (itemID,dateDeleted) VALUES (?,?)", (loser["att"], zot.now()))
            zot.touch(w, loser["att"])
        # 3. trash phantom stubs — but NEVER one that carries annotations. A stub with no
        # local file yet WITH annotations means the file (and its highlights) exist on another
        # device and only the metadata synced here; trashing it would lose those annotations.
        # Leave it for the file to download, and surface it.
        trashed_ph, kept_ph = 0, []
        for p in phantoms:
            n = w.execute("SELECT COUNT(*) FROM itemAnnotations WHERE parentItemID=?", (p["att"],)).fetchone()[0]
            if n:
                kept_ph.append((p["att"], n))
                continue
            w.execute("INSERT OR IGNORE INTO deletedItems (itemID,dateDeleted) VALUES (?,?)", (p["att"], zot.now()))
            zot.touch(w, p["att"]); trashed_ph += 1
        print(f"Colapsate {len(collapse)} (mutate {moved} adnotari), "
              f"{len(trash_empty)} copii goale la cos, {trashed_ph} fantome la cos.")
        if kept_ph:
            print(f"!! {len(kept_ph)} fantome PASTRATE (au adnotari sincronizate — fisierul e pe alt "
                  f"device): {[a for a,_ in kept_ph]}. Lasa-le sa se descarce; nu le sterge orb.")
    print("Scanurile adnotate si rivalii text NU au fost atinsi — vezi lista de mai sus.")


if __name__ == "__main__":
    main()
