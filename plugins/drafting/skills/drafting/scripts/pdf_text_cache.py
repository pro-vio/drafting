# -*- coding: utf-8 -*-
"""
pdf_text_cache.py — memoize extracted PDF text as a Zotero child note, keyed to
a content hash, so the same PDF is never re-extracted twice. Uses Zotero's own
storageHash when the attachment has one (imported stored copies); falls back
to hashing the file ourselves for LINKED attachments, whose storageHash is
always NULL in Zotero (see _content_hash — this is a real, previously-broken
edge case, not a hypothetical one).

Why a Zotero note and not a local file: "Zotero is the only source of truth"
(SKILL.md principle A) — the cache then travels with the library and needs no
separate cache directory to keep in sync. Invalidation is automatic: if the
PDF file changes, storageHash changes, and the stored hash no longer matches.

CHECK FIRST, WITHOUT CLOSING ZOTERO — that's the actual efficiency win, not just
"don't re-extract": get_cached() is read-only and works with Zotero left open.
Only fall through to a closed write_session on an actual cache miss:

    text = get_cached(attachment_key)                    # Zotero can stay open
    if text is None:                                      # miss: NOW pay the close/reopen cost
        with zot.write_session(f"pdf-text-cache-{attachment_key}") as cur:
            text, _ = ensure_cached(cur, attachment_key, pdf_path)

Calling ensure_cached() inside a write_session on every check (skipping the
get_cached() pre-check) still avoids re-extraction, but needlessly closes
Zotero even on a hit — always check read-only first.

CLI (the `ensure` subcommand always opens a write_session, for convenience —
scripted callers should follow the two-step pattern above instead):
  python pdf_text_cache.py get <attachment_key>
  python pdf_text_cache.py ensure <attachment_key> <pdf_path>
  python pdf_text_cache.py offset <attachment_key>            # read the printed-page offset
  python pdf_text_cache.py offset <attachment_key> <offset>   # record it (Zotero closed)
"""
try: import sys; sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import re, html
import fitz
import zot

MARKER = "[zotero-citations:pdf-text-cache]"


def _dehtml(text):
    """Undo Zotero's own wrapping of a plain-text note: each line becomes its
    own <p>...</p> (blank lines -> <p>&nbsp;</p>), and Zotero also substitutes
    &nbsp; for runs of 2+ consecutive spaces WITHIN a line so the HTML doesn't
    collapse them on display. Reverse both: </p> -> newline, strip remaining
    tags, unescape entities, then fold the resulting NBSP (U+00A0) back to a
    plain space — otherwise a cached phrase with a double space would no
    longer substring-match the same phrase typed with regular spaces."""
    text = re.sub(r"</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\xa0", " ")


def _parse_cache_note(note_text):
    """(stored_hash, extracted_text) from a cache note's body, or None if this
    note isn't one of ours. Handles both the as-written plain text (before
    Zotero's next restart) and the <div class="zotero-note znv1"> form Zotero
    wraps it in afterwards."""
    text = _dehtml(note_text) if "zotero-note" in note_text[:80] else note_text
    lines = text.strip("\n").splitlines()
    if not lines or MARKER not in lines[0]:
        return None
    if len(lines) < 2 or not lines[1].strip().startswith("storageHash:"):
        return None
    stored_hash = lines[1].split(":", 1)[1].strip()
    # Header lines after storageHash are optional "key: value" pairs (today: pageOffset).
    # They end at the first blank line; everything after it is the text.
    i = 2
    while i < len(lines) and lines[i].strip() and re.match(r"^[A-Za-z]+:", lines[i]):
        i += 1
    body_lines = lines[i:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)  # drop the blank/nbsp separator line, in either format
    return stored_hash, "\n".join(body_lines)


def _parse_header(note_text):
    """All header 'key: value' pairs of a cache note (storageHash, pageOffset, ...) as a
    dict, or None if the note isn't ours."""
    text = _dehtml(note_text) if "zotero-note" in note_text[:80] else note_text
    lines = text.strip("\n").splitlines()
    if not lines or MARKER not in lines[0]:
        return None
    out = {}
    for ln in lines[1:]:
        if not ln.strip():
            break
        m = re.match(r"^([A-Za-z]+):\s*(.*)$", ln)
        if not m:
            break
        out[m.group(1)] = m.group(2).strip()
    return out


def _rows(execute, sql, params):
    return execute(sql, params).fetchall()


def _sub_parinte(execute, parent_item_id):
    """Notele-copil ale unui item, ca (note_id, note_text). parent_item_id None -> nimic."""
    if parent_item_id is None:
        return []
    return _rows(execute, "SELECT itemID, note FROM items JOIN itemNotes USING(itemID) "
                          "WHERE parentItemID=?", (parent_item_id,))


def _toate_notele_de_cache(execute):
    """Toate notele care poartă marcajul, oriunde ar fi ajuns — inclusiv desprinse."""
    return _rows(execute, "SELECT itemID, note FROM itemNotes WHERE note LIKE ?",
                 (f"%{MARKER}%",))


def _resolve_note(execute, attachment_key, att_item_id, parent_item_id, current_hash):
    """(note_id, header, text) pentru nota de cache a unui atașament, căutată în trei
    locuri, în ordinea costului. Întoarce None dacă nu există niciuna.

    De ce trei: Zotero NU acceptă note sub atașamente. Le scrie fără să se plângă, dar la
    următoarea deschidere verificarea lui de integritate le desprinde — `parentItemID`
    devine NULL — iar o căutare care se uită doar printre copiii atașamentului nu le mai
    găsește. Rezultatul măsurat înainte de reparație: 483 de note orfane din 525, fiecare
    sesiune re-extrăgând un text care exista deja pe disc.

      1. sub atașament — forma veche, plus notele scrise de la ultima deschidere încoace;
      2. sub itemul-părinte, pe antetul `attachment:` — forma nouă, cea care rezistă;
      3. oriunde, pe `storageHash` egal cu hash-ul curent — recuperează orfanele.

    A treia scanează toate notele bibliotecii, deci stă ultima; dar nu scrie nimic, iar
    textul recuperat e valid: hash-ul e cheia conținutului, nu locul notei."""
    for note_id, note_text in _sub_parinte(execute, att_item_id):
        parsed = _parse_cache_note(note_text)
        if parsed:
            return note_id, _parse_header(note_text) or {}, parsed[1]

    for note_id, note_text in _sub_parinte(execute, parent_item_id):
        h = _parse_header(note_text)
        if h and h.get("attachment") == attachment_key:
            parsed = _parse_cache_note(note_text)
            if parsed:
                return note_id, h, parsed[1]

    for note_id, note_text in _toate_notele_de_cache(execute):
        h = _parse_header(note_text)
        if h and h.get("storageHash") == current_hash:
            parsed = _parse_cache_note(note_text)
            if parsed:
                return note_id, h, parsed[1]
    return None


def _atasament(execute, attachment_key):
    """(itemID, storageHash, parentItemID) pentru un atașament, după cheia lui."""
    row = execute("SELECT itemID, storageHash, parentItemID FROM items JOIN itemAttachments "
                  "USING(itemID) WHERE key=?", (attachment_key,)).fetchone()
    if not row:
        raise ValueError(f"no attachment with key {attachment_key!r}")
    return row


def _content_hash(stored_hash, pdf_path):
    """Zotero's own storageHash when available (imported stored-copy
    attachments); for LINKED attachments storageHash is always NULL — Zotero
    doesn't own/hash a file it doesn't manage — so fall back to hashing the
    file ourselves. Never fall back to the literal Python None: str(None) ==
    "None" was written into a note as 'storageHash: None' by an earlier
    version, and a stored *string* "None" can never equal a freshly-read
    *NoneType* None on the next check — the cache looked stale forever for
    every linked attachment. This happened for real: 13 notes on linked
    reference PDFs were permanently unreadable this way before the fix."""
    if stored_hash:
        return stored_hash
    import hashlib
    with open(pdf_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def get_cached(attachment_key):
    """Extracted text if a fresh cache note exists (hash matches current content), else
    None. Read-only — works whether Zotero is open or not, and finds the note even after
    Zotero has detached it from the attachment (see _resolve_note)."""
    ro = zot._ro()
    att_id, stored_hash_db, parent_id = _atasament(ro.execute, attachment_key)
    current_hash = _content_hash(stored_hash_db, zot.attachment_path(attachment_key))
    found = _resolve_note(ro.execute, attachment_key, att_id, parent_id, current_hash)
    if not found:
        return None
    _, header, text = found
    return text if header.get("storageHash") == current_hash else None


def get_page_offset(attachment_key):
    """The printed-page offset recorded in the cache note, or None. Read-only.
    printed page = PDF page - offset (offset 18: PDF page 47 is printed page 29).
    Recorded once, by someone who checked it against the running heads, so every
    later citation can carry the printed number without re-deriving it."""
    ro = zot._ro()
    att_id, stored_hash_db, parent_id = _atasament(ro.execute, attachment_key)
    current_hash = _content_hash(stored_hash_db, zot.attachment_path(attachment_key))
    found = _resolve_note(ro.execute, attachment_key, att_id, parent_id, current_hash)
    if not found:
        return None
    try:
        return int(found[1]["pageOffset"])
    except (KeyError, ValueError):
        return None


def set_page_offset(cur, attachment_key, offset):
    """Inside a zot.write_session: record the printed-page offset in the existing cache
    note, keeping hash and text untouched. Needs a cache note (ensure_cached first)."""
    att_id, stored_hash_db, parent_id = _atasament(cur.execute, attachment_key)
    current_hash = _content_hash(stored_hash_db, zot.attachment_path(attachment_key))
    found = _resolve_note(cur.execute, attachment_key, att_id, parent_id, current_hash)
    if not found:
        raise ValueError(f"no cache note yet for {attachment_key!r}; ensure_cached first")
    note_id, header, text = found
    antet = _antet(header.get("storageHash", current_hash), attachment_key, int(offset))
    zot.update_note(cur, note_id, antet + "\n\n" + text)


def extract_pdf_text(pdf_path):
    """Plain per-page text extraction, page-marked. Call ocr_overlay.detect_scan()
    first if the source might be an unprocessed scan — this function does not
    OCR anything; it only pulls whatever text layer the PDF already has."""
    doc = fitz.open(pdf_path)
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"--- page {i + 1} ---\n{page.get_text().strip()}")
    doc.close()
    return "\n\n".join(parts)


# `_find_cache_note_cur` a dispărut: căutarea peste cursorul apelantului e acum în
# `_resolve_note`, care primește funcția `execute` — a lui `zot._ro()` la citire, a
# cursorului la scriere. Motivul pentru care nu se deschide o a doua conexiune rămâne
# valabil și e cel care a impus forma asta: o conexiune nouă deschisă în mijlocul unei
# tranzacții de scriere (la itemul 2+ dintr-un `write_session` grupat) lovește lacătul de
# scriitor al SQLite. Măsurat: un lot de 81 de apeluri `ensure_cached` a eșuat pe toate 81
# cu „database is locked" înainte de reparație — commit-ul „reușea", ca no-op, deci nu s-a
# pierdut nimic, dar nici nu s-a memoizat nimic.


def _antet(hash_continut, attachment_key, page_offset=None):
    """Antetul notei. `attachment` e cel care face nota regăsibilă după ce Zotero o
    desprinde: cheia atașamentului nu se schimbă, iar itemul-părinte poate avea mai multe
    atașamente, deci nu e de ajuns să știi sub cine stă nota."""
    linii = [MARKER, f"storageHash: {hash_continut}", f"attachment: {attachment_key}"]
    if page_offset is not None:
        linii.append(f"pageOffset: {int(page_offset)}")
    return "\n".join(linii)


def ensure_cached(cur, attachment_key, pdf_path):
    """Inside a zot.write_session: return (text, was_cached). Extracts and writes the cache
    note only when there is no fresh cache already.

    Nota se scrie **sub itemul-părinte al atașamentului**, nu sub atașament. Zotero nu
    acceptă note sub atașamente și le desprinde la prima verificare de integritate, iar o
    notă desprinsă nu se mai găsea — fiecare sesiune re-extrăgea și rescria o notă care
    urma să fie desprinsă la rândul ei. Când atașamentul n-are părinte (142 din 2661 în
    biblioteca pe care s-a măsurat), nota se scrie de sine stătătoare, deliberat: acolo
    n-are unde sta, iar regăsirea se face pe hash.

    O notă găsită sub atașament — veche, sau scrisă de la ultima deschidere a lui Zotero —
    se **mută** sub părinte cu ocazia asta. Reparația se face pe măsură ce se lucrează, nu
    doar prin scriptul de re-adopție."""
    att_id, stored_hash_db, parent_id = _atasament(cur.execute, attachment_key)
    current_hash = _content_hash(stored_hash_db, pdf_path)
    found = _resolve_note(cur.execute, attachment_key, att_id, parent_id, current_hash)

    gazda = parent_id if parent_id is not None else None
    if found and found[1].get("storageHash") == current_hash:
        _muta_sub_gazda(cur, found[0], gazda)
        return found[2], True

    text = extract_pdf_text(pdf_path)
    offset = found[1].get("pageOffset") if found else None
    # Un pageOffset verificat de om se păstrează la reextragere: numerotarea tipărită nu se
    # mișcă atunci când se reface stratul de text.
    corp = _antet(current_hash, attachment_key, offset) + "\n\n" + text
    if found:
        zot.update_note(cur, found[0], corp)
        _muta_sub_gazda(cur, found[0], gazda)
    else:
        zot.add_child_note(cur, gazda, corp)
    return text, False


def _muta_sub_gazda(cur, note_id, gazda):
    """Reparentează o notă de cache, dacă nu stă deja unde trebuie. Nicio scriere când
    e deja acolo — ca să nu marcheze inutil itemi pentru sincronizare."""
    row = cur.execute("SELECT parentItemID FROM itemNotes WHERE itemID=?", (note_id,)).fetchone()
    if row and row[0] == gazda:
        return False
    cur.execute("UPDATE itemNotes SET parentItemID=? WHERE itemID=?", (gazda, note_id))
    zot.touch(cur, note_id)
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "get":
        t = get_cached(sys.argv[2])
        print(t if t is not None else "(no fresh cache)")
    elif cmd == "offset":
        if len(sys.argv) > 3:
            with zot.write_session(f"pdf-text-cache-offset-{sys.argv[2]}") as cur:
                set_page_offset(cur, sys.argv[2], int(sys.argv[3]))
            print(f"pageOffset={sys.argv[3]} written")
        else:
            print(get_page_offset(sys.argv[2]))
    elif cmd == "ensure":
        with zot.write_session(f"pdf-text-cache-{sys.argv[2]}") as cur:
            text, was_cached = ensure_cached(cur, sys.argv[2], sys.argv[3])
        print(f"was_cached={was_cached}  chars={len(text)}")
    else:
        print(__doc__)
