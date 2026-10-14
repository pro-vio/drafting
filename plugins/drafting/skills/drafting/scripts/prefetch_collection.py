# -*- coding: utf-8 -*-
"""
prefetch_collection.py — batch-populate the PDF text cache for every attachment
in a Zotero collection, in ONE write_session, so a subsequent source-verification
pass (comparing a thesis's citations against their sources) reads from cache —
zero new Read/Bash prompts, however many sources are cited.

Built for the "verify a thesis's citations" workflow (grila_evaluare coordination),
but generic: works for any named collection. Pair with adding the folder that
holds the actual PDFs (e.g. a student's own reference folder, for linked
attachments) to `additionalDirectories` in ~/.claude/settings.json — this script
removes the re-extraction cost; that settings entry removes the per-file
permission prompt. Both were needed; neither alone was enough.

CLI:
  python prefetch_collection.py <collection name>
"""
import os
try: import sys; sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import zot, pdf_text_cache as ptc


def _resolve_path(attachment_key, raw_path):
    """Same join `zot.attachment_path(key)` does — **aceea e unealta publică**, de folosit
    când ai o singură cheie și n-ai calea în mână. Asta e varianta din buclă, cu calea deja
    citită —
    avoids opening one throwaway _ro() connection per attachment. Opening many
    short-lived connections in a tight loop, right before a write_session,
    is exactly the shape that produced 'database is locked' here in testing:
    Windows doesn't necessarily drop a SQLite file lock the instant a Python
    connection object is dereferenced, so a pile of them can still be settling
    when open_rw() tries to write moments later. Fewer connections, not just
    correctly-scoped ones, is the safer bar for anything that runs in a loop."""
    if not raw_path:
        return None
    return (os.path.join(zot.STORAGE, attachment_key, raw_path[len("storage:"):])
            if raw_path.startswith("storage:") else raw_path)


def collection_attachments(collection_key_or_name):
    """[(itemKey, title, attachmentKey, path)] for every PDF attachment of every
    item directly in the named collection. Read-only; opens exactly ONE
    connection for the whole call (see _resolve_path for why that matters)."""
    found = zot.find_collection(collection_key_or_name)
    if not found:
        raise ValueError(f"no collection named {collection_key_or_name!r}")
    _, collection_id = found
    con = zot._ro()
    try:
        rows = con.execute("""
            SELECT i.key, i.itemID FROM collectionItems ci
            JOIN items i ON i.itemID = ci.itemID
            WHERE ci.collectionID = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """, (collection_id,)).fetchall()
        out = []
        for item_key, item_id in rows:
            att = con.execute("""
                SELECT ai.key, ia.path FROM itemAttachments ia JOIN items ai ON ai.itemID = ia.itemID
                WHERE ia.parentItemID = ? AND ia.contentType = 'application/pdf'
                  AND ai.itemID NOT IN (SELECT itemID FROM deletedItems)
                LIMIT 1
            """, (item_id,)).fetchone()
            title = con.execute("""
                SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID = d.valueID
                JOIN fields f ON f.fieldID = d.fieldID
                WHERE d.itemID = ? AND f.fieldName = 'title'
            """, (item_id,)).fetchone()
            if att:
                out.append((item_key, title[0] if title else "(no title)", att[0],
                            _resolve_path(att[0], att[1])))
        return out
    finally:
        con.close()


def attachments_by_key(attachment_keys):
    """[(itemKey, title, attachmentKey, path)] pentru o listă de chei de ATAȘAMENT.

    Perechea lui `collection_attachments`, pentru cazul celălalt — și cel obișnuit la o trecere
    prin literatură: surse răzlețe prin bibliotecă, găsite cu `zot.find`, care nu stau într-o
    colecție. Fără ea, fiecare consumator își scria propria buclă peste `zot._ro()` și își
    reconstruia calea din prefixul `storage:`.

    O singură conexiune pentru tot apelul, din același motiv ca acolo: multe conexiuni
    scurte deschise în buclă chiar înaintea unui `write_session` sunt tiparul care a produs
    `database is locked` la testare."""
    con = zot._ro()
    try:
        out = []
        for att_key in attachment_keys:
            r = con.execute("""
                SELECT ia.path, ia.parentItemID FROM itemAttachments ia
                JOIN items ai ON ai.itemID = ia.itemID
                WHERE ai.key = ? AND ai.itemID NOT IN (SELECT itemID FROM deletedItems)
            """, (att_key,)).fetchone()
            if not r:
                out.append((None, "(atașament inexistent sau în coș)", att_key, None))
                continue
            raw_path, parent_id = r
            item_key, title = None, "(fără titlu)"
            if parent_id:
                p = con.execute("SELECT key FROM items WHERE itemID = ?", (parent_id,)).fetchone()
                item_key = p[0] if p else None
                tt = con.execute("""
                    SELECT v.value FROM itemData d JOIN itemDataValues v ON v.valueID = d.valueID
                    JOIN fields f ON f.fieldID = d.fieldID
                    WHERE d.itemID = ? AND f.fieldName = 'title'
                """, (parent_id,)).fetchone()
                if tt:
                    title = tt[0]
            out.append((item_key, title, att_key, _resolve_path(att_key, raw_path)))
        return out
    finally:
        con.close()


def prefetch(collection_name=None, attachment_keys=None):
    """Batch-populate the cache in ONE write_session — pentru o colecție sau pentru o listă
    de chei de atașament. Returns a manifest: [(title, attachment_key, status, chars_or_error)].

        prefetch("Metoda")                                  # colecția întreagă
        prefetch(attachment_keys=["ABCD1234", "EFGH5678"])  # surse răzlețe

    Un singur ciclu de închidere în ambele cazuri: asta e tot rostul funcției."""
    if (collection_name is None) == (attachment_keys is None):
        raise ValueError("dă ori un nume de colecție, ori o listă de chei de atașament")
    if attachment_keys is not None:
        items = attachments_by_key(attachment_keys)
        tag = "prefetch-%d-atasamente" % len(items)
    else:
        items = collection_attachments(collection_name)
        tag = f"prefetch-{collection_name}"
    manifest = []
    with zot.write_session(tag) as cur:
        for item_key, title, att_key, path in items:
            if not path:
                manifest.append((title, att_key, "no-file", None))
                continue
            try:
                text, was_cached = ptc.ensure_cached(cur, att_key, path)
                manifest.append((title, att_key, "cached" if was_cached else "extracted", len(text)))
            except Exception as e:
                manifest.append((title, att_key, "error", str(e)))
    return manifest


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit("folosire:  prefetch_collection.py \"<colecție>\"\n"
                         "           prefetch_collection.py --chei CHEIE1 CHEIE2 ...")
    if args[0] == "--chei":
        chei = args[1:]
        if not chei:
            raise SystemExit("--chei cere cel puțin o cheie de atașament")
        manifest = prefetch(attachment_keys=chei)
        despre = "%d atașamente date pe cheie" % len(chei)
    else:
        manifest = prefetch(args[0])
        despre = "colecția %r" % args[0]
    for title, key, status, info in manifest:
        print(f"{status:10s}  {key}  {info if info is not None else ''}  {title}")
    print(f"\n{len(manifest)} attachments processed for {despre}.")
