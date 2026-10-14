# -*- coding: utf-8 -*-
"""readopt_cache_notes.py — reatașează notele de cache desprinse de Zotero.

O singură rulare, cu Zotero ÎNCHIS. Zotero nu acceptă note sub atașamente: le scrie fără
să se plângă, dar la următoarea deschidere le desprinde (`parentItemID` devine NULL). Ele
rămân valide — textul e acolo, cu hash-ul lui în antet — dar căutarea veche nu le mai
găsea, deci fiecare sesiune re-extrăgea degeaba.

`pdf_text_cache` le regăsește acum pe hash, deci nimic nu e pierdut nici fără scriptul
ăsta. El face curat: le pune sub itemul-părinte al atașamentului, unde rezistă, și le
completează antetul cu cheia atașamentului, ca regăsirea să nu mai coste o scanare.

    python readopt_cache_notes.py            # simulare, nu scrie nimic
    python readopt_cache_notes.py --scrie    # execută, cere Zotero închis
"""
import sys, os, hashlib
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zot
import pdf_text_cache as c


def _hash_fisier(cale):
    try:
        with open(cale, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def inventar(execute):
    """Notele de cache care nu stau sub un item obișnuit, cu atașamentul fiecăreia."""
    atasamente = execute(
        "SELECT i.itemID, i.key, a.storageHash, a.parentItemID FROM items i "
        "JOIN itemAttachments a USING(itemID) WHERE i.itemID NOT IN "
        "(SELECT itemID FROM deletedItems)", ()).fetchall()
    dupa_cheie = {k: (iid, sh, par) for iid, k, sh, par in atasamente}
    dupa_hash = {sh: (iid, k, par) for iid, k, sh, par in atasamente if sh}
    fara_hash = [(iid, k, par) for iid, k, sh, par in atasamente if not sh]

    note = execute("SELECT itemID, parentItemID, note FROM itemNotes WHERE note LIKE ?",
                   ("%" + c.MARKER + "%",)).fetchall()
    id_atasamente = {iid for iid, _, _, _ in atasamente}

    rezultat = []
    hashuri_calculate = {}
    for note_id, parinte, text in note:
        # nota e „așezată bine" doar dacă stă sub un item care NU e atașament
        if parinte is not None and parinte not in id_atasamente:
            continue
        h = c._parse_header(text) or {}
        cheie, hash_notei = h.get("attachment"), h.get("storageHash")

        tinta = None
        if cheie and cheie in dupa_cheie:
            iid, sh, par = dupa_cheie[cheie]
            tinta = (iid, cheie, par)
        elif hash_notei and hash_notei in dupa_hash:
            iid, k, par = dupa_hash[hash_notei]
            tinta = (iid, k, par)
        elif hash_notei:
            for iid, k, par in fara_hash:
                if iid not in hashuri_calculate:
                    hashuri_calculate[iid] = _hash_fisier(zot.attachment_path(k))
                if hashuri_calculate[iid] == hash_notei:
                    tinta = (iid, k, par)
                    break
        rezultat.append((note_id, parinte, tinta, hash_notei, "attachment" in h))
    return rezultat


def main(scrie=False):
    ro = zot._ro()
    lucruri = inventar(ro.execute)
    de_mutat = [x for x in lucruri if x[2] and x[2][2] is not None]
    fara_parinte = [x for x in lucruri if x[2] and x[2][2] is None]
    negasite = [x for x in lucruri if not x[2]]

    print(f"note de cache asezate gresit: {len(lucruri)}")
    print(f"  {len(de_mutat)} se pot muta sub itemul-parinte")
    print(f"  {len(fara_parinte)} raman de sine statatoare (atasamentul n-are item-parinte)")
    print(f"  {len(negasite)} fara atasament identificabil (fisier disparut sau antet rupt)")
    if not scrie:
        print("SIMULARE — nu s-a scris nimic. Reia cu --scrie, cu Zotero inchis.")
        return

    with zot.write_session("readopt-cache-notes") as cur:
        mutate = completate = 0
        for note_id, _, tinta, hash_notei, are_cheie in lucruri:
            if not tinta:
                continue
            att_id, cheie, parinte_real = tinta
            if parinte_real is not None:
                cur.execute("UPDATE itemNotes SET parentItemID=? WHERE itemID=?",
                            (parinte_real, note_id))
                zot.touch(cur, note_id)
                mutate += 1
            if not are_cheie:
                text = cur.execute("SELECT note FROM itemNotes WHERE itemID=?", (note_id,)).fetchone()[0]
                parsed = c._parse_cache_note(text)
                if parsed:
                    h = c._parse_header(text) or {}
                    offset = h.get("pageOffset")
                    corp = c._antet(parsed[0], cheie, offset) + "\n\n" + parsed[1]
                    zot.update_note(cur, note_id, corp)
                    completate += 1
        print(f"mutate sub parinte: {mutate} | antete completate cu cheia atasamentului: {completate}")


if __name__ == "__main__":
    main(scrie="--scrie" in sys.argv)
