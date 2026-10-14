# -*- coding: utf-8 -*-
"""
audit_library.py — read-only health check of a Zotero library.

Runs safely while Zotero is OPEN (through zot._ro: immutable when the -wal journal is empty, a copy with the journal applied when it is not). Reports, and writes a
machine-readable findings file for reconcile_attachments.py to consume:

  A. Referential integrity — orphan rows across every child table.
  B. Attachment files — stored files missing from disk, and *phantom* imported_file rows
     (no storageHash + no storage/<key> folder). Those phantoms are the usual cause of the
     Zotero sync error "Cannot change attachment linkMode": a local imported-file stub that
     never had bytes here cannot be reconciled against the server's copy. They are NOT the
     user's own lost files — they arrive via sync from another device. See references/
     zotero-schema.md "Phantom attachments". Also *linked* files (linkMode 2) whose path —
     absolute, or `attachments:` under baseAttachmentPath — no longer exists, listed with the
     path: a move to another computer breaks exactly these. Web links (linkMode 3) are counted
     apart and kept out of the PDF total.
  C. Items with 2+ *present* PDF files — the real duplicate-attachment set to reconcile
     (classified text-native vs image-scan via ocr_overlay.detect_scan).
  D. Metadata smells — items without a title, placeholder titles ("No Title", CJK junk),
     and identifiers (DOI/ISBN) stamped on many unrelated items (Mendeley's junk-DOI bug).
  E. Trash count.

CLI:
  python audit_library.py                 # full report to stdout
  python audit_library.py --json out.json # + write findings for reconcile step
  python audit_library.py --no-hash       # skip content hashing / scan detection (fast)
"""
import sqlite3, sys, os, json, hashlib, re, argparse
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zot
try:
    import ocr_overlay
    HAVE_OCR = True
except Exception:
    HAVE_OCR = False

DB = os.path.join(zot.ZDIR, "zotero.sqlite")
ZSTOR = os.path.join(zot.ZDIR, "storage")
NOT_TRASHED = "itemID NOT IN (SELECT itemID FROM deletedItems)"


def _ro():
    # zot._ro() citeste si jurnalul -wal cand nu e gol; immutable=1 singur l-ar ignora tacut
    return zot._ro().cursor()


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def _stored_path(c, att_id):
    r = c.execute("SELECT i.key, ia.path FROM itemAttachments ia JOIN items i ON i.itemID=ia.itemID "
                  "WHERE ia.itemID=?", (att_id,)).fetchone()
    if not r or not r[1] or not r[1].startswith("storage:"):
        return None
    return os.path.join(ZSTOR, r[0], r[1][len("storage:"):])


def integrity(c):
    checks = {
        "itemData_orphan": "SELECT COUNT(*) FROM itemData WHERE itemID NOT IN (SELECT itemID FROM items)",
        "itemCreators_orphan": "SELECT COUNT(*) FROM itemCreators WHERE itemID NOT IN (SELECT itemID FROM items)",
        "itemTags_orphan": "SELECT COUNT(*) FROM itemTags WHERE itemID NOT IN (SELECT itemID FROM items)",
        "itemNotes_orphan": "SELECT COUNT(*) FROM itemNotes WHERE itemID NOT IN (SELECT itemID FROM items)",
        "note_parent_missing": "SELECT COUNT(*) FROM itemNotes WHERE parentItemID IS NOT NULL AND parentItemID NOT IN (SELECT itemID FROM items)",
        "attachment_orphan": "SELECT COUNT(*) FROM itemAttachments WHERE itemID NOT IN (SELECT itemID FROM items)",
        "attachment_parent_missing": "SELECT COUNT(*) FROM itemAttachments WHERE parentItemID IS NOT NULL AND parentItemID NOT IN (SELECT itemID FROM items)",
        "annotation_orphan": "SELECT COUNT(*) FROM itemAnnotations WHERE itemID NOT IN (SELECT itemID FROM items)",
        "annotation_parent_missing": "SELECT COUNT(*) FROM itemAnnotations WHERE parentItemID NOT IN (SELECT itemID FROM items)",
        "annotation_empty_position": "SELECT COUNT(*) FROM itemAnnotations WHERE position IS NULL OR position=''",
        "collectionItems_orphan_item": "SELECT COUNT(*) FROM collectionItems WHERE itemID NOT IN (SELECT itemID FROM items)",
        "collectionItems_orphan_coll": "SELECT COUNT(*) FROM collectionItems WHERE collectionID NOT IN (SELECT collectionID FROM collections)",
        "collection_parent_missing": "SELECT COUNT(*) FROM collections WHERE parentCollectionID IS NOT NULL AND parentCollectionID NOT IN (SELECT collectionID FROM collections)",
        "itemRelations_orphan": "SELECT COUNT(*) FROM itemRelations WHERE itemID NOT IN (SELECT itemID FROM items)",
    }
    return {k: c.execute(q).fetchone()[0] for k, q in checks.items()}


LINKED_FILE, LINKED_URL = 2, 3   # itemAttachments.linkMode


def base_attachment_path():
    """`extensions.zotero.baseAttachmentPath` din prefs.js al profilului, sau None.

    E radacina fata de care se rezolva caile `attachments:<relativ>` ale fisierelor legate.
    Directorul profilelor se cauta in locurile standard; ZOTERO_PROFILE_DIR il fixeaza."""
    import glob, json as _json  # noqa: PLC0415
    radacini = [os.environ.get("ZOTERO_PROFILE_DIR", "")] + [
        os.path.join(os.environ.get("APPDATA", ""), "Zotero", "Zotero", "Profiles", "*"),
        os.path.expanduser("~/Library/Application Support/Zotero/Profiles/*"),
        os.path.expanduser("~/.zotero/zotero/*")]
    for r in radacini:
        for prefs in glob.glob(os.path.join(r, "prefs.js")) if r else []:
            for linie in open(prefs, encoding="utf-8", errors="replace"):
                m = re.match(r'user_pref\("extensions\.zotero\.baseAttachmentPath",\s*(".*")\);', linie)
                if m:
                    return _json.loads(m.group(1))
    return None


def linked_path(path, base):
    """Calea pe disc a unui fisier legat (linkMode 2): absoluta ca atare, iar `attachments:`
    relativ la `baseAttachmentPath`. None cand calea e relativa si radacina nu e setata."""
    if path.startswith("attachments:"):
        return os.path.join(base, path[len("attachments:"):]) if base else None
    return path


def attachments(c, base=None):
    """PDF-urile atasate, pe trei feluri, fiindca fiecare lipseste din alt motiv:

    - importate (`storage:`): fisierul sta in storage/<cheie>; lipsa lui e sync-abila sau fantoma;
    - legate (linkMode 2): fisierul sta in afara Zotero, la o cale scrisa in baza. O mutare pe
      alt calculator le rupe pe toate cele cu cale absoluta, iar inainte nu se verificau deloc:
      auditul de dupa mutarea din 5 octombrie 2026 a raportat 19 lipsa fara sa numeasca vreunul;
    - legaturi web (linkMode 3): n-au fisier prin natura lor, deci nu intra in numaratoarea PDF."""
    rows = c.execute(f"""SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.path, ia.storageHash, i.key, i.dateAdded
        FROM itemAttachments ia JOIN items i ON i.itemID=ia.itemID
        WHERE ia.contentType='application/pdf' AND ia.parentItemID IS NOT NULL
          AND ia.{NOT_TRASHED} AND ia.parentItemID NOT IN (SELECT itemID FROM deletedItems)""").fetchall()
    present, missing_with_hash, phantom, linked_missing, web = 0, [], [], [], 0
    for aid, parent, lm, path, shash, key, dadd in rows:
        if lm == LINKED_URL:
            web += 1
            continue
        if lm == LINKED_FILE and path and not path.startswith("storage:"):
            fp = linked_path(path, base)
            if fp and os.path.exists(fp):
                present += 1
            else:
                linked_missing.append({"att": aid, "parent": parent, "key": key, "path": path})
            continue
        if not path or not path.startswith("storage:"):
            continue
        fp = os.path.join(ZSTOR, key, path[len("storage:"):])
        if os.path.exists(fp):
            present += 1
        elif shash or os.path.isdir(os.path.join(ZSTOR, key)):
            missing_with_hash.append(aid)          # sync may still fetch it
        else:
            phantom.append({"att": aid, "parent": parent, "linkMode": lm, "dateAdded": str(dadd)[:10]})
    return len(rows) - web, present, missing_with_hash, phantom, linked_missing, web


def dup_attachments(c, do_hash=True):
    """Items with 2+ PDF files present on disk. Classify each present file."""
    groups = c.execute(f"""SELECT ia.parentItemID FROM itemAttachments ia
        WHERE ia.contentType='application/pdf' AND ia.parentItemID IS NOT NULL AND ia.{NOT_TRASHED}
          AND ia.parentItemID NOT IN (SELECT itemID FROM deletedItems)
        GROUP BY ia.parentItemID HAVING COUNT(*)>=2""").fetchall()
    out = []
    for (parent,) in groups:
        atts = [a for (a,) in c.execute(
            f"SELECT ia.itemID FROM itemAttachments ia WHERE ia.parentItemID=? "
            f"AND ia.contentType='application/pdf' AND ia.{NOT_TRASHED}", (parent,)).fetchall()]
        present = []
        for a in atts:
            p = _stored_path(c, a)
            if not p or not os.path.exists(p):
                continue
            ann = c.execute("SELECT COUNT(*) FROM itemAnnotations WHERE parentItemID=?", (a,)).fetchone()[0]
            info = {"att": a, "path": p, "annotations": ann}
            if do_hash and HAVE_OCR:
                is_scan, pages, avg = ocr_overlay.detect_scan(p)
                info.update(is_scan=is_scan, pages=pages, avg_chars=round(avg, 1), sha1=_sha1(p))
            elif do_hash:
                info["sha1"] = _sha1(p)
            present.append(info)
        if len(present) >= 2:
            out.append({"parent": parent, "attachments": present})
    return out


def metadata_smells(c):
    no_title = c.execute(f"""SELECT COUNT(*) FROM items i JOIN itemTypes it ON it.itemTypeID=i.itemTypeID
        WHERE it.typeName NOT IN ('attachment','note','annotation') AND i.{NOT_TRASHED}
        AND NOT EXISTS(SELECT 1 FROM itemData d JOIN fields f ON f.fieldID=d.fieldID
          WHERE d.itemID=i.itemID AND f.fieldName IN ('title','nameOfAct','caseName'))""").fetchone()[0]
    placeholders = c.execute("""SELECT d.itemID, v.value FROM itemData d JOIN itemDataValues v ON v.valueID=d.valueID
        JOIN fields f ON f.fieldID=d.fieldID WHERE f.fieldName='title'
        AND (v.value LIKE '%No Title%' OR v.value LIKE '%済無%' OR v.value GLOB '*[一-龯]*No Title*')""").fetchall()
    # identifiers shared across many items = junk (e.g. Mendeley's CBO9781107415324.004)
    shared = {}
    for field in ("DOI", "ISBN"):
        rows = c.execute(f"""SELECT LOWER(v.value) val, COUNT(DISTINCT d.itemID) n FROM itemData d
            JOIN itemDataValues v ON v.valueID=d.valueID JOIN fields f ON f.fieldID=d.fieldID
            JOIN items i ON i.itemID=d.itemID
            WHERE f.fieldName=? AND i.{NOT_TRASHED} GROUP BY LOWER(v.value) HAVING n>=3
            ORDER BY n DESC""", (field,)).fetchall()
        if rows:
            shared[field] = [(v, n) for v, n in rows]
    return {"no_title": no_title, "placeholders": placeholders, "shared_identifiers": shared}


def run(write_json=None, do_hash=True):
    c = _ro()
    print("=" * 74)
    print("A. INTEGRITATE REFERENTIALA")
    integ = integrity(c)
    for k, n in integ.items():
        print(f"  {'OK ' if n == 0 else '!! '}{k}: {n}")

    print("\n" + "=" * 74)
    print("B. ATASAMENTE")
    base = base_attachment_path()
    total, present, missing_hash, phantom, linked_missing, web = attachments(c, base)
    print(f"  PDF (netrashed, cu parinte, fara legaturi web): {total} | fisier prezent: {present}")
    print(f"  legaturi web catre PDF (n-au fisier, nu intra in total): {web}")
    print(f"  fisier lipsa DAR sync-abil (are hash/folder): {len(missing_hash)}")
    print(f"  !! FANTOME (imported_file, fara hash/folder — sparg sync 'linkMode'): {len(phantom)}")
    if phantom:
        by_date = Counter(p["dateAdded"] for p in phantom)
        print(f"     dateAdded: {dict(by_date.most_common(6))}")
    print(f"  !! fisiere legate lipsa (cale in afara Zotero): {len(linked_missing)}"
          f"   [baseAttachmentPath: {base or 'nesetat'}]")
    for l in linked_missing:
        print(f"       {l['key']}  {l['path']}")
    n_rest = total - present - len(missing_hash) - len(phantom) - len(linked_missing)
    if n_rest:
        print(f"  alte atasamente fara fisier verificabil (fara cale): {n_rest}")

    print("\n" + "=" * 74)
    print("C. ITEME CU 2+ PDF PREZENTE (de reconciliat)")
    dups = dup_attachments(c, do_hash=do_hash)
    print(f"  {len(dups)} iteme")
    if do_hash and HAVE_OCR:
        cats = Counter()
        for g in dups:
            atts = g["attachments"]
            hashes = {a["sha1"] for a in atts}
            scans = [a for a in atts if a.get("is_scan")]
            if len(hashes) == 1:
                cats["continut identic (dedup simplu)"] += 1
            elif scans and len(scans) < len(atts):
                cats["scan + text-native (regula prioritate)"] += 1
            elif len(scans) == len(atts):
                cats["toate scanuri (candidat OCR)"] += 1
            else:
                cats["text-native diferite (editii/descarcari)"] += 1
        for k, n in cats.most_common():
            print(f"     {n:4}  {k}")
        stranded = sum(1 for g in dups for a in g["attachments"]
                       if a.get("is_scan") and a["annotations"] > 0)
        print(f"  adnotari pe copii SCAN (nu se mut fara OCR): {stranded} atasamente")

    print("\n" + "=" * 74)
    print("D. METADATE")
    sm = metadata_smells(c)
    print(f"  iteme fara titlu: {sm['no_title']}")
    print(f"  titluri-placeholder: {len(sm['placeholders'])} {[p[0] for p in sm['placeholders']][:8]}")
    for field, rows in sm["shared_identifiers"].items():
        print(f"  {field} pe 3+ iteme diferite (suspect junk): {len(rows)}")
        for v, n in rows[:5]:
            print(f"       {n}x  {v[:60]}")

    print("\n" + "=" * 74)
    print("E. COS")
    print(f"  iteme in trash: {c.execute('SELECT COUNT(*) FROM deletedItems').fetchone()[0]}")

    if write_json:
        json.dump({"integrity": integ,
                   "phantom_attachments": phantom,
                   "missing_but_syncable": missing_hash,
                   "linked_missing": linked_missing,
                   "dup_attachments": dups,
                   "metadata": {"no_title": sm["no_title"],
                                "placeholders": sm["placeholders"],
                                "shared_identifiers": sm["shared_identifiers"]}},
                  open(write_json, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"\nfindings -> {write_json}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write findings JSON for the reconcile step")
    ap.add_argument("--no-hash", action="store_true", help="skip hashing/scan detection (fast)")
    a = ap.parse_args()
    run(write_json=a.json, do_hash=not a.no_hash)
